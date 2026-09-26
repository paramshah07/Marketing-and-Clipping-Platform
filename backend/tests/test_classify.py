"""classify / describe / outcome: pure, no database."""

import json
from pathlib import Path

from app.services.errors import CATEGORY, CAUSES, classify, describe
from app.services.publisher import outcome

FIX = Path(__file__).parent / "fixtures" / "zernio"


def body(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text())["body"]


def test_classify_every_documented_category():
    assert {c: classify(c) for c in CATEGORY} == {
        "auth_expired": "ACCOUNT_DISCONNECTED",
        "account_issue": "ACCOUNT_DISCONNECTED",
        "platform_rate_limit": "RATE_LIMITED",
        "quota_exhausted": "RATE_LIMITED",
        "user_content": "CONTENT_REJECTED",
        "platform_rejected": "CONTENT_REJECTED",
        "platform_error": "NETWORK_ERROR",
        "system_error": "NETWORK_ERROR",
        "user_abuse": "UNKNOWN",
        "unknown": "UNKNOWN",
    }
    assert classify(None) == classify("something_new") == "UNKNOWN"


def test_describe():
    assert describe(None) == (None, None)
    actions = {code: describe(code)[1].action for code in CAUSES}
    assert actions["ACCOUNT_DISCONNECTED"] == "reconnect"
    assert actions["CONTENT_REJECTED"] == actions["RENDER_FAILED"] == actions["WINDOW_EXPIRED"] == "rerender"
    assert actions["RATE_LIMITED"] == actions["MISSED"] == "auto"
    assert {c for c, a in actions.items() if a == "retry"} == {
        "NETWORK_ERROR", "UNKNOWN", "TOO_LONG", "TOO_SHORT", "WORKER_CRASHED", "NO_FREE_SLOT"
    }  # fmt: skip
    cause, remedy = describe("SOMETHING_ELSE")
    assert "SOMETHING_ELSE" in cause and remedy.label == "Retry now"


def test_outcome_branches_on_post_status_not_http_status():
    assert outcome(body("docs_create_published")["post"]) == "PUBLISHED"
    assert outcome(body("docs_replay_published")["post"]) == "PUBLISHED"
    assert outcome(body("docs_207_failed")["post"]) == "CONTENT_REJECTED"
    assert outcome(body("docs_207_partial")["post"]) == "CONTENT_REJECTED"  # linkedin published, instagram not
    assert outcome(body("docs_207_scheduled")["post"]) == "PROCESSING"  # Zernio retries by itself
    assert outcome(body("draft_create")["post"]) == "UNKNOWN"  # a draft never counts as published
