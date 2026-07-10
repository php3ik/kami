import { create } from 'zustand'
import * as api from '../api/client'
import { wsClient } from '../api/client'

interface SimState {
  // Status
  currentTick: number
  simTimeMinutes: number
  timeMode: 'dense' | 'sparse'
  lastSkippedTicks: number
  running: boolean
  paused: boolean
  totalCostUsd: number
  totalCalls: number

  // Graph & Entitites
  graph: { nodes: any[]; edges: any[] }
  agents: any[]
  simulations: any[]
  activeSimulationId: string | null

  // Selection
  selectedKami: string | null
  selectedAgent: string | null
  kamiDetail: any | null
  agentDetail: any | null
  selectionLoading: boolean
  selectionError: string | null
  
  isCreateModalOpen: boolean

  // Events
  recentEvents: any[]

  // Tick log
  tickLog: any[]
  liveActivity: any[]
  viewMode: 'graph' | 'timeline'
  timelineMode: 'agents' | 'kami'
  timelineData: any | null
  timelineSelection: { kind: 'agent' | 'kami'; id: string; tick: number } | null

  // Actions
  refreshStatus: () => Promise<void>
  loadSimulations: () => Promise<void>
  switchSimulation: (id: string) => Promise<void>
  deleteSimulation: (id: string) => Promise<void>
  loadGraph: () => Promise<void>
  loadAgents: () => Promise<void>
  selectKami: (id: string | null) => Promise<void>
  selectAgent: (id: string | null) => Promise<void>
  step: (ticks?: number) => Promise<void>
  startRun: (ticks?: number) => Promise<void>
  pause: () => Promise<void>
  addTickResult: (result: any) => void
  setViewMode: (mode: 'graph' | 'timeline') => void
  setTimelineMode: (mode: 'agents' | 'kami') => Promise<void>
  loadTimeline: () => Promise<void>
  selectTimelineCell: (kind: 'agent' | 'kami', id: string, tick: number) => Promise<void>
  openCreateModal: (open: boolean) => void
  createSim: (prompt: string, count: number, name?: string) => Promise<void>
}

export const useSimStore = create<SimState>((set, get) => ({
  currentTick: 0,
  simTimeMinutes: 0,
  timeMode: 'dense',
  lastSkippedTicks: 0,
  running: false,
  paused: true,
  totalCostUsd: 0,
  totalCalls: 0,
  graph: { nodes: [], edges: [] },
  agents: [],
  simulations: [],
  activeSimulationId: null,
  selectedKami: null,
  selectedAgent: null,
  kamiDetail: null,
  agentDetail: null,
  selectionLoading: false,
  selectionError: null,
  isCreateModalOpen: false,
  recentEvents: [],
  tickLog: [],
  liveActivity: [],
  viewMode: 'graph',
  timelineMode: 'agents',
  timelineData: null,
  timelineSelection: null,

  refreshStatus: async () => {
    const status = await api.fetchStatus()
    set({
      currentTick: status.current_tick,
      simTimeMinutes: status.sim_time_minutes ?? status.current_tick,
      timeMode: status.time_mode === 'sparse' ? 'sparse' : 'dense',
      lastSkippedTicks: status.last_skipped_ticks ?? 0,
      running: status.running,
      paused: status.paused,
      totalCostUsd: status.budget?.total_cost_usd ?? 0,
      totalCalls: status.budget?.total_calls ?? 0,
      activeSimulationId: status.active_simulation_id ?? null,
    })
  },

  loadSimulations: async () => {
    const data = await api.fetchSimulations()
    set({
      simulations: data.simulations || [],
      activeSimulationId: data.active_id || null,
    })
  },

  switchSimulation: async (id) => {
    const result = await api.switchSimulation(id)
    if (result.error) throw new Error(result.error)
    set({
      tickLog: [],
      recentEvents: [],
      liveActivity: [],
      selectedKami: null,
      selectedAgent: null,
      kamiDetail: null,
      agentDetail: null,
      timelineData: null,
      timelineSelection: null,
    })
    await get().loadGraph()
    await get().loadAgents()
    await get().refreshStatus()
    await get().loadSimulations()
    if (get().viewMode === 'timeline') await get().loadTimeline()
  },

  deleteSimulation: async (id) => {
    const result = await api.deleteSimulation(id)
    if (result.error) throw new Error(result.error)
    await get().loadSimulations()
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
    const graphNode = get().graph.nodes.find((node: any) => node.id === id)
    const placeholder = id
      ? {
          kami_id: id,
          name: graphNode?.name || graphNode?.label || id,
          archetype: { kind: graphNode?.kind },
          entities: [],
          recent_events: [],
          entity_count: 0,
          loading: true,
        }
      : null
    set({
      selectedKami: id,
      selectedAgent: null,
      kamiDetail: placeholder,
      agentDetail: null,
      selectionLoading: Boolean(id),
      selectionError: null,
    })
    if (id) {
      try {
        const detail = await api.fetchKami(id)
        if (get().selectedKami === id) {
          set({ kamiDetail: detail, selectionLoading: false, selectionError: detail?.error || null })
        }
      } catch (error) {
        if (get().selectedKami === id) {
          set({
            selectionLoading: false,
            selectionError: error instanceof Error ? error.message : 'Failed to load kami',
          })
        }
      }
    } else {
      set({ kamiDetail: null, selectionLoading: false })
    }
  },

  selectAgent: async (id) => {
    const agent = get().agents.find((item: any) => item.entity_id === id)
    const placeholder = id
      ? {
          entity_id: id,
          name: agent?.name || id,
          archetype: { role: agent?.role },
          location: { kami_id: agent?.kami_id },
          states: {},
          relations: [],
          beliefs: [],
          recent_thoughts: [],
          action_history: agent?.latest_event ? [agent.latest_event] : [],
          loading: true,
        }
      : null
    set({
      selectedAgent: id,
      selectedKami: null,
      agentDetail: placeholder,
      kamiDetail: null,
      selectionLoading: Boolean(id),
      selectionError: null,
    })
    if (id) {
      try {
        const detail = await api.fetchAgent(id)
        if (get().selectedAgent === id) {
          set({ agentDetail: detail, selectionLoading: false, selectionError: detail?.error || null })
        }
      } catch (error) {
        if (get().selectedAgent === id) {
          set({
            selectionLoading: false,
            selectionError: error instanceof Error ? error.message : 'Failed to load agent',
          })
        }
      }
    } else {
      set({ agentDetail: null, selectionLoading: false })
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
    set((state) => {
      const nextTick = result.next_tick ?? (
        result.tick !== undefined ? Number(result.tick) + 1 : state.currentTick
      )
      return {
        tickLog: [...state.tickLog, result],
        currentTick: nextTick,
        simTimeMinutes: result.sim_time_minutes ?? state.simTimeMinutes,
        timeMode: result.time_mode === 'sparse' ? 'sparse' : 'dense',
        lastSkippedTicks: result.skipped_ticks ?? 0,
        simulations: state.simulations.map((simulation: any) =>
          simulation.id === state.activeSimulationId
            ? { ...simulation, current_tick: nextTick, ticks: nextTick }
            : simulation
        ),
      }
    })
    if (get().viewMode === 'timeline') {
      get().loadTimeline()
    }
  },

  setViewMode: (mode) => {
    set({ viewMode: mode })
    if (mode === 'timeline') {
      get().loadTimeline()
    }
  },

  setTimelineMode: async (mode) => {
    set({ timelineMode: mode, timelineSelection: null })
    await get().loadTimeline()
  },

  loadTimeline: async () => {
    const mode = get().timelineMode
    const currentTick = get().currentTick
    const sinceTick = Math.max(0, currentTick - 80)
    const timeline = await api.fetchTimeline({ mode, since_tick: sinceTick })
    set({ timelineData: timeline })
  },

  selectTimelineCell: async (kind, id, tick) => {
    set({ timelineSelection: { kind, id, tick }, selectionLoading: true, selectionError: null })
    try {
      const snapshot = await api.fetchTimelineSnapshot(kind, id, tick)
      if (get().timelineSelection?.id !== id || get().timelineSelection?.tick !== tick) return
      if (kind === 'agent') {
        set({
          selectedAgent: id,
          selectedKami: null,
          agentDetail: snapshot,
          kamiDetail: null,
          selectionLoading: false,
          selectionError: snapshot?.error || null,
        })
      } else {
        set({
          selectedKami: id,
          selectedAgent: null,
          kamiDetail: snapshot,
          agentDetail: null,
          selectionLoading: false,
          selectionError: snapshot?.error || null,
        })
      }
    } catch (error) {
      set({
        selectionLoading: false,
        selectionError: error instanceof Error ? error.message : 'Failed to load timeline snapshot',
      })
    }
  },

  openCreateModal: (open) => set({ isCreateModalOpen: open }),

  createSim: async (prompt, count, name) => {
    await api.createSim(prompt, count, name)
    set({
      tickLog: [],
      recentEvents: [],
      liveActivity: [],
      selectedKami: null,
      selectedAgent: null,
      kamiDetail: null,
      agentDetail: null,
      selectionLoading: false,
      selectionError: null,
      timelineData: null,
      timelineSelection: null,
    })
    await get().loadGraph()
    await get().loadAgents()
    await get().refreshStatus()
    await get().loadSimulations()
    if (get().viewMode === 'timeline') await get().loadTimeline()
  },
}))

// Setup WebSocket listeners. App connects after the auth gate succeeds.
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
  } else if (msg.type === 'simulation_switched') {
    useSimStore.setState({
      tickLog: [],
      recentEvents: [],
      liveActivity: [],
      selectedKami: null,
      selectedAgent: null,
      kamiDetail: null,
      agentDetail: null,
      selectionLoading: false,
      selectionError: null,
      timelineData: null,
      timelineSelection: null,
    })
    state.loadGraph()
    state.loadAgents()
    state.refreshStatus()
    state.loadSimulations()
  }
})
