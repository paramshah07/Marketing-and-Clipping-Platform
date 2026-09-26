# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.28"]
# ///
"""Phase 0 spike: prove Zernio can publish a Reel for Clipper.

Run from the repo root (reads ZERNIO_API_KEY from ./.env):
  uv run backend/scripts/spike_zernio.py accounts
  uv run backend/scripts/spike_zernio.py limit ACCOUNT_ID
  uv run backend/scripts/spike_zernio.py upload FILE.mp4
  uv run backend/scripts/spike_zernio.py draft-idempotency ACCOUNT_ID MEDIA_URL
  uv run backend/scripts/spike_zernio.py publish ACCOUNT_ID MEDIA_URL --confirm-username NAME [--trial]

Every request and response is logged (API key and upload signatures redacted) and response
bodies are saved to backend/tests/fixtures/zernio/ for the Phase 5 state-machine tests.
Exits non-zero on any unexpected result. No retries: failures should be seen, not hidden.
"""

import argparse
import json
import mimetypes
import re
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "backend/tests/fixtures/zernio"
BASE = "https://zernio.com/api/v1"


def api_key() -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("ZERNIO_API_KEY="):
            return line.split("=", 1)[1].split("#")[0].strip()
    sys.exit("ZERNIO_API_KEY missing from .env")


def redact(text: str) -> str:
    text = re.sub(r"sk_[A-Za-z0-9_-]+", "sk_[redacted]", text)
    text = re.sub(r"(X-Amz-(Signature|Credential|Security-Token)=)[^&\"]+", r"\1[redacted]", text)
    return re.sub(r'("bio":\s*)"[^"]*"', r'\1"[redacted]"', text)


def log_response(r: httpx.Response, fixture: str | None = None) -> dict:
    req = r.request
    print(f"\n>>> {req.method} {redact(str(req.url))}")
    for h in ("Idempotency-Key", "Content-Type"):
        if h in req.headers:
            print(f"    {h}: {req.headers[h]}")
    if req.method != "PUT":  # PUT bodies are streamed video bytes
        print(f"    body: {redact(req.content.decode(errors='replace'))}")
    print(f"<<< HTTP {r.status_code}  Retry-After={r.headers.get('Retry-After')}")
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:2000]}
    print(redact(json.dumps(data, indent=2)))
    if fixture:
        FIXTURES.mkdir(parents=True, exist_ok=True)
        (FIXTURES / f"{fixture}.json").write_text(
            redact(json.dumps({"status": r.status_code, "headers": {"Retry-After": r.headers.get("Retry-After")}, "body": data}, indent=2))
        )
    return data


def expect(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        sys.exit(1)


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {api_key()}"}, timeout=httpx.Timeout(30, read=600))


def post_body(account_id: str, media_url: str, caption: str, **mode) -> dict:
    return {
        "content": caption,
        "mediaItems": [{"type": "video", "url": media_url}],
        "platforms": [{"platform": "instagram", "accountId": account_id, "platformSpecificData": mode.pop("psd", {"shareToFeed": True})}],
        **mode,
    }


def cmd_accounts(a) -> None:
    with client() as c:
        data = log_response(c.get("/accounts"), "accounts")
    for acc in data.get("accounts", []):
        print(f"account {acc['_id']} {acc['platform']} @{acc.get('username')} active={acc.get('isActive')} profile={acc.get('profileId')}")


def cmd_limit(a) -> None:
    with client() as c:
        data = log_response(c.get(f"/accounts/{a.account_id}/instagram/publishing-limit"), "publishing_limit")
    expect("quotaTotal" in data, f"quota {data.get('quotaUsage')}/{data.get('quotaTotal')} per {data.get('quotaDurationSeconds')}s")


def cmd_upload(a) -> None:
    f = Path(a.file)
    ctype = mimetypes.guess_type(f.name)[0] or "video/mp4"
    with client() as c:
        pre = log_response(c.post("/media/presign", json={"filename": f.name, "contentType": ctype, "size": f.stat().st_size}), "presign")
        expect("uploadUrl" in pre and "publicUrl" in pre, "presign returned uploadUrl + publicUrl")
    with f.open("rb") as fh:
        put = httpx.put(pre["uploadUrl"], content=fh, headers={"Content-Type": ctype}, timeout=600)
    log_response(put)
    expect(put.status_code in (200, 201, 204), "PUT upload accepted")
    head = httpx.head(pre["publicUrl"], timeout=60, follow_redirects=True)
    print(f"HEAD publicUrl -> {head.status_code} {head.headers.get('content-type')} {head.headers.get('content-length')} bytes")
    expect(head.status_code == 200, "publicUrl is publicly reachable")
    print(f"\nMEDIA_URL={pre['publicUrl']}")


def cmd_draft_idempotency(a) -> None:
    key = f"spike-draft-{uuid.uuid4()}"
    with client() as c:
        r1 = c.post("/posts", json=post_body(a.account_id, a.media_url, "Clipper spike draft (never published)", isDraft=True), headers={"Idempotency-Key": key})
        d1 = log_response(r1, "draft_create")
        expect(r1.status_code == 201, "draft created (201)")
        post_id = d1["post"]["_id"]
        try:
            r2 = c.post("/posts", json=post_body(a.account_id, a.media_url, "Clipper spike draft (never published)", isDraft=True), headers={"Idempotency-Key": key})
            d2 = log_response(r2, "draft_replay_same_body")
            expect(r2.status_code == 200 and d2["post"]["_id"] == post_id, "same key + same body replays the original (200, same _id)")
            r3 = c.post("/posts", json=post_body(a.account_id, a.media_url, "DIFFERENT caption, same key", isDraft=True), headers={"Idempotency-Key": key})
            d3 = log_response(r3, "draft_replay_diff_body")
            expect(r3.status_code == 200 and d3["post"]["_id"] == post_id, "same key + different body still replays the original")
        finally:
            log_response(c.delete(f"/posts/{post_id}"), "draft_delete")
        lst = log_response(c.get(f"/posts/{post_id}"))
        print(f"after delete GET status: {lst.get('error') or lst.get('post', {}).get('status')}")


def cmd_publish(a) -> None:
    with client() as c:
        accs = c.get("/accounts").json()["accounts"]
        acc = next((x for x in accs if x["_id"] == a.account_id), None)
        expect(acc is not None and acc.get("username") == a.confirm_username, f"target is @{a.confirm_username} (confirmed by operator)")
        psd = {"shareToFeed": True}
        if a.trial:
            psd["trialParams"] = {"graduationStrategy": "MANUAL"}
        key = f"spike-publish-{uuid.uuid4()}"
        print(f"Idempotency-Key: {key}")
        body = post_body(a.account_id, a.media_url, a.caption, publishNow=True, psd=psd)
        r1 = c.post("/posts", json=body, headers={"Idempotency-Key": key})
        d1 = log_response(r1, f"publish_{a.label}")
        post = d1.get("post", {})
        if r1.status_code != 201:
            expect(False, f"publishNow returned {r1.status_code}, post.status={post.get('status')}")
        # Live finding: for Instagram video a 201 comes back while post.status is still "publishing"
        for _ in range(60):
            if post.get("status") not in ("publishing", "scheduled", "pending", "processing"):
                break
            time.sleep(10)
            post = c.get(f"/posts/{post['_id']}").json()["post"]
        urls = [p.get("platformPostUrl") for p in post.get("platforms", [])]
        FIXTURES.joinpath(f"get_after_publish_{a.label}.json").write_text(redact(json.dumps({"status": 200, "body": {"post": post}}, indent=2)))
        expect(post.get("status") == "published", f"published: {urls} (status {post.get('status')}, {[p.get('errorCategory') for p in post.get('platforms', [])]})")
        r2 = c.post("/posts", json=body, headers={"Idempotency-Key": key})
        d2 = log_response(r2, f"publish_{a.label}_replay")
        expect(r2.status_code == 200 and d2["post"]["_id"] == post["_id"], "replay with same key returns the original post, no second Reel")
        log_response(c.get(f"/posts/{post['_id']}"), f"get_published_{a.label}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(required=True)
    sub.add_parser("accounts").set_defaults(fn=cmd_accounts)
    s = sub.add_parser("limit"); s.add_argument("account_id"); s.set_defaults(fn=cmd_limit)
    s = sub.add_parser("upload"); s.add_argument("file"); s.set_defaults(fn=cmd_upload)
    s = sub.add_parser("draft-idempotency"); s.add_argument("account_id"); s.add_argument("media_url"); s.set_defaults(fn=cmd_draft_idempotency)
    s = sub.add_parser("publish"); s.add_argument("account_id"); s.add_argument("media_url")
    s.add_argument("--confirm-username", required=True); s.add_argument("--trial", action="store_true")
    s.add_argument("--caption", default="Clipper test post"); s.add_argument("--label", default="8s")
    s.set_defaults(fn=cmd_publish)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
