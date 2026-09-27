import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef } from "react"
import { AtSign, CalendarDays, Film, Stamp } from "lucide-react"
import { Link, NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router"

import { listPostsOptions, statusOptions } from "@/api/@tanstack/react-query.gen"
import { Empty, Header } from "@/components/bits"
import { cn } from "@/lib/utils"
import { Accounts } from "@/routes/Accounts"
import { Brands } from "@/routes/Brands"
import { Calendar } from "@/routes/Calendar"
import { Editor } from "@/routes/Editor"
import { Library } from "@/routes/Library"
import { Recover } from "@/routes/Recover"

export function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/library" replace />} />
        <Route path="library" element={<Library />} />
        <Route path="editor/:clipId" element={<Editor />} />
        <Route path="brands" element={<Brands />} />
        <Route path="calendar" element={<Calendar />} />
        <Route path="accounts" element={<Accounts />} />
        <Route path="*" element={<Soon title="Not found" note="Nothing lives at this address." />} />
      </Route>
      <Route path="recover/:postId" element={<Recover />} /> {/* mobile-first, no sidebar */}
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
  // After an outage (api or db down), pages stuck on "Couldn't load …" reload themselves once it answers again
  const qc = useQueryClient()
  const down = useRef(false)
  const healthy = !isError && !!st?.db
  useEffect(() => {
    if (!healthy) {
      if (isError || st) down.current = true // a retry in flight (no error, no data yet) keeps the flag
      return
    }
    if (down.current) {
      down.current = false
      qc.refetchQueries({ predicate: (q) => q.state.status === "error" })
    }
  }, [healthy, isError, st, qc])
  const online = !isError && st?.db && st.worker_alive
  const { pathname } = useLocation()
  // The badge opens the oldest failure, which may sit in a week the calendar isn't showing.
  const failed = useQuery({ ...listPostsOptions({ query: { status: ["FAILED", "DEAD_LETTER"] } }), enabled: !!st?.failed_posts })
  const oldest = failed.data?.reduce((a, b) => (Date.parse(b.scheduled_for) < Date.parse(a.scheduled_for) ? b : a), failed.data[0])
  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="flex w-[200px] shrink-0 flex-col border-r border-line bg-panel">
        <div className="flex h-12 items-center gap-2 border-b border-line px-4">
          <div className="size-4 rounded-sm bg-fg" />
          <span className="text-md font-semibold tracking-tight">Clipper</span>
        </div>
        <nav className="space-y-0.5 p-2">
          {NAV.map(({ to, label, icon: Icon, also }) => (
            <div key={to} className="relative">
              <NavLink
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
              {to === "/calendar" && !!st?.failed_posts && (
                <Link
                  to={oldest ? `/recover/${oldest.id}` : "/calendar"}
                  title={`${st.failed_posts} failed post${st.failed_posts === 1 ? "" : "s"}: open the oldest`}
                  className="absolute top-1.5 right-2 rounded bg-bad/15 px-1.5 text-xs leading-5 font-medium tabular-nums text-bad hover:bg-bad/25"
                >
                  {st.failed_posts}
                </Link>
              )}
            </div>
          ))}
        </nav>
        {/* min-h: three rows reserved, so the footer doesn't grow once status loads */}
        <div className="mt-auto min-h-[89px] space-y-1.5 border-t border-line p-3 text-sm text-muted">
          <div className="flex items-center gap-2">
            <span className={cn("size-1.5 rounded-full", online ? "bg-ok" : !st && !isError ? "bg-subtle" : "bg-bad")} />
            {isError ? "API offline" : !st ? "Checking…" : !st.db ? "Database offline" : online ? "Worker online" : "Worker offline"}
          </div>
          {st?.publishing_enabled === true && (
            <div className="flex items-center gap-2" title={online ? "Scheduled posts go out to Instagram at their time" : "Nothing publishes until the worker and database are back"}>
              <span className={cn("size-1.5 rounded-full", online ? "bg-ok" : "bg-subtle")} />
              {online ? "Publishing live" : "Publishing paused"}
            </div>
          )}
          {st?.publishing_enabled === false && (
            <div className="flex items-center gap-2 text-warn" title="PUBLISHING_ENABLED is off or ZERNIO_API_KEY is unset: scheduled posts stay Scheduled and nothing reaches Instagram.">
              <span className="size-1.5 rounded-full bg-warn" />
              Publishing off
            </div>
          )}
          {st && (
            <div className="tabular-nums">
              {st.rendering_renders ?? 0} rendering · {st.scheduled_posts ?? 0} scheduled
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

function Soon({ title, note }: { title: string; note: string }) {
  return (
    <>
      <Header>
        <h1 className="text-lg font-semibold">{title}</h1>
      </Header>
      <Empty>{note}</Empty>
    </>
  )
}
