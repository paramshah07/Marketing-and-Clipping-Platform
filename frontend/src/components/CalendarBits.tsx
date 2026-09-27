import { TriangleAlert } from "lucide-react"
import type { DragEvent, ReactNode } from "react"
import { Link } from "react-router"

import type { PostOut } from "@/api"
import { FAILED, MOVABLE, STATUS_LABEL, shortWhen, slotTime } from "@/lib/schedule"
import { cn, shortUrl } from "@/lib/utils"

const LOOK: Record<PostOut["status"], { dot: string; label?: boolean }> = {
  DRAFT: { dot: "border border-muted", label: true },
  SCHEDULED: { dot: "bg-accent" },
  PUBLISHING: { dot: "bg-accent", label: true },
  PUBLISHED: { dot: "bg-ok" },
  FAILED: { dot: "bg-bad", label: true },
  DEAD_LETTER: { dot: "bg-bad", label: true },
  CANCELLED: { dot: "bg-subtle", label: true },
}

export function Legend() {
  const items = [
    ["bg-accent", "Scheduled"],
    ["border border-muted", "Draft"],
    ["bg-ok", "Published"],
    ["bg-bad", "Failed"],
  ]
  return (
    <div className="grid grid-cols-2 content-center gap-x-2 px-3 text-xs text-subtle">
      {items.map(([dot, text]) => (
        <span key={text} className="flex items-center gap-1.5">
          <span className={cn("size-1.5 shrink-0 rounded-full", dot)} />
          {text}
        </span>
      ))}
    </div>
  )
}

export function GapNote({ minutes }: { minutes: number }) {
  return (
    <div className="-my-0.5 flex h-3.5 items-center justify-center gap-1 text-xs leading-none tabular-nums text-warn">
      <TriangleAlert className="size-3" />
      {minutes} min apart
    </div>
  )
}

export function PostCard(props: { p: PostOut; tz: string; warn: boolean; ghost?: boolean; onOpen: () => void; onDragStart: (e: DragEvent) => void; onDragEnd: () => void }) {
  const { p } = props
  const look = LOOK[p.status]
  const draft = p.status === "DRAFT"
  const failed = FAILED.has(p.status)
  const brand = p.render.brand_name ?? "No logo"
  const clip = shortUrl(p.render.clip_name ?? `Clip ${p.render.clip_id}`)
  const body: ReactNode = (
    <>
      <img src={p.render.thumbnail_url ?? ""} alt="" className="h-16 w-9 shrink-0 bg-panel object-cover" />
      <div className="flex min-w-0 flex-1 flex-col py-1 pr-1 pl-1.5 text-left">
        <div className="flex items-center justify-between gap-1 leading-4">
          <span className="font-medium tabular-nums" title={p.published_at ? `Published ${shortWhen(p.published_at, props.tz)}` : undefined}>
            {slotTime(p, props.tz)}
          </span>
          <span className={cn("size-1.5 shrink-0 rounded-full", look.dot)} />
        </div>
        <div className={cn(look.label ? "truncate" : "line-clamp-2", "text-sm leading-[14px] text-muted")} title={brand}>
          {brand}
        </div>
        <div className="truncate text-sm leading-[14px] text-muted" title={p.render.clip_name ?? undefined}>
          {clip}
        </div>
        {look.label && <div className={cn("mt-auto text-xs leading-[14px]", failed ? "text-bad" : "text-muted")}>{STATUS_LABEL[p.status]}</div>}
      </div>
    </>
  )
  const cls = cn(
    "relative flex h-[66px] w-full shrink-0 overflow-hidden rounded border transition-opacity",
    props.warn ? "border-warn" : failed ? "border-bad/50" : draft ? "border-dashed border-line-strong" : "border-line",
    draft ? "bg-panel" : "bg-raised",
    "hover:bg-hover",
    props.ghost && "opacity-25"
  )
  if (failed)
    return (
      <Link to={`/recover/${p.id}`} data-post={p.id} data-status={p.status} className={cls} title="Open recovery">
        {body}
      </Link>
    )
  const movable = MOVABLE.has(p.status)
  return (
    <button
      data-post={p.id}
      data-status={p.status}
      className={cn(cls, movable && "cursor-grab active:cursor-grabbing")}
      draggable={movable}
      onDragStart={props.onDragStart}
      onDragEnd={props.onDragEnd}
      onClick={props.onOpen}
    >
      {body}
    </button>
  )
}

export function SlotBox(props: { time: string; over: boolean; handlers: object }) {
  return (
    <div
      data-slot={props.time}
      {...props.handlers}
      className={cn("h-[66px] shrink-0 rounded border border-dashed pt-1 pl-[43px] text-sm tabular-nums", props.over ? "border-accent bg-accent/10 text-accent" : "border-line text-subtle")}
    >
      {props.time}
    </div>
  )
}

export function DayMeter({ n, cap }: { n: number; cap: number }) {
  return (
    <div className="mt-auto flex items-center gap-1.5 pt-1" title={`${n} of ${cap} posts for the day`}>
      <div className="h-[3px] flex-1 overflow-hidden rounded-full bg-line">
        <div className={cn("h-full", n >= cap ? "bg-warn" : "bg-muted")} style={{ width: `${Math.min(100, (n / cap) * 100)}%` }} />
      </div>
      <span className={cn("text-xs tabular-nums", n >= cap ? "text-warn" : "text-subtle")}>
        {n}/{cap}
      </span>
    </div>
  )
}
