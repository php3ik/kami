import { useSimStore } from '../stores/simStore'
import { FastForward, Pause, Play, Settings2, StepForward } from 'lucide-react'

interface Props {
  onOpenSettings?: () => void
}

export default function TimeControls({ onOpenSettings }: Props) {
  const { step, startRun, pause, running, paused } = useSimStore()

  return (
    <div className="flex flex-wrap items-center gap-2 px-4 py-2 bg-slate-900 border-b border-slate-800">
      <button
        onClick={() => step(1)}
        disabled={running}
        className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-600 hover:bg-blue-500 disabled:bg-slate-800 disabled:text-slate-500 rounded text-sm font-semibold transition-colors"
      >
        <StepForward size={15} />
        Step 1
      </button>
      <button
        onClick={() => step(10)}
        disabled={running}
        className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-700 hover:bg-blue-600 disabled:bg-slate-800 disabled:text-slate-500 rounded text-sm font-semibold transition-colors"
      >
        <FastForward size={15} />
        Step 10
      </button>
      <button
        onClick={() => step(100)}
        disabled={running}
        className="inline-flex items-center gap-2 px-3 py-1.5 bg-blue-800 hover:bg-blue-700 disabled:bg-slate-800 disabled:text-slate-500 rounded text-sm font-semibold transition-colors"
      >
        <FastForward size={15} />
        Step 100
      </button>
      <button
        onClick={() => startRun(100)}
        disabled={running}
        className="inline-flex items-center gap-2 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-800 disabled:text-slate-500 rounded text-sm font-semibold transition-colors"
      >
        <Play size={15} />
        Run 100
      </button>
      {running && (
        <button
          onClick={() => pause()}
          className="inline-flex items-center gap-2 px-3 py-1.5 bg-amber-600 hover:bg-amber-500 rounded text-sm font-semibold transition-colors"
        >
          <Pause size={15} />
          Pause
        </button>
      )}
      <div className="ml-auto text-xs text-slate-500 min-w-[120px] text-right">
        {paused ? 'Ready for the next tick' : running ? 'Streaming sub-tick activity' : 'Simulation idle'}
      </div>
      <button
        onClick={onOpenSettings}
        className="inline-flex h-8 w-8 items-center justify-center border border-slate-800 bg-slate-950 text-slate-400 transition-colors hover:border-purple-500 hover:text-slate-100"
        title="LLM settings"
      >
        <Settings2 size={15} />
      </button>
    </div>
  )
}
