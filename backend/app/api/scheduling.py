"""Accounts, posts and scheduling (Phase 4). The remedy route lives in app/api/recovery.py (Phase 5).

Contract first: every route's signature and response model is fixed here so the frontend can be
built in parallel. Errors use HTTPException(detail={"code": ..., "message": ...}), e.g. 409
{"code": "RIGHTS_NONE"} or {"code": "STATE_CONFLICT"}, 422 {"code": "RENDER_NOT_READY" | "TOO_LONG" |
"ACCOUNT_UNAVAILABLE" | "TOO_SOON"}.
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.schemas import (
    AccountOut,
    AccountPatch,
    AutoScheduleIn,
    AutoScheduleOut,
    NextSlot,
    PostCreate,
    PostOut,
    PostPatch,
    PostStatus,
)

router = APIRouter(prefix="/api")


def _todo():
    raise HTTPException(501, {"code": "NOT_IMPLEMENTED", "message": "stub"})


async def load_post_out(s, post_id: int) -> PostOut:
    """Build one PostOut (joins render, clip, brand, account; cause/remedy via app.services.errors.describe).
    Shared with app/api/recovery.py. 404 {"code": "NOT_FOUND"} if missing."""
    raise NotImplementedError


@router.get("/accounts")
async def list_accounts() -> list[AccountOut]:
    _todo()


@router.post("/accounts/sync")
async def sync_accounts() -> list[AccountOut]:
    """Pull GET /v1/accounts from Zernio (read-only) and upsert Instagram accounts by zernio_account_id."""
    _todo()


@router.patch("/accounts/{account_id}")
async def update_account(account_id: int, body: AccountPatch) -> AccountOut:
    _todo()


@router.get("/accounts/{account_id}/next-slot")
async def next_slot(account_id: int) -> NextSlot:
    _todo()


@router.get("/posts")
async def list_posts(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = None,
    account_id: int | None = None,
    brand_id: int | None = None,
    status: list[PostStatus] | None = Query(None),
) -> list[PostOut]:
    """Sorted by scheduled_for. from/to filter on coalesce(published_at, scheduled_for)."""
    _todo()


@router.get("/posts/{post_id}")
async def get_post(post_id: int) -> PostOut:
    _todo()


@router.post("/posts", status_code=201)
async def create_post(body: PostCreate) -> PostOut:
    _todo()


@router.post("/posts/auto-schedule")
async def auto_schedule(body: AutoScheduleIn) -> AutoScheduleOut:
    _todo()


@router.patch("/posts/{post_id}")
async def update_post(post_id: int, body: PostPatch) -> PostOut:
    _todo()


@router.post("/posts/{post_id}/approve")
async def approve_post(post_id: int) -> PostOut:
    _todo()


@router.post("/posts/{post_id}/cancel")
async def cancel_post(post_id: int) -> PostOut:
    _todo()
