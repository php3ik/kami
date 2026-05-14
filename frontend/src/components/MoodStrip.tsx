import { useSimStore } from '../stores/simStore'
import { Activity, Clock, Cpu, DollarSign } from 'lucide-react'

export default function MoodStrip() {
  const { currentTick, running, paused, totalCostUsd, totalCalls } = useSimStore()

  const simMinutes = currentTick
  const simHours = Math.floor(simMinutes / 60)
  const simMins = simMinutes % 60
  const simDays = Math.floor(simHours / 24)
  const dayHours = simHours % 24

  return (
    <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 bg-slate-950 border-b border-slate-800 text-sm">
      <div className="flex flex-wrap items-center gap-2 min-w-0">
        <div className="flex items-center gap-2 px-2.5 py-1 rounded border border-slate-800 bg-slate-900/80">
          <Activity size={14} className={running ? 'text-emerald-400' : paused ? 'text-amber-400' : 'text-slate-500'} />
          <span className="text-slate-400">Status</span>
          <span className={running ? 'text-emerald-300 font-semibold' : paused ? 'text-amber-300 font-semibold' : 'text-slate-400 font-semibold'}>
            {running ? 'Running' : paused ? 'Paused' : 'Idle'}
          </span>
        </div>
        <div className="flex items-center gap-2 px-2.5 py-1 rounded border border-slate-800 bg-slate-900/80">
          <Clock size={14} className="text-blue-300" />
          <span className="text-slate-400">Tick</span>
          <span className="text-white font-semibold tabular-nums">{currentTick}</span>
          <span className="text-slate-600">/</span>
          <span className="text-slate-300 tabular-nums">
            Day {simDays + 1}, {String(dayHours).padStart(2, '0')}:{String(simMins).padStart(2, '0')}
          </span>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex items-center gap-2 px-2.5 py-1 rounded border border-slate-800 bg-slate-900/80">
          <Cpu size={14} className="text-purple-300" />
          <span className="text-slate-400">LLM</span>
          <span className="text-white font-semibold tabular-nums">{totalCalls}</span>
        </div>
        <div className="flex items-center gap-2 px-2.5 py-1 rounded border border-slate-800 bg-slate-900/80">
          <DollarSign size={14} className="text-emerald-300" />
          <span className="text-slate-400">Cost</span>
          <span className="text-emerald-300 font-semibold tabular-nums">${totalCostUsd.toFixed(4)}</span>
        </div>
      </div>
    </div>
  )
}
