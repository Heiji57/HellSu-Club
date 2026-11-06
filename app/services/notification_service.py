"""지난 인증 결과 알림 (ADR-0005).

관리자가 나중에 결정한 결과(승인·거절·출석 취소)는 회원이 다음에 봇 명령을 쓸 때 알린다.
조건부 UPDATE로 먼저 "알림 완료"를 기록(선점)한 뒤 보낸다. 같은 결과를 두 번 보내지 않는 대신,
전송이 실패하면 그 알림은 사라진다. 결과는 /내기록에서 언제든 볼 수 있다.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select, update

from app.core.clock import Clock
from app.core.database import Database
from app.models import Member, ReviewDecision, Verification, VerificationStatus
from app.repositories.verification import VerificationRepository


@dataclass(frozen=True)
class Notice:
    verification_id: int
    workout_date: date
    status: VerificationStatus
    revoked: bool
    reason: str | None


class NotificationService:
    def __init__(self, db: Database, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    async def claim(self, discord_user_id: int) -> list[Notice]:
        async with self._db.transaction() as s:
            member_id = await s.scalar(
                select(Member.id).where(Member.discord_user_id == discord_user_id)
            )
            if member_id is None:
                return []
            rows = (
                await s.execute(
                    update(Verification)
                    .where(
                        Verification.member_id == member_id,
                        Verification.status != VerificationStatus.REVIEW_REQUIRED,
                        Verification.result_notified_at.is_(None),
                    )
                    .values(result_notified_at=self._clock.now())
                    .returning(Verification.id, Verification.workout_date, Verification.status)
                )
            ).all()
            reviews = await VerificationRepository(s).latest_reviews(r.id for r in rows)
        notices = []
        for row in sorted(rows, key=lambda r: (r.workout_date, r.id)):
            review = reviews.get(row.id)
            notices.append(
                Notice(
                    verification_id=row.id,
                    workout_date=row.workout_date,
                    status=VerificationStatus(row.status),
                    revoked=review is not None and review.decision == ReviewDecision.REVOKE,
                    reason=review.reason if review else None,
                )
            )
        return notices
