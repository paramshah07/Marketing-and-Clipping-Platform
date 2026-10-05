"""probe_clip, download_clip and render run on the `media` queue (the worker service), everything else on the
default queue (the publisher service): jobs already queued under the old code move over, or the publisher, which
holds users' keys, would run ffmpeg and yt-dlp on them. Data only.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-28 23:00:00
"""

from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # a new database has no Procrastinate tables yet (migrate applies its schema after alembic)
    op.execute("""DO $$ BEGIN IF to_regclass('procrastinate_jobs') IS NOT NULL THEN
        UPDATE procrastinate_jobs SET queue_name = 'media'
        WHERE task_name IN ('probe_clip', 'download_clip', 'render') AND status IN ('todo', 'doing');
    END IF; END $$""")


def downgrade() -> None:
    pass  # the old worker listens on every queue: it runs them where they are
