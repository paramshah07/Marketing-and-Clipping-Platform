/* The preview-vs-render contract. Mirrors backend/app/services/render.py (build_ffmpeg_args, crop_px):
   - overlay {x, y, w} = fractions of the 1080x1920 OUTPUT; logo px width round(w*1080), height from the
     logo's own aspect (scale=W:-1), top-left at round(x*1080), round(y*1920);
   - crop {x, y, w, h} = fractions of the SOURCE display frame, floored to even px; the crop (or the whole
     source) is scaled to cover 1080x1920 and centre-cropped.
   A Box is {x, y, w, h} as fractions of whatever container it lives in. Tested in geometry.test.ts. */

export const OUT_W = 1080
export const OUT_H = 1920

export type Box = { x: number; y: number; w: number; h: number }
export type Corner = "nw" | "ne" | "sw" | "se"
export type Handle = Corner | "n" | "s" | "e" | "w"

// ponytail: Instagram Reels chrome, approximated from Meta's ads safe-zone guide (keep 14% top, 35% bottom,
// 6% sides clear; the action rail is ~0.11-0.21 W wide from ~0.60 H down). Fractions of the 9:16 frame.
// Re-measure against a real Reel if Instagram changes its layout.
export const IG = { top: 0.14, bottom: 0.35, side: 0.06, railW: 0.14, railTop: 0.6 }

/** lo wins when hi < lo (a box taller than its container still starts at 0, never above it). */
export const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v))

/** Drag: shift by (dx, dy), keeping the whole box inside the container. */
export const moveBox = (b: Box, dx: number, dy: number): Box => ({
  ...b,
  x: clamp(b.x + dx, 0, 1 - b.w),
  y: clamp(b.y + dy, 0, 1 - b.h),
})

// Edge handles reuse a corner's maths with the other axis ignored (the aspect is locked either way).
const EDGE = { n: "ne", s: "se", e: "se", w: "sw" } as const

/** Corner resize with the opposite corner fixed and h/w = aspect kept. The pointer axis that moved further
 * wins; w is clamped to [minW, maxW] and to the room left inside the container. */
export function resizeBox(b: Box, handle: Handle, dx: number, dy: number, aspect: number, minW: number, maxW = 1): Box {
  if (handle === "n" || handle === "s") dx = 0
  if (handle === "e" || handle === "w") dy = 0
  const corner = handle.length === 2 ? handle : EDGE[handle as keyof typeof EDGE]
  const sx = corner.includes("e") ? 1 : -1
  const sy = corner.includes("s") ? 1 : -1
  let w = Math.abs(dx) >= Math.abs(dy / aspect) ? b.w + sx * dx : (b.h + sy * dy) / aspect
  const roomX = sx > 0 ? 1 - b.x : b.x + b.w
  const roomY = (sy > 0 ? 1 - b.y : b.y + b.h) / aspect
  w = clamp(w, minW, Math.min(maxW, roomX, roomY))
  const h = w * aspect
  return { x: sx > 0 ? b.x : b.x + b.w - w, y: sy > 0 ? b.y : b.y + b.h - h, w, h }
}

/** h/w of the logo box in output fractions (ffmpeg scale=W:-1 keeps the logo's aspect). */
export const logoAspect = (logoW: number, logoH: number) => (logoH / logoW) * (OUT_W / OUT_H)

/** h/w of a 9:16 region in fractions of a srcW x srcH frame. */
export const cropAspect = (srcW: number, srcH: number) => (16 / 9) * (srcW / srcH)

/** The largest centred 9:16 region of the source ("9:16 region" preset). */
export function crop916(srcW: number, srcH: number): Box {
  const a = cropAspect(srcW, srcH)
  const w = Math.min(1, 1 / a)
  const h = w * a
  return { x: (1 - w) / 2, y: (1 - h) / 2, w, h }
}

/** backend crop_px: fractions -> even integer px [w, h, x, y]. */
export function cropPx(c: Box, srcW: number, srcH: number): [number, number, number, number] {
  const even = (v: number) => Math.floor(v / 2) * 2
  return [Math.max(2, even(c.w * srcW)), Math.max(2, even(c.h * srcH)), even(c.x * srcW), even(c.y * srcH)]
}

/** Cover scale from a cw x ch region to 1080x1920 (the "upscaled N.NNx" readout). */
export const coverScale = (cw: number, ch: number) => Math.max(OUT_W / cw, OUT_H / ch)

/** Where the whole source frame sits in the output, as fractions of the output: position the source
 * <video> at these percentages of the 9:16 stage and the stage shows exactly what the render will.
 * No crop = object-fit: cover. */
export function outputView(srcW: number, srcH: number, crop: Box | null) {
  const [cw, ch, cx, cy] = crop ? cropPx(crop, srcW, srcH) : [srcW, srcH, 0, 0]
  const s = coverScale(cw, ch)
  return {
    left: ((OUT_W - cw * s) / 2 - cx * s) / OUT_W,
    top: ((OUT_H - ch * s) / 2 - cy * s) / OUT_H,
    width: (srcW * s) / OUT_W,
    height: (srcH * s) / OUT_H,
  }
}

/** Snap-grid cell 0..8 (row-major) -> logo top-left inside the IG safe zone. margin is a fraction of the
 * output width, applied as the same number of px on both axes. */
export function snapPosition(cell: number, w: number, h: number, margin: number) {
  const my = (margin * OUT_W) / OUT_H
  const xs = [IG.side + margin, 0.5 - w / 2, 1 - IG.side - margin - w]
  const ys = [IG.top + my, (IG.top + 1 - IG.bottom) / 2 - h / 2, 1 - IG.bottom - my - h]
  return { x: clamp(xs[cell % 3], 0, 1 - w), y: clamp(ys[Math.floor(cell / 3)], 0, 1 - h) }
}
