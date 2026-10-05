"""posts.music: the Instagram catalog track a Reel goes out with (Zernio audioConfiguration), null = none

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-05 13:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('posts', sa.Column('music', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('posts', 'music')
