import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import { useSimStore } from '../stores/simStore'

export default function KamiGraph() {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const { graph, agents, selectKami, selectAgent } = useSimStore()

  // Initialize graph topology
  useEffect(() => {
    if (!containerRef.current || !graph.nodes.length) return

    const elements: cytoscape.ElementDefinition[] = [
      ...graph.nodes.map((n: any) => ({
        data: {
          id: n.id,
          label: n.name || n.id,
          kind: n.kind || 'location',
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
            'font-size': '12px',
            'font-weight': 'bold',
            color: '#e2e8f0',
            'background-color': '#4a5568',
            width: 50,
            height: 50,
            'text-margin-y': 8,
          } as any,
        },
        {
          selector: 'node[kind="agent"]',
          style: {
            label: 'data(label)',
            'text-valign': 'center',
            'text-halign': 'center',
            'font-size': '10px',
            'font-weight': 'bold',
            color: '#fff',
            'background-color': '#9f7aea', // purple-500
            width: 20,
            height: 20,
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
            'border-width': 3,
            'border-color': '#f6e05e',
          } as any,
        },
      ],
    })

    // Layout only the kamis and their edges
    cy.layout({
      name: 'cose',
      padding: 40,
      animate: false,
      randomize: true, // ensure it spreads out
      nodeRepulsion: () => 400000,
      idealEdgeLength: () => 100,
      eles: cy.elements().filter(ele => ele.isEdge() || ele.data('kind') !== 'agent'),
    }).run()

    cy.on('tap', 'node', (evt) => {
      const node = evt.target
      if (node.data('kind') !== 'agent') {
        // We use the getState to avoid dependency issues on re-render
        useSimStore.getState().selectKami(node.id())
      } else {
        useSimStore.getState().selectAgent(node.id())
      }
    })

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
      }
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
  }, [agents])

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-gray-950"
      style={{ minHeight: '400px' }}
    />
  )
}
