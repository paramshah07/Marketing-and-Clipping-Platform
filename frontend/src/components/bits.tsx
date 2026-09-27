import { LoaderCircle } from "lucide-react"
import { useEffect, useRef, type ReactNode } from "react"

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

/** Full-height right drawer over a dimmed page; the backdrop or Escape closes it. */
export function Drawer({ label, onClose, className, children }: { label: string; onClose: () => void; className?: string; children: ReactNode }) {
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && !document.fullscreenElement && onClose() // Escape leaves a fullscreen video first
    addEventListener("keydown", onKey)
    return () => removeEventListener("keydown", onKey)
  }, [onClose])
  useEffect(() => {
    // focus moves in on open and back to whatever opened it on close
    const prev = document.activeElement as HTMLElement | null
    ref.current?.focus()
    return () => prev?.focus()
  }, [])
  return (
    <>
      <div className="fixed inset-0 z-40 bg-black/40" onClick={onClose} />
      <aside
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className={cn("fixed inset-y-0 right-0 z-40 flex w-[400px] flex-col border-l border-line bg-panel shadow-[-12px_0_32px_rgba(0,0,0,0.45)] outline-none", className)}
      >
        {children}
      </aside>
    </>
  )
}
