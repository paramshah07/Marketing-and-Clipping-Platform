"""Run the pipeline in this process, without the api or the queue. Worker container only (ffmpeg):

    docker compose run --rm worker python -m app.cli render <clip_id> <brand_id> [--x .. --y .. --w .. --opacity ..]

Probes the clip first if it isn't READY, renders it with the brand's logo (default overlay, overridden by
any --x/--y/--w/--opacity) and prints the output path.
"""

import argparse
import sys

from pydantic import ValidationError

from app.core.db import SyncSession
from app.models import Brand, Render, SourceClip, cas
from app.schemas import OverlayConfig
from app.services import storage
from app.tasks.media import probe_clip, render


def _run(task, model, id: int, in_progress: list[str]) -> None:
    """Run a job in this process. Ctrl-C (or any crash) must not strand the row in PROBING/RENDERING:
    no queued job exists to finish it, and the api only retries or deletes FAILED rows."""
    try:
        task(id)
    except BaseException:
        with SyncSession() as s, s.begin():
            s.execute(cas(model, id, in_progress, status="FAILED", error_code="INTERRUPTED"))
        raise


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("render", help="probe if needed, render, print the output path")
    cmd.add_argument("clip_id", type=int)
    cmd.add_argument("brand_id", type=int)
    for name in ("x", "y", "w", "opacity"):
        cmd.add_argument(f"--{name}", type=float, help="overlay fraction (default: the brand's)")
    args = parser.parse_args()

    with SyncSession() as s, s.begin():
        clip, brand = s.get(SourceClip, args.clip_id), s.get(Brand, args.brand_id)
        if clip is None or brand is None or not brand.logo_key:
            sys.exit("clip or brand not found, or the brand has no logo")
        if clip.status != "READY" and not (
            clip.raw_key and s.execute(cas(SourceClip, clip.id, ["FAILED", "PROBING"], status="PROBING")).rowcount
        ):
            sys.exit(f"clip {clip.id} is {clip.status} with no file to probe")
        overrides = {k: getattr(args, k) for k in ("x", "y", "w", "opacity") if getattr(args, k) is not None}
        try:
            overlay = OverlayConfig(**(brand.default_overlay_config | overrides)).model_dump()
        except ValidationError as e:  # exits before commit: the probe CAS above is rolled back
            err = e.errors()[0]
            sys.exit(f"bad overlay: {'.'.join(map(str, err['loc'])) or 'x, w'}: {err['msg']}")

    if clip.status != "READY":
        _run(probe_clip, SourceClip, clip.id, ["PROBING"])
        with SyncSession() as s:
            clip = s.get(SourceClip, clip.id)
        if clip.status != "READY":
            sys.exit(f"probe failed: {clip.error_code} {clip.error_detail}")

    with SyncSession() as s, s.begin():
        r = Render(source_clip_id=clip.id, brand_id=brand.id, overlay_config=overlay)
        s.add(r)
        s.flush()
        render_id = r.id
    _run(render, Render, render_id, ["PENDING", "RENDERING"])
    with SyncSession() as s:
        r = s.get(Render, render_id)
    if r.status != "READY":
        sys.exit(f"render {render_id} {r.status} {r.error_code}\n{(r.ffmpeg_log or '')[-3000:]}")
    print(storage.path_for(r.output_key))


if __name__ == "__main__":
    main()
