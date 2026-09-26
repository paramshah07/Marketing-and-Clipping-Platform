// Same rules as backend/app/services/slots.py (zoneinfo, fold=0, gap shifts forward).
import { describe, expect, it } from "vitest"
import { ZONES, addDays, apiError, dayLabel, dropTime, localParts, tooClose, utcOffset, weekOf, zonedToUtc } from "./schedule"

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
  it("drop on a slot takes the slot's time that day", () => expect(dropTime(post, "Europe/London", "2026-09-30", "19:00")).toBe("2026-09-30T18:00:00.000Z"))
  it("drop on a cell keeps the local time", () => expect(dropTime(post, "Europe/London", "2026-10-26")).toBe("2026-10-26T09:00:00.000Z"))
  it("keeps local time across the zone's DST change", () => expect(localParts(dropTime(post, "Europe/London", "2026-12-01"), "Europe/London").time).toBe("09:00"))
})

describe("weeks", () => {
  it("Monday to Sunday", () => {
    expect(weekOf("2026-10-01")).toEqual(["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"])
    expect(weekOf("2026-09-28")[0]).toBe("2026-09-28")
    expect(weekOf("2026-10-04")[0]).toBe("2026-09-28")
  })
  it("addDays crosses months and DST", () => {
    expect(addDays("2026-10-25", 1)).toBe("2026-10-26")
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28")
  })
})

describe("tooClose", () => {
  it("flags the later of two posts under the min gap", () => {
    const posts = [
      { id: 2, scheduled_for: "2026-09-30T18:18:00Z" },
      { id: 1, scheduled_for: "2026-09-30T18:00:00Z" },
      { id: 3, scheduled_for: "2026-09-30T20:00:00Z" },
    ]
    const { gaps, warn } = tooClose(posts, 45)
    expect([...gaps]).toEqual([[2, 18]])
    expect([...warn].sort()).toEqual([1, 2])
    expect(tooClose(posts, 0).warn.size).toBe(0)
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
