import { useSimStore } from '../stores/simStore'

export default function MoodStrip() {
  const { currentTick, running, paused, totalCostUsd, totalCalls } = useSimStore()

  const simMinutes = currentTick
  const simHours = Math.floor(simMinutes / 60)
  const simMins = simMinutes % 60
  const simDays = Math.floor(simHours / 24)
  const dayHours = simHours % 24

  return (
    <div className="flex items-center justify-between px-4 py-2 bg-gray-900 border-b border-gray-800 text-sm font-mono">
      <div className="flex gap-6">
        <span className="text-gray-400">
          Tick: <span className="text-white font-bold">{currentTick}</span>
        </span>
        <span className="text-gray-400">
          Sim time:{' '}
          <span className="text-white">
            Day {simDays + 1}, {String(dayHours).padStart(2, '0')}:{String(simMins).padStart(2, '0')}
          </span>
        </span>
      </div>
      <div className="flex gap-6">
        <span className="text-gray-400">
          Status:{' '}
          <span className={running ? 'text-green-400' : paused ? 'text-yellow-400' : 'text-gray-500'}>
            {running ? 'RUNNING' : paused ? 'PAUSED' : 'IDLE'}
          </span>
        </span>
        <span className="text-gray-400">
          LLM calls: <span className="text-white">{totalCalls}</span>
        </span>
        <span className="text-gray-400">
          Cost:{' '}
          <span className="text-emerald-400 font-bold">${totalCostUsd.toFixed(4)}</span>
        </span>
      </div>
    </div>
  )
}
