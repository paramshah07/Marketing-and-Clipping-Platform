import { useQuery } from "@tanstack/react-query"
import { AtSign, CalendarDays, Film, Stamp } from "lucide-react"
import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router"

import { statusOptions } from "@/api/@tanstack/react-query.gen"
import { Empty, Header } from "@/components/bits"
import { cn } from "@/lib/utils"
import { Brands } from "@/routes/Brands"
import { Editor } from "@/routes/Editor"
import { Library } from "@/routes/Library"

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/library" replace />} />
        <Route path="library" element={<Library />} />
        <Route path="editor/:clipId" element={<Editor />} />
        <Route path="brands" element={<Brands />} />
        <Route path="calendar" element={<Soon title="Calendar" />} />
        <Route path="accounts" element={<Soon title="Accounts" />} />
        <Route path="*" element={<Soon title="Not found" note="Nothing lives at this address." />} />
      </Route>
    </Routes>
  )
}

const NAV = [
  { to: "/library", label: "Library", icon: Film, also: "/editor" },
  { to: "/calendar", label: "Calendar", icon: CalendarDays },
  { to: "/accounts", label: "Accounts", icon: AtSign },
  { to: "/brands", label: "Brands", icon: Stamp },
]

function Shell() {
  const { data: st, isError } = useQuery({ ...statusOptions(), refetchInterval: 10_000 })
  const online = !isError && st?.worker_alive
  const { pathname } = useLocation()
  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-[200px] shrink-0 flex-col border-r border-line bg-panel">
        <div className="flex h-12 items-center gap-2 border-b border-line px-4">
          <div className="size-4 rounded-sm bg-fg" />
          <span className="text-md font-semibold tracking-tight">Clipper</span>
        </div>
        <nav className="space-y-0.5 p-2">
          {NAV.map(({ to, label, icon: Icon, also }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex h-8 items-center gap-2.5 rounded px-2.5",
                  isActive || (also && pathname.startsWith(also)) ? "bg-raised text-fg" : "text-muted hover:bg-hover"
                )
              }
            >
              <Icon className="size-4" strokeWidth={1.75} />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-1.5 border-t border-line p-3 text-sm text-muted">
          <div className="flex items-center gap-2">
            <span className={cn("size-1.5 rounded-full", online ? "bg-ok" : "bg-bad")} />
            {isError ? "API offline" : !st ? "Checking…" : online ? "Worker online" : "Worker offline"}
          </div>
          {st && (
            <div className="tabular-nums">
              {st.jobs.doing ?? 0} running · {st.jobs.todo ?? 0} queued
            </div>
          )}
        </div>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <Outlet />
      </main>
    </div>
  )
}

function Soon({ title, note = "Coming in Phase 4." }: { title: string; note?: string }) {
  return (
    <>
      <Header>
        <h1 className="text-lg font-semibold">{title}</h1>
      </Header>
      <Empty>{note}</Empty>
    </>
  )
}
