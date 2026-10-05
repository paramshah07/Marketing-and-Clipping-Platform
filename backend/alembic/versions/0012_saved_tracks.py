"""saved_tracks (Customizations: songs uploaded to mix into renders), one default per user, row-level security as
0007's tables; renders.music: the track mixed into a render and its volumes, null = the clip's own sound

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-05 14:00:00
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None

UID = "coalesce(nullif(current_setting('app.uid', true), '')::int, 1)"  # models.UID


def upgrade() -> None:
    op.create_table('saved_tracks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('audio_key', sa.Text(), nullable=False),
    sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('user_id', sa.Integer(), server_default=sa.text(UID), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_saved_tracks_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_saved_tracks'))
    )
    op.create_index(op.f('ix_saved_tracks_user_id'), 'saved_tracks', ['user_id'], unique=False)
    op.create_index('uq_saved_tracks_default', 'saved_tracks', ['user_id'], unique=True, postgresql_where=sa.text('is_default'))
    op.execute("ALTER TABLE saved_tracks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE saved_tracks FORCE ROW LEVEL SECURITY")
    op.execute("CREATE POLICY tenant ON saved_tracks USING (user_id = nullif(current_setting('app.uid', true), '')::int)")
    op.add_column('renders', sa.Column('music', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    op.drop_column('renders', 'music')
    op.drop_table('saved_tracks')  # its policy and indexes go with it
