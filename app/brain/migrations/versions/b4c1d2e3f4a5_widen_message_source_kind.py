"""widen message source_kind

Revision ID: b4c1d2e3f4a5
Revises: 9b7e4c1a2f60
Create Date: 2026-10-28 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b4c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = '9b7e4c1a2f60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'messages',
        'source_kind',
        existing_type=sa.String(length=8),
        type_=sa.String(length=32),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(
        "UPDATE messages SET source_kind = 'video' "
        "WHERE source_kind IN ('animation', 'video_note')"
    )
    op.alter_column(
        'messages',
        'source_kind',
        existing_type=sa.String(length=32),
        type_=sa.String(length=8),
        existing_nullable=False,
    )
