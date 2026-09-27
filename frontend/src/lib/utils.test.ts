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
