from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, pk


class VerificationStatus(StrEnum):
    APPROVED = "APPROVED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    REJECTED = "REJECTED"


class ReviewerType(StrEnum):
    RULE = "RULE"
    ADMIN = "ADMIN"


class ReviewDecision(StrEnum):
    APPROVE = "APPROVE"
    REVIEW = "REVIEW"
    REJECT = "REJECT"
    REVOKE = "REVOKE"


# 하루 1회 인증: 승인됐거나 검토 대기 중인 인증이 있는 날에는 더 만들 수 없다.
# 거절된 날은 다시 제출할 수 있도록 부분 인덱스로 둔다.
ACTIVE_VERIFICATION = "status IN ('APPROVED', 'REVIEW_REQUIRED')"


class Verification(Base):
    """운동 인증 원본. 원본 필드는 INSERT 후 바꾸지 않는다(ARCHITECTURE 3.3)."""

    __tablename__ = "verification"
    __table_args__ = (
        CheckConstraint(
            "status IN ('APPROVED', 'REVIEW_REQUIRED', 'REJECTED')",
            name="status",
        ),
        CheckConstraint("content IS NOT NULL OR image_sha256 IS NOT NULL", name="has_content"),
        CheckConstraint(
            "(image_sha256 IS NULL AND image_phash IS NULL"
            " AND image_mime IS NULL AND image_bytes IS NULL AND image_path IS NULL)"
            " OR (image_sha256 IS NOT NULL AND image_phash IS NOT NULL"
            " AND image_mime IS NOT NULL AND image_bytes IS NOT NULL)",
            name="image_fields",
        ),
        CheckConstraint(
            "(status = 'REVIEW_REQUIRED') = (decided_at IS NULL)",
            name="decided_at_matches_status",
        ),
        Index(
            "uq_verification_member_day_active",
            "member_id",
            "workout_date",
            unique=True,
            postgresql_where=text(ACTIVE_VERIFICATION),
        ),
        Index("ix_verification_member_submitted", "member_id", "submitted_at"),
        Index("ix_verification_image_sha256", "image_sha256"),
        Index(
            "ix_verification_review_required",
            "submitted_at",
            postgresql_where=text("status = 'REVIEW_REQUIRED'"),
        ),
    )

    id: Mapped[int] = pk()
    member_id: Mapped[int] = mapped_column(ForeignKey("member.id"), nullable=False)
    interaction_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    channel_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    workout_date: Mapped[date] = mapped_column(Date, nullable=False)
    image_path: Mapped[str | None] = mapped_column(Text)
    image_mime: Mapped[str | None] = mapped_column(String(32))
    image_bytes: Mapped[int | None] = mapped_column(Integer)
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    image_phash: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_message_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = created_at()

    @property
    def has_image(self) -> bool:
        return self.image_sha256 is not None


class VerificationReview(Base):
    """판정 이력. INSERT만 한다. verification.status는 마지막 이력의 캐시다."""

    __tablename__ = "verification_review"
    __table_args__ = (
        CheckConstraint("reviewer_type IN ('RULE', 'ADMIN')", name="reviewer_type"),
        CheckConstraint("decision IN ('APPROVE', 'REVIEW', 'REJECT', 'REVOKE')", name="decision"),
        CheckConstraint(
            "(reviewer_type = 'RULE') = (actor_discord_id IS NULL)", name="actor_matches_type"
        ),
    )

    id: Mapped[int] = pk()
    verification_id: Mapped[int] = mapped_column(
        ForeignKey("verification.id"), nullable=False, index=True
    )
    reviewer_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_discord_id: Mapped[int | None] = mapped_column(BigInteger)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = created_at()
