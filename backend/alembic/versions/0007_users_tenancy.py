"""users: sessions, telegram_bots, user_id + row-level security on every tenant table

Every tenant table gets user_id, the row-level security policy `tenant` (user_id = app.uid) and composite FKs,
and each user their own defaults. Every existing row is user 1's, the operator ('clipper'; `python -m app.cli
bootstrap` sets its name and password). posts.key_gen: see models.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28 18:00:00
"""

import sqlalchemy as sa
from alembic import op

revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None

OWNED = ('source_clips', 'brands', 'saved_captions', 'saved_covers', 'renders', 'accounts', 'posts')
TARGETS = ('source_clips', 'brands', 'renders', 'accounts')  # UNIQUE (id, user_id), for the composite FKs
COMPOSITE = (('renders', 'source_clip_id', 'source_clips'), ('renders', 'brand_id', 'brands'),
             ('posts', 'render_id', 'renders'), ('posts', 'account_id', 'accounts'))
DEFAULTED = ('brands', 'saved_captions', 'saved_covers')
UID = "coalesce(nullif(current_setting('app.uid', true), '')::int, 1)"  # models.UID
# The api's startup sweep: under RLS it would see no one's uploads. Fixed body, no arguments.
SWEEP = """CREATE FUNCTION abandon_orphan_uploads() RETURNS TABLE (clip_id int, owner int)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE source_clips SET status = 'FAILED', error_code = 'UPLOAD_ABANDONED',
    error_detail = 'the api restarted during the upload'
  WHERE status = 'UPLOADING' RETURNING id, user_id
$$"""


def upgrade() -> None:
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('zernio_key_enc', sa.LargeBinary(), nullable=True),
    sa.Column('zernio_key_last4', sa.Text(), nullable=True),
    sa.Column('zernio_user_id', sa.Text(), nullable=True),
    sa.Column('zernio_email', sa.Text(), nullable=True),
    sa.Column('zernio_name', sa.Text(), nullable=True),
    sa.Column('zernio_key_status', sa.Text(), server_default='none', nullable=False),
    sa.Column('zernio_checked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('zernio_error', sa.Text(), nullable=True),
    sa.Column('zernio_key_gen', sa.Integer(), server_default='0', nullable=False),
    sa.Column('quota_bytes', sa.BigInteger(), nullable=True),
    sa.Column('env_imported_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("username ~ '^[a-z0-9][a-z0-9_.-]{2,31}$'", name=op.f('ck_users_username')),
    sa.CheckConstraint("zernio_key_status IN ('none', 'valid', 'invalid')", name=op.f('ck_users_zernio_key_status')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('username', name=op.f('uq_users_username')),
    sa.UniqueConstraint('zernio_user_id', name=op.f('uq_users_zernio_user_id'))
    )
    op.execute("INSERT INTO users (id, username) VALUES (1, 'clipper')")
    op.execute("SELECT setval('users_id_seq', 1)")  # signups start at 2
    op.create_table('sessions',
    sa.Column('token_sha256', sa.LargeBinary(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('token_sha256', name=op.f('pk_sessions'))
    )
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('telegram_bots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), server_default=sa.text(UID), nullable=False),
    sa.Column('bot_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.Text(), nullable=True),
    sa.Column('token_enc', sa.LargeBinary(), nullable=False),
    sa.Column('chat_id', sa.BigInteger(), nullable=True),
    sa.Column('chat_title', sa.Text(), nullable=True),
    sa.Column('pair_sha256', sa.LargeBinary(), nullable=True),
    sa.Column('pair_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('alerts', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_telegram_bots_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_telegram_bots')),
    sa.UniqueConstraint('bot_id', name=op.f('uq_telegram_bots_bot_id'))
    )
    op.create_index(op.f('ix_telegram_bots_user_id'), 'telegram_bots', ['user_id'], unique=False)

    for t in OWNED:
        # a constant default first (no table rewrite): every existing row is the operator's
        op.add_column(t, sa.Column('user_id', sa.Integer(), server_default='1', nullable=False))
        op.alter_column(t, 'user_id', server_default=sa.text(UID))
        op.create_foreign_key(op.f(f'fk_{t}_user_id_users'), t, 'users', ['user_id'], ['id'], ondelete='RESTRICT')
        op.create_index(op.f(f'ix_{t}_user_id'), t, ['user_id'], unique=False)
    for t in TARGETS:
        op.create_unique_constraint(op.f(f'uq_{t}_id_user_id'), t, ['id', 'user_id'])
    for t, col, ref in COMPOSITE:  # same names: a row can only point at a row of its own user
        op.drop_constraint(op.f(f'fk_{t}_{col}_{ref}'), t, type_='foreignkey')
        op.create_foreign_key(op.f(f'fk_{t}_{col}_{ref}'), t, ref, [col, 'user_id'], ['id', 'user_id'], ondelete='RESTRICT')
    for t in DEFAULTED:  # one default per user, same names (the api's 409 message)
        op.drop_index(f'uq_{t}_default', table_name=t)
        op.create_index(f'uq_{t}_default', t, ['user_id'], unique=True, postgresql_where=sa.text('is_default'))
    op.add_column('posts', sa.Column('key_gen', sa.Integer(), nullable=True))
    op.execute("UPDATE posts SET key_gen = 1 WHERE first_post_at IS NOT NULL")  # the .env key is user 1's gen 1

    # FORCE: the owner too (it is the superuser, which bypasses RLS anyway; this keeps it so if that changes)
    for t in (*OWNED, 'telegram_bots'):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant ON {t} USING (user_id = nullif(current_setting('app.uid', true), '')::int)")
    op.execute(SWEEP)


def downgrade() -> None:
    op.execute("DROP FUNCTION abandon_orphan_uploads()")
    for t in OWNED:
        op.execute(f"DROP POLICY tenant ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    op.drop_column('posts', 'key_gen')
    for t in DEFAULTED:
        op.drop_index(f'uq_{t}_default', table_name=t)
        op.execute(f"UPDATE {t} SET is_default = false WHERE is_default AND user_id <> 1")  # one default left
        op.create_index(f'uq_{t}_default', t, ['is_default'], unique=True, postgresql_where=sa.text('is_default'))
    for t, col, ref in COMPOSITE:
        op.drop_constraint(op.f(f'fk_{t}_{col}_{ref}'), t, type_='foreignkey')
        op.create_foreign_key(op.f(f'fk_{t}_{col}_{ref}'), t, ref, [col], ['id'], ondelete='RESTRICT')
    for t in TARGETS:
        op.drop_constraint(op.f(f'uq_{t}_id_user_id'), t, type_='unique')
    for t in OWNED:
        op.drop_column(t, 'user_id')  # its FK and index go with it
    op.drop_table('telegram_bots')
    op.drop_table('sessions')
    op.drop_table('users')
