import { useSimStore } from '../../stores/simStore'

export default function AgentInspector() {
  const { agentDetail } = useSimStore()

  if (!agentDetail) return null
  if (agentDetail.error) return <div className="p-4 text-red-400 text-sm">{agentDetail.error}</div>

  const arch = agentDetail.archetype || {}

  return (
    <div className="p-3 space-y-4 text-sm overflow-y-auto">
      <div>
        <h3 className="text-lg font-bold text-white">{agentDetail.name}</h3>
        <p className="text-gray-400">
          {arch.age && `Age ${arch.age}`} | Location: {agentDetail.location?.kami_id || 'unknown'}
        </p>
      </div>

      {arch.background && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Background</h4>
          <p className="text-gray-400">{arch.background}</p>
        </div>
      )}

      {arch.traits && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Traits</h4>
          <div className="flex flex-wrap gap-1">
            {arch.traits.map((t: string) => (
              <span key={t} className="px-2 py-0.5 bg-gray-800 rounded text-xs text-gray-300">
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {arch.goals && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Goals</h4>
          {Object.entries(arch.goals).map(([level, goal]) => (
            <div key={level} className="px-2 py-0.5">
              <span className="text-yellow-500 text-xs">{level}:</span>{' '}
              <span className="text-gray-400">{goal as string}</span>
            </div>
          ))}
        </div>
      )}

      {agentDetail.states && Object.keys(agentDetail.states).length > 0 && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Physical State</h4>
          {Object.entries(agentDetail.states).map(([k, v]) => (
            <div key={k} className="px-2 py-0.5 text-gray-400">
              {k}: <span className="text-white">{String(v)}</span>
            </div>
          ))}
        </div>
      )}

      {agentDetail.beliefs && agentDetail.beliefs.length > 0 && (
        <div className="mt-4">
          <h4 className="font-semibold text-gray-300 mb-1">Context & Beliefs</h4>
          {agentDetail.beliefs.map((b: any, i: number) => (
            <div key={i} className="px-2 py-0.5 text-gray-400">
              <span className="text-indigo-400">{b.kind}:</span> <span className="text-white">{b.value}</span> <span className="text-gray-600">({Math.round(b.confidence*100)}%)</span>
            </div>
          ))}
        </div>
      )}

      {agentDetail.recent_thoughts && agentDetail.recent_thoughts.length > 0 && (
        <div className="mt-4">
          <h4 className="font-semibold text-gray-300 mb-1">Recent Thoughts</h4>
          {agentDetail.recent_thoughts.map((t: any, i: number) => (
            <div key={i} className="px-2 py-1 bg-purple-900/30 rounded mb-1 text-gray-300 border border-purple-800/50 italic break-words">
              <span className="text-purple-500 text-xs block mb-1">Tick {t.tick}</span>
              {t.thought}
            </div>
          ))}
        </div>
      )}

      {agentDetail.action_history && agentDetail.action_history.length > 0 && (
        <div className="mt-4">
          <h4 className="font-semibold text-gray-300 mb-1">Action History</h4>
          {agentDetail.action_history.map((h: any, i: number) => (
            <div key={i} className="px-2 py-1 border-l-2 border-gray-700 mb-1 text-gray-400 break-words">
              <span className="text-yellow-600 text-xs font-bold">Tick {h.tick} [{h.event_type}]</span><br/>
              {h.narrative}
            </div>
          ))}
        </div>
      )}

      {agentDetail.relations && agentDetail.relations.length > 0 && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Relations</h4>
          {agentDetail.relations.map((r: any, i: number) => (
            <div key={i} className="px-2 py-0.5 text-gray-400">
              {r.type} {r.from === agentDetail.entity_id ? r.to : r.from}
              {r.weight && Object.keys(r.weight).length > 0 && (
                <span className="text-gray-600 ml-1">
                  ({Object.entries(r.weight).map(([k, v]) => `${k}=${v}`).join(', ')})
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {arch.emotion && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Emotional State</h4>
          {Object.entries(arch.emotion).map(([k, v]) => (
            <div key={k} className="px-2 py-0.5 text-gray-400">
              {k}: <span className="text-white">{String(v)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
