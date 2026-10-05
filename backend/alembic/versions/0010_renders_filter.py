"""renders.filter: the Instagram-style filter baked into the render (a services.render.FILTERS name), null = none

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-05 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('renders', sa.Column('filter', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('renders', 'filter')
