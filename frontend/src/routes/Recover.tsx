import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronLeft, ChevronRight, Copy, Link2, Play, RefreshCw, RotateCw } from "lucide-react"
import { useState, type ReactNode } from "react"
import { Link, useParams } from "react-router"

import type { PostOut, PostRender, Remedy } from "@/api"
import { getPostOptions, getPostQueryKey, listAccountsOptions, listPostsQueryKey, nextSlotOptions, remedyPostMutation, statusQueryKey } from "@/api/@tanstack/react-query.gen"
import { ZERNIO_URL } from "@/components/AccountBits"
import { Chip } from "@/components/bits"
import { FAILED, STATUS_LABEL, apiError, shortWhen } from "@/lib/schedule"
import { cn, label, mmss, shortUrl } from "@/lib/utils"

const TONE: Partial<Record<PostOut["status"], "accent" | "ok" | "bad" | "warn">> = { DRAFT: "warn", SCHEDULED: "accent", PUBLISHING: "accent", PUBLISHED: "ok", FAILED: "bad", DEAD_LETTER: "bad" }
const HEADLINE: Record<PostOut["status"], string> = {
  DRAFT: "Waiting for approval",
  SCHEDULED: "Scheduled to publish",
  PUBLISHING: "Publishing now",
  PUBLISHED: "Live on Instagram",
  FAILED: "Publishing failed",
  DEAD_LETTER: "Publishing failed",
  CANCELLED: "Cancelled",
}
// Short titles for the failure; the plain-language sentence is PostOut.cause (app/services/errors.py).
const TITLES: Record<string, string> = {
  ACCOUNT_DISCONNECTED: "Account disconnected",
  CONTENT_REJECTED: "Instagram rejected the video",
  RATE_LIMITED: "Instagram rate limit reached",
  NETWORK_ERROR: "Couldn't reach Instagram",
  TOO_LONG: "Video too long for a Reel",
  RENDER_FAILED: "Render failed",
  WORKER_CRASHED: "Worker crashed while publishing",
  MISSED: "Missed its slot",
  NO_FREE_SLOT: "No free slot to move to",
  WINDOW_EXPIRED: "Outcome unknown",
  UNKNOWN: "Zernio reported a failure",
}
const DOES: Record<Remedy["action"], string> = {
  reconnect: "Reconnect the account in Zernio, then check here: its failed posts move to the next free slots. The next account sync does the same.",
  rerender: "Creates a fresh render and publishes it in the next free slot",
  retry: "Tries again now. If Zernio already has this post, it checks and retries that one, so Instagram never gets a duplicate",
  auto: "Nothing to do: Clipper moves it to the next free slot on its own",
}
const ICONS = { reconnect: Link2, rerender: RefreshCw, retry: RotateCw, auto: RotateCw }
const MAX_RESTARTS = 3 // backend publish.MAX_ORPHAN_REDEFERS, then DEAD_LETTER WORKER_CRASHED
// The Reel may already be live (publish.AMBIGUOUS): a re-render gets a new idempotency key, so ask first.
const MAYBE_LIVE = new Set(["NETWORK_ERROR", "WORKER_CRASHED", "WINDOW_EXPIRED"])

const browserTz = Intl.DateTimeFormat().resolvedOptions().timeZone
const inFlight = (p?: PostOut) => !!p && (p.status === "PUBLISHING" || (p.status === "SCHEDULED" && Date.parse(p.scheduled_for) < Date.now() + 120_000))

export function Recover() {
  const id = Number(useParams().postId)
  const qc = useQueryClient()
  const post = useQuery({ ...getPostOptions({ path: { post_id: id } }), enabled: id > 0, refetchInterval: (q) => (inFlight(q.state.data) ? 5000 : false) })
  const accounts = useQuery(listAccountsOptions())
  const p = post.data
  const account = accounts.data?.find((a) => a.id === p?.account_id)
  const tz = account?.timezone ?? browserTz
  const failed = !!p && FAILED.has(p.status)
  const fix = p?.error_code === "TOO_LONG" ? null : p?.remedy // nothing Clipper can do: the clip itself is too long
  const slot = useQuery({ ...nextSlotOptions({ path: { account_id: p?.account_id ?? 0 } }), enabled: failed && fix?.action === "rerender" })
  const [note, setNote] = useState("")
  const remedy = useMutation({
    ...remedyPostMutation(),
    onSuccess: (next) => {
      qc.setQueryData(getPostQueryKey({ path: { post_id: id } }), next)
      qc.invalidateQueries({ queryKey: listPostsQueryKey() })
      qc.invalidateQueries({ queryKey: statusQueryKey() })
      if (next.error_code === "ACCOUNT_DISCONNECTED" && FAILED.has(next.status)) setNote(`@${next.account_username} is still disconnected in Zernio.`)
    },
  })
  function apply() {
    setNote("")
    if (fix?.action === "rerender" && MAYBE_LIVE.has(p!.error_code ?? "") && !confirm(`Check @${p!.account_username} on Instagram first. This Reel may already be live; re-rendering posts it again.\n\nOK only if you checked and it is not there.`)) return
    remedy.mutate({ path: { post_id: id }, body: {} })
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-md flex-col">
      <header className="sticky top-0 z-10 flex h-12 shrink-0 items-center gap-1 border-b border-line bg-panel px-1.5">
        <Link to="/calendar" aria-label="Back to calendar" className="grid size-9 place-items-center rounded text-muted hover:bg-hover hover:text-fg">
          <ChevronLeft className="size-5" />
        </Link>
        <div className="size-4 rounded-sm bg-fg" />
        <span className="ml-1 text-md font-semibold tracking-tight">Clipper</span>
      </header>

      {!p ? (
        <p className="p-8 text-center text-muted">{post.isError ? `Couldn't load post ${id}: ${apiError(post.error).message}` : "Loading…"}</p>
      ) : (
        <main className="px-4 pt-4 pb-6">
          <div className="flex items-center gap-2">
            {TONE[p.status] ? <Chip tone={TONE[p.status]!}>{STATUS_LABEL[p.status]}</Chip> : <span className="inline-flex h-5 items-center rounded bg-raised px-1.5 text-xs font-medium text-muted">{STATUS_LABEL[p.status]}</span>}
            <h1 className="text-lg font-semibold">{failed ? (TITLES[p.error_code ?? ""] ?? HEADLINE[p.status]) : HEADLINE[p.status]}</h1>
          </div>

          <div className="mt-4 flex flex-col items-center">
            <Player r={p.render} />
            <div className="mt-3 flex items-center gap-1.5">
              {account?.avatar_url && <img src={account.avatar_url} alt="" className="size-5 rounded-full" />}
              <span className="text-md font-medium">@{p.account_username}</span>
            </div>
            <div className="mt-1 text-sm tabular-nums text-muted">
              {p.published_at ? "Published" : "Scheduled"} {shortWhen(p.published_at ?? p.scheduled_for, tz)} <span className="text-subtle">({tz})</span>
            </div>
            <div className="mt-0.5 max-w-full truncate text-center text-sm text-subtle" title={p.render.clip_name ?? undefined}>
              {[p.render.brand_name ?? "No logo", p.render.clip_name && shortUrl(p.render.clip_name)].filter(Boolean).join(" · ")}
            </div>
          </div>

          {failed ? (
            <>
              {p.cause && <p className="mt-5 text-md">{p.cause}</p>}
              {fix && <RemedyButton remedy={fix} busy={remedy.isPending} onClick={apply} />}
              {fix && (
                <p className="mt-2 text-center text-sm tabular-nums text-muted">
                  {DOES[fix.action]}
                  {fix.action === "rerender" && slot.data?.scheduled_for && <span className="whitespace-nowrap"> ({shortWhen(slot.data.scheduled_for, tz)})</span>}
                </p>
              )}
              {note && <p className="mt-2 text-center text-sm text-warn">{note}</p>}
              {remedy.isError && <p className="mt-2 text-center text-sm text-bad">{apiError(remedy.error).message}</p>}
            </>
          ) : (
            <div className="mt-5 flex flex-col items-center gap-2 text-center">
              {p.permalink && (
                <a href={p.permalink} target="_blank" rel="noreferrer" className="text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg">
                  View on Instagram
                </a>
              )}
              <Link to="/calendar" className="text-muted hover:text-fg hover:underline">
                Open the calendar
              </Link>
            </div>
          )}

          <Technical p={p} />
        </main>
      )}
    </div>
  )
}

/** Reconnect is two steps: open Zernio, then (after reconnecting there) ask the backend to sync. */
function RemedyButton({ remedy, busy, onClick }: { remedy: Remedy; busy: boolean; onClick: () => void }) {
  const [opened, setOpened] = useState(false)
  const Icon = ICONS[remedy.action]
  const reconnect = remedy.action === "reconnect"
  const cls = "mt-4 flex h-12 w-full items-center justify-center gap-2 rounded text-md font-medium disabled:opacity-50"
  return (
    <>
      {reconnect && (
        <a
          href={ZERNIO_URL}
          target="_blank"
          rel="noreferrer"
          onClick={() => setOpened(true)}
          className={cn(cls, opened ? "border border-line-strong bg-raised text-fg hover:bg-hover" : "bg-accent text-white hover:bg-accent-hover")}
        >
          <Icon className="size-4" />
          {remedy.label} in Zernio
        </a>
      )}
      {(!reconnect || opened) && (
        <button disabled={busy || remedy.action === "auto"} onClick={onClick} className={cn(cls, "bg-accent text-white hover:bg-accent-hover", reconnect && "mt-2")}>
          {reconnect ? <RefreshCw className={cn("size-4", busy && "animate-spin")} /> : <Icon className={cn("size-4", busy && "animate-spin")} />}
          {reconnect ? "I've reconnected: check now" : remedy.label}
        </button>
      )}
    </>
  )
}

function Technical({ p }: { p: PostOut }) {
  const raw = p.error_detail ? JSON.stringify(p.error_detail, null, 2) : ""
  const row = (k: string, v: ReactNode) => (
    <>
      <dt className={cn(label, "flex h-9 items-center")}>{k}</dt>
      <dd className="flex h-9 items-center justify-between font-mono text-sm">{v}</dd>
    </>
  )
  return (
    <details className="group mt-5 border-y border-line open:pb-4">
      <summary className="flex h-11 cursor-pointer list-none items-center gap-1 text-muted">
        Technical details
        <ChevronRight className="size-3.5 group-open:rotate-90" />
      </summary>
      <dl className="grid grid-cols-[88px_1fr]">
        {row("Error code", p.error_code ? <span className="inline-flex h-5 items-center rounded border border-line bg-raised px-1.5">{p.error_code}</span> : <span className="text-subtle">—</span>)}
        {row("Post", <>{p.id}<CopyButton text={String(p.id)} what="post id" /></>)}
        {row("Render", <>{p.render_id}<CopyButton text={String(p.render_id)} what="render id" /></>)}
        {p.zernio_post_id && row("Zernio", <><span className="truncate">{p.zernio_post_id}</span><CopyButton text={p.zernio_post_id} what="Zernio post id" /></>)}
        {row("Restarts", <span className="font-sans tabular-nums" title="Worker restarts in the middle of publishing; Clipper gives up after the last one">{p.attempt_count} / {MAX_RESTARTS}</span>)}
      </dl>
      <div className="mt-3 rounded border border-line bg-panel">
        <div className="flex h-10 items-center justify-between border-b border-line pr-0.5 pl-3">
          <span className={label}>Raw payload</span>
          {raw && <CopyButton text={raw} what="payload" label="Copy" />}
        </div>
        <pre className="max-h-[264px] overflow-auto p-3 font-mono text-xs break-all whitespace-pre-wrap">{raw || "No error payload."}</pre>
      </div>
    </details>
  )
}

function CopyButton({ text, what, label }: { text: string; what: string; label?: string }) {
  const [done, setDone] = useState(false)
  const copy = () => navigator.clipboard.writeText(text).then(() => (setDone(true), setTimeout(() => setDone(false), 1200)))
  return (
    <button onClick={copy} aria-label={done ? "Copied" : `Copy ${what}`} className="flex h-9 min-w-9 items-center justify-center gap-1.5 rounded px-1.5 font-sans text-sm text-subtle hover:bg-hover hover:text-fg">
      <Copy className="size-3.5" />
      {done ? "Copied" : label}
    </button>
  )
}

/** The render's thumbnail; a tap swaps in the playable output (no download until then). */
function Player({ r }: { r: PostRender }) {
  const [play, setPlay] = useState(false)
  const box = "relative h-[341px] w-[192px] overflow-hidden rounded-md border border-line bg-raised"
  if (play && r.output_url) return <video src={r.output_url} controls autoPlay playsInline className={cn(box, "object-cover")} />
  return (
    <button className={cn(box, "group")} disabled={!r.output_url} onClick={() => setPlay(true)} aria-label="Play the render">
      {r.thumbnail_url && <img src={r.thumbnail_url} alt="" className="size-full object-cover" />}
      {r.output_url && (
        <span className="absolute top-1/2 left-1/2 grid size-10 -translate-1/2 place-items-center rounded-full bg-black/50 text-white group-hover:bg-black/70">
          <Play className="size-4 fill-current" />
        </span>
      )}
      <span className="absolute right-1.5 bottom-1.5 rounded-sm bg-black/75 px-1 text-xs tabular-nums text-white">{mmss(r.duration_s)}</span>
    </button>
  )
}
