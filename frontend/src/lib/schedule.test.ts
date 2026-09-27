// Same rules as backend/app/services/slots.py (zoneinfo, fold=0, gap shifts forward).
import { describe, expect, it } from "vitest"
import { SLOT_PRESETS, ZONES, addDays, apiError, boardRows, boardSpot, dayLabel, dropTime, everyN, firstFree, localParts, planFill, slotInstants, tooClose, tzName, utcOffset, zonedToUtc } from "./schedule"

const iso = (d: Date) => d.toISOString()

describe("zonedToUtc", () => {
  it("summer and winter London", () => {
    expect(iso(zonedToUtc("2026-07-01", "09:00", "Europe/London"))).toBe("2026-07-01T08:00:00.000Z")
    expect(iso(zonedToUtc("2026-12-01", "09:00", "Europe/London"))).toBe("2026-12-01T09:00:00.000Z")
    expect(iso(zonedToUtc("2026-07-01", "19:00", "America/New_York"))).toBe("2026-07-01T23:00:00.000Z")
    expect(iso(zonedToUtc("2026-07-01", "09:00", "Asia/Kolkata"))).toBe("2026-07-01T03:30:00.000Z")
  })
  it("DST gap shifts forward (01:30 on 29 Mar 2026 does not exist in London)", () => {
    const d = zonedToUtc("2026-03-29", "01:30", "Europe/London")
    expect(iso(d)).toBe("2026-03-29T01:30:00.000Z")
    expect(localParts(d, "Europe/London").time).toBe("02:30")
  })
  it("DST overlap takes the earlier instant (fold=0)", () => {
    expect(iso(zonedToUtc("2026-10-25", "01:30", "Europe/London"))).toBe("2026-10-25T00:30:00.000Z")
    expect(iso(zonedToUtc("2026-11-01", "01:30", "America/New_York"))).toBe("2026-11-01T05:30:00.000Z")
  })
  it("zones at UTC+12 and beyond (Auckland, Chatham)", () => {
    expect(iso(zonedToUtc("2026-09-27", "02:30", "Pacific/Auckland"))).toBe("2026-09-26T14:30:00.000Z") // gap -> 03:30 NZDT
    expect(iso(zonedToUtc("2026-04-05", "02:00", "Pacific/Auckland"))).toBe("2026-04-04T13:00:00.000Z") // overlap -> NZDT
    expect(iso(zonedToUtc("2026-09-28", "09:00", "Pacific/Auckland"))).toBe("2026-09-27T20:00:00.000Z")
    expect(iso(zonedToUtc("2026-04-05", "02:45", "Pacific/Chatham"))).toBe("2026-04-04T13:00:00.000Z")
  })
  it("hours next to a transition are unaffected", () => {
    expect(iso(zonedToUtc("2026-03-29", "00:30", "Europe/London"))).toBe("2026-03-29T00:30:00.000Z")
    expect(iso(zonedToUtc("2026-03-29", "09:00", "Europe/London"))).toBe("2026-03-29T08:00:00.000Z")
    expect(iso(zonedToUtc("2026-10-25", "09:00", "Europe/London"))).toBe("2026-10-25T09:00:00.000Z")
  })
  it("round-trips through localParts", () => {
    for (const tz of ["Europe/London", "America/New_York", "Australia/Sydney", "UTC"])
      for (const t of ["00:00", "09:00", "13:00", "23:59"]) expect(localParts(zonedToUtc("2026-09-28", t, tz), tz)).toEqual({ date: "2026-09-28", time: t })
  })
})

describe("dropTime", () => {
  const post = "2026-09-28T08:00:00Z" // 09:00 London
  it("drop on a cell keeps the local time", () => expect(dropTime(post, "Europe/London", "2026-10-26")).toBe("2026-10-26T09:00:00.000Z"))
  it("keeps local time across the zone's DST change", () => expect(localParts(dropTime(post, "Europe/London", "2026-12-01"), "Europe/London").time).toBe("09:00"))
})

describe("days", () => {
  it("addDays crosses months and DST", () => {
    expect(addDays("2026-10-25", 1)).toBe("2026-10-26")
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28")
  })
})

describe("tooClose", () => {
  it("flags the later of two posts under the min gap", () => {
    const posts = [
      { id: 2, scheduled_for: "2026-09-30T18:18:00Z", status: "SCHEDULED" as const },
      { id: 1, scheduled_for: "2026-09-30T18:00:00Z", status: "PUBLISHED" as const },
      { id: 3, scheduled_for: "2026-09-30T20:00:00Z", status: "DRAFT" as const },
    ]
    const { gaps, prev, warn } = tooClose(posts, 45)
    expect([...gaps]).toEqual([[2, 18]])
    expect([...prev]).toEqual([[2, 1]])
    expect([...warn].sort()).toEqual([1, 2])
    expect(tooClose(posts, 0).warn.size).toBe(0)
  })

  it("ignores pairs that are both already out (nothing left to move)", () => {
    const posts = [
      { id: 1, scheduled_for: "2026-09-26T18:00:00Z", status: "PUBLISHED" as const },
      { id: 2, scheduled_for: "2026-09-26T18:10:00Z", status: "PUBLISHED" as const },
      { id: 3, scheduled_for: "2026-09-26T18:19:00Z", status: "FAILED" as const },
    ]
    expect(tooClose(posts, 30).warn.size).toBe(0)
  })
})

// Same cases as backend/tests (slots.first_free): New York, slots 09/13/19, Sat 26 Sep 2026 23:40 EDT.
const NY = "America/New_York"
const T3 = ["09:00", "13:00", "19:00"]
const ms = (s: string) => Date.parse(s)
const at = (date: string, time: string) => zonedToUtc(date, time, NY).getTime()
const NOW = ms("2026-09-27T03:40:00Z")

describe("slot engine (mirrors slots.py)", () => {
  it("slotInstants: sorted, inside [after, until], wall clock across the DST change", () => {
    expect(slotInstants(T3, NY, NOW, ms("2026-09-28T00:00:00Z")).map((t) => new Date(t).toISOString())).toEqual(["2026-09-27T13:00:00.000Z", "2026-09-27T17:00:00.000Z", "2026-09-27T23:00:00.000Z"])
    expect(slotInstants(["09:00"], NY, ms("2026-10-31T00:00:00Z"), ms("2026-11-02T00:00:00Z")).map((t) => localParts(t, NY))).toEqual([
      { date: "2026-10-31", time: "09:00" },
      { date: "2026-11-01", time: "09:00" },
    ])
  })
  it("firstFree: next slot, min gap both sides, exact gap ok, same instant never free", () => {
    expect(firstFree(T3, NY, 10, 30, [], NOW)).toBe(at("2026-09-27", "09:00"))
    expect(firstFree(T3, NY, 10, 30, [at("2026-09-27", "08:40")], NOW)).toBe(at("2026-09-27", "13:00")) // 20 min before
    expect(firstFree(T3, NY, 10, 30, [at("2026-09-27", "09:20")], NOW)).toBe(at("2026-09-27", "13:00")) // 20 min after
    expect(firstFree(T3, NY, 10, 30, [at("2026-09-27", "08:30")], NOW)).toBe(at("2026-09-27", "09:00")) // exactly 30
    expect(firstFree(T3, NY, 10, 0, [at("2026-09-27", "09:00")], NOW)).toBe(at("2026-09-27", "13:00"))
  })
  it("firstFree: the daily cap counts every post that day, off-slot ones too", () => {
    const sunday = ["06:00", "07:00"].map((t) => at("2026-09-27", t))
    expect(firstFree(T3, NY, 2, 30, sunday, NOW)).toBe(at("2026-09-28", "09:00"))
    expect(firstFree(T3, NY, 3, 30, sunday, NOW)).toBe(at("2026-09-27", "09:00"))
  })
  it("firstFree: nothing inside the horizon, or no slots", () => {
    expect(firstFree(T3, NY, 0, 30, [], NOW)).toBeNull()
    expect(firstFree([], NY, 10, 30, [], NOW)).toBeNull()
  })
  it("planFill: input order, each placement blocks the next, unfit renders skipped", () => {
    const a = { posting_slots: { times: T3 }, timezone: NY, daily_cap: 10, min_gap_minutes: 30 }
    const out = planFill(
      [
        { id: 30, duration_s: 22 },
        { id: 6, duration_s: 901 },
        { id: 25, duration_s: 8 },
        { id: 30, duration_s: 22 },
        { id: 20, duration_s: 5 },
      ],
      a,
      [at("2026-09-27", "13:10")],
      NOW
    )
    expect([...out.placed].map(([id, t]) => [id, localParts(t, NY)])).toEqual([
      [30, { date: "2026-09-27", time: "09:00" }],
      [25, { date: "2026-09-27", time: "19:00" }], // 13:00 is 10 min from the 13:10 post
      [20, { date: "2026-09-28", time: "09:00" }],
    ])
    expect(out.unplaced).toEqual([{ render_id: 6, reason: "longer than 15 min" }])
    expect(planFill([{ id: 1, duration_s: 8 }], { ...a, posting_slots: { times: [] } }, [], NOW).unplaced[0].reason).toBe("account has no posting slots")
  })
  it("planFill: skips slots inside the 10 min lead", () => {
    const a = { posting_slots: { times: ["23:45"] }, timezone: NY, daily_cap: 10, min_gap_minutes: 0 }
    expect(localParts(planFill([{ id: 1, duration_s: 8 }], a, [], NOW).placed.get(1)!, NY)).toEqual({ date: "2026-09-27", time: "23:45" })
  })
})

describe("slot board", () => {
  it("boardSpot: slot row by scheduled wall clock, day by when it went out, off-slot posts to the band between slots", () => {
    expect(boardSpot({ scheduled_for: "2026-09-27T13:00:00Z", published_at: "2026-09-27T13:03:00Z" }, T3, NY)).toEqual({ date: "2026-09-27", time: "09:00", slot: "09:00" })
    expect(boardSpot({ scheduled_for: "2026-09-26T19:46:05Z", published_at: "2026-09-26T19:49:10Z" }, T3, NY)).toEqual({ date: "2026-09-26", time: "15:46", band: 2 })
    expect(boardSpot({ scheduled_for: "2026-09-27T12:00:00Z", published_at: null }, T3, NY).band).toBe(0) // 08:00
    expect(boardSpot({ scheduled_for: "2026-09-28T02:00:00Z", published_at: null }, T3, NY).band).toBe(3) // 22:00
  })
  it("boardRows: bands only where needed, in time order", () => {
    expect(boardRows(T3, []).map((r) => r.key)).toEqual(["09:00", "13:00", "19:00"])
    expect(boardRows(T3, [2, 0, 3]).map((r) => r.key)).toEqual(["b0", "09:00", "13:00", "b2", "19:00", "b3"])
    expect(boardRows([], [0]).map((r) => r.key)).toEqual(["b0"])
  })
  it("tzName follows DST", () => {
    expect(tzName(NY, ms("2026-09-27T12:00:00Z"))).toBe("EDT")
    expect(tzName(NY, ms("2026-11-02T12:00:00Z"))).toBe("EST")
  })
})

describe("misc", () => {
  it("utcOffset", () => {
    expect(utcOffset("Europe/London", Date.parse("2026-07-01T00:00:00Z"))).toBe("UTC+1")
    expect(utcOffset("America/New_York", Date.parse("2026-07-01T00:00:00Z"))).toBe("UTC-4")
    expect(utcOffset("Asia/Kolkata")).toBe("UTC+5:30")
    expect(utcOffset("UTC")).toBe("UTC")
  })
  it("dayLabel", () => {
    expect(dayLabel("2026-09-28", { weekday: true })).toBe("Mon 28 Sep")
    expect(dayLabel("2026-09-29", { weekday: true, month: false })).toBe("Tue 29")
    expect(dayLabel("2026-10-04", { year: true })).toBe("4 Oct 2026")
  })
  it("ZONES: current IANA names only, UTC first", () => {
    expect(ZONES[0]).toBe("UTC")
    expect(ZONES).toContain("Asia/Kolkata")
    expect(ZONES).not.toContain("Asia/Calcutta")
    expect(ZONES).not.toContain("Europe/Kiev")
    expect(new Set(ZONES).size).toBe(ZONES.length)
  })
  it("apiError reads {detail: {code, message}}", () => {
    expect(apiError({ detail: { code: "RIGHTS_NONE", message: "no rights" } })).toEqual({ code: "RIGHTS_NONE", message: "no rights" })
    expect(apiError({ detail: "Not found" })).toEqual({ message: "Not found" })
  })
})

describe("slot presets", () => {
  it("everyN spans both ends; the presets are sorted, unique HH:MM", () => {
    expect(everyN(60, "07:00", "23:00")).toEqual(["07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17", "18", "19", "20", "21", "22", "23"].map((h) => `${h}:00`))
    expect(everyN(30, "22:30", "23:30")).toEqual(["22:30", "23:00", "23:30"])
    expect(everyN(120, "08:00", "22:00")).toEqual(["08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00"])
    for (const p of SLOT_PRESETS) expect(p.times).toEqual([...new Set(p.times)].sort())
    expect(SLOT_PRESETS.map((p) => p.times.length)).toEqual([17, 34, 8, 3])
  })
})
