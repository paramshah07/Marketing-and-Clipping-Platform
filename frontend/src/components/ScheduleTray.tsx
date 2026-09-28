import { CalendarPlus, Check, ChevronDown, Clapperboard, PanelRightClose, PanelRightOpen } from "lucide-react"
import { useState, type DragEvent } from "react"
import { Link } from "react-router"

import type { RenderOut } from "@/api"
import { sk } from "@/components/CalendarBits"
import { MAX_REEL_SECONDS, btn, cn, mmss } from "@/lib/utils"

export type Group = { clipId: number; items: RenderOut[] }

type Props = {
  loading: boolean
  error?: string
  groups: Group[] // renders of one source clip, in queue order
  clip: (clipId: number) => { name: string; title?: string }
  brand: (id: number | null) => string
  sel: Set<number>
  onSelect: (ids: number[], on: boolean) => void
  dragging: number | null
  onDrag: (r: RenderOut | null) => void
  collapsed: boolean
  onCollapse: (v: boolean) => void
  free?: number // free slots in view (undefined while the board loads)
  next: string // "Sun 27 · 09:00"
  chosen: number
  canRun: boolean
  running: boolean
  onRun: () => void
  footnote: string // where the selection lands, or what to do next
  unplaced: { render_id: number; reason: string }[]
}

/** Right-hand queue: READY renders with no live post, grouped by clip; select + Auto-schedule, or drag onto a slot. */
export function ScheduleTray(props: Props) {
  const [open, setOpen] = useState<Set<number>>(new Set()) // expanded groups; all start collapsed
  const ids = props.groups.flatMap((g) => g.items.map((r) => r.id))
  const on = ids.filter((id) => props.sel.has(id)).length
  const all: boolean | "mixed" = on && on === ids.length ? true : on ? "mixed" : false
  const run = (
    <button
      className={cn(btn.primary, "h-8 w-full gap-2 tabular-nums transition-colors duration-150 ease-out disabled:bg-raised disabled:text-dim disabled:opacity-100")}
      disabled={!props.canRun || props.running}
      onClick={props.onRun}
      aria-label={props.collapsed ? `Auto-schedule ${props.chosen || ""}` : undefined}
    >
      <CalendarPlus className="size-4" />
      {!props.collapsed && (props.running ? "Scheduling…" : `Auto-schedule${props.chosen ? ` ${props.chosen}` : ""}`)}
    </button>
  )

  if (props.collapsed)
    return (
      <aside data-queue className="flex w-11 shrink-0 flex-col items-center border-l border-line bg-panel">
        <div className="grid h-12 w-full shrink-0 place-items-center border-b border-line">
          <button className={btn.icon} aria-label="Show the queue" title="Show the queue" onClick={() => props.onCollapse(false)}>
            <PanelRightOpen className="size-4" />
          </button>
        </div>
        <div className="flex flex-col items-center gap-0.5 py-3 text-center leading-4 tabular-nums">
          <span>{ids.length}</span>
          <span className="text-xs text-dim">ready</span>
          {props.chosen > 0 && <span className="mt-2 font-medium">{props.chosen}</span>}
        </div>
        <div className="mt-auto w-full p-1.5 [&>button]:h-8 [&>button]:px-0" title={props.footnote}>
          {run}
        </div>
      </aside>
    )

  function row(r: RenderOut, child: boolean) {
    const c = props.clip(r.source_clip_id)
    const long = (r.duration_s ?? 0) > MAX_REEL_SECONDS
    const selected = props.sel.has(r.id)
    return (
      <label
        key={r.id}
        data-render={r.id}
        draggable
        onDragStart={(e: DragEvent) => (e.dataTransfer.setData("text/plain", `render ${r.id}`), (e.dataTransfer.effectAllowed = "copy"), props.onDrag(r))}
        onDragEnd={() => props.onDrag(null)}
        className={cn(
          "relative flex h-14 cursor-grab items-center gap-2.5 pr-3 transition-colors duration-150 ease-out active:cursor-grabbing",
          child ? "pl-8 before:absolute before:inset-y-0 before:left-[18px] before:w-px before:bg-white/[0.08]" : "pl-3",
          selected ? "bg-raised hover:bg-hover" : "hover:bg-raised",
          props.dragging === r.id && "opacity-40"
        )}
      >
        <Box state={selected} label={`${child ? props.brand(r.brand_id) : c.name}, ${child ? c.name : props.brand(r.brand_id)}, ${mmss(r.duration_s)}, #${r.id}`} onChange={() => props.onSelect([r.id], !selected)} />
        <img src={r.thumbnail_url ?? ""} alt="" draggable={false} className="h-[50px] w-7 shrink-0 rounded-sm bg-raised object-cover" />
        <span className="min-w-0 flex-1 leading-4">
          {child ? (
            <span className="block truncate">{props.brand(r.brand_id)}</span>
          ) : (
            <>
              <span className="block truncate" title={c.title}>
                {c.name}
              </span>
              <span className="block truncate text-sm text-muted">{props.brand(r.brand_id)}</span>
            </>
          )}
        </span>
        <span className="shrink-0 text-right leading-4">
          <span className={cn("block text-sm tabular-nums", long ? "text-warn" : "text-muted")} title={long ? `Instagram Reels can be at most ${MAX_REEL_SECONDS / 60} min` : undefined}>
            {mmss(r.duration_s)}
          </span>
          <span className="block font-mono text-xs text-dim">#{r.id}</span>
        </span>
      </label>
    )
  }

  function group(g: Group) {
    if (g.items.length === 1) return row(g.items[0], false)
    const c = props.clip(g.clipId)
    const n = g.items.length
    const picked = g.items.filter((r) => props.sel.has(r.id)).length
    const brands = [...new Set(g.items.map((r) => props.brand(r.brand_id)))]
    const expanded = open.has(g.clipId)
    return (
      <div key={`c${g.clipId}`} data-group={g.clipId}>
        <div className="flex h-14 items-center gap-2.5 pr-3 transition-colors duration-150 ease-out hover:bg-raised">
          <label className="flex min-w-0 flex-1 cursor-pointer items-center gap-2.5 self-stretch pl-3">
            <Box
              state={picked === n ? true : picked ? "mixed" : false}
              label={`Select all ${n} renders of ${c.name}`}
              onChange={() =>
                props.onSelect(
                  g.items.map((r) => r.id),
                  picked !== n
                )
              }
            />
            <span className="relative h-[50px] w-7 shrink-0">
              <img src={g.items[1].thumbnail_url ?? ""} alt="" className="absolute top-0 left-[3px] h-[50px] w-7 rounded-sm bg-raised object-cover opacity-50" />
              <img src={g.items[0].thumbnail_url ?? ""} alt="" className="absolute top-0 left-0 h-[50px] w-7 rounded-sm bg-raised object-cover" />
            </span>
            <span className="min-w-0 flex-1 leading-4">
              <span className="block truncate" title={c.title}>
                {c.name}
              </span>
              <span className="block truncate text-sm text-muted">{brands.length > 1 ? `${brands.length} brands` : brands[0]}</span>
            </span>
          </label>
          <span className="flex shrink-0 flex-col items-end leading-4">
            <span className="text-sm text-muted tabular-nums">{mmss(g.items[0].duration_s)}</span>
            <button
              className="-mr-1 flex h-4 items-center gap-0.5 rounded px-1 text-xs text-dim tabular-nums hover:bg-hover hover:text-fg"
              aria-expanded={expanded}
              aria-label={`${expanded ? "Hide" : "Show"} the ${n} renders of ${c.name}`}
              onClick={() =>
                setOpen((s) => {
                  const x = new Set(s)
                  if (!x.delete(g.clipId)) x.add(g.clipId)
                  return x
                })
              }
            >
              ×{n}
              <ChevronDown className={cn("size-3 transition-transform duration-150", expanded && "rotate-180")} />
            </button>
          </span>
        </div>
        {expanded && g.items.map((r) => row(r, true))}
      </div>
    )
  }

  const empty = !props.loading && !props.error && ids.length === 0
  return (
    <aside data-queue className="flex w-[clamp(248px,19vw,288px)] shrink-0 flex-col border-l border-line bg-panel">
      <div className="flex h-12 shrink-0 items-center gap-2.5 border-b border-line pr-2 pl-3">
        {ids.length > 0 && <Box state={all} label="Select every render" onChange={() => props.onSelect(ids, all !== true)} />}
        <span className="font-medium">Ready to schedule</span>
        {!props.loading && <span className="text-muted tabular-nums">{ids.length}</span>}
        <button className={cn(btn.icon, "ml-auto")} aria-label="Collapse the queue" title="Collapse the queue" onClick={() => props.onCollapse(true)}>
          <PanelRightClose className="size-4" />
        </button>
      </div>
      <div className={cn("min-h-0 flex-1 overflow-auto", !empty && "py-1")}>
        {props.loading ? (
          Array.from({ length: 12 }, (_, i) => (
            <div key={i} className="flex h-14 items-center gap-2.5 px-3">
              <span className={cn(sk, "size-3.5")} />
              <span className={cn(sk, "h-[50px] w-7 rounded-sm")} />
              <span className="flex-1 space-y-1.5">
                <span className={cn(sk, "block h-3")} style={{ width: `${[70, 55, 80, 62][i % 4]}%` }} />
                <span className={cn(sk, "block h-2.5 w-2/5")} />
              </span>
              <span className={cn(sk, "h-2.5 w-8")} />
            </div>
          ))
        ) : props.error ? (
          <p className="p-3 text-sm text-bad">{props.error}</p>
        ) : empty ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
            <Clapperboard className="size-4 text-dim" />
            <div className="leading-5">
              <div className="font-medium">Nothing ready to schedule</div>
              <div className="text-muted">Finished renders land here. Pick a clip and a brand in the Library to make one.</div>
            </div>
            <Link to="/library" className={cn(btn.secondary, "font-normal")}>
              Open Library
            </Link>
          </div>
        ) : (
          props.groups.map(group)
        )}
      </div>
      {!empty && (
        <div className="shrink-0 space-y-2 border-t border-line p-3">
          <div className="flex h-4 items-center justify-between text-sm tabular-nums">
            {props.loading ? (
              <span className={cn(sk, "h-2.5 w-24")} />
            ) : props.chosen ? (
              <span>
                {props.chosen} selected
                <button className="ml-2 text-muted hover:text-fg" onClick={() => props.onSelect(ids, false)}>
                  Clear
                </button>
              </span>
            ) : (
              <span className="truncate text-muted">
                Next free: <span className="text-fg">{props.next || "none"}</span>
              </span>
            )}
            {props.free != null && (
              <span className="shrink-0 text-muted">
                {props.free} free {props.free === 1 ? "slot" : "slots"}
              </span>
            )}
          </div>
          {run}
          <div data-footnote className="line-clamp-2 min-h-4 text-xs text-muted tabular-nums" title={props.footnote}>
            {props.footnote}
          </div>
          {props.unplaced.length > 0 && (
            <div data-result className="max-h-24 space-y-0.5 overflow-auto text-sm">
              {props.unplaced.map((u) => (
                <div key={u.render_id} className="text-warn">
                  <span className="font-mono text-xs">#{u.render_id}</span> {u.reason}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </aside>
  )
}

/** A real checkbox (tri-state via .indeterminate, which screen readers announce as mixed), drawn to match. */
function Box({ state, label, onChange }: { state: boolean | "mixed"; label: string; onChange: () => void }) {
  return (
    <span className="relative grid size-3.5 shrink-0 place-items-center">
      <input
        type="checkbox"
        aria-label={label}
        checked={state === true}
        ref={(el) => {
          if (el) el.indeterminate = state === "mixed"
        }}
        onChange={onChange}
        className="peer absolute inset-0 m-0 cursor-pointer appearance-none rounded-[3px] border border-subtle checked:border-fg checked:bg-fg indeterminate:border-fg indeterminate:bg-fg"
      />
      <Check className="pointer-events-none relative size-2.5 text-bg opacity-0 peer-checked:opacity-100" strokeWidth={3} />
      <span className="pointer-events-none absolute h-[1.5px] w-[7px] rounded-full bg-bg opacity-0 peer-indeterminate:opacity-100" />
    </span>
  )
}
