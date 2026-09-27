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
  // One status, the first thing in the way of a post going out: api, database, worker, then the publishing switch
  const bad = { dot: "bg-bad", text: "text-bad" }
  const status = isError
    ? { ...bad, label: "API offline", hint: "The api isn't answering: nothing on this page is current." }
    : !st
      ? { dot: "bg-subtle", text: "", label: "Checking…", hint: "Checking…" }
      : !st.db
        ? { ...bad, label: "Database offline", hint: "Nothing renders or publishes until the database is back." }
        : !st.worker_alive
          ? { ...bad, label: "Worker offline", hint: "Nothing renders or publishes until the worker is back." }
          : !st.publishing_enabled
            ? { dot: "bg-warn", text: "text-warn", label: "Publishing off", hint: "Worker online, renders run. PUBLISHING_ENABLED is off or ZERNIO_API_KEY is unset: scheduled posts stay Scheduled and nothing reaches Instagram." }
            : { dot: "bg-ok", text: "", label: "Publishing live", hint: "Worker online. Scheduled posts go out to Instagram at their time." }
  const { pathname } = useLocation()
  // The badge opens the oldest failure, which may sit in a week the calendar isn't showing.
  const failed = useQuery({ ...listPostsOptions({ query: { status: ["FAILED", "DEAD_LETTER"] } }), enabled: !!st?.failed_posts })
  const oldest = failed.data?.reduce((a, b) => (Date.parse(b.scheduled_for) < Date.parse(a.scheduled_for) ? b : a), failed.data[0])
  // The calendar needs every column it can get: below 1400 px the sidebar folds to an icon rail there.
  const rail = pathname.startsWith("/calendar")
  const word = rail ? "max-[1400px]:sr-only" : ""
  return (
    <div className="flex h-screen overflow-hidden">
      <aside className={cn("flex w-[200px] shrink-0 flex-col border-r border-line bg-panel", rail && "max-[1400px]:w-14")}>
        <div className={cn("flex h-12 items-center gap-2 border-b border-line px-4", rail && "max-[1400px]:justify-center max-[1400px]:px-0")}>
          <div className="size-4 shrink-0 rounded-sm bg-fg" />
          <span className={cn("text-md font-semibold tracking-tight", word)}>Clipper</span>
        </div>
        <nav className="space-y-0.5 p-2">
          {NAV.map(({ to, label, icon: Icon, also }) => (
            <div key={to} className="relative">
              <NavLink
                to={to}
                className={({ isActive }) =>
                  cn(
                    "flex h-8 items-center gap-2.5 rounded px-2.5",
                    rail && "max-[1400px]:justify-center max-[1400px]:px-0",
                    isActive || (also && pathname.startsWith(also)) ? "bg-raised text-fg" : "text-muted hover:bg-hover"
                  )
                }
              >
                <Icon className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
                <span className={word}>{label}</span>
              </NavLink>
              {to === "/calendar" && !!st?.failed_posts && (
                <Link
                  to={oldest ? `/recover/${oldest.id}` : "/calendar"}
                  title={`${st.failed_posts} failed post${st.failed_posts === 1 ? "" : "s"}: open the oldest`}
                  className={cn(
                    "absolute top-1.5 right-2 rounded bg-bad/15 px-1.5 text-xs leading-5 font-medium tabular-nums text-bad hover:bg-bad/25",
                    rail && "max-[1400px]:-top-0.5 max-[1400px]:right-0 max-[1400px]:px-1 max-[1400px]:leading-4"
                  )}
                >
                  {st.failed_posts}
                </Link>
              )}
            </div>
          ))}
        </nav>
        {/* min-h: two rows reserved, so the footer doesn't grow once status loads */}
        <div className={cn("mt-auto min-h-[63px] space-y-1.5 border-t border-line p-3 text-sm text-muted", rail && "max-[1400px]:[&>div]:justify-center")}>
          <div className={cn("flex items-center gap-2", status.text)} title={status.hint}>
            <span className={cn("size-1.5 shrink-0 rounded-full", status.dot)} />
            <span className={word}>{status.label}</span>
          </div>
          {st && (
            <div className={cn("tabular-nums", word)}>
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
