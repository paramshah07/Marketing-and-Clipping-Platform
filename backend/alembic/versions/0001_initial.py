"""initial: all Clipper tables (docs/PLAN.md section 3)

Revision ID: 0001
Revises:
Create Date: 2026-09-26 08:32:10.150463
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('zernio_account_id', sa.Text(), nullable=False),
    sa.Column('zernio_profile_id', sa.Text(), nullable=False),
    sa.Column('username', sa.Text(), nullable=False),
    sa.Column('avatar_url', sa.Text(), nullable=True),
    sa.Column('connection_status', sa.Text(), server_default='connected', nullable=False),
    sa.Column('posting_slots', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text('\'{"times": []}\'::jsonb'), nullable=False),
    sa.Column('daily_cap', sa.Integer(), server_default='10', nullable=False),
    sa.Column('timezone', sa.Text(), nullable=False),
    sa.Column('min_gap_minutes', sa.Integer(), server_default='30', nullable=False),
    sa.Column('last_alerts', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('connected_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_publish_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("connection_status IN ('connected', 'disconnected')", name=op.f('ck_accounts_connection_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accounts')),
    sa.UniqueConstraint('zernio_account_id', name=op.f('uq_accounts_zernio_account_id'))
    )
    op.create_table('brands',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('logo_key', sa.Text(), nullable=True),
    sa.Column('default_overlay_config', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text('\'{"x": 0.72, "y": 0.06, "w": 0.22, "opacity": 1}\'::jsonb'), nullable=False),
    sa.Column('caption_template', sa.Text(), nullable=True),
    sa.Column('link', sa.Text(), nullable=True),
    sa.Column('auto_approve', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_brands'))
    )
    op.create_table('source_clips',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('origin', sa.Text(), nullable=False),
    sa.Column('source_url', sa.Text(), nullable=True),
    sa.Column('original_filename', sa.Text(), nullable=True),
    sa.Column('platform', sa.Text(), nullable=True),
    sa.Column('source_creator_handle', sa.Text(), nullable=True),
    sa.Column('rights_status', sa.Text(), server_default='none', nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('raw_key', sa.Text(), nullable=True),
    sa.Column('thumbnail_key', sa.Text(), nullable=True),
    sa.Column('content_type', sa.Text(), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('duration_s', sa.Double(), nullable=True),
    sa.Column('width', sa.Integer(), nullable=True),
    sa.Column('height', sa.Integer(), nullable=True),
    sa.Column('fps', sa.Double(), nullable=True),
    sa.Column('video_codec', sa.Text(), nullable=True),
    sa.Column('color_transfer', sa.Text(), nullable=True),
    sa.Column('has_audio', sa.Boolean(), nullable=True),
    sa.Column('has_watermark', sa.Boolean(), nullable=True),
    sa.Column('error_code', sa.Text(), nullable=True),
    sa.Column('error_detail', sa.Text(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("origin IN ('upload', 'url')", name=op.f('ck_source_clips_origin')),
    sa.CheckConstraint("rights_status IN ('permission_granted', 'none', 'own_content')", name=op.f('ck_source_clips_rights_status')),
    sa.CheckConstraint("status IN ('UPLOADING', 'DOWNLOADING', 'PROBING', 'READY', 'FAILED')", name=op.f('ck_source_clips_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_clips'))
    )
    op.create_table('renders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source_clip_id', sa.Integer(), nullable=False),
    sa.Column('brand_id', sa.Integer(), nullable=True),
    sa.Column('overlay_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('crop_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('caption', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default='PENDING', nullable=False),
    sa.Column('output_key', sa.Text(), nullable=True),
    sa.Column('thumbnail_key', sa.Text(), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=True),
    sa.Column('duration_s', sa.Double(), nullable=True),
    sa.Column('error_code', sa.Text(), nullable=True),
    sa.Column('ffmpeg_log', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('PENDING', 'RENDERING', 'READY', 'FAILED')", name=op.f('ck_renders_status')),
    sa.ForeignKeyConstraint(['brand_id'], ['brands.id'], name=op.f('fk_renders_brand_id_brands'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_clip_id'], ['source_clips.id'], name=op.f('fk_renders_source_clip_id_source_clips'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_renders'))
    )
    op.create_table('posts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('render_id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('caption', sa.Text(), nullable=False),
    sa.Column('scheduled_for', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Text(), server_default='DRAFT', nullable=False),
    sa.Column('idempotency_key', sa.Text(), nullable=False),
    sa.Column('zernio_media_url', sa.Text(), nullable=True),
    sa.Column('zernio_post_id', sa.Text(), nullable=True),
    sa.Column('ig_media_id', sa.Text(), nullable=True),
    sa.Column('permalink', sa.Text(), nullable=True),
    sa.Column('attempt_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('error_code', sa.Text(), nullable=True),
    sa.Column('error_detail', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('alerted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('DRAFT', 'SCHEDULED', 'PUBLISHING', 'PUBLISHED', 'FAILED', 'DEAD_LETTER', 'CANCELLED')", name=op.f('ck_posts_status')),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_posts_account_id_accounts'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['render_id'], ['renders.id'], name=op.f('fk_posts_render_id_renders'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_posts'))
    )
    op.create_index('ix_posts_account_id_scheduled_for', 'posts', ['account_id', 'scheduled_for'], unique=False)
    op.create_index('ix_posts_status_scheduled_for', 'posts', ['status', 'scheduled_for'], unique=False)
    op.create_index('uq_posts_idempotency_key_live', 'posts', ['idempotency_key'], unique=True, postgresql_where=sa.text("status NOT IN ('CANCELLED', 'FAILED', 'DEAD_LETTER')"))


def downgrade() -> None:
    op.drop_table('posts')
    op.drop_table('renders')
    op.drop_table('source_clips')
    op.drop_table('brands')
    op.drop_table('accounts')
