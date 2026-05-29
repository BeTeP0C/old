"""import logs: store raw content for download

Revision ID: 20260528_0012
Revises: 20260528_0011
Create Date: 2026-05-29
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260528_0012"
down_revision: str | None = "20260528_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # content — исходный текст загруженного файла (для скачивания из истории).
    op.add_column("import_logs", sa.Column("content", sa.Text(), nullable=True))
    # has_file — есть ли что скачивать (дешёвый флаг для списка, content deferred).
    op.add_column(
        "import_logs",
        sa.Column("has_file", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("import_logs", "has_file")
    op.drop_column("import_logs", "content")
