import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CalendarPlus, CalendarX, CheckCheck, ChevronLeft, ChevronRight, CircleDashed, Clapperboard, Settings2, TriangleAlert, X } from "lucide-react"
import { HoverCard, Popover } from "radix-ui"
import { Fragment, useEffect, useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from "react"
import { Link, useSearchParams } from "react-router"

import { approvePost, autoSchedule, createPost, type AccountOut, type AutoScheduleOut, type PostOut, type RenderOut } from "@/api"
import {
  getPostOptions,
  listAccountsOptions,
  listAccountsQueryKey,
  listBrandsOptions,
  listClipsOptions,
  listPostsOptions,
  listPostsQueryKey,
  listRendersOptions,
  listRendersQueryKey,
  nextSlotOptions,
  updatePostMutation,
} from "@/api/@tanstack/react-query.gen"
import { Avatar, ConnChip, ZERNIO_URL } from "@/components/AccountBits"
import { BoardSkeleton, FreeSlot, GapFix, Ghost, PublishedChip, Tile, TightSlot, type Size } from "@/components/CalendarBits"
import { PostDrawer } from "@/components/PostDrawer"
import { ScheduleTray, type Group } from "@/components/ScheduleTray"
import { Empty, Header } from "@/components/bits"
import {
  AUTO_LEAD,
  BROWSER_TZ,
  FAILED,
  MIN_LEAD,
  MOVABLE,
  STATUS_LABEL,
  addDays,
  apiError,
  boardRows,
  boardSpot,
  dayLabel,
  dropTime,
  firstFree,
  localParts,
  planFill,
  postAt,
  shortWhen,
  slotTime,
  today,
  tooClose,
  tzName,
  utcOffset,
  zonedToUtc,
} from "@/lib/schedule"
import { MAX_REEL_SECONDS, MIN_REEL_SECONDS, btn, clipName, cn, shortUrl } from "@/lib/utils"

// The slot board: one account at a time (its own zone), 7 days from ?week= (default: today there), one row per
// posting slot. Free slots are drop targets; the queue on the right fills them (select + Auto-schedule, or drag).

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`
const MIN = 60_000
type Drag = { post: PostOut } | { render: RenderOut }
type Notice = { ok: boolean; text: string }

export function Calendar() {
  const qc = useQueryClient()
  const [params, setParams] = useSearchParams()
  const setParam = (next: Record<string, string | null>) =>
    setParams((cur) => {
      const n = new URLSearchParams(cur)
      for (const [k, v] of Object.entries(next)) {
        if (v == null) n.delete(k)
        else n.set(k, v)
      }
      return n
    })

  const accounts = useQuery(listAccountsOptions())
  const lanes = (accounts.data ?? []).filter((x) => !x.disabled_at)
  const a = lanes.find((x) => String(x.id) === params.get("account")) ?? lanes[0]
  const tz = a?.timezone ?? BROWSER_TZ
  const todayDate = today(tz)
  const week = params.get("week") ?? ""
  const start = /^\d{4}-\d\d-\d\d$/.test(week) ? week : todayDate
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i))
  // The window plus 30 days ahead (the Auto-schedule preview and the gap fixes need every upcoming post), with a
  // UTC day of slack either side; cells filter by the account's local date.
  const range = {
    from: `${[addDays(start, -1), addDays(todayDate, -2)].sort()[0]}T00:00:00Z`,
    to: `${[addDays(start, 8), addDays(todayDate, 34)].sort()[1]}T00:00:00Z`,
  }
  const key = listPostsQueryKey({ query: range })
  const posts = useQuery({ ...listPostsOptions({ query: range }), enabled: !!accounts.data, refetchInterval: 30_000, placeholderData: keepPreviousData })
  const failedQ = useQuery({ ...listPostsOptions({ query: { status: ["FAILED", "DEAD_LETTER"] } }), refetchInterval: 30_000 })
  const draftsQ = useQuery({ ...listPostsOptions({ query: { status: ["DRAFT"] } }), refetchInterval: 30_000 })
  const renders = useQuery(listRendersOptions({ query: { status: "READY", unscheduled: true } }))
  const clips = useQuery(listClipsOptions())
  const brands = useQuery(listBrandsOptions())
  const archived = useQuery(listBrandsOptions({ query: { archived: true } }))
  const target = a?.connection_status === "connected" ? a : undefined
  const next = useQuery({ ...nextSlotOptions({ path: { account_id: target?.id ?? 0 } }), enabled: !!target })
  const now = posts.dataUpdatedAt || Date.now() // refreshed every 30 s; placeholder data (a range not fetched yet) reports 0

  const [open, setOpen] = useState<number | null>(null)
  const [drag, setDrag] = useState<Drag | null>(null)
  const [over, setOver] = useState("")
  const [notice, setNotice] = useState<Notice | null>(null)
  const [sel, setSel] = useState<Set<number>>(new Set())
  const [unplaced, setUnplaced] = useState<AutoScheduleOut["unplaced"]>([]) // the last Auto-schedule's leftovers
  const [busy, setBusy] = useState(false)
  const [pending, setPending] = useState<{ key: string; r: RenderOut } | null>(null) // a placed render while it saves
  const placing = useRef(false) // set in the same tick as the click: two quick placements can't both start
  const [approving, setApproving] = useState<Set<number>>(new Set())
  const [flash, setFlash] = useState<number | null>(null)
  const [collapsed, setCollapsed] = useState(false)
  const endDrag = () => (setDrag(null), setOver(""))

  useEffect(() => {
    if (!notice?.ok) return
    const t = setTimeout(() => setNotice(null), 5000)
    return () => clearTimeout(t)
  }, [notice])
  useEffect(() => {
    if (flash == null) return
    document.querySelector(`[data-post="${flash}"]`)?.scrollIntoView({ block: "nearest" })
    const t = setTimeout(() => setFlash(null), 1600)
    return () => clearTimeout(t)
  }, [flash])

  const refresh = () =>
    Promise.all([
      qc.invalidateQueries({ queryKey: listPostsQueryKey() }),
      qc.invalidateQueries({ queryKey: listRendersQueryKey() }),
      qc.invalidateQueries({ queryKey: listAccountsQueryKey() }),
      qc.invalidateQueries({ queryKey: [{ _id: "nextSlot" }] }),
      qc.invalidateQueries({ queryKey: [{ _id: "getPost" }] }),
    ])
  const fail = (what: string, e: unknown) => {
    const { code, message } = apiError(e)
    setNotice({ ok: false, text: `${what}: ${message}${code ? ` (${code})` : ""}` })
  }

  // ---- queue: READY renders grouped by clip, in the API's order; the order sent is the order shown
  const clipOf = (id: number) => clips.data?.find((c) => c.id === id)
  const brandOf = (id: number | null) => (id == null ? undefined : [...(brands.data ?? []), ...(archived.data ?? [])].find((b) => b.id === id))
  const nameOf = (clipId: number, fallback?: string | null) => {
    const c = clipOf(clipId)
    return c ? clipName(c) : shortUrl(fallback ?? `Clip ${clipId}`)
  }
  const groups: Group[] = []
  for (const r of renders.data ?? []) {
    const g = groups.find((x) => x.clipId === r.source_clip_id)
    if (g) g.items.push(r)
    else groups.push({ clipId: r.source_clip_id, items: [r] })
  }
  const ordered = groups.flatMap((g) => g.items)
  const chosen = ordered.filter((r) => sel.has(r.id) && r.id !== pending?.r.id) // a render being placed can't go twice
  const select = (ids: number[], on: boolean) => {
    setUnplaced([])
    setSel((s) => {
      const n = new Set(s)
      for (const id of ids) {
        if (on) n.add(id)
        else n.delete(id)
      }
      return n
    })
  }

  // ---- board model for one account
  const live = (posts.data ?? []).filter((p) => p.status !== "CANCELLED")
  function boardOf(acc: AccountOut) {
    const times = [...(acc.posting_slots.times ?? [])].sort()
    const mine = live.filter((p) => p.account_id === acc.id)
    const cells = new Map<string, PostOut[]>() // "date|09:00" (slot) or "date|b2" (band)
    const perDay = new Map<string, number>() // the daily cap counts every post by when it goes (or went) out
    const dayOf = new Map<number, string>()
    const bands = new Set<number>()
    for (const p of mine) {
      const s = boardSpot(p, times, acc.timezone)
      const k = `${s.date}|${s.slot ?? `b${s.band}`}`
      cells.set(k, [...(cells.get(k) ?? []), p])
      perDay.set(s.date, (perDay.get(s.date) ?? 0) + 1)
      dayOf.set(p.id, s.date)
      if (s.band != null && days.includes(s.date)) bands.add(s.band)
    }
    // The same rules as the server's slot search (slots.first_free), by instant, not by "HH:MM" label (DST days).
    // except: the post being dragged, whose own spot doesn't count against its new one.
    const others = (except?: number) => mine.filter((p) => p.id !== except)
    const count = (date: string, except?: number) => (perDay.get(date) ?? 0) - (except != null && dayOf.get(except) === date ? 1 : 0)
    const takenAt = (ms: number, except?: number) => others(except).some((p) => Date.parse(p.scheduled_for) === ms || Date.parse(postAt(p)) === ms)
    const near = (ms: number, except?: number) => others(except).find((p) => Math.abs(Date.parse(postAt(p)) - ms) < acc.min_gap_minutes * MIN)
    const state = (date: string, slot: string, except?: number) => {
      const ms = zonedToUtc(date, slot, acc.timezone).getTime()
      return takenAt(ms, except) ? "taken" : ms < now + MIN_LEAD ? "past" : count(date, except) >= acc.daily_cap ? "full" : near(ms, except) ? "tight" : "free"
    }
    /** Can a post go out at ms (local day date)? The day-head drop keeps a post's own time, so it needs the check too. */
    const fits = (date: string, ms: number, except?: number) => ms >= now + MIN_LEAD && count(date, except) < acc.daily_cap && !takenAt(ms, except) && !near(ms, except)
    const free = days.flatMap((d) => times.filter((t) => state(d, t) === "free").map((t) => `${d}|${t}`))
    const close = tooClose(
      mine.map((p) => ({ id: p.id, scheduled_for: postAt(p), status: p.status })),
      acc.min_gap_minutes
    )
    return { acc, times, mine, cells, rows: boardRows(times, bands), state, near, fits, free, ...close }
  }
  const boards = posts.data ? lanes.map(boardOf) : []
  const board = boards.find((b) => b.acc.id === a?.id)
  type Board = NonNullable<typeof board>
  const taken = (b: Board, except?: number) => b.mine.filter((p) => p.id !== except).map((p) => Date.parse(postAt(p)))

  // ---- attention: global (every account), so nothing that needs the operator hides behind the switcher
  const onLane = (p: PostOut) => lanes.some((x) => x.id === p.account_id)
  const failed = (failedQ.data ?? []).filter(onLane).sort((x, y) => x.account_username.localeCompare(y.account_username) || Date.parse(x.scheduled_for) - Date.parse(y.scheduled_for))
  const drafts = (draftsQ.data ?? []).filter(onLane).sort((x, y) => Date.parse(x.scheduled_for) - Date.parse(y.scheduled_for))
  const conflicts = boards.flatMap((b) => b.mine.filter((p) => b.gaps.has(p.id) && Date.parse(postAt(p)) >= now).map((p) => ({ b, p })))
  function jump() {
    const c = conflicts[0]
    if (!c) return
    const date = localParts(postAt(c.p), c.b.acc.timezone).date
    if (c.b.acc.id !== a?.id || !days.includes(date)) setParam({ account: String(c.b.acc.id), week: date })
    setFlash(c.p.id)
  }

  // ---- Auto-schedule preview: the same slot search as the server, over every upcoming post of the account
  const plan = target && board && chosen.length ? planFill(chosen, target, taken(board), now) : undefined
  const previews = new Map<string, { r: RenderOut; i: number }>()
  const where = new Map<string, string[]>()
  let outside = 0
  for (const [i, [rid, t]] of [...(plan?.placed ?? [])].entries()) {
    const { date, time } = localParts(t, tz)
    if (board && days.includes(date) && board.times.includes(time) && !board.cells.has(`${date}|${time}`)) {
      previews.set(`${date}|${time}`, { r: chosen.find((r) => r.id === rid)!, i })
      where.set(date, [...(where.get(date) ?? []), time])
    } else outside++
  }
  const into = [...where].map(([d, ts]) => `${dayLabel(d, { weekday: true, month: false })} · ${ts.join(", ")}`).join("; ")
  const firstAt = plan?.placed.size ? Math.min(...plan.placed.values()) : 0
  const lands = firstAt
    ? `From ${shortWhen(new Date(firstAt).toISOString(), tz, now)} (${firstAt < zonedToUtc(days[0], "00:00", tz).getTime() ? "before" : "after"} this week)`
    : ""
  const footnote = !a
    ? ""
    : !target
      ? `@${a.username} is disconnected`
      : plan
        ? [
            plan.placed.size ? `${into ? `Into ${into}${outside ? ` +${outside} outside this view` : ""}` : lands} on @${target.username}` : "",
            plan.unplaced.length ? `${plan.unplaced.length} won't fit` : "",
          ]
            .filter(Boolean)
            .join(" · ")
        : "Select renders, or drag one onto a slot"
  const outcome = (r: RenderOut) => (brandOf(r.brand_id)?.auto_approve ? "scheduled" : "as a draft")
  // "+" with a selection places the next selected render the preview doesn't already show in view (else the last one)
  const inView = new Set([...previews.values()].map((v) => v.r.id))
  const first = chosen.find((r) => !inView.has(r.id)) ?? chosen[chosen.length - 1]

  // ---- actions
  async function fill() {
    if (!target || !chosen.length || busy || placing.current) return
    const acc = target
    setBusy(true)
    setUnplaced([])
    setNotice(null)
    try {
      const { data: out } = await autoSchedule({ body: { render_ids: chosen.map((r) => r.id), account_id: acc.id }, throwOnError: true })
      setUnplaced(out.unplaced)
      setSel(new Set(out.unplaced.map((u) => u.render_id)))
      setNotice({ ok: true, text: `Placed ${out.placed.length} on @${acc.username}${out.unplaced.length ? `, ${out.unplaced.length} not placed` : ""}` })
    } catch (e) {
      fail("Couldn't auto-schedule", e)
    } finally {
      await refresh()
      setBusy(false)
    }
  }

  async function place(r: RenderOut, date: string, slot: string) {
    if (!target || placing.current || busy) return // one placement at a time: the same render must never get two posts
    const acc = target
    placing.current = true
    setNotice(null)
    setPending({ key: `${date}|${slot}`, r })
    try {
      const body = { render_id: r.id, account_id: acc.id, scheduled_for: zonedToUtc(date, slot, acc.timezone).toISOString() }
      const { data: out } = await createPost({ body, throwOnError: true })
      select([r.id], false)
      setNotice({ ok: true, text: `${out.status === "DRAFT" ? "Draft" : "Scheduled"} for ${shortWhen(out.scheduled_for, acc.timezone)} on @${acc.username}` })
    } catch (e) {
      fail(`Couldn't schedule render ${r.id}`, e)
    } finally {
      await refresh()
      placing.current = false
      setPending(null)
    }
  }

  // Moves show at once and roll back (just that post) if the server refuses; callbacks on the hook run for every call.
  const move = useMutation({
    mutationFn: updatePostMutation().mutationFn!,
    onMutate: ({ path, body }) => {
      void qc.cancelQueries({ queryKey: key })
      const old = qc.getQueryData<PostOut[]>(key)?.find((x) => x.id === path.post_id)?.scheduled_for
      qc.setQueryData<PostOut[]>(key, (xs) => xs?.map((x) => (x.id === path.post_id && body?.scheduled_for ? { ...x, scheduled_for: body.scheduled_for } : x)))
      return { key, old }
    },
    onError: (e, { path }, ctx) => {
      if (ctx?.old) qc.setQueryData<PostOut[]>(ctx.key, (xs) => xs?.map((x) => (x.id === path.post_id ? { ...x, scheduled_for: ctx.old! } : x)))
      fail(`Couldn't move post ${path.post_id}`, e)
    },
    onSettled: () => void refresh(),
  })
  function moveTo(p: PostOut, iso: string) {
    if (Date.parse(iso) === Date.parse(p.scheduled_for)) return
    setNotice(null)
    move.mutate({ path: { post_id: p.id }, body: { scheduled_for: iso } })
  }

  async function approve(p: PostOut) {
    setApproving((s) => new Set(s).add(p.id))
    try {
      const { data } = await approvePost({ path: { post_id: p.id }, throwOnError: true })
      setNotice({ ok: true, text: `Approved: goes out ${shortWhen(data.scheduled_for, tzOf(data.account_id))}` })
    } catch (e) {
      fail(`Couldn't approve post ${p.id}`, e)
    } finally {
      await refresh()
      setApproving((s) => {
        const n = new Set(s)
        n.delete(p.id)
        return n
      })
    }
  }

  // Every draft on every account, so the confirm lists each one: approving is what lets a post publish.
  async function approveAll() {
    const list = drafts
    const lines = list.map((p) => `  ${lanes.length > 1 ? `@${p.account_username}  ` : ""}${shortWhen(p.scheduled_for, tzOf(p.account_id))}  ${p.render.brand_name ?? "No logo"} · ${shortUrl(p.render.clip_name ?? `Clip ${p.render.clip_id}`)}`)
    const late = list.filter((p) => Date.parse(p.scheduled_for) < Date.now() + MIN_LEAD).length
    const text = [
      `Approve ${plural(list.length, "draft")}? Each one then publishes at its time.`,
      [...lines.slice(0, 15), ...(lines.length > 15 ? [`  …and ${lines.length - 15} more`] : [])].join("\n"),
      late ? `${late} past due: ${late === 1 ? "it moves" : "they move"} to the next free slot.` : "",
    ]
    if (!confirm(text.filter(Boolean).join("\n\n"))) return
    setBusy(true)
    const bad: string[] = []
    for (const p of list) await approvePost({ path: { post_id: p.id }, throwOnError: true }).catch((e) => bad.push(`post ${p.id}: ${apiError(e).message}`))
    setNotice(bad.length ? { ok: false, text: `Approved ${list.length - bad.length}; ${bad.join("; ")}` } : { ok: true, text: `Approved ${plural(list.length, "draft")}` })
    await refresh()
    setBusy(false)
  }

  const tzOf = (accountId: number) => accounts.data?.find((x) => x.id === accountId)?.timezone ?? tz

  // ---- drag targets: free future slots of this account; a day head keeps the post's own time
  function targetOf(k: string, ok: boolean, drop: () => void) {
    return {
      onDragOver: (e: DragEvent) => {
        if (!ok) return
        e.preventDefault()
        if (over !== k) setOver(k)
      },
      onDragLeave: (e: DragEvent) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver((o) => (o === k ? "" : o))
      },
      onDrop: (e: DragEvent) => {
        e.preventDefault()
        endDrag()
        if (ok) drop()
      },
    }
  }
  const dragPost = drag && "post" in drag && MOVABLE.has(drag.post.status) && drag.post.account_id === a?.id ? drag.post : null
  const dragRender = drag && "render" in drag && target ? drag.render : null

  // The drawer loads its own post, so it stays open when a save moves the post out of view.
  const one = useQuery({ ...getPostOptions({ path: { post_id: open ?? 0 } }), enabled: open != null, refetchInterval: 30_000 })
  const drawerPost = open == null ? undefined : (one.data ?? posts.data?.find((p) => p.id === open))
  const drawerAccount = accounts.data?.find((x) => x.id === drawerPost?.account_id)

  // ---- rendering helpers
  const size: Size = (board?.times.length ?? 0) > 4 ? "compact" : "full"
  const noon = (d: string) => zonedToUtc(d, "12:00", tz).getTime()
  const day = (d: string) => dayLabel(d, { weekday: true })
  const short = (d: string) => dayLabel(d, { weekday: true, month: false })
  // Tab stops: every post, but only the first open slot of each day; the arrow keys reach the rest.
  const tabbable = new Set(board ? days.map((d) => `${d}|${board.times.find((t) => board.state(d, t) === "free")}`) : [])

  function tile(b: Board, p: PostOut, s: Size) {
    const gap = b.gaps.get(p.id)
    let fix: ReactNode
    if (gap != null) {
      const before = b.mine.find((x) => x.id === b.prev.get(p.id))
      const mover = (MOVABLE.has(p.status) ? p : before)!
      // the next slot that keeps the gap, from where the mover already is (not from now): it stays near its plan
      const from = Math.max(now + AUTO_LEAD, Date.parse(mover.scheduled_for))
      const to = firstFree(b.times, b.acc.timezone, b.acc.daily_cap, b.acc.min_gap_minutes, taken(b, mover.id), from)
      fix = (
        <GapFix
          minutes={gap}
          min={b.acc.min_gap_minutes}
          after={before ? slotTime(before, tz) : "previous"}
          mover={mover === p ? "this post" : `the ${slotTime(mover, tz)} ${STATUS_LABEL[mover.status].toLowerCase()}`}
          to={to ? shortWhen(new Date(to).toISOString(), tz, now) : null}
          onMove={() => to && moveTo(mover, new Date(to).toISOString())}
          onOpen={() => setOpen(mover.id)}
        />
      )
    }
    const date = localParts(postAt(p), tz).date
    return (
      <Tile
        key={p.id}
        p={p}
        size={s}
        day={day(date)}
        time={slotTime(p, tz)}
        name={nameOf(p.render.clip_id, p.render.clip_name)}
        warn={b.warn.has(p.id)}
        flash={flash === p.id}
        lifted={dragPost?.id === p.id}
        gap={fix}
        onOpen={() => setOpen(p.id)}
        onApprove={p.status === "DRAFT" ? () => approve(p) : undefined}
        approving={approving.has(p.id)}
        onDragStart={(e) => (e.dataTransfer.setData("text/plain", `post ${p.id}`), (e.dataTransfer.effectAllowed = "move"), setDrag({ post: p }))}
        onDragEnd={endDrag}
      />
    )
  }

  // Drop handling sits on the cell, which stays mounted while its content swaps (preview -> drop target).
  function cellTarget(b: Board, d: string, slot: string) {
    const ok = (!!dragPost || !!dragRender) && b.state(d, slot, dragPost?.id) === "free"
    return targetOf(`${d}|${slot}`, ok, () => (dragPost ? moveTo(dragPost, zonedToUtc(d, slot, tz).toISOString()) : dragRender && place(dragRender, d, slot)))
  }

  function slotCell(b: Board, d: string, slot: string) {
    const k = `${d}|${slot}`
    const here = b.cells.get(k)
    if (here?.length === 1) return tile(b, here[0], size)
    if (here) return <div className="flex h-full flex-col gap-1 overflow-auto">{here.map((p) => tile(b, p, "thin"))}</div>
    if (pending?.key === k)
      return (
        <Ghost
          size={size}
          thumb={pending.r.thumbnail_url}
          time={slot}
          brand={brandOf(pending.r.brand_id)?.name ?? "No logo"}
          name={nameOf(pending.r.source_clip_id)}
          tag="Saving…"
          outcome={outcome(pending.r)}
        />
      )
    const st = b.state(d, slot, dragPost?.id)
    if (st === "past" || st === "taken") return null
    if (st === "full")
      return (
        <div className="grid h-full place-items-center text-xs text-dim" title={`${day(d)} already has ${b.acc.daily_cap} posts (the daily cap)`}>
          Full
        </div>
      )
    if (st === "tight") {
      const ms = zonedToUtc(d, slot, tz).getTime()
      const n = b.near(ms, dragPost?.id)!
      return <TightSlot minutes={Math.round(Math.abs(Date.parse(postAt(n)) - ms) / MIN)} time={localParts(postAt(n), tz).time} min={b.acc.min_gap_minutes} />
    }
    const pv = previews.get(k)
    if (pv && over !== k)
      return (
        <Ghost
          size={size}
          thumb={pv.r.thumbnail_url}
          time={slot}
          brand={brandOf(pv.r.brand_id)?.name ?? "No logo"}
          name={nameOf(pv.r.source_clip_id)}
          tag={`Fill ${pv.i + 1}`}
          of={plan!.placed.size}
          outcome={outcome(pv.r)}
          label={`Schedule #${pv.r.id} (${nameOf(pv.r.source_clip_id)}) at ${day(d)} ${slot}, ${outcome(pv.r)}`}
          tabIndex={tabbable.has(k) ? 0 : -1}
          onClick={() => place(pv.r, d, slot)}
        />
      )
    return (
      <FreeSlot
        title={
          !target
            ? `${day(d)} ${slot}: reconnect @${b.acc.username} to use it`
            : first
              ? `Schedule #${first.id} (${nameOf(first.source_clip_id)}) at ${day(d)} ${slot}`
              : `Free: ${day(d)} ${slot}. Select a render in the queue, then click, or drag one here`
        }
        armed={!!(dragPost || dragRender)}
        over={over === k}
        disabled={!target}
        tabIndex={tabbable.has(k) ? 0 : -1}
        drop={`${short(d)} · ${slot}`}
        outcome={dragRender ? `lands ${outcome(dragRender)}` : "move here"}
        hint={first && `Place #${first.id}`}
        onClick={() => {
          if (!target) return
          if (first) return void place(first, d, slot)
          setCollapsed(false)
          setTimeout(() => document.querySelector<HTMLElement>("[data-queue] input")?.focus())
        }}
      />
    )
  }

  function bandCell(b: Board, d: string, band: number) {
    const list = b.cells.get(`${d}|b${band}`) ?? []
    const pub = list.filter((p) => p.status === "PUBLISHED")
    const rest = pub.length > 1 ? list.filter((p) => p.status !== "PUBLISHED") : list
    return (
      <div className="flex flex-col gap-1">
        {pub.length > 1 && <PublishedChip posts={pub} tz={tz} onOpen={setOpen} />}
        {rest.map((p) => tile(b, p, "thin"))}
      </div>
    )
  }

  function dayHead(b: Board, d: string, i: number) {
    const isToday = d === todayDate
    const inDay = b.mine.filter((p) => localParts(postAt(p), tz).date === d)
    const states = b.times.map((t) => b.state(d, t))
    const nFree = states.filter((s) => s === "free").length
    const future = states.filter((s) => s !== "past").length
    const nFailed = inDay.filter((p) => FAILED.has(p.status)).length
    const note = nFailed
      ? { text: `${nFailed} failed`, tone: "text-bad" }
      : inDay.some((p) => b.gaps.has(p.id))
        ? { text: "Too close", tone: "text-warn" }
        : future && !nFree
          ? { text: "Full", tone: "text-dim" }
          : nFree && nFree < future
            ? { text: `${nFree} free`, tone: "text-dim" }
            : null
    const zone = i > 0 && tzName(tz, noon(d)) !== tzName(tz, noon(days[i - 1])) ? tzName(tz, noon(d)) : ""
    const k = `${d}|day`
    const keepIso = dragPost && dropTime(dragPost.scheduled_for, tz, d)
    const keep = !!keepIso && b.fits(d, Date.parse(keepIso), dragPost?.id)
    const summary = `${day(d)}: ${b.times.length - future} past, ${future - nFree} taken, ${nFree} free${nFailed ? `, ${nFailed} failed` : ""}`
    return (
      <div
        key={d}
        title={keep ? `Drop to keep its time on ${day(d)}` : summary}
        {...targetOf(k, keep, () => dragPost && keepIso && moveTo(dragPost, keepIso))}
        className={cn(
          "sticky top-0 z-20 flex min-w-0 items-center justify-between gap-2 border-l border-grid px-2 leading-4 whitespace-nowrap",
          isToday ? "bg-today" : "bg-bg",
          over === k && "bg-accent/10"
        )}
      >
        <span className="flex min-w-0 items-center gap-1.5 tabular-nums">
          {isToday ? (
            <>
              <span className="font-medium">{short(d).split(" ")[0]}</span>
              <span className="grid h-5 min-w-5 place-items-center rounded bg-hover px-1 font-semibold">{Number(d.slice(8))}</span>
            </>
          ) : (
            <span className={d < todayDate ? "text-dim" : "text-muted"}>{short(d)}</span>
          )}
          {d.endsWith("-01") && <span className="text-xs text-dim">{dayLabel(d).split(" ")[1]}</span>}
          {zone && <span className="text-xs text-dim">{zone}</span>}
        </span>
        <span className="sr-only">{summary}</span>
        {note && (
          <span className={cn("min-w-0 truncate text-xs", note.tone)} title={note.text}>
            {note.text}
          </span>
        )}
      </div>
    )
  }

  // Arrow keys move between board cells (only one open slot per day is a Tab stop).
  function arrows(e: KeyboardEvent<HTMLDivElement>) {
    const step = ({ ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] } as Record<string, number[]>)[e.key]
    const cell = (e.target as HTMLElement).closest<HTMLElement>("[data-cell]")
    if (!step || !cell || !board) return
    const [d, row] = cell.dataset.cell!.split("|")
    for (let x = days.indexOf(d) + step[0], y = board.rows.findIndex((r) => r.key === row) + step[1]; days[x] && board.rows[y]; x += step[0], y += step[1]) {
      const to = document.querySelector(`[data-cell="${days[x]}|${board.rows[y].key}"]`)?.querySelector<HTMLElement>("button:not(:disabled), a")
      if (to) {
        e.preventDefault()
        return to.focus()
      }
    }
  }

  // ---- the empty-window callout: says what to do next, and knows what is already going on
  const fits = (r: RenderOut) => r.duration_s == null || (r.duration_s <= MAX_REEL_SECONDS && r.duration_s >= MIN_REEL_SECONDS)
  const cta = cn(btn.secondary, "ml-auto shrink-0 font-normal whitespace-nowrap tabular-nums")
  function callout(b: Board) {
    if (b.acc.connection_status !== "connected")
      return (
        <>
          <ConnChip a={b.acc} />
          <span className="font-medium whitespace-nowrap">@{b.acc.username} is disconnected</span>
          <span className="min-w-0 text-muted">Nothing publishes or schedules on it until it is reconnected in Zernio.</span>
          <a href={ZERNIO_URL} target="_blank" rel="noreferrer" className={cta}>
            Reconnect
          </a>
        </>
      )
    if (!b.times.length)
      return (
        <>
          <CalendarX className="size-4 shrink-0 text-muted" />
          <span className="font-medium whitespace-nowrap">No posting slots on @{b.acc.username}</span>
          <span className="min-w-0 text-muted">Auto-schedule needs at least one.</span>
          <Link to="/accounts" className={cta}>
            Add slots on Accounts
          </Link>
        </>
      )
    const queued = b.mine.some((p) => days.includes(localParts(postAt(p), tz).date) && ["DRAFT", "SCHEDULED", "PUBLISHING"].includes(p.status))
    if (queued || !b.free.length) return null
    const title = start === todayDate ? "Nothing queued for the next 7 days" : "Nothing queued in these 7 days"
    if (chosen.length)
      return (
        <>
          <CalendarPlus className="size-4 shrink-0 text-accent" />
          <span className="font-medium whitespace-nowrap tabular-nums">{previews.size ? `Previewing ${plural(previews.size, "fill")}` : `${chosen.length} selected`}</span>
          <span className="min-w-0 text-muted">{previews.size ? "Auto-schedule places them all, or click one to place just that render." : footnote}</span>
        </>
      )
    if (!ordered.length)
      return (
        <>
          <Clapperboard className="size-4 shrink-0 text-muted" />
          <span className="font-medium whitespace-nowrap">{title}</span>
          <span className="min-w-0 text-muted">Nothing is ready to schedule either: renders made in the Library land in the queue.</span>
          <Link to="/library" className={cta}>
            Open Library
          </Link>
        </>
      )
    // Auto-schedule fills from today, so only a window that starts today (or earlier) offers the one-click fill
    const fromNow = start <= todayDate
    const oldest = [...ordered]
      .filter(fits)
      .sort((x, y) => Date.parse(x.completed_at ?? x.created_at) - Date.parse(y.completed_at ?? y.created_at))
      .slice(0, b.free.length)
    return (
      <>
        <CalendarPlus className="size-4 shrink-0 text-muted" />
        <span className="font-medium whitespace-nowrap">{title}</span>
        <span className="min-w-0 text-muted tabular-nums">
          {plural(b.free.length, "free slot")} · {fromNow ? "select renders or drag one in" : "drag a render onto one"}
        </span>
        {target && fromNow && oldest.length > 0 && (
          <button className={cta} onClick={() => (setUnplaced([]), setSel(new Set(oldest.map((r) => r.id))))}>
            Select {oldest.length} oldest
          </button>
        )}
      </>
    )
  }

  // ---- the header gives way only when it collides: first the year, then a long handle, then the page title
  const headRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(innerWidth)
  useEffect(() => {
    const f = () => setWidth(innerWidth)
    addEventListener("resize", f)
    return () => removeEventListener("resize", f)
  }, [])
  const fitKey = [width, start, lanes.length, a?.id, conflicts.length, failed.length, drafts.length].join("|") // what changes the header's width
  const [squeeze, setSqueeze] = useState({ key: "", level: 0 })
  const level = squeeze.key === fitKey ? squeeze.level : 0
  useLayoutEffect(() => {
    const el = headRef.current
    if (el && level < 3 && el.scrollWidth > el.clientWidth + 1) setSqueeze({ key: fitKey, level: level + 1 })
  }, [fitKey, level])

  const loading = accounts.isPending || (!!accounts.data && lanes.length > 0 && posts.isPending)
  const error = accounts.isError || posts.isError ? apiError(accounts.error ?? posts.error).message : ""
  const note = board && callout(board)
  const rangeLabel = `${dayLabel(days[0], { year: days[0].slice(0, 4) !== days[6].slice(0, 4) })} – ${dayLabel(days[6])}`

  return (
    <div className="flex min-h-0 flex-1">
      <div className="relative flex min-w-0 flex-1 flex-col">
        <Header>
          <div ref={headRef} className="flex min-w-0 items-center gap-3">
            <h1 className={cn("shrink-0 text-lg font-semibold", level >= 3 && "sr-only")}>Calendar</h1>
            {a && (
              <>
                <span className={cn("h-4 w-px shrink-0 bg-line", level >= 3 && "hidden")} />
                <Switcher lanes={lanes} active={a} boards={boards} failed={failed} drafts={drafts} narrow={level >= 2} onPick={(id) => setParam({ account: String(id) })} />
              </>
            )}
            <span className="h-4 w-px shrink-0 bg-line" />
            <div className="flex shrink-0 items-center">
              <button className={cn(btn.ghost, "text-fg")} disabled={start === todayDate} title="Back to today" onClick={() => setParam({ week: null })}>
                Today
              </button>
              <button className={btn.icon} aria-label="Previous 7 days" onClick={() => setParam({ week: addDays(start, -7) })}>
                <ChevronLeft className="size-4" />
              </button>
              <button className={btn.icon} aria-label="Next 7 days" onClick={() => setParam({ week: addDays(start, 7) })}>
                <ChevronRight className="size-4" />
              </button>
            </div>
            <span className="shrink-0 text-lg font-semibold whitespace-nowrap tabular-nums">
              {rangeLabel}
              {level < 1 && ` ${days[6].slice(0, 4)}`}
            </span>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {conflicts.length > 0 && (
              <button className={cn(btn.ghost, "text-fg")} onClick={jump} title="Show the first one">
                <TriangleAlert className="size-3.5 text-warn" />
                <span className="tabular-nums">{conflicts.length} too close</span>
              </button>
            )}
            {failed.length > 0 && <FailedLink failed={failed} tzOf={tzOf} />}
            {drafts.length > 0 && (
              <button className={cn(btn.secondary, "ml-1 font-normal")} disabled={busy} onClick={approveAll} title="Lists every draft before approving">
                <CheckCheck className="size-4 text-muted" />
                <span className="tabular-nums">Approve {plural(drafts.length, "draft")}</span>
              </button>
            )}
          </div>
        </Header>

        {error ? (
          <Empty>Couldn't load the calendar: {error}</Empty>
        ) : accounts.data && lanes.length === 0 ? (
          <Empty>
            <span>
              No accounts to schedule on.{" "}
              <Link to="/accounts" className="text-fg underline underline-offset-2">
                Connect one on the Accounts page
              </Link>
              .
            </span>
          </Empty>
        ) : loading || !board ? (
          <BoardSkeleton days={days} today={todayDate} times={[...(a?.posting_slots.times ?? [])].sort()} />
        ) : (
          <div className="min-h-0 flex-1 overflow-auto" onDragEnd={endDrag}>
            <div
              className="grid h-full" // definite: slot rows share the height, and overflow (scroll) only below their minimum
              onKeyDown={arrows}
              style={{
                gridTemplateColumns: "56px repeat(7, minmax(0, 1fr))",
                gridTemplateRows: ["40px", note ? "auto" : "", ...board.rows.map((r) => (r.band != null ? "auto" : size === "full" ? "minmax(184px, 1fr)" : "minmax(80px, 1fr)"))]
                  .filter(Boolean)
                  .join(" "),
              }}
            >
              <div className="sticky top-0 z-20 flex items-center justify-end bg-bg pr-2 text-xs text-dim" title={`${tz} (${utcOffset(tz, noon(days[0]))})`}>
                {tzName(tz, noon(days[0]))}
              </div>
              {days.map((d, i) => dayHead(board, d, i))}
              {note && (
                <>
                  <div className="border-t border-grid" />
                  <div data-callout className="col-span-7 flex min-h-11 min-w-0 items-center gap-2.5 border-t border-l border-grid px-3 py-2">
                    {note}
                  </div>
                </>
              )}
              {board.rows.map((row) => (
                <Fragment key={row.key}>
                  <div className="border-t border-grid pt-2.5 pr-2 text-right">
                    {row.slot ? <span className="text-sm text-dim tabular-nums">{row.slot}</span> : <span className="text-xs whitespace-nowrap text-dim">Off-slot</span>}
                  </div>
                  {days.map((d) => (
                    <div
                      key={d}
                      data-cell={`${d}|${row.key}`}
                      {...(row.slot ? cellTarget(board, d, row.slot) : {})}
                      // mid-drag an open slot's cell is its only hit target: the content swaps (preview -> drop target) under
                      // the pointer, and Chrome sends the drop to the element that got the last dragover, detached or not.
                      // Never on a cell with posts: one of them may be the drag source.
                      className={cn(
                        "@container min-w-0 border-t border-l border-grid p-1.5",
                        d === todayDate && "bg-today",
                        drag && row.slot && !board.cells.has(`${d}|${row.slot}`) && "[&_*]:pointer-events-none!"
                      )}
                    >
                      {row.slot ? slotCell(board, d, row.slot) : bandCell(board, d, row.band!)}
                    </div>
                  ))}
                </Fragment>
              ))}
            </div>
          </div>
        )}

        {notice && (
          <div
            role="status"
            className="absolute bottom-4 left-1/2 z-30 flex max-w-[min(640px,90%)] -translate-x-1/2 items-center gap-3 rounded-md border border-line-strong bg-raised py-2 pr-2 pl-3 shadow-[0_12px_28px_rgba(0,0,0,0.5)] transition-opacity duration-200 ease-out starting:opacity-0 motion-reduce:transition-none"
          >
            <span className={cn("min-w-0 truncate", notice.ok ? "text-fg" : "text-bad")} title={notice.text}>
              {notice.text}
            </span>
            <button className="grid size-6 shrink-0 place-items-center rounded text-muted hover:bg-hover hover:text-fg" aria-label="Dismiss" onClick={() => setNotice(null)}>
              <X className="size-3.5" />
            </button>
          </div>
        )}
      </div>

      <ScheduleTray
        loading={renders.isPending}
        error={renders.isError ? apiError(renders.error).message : undefined}
        groups={groups}
        clip={(id) => {
          const c = clipOf(id)
          return { name: nameOf(id), title: c ? (c.source_url ?? c.original_filename ?? undefined) : undefined }
        }}
        brand={(id) => (id == null ? "No logo" : (brandOf(id)?.name ?? `Brand ${id}`))}
        sel={sel}
        onSelect={select}
        dragging={drag && "render" in drag ? drag.render.id : null}
        onDrag={(r) => (r ? setDrag({ render: r }) : endDrag())}
        collapsed={collapsed}
        onCollapse={setCollapsed}
        free={target ? board?.free.length : undefined}
        next={target && next.data?.scheduled_for ? shortWhen(next.data.scheduled_for, target.timezone, now) : ""}
        chosen={chosen.length}
        canRun={!!target && chosen.length > 0 && !busy && !pending}
        running={busy}
        onRun={fill}
        footnote={footnote}
        unplaced={unplaced}
      />
      {drawerPost && drawerAccount && <PostDrawer key={drawerPost.id} p={drawerPost} a={drawerAccount} onClose={() => setOpen(null)} />}
    </div>
  )
}

/** One pill per account: the active one names itself (hover or focus: zone, slots, cap, gap); the others are an avatar
 * with what needs them (failed dot, draft count), the rest in their tooltip. */
function Switcher(props: {
  lanes: AccountOut[]
  active: AccountOut
  boards: { acc: AccountOut; free: string[] }[]
  failed: PostOut[]
  drafts: PostOut[]
  narrow: boolean // a crowded header: the active handle truncates sooner
  onPick: (id: number) => void
}) {
  const { active: a } = props
  const multi = props.lanes.length > 1
  return (
    <div className={cn("flex h-7 shrink-0 items-center gap-0.5", multi && "rounded-md border border-white/[0.08] bg-panel p-0.5")}>
      {props.lanes.map((x) => {
        if (x.id !== a.id) {
          const failed = props.failed.filter((p) => p.account_id === x.id).length
          const drafts = props.drafts.filter((p) => p.account_id === x.id).length
          const off = x.connection_status !== "connected"
          const free = props.boards.find((b) => b.acc.id === x.id)?.free.length ?? 0
          const label = `@${x.username}: ${[off && "disconnected", failed && `${failed} failed`, drafts && plural(drafts, "draft"), !off && `${free} free in view`].filter(Boolean).join(", ")}`
          return (
            <button
              key={x.id}
              aria-label={label}
              title={label}
              onClick={() => props.onPick(x.id)}
              className="relative flex h-6 shrink-0 items-center gap-1 rounded px-1 transition-colors duration-150 ease-out hover:bg-hover"
            >
              <Avatar a={x} className={cn("size-[18px] text-xs", off && "opacity-40 grayscale")} />
              {(failed > 0 || off) && <span className="absolute top-0.5 left-[17px] size-1.5 rounded-full bg-bad ring-2 ring-panel" />}
              {drafts > 0 && (
                <span className="flex items-center gap-0.5 text-xs text-muted tabular-nums">
                  <CircleDashed className="size-3" />
                  {drafts}
                </span>
              )}
            </button>
          )
        }
        const slots = a.posting_slots.times ?? []
        return (
          <HoverCard.Root key={x.id} openDelay={250} closeDelay={100}>
            <HoverCard.Trigger asChild>
              <Link
                to="/accounts"
                aria-current="true"
                className={cn("flex h-6 min-w-0 shrink-0 items-center gap-1.5 rounded pr-2 pl-1 transition-colors duration-150 ease-out hover:bg-hover", multi && "bg-raised")}
              >
                <Avatar a={a} className="size-5 text-xs" />
                <span className={cn("truncate font-medium whitespace-nowrap", props.narrow ? "max-w-24" : "max-w-40")}>@{a.username}</span>
              </Link>
            </HoverCard.Trigger>
            <HoverCard.Portal>
              <HoverCard.Content side="bottom" align="start" sideOffset={6} collisionPadding={8} className="z-50 w-72 space-y-2.5 rounded-md border border-line-strong bg-panel p-3 text-sm shadow-2xl">
                <div className="flex items-center gap-2">
                  <Avatar a={a} className="size-6 text-xs" />
                  <span className="min-w-0 flex-1 truncate text-base font-medium">@{a.username}</span>
                  <ConnChip a={a} />
                </div>
                <dl className="grid grid-cols-[48px_1fr] gap-x-2 gap-y-1 tabular-nums">
                  <dt className="text-dim">Zone</dt>
                  <dd className="truncate">
                    {a.timezone} · {utcOffset(a.timezone)}
                  </dd>
                  <dt className="text-dim">Slots</dt>
                  <dd>{slots.length ? slots.join(" · ") : "none"}</dd>
                  <dt className="text-dim">Cap</dt>
                  <dd>{a.daily_cap} a day</dd>
                  <dt className="text-dim">Gap</dt>
                  <dd>{a.min_gap_minutes} min apart</dd>
                </dl>
                <div className="flex items-center gap-1.5 text-muted">
                  <Settings2 className="size-3.5" />
                  Click to change them on Accounts
                </div>
              </HoverCard.Content>
            </HoverCard.Portal>
          </HoverCard.Root>
        )
      })}
    </div>
  )
}

/** "● 1 failed" opens its recovery page; several open a list grouped by account. */
function FailedLink({ failed, tzOf }: { failed: PostOut[]; tzOf: (accountId: number) => string }) {
  const cls = cn(btn.ghost, "text-fg")
  const label = (
    <>
      <span className="size-1.5 rounded-full bg-bad" />
      <span className="tabular-nums">{failed.length} failed</span>
    </>
  )
  if (failed.length === 1)
    return (
      <Link to={`/recover/${failed[0].id}`} className={cls} title="Open recovery">
        {label}
      </Link>
    )
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button className={cls}>{label}</button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="bottom" align="end" sideOffset={4} collisionPadding={8} className="z-50 max-h-80 w-80 overflow-auto rounded-md border border-line-strong bg-panel p-1 shadow-2xl">
          {failed.map((p) => (
            <Link key={p.id} to={`/recover/${p.id}`} className="flex h-10 items-center gap-2 rounded px-1.5 hover:bg-hover">
              <img src={p.render.thumbnail_url ?? ""} alt="" className="h-8 w-[18px] shrink-0 rounded-[2px] bg-raised object-cover" />
              <span className="shrink-0">@{p.account_username}</span>
              <span className="shrink-0 text-muted tabular-nums">{shortWhen(p.scheduled_for, tzOf(p.account_id))}</span>
              <span className="min-w-0 flex-1 truncate text-right font-mono text-xs text-bad">{p.error_code ?? p.status}</span>
            </Link>
          ))}
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}
