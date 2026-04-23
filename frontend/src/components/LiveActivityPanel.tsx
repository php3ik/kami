import { useSimStore } from '../stores/simStore'
import { useEffect, useRef } from 'react'

export default function LiveActivityPanel() {
  const { liveActivity } = useSimStore()
  const logEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    logEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [liveActivity])

  if (liveActivity.length === 0) return null

  return (
    <div className="border-t border-gray-800 p-3 h-48 overflow-y-auto bg-gray-900 font-mono text-xs shadow-inner">
      <h3 className="text-gray-500 font-bold mb-2 uppercase tracking-widest text-[10px] flex items-center gap-2">
        <span className="relative flex h-2 w-2">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
          <span className="relative inline-flex rounded-full h-2 w-2 bg-green-500"></span>
        </span>
        Live Sub-Tick Activity
      </h3>
      {liveActivity.map((act, i) => (
        <div key={i} className="mb-2">
          {act.step === 'agent_think_start' && <span className="text-yellow-500/80 animate-pulse">▶ Agent {act.agent_id} is pondering...</span>}
          {act.step === 'agent_think_end' && (
            <div className="border-l-2 border-purple-500/30 pl-2">
              <span className="text-purple-400">Agent {act.agent_id}: </span>
              <span className="italic text-gray-300">"{act.inner_monologue}"</span>
            </div>
          )}
          {act.step === 'kami_render_start' && <span className="text-blue-400/80 animate-pulse">▶ Kami {act.kami_id} is resolving intents...</span>}
          {act.step === 'kami_render_end' && (
            <div className="border-l-2 border-blue-500/30 pl-2">
              <span className="text-blue-400">Kami {act.kami_id} rendered: </span>
              <span className="text-gray-300">{act.narrative}</span>
            </div>
          )}
        </div>
      ))}
      <div ref={logEndRef} />
    </div>
  )
}
