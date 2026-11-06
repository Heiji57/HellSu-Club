"""관리자 검토: 승인, 거절, 출석 취소 (REQUIREMENTS 6.1, 6.2, ARCHITECTURE 3.4, 4.2).

모든 상태 변경은 현재 상태를 조건으로 거는 UPDATE로 한다. 두 관리자가 동시에 눌러도
한 명만 성공하고, 나머지는 0행이 되어 AlreadyProcessed를 받는다.

상태 변경, 판정 이력, AuditLog, 출석 생성·취소가 한 트랜잭션이다.
관리자가 결정한 결과는 result_notified_at = NULL로 두어, 회원이 다음 명령을 쓸 때 알린다 (ADR-0005).
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.database import Database
from app.core.exceptions import AlreadyProcessed, ReasonRequired, VerificationNotFound
from app.image.storage import ImageStorage
from app.models import (
    Attendance,
    AttendanceStatus,
    Member,
    ReviewDecision,
    ReviewerType,
    Verification,
    VerificationReview,
    VerificationStatus,
)
from app.repositories.audit import AuditRepository
from app.repositories.verification import VerificationRef, VerificationRepository
from app.services.attendance_service import AttendanceService

log = logging.getLogger(__name__)

MAX_REASON_LENGTH = 500

STATUS_TEXT = {
    VerificationStatus.APPROVED: "승인",
    VerificationStatus.REVIEW_REQUIRED: "검토 대기",
    VerificationStatus.REJECTED: "거절",
}


@dataclass(frozen=True)
class ReviewOutcome:
    verification_id: int
    decision: ReviewDecision
    status: VerificationStatus
    member_discord_id: int
    member_display_name: str
    workout_date: date
    reason: str | None
    actor_discord_id: int
    review_message_id: int | None


@dataclass(frozen=True)
class VerificationDetail:
    verification: Verification
    member: Member
    reviews: tuple[VerificationReview, ...]  # 오래된 순
    matches: tuple[tuple[VerificationRef, int, bool], ...]  # (기존 인증, 거리, 완전 일치)
    image_file: Path | None

    @property
    def status(self) -> VerificationStatus:
        return VerificationStatus(self.verification.status)

    @property
    def revoked(self) -> bool:
        return bool(self.reviews) and self.reviews[-1].decision == ReviewDecision.REVOKE


@dataclass(frozen=True)
class PendingItem:
    verification_id: int
    member_display_name: str
    workout_date: date
    submitted_at: datetime
    reason: str | None
    review_message_id: int | None


def clean_reason(reason: str | None, *, required: bool) -> str | None:
    reason = (reason or "").strip() or None
    if reason is None and required:
        raise ReasonRequired()
    if reason is not None and len(reason) > MAX_REASON_LENGTH:
        raise ReasonRequired(f"사유는 {MAX_REASON_LENGTH}자 이하로 적어 주세요.")
    return reason


class ReviewService:
    def __init__(
        self, db: Database, clock: Clock, *, attendance: AttendanceService, storage: ImageStorage
    ) -> None:
        self._db = db
        self._clock = clock
        self._attendance = attendance
        self._storage = storage

    async def approve(
        self, verification_id: int, *, actor_discord_id: int, reason: str | None = None
    ) -> ReviewOutcome:
        reason = clean_reason(reason, required=False)
        async with self._db.transaction() as s:
            v = await self._transition(
                s, verification_id, VerificationStatus.REVIEW_REQUIRED, VerificationStatus.APPROVED
            )
            await self._attendance.create(s, v)
            return await self._record(
                s, v, ReviewDecision.APPROVE, actor_discord_id, reason, "VERIFICATION_APPROVED"
            )

    async def reject(
        self, verification_id: int, *, actor_discord_id: int, reason: str | None
    ) -> ReviewOutcome:
        reason = clean_reason(reason, required=True)
        async with self._db.transaction() as s:
            v = await self._transition(
                s, verification_id, VerificationStatus.REVIEW_REQUIRED, VerificationStatus.REJECTED
            )
            return await self._record(
                s, v, ReviewDecision.REJECT, actor_discord_id, reason, "VERIFICATION_REJECTED"
            )

    async def revoke(
        self, verification_id: int, *, actor_discord_id: int, reason: str | None
    ) -> ReviewOutcome:
        """승인된 인증의 출석을 취소한다. 출석 REVOKED + 인증 REJECTED."""
        reason = clean_reason(reason, required=True)
        async with self._db.transaction() as s:
            v = await self._transition(
                s, verification_id, VerificationStatus.APPROVED, VerificationStatus.REJECTED
            )
            await s.execute(
                update(Attendance)
                .where(
                    Attendance.verification_id == v.id,
                    Attendance.status == AttendanceStatus.ACTIVE,
                )
                .values(status=AttendanceStatus.REVOKED, revoked_at=self._clock.now())
            )
            return await self._record(
                s, v, ReviewDecision.REVOKE, actor_discord_id, reason, "ATTENDANCE_REVOKED"
            )

    async def detail(self, verification_id: int) -> VerificationDetail:
        async with self._db.transaction() as s:
            v = await s.get(Verification, verification_id)
            if v is None:
                raise VerificationNotFound()
            member = await s.get(Member, v.member_id)
            reviews = tuple(
                await s.scalars(
                    select(VerificationReview)
                    .where(VerificationReview.verification_id == v.id)
                    .order_by(VerificationReview.id)
                )
            )
            raw = next(
                (r.details.get("matches", []) for r in reviews if r.reviewer_type == "RULE"), []
            )
            refs = await VerificationRepository(s).refs(m["verification_id"] for m in raw)
        matches = tuple(
            (refs[m["verification_id"]], m["distance"], m["exact"])
            for m in raw
            if m["verification_id"] in refs
        )
        image = None
        if v.image_path:
            path = self._storage.absolute(v.image_path)
            image = path if path.exists() else None
        assert member is not None
        return VerificationDetail(v, member, reviews, matches, image)

    async def list_pending(self, limit: int = 10) -> tuple[Sequence[PendingItem], int]:
        """검토 대기 목록(오래된 순)과 전체 건수."""
        async with self._db.transaction() as s:
            pending = Verification.status == VerificationStatus.REVIEW_REQUIRED
            stmt = (
                select(Verification, Member.display_name)
                .join(Member, Member.id == Verification.member_id)
                .where(pending)
                .order_by(Verification.submitted_at)
                .limit(limit)
            )
            rows = (await s.execute(stmt)).all()
            total = await s.scalar(select(func.count()).select_from(Verification).where(pending))
            reviews = await VerificationRepository(s).latest_reviews(v.id for v, _ in rows)
        items = [
            PendingItem(
                v.id,
                name,
                v.workout_date,
                v.submitted_at,
                reviews[v.id].reason if v.id in reviews else None,
                v.review_message_id,
            )
            for v, name in rows
        ]
        return items, total or 0

    # --- 내부 ---

    async def _transition(
        self,
        s: AsyncSession,
        verification_id: int,
        expected: VerificationStatus,
        new: VerificationStatus,
    ) -> Verification:
        now = self._clock.now()
        result = await s.execute(
            update(Verification)
            .where(Verification.id == verification_id, Verification.status == expected)
            .values(status=new, decided_at=now, result_notified_at=None)
            .returning(Verification.id)
        )
        if result.scalar_one_or_none() is None:
            current = await s.scalar(
                select(Verification.status).where(Verification.id == verification_id)
            )
            if current is None:
                raise VerificationNotFound()
            raise AlreadyProcessed(
                f"이미 처리된 인증입니다. (현재 상태: {STATUS_TEXT[VerificationStatus(current)]})"
            )
        v = await s.get(Verification, verification_id, populate_existing=True)
        assert v is not None
        return v

    async def _record(
        self,
        s: AsyncSession,
        v: Verification,
        decision: ReviewDecision,
        actor_discord_id: int,
        reason: str | None,
        action: str,
    ) -> ReviewOutcome:
        s.add(
            VerificationReview(
                verification_id=v.id,
                reviewer_type=ReviewerType.ADMIN,
                actor_discord_id=actor_discord_id,
                decision=decision,
                reason=reason,
            )
        )
        member = await s.get(Member, v.member_id)
        assert member is not None
        await AuditRepository(s).record(
            actor_discord_id=actor_discord_id,
            action=action,
            target_type="verification",
            target_id=v.id,
            details={
                "member_discord_id": member.discord_user_id,
                "workout_date": v.workout_date.isoformat(),
                "reason": reason,
            },
        )
        log.info("verification #%d %s by %s", v.id, decision, actor_discord_id)
        return ReviewOutcome(
            verification_id=v.id,
            decision=decision,
            status=VerificationStatus(v.status),
            member_discord_id=member.discord_user_id,
            member_display_name=member.display_name,
            workout_date=v.workout_date,
            reason=reason,
            actor_discord_id=actor_discord_id,
            review_message_id=v.review_message_id,
        )
