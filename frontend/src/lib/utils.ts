import type { ClipOut } from "@/api"

export { cn } from "cn"

// Class strings lifted from the mockups (docs/design/*.html).
export const btn = {
  primary: "inline-flex h-7 items-center justify-center gap-1.5 rounded bg-accent px-3 font-medium text-white hover:bg-accent-hover disabled:pointer-events-none disabled:opacity-50",
  secondary: "inline-flex h-7 items-center justify-center gap-1.5 rounded border border-line-strong bg-raised px-3 font-medium text-fg hover:bg-hover disabled:pointer-events-none disabled:opacity-50",
  ghost: "inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded px-2 text-muted hover:bg-hover hover:text-fg disabled:pointer-events-none disabled:text-subtle",
  icon: "inline-grid size-7 shrink-0 place-items-center rounded text-muted hover:bg-hover hover:text-fg disabled:bg-transparent disabled:text-subtle disabled:opacity-50",
}
export const field = "h-7 rounded border border-line bg-panel px-2 text-base text-fg outline-none placeholder:text-subtle focus:border-muted"
export const label = "text-xs uppercase tracking-wider text-subtle"

/** Files read for the links in them (Library "Import links"), not uploaded as videos. */
export const DOCUMENTS = ["docx", "txt", "csv", "md", "rtf", "html", "htm", "xlsx", "pptx", "odt"]

export const MAX_UPLOAD_BYTES = 2 * 1024 ** 3 // backend MAX_UPLOAD_BYTES default
export const MAX_REEL_SECONDS = 900 // backend ZERNIO_MAX_REEL_SECONDS: Instagram's 15 min API Reel limit (Zernio's documented 90 s is not enforced)
export const MIN_REEL_SECONDS = 3 // backend ZERNIO_MIN_REEL_SECONDS

/** Clip and render error codes in plain words. */
export const CAUSES: Record<string, string> = {
  PRIVATE: "Private video",
  LOGIN_REQUIRED: "Needs login cookies",
  REMOVED: "Video removed",
  GEO_BLOCKED: "Blocked in this region",
  EXTRACTOR_FAILED: "Import failed",
  DURATION_OUT_OF_RANGE: "Must be 3 s to 15 min",
  PROBE_FAILED: "Not a readable video",
  THUMBNAIL_FAILED: "Thumbnail failed",
  WORKER_CRASHED: "Worker crashed",
  INTERRUPTED: "Interrupted",
  INTERNAL_ERROR: "Internal error",
  UPLOAD_ABANDONED: "Upload abandoned",
  FFMPEG_FAILED: "ffmpeg exited with an error",
  OUTPUT_TOO_LARGE: "Output file too large",
}

export const CAPTION_MAX = 2200
export const HASHTAG_MAX = 30

const pad = (n: number) => String(Math.floor(n)).padStart(2, "0")
export const mmss = (s?: number | null) => (s == null ? "—" : `${pad(s / 60)}:${pad(s % 60)}`)
export const mb = (bytes: number) => `${(bytes / 1e6).toFixed(1)} MB`

export function ago(iso: string, now = Date.now()) {
  const s = (now - Date.parse(iso)) / 1000
  if (s < 45) return "just now"
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  if (s < 2 * 86400) return "Yesterday"
  return `${Math.floor(s / 86400)}d ago`
}

export const hashtagCount = (s: string) => (s.match(/#[\p{L}\p{N}_]+/gu) ?? []).length

/** Brand template → caption. With no creator, the " · " part (or whole line) holding {creator} is dropped
 * instead of leaving "clip by " / "Clip: " dangling. */
export const fillCaption = (template: string | null, link: string | null, creator: string | null) =>
  (template ?? "")
    .split("\n")
    .map((line) => (creator || !line.includes("{creator}") ? line : line.split(" · ").filter((part) => !part.includes("{creator}")).join(" · ")))
    .filter((line, i, all) => line.trim() || !(template ?? "").split("\n")[i].includes("{creator}") || all.length === 1)
    .join("\n")
    .replaceAll("{link}", link ?? "")
    .replaceAll("{creator}", creator ?? "")

/** "tiktok.com/@creator/video/7612…" for an http(s) URL: no scheme, no www, no tracking query (YouTube's
 * ?v= is the video, so it stays); anything else as is. */
export function shortUrl(s: string) {
  if (!/^https?:\/\//i.test(s)) return s
  try {
    const u = new URL(s)
    const v = u.searchParams.get("v")
    return u.host.replace(/^www\./, "") + u.pathname.replace(/\/$/, "") + (v ? `?v=${v}` : "")
  } catch {
    return s
  }
}

/** Filename for uploads, host + path for URL imports. */
export const clipName = (c: ClipOut) => c.original_filename || (c.source_url ? shortUrl(c.source_url) : `Clip ${c.id}`)

/** FastAPI error body ({detail: string | [{loc, msg}]}) or anything else -> one line. */
const UNREACHABLE = "The server didn't answer. Is the stack running (docker compose up -d)?"

export function errorText(e: unknown): string {
  const d = (e as { detail?: unknown })?.detail
  if (typeof d === "string") return d
  if (Array.isArray(d)) return d.map((x) => `${x.loc?.slice(1).join(".")}: ${x.msg}`).join("; ")
  if (e instanceof Error) return e.message
  return typeof e === "string" && e.trim() ? e : UNREACHABLE // e.g. the dev proxy's empty 502 while the api is down
}
export { UNREACHABLE }
