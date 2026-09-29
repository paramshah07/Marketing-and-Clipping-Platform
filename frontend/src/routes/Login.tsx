import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { LoaderCircle } from "lucide-react"
import { useState } from "react"
import { Link, useNavigate, useSearchParams } from "react-router"

import { login, signup, type Credentials } from "@/api"
import { signupStatusOptions } from "@/api/@tanstack/react-query.gen"
import { say } from "@/lib/schedule"
import { btn, cn, field, label, nextPath } from "@/lib/utils"

export const Login = () => <SignIn />
export const Signup = () => <SignIn fresh />

const input = cn(field, "w-full border-line-strong bg-bg")
const link = "text-fg underline decoration-line-strong underline-offset-2 hover:decoration-fg"

/** /login and /signup, outside the Shell: a username and a password, nothing else. Signup goes on to /setup, sign-in
 * back to where the 401 came from (?next=). */
function SignIn({ fresh }: { fresh?: boolean }) {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const spots = useQuery({ ...signupStatusOptions(), enabled: !!fresh })
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const go = useMutation({
    mutationFn: async (body: Credentials) => (fresh ? await signup({ body, throwOnError: true }) : await login({ body, throwOnError: true })).data,
    onSuccess: () => {
      qc.clear() // nothing cached from before this session
      navigate(fresh ? "/setup" : nextPath(params.get("next")), { replace: true })
    },
  })
  const full = fresh && spots.data?.open === false
  const left = spots.data?.remaining ?? 0

  return (
    <div className="grid min-h-dvh place-items-center p-4">
      <form
        className="w-[320px] space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          go.mutate({ username, password })
        }}
      >
        <div className="flex items-center gap-2 px-1">
          <div className="size-4 rounded-sm bg-fg" />
          <span className="text-md font-semibold tracking-tight">Clipper</span>
        </div>
        <div className="space-y-3 rounded-md border border-line bg-panel p-4">
          <h1 className="text-lg font-semibold">{fresh ? "Create your account" : "Sign in"}</h1>
          <label className="block">
            <span className={cn(label, "mb-1.5 block")}>Username</span>
            <input className={input} required autoFocus autoComplete="username" autoCapitalize="none" spellCheck={false} maxLength={32} value={username} onChange={(e) => setUsername(e.target.value)} />
            {fresh && <span className="mt-1 block text-sm text-subtle">3 to 32 characters: a–z, 0–9, dot, dash or underscore</span>}
          </label>
          <label className="block">
            <span className={cn(label, "mb-1.5 block")}>Password</span>
            <input className={input} type="password" required minLength={fresh ? 8 : undefined} maxLength={128} autoComplete={fresh ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} />
            {fresh && <span className="mt-1 block text-sm text-subtle">At least 8 characters</span>}
          </label>
          {fresh && spots.data && (full ? <p className="text-sm text-warn">Signups are full.</p> : <p className="text-sm tabular-nums text-muted">{left} spot{left === 1 ? "" : "s"} left</p>)}
          {go.isError && (
            <p role="alert" className="text-sm text-bad">
              {say(go.error)}
            </p>
          )}
          <button className={cn(btn.primary, "h-8 w-full")} disabled={go.isPending || full}>
            {go.isPending && <LoaderCircle className="size-3.5 animate-spin" />}
            {fresh ? "Create account" : "Sign in"}
          </button>
        </div>
        <p className="px-1 text-sm text-muted">
          {fresh ? (
            <>
              Have an account?{" "}
              <Link to="/login" className={link}>
                Sign in
              </Link>
            </>
          ) : (
            <>
              No account yet?{" "}
              <Link to="/signup" className={link}>
                Sign up
              </Link>
            </>
          )}
        </p>
      </form>
    </div>
  )
}
