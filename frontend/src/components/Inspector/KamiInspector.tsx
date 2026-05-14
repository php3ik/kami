import { Box, Map, Radio, Users } from 'lucide-react'
import { useSimStore } from '../../stores/simStore'
import { kindLabel } from '../../utils/simView'
import EntityReferenceText from './EntityReferenceText'

export default function KamiInspector() {
  const { kamiDetail, selectAgent, selectionLoading, selectionError } = useSimStore()

  if (!kamiDetail) {
    return <div className="p-4 text-slate-500 text-sm">Select a kami to inspect</div>
  }
  if (selectionError || kamiDetail.error) {
    return (
      <div className="h-full bg-slate-950 p-4 text-sm">
        <div className="rounded border border-rose-800 bg-rose-950/30 p-4 text-rose-200">
          <div className="font-semibold text-slate-100">Could not load kami</div>
          <div className="mt-2 leading-relaxed">{selectionError || kamiDetail.error}</div>
        </div>
      </div>
    )
  }

  const agents = kamiDetail.entities?.filter((entity: any) => entity.kind === 'agent') || []
  const objects = kamiDetail.entities?.filter((entity: any) => entity.kind === 'object') || []
  const events = kamiDetail.recent_events || []
  const kind = kamiDetail.archetype?.kind || kamiDetail.archetype?.kami_kind || 'location'

  return (
    <div className="h-full overflow-y-auto bg-slate-950 text-sm">
      <div className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/95 backdrop-blur p-4">
        <div className="flex items-start gap-3">
          <div className="h-11 w-11 rounded bg-blue-950 border border-blue-800 grid place-items-center">
            <Map size={19} className="text-blue-200" />
          </div>
          <div className="min-w-0">
            <h3 className="text-xl font-bold text-white leading-tight">{kamiDetail.name || kamiDetail.kami_id}</h3>
            <p className="text-xs text-slate-500 mt-1">{kindLabel(kind)} · {kamiDetail.kami_id}</p>
            {kamiDetail.snapshot_tick !== undefined && (
              <p className="text-[11px] text-amber-300 mt-1">Snapshot at tick {kamiDetail.snapshot_tick}</p>
            )}
          </div>
        </div>
        <div className="grid grid-cols-3 gap-2 mt-4">
          <Metric label="Entities" value={kamiDetail.entity_count || 0} />
          <Metric label="Agents" value={agents.length} />
          <Metric label={selectionLoading || kamiDetail.loading ? 'Loading' : 'Events'} value={selectionLoading || kamiDetail.loading ? '...' : events.length} />
        </div>
      </div>

      <div className="p-4 space-y-5">
        {kamiDetail.archetype?.description && (
          <section className="rounded border border-slate-800 bg-slate-900/60 p-3">
            <h4 className="font-semibold text-slate-100 mb-2">Scene Description</h4>
            <p className="text-slate-400 leading-relaxed">{kamiDetail.archetype.description}</p>
          </section>
        )}

        {agents.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <Users size={15} className="text-purple-300" />
              <h4 className="font-semibold text-slate-100">Agents Present</h4>
            </div>
            <div className="space-y-2">
              {agents.map((agent: any) => (
                <button
                  key={agent.entity_id}
                  className="w-full px-3 py-2 rounded bg-slate-900/70 border border-slate-800 hover:border-purple-600 text-left transition-colors"
                  onClick={() => selectAgent(agent.entity_id)}
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-purple-100 truncate">{agent.name}</span>
                    <span className="text-[11px] text-slate-500">{agent.kind}</span>
                  </div>
                  {agent.states && Object.keys(agent.states).length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {Object.entries(agent.states).map(([key, value]) => (
                        <span key={key} className="px-1.5 py-0.5 rounded bg-slate-950 text-[11px] text-slate-400 border border-slate-800">
                          {key}: {String(value)}
                        </span>
                      ))}
                    </div>
                  )}
                </button>
              ))}
            </div>
          </section>
        )}

        {objects.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <Box size={15} className="text-amber-300" />
              <h4 className="font-semibold text-slate-100">Objects</h4>
            </div>
            <div className="space-y-1">
              {objects.map((object: any) => (
                <div key={object.entity_id} className="px-3 py-2 rounded bg-slate-900/50 border border-slate-800">
                  <div className="font-medium text-slate-200">{object.name}</div>
                  {object.states && Object.keys(object.states).length > 0 && (
                    <div className="mt-1 text-xs text-slate-500">
                      {Object.entries(object.states).map(([key, value]) => `${key}: ${value}`).join(' · ')}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {events.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <Radio size={15} className="text-emerald-300" />
              <h4 className="font-semibold text-slate-100">Scene Event Stream</h4>
            </div>
            <div className="space-y-2">
              {events.slice(0, 20).map((event: any) => (
                <div key={event.event_id} className="px-3 py-2 border-l-2 border-blue-700 bg-slate-900/70 rounded-r">
                  <div className="flex items-center justify-between text-[11px] mb-1">
                    <span className="text-blue-300 font-semibold">Tick {event.tick}</span>
                    <span className="text-slate-500">{event.event_type}</span>
                    <span className="text-slate-600">s={event.salience?.toFixed(2)}</span>
                  </div>
                  <p className="text-slate-300 leading-relaxed"><EntityReferenceText text={event.narrative} /></p>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded bg-slate-900 border border-slate-800 px-2 py-2">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="text-slate-100 font-bold tabular-nums">{value}</div>
    </div>
  )
}
