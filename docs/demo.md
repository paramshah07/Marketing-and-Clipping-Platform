# Clipper V1: 5-minute demo

This demo covers the V1 flow on localhost. A clip gets a brand logo in the editor, renders to a 1080x1920
Reel, is scheduled on the calendar and approved, and then cancelled. After that it shows the Published
tab, a Recover screen, Brands and Accounts. Publishing stays off unless the operator decides otherwise
(see step 3 of the checklist). The whole script works with publishing off.

## Pre-demo checklist (about 10 minutes, do it before the audience arrives)

1. **Stack up.** From the repo root, run `docker compose up -d --build`, then `docker compose ps`. Postgres
   should be healthy and api and worker should be Up. Then run `curl -s http://127.0.0.1:8000/api/status`
   and check for `"db":true` and `"worker_alive":true`.
2. **Dev server.** Run `cd frontend && npm run dev` and open http://localhost:5173 in Chrome. Use a
   window of at least 1440x900 and close DevTools. The sidebar footer should read "Worker online".
3. **Publishing on or off.** Decide this before the demo.
   - **Off (recommended, and the default).** `.env` has `PUBLISHING_ENABLED=false`. The sidebar footer
     shows an amber **Publishing off**, and `/api/status` returns `"publishing_enabled":false`.
     Scheduling, approving, calendar moves and Recover screens all work, and nothing reaches Instagram.
   - **On (only with the operator's go-ahead for this specific demo).** Set `PUBLISHING_ENABLED=true` in
     `.env`, run `docker compose up -d --force-recreate api worker`, and check that "Publishing off" is
     gone from the footer. From then on, every Scheduled post whose time comes goes out as a real Reel on
     @i.cant.de, and Zernio cannot delete it. Turn it back off (same command) straight after.
4. **Account.** Go to Accounts and press **Sync accounts**. This is a read-only Zernio call and proves the
   key works. @i.cant.de should show Connected, America/New_York, slots 09:00 / 13:00 / 19:00, daily cap 10
   and min gap 30.
5. **Clip and brand.**
   - Clip: use `clip-16x9.mp4` (clip 1, 8 s, landscape) to show the 9:16 crop, or
     `tiktok.com/@ramseycheframbo/…` (clip 81, 33 s, portrait) for a real-looking Reel. Open `/editor/1`
     or `/editor/81` once beforehand so the video is cached. Both clips have rights set to "own content",
     so scheduling won't ask for a rights override.
   - Brand: use **Kite VPN**. It is not auto-approve, so the post saves as a Draft and you can show the
     Approve step. With publishing on, do not use Northwind Coffee or Flux Energy: they auto-approve, so
     their posts go straight to Scheduled.
6. **Tidy the data (the operator's call).** Most of the dev data is QA fixtures: 7 copies of
   `phase3-src.mp4`, `01_rot_m90.mov` … `18_video2s_audio4s.mp4`, 6 active "Phase3 Test Co" brands, and
   a Ready to schedule tray of 40. Either say so up front, or archive the Phase3 Test Co brands first
   (Brands → open the brand → Archive). Archiving hides a brand from the pickers and keeps its renders
   and history. To find the demo clip in the Library, use its search box (`/`).
7. **Seed one failed post for the Recover screen.** With publishing off nothing can fail for real, and
   the dev data has no failed posts. Take any READY render id: the render card in the editor shows it,
   or run `curl -s 'http://127.0.0.1:8000/api/renders?unscheduled=true'`. Pick a slot at least two days
   out. For example, 13:00 London is 12:00Z while BST lasts.
   ```sh
   curl -s -X POST http://127.0.0.1:8000/api/posts -H 'content-type: application/json' \
     -d '{"render_id":<render_id>,"account_id":1,"scheduled_for":"<YYYY-MM-DD>T12:00:00Z","caption":"DEMO seeded failure, do not publish"}'
   docker compose exec postgres psql -U clipper -c "UPDATE posts SET status='FAILED', error_code='CONTENT_REJECTED', error_detail='{\"errorMessage\":\"demo: seeded failure\"}' WHERE id=<post_id> AND caption LIKE 'DEMO%'"
   ```
   The calendar then shows it as a red card, and `/recover/<post_id>` opens its Recover screen. A seeded
   failure sends no Telegram alert, because only the publish task sends those.
8. **Rehearse once, then clean up** (see "After the demo").

## How not to post by accident

- Keep publishing off. That is the only setting that guarantees nothing reaches Instagram.
- If publishing is on:
  - Schedule at least a day ahead, never "a few minutes out".
  - Do not press **Select all → Auto-schedule** in the calendar tray, or **Approve all drafts**.
    Auto-schedule fills the next free slots, and the first one can be about 10 minutes away.
  - On a Recover screen, do not press the remedy button. "Re-render and retry" and "I've reconnected:
    check now" both move the post to the next free slot.
- Cancel every demo post before you finish. A Scheduled post left behind while publishing is off just
  waits. If publishing is turned on later, the dispatcher moves it to the next free slot (MISSED) and
  publishes it.

## The script (about 5 minutes)

Each step lists what to click, what to say, and what the audience sees.

### 1. Library (0:00–0:30)
- **Click:** Library in the sidebar.
- **Say:** "Every source video lives here. Drop in files or paste a TikTok or YouTube URL. Clipper probes
  each one and records its rights, so we only post what we're allowed to."
- **They see:** a dense table with a 9:16 thumbnail, duration, resolution and fps, a rights chip, Ready
  or Failed status (failed rows say why in plain words, such as "Must be 3 s to 15 min"), and a render
  count. The drop zone and URL field are at the top. The footer shows Worker online, Publishing off and
  the counts.
- **Optional:** drag a short mp4 onto the drop zone to show the upload progress row. If you do, remove
  the clip afterwards.

### 2. Editor (0:30–1:45)
- **Click:** Open editor on the demo clip. Pick **Kite VPN** under Brand. Drag the logo, then move the
  Scale and Opacity sliders or click a Position cell. Toggle **IG overlay** off and on. On
  `clip-16x9.mp4`, turn **Crop** on, drag the 9:16 region, and switch back to Output. Then press
  **Render** (⌘↵).
- **Say:** "This is exactly what Instagram will show. The dashed box is the Reels safe zone, and the
  overlay is the real app chrome with our handle. The caption comes from the brand's template, with the
  link and creator filled in. Render runs ffmpeg on the worker. The output is always a 1080x1920 H.264
  Reel with AAC audio, and what you see here matches the rendered frame to within 1%."
- **They see:** the Reels mock with @i.cant.de and the logo on the frame. A render card appears on the
  right as Rendering and turns Ready within a few seconds, showing duration and size. Press **Preview**
  to play the actual MP4.

### 3. Schedule (1:45–2:15)
- **Click:** **Schedule…** on the Ready render card. Keep @i.cant.de. The time field already holds the
  next free slot. Set the date to at least tomorrow, then press **Schedule**.
- **Say:** "It suggests the account's next free slot, in the account's own timezone, respecting the
  daily cap and minimum gap."
- **They see:** "Saved as a draft for <day> 13:00 on @i.cant.de", then "This brand needs approval…",
  then in amber "Publishing is off: nothing reaches Instagram until it is turned on." Click
  **Open calendar**.

### 4. Calendar (2:15–3:15)
- **Click:** the dashed Draft card in its slot, then **Approve** in the drawer. Then **Cancel post**.
- **Say:** "One lane per Instagram account, with slots at 09:00, 13:00 and 19:00 London and a daily cap
  bar. Brands that aren't auto-approve wait as drafts until I approve them. Approving makes it
  Scheduled, and a worker publishes it at that minute through Zernio. It keeps an idempotency key, so a
  crash or retry can never post twice. I'll cancel this one so nothing goes out."
- **They see:** the week grid, with the Ready to schedule tray on the left and the drawer on the right
  showing the preview, time and editable caption. The status goes Draft → Approved (blue dot) →
  Cancelled, and the card leaves the grid. Use the ‹ › arrows to go back to 21–27 Sep: the six green
  published Reels on Sat 26 show no gap warnings.

### 5. Published tab (3:15–3:35)
- **Click:** Library → **Published**.
- **Say:** "These are the Reels Clipper actually published to @i.cant.de on 26 Sep. They were our live
  acceptance runs, including tests where we killed the worker mid-publish and it still posted exactly
  once. View on Instagram opens the live Reel."
- **They see:** 6 rows with time, account, brand, caption and **View on Instagram**, plus "Re-render
  for…" to reuse a clip for another brand. Times show in the laptop's timezone, with the London time
  underneath. The captions are test captions. Say so; Zernio can't delete Reels.

### 6. Recover screen (3:35–4:10)
- **Click:** the seeded red card on the calendar, or open `/recover/<post_id>`. Narrow the window, or
  open it on a phone on the same machine, to show the mobile layout. Expand **Technical details**.
- **Say:** "If a post fails, the Telegram alert links here. It gives one plain sentence on what went
  wrong and one button that fixes it. Here Instagram rejected the video, so the fix is re-render and
  retry. Raw error codes stay under Technical details."
- **They see:** a Failed chip with "Instagram rejected the video", the render preview, the account and
  time, the cause sentence, a single **Re-render and retry** button with "publishes it in the next free
  slot (…)", and the collapsible technical details.
- **Do not press the button** if publishing is on. With publishing off it is safe to press. It creates a
  new render and moves the post to the next free slot. Cancel that post afterwards.

### 7. Brands (4:10–4:35)
- **Click:** Brands. Open one brand, then close it.
- **Say:** "Each advertiser has a transparent PNG logo, a default position, a caption template with
  `{link}` and `{creator}`, and an auto-approve switch. An opaque logo is refused, because it would cover
  the video."
- **They see:** a table with logo, name, caption template, link, a placement thumbnail and the
  auto-approve switch, plus the "Show archived" toggle. The brand drawer shows the logo on a checkerboard,
  the template placeholders and the default placement.

### 8. Accounts (4:35–5:00)
- **Click:** Accounts.
- **Say:** "Accounts come from Zernio. We connect Instagram there, not through Meta directly, and press
  Sync. Each account has its own timezone, slots, daily cap and minimum gap, and shows the Meta quota
  and today's count."
- **They see:** the @i.cant.de card showing Connected, "Meta: n/100 used (24h)", "Today: n/10 cap", Last
  published and Next, with the timezone, slot chips, daily cap and min gap fields.

## After the demo

1. Calendar: open each demo or seeded post and choose **Cancel post**, so nothing is left Scheduled,
   Draft or Failed.
2. Editor: delete each demo render with the trash icon on its card. This also removes its cancelled
   posts. A render with a live post is refused, and the alert says why.
3. Library: remove any clip you uploaded. The trash icon works once the clip has no renders.
4. If publishing was turned on, set `PUBLISHING_ENABLED=false` and run
   `docker compose up -d --force-recreate api worker`.

## Known limitations (V1)

- **Localhost only, one operator.** There is no login, no tunnel and no cloud storage. Files live in
  `./data`.
- **Publishing goes through Zernio to one Instagram account.** Reels must be 3 s to 15 min, and uploads
  are capped at 2 GB. Zernio cannot delete a published Reel. The six live Reels on @i.cant.de carry test
  captions and have to be archived in the Instagram app if they should go.
- **Publishing off is silent apart from the UI.** Scheduled posts stay Scheduled past their time. The
  sidebar footer and the schedule confirmation are the only signals.
- **Disconnected accounts.** A 403 or auth-expired failure now marks the account disconnected, and the
  next sync moves its failed posts to free slots. This is covered by backend tests only. It has never
  happened live, and checking it live would mean a real failed publish.
- **No trimming, timeline or multi-clip editing,** by design. One clip, one logo, one caption per render.
- **Redundant render.** If a failed render was already retried in the editor, "Re-render and retry" on
  its post runs one more ffmpeg job. It wastes a render but never duplicates a Reel.
- **Timezones.** The Published tab shows times in the laptop's timezone, with the account time
  underneath. The calendar and Accounts screens use each account's own zone.
- **Demo data.** The dev database is mostly QA fixtures (see checklist step 6). A clean demo needs a
  fresh database or the operator's own tidy-up.
- **Frontend bundle.** It is one 550 kB chunk, and Vite warns about it at build time. This doesn't
  matter on localhost.
