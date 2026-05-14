import { useState } from 'react'
import { Box, ExternalLink, Loader2, MapPin } from 'lucide-react'
import * as api from '../../api/client'

const ENTITY_ID_RE = /\b(?:sim_[^\s.,;:!?()[\]{}"']+|(?:agent|kami|obj|animal|plant|vehicle|document|channel)_[^\s.,;:!?()[\]{}"']+)/g

const cache = new Map<string, any>()

function trimTrailingTokenPunctuation(value: string) {
  const match = value.match(/^(.*?)([),.;:!?]+)?$/)
  return {
    token: match?.[1] || value,
    trailing: match?.[2] || '',
  }
}

function labelFor(id: string, detail?: any) {
  if (detail?.name) return detail.name
  const clean = id.split('__').pop() || id
  return clean.replace(/^(obj|kami|agent|animal|plant|vehicle|document|channel)_/, '').replace(/_/g, ' ')
}

export default function EntityReferenceText({ text, className = '' }: { text?: string | null; className?: string }) {
  if (!text) return null

  const parts: Array<{ text: string; entityId?: string }> = []
  let lastIndex = 0
  for (const match of text.matchAll(ENTITY_ID_RE)) {
    const raw = match[0]
    const index = match.index ?? 0
    const { token, trailing } = trimTrailingTokenPunctuation(raw)
    if (index > lastIndex) parts.push({ text: text.slice(lastIndex, index) })
    parts.push({ text: token, entityId: token })
    if (trailing) parts.push({ text: trailing })
    lastIndex = index + raw.length
  }
  if (lastIndex < text.length) parts.push({ text: text.slice(lastIndex) })

  return (
    <span className={className}>
      {parts.map((part, index) => (
        part.entityId
          ? <EntityReference key={`${part.entityId}-${index}`} entityId={part.entityId} />
          : <span key={`text-${index}`}>{part.text}</span>
      ))}
    </span>
  )
}

function EntityReference({ entityId }: { entityId: string }) {
  const [detail, setDetail] = useState<any>(() => cache.get(entityId))
  const [open, setOpen] = useState(false)
  const [pinned, setPinned] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = async () => {
    if (cache.has(entityId) || loading) return
    setLoading(true)
    setError(null)
    try {
      const data = await api.fetchEntity(entityId)
      cache.set(entityId, data)
      setDetail(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load entity')
    } finally {
      setLoading(false)
    }
  }

  const show = () => {
    setOpen(true)
    void load()
  }

  const hide = () => {
    if (!pinned) setOpen(false)
  }

  return (
    <span className="relative inline-block">
      <button
        type="button"
        onMouseEnter={show}
        onFocus={show}
        onMouseLeave={hide}
        onClick={(event) => {
          event.preventDefault()
          setPinned((value) => !value)
          setOpen(true)
          void load()
        }}
        className="mx-0.5 inline-flex max-w-full items-center gap-1 rounded border border-cyan-700/70 bg-cyan-950/50 px-1.5 py-0.5 align-baseline font-mono text-[0.9em] text-cyan-100 underline decoration-cyan-500/60 underline-offset-2 hover:border-cyan-300 hover:bg-cyan-900/80"
        title="Inspect entity"
      >
        <Box size={11} />
        <span className="truncate">{labelFor(entityId, detail)}</span>
      </button>

      {open && (
        <span
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={hide}
          className="absolute left-0 top-full z-50 mt-1 block w-80 rounded border border-slate-700 bg-slate-950 p-3 text-left text-xs text-slate-300 shadow-2xl shadow-black/50"
        >
          <span className="mb-2 flex items-start justify-between gap-2">
            <span className="min-w-0">
              <span className="block truncate text-sm font-bold text-slate-100">
                {loading ? 'Loading entity...' : detail?.name || labelFor(entityId)}
              </span>
              <span className="mt-0.5 block break-all font-mono text-[10px] text-slate-500">{entityId}</span>
            </span>
            {pinned && (
              <button
                type="button"
                onClick={() => {
                  setPinned(false)
                  setOpen(false)
                }}
                className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] text-slate-400 hover:border-slate-400 hover:text-slate-100"
              >
                close
              </button>
            )}
          </span>

          {loading && (
            <span className="flex items-center gap-2 text-slate-400">
              <Loader2 size={13} className="animate-spin" />
              Loading details
            </span>
          )}
          {error && <span className="block text-rose-200">{error}</span>}
          {detail && <EntityCard detail={detail} />}
        </span>
      )}
    </span>
  )
}

function EntityCard({ detail }: { detail: any }) {
  const arch = detail.archetype || {}
  const states = detail.states || {}
  const location = detail.location || {}
  const description = arch.description || arch.background || arch.current_situation || arch.history

  return (
    <span className="block space-y-3">
      <span className="flex flex-wrap gap-1.5">
        <span className="rounded bg-slate-900 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-400">{detail.kind}</span>
        {detail.created_at_tick !== undefined && (
          <span className="rounded bg-slate-900 px-2 py-1 text-[10px] text-slate-400">created tick {detail.created_at_tick}</span>
        )}
      </span>

      {location.kami_name || location.kami_id ? (
        <span className="flex items-start gap-1.5 rounded border border-blue-900/60 bg-blue-950/30 px-2 py-1.5 text-blue-100">
          <MapPin size={12} className="mt-0.5 flex-shrink-0 text-blue-300" />
          <span className="min-w-0">
            <span className="block truncate">{location.kami_name || location.kami_id}</span>
            {location.container_name && <span className="block text-[10px] text-blue-300">inside {location.container_name}</span>}
          </span>
        </span>
      ) : null}

      {description && <span className="block leading-relaxed text-slate-300">{description}</span>}

      {Object.keys(states).length > 0 && (
        <span className="block">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">State</span>
          <span className="flex flex-wrap gap-1">
            {Object.entries(states).slice(0, 8).map(([key, value]) => (
              <span key={key} className="rounded border border-slate-800 bg-slate-900 px-1.5 py-0.5 text-[10px] text-slate-300">
                {key}: {String(value)}
              </span>
            ))}
          </span>
        </span>
      )}

      {Array.isArray(detail.recent_events) && detail.recent_events.length > 0 && (
        <span className="block border-t border-slate-800 pt-2">
          <span className="mb-1 block text-[10px] uppercase tracking-wider text-slate-500">Recent event</span>
          <span className="block leading-relaxed text-slate-400">
            Tick {detail.recent_events[0].tick}: {detail.recent_events[0].narrative}
          </span>
        </span>
      )}
    </span>
  )
}
