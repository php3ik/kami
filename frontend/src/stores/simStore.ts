import { create } from 'zustand'
import * as api from '../api/client'
import { wsClient } from '../api/client'

interface SimState {
  // Status
  currentTick: number
  running: boolean
  paused: boolean
  totalCostUsd: number
  totalCalls: number

  // Graph & Entitites
  graph: { nodes: any[]; edges: any[] }
  agents: any[]

  // Selection
  selectedKami: string | null
  selectedAgent: string | null
  kamiDetail: any | null
  agentDetail: any | null
  
  isCreateModalOpen: boolean

  // Events
  recentEvents: any[]

  // Tick log
  tickLog: any[]
  liveActivity: any[]

  // Actions
  refreshStatus: () => Promise<void>
  loadGraph: () => Promise<void>
  loadAgents: () => Promise<void>
  selectKami: (id: string | null) => Promise<void>
  selectAgent: (id: string | null) => Promise<void>
  step: (ticks?: number) => Promise<void>
  startRun: (ticks?: number) => Promise<void>
  pause: () => Promise<void>
  addTickResult: (result: any) => void
  openCreateModal: (open: boolean) => void
  createSim: (prompt: string, count: number) => Promise<void>
}

export const useSimStore = create<SimState>((set, get) => ({
  currentTick: 0,
  running: false,
  paused: true,
  totalCostUsd: 0,
  totalCalls: 0,
  graph: { nodes: [], edges: [] },
  agents: [],
  selectedKami: null,
  selectedAgent: null,
  kamiDetail: null,
  agentDetail: null,
  isCreateModalOpen: false,
  recentEvents: [],
  tickLog: [],
  liveActivity: [],

  refreshStatus: async () => {
    const status = await api.fetchStatus()
    set({
      currentTick: status.current_tick,
      running: status.running,
      paused: status.paused,
      totalCostUsd: status.budget?.total_cost_usd ?? 0,
      totalCalls: status.budget?.total_calls ?? 0,
    })
  },

  loadGraph: async () => {
    const graph = await api.fetchGraph()
    set({ graph })
  },

  loadAgents: async () => {
    const agents = await api.fetchAgents()
    set({ agents })
  },

  selectKami: async (id) => {
    set({ selectedKami: id, selectedAgent: null })
    if (id) {
      const detail = await api.fetchKami(id)
      set({ kamiDetail: detail })
    } else {
      set({ kamiDetail: null })
    }
  },

  selectAgent: async (id) => {
    set({ selectedAgent: id })
    if (id) {
      const detail = await api.fetchAgent(id)
      set({ agentDetail: detail })
    } else {
      set({ agentDetail: null })
    }
  },

  step: async (ticks = 1) => {
    set({ running: true, liveActivity: [] }) // Clear live activity at start of step
    try {
      await api.stepTick(ticks)
      const state = get()
      // WS will append to tickLog
      await state.refreshStatus()
      // Refresh selected views
      if (state.selectedKami) await state.selectKami(state.selectedKami)
      if (state.selectedAgent) await state.selectAgent(state.selectedAgent)
    } finally {
      set({ running: false })
    }
  },

  startRun: async (ticks = 100) => {
    set({ running: true, paused: false, liveActivity: [] })
    await api.startRun(ticks)
  },

  pause: async () => {
    await api.pauseSim()
    set({ paused: true })
    await get().refreshStatus()
  },

  addTickResult: (result) => {
    set((state) => ({
      tickLog: [...state.tickLog, result],
      currentTick: result.tick ?? state.currentTick,
    }))
  },

  openCreateModal: (open) => set({ isCreateModalOpen: open }),

  createSim: async (prompt, count) => {
    await api.createSim(prompt, count)
    get().tickLog = [] // Flush local prior log visually
    await get().loadGraph()
    await get().loadAgents()
    await get().refreshStatus()
    set({ tickLog: [], recentEvents: [], liveActivity: [], currentTick: 0, selectedKami: null, selectedAgent: null })
  },
}))

// Setup WebSocket listeners
wsClient.connect()
wsClient.onMessage((msg) => {
  const state = useSimStore.getState()
  if (msg.type === 'tick') {
    state.addTickResult(msg.data)
    useSimStore.setState({ liveActivity: [] }) // clear on tick end
    state.loadAgents() // update locations
  } else if (msg.type === 'progress') {
    useSimStore.setState({
      liveActivity: [...state.liveActivity, msg.data].slice(-50)
    })
  }
})
