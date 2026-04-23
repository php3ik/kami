import { useState } from 'react'
import { useSimStore } from '../stores/simStore'

export default function CreateSimModal() {
  const { openCreateModal, createSim } = useSimStore()
  const [prompt, setPrompt] = useState('')
  const [count, setCount] = useState(10)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    try {
      await createSim(prompt, count)
      openCreateModal(false)
    } catch (e) {
      console.error(e)
      alert("Failed to build world. See console for details.")
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-gray-800 p-6 rounded-lg shadow-xl w-full max-w-lg border border-gray-700">
        <h2 className="text-xl font-bold text-gray-100 mb-4">Create New Simulation</h2>
        
        {loading ? (
          <div className="space-y-4">
            <div className="flex items-center space-x-3 text-indigo-400">
              <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
              <span>Building world... This uses LLMs and may take a few minutes.</span>
            </div>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-gray-400 mb-1">Town Description</label>
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
                max="1000"
                className="w-full bg-gray-900 border border-gray-700 rounded p-2 text-gray-100 focus:outline-none focus:border-indigo-500"
                value={count}
                onChange={e => setCount(parseInt(e.target.value))}
                required
              />
            </div>

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
