import { ChevronDown, Database, Play, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { useSimStore } from '../stores/simStore'

function fmtCost(value: number | undefined) {
  return `$${Number(value || 0).toFixed(4)}`
}

function fmtDate(value: string | undefined) {
  if (!value) return 'unknown'
  return new Date(value).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export default function WorldSwitcher() {
  const { simulations, activeSimulationId, switchSimulation, deleteSimulation, loadSimulations } = useSimStore()
  const [open, setOpen] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)

  const active = useMemo(
    () => simulations.find((sim: any) => sim.id === activeSimulationId) || simulations[0],
    [simulations, activeSimulationId],
  )

  const handleSwitch = async (id: string) => {
    if (id === activeSimulationId) return
    setBusyId(id)
    try {
      await switchSimulation(id)
      setOpen(false)
    } finally {
      setBusyId(null)
    }
  }

  const handleDelete = async (id: string) => {
    setBusyId(id)
    try {
      await deleteSimulation(id)
      await loadSimulations()
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="rounded border border-slate-800 bg-slate-900/70 overflow-hidden">
      <button
        onClick={() => setOpen(!open)}
        className="w-full px-3 py-2.5 flex items-center justify-between gap-2 text-left hover:bg-slate-900 transition-colors"
      >
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-slate-100 font-semibold">
            <Database size={14} className="text-emerald-300" />
            <span className="truncate">{active?.name || 'No world selected'}</span>
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-slate-500">
            <span>{active?.ticks ?? 0} ticks</span>
            <span>{active?.population ?? 0} agents</span>
            <span>{active?.kami_count ?? 0} kami</span>
            <span>{fmtCost(active?.total_cost_usd)}</span>
          </div>
        </div>
        <ChevronDown size={14} className={`text-slate-500 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="border-t border-slate-800 max-h-80 overflow-y-auto">
          {simulations.length === 0 ? (
            <div className="p-3 text-xs text-slate-500">No saved worlds yet.</div>
          ) : simulations.map((sim: any) => {
            const isActive = sim.id === activeSimulationId
            return (
              <div key={sim.id} className={`p-2 border-b border-slate-800/70 ${isActive ? 'bg-emerald-950/20' : ''}`}>
                <div className="flex items-start justify-between gap-2">
                  <button
                    onClick={() => handleSwitch(sim.id)}
                    disabled={Boolean(busyId)}
                    className="min-w-0 flex-1 text-left"
                  >
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-slate-100 truncate">{sim.name}</span>
                      {isActive && <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-900 text-emerald-200 border border-emerald-700">active</span>}
                    </div>
                    <div className="mt-1 text-[11px] text-slate-500 line-clamp-2">{sim.prompt}</div>
                    <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 text-[10px] text-slate-600">
                      <span>{sim.ticks ?? 0} ticks</span>
                      <span>{sim.population ?? 0} pop</span>
                      <span>{sim.kami_count ?? 0} kami</span>
                      <span>{sim.event_count ?? 0} events</span>
                      <span>{fmtCost(sim.total_cost_usd)}</span>
                      <span>{fmtDate(sim.updated_at)}</span>
                    </div>
                  </button>
                  <div className="flex items-center gap-1">
                    {!isActive && (
                      <button
                        title="Switch to world"
                        onClick={() => handleSwitch(sim.id)}
                        disabled={Boolean(busyId)}
                        className="h-7 w-7 rounded border border-slate-700 bg-slate-950 text-slate-300 hover:border-blue-500 hover:text-blue-200 grid place-items-center"
                      >
                        <Play size={12} />
                      </button>
                    )}
                    {!isActive && (
                      <button
                        title="Delete world"
                        onClick={() => handleDelete(sim.id)}
                        disabled={Boolean(busyId)}
                        className="h-7 w-7 rounded border border-slate-700 bg-slate-950 text-slate-400 hover:border-rose-500 hover:text-rose-200 grid place-items-center"
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
