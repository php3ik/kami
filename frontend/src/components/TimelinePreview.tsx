import { useEffect, useMemo } from 'react'
import { Bot, Map as MapIcon, MessageCircle, MoveRight, Radio, Sparkles } from 'lucide-react'
import { useSimStore } from '../stores/simStore'
import { digest, humanizeId, kindLabel } from '../utils/simView'

function cellTone(cell: any) {
  if (!cell) return 'bg-slate-900/60 border-slate-800 hover:border-slate-600'
  const types = new Set(Array.isArray(cell.event_types) ? cell.event_types : [cell.event_types].filter(Boolean))
  if (types.has('conversation') || types.has('talk')) return 'bg-cyan-950/80 border-cyan-700/80 hover:border-cyan-400'
  if (types.has('move') || types.has('arrival') || types.has('departure')) return 'bg-blue-950/80 border-blue-700/80 hover:border-blue-400'
  if (types.has('idle')) return 'bg-slate-900 border-slate-700 hover:border-slate-500'
  if (cell.salience >= 0.75) return 'bg-rose-950/80 border-rose-700/80 hover:border-rose-400'
  if (cell.salience >= 0.45) return 'bg-amber-950/80 border-amber-700/80 hover:border-amber-400'
  return 'bg-purple-950/70 border-purple-700/80 hover:border-purple-400'
}

function cellIcon(cell: any) {
  const types = new Set(Array.isArray(cell?.event_types) ? cell.event_types : [cell?.event_types].filter(Boolean))
  if (types.has('conversation')) return MessageCircle
  if (types.has('move') || types.has('arrival') || types.has('departure')) return MoveRight
  if (types.has('intent')) return Sparkles
  if (types.has('thought')) return Bot
  return Radio
}

export default function TimelinePreview() {
  const {
    timelineMode,
    timelineData,
    timelineSelection,
    setTimelineMode,
    loadTimeline,
    selectTimelineCell,
    currentTick,
  } = useSimStore()

  useEffect(() => {
    loadTimeline()
  }, [])

  const ticks = timelineData?.ticks || []
  const rows = timelineData?.rows || []
  const kind = timelineMode === 'agents' ? 'agent' : 'kami'

  const visibleRows = useMemo(() => {
    return [...rows].sort((a: any, b: any) => {
      const aActivity = a.cells?.length || 0
      const bActivity = b.cells?.length || 0
      return bActivity - aActivity || (a.name || '').localeCompare(b.name || '')
    })
  }, [rows])

  return (
    <div className="h-full min-h-0 bg-slate-950 flex flex-col">
      <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-slate-800 bg-slate-900/80">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">Timeline Preview</h2>
          <p className="text-xs text-slate-500">
            Click any tick cell to inspect that {timelineMode === 'agents' ? 'character' : 'kami'} at that moment.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setTimelineMode('agents')}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded border text-xs transition-colors ${
              timelineMode === 'agents'
                ? 'bg-purple-700 border-purple-400 text-white'
                : 'bg-slate-950 border-slate-700 text-slate-300 hover:border-slate-500'
            }`}
          >
            <Bot size={13} />
            Agents
          </button>
          <button
            onClick={() => setTimelineMode('kami')}
            className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded border text-xs transition-colors ${
              timelineMode === 'kami'
                ? 'bg-blue-700 border-blue-400 text-white'
                : 'bg-slate-950 border-slate-700 text-slate-300 hover:border-slate-500'
            }`}
          >
            <MapIcon size={13} />
            Kami
          </button>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-auto">
        <div className="min-w-max">
          <div className="sticky top-0 z-20 grid bg-slate-950/95 backdrop-blur border-b border-slate-800" style={{ gridTemplateColumns: `260px repeat(${ticks.length}, 42px)` }}>
            <div className="sticky left-0 z-30 bg-slate-950/95 px-3 py-2 text-[11px] uppercase tracking-wider text-slate-500 border-r border-slate-800">
              {timelineMode === 'agents' ? 'Character' : 'Kami'}
            </div>
            {ticks.map((tick: number) => (
              <div
                key={tick}
                className={`px-1 py-2 text-center text-[11px] tabular-nums border-r border-slate-900 ${
                  tick === currentTick ? 'text-emerald-300 bg-emerald-950/20' : 'text-slate-500'
                }`}
              >
                {tick}
              </div>
            ))}
          </div>

          {visibleRows.length === 0 ? (
            <div className="p-6 text-sm text-slate-500">No timeline data yet. Run a few ticks to grow the matrix.</div>
          ) : (
            visibleRows.map((row: any) => {
              const cells = new Map<number, any>((row.cells || []).map((cell: any) => [cell.tick, cell]))
              return (
                <div
                  key={row.id}
                  className="grid border-b border-slate-900/80"
                  style={{ gridTemplateColumns: `260px repeat(${ticks.length}, 42px)` }}
                >
                  <div className="sticky left-0 z-10 bg-slate-950 border-r border-slate-800 px-3 py-2 min-w-0">
                    <div className="flex items-center gap-2 min-w-0">
                      <div className={`h-7 w-7 rounded border grid place-items-center flex-shrink-0 ${
                        timelineMode === 'agents' ? 'bg-purple-950 border-purple-800 text-purple-200' : 'bg-blue-950 border-blue-800 text-blue-200'
                      }`}>
                        {timelineMode === 'agents' ? <Bot size={13} /> : <MapIcon size={13} />}
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm font-semibold text-slate-100 truncate">{row.name}</div>
                        <div className="text-[11px] text-slate-500 truncate">
                          {timelineMode === 'agents'
                            ? row.archetype?.role || row.id
                            : kindLabel(row.archetype?.kind || row.archetype?.kami_kind)}
                        </div>
                      </div>
                    </div>
                  </div>

                  {ticks.map((tick: number) => {
                    const cell = cells.get(tick)
                    const Icon = cellIcon(cell)
                    const selected = timelineSelection?.id === row.id && timelineSelection?.tick === tick && timelineSelection?.kind === kind
                    const label = cell
                      ? `${row.name}, tick ${tick}: ${Array.isArray(cell.event_types) ? cell.event_types.join(', ') : cell.event_types || 'activity'}`
                      : `${row.name}, tick ${tick}: no recorded activity`
                    return (
                      <button
                        key={`${row.id}-${tick}`}
                        title={cell ? digest((Array.isArray(cell.narratives) ? cell.narratives[0] : cell.narratives) || cell.thought || cell.intent?.summary, 180) : label}
                        onClick={() => selectTimelineCell(kind, row.id, tick)}
                        className={`h-11 border-r border-b border-slate-950 grid place-items-center transition-all ${
                          selected ? 'outline outline-2 outline-yellow-300 z-10' : ''
                        } ${cellTone(cell)}`}
                      >
                        {cell ? (
                          <div className="flex flex-col items-center gap-0.5">
                            <Icon size={13} className="text-slate-100" />
                            <span className="text-[9px] text-slate-300 tabular-nums">
                              {cell.event_count || cell.intent ? cell.event_count || 1 : ''}
                            </span>
                          </div>
                        ) : (
                          <span className="h-1.5 w-1.5 rounded-full bg-slate-700" />
                        )}
                      </button>
                    )
                  })}
                </div>
              )
            })
          )}
        </div>
      </div>

      <div className="flex items-center gap-4 px-4 py-2 border-t border-slate-800 bg-slate-900/70 text-[11px] text-slate-500">
        <Legend color="bg-cyan-700" label="conversation" />
        <Legend color="bg-blue-700" label="movement" />
        <Legend color="bg-rose-700" label="high salience" />
        <Legend color="bg-purple-700" label="intent/thought" />
        <span className="ml-auto">{ticks.length} ticks · {visibleRows.length} rows</span>
      </div>
    </div>
  )
}

function Legend({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className={`h-2 w-2 rounded-full ${color}`} />
      {label}
    </span>
  )
}
