"""All tables (docs/PLAN.md section 3). Statuses are text + CHECK, FKs ON DELETE RESTRICT,
timestamps are timestamptz, *_key columns are paths under DATA_DIR."""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, MetaData, Text, func, text
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
    type_annotation_map = {datetime: DateTime(timezone=True), dict[str, Any]: JSONB, str: Text}


def one_of(column: str, *values: str) -> CheckConstraint:
    return CheckConstraint(f"{column} IN ({', '.join(repr(v) for v in values)})", name=column)


class SourceClip(Base):
    __tablename__ = "source_clips"
    __table_args__ = (
        one_of("origin", "upload", "url"),
        one_of("rights_status", "permission_granted", "none", "own_content"),
        one_of("status", "UPLOADING", "DOWNLOADING", "PROBING", "READY", "FAILED"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    origin: Mapped[str]
    source_url: Mapped[str | None]
    original_filename: Mapped[str | None]
    platform: Mapped[str | None]
    source_creator_handle: Mapped[str | None]
    rights_status: Mapped[str] = mapped_column(server_default="none")
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


class Brand(Base):
    __tablename__ = "brands"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    logo_key: Mapped[str | None]  # PNG, null until uploaded
    default_overlay_config: Mapped[dict[str, Any]] = mapped_column(
        server_default=text("""'{"x": 0.72, "y": 0.06, "w": 0.22, "opacity": 1}'::jsonb""")
    )
    caption_template: Mapped[str | None]
    link: Mapped[str | None]
    auto_approve: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    archived_at: Mapped[datetime | None]


class Render(Base):
    __tablename__ = "renders"
    __table_args__ = (one_of("status", "PENDING", "RENDERING", "READY", "FAILED"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_clip_id: Mapped[int] = mapped_column(ForeignKey("source_clips.id", ondelete="RESTRICT"))
    brand_id: Mapped[int | None] = mapped_column(ForeignKey("brands.id", ondelete="RESTRICT"))  # null = no logo
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


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (one_of("connection_status", "connected", "disconnected"),)

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


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
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
    render_id: Mapped[int] = mapped_column(ForeignKey("renders.id", ondelete="RESTRICT"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"))
    caption: Mapped[str]
    scheduled_for: Mapped[datetime]
    status: Mapped[str] = mapped_column(server_default="DRAFT")
    # sha256(render_id, account_id, scheduled_for), set once at creation, sent as Zernio's Idempotency-Key
    idempotency_key: Mapped[str]
    zernio_media_url: Mapped[str | None]  # exact URL reused on every retry
    zernio_post_id: Mapped[str | None]
    ig_media_id: Mapped[str | None]
    permalink: Mapped[str | None]
    attempt_count: Mapped[int] = mapped_column(server_default="0")
    error_code: Mapped[str | None]
    error_detail: Mapped[dict[str, Any] | None]
    alerted_at: Mapped[datetime | None]
    published_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
