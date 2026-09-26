"""Recovery (Phase 5): apply a failed post's remedy."""

from fastapi import APIRouter, HTTPException

from app.schemas import PostOut, RemedyIn

router = APIRouter(prefix="/api")


@router.post("/posts/{post_id}/remedy")
async def remedy_post(post_id: int, body: RemedyIn) -> PostOut:
    """FAILED / DEAD_LETTER only (409 {"code": "STATE_CONFLICT"} otherwise). See docs/PLAN.md section 4."""
    raise HTTPException(501, {"code": "NOT_IMPLEMENTED", "message": "stub"})
