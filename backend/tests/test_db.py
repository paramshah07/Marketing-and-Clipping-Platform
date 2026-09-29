from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Account, Brand, Post, Render, SourceClip
from conftest import ALEMBIC

TABLES = {"source_clips", "brands", "renders", "accounts", "posts", "saved_captions", "saved_covers", "users", "sessions",
          "telegram_bots"}


def make_post(s: Session, idempotency_key: str) -> Post:
    """One row in every table, flushed."""
    clip = SourceClip(origin="upload", status="READY", raw_key="raw/a.mp4", duration_s=8.0)
    brand = Brand(name="Acme")
    account = Account(
        zernio_account_id=str(uuid4()), zernio_profile_id="p1", username="me", timezone="Europe/London"
    )
    s.add_all([clip, brand, account])
    s.flush()
    render = Render(source_clip_id=clip.id, brand_id=brand.id, overlay_config=brand.default_overlay_config)
    s.add(render)
    s.flush()
    post = Post(
        render_id=render.id,
        account_id=account.id,
        caption="hi",
        scheduled_for=datetime(2026, 10, 1, 9, tzinfo=UTC),
        idempotency_key=idempotency_key,
    )
    s.add(post)
    s.flush()
    return post


def test_migration_downgrade_and_upgrade(db):
    command.downgrade(ALEMBIC, "base")
    names = set(inspect(db).get_table_names())
    assert TABLES.isdisjoint(names)
    assert "procrastinate_jobs" in names  # the downgrade leaves Procrastinate's tables alone
    command.upgrade(ALEMBIC, "head")
    assert TABLES <= set(inspect(db).get_table_names())
    command.check(ALEMBIC)  # raises if the models and the migration disagree


def test_insert_every_table(db):
    with Session(db) as s:
        post = make_post(s, "k-insert")
        s.commit()
        render = s.get(Render, post.render_id)
        assert render.overlay_config == {"x": 0.72, "y": 0.16, "w": 0.22, "opacity": 1}
        assert render.status == "PENDING"
        assert post.status == "DRAFT" and post.attempt_count == 0
        assert post.created_at.tzinfo is not None
        assert s.get(Account, post.account_id).posting_slots == {"times": []}


def test_check_constraint_rejects_bad_status(db):
    with Session(db) as s:
        post = make_post(s, "k-check")
        post.status = "LIVE"
        with pytest.raises(IntegrityError, match="ck_posts_status"):
            s.flush()


def test_idempotency_key_unique_only_while_post_is_live(db):
    with Session(db) as s:
        first = make_post(s, "k-dup")
        s.commit()
        with pytest.raises(IntegrityError, match="uq_posts_idempotency_key_live"):
            make_post(s, "k-dup")
        s.rollback()
        first.status = "CANCELLED"
        s.commit()
        second = make_post(s, "k-dup")
        s.commit()
        assert second.id != first.id
