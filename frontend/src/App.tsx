import { useEffect, useRef, useState } from 'react'
import { useSimStore } from './stores/simStore'
import MoodStrip from './components/MoodStrip'
import TimeControls from './components/TimeControls'
import Sidebar from './components/Sidebar'
import KamiGraph from './components/KamiGraph'
import KamiInspector from './components/Inspector/KamiInspector'
import AgentInspector from './components/Inspector/AgentInspector'
import CreateSimModal from './components/CreateSimModal'
import AgentActivityBoard from './components/AgentActivityBoard'
import TimelinePreview from './components/TimelinePreview'
import LLMSettingsModal from './components/LLMSettingsModal'
import { GitBranch, Rows3 } from 'lucide-react'
import { wsClient } from './api/client'

export default function App() {
  const { loadGraph, refreshStatus, selectedAgent, selectedKami, tickLog, isCreateModalOpen, loadAgents, viewMode, setViewMode, loadSimulations } = useSimStore()
  const logEndRef = useRef<HTMLDivElement>(null)

  const [leftWidth, setLeftWidth] = useState(280)
  const [rightWidth, setRightWidth] = useState(380)
  const [isNarrow, setIsNarrow] = useState(false)
  const [isSettingsOpen, setIsSettingsOpen] = useState(false)

  const handleLeftDrag = (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = leftWidth

    const onMouseMove = (moveEvent: MouseEvent) => {
      const newWidth = Math.max(200, Math.min(600, startWidth + moveEvent.clientX - startX))
      setLeftWidth(newWidth)
    }
    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }

  const handleRightDrag = (e: React.MouseEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startWidth = rightWidth

    const onMouseMove = (moveEvent: MouseEvent) => {
      const newWidth = Math.max(250, Math.min(800, startWidth - (moveEvent.clientX - startX)))
      setRightWidth(newWidth)
    }
    const onMouseUp = () => {
      document.removeEventListener('mousemove', onMouseMove)
      document.removeEventListener('mouseup', onMouseUp)
    }
    document.addEventListener('mousemove', onMouseMove)
    document.addEventListener('mouseup', onMouseUp)
  }

  useEffect(() => {
    wsClient.connect()
    loadGraph()
    loadAgents()
    loadSimulations()
    refreshStatus()
    return () => wsClient.disconnect()
  }, [])

  useEffect(() => {
    const update = () => setIsNarrow(window.innerWidth < 900)
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [tickLog])

  // Show recent tick narratives
  const recentTicks = tickLog.slice(-20)

  return (
    <div className={`${isNarrow ? 'min-h-screen' : 'h-screen'} flex flex-col bg-slate-950 text-slate-100`}>
      {/* Top: mood strip */}
      <MoodStrip />

      {/* Time controls */}
      <TimeControls onOpenSettings={() => setIsSettingsOpen(true)} />

      {/* Main three-column layout */}
      <div className={`flex-1 ${isNarrow ? 'flex flex-col overflow-y-auto' : 'flex overflow-hidden'}`}>
        {/* Left: sidebar */}
        <div
          style={isNarrow ? undefined : { width: leftWidth }}
          className={`${isNarrow ? 'h-80 border-b' : 'flex-shrink-0 border-r overflow-y-auto'} border-slate-800`}
        >
          <Sidebar />
        </div>
        
        {/* Left Resizer */}
        {!isNarrow && <div 
          className="w-1 cursor-col-resize hover:bg-purple-500 bg-gray-800/50 transition-colors z-10" 
          onMouseDown={handleLeftDrag}
        />}

        {/* Center: graph + event log */}
        <div className={`${isNarrow ? 'min-h-[760px]' : 'flex-1'} flex flex-col min-w-0`}>
          <div className="flex items-center justify-between gap-3 px-4 py-2 border-b border-slate-800 bg-slate-950">
            <div className="text-xs text-slate-500">
              {viewMode === 'graph' ? 'Spatial map of kami and live agent positions' : 'Temporal matrix of ticks, entities and state snapshots'}
            </div>
            <div className="inline-flex rounded border border-slate-800 bg-slate-900 p-1">
              <button
                onClick={() => setViewMode('graph')}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs transition-colors ${
                  viewMode === 'graph' ? 'bg-blue-700 text-white' : 'text-slate-400 hover:text-slate-100'
                }`}
              >
                <GitBranch size={13} />
                Graph
              </button>
              <button
                onClick={() => setViewMode('timeline')}
                className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded text-xs transition-colors ${
                  viewMode === 'timeline' ? 'bg-purple-700 text-white' : 'text-slate-400 hover:text-slate-100'
                }`}
              >
                <Rows3 size={13} />
                Timeline
              </button>
            </div>
          </div>
          <div className={`${isNarrow ? 'h-[420px]' : 'flex-1 min-h-0'}`}>
            {viewMode === 'graph' ? <KamiGraph /> : <TimelinePreview />}
          </div>

          {/* Agent Activity Board at bottom of center */}
          {viewMode === 'graph' && <AgentActivityBoard />}
        </div>

        {/* Right Resizer */}
        {!isNarrow && <div 
          className="w-1 cursor-col-resize hover:bg-purple-500 bg-gray-800/50 transition-colors z-10" 
          onMouseDown={handleRightDrag}
        />}

        {/* Right: inspector */}
        <div
          style={isNarrow ? undefined : { width: rightWidth }}
          className={`${isNarrow ? 'h-[520px] border-t' : 'flex-shrink-0 overflow-y-auto'} border-slate-800 bg-slate-950`}
        >
          {selectedAgent ? (
            <AgentInspector />
          ) : selectedKami ? (
            <KamiInspector />
          ) : (
            <div className="p-4 text-gray-500 text-sm">
              Click a kami node or select from the sidebar to inspect.
            </div>
          )}
        </div>
      </div>
      {isCreateModalOpen && <CreateSimModal />}
      <LLMSettingsModal open={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
    </div>
  )
}
