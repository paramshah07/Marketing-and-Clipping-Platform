import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Check } from "lucide-react"
import { useState } from "react"

import type { AccountOut, AutoScheduleOut } from "@/api"
import {
  autoScheduleMutation,
  listAccountsQueryKey,
  listBrandsOptions,
  listClipsOptions,
  listPostsQueryKey,
  listRendersOptions,
  listRendersQueryKey,
  nextSlotOptions,
  nextSlotQueryKey,
} from "@/api/@tanstack/react-query.gen"
import { apiError, shortWhen } from "@/lib/schedule"
import { MAX_REEL_SECONDS, ago, btn, clipName, cn, field, mmss } from "@/lib/utils"

/** Left tray: READY renders with no live post, checkboxes, target account, Auto-schedule. accounts: undefined while loading. */
export function ScheduleTray({ accounts }: { accounts?: AccountOut[] }) {
  const qc = useQueryClient()
  const renders = useQuery(listRendersOptions({ query: { status: "READY", unscheduled: true } }))
  const clips = useQuery(listClipsOptions())
  const brands = useQuery(listBrandsOptions())
  const archived = useQuery(listBrandsOptions({ query: { archived: true } }))
  const targets = (accounts ?? []).filter((a) => a.connection_status === "connected")
  const [picked, setPicked] = useState<number | null>(null)
  const account = targets.find((a) => a.id === picked) ?? targets[0]
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [result, setResult] = useState<AutoScheduleOut | null>(null)
  const [error, setError] = useState("")
  const next = useQuery({ ...nextSlotOptions({ path: { account_id: account?.id ?? 0 } }), enabled: !!account })
  const auto = useMutation(autoScheduleMutation())

  const list = renders.data ?? []
  const chosen = list.filter((r) => sel.has(r.id)).map((r) => r.id) // keeps the tray (input) order
  const clipOf = (id: number) => clips.data?.find((c) => c.id === id)
  const brandOf = (id: number | null) => [...(brands.data ?? []), ...(archived.data ?? [])].find((b) => b.id === id)?.name ?? (id == null ? "No logo" : `Brand ${id}`)
  const toggle = (id: number) =>
    setSel((s) => {
      const n = new Set(s)
      if (!n.delete(id)) n.add(id)
      return n
    })

  async function run(override = false) {
    if (!account || !chosen.length) return
    setError("")
    setResult(null)
    try {
      const out = await auto.mutateAsync({ body: { render_ids: chosen, account_id: account.id, rights_override: override } })
      setResult(out)
      setSel(new Set(out.unplaced.map((u) => u.render_id)))
    } catch (e) {
      const { code, message } = apiError(e)
      if (code === "RIGHTS_NONE" && !override) {
        const ids = (e as { detail?: { render_ids?: number[] } }).detail?.render_ids ?? []
        const names = ids.map((id) => {
          const clip = clipOf(list.find((r) => r.id === id)?.source_clip_id ?? -1)
          return `  ${clip ? clipName(clip) : `render ${id}`}`
        })
        if (confirm(`No rights recorded for:\n${names.join("\n")}\n\nSchedule them anyway?`)) return run(true)
        return
      }
      setError(message)
    } finally {
      qc.invalidateQueries({ queryKey: listRendersQueryKey() })
      qc.invalidateQueries({ queryKey: listPostsQueryKey() })
      qc.invalidateQueries({ queryKey: listAccountsQueryKey() })
      qc.invalidateQueries({ queryKey: nextSlotQueryKey({ path: { account_id: account.id } }) })
    }
  }

  return (
    <aside className="flex w-[240px] shrink-0 flex-col border-r border-line bg-panel">
      <div className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
        <span className="font-medium">
          Ready to schedule {renders.data && <span className="font-normal tabular-nums text-subtle">{list.length}</span>}
        </span>
        <button className="text-sm text-muted hover:text-fg" onClick={() => setSel(chosen.length === list.length ? new Set() : new Set(list.map((r) => r.id)))}>
          {list.length > 0 && chosen.length === list.length ? "Select none" : "Select all"}
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-1.5 overflow-auto p-2">
        {renders.isError && <p className="p-2 text-sm text-bad">{apiError(renders.error).message}</p>}
        {renders.data?.length === 0 && <p className="p-2 text-sm text-subtle">Nothing waiting. Renders show up here once they're ready and not yet scheduled.</p>}
        {list.map((r) => {
          const on = sel.has(r.id)
          const clip = clipOf(r.source_clip_id)
          const long = (r.duration_s ?? 0) > MAX_REEL_SECONDS
          return (
            <label key={r.id} data-render={r.id} className={cn("relative flex cursor-pointer gap-2 rounded border p-1.5 hover:bg-hover has-[:focus-visible]:border-muted", on ? "border-line-strong bg-raised" : "border-line")}>
              <input type="checkbox" className="sr-only" checked={on} onChange={() => toggle(r.id)} />
              <span className={cn("mt-0.5 grid size-3.5 shrink-0 place-items-center rounded-sm", on ? "bg-fg" : "border border-line-strong")}>{on && <Check className="size-2.5 text-bg" strokeWidth={3} />}</span>
              <img src={r.thumbnail_url ?? ""} alt="" className="h-16 w-9 shrink-0 rounded-sm bg-raised object-cover" />
              <div className="flex min-w-0 flex-1 flex-col">
                <div className="truncate" title={clip?.source_url ?? clip?.original_filename ?? undefined}>
                  {clip ? clipName(clip) : `Clip ${r.source_clip_id}`}
                </div>
                <div className="truncate text-sm text-muted">{brandOf(r.brand_id)}</div>
                <div className="mt-auto flex justify-between text-sm tabular-nums text-subtle">
                  <span className={cn(long && "text-warn")} title={long ? `Instagram Reels can be at most ${MAX_REEL_SECONDS / 60} min` : undefined}>
                    {mmss(r.duration_s)}
                  </span>
                  <span>{ago(r.completed_at ?? r.created_at)}</span>
                </div>
              </div>
            </label>
          )
        })}
      </div>
      <div className="shrink-0 space-y-1.5 border-t border-line p-2">
        <label className="flex items-center gap-1.5">
          <span className="text-sm text-subtle">To</span>
          <select aria-label="Account" className={cn(field, "min-w-0 flex-1 bg-raised")} value={account?.id ?? ""} onChange={(e) => setPicked(Number(e.target.value))}>
            {targets.length === 0 && <option value="">{accounts ? "No connected account" : "Loading…"}</option>}
            {targets.map((a) => (
              <option key={a.id} value={a.id}>
                @{a.username}
              </option>
            ))}
          </select>
        </label>
        <button className={cn(btn.primary, "w-full tabular-nums")} disabled={!account || !chosen.length || auto.isPending} onClick={() => run()}>
          {auto.isPending ? "Scheduling…" : `Auto-schedule ${chosen.length || ""}`}
        </button>
        <div className="text-sm tabular-nums text-subtle">
          {account && next.data ? (next.data.scheduled_for ? `Next free slot: ${shortWhen(next.data.scheduled_for, account.timezone)}` : "No free slot within 30 days") : " "}
        </div>
        {error && <p className="text-sm text-bad">{error}</p>}
        {result && (
          <div data-result className="space-y-0.5 text-sm">
            <div className="text-ok tabular-nums">Placed {result.placed.length}</div>
            {result.unplaced.map((u) => (
              <div key={u.render_id} className="text-warn">
                <span className="font-mono text-xs">render {u.render_id}</span>: {u.reason}
              </div>
            ))}
          </div>
        )}
      </div>
    </aside>
  )
}
