import { LoaderCircle } from "lucide-react"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

export function Header({ children }: { children: ReactNode }) {
  return <header className="flex h-12 shrink-0 items-center justify-between gap-4 border-b border-line px-4">{children}</header>
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="grid flex-1 place-items-center p-8 text-center text-muted">{children}</div>
}

const TONES = { accent: "bg-accent/10 text-accent", ok: "bg-ok/10 text-ok", bad: "bg-bad/10 text-bad", warn: "bg-warn/10 text-warn" }

export function Chip({ tone, spin, dot, children }: { tone: keyof typeof TONES; spin?: boolean; dot?: boolean; children: ReactNode }) {
  return (
    <span className={cn("inline-flex h-5 items-center gap-1.5 whitespace-nowrap rounded px-1.5 text-xs font-medium", TONES[tone])}>
      {spin && <LoaderCircle className="size-3 animate-spin" />}
      {dot && <span className="size-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}
