"""add error events

Revision ID: a1f4c9d2e7b3
Revises: c5868b27f719
Create Date: 2026-10-29 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f4c9d2e7b3'
down_revision: Union[str, Sequence[str], None] = 'c5868b27f719'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'error_events',
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('level', sa.String(length=16), nullable=False),
        sa.Column('logger', sa.String(length=255), nullable=False),
        sa.Column('module', sa.String(length=255), nullable=False),
        sa.Column('function', sa.String(length=255), nullable=False),
        sa.Column('exception_type', sa.String(length=255), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('traceback', sa.Text(), nullable=True),
        sa.Column(
            'count',
            sa.Integer(),
            server_default=sa.text('1'),
            nullable=False,
        ),
        sa.Column(
            'first_seen',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'last_seen',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('fingerprint'),
    )
    op.create_index(
        op.f('ix_error_events_last_seen'),
        'error_events',
        ['last_seen'],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_error_events_last_seen'), table_name='error_events')
    op.drop_table('error_events')
