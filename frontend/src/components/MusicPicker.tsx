import { useQuery } from "@tanstack/react-query"
import { Music2, Pause, Play, Search } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import type { MusicOut, PostMusic } from "@/api"
import { searchMusicOptions } from "@/api/@tanstack/react-query.gen"
import { Slider } from "@/components/ui/slider"
import { apiError } from "@/lib/schedule"
import { cn, field, label, mmss } from "@/lib/utils"

const KINDS = [
  ["music", "Music"],
  ["original_sound", "Original sounds"],
] as const

/** A post's Instagram music: Instagram's own catalog through the account (GET /api/accounts/{id}/music, Zernio),
 * attached when the Reel publishes. Trending until you search; ▶ plays Meta's preview. */
export function MusicPicker({ accountId, value, onChange }: { accountId?: number; value: PostMusic | null; onChange: (m: PostMusic | null) => void }) {
  const [open, setOpen] = useState(false)
  const [text, setText] = useState("")
  const [q, setQ] = useState("") // searched on Enter: every search is a call to Instagram
  const [kind, setKind] = useState<MusicOut["kind"]>("music")
  const found = useQuery({ ...searchMusicOptions({ path: { account_id: accountId ?? 0 }, query: { q: q || undefined, kind } }), enabled: open && !!accountId, staleTime: 5 * 60_000, retry: false })
  const audio = useRef<HTMLAudioElement>(null)
  const [playing, setPlaying] = useState<string | null>(null)
  useEffect(() => {
    const a = audio.current
    return () => a?.pause() // closing the popover or drawer stops the preview
  }, [])

  function preview(m: MusicOut) {
    const a = audio.current!
    if (playing === m.id) return a.pause()
    a.src = m.preview_url!
    a.play().catch(() => setPlaying(null))
    setPlaying(m.id)
  }
  function pick(m: MusicOut) {
    audio.current?.pause()
    onChange({ id: m.id, title: m.title, artist: m.artist, volume: value?.volume ?? 100, video_volume: value?.video_volume ?? 100 })
    setOpen(false)
  }

  return (
    <div className="space-y-1.5">
      <audio ref={audio} onPause={() => setPlaying(null)} onEnded={() => setPlaying(null)} />
      <div className="flex items-center justify-between">
        <span className={label}>Music</span>
        {!open && (
          <span className="flex gap-2 text-sm">
            <button type="button" className="text-muted hover:text-fg" onClick={() => setOpen(true)}>
              {value ? "Change" : "Add Instagram music…"}
            </button>
            {value && (
              <button type="button" className="text-muted hover:text-bad" onClick={() => onChange(null)}>
                Remove
              </button>
            )}
          </span>
        )}
      </div>
      {!open &&
        (value ? (
          <div className="space-y-1.5">
            <div className="flex min-w-0 items-center gap-1.5">
              <Music2 className="size-3.5 shrink-0 text-muted" />
              <span className="truncate">{value.title ?? "Instagram audio"}</span>
              {value.artist && <span className="truncate text-muted">· {value.artist}</span>}
            </div>
            <Volume name="Track" value={value.volume ?? 100} onChange={(volume) => onChange({ ...value, volume })} />
            <Volume name="Clip's sound" value={value.video_volume ?? 100} onChange={(video_volume) => onChange({ ...value, video_volume })} />
          </div>
        ) : (
          <p className="text-sm text-subtle">None: the clip's own sound.</p>
        ))}
      {open && (
        <div className="space-y-1.5">
          <form className="flex gap-1.5" onSubmit={(e) => (e.preventDefault(), setQ(text.trim()))}>
            <input aria-label="Search Instagram music" placeholder="Search, or leave empty for trending" className={cn(field, "min-w-0 flex-1")} value={text} onChange={(e) => setText(e.target.value)} />
            <button aria-label="Search" className="inline-grid size-7 shrink-0 place-items-center rounded border border-line text-muted hover:text-fg">
              <Search className="size-3.5" />
            </button>
          </form>
          <div className="grid h-7 grid-cols-2 rounded border border-line bg-bg p-0.5 text-sm">
            {KINDS.map(([k, name]) => (
              <button key={k} type="button" aria-pressed={kind === k} className={cn("rounded-sm", kind === k ? "bg-hover font-medium text-fg" : "text-muted hover:text-fg")} onClick={() => setKind(k)}>
                {name}
              </button>
            ))}
          </div>
          <div className="text-xs text-subtle">{q ? `Results for “${q}”` : "Trending on Instagram"}</div>
          <ul className="max-h-48 divide-y divide-line overflow-auto rounded border border-line">
            {found.isPending && <li className="px-2 py-1.5 text-sm text-muted">Asking Instagram…</li>}
            {found.isError && <li className="px-2 py-1.5 text-sm text-bad">{apiError(found.error).message}</li>}
            {found.data?.length === 0 && <li className="px-2 py-1.5 text-sm text-muted">Nothing found.</li>}
            {found.data?.map((m) => (
              <li key={m.id} className="flex items-center gap-1.5 px-1.5 py-1 hover:bg-hover">
                <button type="button" aria-label={playing === m.id ? `Stop ${m.title}` : `Preview ${m.title}`} disabled={!m.preview_url} className="inline-grid size-6 shrink-0 place-items-center rounded text-muted hover:text-fg disabled:opacity-30" onClick={() => preview(m)}>
                  {playing === m.id ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
                </button>
                <button type="button" className="min-w-0 flex-1 text-left" onClick={() => pick(m)}>
                  <span className="block truncate">{m.title ?? "Original audio"}</span>
                  <span className="block truncate text-sm text-muted">{m.artist ?? " "}</span>
                </button>
                <span className="shrink-0 text-sm tabular-nums text-subtle">{mmss(m.duration_s)}</span>
              </li>
            ))}
          </ul>
          <button type="button" className="text-sm text-muted hover:text-fg" onClick={() => (audio.current?.pause(), setOpen(false))}>
            {value ? "Keep the current track" : "Cancel"}
          </button>
        </div>
      )}
    </div>
  )
}

function Volume({ name, value, onChange }: { name: string; value: number; onChange: (v: number) => void }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="w-[84px] shrink-0 text-muted">{name}</span>
      <Slider aria-label={`${name} volume`} min={0} max={100} step={5} value={[value]} onValueChange={([v]) => onChange(v)} className="flex-1" />
      <span className="w-9 text-right tabular-nums">{value}%</span>
    </div>
  )
}
