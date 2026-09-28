"""users.notifications_paused

Revision ID: 7a1c2e9b4d10
Revises: 30c736292a40
Create Date: 2026-09-27 12:00:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "7a1c2e9b4d10"
down_revision: Union[str, None] = "30c736292a40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # IF NOT EXISTS — чтобы миграция не падала, если колонку уже добавил бот при старте
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS notifications_paused BOOLEAN NOT NULL DEFAULT false"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS notifications_paused")
