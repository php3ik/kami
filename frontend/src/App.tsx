import { useEffect, useRef } from 'react'
import { useSimStore } from './stores/simStore'
import MoodStrip from './components/MoodStrip'
import TimeControls from './components/TimeControls'
import Sidebar from './components/Sidebar'
import KamiGraph from './components/KamiGraph'
import KamiInspector from './components/Inspector/KamiInspector'
import AgentInspector from './components/Inspector/AgentInspector'
import CreateSimModal from './components/CreateSimModal'

export default function App() {
  const { loadGraph, refreshStatus, selectedAgent, selectedKami, tickLog, isCreateModalOpen, loadAgents } = useSimStore()
  const logEndRef = useRef<HTMLDivElement>(null)

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
        <div className="w-56 border-r border-gray-800 overflow-y-auto">
          <Sidebar />
        </div>

        {/* Center: graph + event log */}
        <div className="flex-1 flex flex-col">
          <div className="flex-1 min-h-0">
            <KamiGraph />
          </div>

          {/* Event log at bottom of center */}
          {recentTicks.length > 0 && (
            <div className="h-48 border-t border-gray-800 overflow-y-auto p-3 text-xs font-mono">
              <h3 className="text-gray-500 mb-2">Event Log</h3>
              {recentTicks.map((tick: any, i: number) => (
                <div key={i} className="mb-2">
                  <span className="text-yellow-600">tick {tick.tick}</span>
                  {tick.error && <div className="text-red-500 ml-4 font-bold max-w-lg break-words whitespace-pre-wrap">Error: {tick.error}</div>}
                  {tick.events?.map((evt: any, j: number) => (
                    <div key={j} className="ml-4 text-gray-400">
                      [{evt.event_type}] {evt.narrative}
                    </div>
                  ))}
                  {Object.entries(tick.monologues || {}).map(([agentId, thought]) => (
                    <div key={agentId} className="ml-4 text-purple-400 italic">
                      {agentId}: {thought as string}
                    </div>
                  ))}
                </div>
              ))}
              <div ref={logEndRef} />
            </div>
          )}
        </div>

        {/* Right: inspector */}
        <div className="w-80 border-l border-gray-800 overflow-y-auto">
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
