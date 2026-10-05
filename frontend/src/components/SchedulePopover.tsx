import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Popover } from "radix-ui"
import { useState, type ReactNode } from "react"
import { Link } from "react-router"

import type { PostMusic, PostOut, RenderOut } from "@/api"
import {
  approvePostMutation,
  listAccountsOptions,
  listAccountsQueryKey,
  listPostsQueryKey,
  listRendersQueryKey,
  nextSlotOptions,
  nextSlotQueryKey,
  statusOptions,
} from "@/api/@tanstack/react-query.gen"
import { MusicPicker } from "@/components/MusicPicker"
import { apiError, createPostAsking, localParts, nowIso, postNowConfirm, shortWhen, utcOffset, zonedToUtc } from "@/lib/schedule"
import { CAPTION_MAX, MAX_REEL_SECONDS, PUBLISHING_OFF, btn, cn, field, label } from "@/lib/utils"

/** Render queue "Schedule…": pick an account, take the suggested slot (or edit it), POST /api/posts. */
export function SchedulePopover({ r, children }: { r: RenderOut; children: ReactNode }) {
  return (
    <Popover.Root>
      <Popover.Trigger asChild>{children}</Popover.Trigger>
      <Popover.Portal>
        <Popover.Content side="bottom" align="start" sideOffset={4} collisionPadding={8} className="z-50 w-[300px] rounded-md border border-line-strong bg-panel p-3 text-fg shadow-2xl">
          <ScheduleForm r={r} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  )
}

function ScheduleForm({ r }: { r: RenderOut }) {
  const qc = useQueryClient()
  const accounts = useQuery(listAccountsOptions())
  const targets = (accounts.data ?? []).filter((a) => a.connection_status === "connected" && !a.disabled_at)
  const [picked, setPicked] = useState<number | null>(null)
  const a = targets.find((x) => x.id === picked) ?? targets[0]
  const next = useQuery({ ...nextSlotOptions({ path: { account_id: a?.id ?? 0 } }), enabled: !!a })
  const suggested = a && next.data?.scheduled_for ? localParts(next.data.scheduled_for, a.timezone) : { date: "", time: "" }
  const [when, setWhen] = useState<{ date: string; time: string } | null>(null) // null: follow the suggestion
  const { date, time } = when ?? suggested
  const [caption, setCaption] = useState(r.caption ?? "")
  const [music, setMusic] = useState<PostMusic | null>(null)
  const [error, setError] = useState("")
  const [done, setDone] = useState<PostOut | null>(null)
  const [now, setNow] = useState(false) // done came from Post now
  const create = useMutation({ mutationFn: createPostAsking })
  const approve = useMutation(approvePostMutation())
  const busy = create.isPending || approve.isPending
  const st = useQuery(statusOptions()) // the sidebar's query; shares its cache
  const long = (r.duration_s ?? 0) > MAX_REEL_SECONDS

  async function submit(now = false) {
    if (!a || (!now && (!date || !time))) return
    setError("")
    try {
      const scheduled_for = now ? nowIso() : zonedToUtc(date, time, a.timezone).toISOString()
      let post = await create.mutateAsync({ render_id: r.id, account_id: a.id, scheduled_for, caption, music })
      if (!post) return // the video is on the account already, and the answer was: don't post it again
      if (now && post.status === "DRAFT") post = await approve.mutateAsync({ path: { post_id: post.id } }) // Post now is the approval
      setNow(now)
      setDone(post)
    } catch (e) {
      setError(apiError(e).message)
    } finally {
      // also after a failure: the post may exist as a draft (created, approve refused)
      qc.invalidateQueries({ queryKey: listPostsQueryKey() })
      qc.invalidateQueries({ queryKey: listAccountsQueryKey() })
      qc.invalidateQueries({ queryKey: listRendersQueryKey() })
      qc.invalidateQueries({ queryKey: nextSlotQueryKey({ path: { account_id: a.id } }) })
    }
  }

  if (done && a)
    return (
      <div data-scheduled={done.id} className="space-y-2">
        {now ? (
          <p className="text-ok">Posting now on @{a.username}: live within about a minute.</p>
        ) : (
          <p className="text-ok">
            {done.status === "DRAFT" ? "Saved as a draft" : "Scheduled"} for <span className="tabular-nums">{shortWhen(done.scheduled_for, a.timezone)}</span> on @{a.username}.
          </p>
        )}
        {done.status === "DRAFT" && <p className="text-sm text-muted">{r.brand_id ? "This brand needs approval before it publishes." : "Posts without a brand start as drafts."} Approve it on the calendar.</p>}
        {st.data?.publishing_off && <p className="text-sm text-warn">{PUBLISHING_OFF[st.data.publishing_off].why}</p>}
        <Link to={`/calendar?week=${localParts(done.scheduled_for, a.timezone).date}&account=${a.id}`} className="text-sm underline decoration-line-strong underline-offset-2 hover:decoration-fg">
          Open calendar
        </Link>
      </div>
    )

  return (
    <div className="space-y-2.5">
      <div className="font-medium">Schedule render {r.id}</div>
      {long && <p className="text-sm text-warn">Instagram Reels can be at most {MAX_REEL_SECONDS / 60} min.</p>}
      {accounts.data && !targets.length && (
        <p className="text-sm text-muted">
          No connected account.{" "}
          <Link to="/accounts" className="underline underline-offset-2">
            Accounts
          </Link>
        </p>
      )}
      <fieldset disabled={!a || long || busy} className="space-y-2.5">
        <label className="block space-y-1">
          <span className={label}>Account</span>
          <select className={cn(field, "w-full")} value={a?.id ?? ""} onChange={(e) => (setPicked(Number(e.target.value)), setWhen(null))}>
            {targets.map((x) => (
              <option key={x.id} value={x.id}>
                @{x.username}
              </option>
            ))}
          </select>
        </label>
        <div className="space-y-1">
          <div className={label}>
            Time{" "}
            {a && (
              <span className="normal-case tracking-normal">
                · {a.timezone} {utcOffset(a.timezone)}
              </span>
            )}
          </div>
          <div className="flex gap-1.5">
            <input type="date" aria-label="Date" className={cn(field, "min-w-0 flex-1 tabular-nums")} value={date} onChange={(e) => setWhen({ date: e.target.value, time })} />
            <input type="time" aria-label="Time" className={cn(field, "w-[124px] tabular-nums")} value={time} onChange={(e) => setWhen({ date, time: e.target.value })} />
          </div>
          <div className="text-xs text-subtle">
            {!a ? " " : next.isPending ? "Finding the next free slot…" : next.isError ? `No suggestion: ${apiError(next.error).message}` : next.data && !next.data.scheduled_for ? "No free slot within 30 days" : when ? "Edited" : "Next free slot"}
          </div>
        </div>
        <label className="block space-y-1">
          <span className="flex justify-between">
            <span className={label}>Caption</span>
            <span className="text-xs tabular-nums text-subtle">
              {caption.length}/{CAPTION_MAX}
            </span>
          </span>
          <textarea rows={4} maxLength={CAPTION_MAX} className={cn(field, "h-auto w-full resize-none py-1.5")} value={caption} onChange={(e) => setCaption(e.target.value)} />
        </label>
        {st.data?.instagram_music && <MusicPicker accountId={a?.id} value={music} onChange={setMusic} />}
        {error && <p className="text-sm text-bad">{error}</p>}
        <div className="flex gap-1.5">
          <button className={cn(btn.primary, "flex-1")} disabled={!date || !time} onClick={() => submit()}>
            {busy ? "Scheduling…" : "Schedule"}
          </button>
          <button
            className={btn.secondary}
            disabled={st.data?.publishing_enabled === false}
            title={st.data?.publishing_enabled === false ? "Publishing is off" : "Skip the schedule: publish within about a minute"}
            onClick={() => a && confirm(postNowConfirm(a.username)) && submit(true)}
          >
            Post now
          </button>
        </div>
      </fieldset>
    </div>
  )
}
