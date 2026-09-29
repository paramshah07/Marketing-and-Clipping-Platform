"""the bot service's view of telegram_bots: its list of bots to run and its health report

Row-level security shows the api's role only the request user's bots; the supervisor runs every user's. Two
SECURITY DEFINER functions, as for the upload sweep (0007): bots_for_supervisor() has a fixed body, and
report_bots() only touches liveness, the username and the one error, of a row whose sealed token is the one
the supervisor ran (ver: a token replaced meanwhile is never marked rejected for its predecessor's 401).

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-29 01:00:00
"""

from alembic import op

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None

LIST = """CREATE FUNCTION bots_for_supervisor()
RETURNS TABLE (id int, user_id int, token_enc bytea, chat_id bigint, ver text)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
  SELECT b.id, b.user_id, b.token_enc, b.chat_id, encode(sha256(b.token_enc), 'hex')
  FROM telegram_bots b JOIN users u ON u.id = b.user_id
  WHERE b.error IS NULL AND u.disabled_at IS NULL ORDER BY b.id
$$"""
REPORT = """CREATE FUNCTION report_bots(report jsonb) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE telegram_bots b SET
    last_seen_at = CASE WHEN r.error IS NULL THEN now() ELSE b.last_seen_at END,
    username = coalesce(r.username, b.username), error = r.error
  FROM jsonb_to_recordset(report) AS r (id int, ver text, username text, error text)
  WHERE b.id = r.id AND b.error IS NULL AND encode(sha256(b.token_enc), 'hex') = r.ver
    AND (r.error IS NULL OR r.error = 'TOKEN_REJECTED')
$$"""


def upgrade() -> None:
    op.execute(LIST)
    op.execute(REPORT)


def downgrade() -> None:
    op.execute("DROP FUNCTION report_bots(jsonb)")
    op.execute("DROP FUNCTION bots_for_supervisor()")
