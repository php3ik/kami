import { useEffect, useMemo, useRef, useState } from 'react'
import cytoscape from 'cytoscape'
import { useSimStore } from '../stores/simStore'
import { activeKamiSet, agentsByKami, recentEventsByKami } from '../utils/simView'
import * as api from '../api/client'
import { ChevronDown, ChevronUp, Image, Loader2, Map as MapIcon, Sparkles } from 'lucide-react'

function resolveGeneratedAssetUrl(url?: string | null) {
  if (!url) return ''
  if (!url.startsWith('/generated/')) return url
  if (window.location.port === '5173') {
    return `${window.location.protocol}//${window.location.hostname}:8000${url}`
  }
  return url
}

export default function KamiGraph() {
  const containerRef = useRef<HTMLDivElement>(null)
  const cyRef = useRef<cytoscape.Core | null>(null)
  const [hovered, setHovered] = useState<any | null>(null)
  const [styles, setStyles] = useState<any[]>([])
  const [mapStyle, setMapStyle] = useState('realism')
  const [mapTier, setMapTier] = useState<'cheap' | 'strong'>('strong')
  const [generatedMap, setGeneratedMap] = useState<any | null>(null)
  const [mapLoading, setMapLoading] = useState(false)
  const [mapError, setMapError] = useState<string | null>(null)
  const [showGeneratedMap, setShowGeneratedMap] = useState(true)
  const [mapPanelCollapsed, setMapPanelCollapsed] = useState(false)
  const [showGraphLinks, setShowGraphLinks] = useState(true)
  const [mapViewport, setMapViewport] = useState({ zoom: 1, pan: { x: 0, y: 0 } })
  const { graph, agents, selectedKami, selectedAgent, tickLog } = useSimStore()
  const agentGroups = useMemo(() => agentsByKami(agents), [agents])
  const eventCounts = useMemo(() => recentEventsByKami(tickLog, 8), [tickLog])
  const activeKamis = useMemo(() => activeKamiSet(tickLog), [tickLog])
  const generatedMapSrc = useMemo(() => {
    const url = resolveGeneratedAssetUrl(generatedMap?.url)
    if (!url) return ''
    return `${url}?v=${encodeURIComponent(String(generatedMap?.created_at || generatedMap?.url || 'map'))}`
  }, [generatedMap])
  const hasAnchoredMap = Boolean(
    generatedMap?.image_width && generatedMap?.image_height && generatedMap?.bboxes?.length
  )
  const anchoredImageStyle = hasAnchoredMap
    ? {
        width: `${generatedMap.image_width}px`,
        height: `${generatedMap.image_height}px`,
        transform: `translate(${mapViewport.pan.x}px, ${mapViewport.pan.y}px) scale(${mapViewport.zoom})`,
        transformOrigin: '0 0',
      }
    : undefined

  useEffect(() => {
    api.fetchWorldMapStyles()
      .then((data) => {
        const nextStyles = data.styles || []
        setStyles(nextStyles)
        if (nextStyles.length && !nextStyles.some((item: any) => item.id === mapStyle)) {
          setMapStyle(nextStyles[0].id)
        }
      })
      .catch(() => setStyles([]))
  }, [])

  const generateDetailedMap = async () => {
    setMapLoading(true)
    setMapError(null)
    try {
      const result = await api.generateWorldMap({
        style: mapStyle,
        tier: mapTier,
        size: '1536x1024',
        quality: mapTier === 'strong' ? 'high' : 'medium',
      })
      setGeneratedMap(result)
      setShowGeneratedMap(true)
    } catch (error) {
      setMapError(error instanceof Error ? error.message : 'Failed to generate world map')
    } finally {
      setMapLoading(false)
    }
  }

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
            color: '#f8fafc',
            'text-outline-width': 6,
            'text-outline-color': '#020617',
            'background-color': '#0f172a',
            'background-opacity': 0.96,
            'border-width': 3,
            'border-color': '#cbd5e1',
            width: 52,
            height: 52,
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
            'font-size': '12px',
            'font-weight': 'bold',
            color: '#fff',
            'text-outline-width': 4,
            'text-outline-color': '#1e1b4b',
            'background-color': '#a855f7',
            'background-opacity': 1,
            width: 24,
            height: 24,
            'border-width': 3,
            'border-color': '#f5d0fe',
            shape: 'ellipse',
            'z-index': 100,
          } as any,
        },
        {
          selector: 'node[kind="map_extent"]',
          style: {
            label: '',
            width: 1,
            height: 1,
            opacity: 0,
            events: 'no',
            'background-opacity': 0,
            'border-opacity': 0,
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
            width: 3,
            'line-color': '#e2e8f0',
            opacity: 0.85,
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

    const syncViewport = () => {
      setMapViewport({
        zoom: cy.zoom(),
        pan: cy.pan(),
      })
    }
    syncViewport()
    cy.on('pan zoom resize', syncViewport)

    cy.on('tap', 'node', (evt) => {
      const node = evt.target
      if (node.data('kind') === 'map_extent') {
        return
      }
      if (node.data('kind') !== 'agent') {
        // We use the getState to avoid dependency issues on re-render
        useSimStore.getState().selectKami(node.id())
      } else {
        useSimStore.getState().selectAgent(node.id())
      }
    })

    cy.on('mouseover', 'node', (evt) => {
      const node = evt.target
      if (node.data('kind') === 'map_extent') return
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
      cy.removeListener('pan zoom resize', syncViewport)
      cy.destroy()
    }
  }, [graph]) // only on graph change

  useEffect(() => {
    let cancelled = false
    setGeneratedMap(null)
    setMapError(null)
    if (!graph.nodes.length) {
      return () => {
        cancelled = true
      }
    }

    api.fetchLatestWorldMap()
      .then((data) => {
        if (!cancelled && data?.url) {
          setGeneratedMap(data)
          setShowGeneratedMap(true)
        }
      })
      .catch(() => {
        if (!cancelled) setGeneratedMap(null)
      })

    return () => {
      cancelled = true
    }
  }, [graph])

  useEffect(() => {
    const cy = cyRef.current
    if (!cy) return

    const width = Number(generatedMap?.image_width || 0)
    const height = Number(generatedMap?.image_height || 0)
    const bboxes = Array.isArray(generatedMap?.bboxes) ? generatedMap.bboxes : []
    if (!width || !height || !bboxes.length) {
      cy.nodes('[kind="map_extent"]').remove()
      cy.nodes('[kind!="agent"]').forEach((node) => {
        node.unlock()
      })
      return
    }

    const bboxByKami = new Map<string, any>(
      bboxes
        .filter((item: any) => item?.id && item?.bbox)
        .map((item: any) => [item.id, item.bbox])
    )

    cy.batch(() => {
      cy.nodes('[kind="map_extent"]').remove()
      cy.add([
        {
          group: 'nodes',
          data: { id: '__map_extent_top_left', kind: 'map_extent' },
          position: { x: 0, y: 0 },
          selectable: false,
          grabbable: false,
        },
        {
          group: 'nodes',
          data: { id: '__map_extent_bottom_right', kind: 'map_extent' },
          position: { x: width, y: height },
          selectable: false,
          grabbable: false,
        },
      ])

      graph.nodes.forEach((graphNode: any) => {
        const node = cy.getElementById(graphNode.id)
        const bbox = bboxByKami.get(graphNode.id)
        if (!node.length || !bbox) return
        const cx = (Number(bbox.x) + Number(bbox.w) / 2) * width
        const cyPos = (Number(bbox.y) + Number(bbox.h) / 2) * height
        node.unlock()
        node.position({ x: cx, y: cyPos })
        node.data('mapBbox', bbox)
        node.lock()
      })
    })

    cy.fit(cy.nodes('[kind!="agent"]'), 45)
    setMapViewport({ zoom: cy.zoom(), pan: cy.pan() })
  }, [generatedMap, graph])

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
    cy.nodes('[kind!="agent"][kind!="map_extent"]').forEach(node => {
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
    cy.edges().style('display', showGraphLinks ? 'element' : 'none')
  }, [showGraphLinks, graph])

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
    <div className="relative w-full h-full overflow-hidden bg-slate-950">
      {generatedMapSrc && showGeneratedMap && (
        <img
          src={generatedMapSrc}
          alt="Generated world map"
          className={`pointer-events-none absolute z-0 opacity-95 ${hasAnchoredMap ? 'left-0 top-0 object-fill' : 'inset-0 h-full w-full object-contain'}`}
          style={anchoredImageStyle}
          onError={() => setMapError('Generated image exists, but the browser could not load it. Try opening the image directly.')}
        />
      )}
      <div
        ref={containerRef}
        className="relative z-10 w-full h-full"
        style={{ minHeight: '400px' }}
      />
      <div className={`absolute left-4 top-4 z-30 border border-slate-800 bg-slate-950/90 backdrop-blur text-xs text-slate-400 shadow-2xl shadow-black/30 ${mapPanelCollapsed ? 'w-auto px-2 py-2' : 'w-[330px] px-3 py-3'}`}>
        <div className="mb-2 flex items-center justify-between gap-2">
          <div>
            <div className="flex items-center gap-2 font-semibold text-slate-200">
              <MapIcon size={14} />
              World Map
            </div>
            {!mapPanelCollapsed && <div className="mt-1 text-slate-500">Graph overlay + generated top-view map</div>}
          </div>
          <div className="flex items-center gap-1">
          {generatedMapSrc && !mapPanelCollapsed && (
            <button
              onClick={() => setShowGeneratedMap((value) => !value)}
              className="inline-flex h-7 w-7 items-center justify-center border border-slate-800 text-slate-400 hover:border-cyan-500 hover:text-slate-100"
              title={showGeneratedMap ? 'Hide generated map' : 'Show generated map'}
            >
              <Image size={14} />
            </button>
          )}
            <button
              onClick={() => setMapPanelCollapsed((value) => !value)}
              className="inline-flex h-7 w-7 items-center justify-center border border-slate-800 text-slate-400 hover:border-cyan-500 hover:text-slate-100"
              title={mapPanelCollapsed ? 'Expand world map controls' : 'Collapse world map controls'}
            >
              {mapPanelCollapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}
            </button>
          </div>
        </div>
        {!mapPanelCollapsed && (
          <>
        <div className="grid grid-cols-[1fr_auto] gap-2">
          <select
            value={mapStyle}
            onChange={(e) => setMapStyle(e.target.value)}
            className="border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs text-slate-100 outline-none focus:border-cyan-400"
          >
            {(styles.length ? styles : [{ id: 'realism', label: 'Realism' }]).map((style: any) => (
              <option key={style.id} value={style.id}>{style.label}</option>
            ))}
          </select>
          <select
            value={mapTier}
            onChange={(e) => setMapTier(e.target.value as 'cheap' | 'strong')}
            className="border border-slate-800 bg-slate-900 px-2 py-1.5 text-xs text-slate-100 outline-none focus:border-cyan-400"
          >
            <option value="strong">Strong</option>
            <option value="cheap">Cheap</option>
          </select>
        </div>
        <button
          onClick={generateDetailedMap}
          disabled={mapLoading || !graph.nodes.length}
          className="mt-2 inline-flex w-full items-center justify-center gap-2 bg-cyan-600 px-3 py-2 text-xs font-semibold text-white hover:bg-cyan-500 disabled:bg-slate-800 disabled:text-slate-500"
        >
          {mapLoading ? <Loader2 size={14} className="animate-spin" /> : <Sparkles size={14} />}
          {mapLoading ? 'Generating detailed map...' : 'Generate Detailed Map'}
        </button>
        <label className="mt-2 flex cursor-pointer items-center justify-between gap-3 border border-slate-800 bg-slate-900/60 px-2 py-1.5 text-slate-300">
          <span>Show node links</span>
          <input
            type="checkbox"
            checked={showGraphLinks}
            onChange={(event) => setShowGraphLinks(event.target.checked)}
            className="h-4 w-4 accent-cyan-500"
          />
        </label>
        {mapError && <div className="mt-2 border border-rose-500/50 bg-rose-950/40 px-2 py-1.5 text-rose-100">{mapError}</div>}
        {generatedMapSrc && (
          <div className="mt-2 flex items-center justify-between gap-2 text-slate-500">
            {generatedMap.provider} · {generatedMap.model} · {generatedMap.style}
          </div>
        )}
        <div className="mt-3 border-t border-slate-800 pt-2">
          <div>Blue glow: recently active scene</div>
          <div>Node size: recent event density</div>
        </div>
          </>
        )}
      </div>
      {hovered && (
        <div className="absolute right-4 top-4 z-20 max-w-xs rounded border border-slate-700 bg-slate-950/95 backdrop-blur px-3 py-2 text-xs shadow-xl">
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
