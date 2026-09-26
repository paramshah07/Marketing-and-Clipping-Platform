"""API request/response bodies. *_key columns go out as *_url (served under /media)."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, computed_field, model_validator

from app.services import storage

RightsStatus = Literal["permission_granted", "none", "own_content"]


def _url(key: str | None) -> str | None:
    return storage.url_for(key) if key else None


class OverlayConfig(BaseModel):
    """Logo box as fractions of the 1080x1920 output: top-left x, y and width (height follows the logo,
    so only y < 1 is checked here)."""

    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, lt=1)
    w: float = Field(ge=0.01, le=1)  # not round(w * 1080) == 0: ffmpeg's scale=0 keeps the logo's own width
    opacity: float = Field(1, ge=0, le=1)

    @model_validator(mode="after")
    def inside_frame(self):
        if self.x + self.w > 1 + 1e-6:
            raise ValueError("logo must stay inside the frame (x + w <= 1)")
        return self


class CropConfig(BaseModel):
    """Crop rectangle as fractions of the source frame (after autorotate)."""

    x: float = Field(ge=0, lt=1)
    y: float = Field(ge=0, lt=1)
    w: float = Field(gt=0, le=1)
    h: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def inside_frame(self):
        if self.x + self.w > 1 + 1e-6 or self.y + self.h > 1 + 1e-6:
            raise ValueError("crop must stay inside the frame (x + w <= 1, y + h <= 1)")
        return self


class ClipFromUrl(BaseModel):
    url: HttpUrl
    rights_status: RightsStatus
    source_creator_handle: str | None = None


class ClipPatch(BaseModel):  # omit a field to leave it unchanged
    rights_status: RightsStatus = None
    source_creator_handle: str | None = None
    has_watermark: bool | None = None


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    origin: str
    status: str
    error_code: str | None
    error_detail: str | None
    source_url: str | None
    original_filename: str | None
    platform: str | None
    source_creator_handle: str | None
    rights_status: RightsStatus
    has_watermark: bool | None
    content_type: str | None
    size_bytes: int | None
    duration_s: float | None
    width: int | None  # display dims, after rotation
    height: int | None
    fps: float | None
    video_codec: str | None
    color_transfer: str | None
    has_audio: bool | None
    uploaded_at: datetime | None
    created_at: datetime
    raw_key: str | None = Field(exclude=True)
    thumbnail_key: str | None = Field(exclude=True)

    @computed_field
    def raw_url(self) -> str | None:
        return _url(self.raw_key)

    @computed_field
    def thumbnail_url(self) -> str | None:
        return _url(self.thumbnail_key)


class BrandCreate(BaseModel):
    name: str = Field(min_length=1)
    caption_template: str | None = None
    link: str | None = None
    auto_approve: bool = False
    default_overlay_config: OverlayConfig | None = None  # None: the column default (top right)


class BrandPatch(BaseModel):  # omit a field to leave it unchanged
    name: str = Field(None, min_length=1)
    caption_template: str | None = None
    link: str | None = None
    auto_approve: bool = None
    default_overlay_config: OverlayConfig = None
    archived: bool = None


class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    caption_template: str | None
    link: str | None
    auto_approve: bool
    default_overlay_config: OverlayConfig
    created_at: datetime
    archived_at: datetime | None
    logo_key: str | None = Field(exclude=True)

    @computed_field
    def logo_url(self) -> str | None:
        return _url(self.logo_key)


class RenderCreate(BaseModel):
    clip_id: int
    brand_id: int | None = None  # None: no logo
    overlay_config: OverlayConfig | None = None  # None: the brand's default
    crop_config: CropConfig | None = None  # None: fill + centre crop of the whole frame
    caption: str | None = None


class RenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_clip_id: int
    brand_id: int | None
    overlay_config: OverlayConfig | None
    crop_config: CropConfig | None
    caption: str | None
    status: str
    error_code: str | None
    size_bytes: int | None
    duration_s: float | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    output_key: str | None = Field(exclude=True)
    thumbnail_key: str | None = Field(exclude=True)

    @computed_field
    def output_url(self) -> str | None:
        return _url(self.output_key)

    @computed_field
    def thumbnail_url(self) -> str | None:
        return _url(self.thumbnail_key)


class RenderDetail(RenderOut):
    ffmpeg_log: str | None
