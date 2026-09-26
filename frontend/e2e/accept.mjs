// Phase 3 acceptance: drives the real UI (compose stack up + `npm run dev`) in Google Chrome.
//   node e2e/accept.mjs
// Uploads a generated clip through the drop zone, creates a brand with a transparent logo, edits and
// renders three configurations in the editor, then checks preview == render for each: the overlay_config
// the UI sent, the logo's DOM box on the stage and the logo's bounding box in a frame of the rendered MP4
// must agree within 1% of the frame (same for a red marker in the source, which checks the crop / cover).
//   A: dragged + scaled logo, 9:16 crop dragged left (⌘↵)
//   B: crop off (cover), logo snapped top right (Render button)
//   C: crop resized with its left edge handle, logo snapped top left (⌘↵)
// Screenshots: docs/phase-3-*.png.
// Never calls Zernio, publishes nothing.
import { execFileSync } from "node:child_process"
import { chromium } from "playwright-core"

const ROOT = new URL("../..", import.meta.url).pathname
const BASE = process.env.BASE_URL ?? "http://localhost:5173"
const TOL = 0.01
const worker = (cmd, opts = {}) => execFileSync("docker", ["compose", "exec", "-T", "worker", "sh", "-c", cmd], { cwd: ROOT, maxBuffer: 64 << 20, ...opts })
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a)
const out = {}

// 1. media, made by ffmpeg in the worker: 1920x1080 dark noise + grid + a red 80x80 marker at (880, 500),
// a 400x200 RGBA logo (green cross touching all four edges, the rest transparent) and an opaque PNG.
log("making media in data/qa/phase3")
worker(`set -e; mkdir -p /data/qa/phase3 && cd /data/qa/phase3
ffmpeg -v error -y -f lavfi -i "color=c=0x1c2330:s=1920x1080:r=30:d=8,noise=alls=8:allf=t,drawgrid=w=120:h=120:t=2:c=white@0.25,drawbox=x=880:y=500:w=80:h=80:color=red:t=fill" -f lavfi -i "sine=f=440:d=8" -c:v libx264 -crf 22 -preset veryfast -pix_fmt yuv420p -c:a aac -shortest src.mp4
ffmpeg -v error -y -f lavfi -i "color=s=400x200:c=black,format=rgba" -vf "geq=r=0:g=255:b=0:a=if(between(Y\\,80\\,119)+between(X\\,180\\,219)\\,255\\,0)" -frames:v 1 logo.png
ffmpeg -v error -y -f lavfi -i "color=s=400x200:c=white" -frames:v 1 -pix_fmt rgb24 logo-opaque.png`)
const MEDIA = `${ROOT}data/qa/phase3/`

const browser = await chromium.launch({ channel: "chrome" })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } })
const page = await ctx.newPage()
page.on("pageerror", (e) => log("pageerror", e.message))
const shot = (name) => page.screenshot({ path: `${ROOT}docs/phase-3-${name}.png` })

// 2. upload through the drop zone, throttled so the progress row is visible
await page.goto(`${BASE}/library`, { waitUntil: "networkidle" })
await page.evaluate(async () => {
  const blob = await (await fetch("/media/qa/phase3/src.mp4")).blob()
  window.__clip = new File([blob], "phase3-src.mp4", { type: "video/mp4" })
})
const cdp = await ctx.newCDPSession(page)
await cdp.send("Network.enable")
await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: 3_000_000 })
const uploaded = page.waitForResponse((r) => r.url().endsWith("/api/clips") && r.request().method() === "POST", { timeout: 120_000 })
await page.evaluate(() => {
  const dt = new DataTransfer()
  dt.items.add(window.__clip)
  document.querySelector("[data-testid=dropzone]").dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }))
})
const upRow = page.locator('tr[data-upload="phase3-src.mp4"]')
await page.waitForFunction(() => /Uploading [3-9]\d%/.test(document.querySelector('tr[data-upload="phase3-src.mp4"]')?.textContent ?? ""))
out.uploadProgress = (await upRow.innerText()).replace(/\s+/g, " ")
log("mid-upload:", out.uploadProgress)
await shot("library-uploading")
const clip = await (await uploaded).json()
await cdp.send("Network.emulateNetworkConditions", { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 })
log("upload response", clip.id, clip.status)
const readyThumb = page.locator(`tr[data-clip="${clip.id}"][data-status="READY"] img`).first()
await readyThumb.waitFor({ timeout: 60_000 })
await page.waitForFunction((id) => document.querySelector(`tr[data-clip="${id}"] img`)?.naturalWidth > 0, clip.id)
out.clipRow = (await page.locator(`tr[data-clip="${clip.id}"]`).innerText()).replace(/\s+/g, " ")
log("READY row:", out.clipRow)
await shot("library")
await page.getByRole("button", { name: /^Published/ }).click()
await page.getByText("Published at").waitFor() // react-router commits in a transition
await shot("library-published")

// 3. brand: an opaque logo is refused (alpha surfaced), then the transparent one is accepted
await page.goto(`${BASE}/brands`)
await page.getByRole("button", { name: "New brand" }).click()
await page.getByLabel("Name").fill(`Phase3 Test Co ${Date.now() % 10000}`)
await page.getByLabel("Link").fill("https://phase3.test/go")
await page.getByLabel("Caption template").fill("Night fuel → {link} · clip by {creator}\n#ad #phase3 #clipper")
const logoInput = page.locator('input[type=file][accept="image/png"]')
await logoInput.setInputFiles(`${MEDIA}logo-opaque.png`)
const created = page.waitForResponse((r) => r.url().endsWith("/api/brands") && r.request().method() === "POST")
await page.getByRole("button", { name: "Create" }).click()
const brand = await (await created).json()
const alphaError = page.getByText(/logo PNG has no transparen/)
await alphaError.waitFor()
out.alphaError = await alphaError.innerText()
log("opaque logo:", out.alphaError)
await shot("brands-alpha-error")
await logoInput.setInputFiles(`${MEDIA}logo.png`)
const logoUp = page.waitForResponse((r) => r.url().endsWith(`/api/brands/${brand.id}/logo`))
await page.getByRole("button", { name: "Save" }).click()
out.logoUpload = (await logoUp).status()
await page.locator(`tr[data-brand="${brand.id}"] img`).first().waitFor()
await page.getByText("Current logo").waitFor()
log("brand", brand.id, "logo upload HTTP", out.logoUpload)
await shot("brands")

// 4. editor: pick the brand, drag the logo, scale, opacity, IG overlay, crop
await page.goto(`${BASE}/library`)
await page.locator(`tr[data-clip="${clip.id}"]`).getByRole("link", { name: "Open editor" }).click()
await page.locator("select[aria-label=Brand]").selectOption(String(brand.id))
const logoBox = page.locator("[data-fracbox=logo]")
await page.waitForFunction(() => document.querySelector("[data-fracbox=logo] img")?.naturalWidth > 0)
await page.waitForFunction(() => document.querySelector("[data-stage] video")?.readyState >= 1)
let r = await logoBox.boundingBox()
await page.mouse.move(r.x + r.width / 2, r.y + r.height / 2)
await page.mouse.down()
await page.mouse.move(r.x + r.width / 2 - 90, r.y + r.height / 2 + 160, { steps: 12 })
await page.mouse.up()
const thumb = (name) => page.getByRole("slider", { name })
await thumb("Logo scale").focus()
for (let i = 0; i < 10; i++) await page.keyboard.press("ArrowRight") // +5% of width
await thumb("Logo opacity").focus()
for (let i = 0; i < 15; i++) await page.keyboard.press("ArrowLeft") // 85%
const igButton = page.getByRole("button", { name: "IG overlay" })
await igButton.click()
out.igOverlayOff = await page.locator("[data-ig-overlay]").count()
await igButton.click()
out.igOverlayOn = await page.locator("[data-ig-overlay]").count()
await page.getByRole("button", { name: "crop", exact: true }).click()
const cropBox = page.locator("[data-fracbox=crop]")
await cropBox.waitFor()
const before = await page.getByTestId("crop-readout").innerText()
r = await cropBox.boundingBox()
await page.mouse.move(r.x + r.width / 2, r.y + r.height / 2)
await page.mouse.down()
await page.mouse.move(r.x + r.width / 2 - 40, r.y + r.height / 2, { steps: 8 })
await page.mouse.up()
out.crop = { before, after: await page.getByTestId("crop-readout").innerText() }
log("crop", out.crop)
await shot("editor-crop")
const toOutput = async () => {
  await page.getByRole("button", { name: "output", exact: true }).click()
  await logoBox.waitFor()
  await page.waitForTimeout(300)
}
await toOutput()
await shot("editor")

// DOM geometry as fractions of the 9:16 stage, then render; returns what was sent and what came back
async function renderNow(name, how) {
  const dom = await page.evaluate(() => {
    const s = document.querySelector("[data-stage=output]").getBoundingClientRect()
    const f = (e) => {
      const b = e.getBoundingClientRect()
      return { x: (b.left - s.left) / s.width, y: (b.top - s.top) / s.height, w: b.width / s.width, h: b.height / s.height }
    }
    return { stagePx: [s.width, s.height], logo: f(document.querySelector("[data-fracbox=logo]")), video: f(document.querySelector("[data-stage] video")) }
  })
  const sentReq = page.waitForRequest((q) => q.url().endsWith("/api/renders") && q.method() === "POST")
  const renderRes = page.waitForResponse((q) => q.url().endsWith("/api/renders") && q.request().method() === "POST")
  await how()
  const sent = (await sentReq).postDataJSON()
  const render = await (await renderRes).json()
  log(name, "render", render.id, "sent", JSON.stringify(sent))
  await page.waitForTimeout(1100) // the editor ignores another render for 1 s (double ⌘↵ guard)
  return { name, dom, sent, render }
}
const cmdEnter = () => page.keyboard.press("ControlOrMeta+Enter") // the Render button's shortcut

// 5. three renders
const runs = [await renderNow("A", cmdEnter)]
await page.getByRole("switch", { name: "Crop" }).click() // off: cover
await page.getByRole("button", { name: "Top right" }).click()
await page.waitForTimeout(200)
runs.push(await renderNow("B", () => page.getByRole("button", { name: /^Render/ }).click()))
await page.getByRole("switch", { name: "Crop" }).click() // on, opens Crop mode with A's region
await cropBox.waitFor()
out.cropHandles = await page.locator("[data-fracbox=crop] [data-handle]").count()
r = await page.locator("[data-fracbox=crop] [data-handle=w]").boundingBox()
await page.mouse.move(r.x + r.width / 2, r.y + r.height / 2)
await page.mouse.down()
await page.mouse.move(r.x + r.width / 2 + 40, r.y + r.height / 2, { steps: 8 })
await page.mouse.up()
out.cropC = await page.getByTestId("crop-readout").innerText()
out.cropCRegion = await page.getByText(/^Region /).innerText()
log("crop C", out.cropC, "|", out.cropCRegion, "| handles", out.cropHandles)
await toOutput()
await page.getByRole("button", { name: "Top left" }).click()
await page.waitForTimeout(200)
runs.push(await renderNow("C", cmdEnter))
await page.locator(`li[data-render="${runs[0].render.id}"][data-status="RENDERING"], li[data-render="${runs[0].render.id}"][data-status="READY"]`).first().waitFor()
await shot("editor-rendering")
for (const { render } of runs) await page.locator(`li[data-render="${render.id}"][data-status="READY"]`).waitFor({ timeout: 240_000 })
const render = runs[0].render
const card = page.locator(`li[data-render="${render.id}"]`)
out.renderCard = (await card.innerText()).replace(/\s+/g, " ")
await card.getByRole("button", { name: "Preview" }).click()
await page.waitForFunction((u) => {
  const v = document.querySelector("[data-stage=preview] video")
  return v?.src.endsWith(u) && v.videoWidth === 1080 && v.videoHeight === 1920
}, render.output_url ?? `/media/renders/${render.id}.mp4`)
await page.evaluate(() => {
  const v = document.querySelector("[data-stage=preview] video")
  v.currentTime = 2
  return new Promise((res) => v.addEventListener("seeked", res, { once: true }))
})
await shot("editor-preview")
out.previewDecoded = true
out.previewLabel = await page.locator("span", { hasText: new RegExp(`^Render ${render.id} · `) }).last().innerText()
await page.goto(`${BASE}/calendar`)
await shot("calendar")
await browser.close()

// 6. measure each rendered frame (ffmpeg in the worker, raw RGB out)
const W = 1080
const H = 1920
function bbox(rgb, test) {
  let [x0, y0, x1, y1] = [W, H, -1, -1]
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 3
      if (test(rgb[i], rgb[i + 1], rgb[i + 2])) (x0 = Math.min(x0, x)), (x1 = Math.max(x1, x)), (y0 = Math.min(y0, y)), (y1 = Math.max(y1, y))
    }
  return { x: x0 / W, y: y0 / H, w: (x1 - x0 + 1) / W, h: (y1 - y0 + 1) / H, px: [x0, y0, x1 - x0 + 1, y1 - y0 + 1] }
}
const diff = (a, b) => Math.max(...["x", "y", "w", "h"].map((k) => Math.abs(a[k] - b[k])))
const checks = {}
out.configs = runs.map(({ name, dom, sent, render }) => {
  const rgb = worker(`ffmpeg -v error -ss 2 -i /data/renders/${render.id}.mp4 -frames:v 1 -f rawvideo -pix_fmt rgb24 -`)
  const green = bbox(rgb, (R, G, B) => G > 128 && R < 100 && B < 100)
  const red = bbox(rgb, (R, G, B) => R > 150 && G < 90 && B < 90)
  const o = sent.overlay_config
  const config = { x: o.x, y: o.y, w: o.w, h: (o.w * W * (200 / 400)) / H } // logo is 400x200
  // the red marker's predicted place from the DOM: source px (880..960, 500..580) of 1920x1080 inside the <video> box
  const v = dom.video
  const redDom = { x: v.x + (880 / 1920) * v.w, y: v.y + (500 / 1080) * v.h, w: (80 / 1920) * v.w, h: (80 / 1080) * v.h }
  Object.assign(checks, {
    [`${name}: config vs render (logo)`]: diff(config, green),
    [`${name}: DOM vs render (logo)`]: diff(dom.logo, green),
    [`${name}: DOM vs config (logo)`]: diff(dom.logo, config),
    [`${name}: DOM vs render (${sent.crop_config ? "crop" : "cover"} marker)`]: diff(redDom, red),
  })
  return { name, render: render.id, sent, dom, config, renderLogo: green, renderMarker: red, redDom }
})
Object.assign(out, { clip: clip.id, brand: brand.id, checks })
console.log(JSON.stringify(out, null, 2))
const failed = Object.entries(checks).filter(([, d]) => !(d <= TOL))
if (failed.length || out.igOverlayOff !== 0 || out.igOverlayOn !== 1 || out.crop.before === out.crop.after || out.cropHandles !== 8) {
  console.error("FAIL", failed)
  process.exit(1)
}
console.log(`PASS: every check within ${TOL * 100}% of the frame (worst ${(Math.max(...Object.values(checks)) * 100).toFixed(2)}%)`)
