import { useSimStore } from '../../stores/simStore'

export default function KamiInspector() {
  const { kamiDetail, selectAgent } = useSimStore()

  if (!kamiDetail) {
    return <div className="p-4 text-gray-500 text-sm">Select a kami to inspect</div>
  }

  const agents = kamiDetail.entities?.filter((e: any) => e.kind === 'agent') || []
  const objects = kamiDetail.entities?.filter((e: any) => e.kind === 'object') || []
  const events = kamiDetail.recent_events || []

  return (
    <div className="p-3 space-y-4 text-sm overflow-y-auto">
      <div>
        <h3 className="text-lg font-bold text-white">{kamiDetail.kami_id}</h3>
        <p className="text-gray-400">Entities: {kamiDetail.entity_count}</p>
      </div>

      {agents.length > 0 && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Agents Present</h4>
          {agents.map((a: any) => (
            <div
              key={a.entity_id}
              className="px-2 py-1 rounded cursor-pointer hover:bg-gray-800"
              onClick={() => selectAgent(a.entity_id)}
            >
              <span className="text-blue-400">{a.name}</span>
              {a.states && Object.keys(a.states).length > 0 && (
                <span className="text-gray-500 ml-2">
                  ({Object.entries(a.states).map(([k, v]) => `${k}=${v}`).join(', ')})
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {objects.length > 0 && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Objects</h4>
          {objects.map((o: any) => (
            <div key={o.entity_id} className="px-2 py-0.5 text-gray-400">
              {o.name}
            </div>
          ))}
        </div>
      )}

      {events.length > 0 && (
        <div>
          <h4 className="font-semibold text-gray-300 mb-1">Recent Events</h4>
          {events.map((e: any) => (
            <div key={e.event_id} className="px-2 py-1 border-l-2 border-gray-700 mb-2">
              <div className="flex justify-between text-xs">
                <span className="text-yellow-500">tick {e.tick}</span>
                <span className="text-gray-500">{e.event_type}</span>
                <span className="text-gray-600">s={e.salience?.toFixed(2)}</span>
              </div>
              <p className="text-gray-300 mt-0.5">{e.narrative}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
