import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, ChevronDown, Plus, RefreshCw, X } from "lucide-react"
import { useState } from "react"
import { Link } from "react-router"

import type { AccountOut, AccountPatch } from "@/api"
import { listAccountsOptions, listAccountsQueryKey, listPostsQueryKey, syncAccountsMutation, updateAccountMutation } from "@/api/@tanstack/react-query.gen"
import { Avatar, ConnChip, ZERNIO_URL } from "@/components/AccountBits"
import { Drawer, Empty, Header } from "@/components/bits"
import { SLOT_PRESETS, ZONES, apiError, isHHMM, shortWhen, utcOffset } from "@/lib/schedule"
import { ago, btn, cn, field, label } from "@/lib/utils"

export function Accounts() {
  const qc = useQueryClient()
  const accounts = useQuery(listAccountsOptions())
  const [drawer, setDrawer] = useState(false)
  const sync = useMutation({
    ...syncAccountsMutation(),
    onSuccess: (data) => {
      qc.setQueryData(listAccountsQueryKey(), data)
      qc.invalidateQueries({ queryKey: listPostsQueryKey() }) // a reconnected account's failed posts move to free slots
    },
  })
  const connected = accounts.data?.filter((a) => a.connection_status === "connected" && !a.disabled_at).length ?? 0

  return (
    <>
      <Header>
        <div className="flex items-baseline gap-2">
          <h1 className="text-lg font-semibold">Accounts</h1>
          {accounts.data && <span className="text-sm tabular-nums text-subtle">{connected} connected</span>}
        </div>
        <div className="flex items-center gap-2">
          {sync.isError && (
            <span className="text-sm text-bad">
              Sync failed: {apiError(sync.error).message}
              {apiError(sync.error).code?.startsWith("ZERNIO_KEY") && (
                <Link to="/settings" className="ml-1.5 text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg">
                  Settings
                </Link>
              )}
            </span>
          )}
          {sync.isSuccess && <span className="text-sm tabular-nums text-muted">Synced {sync.data.length} account{sync.data.length === 1 ? "" : "s"}</span>}
          <button className={btn.secondary} onClick={() => setDrawer(true)}>
            <Plus className="size-3.5" />
            Connect account
          </button>
          <button className={btn.primary} disabled={sync.isPending} onClick={() => sync.mutate({})}>
            <RefreshCw className={cn("size-3.5", sync.isPending && "animate-spin")} />
            Sync accounts
          </button>
        </div>
      </Header>
      <section className="min-h-0 flex-1 overflow-auto">
          {accounts.isError ? (
            <Empty>Couldn't load accounts: {apiError(accounts.error).message}</Empty>
          ) : accounts.data?.length === 0 ? (
            <Empty>
              <span>
                No accounts yet. Add your Zernio API key in{" "}
                <Link to="/settings" className="text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg">
                  Settings
                </Link>
                , connect an Instagram account in Zernio, then Sync accounts.
              </span>
            </Empty>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,400px)] items-start gap-3 p-4">
              {accounts.data?.map((a) => <AccountCard key={a.id} a={a} />)}
            </div>
          )}
      </section>
      {drawer && <ConnectDrawer onClose={() => setDrawer(false)} onSync={() => sync.mutate({})} syncing={sync.isPending} />}
    </>
  )
}

function AccountCard({ a }: { a: AccountOut }) {
  const qc = useQueryClient()
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const patch = useMutation({
    ...updateAccountMutation(),
    onSuccess: (acc) => {
      qc.setQueryData<AccountOut[]>(listAccountsQueryKey(), (xs) => xs?.map((x) => (x.id === acc.id ? acc : x)))
      setNote({ ok: true, text: "Saved" })
    },
    onError: (e) => setNote({ ok: false, text: apiError(e).message }),
  })
  const save = (body: AccountPatch) => (setNote(null), patch.mutate({ path: { account_id: a.id }, body }))
  const times = a.posting_slots.times ?? []
  const today = a.today_count ?? 0
  const zones = ZONES.includes(a.timezone) ? ZONES : [a.timezone, ...ZONES]
  const disabled = !!a.disabled_at

  return (
    <article data-account={a.id} className={cn("rounded-md border border-line bg-panel", disabled && "opacity-60")}>
      <div className="flex items-start gap-3 p-3">
        <Avatar a={a} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-md leading-5 font-medium">@{a.username}</div>
          <div className="truncate font-mono text-sm text-subtle">{a.zernio_account_id}</div>
        </div>
        <ConnChip a={a} />
      </div>
      <div className="space-y-1 px-3 pb-3 text-sm text-muted tabular-nums">
        <div>
          Meta:{" "}
          {a.quota ? (
            <>
              <span className="text-fg">{a.quota.used}</span>/{a.quota.total} used ({Math.round(a.quota.duration_s / 3600)}h)
            </>
          ) : (
            <span className="text-subtle">unavailable</span>
          )}{" "}
          · Today: <span className={cn("text-fg", today >= a.daily_cap && "text-warn")}>{today}</span>/{a.daily_cap} cap
        </div>
        <div>
          {a.last_publish_at ? `Last published ${ago(a.last_publish_at)}` : "Nothing published yet"} · {a.next_post_at ? `Next ${shortWhen(a.next_post_at, a.timezone)}` : "Nothing queued"}
        </div>
      </div>
      <fieldset disabled={disabled || patch.isPending} className="border-t border-line p-3">
        <div className={cn(label, "mb-2")}>Slots</div>
        <div className="grid grid-cols-[64px_1fr] items-center gap-2">
          <span className="text-sm text-muted">Timezone</span>
          {/* the list shows each zone's offset; the closed control shows the zone plus a muted offset */}
          <label className={cn(field, "relative flex min-w-0 items-center gap-2 bg-bg has-[:focus-visible]:border-muted")}>
            <span className="truncate">{a.timezone}</span>
            <span className="shrink-0 text-sm tabular-nums text-subtle">{utcOffset(a.timezone)}</span>
            <ChevronDown className="ml-auto size-3.5 shrink-0 text-subtle" />
            <select aria-label="Timezone" className="absolute inset-0 cursor-pointer opacity-0" value={a.timezone} onChange={(e) => save({ timezone: e.target.value })}>
              {zones.map((z) => (
                <option key={z} value={z}>
                  {z} ({utcOffset(z)})
                </option>
              ))}
            </select>
          </label>
          <span className="text-sm text-muted">Times</span>
          <Times times={times} onChange={(t) => save({ posting_slots: { times: t } })} />
          <span className="text-sm text-muted">Daily cap</span>
          <div className="flex items-center gap-2">
            <NumberField label="Daily cap" value={a.daily_cap} min={1} max={100} onCommit={(v) => save({ daily_cap: v })} className="w-14" />
            <span className="ml-3 text-sm text-muted">Min gap</span>
            <NumberField label="Min gap" value={a.min_gap_minutes} min={0} max={720} onCommit={(v) => save({ min_gap_minutes: v })} className="w-16" />
            <span className="text-sm text-subtle">min</span>
          </div>
        </div>
      </fieldset>
      <div className="flex h-10 items-center justify-between gap-2 border-t border-line px-3">
        <span className={cn("truncate text-sm tabular-nums", note ? (note.ok ? "text-ok" : "text-bad") : "text-subtle")}>
          {note?.text ?? (a.connection_status === "disconnected" ? "Reconnect it in Zernio, then Sync" : `Connected ${ago(a.connected_at)}`)}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          {a.connection_status === "disconnected" && (
            <a className={cn(btn.secondary, "px-2.5 font-normal")} href={ZERNIO_URL} target="_blank" rel="noreferrer">
              Reconnect
              <ArrowUpRight className="size-3.5" />
            </a>
          )}
          <button
            className={btn.ghost}
            disabled={patch.isPending}
            onClick={() =>
              disabled
                ? save({ disabled: false })
                : confirm(`Disable @${a.username}? Its drafts and scheduled posts are cancelled.`) && save({ disabled: true })
            }
          >
            {disabled ? "Enable" : "Disable"}
          </button>
        </div>
      </div>
    </article>
  )
}

function Times({ times, onChange }: { times: string[]; onChange: (t: string[]) => void }) {
  const [adding, setAdding] = useState<string | null>(null)
  const add = (t: string) => {
    setAdding(null)
    if (isHHMM(t) && !times.includes(t)) onChange([...times, t].sort())
  }
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {times.map((t) => (
        <span key={t} className="inline-flex h-6 items-center gap-1 rounded border border-line bg-raised pr-1 pl-2 tabular-nums">
          {t}
          <button aria-label={`Remove ${t}`} className="text-subtle hover:text-fg" onClick={() => onChange(times.filter((x) => x !== t))}>
            <X className="size-3" />
          </button>
        </span>
      ))}
      {adding != null ? (
        <input
          autoFocus
          placeholder="HH:MM"
          maxLength={5}
          onFocus={(e) => e.currentTarget.select()}
          aria-label="New slot time"
          className={cn(field, "h-6 w-24 bg-bg tabular-nums")}
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          onBlur={() => add(adding)}
          onKeyDown={(e) => (e.key === "Enter" ? e.currentTarget.blur() : e.key === "Escape" && setAdding(null))}
        />
      ) : (
        <button className="inline-flex h-6 items-center gap-1 rounded border border-dashed border-line-strong pr-2 pl-1.5 text-muted hover:text-fg" onClick={() => setAdding("12:00")}>
          <Plus className="size-3" />
          Add
        </button>
      )}
      {/* a whole set at once: replaces the times above (existing posts keep theirs) */}
      <label className="relative inline-flex h-6 items-center gap-1 rounded border border-dashed border-line-strong pr-1.5 pl-2 text-muted hover:text-fg has-[:focus-visible]:border-muted">
        Presets
        <ChevronDown className="size-3" />
        <select aria-label="Slot presets" value="" onChange={(e) => onChange(SLOT_PRESETS[Number(e.target.value)].times)} className="absolute inset-0 cursor-pointer opacity-0">
          <option value="" disabled>
            Replace the times with…
          </option>
          {SLOT_PRESETS.map((p, i) => (
            <option key={p.label} value={i}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  )
}

function NumberField(props: { label: string; value: number; min: number; max: number; className?: string; onCommit: (v: number) => void }) {
  const [draft, setDraft] = useState<string | null>(null)
  const commit = () => {
    const v = Number(draft)
    setDraft(null)
    if (draft != null && draft !== "" && Number.isInteger(v) && v >= props.min && v <= props.max && v !== props.value) props.onCommit(v)
  }
  return (
    <input
      type="number"
      aria-label={props.label}
      min={props.min}
      max={props.max}
      className={cn(field, "bg-bg tabular-nums", props.className)}
      value={draft ?? props.value}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === "Enter" && e.currentTarget.blur()}
    />
  )
}

function ConnectDrawer({ onClose, onSync, syncing }: { onClose: () => void; onSync: () => void; syncing: boolean }) {
  const steps = [
    ["Add your Zernio API key", "In Settings. Clipper reads your accounts from your own Zernio account through it, and publishes with it."],
    ["Create a Zernio profile", "One profile per Instagram account, so each account keeps its own queue and limits."],
    ["Connect Instagram in that profile", "The account must be an Instagram Business or Creator account. Zernio's approved Meta app handles the login."],
    ["Sync accounts here", "Clipper pulls the connected accounts from Zernio. New accounts start on Europe/London with a slot every hour from 07:00 to 23:00."],
  ]
  return (
    <Drawer label="Connect account" onClose={onClose}>
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
        <h2 className="text-md font-semibold">Connect account</h2>
        <button className={btn.icon} aria-label="Close" onClick={onClose}>
          <X className="size-4" />
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-auto p-4">
        <p className="text-muted">Clipper publishes through Zernio. Accounts are connected in Zernio, not here.</p>
        <ol className="space-y-4">
          {steps.map(([title, body], i) => (
            <li key={title} className="flex gap-3">
              <span className="grid size-5 shrink-0 place-items-center rounded-full border border-line-strong text-xs tabular-nums text-muted">{i + 1}</span>
              <div className="space-y-1">
                <div className="font-medium">{title}</div>
                <p className="text-muted">{body}</p>
                {i === 0 && (
                  <Link to="/settings" className="inline-flex items-center gap-1 underline decoration-line-strong underline-offset-2 hover:decoration-fg">
                    Open Settings
                  </Link>
                )}
                {i === 1 && (
                  <a href={ZERNIO_URL} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 underline decoration-line-strong underline-offset-2 hover:decoration-fg">
                    Open Zernio
                    <ArrowUpRight className="size-3.5" />
                  </a>
                )}
                {i === 3 && (
                  <button className={cn(btn.primary, "mt-2")} disabled={syncing} onClick={onSync}>
                    <RefreshCw className={cn("size-3.5", syncing && "animate-spin")} />
                    Sync accounts
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>
      </div>
      <div className="flex h-12 shrink-0 items-center justify-end border-t border-line px-4">
        <button className={btn.ghost} onClick={onClose}>
          Cancel
        </button>
      </div>
    </Drawer>
  )
}
