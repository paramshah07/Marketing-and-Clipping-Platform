import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, Maximize2, Music2, Pause, Play, Volume2, VolumeX, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { Link } from "react-router"

import type { AccountOut, PostOut } from "@/api"
import { approvePostMutation, cancelPostMutation, getPostQueryKey, listAccountsQueryKey, listPostsQueryKey, listRendersQueryKey, statusOptions, updatePostMutation } from "@/api/@tanstack/react-query.gen"
import { Avatar } from "@/components/AccountBits"
import { MusicPicker } from "@/components/MusicPicker"
import { Drawer } from "@/components/bits"
import { Slider } from "@/components/ui/slider"
import { FAILED, MOVABLE, STATUS_LABEL, apiError, dayLabel, isHHMM, localParts, nowIso, postAt, postNowConfirm, shortWhen, slotTime, utcOffset, zonedToUtc } from "@/lib/schedule"
import { CAPTION_MAX, btn, cn, field, label, mmss, shortUrl } from "@/lib/utils"

export function PostDrawer({ p, a, onClose }: { p: PostOut; a: AccountOut; onClose: () => void }) {
  const qc = useQueryClient()
  const at = localParts(p.scheduled_for, a.timezone)
  const [caption, setCaption] = useState(p.caption)
  const [music, setMusic] = useState(p.music)
  const [date, setDate] = useState(at.date)
  const [time, setTime] = useState(at.time)
  // Re-seed the fields when the post changes under the drawer (approve moved it, a drag, the 30 s refetch).
  const seed = `${p.scheduled_for}|${p.status}|${p.caption}|${JSON.stringify(p.music)}`
  const [seen, setSeen] = useState(seed)
  if (seen !== seed) {
    setSeen(seed)
    setCaption(p.caption)
    setMusic(p.music)
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
  const st = useQuery(statusOptions()) // the sidebar's query; shares its cache

  const editable = MOVABLE.has(p.status)
  const moved = date !== at.date || time !== at.time
  const retuned = JSON.stringify(music) !== JSON.stringify(p.music)
  const dirty = moved || caption !== p.caption || retuned
  function save() {
    setMsg(null)
    update.mutate({
      path: { post_id: p.id },
      body: { ...(moved && { scheduled_for: zonedToUtc(date, time, a.timezone).toISOString() }), ...(caption !== p.caption && { caption }), ...(retuned && { music }) },
    })
  }

  async function postNow() {
    if (!confirm(postNowConfirm(a.username))) return
    setMsg(null)
    try {
      await update.mutateAsync({ path: { post_id: p.id }, body: { scheduled_for: nowIso() } })
      if (p.status === "DRAFT") await approve.mutateAsync({ path: { post_id: p.id } }) // Post now is the approval
      setMsg({ ok: true, text: "Posting now: live within about a minute" })
    } catch {
      // onError has shown it
    }
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
          <ReelPlayer r={p.render} />
          <div className="min-w-0 space-y-1.5 text-sm text-muted">
            <div className="flex items-center gap-2 text-base text-fg">
              <Avatar a={a} className="size-5 text-xs" />@{a.username}
            </div>
            <div className="truncate" title={p.render.clip_name ?? undefined}>
              {shortUrl(p.render.clip_name ?? `Clip ${p.render.clip_id}`)}
            </div>
            <div>{p.render.brand_name ?? "No logo"}</div>
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
            {st.data?.instagram_music && <MusicPicker accountId={a.id} value={music} onChange={setMusic} />}
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
            {p.music && (
              <div className="space-y-1.5">
                <div className={label}>Music</div>
                <div className="flex min-w-0 items-center gap-1.5">
                  <Music2 className="size-3.5 shrink-0 text-muted" />
                  <span className="truncate">{p.music.title ?? "Instagram audio"}</span>
                  {p.music.artist && <span className="truncate text-muted">· {p.music.artist}</span>}
                </div>
              </div>
            )}
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
          {editable && (
            <button
              className={btn.secondary}
              disabled={busy || dirty || st.data?.publishing_enabled === false}
              title={st.data?.publishing_enabled === false ? "Publishing is off" : dirty ? "Save changes first" : "Skip the schedule: publish within about a minute"}
              onClick={postNow}
            >
              Post now
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

/** The render, played in place like a Reel: a click anywhere plays or pauses (sound on, looping), the strip
 * seeks and mutes, the corner button goes fullscreen, where the browser's own controls take over.
 * Nothing downloads before the first play. */
function ReelPlayer({ r }: { r: PostOut["render"] }) {
  const video = useRef<HTMLVideoElement>(null)
  const [started, setStarted] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [t, setT] = useState(0)
  const [dur, setDur] = useState(r.duration_s ?? 0)
  const [full, setFull] = useState(false)
  const [broken, setBroken] = useState(false)
  useEffect(() => {
    const on = () => setFull(document.fullscreenElement === video.current)
    document.addEventListener("fullscreenchange", on)
    return () => document.removeEventListener("fullscreenchange", on)
  }, [])
  const tool = "inline-grid size-6 place-items-center rounded text-white/80 hover:bg-white/15 hover:text-white"
  return (
    <div className="group relative h-[256px] w-[144px] shrink-0 overflow-hidden rounded bg-raised">
      <video
        ref={video}
        src={r.output_url ?? undefined}
        poster={r.thumbnail_url ?? undefined}
        preload="none"
        loop
        playsInline
        muted={muted}
        controls={full}
        className="size-full object-cover [&:fullscreen]:object-contain"
        onPlay={() => (setPlaying(true), setStarted(true))}
        onPause={() => setPlaying(false)}
        onTimeUpdate={(e) => setT(e.currentTarget.currentTime)}
        onDurationChange={(e) => Number.isFinite(e.currentTarget.duration) && setDur(e.currentTarget.duration)}
        onVolumeChange={(e) => setMuted(e.currentTarget.muted)} // muted in fullscreen stays muted here
        onError={() => setBroken(true)}
      />
      {broken ? (
        <div className="absolute inset-x-0 bottom-0 bg-black/75 px-2 py-1.5 text-xs text-white/80">Can't play this render in the browser.</div>
      ) : (
        <>
          <button
            aria-label={playing ? "Pause" : "Play"}
            disabled={!r.output_url}
            className="absolute inset-0 grid place-items-center"
            onClick={() => (video.current?.paused ? video.current.play() : video.current?.pause())}
          >
            {/* playing: out of the picture's way until the pointer is over it */}
            <span className={cn("grid size-10 place-items-center rounded-full bg-black/50 text-white", playing && "opacity-0 group-hover:opacity-100")}>
              {playing ? <Pause className="size-4 fill-current" /> : <Play className="size-4 fill-current" />}
            </span>
          </button>
          {started ? (
            <div className="absolute inset-x-0 bottom-0 bg-black/60 px-1.5 pt-0.5">
              <Slider aria-label="Seek" min={0} max={dur || 1} step={0.01} value={[t]} onValueChange={([v]) => video.current && (video.current.currentTime = v)} />
              <div className="flex items-center text-xs tabular-nums text-white/80">
                <span>
                  <span className="text-white">{mmss(t)}</span> / {mmss(dur)}
                </span>
                <button aria-label={muted ? "Unmute" : "Mute"} className={cn(tool, "ml-auto")} onClick={() => setMuted(!muted)}>
                  {muted ? <VolumeX className="size-3.5" /> : <Volume2 className="size-3.5" />}
                </button>
                <button aria-label="Fullscreen" className={tool} onClick={() => video.current?.requestFullscreen()}>
                  <Maximize2 className="size-3" />
                </button>
              </div>
            </div>
          ) : (
            <span className="pointer-events-none absolute right-1.5 bottom-1.5 rounded-sm bg-black/75 px-1 text-xs tabular-nums text-white">{mmss(r.duration_s)}</span>
          )}
        </>
      )}
    </div>
  )
}
