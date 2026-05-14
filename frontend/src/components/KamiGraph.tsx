import { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import { useSimStore } from '../stores/simStore'
import { activeKamiSet, agentsByKami, recentEventsByKami } from '../utils/simView'

export default function KamiGraph() {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const [hovered, setHovered] = useState<any | null>(null)
  const { graph, agents, selectedKami, selectedAgent, tickLog } = useSimStore()
  const agentGroups = useMemo(() => agentsByKami(agents), [agents])
  const eventCounts = useMemo(() => recentEventsByKami(tickLog, 8), [tickLog])
  const activeKamis = useMemo(() => activeKamiSet(tickLog), [tickLog])

  // Initialize graph topology
  useEffect(() => {
    if (!containerRef.current || !graph.nodes.length) return

    const elements: cytoscape.ElementDefinition[] = [
      ...graph.nodes.map((n: any) => ({
          data: {
            id: n.id,
            label: n.name || n.id,
            kind: n.kind || 'location',
            agentCount: 0,
            eventCount: 0,
            active: 'no',
          },
        })),
      ...graph.edges.map((e: any, i: number) => ({
        data: {
          id: `edge-${i}`,
          source: e.source,
          target: e.target,
          edgeType: e.edge_type || 'adjacent',
        },
      })),
    ]

    if (cyRef.current) {
      cyRef.current.destroy()
    }

    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: 'node[kind!="agent"]',
          style: {
            label: 'data(label)',
            'text-valign': 'bottom',
            'text-halign': 'center',
            'font-size': '15px',
            'font-weight': 'bold',
            color: '#e2e8f0',
            'text-outline-width': 3,
            'text-outline-color': '#020617',
            'background-color': '#475569',
            width: 58,
            height: 58,
            'text-margin-y': 10,
          } as any,
        },
        {
          selector: 'node[active="yes"]',
          style: {
            'background-color': '#2563eb',
            'border-width': 4,
            'border-color': '#38bdf8',
            'shadow-blur': 18,
            'shadow-color': '#38bdf8',
            'shadow-opacity': 0.45,
          } as any,
        },
        {
          selector: 'node[eventCount > 0][kind!="agent"]',
          style: {
            width: 'mapData(eventCount, 0, 8, 60, 88)',
            height: 'mapData(eventCount, 0, 8, 60, 88)',
          } as any,
        },
        {
          selector: 'node[kind="agent"]',
          style: {
            label: 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'font-size': '11px',
            'font-weight': 'bold',
            color: '#fff',
            'text-outline-width': 2,
            'text-outline-color': '#312e81',
            'background-color': '#9f7aea', // purple-500
            width: 24,
            height: 24,
            'border-width': 2,
            'border-color': '#553c9a',
            shape: 'ellipse',
            'z-index': 100,
          } as any,
        },
        {
          selector: 'node[kind="commercial"]',
          style: { 'background-color': '#d69e2e' } as any,
        },
        {
          selector: 'node[kind="public_outdoor"]',
          style: { 'background-color': '#48bb78' } as any,
        },
        {
          selector: 'node[kind="residential"]',
          style: { 'background-color': '#4299e1' } as any,
        },
        {
          selector: 'edge',
          style: {
            width: 2,
            'line-color': '#4a5568',
            'curve-style': 'bezier',
          },
        },
        {
          selector: 'edge[edgeType="contains"]',
          style: {
            'line-style': 'dashed',
            'line-color': '#2d3748',
          },
        },
        {
          selector: ':selected',
          style: {
            'border-width': 5,
            'border-color': '#f6e05e',
            'shadow-blur': 24,
            'shadow-color': '#f6e05e',
            'shadow-opacity': 0.5,
          } as any,
        },
      ],
    })

    // Layout only the kamis and their edges
    const layoutElements = cy.elements().filter(ele => ele.isEdge() || ele.data('kind') !== 'agent')
    const layout = layoutElements.layout({
      name: 'cose',
      padding: 40,
      animate: false,
      randomize: true, // ensure it spreads out
      nodeRepulsion: () => 400000,
      idealEdgeLength: () => 100,
    })
    cy.one('layoutstop', () => {
      cy.fit(cy.elements(), 60)
    })
    layout.run()

    cy.on('tap', 'node', (evt) => {
      const node = evt.target
      if (node.data('kind') !== 'agent') {
        // We use the getState to avoid dependency issues on re-render
        useSimStore.getState().selectKami(node.id())
      } else {
        useSimStore.getState().selectAgent(node.id())
      }
    })

    cy.on('mouseover', 'node', (evt) => {
      const node = evt.target
      setHovered({
        id: node.id(),
        label: node.data('label'),
        kind: node.data('kind'),
        agentCount: node.data('agentCount') || 0,
        eventCount: node.data('eventCount') || 0,
      })
    })

    cy.on('mouseout', 'node', () => setHovered(null))

    cy.on('tap', (evt) => {
      if (evt.target === cy) {
        useSimStore.getState().selectKami(null)
      }
    })

    cyRef.current = cy

    return () => {
      cy.destroy()
    }
  }, [graph]) // only on graph change

  // Update agent positions manually
  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return

    // 1. Add missing agent nodes
    agents.forEach(agent => {
      if (!agent.kami_id) return
      
      const agentNode = cy.getElementById(agent.entity_id)
      const initials = agent.name.substring(0, 2).toUpperCase()

      if (agentNode.length === 0) {
        cy.add({
          group: 'nodes',
          data: {
            id: agent.entity_id,
            label: initials,
            kind: 'agent',
            kami_id: agent.kami_id
          },
          position: { x: 0, y: 0 },
          locked: false,
          grabbable: false,
        })
      } else {
        // update kami pointer
        agentNode.data('kami_id', agent.kami_id)
        agentNode.data('label', initials)
      }
    })

    // Remove agents no longer present
    const agentIds = new Set(agents.map((agent: any) => agent.entity_id))
    cy.nodes('[kind="agent"]').forEach(agentNode => {
      if (!agentIds.has(agentNode.id())) agentNode.remove()
    })

    // Update kami activity metadata
    cy.nodes('[kind!="agent"]').forEach(node => {
      const id = node.id()
      node.data('agentCount', agentGroups.get(id)?.length || 0)
      node.data('eventCount', eventCounts.get(id) || 0)
      node.data('active', activeKamis.has(id) ? 'yes' : 'no')
    })

    // 2. Function to snap agents to their kami
    const updateAgentPositions = () => {
      // Group agents by kami
      const kamiToAgents: Record<string, cytoscape.NodeSingular[]> = {}
      cy.nodes('[kind="agent"]').forEach(agentNode => {
        const kId = agentNode.data('kami_id')
        if (kId) {
          if (!kamiToAgents[kId]) kamiToAgents[kId] = []
          kamiToAgents[kId].push(agentNode)
        }
      })

      // Position them inside the kami circle
      Object.entries(kamiToAgents).forEach(([kId, agentNodes]) => {
        const kamiNode = cy.getElementById(kId)
        if (kamiNode.length === 0) return
        
        const pos = kamiNode.position()
        const count = agentNodes.length
        
        // Arrange in a small circle around the center of the kami node
        const radius = count > 1 ? 12 : 0
        agentNodes.forEach((an, i) => {
          const angle = (i / count) * Math.PI * 2
          an.position({
            x: pos.x + radius * Math.cos(angle),
            y: pos.y + radius * Math.sin(angle),
          })
        })
      })
    }

    // Sync positions immediately
    updateAgentPositions()

    // Sync positions whenever kamis are dragged or layout changes
    cy.on('position', 'node[kind!="agent"]', updateAgentPositions)
    cy.on('layoutstop', updateAgentPositions)

    return () => {
      cy.removeListener('position', 'node[kind!="agent"]', updateAgentPositions)
      cy.removeListener('layoutstop', updateAgentPositions)
    }
  }, [agents, agentGroups, eventCounts, activeKamis])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return
    cy.elements().unselect()
    if (selectedAgent) {
      cy.getElementById(selectedAgent).select()
      const agent = cy.getElementById(selectedAgent)
      if (agent.length > 0) cy.animate({ center: { eles: agent }, zoom: Math.max(cy.zoom(), 1.2) }, { duration: 250 })
    } else if (selectedKami) {
      const kami = cy.getElementById(selectedKami)
      kami.select()
      if (kami.length > 0) cy.animate({ center: { eles: kami }, zoom: Math.max(cy.zoom(), 1) }, { duration: 250 })
    }
  }, [selectedAgent, selectedKami])

  return (
    <div className="relative w-full h-full bg-slate-950">
      <div
        ref={containerRef}
        className="w-full h-full"
        style={{ minHeight: '400px' }}
      />
      <div className="absolute left-4 top-4 rounded border border-slate-800 bg-slate-950/85 backdrop-blur px-3 py-2 text-xs text-slate-400">
        <div className="font-semibold text-slate-200 mb-1">World Map</div>
        <div>Blue glow: recently active scene</div>
        <div>Node size: recent event density</div>
      </div>
      {hovered && (
        <div className="absolute right-4 top-4 max-w-xs rounded border border-slate-700 bg-slate-950/95 backdrop-blur px-3 py-2 text-xs shadow-xl">
          <div className="font-semibold text-slate-100 truncate">{hovered.label}</div>
          <div className="text-slate-500 mt-1">{hovered.kind}</div>
          <div className="mt-2 flex gap-3 text-slate-300">
            <span>{hovered.agentCount} agents</span>
            <span>{hovered.eventCount} events</span>
          </div>
        </div>
      )}
    </div>
  )
}
