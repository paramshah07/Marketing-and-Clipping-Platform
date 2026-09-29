// Time-zone math for the calendar and the schedule popover. Mirrors backend/app/services/slots.py:
// slot times are "HH:MM" wall clock in the account's IANA zone; a DST gap shifts forward and an
// overlap takes the earlier instant (Python fold=0). Days are "YYYY-MM-DD" calendar dates.

import type { AccountOut, PostOut } from "@/api"
import { MAX_REEL_SECONDS, MIN_REEL_SECONDS, errorText } from "@/lib/utils"

const MIN = 60_000
const DAY = 86_400_000
export const MIN_LEAD = -1 * MIN // backend MIN_LEAD: now is allowed, a time already passed is not
export const AUTO_LEAD = 10 * MIN // backend slots.AUTO_LEAD: automatic placement skips nearer slots
/** "Post now": this instant, to the second. The dispatcher takes it within a minute. */
export const nowIso = () => new Date(Math.floor(Date.now() / 1000) * 1000).toISOString()
export const postNowConfirm = (username: string) => `Post to @${username} now?\n\nIt goes live on Instagram within about a minute and can't be deleted from here.`
const HORIZON = 30 * DAY // backend slots.HORIZON

/** "HH:MM" every `step` minutes from `from` to `to`, both included. */
export function everyN(step: number, from: string, to: string): string[] {
  const m = (t: string) => Number(t.slice(0, 2)) * 60 + Number(t.slice(3))
  const hhmm = (n: number) => `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`
  const out: string[] = []
  for (let n = m(from); n <= m(to); n += step) out.push(hhmm(n))
  return out
}

/** One-click posting-slot sets (Accounts page; the bot's account card has the same, backend app/bot/fmt.py). */
export const SLOT_PRESETS = [
  { label: "Every hour, 07:00–23:00", times: everyN(60, "07:00", "23:00") }, // new accounts' default
  { label: "Every 30 min, 07:00–23:30", times: everyN(30, "07:00", "23:30") },
  { label: "Every 2 hours, 08:00–22:00", times: everyN(120, "08:00", "22:00") },
  { label: "3 a day: 09:00, 13:00, 19:00", times: ["09:00", "13:00", "19:00"] },
]

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

/** Today's date in a zone (the browser's by default). */
export const BROWSER_TZ = Intl.DateTimeFormat().resolvedOptions().timeZone
export const today = (tz = BROWSER_TZ) => localParts(Date.now(), tz).date

/** New scheduled_for for a post dropped on a day head: the same local time on `date`. */
export function dropTime(currentIso: string, tz: string, date: string) {
  return zonedToUtc(date, localParts(currentIso, tz).time, tz).toISOString()
}

/** Posts closer than minGap (same account). gaps: later post id -> minutes apart; prev: later -> earlier
 * post id; warn: both ends. Only pairs the operator can still fix: at least one side is DRAFT or SCHEDULED. */
export function tooClose(posts: { id: number; scheduled_for: string; status: PostOut["status"] }[], minGap: number) {
  const gaps = new Map<number, number>()
  const prev = new Map<number, number>()
  const warn = new Set<number>()
  const sorted = [...posts].sort((a, b) => Date.parse(a.scheduled_for) - Date.parse(b.scheduled_for))
  for (let i = 1; i < sorted.length; i++) {
    const gap = Math.round((Date.parse(sorted[i].scheduled_for) - Date.parse(sorted[i - 1].scheduled_for)) / MIN)
    if (gap >= minGap || !(MOVABLE.has(sorted[i].status) || MOVABLE.has(sorted[i - 1].status))) continue
    gaps.set(sorted[i].id, gap)
    prev.set(sorted[i].id, sorted[i - 1].id)
    warn.add(sorted[i].id).add(sorted[i - 1].id)
  }
  return { gaps, prev, warn }
}

/** Every "HH:MM" on every local day, as sorted UTC ms in [after, until] (slots.py slot_instants). */
export function slotInstants(times: string[], tz: string, after: number, until: number) {
  const out = new Set<number>()
  const last = addDays(localParts(until, tz).date, 1)
  for (let d = addDays(localParts(after, tz).date, -1); d <= last; d = addDays(d, 1))
    for (const t of times) {
      const ms = zonedToUtc(d, t, tz).getTime()
      if (ms >= after && ms <= until) out.add(ms)
    }
  return [...out].sort((a, b) => a - b)
}

type Slots = { ms: number; day: string }[]
const slotsOf = (times: string[], tz: string, after: number, until: number): Slots => slotInstants(times, tz, after, until).map((ms) => ({ ms, day: localParts(ms, tz).date }))
const countDays = (taken: number[], tz: string) => {
  const perDay = new Map<string, number>()
  for (const t of taken) {
    const d = localParts(t, tz).date
    perDay.set(d, (perDay.get(d) ?? 0) + 1)
  }
  return perDay
}
/** The first slot whose local day has < cap posts and that is >= gap from every taken instant (exactly gap apart is
 * fine; the same instant is never free). */
const pick = (slots: Slots, cap: number, gapMin: number, taken: number[], perDay: Map<string, number>) =>
  slots.find((s) => (perDay.get(s.day) ?? 0) < cap && !taken.some((t) => t === s.ms || Math.abs(t - s.ms) < gapMin * MIN))

/** slots.py first_free: the earliest free slot in [after, until]. */
export function firstFree(times: string[], tz: string, cap: number, gapMin: number, taken: number[], after: number, until = after + HORIZON) {
  return pick(slotsOf(times, tz, after, until), cap, gapMin, taken, countDays(taken, tz))?.ms ?? null
}

type Slotted = Pick<AccountOut, "posting_slots" | "timezone" | "daily_cap" | "min_gap_minutes">

/** What POST /posts/auto-schedule will do (scheduling.py auto_schedule): each render, in the order sent, takes
 * the account's next free slot >= now + 10 min. taken: when the account's non-cancelled posts go (or went) out. */
export function planFill(renders: { id: number; duration_s: number | null }[], a: Slotted, taken: number[], now: number) {
  const times = a.posting_slots.times ?? []
  const slots = slotsOf(times, a.timezone, now + AUTO_LEAD, now + AUTO_LEAD + HORIZON) // once, not per render
  const busy = [...taken]
  const perDay = countDays(taken, a.timezone)
  const placed = new Map<number, number>() // render id -> slot (ms), in placement order
  const unplaced: { render_id: number; reason: string }[] = []
  for (const r of renders) {
    if (placed.has(r.id) || unplaced.some((u) => u.render_id === r.id)) continue // a repeated id is placed once
    const d = r.duration_s
    const unfit = d != null && d > MAX_REEL_SECONDS ? `longer than ${MAX_REEL_SECONDS / 60} min` : d != null && d < MIN_REEL_SECONDS ? `shorter than ${MIN_REEL_SECONDS} s` : ""
    const at = unfit ? undefined : pick(slots, a.daily_cap, a.min_gap_minutes, busy, perDay)
    if (!at) unplaced.push({ render_id: r.id, reason: unfit || (times.length ? "no free slot within 30 days" : "account has no posting slots") })
    else {
      placed.set(r.id, at.ms)
      busy.push(at.ms)
      perDay.set(at.day, (perDay.get(at.day) ?? 0) + 1)
    }
  }
  return { placed, unplaced }
}

export type BoardRow = { key: string; slot?: string; band?: number }

/** Slot-board rows: one per posting slot, plus an "Other times" band wherever off-slot posts fall (band i sits
 * just before slot i; band times.length after the last), so the board keeps time order. */
export function boardRows(times: string[], bands: Iterable<number>): BoardRow[] {
  const b = new Set(bands)
  const rows: BoardRow[] = []
  times.forEach((t, i) => {
    if (b.has(i)) rows.push({ key: `b${i}`, band: i })
    rows.push({ key: t, slot: t })
  })
  if (b.has(times.length)) rows.push({ key: `b${times.length}`, band: times.length })
  return rows
}

/** Where a post sits on the slot board: its local day (when it goes or went out), and a slot row when its
 * scheduled wall-clock time is one of the account's slots, else the band between the slots around it. */
export function boardSpot(p: Pick<PostOut, "published_at" | "scheduled_for">, times: string[], tz: string) {
  const date = localParts(postAt(p), tz).date
  const time = slotTime(p, tz)
  return times.includes(time) ? { date, time, slot: time } : { date, time, band: times.filter((s) => s <= time).length }
}

/** "EDT", "GMT+1": the zone's short name at an instant (it changes across DST). */
export const tzName = (tz: string, at: number) => new Intl.DateTimeFormat("en-US", { timeZone: tz, timeZoneName: "short" }).formatToParts(at).find((x) => x.type === "timeZoneName")?.value ?? tz

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
  return { message: errorText(e) }
}

// The few codes whose api message speaks to whoever runs the server, not to you
const SAY: Record<string, string> = {
  CROSS_SITE: "The server refused a request from this address: it expects another one (APP_BASE_URL).",
  SECRETS_KEY_MISSING: "This server can't store keys or bot tokens yet (SECRETS_KEY is not set): ask whoever runs it.",
  USERNAME_TAKEN: "That username is taken: pick another.", // the api's starts with the name, which must keep its case
}

/** An api error as one sentence. The api's messages are written for people ("the current password is wrong"). */
export function say(e: unknown) {
  const { code, message } = apiError(e)
  const s = (code && SAY[code]) || message
  return s.charAt(0).toUpperCase() + s.slice(1)
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
