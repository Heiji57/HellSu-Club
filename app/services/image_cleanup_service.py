"""보관 기간이 지난 인증 이미지 정리 (REQUIREMENTS 9).

판정이 확정된(검토 대기가 아닌) 인증 중 decided_at + IMAGE_RETENTION_DAYS가 지난 것의 파일을 지우고
image_path를 NULL로 바꾼다. 해시(SHA-256, pHash)는 남기므로 중복 탐지는 계속 동작한다.
검토 대기 중인 인증의 사진은 관리자가 봐야 하므로 지우지 않는다.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update

from app.core.clock import Clock
from app.core.database import Database
from app.image.storage import ImageStorage
from app.models import Verification, VerificationStatus

log = logging.getLogger(__name__)

BATCH_SIZE = 200


@dataclass(frozen=True)
class CleanupResult:
    cleared: int  # image_path를 NULL로 바꾼 인증 수
    deleted_files: int  # 실제로 지운 파일 수 (이미 없던 파일은 제외)


class ImageCleanupService:
    def __init__(
        self, db: Database, clock: Clock, storage: ImageStorage, *, retention_days: int
    ) -> None:
        self._db = db
        self._clock = clock
        self._storage = storage
        self._retention = timedelta(days=retention_days)

    async def run(self) -> CleanupResult:
        cutoff = self._clock.now() - self._retention
        cleared = deleted = 0
        while True:
            async with self._db.transaction() as s:
                rows = (
                    await s.execute(
                        select(Verification.id, Verification.image_path)
                        .where(
                            Verification.image_path.is_not(None),
                            Verification.status != VerificationStatus.REVIEW_REQUIRED,
                            Verification.decided_at < cutoff,
                        )
                        .order_by(Verification.id)
                        .limit(BATCH_SIZE)
                    )
                ).all()
            if not rows:
                break
            # 파일을 먼저 지우고, 지운(또는 이미 없던) 것만 경로를 비운다.
            # 삭제에 실패한 파일은 경로를 남겨 다음 실행에서 다시 시도한다.
            done: list[int] = []
            for row in rows:
                try:
                    deleted += await self._storage.delete(row.image_path)
                    done.append(row.id)
                except (OSError, ValueError):
                    log.warning("이미지 삭제 실패: #%d %s", row.id, row.image_path, exc_info=True)
            if not done:
                break  # 남은 것이 모두 실패 → 다음 실행에서 재시도
            async with self._db.transaction() as s:
                await s.execute(
                    update(Verification).where(Verification.id.in_(done)).values(image_path=None)
                )
            cleared += len(done)
        if cleared:
            log.info("이미지 정리: 인증 %d건, 파일 %d개 삭제", cleared, deleted)
        return CleanupResult(cleared, deleted)
