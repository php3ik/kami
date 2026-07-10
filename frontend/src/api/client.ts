const API_BASE = '/api'
const TOKEN_KEY = 'kami_api_token'

export function getApiToken() {
  return sessionStorage.getItem(TOKEN_KEY) || ''
}

export function setApiToken(token: string) {
  const value = token.trim()
  if (value) sessionStorage.setItem(TOKEN_KEY, value)
  else sessionStorage.removeItem(TOKEN_KEY)
}

async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}) {
  const headers = new Headers(init.headers)
  const token = getApiToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const response = await fetch(input, { ...init, headers })
  if (response.status === 401) {
    window.dispatchEvent(new CustomEvent('kami-auth-required'))
  }
  return response
}

export async function fetchAuthStatus() {
  const res = await apiFetch(`${API_BASE}/auth/status`)
  return parseResponse(res) as Promise<{ required: boolean; authenticated: boolean }>
}

export async function fetchStatus() {
  const res = await apiFetch(`${API_BASE}/status`)
  return parseResponse(res)
}

export async function fetchGraph() {
  const res = await apiFetch(`${API_BASE}/graph`)
  return parseResponse(res)
}

export async function fetchKami(kamiId: string) {
  const res = await apiFetch(`${API_BASE}/kami/${kamiId}`)
  return parseResponse(res)
}

export async function fetchAgent(agentId: string) {
  const res = await apiFetch(`${API_BASE}/agent/${agentId}`)
  return parseResponse(res)
}

export async function fetchEntity(entityId: string) {
  const res = await apiFetch(`${API_BASE}/entity/${encodeURIComponent(entityId)}`)
  return parseResponse(res)
}

export async function fetchAgents() {
  const res = await apiFetch(`${API_BASE}/agents`)
  return parseResponse(res)
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
  const res = await apiFetch(`${API_BASE}/events?${query}`)
  return parseResponse(res)
}

async function parseResponse(res: Response) {
  const text = await res.text()
  let data: any = {}
  try {
    data = text ? JSON.parse(text) : {}
  } catch {
    data = { error: text || res.statusText }
  }
  if (!res.ok || data?.error) {
    const detail =
      typeof data?.detail === 'string'
        ? data.detail
        : data?.detail
          ? JSON.stringify(data.detail)
          : ''
    throw new Error(data?.error || detail || res.statusText)
  }
  return data
}

export async function fetchSimulations() {
  const res = await apiFetch(`${API_BASE}/simulations`)
  return parseResponse(res)
}

export async function fetchLLMSettings() {
  const res = await apiFetch(`${API_BASE}/settings/llm`)
  return parseResponse(res)
}

export async function updateLLMSettings(payload: {
  provider?: string
  cheap_model?: string
  strong_model?: string
  image_provider?: string
  cheap_image_model?: string
  strong_image_model?: string
  anthropic_api_key?: string
  openai_api_key?: string
  gemini_api_key?: string
}) {
  const res = await apiFetch(`${API_BASE}/settings/llm`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(res)
}

export async function fetchWorldMapStyles() {
  const res = await apiFetch(`${API_BASE}/world-map/styles`)
  return parseResponse(res)
}

export async function generateWorldMap(payload: {
  style: string
  tier?: 'cheap' | 'strong'
  provider?: string
  model?: string
  size?: string
  quality?: string
}) {
  const res = await apiFetch(`${API_BASE}/world-map/generate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(res)
}

export async function fetchLatestWorldMap() {
  const res = await apiFetch(`${API_BASE}/world-map/latest`)
  return parseResponse(res)
}

export async function switchSimulation(id: string) {
  const res = await apiFetch(`${API_BASE}/simulations/${id}/switch`, { method: 'POST' })
  return parseResponse(res)
}

export async function deleteSimulation(id: string) {
  const res = await apiFetch(`${API_BASE}/simulations/${id}`, { method: 'DELETE' })
  return parseResponse(res)
}

export async function fetchTimeline(params: {
  mode: 'agents' | 'kami'
  since_tick?: number
  until_tick?: number
}) {
  const query = new URLSearchParams()
  query.set('mode', params.mode)
  if (params.since_tick !== undefined) query.set('since_tick', String(params.since_tick))
  if (params.until_tick !== undefined) query.set('until_tick', String(params.until_tick))
  const res = await apiFetch(`${API_BASE}/timeline?${query}`)
  return parseResponse(res)
}

export async function fetchTimelineSnapshot(kind: 'agent' | 'kami', id: string, tick: number) {
  const res = await apiFetch(`${API_BASE}/timeline/snapshot/${kind}/${id}?tick=${tick}`)
  return parseResponse(res)
}

export async function stepTick(ticks = 1) {
  const res = await apiFetch(`${API_BASE}/sim/step?ticks=${ticks}`, { method: 'POST' })
  return parseResponse(res)
}

export async function startRun(ticks = 100) {
  const res = await apiFetch(`${API_BASE}/sim/run?ticks=${ticks}`, { method: 'POST' })
  return parseResponse(res)
}

export async function createSim(prompt: string, count: number, name?: string) {
  const res = await apiFetch(`${API_BASE}/sim/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prompt, agent_count: count, name })
  })
  return parseResponse(res)
}

export async function pauseSim() {
  const res = await apiFetch(`${API_BASE}/sim/pause`, { method: 'POST' })
  return parseResponse(res)
}

export class SimWebSocket {
  private ws: WebSocket | null = null
  private listeners: ((data: any) => void)[] = []
  private reconnect = false

  connect() {
    if (this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) return
    this.reconnect = true
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const token = getApiToken()
    const authProtocol = token ? `token.${encodeToken(token)}` : null
    const socket = new WebSocket(
      `${protocol}//${window.location.host}/ws`,
      authProtocol ? ['kami-auth', authProtocol] : undefined,
    )
    this.ws = socket
    socket.onmessage = (event) => {
      const data = JSON.parse(event.data)
      this.listeners.forEach((fn) => fn(data))
    }
    socket.onclose = (event) => {
      if (this.ws !== socket) return
      this.ws = null
      if (event.code === 4401) {
        this.reconnect = false
        window.dispatchEvent(new CustomEvent('kami-auth-required'))
      } else if (this.reconnect) {
        setTimeout(() => this.connect(), 2000)
      }
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
    this.reconnect = false
    this.ws?.close()
    this.ws = null
  }
}

export async function fetchChannels(agentId?: string) {
  const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : ''
  const res = await apiFetch(`${API_BASE}/channels${query}`)
  return parseResponse(res)
}

export async function createChannel(payload: {
  kind: string
  participants: string[]
  subscribers?: string[]
  medium_properties?: Record<string, unknown>
  metadata?: Record<string, unknown>
}) {
  const res = await apiFetch(`${API_BASE}/channels`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(res)
}

export async function sendChannelMessage(channelId: string, payload: {
  sender_id: string
  content: string
  salience?: number
}) {
  const res = await apiFetch(`${API_BASE}/channels/${encodeURIComponent(channelId)}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(res)
}

export async function startCall(payload: {
  sender_id: string
  recipient_id: string
  channel_id?: string
}) {
  const res = await apiFetch(`${API_BASE}/calls`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(res)
}

export async function updateCall(channelId: string, payload: {
  agent_id: string
  state: 'active' | 'declined' | 'ended'
}) {
  const res = await apiFetch(`${API_BASE}/channels/${encodeURIComponent(channelId)}/call`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  return parseResponse(res)
}

function encodeToken(token: string) {
  const bytes = new TextEncoder().encode(token)
  let binary = ''
  bytes.forEach((byte) => { binary += String.fromCharCode(byte) })
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

export const wsClient = new SimWebSocket()
