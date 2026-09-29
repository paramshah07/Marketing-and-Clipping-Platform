import type { CoverOut } from "@/api"
import { OUT_H, OUT_W, coverFit } from "@/lib/geometry"
import { signedOut } from "@/lib/utils"

/** Any image the browser can decode -> the 1080x1920 JPEG that gets published: cover-fit, centre-cropped, black
 * under transparency. createImageBitmap applies the EXIF orientation. */
export async function coverJpeg(file: File): Promise<Blob> {
  const img = await createImageBitmap(file).catch(() => null)
  if (!img) throw new Error(`Can't read ${file.name} as an image in this browser`)
  const c = new OffscreenCanvas(OUT_W, OUT_H)
  const g = c.getContext("2d")!
  g.fillStyle = "#000"
  g.fillRect(0, 0, OUT_W, OUT_H)
  g.imageSmoothingQuality = "high" // photos are usually downscaled a lot
  const { sx, sy, sw, sh } = coverFit(img.width, img.height)
  g.drawImage(img, sx, sy, sw, sh, 0, 0, OUT_W, OUT_H)
  img.close()
  return c.convertToBlob({ type: "image/jpeg", quality: 0.9 })
}

/** A saved cover's JPEG (Customizations), attached to a render like a chosen file: the render gets its own copy. */
export async function savedCover(c: CoverOut): Promise<Blob> {
  const r = await fetch(c.image_url)
  if (r.status === 401) signedOut()
  if (!r.ok) throw new Error(`Couldn't load the saved cover ${c.name} (HTTP ${r.status})`)
  return r.blob()
}
