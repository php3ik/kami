import { useMemo } from 'react'
import { AlertTriangle, Bot, MapPin, MessageCircle, MoveRight, Sparkles } from 'lucide-react'
import { useSimStore } from '../stores/simStore'
import {
  agentInitials,
  classifyThought,
  digest,
  getKamiName,
  latestThought,
  makeKamiLookup,
} from '../utils/simView'

const statusMeta: Record<string, { label: string; className: string; icon: any }> = {
  error: { label: 'LLM issue', className: 'bg-rose-950/70 text-rose-200 border-rose-700/70', icon: AlertTriangle },
  talk: { label: 'Talking', className: 'bg-cyan-950/70 text-cyan-200 border-cyan-700/70', icon: MessageCircle },
  move: { label: 'Moving', className: 'bg-blue-950/70 text-blue-200 border-blue-700/70', icon: MoveRight },
  observe: { label: 'Observing', className: 'bg-amber-950/70 text-amber-200 border-amber-700/70', icon: Sparkles },
  think: { label: 'Thinking', className: 'bg-purple-950/70 text-purple-200 border-purple-700/70', icon: Bot },
}

export default function AgentActivityBoard() {
  const { agents, liveActivity, tickLog, selectedAgent, selectAgent, graph } = useSimStore()
  const kamiLookup = useMemo(() => makeKamiLookup(graph.nodes || []), [graph.nodes])

  if (!agents || agents.length === 0) return null

  const sortedAgents = [...agents].sort((a, b) => {
    if (a.entity_id === selectedAgent) return -1
    if (b.entity_id === selectedAgent) return 1
    const aLive = liveActivity.some((act) => act.agent_id === a.entity_id)
    const bLive = liveActivity.some((act) => act.agent_id === b.entity_id)
    return Number(bLive) - Number(aLive) || (a.name || '').localeCompare(b.name || '')
  })

  return (
    <div className="h-80 border-t border-slate-800 bg-slate-950 flex flex-col">
      <div className="flex items-center justify-between px-4 py-2 border-b border-slate-800 bg-slate-900/80">
        <div>
          <h2 className="text-sm font-semibold text-slate-100">Agent Activity Deck</h2>
          <p className="text-xs text-slate-500">Live cognition, location and last known intent signal</p>
        </div>
        <div className="text-xs text-slate-500">{agents.length} agents</div>
      </div>

      <div className="flex-1 p-3 flex gap-3 overflow-x-auto">
        {sortedAgents.map((agent: any) => {
          const id = agent.entity_id
          const agentLiveActs = liveActivity.filter((act) => act.agent_id === id)
          const isThinking = agentLiveActs.some((act) => act.step === 'agent_think_start') &&
            !agentLiveActs.some((act) => act.step === 'agent_think_end')
          const latestLiveEnd = [...agentLiveActs].reverse().find((act) => act.step === 'agent_think_end')
          const historical = latestThought(id, tickLog)
          const eventFallback = agent.latest_event?.narrative
          const thought = latestLiveEnd?.inner_monologue?.trim() || historical?.thought || eventFallback || ''
          const status = isThinking ? 'think' : classifyThought(thought || agent.latest_event?.event_type)
          const meta = statusMeta[status] || statusMeta.think
          const StatusIcon = meta.icon
          const isSelected = selectedAgent === id
          const locationName = agent.location_name || getKamiName(agent.kami_id, kamiLookup)

          return (
            <button
              key={id}
              onClick={() => selectAgent(id)}
              className={`flex-shrink-0 w-80 lg:w-96 flex flex-col text-left border rounded overflow-hidden transition-all ${
                isSelected
                  ? 'border-purple-500 bg-slate-900 shadow-lg shadow-purple-950/40'
                  : 'border-slate-800 bg-slate-900/75 hover:border-slate-600'
              }`}
            >
              <div className={`p-3 border-b ${isSelected ? 'border-purple-800/70 bg-purple-950/40' : 'border-slate-800 bg-slate-900'}`}>
                <div className="flex items-start gap-3">
                  <div className={`h-10 w-10 rounded grid place-items-center font-bold border ${
                    isSelected ? 'bg-purple-700 border-purple-400 text-white' : 'bg-slate-800 border-slate-700 text-purple-200'
                  }`}>
                    {agentInitials(agent.name)}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="font-semibold text-slate-100 truncate">{agent.name}</h3>
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[11px] ${meta.className}`}>
                        <StatusIcon size={11} />
                        {isThinking ? 'Live' : meta.label}
                      </span>
                    </div>
                    <p className="text-xs text-slate-500 truncate">{agent.role || 'Agent'}</p>
                  </div>
                </div>

                <div className="mt-3 flex items-center gap-2 text-xs text-blue-200 bg-slate-950/70 border border-slate-800 rounded px-2 py-1">
                  <MapPin size={12} className="text-blue-300 flex-shrink-0" />
                  <span className="truncate">{locationName}</span>
                </div>
              </div>

              <div className="flex-1 p-3">
                {isThinking ? (
                  <div className="flex items-center gap-2 text-amber-300 animate-pulse text-sm">
                    <span className="h-2 w-2 rounded-full bg-amber-300" />
                    Pondering next action...
                  </div>
                ) : (
                  <p className={`text-sm leading-relaxed ${status === 'error' ? 'text-rose-200' : 'text-slate-200'}`}>
                    {digest(thought, 210)}
                  </p>
                )}
              </div>

              <div className="px-3 py-2 border-t border-slate-800 flex items-center justify-between text-[11px] text-slate-500">
                <span>
                  {historical?.tick !== undefined
                    ? `last thought tick ${historical.tick}`
                    : agent.latest_event?.tick !== undefined
                      ? `last event tick ${agent.latest_event.tick}`
                      : 'no history yet'}
                </span>
                <span className="text-slate-600">{agent.kami_id || 'unknown'}</span>
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
