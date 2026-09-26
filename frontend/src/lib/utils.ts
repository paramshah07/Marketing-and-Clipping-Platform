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

export const RIGHTS = { own_content: "own content", permission_granted: "permission granted", none: "none" } as const
export type Rights = keyof typeof RIGHTS

export const MAX_UPLOAD_BYTES = 2 * 1024 ** 3 // backend MAX_UPLOAD_BYTES default
export const MAX_REEL_SECONDS = 900 // backend ZERNIO_MAX_REEL_SECONDS: Instagram's 15 min API Reel limit (Zernio's documented 90 s is not enforced)
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

export const fillCaption = (template: string | null, link: string | null, creator: string | null) =>
  (template ?? "").replaceAll("{link}", link ?? "").replaceAll("{creator}", creator ?? "")

/** "youtube.com/watch?v=…" for an http(s) URL (no scheme, no www); anything else as is. */
export function shortUrl(s: string) {
  if (!/^https?:\/\//i.test(s)) return s
  try {
    const u = new URL(s)
    return u.host.replace(/^www\./, "") + u.pathname + u.search
  } catch {
    return s
  }
}

/** Filename for uploads, host + path for URL imports. */
export const clipName = (c: ClipOut) => c.original_filename || (c.source_url ? shortUrl(c.source_url) : `Clip ${c.id}`)

/** FastAPI error body ({detail: string | [{loc, msg}]}) or anything else -> one line. */
export function errorText(e: unknown): string {
  const d = (e as { detail?: unknown })?.detail
  if (typeof d === "string") return d
  if (Array.isArray(d)) return d.map((x) => `${x.loc?.slice(1).join(".")}: ${x.msg}`).join("; ")
  return e instanceof Error ? e.message : String(e)
}
