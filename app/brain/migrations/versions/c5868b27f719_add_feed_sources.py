"""add feed sources

Revision ID: c5868b27f719
Revises: b4c1d2e3f4a5
Create Date: 2026-10-28 23:30:34.365612

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c5868b27f719'
down_revision: Union[str, Sequence[str], None] = 'b4c1d2e3f4a5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'feed_sources',
        sa.Column('source_id', sa.Uuid(), nullable=False),
        sa.Column('url', sa.String(length=1024), nullable=False),
        sa.Column(
            'kind',
            sa.String(length=16),
            server_default='rss',
            nullable=False,
        ),
        sa.Column('title', sa.String(length=256), nullable=True),
        sa.Column(
            'is_active',
            sa.Boolean(),
            server_default=sa.text('true'),
            nullable=False,
        ),
        sa.Column(
            'last_checked_at',
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('rss', 'telegram')",
            name='kind_valid',
        ),
        sa.PrimaryKeyConstraint('source_id'),
        sa.UniqueConstraint('url'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('feed_sources')
