import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, Check, CircleAlert, Copy, LoaderCircle, RefreshCw, TriangleAlert } from "lucide-react"
import { useEffect, useState, type ReactNode } from "react"
import { Link } from "react-router"

import type { BotOut, BotPairing, KeyCheck, Me } from "@/api"
import {
  addBotMutation,
  changePasswordMutation,
  checkZernioKeyMutation,
  deleteBotMutation,
  deleteZernioKeyMutation,
  listAccountsOptions,
  listAccountsQueryKey,
  listBotsOptions,
  listBotsQueryKey,
  logoutMutation,
  meOptions,
  meQueryKey,
  pairBotMutation,
  patchBotMutation,
  putZernioKeyMutation,
  statusQueryKey,
  testBotMutation,
} from "@/api/@tanstack/react-query.gen"
import { Avatar, ConnChip, ZERNIO_URL } from "@/components/AccountBits"
import { Chip, Header } from "@/components/bits"
import { Switch } from "@/components/ui/switch"
import { apiError, say } from "@/lib/schedule"
import { ago, btn, cn, field, gb, label, pairState } from "@/lib/utils"

const KEYS_URL = "https://zernio.com/dashboard/api-keys" // docs.zernio.com Quickstart, step 1
const BOTFATHER_URL = "https://t.me/BotFather"
const input = cn(field, "min-w-0 flex-1 border-line-strong bg-bg")
const link = "underline decoration-line-strong underline-offset-2 hover:decoration-fg"

/** After signup: what makes Clipper publish, each step checked live. All skippable: the Library, Editor and
 * Customizations work without them. */
export function Setup() {
  const me = useQuery(meOptions())
  const done = me.data ? Object.values(me.data.setup).filter(Boolean).length : 0
  const ready = me.data?.setup.zernio && me.data.setup.instagram // Telegram is optional
  return (
    <>
      <Header>
        <div className="flex items-baseline gap-2">
          <h1 className="text-lg font-semibold">Set up Clipper</h1>
          {me.data && <span className="text-sm tabular-nums text-subtle">{done}/3 done</span>}
        </div>
        <Link to="/library" className={ready ? btn.primary : btn.ghost}>
          {ready ? "Go to Library" : "Do this later"}
        </Link>
      </Header>
      <Page>
        <p className="text-muted">
          Clipper publishes your Reels through <span className="text-fg">Zernio</span>, with your own Zernio account. Uploads, renders and brands work without these steps; you can do
          them any time in Settings.
        </p>
        <Cards steps />
      </Page>
    </>
  )
}

export function Settings() {
  return (
    <>
      <Header>
        <h1 className="text-lg font-semibold">Settings</h1>
      </Header>
      <Page>
        <Cards />
        <AccountCard />
      </Page>
    </>
  )
}

function Page({ children }: { children: ReactNode }) {
  return (
    <section className="min-h-0 flex-1 overflow-auto">
      <div className="max-w-[720px] space-y-3 p-4">{children}</div>
    </section>
  )
}

/** After any key call, failed ones too (a PUT stores the key, then can fail listing its accounts): what it changed. */
function useKeyRefetch() {
  const qc = useQueryClient()
  return () => {
    for (const queryKey of [meQueryKey(), listAccountsQueryKey(), statusQueryKey()]) qc.invalidateQueries({ queryKey })
  }
}

/** The three cards, sharing the last key check: what Verify / Re-check found, skipped and held back. */
function Cards({ steps }: { steps?: boolean }) {
  const me = useQuery(meOptions())
  const [check, setCheck] = useState<KeyCheck | null>(null)
  if (me.isError) return <p className="text-bad">Couldn't load your settings: {say(me.error)}</p>
  if (!me.data) return <p className="text-muted">Loading…</p>
  return (
    <>
      <ZernioCard n={steps ? 1 : undefined} me={me.data} onChecked={setCheck} />
      <InstagramCard n={steps ? 2 : undefined} me={me.data} check={check} onChecked={setCheck} />
      <TelegramCard n={steps ? 3 : undefined} paired={me.data.setup.telegram} />
    </>
  )
}

// ---------------------------------------------------------------- Zernio

function ZernioCard({ n, me, onChecked }: { n?: number; me: Me; onChecked: (kc: KeyCheck | null) => void }) {
  const z = me.zernio
  const [key, setKey] = useState("")
  const [replacing, setReplacing] = useState(false)
  const [problem, setProblem] = useState<unknown>(null)
  const refetch = useKeyRefetch()
  const guard = { onMutate: () => setProblem(null), onError: setProblem, onSettled: refetch }
  const put = useMutation({ ...putZernioKeyMutation(), ...guard, onSuccess: (kc) => (setKey(""), setReplacing(false), onChecked(kc)) })
  const recheck = useMutation({ ...checkZernioKeyMutation(), ...guard, onSuccess: onChecked })
  const remove = useMutation({ ...deleteZernioKeyMutation(), ...guard, onSuccess: () => onChecked(null) })
  const busy = put.isPending || recheck.isPending || remove.isPending
  const who = z.name && z.email ? `${z.name} (${z.email})` : (z.name ?? z.email ?? "your Zernio account")
  const state =
    put.isPending || recheck.isPending ? (
      <Chip tone="accent" spin>
        Checking with Zernio
      </Chip>
    ) : z.status === "valid" ? (
      <Chip tone="ok" dot>
        Connected
      </Chip>
    ) : z.status === "invalid" ? (
      <Chip tone="bad" dot>
        Refused
      </Chip>
    ) : (
      <Chip tone="neutral">Not set</Chip>
    )

  return (
    <Card n={n} done={me.setup.zernio} title="Zernio API key" aside={state}>
      {z.status !== "none" && (
        <Line tone={z.status === "valid" ? "ok" : "bad"}>
          {z.status === "valid" ? (
            <>
              Connected as <span className="font-medium">{who}</span>
            </>
          ) : (
            <span className="text-bad">{z.error ?? "Zernio refused the key"}</span>
          )}
          <span className="tabular-nums text-muted"> · key ••••{z.last4}</span>
          <span className="text-subtle"> · {z.checked_at ? `checked ${ago(z.checked_at)}` : "not checked yet"}</span>
        </Line>
      )}
      {z.status === "invalid" && <p className="text-sm text-muted">Your scheduled posts wait until the key works again. Re-check after fixing it in Zernio, or paste a new key.</p>}
      {z.status === "none" || replacing ? (
        <>
          <ol className="list-decimal space-y-1 pl-4 text-muted marker:text-subtle">
            <li>
              Sign up or log in at{" "}
              <a className={cn(link, "text-fg")} href={ZERNIO_URL} target="_blank" rel="noreferrer">
                zernio.com
              </a>
              .
            </li>
            <li>
              Open{" "}
              <a className={cn(link, "text-fg")} href={KEYS_URL} target="_blank" rel="noreferrer">
                API keys
              </a>{" "}
              and click <span className="text-fg">Create API key</span>. Keep the defaults: scope <span className="text-fg">Full</span> (all profiles), permission{" "}
              <span className="text-fg">Read-write</span>, no expiry.
            </li>
            <li>Copy the key (sk_…, Zernio shows it once) and paste it here. Clipper checks it with Zernio and stores it encrypted; it is never shown again.</li>
          </ol>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              put.mutate({ body: { key } })
            }}
          >
            <input className={input} type="password" aria-label="Zernio API key" placeholder="sk_…" autoComplete="off" spellCheck={false} value={key} onChange={(e) => setKey(e.target.value)} />
            <button className={btn.primary} disabled={!key.trim() || busy}>
              {put.isPending && <LoaderCircle className="size-3.5 animate-spin" />}
              {put.isPending ? "Verifying…" : "Verify"}
            </button>
            {replacing && (
              <button type="button" className={btn.ghost} onClick={() => (setReplacing(false), setKey(""), setProblem(null))}>
                Cancel
              </button>
            )}
          </form>
        </>
      ) : (
        <div className="flex items-center gap-1">
          <button className={cn(btn.secondary, "mr-1")} disabled={busy} onClick={() => recheck.mutate({})}>
            <RefreshCw className={cn("size-3.5", recheck.isPending && "animate-spin")} />
            Re-check
          </button>
          <button className={btn.ghost} disabled={busy} onClick={() => setReplacing(true)}>
            Replace key
          </button>
          <button className={btn.ghost} disabled={busy} onClick={() => confirm("Remove your Zernio key? Your scheduled posts stop going out until you add one again.") && remove.mutate({})}>
            Remove
          </button>
        </div>
      )}
      {problem != null && <Problem e={problem} />}
    </Card>
  )
}

// ---------------------------------------------------------------- Instagram

const handles = (xs: string[]) => xs.map((x) => `@${x}`).join(", ")

function InstagramCard({ n, me, check, onChecked }: { n?: number; me: Me; check: KeyCheck | null; onChecked: (kc: KeyCheck) => void }) {
  const accounts = useQuery(listAccountsOptions())
  const refetch = useKeyRefetch()
  const recheck = useMutation({ ...checkZernioKeyMutation(), onSuccess: onChecked, onSettled: refetch })
  const keyed = me.zernio.status === "valid"
  const list = accounts.data ?? []
  const usable = list.filter((a) => a.connection_status === "connected" && !a.disabled_at).length
  // the list waits on Zernio (each account's publishing limit): until it answers, "None found" would be a guess
  const state = recheck.isPending || accounts.isPending ? (
    <Chip tone="accent" spin>
      Checking
    </Chip>
  ) : usable ? (
    <Chip tone="ok" dot>
      {usable} connected
    </Chip>
  ) : keyed ? (
    <Chip tone="warn" dot>
      None found
    </Chip>
  ) : (
    <Chip tone="neutral">Needs the key</Chip>
  )

  return (
    <Card n={n} done={me.setup.instagram} title="Instagram accounts" aside={state}>
      <p className="text-muted">
        Connect Instagram in Zernio, not here: a Business or Creator account, one Zernio profile per account (Zernio's first 2 accounts are free). Clipper finds them through your key.
      </p>
      {list.length > 0 && (
        <ul className="divide-y divide-line rounded border border-line">
          {list.map((a) => (
            <li key={a.id} className="flex h-10 items-center gap-2.5 px-2.5">
              <Avatar a={a} className="size-6 text-xs" />
              <span className="truncate font-medium">@{a.username}</span>
              <span className="ml-auto">
                <ConnChip a={a} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {!keyed && me.zernio.status === "none" && <p className="text-subtle">Add your Zernio key first: Clipper finds your Instagram accounts through it.</p>}
      {keyed && accounts.data?.length === 0 && !check?.skipped.length && !check?.over_limit.length && <Line tone="warn">No Instagram accounts in your Zernio account yet. Connect one in Zernio, then Re-check.</Line>}
      {!!check?.skipped.length && (
        <Line tone="warn">
          {handles(check.skipped)} {check.skipped.length === 1 ? "is" : "are"} connected to another Clipper user, so Clipper didn't add {check.skipped.length === 1 ? "it" : "them"} for you.
        </Line>
      )}
      {!!check?.over_limit.length && (
        <Line tone="warn">
          {handles(check.over_limit)}: beyond your Zernio plan's account limit, so Zernio won't post to {check.over_limit.length === 1 ? "it" : "them"}. Upgrade the plan (or remove an account) in Zernio, then
          Re-check.
        </Line>
      )}
      <div className="flex items-center gap-2">
        <a className={btn.secondary} href={ZERNIO_URL} target="_blank" rel="noreferrer">
          Connect in Zernio
          <ArrowUpRight className="size-3.5" />
        </a>
        <button className={btn.secondary} disabled={me.zernio.status === "none" || recheck.isPending} onClick={() => recheck.mutate({})}>
          <RefreshCw className={cn("size-3.5", recheck.isPending && "animate-spin")} />
          Re-check
        </button>
      </div>
      {recheck.isError && <Problem e={recheck.error} />}
    </Card>
  )
}

// ---------------------------------------------------------------- Telegram

// BotOut.health, from last_seen_at on the server (docs/telegram-bot.md §1)
const HEALTH: Record<BotOut["health"], { tone: "ok" | "accent" | "bad" | "warn"; label: string }> = {
  running: { tone: "ok", label: "Running" },
  waiting: { tone: "accent", label: "Waiting for Start" },
  rejected: { tone: "bad", label: "Token rejected" },
  not_responding: { tone: "warn", label: "Not responding" },
}
const DOT = { ok: "bg-ok", accent: "bg-accent", bad: "bg-bad", warn: "bg-warn" }
const TEXT = { ok: "text-ok", accent: "text-accent", bad: "text-bad", warn: "text-warn" }

function TelegramCard({ n, paired }: { n?: number; paired: boolean }) {
  const qc = useQueryClient()
  const [pairs, setPairs] = useState<Record<number, BotPairing>>({}) // guides open here: the codes this page handed out
  // quick while a code is out (the pairing shows up within seconds), else the health every 10 s
  const bots = useQuery({ ...listBotsOptions(), refetchInterval: (q) => (Object.keys(pairs).length || q.state.data?.some((b) => b.pairing) ? 2000 : 10_000) })
  const refresh = () => {
    qc.invalidateQueries({ queryKey: listBotsQueryKey() })
    qc.invalidateQueries({ queryKey: meQueryKey() })
  }
  // the row as the code went out (pairing: true), before the next poll: else the guide reads the old row as done
  const guide = (p: BotPairing) => {
    qc.setQueryData<BotOut[]>(listBotsQueryKey(), (xs) => (xs?.some((y) => y.id === p.bot.id) ? xs.map((y) => (y.id === p.bot.id ? p.bot : y)) : [...(xs ?? []), p.bot]))
    setPairs((x) => ({ ...x, [p.bot.id]: p }))
  }
  const close = (id: number) => setPairs((x) => Object.fromEntries(Object.entries(x).filter(([k]) => Number(k) !== id)))
  const [token, setToken] = useState("")
  const add = useMutation({
    ...addBotMutation(),
    onSuccess: (p) => {
      setToken("")
      if (p.start) guide(p)
      refresh()
    },
  })
  const list = bots.data ?? []
  // a pairing happens in Telegram: the polled list sees it first, and /api/me (the checklist, the badge) follows
  const done = bots.data ? list.some((b) => b.health === "running" || b.health === "not_responding") : paired
  useEffect(() => {
    if (done !== paired) qc.invalidateQueries({ queryKey: meQueryKey() })
  }, [done, paired, qc])
  const worst = (["rejected", "not_responding", "waiting"] as const).find((h) => list.some((b) => b.health === h))
  const state = !list.length ? (
    <Chip tone="neutral">Optional</Chip>
  ) : (
    <Chip tone={worst ? HEALTH[worst].tone : "ok"} dot>
      <span className="tabular-nums">
        {list.filter((b) => b.health === "running").length}/{list.length} running
      </span>
    </Chip>
  )
  const same = add.data && !add.data.start // your own bot again with a new token: its chat stays

  return (
    <Card n={n} done={done} title="Telegram bots" aside={state}>
      <p className="text-muted">Optional. A Telegram bot of your own sends you failure alerts and runs Clipper from the chat. Add as many as you like; each answers only the chat you pair it with.</p>
      {list.map((b) => (
        <BotRow key={b.id} b={b} pair={pairs[b.id]} onPair={guide} onClose={() => close(b.id)} onChange={refresh} />
      ))}
      {bots.isError && <Problem e={bots.error} />}
      <div className="space-y-2 rounded border border-dashed border-line-strong p-3">
        <div className="font-medium">{list.length ? "Add another bot" : "Add a bot"}</div>
        <ol className="list-decimal space-y-1 pl-4 text-muted marker:text-subtle">
          <li>
            In Telegram, open{" "}
            <a className={cn(link, "text-fg")} href={BOTFATHER_URL} target="_blank" rel="noreferrer">
              @BotFather
            </a>{" "}
            and send <span className="text-fg">/newbot</span>. Give it a name, then a username ending in “bot”. Make a new bot just for Clipper: one that another app uses is refused.
          </li>
          <li>Paste the token BotFather sends back (it looks like 123456789:AAE…).</li>
        </ol>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            add.mutate({ body: { token } })
          }}
        >
          <input className={input} type="password" aria-label="Bot token" placeholder="123456789:AA…" autoComplete="off" spellCheck={false} value={token} onChange={(e) => setToken(e.target.value)} />
          <button className={btn.primary} disabled={!token.trim() || add.isPending}>
            {add.isPending && <LoaderCircle className="size-3.5 animate-spin" />}
            {add.isPending ? "Verifying…" : "Verify"}
          </button>
        </form>
        {add.isError && <Problem e={add.error} />}
        {same && (
          <Line tone="ok">
            Token updated: {botName(add.data.bot)} keeps its chat{add.data.bot.chat_title && <> ({add.data.bot.chat_title})</>}.
          </Line>
        )}
      </div>
    </Card>
  )
}

const botName = (b: BotOut) => (b.username ? `@${b.username}` : `Bot ${b.id}`)

function BotRow({ b, pair, onPair, onClose, onChange }: { b: BotOut; pair?: BotPairing; onPair: (p: BotPairing) => void; onClose: () => void; onChange: () => void }) {
  const qc = useQueryClient()
  const [problem, setProblem] = useState<unknown>(null)
  const guard = { onMutate: () => setProblem(null), onError: setProblem }
  const patch = useMutation({
    ...patchBotMutation(),
    ...guard,
    onSuccess: (x) => {
      qc.setQueryData<BotOut[]>(listBotsQueryKey(), (xs) => xs?.map((y) => (y.id === x.id ? x : y)))
      onChange()
    },
  })
  const repair = useMutation({ ...pairBotMutation(), ...guard, onSuccess: onPair })
  const test = useMutation({ ...testBotMutation(), ...guard })
  const remove = useMutation({ ...deleteBotMutation(), ...guard, onSuccess: () => (onClose(), onChange()) })
  const h = HEALTH[b.health]
  const name = botName(b)
  const path = { path: { id: b.id } }
  const sendTest = () => test.mutate(path)

  return (
    <div data-bot={b.id} className="rounded border border-line">
      <div className="flex h-10 items-center gap-2 px-2.5">
        <span className={cn("size-1.5 shrink-0 rounded-full", DOT[h.tone])} />
        <span className="shrink-0 font-medium">{name}</span>
        <span className={cn("shrink-0 text-sm", b.health !== "running" && TEXT[h.tone])}>
          {h.label}
          {b.health === "not_responding" && <span className="tabular-nums text-subtle"> · {b.last_seen_at ? `last seen ${ago(b.last_seen_at)}` : "never seen"}</span>}
        </span>
        {b.chat_title && <span className="min-w-0 truncate text-sm text-subtle">· Paired with {b.chat_title}</span>}
        <div className="ml-auto flex shrink-0 items-center gap-0.5">
          <label className="mr-1.5 flex items-center gap-1.5 text-sm text-muted" title="Your failure alerts go to this bot's chat">
            <Switch checked={b.alerts} disabled={patch.isPending} onCheckedChange={(alerts) => patch.mutate({ ...path, body: { alerts } })} />
            Alerts
          </label>
          <button className={btn.ghost} disabled={b.health === "waiting" || test.isPending} onClick={sendTest}>
            Test
          </button>
          <button className={btn.ghost} disabled={b.health === "rejected" || repair.isPending} onClick={() => repair.mutate(path)}>
            {b.health === "waiting" ? "Pair" : "Re-pair"}
          </button>
          <button className={btn.ghost} disabled={remove.isPending} onClick={() => confirm(`Remove ${name}? It stops answering within 10 seconds.`) && remove.mutate(path)}>
            Remove
          </button>
        </div>
      </div>
      {(pair || b.health === "rejected" || test.data || problem != null) && (
        <div className="space-y-2 border-t border-line px-2.5 py-2.5">
          {b.health === "rejected" ? (
            <Line tone="bad">Telegram refused this bot's token (revoked in @BotFather?). Paste its new token below (BotFather: /token): it keeps its chat.</Line>
          ) : (
            pair && <PairGuide b={b} pair={pair} name={name} testing={test.isPending} onTest={sendTest} onRepair={() => repair.mutate(path)} onClose={onClose} />
          )}
          {test.data &&
            (test.data.ok ? (
              <Line tone="ok">Test message delivered: check the chat in Telegram.</Line>
            ) : (
              <Line tone="bad">
                <span className="text-bad">Telegram didn't take the test message: {test.data.error}</span>
              </Line>
            ))}
          {problem != null && <Problem e={problem} />}
        </div>
      )}
    </div>
  )
}

/** A code this page handed out: open the bot and tap Start (or send the code), wait until a chat used it, then a test
 * message. */
function PairGuide(props: { b: BotOut; pair: BotPairing; name: string; testing: boolean; onTest: () => void; onRepair: () => void; onClose: () => void }) {
  const { b, pair, name } = props
  const state = pairState(b, pair)
  if (state === "expired")
    return (
      <Line tone="warn">
        The code expired before a chat used it.{" "}
        <button className={cn(link, "text-fg")} onClick={props.onRepair}>
          Get a new code
        </button>
      </Line>
    )
  if (state === "paired")
    return (
      <>
        <Line tone="ok">Paired with {b.chat_title ?? "your chat"}. Send a test message to see it arrive.</Line>
        <div className="flex items-center gap-1">
          <button className={cn(btn.primary, "mr-1")} disabled={props.testing} onClick={props.onTest}>
            {props.testing && <LoaderCircle className="size-3.5 animate-spin" />}
            Send test message
          </button>
          <button className={btn.ghost} onClick={props.onClose}>
            Done
          </button>
        </div>
      </>
    )
  const until = pair.expires_at && new Date(pair.expires_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
  return (
    <>
      <div className="flex items-center gap-3">
        {pair.pair_url && (
          <a className={btn.primary} href={pair.pair_url} target="_blank" rel="noreferrer">
            Open {name} and tap Start
            <ArrowUpRight className="size-3.5" />
          </a>
        )}
        <span className="flex items-center gap-1.5 text-sm text-muted">
          <LoaderCircle className="size-3 animate-spin text-accent" />
          This updates by itself once you tap Start
        </span>
      </div>
      <p className="flex flex-wrap items-center gap-x-1 text-sm text-muted">
        {pair.pair_url ? "Or send" : "In a private chat with the bot, send"} <span className="rounded border border-line bg-raised px-1 font-mono text-xs text-fg">{pair.start}</span>
        <CopyButton text={pair.start ?? ""} />
        {pair.pair_url && `to ${name} in a private chat`}. <span className="tabular-nums">The code works until {until}.</span>
      </p>
    </>
  )
}

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false)
  const copy = () => navigator.clipboard.writeText(text).then(() => (setDone(true), setTimeout(() => setDone(false), 1200)))
  return (
    <button className="inline-flex h-5 items-center gap-1 rounded px-1 text-subtle hover:bg-hover hover:text-fg" aria-label={done ? "Copied" : "Copy the start command"} onClick={copy}>
      <Copy className="size-3" />
      {done && <span className="text-xs">Copied</span>}
    </button>
  )
}

// ---------------------------------------------------------------- your account

function AccountCard() {
  const me = useQuery(meOptions())
  const [current, setCurrent] = useState("")
  const [next, setNext] = useState("")
  const change = useMutation({ ...changePasswordMutation(), onSuccess: () => (setCurrent(""), setNext("")) })
  const out = useMutation({ ...logoutMutation(), onSettled: () => location.assign("/login") }) // a full load: no cache survives
  if (!me.data) return null
  const { used_bytes: used, quota_bytes: quota } = me.data.storage
  const share = quota ? Math.min(1, used / quota) : 0

  return (
    <Card
      title="Account"
      aside={
        <button className={btn.secondary} disabled={out.isPending} onClick={() => out.mutate({})}>
          Log out
        </button>
      }
    >
      <p>
        Signed in as <span className="font-medium">{me.data.username}</span>
      </p>
      <div>
        <div className="flex items-baseline justify-between">
          <span className={label}>Storage</span>
          <span className="text-sm tabular-nums text-muted">
            <span className="text-fg">{gb(used)}</span> {quota == null ? "used · no limit" : `of ${gb(quota)}`}
          </span>
        </div>
        {quota != null && (
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-line">
            <div className={cn("h-full rounded-full", share >= 1 ? "bg-bad" : share > 0.8 ? "bg-warn" : "bg-muted")} style={{ width: `${share * 100}%` }} />
          </div>
        )}
        <p className="mt-1.5 text-sm text-subtle">Your clips and renders. When it is full, uploads, imports and renders stop until you delete some.</p>
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          change.mutate({ body: { current, new: next } })
        }}
      >
        <div className={cn(label, "mb-1.5")}>Change password</div>
        <div className="flex gap-2">
          <input className={input} type="password" aria-label="Current password" placeholder="Current password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          <input className={input} type="password" aria-label="New password" placeholder="New password, 8+ characters" autoComplete="new-password" minLength={8} maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} />
          <button className={btn.secondary} disabled={!current || next.length < 8 || change.isPending}>
            Change
          </button>
        </div>
        <div className="mt-2 empty:hidden">
          {change.isSuccess && <Line tone="ok">Password changed. Your other sessions are signed out.</Line>}
          {change.isError && <Problem e={change.error} />}
        </div>
      </form>
    </Card>
  )
}

// ---------------------------------------------------------------- bits

/** A setup step: its number (a check once done) and live state, then its body. */
function Card({ n, done, title, aside, children }: { n?: number; done?: boolean; title: string; aside: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-md border border-line bg-panel">
      <header className="flex h-10 items-center gap-2.5 border-b border-line px-3">
        {n != null && (
          <span className={cn("grid size-5 shrink-0 place-items-center rounded-full text-xs tabular-nums", done ? "bg-ok/15 text-ok" : "border border-line-strong text-muted")}>
            {done ? <Check className="size-3" strokeWidth={2.5} aria-label="Done" /> : n}
          </span>
        )}
        <h2 className="font-medium">{title}</h2>
        <span className="ml-auto">{aside}</span>
      </header>
      <div className="space-y-3 p-3">{children}</div>
    </section>
  )
}

const ICON = { ok: [Check, "text-ok"], warn: [TriangleAlert, "text-warn"], bad: [CircleAlert, "text-bad"] } as const

/** One checked fact with its verdict. */
function Line({ tone, children }: { tone: keyof typeof ICON; children: ReactNode }) {
  const [Icon, color] = ICON[tone]
  return (
    <p className="flex gap-2">
      <Icon className={cn("mt-0.5 size-3.5 shrink-0", color)} />
      <span className="min-w-0">{children}</span>
    </p>
  )
}

/** An api error in plain words, with its code for reference. */
function Problem({ e }: { e: unknown }) {
  const { code } = apiError(e)
  return (
    <Line tone="bad">
      <span className="text-bad">{say(e)}</span>
      {code && <span className="ml-2 font-mono text-xs text-subtle">{code}</span>}
    </Line>
  )
}
