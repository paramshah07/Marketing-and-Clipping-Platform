# Phase 3: frontend (library, upload, editor, brands)

What exists now, in `frontend/`:
- **Scaffold**: `npx shadcn@latest init -t vite` (shadcn 4.21, preset nova, radix), then trimmed: no
  ThemeProvider (it used localStorage), no prettier, no button/dialog/select, and `shadcn` is not a
  dependency (its `tailwind.css` was imported only for two variants; `index.css` declares
  `data-checked` / `data-unchecked` itself; `npx shadcn add` still works through `components.json`).
  Dark only (`class="dark"` on `<html>`). React 19.2, Vite 8, Tailwind v4, TypeScript 6, react-router 8.4,
  TanStack Query 5, vitest 5.
- **Tokens** (`src/index.css`): the mockup colours, font sizes and radii from `docs/design/_base.html` as
  Tailwind v4 `@theme` variables, so the mockup class names (`bg-panel`, `text-subtle`, `border-line`,
  `text-base` = 13/18 ...) work as written. shadcn's variables (`--background`, `--primary`, `--border`,
  `--ring` ...) are mapped onto them. Geist + Geist Mono from `@fontsource-variable`.
- **Focus**: one global `:focus-visible` ring, 1 px `fg` at 60 % with a 1 px offset (neutral, so accent
  stays for primary actions). Bare inputs/selects inside bordered wrappers (search, URL, brand select,
  margin, drawer Link) light the wrapper with `focus-within:border-muted`; `field`, the textareas and the
  rights chip use `focus:border-muted`. Slider thumbs and switches keep their accent ring.
- **API client**: `npm run gen:api` runs `@hey-api/openapi-ts` 0.99.0 (exact pin) on
  `../backend/openapi.json` into `src/api` (client-fetch + TanStack Query plugins). Every server call goes
  through the generated `*Options()` / `*Mutation()` helpers, except the clip upload (XHR, for progress).
  Same origin in the browser: Vite proxies `/api` and `/media` to `127.0.0.1:8000`.
- **Screens** (`src/routes/`, shell in `src/App.tsx`):
  - Sidebar as in `_base.html`; worker status and job counts from `GET /api/status` every 10 s.
    `/calendar` and `/accounts` say "Coming in Phase 4".
  - `/library` (`/` redirects): Clips | Published tabs (`?tab=published`, empty until Phase 5). Drop
    strip (multi-file drop, Upload button = file picker, also from the Published tab, which switches back)
    with URL import, a rights select and an optional @handle that apply to the next drop or import. Dense
    table, 56 px rows with a hover background, 28x50 thumbs with a larger preview on hover, platform glyph
    (TikTok / Instagram / YouTube, Globe otherwise) before the @creator. Rows poll every 2 s until
    READY/FAILED (an UPLOADING row older than 10 min is treated as an orphan and not polled for).
  - `/editor/:clipId` (`?brand=<id>` preselects a brand): stage, controls, render queue.
  - `/brands`: table + docked drawer (create/edit, logo, archive), "Show archived".
- **Geometry** (`src/lib/geometry.ts`, tests in `geometry.test.ts`): the preview-vs-render contract.
  `outputView` places the source `<video>` inside the 9:16 stage exactly as ffmpeg will (crop floored to
  even px like `crop_px`, then scale to cover and centre-crop; no crop = `object-fit: cover`).
  `snapPosition`, `moveBox`, `resizeBox`, `crop916`, `logoAspect`. `IG` holds the safe-zone fractions
  (`ponytail:` comment: approximations).
- **FracBox** (`src/components/FracBox.tsx`): the one drag/resize component. Pointer events,
  `setPointerCapture`, `touch-none`; stores `{x, y, w, h}` as fractions of its parent's
  `getBoundingClientRect()`; corner handles keep the aspect, and with `edges` (the crop) four edge handles
  do too (each uses the adjacent corner's maths with the other axis ignored). The drag ends on
  `lostpointercapture`, so it can't outlive its capture. Used for the logo (4 handles) and the crop (8).
- **Acceptance script**: `frontend/e2e/accept.mjs` (playwright-core, Google Chrome).

## Decisions worth knowing
- **Preview == render by construction.** The stage never asks the server for a preview. Output mode
  positions the same `<video>` (or the thumbnail, if the browser can't decode the source) with
  `outputView`, and the logo box at `x*W, y*H` with width `w*W` and height from the logo's own aspect, which is what
  `scale=round(w*1080):-1` + `overlay=round(x*1080):round(y*1920)` do. One `<video>` element serves
  Output, Crop and Preview, so switching modes never reloads the source.
- **Crop is locked to 9:16 in source pixels** (`h/w = 16/9 * srcW/srcH` in fractions). Presets: "9:16
  region" = the largest centred 9:16 region; "Full frame" = `{0, 0, 1, 1}`, which renders the same as crop
  off (cover). Dragging a corner of a full-frame box locks it back to 9:16. Readout shows the even-floored
  region in px and the cover scale ("Region 606x1080, upscaled 1.78x to 1080x1920").
- **Snap grid** positions the logo inside the IG safe zone (6% sides, 14% top, 35% bottom, dashed on
  the stage). The margin is a % of the output width, applied as the same px on both axes. A drag clears
  the snap. With a snap active, the scale slider and the margin input re-apply it, so the logo stays in
  its corner.
- **Scale** is 2-60% of the width, and never more than `1 / aspect` (a tall logo must also fit the frame's
  height), for the slider, the snaps and the handles. The logo box is clamped inside the frame (the backend
  only checks `x + w <= 1`, `0 <= y < 1`); `clamp()` lets the lower bound win, so y can't go negative.
- **Uploads**: XHR multipart to `POST /api/clips` with `upload.onprogress` (MB/s = average since the start).
  Extension, empty files and size (2 GB) are checked before sending, because the api's early 413/415 can
  reach a browser as a bare network error. Each file gets its own row at once (sizes in KB under 1 MB,
  MB with one decimal above). The rows live in a small module-level store (`useSyncExternalStore`), not
  in the component, so progress, Cancel and Retry survive the Published tab and other pages; a reload
  loses them, so `beforeunload` asks first while any upload is in flight. Failures:
  - network error → "Upload interrupted — network" + Retry (re-sends the same File, same rights/handle);
  - HTTP error → the api's `detail` + status (413/415/client-side checks → Dismiss only, rights "—");
  - Cancel aborts the XHR.

  While a POST runs, the api already has an UPLOADING row; the newest N such rows are hidden while N of
  our uploads are in flight (`ponytail:` matched by count, since the id only arrives with the response).
- **Drops outside a drop zone** are swallowed by a window-level `dragover`/`drop` guard in `main.tsx`
  (no-drop cursor), so Chrome can't open the file in the tab and unload the app mid-upload.
- **Failed clips**: the code in mono beside the Failed chip, the cause in words below (full
  `error_detail` on hover), as on the render cards. Retry (`POST /clips/{id}/retry`) only where a retry
  can help. PRIVATE, REMOVED, GEO_BLOCKED, DURATION_OUT_OF_RANGE, PROBE_FAILED and UPLOAD_ABANDONED get
  "Remove" instead, as in the mockup. A failed URL import says "URL import", not "Importing".
- **Row actions** end in a fixed 28 px slot, so "Open editor" / "Remove" / "Cancel" / "Dismiss" line up.
  The trash (Remove) is only live where the api can delete: READY without renders, or a retryable FAILED
  clip. A READY clip with renders shows it disabled ("Delete its renders first"). "Open editor" on a
  clip that isn't READY is plain text (not a link, not focusable). Action errors (409 etc.) float over the
  table header, so nothing shifts.
- **Brands**: the logo is sent after the brand is saved (`POST /brands` then `POST /brands/{id}/logo`),
  through the generated client (multipart via its FormData serializer). Before sending, the drawer decodes
  the PNG and looks for one pixel with alpha < 255 ("logo PNG has no transparent pixels"): the api only
  checks for an alpha channel / tRNS, which a design tool's opaque RGBA export passes. If the logo is
  refused (by either check), the brand exists and the drawer stays open on it with the error, so Replace +
  Save fixes it. "Edit in editor" opens the newest READY clip with `?brand=<id>`. The brand name is a
  button, so a keyboard user can open the drawer (Tab, Enter); rows keep the click for the mouse. Archived
  dates read "26 Sep".
- **Editor extras**: "Save as brand default" PATCHes `default_overlay_config` (enabled only when the
  logo differs from the default, shown as "edited"). Caption = template with `{link}` and `{creator}`
  filled in, counters for 2200 characters and 30 hashtags. Over either limit the counter turns red and
  both the Render button and ⌘↵ are blocked (the api also refuses captions over 2200:
  `RenderCreate.caption` has `max_length=2200`). A warning over the Render button when the clip is longer
  than 90 s. ⌘↵ / Ctrl+↵ renders (key repeat ignored; no second render until 1 s after the last one
  settled, because a localhost POST settles in ~15 ms, before a fast double press lands), ⌘S / Ctrl+S
  saves a brand.
- **Editor page states**: a clip that isn't READY yet is polled every 2 s and the editor opens by itself;
  a FAILED clip says so ("This clip failed (CODE); there is nothing to edit"). If the clip or brands
  query fails, the error is shown instead of "Loading…".
- **Stage size**: 420-576 px tall as the height allows, and never wider than its column (the height
  follows the width below that), so at 1024x768 the stage is 176x312 inside its column instead of
  overlapping the sidebar. Crop mode's source box is up to 592 px wide (the mockup's).
- **Crop presets**: only the first matching preset lights up (on a 9:16 source "9:16 region" and "Full
  frame" are the same box); the readout says "no scaling" at 1.00x.
- **Accessibility**: slider thumbs carry the slider's `aria-label` (Logo scale, Logo opacity, Seek);
  the margin input is "Logo margin, % of width"; snap cells are "Top left" … "Bottom right" with
  `aria-pressed`; the segmented buttons (Clips/Published, Output/Crop, crop presets) expose
  `aria-pressed`.
- **Errors**: a failed clips or brands list says so (not the empty state); render Retry and the
  auto-approve switch alert on failure; the log dialog shows a fetch error instead of "No log.".
- **Render queue**: polls every 2 s while a render is PENDING/RENDERING. Cards show Queued (chip and a
  static track: nothing has started) / Rendering (spinner, indeterminate accent bar, elapsed since
  `updated_at`), Ready (duration · size, Preview swaps the stage to the MP4 with a "RENDER 60 · brand ·
  1080x1920" label above it, download link, "Schedule…" disabled with the tooltip "Phase 4"), Failed
  (`error_code` in mono, View log = `ffmpeg_log` from `GET /renders/{id}` in a native `<dialog>`, Retry).
- **No localStorage / sessionStorage** anywhere: all state is React state or the server's.

### Deviations from the task / mockups (deliberate, small)
| What | Instead | Add when |
|---|---|---|
| TanStack Table v9 | plain `<table>` + `map()` | sorting, selection or column state is needed (Phase 4 tray, Phase 5 filters) |
| Checkbox column, kebab menus in the library | none; a trash icon (Remove) in the kebab's 28 px slot, disabled when the clip has renders | bulk actions exist |
| Queued render card (not in the mockup) | Queued chip + static grey track, no spinner | — |
| Crop edge handles: centred growth | an edge handle grows from the opposite edge, anchored like the adjacent corner (n → ne, s/e → se, w → sw) | it feels wrong in use |
| shadcn Select / Dialog / Tooltip | native `<select>`, `<dialog>`, `title` | a native one falls short |
| Brand "Usage" counts | omitted | the API returns counts |
| react-router 8 needs Node ≥ 22.22 (`engines`) | installed on the host's 22.20 (npm warns); dev, build and tests all run | upgrade Node |

## Acceptance

Stack: `docker compose up -d` (Phase 2 images), `npm run dev` on :5173. Run 2026-09-26, macOS arm64,
Google Chrome 149 via playwright-core 1.61.1. No Zernio call, nothing published. Re-run after the QA fix
round below.

### Build, types, tests, lint
```
$ npm run build        ✓ built in 153ms   (dist/assets/index-*.js 437 kB, 139 kB gzip)
$ npm run typecheck    exit 0
$ npm test             Test Files 1 passed (1) · Tests 9 passed (9)
$ npm run lint         exit 0
$ docker compose run --rm worker pytest    59 passed   (after RenderCreate.caption max_length=2200)
```
`geometry.test.ts` checks against the backend formulas:
- FracBox move clamps; corner resize keeps the opposite corner and the aspect, and clamps at the frame
  edges and at min/max width. Edge handles (`n/s/e/w`) use one axis only and keep the opposite edge.
- A box taller than the frame starts at y = 0, never above it (`clamp` lets the lower bound win; a 60x400
  logo at 60% width snapped bottom-left gives y 0, not -1.25).
- Logo 400x200 at w 0.22, top-right snap with a 4% margin: x 0.68, y 0.1625 = backend px
  `{x: 734, y: 312, w: 238, h: 95}`; the preview height (from the logo's aspect) is within 0.5 px of the
  render's. All 9 snaps sit inside the safe zone.
- Crop lock: 1920x1080 gives `{x 0.341796875, w 0.31640625, h 1}` → `crop_px` `[606, 1080, 656, 0]`.
  320x240 gives w 0.421875 (the Phase 2 crop render). A resized crop stays 9:16 in px.
- `outputView`: no crop = `object-fit: cover`. With a crop, the region's edges land on output x = 0 and
  1080, and the vertical offset is within the 2 px that ffmpeg's centre-crop of 1080x1924 removes.

### Playwright run (`node e2e/accept.mjs`, exit 0)
Media is made by ffmpeg in the worker (`data/qa/phase3/`):
- `src.mp4`: 1920x1080, 30 fps, 8 s, 17 MB. Dark noise and a grid, with a red 80x80 marker at (880, 500)
  to check the crop / cover.
- `logo.png`: 400x200 RGBA, a green cross touching all four edges, the rest transparent.
- `logo-opaque.png`: RGB white.

What the script does:
- **Upload**: drops the file on the drop zone (a synthetic `drop` with a `DataTransfer`), with the upload
  throttled to 3 MB/s through CDP.
- **Brand**: creates it with the opaque PNG first (refused), then replaces it with the transparent one.
- **Editor**: picks the brand, drags the logo (-90, +160 px), scale +5% and opacity 85% with the arrow keys
  on the sliders (found by role + name), IG overlay off/on, Crop mode, drags the crop 40 px left.
- **Three renders**, each measured against its own DOM snapshot:
  - A: that state (dragged logo, 9:16 crop dragged left), ⌘↵;
  - B: crop switched off (cover), logo snapped "Top right", Render button;
  - C: crop back on, its left **edge** handle dragged 40 px right ("Region 476x848, upscaled 2.27x"),
    logo snapped "Top left", ⌘↵.
  Waits for all three READY, previews A (label "RENDER 60 · Phase3 Test Co 8590 · 1080x1920").
- **Measure**: extracts the frame at 2 s of each render with `docker compose exec worker ffmpeg ... -f
  rawvideo`, then takes the bounding boxes of the green (logo) and red (marker) pixels.
```
mid-upload:  phase3-src.mp4 Uploading · 5.1 MB of 16.9 MB … Uploading 30% 3.0 MB/s … Cancel
upload response 76 PROBING
opaque logo: logo PNG has no transparent pixels (its alpha is opaque everywhere)
brand 15 logo upload HTTP 200
crop A: before x 0.34 · y 0.00 · w 0.32 · h 1.00   after x 0.27 · y 0.00 · w 0.32 · h 1.00
crop C: x 0.34 · y 0.00 · w 0.25 · h 0.79 | Region 476x848, upscaled 2.27x to 1080x1920. | handles 8
A render 60 sent overlay {x 0.44222, y 0.33778, w 0.27, opacity 0.85}  crop {x 0.27423, y 0, w 0.31641, h 1}
B render 61 sent overlay {x 0.63,    y 0.1625,  w 0.27, opacity 0.85}  crop null
C render 62 sent overlay {x 0.1,     y 0.1625,  w 0.27, opacity 0.85}  crop {x 0.34180, y 0, w 0.24884, h 0.78645}

                              x         y         w         h
A  overlay_config sent        0.44222   0.33778   0.27000   0.07594   (h = w*1080*200/400/1920)
   logo DOM box / stage       0.44218   0.33775   0.26997   0.07593   (stage 324x576 px)
   logo in rendered frame     0.44259   0.33750   0.27037   0.07604   (px 478, 648, 292x146)
   marker, DOM prediction     0.58418   0.46289   0.13201   0.07426
   marker in rendered frame   0.58426   0.46354   0.13241   0.07396   (px 631, 890, 143x142)
B  overlay_config sent        0.63000   0.16250   0.27000   0.07594
   logo DOM box / stage       0.62997   0.16249   0.26997   0.07593
   logo in rendered frame     0.62963   0.16250   0.27037   0.07604   (px 680, 312, 292x146)
   marker, DOM prediction     0.36831   0.46296   0.13169   0.07407
   marker in rendered frame   0.36852   0.46302   0.13333   0.07396   (px 398, 889, 144x142)
C  overlay_config sent        0.10000   0.16250   0.27000   0.07594
   logo DOM box / stage       0.09997   0.16249   0.26997   0.07593
   logo in rendered frame     0.10000   0.16250   0.27037   0.07604   (px 108, 312, 292x146)
   marker, DOM prediction     0.47060   0.58983   0.16807   0.09454
   marker in rendered frame   0.47037   0.58958   0.16852   0.09479   (px 508, 1132, 182x182)

                                  A         B         C
config vs render (logo)       0.00037   0.00037   0.00037
DOM vs render (logo)          0.00041   0.00041   0.00041
DOM vs config (logo)          0.00004   0.00003   0.00003
DOM vs render (marker)        0.00066   0.00165   0.00045   (A, C crop; B cover)
PASS: every check within 1% of the frame (worst 0.16%)
```
Renders 60, 61, 62, as ffprobe sees them: `h264 High 1080x1920 yuv420p 30/1 | aac LC 48000 Hz 2 ch`.

### QA fix round (2026-09-26)
Each item was reproduced or confirmed from the code first, then checked in Google Chrome with a one-off
Playwright script (not in the repo) after the fix:
- **Focus**: tabbing the Library header gives `outline: solid 1px color(srgb .93 .93 .94 / .6)` on links and
  buttons; the search and URL inputs light their wrapper border to #A0A0A8. In the editor: Render, IG
  overlay and a snap cell show the ring; the Brand select, caption and margin wrappers go to #A0A0A8.
- **Remove**: READY rows with renders show a disabled trash titled "Delete its renders first"; READY rows
  with 0 renders a live one. A forced 409 (routed) shows the notice floating over the table: thead top
  stayed at y 105.
- **Row actions** end at one x (1396 px) on every row, READY / FAILED / PROBING / upload rows alike.
- **Failed rows**: `DURATION_OUT_OF_RANGE`, `EXTRACTOR_FAILED`, `REMOVED` fit beside the chip
  (scrollWidth ≤ clientWidth).
- **Platform glyph**: TikTok / Instagram paths on rows whose `platform` was rewritten in a routed list
  response, YouTube on clips 7 and 10, Globe + "URL import" on the failed imports 8 and 9.
- **Non-READY row** (clip 33 routed as PROBING): no `<a>` in the row, "Open editor" is a `<span>`.
- **Row hover**: `rgb(17, 17, 19)` (panel).
- **Queued card** (worker stopped): `PENDING`, "Queued", 0 indeterminate bars, 0 spinners; once RENDERING,
  1 bar and 1 spinner.
- **Rejected files** picked from the Published tab's Upload button (the tab switches back to Clips):
  "empty.mp4 0 KB · — · Failed Empty file · Dismiss", "notes.txt 1 KB · — · Failed Not mp4, mov or webm ·
  Dismiss". Neither was sent.
- **Upload lifecycle** (1 MB/s): `beforeunload` is cancelled while uploading (and not once failed); the row
  kept its progress and Cancel through Published and back ("Uploading 5%") and through /brands and back
  ("Uploading 10%"). A second dev server on :5174 killed mid-upload gave "Stopped at 2.5 MB of 16.9 MB ·
  Failed · Upload interrupted — network · Retry"; the api dropped its UPLOADING row
  (`docs/phase-3-library-upload-failed.png`).
- **Drop guard**: a synthetic dragover and drop on the table are both `defaultPrevented`.
- **Published tab**: the search is hidden.
- **Brands**: the last column's header text ends at 1423 px (16 px gutter, column widened to 116 px);
  archived rows read "Archived 26 Sep". Tab from "New brand" lands on the "Acme" name button, Enter
  opens its drawer. A checked switch is `rgb(237, 237, 239)` without shadcn's CSS.
- **Opaque RGBA logo** (300x100, alpha 255 everywhere): "logo PNG has no transparent pixels", no
  `/logo` request sent.
- **Caption limits**: 2300 characters → Render disabled, ⌘↵ sent 0 POSTs; 31 hashtags ("31 / 30
  hashtags") → disabled, 0 POSTs. `POST /api/renders` with a 2201-character caption now returns 422
  `string_too_long`.
- **Double ⌘↵**: the first POST settles in 13 ms, before the second keypress (measured), so an in-flight
  flag alone let 2 through; with the 1 s cool-down two quick presses send 1 POST.
- **Accessible names**: `getByRole("slider", { name })` finds Logo scale, Logo opacity and Seek; the margin
  input is labelled; snap cells read "Top left" … "Bottom right" with `aria-pressed` on the active one;
  Output/Crop expose `aria-pressed`.
- **Lost pointer capture**: the logo moved 10 px while captured and 0 px after `releasePointerCapture`.
- **Crop stage** at 1440x900: source box 592 px wide from x 224 (the mockup's 224–816).
- **9:16 source** (clip 24, 1080x1920): only "9:16 region" pressed; "Region 1080x1920, no scaling."
- **Tall logo** (60x400 transparent): End on Logo scale stops at 26.5% (slider max 26.67 = 1/aspect), box
  y 0.0062, h 0.9937; "Bottom left" gives y 0; ⌘↵ → 201 (was 422).
- **Editor page states**: a freshly uploaded PROBING clip opened by URL showed the waiting page, then the
  stage by itself once READY; the FAILED clip 28 reads "This clip failed (DURATION_OUT_OF_RANGE); there is
  nothing to edit. Retry or remove it in the Library."
- **Window sizes**: 1024x768 → stage 176x312 at x 224, inside its column (controls from x 424), the
  Output/Crop + IG overlay row wraps, Render visible; 1280x720 → stage 324x576, Render visible.
- **Error states** (routed 500 `{"detail": "boom"}`): Library "Couldn't load clips: boom"; Brands
  "Couldn't load brands: boom"; Editor shows "boom" instead of "Loading…"; the log dialog shows "boom";
  render Retry alerts "Retry failed: boom"; the auto-approve switch alerts "Couldn't change auto-approve:
  boom".
- **Also fixed while comparing screenshots**: a tall logo overflowed its 48 px tile in the Brands table
  (a grid track grew to the image); the tile is a flex box now (`docs/phase-3-brands-archived.png`).
- The e2e's Published screenshot waits for the empty state: react-router commits the tab change in a
  transition (7–42 ms), which the old screenshot could race.

### Earlier one-off checks (first round, unchanged code paths)
- **Three files in one drop** (`multi-a.mp4`, `multi-b.mov`, `notes.txt`): three rows at once; both videos
  went READY; `notes.txt` was never sent.
- **95 s clip**: "Clip is 01:35 long: Zernio can't post Reels over 90 s. It still renders."
  (`docs/phase-3-editor-long.png`).
- **Snap grid** (Acme 400x160 logo, 4% margin): the DOM box was x 0.6800, y 0.1625, w 0.22, as in the unit
  test; "Save as brand default" PATCHed it (restored afterwards).
- **Corner handles**: logo `se` stopped at the right edge, `nw` at the top edge, h/w stayed 0.2250; crop
  `se` gave "Region 384x684, upscaled 2.81x".
- **Failed render** (retaken): with clip 1's raw file hidden for a moment, render 56 went FAILED
  `FFMPEG_FAILED`; View log showed "Error opening input file /data/raw/1.mp4."
  (`docs/phase-3-editor-failed-log.png`). The file was put back and render 56 deleted.
- **Decode fallback** (retaken): Playwright's own Chromium (Chrome for Testing 149) can't decode HEVC; clip
  22 shows the thumbnail in the output geometry and "Preview unavailable in this browser; render still
  works." (`docs/phase-3-editor-fallback.png`). Google Chrome decodes HEVC.
- **No browser storage**:
  ```
  $ grep -rnE 'localStorage|sessionStorage' src e2e index.html   → no match (exit 1)
  $ grep -c 'localStorage\|sessionStorage' dist/assets/*.js       → 0
  ```

### Screenshots (1440x900, all retaken in this round)
Compared by eye with `docs/design/*.png`: same shell, header, column and panel geometry (editor stage
358–683 px, controls 840–1160, queue 1160–1440; crop box 224–816), with the deviations listed above.
- Library: `docs/phase-3-library-uploading.png` (30%, 3.0 MB/s), `phase-3-library.png`,
  `phase-3-library-upload-failed.png`, `phase-3-library-published.png`.
- Editor: `phase-3-editor.png` (Output, logo selected, IG overlay on), `phase-3-editor-crop.png` (8
  handles), `phase-3-editor-rendering.png`, `phase-3-editor-preview.png` (render 60 at 00:02, labelled; it
  matches `phase-3-editor.png`), `phase-3-editor-long.png`, `phase-3-editor-failed-log.png`,
  `phase-3-editor-fallback.png`.
- Brands: `phase-3-brands.png`, `phase-3-brands-alpha-error.png`, `phase-3-brands-archived.png`.
- Other: `phase-3-calendar.png`.

Test data left in the dev database: clips 31, 36, 70, 75, 76 (`phase3-src.mp4`), 32 (`long95.mp4`) and
33 (`net-cut.mp4`); brands 3, 10, 14, 15 ("Phase3 Test Co …") and archived 2, 11 ("QA fix tall"), 12, 13
("QA fix rgba"); renders 25, 27, 46-48, 57-62. The other QA renders and clips were deleted.

## Could not verify
- **Other browsers**: Safari and Firefox were not run, only Google Chrome 149 (and Chrome for Testing 149
  for the fallback). The drag maths only uses pointer events, but touch/pen input was not tried.
- **A real OS file drag** onto the table (headless Playwright can't do one): the window guard was checked
  with synthetic events only.
- **Real phone sources**: no original iPhone HEVC / Dolby Vision .mov went through the editor. HEVC
  playback depends on the browser and OS; the fallback is shown when decoding fails or yields no frames.
- **The IG chrome proportions** are approximations (Meta's safe-zone guide + the mockup), not measured
  against a real Reel on a phone. The 14/35/6 % safe zone is used for snapping and drawn dashed.
- **Big uploads**: nothing near 2 GB was tried. The > 2 GB client check is code-read only, and the upload
  rate at real Wi-Fi speeds was not measured (CDP throttling only).
- **Upload hidden-row matching**: the server's UPLOADING row is hidden by count, not by id. With uploads
  from two browser tabs at once, one tab can hide the other's in-flight row until it finishes.
- **Logo transparency through the API**: `POST /brands/{id}/logo` still only checks the PNG's structure
  (alpha channel or tRNS), so an opaque RGBA PNG sent with curl is accepted; only the drawer checks the
  pixels (a known ceiling; decoding pixels in the api would need Pillow).
- **Window sizes**: 1440x900, 1280x720 and 1024x768 were checked; below ~1000 px wide the three columns
  no longer fit (the controls and queue keep their 320 + 280 px).
- **URL import through the UI**: the form posts `POST /clips/from-url` like the curl runs in Phase 2, but
  no live URL was imported from the browser in this phase (yt-dlp calls to real sites were avoided).
- **⌘S** in the brand drawer was not pressed by a script (Save was clicked). ⌘↵ was used for renders.
- **Dev-only audit findings**: `npm audit` reports 4 high findings in js-yaml, pulled in by
  `@hey-api/openapi-ts` 0.99.0 (pinned on purpose). It only parses our own committed openapi.json at
  codegen time and is not in the bundle.
