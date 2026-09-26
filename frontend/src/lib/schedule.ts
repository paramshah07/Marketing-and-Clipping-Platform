// Time-zone math for the calendar and the schedule popover. Mirrors backend/app/services/slots.py:
// slot times are "HH:MM" wall clock in the account's IANA zone; a DST gap shifts forward and an
// overlap takes the earlier instant (Python fold=0). Days are "YYYY-MM-DD" calendar dates.

import type { PostOut } from "@/api"

const MIN = 60_000
const DAY = 86_400_000

const fmts = new Map<string, Intl.DateTimeFormat>()
function fmt(tz: string) {
  let f = fmts.get(tz)
  if (!f) {
    f = new Intl.DateTimeFormat("en-GB", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    fmts.set(tz, f)
  }
  return f
}

/** Wall clock of an instant in a zone. */
export function localParts(at: string | number | Date, tz: string) {
  const p = Object.fromEntries(fmt(tz).formatToParts(new Date(at)).map((x) => [x.type, x.value]))
  return { date: `${p.year}-${p.month}-${p.day}`, time: `${p.hour}:${p.minute}` }
}

const wallMs = (date: string, time: string) => Date.parse(`${date}T${time}:00Z`)

/** UTC offset of a zone at an instant, in ms (whole minutes). */
function offset(ms: number, tz: string) {
  const { date, time } = localParts(ms, tz)
  return wallMs(date, time) - Math.floor(ms / MIN) * MIN
}

/** Wall-clock date + time in tz -> UTC instant. Gap: shifted forward; overlap: the earlier one. */
export function zonedToUtc(date: string, time: string, tz: string): Date {
  const wall = wallMs(date, time)
  // the instant is wall - offset with offset in [-12h, +14h], so wall ± 1 day always brackets it
  const before = offset(wall - DAY, tz)
  const after = offset(wall + DAY, tz)
  const a = wall - before
  if (offset(a, tz) === before) return new Date(a) // normal, or overlap (earlier instant)
  const b = wall - after
  if (offset(b, tz) === after) return new Date(b)
  return new Date(a) // gap: pre-transition offset lands after the jump, like fold=0
}

export const addDays = (date: string, n: number) => new Date(wallMs(date, "00:00") + n * DAY).toISOString().slice(0, 10)

/** Monday..Sunday containing `date`. */
export function weekOf(date: string) {
  const dow = (new Date(wallMs(date, "00:00")).getUTCDay() + 6) % 7 // Mon = 0
  return Array.from({ length: 7 }, (_, i) => addDays(date, i - dow))
}

/** Today's date in the browser's zone. */
export const today = () => localParts(Date.now(), Intl.DateTimeFormat().resolvedOptions().timeZone).date

/** New scheduled_for for a card dropped on `date`: on a slot -> that slot's time, else the card's own local time. */
export function dropTime(currentIso: string, tz: string, date: string, slot?: string) {
  return zonedToUtc(date, slot ?? localParts(currentIso, tz).time, tz).toISOString()
}

/** Posts closer than minGap (same account). gaps: later post id -> minutes apart; warn: both ends. */
export function tooClose(posts: { id: number; scheduled_for: string }[], minGap: number) {
  const gaps = new Map<number, number>()
  const warn = new Set<number>()
  const sorted = [...posts].sort((a, b) => Date.parse(a.scheduled_for) - Date.parse(b.scheduled_for))
  for (let i = 1; i < sorted.length; i++) {
    const gap = Math.round((Date.parse(sorted[i].scheduled_for) - Date.parse(sorted[i - 1].scheduled_for)) / MIN)
    if (gap >= minGap) continue
    gaps.set(sorted[i].id, gap)
    warn.add(sorted[i].id).add(sorted[i - 1].id)
  }
  return { gaps, warn }
}

/** "UTC+1", "UTC-4", "UTC" at the given instant. */
export function utcOffset(tz: string, at = Date.now()) {
  const m = offset(at, tz) / MIN
  if (!m) return "UTC"
  const h = Math.floor(Math.abs(m) / 60)
  const r = Math.abs(m) % 60
  return `UTC${m < 0 ? "-" : "+"}${h}${r ? `:${String(r).padStart(2, "0")}` : ""}`
}

const labelFmt = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short", year: "numeric" })
/** "Mon 28 Sep", "28 Sep", "4 Oct 2026" for a calendar date ("Sep", not en-GB's "Sept"). */
export function dayLabel(date: string, { weekday = false, month = true, year = false } = {}) {
  const p = Object.fromEntries(labelFmt.formatToParts(new Date(wallMs(date, "12:00"))).map((x) => [x.type, x.value]))
  return [weekday && p.weekday, p.day, month && p.month, year && p.year].filter(Boolean).join(" ")
}

/** "Sun 09:00" / "today 19:00" for an instant in tz. */
export function shortWhen(iso: string, tz: string, now = Date.now()) {
  const { date, time } = localParts(iso, tz)
  const t = localParts(now, tz).date
  if (date === t) return `today ${time}`
  if (date === addDays(t, 1)) return `tomorrow ${time}`
  return `${dayLabel(date, { weekday: true })} ${time}`
}

export const isHHMM = (s: string) => /^([01]\d|2[0-3]):[0-5]\d$/.test(s)

/** HTTPException(detail={code, message}) body, FastAPI validation errors, or anything else. */
export function apiError(e: unknown): { code?: string; message: string } {
  const d = (e as { detail?: unknown })?.detail
  if (d && typeof d === "object" && !Array.isArray(d)) {
    const { code, message } = d as { code?: string; message?: string }
    return { code, message: message || code || "Request failed" }
  }
  if (typeof d === "string") return { message: d }
  if (Array.isArray(d)) return { message: d.map((x) => `${x.loc?.slice(1).join(".")}: ${x.msg}`).join("; ") }
  return { message: e instanceof Error ? e.message : String(e) }
}

// Chrome's ICU list uses old names the backend's tzdata (no backward links) rejects: store the current ones.
const CANONICAL: Record<string, string> = {
  "Africa/Asmera": "Africa/Asmara",
  "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
  "America/Catamarca": "America/Argentina/Catamarca",
  "America/Cordoba": "America/Argentina/Cordoba",
  "America/Godthab": "America/Nuuk",
  "America/Indianapolis": "America/Indiana/Indianapolis",
  "America/Jujuy": "America/Argentina/Jujuy",
  "America/Louisville": "America/Kentucky/Louisville",
  "America/Mendoza": "America/Argentina/Mendoza",
  "Asia/Calcutta": "Asia/Kolkata",
  "Asia/Katmandu": "Asia/Kathmandu",
  "Asia/Rangoon": "Asia/Yangon",
  "Asia/Saigon": "Asia/Ho_Chi_Minh",
  "Atlantic/Faeroe": "Atlantic/Faroe",
  "Europe/Kiev": "Europe/Kyiv",
  "Pacific/Enderbury": "Pacific/Kanton",
  "Pacific/Ponape": "Pacific/Pohnpei",
  "Pacific/Truk": "Pacific/Chuuk",
}
/** Every IANA zone the browser knows, by its current name, plus UTC (for the timezone select). */
export const ZONES: string[] = ["UTC", ...(Intl.supportedValuesOf?.("timeZone") ?? ["Europe/London"]).map((z) => CANONICAL[z] ?? z).filter((z) => z !== "UTC").sort()]

export const MOVABLE = new Set<PostOut["status"]>(["DRAFT", "SCHEDULED"])
export const FAILED = new Set<PostOut["status"]>(["FAILED", "DEAD_LETTER"])
export const STATUS_LABEL: Record<PostOut["status"], string> = {
  DRAFT: "Draft",
  SCHEDULED: "Scheduled",
  PUBLISHING: "Publishing",
  PUBLISHED: "Published",
  FAILED: "Failed",
  DEAD_LETTER: "Dead letter",
  CANCELLED: "Cancelled",
}
/** Where a post sits on the calendar: its publish time once live, else its slot. */
export const postAt = (p: Pick<PostOut, "published_at" | "scheduled_for">) => p.published_at ?? p.scheduled_for
/** The slot a post holds (its scheduled wall-clock time), even once it went out a few minutes later. */
export const slotTime = (p: Pick<PostOut, "scheduled_for">, tz: string) => localParts(p.scheduled_for, tz).time
