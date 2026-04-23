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

export default function App() {
  const { loadGraph, refreshStatus, selectedAgent, selectedKami, tickLog, isCreateModalOpen, loadAgents } = useSimStore()
  const logEndRef = useRef<HTMLDivElement>(null)

  const [leftWidth, setLeftWidth] = useState(280)
  const [rightWidth, setRightWidth] = useState(380)

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
    loadGraph()
    loadAgents()
    refreshStatus()
  }, [])

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [tickLog])

  // Show recent tick narratives
  const recentTicks = tickLog.slice(-20)

  return (
    <div className="h-screen flex flex-col bg-gray-950 text-gray-100">
      {/* Top: mood strip */}
      <MoodStrip />

      {/* Time controls */}
      <TimeControls />

      {/* Main three-column layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: sidebar */}
        <div style={{ width: leftWidth }} className="flex-shrink-0 border-r border-gray-800 overflow-y-auto">
          <Sidebar />
        </div>
        
        {/* Left Resizer */}
        <div 
          className="w-1 cursor-col-resize hover:bg-purple-500 bg-gray-800/50 transition-colors z-10" 
          onMouseDown={handleLeftDrag}
        />

        {/* Center: graph + event log */}
        <div className="flex-1 flex flex-col min-w-0">
          <div className="flex-1 min-h-0">
            <KamiGraph />
          </div>

          {/* Agent Activity Board at bottom of center */}
          <AgentActivityBoard />
        </div>

        {/* Right Resizer */}
        <div 
          className="w-1 cursor-col-resize hover:bg-purple-500 bg-gray-800/50 transition-colors z-10" 
          onMouseDown={handleRightDrag}
        />

        {/* Right: inspector */}
        <div style={{ width: rightWidth }} className="flex-shrink-0 overflow-y-auto bg-gray-950">
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
    </div>
  )
}
