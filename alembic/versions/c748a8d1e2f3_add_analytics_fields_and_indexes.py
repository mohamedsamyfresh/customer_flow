"""add analytics fields and indexes
Revision ID: c748a8d1e2f3
Revises: 13d0ee0c40e5
Create Date: 2026-08-26 10:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c748a8d1e2f3'
down_revision: str | Sequence[str] | None = '13d0ee0c40e5'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('entries', sa.Column('branch_id', sa.String(length=50), nullable=True))
    op.add_column('entries', sa.Column('camera_id', sa.String(length=50), nullable=True))
    op.create_index(op.f('ix_entries_branch_id'), 'entries', ['branch_id'], unique=False)
    op.create_index(op.f('ix_entries_camera_id'), 'entries', ['camera_id'], unique=False)
    op.create_index(op.f('ix_entries_entry_time'), 'entries', ['entry_time'], unique=False)
    op.create_index(op.f('ix_entries_entry_count'), 'entries', ['entry_count'], unique=False)
    op.create_index(op.f('ix_entries_exit_time'), 'entries', ['exit_time'], unique=False)
    op.create_index(op.f('ix_entries_exit_count'), 'entries', ['exit_count'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_entries_exit_count'), table_name='entries')
    op.drop_index(op.f('ix_entries_exit_time'), table_name='entries')
    op.drop_index(op.f('ix_entries_entry_count'), table_name='entries')
    op.drop_index(op.f('ix_entries_entry_time'), table_name='entries')
    op.drop_index(op.f('ix_entries_camera_id'), table_name='entries')
    op.drop_index(op.f('ix_entries_branch_id'), table_name='entries')
    op.drop_column('entries', 'camera_id')
    op.drop_column('entries', 'branch_id')
