import { useState } from 'react'
import { useSimStore } from '../stores/simStore'
import { Loader2, RotateCcw, Square } from 'lucide-react'

export default function CreateSimModal() {
  const {
    openCreateModal, createSim, worldBuildJob, cancelWorldBuild, resumeWorldBuild,
  } = useSimStore()
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [count, setCount] = useState(10)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await createSim(prompt, count, name.trim() || undefined)
      openCreateModal(false)
    } catch (e) {
      console.error(e)
      setError(e instanceof Error ? e.message : 'Failed to build world.')
    } finally {
      setLoading(false)
    }
  }

  const handleResume = async () => {
    setLoading(true)
    setError(null)
    try {
      await resumeWorldBuild()
      openCreateModal(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to resume world build.')
    } finally {
      setLoading(false)
    }
  }

  const progress = worldBuildJob
    ? Math.round((worldBuildJob.completed_units / Math.max(1, worldBuildJob.total_units)) * 100)
    : 0

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-3">
      <div className="bg-gray-800 p-6 rounded-lg shadow-xl w-full max-w-lg max-h-[calc(100vh-1.5rem)] overflow-y-auto border border-gray-700">
        <h2 className="text-xl font-bold text-gray-100 mb-4">Create New Simulation</h2>
        
        {loading ? (
          <div className="space-y-4">
            <div className="flex items-center space-x-3 text-indigo-400">
              <Loader2 size={20} className="animate-spin shrink-0" />
              <span>{worldBuildJob?.message || 'Preparing world build...'}</span>
            </div>
            <div className="h-2 overflow-hidden rounded bg-gray-900">
              <div className="h-full bg-indigo-500 transition-all" style={{ width: `${progress}%` }} />
            </div>
            <div className="flex items-center justify-between text-xs text-gray-400">
              <span className="capitalize">{String(worldBuildJob?.stage || 'queued').replace('_', ' ')}</span>
              <span className="tabular-nums">{progress}%</span>
            </div>
            {worldBuildJob?.job_id && (
              <button
                type="button"
                onClick={() => cancelWorldBuild().catch(e => setError(String(e)))}
                className="inline-flex items-center gap-2 text-sm text-rose-300 hover:text-rose-200"
              >
                <Square size={14} /> Cancel build
              </button>
            )}
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <div className="rounded border border-rose-700 bg-rose-950/40 px-3 py-2 text-sm text-rose-200">
                {error}
              </div>
            )}
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">World Name</label>
              <input
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-gray-100 focus:outline-none focus:border-indigo-500"
                placeholder="ISS tension study"
                value={name}
                onChange={e => setName(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">World Prompt</label>
              <textarea 
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-gray-100 focus:outline-none focus:border-indigo-500 h-24"
                placeholder="A small fishing village in Norway..."
                value={prompt}
                onChange={e => setPrompt(e.target.value)}
                required
              />
            </div>
            
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">Population Count</label>
              <input 
                type="number"
                min="2"
                max="100"
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-gray-100 focus:outline-none focus:border-indigo-500"
                value={count}
                onChange={e => setCount(parseInt(e.target.value))}
                required
              />
            </div>
            {worldBuildJob && ['failed', 'cancelled'].includes(worldBuildJob.status) && (
              <button
                type="button"
                onClick={handleResume}
                className="inline-flex items-center gap-2 text-sm text-indigo-300 hover:text-indigo-200"
              >
                <RotateCcw size={15} /> Resume from checkpoint
              </button>
            )}

            <div className="flex justify-end space-x-3 pt-4 border-t border-gray-700">
              <button 
                type="button"
                onClick={() => openCreateModal(false)}
                className="px-4 py-2 text-sm text-gray-400 hover:text-gray-100"
              >
                Cancel
              </button>
              <button 
                type="submit"
                className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded text-sm font-bold"
              >
                Create World
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
