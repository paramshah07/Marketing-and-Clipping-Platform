"""brands.default_overlay_config default: y 0.06 → 0.16, below Instagram's top bar (the editor's safe
zone starts at 0.14). Existing brands keep what they have.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27 00:30:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('brands', 'default_overlay_config',
                    server_default=sa.text('\'{"x": 0.72, "y": 0.16, "w": 0.22, "opacity": 1}\'::jsonb'))


def downgrade() -> None:
    op.alter_column('brands', 'default_overlay_config',
                    server_default=sa.text('\'{"x": 0.72, "y": 0.06, "w": 0.22, "opacity": 1}\'::jsonb'))
