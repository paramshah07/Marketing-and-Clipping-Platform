import type { AccountOut } from "@/api"
import { Chip } from "@/components/bits"
import { cn } from "@/lib/utils"

export const ZERNIO_URL = "https://zernio.com"

export function Avatar({ a, className }: { a: Pick<AccountOut, "avatar_url" | "username">; className?: string }) {
  return a.avatar_url ? (
    <img src={a.avatar_url} alt="" referrerPolicy="no-referrer" className={cn("size-10 shrink-0 rounded-full bg-raised object-cover", className)} />
  ) : (
    <span className={cn("grid size-10 shrink-0 place-items-center rounded-full bg-raised font-medium text-muted uppercase", className)}>{a.username.slice(0, 1)}</span>
  )
}

/** Connection-status chip (replaces the mockups' token-expiry chip, PLAN §8). */
export function ConnChip({ a }: { a: Pick<AccountOut, "connection_status" | "disabled_at"> }) {
  if (a.disabled_at) return <span className="inline-flex h-5 items-center rounded bg-raised px-1.5 text-xs font-medium text-muted">Disabled</span>
  return a.connection_status === "connected" ? (
    <Chip tone="ok" dot>
      Connected
    </Chip>
  ) : (
    <Chip tone="bad" dot>
      Disconnected
    </Chip>
  )
}
