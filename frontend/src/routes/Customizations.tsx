import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Archive, ArchiveRestore, ArrowUpRight, Image, Info, Link2, Pencil, Plus, Trash2, Upload, X } from "lucide-react"
import { useEffect, useMemo, useRef, useState, type DragEvent, type ReactNode } from "react"
import { Link, Navigate, NavLink, useParams } from "react-router"

import type { BrandOut, CaptionOut, ClipOut } from "@/api"
import {
  createBrandMutation,
  createCaptionMutation,
  deleteCaptionMutation,
  deleteCoverMutation,
  deleteTrackMutation,
  listBrandsOptions,
  listBrandsQueryKey,
  listCaptionsOptions,
  listCaptionsQueryKey,
  listClipsOptions,
  listCoversOptions,
  listCoversQueryKey,
  listTracksOptions,
  listTracksQueryKey,
  updateBrandMutation,
  updateCaptionMutation,
  updateCoverMutation,
  updateTrackMutation,
  uploadBrandLogoMutation,
  uploadCoverMutation,
  uploadTrackMutation,
} from "@/api/@tanstack/react-query.gen"
import { Chip, Drawer, Empty, Header } from "@/components/bits"
import { Switch } from "@/components/ui/switch"
import { coverJpeg } from "@/lib/cover"
import { BROWSER_TZ, dayLabel, localParts, today } from "@/lib/schedule"
import { CAPTION_MAX, HASHTAG_MAX, SONG_TYPES, cn, errorText, hashtagCount, btn, label, songName } from "@/lib/utils"

/** /customizations/:tab: the brands, saved captions, saved covers and songs the Editor offers; each has at most one
 * default, which the Editor preselects. */
export function Customizations() {
  const { tab } = useParams()
  if (tab === "brands") return <Brands />
  if (tab === "captions") return <Captions />
  if (tab === "covers") return <Covers />
  if (tab === "music") return <Music />
  return <Navigate to="/customizations/brands" replace />
}

/** The page header: title and tabs with their counts, then the open tab's own actions. */
function Tabs({ children }: { children: ReactNode }) {
  const counts = {
    brands: useQuery(listBrandsOptions()).data?.length,
    captions: useQuery(listCaptionsOptions()).data?.length,
    covers: useQuery(listCoversOptions()).data?.length,
    music: useQuery(listTracksOptions()).data?.length,
  }
  return (
    <Header>
      <div className="flex items-center gap-4">
        <h1 className="text-lg font-semibold">Customizations</h1>
        <nav className="flex h-7 items-center rounded border border-line bg-panel p-0.5">
          {(["brands", "captions", "covers", "music"] as const).map((t) => (
            <NavLink
              key={t}
              to={`/customizations/${t}`}
              className={({ isActive }) => cn("flex h-full items-center gap-1.5 rounded-sm px-2.5 capitalize", isActive ? "bg-raised font-medium text-fg" : "text-muted hover:text-fg")}
            >
              {t}
              <span className="text-sm font-normal tabular-nums text-muted">{counts[t] ?? ""}</span>
            </NavLink>
          ))}
        </nav>
      </div>
      <div className="flex items-center gap-3">{children}</div>
    </Header>
  )
}

function Brands() {
  const qc = useQueryClient()
  const [showArchived, setShowArchived] = useState(false)
  const [open, setOpen] = useState<number | "new" | null>(null)
  const [session, setSession] = useState(0) // drawer identity: a new brand keeps its drawer (and errors) once created
  const show = (id: number | "new") => (setOpen(id), setSession((n) => n + 1))
  const active = useQuery(listBrandsOptions())
  const archived = useQuery(listBrandsOptions({ query: { archived: true } }))
  const clips = useQuery(listClipsOptions())
  const ready = clips.data?.find((c) => c.status === "READY") // newest first
  const patch = useMutation({
    ...updateBrandMutation(),
    onSuccess: () => qc.invalidateQueries({ queryKey: listBrandsQueryKey() }),
    onError: (e) => alert(`Couldn't change auto-approve: ${errorText(e)}`),
  })
  const rows = [...(active.data ?? []), ...(showArchived ? (archived.data ?? []) : [])]
  const editing = open === "new" ? null : rows.find((b) => b.id === open)

  return (
    <>
      <Tabs>
        <label className="flex items-center gap-2 text-sm text-muted">
          <Switch checked={showArchived} onCheckedChange={setShowArchived} />
          Show archived <span className="tabular-nums text-subtle">{archived.data?.length ?? 0}</span>
        </label>
        <div className="h-4 w-px bg-line" />
        <button className={cn(btn.primary, "pl-2")} onClick={() => show("new")}>
          <Plus className="size-3.5" />
          New brand
        </button>
      </Tabs>
      <section className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto">
          <table className="w-full table-fixed border-collapse">
            <colgroup>
              <col className="w-[68px]" />
              <col className="w-[200px]" />
              <col />
              <col className="w-[130px]" />
              <col className="w-[86px]" />
              <col className="w-[116px]" />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-bg">
              <tr className="h-8 whitespace-nowrap border-b border-line text-left text-xs uppercase tracking-wider text-subtle [&>th]:px-2 [&>th]:font-medium [&>th:last-child]:pr-4">
                <th className="!pl-4">Logo</th>
                <th>Name</th>
                <th>Caption template</th>
                <th>Link</th>
                <th>Placement</th>
                <th>Auto-approve</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => {
                const gone = !!b.archived_at
                return (
                  <tr
                    key={b.id}
                    data-brand={b.id}
                    onClick={() => show(b.id)}
                    className={cn("h-14 cursor-pointer border-b border-line [&>td]:px-2 [&>td:last-child]:pr-4", open === b.id ? "bg-raised" : "hover:bg-panel", gone && "text-subtle")}
                  >
                    <td className="!pl-4">
                      <LogoTile url={b.logo_url} className={cn("size-12", gone && "opacity-50 grayscale")} />
                    </td>
                    <td>
                      {/* a button for the keyboard; its click bubbles to the row's onClick */}
                      <div className="flex min-w-0 items-center gap-1.5">
                        <button className={cn("min-w-0 truncate rounded-sm text-left font-medium", gone && "text-muted")}>{b.name}</button>
                        {b.is_default && <Chip tone="neutral">Default</Chip>}
                      </div>
                      {gone ? (
                        <div className="flex items-center gap-1 text-sm tabular-nums">
                          <Archive className="size-3" />
                          Archived {shortDay(b.archived_at!)}
                        </div>
                      ) : (
                        <div className="font-mono text-xs text-subtle">brand {b.id}</div>
                      )}
                    </td>
                    <td>
                      <div className={cn("truncate", !gone && "text-muted")}>
                        <Template text={b.caption_template} />
                      </div>
                    </td>
                    <td className="truncate text-sm text-muted">{b.link?.replace(/^https?:\/\/(www\.)?/, "") ?? "—"}</td>
                    <td>
                      <Placement brand={b} className="h-12 w-[27px]" />
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <Switch
                        aria-label={`Auto-approve ${b.name}`}
                        checked={b.auto_approve}
                        disabled={gone}
                        onCheckedChange={(v) => patch.mutate({ path: { brand_id: b.id }, body: { auto_approve: v } })}
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
          {(active.isError || archived.isError) && <p className="p-8 text-center text-bad">Couldn't load brands: {errorText(active.error ?? archived.error)}</p>}
          {active.data?.length === 0 && !showArchived && <p className="p-8 text-center text-muted">No brands yet. Create one with its logo to start rendering.</p>}
          {showArchived && (
            <p className="flex items-center gap-1.5 px-4 py-3 text-sm text-subtle">
              <Info className="size-3.5" />
              Archived brands keep their renders and post history, but can't be picked in the editor.
            </p>
          )}
        </div>
        {open != null && (open === "new" || editing) && (
          <BrandDrawer key={session} brand={editing ?? null} ready={ready} onClose={() => setOpen(null)} onCreated={(id) => setOpen(id)} />
        )}
      </section>
    </>
  )
}

/** "26 Sep", with the year once it isn't this year. */
function shortDay(iso: string) {
  const d = localParts(iso, BROWSER_TZ).date
  return dayLabel(d, { year: d.slice(0, 4) !== today().slice(0, 4) })
}

function LogoTile({ url, className }: { url: string | null; className?: string }) {
  return (
    // flex, not grid: a grid's auto track grows to a tall logo, so max-h-full would never bind
    <div className={cn("checker flex items-center justify-center rounded-sm border border-line p-1", className)}>
      {url ? <img src={url} alt="" className="max-h-full max-w-full object-contain" /> : <Image className="size-4 text-subtle" />}
    </div>
  )
}

/** Caption template with {link} / {creator} drawn as mono chips. */
function Template({ text }: { text: string | null }) {
  if (!text) return <span className="text-subtle">—</span>
  return text.split(/(\{link\}|\{creator\})/).map((part, i) =>
    i % 2 ? (
      <span key={i} className="rounded-sm border border-line bg-raised px-1 font-mono text-xs text-fg">
        {part}
      </span>
    ) : (
      part
    )
  )
}

/** The brand's default logo placement on a 9:16 frame: width w of the frame, height from the logo (as the render). */
function Placement({ brand, className, backdrop }: { brand: BrandOut; className: string; backdrop?: string | null }) {
  const o = brand.default_overlay_config
  return (
    <div className={cn("relative overflow-hidden rounded-sm border border-line-strong bg-raised", className)}>
      {backdrop && <img src={backdrop} alt="" className="size-full object-cover opacity-50" />}
      {brand.logo_url ? (
        <img src={brand.logo_url} alt="" className="absolute max-w-none" style={{ left: `${o.x * 100}%`, top: `${o.y * 100}%`, width: `${o.w * 100}%`, opacity: o.opacity }} />
      ) : (
        <span className="absolute rounded-[1px] bg-muted" style={{ left: `${o.x * 100}%`, top: `${o.y * 100}%`, width: `${o.w * 100}%`, height: "6%" }} />
      )}
    </div>
  )
}

function BrandDrawer({ brand, ready, onClose, onCreated }: { brand: BrandOut | null; ready?: ClipOut; onClose: () => void; onCreated: (id: number) => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(brand?.name ?? "")
  const [link, setLink] = useState(brand?.link ?? "")
  const [template, setTemplate] = useState(brand?.caption_template ?? "")
  const [autoApprove, setAutoApprove] = useState(brand?.auto_approve ?? false)
  const [isDefault, setIsDefault] = useState(brand?.is_default ?? false)
  const [file, setFile] = useState<File | null>(null)
  const [dims, setDims] = useState("")
  const [error, setError] = useState("")
  const pick = useRef<HTMLInputElement>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: listBrandsQueryKey() })
  const create = useMutation(createBrandMutation())
  const update = useMutation(updateBrandMutation())
  const logo = useMutation(uploadBrandLogoMutation())
  const busy = create.isPending || update.isPending || logo.isPending

  const fileUrl = useObjectUrl(file)
  const shown = fileUrl ?? brand?.logo_url ?? null
  const values = { name: name.trim(), link: link.trim() || null, caption_template: template.trim() ? template : null, auto_approve: autoApprove, is_default: isDefault }
  const base = brand ?? { name: "", link: null, caption_template: null, auto_approve: false, is_default: false }
  const dirty =
    !!file || values.name !== base.name || values.link !== base.link || values.caption_template !== base.caption_template || autoApprove !== base.auto_approve || isDefault !== base.is_default
  const close = () => (!dirty || confirm("Discard unsaved changes?")) && onClose()

  async function save() {
    if (!values.name || busy) return
    setError("")
    let id = brand?.id
    try {
      if (id == null) id = (await create.mutateAsync({ body: values })).id
      else await update.mutateAsync({ path: { brand_id: id }, body: values })
      if (file && !(await transparent(file))) throw new Error("logo PNG has no transparent pixels (its alpha is opaque everywhere)")
      if (file) await logo.mutateAsync({ path: { brand_id: id }, body: { file } })
      setFile(null)
    } catch (e) {
      setError(errorText(e))
    }
    await refresh()
    if (!brand && id != null) onCreated(id)
  }
  async function archive(v: boolean) {
    try {
      await update.mutateAsync({ path: { brand_id: brand!.id }, body: { archived: v } })
      await refresh()
      onClose()
    } catch (e) {
      setError(errorText(e))
    }
  }
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => (e.metaKey || e.ctrlKey) && e.key === "s" && (e.preventDefault(), save())
    addEventListener("keydown", onKey)
    return () => removeEventListener("keydown", onKey)
  })

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0])
  }
  const tags = hashtagCount(template)

  return (
    <Drawer label={brand ? brand.name : "New brand"} onClose={close} className="w-[440px]">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
        <div className="flex min-w-0 items-baseline gap-2">
          <h2 className="truncate text-md font-semibold">{brand ? brand.name : "New brand"}</h2>
          {brand && <span className="font-mono text-xs text-subtle">brand {brand.id}</span>}
        </div>
        <div className="flex items-center gap-2">
          {brand && dirty && (
            <span className="flex items-center gap-1.5 text-sm text-subtle">
              <span className="size-1.5 rounded-full bg-muted" />
              Unsaved changes
            </span>
          )}
          <button className={btn.icon} aria-label="Close" onClick={close}>
            <X className="size-4" />
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto px-4 py-3">
        <div>
          <div className={cn(label, "mb-1.5")}>Logo</div>
          <div className="rounded border border-dashed border-line-strong p-2" onDragOver={(e) => e.preventDefault()} onDrop={onDrop}>
            <div className="checker grid h-[84px] place-items-center rounded-sm">
              {shown ? (
                <img src={shown} alt="" className="max-h-16 max-w-[200px] object-contain" onLoad={(e) => setDims(`${e.currentTarget.naturalWidth}×${e.currentTarget.naturalHeight}`)} />
              ) : (
                <span className="text-sm text-subtle">Drop a PNG here</span>
              )}
            </div>
            <div className="mt-2 flex items-center gap-2">
              <Image className="size-4 shrink-0 text-subtle" />
              <div className="min-w-0 flex-1">
                <div className="truncate text-sm">{file?.name ?? (brand?.logo_url ? "Current logo" : "No logo yet")}</div>
                <div className="text-xs tabular-nums text-subtle">
                  {shown ? `PNG · ${dims}${file ? ` · ${Math.ceil(file.size / 1024)} KB` : ""}` : "PNG with a transparent background (alpha) is required"}
                </div>
              </div>
              <input ref={pick} type="file" accept="image/png" hidden onChange={(e) => (setFile(e.target.files?.[0] ?? null), (e.target.value = ""))} />
              <button className={cn(btn.secondary, "px-2.5 font-normal")} onClick={() => pick.current?.click()}>
                <Upload className="size-3.5" />
                {shown ? "Replace" : "Choose PNG"}
              </button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-[1fr_1.35fr] gap-3">
          <label>
            <span className={cn(label, "mb-1.5 block")}>Name</span>
            <input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} className="h-7 w-full rounded border border-line-strong bg-bg px-2 outline-none focus:border-muted" />
          </label>
          <label>
            <span className={cn(label, "mb-1.5 block")}>Link</span>
            <span className="flex h-7 items-center gap-1.5 rounded border border-line-strong bg-bg px-2 focus-within:border-muted">
              <Link2 className="size-3.5 shrink-0 text-subtle" />
              <input aria-label="Link" value={link} onChange={(e) => setLink(e.target.value)} placeholder="https://" className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-subtle" />
            </span>
          </label>
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className={label}>Caption template</span>
            <span className={cn("text-xs tabular-nums text-subtle", (template.length > CAPTION_MAX || tags > HASHTAG_MAX) && "text-bad")}>
              {template.length} / {CAPTION_MAX} · {tags} / {HASHTAG_MAX} hashtags
            </span>
          </div>
          <textarea
            aria-label="Caption template"
            rows={4}
            value={template}
            onChange={(e) => setTemplate(e.target.value)}
            className="w-full resize-none rounded border border-line-strong bg-bg px-2 py-1.5 leading-[18px] outline-none focus:border-muted"
          />
          <div className="mt-1.5 flex items-center gap-3 text-sm text-subtle">
            <span className="flex items-center gap-1.5">
              <Template text="{link}" />
              the link above
            </span>
            <span className="flex items-center gap-1.5">
              <Template text="{creator}" />
              @handle of the source creator
            </span>
          </div>
        </div>

        <label className="flex items-start justify-between gap-4 rounded border border-line px-3 py-2.5">
          <span>
            <span className="block font-medium">Auto-approve</span>
            <span className="text-sm text-muted">Posts for this brand skip Draft and go straight to Scheduled</span>
          </span>
          <Switch className="mt-0.5" checked={autoApprove} onCheckedChange={setAutoApprove} />
        </label>

        <label className="flex items-start justify-between gap-4 rounded border border-line px-3 py-2.5">
          <span>
            <span className="block font-medium">Default brand</span>
            <span className="text-sm text-muted">{brand?.archived_at ? "An archived brand can't be the default" : "Preselected in the Editor"}</span>
          </span>
          <Switch className="mt-0.5" checked={isDefault} disabled={!!brand?.archived_at} onCheckedChange={setIsDefault} />
        </label>

        {brand && (
          <div>
            <div className={cn(label, "mb-1.5")}>Default placement</div>
            <div className="flex gap-4">
              <Placement brand={brand} backdrop={ready?.thumbnail_url} className="h-[192px] w-[108px] shrink-0 bg-black" />
              <div className="flex min-w-0 flex-1 flex-col">
                <dl className="grid grid-cols-[72px_1fr] gap-y-1.5 text-sm tabular-nums [&>dt]:text-subtle">
                  <dt>Position</dt>
                  <dd>
                    x {brand.default_overlay_config.x.toFixed(2)} · y {brand.default_overlay_config.y.toFixed(2)}
                  </dd>
                  <dt>Scale</dt>
                  <dd>{Math.round(brand.default_overlay_config.w * 100)}% of width</dd>
                  <dt>Opacity</dt>
                  <dd>{Math.round((brand.default_overlay_config.opacity ?? 1) * 100)}%</dd>
                </dl>
                <p className="mt-3 text-sm text-subtle">Applied when this brand is picked in the editor. Tweaking one clip doesn't change the default.</p>
                {ready && brand.logo_url && !brand.archived_at ? (
                  <Link
                    to={`/editor/${ready.id}?brand=${brand.id}`}
                    className="mt-auto inline-flex items-center gap-1 self-start underline decoration-line-strong underline-offset-4 hover:decoration-fg"
                  >
                    Edit in editor
                    <ArrowUpRight className="size-3.5" />
                  </Link>
                ) : (
                  <span className="mt-auto text-sm text-subtle">{brand.logo_url ? "Needs a READY clip to edit on." : "Upload a logo to edit the placement."}</span>
                )}
              </div>
            </div>
          </div>
        )}
        {error && <p className="text-sm text-bad">{error}</p>}
      </div>

      <div className="flex h-12 shrink-0 items-center justify-between border-t border-line px-4">
        {brand ? (
          brand.archived_at ? (
            <button className={btn.ghost} onClick={() => archive(false)}>
              <ArchiveRestore className="size-3.5" />
              Unarchive
            </button>
          ) : (
            <button className="flex h-7 items-center gap-1.5 rounded px-2.5 text-bad hover:bg-bad/10" onClick={() => archive(true)}>
              <Archive className="size-3.5" />
              Archive
            </button>
          )
        ) : (
          <span />
        )}
        <div className="flex items-center gap-2">
          <span className="text-sm text-subtle">⌘S</span>
          <button className={cn(btn.primary, "px-4")} disabled={!values.name || !dirty || busy} onClick={save}>
            {busy ? "Saving…" : brand ? "Save" : "Create"}
          </button>
        </div>
      </div>
    </Drawer>
  )
}

/** The api only checks the PNG has an alpha channel (or tRNS); a design tool's "RGBA, opaque background" export
 * passes that and renders as a box. So look for one pixel that isn't fully opaque. Undecodable: the api decides. */
async function transparent(file: File) {
  const img = await createImageBitmap(file).catch(() => null)
  if (!img) return true
  const g = new OffscreenCanvas(img.width, img.height).getContext("2d")!
  g.drawImage(img, 0, 0)
  const px = g.getImageData(0, 0, img.width, img.height).data
  for (let i = 3; i < px.length; i += 4) if (px[i] < 255) return true
  return false
}

function useObjectUrl(file: File | null) {
  const url = useMemo(() => file && URL.createObjectURL(file), [file])
  useEffect(() => () => void (url && URL.revokeObjectURL(url)), [url])
  return url
}

function Captions() {
  const qc = useQueryClient()
  const list = useQuery(listCaptionsOptions())
  const [open, setOpen] = useState<number | "new" | null>(null)
  const refresh = () => qc.invalidateQueries({ queryKey: listCaptionsQueryKey() })
  const onError = (e: unknown) => alert(errorText(e))
  const patch = useMutation({ ...updateCaptionMutation(), onSuccess: refresh, onError })
  const remove = useMutation({ ...deleteCaptionMutation(), onSuccess: refresh, onError })
  const editing = open === "new" ? null : list.data?.find((c) => c.id === open)

  return (
    <>
      <Tabs>
        <button className={cn(btn.primary, "pl-2")} onClick={() => setOpen("new")}>
          <Plus className="size-3.5" />
          New caption
        </button>
      </Tabs>
      {list.data?.length === 0 ? (
        <Empty>No saved captions yet. The default one fills the Editor's caption when the brand has no template.</Empty>
      ) : (
        <section className="min-h-0 flex-1 overflow-auto">
          <table className="w-full table-fixed border-collapse">
            <colgroup>
              <col className="w-[216px]" />
              <col />
              <col className="w-[156px]" />
              <col className="w-[84px]" />
              <col className="w-[196px]" />
            </colgroup>
            <thead className="sticky top-0 z-10 bg-bg">
              <tr className="h-8 whitespace-nowrap border-b border-line text-left text-xs uppercase tracking-wider text-subtle [&>th]:px-2 [&>th]:font-medium">
                <th className="!pl-4">Name</th>
                <th>Caption</th>
                <th>Length</th>
                <th>Default</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.data?.map((c) => (
                <tr
                  key={c.id}
                  data-caption={c.id}
                  onClick={() => setOpen(c.id)}
                  className={cn("group/row h-14 cursor-pointer border-b border-line [&>td]:px-2 [&>td:last-child]:pr-4", open === c.id ? "bg-raised" : "hover:bg-panel")}
                >
                  <td className="!pl-4">
                    {/* a button for the keyboard; its click bubbles to the row's onClick */}
                    <button className="block max-w-full truncate rounded-sm text-left font-medium">{c.name}</button>
                  </td>
                  <td>
                    <div className="line-clamp-2 text-sm leading-4 whitespace-pre-line text-muted">
                      <Template text={c.text} />
                    </div>
                  </td>
                  <td className="text-sm whitespace-nowrap tabular-nums text-muted">
                    {c.text.length} / {CAPTION_MAX} · {hashtagCount(c.text)} tags
                  </td>
                  <td>{c.is_default && <Chip tone="neutral">Default</Chip>}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    {/* hover or keyboard focus reveals them, as in the Library */}
                    <div className="flex items-center justify-end gap-1 opacity-0 group-hover/row:opacity-100 group-focus-within/row:opacity-100">
                      <button className={btn.ghost} onClick={() => patch.mutate({ path: { caption_id: c.id }, body: { is_default: !c.is_default } })}>
                        {c.is_default ? "Clear default" : "Make default"}
                      </button>
                      <button className={btn.ghost} onClick={() => setOpen(c.id)}>
                        Edit
                      </button>
                      <button className={btn.icon} title="Delete" onClick={() => confirm(`Delete the caption ${c.name}?`) && remove.mutate({ path: { caption_id: c.id } })}>
                        <Trash2 className="size-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {list.isError && <p className="p-8 text-center text-bad">Couldn't load captions: {errorText(list.error)}</p>}
        </section>
      )}
      {open != null && (open === "new" || editing) && <CaptionDrawer key={open} caption={editing ?? null} onClose={() => setOpen(null)} />}
    </>
  )
}

function CaptionDrawer({ caption, onClose }: { caption: CaptionOut | null; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState(caption?.name ?? "")
  const [text, setText] = useState(caption?.text ?? "")
  const [isDefault, setIsDefault] = useState(caption?.is_default ?? false)
  const [error, setError] = useState("")
  const create = useMutation(createCaptionMutation())
  const update = useMutation(updateCaptionMutation())
  const remove = useMutation(deleteCaptionMutation())
  const busy = create.isPending || update.isPending || remove.isPending
  const values = { name: name.trim(), text, is_default: isDefault }
  const base = caption ?? { name: "", text: "", is_default: false }
  const dirty = values.name !== base.name || text !== base.text || isDefault !== base.is_default
  const tags = hashtagCount(text)
  const tooLong = text.length > CAPTION_MAX || tags > HASHTAG_MAX // the Editor won't render with it
  const close = () => (!dirty || confirm("Discard unsaved changes?")) && onClose()

  async function run(call: () => Promise<unknown>) {
    setError("")
    try {
      await call()
      await qc.invalidateQueries({ queryKey: listCaptionsQueryKey() })
      onClose()
    } catch (e) {
      setError(errorText(e))
    }
  }
  const save = () =>
    values.name && dirty && !tooLong && !busy && run(() => (caption ? update.mutateAsync({ path: { caption_id: caption.id }, body: values }) : create.mutateAsync({ body: values })))
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => (e.metaKey || e.ctrlKey) && e.key === "s" && (e.preventDefault(), save())
    addEventListener("keydown", onKey)
    return () => removeEventListener("keydown", onKey)
  })

  return (
    <Drawer label={caption ? caption.name : "New caption"} onClose={close} className="w-[440px]">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
        <h2 className="truncate text-md font-semibold">{caption ? caption.name : "New caption"}</h2>
        <button className={btn.icon} aria-label="Close" onClick={close}>
          <X className="size-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-auto px-4 py-3">
        <label className="block">
          <span className={cn(label, "mb-1.5 block")}>Name</span>
          <input aria-label="Name" maxLength={100} value={name} onChange={(e) => setName(e.target.value)} className="h-7 w-full rounded border border-line-strong bg-bg px-2 outline-none focus:border-muted" />
        </label>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <span className={label}>Caption</span>
            <span className={cn("text-xs tabular-nums text-subtle", tooLong && "text-bad")}>
              {text.length} / {CAPTION_MAX} · {tags} / {HASHTAG_MAX} hashtags
            </span>
          </div>
          <textarea
            aria-label="Caption"
            rows={9}
            value={text}
            onChange={(e) => setText(e.target.value)}
            className="w-full resize-none rounded border border-line-strong bg-bg px-2 py-1.5 leading-[18px] outline-none focus:border-muted"
          />
          <div className="mt-1.5 space-y-1 text-sm text-subtle">
            <p>Filled in by the Editor:</p>
            <p className="flex items-center gap-3">
              <span className="flex items-center gap-1.5">
                <Template text="{link}" />
                the brand's link
              </span>
              <span className="flex items-center gap-1.5">
                <Template text="{creator}" />
                @handle of the source creator
              </span>
            </p>
          </div>
        </div>

        <label className="flex items-start justify-between gap-4 rounded border border-line px-3 py-2.5">
          <span>
            <span className="block font-medium">Default caption</span>
            <span className="text-sm text-muted">Preselected in the Editor when the brand has no caption template</span>
          </span>
          <Switch className="mt-0.5" checked={isDefault} onCheckedChange={setIsDefault} />
        </label>
        {error && <p className="text-sm text-bad">{error}</p>}
      </div>

      <div className="flex h-12 shrink-0 items-center justify-between border-t border-line px-4">
        {caption ? (
          <button
            className="flex h-7 items-center gap-1.5 rounded px-2.5 text-bad hover:bg-bad/10"
            onClick={() => confirm(`Delete the caption ${caption.name}?`) && run(() => remove.mutateAsync({ path: { caption_id: caption.id } }))}
          >
            <Trash2 className="size-3.5" />
            Delete
          </button>
        ) : (
          <span />
        )}
        <div className="flex items-center gap-2">
          <span className="text-sm text-subtle">⌘S</span>
          <button className={cn(btn.primary, "px-4")} disabled={!values.name || !dirty || tooLong || busy} onClick={save}>
            {busy ? "Saving…" : caption ? "Save" : "Create"}
          </button>
        </div>
      </div>
    </Drawer>
  )
}

function Covers() {
  const qc = useQueryClient()
  const list = useQuery(listCoversOptions())
  const pick = useRef<HTMLInputElement>(null)
  const [error, setError] = useState("")
  const refresh = () => qc.invalidateQueries({ queryKey: listCoversQueryKey() })
  const onError = (e: unknown) => setError(errorText(e))
  const upload = useMutation(uploadCoverMutation())
  const patch = useMutation({ ...updateCoverMutation(), onSuccess: refresh, onError })
  const remove = useMutation({ ...deleteCoverMutation(), onSuccess: refresh, onError })

  // Each image becomes the 1080x1920 JPEG the Editor would make of it, named after its file
  async function add(files: File[]) {
    setError("")
    for (const f of files)
      try {
        const name = f.name.replace(/\.[^.]*$/, "").trim().slice(0, 100) || "Cover"
        await upload.mutateAsync({ body: { file: await coverJpeg(f), name } })
      } catch (e) {
        setError(`${f.name}: ${errorText(e)}`)
      }
    await refresh()
  }
  const rename = (id: number, name: string) => {
    const next = prompt("Rename cover", name)?.trim()
    if (next && next !== name) patch.mutate({ path: { cover_id: id }, body: { name: next.slice(0, 100) } })
  }

  return (
    <>
      <Tabs>
        <input ref={pick} type="file" accept="image/*" multiple hidden onChange={(e) => (add([...(e.target.files ?? [])]), (e.target.value = ""))} />
        <button className={cn(btn.primary, "pl-2")} disabled={upload.isPending} onClick={() => pick.current?.click()}>
          <Upload className="size-3.5" />
          {upload.isPending ? "Uploading…" : "Upload cover"}
        </button>
      </Tabs>
      {error && <p className="border-b border-line px-4 py-2 text-sm text-bad">{error}</p>}
      {list.isError && <p className="p-8 text-center text-bad">Couldn't load covers: {errorText(list.error)}</p>}
      {list.data?.length === 0 ? (
        <Empty>No saved covers yet. Upload an image: the default cover is preselected in the Editor, and each render gets its own copy.</Empty>
      ) : (
        <section className="min-h-0 flex-1 overflow-auto p-4">
          <ul className="grid grid-cols-[repeat(auto-fill,256px)] gap-2">
            {list.data?.map((c) => (
              <li key={c.id} data-cover={c.id} className="flex gap-3 rounded border border-line bg-panel p-2">
                <img src={c.image_url} alt="" className="h-32 w-[72px] shrink-0 rounded-sm bg-raised object-cover" />
                <div className="flex min-w-0 flex-1 flex-col items-start gap-1">
                  <span className="max-w-full truncate font-medium" title={c.name}>
                    {c.name}
                  </span>
                  <span className="text-sm tabular-nums text-muted">Added {shortDay(c.created_at)}</span>
                  {c.is_default && <Chip tone="neutral">Default</Chip>}
                  <div className="mt-auto -ml-2 flex items-center gap-0.5">
                    <button className={btn.ghost} onClick={() => patch.mutate({ path: { cover_id: c.id }, body: { is_default: !c.is_default } })}>
                      {c.is_default ? "Clear default" : "Make default"}
                    </button>
                    <button className={btn.icon} title="Rename" onClick={() => rename(c.id, c.name)}>
                      <Pencil className="size-3.5" />
                    </button>
                    <button className={btn.icon} title="Delete" onClick={() => confirm(`Delete the cover ${c.name}? Renders keep their own copy.`) && remove.mutate({ path: { cover_id: c.id } })}>
                      <Trash2 className="size-4" />
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

/** Songs the Editor mixes into renders (looped to the clip, faded out at its end). A song's name is also the Reel's
 * audio label on Instagram. */
function Music() {
  const qc = useQueryClient()
  const list = useQuery(listTracksOptions())
  const pick = useRef<HTMLInputElement>(null)
  const [error, setError] = useState("")
  const refresh = () => qc.invalidateQueries({ queryKey: listTracksQueryKey() })
  const onError = (e: unknown) => setError(errorText(e))
  const upload = useMutation(uploadTrackMutation())
  const patch = useMutation({ ...updateTrackMutation(), onSuccess: refresh, onError })
  const remove = useMutation({ ...deleteTrackMutation(), onSuccess: refresh, onError })

  async function add(files: File[]) {
    setError("")
    for (const f of files)
      try {
        await upload.mutateAsync({ body: { file: f, name: songName(f) } })
      } catch (e) {
        setError(`${f.name}: ${errorText(e)}`)
      }
    await refresh()
  }
  const rename = (id: number, name: string) => {
    const next = prompt("Rename song (Instagram shows this name as the Reel's audio)", name)?.trim()
    if (next && next !== name) patch.mutate({ path: { track_id: id }, body: { name: next.slice(0, 100) } })
  }

  return (
    <>
      <Tabs>
        <input ref={pick} type="file" accept={SONG_TYPES} multiple hidden onChange={(e) => (add([...(e.target.files ?? [])]), (e.target.value = ""))} />
        <button className={cn(btn.primary, "pl-2")} disabled={upload.isPending} onClick={() => pick.current?.click()}>
          <Upload className="size-3.5" />
          {upload.isPending ? "Uploading…" : "Upload song"}
        </button>
      </Tabs>
      {error && <p className="border-b border-line px-4 py-2 text-sm text-bad">{error}</p>}
      {list.isError && <p className="p-8 text-center text-bad">Couldn't load songs: {errorText(list.error)}</p>}
      {list.data?.length === 0 ? (
        <Empty>
          No songs yet. Upload an MP3, M4A, AAC, WAV, Ogg or FLAC (up to 20 MB) that you have the rights to: the Editor mixes it into a render, and Instagram may mute a copyrighted song.
        </Empty>
      ) : (
        <section className="min-h-0 flex-1 overflow-auto">
          <ul className="divide-y divide-line">
            {list.data?.map((t) => (
              <li key={t.id} data-track={t.id} className="flex h-14 items-center gap-3 px-4">
                <span className="min-w-0 flex-1 truncate font-medium" title={t.name}>
                  {t.name}
                </span>
                {t.is_default && <Chip tone="neutral">Default</Chip>}
                <audio controls preload="none" src={t.audio_url} className="h-8 w-[280px] shrink-0" />
                <span className="w-24 shrink-0 text-sm tabular-nums text-muted">Added {shortDay(t.created_at)}</span>
                <div className="flex shrink-0 items-center gap-0.5">
                  <button className={btn.ghost} onClick={() => patch.mutate({ path: { track_id: t.id }, body: { is_default: !t.is_default } })}>
                    {t.is_default ? "Clear default" : "Make default"}
                  </button>
                  <button className={btn.icon} title="Rename" onClick={() => rename(t.id, t.name)}>
                    <Pencil className="size-3.5" />
                  </button>
                  <button className={btn.icon} title="Delete" onClick={() => confirm(`Delete the song ${t.name}? Finished renders keep it; re-rendering one of them fails.`) && remove.mutate({ path: { track_id: t.id } })}>
                    <Trash2 className="size-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}
