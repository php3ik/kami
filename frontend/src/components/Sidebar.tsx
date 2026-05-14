import { useMemo, useState } from 'react'
import { Bot, ChevronDown, MapPin, Plus, Radio, Search, Users } from 'lucide-react'
import { useSimStore } from '../stores/simStore'
import { activeKamiSet, agentsByKami, kindLabel, makeKamiLookup, recentEventsByKami } from '../utils/simView'
import WorldSwitcher from './WorldSwitcher'

export default function Sidebar() {
  const { graph, selectKami, selectedKami, agents, selectAgent, selectedAgent, tickLog } = useSimStore()
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})

  const kamiNodes = graph.nodes || []
  const kamiLookup = useMemo(() => makeKamiLookup(kamiNodes), [kamiNodes])
  const agentGroups = useMemo(() => agentsByKami(agents), [agents])
  const eventCounts = useMemo(() => recentEventsByKami(tickLog, 8), [tickLog])
  const activeKamis = useMemo(() => activeKamiSet(tickLog), [tickLog])
  const normalizedQuery = query.trim().toLowerCase()

  const grouped = useMemo(() => {
    const groups: Record<string, any[]> = {}
    for (const node of kamiNodes) {
      const haystack = `${node.name || ''} ${node.id} ${node.kind || ''}`.toLowerCase()
      if (normalizedQuery && !haystack.includes(normalizedQuery)) continue
      const kind = node.kind || 'other'
      if (!groups[kind]) groups[kind] = []
      groups[kind].push(node)
    }
    return Object.fromEntries(
      Object.entries(groups).sort(([a], [b]) => kindLabel(a).localeCompare(kindLabel(b)))
    )
  }, [kamiNodes, normalizedQuery])

  const visibleAgents = useMemo(() => {
    return (agents || []).filter((agent: any) => {
      if (!normalizedQuery) return true
      const locationName = kamiLookup.get(agent.kami_id || '')?.name || ''
      return `${agent.name} ${agent.role || ''} ${agent.kami_id || ''} ${locationName}`.toLowerCase().includes(normalizedQuery)
    })
  }, [agents, kamiLookup, normalizedQuery])

  const hotLocations = useMemo(() => {
    return [...kamiNodes]
      .map((node: any) => ({
        node,
        agents: agentGroups.get(node.id)?.length || 0,
        events: eventCounts.get(node.id) || 0,
        active: activeKamis.has(node.id),
      }))
      .filter((item) => item.agents || item.events || item.active)
      .sort((a, b) => Number(b.active) - Number(a.active) || b.events - a.events || b.agents - a.agents)
      .slice(0, 4)
  }, [kamiNodes, agentGroups, eventCounts, activeKamis])

  return (
    <div className="h-full flex flex-col bg-slate-950 text-sm">
      <div className="p-3 border-b border-slate-800 space-y-3">
        <WorldSwitcher />
        <button
          onClick={() => useSimStore.getState().openCreateModal(true)}
          className="w-full inline-flex items-center justify-center gap-2 bg-indigo-600 hover:bg-indigo-500 text-white px-3 py-2 rounded font-semibold transition-colors"
        >
          <Plus size={15} />
          Create Simulation
        </button>

        <label className="relative block">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search places or agents"
            className="w-full bg-slate-900 border border-slate-800 rounded pl-9 pr-3 py-2 text-slate-200 placeholder:text-slate-600 outline-none focus:border-blue-500"
          />
        </label>
      </div>

      <div className="p-3 border-b border-slate-800">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Activity Focus</h2>
          <Radio size={13} className="text-emerald-400" />
        </div>
        <div className="space-y-1">
          {hotLocations.length === 0 ? (
            <div className="text-xs text-slate-600">No active locations yet.</div>
          ) : hotLocations.map(({ node, agents: agentCount, events, active }) => (
            <button
              key={node.id}
              onClick={() => selectKami(node.id)}
              className={`w-full text-left px-2 py-2 rounded border transition-colors ${
                selectedKami === node.id
                  ? 'bg-blue-950/70 border-blue-600 text-white'
                  : 'bg-slate-900/60 border-slate-800 text-slate-300 hover:border-slate-600'
              }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">{node.name || node.id}</span>
                {active && <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_10px_rgba(52,211,153,0.9)]" />}
              </div>
              <div className="mt-1 flex gap-2 text-[11px] text-slate-500">
                <span>{agentCount} agents</span>
                <span>{events} events</span>
              </div>
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-5">
        <section>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Kami Locations</h2>
            <span className="text-[11px] text-slate-600">{kamiNodes.length}</span>
          </div>

          {Object.entries(grouped).map(([kind, nodes]) => {
            const isCollapsed = collapsed[kind]
            return (
              <div key={kind} className="mb-3">
                <button
                  onClick={() => setCollapsed((state) => ({ ...state, [kind]: !state[kind] }))}
                  className="w-full flex items-center justify-between text-[11px] uppercase tracking-wider text-slate-500 mb-1"
                >
                  <span>{kindLabel(kind)}</span>
                  <span className="flex items-center gap-1">
                    {nodes.length}
                    <ChevronDown size={12} className={`transition-transform ${isCollapsed ? '-rotate-90' : ''}`} />
                  </span>
                </button>
                {!isCollapsed && nodes.map((node: any) => {
                  const agentCount = agentGroups.get(node.id)?.length || 0
                  const events = eventCounts.get(node.id) || 0
                  const active = activeKamis.has(node.id)
                  return (
                    <button
                      key={node.id}
                      className={`w-full group px-2 py-1.5 rounded text-left transition-colors ${
                        selectedKami === node.id
                          ? 'bg-blue-950/80 text-white border border-blue-700'
                          : 'text-slate-400 hover:bg-slate-900 hover:text-slate-100 border border-transparent'
                      }`}
                      onClick={() => selectKami(node.id)}
                    >
                      <div className="flex items-center gap-2">
                        <MapPin size={13} className={active ? 'text-emerald-400' : 'text-slate-600 group-hover:text-slate-400'} />
                        <span className="truncate">{node.name || node.id}</span>
                      </div>
                      <div className="ml-5 mt-1 flex items-center gap-2 text-[11px] text-slate-600">
                        {agentCount > 0 && <span className="text-blue-300">{agentCount} agents</span>}
                        {events > 0 && <span className="text-amber-300">{events} events</span>}
                        {!agentCount && !events && <span>idle</span>}
                      </div>
                    </button>
                  )
                })}
              </div>
            )
          })}
        </section>

        <section>
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-[11px] font-bold text-slate-500 uppercase tracking-wider">Agents</h2>
            <span className="text-[11px] text-slate-600">{visibleAgents.length}</span>
          </div>
          <div className="space-y-1">
            {visibleAgents.map((agent: any) => (
              <button
                key={agent.entity_id}
                className={`w-full px-2 py-2 rounded text-left border transition-colors ${
                  selectedAgent === agent.entity_id
                    ? 'bg-purple-950/80 text-white border-purple-600'
                    : 'bg-slate-900/50 text-slate-400 border-slate-800 hover:border-slate-600 hover:text-slate-100'
                }`}
                onClick={() => selectAgent(agent.entity_id)}
              >
                <div className="flex items-center gap-2">
                  <Bot size={14} className="text-purple-300" />
                  <span className="truncate font-medium">{agent.name}</span>
                </div>
                <div className="mt-1 flex items-center gap-2 text-[11px] text-slate-500">
                  <Users size={11} />
                  <span className="truncate">{agent.role || 'Agent'}</span>
                </div>
                <div className="mt-1 text-[11px] text-blue-300 truncate">
                  {kamiLookup.get(agent.kami_id || '')?.name || agent.kami_id || 'Unknown location'}
                </div>
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
