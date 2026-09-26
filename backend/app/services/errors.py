"""Plain-language cause + the one remedy for a post's error_code (Phase 5 fills this in)."""

from app.schemas import Remedy


def describe(error_code: str | None) -> tuple[str | None, Remedy | None]:
    """(cause, remedy) for the recovery screen and PostOut. Pure; (None, None) when there is no error."""
    return None, None
