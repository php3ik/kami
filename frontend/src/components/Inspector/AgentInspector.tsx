import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Brain,
  Fingerprint,
  HeartPulse,
  Lightbulb,
  MapPin,
  MessageCircle,
  Network,
  ScrollText,
  Sparkles,
} from 'lucide-react'
import { useSimStore } from '../../stores/simStore'
import { buildAgentTimeline, classifyThought, digest, getKamiName, makeKamiLookup } from '../../utils/simView'
import EntityReferenceText from './EntityReferenceText'

type AgentTab = 'persona' | 'mind' | 'memory' | 'messages' | 'social' | 'trace'

const tabs: Array<{ id: AgentTab; label: string }> = [
  { id: 'persona', label: 'Persona' },
  { id: 'mind', label: 'Mind' },
  { id: 'memory', label: 'Memory' },
  { id: 'messages', label: 'Messages' },
  { id: 'social', label: 'Social' },
  { id: 'trace', label: 'Trace' },
]

export default function AgentInspector() {
  const { agentDetail, graph, tickLog, selectKami, selectionLoading, selectionError } = useSimStore()
  const [activeTab, setActiveTab] = useState<AgentTab>('mind')
  const kamiLookup = useMemo(() => makeKamiLookup(graph.nodes || []), [graph.nodes])

  useEffect(() => setActiveTab('mind'), [agentDetail?.entity_id])

  if (!agentDetail) return <InspectorState title="Loading agent" body="Preparing the profile..." />
  if (selectionError || agentDetail.error) {
    return <InspectorState title="Could not load agent" body={selectionError || agentDetail.error} tone="error" />
  }

  const arch = agentDetail.archetype || {}
  const timeline = buildAgentTimeline(agentDetail, tickLog).slice(0, 20)
  const currentLocation = agentDetail.location?.kami_id
  const currentLocationName = getKamiName(currentLocation, kamiLookup)
  const latest = timeline[0]
  const isError = classifyThought(latest?.thought) === 'error'

  return (
    <div className="h-full overflow-y-auto bg-slate-950 text-sm">
      <div className="sticky top-0 z-10 border-b border-slate-800 bg-slate-950/95 backdrop-blur">
        <div className="p-4 pb-3">
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
                : isError
                  ? 'bg-rose-950/70 text-rose-200 border-rose-700'
                  : 'bg-emerald-950/70 text-emerald-200 border-emerald-700'
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
        <div className="flex overflow-x-auto px-2" role="tablist" aria-label="Agent detail views">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={activeTab === tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`min-w-[64px] flex-1 border-b-2 px-2 py-2 text-xs font-medium transition-colors ${
                activeTab === tab.id
                  ? 'border-cyan-400 text-cyan-100'
                  : 'border-transparent text-slate-500 hover:text-slate-300'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      <div className="p-4">
        {activeTab === 'persona' && <PersonaPane arch={arch} />}
        {activeTab === 'mind' && <MindPane detail={agentDetail} arch={arch} latest={latest} isError={isError} />}
        {activeTab === 'memory' && <MemoryPane memory={agentDetail.memory || {}} />}
        {activeTab === 'messages' && <MessagesPane communications={agentDetail.communications || {}} agentId={agentDetail.entity_id} />}
        {activeTab === 'social' && <SocialPane detail={agentDetail} />}
        {activeTab === 'trace' && <TracePane timeline={timeline} />}
      </div>
    </div>
  )
}

function PersonaPane({ arch }: { arch: any }) {
  return (
    <div className="space-y-5">
      {arch.background && (
        <section>
          <SectionTitle icon={<Fingerprint size={15} />} title="Background" />
          <p className="text-slate-400 leading-relaxed">{arch.background}</p>
        </section>
      )}
      {arch.traits?.length > 0 && (
        <section>
          <h4 className="font-semibold text-slate-100 mb-2">Traits</h4>
          <div className="flex flex-wrap gap-1.5">
            {arch.traits.map((trait: string) => (
              <span key={trait} className="px-2 py-1 bg-slate-900 border border-slate-800 rounded text-xs text-slate-300">{trait}</span>
            ))}
          </div>
        </section>
      )}
      <TextList title="Desires" values={arch.desires} />
      <TextList title="Fears" values={arch.fears} />
      <TextList title="Private history" values={arch.private_history} />
    </div>
  )
}

function MindPane({ detail, arch, latest, isError }: { detail: any; arch: any; latest: any; isError: boolean }) {
  return (
    <div className="space-y-5">
      <section className="rounded border border-slate-800 bg-slate-900/60 p-3">
        <SectionTitle icon={<Brain size={15} />} title="Current Signal" />
        <p className={`leading-relaxed ${isError ? 'text-rose-200' : 'text-slate-200'}`}>
          <EntityReferenceText text={digest(latest?.thought, 420)} />
        </p>
        {latest?.narrative && (
          <p className="mt-3 border-t border-slate-800 pt-3 text-slate-300 leading-relaxed">
            <EntityReferenceText text={latest.narrative} />
          </p>
        )}
      </section>
      {detail.states && Object.keys(detail.states).length > 0 && (
        <section>
          <SectionTitle icon={<HeartPulse size={15} />} title="Physical State" />
          <div className="grid grid-cols-2 gap-2">
            {Object.entries(detail.states).map(([key, value]) => (
              <div key={key} className="rounded border border-slate-800 bg-slate-900/70 px-2 py-2">
                <div className="text-[11px] uppercase tracking-wider text-slate-500">{key}</div>
                <div className="text-slate-100 font-semibold break-words">{String(value)}</div>
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
                <div className="text-slate-300 mt-1 leading-relaxed">{String(goal)}</div>
              </div>
            ))}
          </div>
        </section>
      )}
      {detail.beliefs?.length > 0 && (
        <section>
          <SectionTitle icon={<Lightbulb size={15} />} title="Beliefs" />
          <div className="space-y-2">
            {detail.beliefs.map((belief: any, index: number) => (
              <div key={`${belief.kind}-${index}`} className="rounded border border-slate-800 bg-slate-900/50 p-2">
                <div className="flex justify-between gap-2 text-xs">
                  <span className="text-amber-200">{belief.kind}</span>
                  <span className="text-slate-500 tabular-nums">{Number(belief.confidence || 0).toFixed(2)}</span>
                </div>
                <div className="mt-1 text-slate-300 break-words">{JSON.stringify(belief.value)}</div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

function MemoryPane({ memory }: { memory: any }) {
  const insights = memory.insights || []
  const active = insights.filter((item: any) => item.status === 'active')
  const archived = insights.filter((item: any) => item.status !== 'active')
  return (
    <div className="space-y-5">
      {memory.life_narrative && (
        <section>
          <h4 className="font-semibold text-slate-100 mb-2">Life Narrative</h4>
          <p className="text-slate-300 leading-relaxed whitespace-pre-wrap">{memory.life_narrative}</p>
        </section>
      )}
      <InsightList title="Active Insights" items={active} />
      <InsightList title="Archived Insights" items={archived} muted />
      {memory.summaries?.length > 0 && (
        <section>
          <SectionTitle icon={<ScrollText size={15} />} title="Daily Summaries" />
          <div className="space-y-2">
            {memory.summaries.map((item: any) => (
              <div key={item.summary_id} className="rounded border border-slate-800 bg-slate-900/60 p-3">
                <div className="text-xs font-semibold text-blue-300">Tick {item.tick}</div>
                <p className="mt-1 text-slate-300 leading-relaxed">{item.summary}</p>
              </div>
            ))}
          </div>
        </section>
      )}
      {memory.episodic?.length > 0 && (
        <section>
          <h4 className="font-semibold text-slate-100 mb-2">Episodic Recall</h4>
          <div className="space-y-2">
            {memory.episodic.map((item: any) => (
              <div key={item.memory_id} className="border-l-2 border-cyan-700 bg-slate-900/60 px-3 py-2">
                <div className="flex justify-between text-[11px] text-slate-500">
                  <span>Tick {item.tick}</span>
                  <span className="tabular-nums">importance {Number(item.importance || 0).toFixed(2)}</span>
                </div>
                <p className="mt-1 text-slate-300 leading-relaxed"><EntityReferenceText text={item.content} /></p>
              </div>
            ))}
          </div>
        </section>
      )}
      {!memory.life_narrative && !active.length && !memory.summaries?.length && !memory.episodic?.length && (
        <EmptyState text="No durable memories yet." />
      )}
    </div>
  )
}

function SocialPane({ detail }: { detail: any }) {
  return detail.relations?.length > 0 ? (
    <section>
      <SectionTitle icon={<Network size={15} />} title="Relations" />
      <div className="space-y-2">
        {detail.relations.map((relation: any, index: number) => (
          <div key={`${relation.from}-${relation.to}-${index}`} className="rounded border border-slate-800 bg-slate-900/60 p-3">
            <div className="text-cyan-200">{relation.type}</div>
            <div className="mt-1 text-slate-400 break-all">{relation.from === detail.entity_id ? relation.to : relation.from}</div>
            {relation.weight && <div className="mt-2 text-xs text-slate-500 break-words">{JSON.stringify(relation.weight)}</div>}
          </div>
        ))}
      </div>
    </section>
  ) : <EmptyState text="No recorded relations." />
}

function MessagesPane({ communications, agentId }: { communications: any; agentId: string }) {
  const channels = communications.channels || []
  if (!channels.length) return <EmptyState text="No communication channels." />
  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <SectionTitle icon={<MessageCircle size={15} />} title="Communications" />
        <span className="text-xs tabular-nums text-slate-500">
          {communications.unread_count || 0} unread
        </span>
      </div>
      {channels.map((channel: any) => {
        const contacts = (channel.participants || [])
          .filter((participant: any) => participant.entity_id !== agentId)
          .map((participant: any) => participant.name)
          .join(', ')
        return (
          <section key={channel.channel_id} className="border-t border-slate-800 pt-4 first:border-t-0 first:pt-0">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h4 className="font-semibold text-slate-100 break-words">{contacts || channel.kind}</h4>
                <div className="mt-1 text-[11px] text-slate-500 break-all">{channel.channel_id}</div>
              </div>
              <div className="shrink-0 text-right text-[11px]">
                <div className="text-cyan-300">{channel.kind}</div>
                {channel.unread_count > 0 && <div className="mt-1 text-amber-300">{channel.unread_count} unread</div>}
                {channel.call_state && <div className="mt-1 text-emerald-300">call {channel.call_state}</div>}
              </div>
            </div>
            <div className="mt-3 space-y-2">
              {(channel.messages || []).length > 0 ? channel.messages.map((message: any) => {
                const outgoing = message.sender_id === agentId
                const unread = !outgoing && !message.read_at_tick
                return (
                  <div
                    key={message.message_id}
                    className={`border-l-2 px-3 py-2 ${
                      unread
                        ? 'border-amber-500 bg-amber-950/20'
                        : outgoing
                          ? 'border-blue-700 bg-blue-950/20'
                          : 'border-slate-700 bg-slate-900/50'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2 text-[11px]">
                      <span className={outgoing ? 'text-blue-300' : 'text-slate-300'}>
                        {outgoing ? 'You' : message.sender_name}
                      </span>
                      <span className="shrink-0 tabular-nums text-slate-500">Tick {message.sent_at_tick}</span>
                    </div>
                    <p className="mt-1 whitespace-pre-wrap break-words text-slate-200 leading-relaxed">{message.content}</p>
                    <div className="mt-1 flex flex-wrap gap-x-3 text-[10px] text-slate-600">
                      {message.kind !== 'message' && <span>{message.kind}</span>}
                      {message.delivery_mode && <span>{message.delivery_mode}</span>}
                      {message.read_at_tick !== null && message.read_at_tick !== undefined && <span>read tick {message.read_at_tick}</span>}
                    </div>
                  </div>
                )
              }) : <div className="text-xs text-slate-600">No messages yet.</div>}
            </div>
          </section>
        )
      })}
    </div>
  )
}

function TracePane({ timeline }: { timeline: any[] }) {
  return timeline.length > 0 ? (
    <section>
      <SectionTitle icon={<ScrollText size={15} />} title="Tick Timeline" />
      <div className="space-y-2">
        {timeline.map((row: any) => {
          const rowError = classifyThought(row.thought) === 'error'
          return (
            <div key={`${row.tick}-${row.event_type || 'thought'}`} className={`rounded border p-3 ${
              rowError ? 'border-rose-800 bg-rose-950/30' : 'border-slate-800 bg-slate-900/60'
            }`}>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-semibold text-blue-300">Tick {row.tick}</span>
                {row.event_type && <span className="text-[11px] text-slate-500">{row.event_type}</span>}
              </div>
              {row.thought && <p className={rowError ? 'text-rose-200' : 'text-purple-100'}><EntityReferenceText text={row.thought} /></p>}
              {row.narrative && <p className="mt-2 border-t border-slate-800 pt-2 text-slate-300"><EntityReferenceText text={row.narrative} /></p>}
            </div>
          )
        })}
      </div>
    </section>
  ) : <EmptyState text="No trace entries yet." />
}

function InsightList({ title, items, muted = false }: { title: string; items: any[]; muted?: boolean }) {
  if (!items.length) return null
  return (
    <section>
      <h4 className="font-semibold text-slate-100 mb-2">{title}</h4>
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.insight_id} className={`rounded border border-slate-800 p-3 ${muted ? 'bg-slate-950/50 text-slate-500' : 'bg-slate-900/60 text-slate-300'}`}>
            <p className="leading-relaxed">{item.content}</p>
            <div className="mt-2 text-[11px] text-slate-500">strength {Number(item.strength || 0).toFixed(2)} · tick {item.last_reinforced_tick}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

function TextList({ title, values }: { title: string; values?: string[] }) {
  if (!values?.length) return null
  return (
    <section>
      <h4 className="font-semibold text-slate-100 mb-2">{title}</h4>
      <ul className="space-y-1 text-slate-400">
        {values.map((value) => <li key={value} className="border-l-2 border-slate-800 pl-3 py-1">{value}</li>)}
      </ul>
    </section>
  )
}

function SectionTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return <div className="flex items-center gap-2 mb-2 text-cyan-300">{icon}<h4 className="font-semibold text-slate-100">{title}</h4></div>
}

function EmptyState({ text }: { text: string }) {
  return <div className="border border-dashed border-slate-800 p-4 text-center text-slate-500">{text}</div>
}

function InspectorState({ title, body, tone = 'neutral' }: { title: string; body: string; tone?: 'neutral' | 'error' }) {
  return (
    <div className="h-full bg-slate-950 p-4 text-sm">
      <div className={`rounded border p-4 ${tone === 'error' ? 'border-rose-800 bg-rose-950/30 text-rose-200' : 'border-slate-800 bg-slate-900/60 text-slate-300'}`}>
        <div className="font-semibold text-slate-100">{title}</div>
        <div className="mt-2 leading-relaxed">{body}</div>
      </div>
    </div>
  )
}
