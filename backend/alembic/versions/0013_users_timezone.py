"""users.timezone: the user's own IANA zone (the web app sends its browser's), null = not known yet; the evening
digest goes out at 20:00 there

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-05 23:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('timezone', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'timezone')
