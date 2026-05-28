"""smart notifications: dedup_key, severity, status, deferred_until

Revision ID: 20260528_0009
Revises: 20260528_0008
Create Date: 2026-05-28
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260528_0009"
down_revision: str | None = "20260528_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notifications",
        sa.Column("dedup_key", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "notifications",
        sa.Column("severity", sa.String(length=20), nullable=False, server_default="medium"),
    )
    op.add_column(
        "notifications",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="delivered"),
    )
    op.add_column(
        "notifications",
        sa.Column("deferred_until", sa.DateTime(timezone=True), nullable=True),
    )
    # Снимаем server_default — приложение должно явно указывать severity/status
    # на новых вставках; существующие строки уже заполнены дефолтами.
    op.alter_column("notifications", "severity", server_default=None)
    op.alter_column("notifications", "status", server_default=None)

    op.create_index(
        "uq_notifications_dedup_key",
        "notifications",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_notifications_dedup_key", table_name="notifications")
    op.drop_column("notifications", "deferred_until")
    op.drop_column("notifications", "status")
    op.drop_column("notifications", "severity")
    op.drop_column("notifications", "dedup_key")
