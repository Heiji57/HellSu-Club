"""weekly_report

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weekly_report",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(posted_at IS NULL) = (message_id IS NULL)",
            name=op.f("ck_weekly_report_posted_matches_message"),
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM week_start) = 1", name=op.f("ck_weekly_report_week_start_monday")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weekly_report")),
        sa.UniqueConstraint("week_start", name=op.f("uq_weekly_report_week_start")),
    )


def downgrade() -> None:
    op.drop_table("weekly_report")
