from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, pk


class MemberStatus(StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class Member(Base):
    """헬스클럽 회원. 권한은 Discord 역할로 판단하므로 role 컬럼을 두지 않는다 (ADR-0004)."""

    __tablename__ = "member"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="status"),
        CheckConstraint(
            "(status = 'ACTIVE' AND deactivated_at IS NULL)"
            " OR (status = 'INACTIVE' AND deactivated_at IS NOT NULL)",
            name="deactivated_at_matches_status",
        ),
    )

    id: Mapped[int] = pk()
    discord_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_active(self) -> bool:
        return self.status == MemberStatus.ACTIVE
