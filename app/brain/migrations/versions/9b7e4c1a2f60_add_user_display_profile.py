"""add user display profile

Revision ID: 9b7e4c1a2f60
Revises: 03a6ff552596
Create Date: 2026-10-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b7e4c1a2f60'
down_revision: Union[str, Sequence[str], None] = '03a6ff552596'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'users',
        sa.Column('display_name', sa.String(length=128), nullable=True),
    )
    op.add_column(
        'users',
        sa.Column('tg_username', sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('users', 'tg_username')
    op.drop_column('users', 'display_name')
