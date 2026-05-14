export type GraphNode = {
  id: string
  name?: string
  kind?: string
  [key: string]: any
}

export type Agent = {
  entity_id: string
  name: string
  role?: string
  kami_id?: string | null
  location_name?: string | null
  latest_event?: any
}

export type TickResult = {
  tick?: number
  events?: any[]
  monologues?: Record<string, string>
  narratives?: Record<string, string>
  active_kami?: string[]
  active_kami_count?: number
  active_agent_count?: number
  error?: string
}

export function makeKamiLookup(nodes: GraphNode[] = []) {
  return new Map(nodes.map((node) => [node.id, node]))
}

export function getKamiName(kamiId: string | null | undefined, lookup: Map<string, GraphNode>) {
  if (!kamiId) return 'Unknown location'
  return lookup.get(kamiId)?.name || humanizeId(kamiId)
}

export function humanizeId(id: string) {
  return id
    .replace(/^kami_/, '')
    .replace(/^agent_/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (char) => char.toUpperCase())
}

export function kindLabel(kind: string | undefined) {
  return humanizeId(kind || 'other')
}

export function agentsByKami(agents: Agent[] = []) {
  const counts = new Map<string, Agent[]>()
  for (const agent of agents) {
    if (!agent.kami_id) continue
    const list = counts.get(agent.kami_id) || []
    list.push(agent)
    counts.set(agent.kami_id, list)
  }
  return counts
}

export function recentEventsByKami(tickLog: TickResult[] = [], limit = 8) {
  const counts = new Map<string, number>()
  for (const tick of tickLog.slice(-limit)) {
    for (const event of tick.events || []) {
      if (!event.kami_id) continue
      counts.set(event.kami_id, (counts.get(event.kami_id) || 0) + 1)
    }
  }
  return counts
}

export function activeKamiSet(tickLog: TickResult[] = []) {
  const latest = [...tickLog].reverse().find((tick) => tick.active_kami?.length)
  return new Set(latest?.active_kami || [])
}

export function latestThought(agentId: string, tickLog: TickResult[] = []) {
  for (const tick of [...tickLog].reverse()) {
    const thought = tick.monologues?.[agentId]?.trim()
    if (thought) return { tick: tick.tick, thought }
  }
  return null
}

export function classifyThought(thought: string | undefined) {
  const text = thought || ''
  if (/quota|LLM unavailable|LLM request failed|rate limit/i.test(text)) return 'error'
  if (/pause|quiet|observe|watch|listen/i.test(text)) return 'observe'
  if (/talk|ask|tell|conversation|say/i.test(text)) return 'talk'
  if (/move|go|return|float|walk|head/i.test(text)) return 'move'
  return 'think'
}

export function digest(text: string | undefined, max = 150) {
  const clean = (text || '').replace(/\s+/g, ' ').trim()
  if (!clean) return 'No thought recorded yet.'
  if (clean.length <= max) return clean
  return `${clean.slice(0, max - 1).trim()}...`
}

export function agentInitials(name: string | undefined) {
  return (name || '?')
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join('') || '?'
}

export function buildAgentTimeline(agentDetail: any, tickLog: TickResult[] = []) {
  const rows = new Map<number, any>()

  for (const t of agentDetail?.recent_thoughts || []) {
    const tick = Number(t.tick)
    rows.set(tick, { ...(rows.get(tick) || { tick }), thought: t.thought })
  }

  for (const h of agentDetail?.action_history || []) {
    const tick = Number(h.tick)
    rows.set(tick, {
      ...(rows.get(tick) || { tick }),
      event_type: h.event_type,
      narrative: h.narrative,
    })
  }

  for (const tick of tickLog) {
    const thought = tick.monologues?.[agentDetail?.entity_id]
    if (!thought) continue
    const tickNumber = Number(tick.tick)
    rows.set(tickNumber, { ...(rows.get(tickNumber) || { tick: tickNumber }), thought })
  }

  return [...rows.values()].sort((a, b) => b.tick - a.tick)
}
