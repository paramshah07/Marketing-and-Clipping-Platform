"""source_clips.rights_status dropped (no ownership tag anywhere); Customizations: brands.is_default and the
saved_captions / saved_covers libraries, each with at most one default (partial unique uq_<table>_default)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28 12:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None

DEFAULTED = ('brands', 'saved_captions', 'saved_covers')


def upgrade() -> None:
    op.drop_column('source_clips', 'rights_status')  # its CHECK goes with it
    op.add_column('brands', sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_table('saved_captions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_saved_captions'))
    )
    op.create_table('saved_covers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('image_key', sa.Text(), nullable=False),
    sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_saved_covers'))
    )
    for table in DEFAULTED:
        op.create_index(f'uq_{table}_default', table, ['is_default'], unique=True, postgresql_where=sa.text('is_default'))


def downgrade() -> None:
    for table in DEFAULTED:
        op.drop_index(f'uq_{table}_default', table_name=table)
    op.drop_table('saved_covers')  # the files under cover-library/ stay
    op.drop_table('saved_captions')
    op.drop_column('brands', 'is_default')
    op.add_column('source_clips', sa.Column('rights_status', sa.Text(), server_default='none', nullable=False))
    op.create_check_constraint(op.f('ck_source_clips_rights_status'), 'source_clips',
                               "rights_status IN ('permission_granted', 'none', 'own_content')")
