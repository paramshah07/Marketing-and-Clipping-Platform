import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Archive, ArchiveRestore, ArrowUpRight, Image, Info, Link2, Plus, Upload, X } from "lucide-react"
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react"
import { Link } from "react-router"

import type { BrandOut, ClipOut } from "@/api"
import {
  createBrandMutation,
  listBrandsOptions,
  listBrandsQueryKey,
  listClipsOptions,
  updateBrandMutation,
  uploadBrandLogoMutation,
} from "@/api/@tanstack/react-query.gen"
import { Header } from "@/components/bits"
import { Switch } from "@/components/ui/switch"
import { CAPTION_MAX, HASHTAG_MAX, cn, errorText, hashtagCount, btn, label } from "@/lib/utils"

export function Brands() {
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
      <Header>
        <div className="flex items-baseline gap-2">
          <h1 className="text-lg font-semibold">Brands</h1>
          <span className="text-sm tabular-nums text-subtle">
            {active.data?.length ?? 0} active · {archived.data?.length ?? 0} archived
          </span>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-muted">
            <Switch checked={showArchived} onCheckedChange={setShowArchived} />
            Show archived
          </label>
          <div className="h-4 w-px bg-line" />
          <button className={cn(btn.primary, "pl-2")} onClick={() => show("new")}>
            <Plus className="size-3.5" />
            New brand
          </button>
        </div>
      </Header>
      <section className="flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-auto">
          <table className="w-full table-fixed border-collapse">
            <colgroup>
              <col className="w-[68px]" />
              <col className="w-[150px]" />
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
                      <button className={cn("block max-w-full truncate rounded-sm text-left font-medium", gone && "text-muted")}>{b.name}</button>
                      {gone ? (
                        <div className="flex items-center gap-1 text-sm tabular-nums">
                          <Archive className="size-3" />
                          Archived {new Date(b.archived_at!).toLocaleDateString("en-GB", { day: "numeric", month: "short" }).replace("Sept", "Sep")}
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
  const values = { name: name.trim(), link: link.trim() || null, caption_template: template.trim() ? template : null, auto_approve: autoApprove }
  const dirty = !brand || !!file || values.name !== brand.name || values.link !== brand.link || values.caption_template !== brand.caption_template || autoApprove !== brand.auto_approve

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
    <aside className="flex w-[440px] shrink-0 flex-col border-l border-line bg-panel shadow-[-12px_0_32px_rgba(0,0,0,0.45)]">
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
          <button className={btn.icon} aria-label="Close" onClick={onClose}>
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
    </aside>
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
