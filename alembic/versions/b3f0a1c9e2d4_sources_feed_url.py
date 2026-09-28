"""sources.feed_url / added_by — RSS-источники, добавленные из бота

Revision ID: b3f0a1c9e2d4
Revises: 7a1c2e9b4d10
Create Date: 2026-09-27 21:45:00

"""
from typing import Sequence, Union

from alembic import op

revision: str = "b3f0a1c9e2d4"
down_revision: Union[str, None] = "7a1c2e9b4d10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS feed_url VARCHAR(1024)")
    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS added_by BIGINT")
    op.execute("ALTER TABLE sources ADD COLUMN IF NOT EXISTS feed_kind VARCHAR(16)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_sources_feed_url ON sources (feed_url)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_sources_feed_url")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS feed_kind")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS added_by")
    op.execute("ALTER TABLE sources DROP COLUMN IF EXISTS feed_url")
