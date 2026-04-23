import { useSimStore } from '../stores/simStore'

export default function TimeControls() {
  const { step, startRun, pause, running, paused } = useSimStore()

  return (
    <div className="flex gap-2 p-3 bg-gray-900 border-b border-gray-800">
      <button
        onClick={() => step(1)}
        disabled={running}
        className="px-3 py-1.5 bg-blue-700 hover:bg-blue-600 disabled:bg-gray-700 disabled:text-gray-500 rounded text-sm"
      >
        Step 1
      </button>
      <button
        onClick={() => step(10)}
        disabled={running}
        className="px-3 py-1.5 bg-blue-700 hover:bg-blue-600 disabled:bg-gray-700 disabled:text-gray-500 rounded text-sm"
      >
        Step 10
      </button>
      <button
        onClick={() => step(100)}
        disabled={running}
        className="px-3 py-1.5 bg-blue-700 hover:bg-blue-600 disabled:bg-gray-700 disabled:text-gray-500 rounded text-sm"
      >
        Step 100
      </button>
      <button
        onClick={() => startRun(100)}
        disabled={running}
        className="px-3 py-1.5 bg-green-700 hover:bg-green-600 disabled:bg-gray-700 disabled:text-gray-500 rounded text-sm"
      >
        Run 100
      </button>
      {running && (
        <button
          onClick={() => pause()}
          className="px-3 py-1.5 bg-yellow-700 hover:bg-yellow-600 rounded text-sm"
        >
          Pause
        </button>
      )}
    </div>
  )
}
