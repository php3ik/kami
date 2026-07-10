import { useEffect, useState } from 'react'
import { Box, History, Map, Radio, ScrollText, Stamp, Users } from 'lucide-react'
import { useSimStore } from '../../stores/simStore'
import { kindLabel } from '../../utils/simView'
import EntityReferenceText from './EntityReferenceText'

type KamiTab = 'scene' | 'history' | 'imprints' | 'trace'

const tabs: Array<{ id: KamiTab; label: string }> = [
  { id: 'scene', label: 'Scene' },
  { id: 'history', label: 'History' },
  { id: 'imprints', label: 'Imprints' },
  { id: 'trace', label: 'Trace' },
]

export default function KamiInspector() {
  const { kamiDetail, selectAgent, selectionLoading, selectionError } = useSimStore()
  const [activeTab, setActiveTab] = useState<KamiTab>('scene')

  useEffect(() => setActiveTab('scene'), [kamiDetail?.kami_id])

  if (!kamiDetail) return <div className="p-4 text-slate-500 text-sm">Select a kami to inspect</div>
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
  const objects = kamiDetail.entities?.filter((entity: any) => entity.kind !== 'agent') || []
  const events = kamiDetail.recent_events || []
  const memory = kamiDetail.memory || {}
  const kind = kamiDetail.archetype?.kind || kamiDetail.archetype?.kami_kind || 'location'

  return (
    <div className="h-full overflow-y-auto bg-slate-950 text-sm">
      <div className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/95 backdrop-blur">
        <div className="p-4 pb-3">
          <div className="flex items-start gap-3">
            <div className="h-11 w-11 shrink-0 rounded bg-blue-950 border border-blue-800 grid place-items-center">
              <Map size={19} className="text-blue-200" />
            </div>
            <div className="min-w-0">
              <h3 className="text-xl font-bold text-white leading-tight break-words">{kamiDetail.name || kamiDetail.kami_id}</h3>
              <p className="text-xs text-slate-500 mt-1 break-all">{kindLabel(kind)} · {kamiDetail.kami_id}</p>
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
        <div className="flex px-2" role="tablist" aria-label="Kami detail views">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={activeTab === tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex-1 border-b-2 px-2 py-2 text-xs font-medium transition-colors ${
                activeTab === tab.id
                  ? 'border-blue-400 text-blue-100'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-4">
        {activeTab === 'scene' && <ScenePane detail={kamiDetail} agents={agents} objects={objects} selectAgent={selectAgent} />}
        {activeTab === 'history' && <HistoryPane memory={memory} />}
        {activeTab === 'imprints' && <ImprintPane imprints={memory.imprints || []} />}
        {activeTab === 'trace' && <TracePane events={events} />}
      </div>
    </div>
  )
}

function ScenePane({ detail, agents, objects, selectAgent }: { detail: any; agents: any[]; objects: any[]; selectAgent: (id: string) => Promise<void> }) {
  return (
    <div className="space-y-5">
      {detail.archetype?.description && (
        <section className="rounded border border-slate-800 bg-slate-900/60 p-3">
          <h4 className="font-semibold text-slate-100 mb-2">Scene Description</h4>
          <p className="text-slate-400 leading-relaxed">{detail.archetype.description}</p>
        </section>
      )}
      {agents.length > 0 && (
        <section>
          <SectionTitle icon={<Users size={15} />} title="Agents Present" />
          <div className="space-y-2">
            {agents.map((agent) => (
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
          <SectionTitle icon={<Box size={15} />} title="Objects" />
          <div className="space-y-1">
            {objects.map((object) => (
              <div key={object.entity_id} className="px-3 py-2 rounded bg-slate-900/50 border border-slate-800">
                <div className="font-medium text-slate-200">{object.name}</div>
                {object.states && Object.keys(object.states).length > 0 && (
                  <div className="mt-1 text-xs text-slate-500 break-words">
                    {Object.entries(object.states).map(([key, value]) => `${key}: ${value}`).join(' · ')}
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      )}
      {!detail.archetype?.description && !agents.length && !objects.length && <EmptyState text="The scene is empty." />}
    </div>
  )
}

function HistoryPane({ memory }: { memory: any }) {
  return (
    <div className="space-y-5">
      {memory.long_term_memory && (
        <section>
          <SectionTitle icon={<History size={15} />} title="Long-Term Memory" />
          <p className="whitespace-pre-wrap text-slate-300 leading-relaxed">{memory.long_term_memory}</p>
        </section>
      )}
      {memory.summaries?.length > 0 && (
        <section>
          <SectionTitle icon={<ScrollText size={15} />} title="Daily History" />
          <div className="space-y-2">
            {memory.summaries.map((summary: any) => (
              <div key={summary.summary_id} className="rounded border border-slate-800 bg-slate-900/60 p-3">
                <div className="flex items-center justify-between gap-2 text-[11px]">
                  <span className="font-semibold text-blue-300">Tick {summary.tick}</span>
                  <span className="text-slate-500 tabular-nums">{summary.event_count} events · peak {Number(summary.peak_salience || 0).toFixed(2)}</span>
                </div>
                <p className="mt-2 text-slate-300 leading-relaxed">{summary.summary}</p>
              </div>
            ))}
          </div>
        </section>
      )}
      {!memory.long_term_memory && !memory.summaries?.length && <EmptyState text="No consolidated history yet." />}
    </div>
  )
}

function ImprintPane({ imprints }: { imprints: any[] }) {
  return imprints.length > 0 ? (
    <section>
      <SectionTitle icon={<Stamp size={15} />} title="Permanent Imprints" />
      <div className="space-y-2">
        {imprints.map((imprint) => (
          <div key={imprint.imprint_id} className="border-l-2 border-amber-500 bg-slate-900/60 px-3 py-3">
            <div className="flex justify-between gap-2 text-[11px] text-slate-500">
              <span>Tick {imprint.tick} · {imprint.category}</span>
              <span className="tabular-nums">{Number(imprint.importance || 0).toFixed(2)}</span>
            </div>
            <p className="mt-2 text-amber-50 leading-relaxed"><EntityReferenceText text={imprint.fact} /></p>
          </div>
        ))}
      </div>
    </section>
  ) : <EmptyState text="No permanent imprints." />
}

function TracePane({ events }: { events: any[] }) {
  return events.length > 0 ? (
    <section>
      <SectionTitle icon={<Radio size={15} />} title="Scene Event Stream" />
      <div className="space-y-2">
        {events.slice(0, 100).map((event) => (
          <div key={event.event_id} className="px-3 py-2 border-l-2 border-blue-700 bg-slate-900/70">
            <div className="flex items-center justify-between gap-2 text-[11px] mb-1">
              <span className="text-blue-300 font-semibold">Tick {event.tick}</span>
              <span className="text-slate-500 truncate">{event.event_type}</span>
              <span className="text-slate-600 tabular-nums">s={Number(event.salience || 0).toFixed(2)}</span>
            </div>
            <p className="text-slate-300 leading-relaxed"><EntityReferenceText text={event.narrative} /></p>
          </div>
        ))}
      </div>
    </section>
  ) : <EmptyState text="No scene events yet." />
}

function SectionTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return <div className="flex items-center gap-2 mb-2 text-blue-300">{icon}<h4 className="font-semibold text-slate-100">{title}</h4></div>
}

function EmptyState({ text }: { text: string }) {
  return <div className="border border-dashed border-slate-800 p-4 text-center text-slate-500">{text}</div>
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded bg-slate-900 border border-slate-800 px-2 py-2 min-w-0">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 truncate">{label}</div>
      <div className="text-slate-100 font-bold tabular-nums truncate">{value}</div>
    </div>
  )
}
