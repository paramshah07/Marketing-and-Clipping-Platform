"""posts.first_post_at: when the post's first POST /v1/posts went out (the 20 h replay guard's clock)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26 20:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('posts', sa.Column('first_post_at', sa.DateTime(timezone=True), nullable=True))
    # older rows: a committed media URL may have been POSTed; its scheduled_for is the best clock we have
    op.execute("UPDATE posts SET first_post_at = scheduled_for WHERE zernio_media_url IS NOT NULL")


def downgrade() -> None:
    op.drop_column('posts', 'first_post_at')
