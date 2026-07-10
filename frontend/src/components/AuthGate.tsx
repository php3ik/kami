import { FormEvent, ReactNode, useEffect, useState } from 'react'
import { KeyRound, Loader2, LogIn } from 'lucide-react'
import { fetchAuthStatus, setApiToken, wsClient } from '../api/client'

type AuthState = 'checking' | 'locked' | 'open'

export default function AuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>('checking')
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let active = true
    fetchAuthStatus()
      .then((status) => {
        if (!active) return
        setState(!status.required || status.authenticated ? 'open' : 'locked')
      })
      .catch(() => {
        if (!active) return
        setError('Cannot reach the Kami API')
        setState('locked')
      })

    const requireAuth = () => {
      wsClient.disconnect()
      setError('Session authorization is no longer valid')
      setState('locked')
    }
    window.addEventListener('kami-auth-required', requireAuth)
    return () => {
      active = false
      window.removeEventListener('kami-auth-required', requireAuth)
    }
  }, [])

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    setApiToken(token)
    try {
      const status = await fetchAuthStatus()
      if (!status.authenticated) {
        setApiToken('')
        setError('Invalid API token')
        return
      }
      setToken('')
      setState('open')
    } catch {
      setApiToken('')
      setError('Authorization check failed')
    } finally {
      setSubmitting(false)
    }
  }

  if (state === 'open') return children

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-950 px-4 text-slate-100">
      {state === 'checking' ? (
        <div className="flex items-center gap-3 text-sm text-slate-400">
          <Loader2 size={18} className="animate-spin" />
          Connecting to Kami
        </div>
      ) : (
        <form onSubmit={submit} className="w-full max-w-sm border border-slate-700 bg-slate-900 p-6 shadow-2xl">
          <div className="mb-6 flex items-center gap-3">
            <div className="flex size-10 items-center justify-center border border-cyan-500/50 bg-cyan-950 text-cyan-200">
              <KeyRound size={20} />
            </div>
            <div>
              <h1 className="text-xl font-semibold">Kami</h1>
              <p className="text-sm text-slate-400">Operator access</p>
            </div>
          </div>
          <label className="block text-xs font-semibold uppercase text-slate-400">
            API token
            <input
              autoFocus
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(event) => setToken(event.target.value)}
              className="mt-2 w-full border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-slate-100 outline-none focus:border-cyan-400"
            />
          </label>
          {error && <div className="mt-3 border border-rose-500/60 bg-rose-950/40 px-3 py-2 text-sm text-rose-100">{error}</div>}
          <button
            type="submit"
            disabled={submitting || !token.trim()}
            className="mt-5 flex w-full items-center justify-center gap-2 bg-cyan-700 px-4 py-2.5 text-sm font-semibold text-white hover:bg-cyan-600 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? <Loader2 size={16} className="animate-spin" /> : <LogIn size={16} />}
            Sign in
          </button>
        </form>
      )}
    </main>
  )
}
