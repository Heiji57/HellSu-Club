from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.week import Week
from app.models import Member, Verification, VerificationReview, VerificationStatus

ACTIVE_STATUSES = (VerificationStatus.APPROVED, VerificationStatus.REVIEW_REQUIRED)


@dataclass(frozen=True)
class VerificationRef:
    """중복 탐지 결과에 보여줄 기존 인증 요약."""

    id: int
    member_display_name: str
    workout_date: date
    status: str
    review_message_id: int | None


class VerificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, verification_id: int) -> Verification | None:
        return await self._s.get(Verification, verification_id)

    async def exists_active_on(self, member_id: int, workout_date: date) -> bool:
        stmt = select(
            exists().where(
                Verification.member_id == member_id,
                Verification.workout_date == workout_date,
                Verification.status.in_(ACTIVE_STATUSES),
            )
        )
        return bool(await self._s.scalar(stmt))

    async def add(self, verification: Verification) -> Verification:
        self._s.add(verification)
        await self._s.flush()
        return verification

    async def add_review(self, review: VerificationReview) -> VerificationReview:
        self._s.add(review)
        await self._s.flush()
        return review

    async def ids_with_sha256(self, sha256: str) -> Sequence[int]:
        stmt = (
            select(Verification.id)
            .where(Verification.image_sha256 == sha256)
            .order_by(Verification.id)
        )
        return (await self._s.scalars(stmt)).all()

    async def all_phashes(self) -> Sequence[tuple[int, str]]:
        """모든 기존 인증 이미지의 pHash. 회원 수십 명 규모라 전수 비교한다 (REQUIREMENTS 9)."""
        stmt = select(Verification.id, Verification.image_phash).where(
            Verification.image_phash.is_not(None)
        )
        return [(row.id, row.image_phash) for row in await self._s.execute(stmt)]

    async def refs(self, ids: Iterable[int]) -> dict[int, VerificationRef]:
        ids = list(ids)
        if not ids:
            return {}
        stmt = (
            select(
                Verification.id,
                Member.display_name,
                Verification.workout_date,
                Verification.status,
                Verification.review_message_id,
            )
            .join(Member, Member.id == Verification.member_id)
            .where(Verification.id.in_(ids))
        )
        return {row.id: VerificationRef(*row) for row in await self._s.execute(stmt)}

    async def set_image_path(self, verification_id: int, image_path: str) -> None:
        await self._s.execute(
            update(Verification)
            .where(Verification.id == verification_id)
            .values(image_path=image_path)
        )

    async def set_review_message(self, verification_id: int, message_id: int) -> None:
        await self._s.execute(
            update(Verification)
            .where(Verification.id == verification_id)
            .values(review_message_id=message_id)
        )

    async def count_pending(self, *, week: Week, member_id: int | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(Verification)
            .where(
                Verification.status == VerificationStatus.REVIEW_REQUIRED,
                Verification.workout_date.between(week.start, week.end),
            )
        )
        if member_id is not None:
            stmt = stmt.where(Verification.member_id == member_id)
        return await self._s.scalar(stmt) or 0

    async def count_for_member(self, member_id: int) -> int:
        stmt = (
            select(func.count())
            .select_from(Verification)
            .where(Verification.member_id == member_id)
        )
        return await self._s.scalar(stmt) or 0

    async def page_for_member(
        self, member_id: int, *, offset: int, limit: int
    ) -> Sequence[Verification]:
        stmt = (
            select(Verification)
            .where(Verification.member_id == member_id)
            .order_by(Verification.submitted_at.desc(), Verification.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return (await self._s.scalars(stmt)).all()

    async def latest_reviews(self, ids: Iterable[int]) -> dict[int, VerificationReview]:
        """인증별 마지막 판정 이력."""
        ids = list(ids)
        if not ids:
            return {}
        stmt = (
            select(VerificationReview)
            .where(VerificationReview.verification_id.in_(ids))
            .distinct(VerificationReview.verification_id)
            .order_by(VerificationReview.verification_id, VerificationReview.id.desc())
        )
        return {r.verification_id: r for r in await self._s.scalars(stmt)}
