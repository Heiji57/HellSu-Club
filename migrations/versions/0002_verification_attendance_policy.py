"""verification, verification_review, attendance, club_policy

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "club_policy",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("weekly_target", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("created_by_discord_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM effective_from) = 1",
            name=op.f("ck_club_policy_effective_from_monday"),
        ),
        sa.CheckConstraint(
            "weekly_target BETWEEN 1 AND 7", name=op.f("ck_club_policy_weekly_target")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_club_policy")),
    )
    op.create_table(
        "verification",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("interaction_id", sa.BigInteger(), nullable=False),
        sa.Column("channel_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workout_date", sa.Date(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=True),
        sa.Column("image_mime", sa.String(length=32), nullable=True),
        sa.Column("image_bytes", sa.Integer(), nullable=True),
        sa.Column("image_sha256", sa.String(length=64), nullable=True),
        sa.Column("image_phash", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'REVIEW_REQUIRED') = (decided_at IS NULL)",
            name=op.f("ck_verification_decided_at_matches_status"),
        ),
        sa.CheckConstraint(
            "status IN ('APPROVED', 'REVIEW_REQUIRED', 'REJECTED')",
            name=op.f("ck_verification_status"),
        ),
        sa.CheckConstraint(
            "(image_sha256 IS NULL AND image_phash IS NULL"
            " AND image_mime IS NULL AND image_bytes IS NULL AND image_path IS NULL)"
            " OR (image_sha256 IS NOT NULL AND image_phash IS NOT NULL"
            " AND image_mime IS NOT NULL AND image_bytes IS NOT NULL)",
            name=op.f("ck_verification_image_fields"),
        ),
        sa.CheckConstraint(
            "content IS NOT NULL OR image_sha256 IS NOT NULL",
            name=op.f("ck_verification_has_content"),
        ),
        sa.ForeignKeyConstraint(
            ["member_id"], ["member.id"], name=op.f("fk_verification_member_id_member")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification")),
        sa.UniqueConstraint("interaction_id", name=op.f("uq_verification_interaction_id")),
    )
    op.create_index("ix_verification_image_sha256", "verification", ["image_sha256"], unique=False)
    op.create_index(
        "ix_verification_member_submitted",
        "verification",
        ["member_id", "submitted_at"],
        unique=False,
    )
    op.create_index(
        "ix_verification_review_required",
        "verification",
        ["submitted_at"],
        unique=False,
        postgresql_where=sa.text("status = 'REVIEW_REQUIRED'"),
    )
    op.create_index(
        "uq_verification_member_day_active",
        "verification",
        ["member_id", "workout_date"],
        unique=True,
        postgresql_where=sa.text("status IN ('APPROVED', 'REVIEW_REQUIRED')"),
    )
    op.create_table(
        "attendance",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("member_id", sa.BigInteger(), nullable=False),
        sa.Column("verification_id", sa.BigInteger(), nullable=False),
        sa.Column("workout_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'ACTIVE') = (revoked_at IS NULL)",
            name=op.f("ck_attendance_revoked_at_matches_status"),
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'REVOKED')", name=op.f("ck_attendance_status")),
        sa.ForeignKeyConstraint(
            ["member_id"], ["member.id"], name=op.f("fk_attendance_member_id_member")
        ),
        sa.ForeignKeyConstraint(
            ["verification_id"],
            ["verification.id"],
            name=op.f("fk_attendance_verification_id_verification"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_attendance")),
        sa.UniqueConstraint("verification_id", name=op.f("uq_attendance_verification_id")),
    )
    op.create_index("ix_attendance_workout_date", "attendance", ["workout_date"], unique=False)
    op.create_index(
        "uq_attendance_member_day_active",
        "attendance",
        ["member_id", "workout_date"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_table(
        "verification_review",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("verification_id", sa.BigInteger(), nullable=False),
        sa.Column("reviewer_type", sa.String(length=16), nullable=False),
        sa.Column("actor_discord_id", sa.BigInteger(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(reviewer_type = 'RULE') = (actor_discord_id IS NULL)",
            name=op.f("ck_verification_review_actor_matches_type"),
        ),
        sa.CheckConstraint(
            "decision IN ('APPROVE', 'REVIEW', 'REJECT', 'REVOKE')",
            name=op.f("ck_verification_review_decision"),
        ),
        sa.CheckConstraint(
            "reviewer_type IN ('RULE', 'ADMIN')", name=op.f("ck_verification_review_reviewer_type")
        ),
        sa.ForeignKeyConstraint(
            ["verification_id"],
            ["verification.id"],
            name=op.f("fk_verification_review_verification_id_verification"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_review")),
    )
    op.create_index(
        op.f("ix_verification_review_verification_id"),
        "verification_review",
        ["verification_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("verification_review")
    op.drop_table("attendance")
    op.drop_table("verification")
    op.drop_table("club_policy")
