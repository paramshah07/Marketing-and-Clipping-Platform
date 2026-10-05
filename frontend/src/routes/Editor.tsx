import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CalendarPlus, Camera, ChevronDown, ChevronsUpDown, Download, Ellipsis, Eye, EyeOff, FileText, Heart, MessageCircle, Music2, Pause, Play, RotateCw, Send, Trash2, Upload, Volume2, VolumeX, X } from "lucide-react"
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from "react"
import { Link, useParams, useSearchParams } from "react-router"

import type { BrandOut, CaptionOut, ClipOut, CoverOut, FilterOut, OverlayConfig, RenderOut, TrackOut } from "@/api"
import {
  createRenderMutation,
  deleteRenderMutation,
  getClipOptions,
  getRenderOptions,
  listAccountsOptions,
  listBrandsOptions,
  listBrandsQueryKey,
  listCaptionsOptions,
  listCoversOptions,
  listFiltersOptions,
  listRendersOptions,
  listRendersQueryKey,
  listTracksOptions,
  listTracksQueryKey,
  retryRenderMutation,
  setRenderCoverMutation,
  updateBrandMutation,
  uploadTrackMutation,
} from "@/api/@tanstack/react-query.gen"
import { FracBox } from "@/components/FracBox"
import { SchedulePopover } from "@/components/SchedulePopover"
import { Chip, Empty, Header } from "@/components/bits"
import { Slider } from "@/components/ui/slider"
import { Switch } from "@/components/ui/switch"
import { coverJpeg, savedCover } from "@/lib/cover"
import { GRID, IG, OUT_H, OUT_W, type Box, clamp, coverScale, crop916, cropAspect, cropPx, logoAspect, outputView, snapPosition } from "@/lib/geometry"
import { CAPTION_MAX, CAUSES, HASHTAG_MAX, MAX_REEL_SECONDS, SONG_TYPES, ago, clipName, cn, errorText, fillCaption, hashtagCount, mb, mmss, btn, label, songName } from "@/lib/utils"

const DEFAULT_OVERLAY: OverlayConfig = { x: 0.72, y: 0.16, w: 0.22, opacity: 1 } // backend brands default (below the IG top bar)
const MIN_W = 0.02
const MAX_W = 0.6
const LIVE = new Set(["PENDING", "RENDERING"])
const SNAPS = ["Top left", "Top centre", "Top right", "Middle left", "Centre", "Middle right", "Bottom left", "Bottom centre", "Bottom right"]

export function Editor() {
  const id = Number(useParams().clipId)
  const clip = useQuery({
    ...getClipOptions({ path: { clip_id: id } }),
    refetchInterval: (q) => (q.state.data && !["READY", "FAILED"].includes(q.state.data.status) ? 2000 : false),
  })
  const brands = useQuery(listBrandsOptions())
  const history = useQuery(listRendersOptions({ query: { clip_id: id } }))
  // Saved captions, covers and songs (Customizations) and the filters are extras: the editor opens without them if they fail to load
  const captions = useQuery(listCaptionsOptions())
  const covers = useQuery(listCoversOptions())
  const tracks = useQuery(listTracksOptions())
  const filters = useQuery({ ...listFiltersOptions(), staleTime: Infinity })
  const def = covers.data?.find((c) => c.is_default)
  const defCover = useQuery({ queryKey: ["saved-cover", def?.id], queryFn: () => savedCover(def!), enabled: !!def, staleTime: Infinity })
  const [params] = useSearchParams()
  if (clip.isError || brands.isError) return <Page title="Clip">{errorText(clip.error ?? brands.error)}</Page>
  if (!clip.data || !brands.data || history.isPending || captions.isPending || covers.isPending || tracks.isPending || filters.isPending || (def && defCover.isPending))
    return <Page title="Clip">Loading…</Page>
  // ?brand= wins, else the brand this clip was last rendered with (newest first), else the default brand, else the operator picks
  const byId = (bid: number | null | undefined) => brands.data.find((b) => b.id === bid && b.logo_url)
  const initial =
    byId(Number(params.get("brand"))) ?? (history.data ?? []).map((r) => byId(r.brand_id)).find(Boolean) ?? byId(brands.data.find((b) => b.is_default)?.id) ?? null
  if (clip.data.status === "FAILED")
    return (
      <Page title={clipName(clip.data)}>
        This clip failed ({clip.data.error_code}); there is nothing to edit. Retry or remove it in the Library.
      </Page>
    )
  if (clip.data.status !== "READY" || !clip.data.width || !clip.data.height)
    return <Page title={clipName(clip.data)}>This clip is {clip.data.status.toLowerCase()}; the editor opens once it is READY.</Page>
  const cover = def && defCover.data ? { jpeg: defCover.data, url: def.image_url, from: def.id } : null
  return <EditorBody key={id} clip={clip.data} brands={brands.data} initial={initial} captions={captions.data ?? []} covers={covers.data ?? []} tracks={tracks.data ?? []} filters={filters.data ?? []} initialCover={cover} />
}

function Page({ title, children }: { title: string; children: ReactNode }) {
  return (
    <>
      <Header>
        <h1 className="truncate text-lg font-semibold" title={title}>
          {title}
        </h1>
      </Header>
      <Empty>{children}</Empty>
    </>
  )
}

function useSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useLayoutEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ width: e.contentRect.width, height: e.contentRect.height }))
    ro.observe(ref.current!)
    return () => ro.disconnect()
  }, [])
  return [ref, size] as const
}

// url: an object URL for a chosen file, a saved cover's own /media URL (revoking that is a no-op); from: that saved cover
type Cover = { jpeg: Blob; url: string; from?: number }

function EditorBody({ clip, brands, initial, captions, covers, tracks, filters, initialCover }: { clip: ClipOut; brands: BrandOut[]; initial: BrandOut | null; captions: CaptionOut[]; covers: CoverOut[]; tracks: TrackOut[]; filters: FilterOut[]; initialCover: Cover | null }) {
  const qc = useQueryClient()
  const W = clip.width!
  const H = clip.height!

  const first = initial
  const [brandId, setBrandId] = useState<number | null>(first?.id ?? null)
  const [chosen, setChosen] = useState(!!first) // false: nothing picked yet, Render waits for a choice
  const brand = brands.find((b) => b.id === brandId) ?? null
  const [overlay, setOverlay] = useState<OverlayConfig>(first?.default_overlay_config ?? DEFAULT_OVERLAY)
  const fill = (text: string | null, b: BrandOut | null) => fillCaption(text, b?.link ?? null, clip.source_creator_handle)
  // the brand's caption template, else the default saved caption (with the brand's link), else empty
  const template = (b: BrandOut | null) => fill(b?.caption_template || (captions.find((c) => c.is_default)?.text ?? null), b)
  const [caption, setCaption] = useState(() => template(first))
  const [cell, setCell] = useState<number | null>(null)
  const [margin, setMargin] = useState(4) // % of the output width
  const [logoAR, setLogoAR] = useState<number | null>(null) // logo natural h/w
  const [cropOn, setCropOn] = useState(false)
  const [crop, setCrop] = useState<Box>(() => crop916(W, H))
  const [filter, setFilter] = useState<FilterOut | null>(null)
  // the song mixed into the render; the default song (Customizations) is preselected
  const [music, setMusic] = useState<{ track: TrackOut; volume: number; clipVolume: number } | null>(() => {
    const d = tracks.find((t) => t.is_default)
    return d ? { track: d, volume: 100, clipVolume: 100 } : null
  })
  const pickSong = (t: TrackOut | null) => setMusic(t && { track: t, volume: music?.volume ?? 100, clipVolume: music?.clipVolume ?? 100 })
  const songFile = useRef<HTMLInputElement>(null)
  const songUpload = useMutation({
    ...uploadTrackMutation(),
    onSuccess: (t) => (pickSong(t), qc.invalidateQueries({ queryKey: listTracksQueryKey() })),
    onError: (e) => setError(errorText(e)),
  })
  const [mode, setMode] = useState<"output" | "crop" | "cover">("output")
  const [cover, setCover] = useState<Cover | null>(initialCover) // the Reel cover exactly as uploaded
  useEffect(() => () => void (cover && URL.revokeObjectURL(cover.url)), [cover]) // on replace and unmount
  const pickCover = useRef<HTMLInputElement>(null)
  const [ig, setIg] = useState(true)
  const [previewId, setPreview] = useState<number | null>(null)
  const [logFor, setLogFor] = useState<number | null>(null)
  const [error, setError] = useState("")

  const aspect = logoAspect(1, logoAR ?? 1) // logo box h/w in output fractions
  const maxW = Math.min(MAX_W, 1 / aspect) // a tall logo also has to fit the frame's height
  const place = (o: OverlayConfig, c: number | null, m: number): OverlayConfig => {
    const w = Math.min(o.w, maxW)
    return c == null ? { ...o, w, x: clamp(o.x, 0, 1 - w), y: clamp(o.y, 0, 1 - w * aspect) } : { ...o, w, ...snapPosition(c, w, w * aspect, m / 100) }
  }

  function pickBrand(b: BrandOut | null) {
    setChosen(true)
    setBrandId(b?.id ?? null)
    setOverlay(b?.default_overlay_config ?? DEFAULT_OVERLAY)
    setCaption((c) => (c === template(brand) ? template(b) : c)) // keep the operator's own edits
    setCell(null)
    setLogoAR(null)
  }

  const renders = useQuery({
    ...listRendersOptions({ query: { clip_id: clip.id } }),
    refetchInterval: (q) => (q.state.data?.some((r) => LIVE.has(r.status)) ? 2000 : false),
  })
  const preview = renders.data?.find((r) => r.id === previewId) ?? null // null again once that render is deleted
  const archived = useQuery(listBrandsOptions({ query: { archived: true } }))
  const accounts = useQuery(listAccountsOptions())
  const handle = accounts.data?.find((a) => a.connection_status === "connected" && !a.disabled_at)?.username
  const brandName = (id: number | null) => (id == null ? "No logo" : ([...brands, ...(archived.data ?? [])].find((b) => b.id === id)?.name ?? `Brand ${id}`))
  // No second render until 1 s after this one settles. Set synchronously (isPending needs a re-render), and the
  // cool-down because a localhost POST settles in ~15 ms, before a fast second ⌘↵ arrives.
  const busyUntil = useRef(0)
  const render = useMutation({
    ...createRenderMutation(),
    onSettled: () => (busyUntil.current = Date.now() + 1000),
    onSuccess: () => (setError(""), qc.invalidateQueries({ queryKey: listRendersQueryKey({ query: { clip_id: clip.id } }) })),
    onError: (e) => setError(errorText(e)),
  })
  const coverUpload = useMutation({
    ...setRenderCoverMutation(),
    onSuccess: () => qc.invalidateQueries({ queryKey: listRendersQueryKey({ query: { clip_id: clip.id } }) }),
    onError: (e, v) => setError(`Render #${v.path.render_id} queued without its cover: ${errorText(e)}`),
  })
  const saveDefault = useMutation({
    ...updateBrandMutation(),
    onSuccess: () => qc.invalidateQueries({ queryKey: listBrandsQueryKey() }),
    onError: (e) => setError(errorText(e)),
  })

  const tags = hashtagCount(caption)
  const tooLong = caption.length > CAPTION_MAX || tags > HASHTAG_MAX // Instagram refuses these captions
  function submit() {
    if (Date.now() < busyUntil.current || tooLong || !chosen) return
    busyUntil.current = Infinity
    const jpeg = cover?.jpeg
    // the promise, not mutate's per-call onSuccess: that is dropped when the Editor unmounts first, and the cover with it
    render
      .mutateAsync({
        body: {
          clip_id: clip.id,
          brand_id: brand?.id ?? null,
          overlay_config: brand ? overlay : null,
          crop_config: cropOn ? { x: crop.x, y: crop.y, w: Math.min(1, crop.w), h: Math.min(1, crop.h) } : null,
          filter: filter?.name ?? null,
          music: music && { track_id: music.track.id, volume: music.volume, clip_volume: music.clipVolume },
          caption: caption.trim() || null,
        },
      })
      .then((r) => jpeg && coverUpload.mutate({ path: { render_id: r.id }, body: { file: jpeg } }), () => {}) // render's onError reports
  }
  async function chooseCover(from: File | CoverOut) {
    try {
      if (from instanceof File) {
        const jpeg = await coverJpeg(from)
        setCover({ jpeg, url: URL.createObjectURL(jpeg) })
      } else setCover({ jpeg: await savedCover(from), url: from.image_url, from: from.id })
      setMode("cover")
      setPreview(null)
      setError("")
    } catch (e) {
      setError(errorText(e))
    }
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key !== "Enter") return
      if ((e.target as Element | null)?.closest?.('dialog, [role="dialog"]')) return // popover / log dialog: not a render
      e.preventDefault()
      if (!e.repeat) submit()
    }
    addEventListener("keydown", onKey)
    return () => removeEventListener("keydown", onKey)
  })

  const def = brand?.default_overlay_config
  const edited = !!def && (["x", "y", "w", "opacity"] as const).some((k) => Math.abs((def[k] ?? 1) - (overlay[k] ?? 1)) > 1e-4)
  const [cw, ch] = cropPx(crop, W, H)
  const scale = coverScale(cw, ch)
  const presets = [
    ["9:16 region", crop916(W, H)],
    ["Full frame", { x: 0, y: 0, w: 1, h: 1 }],
  ] as const
  // a 9:16 source's 9:16 region is its full frame: light only the first match
  const preset = cropOn ? presets.findIndex(([, b]) => Math.abs(crop.w - b.w) < 1e-9 && Math.abs(crop.h - b.h) < 1e-9) : -1
  const long = (clip.duration_s ?? 0) > MAX_REEL_SECONDS

  return (
    <>
      <Header>
        <div className="flex min-w-0 items-center gap-3">
          <nav className="flex min-w-0 items-center gap-1.5 text-lg">
            <Link to="/library" className="shrink-0 text-muted hover:text-fg">
              Library
            </Link>
            <span className="text-subtle">/</span>
            <h1 className="truncate font-semibold" title={clip.source_url ?? clipName(clip)}>
              {clipName(clip)}
            </h1>
          </nav>
          <span className="shrink-0 whitespace-nowrap text-sm tabular-nums text-muted">
            {mmss(clip.duration_s)} · {W}x{H} · {clip.fps ? +clip.fps.toFixed(2) : "?"}fps · {clip.has_audio ? "has audio" : "no audio"}
          </span>
        </div>
        <span className="shrink-0 font-mono text-sm whitespace-nowrap text-subtle">clip {clip.id}</span>
      </Header>
      <section className="flex min-h-0 flex-1">
        <Stage
          clip={clip}
          mode={preview ? "preview" : mode}
          previewSrc={preview?.output_url ?? null}
          previewPoster={preview?.cover_url ?? null}
          cover={cover?.url ?? null}
          previewLabel={preview ? `#${preview.id} · ${brandName(preview.brand_id)} · 1080x1920` : ""}
          onExitPreview={() => setPreview(null)}
          view={outputView(W, H, cropOn ? crop : null)}
          look={filter}
          song={music && { url: music.track.audio_url, volume: music.volume, clipVolume: clip.has_audio ? music.clipVolume : 100 }}
          setMode={(m) => {
            if (m === "crop") setCropOn(true)
            setMode(m)
          }}
          ig={ig}
          setIg={setIg}
          caption={caption}
          handle={handle}
          logo={
            brand?.logo_url
              ? (
                  <FracBox
                    label="logo"
                    box={{ x: overlay.x, y: overlay.y, w: overlay.w, h: overlay.w * aspect }}
                    aspect={aspect}
                    minW={MIN_W}
                    maxW={maxW}
                    onChange={(b) => (setCell(null), setOverlay({ ...overlay, x: b.x, y: b.y, w: b.w }))}
                  >
                    <img
                      key={brand.logo_url}
                      src={brand.logo_url!}
                      alt=""
                      draggable={false}
                      className="pointer-events-none size-full max-w-none select-none"
                      style={{ opacity: overlay.opacity }}
                      onLoad={(e) => setLogoAR(e.currentTarget.naturalHeight / e.currentTarget.naturalWidth)}
                    />
                  </FracBox>
                )
              : null
          }
          cropBox={
            <FracBox label="crop" edges box={crop} aspect={cropAspect(W, H)} minW={0.05} onChange={setCrop}>
              <div className="pointer-events-none absolute inset-0 grid grid-cols-3 grid-rows-3 [&>i]:border-white/30 [&>i:nth-child(-n+6)]:border-b [&>i:not(:nth-child(3n))]:border-r">
                {Array.from({ length: 9 }, (_, i) => (
                  <i key={i} />
                ))}
              </div>
              <span className="absolute -bottom-6 left-1/2 -translate-x-1/2 whitespace-nowrap text-xs tabular-nums text-muted">
                {cw} x {ch}
              </span>
            </FracBox>
          }
          crop={crop}
        />

        {/* CONTROLS */}
        <aside className="flex w-[320px] shrink-0 flex-col border-l border-line bg-panel">
          <div className="min-h-0 flex-1 divide-y divide-line overflow-auto">
            <div className="space-y-2 px-4 py-3">
              <div className="flex items-center justify-between">
                <span className={label}>Brand</span>
                {brand && (
                  <button
                    className="text-sm text-muted underline decoration-line-strong underline-offset-2 hover:text-fg disabled:no-underline disabled:opacity-50"
                    disabled={!edited || saveDefault.isPending}
                    onClick={() => saveDefault.mutate({ path: { brand_id: brand.id }, body: { default_overlay_config: overlay } })}
                  >
                    {saveDefault.isPending ? "Saving…" : "Save as brand default"}
                  </button>
                )}
              </div>
              <label className="flex h-7 w-full items-center gap-2 rounded border border-line bg-bg px-2 hover:border-line-strong focus-within:border-muted">
                {brand?.logo_url ? <img src={brand.logo_url} alt="" className="checker size-4 shrink-0 rounded-sm object-contain" /> : <span className="size-4 shrink-0 rounded-sm border border-line-strong" />}
                <select
                  aria-label="Brand"
                  value={chosen ? (brandId ?? "none") : ""}
                  onChange={(e) => pickBrand(brands.find((b) => b.id === Number(e.target.value)) ?? null)}
                  className="min-w-0 flex-1 appearance-none bg-transparent text-fg outline-none"
                >
                  {!chosen && (
                    <option value="" disabled>
                      Choose a brand…
                    </option>
                  )}
                  <option value="none">No logo</option>
                  {brands.map((b) => (
                    <option key={b.id} value={b.id} disabled={!b.logo_url}>
                      {b.name}
                      {b.logo_url ? "" : " (no logo)"}
                    </option>
                  ))}
                </select>
                {edited && <span className="text-sm text-subtle">edited</span>}
                <ChevronsUpDown className="size-3.5 text-subtle" />
              </label>
            </div>

            <fieldset disabled={!brand} className="space-y-3 px-4 py-3 disabled:opacity-40">
              <span className={cn(label, "block")}>Logo</span>
              <Labeled name="Scale" value={<>{Math.round(overlay.w * 100)}% <span className="text-subtle">of width</span></>}>
                <Slider
                  aria-label="Logo scale"
                  min={MIN_W * 100}
                  max={maxW * 100}
                  step={0.5}
                  value={[overlay.w * 100]}
                  onValueChange={([v]) => setOverlay(place({ ...overlay, w: v / 100 }, cell, margin))}
                />
              </Labeled>
              <Labeled name="Opacity" value={`${Math.round(overlay.opacity! * 100)}%`}>
                <Slider aria-label="Logo opacity" min={0} max={100} step={1} value={[overlay.opacity! * 100]} onValueChange={([v]) => setOverlay({ ...overlay, opacity: v / 100 })} />
              </Labeled>
              <div className="flex items-start gap-4 pt-0.5">
                <div className="space-y-1.5">
                  <span className="block text-sm text-muted">Position</span>
                  <div className="grid w-[76px] grid-cols-3 gap-1 rounded border border-line bg-bg p-1">
                    {Array.from({ length: 9 }, (_, i) => (
                      <button
                        key={i}
                        aria-label={SNAPS[i]}
                        aria-pressed={cell === i}
                        className={cn("h-5 rounded-sm", cell === i ? "bg-fg" : "bg-raised hover:bg-hover")}
                        onClick={() => (setCell(i), setOverlay(place(overlay, i, margin)))}
                      />
                    ))}
                  </div>
                </div>
                <div className="flex-1 space-y-1.5">
                  <span className="block text-sm text-muted">Margin</span>
                  <label className="flex h-7 w-[84px] items-center justify-between rounded border border-line bg-bg px-2 tabular-nums focus-within:border-muted">
                    <input
                      aria-label="Logo margin, % of width"
                      type="number"
                      min={0}
                      max={20}
                      value={margin}
                      onChange={(e) => {
                        const m = clamp(Number(e.target.value) || 0, 0, 20)
                        setMargin(m)
                        if (cell != null) setOverlay(place(overlay, cell, m))
                      }}
                      className="w-10 bg-transparent outline-none"
                    />
                    <span className="text-subtle">%</span>
                  </label>
                  <p className="text-sm leading-4 text-subtle">Snaps inside the IG safe zone (dashed).</p>
                </div>
              </div>
            </fieldset>

            <div className="space-y-2 px-4 py-3">
              <div className="flex items-center justify-between">
                <span className={label}>Crop</span>
                <Switch aria-label="Crop" checked={cropOn} onCheckedChange={(on) => (setCropOn(on), setMode(on ? "crop" : "output"))} />
              </div>
              <div className={cn("grid h-7 grid-cols-2 rounded border border-line bg-bg p-0.5 text-sm", !cropOn && "pointer-events-none opacity-40")}>
                {presets.map(([name, b], i) => (
                  <button key={name} aria-pressed={preset === i} className={cn("rounded-sm", preset === i ? "bg-hover font-medium text-fg" : "text-muted hover:text-fg")} onClick={() => setCrop(b)}>
                    {name}
                  </button>
                ))}
              </div>
              {cropOn ? (
                <>
                  <div className="flex items-center justify-between text-sm">
                    <span className="tabular-nums text-fg" data-testid="crop-readout">
                      x {crop.x.toFixed(2)} · y {crop.y.toFixed(2)} · w {crop.w.toFixed(2)} · h {crop.h.toFixed(2)}
                    </span>
                    <button className="text-muted hover:text-fg" onClick={() => setCrop({ ...crop, x: (1 - crop.w) / 2, y: (1 - crop.h) / 2 })}>
                      Centre
                    </button>
                  </div>
                  <p className="text-sm tabular-nums text-subtle">
                    Region {cw}x{ch}, {scale.toFixed(2) === "1.00" ? "no scaling" : `${scale > 1 ? "upscaled" : "downscaled"} ${scale.toFixed(2)}x to 1080x1920`}
                    {Math.abs(cw / ch - 9 / 16) > 0.01 && ", centre-cropped to 9:16"}.
                  </p>
                </>
              ) : (
                <p className="text-sm text-subtle">Off: source centre-fills the 9:16 frame.</p>
              )}
            </div>

            {filters.length > 0 && (
              <div className="space-y-2 px-4 py-3">
                <div className="flex items-center justify-between">
                  <span className={label}>Filter</span>
                  <span className="text-sm text-muted">{filter?.name ?? "Normal"}</span>
                </div>
                <div className="grid grid-cols-[repeat(8,28px)] justify-between gap-y-2.5 py-0.5">
                  {[null, ...filters].map((f) => {
                    const name = f?.name ?? "Normal"
                    const on = filter?.name === f?.name
                    return (
                      <button
                        key={name}
                        aria-label={name}
                        aria-pressed={on}
                        title={name}
                        className={cn("h-[50px] w-7 rounded-sm", on ? "ring-2 ring-fg ring-offset-1 ring-offset-panel" : "hover:ring-1 hover:ring-line-strong")}
                        onClick={() => setFilter(f)}
                      >
                        <Look look={f} className="relative size-full rounded-sm bg-raised">
                          {clip.thumbnail_url && <img src={clip.thumbnail_url} alt="" className="block size-full object-cover" />}
                        </Look>
                      </button>
                    )
                  })}
                </div>
                <p className="text-sm text-subtle">Baked into the render, under the logo.</p>
              </div>
            )}

            <div className="space-y-2 px-4 py-3">
              <div className="flex items-center justify-between">
                <span className={label}>Music</span>
                <Link to="/customizations/music" className="text-sm text-muted hover:text-fg">
                  All songs
                </Link>
              </div>
              <div className="flex gap-1.5">
                {tracks.length > 0 && (
                  <label className="flex h-7 min-w-0 flex-1 items-center gap-2 rounded border border-line bg-bg px-2 hover:border-line-strong focus-within:border-muted">
                    <Music2 className="size-3.5 shrink-0 text-subtle" />
                    <select
                      aria-label="Song"
                      value={music?.track.id ?? ""}
                      onChange={(e) => pickSong(tracks.find((t) => t.id === Number(e.target.value)) ?? null)}
                      className="min-w-0 flex-1 appearance-none truncate bg-transparent outline-none"
                    >
                      <option value="">None</option>
                      {tracks.map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name}
                          {t.is_default ? " (default)" : ""}
                        </option>
                      ))}
                    </select>
                    <ChevronsUpDown className="size-3.5 shrink-0 text-subtle" />
                  </label>
                )}
                <input ref={songFile} type="file" accept={SONG_TYPES} hidden onChange={(e) => (e.target.files?.[0] && songUpload.mutate({ body: { file: e.target.files[0], name: songName(e.target.files[0]) } }), (e.target.value = ""))} />
                <button className={cn(btn.secondary, "px-2.5 font-normal", !tracks.length && "flex-1")} disabled={songUpload.isPending} onClick={() => songFile.current?.click()}>
                  <Upload className="size-3.5" />
                  {songUpload.isPending ? "Uploading…" : tracks.length ? "Upload…" : "Upload a song…"}
                </button>
              </div>
              {music && (
                <>
                  <Labeled name="Song" value={`${music.volume}%`}>
                    <Slider aria-label="Song volume" min={0} max={100} step={5} value={[music.volume]} onValueChange={([v]) => setMusic({ ...music, volume: v })} />
                  </Labeled>
                  {clip.has_audio && (
                    <Labeled name="Clip's sound" value={`${music.clipVolume}%`}>
                      <Slider aria-label="Clip's sound volume" min={0} max={100} step={5} value={[music.clipVolume]} onValueChange={([v]) => setMusic({ ...music, clipVolume: v })} />
                    </Labeled>
                  )}
                </>
              )}
              <p className="text-sm text-subtle">
                {music ? "Mixed into the render, looped to the clip and faded out at its end. Instagram shows its name as the Reel's audio." : "None: the clip's own sound. Use songs you have the rights to."}
              </p>
            </div>

            <div className="space-y-2 px-4 py-3">
              <div className="flex items-center justify-between">
                <span className={label}>Cover</span>
                {cover && (
                  <button className="text-sm text-muted hover:text-fg" onClick={() => (setCover(null), setMode((m) => (m === "cover" ? "output" : m)))}>
                    Remove
                  </button>
                )}
              </div>
              <div className="flex items-center gap-3">
                {cover ? <img src={cover.url} alt="" className="h-16 w-9 shrink-0 rounded-sm object-cover" /> : <span className="h-16 w-9 shrink-0 rounded-sm border border-dashed border-line-strong" />}
                <div className="min-w-0 flex-1 space-y-1.5">
                  <input ref={pickCover} type="file" accept="image/*" hidden onChange={(e) => (e.target.files?.[0] && chooseCover(e.target.files[0]), (e.target.value = ""))} />
                  {covers.length > 0 && (
                    <label className="flex h-7 items-center gap-2 rounded border border-line bg-bg px-2 hover:border-line-strong focus-within:border-muted">
                      <select
                        aria-label="Saved covers"
                        value={cover?.from ?? ""}
                        onChange={(e) => chooseCover(covers.find((c) => c.id === Number(e.target.value))!)}
                        className={cn("min-w-0 flex-1 appearance-none truncate bg-transparent outline-none", cover?.from == null && "text-subtle")}
                      >
                        <option value="" disabled>
                          Saved covers…
                        </option>
                        {covers.map((c) => (
                          <option key={c.id} value={c.id}>
                            {c.name}
                            {c.is_default ? " (default)" : ""}
                          </option>
                        ))}
                      </select>
                      <ChevronsUpDown className="size-3.5 shrink-0 text-subtle" />
                    </label>
                  )}
                  <button className={cn(btn.secondary, "px-2.5 font-normal")} onClick={() => pickCover.current?.click()}>
                    <Upload className="size-3.5" />
                    Choose image…
                  </button>
                  <p className="text-balance text-sm leading-4 tabular-nums text-subtle">{cover ? "1080x1920 JPEG · grid shows the middle 3:4" : "None: Instagram picks a frame."}</p>
                </div>
              </div>
            </div>

            <div className="space-y-2 px-4 py-3">
              <div className="flex items-center gap-3">
                <span className={cn(label, "mr-auto")}>Caption</span>
                {captions.length > 0 && (
                  // an action, not a state: picking one replaces the text (filled like the template)
                  <label className="flex items-center gap-0.5 text-sm text-muted hover:text-fg">
                    <select aria-label="Saved captions" value="" onChange={(e) => setCaption(fill(captions.find((c) => c.id === Number(e.target.value))!.text, brand))} className="max-w-[128px] appearance-none truncate bg-transparent outline-none [field-sizing:content]">
                      <option value="" disabled>
                        Saved captions
                      </option>
                      {captions.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name}
                          {c.is_default ? " (default)" : ""}
                        </option>
                      ))}
                    </select>
                    <ChevronDown className="size-3.5" />
                  </label>
                )}
                <button className="text-sm text-muted hover:text-fg" onClick={() => setCaption(template(brand))}>
                  Reset to template
                </button>
              </div>
              <textarea
                aria-label="Caption"
                rows={9}
                value={caption}
                onChange={(e) => setCaption(e.target.value)}
                className="w-full resize-none rounded border border-line bg-bg px-2 py-1.5 leading-[18px] outline-none focus:border-muted"
              />
              <div className="flex justify-between text-sm tabular-nums text-subtle">
                <span className={cn(tags > HASHTAG_MAX && "text-bad")}>
                  {tags} / {HASHTAG_MAX} hashtags
                </span>
                <span className={cn(caption.length > CAPTION_MAX && "text-bad")}>
                  {caption.length} / {CAPTION_MAX}
                </span>
              </div>
            </div>
          </div>

          <div className="shrink-0 space-y-2 border-t border-line p-4">
            {long && (
              <p className="text-sm text-warn">
                Clip is {mmss(clip.duration_s)} long: Instagram Reels can be at most {MAX_REEL_SECONDS / 60} min. It still renders.
              </p>
            )}
            {error && <p className="text-sm text-bad">{error}</p>}
            <div className="flex justify-between text-sm tabular-nums text-subtle">
              <span>1080x1920 · H.264 · {mmss(clip.duration_s)}</span>
              <span>{brand?.name ?? "No logo"}</span>
            </div>
            <button className={cn(btn.primary, "w-full gap-2")} disabled={render.isPending || tooLong || !chosen} title={chosen ? undefined : "Choose a brand (or No logo) first"} onClick={submit}>
              {render.isPending ? "Queueing…" : "Render"}
              <kbd className="font-sans text-sm text-white/70">⌘↵</kbd>
            </button>
          </div>
        </aside>

        {/* RENDER QUEUE */}
        <aside className="flex w-[280px] shrink-0 flex-col border-l border-line">
          <div className="flex h-9 shrink-0 items-center justify-between border-b border-line px-3">
            <span className="font-medium">Renders for this clip</span>
            <span className="text-sm tabular-nums text-subtle">{renders.data?.length ?? ""}</span>
          </div>
          <ul className="min-h-0 flex-1 divide-y divide-line overflow-auto">
            {renders.data?.map((r) => (
              <RenderCard key={r.id} r={r} now={renders.dataUpdatedAt} name={brandName(r.brand_id)} thumb={r.thumbnail_url ?? clip.thumbnail_url} previewing={preview?.id === r.id} onPreview={() => setPreview(preview?.id === r.id ? null : r.id)} onLog={() => setLogFor(r.id)} />
            ))}
            {renders.data?.length === 0 && <li className="p-3 text-sm text-subtle">No renders yet. Set the logo, then Render.</li>}
          </ul>
        </aside>
      </section>
      {logFor != null && <LogDialog id={logFor} onClose={() => setLogFor(null)} />}
    </>
  )
}

/** A filter's preview: its colour layers blended over children, its CSS filter over the lot (CSSgram's way), the maths
 * render.py's filter_chain replays in ffmpeg. */
function Look({ look, className, style, children }: { look: FilterOut | null; className?: string; style?: CSSProperties; children: ReactNode }) {
  return (
    <div className={cn("isolate overflow-hidden", className)} style={{ ...style, filter: look?.css }}>
      {children}
      {look?.layers.map((l, i) => (
        <div key={i} className="absolute inset-0" style={{ background: l.color, mixBlendMode: l.mode, opacity: l.opacity }} />
      ))}
    </div>
  )
}

function Labeled({ name, value, children }: { name: string; value: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1.5">
      <div className="flex justify-between text-sm">
        <span className="text-muted">{name}</span>
        <span className="tabular-nums">{value}</span>
      </div>
      {children}
    </div>
  )
}

/** The 9:16 stage. Output: the source placed exactly as the render will (outputView), the logo and the Reels
 * chrome on top. Crop: the whole source (contain) with the 9:16 crop box. Cover: the chosen Reel cover, the
 * profile-grid guide on top. Preview: a rendered MP4. One <video> element across all four (hidden under the
 * cover), so switching never reloads the source. */
function Stage(props: {
  clip: ClipOut
  mode: "output" | "crop" | "cover" | "preview"
  previewSrc: string | null
  previewPoster: string | null
  cover: string | null
  previewLabel: string
  onExitPreview: () => void
  view: ReturnType<typeof outputView>
  look: FilterOut | null
  song: { url: string; volume: number; clipVolume: number } | null
  setMode: (m: "output" | "crop" | "cover") => void
  ig: boolean
  setIg: (v: boolean) => void
  caption: string
  handle?: string
  logo: ReactNode
  cropBox: ReactNode
  crop: Box
}) {
  const { clip, mode, view } = props
  const [col, size] = useSize<HTMLDivElement>()
  const video = useRef<HTMLVideoElement>(null)
  const [t, setT] = useState(0)
  const [dur, setDur] = useState(clip.duration_s ?? 0)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(true)
  const [broken, setBroken] = useState<string | null>(null)
  useEffect(() => void (mode === "cover" && video.current?.pause()), [mode]) // its transport is disabled there
  // The song plays with the video, at the render's volumes; it loops there too, so its position wraps. A render
  // (preview) has it mixed in already.
  const song = mode === "preview" ? null : props.song
  const songEl = useRef<HTMLAudioElement>(null)
  function follow() {
    const a = songEl.current
    const v = video.current
    if (!a || !v) return
    if (a.duration > 0 && Number.isFinite(a.duration)) a.currentTime = v.currentTime % a.duration
    if (v.paused) a.pause()
    else a.play().catch(() => {})
  }
  useEffect(() => {
    if (songEl.current) songEl.current.volume = (song?.volume ?? 0) / 100
    if (video.current) video.current.volume = (song?.clipVolume ?? 100) / 100
  })

  const src = mode === "preview" ? props.previewSrc! : clip.raw_url!
  // 420-576 px tall as the height allows, but never wider than the column (size is the content box, padding excluded)
  const H = Math.min(clamp(Math.floor(size.height - 96), 420, 576), Math.floor((size.width * 16) / 9))
  const frameW = (H * 9) / 16
  const ar = clip.width! / clip.height!
  const boxW = clamp(size.width, frameW, 592)
  const cw = Math.min(boxW, H * ar)
  const [fw, fh] = mode === "crop" ? [cw, cw / ar] : [frameW, H]
  const full = { left: 0, top: 0, width: "100%", height: "100%" }
  const pos =
    mode === "output"
      ? { left: `${view.left * 100}%`, top: `${view.top * 100}%`, width: `${view.width * 100}%`, height: `${view.height * 100}%` }
      : full
  const failed = broken === src
  const igOk = mode === "output" || mode === "cover"

  return (
    <div ref={col} className="flex min-w-0 flex-1 flex-col items-center justify-center gap-3 px-6">
      <div
        className={cn("relative flex shrink-0 items-center justify-center", mode === "crop" && "rounded bg-black ring-1 ring-line")}
        style={{ width: mode === "crop" ? boxW : frameW, height: H }}
      >
        {mode === "preview" && (
          <span className="absolute bottom-full left-0 mb-2 whitespace-nowrap text-xs uppercase tracking-wider text-subtle">
            Render <span className="normal-case tracking-normal tabular-nums">{props.previewLabel}</span>
          </span>
        )}
        {mode === "crop" && (
          <span className="absolute top-2.5 left-3 text-xs uppercase tracking-wider text-subtle">
            Source <span className="normal-case tracking-normal tabular-nums">{clip.width}x{clip.height}</span>
          </span>
        )}
        <div data-stage={mode} className={cn("relative shrink-0", mode !== "crop" && "rounded ring-1 ring-line")} style={{ width: fw, height: fh }}>
          <div className="absolute inset-0 overflow-hidden rounded bg-black">
            {/* a render has its filter baked in */}
            <Look look={mode === "preview" ? null : props.look} className="absolute" style={pos}>
              {failed ? (
                <img src={clip.thumbnail_url ?? ""} alt="" className="block size-full max-w-none" />
              ) : (
                <video
                  ref={video}
                  src={src}
                  poster={(mode === "preview" ? props.previewPoster : clip.thumbnail_url) ?? undefined}
                  muted={muted}
                  playsInline
                  loop
                  preload="auto"
                  className="block size-full max-w-none object-fill"
                  onTimeUpdate={(e) => setT(e.currentTarget.currentTime)}
                  onLoadedMetadata={(e) => (e.currentTarget.videoWidth ? setDur(e.currentTarget.duration) : setBroken(src))}
                  onPlay={() => (setPlaying(true), follow())}
                  onPause={() => (setPlaying(false), songEl.current?.pause())}
                  onSeeked={follow}
                  onError={() => setBroken(src)}
                />
              )}
            </Look>
            {song && <audio ref={songEl} src={song.url} loop muted={muted} preload="auto" onLoadedMetadata={follow} />}
            {mode === "output" && props.ig && (
              <>
                <div
                  className="pointer-events-none absolute border border-dashed border-white/20"
                  style={{ left: `${IG.side * 100}%`, right: `${IG.side * 100}%`, top: `${IG.top * 100}%`, bottom: `${IG.bottom * 100}%` }}
                />
                <ReelsChrome scale={frameW / 324} caption={props.caption} handle={props.handle} />
              </>
            )}
            {mode === "cover" && <img src={props.cover!} alt="" className="absolute inset-0 size-full" />}
            {mode === "cover" && props.ig && (
              <div className="pointer-events-none absolute inset-x-0 shadow-[0_0_0_9999px_rgba(0,0,0,0.6)]" style={{ top: `${GRID * 100}%`, bottom: `${GRID * 100}%` }}>
                <span className="absolute bottom-full left-3 mb-2 text-xs uppercase tracking-wider text-white/60">Profile grid 3:4</span>
              </div>
            )}
            {mode === "crop" && (
              <div
                className="pointer-events-none absolute shadow-[0_0_0_9999px_rgba(0,0,0,0.6)]"
                style={{ left: `${props.crop.x * 100}%`, top: `${props.crop.y * 100}%`, width: `${props.crop.w * 100}%`, height: `${props.crop.h * 100}%` }}
              />
            )}
            {failed && mode !== "cover" && (
              <div className="absolute inset-x-0 bottom-0 bg-black/80 px-3 py-2 text-sm text-muted">Preview unavailable in this browser; render still works.</div>
            )}
          </div>
          {mode === "output" && props.logo}
          {mode === "crop" && props.cropBox}
        </div>
        {mode === "crop" && <span className="absolute bottom-2.5 left-3 text-sm text-subtle">Drag to move · ratio locked to 9:16</span>}
      </div>

      <div className="flex h-7 items-center gap-2" style={{ width: frameW }}>
        <button
          aria-label={playing ? "Pause" : "Play"}
          disabled={failed || mode === "cover"}
          className="-ml-1.5 inline-grid size-7 place-items-center rounded text-fg hover:bg-hover disabled:text-subtle"
          onClick={() => (video.current?.paused ? video.current.play() : video.current?.pause())}
        >
          {playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
        </button>
        <Slider aria-label="Seek" disabled={failed || mode === "cover"} min={0} max={dur || 1} step={0.01} value={[t]} onValueChange={([v]) => video.current && (video.current.currentTime = v)} className="flex-1" />
        <span className="whitespace-nowrap text-sm tabular-nums text-muted">
          <span className="text-fg">{mmss(t)}</span> / {mmss(dur)}
        </span>
        <button aria-label={muted ? "Unmute" : "Mute"} disabled={mode === "cover"} className="-mr-1.5 inline-grid size-7 place-items-center rounded text-muted hover:bg-hover disabled:text-subtle" onClick={() => setMuted(!muted)}>
          {muted ? <VolumeX className="size-3.5" /> : <Volume2 className="size-3.5" />}
        </button>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2" style={{ width: frameW }}>
        {mode === "preview" ? (
          <button className={cn(btn.secondary, "font-normal")} onClick={props.onExitPreview}>
            <X className="size-3.5" />
            Back to editing
          </button>
        ) : (
          <div className="inline-flex h-7 rounded border border-line bg-panel p-0.5 text-sm">
            {(["output", "crop", "cover"] as const).map((m) => (
              <button
                key={m}
                aria-pressed={mode === m}
                disabled={m === "cover" && !props.cover}
                title={m === "cover" && !props.cover ? "Choose a cover image first" : undefined}
                className={cn("rounded-sm px-3 capitalize", mode === m ? "bg-raised font-medium text-fg" : "text-muted enabled:hover:text-fg disabled:opacity-40")}
                onClick={() => props.setMode(m)}
              >
                {m}
              </button>
            ))}
          </div>
        )}
        <button
          aria-pressed={props.ig}
          disabled={!igOk}
          title={igOk ? undefined : "Only drawn in Output and Cover mode"}
          className="inline-flex h-7 items-center gap-1.5 whitespace-nowrap rounded border border-line bg-raised px-2 text-sm text-fg hover:bg-hover disabled:opacity-40"
          onClick={() => props.setIg(!props.ig)}
        >
          {props.ig ? <Eye className="size-3.5" /> : <EyeOff className="size-3.5" />}
          IG overlay
        </button>
      </div>
    </div>
  )
}

/** Instagram Reels chrome, drawn at the mockup's 324x576 and scaled to the frame. Non-interactive, 40%. */
function ReelsChrome({ scale, caption, handle }: { scale: number; caption: string; handle?: string }) {
  const pct = (v: number) => `${v * 100}%`
  return (
    <div data-ig-overlay className="pointer-events-none absolute top-0 left-0 h-[576px] w-[324px] origin-top-left text-white opacity-40" style={{ transform: `scale(${scale})` }}>
      <div className="absolute inset-x-0 top-0 flex h-[46px] items-center justify-between px-3.5">
        <span className="text-[17px] font-semibold tracking-tight">Reels</span>
        <Camera className="size-4" strokeWidth={1.75} />
      </div>
      <div className="absolute right-0 bottom-[18px] flex flex-col items-center justify-end gap-3.5 text-[10px] font-medium tabular-nums" style={{ top: pct(IG.railTop), width: pct(IG.railW) }}>
        {[Heart, MessageCircle, Send].map((Icon, i) => (
          <Icon key={i} className="size-4" strokeWidth={1.75} />
        ))}
        <Ellipsis className="size-4" strokeWidth={1.75} />
        <span className="size-[22px] rounded-sm bg-white/40 ring-2 ring-white" />
      </div>
      <div className="absolute bottom-[18px] left-3 space-y-1.5" style={{ right: pct(IG.railW) }}>
        <div className="flex items-center gap-2">
          <span className="size-6 rounded-full bg-white/40" />
          <span className="truncate text-[12px] font-semibold">{handle ?? "your.account"}</span>
          <span className="inline-flex h-[22px] items-center rounded-md border border-white px-2 text-[11px] font-semibold">Follow</span>
        </div>
        <p className="line-clamp-2 text-[12px] leading-[16px]">{caption}</p>
        <div className="flex items-center gap-1.5 text-[11px]">
          <Music2 className="size-3" />
          Original audio
        </div>
      </div>
    </div>
  )
}

function RenderCard(props: { r: RenderOut; now: number; name: string; thumb: string | null; previewing: boolean; onPreview: () => void; onLog: () => void }) {
  const { r } = props
  const qc = useQueryClient()
  const retry = useMutation({
    ...retryRenderMutation(),
    onSuccess: () => qc.invalidateQueries({ queryKey: listRendersQueryKey({ query: { clip_id: r.source_clip_id } }) }),
    onError: (e) => alert(`Retry failed: ${errorText(e)}`),
  })
  const remove = useMutation({
    ...deleteRenderMutation(),
    onSuccess: () => qc.invalidateQueries({ queryKey: listRendersQueryKey() }), // this clip's list and the Library's counts
    onError: (e) => alert(`Couldn't delete render ${r.id}: ${errorText(e)}`),
  })
  const del = (
    <button
      className="inline-grid size-6 place-items-center rounded text-muted hover:bg-hover hover:text-fg disabled:text-subtle"
      title="Delete render"
      aria-label="Delete render"
      disabled={remove.isPending}
      onClick={() => confirm(`Delete render ${r.id}? Its MP4 is deleted too.`) && remove.mutate({ path: { render_id: r.id } })}
    >
      <Trash2 className="size-3.5" />
    </button>
  )
  const live = LIVE.has(r.status)
  const [born] = useState(r.status) // settle only on a READY seen happening, not on load
  const action = "inline-flex h-6 items-center gap-1 rounded px-1.5 text-sm text-fg hover:bg-hover disabled:text-subtle"
  return (
    <li
      data-render={r.id}
      data-status={r.status}
      className={cn("flex gap-2.5 p-3", props.previewing && "bg-raised shadow-[inset_2px_0_0_var(--color-fg)]", born !== "READY" && r.status === "READY" && "settle")}
    >
      <img
        src={props.thumb ?? ""}
        alt=""
        className={cn("h-16 w-9 shrink-0 rounded-sm bg-raised object-cover", live && "opacity-50", r.status === "FAILED" && "opacity-40 grayscale")}
      />
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex items-baseline justify-between gap-2">
          <span className="flex min-w-0 items-baseline gap-1.5">
            <span className="truncate font-medium">{props.name}</span>
            <span className="font-mono text-xs text-subtle">#{r.id}</span>
          </span>
          <span className="whitespace-nowrap text-sm tabular-nums text-subtle">{ago(r.created_at)}</span>
        </div>
        <div className="truncate text-sm text-muted">
          {placement(r)}
          {r.cover_url && " · cover"}
        </div>
        {live && (
          <>
            <div className="flex items-center gap-2">
              <Chip tone="accent" spin={r.status === "RENDERING"}>
                {r.status === "PENDING" ? "Queued" : "Rendering"}
              </Chip>
              {r.status === "RENDERING" && <span className="text-sm tabular-nums text-muted">{mmss(Math.max(0, props.now - Date.parse(r.updated_at)) / 1000)} elapsed</span>}
            </div>
            <div className="relative mt-2.5 h-1 overflow-hidden rounded-full bg-line-strong">
              {r.status === "RENDERING" && <div className="indeterminate absolute inset-y-0 left-0 w-1/3 rounded-full bg-accent" />}
            </div>
          </>
        )}
        {r.status === "READY" && !r.output_url && (
          // Library > Published > Free up space deleted its MP4: it's on Instagram, and can't be posted again
          <div className="flex items-center gap-2">
            <Chip tone="neutral">MP4 deleted</Chip>
            <span className="truncate text-sm tabular-nums text-muted">{mmss(r.duration_s)} · published, freed to save space</span>
          </div>
        )}
        {r.status === "READY" && r.output_url && (
          <>
            <div className="flex items-center gap-2">
              <Chip tone="ok" dot>
                Ready
              </Chip>
              <span className="text-sm tabular-nums text-muted">
                {mmss(r.duration_s)} · {mb(r.size_bytes ?? 0)}
              </span>
            </div>
            <div className="-ml-1.5 flex items-center gap-1">
              {/* a toggle: fixed label, pressed look; a second click leaves the preview */}
              <button className={cn(action, props.previewing && "bg-hover")} aria-pressed={props.previewing} onClick={props.onPreview}>
                <Play className="size-3" />
                Preview
              </button>
              <SchedulePopover r={r}>
                <button className={action}>
                  <CalendarPlus className="size-3" />
                  Schedule…
                </button>
              </SchedulePopover>
              <a href={r.output_url ?? ""} download className="ml-auto inline-grid size-6 place-items-center rounded text-muted hover:bg-hover" title="Download MP4">
                <Download className="size-3.5" />
              </a>
              {del}
            </div>
          </>
        )}
        {r.status === "FAILED" && (
          <>
            <div className="flex min-w-0 items-center gap-2">
              <Chip tone="bad">Failed</Chip>
              <span className="truncate font-mono text-xs text-subtle" title={r.error_code ?? undefined}>
                {r.error_code}
              </span>
            </div>
            <div className="truncate text-sm text-muted">{CAUSES[r.error_code ?? ""] ?? "Render failed"}</div>
            <div className="-ml-1.5 flex items-center gap-1">
              <button className={action} onClick={props.onLog}>
                <FileText className="size-3" />
                View log
              </button>
              <button className={action} disabled={retry.isPending} onClick={() => retry.mutate({ path: { render_id: r.id } })}>
                <RotateCw className="size-3" />
                Retry
              </button>
              <span className="ml-auto" />
              {del}
            </div>
          </>
        )}
      </div>
    </li>
  )
}

/** "Top right · 22% · 9:16 crop" / "Full frame · Juno" (no logo: the card already says so). */
function placement(r: RenderOut) {
  const c = r.crop_config
  const crop = (!c || (c.w > 0.999 && c.h > 0.999) ? "full frame" : "9:16 crop") + (r.filter ? ` · ${r.filter}` : "") + (r.music ? ` · ♫ ${r.music.name ?? "song"}` : "")
  const o = r.overlay_config
  if (r.brand_id == null || !o) return crop[0].toUpperCase() + crop.slice(1)
  // ponytail: the row buckets the logo's centre, estimated as if the logo were square (renders don't carry its aspect);
  // right for square and wide logos at usual sizes, a tall portrait logo can read one row up. Exact: logo size on BrandOut.
  const cy = o.y + (o.w * OUT_W) / OUT_H / 2
  const col = clamp(Math.floor((o.x + o.w / 2) * 3), 0, 2)
  const row = clamp(Math.floor(((cy - IG.top) / (1 - IG.top - IG.bottom)) * 3), 0, 2)
  return `${SNAPS[row * 3 + col]} · ${Math.round(o.w * 100)}% · ${crop}`
}

function LogDialog({ id, onClose }: { id: number; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null)
  const q = useQuery(getRenderOptions({ path: { render_id: id } }))
  useEffect(() => ref.current?.showModal(), [])
  return (
    <dialog ref={ref} onClose={onClose} className="m-auto w-[760px] max-w-[90vw] rounded-md border border-line bg-panel p-0 text-fg shadow-2xl backdrop:bg-black/60">
      <div className="flex h-10 items-center justify-between border-b border-line px-4">
        <span className="font-medium">
          ffmpeg log · <span className="font-mono text-sm">render {id}</span> {q.data?.error_code && <span className="font-mono text-sm text-bad">{q.data.error_code}</span>}
        </span>
        <form method="dialog">
          <button className={btn.icon} aria-label="Close">
            <X className="size-4" />
          </button>
        </form>
      </div>
      <pre className="max-h-[60vh] overflow-auto p-4 font-mono text-xs leading-4 whitespace-pre-wrap text-muted">
        {q.isPending ? "Loading…" : q.isError ? errorText(q.error) : q.data?.ffmpeg_log || "No log."}
      </pre>
    </dialog>
  )
}
