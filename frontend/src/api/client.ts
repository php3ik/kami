const API_BASE = '/api'

export async function fetchStatus() {
  const res = await fetch(`${API_BASE}/status`)
  return res.json()
}

export async function fetchGraph() {
  const res = await fetch(`${API_BASE}/graph`)
  return res.json()
}

export async function fetchKami(kamiId: string) {
  const res = await fetch(`${API_BASE}/kami/${kamiId}`)
  return res.json()
}

export async function fetchAgent(agentId: string) {
  const res = await fetch(`${API_BASE}/agent/${agentId}`)
  return res.json()
}

export async function fetchAgents() {
  const res = await fetch(`${API_BASE}/agents`)
  return res.json()
}

export async function fetchEvents(params: {
  since_tick?: number
  until_tick?: number
  kami_id?: string
  limit?: number
}) {
  const query = new URLSearchParams()
  if (params.since_tick !== undefined) query.set('since_tick', String(params.since_tick))
  if (params.until_tick !== undefined) query.set('until_tick', String(params.until_tick))
  if (params.kami_id) query.set('kami_id', params.kami_id)
  if (params.limit) query.set('limit', String(params.limit))
  const res = await fetch(`${API_BASE}/events?${query}`)
  return res.json()
}

export async function stepTick(ticks = 1) {
  const res = await fetch(`${API_BASE}/sim/step?ticks=${ticks}`, { method: 'POST' })
  return res.json()
}

export async function startRun(ticks = 100) {
  const res = await fetch(`${API_BASE}/sim/run?ticks=${ticks}`, { method: 'POST' })
  return res.json()
}

export async function createSim(prompt: string, count: number) {
  const res = await fetch(`${API_BASE}/sim/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, agent_count: count })
  })
  return res.json()
}

export async function pauseSim() {
  const res = await fetch(`${API_BASE}/sim/pause`, { method: 'POST' })
  return res.json()
}

export class SimWebSocket {
  private ws: WebSocket | null = null
  private listeners: ((data: any) => void)[] = []

  connect() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    this.ws = new WebSocket(`${protocol}//${window.location.host}/ws`)
    this.ws.onmessage = (event) => {
      const data = JSON.parse(event.data)
      this.listeners.forEach((fn) => fn(data))
    }
    this.ws.onclose = () => {
      setTimeout(() => this.connect(), 2000)
    }
  }

  onMessage(fn: (data: any) => void) {
    this.listeners.push(fn)
    return () => {
      this.listeners = this.listeners.filter((l) => l !== fn)
    }
  }

  send(msg: any) {
    this.ws?.send(JSON.stringify(msg))
  }

  disconnect() {
    this.ws?.close()
  }
}

export const wsClient = new SimWebSocket()
