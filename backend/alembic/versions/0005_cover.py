"""renders.cover_key (the Reel cover JPEG) and posts.zernio_cover_url (its Zernio upload, persisted before the
first POST like zernio_media_url)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('renders', sa.Column('cover_key', sa.Text(), nullable=True))
    op.add_column('posts', sa.Column('zernio_cover_url', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('posts', 'zernio_cover_url')
    op.drop_column('renders', 'cover_key')
