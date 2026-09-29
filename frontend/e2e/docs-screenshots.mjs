// Documentation screenshots: drives the real UI in Google Chrome and writes docs/images/*.png with numbered
// callouts, plus docs/images/manifest.json (file, page, title, alt, callouts [{n, selector, label}]) that the
// legend tables in docs/guide/* follow.
//   ./review.sh && (cd frontend && npm run dev)    then, from frontend/:
//   CLIPPER_E2E_USER=clipper CLIPPER_E2E_PASSWORD=… node e2e/docs-screenshots.mjs    (the operator's account on the copy;
//   the password comes from the environment, never from this file)
//   ONLY=calendar,accounts node e2e/docs-screenshots.mjs    retake some;  PREP=0 reuses the last run's data step
// Review stack only (a copy of production, publishing off). Before shooting, prep() shapes that copy so every screen
// has something to show: test brands archived; "Late-night pick" and "Deep dive" made the default caption and cover;
// the account on 3 slots a day (cap 3, 60 min gap); a dozen branded renders; a week of posts from tomorrow (the
// account's other drafts and scheduled posts cancelled); one failed post and one failed render; brand captions on the
// kept renders; every other render hidden from the Ready tray. Writes go through the api on :8000 and, where the api
// has no way (the failures, captions, the tray), psql in the clipper-review project. It refuses to run when the api
// says publishing is on, and never calls Zernio or Telegram. ./review.sh gets a fresh copy back.
import { execFileSync } from "node:child_process"
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { chromium } from "playwright-core"

const ROOT = new URL("../..", import.meta.url).pathname
const BASE = process.env.BASE_URL ?? "http://localhost:5173"
const API = process.env.API_URL ?? "http://127.0.0.1:8000"
const OUT = `${ROOT}docs/images/`
const ONLY = process.env.ONLY?.split(",")
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)

const USER = process.env.CLIPPER_E2E_USER
const PASSWORD = process.env.CLIPPER_E2E_PASSWORD
if (!USER || !PASSWORD) {
  console.error("Set CLIPPER_E2E_USER and CLIPPER_E2E_PASSWORD: a Clipper user of the review stack")
  process.exit(2)
}
// what the browser sends through the dev server: its origin, same-origin (the api's CSRF check)
const SITE = { origin: new URL(BASE).origin, "sec-fetch-site": "same-origin" }
async function signIn() {
  const r = await fetch(`${API}/api/auth/login`, { method: "POST", headers: { ...SITE, "content-type": "application/json" }, body: JSON.stringify({ username: USER, password: PASSWORD }) })
  if (!r.ok) throw new Error(`sign-in as ${USER}: HTTP ${r.status} ${await r.text()}`)
  return r.headers.getSetCookie().find((c) => c.startsWith("clipper_session=")).split(";")[0].slice("clipper_session=".length)
}
const SESSION = await signIn() // the api calls below and every browser context use this session

async function api(method, path, body) {
  const headers = { ...SITE, cookie: `clipper_session=${SESSION}`, ...(body ? { "content-type": "application/json" } : {}) }
  const r = await fetch(`${API}/api${path}`, { method, headers, body: body && JSON.stringify(body) })
  const data = r.status === 204 ? null : await r.json().catch(() => null)
  if (!r.ok) throw new Error(`${method} ${path}: HTTP ${r.status} ${JSON.stringify(data)}`)
  return data
}
// the review project by name: this can never reach the Mac's own stack or the VM
const psql = (q) =>
  execFileSync("docker", ["compose", "-p", "clipper-review", "-f", "compose.yml", "-f", "compose.review.yml", "exec", "-T", "postgres", "psql", "-U", "clipper", "-v", "ON_ERROR_STOP=1", "-qAtc", q], { cwd: ROOT })
    .toString()
    .trim()
const lit = (s) => (s == null ? "NULL" : `'${String(s).replaceAll("'", "''")}'`)
// a brand's caption template for a clip (the Editor's fillCaption, minus its trimming of a missing {creator})
const fill = (b, c) => (b.caption_template ? b.caption_template.replaceAll("{link}", b.link ?? "").replaceAll("{creator}", c?.source_creator_handle ?? "") : null)

// ---- dates in the account's zone
const ymd = (d, tz) => new Intl.DateTimeFormat("en-CA", { timeZone: tz }).format(d) // 2026-09-30
const addDays = (date, n) => new Date(Date.parse(`${date}T12:00:00Z`) + n * 864e5).toISOString().slice(0, 10)
function zoned(date, time, tz) {
  // ponytail: one offset correction, wrong only for a wall time inside a DST gap (none of ours)
  const guess = new Date(`${date}T${time}:00Z`)
  const p = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
      .formatToParts(guess)
      .map((x) => [x.type, x.value])
  )
  return new Date(2 * guess.getTime() - Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute)).toISOString()
}

// ---- 1. the data every screen needs (idempotent: a re-run finds what the last one made)
const SLOTS = ["09:00", "13:00", "19:00"] // the "3 a day" preset
// [key, clip id, brand]: clip ids are production's, a copy without one skips it
const RENDERS = [
  ["A", 119, "Northwind Coffee"],
  ["B", 81, "Flux Energy"],
  ["C", 120, "Kite VPN"],
  ["D", 30, "Northwind Coffee"],
  ["E", 136, "Flux Energy"],
  ["F", 174, "Kite VPN"],
  ["G", 146, "Acme"],
  ["H", 172, "Flux Energy"],
  ["I", 157, "Northwind Coffee"],
  ["J", 142, "Kite VPN"],
  ["K", 149, "Northwind Coffee"], // the failed post
  ["L", 148, "Flux Energy"], // stays in the Ready tray
]
const KEEP = [28, 29, 30, 23, 5] // existing READY renders left in the tray (clip 10 in three brands, two Acme)
// [render key, day from the first board day, slot, state]
const PLAN = [
  ["A", 0, "09:00", "scheduled"],
  ["C", 0, "13:00", "scheduled"],
  ["B", 0, "19:00", "scheduled"],
  ["D", 1, "09:00", "scheduled"],
  ["F", 1, "13:00", "draft"],
  ["E", 1, "19:00", "scheduled"],
  ["J", 2, "09:00", "scheduled"],
  ["H", 2, "19:00", "scheduled"],
  ["G", 3, "13:00", "draft"],
  ["I", 4, "09:00", "scheduled"],
]
const FAILED_KEY = "docs-screenshots-failed"
const EDITOR_CLIP = 10 // the editor shots: a 1080x1920 clip with three branded renders
const CROP_CLIP = 29 // a landscape clip, for the crop box

async function prep() {
  const st = await api("GET", "/status")
  if (st.publishing_enabled) throw new Error("publishing is on: run this against ./review.sh's stack only")
  psql("select 1") // the clipper-review project is up

  const brands = await api("GET", "/brands")
  for (const b of brands) if (/^Phase3 Test Co/.test(b.name)) await api("PATCH", `/brands/${b.id}`, { archived: true })
  const brand = Object.fromEntries(brands.map((b) => [b.name, b]))
  const late = (await api("GET", "/captions")).find((c) => c.name === "Late-night pick")
  if (late && !late.is_default) await api("PATCH", `/captions/${late.id}`, { is_default: true })
  const cover = (await api("GET", "/covers")).find((c) => c.name === "Deep dive")
  if (cover && !cover.is_default) await api("PATCH", `/covers/${cover.id}`, { is_default: true })

  const acc = (await api("GET", "/accounts")).find((a) => a.connection_status === "connected" && !a.disabled_at)
  if (!acc) throw new Error("no connected account in the review copy")
  await api("PATCH", `/accounts/${acc.id}`, { posting_slots: { times: SLOTS }, daily_cap: 3, min_gap_minutes: 60 })

  // renders: reuse a READY one of the same clip and brand, else render it (the review worker runs ffmpeg)
  const clips = Object.fromEntries((await api("GET", "/clips")).map((c) => [c.id, c]))
  const rid = {}
  for (const [key, clipId, name] of RENDERS) {
    const c = clips[clipId]
    const b = brand[name]
    if (c?.status !== "READY" || !b) continue
    const have = (await api("GET", `/renders?clip_id=${clipId}`)).find((r) => r.brand_id === b.id && r.status !== "FAILED")
    rid[key] = (have ?? (await api("POST", "/renders", { clip_id: clipId, brand_id: b.id, caption: fill(b, c) }))).id
  }
  // the kept renders carry test captions ("test caption #ad"): give them their brand's
  for (const id of KEEP) {
    const r = await api("GET", `/renders/${id}`).catch(() => null)
    const b = r && brands.find((x) => x.id === r.brand_id)
    const caption = b && fill(b, clips[r.source_clip_id])
    if (caption && r.caption !== caption) psql(`UPDATE renders SET caption = ${lit(caption)} WHERE id = ${id}`)
  }
  for (let t = 0; ; t++) {
    const rs = await api("GET", "/renders")
    const waiting = rs.filter((r) => Object.values(rid).includes(r.id) && r.status !== "READY")
    if (!waiting.length) break
    if (waiting.some((r) => r.status === "FAILED") || t > 300) throw new Error(`renders not ready: ${waiting.map((r) => `${r.id} ${r.status}`)}`)
    if (t % 10 === 0) log(`waiting for ${waiting.length} renders`)
    await new Promise((f) => setTimeout(f, 2000))
  }

  // the board: 7 days from tomorrow; the account's other drafts and scheduled posts are cancelled (in this copy)
  const tz = acc.timezone
  const day0 = addDays(ymd(new Date(), tz), 1)
  const plan = PLAN.filter(([k]) => rid[k]).map(([k, d, slot, state]) => ({ render_id: rid[k], at: zoned(addDays(day0, d), slot, tz), state }))
  for (const p of await api("GET", `/posts?account_id=${acc.id}&status=DRAFT&status=SCHEDULED`))
    if (!plan.some((x) => x.render_id === p.render_id && Date.parse(x.at) === Date.parse(p.scheduled_for)))
      await api("POST", `/posts/${p.id}/cancel`)
  for (const x of plan) {
    const p = await api("POST", "/posts", { render_id: x.render_id, account_id: acc.id, scheduled_for: x.at })
    if (x.state === "scheduled" && p.status === "DRAFT") await api("POST", `/posts/${p.id}/approve`)
  }

  // a post that failed yesterday at 19:00 (Instagram refused the video), for the Recover screen and the badges
  if (rid.K)
    psql(`INSERT INTO posts (render_id, account_id, caption, scheduled_for, status, idempotency_key, error_code, error_detail, first_post_at)
      SELECT id, ${acc.id}, coalesce(caption, ''), ${lit(zoned(addDays(day0, -2), "19:00", tz))}, 'FAILED', ${lit(FAILED_KEY)}, 'CONTENT_REJECTED',
        '{"platform": "instagram", "status": "failed", "errorCategory": "platform_rejected", "errorSource": "platform", "errorMessage": "Media processing failed: Instagram could not process the video"}',
        ${lit(zoned(addDays(day0, -2), "19:00", tz))}
      FROM renders WHERE id = ${rid.K} AND NOT EXISTS (SELECT 1 FROM posts WHERE idempotency_key = ${lit(FAILED_KEY)})`)
  // a render that failed, for the editor's render cards
  psql(`INSERT INTO renders (source_clip_id, brand_id, overlay_config, status, error_code, ffmpeg_log, completed_at)
    SELECT ${EDITOR_CLIP}, id, default_overlay_config, 'FAILED', 'FFMPEG_FAILED', ${lit("[in#0 @ 0x7f3c] Error opening input: Invalid data found when processing input\nError opening input file /data/raw/clip.mp4.\nError opening input files: Invalid data found when processing input")}, now()
    FROM brands WHERE name = 'Acme' AND NOT EXISTS (SELECT 1 FROM renders WHERE source_clip_id = ${EDITOR_CLIP} AND status = 'FAILED')`)
  // the Ready tray shows only the docs renders: the rest (test renders, mostly) are marked superseded in this copy
  const tray = [...KEEP, rid.L].filter(Boolean)
  psql(`UPDATE renders SET superseded_at = now() WHERE superseded_at IS NULL AND status = 'READY' AND id <> ALL(ARRAY[${tray}])
    AND NOT EXISTS (SELECT 1 FROM posts WHERE posts.render_id = renders.id AND posts.status <> 'CANCELLED')`)

  const failed = psql(`SELECT id FROM posts WHERE idempotency_key = ${lit(FAILED_KEY)}`)
  const draft = (await api("GET", `/posts?status=DRAFT&account_id=${acc.id}&from=${zoned(day0, "00:00", tz)}`)).find((p) => p.render_id === rid.F)
  return { account: acc.id, day0, failed: Number(failed), draft: draft?.id, rid }
}

// ---- 2. callouts: accent circles with a white number at a corner or side of each element (sel: one selector, or
// several whose union is the element), optional outline. at: tl tr bl br (corners), l r t b (outside a side), c (centre)
const S = 22 // badge size
async function annotate(page, callouts) {
  const marks = []
  for (const c of callouts) {
    const boxes = []
    for (const sel of [c.sel].flat()) {
      const b = await page.locator(sel).first().boundingBox()
      if (!b) throw new Error(`callout "${c.label}": nothing visible at ${sel}`)
      boxes.push(b)
    }
    const x = Math.min(...boxes.map((b) => b.x))
    const y = Math.min(...boxes.map((b) => b.y))
    const box = { x, y, width: Math.max(...boxes.map((b) => b.x + b.width)) - x, height: Math.max(...boxes.map((b) => b.y + b.height)) - y }
    marks.push({ box, at: c.at ?? "tl", outline: !!c.outline, dx: c.dx ?? 0, dy: c.dy ?? 0 })
  }
  await page.evaluate(
    ({ marks, S }) => {
      // a popover, so it sits in the top layer above an open modal <dialog> and its backdrop
      const root = document.createElement("div")
      root.popover = "manual"
      root.style.cssText = "position:fixed;inset:0;width:auto;height:auto;margin:0;padding:0;border:0;background:transparent;overflow:visible;pointer-events:none"
      marks.forEach(({ box: b, at, outline, dx, dy }, i) => {
        if (outline) {
          const o = document.createElement("div")
          o.style.cssText = `position:absolute;left:${b.x - 3}px;top:${b.y - 3}px;width:${b.width + 6}px;height:${b.height + 6}px;border:1.5px solid #4F8CFF;border-radius:6px`
          root.append(o)
        }
        const g = S / 2 + 4 // a side badge's centre sits this far out
        const [cx, cy] = {
          tl: [b.x - 4, b.y - 4],
          tr: [b.x + b.width + 4, b.y - 4],
          bl: [b.x - 4, b.y + b.height + 4],
          br: [b.x + b.width + 4, b.y + b.height + 4],
          l: [b.x - g, b.y + b.height / 2],
          r: [b.x + b.width + g, b.y + b.height / 2],
          t: [b.x + S / 2, b.y - g],
          b: [b.x + S / 2, b.y + b.height + g],
          c: [b.x + b.width / 2, b.y + b.height / 2],
        }[at]
        const x = Math.min(Math.max(cx + dx, S / 2 + 2), innerWidth - S / 2 - 2)
        const y = Math.min(Math.max(cy + dy, S / 2 + 2), innerHeight - S / 2 - 2)
        const n = document.createElement("div")
        n.textContent = String(i + 1)
        n.style.cssText = `position:absolute;left:${x - S / 2}px;top:${y - S / 2}px;width:${S}px;height:${S}px;border-radius:50%;background:#4F8CFF;color:#fff;display:grid;place-items:center;font:600 12px/1 "Geist Variable",system-ui,sans-serif;font-variant-numeric:tabular-nums;box-shadow:0 0 0 2px rgba(10,10,10,.9),0 2px 8px rgba(0,0,0,.5)`
        root.append(n)
      })
      document.body.append(root)
      root.showPopover()
    },
    { marks, S }
  )
}

async function settle(page) {
  await page.waitForLoadState("networkidle")
  await page.evaluate(async () => {
    await document.fonts.ready
    await Promise.all([...document.images].filter((i) => !i.complete).map((i) => new Promise((f) => (i.onload = i.onerror = f))))
  })
  await page.waitForTimeout(400)
}

// ---- 3. the shots, in guide order; callouts are numbered in reading order (columns left to right)
const shots = (d) => [
  {
    file: "overview.png",
    page: "docs/guide/01-getting-started.md",
    title: "The Clipper layout",
    alt: "Clipper's Library page with the sidebar on the left: page links, a red failed-post badge on Calendar, and the status footer",
    go: async (p) => p.goto(`${BASE}/library`),
    callouts: [
      { sel: "aside nav", at: "tl", outline: true, label: "Pages: Library, Calendar, Accounts, Customizations" },
      { sel: 'aside a[title*="failed post"]', at: "r", label: "Failed posts badge: opens the oldest failure's Recover page" },
      { sel: "aside div[title]:has-text('Publishing')", at: "r", dx: -28, label: "Status: API, database, worker and the publishing switch, worst first" },
      { sel: "aside div.tabular-nums", at: "r", dx: -28, label: "Renders running now and posts waiting to go out" },
      { sel: "main header", at: "c", outline: true, label: "Page header: title, tabs and the page's main actions" },
    ],
  },
  {
    file: "library.png",
    page: "docs/guide/02-library.md",
    title: "Library",
    alt: "The Library's Clips tab: search, Import links and Upload in the header, a drop zone with a URL field, and a table of clips with their status and render counts",
    go: async (p) => {
      await p.goto(`${BASE}/library`)
      await settle(p)
      await p.locator("tr[data-clip]").first().hover()
    },
    callouts: [
      { sel: "header div:has(> button:has-text('Published'))", at: "r", label: "Clips and Published tabs" },
      { sel: "header label:has(input[placeholder^='Search'])", at: "l", label: "Search clips, creators and URLs (press /)" },
      { sel: "header button:has-text('Import links')", at: "b", dy: -4, label: "Import links: every video linked in pasted text or a document" },
      { sel: "header button:has-text('Upload')", at: "b", dy: -4, label: "Upload video files (mp4, mov, webm, up to 2 GB)" },
      { sel: "[data-testid=dropzone]", at: "tl", outline: true, label: "Drop zone: drop videos (or a document of links) anywhere here" },
      { sel: ["[data-testid=dropzone] form label", "[data-testid=dropzone] form button"], at: "tl", label: "Paste one video URL, optionally with the creator's @handle, then Import" },
      { sel: "tr[data-clip] >> nth=0 >> td:nth-child(5) span", at: "r", label: "Status: Uploading, Downloading, Probing, Ready, or Failed with the reason" },
      { sel: "tr[data-clip] >> nth=0 >> td:nth-child(6)", at: "l", dx: 50, label: "Renders made from this clip" },
      { sel: "tr[data-clip] >> nth=0 >> a:has-text('Open editor')", at: "l", label: "Open editor (shows on hover), and Remove" },
    ],
  },
  {
    file: "library-import-links.png",
    page: "docs/guide/02-library.md",
    title: "Import links",
    alt: "The Import links dialog after reading pasted text: 4 videos found, 2 already in the library, and an Import 2 videos button",
    go: async (p) => {
      await p.goto(`${BASE}/library`)
      await p.getByRole("button", { name: "Import links" }).click()
      await p.getByLabel("Links").fill(
        [
          "Tonight's picks from the group chat:",
          "https://www.tiktok.com/@scout2015/video/6718335390845095173",
          "https://youtube.com/shorts/aqz-KE-bpKQ",
          "https://www.instagram.com/reel/C8x1Qm2tR4a/",
          "https://www.tiktok.com/@patroxofficial/video/6742501081818877190?is_from_webapp=1",
          "the first one again: https://www.tiktok.com/@scout2015/video/6718335390845095173",
          "menu: https://example.com/menu",
        ].join("\n")
      )
      await p.getByRole("button", { name: "Find links" }).click()
      await p.getByText(/Import \d+ videos?/).waitFor()
    },
    callouts: [
      { sel: "dialog span.text-lg", at: "l", label: "Videos found, and where (Change goes back)" },
      { sel: "dialog div.flex-wrap", at: "l", label: "Per platform; repeats dropped, clips already in the library, other links skipped" },
      { sel: "dialog ol", at: "l", label: "Every video link; In library ones are not imported again" },
      { sel: "dialog button:has-text('Import')", at: "t", label: "Import the new ones: they download in the background" },
    ],
  },
  {
    file: "library-published.png",
    page: "docs/guide/02-library.md",
    title: "Published",
    alt: "The Library's Published tab: filters for account, brand and date range, and a table of published posts with Instagram links and a Re-render for… menu",
    go: async (p) => p.goto(`${BASE}/library?tab=published`),
    callouts: [
      { sel: ["main label:has-text('Account')", "main label:has(svg + select)"], at: "r", outline: true, label: "Filter by account, brand and date range" },
      { sel: "main span.tabular-nums:has-text('posts')", at: "l", label: "How many posts; times are in your browser's zone" },
      { sel: "tr[data-post] >> nth=0 >> td:nth-child(2) div.font-medium", at: "r", dx: -8, label: "When it went out (and in the account's zone, if different)" },
      { sel: "tr[data-post] >> nth=0 >> a:has-text('View on Instagram')", at: "l", label: "Open the Reel on Instagram" },
      { sel: "tr[data-post] >> nth=0 >> td:last-child label", at: "l", label: "Re-render for… a brand: same clip and crop, that brand's default logo and caption; opens the editor" },
    ],
  },
  {
    file: "editor.png",
    page: "docs/guide/03-editor.md",
    title: "Editor",
    alt: "The Editor: a 9:16 stage with the brand logo and the Instagram Reels overlay, the controls panel (brand, logo, crop, cover, caption, Render) and the renders for this clip",
    go: async (p) => {
      await p.goto(`${BASE}/editor/${EDITOR_CLIP}?brand=5`)
      await p.waitForFunction(() => document.querySelector("[data-fracbox=logo] img")?.naturalWidth > 0)
    },
    callouts: [
      { sel: "header nav", at: "b", label: "The clip: back to Library, its length, size, frame rate and audio" },
      { sel: "[data-fracbox=logo]", at: "tl", outline: true, label: "The logo: drag it, pull a corner to resize" },
      { sel: "div.inline-flex:has(> button:text-is('output'))", at: "b", label: "Output, Crop and Cover views of the stage" },
      { sel: "button:has-text('IG overlay')", at: "b", dx: 60, label: "IG overlay: Instagram's buttons and caption over the frame, and the safe zone" },
      { sel: "aside div.space-y-2:has(select[aria-label=Brand])", at: "tl", label: "Brand (or No logo); Save as brand default keeps this placement" },
      { sel: "aside fieldset", at: "tl", label: "Logo scale, opacity, snap position and margin" },
      { sel: "aside div.space-y-2:has(> textarea[aria-label=Caption])", at: "tl", label: "Caption: saved captions, reset to the brand template, limits" },
      { sel: "aside button:has-text('Render')", at: "l", label: "Render (⌘↵): queues the 1080x1920 MP4" },
      { sel: "aside:has(span:text-is('Renders for this clip'))", at: "tl", label: "Renders for this clip, newest first" },
    ],
  },
  {
    file: "editor-crop.png",
    page: "docs/guide/03-editor.md",
    title: "Editor: crop",
    alt: "The Editor in Crop view: the whole landscape source with a 9:16 crop box, and the Crop switch, presets and readout in the controls panel",
    go: async (p) => {
      await p.goto(`${BASE}/editor/${CROP_CLIP}`)
      await p.getByRole("switch", { name: "Crop" }).click()
      const box = p.locator("[data-fracbox=crop]")
      await box.waitFor()
      const r = await box.boundingBox()
      await p.mouse.move(r.x + r.width / 2, r.y + r.height / 2)
      await p.mouse.down()
      await p.mouse.move(r.x + r.width / 2 - 70, r.y + r.height / 2, { steps: 8 })
      await p.mouse.up()
      await p.mouse.move(10, 890)
    },
    callouts: [
      { sel: "[data-fracbox=crop]", at: "tr", outline: true, label: "The crop box on the full source: drag to move, pull an edge to resize (9:16)" },
      { sel: "div.inline-flex:has(> button:text-is('crop'))", at: "b", label: "Crop view (Output shows the result)" },
      { sel: "aside div.space-y-2:has([aria-label=Crop])", at: "tl", label: "Crop on or off (off: the source fills the frame, centred)" },
      { sel: "aside div.grid:has(> button:text-is('9:16 region'))", at: "l", label: "Presets: 9:16 region or Full frame" },
      { sel: "[data-testid=crop-readout]", at: "l", label: "Position and size as fractions, and the scaling applied" },
    ],
  },
  {
    file: "editor-cover.png",
    page: "docs/guide/03-editor.md",
    title: "Editor: cover",
    alt: "The Editor in Cover view: the chosen cover image with the 3:4 profile grid band, and the Cover section with saved covers and Choose image…",
    go: async (p) => {
      await p.goto(`${BASE}/editor/${EDITOR_CLIP}?brand=5`)
      await p.locator("button:text-is('cover')").click()
      await p.getByText("Profile grid 3:4").waitFor()
    },
    callouts: [
      { sel: "span:text-is('Profile grid 3:4') >> xpath=..", at: "tr", outline: true, label: "The middle 3:4: what the profile grid shows" },
      { sel: "div.inline-flex:has(> button:text-is('cover'))", at: "b", label: "Cover view (needs a cover)" },
      { sel: "aside div.space-y-2:has(> div > span:text-is('Cover'))", at: "tl", label: "The Reel's cover; Remove lets Instagram pick a frame" },
      { sel: "select[aria-label='Saved covers'] >> xpath=..", at: "r", label: "Saved covers (the default one is preselected)" },
      { sel: "aside button:has-text('Choose image')", at: "r", label: "Choose any image: it becomes a 1080x1920 JPEG" },
    ],
  },
  {
    file: "editor-renders.png",
    page: "docs/guide/03-editor.md",
    title: "Editor: render cards",
    alt: "The renders column with a failed render (View log, Retry) and ready renders; the Schedule popover is open with account, time, caption and Schedule / Post now",
    go: async (p) => {
      await p.goto(`${BASE}/editor/${EDITOR_CLIP}?brand=5`)
      await p.locator('li[data-render="30"] button:has-text("Schedule")').click()
      await p.getByText("Next free slot").waitFor()
    },
    callouts: [
      { sel: "li[data-status=FAILED]", at: "tl", label: "A failed render: its error, View log and Retry" },
      { sel: 'li[data-render="30"] div:has(> button:has-text("Preview"))', at: "l", label: "A ready render: Preview on the stage, Schedule…, Download the MP4, Delete" },
      { sel: "[role=dialog] label:has(span:text-is('Account'))", at: "l", label: "Which Instagram account" },
      { sel: "[role=dialog] input[aria-label=Date] >> xpath=../..", at: "l", label: "Time in the account's zone: the next free slot, or your own" },
      { sel: "[role=dialog] textarea >> xpath=..", at: "l", label: "The post's caption (from the render)" },
      { sel: "[role=dialog] button:text-is('Schedule') >> xpath=..", at: "l", label: "Schedule, or Post now (within about a minute)" },
    ],
  },
  {
    file: "customizations-brands.png",
    page: "docs/guide/04-customizations.md",
    title: "Customizations: brands",
    alt: "The Brands tab with the Flux Energy drawer open: logo, name and link, caption template, Auto-approve, Default brand and the default logo placement",
    go: async (p) => {
      await p.goto(`${BASE}/customizations/brands`)
      await p.locator("tr[data-brand]:has-text('Flux Energy')").click()
      await p.getByText("Default placement").waitFor()
    },
    callouts: [
      { sel: "header nav", at: "b", label: "Brands, Captions and Covers tabs" },
      { sel: "tr[data-brand]:has-text('Flux Energy') td:nth-child(2) button", at: "r", label: "A brand: click its row to edit it" },
      { sel: "[role=dialog] div:has(> div:text-is('Logo'))", at: "l", label: "Logo: a PNG with a transparent background" },
      { sel: "[role=dialog] div.grid:has(input[aria-label=Name])", at: "l", label: "Name and link (the link fills {link})" },
      { sel: "[role=dialog] div:has(> textarea[aria-label='Caption template'])", at: "l", label: "Caption template with {link} and {creator}" },
      { sel: "[role=dialog] label:has-text('Auto-approve')", at: "l", label: "Auto-approve: its posts skip Draft" },
      { sel: "[role=dialog] label:has-text('Default brand')", at: "l", label: "Default brand: preselected in the Editor" },
      { sel: "[role=dialog] div:has(> div:text-is('Default placement'))", at: "l", label: "Default logo placement; Edit in editor changes it" },
      { sel: "[role=dialog] button:has-text('Archive')", at: "l", label: "Archive, and Save (⌘S)" },
    ],
  },
  {
    file: "customizations-captions.png",
    page: "docs/guide/04-customizations.md",
    title: "Customizations: captions",
    alt: "The Captions tab with the Late-night pick drawer open: name, caption text with {link} and {creator}, and the Default caption switch",
    go: async (p) => {
      await p.goto(`${BASE}/customizations/captions`)
      await p.locator("tr[data-caption]:has-text('Late-night pick')").click()
      await p.getByText("Default caption").waitFor()
    },
    callouts: [
      { sel: "header nav", at: "b", label: "Brands, Captions and Covers tabs" },
      { sel: "tr[data-caption]:has-text('Late-night pick') button", at: "r", label: "A saved caption: click its row to edit it" },
      { sel: "[role=dialog] label:has(input[aria-label=Name])", at: "l", label: "Name, as the Editor lists it" },
      { sel: "[role=dialog] div:has(> textarea[aria-label=Caption])", at: "l", label: "The text, with its length and hashtag count" },
      { sel: "[role=dialog] p:has-text('the brand')", at: "l", label: "{link} and {creator} are filled in by the Editor" },
      { sel: "[role=dialog] label:has-text('Default caption')", at: "l", label: "Default caption: used when the brand has no template" },
      { sel: "[role=dialog] button:has-text('Delete')", at: "l", label: "Delete, and Save (⌘S)" },
    ],
  },
  {
    file: "customizations-covers.png",
    page: "docs/guide/04-customizations.md",
    title: "Customizations: covers",
    alt: "The Covers tab: a grid of saved cover images, one marked Default, each with Make default, rename and delete",
    go: async (p) => p.goto(`${BASE}/customizations/covers`),
    callouts: [
      { sel: "header button:has-text('Upload cover')", at: "l", label: "Upload cover (any image, several at once)" },
      { sel: "li[data-cover] >> nth=0", at: "tl", outline: true, label: "A saved cover, as the Editor uses it (1080x1920)" },
      { sel: "li[data-cover] >> nth=0 >> span:text-is('Default')", at: "r", label: "The default cover: preselected in the Editor" },
      { sel: "li[data-cover] >> nth=1 >> button:has-text('Make default')", at: "b", label: "Make default (or Clear default)" },
      { sel: "li[data-cover] >> nth=1 >> button[title=Rename]", at: "b", label: "Rename or delete (renders keep their own copy)" },
    ],
  },
  {
    file: "calendar.png",
    page: "docs/guide/05-calendar.md",
    title: "Calendar",
    alt: "The Calendar week board for one account: three posting slots a day with scheduled posts, drafts and free slots, and the Ready to schedule tray on the right",
    go: async (p) => {
      await p.goto(`${BASE}/calendar?account=${d.account}&week=${d.day0}`)
      await p.locator("[data-post]").first().waitFor()
    },
    callouts: [
      { sel: "a[aria-current=true]", at: "b", dy: -4, label: "The account; hover for its zone, slots, cap and gap" },
      { sel: "header button[title='Back to today'] >> xpath=..", at: "l", label: "Today, previous and next 7 days" },
      { sel: "header a[title='Open recovery']", at: "l", label: "Failed posts: opens recovery" },
      { sel: "header button:has-text('Approve')", at: "r", label: "Approve every draft (lists them first)" },
      { sel: "[data-post][data-status=SCHEDULED] >> nth=0", at: "tl", label: "A scheduled post: click to open it, drag to move it" },
      { sel: "[data-post][data-status=DRAFT] >> nth=0", at: "tl", label: "A draft: needs Approve before it can publish" },
      { sel: "[data-slot] >> nth=0", at: "tl", label: "A free slot: click or drop a render here" },
      { sel: "aside[data-queue]", at: "tl", dy: 76, label: "Ready to schedule: finished renders without a post" },
    ],
  },
  {
    file: "calendar-schedule.png",
    page: "docs/guide/05-calendar.md",
    title: "Calendar: auto-schedule",
    alt: "Three renders selected in the Ready to schedule tray; dashed Fill previews show where Auto-schedule will place them on the board",
    go: async (p) => {
      await p.goto(`${BASE}/calendar?account=${d.account}&week=${d.day0}`)
      await p.locator("[data-post]").first().waitFor()
      for (const id of [d.rid.L, 23, 5]) await p.locator(`aside[data-queue] label[data-render="${id}"] input`).check()
      await p.locator("[data-preview]").first().waitFor()
    },
    callouts: [
      { sel: "[data-preview]:has-text('Fill 1')", at: "tl", outline: true, label: "Where each selected render will land (Fill 1/3); click one to place just that" },
      { sel: "aside[data-queue] input[aria-label='Select every render']", at: "l", label: "Select every render" },
      { sel: `aside[data-queue] label[data-render="${d.rid.L}"]`, at: "l", label: "A selected render (or drag it onto a slot)" },
      { sel: "aside[data-queue] button[aria-expanded]", at: "b", label: "One clip in several brands: expand to pick" },
      { sel: "aside[data-queue] button:has-text('Auto-schedule')", at: "l", label: "Auto-schedule: fills the next free slots" },
      { sel: "[data-footnote]", at: "l", label: "Where they will go" },
    ],
  },
  {
    file: "calendar-post-drawer.png",
    page: "docs/guide/05-calendar.md",
    title: "Calendar: a post",
    alt: "A draft post's drawer over the calendar: the render player, account and brand, editable date, time and caption, and Save, Approve, Post now and Cancel post",
    go: async (p) => {
      await p.goto(`${BASE}/calendar?account=${d.account}&week=${d.day0}`)
      await p.locator(`[data-post="${d.draft}"] button`).first().click()
      await p.locator("[role=dialog] textarea").waitFor()
    },
    callouts: [
      { sel: "[role=dialog] h2", at: "l", label: "Status and post id" },
      { sel: "[role=dialog] button[aria-label=Play] >> xpath=..", at: "tl", label: "The render: click to play it" },
      { sel: ["[role=dialog] div.space-y-1\\.5.text-sm > div >> nth=0", "[role=dialog] div.space-y-1\\.5.text-sm > div >> nth=2"], at: "b", label: "Account, clip and brand" },
      { sel: "[role=dialog] input[aria-label=Date] >> xpath=../..", at: "l", label: "Date and time, in the account's zone" },
      { sel: "[role=dialog] div.space-y-1\\.5:has(> textarea)", at: "l", label: "Caption" },
      { sel: "[role=dialog] button:text-is('Save')", at: "t", label: "Save changes" },
      { sel: "[role=dialog] button:text-is('Approve')", at: "t", label: "Approve: the draft becomes Scheduled" },
      { sel: "[role=dialog] button:text-is('Post now')", at: "t", label: "Post now (off while publishing is off)" },
      { sel: "[role=dialog] button:text-is('Cancel post')", at: "t", dx: 50, label: "Cancel post: its render goes back to the tray" },
    ],
  },
  {
    file: "accounts.png",
    page: "docs/guide/06-accounts.md",
    title: "Accounts",
    alt: "The Accounts page: one Instagram account card with its connection, quota, today's count, time zone, posting slot times, daily cap, minimum gap and Disable",
    go: async (p) => p.goto(`${BASE}/accounts`),
    callouts: [
      { sel: "header button:has-text('Connect account')", at: "b", label: "How to connect an account (in Zernio)" },
      { sel: "header button:has-text('Sync accounts')", at: "b", label: "Sync accounts from Zernio" },
      { sel: "article[data-account] span:has-text('Connected') >> nth=0", at: "r", label: "Connection state" },
      { sel: "article[data-account] div.space-y-1 > div >> nth=0", at: "r", dy: -3, label: "Meta's publishing quota and today's posts against the daily cap" },
      { sel: "article[data-account] div.space-y-1 > div >> nth=1", at: "r", dy: 3, label: "Last published and next post" },
      { sel: "article[data-account] label:has(select[aria-label=Timezone])", at: "r", label: "Timezone the slots are in" },
      { sel: "article[data-account] div.flex-wrap:has(button[aria-label^='Remove'])", at: "r", label: "Posting slots: remove, Add, or pick Presets" },
      { sel: "article[data-account] div:has(> input[aria-label='Daily cap'])", at: "r", label: "Daily cap and minimum gap between posts" },
      { sel: "article[data-account] button:has-text('Disable')", at: "r", label: "Disable: cancels its drafts and scheduled posts" },
    ],
  },
  {
    file: "recover.png",
    page: "docs/guide/07-publishing-and-recovery.md",
    title: "Recover",
    alt: "The Recover page on a phone: a failed post with its cause, what Instagram said, a Re-render and retry button, Dismiss, and Technical details",
    viewport: { width: 480, height: 900 }, // a phone-width column (max-w-md) with room for the callouts
    go: async (p) => p.goto(`${BASE}/recover/${d.failed}`),
    callouts: [
      { sel: "main > div >> nth=0", at: "l", label: "Status and what went wrong" },
      { sel: "button[aria-label='Play the render']", at: "r", label: "The render: tap to play" },
      { sel: "main span.text-md", at: "r", label: "Account and when it was due" },
      { sel: "main p.text-md", at: "l", label: "The cause in plain words, and Instagram's own message" },
      { sel: "main button:has-text('Re-render')", at: "l", label: "The one remedy for this failure" },
      { sel: "main p:has-text('Creates a fresh render')", at: "l", label: "What the remedy does, and the slot it takes" },
      { sel: "main button:has-text('Dismiss')", at: "l", label: "Dismiss: cancel the post instead" },
      { sel: "summary", at: "l", label: "Technical details: error code, ids, raw payload" },
    ],
  },
  {
    file: "hero.png",
    page: "README.md",
    title: "Clipper",
    alt: "Clipper's calendar: a week of Instagram Reels scheduled across three daily slots, with finished renders waiting in the Ready to schedule tray",
    go: async (p) => {
      await p.goto(`${BASE}/calendar?account=${d.account}&week=${d.day0}`)
      await p.locator("[data-post]").first().waitFor()
    },
    callouts: [],
  },
]

// ---- 4. run
const data = process.env.PREP === "0" ? JSON.parse(readFileSync("/tmp/clipper-docs-prep.json")) : await prep()
writeFileSync("/tmp/clipper-docs-prep.json", JSON.stringify(data))
log("data", data)
mkdirSync(OUT, { recursive: true })
const browser = await chromium.launch({ channel: "chrome" })
const manifestPath = `${OUT}manifest.json`
let manifest = []
try {
  manifest = JSON.parse(readFileSync(manifestPath))
} catch {
  // first run
}
for (const s of shots(data)) {
  if (ONLY && !ONLY.includes(s.file.replace(".png", ""))) continue
  const ctx = await browser.newContext({ viewport: s.viewport ?? { width: 1440, height: 900 }, colorScheme: "dark", timezoneId: "Europe/London", locale: "en-GB" })
  await ctx.addCookies([{ name: "clipper_session", value: SESSION, url: BASE }])
  const page = await ctx.newPage()
  page.on("pageerror", (e) => log("pageerror", s.file, e.message))
  page.on("dialog", (dlg) => dlg.dismiss()) // nothing here should confirm anything
  await s.go(page)
  await settle(page)
  await annotate(page, s.callouts)
  await page.screenshot({ path: OUT + s.file })
  await ctx.close()
  const entry = { file: s.file, page: s.page, title: s.title, alt: s.alt, callouts: s.callouts.map((c, i) => ({ n: i + 1, selector: [c.sel].flat().join(" + "), label: c.label })) }
  manifest = [...manifest.filter((m) => m.file !== s.file), entry]
  log("wrote", s.file)
}
await browser.close()
const order = shots(data).map((s) => s.file)
manifest.sort((a, b) => order.indexOf(a.file) - order.indexOf(b.file))
writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + "\n")
log("manifest", manifestPath)
