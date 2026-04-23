import { useEffect, useRef } from 'react'
import cytoscape from 'cytoscape'
import { useSimStore } from '../stores/simStore'

export default function KamiGraph() {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const { graph, selectKami } = useSimStore()

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
          selector: 'node',
          style: {
            label: 'data(label)',
            'text-valign': 'bottom',
            'text-halign': 'center',
            'font-size': '10px',
            color: '#e2e8f0',
            'background-color': '#4a5568',
            width: 40,
            height: 40,
            'text-margin-y': 8,
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
      layout: {
        name: 'cose',
        padding: 40,
        animate: false,
      },
    })

    cy.on('tap', 'node', (evt) => {
      selectKami(evt.target.id())
    })

    cy.on('tap', (evt) => {
      if (evt.target === cy) selectKami(null)
    })

    cyRef.current = cy

    return () => {
      cy.destroy()
    }
  }, [graph, selectKami])

  return (
    <div
      ref={containerRef}
      className="w-full h-full bg-gray-950"
      style={{ minHeight: '400px' }}
    />
  )
}
