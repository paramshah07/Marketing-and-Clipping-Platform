# Clipper UI mockup brief

Static, high-fidelity HTML mockups for operator approval. They are a design contract for the real
React + Tailwind + shadcn/ui build, so use only things that build can reproduce.

## Hard rules (from the product spec)
- Dark-first. Dense working surface, not a marketing page. Resist airy shadcn defaults.
- ONE accent colour (`accent` #4F8CFF), used ONLY for primary actions and in-progress/scheduled state.
  Status colours `ok` / `warn` / `bad` only where state demands it (ready/published, warnings, failures,
  token-expiry chip). Everything else neutral greys.
- No gradients (except the `.checker` transparency pattern behind logos), no glassmorphism, no big
  radii (max `rounded-md` = 6px), no shadows except on popovers/drawers, no hero sections, no
  illustrations, no emoji.
- Type: Geist (UI), `tabular-nums` on every time/count/duration/percentage, Geist Mono (`font-mono`)
  ONLY for IDs and error codes.
- Base size 13px (`text-base`), meta 12px (`text-sm`), labels 11px (`text-xs`, uppercase, tracking-wider,
  `text-subtle`). Page title `text-lg font-semibold`.
- Video is the content: thumbnails get real size; chrome around them stays minimal.
  - Table rows: 56px tall, 9:16 thumb 28x50 (`w-7 h-[50px] rounded-sm object-cover`).
  - Cards (upload rows, render queue, calendar): 9:16 thumb 36x64.
  - Exception, the calendar slot board (2026-09 redesign, `.context/calendar-redesign/slotboard.html`): with up
    to 4 posting slots a tile's 9:16 thumb grows with the row, capped at 128 px tall (72x128), so the advertiser
    logo on the render can be checked on the board. This is intentional. With 5+ slots, tiles fall back to 36x64
    beside the text; off-slot posts are 36 px lines with an 18x32 thumb.
  - The calendar's render queue is a list, not cards: 56 px rows with the table's 28x50 thumb (grouped clips stack
    two 28x50 thumbs in the same 28 px column).
  - Customizations > Covers: saved covers are 72x128 tiles (they are the image that gets published).
- Borders (`border-line`) separate things, not shadows. Controls 28px tall (`h-7`), radius 4px.
- Motion: none in a static mock; you may indicate "animates" in an HTML comment.
- Icons: lucide via `<i data-lucide="name"></i>` (already loaded in the base).

## Base template
Copy `_base.html` (same folder) verbatim as your starting point: it has the Tailwind config with all
colour tokens, fonts, lucide, and the sidebar. Set the `<title>`, the active nav item, the header
title/actions, and fill `<section>`. Do not change the tokens or the sidebar markup (except which item
is active).

## Placeholder media
Use `https://picsum.photos/seed/<word>/360/640` for 9:16 video frames/thumbnails (vary the seed),
`https://picsum.photos/seed/<word>/640/360` for a 16:9 source. Avatars: `https://i.pravatar.cc/64?img=<n>`.
Brand logos: render a simple text wordmark in a white/black box, or an inline SVG — keep it plausible.

## Sample data (use consistently)
- Accounts (all Europe/London unless noted): `@bajjo.clips` (token 43 days, green), `@bajjo.daily`
  (token 11 days, amber), `@late.night.cuts` (token 5 days, red, timezone America/New_York).
- Brands (advertisers): `Northwind Coffee`, `Flux Energy`, `Kite VPN` (archived: `Old Brand Co`).
- Clips: mix of uploads (`IMG_4821.MOV`, `podcast_ep42_cut3.mp4`, `street_interview.webm`) and URL
  imports (`tiktok.com/@creator/video/...`, `youtube.com/shorts/...`, `instagram.com/reel/...`) with
  creator handles.
- IDs look like `clp_7f3a9c`, `rnd_19b2e0`, `pst_a41c07`, container `17912345678901234`.
- Today is Sat 26 Sep 2026, it is 23:40. The calendar week is Mon 28 Sep – Sun 4 Oct.

## Screens (one agent per screen; each writes its HTML + PNG into this folder)

### library-clips (1440x900) — `/library`, tab "Clips" (default route)
Header: title "Library", segmented tabs `Clips | Published` right of title; right side: search input
(`/` hint), primary button "Upload". Under header: a slim dashed drop strip (40px tall) "Drop videos here
(mp4, mov, webm) — or paste a URL" with an inline URL input + "Import" button. Then a dense table (TanStack
style) with columns: [checkbox] thumb | Name (filename or URL host + path, second line: platform icon +
@creator or "Uploaded") | Duration | Size (1080x1920 · 30fps) | Status | Renders (count) |
Added (relative, tabular) | row actions (`Open editor` ghost button + kebab).
Show states: 2 rows uploading at the top (inline progress bar in Status column with %, MB/s), 1 row
PROBING (spinner), 1 FAILED upload row with distinct cause "Upload interrupted — network" + Retry
button, 1 FAILED URL import "Private video (PRIVATE)" with the code in mono, then ~10 READY rows.
Selection bar hint not needed. Fits ~13 rows.

### library-published (1440x900) — `/library`, tab "Published"
Filter bar: Account multi-select, Brand multi-select, date range ("Last 30 days"), result count.
Table: thumb | Published at (date + time, tabular) | Account (avatar 16px + @handle) | Brand | Caption
(1 line, truncated) | Instagram (external-link "View on Instagram") | Actions: "Re-render for…" button
opening a small brand dropdown (show it open on one row with the 3 brands). ~14 rows.

### editor (1440x900) — `/editor/clp_7f3a9c`
NOT a video editor. Three columns inside main: (1) Stage, flexible, centred: a 9:16 frame ~560px tall
(min 420) showing the video still, with: a logo box (brand logo) with 4 corner resize handles and a 1px
accent outline because it is selected; a non-interactive Instagram Reels chrome overlay at 40% opacity
(top bar "Reels" + camera icon, right action rail: heart/comment/send/more icons with counts + tiny
audio disc at bottom right, bottom-left caption block: avatar + @handle + Follow pill + 2 caption
lines, audio ticker line "♫ Original audio" along the bottom). Faint dashed safe-zone guides are
optional. Below the frame: minimal transport (play, scrubber, 00:07 / 00:31 tabular, mute) and a
segmented toggle `Output | Crop` plus an eye toggle "IG overlay" (on). (2) Controls panel 320px,
border-left, sections with 11px uppercase labels: BRAND (select showing "Northwind Coffee" + "Save as
brand default" text link); LOGO (Scale slider 18% of width, Opacity slider 85%, 3x3 position snap grid
with top-right active, margin 4%); CROP (switch off, presets 9:16 region / Full frame disabled when
off); CAPTION (textarea prefilled from template with a link + hashtags, counter "142 / 2200", "3 / 30
hashtags"); sticky bottom: primary full-width "Render" button (shortcut hint ⌘↵). (3) Render queue
280px, border-left, title "Renders for this clip": cards with 36x64 thumb, brand name, status chip
(RENDERING with indeterminate bar in accent, READY in ok with duration 00:31 · 12.4 MB, FAILED in bad
with "ffmpeg exited 1" + "View log"), relative time, and for READY: "Preview" (swaps stage to output),
"Schedule…", download icon. Breadcrumb in header: "Library / IMG_4821.MOV" with clip meta
"00:31 · 1920x1080 · 29.97fps · has audio" in muted tabular.

### editor-crop (1440x900) — same page, Crop mode
Same layout, `Crop` segment active, crop switch ON. The stage now shows the FULL 16:9 source frame
letterboxed inside the stage area (contain), with a 9:16 crop rectangle (accent 1px outline, 8
handles, rule-of-thirds lines inside) and everything outside the rect dimmed 60%. Logo and IG overlay
hidden in this mode. Controls: crop presets `9:16 region` (active) / `Full frame`, readout
"x 0.34 · y 0.00 · w 0.32 · h 1.00" in tabular.

### calendar (1440x900) — `/calendar`
Header: "Calendar", week nav (‹ Today ›) "28 Sep – 4 Oct 2026", timezone note "Times in each account's
zone", primary "Auto-schedule…". Left tray 240px "Ready to schedule (6)": READY render cards (36x64
thumb, clip name, brand, duration) with checkboxes; 2 checked; footer "Auto-schedule 2 → [@bajjo.clips
▾]" button. Main: grid with a sticky left lane-header column (160px: avatar, @handle, "3 slots · cap
10") and 7 day columns (Mon 28 … Sun 4, today-marker not needed since today is Sat 26). One swimlane
per account (3 lanes). Each lane-day cell: the account's slots as small dashed placeholders labelled
with the time (09:00, 13:00, 19:00) and post cards filling them: 36x64 thumb, time (tabular), brand,
status dot/label (Scheduled = accent, Draft = dashed outline "Draft", Published = ok, Failed = bad).
Bottom of each cell: a 3px quota bar with "3/10" tabular. Show one conflict: two posts 18 min apart
on @bajjo.daily Wed — both cards with warn outline and a tiny "18 min apart" warning. Show one card
mid-drag (slightly lifted, 2px accent outline, ghost placeholder in origin slot) — note in a comment.

### accounts (1440x900) — `/accounts`
Header: "Accounts", primary "Connect account". Grid of 3 account cards (dense, ~420px wide each, 2 or 3
per row). Card: avatar 40px, @handle, ig_user_id in mono subtle, token chip (green "Token 43d", amber
"11d", red "5d" with "Refresh now" link), quota line "Meta: 97/100 left (24h) · Today: 3/10 cap",
"Last published 2h ago", then the SLOTS editor inline: timezone select, chips of times "09:00 ×"
"13:00 ×" "19:00 ×" "+ Add", daily cap number input, min gap "45 min"; card footer actions: Reconnect,
Disable. On the right, show the "Connect account" drawer OPEN (400px, border-left, subtle shadow) with a
3-step vertical stepper: 1 "Add as Instagram Tester" (done, green check, external link "Open Meta app
dashboard ↗"), 2 "Accept the invite in Instagram" (done; instructions: instagram.com → Settings →
Website permissions → Apps and websites → Tester invites → Accept), 3 "Log in with Instagram" (current,
accent primary button "Continue with Instagram"; below: "We'll verify with /me and your publishing
quota before saving." ). Dim the page behind the drawer slightly.

### customizations (1440x900) — `/customizations/:tab` (`brands` | `captions` | `covers`; `/brands` redirects)
Header: "Customizations" with segmented tabs `Brands n | Captions n | Covers n` (as Library's), then the tab's
actions on the right. Each tab keeps at most one default, which the Editor preselects; it shows as a neutral
bordered `Default` chip (not the accent). Captions: table Name | caption (2 lines, tokens as mono chips) |
`n / 2200 · n tags` | Default | hover actions (Make default, Edit, delete), New/Edit in a 440px drawer like
the brand's with a "Default caption" switch. Covers: 72x128 tiles with name, added day, Default chip, Make
default / rename / delete; "Upload cover" makes the 1080x1920 JPEG in the browser.
Brands tab (was `/brands`): "Show archived" toggle, primary "New brand". Table: logo (on `.checker`, 48x48) |
Name | Caption template (1 line, truncated, with `{link}` token highlighted as a mono chip) | Link |
Default placement (tiny 9:16 frame 27x48 with the logo position drawn) | Auto-approve (switch) |
Renders / Posts counts | kebab. Right drawer open (440px) editing "Flux Energy": logo dropzone showing
the PNG on checker with "Replace", name, link, caption template textarea with token help ("{link}",
"{creator}"), auto-approve switch with helper text "Posts for this brand skip Draft and go straight to
Scheduled", default placement preview (9:16 frame 108x192 with logo box drawn) + "Edit in editor"
link, a "Default brand" switch, footer: Archive (ghost, bad text), Save (primary).

### recover (390x844, MOBILE) — `/recover/pst_a41c07`
No sidebar (use a minimal top bar: "Clipper" + back chevron). Single column, 16px padding.
Top: status chip bad "Failed" + "Container expired". Large 9:16 thumbnail (~180px wide) centred with the
account avatar + @late.night.cuts and "Scheduled Thu 1 Oct, 19:00 (America/New_York)" tabular.
Cause paragraph in plain language (md size): "Instagram took too long to process this video and the
upload slot expired after 24 hours. Nothing was posted." Then ONE full-width primary button (48px tall,
accent): "Re-render and retry". Under it a small muted line "Creates a fresh render and publishes in
the next free slot (Fri 2 Oct, 09:00)". Then a collapsed disclosure "Technical details ▸" and, to show
it, a SECOND screenshot `recover-expanded.png` with it open: error_code `CONTAINER_EXPIRED` mono chip,
post id, container id, attempt 2/3, and the raw JSON payload in a mono code block (scrollable).
