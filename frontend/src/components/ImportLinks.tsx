import { useMutation, useQueryClient } from "@tanstack/react-query"
import { FileText, X } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import type { LinksOut } from "@/api"
import { createClipsFromUrlsMutation, findLinksMutation, listClipsQueryKey } from "@/api/@tanstack/react-query.gen"
import { DOCUMENTS, RIGHTS, btn, cn, errorText, field, label, shortUrl, type Rights } from "@/lib/utils"

const BATCH = 1000 // the api's limit per request

/** Library "Import links": paste text or give a document, see the videos it links, import the new ones. */
export function ImportLinks({ file, onClose }: { file?: File; onClose: () => void }) {
  const qc = useQueryClient()
  const ref = useRef<HTMLDialogElement>(null)
  const pick = useRef<HTMLInputElement>(null)
  const [text, setText] = useState("")
  const [source, setSource] = useState("")
  const [found, setFound] = useState<LinksOut | null>(null)
  const [rights, setRights] = useState<Rights | "">("")
  const [error, setError] = useState("")
  const [over, setOver] = useState(false)
  const find = useMutation(findLinksMutation())
  const create = useMutation(createClipsFromUrlsMutation())

  async function read(f: File, name: string) {
    setError("")
    try {
      setFound(await find.mutateAsync({ body: { file: f } }))
      setSource(name)
    } catch (e) {
      setError(errorText(e))
    }
  }
  const started = useRef(false)
  useEffect(() => {
    ref.current?.showModal()
    if (file && !started.current) read(file, file.name) // a document dropped on the library
    started.current = true
    // eslint-disable-next-line react-hooks/exhaustive-deps -- once, on open
  }, [])

  const fresh = found?.links.filter((l) => !l.in_library) ?? []
  const count = new Map<string, number>()
  found?.links.forEach((l) => count.set(l.platform, (count.get(l.platform) ?? 0) + 1))
  const platforms = [...count].map(([p, n]) => `${p} ${n}`)

  async function submit() {
    if (!rights) return
    setError("")
    try {
      for (let i = 0; i < fresh.length; i += BATCH) await create.mutateAsync({ body: { urls: fresh.slice(i, i + BATCH).map((l) => l.url), rights_status: rights } })
      onClose()
    } catch (e) {
      setError(errorText(e))
    } finally {
      qc.invalidateQueries({ queryKey: listClipsQueryKey() })
    }
  }

  return (
    <dialog ref={ref} onClose={onClose} className="m-auto w-[640px] max-w-[90vw] rounded-md border border-line bg-panel p-0 text-fg shadow-2xl backdrop:bg-black/60">
      <div className="flex h-10 items-center justify-between border-b border-line px-4">
        <span className="font-medium">Import links</span>
        <form method="dialog">
          <button className={btn.icon} aria-label="Close">
            <X className="size-4" />
          </button>
        </form>
      </div>

      {!found ? (
        <div
          className="space-y-3 p-4"
          onDragOver={(e) => (e.preventDefault(), setOver(true))}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault()
            setOver(false)
            const f = e.dataTransfer.files[0]
            if (f) read(f, f.name)
          }}
        >
          <textarea
            aria-label="Links"
            autoFocus
            rows={9}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={"Paste links, or any text that has them in it.\n\nhttps://www.youtube.com/shorts/…\nhttps://www.tiktok.com/@creator/video/…\nhttps://www.instagram.com/reel/…"}
            className={cn(field, "h-auto w-full resize-none py-1.5 leading-[18px]", over && "border-accent bg-accent/5")}
          />
          <div className="flex items-center gap-2">
            <input ref={pick} type="file" hidden accept={DOCUMENTS.map((x) => `.${x}`).join(",")} onChange={(e) => (e.target.files?.[0] && read(e.target.files[0], e.target.files[0].name), (e.target.value = ""))} />
            <button className={btn.secondary} disabled={find.isPending} onClick={() => pick.current?.click()}>
              <FileText className="size-3.5" />
              Choose a document
            </button>
            <span className="text-sm text-subtle">or drop one here: docx, txt, csv, xlsx…</span>
            <button className={cn(btn.primary, "ml-auto")} disabled={!text.trim() || find.isPending} onClick={() => read(new File([text], "pasted.txt"), "Pasted text")}>
              {find.isPending ? "Reading…" : "Find links"}
            </button>
          </div>
          {error && <p className="text-sm text-bad">{error}</p>}
        </div>
      ) : (
        <div className="space-y-3 p-4">
          <div className="flex items-baseline gap-2">
            <span className="text-lg font-semibold tabular-nums">
              {found.links.length} {found.links.length === 1 ? "video" : "videos"}
            </span>
            <span className="min-w-0 truncate text-sm text-muted" title={source}>
              in {source}
            </span>
            <button className="ml-auto shrink-0 text-sm text-muted underline decoration-line-strong underline-offset-2 hover:text-fg" onClick={() => (setFound(null), setError(""))}>
              Change
            </button>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-0.5 text-sm tabular-nums text-muted">
            {platforms.length > 0 && <span>{platforms.join(" · ")}</span>}
            {found.repeats > 0 && <span>{found.repeats} repeated {found.repeats === 1 ? "link" : "links"} dropped</span>}
            {fresh.length < found.links.length && <span>{found.links.length - fresh.length} already in the library</span>}
            {found.other_count > 0 && (
              <span title={found.other.join("\n") + (found.other_count > found.other.length ? "\n…" : "")} className="underline decoration-line-strong decoration-dotted underline-offset-2">
                {found.other_count} other {found.other_count === 1 ? "link" : "links"} skipped
              </span>
            )}
          </div>
          {found.links.length > 0 && (
            <ol className="max-h-[280px] overflow-auto rounded border border-line">
              {found.links.map((l, i) => (
                <li key={l.url} className={cn("flex h-7 items-center gap-3 px-2 text-sm", i > 0 && "border-t border-line", l.in_library && "text-subtle")}>
                  <span className="w-7 shrink-0 text-right tabular-nums text-subtle">{i + 1}</span>
                  <span className={cn("w-[72px] shrink-0", !l.in_library && "text-muted")}>{l.platform}</span>
                  <span className="min-w-0 flex-1 truncate" title={l.url}>
                    {shortUrl(l.url)}
                  </span>
                  {l.in_library && <span className="shrink-0">In library</span>}
                </li>
              ))}
            </ol>
          )}
          {error && <p className="text-sm text-bad">{error}</p>}
          <div className="flex items-end gap-3">
            <label className="space-y-1">
              <span className={cn(label, "block")}>Rights for these clips</span>
              <select value={rights} onChange={(e) => setRights(e.target.value as Rights)} className={cn(field, "w-[180px]", !rights && "text-subtle")}>
                <option value="" disabled>
                  Choose…
                </option>
                {Object.entries(RIGHTS).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <span className="min-w-0 flex-1 pb-1 text-sm text-subtle">Downloads run two at a time, after renders and publishing.</span>
            <button className={cn(btn.primary, "shrink-0")} disabled={!fresh.length || !rights || create.isPending} onClick={submit}>
              {create.isPending ? "Importing…" : fresh.length ? `Import ${fresh.length} ${fresh.length === 1 ? "video" : "videos"}` : "Nothing new to import"}
            </button>
          </div>
        </div>
      )}
    </dialog>
  )
}
