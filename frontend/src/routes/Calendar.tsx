import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronLeft, ChevronRight, Globe, X } from "lucide-react"
import { Fragment, useState, type DragEvent } from "react"
import { useSearchParams } from "react-router"

import { approvePost, type AccountOut, type PostOut } from "@/api"
import { getPostOptions, listAccountsOptions, listAccountsQueryKey, listPostsOptions, listPostsQueryKey, updatePostMutation } from "@/api/@tanstack/react-query.gen"
import { Avatar, ConnChip, ZERNIO_URL } from "@/components/AccountBits"
import { DayMeter, GapNote, Legend, PostCard, SlotBox } from "@/components/CalendarBits"
import { PostDrawer } from "@/components/PostDrawer"
import { ScheduleTray } from "@/components/ScheduleTray"
import { Empty, Header } from "@/components/bits"
import { addDays, apiError, dayLabel, dropTime, localParts, postAt, slotTime, today, tooClose, weekOf, zonedToUtc } from "@/lib/schedule"
import { btn, cn } from "@/lib/utils"

const LEAD_MS = 5 * 60_000 // backend: scheduled_for >= now + 5 min
const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`

export function Calendar() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const week = weekOf(params.get("week") ?? today())
  const go = (date: string | null) => setParams(date ? { week: date } : {})

  const accounts = useQuery(listAccountsOptions())
  const lanes = (accounts.data ?? []).filter((a) => !a.disabled_at)
  // One UTC day of slack either side covers every zone; cells filter by each account's local date.
  const range = { from: `${addDays(week[0], -1)}T00:00:00Z`, to: `${addDays(week[6], 2)}T00:00:00Z` }
  const key = listPostsQueryKey({ query: range })
  const posts = useQuery({ ...listPostsOptions({ query: range }), refetchInterval: 30_000 })
  const live = (posts.data ?? []).filter((p) => p.status !== "CANCELLED")

  const [open, setOpen] = useState<number | null>(null)
  const [drag, setDrag] = useState<PostOut | null>(null)
  const [over, setOver] = useState("")
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null)
  const refresh = () => (
    qc.invalidateQueries({ queryKey: listPostsQueryKey() }), qc.invalidateQueries({ queryKey: [{ _id: "getPost" }] }), qc.invalidateQueries({ queryKey: listAccountsQueryKey() })
  )

  const move = useMutation({ ...updatePostMutation(), onSettled: refresh })
  function moveTo(p: PostOut, iso: string) {
    if (Date.parse(iso) === Date.parse(p.scheduled_for)) return
    const prev = qc.getQueryData<PostOut[]>(key)
    qc.cancelQueries({ queryKey: key })
    qc.setQueryData<PostOut[]>(key, (xs) => xs?.map((x) => (x.id === p.id ? { ...x, scheduled_for: iso } : x)))
    setNotice(null)
    move.mutate(
      { path: { post_id: p.id }, body: { scheduled_for: iso } },
      {
        onError: (e) => {
          qc.setQueryData(key, prev) // rollback
          const { code, message } = apiError(e)
          setNotice({ ok: false, text: `Couldn't move post ${p.id}: ${message}${code ? ` (${code})` : ""}` })
        },
      }
    )
  }

  const inWeek = (p: PostOut, a: AccountOut) => week.includes(localParts(postAt(p), a.timezone).date)
  const drafts = live.filter((p) => p.status === "DRAFT" && lanes.some((a) => a.id === p.account_id && inWeek(p, a)))
  const approveAll = useMutation({
    mutationFn: async () => {
      const failed: string[] = []
      for (const p of drafts) await approvePost({ path: { post_id: p.id }, throwOnError: true }).catch((e) => failed.push(`post ${p.id}: ${apiError(e).message}`))
      return failed
    },
    onSuccess: (failed) =>
      setNotice(failed.length ? { ok: false, text: `Approved ${drafts.length - failed.length}; ${failed.join("; ")}` } : { ok: true, text: `Approved ${plural(drafts.length, "draft")}` }),
    onSettled: refresh,
  })

  function target(a: AccountOut, date: string, slot?: string) {
    const id = `${a.id}|${date}|${slot ?? ""}`
    return {
      id,
      handlers: {
        onDragOver: (e: DragEvent) => {
          if (drag?.account_id !== a.id) return
          e.preventDefault()
          e.stopPropagation()
          setOver(id)
        },
        onDrop: (e: DragEvent) => {
          e.preventDefault()
          e.stopPropagation()
          if (drag?.account_id === a.id) moveTo(drag, dropTime(drag.scheduled_for, a.timezone, date, slot))
          setDrag(null)
          setOver("")
        },
      },
    }
  }

  // The drawer loads its own post, so it stays open when a save moves the post out of this week.
  const one = useQuery({ ...getPostOptions({ path: { post_id: open ?? 0 } }), enabled: open != null, refetchInterval: 30_000 })
  const drawerPost = open == null ? undefined : (one.data ?? posts.data?.find((p) => p.id === open))
  const drawerAccount = accounts.data?.find((a) => a.id === drawerPost?.account_id)
  const now = posts.dataUpdatedAt // refreshed every 30 s

  return (
    <>
      <Header>
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-semibold">Calendar</h1>
          <div className="flex items-center gap-1">
            <button className={cn(btn.icon, "border border-line")} aria-label="Previous week" onClick={() => go(addDays(week[0], -7))}>
              <ChevronLeft className="size-4" />
            </button>
            <button className="h-7 rounded border border-line px-2.5 hover:bg-hover" onClick={() => go(null)}>
              Today
            </button>
            <button className={cn(btn.icon, "border border-line")} aria-label="Next week" onClick={() => go(addDays(week[0], 7))}>
              <ChevronRight className="size-4" />
            </button>
          </div>
          <span className="font-medium tabular-nums">
            {dayLabel(week[0])} – {dayLabel(week[6], { year: true })}
          </span>
        </div>
        <div className="flex min-w-0 items-center gap-4">
          {notice && (
            <span role="status" className={cn("flex min-w-0 items-center gap-1 text-sm", notice.ok ? "text-ok" : "text-bad")}>
              <span className="truncate" title={notice.text}>
                {notice.text}
              </span>
              <button className="shrink-0 text-subtle hover:text-fg" aria-label="Dismiss" onClick={() => setNotice(null)}>
                <X className="size-3.5" />
              </button>
            </span>
          )}
          <span className="flex shrink-0 items-center gap-1.5 text-sm text-muted">
            <Globe className="size-3.5" />
            Times in each account's zone
          </span>
          <button className={cn(btn.secondary, "tabular-nums")} disabled={!drafts.length || approveAll.isPending} onClick={() => approveAll.mutate()}>
            Approve all drafts{drafts.length ? ` (${drafts.length})` : ""}
          </button>
        </div>
      </Header>
      <section className="flex min-h-0 flex-1">
        <ScheduleTray accounts={lanes} />
        {accounts.isError || posts.isError ? (
          <Empty>Couldn't load the calendar: {apiError(accounts.error ?? posts.error).message}</Empty>
        ) : accounts.data && lanes.length === 0 ? (
          <Empty>No accounts to schedule on. Connect one on the Accounts page.</Empty>
        ) : (
          <div className="min-w-0 flex-1 overflow-auto" onDragEnd={() => (setDrag(null), setOver(""))}>
            <div className="grid min-h-full" style={{ gridTemplateColumns: "160px repeat(7, minmax(112px, 1fr))", gridTemplateRows: `36px repeat(${lanes.length}, auto) 1fr` }}>
              <div className="sticky top-0 left-0 z-30 grid border-r border-b border-line bg-bg">
                <Legend />
              </div>
              {week.map((d, i) => (
                <div key={d} className={cn("sticky top-0 z-20 flex items-center border-r border-b border-line bg-bg px-2 font-medium tabular-nums", d === today() && "bg-raised")}>
                  {dayLabel(d, { weekday: true, month: i === 0 || d.endsWith("-01") })}
                </div>
              ))}
              {lanes.map((a) => {
                const mine = live.filter((p) => p.account_id === a.id)
                const { gaps, warn } = tooClose(mine.map((p) => ({ id: p.id, scheduled_for: postAt(p), status: p.status })), a.min_gap_minutes)
                const slots = a.posting_slots.times ?? []
                return (
                  <Fragment key={a.id}>
                    <div data-lane={a.id} className="sticky left-0 z-10 flex flex-col gap-2 border-r border-b border-line bg-panel px-3 pt-3 pb-1">
                      <div className="flex min-w-0 items-center gap-2">
                        <Avatar a={a} className="size-6 text-xs" />
                        <div className="min-w-0">
                          <div className="truncate font-medium">@{a.username}</div>
                          <div className="text-sm tabular-nums text-muted">
                            {slots.length} slots · cap {a.daily_cap}
                          </div>
                        </div>
                      </div>
                      <div className="text-xs text-subtle">{a.timezone}</div>
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <ConnChip a={a} />
                        {a.connection_status === "disconnected" && (
                          <a href={ZERNIO_URL} target="_blank" rel="noreferrer" className="text-xs text-muted underline underline-offset-2 hover:text-fg">
                            Reconnect
                          </a>
                        )}
                      </div>
                      <div className="mt-auto text-sm tabular-nums text-subtle">{plural(mine.filter((p) => inWeek(p, a)).length, "post")} this week</div>
                    </div>
                    {week.map((date) => {
                      const day = mine.filter((p) => localParts(postAt(p), a.timezone).date === date)
                      const cards = day.map((p) => ({ t: slotTime(p, a.timezone), p })) // a post published at 09:02 fills the 09:00 slot
                      const open = slots.filter((t) => !cards.some((c) => c.t === t)).map((t) => ({ t, p: undefined }))
                      const items = [...cards, ...open].sort((x, y) => x.t.localeCompare(y.t))
                      const cell = target(a, date)
                      return (
                        <div key={date} data-cell={`${a.id}|${date}`} {...cell.handlers} className={cn("flex min-w-0 flex-col gap-1 border-r border-b border-line p-1", over === cell.id && "bg-accent/5")}>
                          {items.map(({ t, p }) => {
                            if (!p) {
                              const slot = target(a, date, t)
                              const past = zonedToUtc(date, t, a.timezone).getTime() < now + LEAD_MS
                              return <SlotBox key={`s${t}`} time={t} past={past} over={over === slot.id} droppable={!past} handlers={slot.handlers} />
                            }
                            const gap = gaps.get(p.id)
                            return (
                              <Fragment key={p.id}>
                                {gap != null && <GapNote minutes={gap} />}
                                <PostCard
                                  p={p}
                                  tz={a.timezone}
                                  warn={warn.has(p.id)}
                                  ghost={drag?.id === p.id}
                                  onOpen={() => setOpen(p.id)}
                                  onDragStart={(e) => (e.dataTransfer.setData("text/plain", String(p.id)), (e.dataTransfer.effectAllowed = "move"), setDrag(p))}
                                  onDragEnd={() => (setDrag(null), setOver(""))}
                                />
                              </Fragment>
                            )
                          })}
                          <DayMeter n={day.length} cap={a.daily_cap} />
                        </div>
                      )
                    })}
                  </Fragment>
                )
              })}
            </div>
          </div>
        )}
        {drawerPost && drawerAccount && <PostDrawer key={drawerPost.id} p={drawerPost} a={drawerAccount} onClose={() => setOpen(null)} />}
      </section>
    </>
  )
}
