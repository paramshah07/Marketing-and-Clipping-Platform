> **Historical record**, kept as written and partly out of date. For current documentation, start at [docs/README.md](README.md).

# Phase 2: ingest + render (backend only)

What exists now:
- **API** (`backend/app/api/pipeline.py`, schemas in `app/schemas/`): clips, brands and renders as in
  PLAN section 5.
- **Jobs** (`app/tasks/media.py`, registered in `import_paths`): `download_clip` (yt-dlp), `probe_clip`
  (ffprobe + thumbnail) and `render` (ffmpeg). They are sync `def` tasks and every status write is a
  compare-and-set (`app.models.cas`).
- **Pure helpers** (`app/services/render.py`): `build_ffmpeg_args`, `crop_px`, `parse_probe`,
  `classify_ytdlp_error` and `source_fields`, plus thin `ffprobe()` / `thumbnail()` runners.
- **Sweeper**: when it gives up on a job, it also sets the job's row to FAILED `WORKER_CRASHED`
  (`GIVE_UP_SQL` in `queue.py`, keyed by task name).
- **CLI**: `python -m app.cli render <clip_id> <brand_id> [--x --y --w --opacity]`.
- **Worker image**: now includes `yt-dlp[default,curl-cffi,deno]` 2026.8.19 from a locked uv
  dependency group `worker`, so the api image doesn't get it. yt-dlp found the deno binary on its own
  (`[debug] JS runtimes: deno-2.9.7`).
- **New api dependency**: `python-multipart`. FastAPI needs it for `UploadFile`, and the streaming clip
  upload uses its parser.

Run on 2026-09-26, macOS arm64, Docker Desktop. No Zernio calls, nothing published. This is the second
run, after the review fixes (see "Review fixes" at the end).

## Decisions worth knowing
- **Upload streaming.** `POST /api/clips` parses the multipart body as it arrives, using python-multipart's
  low-level parser, and writes the `file` part straight to `raw/<id>.part`. Nothing is spooled to /tmp or
  held in memory.
  - The row is `UPLOADING` while the bytes arrive (see c).
  - At the end, `UPLOADING → PROBING` and the probe job defer happen in one transaction.
  - The request is rejected (and the row and part file deleted) for: a `Content-Length` over
    `MAX_UPLOAD_BYTES`, an extension other than mp4/mov/webm (checked as soon as the part headers
    arrive), too many bytes, a bad `rights_status`, a form field that isn't UTF-8 (422), a body that
    ends without its closing boundary (400 `incomplete multipart body`; python-multipart's `finalize()`
    doesn't check this, so an `on_end` callback does), or a client disconnect.
- **Download then probe, one job.** `download_clip` runs the probe itself instead of deferring
  `probe_clip`. A sync task can't defer inside its own DB transaction with the async connector. Probing in
  the same job also means a clip is never PROBING without a job. If the worker dies mid-probe, the sweeper
  re-runs `download_clip`, which sees PROBING and goes straight to the probe.
- **Retries: only for the database** (PLAN D12: ffmpeg exit ≠ 0 is final).
  - Known failures raise `Failed(code, detail)`. Anything unexpected inside the work becomes
    `INTERNAL_ERROR` with the repr. Both end the row in FAILED.
  - The status writes themselves can hit the DB. `render`, `probe_clip` and `download_clip` have
    `RetryStrategy(max_attempts=3, wait=30, retry_exceptions={OperationalError})`, so a failed status
    write re-runs the job instead of leaving the row in RENDERING/PROBING/DOWNLOADING. The re-run redoes
    the work, because the CAS accepts the in-progress status.
  - Both SQLAlchemy engines use `pool_pre_ping`. A render that outlives a Postgres restart does its final
    write on a fresh connection (see g2), and the api's first request after the restart no longer fails.
  - A render job accepts a row that is PENDING or RENDERING, so a job the sweeper re-queues after a crash
    re-runs (see g).
  - The CLI runs jobs in its own process with no queue behind them. If it is interrupted (Ctrl-C, or any
    crash), it sets the row it was working on to FAILED `INTERRUPTED` before exiting, so the api can
    retry or delete it (see e).
- **Crop rounding.** Crop fractions are converted to pixels with **floor to even** (`crop_px`), not
  round-to-nearest. That can never go past the frame edge, even for odd sizes such as 1279x719, and the
  error is at most 2 source px.
- **Overlay.** `POST /api/renders` treats `overlay_config` as optional: without it the brand's
  `default_overlay_config` is used (handy for curl and the CLI). With no brand, no logo is drawn and
  `overlay_config` is null.
  - Bounds: `x + w ≤ 1`, `y < 1`, and `w ≥ 0.01`. Below 1/2160, `round(w*1080)` is 0, and ffmpeg reads
    `scale=0:-1` as "the logo's own width", so the logo would be drawn full size. The logo's height isn't
    known to the API, so a logo can still run off the bottom when y is close to 1.
- **Logo files.** Each logo upload gets a new file name (`logos/<brand>-<random>.png`) and the old file is
  deleted, so a browser never shows a cached old logo. The PNG check walks the chunks and needs IDAT and
  then IEND. A truncated file gets 422 `logo PNG is corrupt` instead of breaking every later render.
  CRCs and the zlib stream are not checked.
- **Deletes.**
  - Clips: only READY or FAILED ones with no renders.
  - Renders: only when not RENDERING and no post (of any status) refers to them. This is stricter than
    PLAN's "no live post", because the FK would block it anyway. Phase 4/5 decides whether dead posts get
    deleted with their render.
  - Both deletes are a single conditional `DELETE`, then the files are removed (in a threadpool, like the
    logo write).
- **Ids past int4** (for example `/api/clips/2147483648`) get 422 `integer out of range` from one
  `DataError` handler in `main.py`, not a 500.
- **`unscheduled=true`** means no post other than CANCELLED ones.
- **Error codes.**
  - Clips: `PRIVATE`, `REMOVED`, `GEO_BLOCKED`, `EXTRACTOR_FAILED` (yt-dlp), `DURATION_OUT_OF_RANGE`,
    `PROBE_FAILED`, `THUMBNAIL_FAILED`, `WORKER_CRASHED`, `INTERRUPTED` (CLI), `INTERNAL_ERROR`.
  - Renders: `FFMPEG_FAILED`, `OUTPUT_TOO_LARGE` (> 300,000,000 bytes), `WORKER_CRASHED`,
    `INTERRUPTED` (CLI), `INTERNAL_ERROR`.
- **yt-dlp.**
  - Classifier: an ordered (substring, code) list; the first match wins. It checks the last `ERROR:`
    line and stores that line in `error_detail`. Three strings were added to the researched list:
    `available in your country` → GEO_BLOCKED (YouTube); `video is unavailable` → REMOVED (YouTube now
    says "This video is unavailable", seen live, see f); `sign in to confirm` → PRIVATE (YouTube's age and
    "not a bot" walls, which cookies fix).
  - A non-zero exit is classified from stderr. An exit 0 with no file (skipped by `--max-filesize`) is
    `EXTRACTOR_FAILED` "yt-dlp downloaded nothing (over MAX_UPLOAD_BYTES?)", not a stray warning line.
  - `-I 1` with `--no-playlist`: a playlist, channel, carousel or multi-video post URL downloads its first
    item only (see f).
  - The per-job cookie copy lives in a `tempfile.TemporaryDirectory()`, not under `DATA_DIR`, which the
    api serves at `/media`.
  - The creator handle is written as `coalesce(source_creator_handle, <yt-dlp's>)` in the final CAS, so a
    handle the operator PATCHes while the download runs wins.
- **Render details.**
  - Duration is the video stream's (`parse_probe` falls back to the format's only when the stream has
    none, as in webm). Audio is `[0:a:0]apad` and `-shortest` is always on, so the output ends with the
    last frame: no silent tail when the audio runs longer, and silence when it runs shorter. A 2 s video
    with 4 s of audio is now `DURATION_OUT_OF_RANGE`.
  - The main scale has `out_range=tv`: a full-range source (yuvj420p / pc) comes out as limited-range
    yuv420p with the same colours as a limited-range source.
  - The graph reads `[0:V:0]` (capital V skips cover art), the stream `parse_probe` measured.
  - HDR (HLG/PQ): zscale + `tonemap=hable` at `npl=100` as researched, then `sidedata=mode=delete`. Without
    that, the source's mastering display and content light level metadata were copied into the SDR H.264
    (both the SEI and the mp4 `mdcv` box). The review suggested `npl=203` + `mobius` (brighter). On real
    footage (see "HDR tonemap" below) the current chain was the closer match, so it stays.
- **Thumbnails.** JPEG, longest side 540 px, taken at min(1 s, duration/2), in `thumbs/clip-<id>.jpg` and
  `thumbs/render-<id>.jpg`. HLG/PQ clips are tonemapped for the thumbnail too.
- **Tests** now run in the worker container: `docker compose run --rm worker pytest`. In the api
  container the 20 ffmpeg tests are skipped. CLAUDE.md and the README are updated.

## Acceptance (all from scratch: `docker compose down -v && docker compose up -d --build`)

`./data/{raw,thumbs,renders,logos}` were moved out first, so ids start at 1. Inputs were made with ffmpeg
in the worker container, in `data/qa/accept/`:
- `logo.png`: 400x160 RGBA, an orange disc and bar on a transparent background.
- `logo-noalpha.png`: 400x160 white RGB.
- `logo-truncated.png`: the first 60 bytes of `logo.png` (IHDR says RGBA, no IDAT or IEND).
- `clip-16x9.mp4`: testsrc2 1920x1080, 30 fps, 8 s, AAC.
- `long-90s.mp4`: the same, 90 s (for the interrupt, DB restart and kill checks).

### a) Stack
```
SERVICE    STATUS
api        Up 6 seconds
migrate    Exited (0) 6 seconds ago
postgres   Up 9 seconds (healthy)
worker     Up 6 seconds
$ curl -s http://127.0.0.1:8000/api/status
{"db":true,"worker_alive":true,"worker_last_heartbeat":"2026-09-26T10:27:10.568082Z","jobs":{"succeeded":1}}
```

### b) Brand + transparent logo via curl
```
$ curl -s -X POST $A/brands -H 'content-type: application/json' -d '{"name":"Acme","link":"https://acme.test"}'
{"id":1,"name":"Acme","caption_template":null,"link":"https://acme.test","auto_approve":false,
 "default_overlay_config":{"x":0.72,"y":0.06,"w":0.22,"opacity":1.0},...,"logo_url":null}
$ curl -F file=@logo-noalpha.png $A/brands/1/logo    {"detail":"logo PNG has no transparency (alpha channel)"} [422]
$ curl -F file=@clip-16x9.mp4 $A/brands/1/logo       {"detail":"logo must be a PNG"} [415]
$ curl -F file=@logo-truncated.png $A/brands/1/logo  {"detail":"logo PNG is corrupt"} [422]
$ curl -F file=@logo.png $A/brands/1/logo            {"id":1,...,"logo_url":"/media/logos/1-9a2c2767.png"} [200]
```

### c) Upload a 16:9 clip via curl → READY with a thumbnail
The upload was rate-limited to 1 MB/s so the row could be seen mid-stream:
```
$ curl -s --limit-rate 1M -F file=@clip-16x9.mp4 -F rights_status=own_content \
       --form-string source_creator_handle=@acme $A/clips
# 3 s in:
 id |  status   | raw_key | original_filename
  1 | UPLOADING |         |
data/raw: 1.part  3342173 bytes          (growing on disk, not in memory or /tmp)
# response when the body finished:
{"id":1,"origin":"upload","status":"PROBING",...,"source_creator_handle":"@acme","rights_status":"own_content",
 "content_type":"video/mp4",...,"raw_url":"/media/raw/1.mp4","thumbnail_url":null}
$ curl -s $A/clips/1
{'id': 1, 'status': 'READY', 'duration_s': 8.0, 'width': 1920, 'height': 1080, 'fps': 30.0, 'video_codec': 'h264',
 'has_audio': True, 'size_bytes': 6479456, 'thumbnail_url': '/media/thumbs/clip-1.jpg'}
GET /media/thumbs/clip-1.jpg 200 image/jpeg 12463
```
Rejected requests. The clip count stayed at 1 and `data/raw` held only `1.mp4` afterwards:
```
.avi filename                       {"detail":"file must be one of .mov, .mp4, .webm"} [415]
rights_status=maybe                 {"detail":"rights_status must be one of permission_granted, none, own_content"} [422]
handle bytes \xff\xfe@bad           {"detail":"form field names and values must be UTF-8"} [422]
no closing boundary (http.client,   {"detail":"incomplete multipart body"} [400]
  first 200000 bytes of the mp4)
GET clips/2147483648                {"detail":"integer out of range"} [422]
GET renders/99999999999             {"detail":"integer out of range"} [422]
POST renders clip_id=99999999999    {"detail":"integer out of range"} [422]
POST renders brand_id=99999999999   {"detail":"integer out of range"} [422]
GET clips/2147483647                {"detail":"source_clips 2147483647 not found"} [404]
overlay {"x":1,"y":1,"w":0.2}       Input should be less than 1 [422]
overlay {"x":0.9,"y":0,"w":0.2}     Value error, logo must stay inside the frame (x + w <= 1) [422]
overlay {"x":0.1,"y":0.1,"w":0.0004} Input should be greater than or equal to 0.01 [422]
```

### d) Render via curl, logo top right → READY, output passes the spec checks
```
$ curl -s -X POST $A/renders -H 'content-type: application/json' \
   -d '{"clip_id":1,"brand_id":1,"overlay_config":{"x":0.72,"y":0.06,"w":0.22,"opacity":1},"caption":"acceptance"}'
{'id': 1, 'status': 'PENDING', 'overlay_config': {'x': 0.72, 'y': 0.06, 'w': 0.22, 'opacity': 1.0}}
# after 6 s:
{'id': 1, 'status': 'READY', 'error_code': None, 'size_bytes': 3322788, 'duration_s': 8.0,
 'output_url': '/media/renders/1.mp4', 'thumbnail_url': '/media/thumbs/render-1.jpg'}
# worker: Job render[3](render_id=1) ended with status: Success, lasted 2.398 s
GET /media/renders/1.mp4 200 video/mp4 3322788
```
`data/qa/accept/spec.py` runs ffprobe, lists the frames and walks the top-level MP4 boxes. PASS means: one
video and one audio stream; h264 High yuv420p 1080x1920; range tv or untagged; avg and r frame rate 30/1;
SAR 1:1; no stream side data; AAC 48 kHz stereo; video duration = file duration (±0.05 s); moov before
mdat; a keyframe exactly every 60 frames.
```
render 1: PASS | h264 High yuv420p range=None trc=None 1080x1920 30/1 SAR 1:1 side_data=[] | aac 48000 ch2 |
  dur fmt 8.000 v 8.000 a 8.000 | frames 240 keys [0, 60, 120, 180] | atoms ['ftyp', 'moov', 'free', 'mdat']
```
`GET /api/renders/1` also returns `ffmpeg_log`, the ffmpeg stderr (it ends with the libx264/aac stats).

**Frame:** [`docs/phase-2-frame.png`](phase-2-frame.png) was taken at 2 s from `renders/1.mp4` and scaled to
540x960. The orange disc and bar sit top right, with testsrc2 showing through the transparent parts
of the logo.

### e) CLI (in the running worker container)
```
$ app.cli render 1 1 --x 0.05 --y 0.85 --w 0.4 --opacity 0.7
/data/renders/2.mp4                                   exit 0   (render 2 READY, overlay as given)
$ app.cli render 1 99
clip or brand not found, or the brand has no logo     exit 1
$ app.cli render 1 1 --w 0
bad overlay: w: Input should be greater than or equal to 0.01                          exit 1
$ app.cli render 1 1 --w 2
bad overlay: w: Input should be less than or equal to 1                                exit 1
$ app.cli render 1 1 --x 0.9
bad overlay: x, w: Value error, logo must stay inside the frame (x + w <= 1)           exit 1
$ app.cli render 1 1 --y 1
bad overlay: y: Input should be less than 1                                            exit 1
# probe-if-needed: clip 1 set to FAILED with its probe columns cleared (SQL), then
$ app.cli render 1 1
/data/renders/3.mp4                                   exit 0   (clip 1 re-probed: READY 1920x1080 8 s)
```
Ctrl-C mid-render: `app.cli render 6 1` on the 90 s clip, then SIGINT to the CLI process after 5 s:
```
before SIGINT: render 4 RENDERING        data/renders: 1.mp4 2.mp4 3.mp4 4.mp4.part
SIGINT 727 python -m app.cli render 6 1
KeyboardInterrupt                        exit 130
after SIGINT:  render 4 FAILED INTERRUPTED   data/renders: 1.mp4 2.mp4 3.mp4   (ffmpeg killed, .part removed)
$ POST /renders/4/retry  → {'id': 4, 'status': 'PENDING', 'error_code': None}
```

### g2) Postgres restarted mid-render
The retried render 4 (90 s source, ~23 s of ffmpeg) was RENDERING when `docker compose restart postgres`
ran:
```
before restart: render 4 RENDERING
06:28:22 render 4: READY - 90
worker: ERROR Side task listener failed with exception ... / Waiting for job to finish: render[5](render_id=4)
        Job render[5](render_id=4) ended with status: Success, lasted 23.316 s
        Stopped worker on all queues / Starting worker on all queues     (worker restarts=1)
```
The final status write used a fresh connection (pre-ping), so the render ended READY. Procrastinate's
listener loses its connection, so the worker process stops once the job is done and compose restarts it.
The api's first `/api/status` after the restart still said `db:false` in this run. That's why its engine
got `pool_pre_ping` too; re-checked afterwards, the first call after another restart returned `db:true`.

### f) From URL
```
POST /api/clips/from-url {"url":"https://www.youtube.com/watch?v=jNQXAC9IVRw","rights_status":"none"}
  → {"id": 7, "status": "READY", "platform": "Youtube", "source_creator_handle": "@jawed",
     "source_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "duration_s": 18.933333, "width": 320, "height": 240,
     "fps": 15.0, "video_codec": "av1", "has_audio": true, "thumbnail_url": "/media/thumbs/clip-7.jpg"}
POST ... {"url":"https://www.youtube.com/watch?v=aaaaaaaaaaa", ...}          (deliberately bad)
  → {"id": 8, "status": "FAILED", "error_code": "REMOVED", "error_detail": "ERROR: [youtube] aaaaaaaaaaa: This video is unavailable"}
POST ... {"url":"https://example.com/not-a-video", ...}
  → {"id": 9, "status": "FAILED", "error_code": "EXTRACTOR_FAILED",
     "error_detail": "ERROR: [generic] not-a-video: Unable to download webpage: HTTP Error 404: Not Found (caused by <HTTPError 404: Not Found>)"}
POST ... {"url":"https://www.youtube.com/@YouTube/shorts", ...}              (a channel tab: a playlist)
  → {"id": 10, "status": "READY", "platform": "Youtube", "source_creator_handle": "@YouTube",
     "source_url": "https://www.youtube.com/watch?v=wBA83zXaYcc", "duration_s": 22.4, "width": 1080, "height": 1920, ...}
data/raw: 1.mp4 10.mp4 6.mp4 7.mp4          (no dl-<id>/ work dirs left)
```
The channel URL gave its first Short only (`-I 1`). `yt-dlp --flat-playlist -I 1:5` on the same URL lists 5
entries, starting with `wBA83zXaYcc`. "Me at the zoo" now probes as 18.933 s: the video stream's
length. The format's 19.021 s came from the audio track.

The zoo clip rendered with a 9:16 crop through the API:
```
{'id': 5, 'source_clip_id': 7, 'crop_config': {'x': 0.33, 'y': 0.0, 'w': 0.421875, 'h': 1.0}, 'status': 'READY',
 'duration_s': 18.933333, 'output_url': '/media/renders/5.mp4'}
render 5: PASS | h264 High yuv420p range=tv trc=bt709 1080x1920 30/1 SAR 1:1 side_data=[] | aac 48000 ch2 |
  dur fmt 18.933 v 18.933 a 18.930 | frames 568 keys [0, 60, 120, 180, 240, 300]...
```

### g) Worker killed mid-render (the sweeper, then the render re-runs)
```
06:28:56 before kill: render 6 RENDERING | job 12 doing attempts 0
SIGKILL 7 [b'/venv/bin/python', b'/venv/bin/procrastinate', b'worker']
06:29:58 render 6 RENDERING | job 12 doing attempts 0
06:30:03 render 6 RENDERING | job 12 todo attempts 1      WARNING:app.tasks.queue:stalled job 12 (render) re-queued
06:30:29 render 6 READY     | job 12 succeeded attempts 2
render 6: PASS | ... dur fmt 90.000 v 90.000 a 89.984 | frames 2700 ...          renders/*.part left: 0
```

### Media edge cases (same fresh stack)
Every source in `data/qa/video/src/` (from the review's `gen.sh`), plus two real HDR samples, was
uploaded through the API and rendered with brand 1's default overlay. `spec.py` then checked every
render:
```
file                     clip                                       render
01_rot_m90.mov           READY 1080x1920 6.0 s                      7  PASS
02_hlg.mov               READY 1920x1080 arib-std-b67               8  PASS (range tv, trc bt709)
03_fps120.mp4            READY 120 fps                              9  PASS
04_vfr.mp4               READY 33.898 fps (avg) 5.9 s               10 PASS 179 frames 5.967 s
05_noaudio.mp4           READY no audio                             11 PASS (silent AAC)
06_odd_1279x719.mp4      READY 1279x719                             12 PASS
07_short_2s.mp4          FAILED DURATION_OUT_OF_RANGE 2.0 s         -
08_vp9_opus.webm         READY 6.008 s (format; no stream duration) 13 PASS
09_4x3_1440x1080.mp4     READY                                      14 PASS
10_two_audio.mp4         READY                                      15 PASS
11_moov_end.mov          READY                                      16 PASS
12_hlg_rot_m90.mov       READY 1080x1920 arib-std-b67               17 PASS (range tv, trc bt709)
13_pq.mp4                READY smpte2084                            18 PASS (range tv, trc bt709)
14_rot_p90.mp4           READY 1080x1920                            19 PASS
15_audio_longer.mp4      READY 5.0 s   (was 8.0)                    20 PASS v 5.000 a 4.992 fmt 5.000 (was 8.000)
16_fullrange.mp4         READY                                      21 PASS yuv420p (was yuvj420p/pc)
17_fullrange_real.mp4    READY                                      22 PASS yuv420p (was yuvj420p/pc)
18_video2s_audio4s.mp4   FAILED DURATION_OUT_OF_RANGE 2.0 s (was READY 4.0)
real_iphone_hlg_20s.mp4  READY 854x480 59.94 fps arib-std-b67       23 PASS (no mastering display side data)
real_pq_20s.webm         READY 854x480 59.94 fps smpte2084          24 PASS (no mastering / light level side data)
```
Full range, decoded to RGB at 1 s (bars from the pattern: red top, grey middle, blue bottom):
```
render 11 (05_noaudio, limited-range reference)  red (252, 0, 0) | grey (126, 126, 126) | blue (0, 0, 253)
render 22 (17_fullrange_real)                    red (252, 0, 0) | grey (126, 126, 126) | blue (0, 0, 253)
render 21 (16_fullrange)                         red (236, 15, 13) | grey 126 | blue (13, 13, 237)
  (its source already decodes to (238, 14, 14) / 128 / (16, 15, 239): gen.sh's double range conversion)
```

### HDR tonemap (the review's "dim" finding)
The review measured the chain on synthetic HLG/PQ built per BT.2408 (SDR white at 75% HLG / 203 nits) and
suggested `npl=203` + `mobius`. To check that on real footage, YouTube's HDR and SDR versions of the same
uploads were downloaded at 480p. YouTube serves both iPhone uploads as HLG (`arib-std-b67`); the Costa
Rica video is PQ. Frames at 10 timestamps went through each variant, and each was compared with YouTube's
own SDR frame at the same time (mean absolute RGB error and mean luma difference; `data/qa/hdr/`):
```
                                               npl=100 hable (kept)  npl=203 mobius     npl=203 hable     npl=100 mobius
iPhone 15 Pro Max HLG (rnMldHU0JVg)  MAE / dY   18.4 / +9.5          27.7 / +20.1       15.8 / -11.0      47.1 / +40.1
iPhone 15 Pro HLG     (5MCiTzfSUqM)  MAE / dY   15.1 / +8.2          26.4 / +21.5       16.1 / -14.2      45.1 / +40.7
Costa Rica PQ HDR10   (LXb3EKWsInQ)  MAE / dY   10.5 / -2.5          16.3 / +12.3       27.0 / -26.3      37.7 / +37.5
```
On real footage the current chain is already a little brighter than YouTube's SDR, and the suggested
variant is about twice as far off. So the chain was not changed. What was wrong was the HDR side data
carried into the output, which is fixed.

### h) Tests
```
$ docker compose run --rm worker pytest
tests/test_api.py      10 passed   upload → probe → READY, render → READY, PNG alpha + truncated PNGs, render and overlay
                                   validation, ids past int4, rejected uploads leave nothing (+ non-UTF-8 field,
                                   missing closing boundary), OUTPUT_TOO_LARGE, FFMPEG_FAILED + retry, 2 s clip →
                                   DURATION_OUT_OF_RANGE, from-url defers a job, download_clip with a fake yt-dlp,
                                   CLI interrupt + bad overlay
tests/test_db.py        4 passed
tests/test_queue.py     1 passed   (+ gave-up render job → render FAILED, gave-up probe job → clip FAILED WORKER_CRASHED)
tests/test_render.py   39 passed   build_ffmpeg_args units, 15 yt-dlp strings, logo/crop bounding boxes
                                   (16:9, 9:16, 4:3, rotated 9:16 × full/crop), output spec × 7 sources, probe of
                                   generated media
tests/test_services.py  5 passed
59 passed in ~23 s                 (api container: 39 passed, 20 skipped: no ffmpeg)
```
- **Logo and crop boxes.** The bounding-box test uses a black source with a red square at (0.55, 0.6) of
  the display frame, off-centre on purpose, and a green 200x100 RGBA logo. It reads the G and R planes of
  an output frame. It asserts that:
  - the logo box is within 1% of x=0.72, y=0.06, w=0.22 (and of the matching height);
  - the red square lands where the crop maths says, within 1%.

  The rotated case stores the portrait frame turned 90° clockwise (coded 1920x1080) with
  `-display_rotation 90`. It uses an asymmetric crop, so the square only lands right if the probe's display
  dims and ffmpeg's autorotate agree (PLAN D3).
- **Output spec sources:**
  - 16:9 with two audio tracks (only the first is kept);
  - a display-matrix-rotated copy (probed as 1080x1920; the output has no display matrix);
  - HEVC 10-bit HLG with mastering display + light level SEI and no audio (tonemapped; the output is tagged
    bt709 and has no side data; a silent AAC track is added);
  - 120 fps (becomes 30 fps);
  - 1279x719 4:4:4 with a crop;
  - 4 s of video with 6 s of audio (the output is 4 s; video = file duration);
  - full-range yuvj420p (the output is yuv420p, limited range).
- **Download test.** yt-dlp is faked at `subprocess.run`. The test checks three things: the cookie copy
  exists during the run and is outside `DATA_DIR`; a handle PATCHed mid-download survives; an exit 0 with
  no file gives the MAX_UPLOAD_BYTES hint.
- **Mutation checks** (each reverted change makes a test fail): format-first probe duration; old audio
  mapping without `apad`/`-shortest`; no `out_range=tv`; `-noautorotate`; no closing-boundary check;
  snapshot handle; cookies in the work dir; no DataError handler; CLI without the guard; no `probe_clip`
  give-up SQL; the old exit-0 classification; no `x + w` check; no `sidedata=mode=delete`. From the first
  pass (not re-run): scaling the logo by `iw*` failed by 150 px, the crop's y used for x failed the crop
  cases, and removing the `GIVE_UP_SQL` call failed the sweeper test.

## Review fixes (second pass)
| Review item | Result |
|---|---|
| ids past int4 → 500 | fixed: `DataError` → 422 |
| truncated PNG accepted as logo | fixed: IDAT + IEND required, 422 `logo PNG is corrupt` |
| non-UTF-8 form field → 500 | fixed: 422 |
| body without closing boundary accepted | fixed: 400 `incomplete multipart body` |
| overlay could sit outside the frame | fixed: `x + w ≤ 1`, `y < 1` |
| CLI traceback on a bad overlay | fixed: one line, exit 1 |
| audio longer than video: silent tail, wrong duration | fixed: `apad` + `-shortest`, video-stream duration |
| full-range source → yuvj420p output | fixed: `out_range=tv` |
| HDR tonemap dim | not changed: real footage says the current chain is closer (table above) |
| `w` below 1/2160 drawn full size (reported twice) | fixed: `w ≥ 0.01` |
| rows stuck in RENDERING/PROBING/DOWNLOADING | fixed: DB `RetryStrategy`, `pool_pre_ping`, CLI `INTERRUPTED` |
| blocking file I/O in async endpoints | fixed: `run_in_threadpool` |
| playlist URLs download every entry | fixed: `-I 1` |
| cookies copy served under /media | fixed: temp dir outside `DATA_DIR` |
| exit 0 with no file misclassified | fixed |
| operator handle overwritten by the download | fixed: `coalesce` in the CAS |
| "Sign in to confirm" → EXTRACTOR_FAILED | fixed: → PRIVATE |
| `[0:v]` could pick cover art | fixed: `[0:V:0]` (not reproducible with ffmpeg-written files) |
| rotation never proven by a test | fixed: rotated bbox case + no display matrix in the output |
| clip give-up SQL untested | fixed: test_queue covers `probe_clip` |
| (found in this run) HDR metadata copied into SDR output | fixed: `sidedata=mode=delete` |

## Could not verify
- **Original phone files.** The real HDR check used YouTube's 480p re-encodes of iPhone HLG uploads, and
  a PQ HDR10 master. It didn't use an original iPhone .mov with a Dolby Vision 8.4 RPU and ambient
  viewing metadata, so the reference was YouTube's SDR conversion, not Apple's. VFR was rendered only
  from a synthetic 60→20 fps file.
- **Other platforms.** Instagram, TikTok and X downloads were not tried; only YouTube (plus the generic
  extractor's 404). Their classifier strings come from research, not live runs. `YTDLP_COOKIES_FILE` was
  exercised only with the fake yt-dlp in pytest, not with real cookies. `-I 1` was checked live on a
  YouTube channel tab, not on an Instagram carousel or a multi-video tweet.
- **DB retry path.** The `OperationalError` RetryStrategy never fired live: in the Postgres restart
  test, pre-ping reconnected before any write failed. A DB that is still down at the final write (retried
  3 times, 30 s apart) was not staged.
- **Cover art first.** No ffmpeg muxer wrote a file whose first video stream is `attached_pic` (mp4 puts
  the cover last; mkv stores it as a normal png track), so `[0:V:0]` is covered only by the rest of the
  ffmpeg tests still passing.
- **Upload limits.** Uploads near 2 GB were not tried. The `MAX_UPLOAD_BYTES` 413 is covered by pytest
  with a small limit. An early 413/415 is sent before the client has finished sending. curl (6.5 MB)
  received the JSON error, but a browser XHR sending a large file may see a network error instead. The
  Phase 3 upload UI should check extension and size first.
- **Tests run jobs inline.** API tests call `probe_clip` / `render` / `download_clip` directly (no worker
  listens on clipper_test). The queued path was exercised only in the live acceptance above.
- **Throughput.** 4 concurrent renders at `FFMPEG_THREADS=2` were not measured, and neither was a
  15-minute source against `RENDER_TIMEOUT_S` (3600).
- **Clip left UPLOADING.** If the api process dies mid-upload, the row stays UPLOADING with a
  `raw/<id>.part`. Clearing it (`UPLOAD_ABANDONED` after 24 h) is PLAN section 4's dispatcher job
  (Phase 4).
- **amd64.** Only built and run on arm64.
