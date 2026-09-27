import { ArrowDownToLine, ArrowRight, Check, CircleAlert, CircleDashed, Clock, LoaderCircle, Plus, TriangleAlert, X, type LucideIcon } from "lucide-react"
import { Popover } from "radix-ui"
import type { DragEvent, ReactNode } from "react"
import { Link } from "react-router"

import type { PostOut } from "@/api"
import { FAILED, MOVABLE, STATUS_LABEL, dayLabel, slotTime } from "@/lib/schedule"
import { cn } from "@/lib/utils"

// Slot board pieces. Surfaces: bg (board) < panel (queue, drafts) < raised (tiles) < hover. Grid lines and tile
// edges are hairlines; dashed means "not live yet" (draft, fill preview); free slots have no border at rest.

/** Tile sizes: full = 9:16 thumb over the text (≤ 4 slots), compact = 36x64 thumb beside it (5+ slots), thin = one
 * 36 px line (off-slot bands, two posts in one slot). */
export type Size = "full" | "compact" | "thin"

const MARK: Record<PostOut["status"], { icon: LucideIcon; tone: string; quiet?: boolean }> = {
  DRAFT: { icon: CircleDashed, tone: "text-muted" },
  SCHEDULED: { icon: Clock, tone: "text-accent", quiet: true },
  PUBLISHING: { icon: LoaderCircle, tone: "text-accent" },
  PUBLISHED: { icon: Check, tone: "text-ok", quiet: true },
  FAILED: { icon: CircleAlert, tone: "text-bad" },
  DEAD_LETTER: { icon: CircleAlert, tone: "text-bad" },
  CANCELLED: { icon: X, tone: "text-dim" },
}

/** Icon + word. The icon's shape carries the state on its own, so narrow tiles drop the quiet words (Scheduled,
 * Published); the loud ones (Draft, Failed) keep the word and drop the icon instead. */
function StatusMark({ status, word = true }: { status: PostOut["status"]; word?: boolean }) {
  const m = MARK[status]
  return (
    <span className={cn("flex min-w-0 items-center gap-1 text-sm", FAILED.has(status) ? "text-bad" : "text-muted")}>
      <m.icon aria-hidden className={cn("size-3.5 shrink-0", m.tone, status === "PUBLISHING" && "motion-safe:animate-spin", word && !m.quiet && "@max-[112px]:hidden")} strokeWidth={2} />
      {word && <span className={cn("truncate", m.quiet && "@max-[132px]:hidden")}>{STATUS_LABEL[status]}</span>}
    </span>
  )
}

const CLAMP = ["", "line-clamp-1", "line-clamp-2", "line-clamp-3", "line-clamp-4"]
/** File names and error codes wrap after _ - / (a zero-width space), not mid-word, and keep their extension:
 * "real_iphone_hlg_ / 20s.mp4". */
const soft = (s: string) => s.replace(/([_/-])/g, "$1\u200b")

/** Thumb + text in the layout of a size. Full tiles keep one rhythm per row whatever the state: the text block has a
 * fixed height under a thumb that takes the rest, so times line up. Brand and clip share `lines` lines (the rest of
 * the block goes to the extras: Approve, a gap note, error + Recover), wrapping instead of cutting words. */
function Frame(props: { size: Size; thumb: string | null; dim?: boolean; top: ReactNode; line1: ReactNode; line2?: ReactNode; lines?: number; extra?: ReactNode }) {
  const { size } = props
  const img = <img src={props.thumb ?? ""} alt="" draggable={false} className={cn("aspect-[9/16] shrink-0 rounded-[3px] bg-panel object-cover", props.dim && "opacity-45")} />
  if (size === "thin")
    return (
      <span className="pointer-events-none flex h-full min-w-0 items-center gap-2 px-1.5">
        <span className="flex h-8 shrink-0 @max-[88px]:hidden [&>img]:h-full">{img}</span>
        <span className="min-w-0 flex-1 leading-4">
          <span className="flex items-center gap-1.5">{props.top}</span>
          <span className="block truncate text-xs text-muted">{props.line1}</span>
        </span>
        {props.extra}
      </span>
    )
  const text = (
    <span className={cn("pointer-events-none flex min-w-0 flex-col", size === "full" ? "h-[106px] shrink-0 px-2 pt-2 pb-1.5" : "flex-1 pr-2")}>
      <span className="flex items-center justify-between gap-1.5">{props.top}</span>
      {size === "full" ? (
        <span className={cn("mt-1 text-sm wrap-anywhere", CLAMP[props.lines ?? 4])}>
          {props.line1}
          {props.line2 != null && (
            <>
              <br />
              <span className="text-muted">{props.line2}</span>
            </>
          )}
        </span>
      ) : (
        <>
          <span className="mt-1 truncate text-sm">{props.line1}</span>
          {props.line2 != null && <span className="truncate text-sm text-muted">{props.line2}</span>}
        </>
      )}
      {props.extra && <span className={cn("relative z-10 flex flex-wrap items-center gap-x-2 gap-y-1", size === "full" ? "mt-auto" : "mt-1.5")}>{props.extra}</span>}
    </span>
  )
  return size === "full" ? (
    <span className="pointer-events-none flex h-full min-h-0 flex-col">
      <span className="flex min-h-0 flex-1 items-end justify-center px-2 pt-2 [&>img]:h-full [&>img]:max-h-32">{img}</span>
      {text}
    </span>
  ) : (
    <span className="pointer-events-none flex h-full min-w-0 items-start gap-2 pt-1.5 pl-1.5">
      <span className="flex h-16 shrink-0 [&>img]:h-full">{img}</span>
      {text}
    </span>
  )
}

export function Tile(props: {
  p: PostOut
  size: Size
  day: string // "Sun 27 Sep", for the aria-labels
  time: string // the slot it holds
  name: string // clip
  warn?: boolean
  flash?: boolean
  lifted?: boolean
  gap?: ReactNode
  onOpen: () => void
  onApprove?: () => void
  approving?: boolean
  onDragStart: (e: DragEvent) => void
  onDragEnd: () => void
}) {
  const { p, size, time } = props
  const draft = p.status === "DRAFT"
  const failed = FAILED.has(p.status)
  const published = p.status === "PUBLISHED"
  const movable = MOVABLE.has(p.status)
  const brand = p.render.brand_name ?? "No logo"
  const aria = `${props.day} ${time}, ${brand}, ${props.name}, ${STATUS_LABEL[p.status]}${props.gap ? ", too close to another post" : ""}`
  const approve = draft && props.onApprove && (
    <button
      className={cn(
        "pointer-events-auto relative z-10 disabled:text-dim",
        size === "thin" ? "grid size-6 shrink-0 place-items-center rounded text-muted hover:bg-hover hover:text-fg" : "h-6 rounded border border-white/10 bg-bg/40 px-2 text-sm hover:bg-hover"
      )}
      disabled={props.approving}
      onClick={props.onApprove}
      aria-label={`Approve the ${props.day} ${time} draft`}
      title={size === "thin" ? "Approve" : undefined}
    >
      {size === "thin" ? <Check className="size-3.5" /> : props.approving ? "Approving…" : "Approve"}
    </button>
  )
  const gap = props.gap && <span className="pointer-events-auto relative z-10 inline-flex">{props.gap}</span>
  const extra =
    size === "thin" ? (
      approve || undefined
    ) : failed ? (
      <span className="pointer-events-none flex min-w-0 flex-col text-xs leading-4">
        <span className="font-mono wrap-anywhere text-bad" title={p.cause ?? undefined}>
          {soft(p.error_code ?? p.status)}
        </span>
        <span className="flex items-center gap-0.5 text-muted">
          Recover
          <ArrowRight className="size-3" />
        </span>
      </span>
    ) : (
      (gap || approve) && (
        <>
          {gap}
          {approve}
        </>
      )
    )
  // the text block holds 4 lines: names get what the extras leave (Approve 1.5, a gap note 1, error + Recover 3)
  const lines = failed ? 1 : 4 - (approve ? 2 : 0) - (gap ? 1 : 0) - (approve && gap ? 1 : 0)
  const top = (
    <>
      <span className={cn("font-medium tabular-nums", size === "thin" ? "text-sm" : "text-base")}>{time}</span>
      <StatusMark status={p.status} word={size !== "thin"} />
    </>
  )
  return (
    <div
      data-post={p.id}
      data-status={p.status}
      draggable={movable}
      onDragStart={props.onDragStart}
      onDragEnd={props.onDragEnd}
      className={cn(
        "@container relative w-full min-w-0 overflow-hidden rounded-md border transition-[background-color,border-color,opacity,box-shadow] duration-150 ease-out hover:bg-hover",
        size === "thin" ? "h-9 shrink-0" : "h-full",
        draft ? "border-dashed border-line-strong bg-panel" : published ? "border-white/[0.08] bg-transparent" : "border-white/[0.08] bg-raised",
        failed && "border-bad/45",
        props.warn && "border-warn/70",
        props.flash && "ring-2 ring-warn/70",
        props.lifted && "opacity-40",
        movable && "cursor-grab active:cursor-grabbing"
      )}
    >
      {failed ? (
        <Link to={`/recover/${p.id}`} draggable={false} aria-label={`${aria}: open recovery`} className="absolute inset-0 rounded-md focus-visible:-outline-offset-2" />
      ) : (
        <button aria-label={aria} onClick={props.onOpen} className="absolute inset-0 rounded-md focus-visible:-outline-offset-2" />
      )}
      <Frame
        size={size}
        thumb={p.render.thumbnail_url}
        top={top}
        line1={size === "thin" ? gap || brand : <span className={published ? "text-muted" : "text-fg"}>{brand}</span>}
        line2={size === "thin" || (failed && size === "full") ? undefined : soft(props.name)} // failed: the error code needs the room
        lines={lines}
        extra={extra}
      />
    </div>
  )
}

/** Where a selected render will land (Auto-schedule preview; click places just that one), or a dropped render while
 * it saves. Fades in, never slides. */
export function Ghost(props: {
  size: Size
  thumb: string | null
  time: string
  brand: string
  name: string
  tag: string // "Fill 1", "Saving…"
  of?: number // the "/3" of "Fill 1/3", dropped on narrow tiles
  outcome: string
  label?: string
  tabIndex?: number
  onClick?: () => void
}) {
  const Tag = props.onClick ? "button" : "div"
  return (
    <Tag
      data-preview={props.time}
      aria-label={props.label}
      tabIndex={props.tabIndex}
      onClick={props.onClick}
      className={cn(
        "@container block w-full overflow-hidden rounded-md border border-dashed border-accent/50 bg-accent/[0.035] text-left transition-[opacity,background-color] duration-200 ease-out starting:opacity-0 motion-reduce:transition-none",
        props.onClick && "hover:bg-accent/[0.08] focus-visible:-outline-offset-2",
        props.size === "thin" ? "h-9" : "h-full"
      )}
      title={props.label ?? `${props.tag}: lands ${props.outcome}`}
    >
      <Frame
        size={props.size}
        thumb={props.thumb}
        dim
        top={
          <>
            <span className={cn("font-medium text-muted tabular-nums", props.size === "thin" ? "text-sm" : "text-base")}>{props.time}</span>
            <span className="shrink-0 text-sm whitespace-nowrap text-accent tabular-nums">
              {props.tag}
              {props.of != null && <span className="@max-[104px]:hidden">/{props.of}</span>}
            </span>
          </>
        }
        line1={props.brand}
        line2={soft(props.name)}
        lines={3}
        extra={props.size === "thin" ? undefined : <span className="text-xs text-dim">lands {props.outcome}</span>}
      />
    </Tag>
  )
}

/** An open slot: a quiet surface with a faint "+"; a neutral hairline while something is dragged; accent only under
 * the pointer. Drag handlers live on the board cell around it, which never unmounts mid-drag. */
export function FreeSlot(props: { title: string; armed: boolean; over: boolean; drop: string; outcome: string; hint?: string; disabled?: boolean; tabIndex: number; onClick: () => void }) {
  return (
    <button
      data-slot
      aria-label={props.title}
      title={props.title}
      disabled={props.disabled}
      tabIndex={props.tabIndex}
      onClick={props.onClick}
      className={cn(
        "group grid h-full min-h-9 w-full place-items-center rounded-md border transition-colors duration-150 ease-out focus-visible:-outline-offset-2",
        props.over
          ? "border-accent bg-accent/10 text-accent"
          : props.armed
            ? "border-white/15 text-white/35"
            : "border-transparent bg-white/[0.012] text-white/35 enabled:hover:border-white/[0.08] enabled:hover:bg-white/[0.03] enabled:hover:text-muted"
      )}
    >
      {props.over ? (
        <span className="pointer-events-none flex flex-col items-center gap-1 px-1 text-center leading-4">
          <ArrowDownToLine className="size-4" />
          <span className="text-sm font-medium tabular-nums">{props.drop}</span>
          <span className="text-xs">{props.outcome}</span>
        </span>
      ) : (
        !props.disabled && (
          <span className="pointer-events-none flex flex-col items-center gap-1">
            <Plus className="size-4" />
            {props.hint && <span className="hidden text-xs tabular-nums group-hover:block group-focus-visible:block">{props.hint}</span>}
          </span>
        )
      )}
    </button>
  )
}

/** A slot inside the min gap of a post (off-slot, or on another account's schedule): not free, says why. */
export function TightSlot({ minutes, time, min }: { minutes: number; time: string; min: number }) {
  return (
    <div className="grid h-full place-items-center px-1 text-center text-xs text-dim tabular-nums" title={`Within ${min} min of the ${time} post (the minimum gap)`}>
      <span>
        <TriangleAlert className="mr-1 inline size-3 align-[-2px] text-warn" />
        {minutes} min from {time}
      </span>
    </div>
  )
}

/** "6 published · 15:46–17:13": several published off-slot posts folded into one line; click lists them. */
export function PublishedChip({ posts, tz, onOpen }: { posts: PostOut[]; tz: string; onOpen: (id: number) => void }) {
  const first = slotTime(posts[0], tz)
  const last = slotTime(posts[posts.length - 1], tz)
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          data-published-chip={posts.length}
          aria-label={`${posts.length} published between ${first} and ${last}: list them`}
          className="flex h-9 w-full min-w-0 shrink-0 items-center gap-2 overflow-hidden rounded-md border border-white/[0.08] px-1.5 text-left transition-colors duration-150 ease-out hover:bg-hover"
        >
          <span className="flex h-7 shrink-0 @max-[112px]:hidden">
            {posts.slice(-2).map((p, i) => (
              <img key={p.id} src={p.render.thumbnail_url ?? ""} alt="" className={cn("h-7 w-4 rounded-[2px] bg-panel object-cover ring-1 ring-bg", i > 0 && "-ml-2.5")} />
            ))}
          </span>
          <span className="min-w-0 leading-4">
            <span className="block truncate text-sm whitespace-nowrap tabular-nums">{posts.length} published</span>
            <span className="block truncate text-xs text-dim tabular-nums">
              {first}–{last}
            </span>
          </span>
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="bottom" align="start" sideOffset={4} collisionPadding={8} className="z-50 w-64 rounded-md border border-line-strong bg-panel p-1 shadow-2xl">
          {posts.map((p) => (
            <Popover.Close asChild key={p.id}>
              <button className="flex h-10 w-full items-center gap-2 rounded px-1.5 text-left hover:bg-hover" onClick={() => onOpen(p.id)}>
                <img src={p.render.thumbnail_url ?? ""} alt="" className="h-8 w-[18px] shrink-0 rounded-[2px] bg-raised object-cover" />
                <span className="font-medium tabular-nums">{slotTime(p, tz)}</span>
                <span className="min-w-0 flex-1 truncate text-muted">{p.render.brand_name ?? "No logo"}</span>
                <Check className="size-3.5 shrink-0 text-ok" />
              </button>
            </Popover.Close>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

/** The amber "20 min gap" note; opens the reason and a one-click move to the next slot that respects the gap. */
export function GapFix(props: { minutes: number; min: number; after: string; mover: string; to: string | null; onMove: () => void; onOpen: () => void }) {
  const same = props.minutes === 0
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          className="pointer-events-auto flex items-center gap-1 rounded text-xs whitespace-nowrap text-warn tabular-nums hover:underline"
          aria-label={`${same ? "Same time as" : `${props.minutes} min after`} the ${props.after} post: fix`}
        >
          <TriangleAlert className="size-3 shrink-0" />
          {same ? (
            "Same time"
          ) : (
            <>
              {props.minutes} min<span className="@max-[120px]:hidden"> gap</span>
            </>
          )}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="bottom" align="start" sideOffset={4} collisionPadding={8} className="z-50 w-72 space-y-2.5 rounded-md border border-line-strong bg-panel p-3 text-fg shadow-2xl">
          <p className="tabular-nums">
            {same ? "Same time as" : `${props.minutes} min after`} the {props.after} post. Minimum gap is {props.min} min.
          </p>
          <Popover.Close asChild>
            {props.to ? (
              <button className="flex min-h-7 items-center gap-1.5 rounded border border-warn/40 px-2.5 py-1 text-left text-warn tabular-nums hover:bg-warn/10" onClick={props.onMove}>
                Move {props.mover} to {props.to}
              </button>
            ) : (
              <button className="flex min-h-7 items-center rounded border border-white/10 px-2.5 py-1 text-left hover:bg-hover" onClick={props.onOpen}>
                No free slot within 30 days: open the post
              </button>
            )}
          </Popover.Close>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

export const sk = "rounded bg-white/[0.05] motion-safe:animate-pulse"

/** Loading board in the final geometry: the real day heads and slot times (known before the posts load), a
 * callout-height row, and neutral cells; nothing implies a tile is there. */
export function BoardSkeleton({ days, today, times }: { days: string[]; today: string; times: string[] }) {
  const rows = times.length ? times : ["", "", ""]
  return (
    <div
      className="grid min-h-0 flex-1"
      style={{ gridTemplateColumns: "56px repeat(7, minmax(0, 1fr))", gridTemplateRows: `40px 45px repeat(${rows.length}, minmax(0, 1fr))` }}
      aria-busy
      aria-label="Loading the calendar"
    >
      <div />
      {days.map((d) => (
        <div key={d} className={cn("flex items-center border-l border-grid px-2 tabular-nums", d === today ? "bg-today font-medium" : "text-muted")}>
          {dayLabel(d, { weekday: true, month: false })}
        </div>
      ))}
      <div className="border-t border-grid" />
      <div className="col-span-7 flex items-center border-t border-l border-grid px-3">
        <span className={cn(sk, "h-3 w-80")} />
      </div>
      {rows.map((t, i) => (
        <div key={i} className="contents">
          <div className="border-t border-grid pt-2.5 pr-2 text-right text-sm text-dim tabular-nums">{t}</div>
          {days.map((d) => (
            <div key={d} className={cn("border-t border-l border-grid p-1.5", d === today && "bg-today")}>
              <div className="h-full rounded-md bg-white/[0.02] motion-safe:animate-pulse" />
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}
