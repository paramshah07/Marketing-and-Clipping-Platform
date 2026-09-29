import { describe, expect, it } from "vitest"

import { fillCaption } from "./utils"

describe("fillCaption", () => {
  it("fills link and creator", () => {
    expect(fillCaption("Fuel up → {link} · clip by {creator}\n#ad", "flux.gg", "@maya")).toBe("Fuel up → flux.gg · clip by @maya\n#ad")
  })
  it("drops the creator part instead of leaving it dangling", () => {
    expect(fillCaption("Fuel up → {link} · clip by {creator}\n#ad", "flux.gg", null)).toBe("Fuel up → flux.gg\n#ad")
    expect(fillCaption("Night fuel\nClip: {creator}\n#ad", null, "")).toBe("Night fuel\n#ad")
    expect(fillCaption(null, null, null)).toBe("")
  })
})

import { shortUrl } from "./utils"

describe("shortUrl", () => {
  it("drops scheme, www and tracking queries but keeps YouTube's video id", () => {
    expect(shortUrl("https://www.tiktok.com/@maya.eats/video/7421983301?is_from_webapp=1&sender_device=pc")).toBe("tiktok.com/@maya.eats/video/7421983301")
    expect(shortUrl("https://www.youtube.com/watch?v=jNQXAC9IVRw&t=4s")).toBe("youtube.com/watch?v=jNQXAC9IVRw")
    expect(shortUrl("https://www.instagram.com/reel/C9xK2mPqL4z/?igsh=abc")).toBe("instagram.com/reel/C9xK2mPqL4z")
    expect(shortUrl("IMG_4821.MOV")).toBe("IMG_4821.MOV")
  })
})

import { errorText, nextPath, pairState } from "./utils"

describe("errorText", () => {
  it("reads {code, message} details too", () => {
    expect(errorText({ detail: { code: "QUOTA_EXCEEDED", message: "your storage is full" } })).toBe("your storage is full")
    expect(errorText({ detail: "Clip not found" })).toBe("Clip not found")
  })
})

describe("nextPath", () => {
  it("keeps paths in the app and nothing that leaves it", () => {
    expect(nextPath("/calendar?week=2026-09-28")).toBe("/calendar?week=2026-09-28")
    expect(nextPath("/recover/12")).toBe("/recover/12")
    for (const bad of [null, "", "//evil.test", "/\\evil.test", "https://evil.test", "calendar", "/login?next=/x", "/signup"]) expect(nextPath(bad)).toBe("/library")
  })
})

describe("pairState", () => {
  const at = Date.parse("2026-09-28T12:00:00Z")
  const exp = "2026-09-28T12:15:00Z"
  const fresh = { bot: { health: "waiting" as const }, expires_at: exp } // a new bot: no chat when the code went out
  const repair = { bot: { health: "running" as const }, expires_at: exp } // Re-pair: it has a chat already
  it("waits while the code is out", () => {
    expect(pairState({ pairing: true, health: "waiting" }, fresh, at)).toBe("waiting")
    expect(pairState({ pairing: true, health: "running" }, repair, at)).toBe("waiting")
  })
  it("a new bot is paired once it has a chat, whenever that was", () => {
    expect(pairState({ pairing: false, health: "running" }, fresh, at + 20 * 60_000)).toBe("paired")
    expect(pairState({ pairing: false, health: "waiting" }, fresh, at + 20 * 60_000)).toBe("expired")
  })
  it("a Re-pair is done when the code went before it expired", () => {
    expect(pairState({ pairing: false, health: "running" }, repair, at)).toBe("paired")
    expect(pairState({ pairing: false, health: "running" }, repair, at + 15 * 60_000)).toBe("expired")
  })
})
