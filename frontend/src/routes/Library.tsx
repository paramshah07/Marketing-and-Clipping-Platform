import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CloudUpload, FileVideo, Globe, HardDriveUpload, Link2, RotateCw, Search, Trash2, Upload as UploadIcon } from "lucide-react"
import { useEffect, useRef, useState, useSyncExternalStore, type DragEvent, type ReactNode } from "react"
import { Link, useSearchParams } from "react-router"

import type { ClipOut } from "@/api"
import {
  createClipFromUrlMutation,
  deleteClipMutation,
  listClipsOptions,
  listClipsQueryKey,
  listRendersOptions,
  retryClipMutation,
  updateClipMutation,
} from "@/api/@tanstack/react-query.gen"
import { Chip, Empty, Header } from "@/components/bits"
import { MAX_UPLOAD_BYTES, ago, clipName, cn, errorText, mb, mmss, RIGHTS, btn, field, type Rights } from "@/lib/utils"

const EXTENSIONS = ["mp4", "mov", "webm"]
const TERMINAL = new Set(["READY", "FAILED"])
// ponytail: an UPLOADING row older than 10 min is an orphan (api killed mid-upload, swept in Phase 4); our own
// uploads refresh the list when their POST returns, so it is not polled for.
const polled = (c: ClipOut) => !TERMINAL.has(c.status) && (c.status !== "UPLOADING" || Date.now() - Date.parse(c.created_at) < 600_000)
const FINAL = new Set(["PRIVATE", "REMOVED", "GEO_BLOCKED", "DURATION_OUT_OF_RANGE", "PROBE_FAILED", "UPLOAD_ABANDONED"])
const CAUSES: Record<string, string> = {
  PRIVATE: "Private video",
  REMOVED: "Video removed",
  GEO_BLOCKED: "Blocked in this region",
  EXTRACTOR_FAILED: "Import failed",
  DURATION_OUT_OF_RANGE: "Must be 3 s to 15 min",
  PROBE_FAILED: "Not a readable video",
  THUMBNAIL_FAILED: "Thumbnail failed",
  WORKER_CRASHED: "Worker crashed",
  INTERRUPTED: "Interrupted",
  INTERNAL_ERROR: "Internal error",
  UPLOAD_ABANDONED: "Upload abandoned",
}

/** A file on its way up; the server row replaces it when the POST returns. */
type Upload = { key: string; file: File; rights: Rights; handle: string; loaded: number; rate: number; error?: string; fatal?: boolean; xhr?: XMLHttpRequest }

// Module state, not component state: switching tabs or pages keeps the rows with their progress, Cancel and
// Retry (the XHRs run on regardless). A reload loses them, hence the beforeunload prompt while any is in flight.
let uploads: Upload[] = []
const subs = new Set<() => void>()
const setUploads = (f: (us: Upload[]) => Upload[]) => ((uploads = f(uploads)), subs.forEach((s) => s()))
const subscribe = (s: () => void) => (subs.add(s), () => void subs.delete(s))
const getUploads = () => uploads
const set = (key: string, p: Partial<Upload>) => setUploads((us) => us.map((u) => (u.key === key ? { ...u, ...p } : u)))
const drop = (key: string) => setUploads((us) => us.filter((u) => u.key !== key))
addEventListener("beforeunload", (e) => uploads.some((u) => !u.error) && e.preventDefault())

function upload(file: File, rights: Rights, handle: string, refresh: () => Promise<unknown>, key: string = crypto.randomUUID()) {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? ""
  // checked here because the api's early 415/413 can reach a browser as a bare network error
  const bad = !EXTENSIONS.includes(ext)
    ? "Not mp4, mov or webm"
    : !file.size
      ? "Empty file"
      : file.size > MAX_UPLOAD_BYTES
        ? `Too large: ${(file.size / 1024 ** 3).toFixed(1)} GB (max 2 GB)`
        : undefined
  const row: Upload = { key, file, rights, handle, loaded: 0, rate: 0, error: bad, fatal: !!bad }
  setUploads((us) => (us.some((u) => u.key === key) ? us.map((u) => (u.key === key ? row : u)) : [row, ...us]))
  if (bad) return
  const form = new FormData()
  form.append("rights_status", rights)
  if (handle) form.append("source_creator_handle", handle)
  form.append("file", file)
  const xhr = new XMLHttpRequest()
  const t0 = performance.now()
  xhr.upload.onprogress = (e) => set(key, { loaded: e.loaded, rate: e.loaded / Math.max(0.001, (performance.now() - t0) / 1000) })
  xhr.onload = async () => {
    if (xhr.status === 201) {
      await refresh() // the server row replaces this one without a flash
      drop(key)
    } else {
      let body: unknown = xhr.responseText
      try {
        body = JSON.parse(xhr.responseText)
      } catch {
        /* not JSON */
      }
      set(key, { error: `${errorText(body)} (HTTP ${xhr.status})`, fatal: xhr.status === 413 || xhr.status === 415 })
    }
  }
  xhr.onerror = () => set(key, { error: "Upload interrupted — network" })
  xhr.onabort = () => drop(key)
  xhr.open("POST", "/api/clips")
  xhr.send(form)
  set(key, { xhr })
}

const size = (n: number) => (n < 1e6 ? `${Math.ceil(n / 1e3)} KB` : mb(n))

export function Library() {
  const [params, setParams] = useSearchParams()
  const published = params.get("tab") === "published"
  const [search, setSearch] = useState("")
  const searchRef = useRef<HTMLInputElement>(null)
  const pickRef = useRef<HTMLInputElement>(null)
  const clips = useQuery({
    ...listClipsOptions(),
    refetchInterval: (q) => (q.state.data?.some(polled) ? 2000 : false),
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "/" && !(e.target as HTMLElement).closest("input, textarea, select")) {
        e.preventDefault()
        searchRef.current?.focus()
      }
    }
    addEventListener("keydown", onKey)
    return () => removeEventListener("keydown", onKey)
  }, [])

  const tab = (on: boolean) =>
    cn("flex h-full items-center gap-1.5 rounded-sm px-2.5", on ? "bg-raised font-medium text-fg" : "text-muted hover:text-fg")

  return (
    <>
      <Header>
        <div className="flex items-center gap-4">
          <h1 className="text-lg font-semibold">Library</h1>
          <div className="flex h-7 items-center rounded border border-line bg-panel p-0.5">
            <button className={tab(!published)} aria-pressed={!published} onClick={() => setParams({})}>
              Clips<span className="text-sm tabular-nums text-muted">{clips.data?.length ?? ""}</span>
            </button>
            <button className={tab(published)} aria-pressed={published} onClick={() => setParams({ tab: "published" })}>
              Published<span className="text-sm tabular-nums text-subtle">0</span>
            </button>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <label hidden={published} className="flex h-7 w-60 items-center gap-2 rounded border border-line bg-panel px-2 text-muted focus-within:border-muted">
            <Search className="size-3.5 text-subtle" />
            <input
              ref={searchRef}
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="min-w-0 flex-1 bg-transparent text-base text-fg outline-none placeholder:text-subtle"
              placeholder="Search clips, creators, URLs"
            />
            <kbd className="grid h-4 min-w-4 place-items-center rounded-sm border border-line-strong px-1 font-sans text-xs text-subtle">/</kbd>
          </label>
          <button className={cn(btn.primary, "pl-2")} onClick={() => (setParams({}), pickRef.current?.click())}>
            <UploadIcon className="size-3.5" />
            Upload
          </button>
        </div>
      </Header>
      {published && <Empty>Published Reels show up here once publishing lands (Phase 5).</Empty>}
      {/* stays mounted on Published: the header's Upload button uses its file input */}
      <div hidden={published} className="contents">
        <Clips clips={clips.data} loading={clips.isPending} error={clips.isError ? errorText(clips.error) : ""} search={search} pickRef={pickRef} />
      </div>
    </>
  )
}

function Clips(props: { clips?: ClipOut[]; loading: boolean; error: string; search: string; pickRef: React.RefObject<HTMLInputElement | null> }) {
  const { clips, loading, search, pickRef } = props
  const qc = useQueryClient()
  const uploads = useSyncExternalStore(subscribe, getUploads)
  const [rights, setRights] = useState<Rights>("own_content")
  const [handle, setHandle] = useState("")
  const [url, setUrl] = useState("")
  const [over, setOver] = useState(false)
  const [notice, setNotice] = useState("")
  const renders = useQuery(listRendersOptions())
  const refresh = () => qc.invalidateQueries({ queryKey: listClipsQueryKey() })
  const onError = (e: unknown) => setNotice(errorText(e))
  const fromUrl = useMutation({ ...createClipFromUrlMutation(), onSuccess: () => (setUrl(""), setHandle(""), refresh()), onError })
  const remove = useMutation({ ...deleteClipMutation(), onSuccess: refresh, onError })
  const retry = useMutation({ ...retryClipMutation(), onSuccess: refresh, onError })
  const patch = useMutation({ ...updateClipMutation(), onSuccess: refresh, onError })

  const addFiles = (files: FileList | null) => [...(files ?? [])].forEach((f) => upload(f, rights, handle.trim(), refresh))
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setOver(false)
    addFiles(e.dataTransfer.files)
  }

  // The api creates an UPLOADING row as soon as a POST starts; hide the newest ones our own uploads stand for.
  // ponytail: matched by count, not id (the id only arrives with the response).
  let hide = uploads.filter((u) => !u.error).length
  const q = search.trim().toLowerCase()
  const rows = (clips ?? [])
    .filter((c) => !(c.status === "UPLOADING" && hide-- > 0))
    .filter((c) => !q || [clipName(c), c.source_creator_handle, c.source_url].some((s) => s?.toLowerCase().includes(q)))
  const counts = new Map<number, number>()
  renders.data?.forEach((r) => counts.set(r.source_clip_id, (counts.get(r.source_clip_id) ?? 0) + 1))

  return (
    <>
      <div className="relative shrink-0 border-b border-line px-4 py-2">
        <div
          data-testid="dropzone"
          onDragOver={(e) => (e.preventDefault(), setOver(true))}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          className={cn("flex h-10 items-center gap-3 rounded border border-dashed pr-1 pl-3", over ? "border-accent bg-accent/5" : "border-line-strong")}
        >
          <CloudUpload className="size-4 text-subtle" />
          <span className="whitespace-nowrap text-muted">
            Drop videos here <span className="text-subtle">(mp4, mov, webm)</span> — or paste a URL
          </span>
          <input ref={pickRef} type="file" multiple hidden accept=".mp4,.mov,.webm,video/mp4,video/quicktime,video/webm" onChange={(e) => (addFiles(e.target.files), (e.target.value = ""))} />
          <form
            className="ml-auto flex items-center gap-1.5"
            onSubmit={(e) => {
              e.preventDefault()
              setNotice("")
              fromUrl.mutate({ body: { url, rights_status: rights, source_creator_handle: handle.trim() || null } })
            }}
          >
            <label className="flex h-7 w-[320px] items-center gap-2 rounded border border-line bg-panel px-2 focus-within:border-muted">
              <Link2 className="size-3.5 text-subtle" />
              <input
                type="url"
                required
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                className="min-w-0 flex-1 bg-transparent text-base text-fg outline-none placeholder:text-subtle"
                placeholder="https://www.tiktok.com/@creator/video/…"
              />
            </label>
            <select title="Rights for new clips (drops and imports)" value={rights} onChange={(e) => setRights(e.target.value as Rights)} className={cn(field, "w-[150px]")}>
              {Object.entries(RIGHTS).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
            <input title="Creator handle for new clips (optional)" value={handle} onChange={(e) => setHandle(e.target.value)} placeholder="@handle (optional)" className={cn(field, "w-[140px]")} />
            <button className={btn.secondary} disabled={fromUrl.isPending}>
              Import
            </button>
          </form>
        </div>
        {notice && (
          // floats over the table header, so the table doesn't jump
          <div className="absolute inset-x-4 top-full z-20 mt-1 flex items-center justify-between rounded border border-bad/40 bg-raised px-2 py-1 text-sm text-bad">
            {notice}
            <button className="text-muted hover:text-fg" onClick={() => setNotice("")}>
              Dismiss
            </button>
          </div>
        )}
      </div>

      <section className="min-h-0 flex-1 overflow-auto">
        <table className="w-full table-fixed border-collapse">
          <colgroup>
            <col className="w-[44px]" />
            <col />
            <col className="w-[72px]" />
            <col className="w-[156px]" />
            <col className="w-[160px]" />
            <col className="w-[212px]" />
            <col className="w-[68px]" />
            <col className="w-[84px]" />
            <col className="w-[150px]" />
          </colgroup>
          <thead className="sticky top-0 z-10 bg-bg">
            <tr className="h-8 text-left text-xs uppercase tracking-wider text-subtle shadow-[inset_0_-1px_0_var(--color-line)] [&>th]:px-3 [&>th]:font-medium">
              <th className="!pl-4 !pr-0" />
              <th>Name</th>
              <th className="text-right">Duration</th>
              <th className="!pl-6">Size</th>
              <th>Rights</th>
              <th>Status</th>
              <th className="text-right">Renders</th>
              <th className="text-muted">Added</th>
              <th />
            </tr>
          </thead>
          <tbody className="[&_td]:px-3 [&_td]:align-middle [&>tr]:h-14 [&>tr]:border-b [&>tr]:border-line [&>tr:hover]:bg-panel">
            {uploads.map((u) => (
              <tr key={u.key} data-upload={u.file.name}>
                <td className="!pl-4 !pr-0">
                  <Placeholder />
                </td>
                <td>
                  <div className="truncate font-medium">{u.file.name}</div>
                  <div className="flex items-center gap-1.5 text-sm tabular-nums text-muted">
                    <HardDriveUpload className="size-3 text-subtle" />
                    {u.fatal ? size(u.file.size) : `${u.error ? "Stopped at" : "Uploading ·"} ${size(u.loaded)} of ${size(u.file.size)}`}
                  </div>
                </td>
                <td className="text-right text-subtle">—</td>
                <td className="!pl-6 text-subtle">—</td>
                <td>{u.fatal ? <span className="text-subtle">—</span> : <RightsChip value={u.rights} />}</td>
                <td>
                  {u.error ? (
                    <Failed cause={u.error} />
                  ) : (
                    <>
                      <div className="flex items-baseline justify-between text-sm tabular-nums">
                        <span className="font-medium text-accent">Uploading {Math.floor(pct(u))}%</span>
                        <span className="text-muted">{(u.rate / 1e6).toFixed(1)} MB/s</span>
                      </div>
                      <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line">
                        <div className="h-full bg-accent" style={{ width: `${pct(u)}%` }} />
                      </div>
                    </>
                  )}
                </td>
                <td className="text-right tabular-nums text-subtle">—</td>
                <td className="tabular-nums text-muted">just now</td>
                <td>
                  <div className="flex items-center justify-end gap-1">
                    {!u.error ? (
                      <>
                        <button className={btn.ghost} onClick={() => u.xhr?.abort()}>
                          Cancel
                        </button>
                        <Slot />
                      </>
                    ) : u.fatal ? (
                      <>
                        <button className={btn.ghost} onClick={() => drop(u.key)}>
                          Dismiss
                        </button>
                        <Slot />
                      </>
                    ) : (
                      <>
                        <button className={cn(btn.secondary, "px-2 font-normal")} onClick={() => upload(u.file, u.rights, u.handle, refresh, u.key)}>
                          <RotateCw className="size-3.5" />
                          Retry
                        </button>
                        <button className={btn.icon} title="Dismiss" onClick={() => drop(u.key)}>
                          <Trash2 className="size-4" />
                        </button>
                      </>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {rows.map((c) => (
              <ClipRow
                key={c.id}
                c={c}
                renders={counts.get(c.id) ?? 0}
                onRights={(v) => patch.mutate({ path: { clip_id: c.id }, body: { rights_status: v } })}
                onRetry={() => retry.mutate({ path: { clip_id: c.id } })}
                onRemove={() => confirm(`Remove ${clipName(c)}? Its file is deleted too.`) && remove.mutate({ path: { clip_id: c.id } })}
              />
            ))}
          </tbody>
        </table>
        {props.error ? (
          <p className="p-8 text-center text-bad">Couldn't load clips: {props.error}</p>
        ) : (
          !loading &&
          !rows.length &&
          !uploads.length && <p className="p-8 text-center text-muted">{search ? "No clips match." : "No clips yet. Drop a video above or import a URL."}</p>
        )}
      </section>
    </>
  )
}

function ClipRow({ c, renders, onRights, onRetry, onRemove }: { c: ClipOut; renders: number; onRights: (v: Rights) => void; onRetry: () => void; onRemove: () => void }) {
  const ready = c.status === "READY"
  // a retry can't fix these: Remove instead (as in the mockup)
  const retryable = c.status === "FAILED" && !FINAL.has(c.error_code ?? "") && (c.origin === "url" || !!c.raw_url)
  const importing = c.status === "DOWNLOADING" || c.status === "PROBING"
  const fps = c.fps ? `${+c.fps.toFixed(2)}fps` : ""
  return (
    <tr data-clip={c.id} data-status={c.status}>
      <td className="!pl-4 !pr-0">
        {c.thumbnail_url ? (
          <div className="group relative w-7">
            <img src={c.thumbnail_url} alt="" className="h-[50px] w-7 rounded-sm bg-raised object-cover" />
            <img src={c.thumbnail_url} alt="" className="pointer-events-none absolute top-0 left-9 z-20 hidden max-h-64 max-w-64 rounded ring-1 ring-line-strong group-hover:block" />
          </div>
        ) : (
          <Placeholder />
        )}
      </td>
      <td>
        <div className="truncate font-medium" title={c.source_url ?? undefined}>
          {clipName(c)}
        </div>
        <div className="flex items-center gap-1.5 truncate text-sm text-muted">
          {c.origin === "url" ? <PlatformIcon platform={c.platform} /> : <HardDriveUpload className="size-3 shrink-0 text-subtle" />}
          {c.source_creator_handle ??
            (c.origin === "url" ? (c.platform ?? (importing ? "Importing" : "URL import")) : c.status === "UPLOADING" ? "Uploading" : "Uploaded")}
        </div>
      </td>
      <td className={cn("text-right tabular-nums", c.duration_s == null && "text-subtle")}>{mmss(c.duration_s)}</td>
      <td className="!pl-6 whitespace-nowrap text-sm tabular-nums text-muted" title={c.size_bytes ? `${(c.size_bytes / 1e6).toFixed(1)} MB` : undefined}>
        {c.width ? `${c.width}x${c.height} · ${fps}` : <span className="text-subtle">—</span>}
      </td>
      <td>
        <RightsChip value={c.rights_status} onChange={onRights} />
      </td>
      <td>
        {c.status === "FAILED" ? (
          <Failed cause={CAUSES[c.error_code ?? ""] ?? "Failed"} code={c.error_code} detail={c.error_detail} />
        ) : ready ? (
          <Chip tone="ok" dot>
            Ready
          </Chip>
        ) : (
          <Chip tone="accent" spin>
            {c.status[0] + c.status.slice(1).toLowerCase()}
          </Chip>
        )}
      </td>
      <td className={cn("text-right tabular-nums", !renders && "text-subtle")}>{ready ? renders : "—"}</td>
      <td className="tabular-nums text-muted">{ago(c.created_at)}</td>
      <td>
        <div className="flex items-center justify-end gap-1">
          {c.status === "FAILED" && retryable ? (
            <button className={cn(btn.secondary, "px-2 font-normal")} onClick={onRetry}>
              <RotateCw className="size-3.5" />
              Retry
            </button>
          ) : c.status === "FAILED" ? (
            <button className={btn.ghost} onClick={onRemove}>
              Remove
            </button>
          ) : ready ? (
            <Link to={`/editor/${c.id}`} className={btn.ghost}>
              Open editor
            </Link>
          ) : (
            <span className={cn(btn.ghost, "text-subtle hover:bg-transparent hover:text-subtle")}>Open editor</span>
          )}
          {/* the api only deletes READY/FAILED clips without renders */}
          {(ready && !renders) || retryable ? (
            <button className={btn.icon} title="Remove" onClick={onRemove}>
              <Trash2 className="size-4" />
            </button>
          ) : ready ? (
            <button className={btn.icon} title="Delete its renders first" aria-label="Remove" disabled>
              <Trash2 className="size-4" />
            </button>
          ) : (
            <Slot />
          )}
        </div>
      </td>
    </tr>
  )
}

/** Keeps the last 28 px of the actions column, so the text actions line up across rows. */
const Slot = () => <span className="size-7 shrink-0" />
const pct = (u: Upload) => (u.file.size ? Math.min(100, (u.loaded / u.file.size) * 100) : 0)

/** lucide 1.x has no brand icons: Instagram and YouTube are lucide 0.469's paths (as in the mockup), TikTok the mockup's. */
function PlatformIcon({ platform }: { platform: string | null }) {
  const p = platform?.toLowerCase() ?? ""
  const svg = (children: ReactNode, fill?: boolean) => (
    <svg viewBox="0 0 24 24" className="size-3 shrink-0 text-subtle" fill={fill ? "currentColor" : "none"} stroke={fill ? "none" : "currentColor"} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  )
  if (p.includes("tiktok"))
    return svg(<path d="M16.6 5.82A4.28 4.28 0 0 1 15.54 3h-3.09v12.4a2.59 2.59 0 1 1-2.59-2.6c.27 0 .53.04.77.12V9.77a5.7 5.7 0 0 0-.77-.05A5.69 5.69 0 1 0 15.54 15.4V9.01a7.35 7.35 0 0 0 4.3 1.38V7.3a4.3 4.3 0 0 1-3.24-1.48z" />, true)
  if (p.includes("instagram"))
    return svg(
      <>
        <rect width="20" height="20" x="2" y="2" rx="5" ry="5" />
        <path d="M16 11.37A4 4 0 1 1 12.63 8 4 4 0 0 1 16 11.37z" />
        <path d="M17.5 6.5h.01" />
      </>
    )
  if (p.includes("youtube"))
    return svg(
      <>
        <path d="M2.5 17a24.12 24.12 0 0 1 0-10 2 2 0 0 1 1.4-1.4 49.56 49.56 0 0 1 16.2 0A2 2 0 0 1 21.5 7a24.12 24.12 0 0 1 0 10 2 2 0 0 1-1.4 1.4 49.55 49.55 0 0 1-16.2 0A2 2 0 0 1 2.5 17" />
        <path d="m10 15 5-3-5-3z" />
      </>
    )
  return <Globe className="size-3 shrink-0 text-subtle" />
}

const Placeholder = () => (
  <div className="grid h-[50px] w-7 place-items-center rounded-sm border border-line bg-raised">
    <FileVideo className="size-3.5 text-subtle" />
  </div>
)

function Failed({ cause, code, detail }: { cause: string; code?: string | null; detail?: string | null }) {
  return (
    <>
      <div className="flex min-w-0 items-center gap-1.5">
        <Chip tone="bad">Failed</Chip>
        {code && (
          <span className="truncate font-mono text-xs text-subtle" title={code}>
            {code}
          </span>
        )}
      </div>
      <div className="mt-0.5 truncate text-sm text-muted" title={detail ?? cause}>
        {cause}
      </div>
    </>
  )
}

/** The rights chip; a native select in chip clothing where it can be changed. */
function RightsChip({ value, onChange }: { value: Rights; onChange?: (v: Rights) => void }) {
  const cls = cn(
    "h-5 appearance-none rounded border bg-transparent px-1.5 text-xs outline-none [field-sizing:content] focus-visible:border-muted",
    value === "none" ? "border-warn/40 text-warn" : "border-line text-muted",
    onChange && "cursor-pointer hover:border-line-strong"
  )
  if (!onChange) return <span className={cn(cls, "inline-flex items-center")}>{RIGHTS[value]}</span>
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as Rights)} className={cls} title="Change rights">
      {Object.entries(RIGHTS).map(([k, v]) => (
        <option key={k} value={k}>
          {v}
        </option>
      ))}
    </select>
  )
}
