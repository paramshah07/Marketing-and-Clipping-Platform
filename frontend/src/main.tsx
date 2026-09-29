import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router"

import "./index.css"
import { client } from "@/api/client.gen"
import { signedOut } from "@/lib/utils"
import { App } from "./App"

// A file dropped outside a drop zone would make Chrome open it in this tab, unloading the app and any
// in-flight upload. Zones call preventDefault themselves first (lower in the bubble path).
addEventListener("dragover", (e) => {
  if (e.defaultPrevented || !e.dataTransfer) return
  e.preventDefault()
  e.dataTransfer.dropEffect = "none"
})
addEventListener("drop", (e) => e.preventDefault())

// A 401 from any call but sign-in's own (a wrong password is one): the session is gone (expired, signed out
// elsewhere, password changed), so to /login?next= and back after.
client.interceptors.response.use((res, req) => {
  if (res.status === 401 && !new URL(req.url).pathname.startsWith("/api/auth/")) signedOut()
  return res
})

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>
)
