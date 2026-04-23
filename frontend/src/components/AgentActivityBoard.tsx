import { useSimStore } from '../stores/simStore'
import { useEffect, useRef } from 'react'

export default function AgentActivityBoard() {
  const { agents, liveActivity, tickLog, selectedAgent, selectAgent } = useSimStore()
  
  // Create a ref for each agent panel to auto-scroll if needed
  const scrollRefs = useRef<Record<string, HTMLDivElement | null>>({})

  if (!agents || agents.length === 0) return null

  return (
    <div className="h-80 border-t border-gray-800 bg-gray-950 p-4 flex gap-4 overflow-x-auto shadow-inner">
      {agents.map(agent => {
        const id = agent.entity_id
        
        // Find latest live activities for this agent
        const agentLiveActs = liveActivity.filter(a => a.agent_id === id)
        const isThinking = agentLiveActs.some(a => a.step === 'agent_think_start') && 
                          !agentLiveActs.some(a => a.step === 'agent_think_end')
        
        // Find last known thought
        const latestLiveEnd = [...agentLiveActs].reverse().find(a => a.step === 'agent_think_end')
        const latestHistorical = [...tickLog].reverse().find(t => t.monologues && t.monologues[id])
        
        const currentThought = latestLiveEnd 
          ? latestLiveEnd.inner_monologue 
          : latestHistorical?.monologues[id] || "Waiting for thoughts..."

        const isSelected = selectedAgent === id

        return (
          <div 
            key={id} 
            onClick={() => selectAgent(id)}
            className={`flex-shrink-0 w-80 lg:w-96 flex flex-col border rounded-lg bg-gray-900 overflow-hidden cursor-pointer transition-colors ${
              isSelected ? 'border-purple-500 shadow-lg shadow-purple-900/20' : 'border-gray-800 hover:border-gray-600'
            }`}
          >
            {/* Header */}
            <div className={`p-3 flex flex-col gap-1 border-b ${isSelected ? 'bg-purple-900/30 border-purple-800/50' : 'bg-gray-800/50 border-gray-800'}`}>
              <div className="flex justify-between items-center">
                <h3 className="font-bold text-lg text-gray-100">{agent.name}</h3>
                {isThinking && (
                  <span className="relative flex h-3 w-3">
                    <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-yellow-400 opacity-75"></span>
                    <span className="relative inline-flex rounded-full h-3 w-3 bg-yellow-500"></span>
                  </span>
                )}
              </div>
              <div className="flex justify-between items-center text-sm">
                <span className="text-gray-400 capitalize">{agent.role}</span>
                <div className="flex items-center gap-1 bg-gray-950 px-2 py-0.5 rounded text-xs border border-gray-700">
                  <span className="text-gray-500">📍</span>
                  <span className="text-blue-300 truncate max-w-[120px]">{agent.kami_id || 'Unknown'}</span>
                </div>
              </div>
            </div>

            {/* Body: Thought Log */}
            <div className="flex-1 p-4 overflow-y-auto font-mono text-sm leading-relaxed text-gray-300">
              {isThinking ? (
                <div className="flex items-center gap-2 text-yellow-500/80 animate-pulse">
                  <span>▶ Pondering next action...</span>
                </div>
              ) : (
                <div className="italic text-purple-200">
                  "{currentThought}"
                </div>
              )}
              
              {/* Reference div for scrolling could go here if keeping history */}
            </div>
          </div>
        )
      })}
    </div>
  )
}
