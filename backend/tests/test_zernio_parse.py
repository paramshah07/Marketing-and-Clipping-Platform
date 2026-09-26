"""Parsing real recorded Zernio bodies (tests/fixtures/zernio/). No HTTP."""

import json
from pathlib import Path

from app.services.zernio import parse_accounts, parse_quota

FIXTURES = Path(__file__).parent / "fixtures" / "zernio"


def body(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())["body"]


def test_parse_accounts_fixture():
    [a] = parse_accounts(body("accounts"))
    assert a["zernio_account_id"] == "6ab77f6a7d5baf4adc07a655"
    assert a["zernio_profile_id"] == "6ab77e2e53aff902195a118e"
    assert a["username"] == "i.cant.de"
    assert a["avatar_url"].startswith("https://")
    assert a["connection_status"] == "connected"


def test_parse_accounts_connection_and_platform():
    [real] = body("accounts")["accounts"]
    accounts = [
        {**real, "_id": "a", "needsReconnection": True},
        {**real, "_id": "b", "isActive": False},
        {**real, "_id": "c", "platform": "tiktok"},
        {**real, "_id": "d", "profileId": "p-as-string"},
    ]
    got = {a["zernio_account_id"]: a for a in parse_accounts({"accounts": accounts})}
    assert set(got) == {"a", "b", "d"}  # Instagram only
    assert got["a"]["connection_status"] == got["b"]["connection_status"] == "disconnected"
    assert got["d"]["zernio_profile_id"] == "p-as-string"
    assert parse_accounts({}) == []


def test_parse_quota_fixture():
    q = parse_quota(body("publishing_limit"))
    assert (q.used, q.total, q.duration_s) == (0, 100, 86400)
