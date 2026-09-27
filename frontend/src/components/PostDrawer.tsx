import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, X } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router"

import type { AccountOut, PostOut } from "@/api"
import { approvePostMutation, cancelPostMutation, getPostQueryKey, listAccountsQueryKey, listPostsQueryKey, listRendersQueryKey, updatePostMutation } from "@/api/@tanstack/react-query.gen"
import { Avatar } from "@/components/AccountBits"
import { Drawer } from "@/components/bits"
import { FAILED, MOVABLE, STATUS_LABEL, apiError, dayLabel, isHHMM, localParts, postAt, shortWhen, slotTime, utcOffset, zonedToUtc } from "@/lib/schedule"
import { CAPTION_MAX, btn, cn, field, label, mmss, shortUrl } from "@/lib/utils"

export function PostDrawer({ p, a, onClose }: { p: PostOut; a: AccountOut; onClose: () => void }) {
  const qc = useQueryClient()
  const at = localParts(p.scheduled_for, a.timezone)
  const [caption, setCaption] = useState(p.caption)
  const [date, setDate] = useState(at.date)
  const [time, setTime] = useState(at.time)
  // Re-seed the fields when the post changes under the drawer (approve moved it, a drag, the 30 s refetch).
  const seed = `${p.scheduled_for}|${p.status}|${p.caption}`
  const [seen, setSeen] = useState(seed)
  if (seen !== seed) {
    setSeen(seed)
    setCaption(p.caption)
    setDate(at.date)
    setTime(at.time)
  }
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null)
  const done = (text: string) => (next: PostOut) => {
    const moved = Date.parse(next.scheduled_for) !== Date.parse(p.scheduled_for) && MOVABLE.has(next.status)
    setMsg({ ok: true, text: moved ? `${text}, moved to ${shortWhen(next.scheduled_for, a.timezone)}` : text })
    qc.setQueryData(getPostQueryKey({ path: { post_id: p.id } }), next)
    qc.invalidateQueries({ queryKey: listPostsQueryKey() })
    qc.invalidateQueries({ queryKey: listAccountsQueryKey() })
    qc.invalidateQueries({ queryKey: listRendersQueryKey() }) // a cancelled post's render is back in the tray
  }
  const onError = (e: unknown) => {
    const { code, message } = apiError(e)
    setMsg({ ok: false, text: code === "STATE_CONFLICT" ? `This post changed state elsewhere (${message}). Reloaded.` : message })
    qc.invalidateQueries({ queryKey: listPostsQueryKey() })
  }
  const update = useMutation({ ...updatePostMutation(), onSuccess: done("Saved"), onError })
  const approve = useMutation({ ...approvePostMutation(), onSuccess: done("Approved"), onError })
  const cancel = useMutation({ ...cancelPostMutation(), onSuccess: done("Cancelled"), onError })
  const busy = update.isPending || approve.isPending || cancel.isPending

  const editable = MOVABLE.has(p.status)
  const moved = date !== at.date || time !== at.time
  const dirty = moved || caption !== p.caption
  function save() {
    setMsg(null)
    update.mutate({
      path: { post_id: p.id },
      body: { ...(moved && { scheduled_for: zonedToUtc(date, time, a.timezone).toISOString() }), ...(caption !== p.caption && { caption }) },
    })
  }

  return (
    <Drawer label={`Post ${p.id}`} onClose={onClose} className="w-[380px]">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="truncate text-md font-semibold">{STATUS_LABEL[p.status]} post</h2>
          <span className="font-mono text-xs text-subtle">post {p.id}</span>
        </div>
        <button className={btn.icon} aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto px-4 py-3">
        <div className="flex gap-3">
          <video src={p.render.output_url ?? undefined} poster={p.render.thumbnail_url ?? undefined} controls preload="none" className="h-[213px] w-[120px] shrink-0 rounded bg-raised object-cover" />
          <div className="min-w-0 space-y-1.5 text-sm text-muted">
            <div className="flex items-center gap-2 text-base text-fg">
              <Avatar a={a} className="size-5 text-xs" />@{a.username}
            </div>
            <div className="truncate" title={p.render.clip_name ?? undefined}>
              {shortUrl(p.render.clip_name ?? `Clip ${p.render.clip_id}`)}
            </div>
            <div>{p.render.brand_name ?? "No logo"}</div>
            <div className="tabular-nums">{mmss(p.render.duration_s)}</div>
            {p.permalink && (
              <a href={p.permalink} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-fg underline decoration-line-strong underline-offset-2">
                View on Instagram
                <ArrowUpRight className="size-3.5" />
              </a>
            )}
            {FAILED.has(p.status) && (
              <Link to={`/recover/${p.id}`} className="block text-bad underline underline-offset-2">
                Open recovery
              </Link>
            )}
          </div>
        </div>

        {editable ? (
          <fieldset disabled={busy} className="space-y-4">
            <div className="space-y-1.5">
              <div className={label}>
                Time <span className="normal-case tracking-normal">
                  · {a.timezone} {utcOffset(a.timezone, Date.parse(p.scheduled_for))}
                </span>
              </div>
              <div className="flex gap-2">
                <input type="date" aria-label="Date" className={cn(field, "tabular-nums")} value={date} onChange={(e) => setDate(e.target.value)} />
                {/* text, not type=time: that one follows the browser locale (01:00 PM); the app is 24 h */}
                <input aria-label="Time" placeholder="HH:MM" maxLength={5} className={cn(field, "w-20 tabular-nums", !isHHMM(time) && "border-bad")} value={time} onChange={(e) => setTime(e.target.value)} />
                {/^\d{4}-\d\d-\d\d$/.test(date) && <span className="self-center text-muted tabular-nums">{dayLabel(date, { weekday: true })}</span>}
              </div>
            </div>
            <div className="space-y-1.5">
              <div className="flex justify-between">
                <span className={label}>Caption</span>
                <span className="text-xs tabular-nums text-subtle">
                  {caption.length}/{CAPTION_MAX}
                </span>
              </div>
              <textarea
                aria-label="Caption"
                rows={8}
                maxLength={CAPTION_MAX}
                className={cn(field, "h-auto w-full resize-none py-1.5 leading-[18px]")}
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
              />
            </div>
          </fieldset>
        ) : (
          <>
            <div className="space-y-1.5">
              <div className={label}>{p.status === "PUBLISHED" ? "Published" : "Time"}</div>
              <div className="tabular-nums">
                {shortWhen(postAt(p), a.timezone)}
                {p.published_at && localParts(p.published_at, a.timezone).time !== slotTime(p, a.timezone) && <span className="text-muted"> (slot {slotTime(p, a.timezone)})</span>}{" "}
                <span className="text-subtle">
                  {a.timezone} {utcOffset(a.timezone, Date.parse(postAt(p)))}
                </span>
              </div>
            </div>
            <div className="space-y-1.5">
              <div className={label}>Caption</div>
              {p.caption ? <p className="break-words whitespace-pre-wrap">{p.caption}</p> : <p className="text-subtle">No caption</p>}
            </div>
          </>
        )}
        {msg && <p className={cn("text-sm", msg.ok ? "text-ok" : "text-bad")}>{msg.text}</p>}
      </div>
      {(editable || FAILED.has(p.status)) && (
        <div className="flex h-12 shrink-0 items-center gap-2 border-t border-line px-4">
          {editable && (
            <button className={btn.secondary} disabled={!dirty || busy || !date || !isHHMM(time)} onClick={save}>
              Save
            </button>
          )}
          {p.status === "DRAFT" && (
            <button className={btn.primary} disabled={busy || dirty} title={dirty ? "Save changes first" : undefined} onClick={() => (setMsg(null), approve.mutate({ path: { post_id: p.id } }))}>
              Approve
            </button>
          )}
          <button
            className={cn(btn.ghost, "ml-auto hover:text-bad")}
            disabled={busy}
            onClick={() => confirm("Cancel this post? It won't be published.") && (setMsg(null), cancel.mutate({ path: { post_id: p.id } }))}
          >
            Cancel post
          </button>
        </div>
      )}
    </Drawer>
  )
}
