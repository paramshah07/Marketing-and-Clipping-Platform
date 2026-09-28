// The preview-vs-render contract: numbers here are the backend's (backend/app/services/render.py).
import { describe, expect, it } from "vitest"
import { GRID, IG, OUT_H, OUT_W, clamp, coverFit, crop916, cropAspect, cropPx, logoAspect, moveBox, outputView, resizeBox, snapPosition } from "./geometry"

const close = (a: object, b: object, digits = 6) =>
  Object.entries(b).forEach(([k, v]) => expect((a as Record<string, number>)[k], k).toBeCloseTo(v, digits))

describe("FracBox maths", () => {
  const b = { x: 0.5, y: 0.1, w: 0.2, h: 0.1 }

  it("move clamps the whole box inside the container", () => {
    close(moveBox(b, 0.5, 0.95), { x: 0.8, y: 0.9, w: 0.2, h: 0.1 })
    close(moveBox(b, -0.9, -0.9), { x: 0, y: 0 })
  })

  it("corner resize keeps the aspect and the opposite corner", () => {
    close(resizeBox(b, "se", 0.1, 0, 0.5, 0.02), { x: 0.5, y: 0.1, w: 0.3, h: 0.15 })
    close(resizeBox(b, "nw", -0.1, 0, 0.5, 0.02), { x: 0.4, y: 0.05, w: 0.3, h: 0.15 })
    close(resizeBox(b, "ne", 0, -0.05, 0.5, 0.02), { x: 0.5, y: 0.05, w: 0.3, h: 0.15 }) // dy wins
  })

  it("corner resize clamps to the frame and to min/max width", () => {
    close(resizeBox(b, "se", 0.9, 0, 0.5, 0.02), { x: 0.5, y: 0.1, w: 0.5, h: 0.25 }) // right edge
    close(resizeBox(b, "nw", -1, 0, 0.5, 0.02), { x: 0.3, y: 0, w: 0.4, h: 0.2 }) // top edge
    close(resizeBox(b, "se", -0.5, 0, 0.5, 0.02), { w: 0.02, h: 0.01 })
    close(resizeBox(b, "se", 0.2, 0, 0.5, 0.02, 0.3), { w: 0.3 })
  })

  it("edge handles use one axis only", () => {
    close(resizeBox(b, "e", 0.1, 0.3, 0.5, 0.02), { x: 0.5, y: 0.1, w: 0.3, h: 0.15 }) // dy ignored
    close(resizeBox(b, "n", 0.3, -0.05, 0.5, 0.02), { x: 0.5, y: 0.05, w: 0.3, h: 0.15 }) // dx ignored, bottom fixed
    close(resizeBox(b, "w", -0.1, 0.3, 0.5, 0.02), { x: 0.4, y: 0.1, w: 0.3, h: 0.15 }) // right edge fixed
  })

  it("a box taller than the frame starts at 0, never above it (tall logos)", () => {
    expect(clamp(0.5, 0, -1.25)).toBe(0)
    const h = 0.6 * logoAspect(60, 400) // a 60x400 logo at 60% width: 2.25 frame heights
    expect(snapPosition(6, 0.6, h, 0.04).y).toBe(0)
  })
})

describe("logo = backend overlay (scale=round(w*1080):-1 at round(x*1080), round(y*1920))", () => {
  it("snaps inside the IG safe zone; preview height within 0.5 px of the render", () => {
    const [lw, lh] = [400, 160]
    const w = 0.22
    const h = w * logoAspect(lw, lh) // FracBox height, fraction of the output height
    expect(h).toBeCloseTo(0.0495, 10)
    const tr = snapPosition(2, w, h, 0.04) // top-right, 4% margin
    close(tr, { x: 0.68, y: 0.1625 }, 10)
    close(snapPosition(4, w, h, 0.04), { x: 0.39, y: 0.37025 }, 10)
    close(snapPosition(6, w, h, 0.04), { x: 0.1, y: 0.578 }, 10)
    for (let cell = 0; cell < 9; cell++) {
      const p = snapPosition(cell, w, h, 0.04)
      expect(p.x).toBeGreaterThanOrEqual(IG.side)
      expect(p.x + w).toBeLessThanOrEqual(1 - IG.side + 1e-12)
      expect(p.y).toBeGreaterThanOrEqual(IG.top)
      expect(p.y + h).toBeLessThanOrEqual(1 - IG.bottom + 1e-12)
    }
    // backend px for the top-right snap
    const W = Math.round(w * OUT_W)
    const render = { x: Math.round(tr.x * OUT_W), y: Math.round(tr.y * OUT_H), w: W, h: Math.round((W * lh) / lw) }
    expect(render).toEqual({ x: 734, y: 312, w: 238, h: 95 })
    expect(Math.abs(h * OUT_H - render.h)).toBeLessThanOrEqual(0.5) // the preview's height (from the logo's aspect)
  })
})

describe("crop = backend crop_px + cover", () => {
  it("locks to 9:16 in source px", () => {
    close(crop916(1920, 1080), { x: 0.341796875, y: 0, w: 0.31640625, h: 1 }, 12)
    close(crop916(320, 240), { w: 0.421875, h: 1 }, 12) // the phase-2 crop render
    close(crop916(1080, 1920), { x: 0, y: 0, w: 1, h: 1 }, 12)
    expect(cropPx(crop916(1920, 1080), 1920, 1080)).toEqual([606, 1080, 656, 0]) // floored to even
    const r = resizeBox(crop916(1920, 1080), "se", -0.1, 0, cropAspect(1920, 1080), 0.05)
    expect((r.w * 1920) / (r.h * 1080)).toBeCloseTo(9 / 16, 12)
  })

  it("no crop = object-fit: cover, centred", () => {
    close(outputView(1920, 1080, null), { left: -(16 / 9 / (9 / 16) - 1) / 2, top: 0, width: 256 / 81, height: 1 }, 12)
    close(outputView(1080, 1920, null), { left: 0, top: 0, width: 1, height: 1 }, 12)
  })

  it("crop region fills the output (scaled to cover, centre-cropped)", () => {
    const v = outputView(1920, 1080, crop916(1920, 1080))
    const s = 1080 / 606 // cover scale of the 606x1080 region
    close(v, { left: (-656 * s) / 1080, top: (1920 - 1080 * s) / 2 / 1920, width: (1920 * s) / 1080, height: (1080 * s) / 1920 }, 12)
    expect(v.left * OUT_W + 656 * s).toBeCloseTo(0, 9) // crop's left edge at output x = 0
    expect(v.left * OUT_W + (656 + 606) * s).toBeCloseTo(OUT_W, 9) // right edge at 1080
    expect(Math.abs(v.top * OUT_H)).toBeLessThan(2.5) // ffmpeg: 1080x1924 centre-cropped to 1920 -> 2 px
  })
})

describe("Reel cover = the 1080x1920 JPEG the Editor uploads", () => {
  it("cover-fits and centre-crops any image", () => {
    close(coverFit(1600, 1200), { sx: 462.5, sy: 0, sw: 675, sh: 1200 }, 12) // wide: the sides go
    close(coverFit(1000, 3000), { sx: 0, sy: 3000 / 2 - 1000 * (16 / 9) / 2, sw: 1000, sh: 1000 * (16 / 9) }, 9) // tall: top and bottom go
    close(coverFit(OUT_W, OUT_H), { sx: 0, sy: 0, sw: OUT_W, sh: OUT_H }, 12)
    close(coverFit(540, 960), { sx: 0, sy: 0, sw: 540, sh: 960 }, 12) // 9:16, upscaled 2x, nothing cut
  })

  it("the profile grid keeps the middle 3:4", () => {
    expect(OUT_H * (1 - 2 * GRID)).toBe((OUT_W * 4) / 3) // 1440
  })
})
