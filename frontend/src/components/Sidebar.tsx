import { useSimStore } from '../stores/simStore'

export default function Sidebar() {
  const { graph, selectKami, selectedKami, agents, selectAgent, selectedAgent } = useSimStore()

  const kamiNodes = graph.nodes || []

  // Group by kind
  const grouped: Record<string, any[]> = {}
  for (const node of kamiNodes) {
    const kind = node.kind || 'other'
    if (!grouped[kind]) grouped[kind] = []
    grouped[kind].push(node)
  }

  return (
    <div className="p-3 space-y-4 text-sm overflow-y-auto">
      <button
        onClick={() => useSimStore.getState().openCreateModal(true)}
        className="w-full bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded text-sm text-center font-bold"
      >
        + Create Simulation
      </button>
      <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider">Kami Locations</h2>

      {Object.entries(grouped).map(([kind, nodes]) => (
        <div key={kind}>
          <h3 className="text-xs text-gray-600 uppercase mb-1">{kind}</h3>
          {nodes.map((n: any) => (
            <div
              key={n.id}
              className={`px-2 py-1 rounded cursor-pointer ${
                selectedKami === n.id
                  ? 'bg-gray-700 text-white'
                  : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
              }`}
              onClick={() => selectKami(n.id)}
            >
              {n.name || n.id}
            </div>
          ))}
        </div>
      ))}

      <h2 className="text-xs font-bold text-gray-500 uppercase tracking-wider mt-6">Agents</h2>
      <div>
        {agents?.map((a: any) => (
          <div
            key={a.entity_id}
            className={`px-2 py-1 rounded cursor-pointer ${
              selectedAgent === a.entity_id
                ? 'bg-gray-700 text-white'
                : 'text-gray-400 hover:bg-gray-800 hover:text-gray-200'
            }`}
            onClick={() => selectAgent(a.entity_id)}
          >
            {a.name} <span className="text-gray-600 text-xs float-right">{a.role}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
