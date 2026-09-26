import { useRef, type PointerEvent, type ReactNode } from "react"

import { type Box, type Handle, moveBox, resizeBox } from "@/lib/geometry"
import { cn } from "@/lib/utils"

const CORNERS: Handle[] = ["nw", "ne", "sw", "se"]
const EDGES: Handle[] = ["n", "s", "e", "w"]
const PLACE: Record<Handle, string> = {
  nw: "-top-1 -left-1 cursor-nwse-resize",
  ne: "-top-1 -right-1 cursor-nesw-resize",
  sw: "-bottom-1 -left-1 cursor-nesw-resize",
  se: "-bottom-1 -right-1 cursor-nwse-resize",
  n: "-top-1 left-1/2 -translate-x-1/2 cursor-ns-resize",
  s: "-bottom-1 left-1/2 -translate-x-1/2 cursor-ns-resize",
  e: "top-1/2 -right-1 -translate-y-1/2 cursor-ew-resize",
  w: "top-1/2 -left-1 -translate-y-1/2 cursor-ew-resize",
}

/** A box stored as fractions of its parent (the parent must be `relative`). Drag to move; corner handles (plus
 * edge handles with `edges`) resize with h/w = aspect locked. Plain pointer events: capture on down, maths from
 * the start state, and the drag ends when capture is lost (pointerup, pointercancel or anything else). */
export function FracBox(props: {
  box: Box
  aspect: number
  minW: number
  maxW?: number
  edges?: boolean
  onChange: (b: Box) => void
  className?: string
  children?: ReactNode
  label?: string
}) {
  const { box, aspect, minW, maxW, onChange } = props
  const el = useRef<HTMLDivElement>(null)
  const drag = useRef<{ px: number; py: number; rect: DOMRect; box: Box; handle?: Handle } | null>(null)

  const down = (handle?: Handle) => (e: PointerEvent<HTMLElement>) => {
    if (e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    e.currentTarget.setPointerCapture(e.pointerId)
    const rect = el.current!.parentElement!.getBoundingClientRect()
    drag.current = { px: e.clientX, py: e.clientY, rect, box, handle }
  }
  const move = (e: PointerEvent) => {
    const d = drag.current
    if (!d) return
    const dx = (e.clientX - d.px) / d.rect.width
    const dy = (e.clientY - d.py) / d.rect.height
    onChange(d.handle ? resizeBox(d.box, d.handle, dx, dy, aspect, minW, maxW) : moveBox(d.box, dx, dy))
  }

  return (
    <div
      ref={el}
      data-fracbox={props.label}
      className={cn("absolute cursor-move touch-none outline outline-1 outline-accent", props.className)}
      style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}
      onPointerDown={down()}
      onPointerMove={move}
      onLostPointerCapture={() => (drag.current = null)}
    >
      {props.children}
      {(props.edges ? [...CORNERS, ...EDGES] : CORNERS).map((h) => (
        <span key={h} data-handle={h} onPointerDown={down(h)} className={cn("absolute size-[7px] rounded-[1px] border border-accent bg-fg", PLACE[h])} />
      ))}
    </div>
  )
}
