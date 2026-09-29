"""All tables (docs/PLAN.md section 3). Statuses are text + CHECK, FKs ON DELETE RESTRICT,
timestamps are timestamptz, *_key columns are paths under DATA_DIR.

Tenancy (migration 0007): every Owned table has user_id and the Postgres row-level security policy `tenant`
(user_id = app.uid). The api connects as clipper_app, so it sees and writes only the signed-in user's rows
(app.core.db sets app.uid per transaction); worker, publisher and CLI are the superuser and bypass it."""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    MetaData,
    Text,
    UniqueConstraint,
    Update,
    func,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(table_name)s_%(column_0_N_name)s",
            "uq": "uq_%(table_name)s_%(column_0_N_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )
    type_annotation_map = {datetime: DateTime(timezone=True), dict[str, Any]: JSONB, str: Text, bytes: LargeBinary}


def one_of(column: str, *values: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=column)


def default_index(table: str) -> Index:
    """At most one row of the table per user is its default (what the Editor preselects)."""
    return Index(f"uq_{table}_default", "user_id", unique=True, postgresql_where=text("is_default"))


# The api's inserts name no user: the column takes the transaction's app.uid. Missing (a superuser: worker,
# CLI, an api from before 0007) it is 1, the operator; under RLS the policy then refuses the row (fail closed).
UID = text("coalesce(nullif(current_setting('app.uid', true), '')::int, 1)")


class Owned:
    """A tenant table: its rows belong to one user (RLS policy `tenant`, migration 0007)."""

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), server_default=UID, index=True)


def owned_by(column: str, table: str) -> ForeignKeyConstraint:
    """(column, user_id) -> table (id, user_id): a row can only point at a row of its own user."""
    return ForeignKeyConstraint([column, "user_id"], [f"{table}.id", f"{table}.user_id"], ondelete="RESTRICT")


def cas(model, id: int, from_statuses: Iterable[str], **values) -> Update:
    """Compare-and-set: UPDATE model SET values WHERE id = :id AND status IN from_statuses.
    Execute it and check rowcount == 1; 0 means another writer got there first (or the row is gone)."""
    return update(model).where(model.id == id, model.status.in_(list(from_statuses))).values(**values)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("username ~ '^[a-z0-9][a-z0-9_.-]{2,31}$'", name="username"),
        one_of("zernio_key_status", "none", "valid", "invalid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(unique=True)  # lower-case; login is case-insensitive
    password_hash: Mapped[str | None]  # bcrypt; null: can't log in (user 1 until bootstrap sets it)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    disabled_at: Mapped[datetime | None]
    # the user's own Zernio API key, sealed (app.core.secrets); zernio_* come from GET /v1/auth/verify
    zernio_key_enc: Mapped[bytes | None]
    zernio_key_last4: Mapped[str | None]
    zernio_user_id: Mapped[str | None] = mapped_column(unique=True)
    zernio_email: Mapped[str | None]
    zernio_name: Mapped[str | None]
    zernio_key_status: Mapped[str] = mapped_column(server_default="none")
    zernio_checked_at: Mapped[datetime | None]
    zernio_error: Mapped[str | None]
    zernio_key_gen: Mapped[int] = mapped_column(server_default="0")  # +1 on every key change (posts.key_gen)
    quota_bytes: Mapped[int | None] = mapped_column(BigInteger)  # storage cap; null = unlimited
    env_imported_at: Mapped[datetime | None]  # the one-shot .env import into user 1 ran


class AuthSession(Base):  # a signed-in browser: the clipper_session cookie's sha256
    __tablename__ = "sessions"

    token_sha256: Mapped[bytes] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TelegramBot(Owned, Base):  # a user's @BotFather bot, run by the bot service's supervisor
    __tablename__ = "telegram_bots"

    id: Mapped[int] = mapped_column(primary_key=True)
    bot_id: Mapped[int] = mapped_column(BigInteger, unique=True)  # the token's numeric prefix: one poller per bot
    username: Mapped[str | None]
    token_enc: Mapped[bytes]  # sealed
    chat_id: Mapped[int | None] = mapped_column(BigInteger)  # null: waiting for /start <code>
    chat_title: Mapped[str | None]
    pair_sha256: Mapped[bytes | None]
    pair_expires_at: Mapped[datetime | None]
    alerts: Mapped[bool] = mapped_column(server_default=text("true"))
    error: Mapped[str | None]  # TOKEN_REJECTED: the supervisor stops running it
    last_seen_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SourceClip(Owned, Base):
    __tablename__ = "source_clips"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),  # target of owned_by
        one_of("origin", "upload", "url"),
        one_of("status", "UPLOADING", "DOWNLOADING", "PROBING", "READY", "FAILED"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str]
    source_url: Mapped[str | None]
    original_filename: Mapped[str | None]
    platform: Mapped[str | None]
    source_creator_handle: Mapped[str | None]
    status: Mapped[str]
    raw_key: Mapped[str | None]
    thumbnail_key: Mapped[str | None]
    content_type: Mapped[str | None]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    # probe results (display dims after rotation, avg_frame_rate)
    duration_s: Mapped[float | None]
    width: Mapped[int | None]
    height: Mapped[int | None]
    fps: Mapped[float | None]
    video_codec: Mapped[str | None]
    color_transfer: Mapped[str | None]
    has_audio: Mapped[bool | None]
    has_watermark: Mapped[bool | None]
    error_code: Mapped[str | None]
    error_detail: Mapped[str | None]
    uploaded_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Brand(Owned, Base):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("id", "user_id"), default_index("brands"))

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    logo_key: Mapped[str | None]  # PNG, null until uploaded
    default_overlay_config: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("""'{"x": 0.72, "y": 0.16, "w": 0.22, "opacity": 1}'::jsonb""")
    )
    caption_template: Mapped[str | None]
    link: Mapped[str | None]
    auto_approve: Mapped[bool] = mapped_column(server_default=text("false"))
    is_default: Mapped[bool] = mapped_column(server_default=text("false"))  # never while archived
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    archived_at: Mapped[datetime | None]


class SavedCaption(Owned, Base):  # Customizations: a caption the Editor can fill in
    __tablename__ = "saved_captions"
    __table_args__ = (default_index("saved_captions"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    text: Mapped[str]
    is_default: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class SavedCover(Owned, Base):  # Customizations: a Reel cover the Editor copies onto a render (renders.cover_key)
    __tablename__ = "saved_covers"
    __table_args__ = (default_index("saved_covers"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    image_key: Mapped[str]  # cover-library/{id}-{hex8}.jpg, a JPEG (1080x1920 from the web app)
    is_default: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Render(Owned, Base):
    __tablename__ = "renders"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        owned_by("source_clip_id", "source_clips"),
        owned_by("brand_id", "brands"),  # MATCH SIMPLE: a null brand skips the check
        one_of("status", "PENDING", "RENDERING", "READY", "FAILED"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_clip_id: Mapped[int]
    brand_id: Mapped[int | None]  # null = no logo
    overlay_config: Mapped[dict[str, Any] | None]  # fractions of the 1080x1920 output
    crop_config: Mapped[dict[str, Any] | None]  # fractions of the source frame
    caption: Mapped[str | None]
    status: Mapped[str] = mapped_column(server_default="PENDING")
    output_key: Mapped[str | None]
    thumbnail_key: Mapped[str | None]
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_s: Mapped[float | None]
    error_code: Mapped[str | None]
    ffmpeg_log: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    completed_at: Mapped[datetime | None]
    superseded_at: Mapped[datetime | None]  # 'Re-render and retry' replaced it: never back in the Ready tray
    cover_key: Mapped[str | None]  # 1080x1920 JPEG Reel cover (Zernio instagramThumbnail); null = Instagram's pick


class Account(Owned, Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("id", "user_id"), one_of("connection_status", "connected", "disconnected"))

    id: Mapped[int] = mapped_column(primary_key=True)
    zernio_account_id: Mapped[str] = mapped_column(unique=True)
    zernio_profile_id: Mapped[str]
    username: Mapped[str]
    avatar_url: Mapped[str | None]
    connection_status: Mapped[str] = mapped_column(server_default="connected")
    posting_slots: Mapped[dict[str, Any]] = mapped_column(server_default=text("""'{"times": []}'::jsonb"""))
    daily_cap: Mapped[int] = mapped_column(server_default="10")
    timezone: Mapped[str]
    min_gap_minutes: Mapped[int] = mapped_column(server_default="30")
    last_alerts: Mapped[dict[str, Any]] = mapped_column(server_default=text("'{}'::jsonb"))
    connected_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_publish_at: Mapped[datetime | None]
    disabled_at: Mapped[datetime | None]


class Post(Owned, Base):
    __tablename__ = "posts"
    __table_args__ = (
        owned_by("render_id", "renders"),
        owned_by("account_id", "accounts"),
        one_of("status", "DRAFT", "SCHEDULED", "PUBLISHING", "PUBLISHED", "FAILED", "DEAD_LETTER", "CANCELLED"),
        Index("ix_posts_status_scheduled_for", "status", "scheduled_for"),
        Index("ix_posts_account_id_scheduled_for", "account_id", "scheduled_for"),
        Index(
            "uq_posts_idempotency_key_live",
            "idempotency_key",
            unique=True,
            postgresql_where=text("status NOT IN ('CANCELLED', 'FAILED', 'DEAD_LETTER')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    render_id: Mapped[int]
    account_id: Mapped[int]
    caption: Mapped[str]
    scheduled_for: Mapped[datetime]
    status: Mapped[str] = mapped_column(server_default="DRAFT")
    # sha256(render_id, account_id, scheduled_for), set once at creation, sent as Zernio's Idempotency-Key
    idempotency_key: Mapped[str]
    zernio_media_url: Mapped[str | None]  # exact URL reused on every retry
    zernio_cover_url: Mapped[str | None]  # the render's cover uploaded to Zernio, same rules as zernio_media_url
    zernio_post_id: Mapped[str | None]
    # committed just before the first POST /v1/posts with this key: set means a post may be live. The 20 h
    # no-re-POST guard counts from it (never from scheduled_for, which reslots move). Cleared only with a new key.
    first_post_at: Mapped[datetime | None]
    key_gen: Mapped[int | None]  # users.zernio_key_gen when first_post_at was set: a replay needs the same key
    ig_media_id: Mapped[str | None]
    permalink: Mapped[str | None]
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    error_code: Mapped[str | None]
    error_detail: Mapped[dict[str, Any] | None]
    alerted_at: Mapped[datetime | None]
    published_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
