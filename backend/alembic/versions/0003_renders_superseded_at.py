"""renders.superseded_at: set when 'Re-render and retry' moves the post to a new render, so the old
(rejected or maybe-live) render never shows in the Ready to schedule tray again

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26 23:50:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('renders', sa.Column('superseded_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('renders', 'superseded_at')
