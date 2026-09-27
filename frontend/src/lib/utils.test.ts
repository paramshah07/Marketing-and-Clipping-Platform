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
