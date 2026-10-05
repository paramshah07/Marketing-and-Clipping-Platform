"""API request/response bodies. *_key columns go out as *_url (served under /media). Unknown fields in a
request are ignored (pydantic's default), so an old client's rights_status / rights_override still works."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, computed_field, model_validator

from app.services import storage
from app.services.render import BLEND, FILTERS

FilterName = Literal[tuple(FILTERS)]


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
    source_creator_handle: str | None = None


class FoundLink(BaseModel):
    url: str  # without its tracking query
    platform: str
    in_library: bool  # a clip of this video exists (whatever its status)


class LinksOut(BaseModel):
    links: list[FoundLink]  # one per video, in document order
    repeats: int  # how many more times those videos were linked
    other: list[str]  # links that aren't one video on a known site (the first 20)
    other_count: int


class ClipsFromUrls(BaseModel):
    urls: list[HttpUrl] = Field(min_length=1, max_length=1000)


class ClipsFromUrlsOut(BaseModel):
    created: int
    skipped: int  # already in the library, or twice in the request
    ids: list[int]  # the created clips, in request order


class ClipPatch(BaseModel):  # omit a field to leave it unchanged
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
    is_default: bool = False  # preselected in the Editor; clears the previous default brand


class BrandPatch(BaseModel):  # omit a field to leave it unchanged
    name: str = Field(None, min_length=1)
    caption_template: str | None = None
    link: str | None = None
    auto_approve: bool = None
    default_overlay_config: OverlayConfig = None
    archived: bool = None  # archiving clears is_default
    is_default: bool = None  # True clears the previous default; 409 on an archived brand


class BrandOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    caption_template: str | None
    link: str | None
    auto_approve: bool
    default_overlay_config: OverlayConfig
    is_default: bool
    created_at: datetime
    archived_at: datetime | None
    logo_key: str | None = Field(exclude=True)

    @computed_field
    def logo_url(self) -> str | None:
        return _url(self.logo_key)


# Customizations: saved captions and covers. At most one of each is the default (the Editor preselects it);
# setting is_default: true clears the previous one.


class CaptionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    text: str = Field(max_length=2200)  # Instagram's caption limit; {creator} and {link} are filled in the Editor
    is_default: bool = False


class CaptionPatch(BaseModel):  # omit a field to leave it unchanged
    name: str = Field(None, min_length=1, max_length=100)
    text: str = Field(None, max_length=2200)
    is_default: bool = None


class CaptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    text: str
    is_default: bool
    created_at: datetime


class CoverPatch(BaseModel):  # omit a field to leave it unchanged
    name: str = Field(None, min_length=1, max_length=100)
    is_default: bool = None


class CoverOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    is_default: bool
    created_at: datetime
    image_key: str = Field(exclude=True)

    @computed_field
    def image_url(self) -> str:  # a JPEG; the Editor uploads a copy as the render's cover
        return storage.url_for(self.image_key)


class RenderCreate(BaseModel):
    clip_id: int
    brand_id: int | None = None  # None: no logo
    overlay_config: OverlayConfig | None = None  # None: the brand's default
    crop_config: CropConfig | None = None  # None: fill + centre crop of the whole frame
    filter: FilterName | None = None  # GET /api/filters; None: no filter
    caption: str | None = Field(None, max_length=2200)  # Instagram's caption limit; a render's caption is fixed


class RenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_clip_id: int
    brand_id: int | None
    overlay_config: OverlayConfig | None
    crop_config: CropConfig | None
    filter: str | None
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
    cover_key: str | None = Field(exclude=True)

    @computed_field
    def output_url(self) -> str | None:
        return _url(self.output_key)

    @computed_field
    def thumbnail_url(self) -> str | None:
        return _url(self.thumbnail_key)

    @computed_field
    def cover_url(self) -> str | None:  # the Reel cover (JPEG, 1080x1920 from the Editor); None: Instagram's pick
        return _url(self.cover_key)


class RenderDetail(RenderOut):
    ffmpeg_log: str | None


class FilterLayer(BaseModel):
    mode: Literal[tuple(BLEND)]  # CSS mix-blend-mode
    color: str  # CSS rgb()
    opacity: float


class FilterOut(BaseModel):  # an Instagram-style filter (services.render.FILTERS), as the Editor previews it
    name: FilterName
    layers: list[FilterLayer]  # solid colours blended over the frame, bottom first
    css: str  # CSS filter functions, over the frame and its layers


class FreedSpace(BaseModel):  # POST /api/renders/free-published
    renders: int  # renders whose MP4 was (dry_run: would be) deleted
    bytes: int


# --- Phase 4: accounts, posts, scheduling (contract shared by backend + frontend agents) ---

PostStatus = Literal["DRAFT", "SCHEDULED", "PUBLISHING", "PUBLISHED", "FAILED", "DEAD_LETTER", "CANCELLED"]


class PostingSlots(BaseModel):
    times: list[str] = Field(default_factory=list)  # "HH:MM" wall-clock times in the account's timezone, every day


class Quota(BaseModel):  # Zernio publishing-limit, Meta's rolling window
    used: int
    total: int
    duration_s: int


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    zernio_account_id: str
    zernio_profile_id: str
    username: str
    avatar_url: str | None
    connection_status: Literal["connected", "disconnected"]
    posting_slots: PostingSlots
    daily_cap: int
    timezone: str
    min_gap_minutes: int
    connected_at: datetime
    last_publish_at: datetime | None
    disabled_at: datetime | None
    today_count: int = 0  # non-cancelled posts on the account's current LOCAL day
    quota: Quota | None = None  # None when Zernio can't be reached; cached 5 min
    next_post_at: datetime | None = None  # next DRAFT/SCHEDULED post


class AccountPatch(BaseModel):  # omit a field to leave it unchanged
    posting_slots: PostingSlots = None
    daily_cap: int = Field(None, ge=1, le=100)
    timezone: str = None  # IANA name, validated with zoneinfo
    min_gap_minutes: int = Field(None, ge=0, le=720)
    disabled: bool = None  # True cancels the account's DRAFT/SCHEDULED posts


class NextSlot(BaseModel):
    scheduled_for: datetime | None  # None: nothing free within 30 days


class PostRender(BaseModel):  # what a calendar card / recovery screen needs about the render
    id: int
    clip_id: int
    clip_name: str | None
    brand_id: int | None
    brand_name: str | None
    duration_s: float | None
    thumbnail_url: str | None
    output_url: str | None


class Remedy(BaseModel):
    action: Literal["reconnect", "rerender", "retry", "auto"]
    label: str  # e.g. "Reconnect account", "Re-render and retry", "Retry now", "Moved to next free slot"


class PostMusic(BaseModel):
    """Instagram's catalog track a Reel goes out with (Zernio audioConfiguration); title and artist are for show."""

    id: str = Field(pattern=r"^\d{1,30}$")  # Zernio's audioId (GET /api/accounts/{id}/music)
    title: str | None = Field(None, max_length=300)
    artist: str | None = Field(None, max_length=300)  # the artist, or the @creator of an original sound
    volume: int = Field(100, ge=0, le=100)  # audioVolume: the track
    video_volume: int = Field(100, ge=0, le=100)  # videoVolume: the clip's own sound; 0 mutes it


class MusicOut(BaseModel):  # GET /api/accounts/{id}/music: one asset of Instagram's audio catalog
    id: str
    title: str | None
    artist: str | None  # the artist, or the @creator of an original sound
    kind: Literal["music", "original_sound"]
    duration_s: float | None
    preview_url: str | None  # Meta's, expires after about 1.5 days
    artwork_url: str | None


class PostOut(BaseModel):
    id: int
    render_id: int
    account_id: int
    account_username: str
    caption: str
    music: PostMusic | None
    scheduled_for: datetime
    status: PostStatus
    error_code: str | None
    error_detail: dict | None  # raw payload, for the collapsed disclosure only
    cause: str | None  # plain-language cause derived from error_code (Phase 5)
    remedy: Remedy | None  # the one primary action for FAILED / DEAD_LETTER (Phase 5)
    attempt_count: int
    zernio_post_id: str | None  # Zernio's post id, for the recovery screen's technical details
    permalink: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime
    render: PostRender


class PostCreate(BaseModel):
    render_id: int
    account_id: int
    scheduled_for: datetime  # must be tz-aware
    caption: str | None = Field(None, max_length=2200)  # None: the render's caption
    music: PostMusic | None = None  # None: the clip's own sound
    repost: bool = False  # post it even if this video already went (or is queued) to the account: else 409 ALREADY_POSTED


class PostPatch(BaseModel):  # DRAFT or SCHEDULED only; omit a field to leave it unchanged
    scheduled_for: datetime = None
    caption: str = Field(None, max_length=2200)
    music: PostMusic | None = None  # null takes it off; not once a publish was attempted (409 MUSIC_LOCKED)


class AutoScheduleIn(BaseModel):
    render_ids: list[int] = Field(min_length=1)
    account_id: int


class Placed(BaseModel):
    render_id: int
    post: PostOut


class Unplaced(BaseModel):
    render_id: int
    reason: str  # e.g. "no free slot within 30 days", "render longer than the Reel limit"


class AutoScheduleOut(BaseModel):
    placed: list[Placed]
    unplaced: list[Unplaced]


class RemedyIn(BaseModel):
    action: Literal["reconnect", "rerender", "retry"] | None = None  # None: the post's mapped remedy


# ---------------------------------------------------------------- users (app/api/auth.py)


class Credentials(BaseModel):  # signup and login
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str


class SignupStatus(BaseModel):
    open: bool
    remaining: int  # spots left before MAX_USERS
    max_users: int


class PasswordChange(BaseModel):
    current: str
    new: str


class ZernioKeyOut(BaseModel):  # the key itself never leaves the server
    status: Literal["none", "valid", "invalid"]
    last4: str | None
    email: str | None  # the key's Zernio user (GET /v1/auth/verify)
    name: str | None
    checked_at: datetime | None
    error: str | None


class ZernioKeyIn(BaseModel):
    key: str  # sk_... or zrk_... (Zernio: API keys); never sent back


class KeyCheck(BaseModel):  # PUT /api/me/zernio-key and its Re-check: the key, then its Instagram accounts
    zernio: ZernioKeyOut
    accounts: list[str]  # the key's Instagram accounts (usernames), now in Accounts
    skipped: list[str]  # listed too, but another Clipper user already has them: not added
    over_limit: list[str]  # beyond the Zernio plan's account limit: Zernio doesn't offer them for posting


class Storage(BaseModel):
    used_bytes: int  # clips and renders
    quota_bytes: int | None  # null: unlimited


class BotOut(BaseModel):
    id: int
    username: str | None  # @name, from getMe
    chat_title: str | None  # the paired chat
    alerts: bool  # it sends your failure alerts
    # running: seen in the last 90 s; waiting: for /start <code>; rejected: Telegram refused the token
    health: Literal["running", "waiting", "rejected", "not_responding"]
    pairing: bool  # a pairing code is out (new bot or Re-pair): false again once a chat used it
    last_seen_at: datetime | None
    created_at: datetime


class BotIn(BaseModel):
    token: str  # from @BotFather; never sent back


class BotPatch(BaseModel):
    alerts: bool


class BotPairing(BaseModel):  # open pair_url (or send the bot `start`) in a private chat to pair it with that chat
    bot: BotOut
    pair_url: str | None  # https://t.me/<bot>?start=<code>; null: no code (a paired bot's new token) or no @name yet
    start: str | None  # "/start <code>"
    expires_at: datetime | None  # the code's, 15 min


class BotTest(BaseModel):
    ok: bool  # Telegram took the test message
    error: str | None  # Telegram's reason when not


class Setup(BaseModel):  # the setup checklist: a valid key, a usable account, a paired bot
    zernio: bool
    instagram: bool
    telegram: bool


class Me(BaseModel):
    id: int
    username: str
    setup: Setup
    zernio: ZernioKeyOut
    bots: list[BotOut]
    storage: Storage
