import { useMemo } from 'react'
import { AlertTriangle, Brain, Fingerprint, HeartPulse, MapPin, Network, ScrollText, Sparkles } from 'lucide-react'
import { useSimStore } from '../../stores/simStore'
import { buildAgentTimeline, classifyThought, digest, getKamiName, makeKamiLookup } from '../../utils/simView'
import EntityReferenceText from './EntityReferenceText'

export default function AgentInspector() {
  const { agentDetail, graph, tickLog, selectKami, selectionLoading, selectionError } = useSimStore()
  const kamiLookup = useMemo(() => makeKamiLookup(graph.nodes || []), [graph.nodes])

  if (!agentDetail) return <InspectorState title="Loading agent" body="Preparing the crew profile..." />
  if (selectionError || agentDetail.error) {
    return <InspectorState title="Could not load agent" body={selectionError || agentDetail.error} tone="error" />
  }

  const arch = agentDetail.archetype || {}
  const timeline = buildAgentTimeline(agentDetail, tickLog).slice(0, 12)
  const currentLocation = agentDetail.location?.kami_id
  const currentLocationName = getKamiName(currentLocation, kamiLookup)
  const latest = timeline[0]
  const latestStatus = classifyThought(latest?.thought)
  const isError = latestStatus === 'error'

  return (
    <div className="h-full overflow-y-auto bg-slate-950 text-sm">
      <div className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/95 backdrop-blur p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="text-xl font-bold text-white truncate">{agentDetail.name}</h3>
            <p className="text-xs text-slate-500 mt-1">
              {arch.age ? `Age ${arch.age}` : 'Age unknown'} · {arch.role || 'Agent'}
            </p>
            {agentDetail.snapshot_tick !== undefined && (
              <p className="text-[11px] text-amber-300 mt-1">Snapshot at tick {agentDetail.snapshot_tick}</p>
            )}
          </div>
          <span className={`inline-flex items-center gap-1 px-2 py-1 rounded border text-xs ${
            selectionLoading || agentDetail.loading
              ? 'bg-blue-950/70 text-blue-200 border-blue-700'
              : isError ? 'bg-rose-950/70 text-rose-200 border-rose-700' : 'bg-emerald-950/70 text-emerald-200 border-emerald-700'
          }`}>
            {isError ? <AlertTriangle size={12} /> : <Sparkles size={12} />}
            {selectionLoading || agentDetail.loading ? 'Loading' : isError ? 'Needs attention' : 'In character'}
          </span>
        </div>
        <button
          onClick={() => currentLocation && selectKami(currentLocation)}
          className="mt-3 w-full flex items-center gap-2 text-left px-2.5 py-2 rounded bg-blue-950/40 border border-blue-900/70 hover:border-blue-600 transition-colors"
        >
          <MapPin size={14} className="text-blue-300" />
          <span className="text-blue-100 truncate">{currentLocationName}</span>
        </button>
      </div>

      <div className="p-4 space-y-5">
        <section className="rounded border border-slate-800 bg-slate-900/60 p-3">
          <div className="flex items-center gap-2 mb-2">
            <Brain size={15} className="text-purple-300" />
            <h4 className="font-semibold text-slate-100">Current Signal</h4>
          </div>
          <p className={`leading-relaxed ${isError ? 'text-rose-200' : 'text-slate-200'}`}>
            <EntityReferenceText text={digest(latest?.thought, 360)} />
          </p>
          {latest?.narrative && (
            <div className="mt-3 border-t border-slate-800 pt-3">
              <div className="text-[11px] uppercase tracking-wider text-slate-500 mb-1">Resolved Event</div>
              <p className="text-slate-300 leading-relaxed"><EntityReferenceText text={latest.narrative} /></p>
            </div>
          )}
        </section>

        {arch.background && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <Fingerprint size={15} className="text-slate-400" />
              <h4 className="font-semibold text-slate-100">Background</h4>
            </div>
            <p className="text-slate-400 leading-relaxed">{arch.background}</p>
          </section>
        )}

        {arch.traits && (
          <section>
            <h4 className="font-semibold text-slate-100 mb-2">Traits</h4>
            <div className="flex flex-wrap gap-1.5">
              {arch.traits.map((trait: string) => (
                <span key={trait} className="px-2 py-1 bg-slate-900 border border-slate-800 rounded text-xs text-slate-300">
                  {trait}
                </span>
              ))}
            </div>
          </section>
        )}

        {agentDetail.states && Object.keys(agentDetail.states).length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <HeartPulse size={15} className="text-emerald-300" />
              <h4 className="font-semibold text-slate-100">Physical State</h4>
            </div>
            <div className="grid grid-cols-2 gap-2">
              {Object.entries(agentDetail.states).map(([key, value]) => (
                <div key={key} className="rounded border border-slate-800 bg-slate-900/70 px-2 py-2">
                  <div className="text-[11px] uppercase tracking-wider text-slate-500">{key}</div>
                  <div className="text-slate-100 font-semibold">{String(value)}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {arch.goals && (
          <section>
            <h4 className="font-semibold text-slate-100 mb-2">Goal Stack</h4>
            <div className="space-y-2">
              {Object.entries(arch.goals).map(([level, goal]) => (
                <div key={level} className="rounded border border-slate-800 bg-slate-900/50 p-2">
                  <div className="text-[11px] uppercase tracking-wider text-amber-300">{level}</div>
                  <div className="text-slate-300 mt-1 leading-relaxed">{goal as string}</div>
                </div>
              ))}
            </div>
          </section>
        )}

        {timeline.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <ScrollText size={15} className="text-blue-300" />
              <h4 className="font-semibold text-slate-100">Tick Timeline</h4>
            </div>
            <div className="space-y-2">
              {timeline.map((row: any) => {
                const rowError = classifyThought(row.thought) === 'error'
                return (
                  <div key={row.tick} className={`rounded border p-3 ${
                    rowError ? 'border-rose-800 bg-rose-950/30' : 'border-slate-800 bg-slate-900/60'
                  }`}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-semibold text-blue-300">Tick {row.tick}</span>
                      {row.event_type && <span className="text-[11px] text-slate-500">{row.event_type}</span>}
                    </div>
                    {row.thought && (
                      <p className={`${rowError ? 'text-rose-200' : 'text-purple-100'} leading-relaxed`}>
                        <EntityReferenceText text={row.thought} />
                      </p>
                    )}
                    {row.narrative && (
                      <p className="mt-2 border-t border-slate-800 pt-2 text-slate-300 leading-relaxed">
                        <EntityReferenceText text={row.narrative} />
                      </p>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        )}

        {agentDetail.relations && agentDetail.relations.length > 0 && (
          <section>
            <div className="flex items-center gap-2 mb-2">
              <Network size={15} className="text-cyan-300" />
              <h4 className="font-semibold text-slate-100">Relations</h4>
            </div>
            <div className="space-y-1">
              {agentDetail.relations.slice(0, 12).map((relation: any, i: number) => (
                <div key={i} className="px-2 py-1.5 rounded bg-slate-900/60 border border-slate-800 text-slate-400">
                  <span className="text-cyan-200">{relation.type}</span>{' '}
                  <span>{relation.from === agentDetail.entity_id ? relation.to : relation.from}</span>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}

function InspectorState({ title, body, tone = 'neutral' }: { title: string; body: string; tone?: 'neutral' | 'error' }) {
  return (
    <div className="h-full bg-slate-950 p-4 text-sm">
      <div className={`rounded border p-4 ${
        tone === 'error' ? 'border-rose-800 bg-rose-950/30 text-rose-200' : 'border-slate-800 bg-slate-900/60 text-slate-300'
      }`}>
        <div className="font-semibold text-slate-100">{title}</div>
        <div className="mt-2 leading-relaxed">{body}</div>
      </div>
    </div>
  )
}
