# Kami Simulation — Architectural Specification and Implementation Plan

> A self-contained document for an autonomous coding agent to understand, design, and implement an LLM-driven multi-agent town simulation. Read this document end-to-end before writing any code. Every architectural decision below has a reason explained inline — do not "optimize" or skip parts without understanding the trade-off being made.

---

## 0. Document scope and how to use it

This spec describes a research-grade LLM-based simulation of a small town (~100 inhabitants) where autonomous agents live their lives in a topological world represented as a graph of "kami" (place-spirits, each a stateful LLM-rendered location). The system is designed to be **economically feasible** (a multi-day simulation should cost dollars, not thousands of dollars), **narratively coherent over long horizons**, and **scientifically usable** as a research instrument.

The document is divided into:
- **Part I** — concept and design philosophy (read once, internalize)
- **Part II** — architecture of each subsystem (reference while implementing)
- **Part III** — concrete implementation plan with phases, MVP definition, and validation tests
- **Part IV** — known risks, anti-patterns, and tuning knobs

When implementing, always start from Part III. Refer back to Part II for component-level detail. Refer to Part I when unsure why something is structured a particular way.

---

# PART I — CONCEPT AND PHILOSOPHY

## 1.1 The core idea

A town is modelled as a **graph of kami**. A kami is a place-spirit: a stateful node corresponding to a location (a park, an apartment, a corridor, a shop), backed by an LLM that acts as a **game master** for whatever happens inside it. Kami are connected by edges representing physical adjacency or containment ("apartment 4B is inside building 17", "the park entrance is adjacent to Main Street").

Agents (people, animals) physically inhabit one kami at a time. When something happens in a kami, the LLM call for that kami renders the scene, resolves agent intents, and emits events. Events propagate to neighbouring kami with a one-tick delay (the speed of causality).

The whole system is driven by a **discrete tick scheduler**. One tick ≈ one in-sim minute by default. Crucially, **only kami that need rendering are rendered** — empty, quiet kami are skipped, their history filled in later with cheap summaries. This is the single most important economic property of the system.

## 1.2 Why kami and not "agents talking to each other directly"

Prior work (Park et al., "Generative Agents", 2023) modelled each agent as an independent LLM with its own context. This works for ~25 agents and breaks economically beyond that, because every agent must reason about every relevant other agent in its perceptual range.

The kami approach inverts this: **the place is the primary computational unit, not the agent**. A kami with 4 agents inside renders all 4 in one LLM call (the kami acts as GM for the scene). A kami with 0 agents is not rendered at all. Population scales sublinearly with active kami, not linearly with agents.

## 1.3 Two foundational invariants

Everything else in this spec serves two principles. If you find yourself violating either, stop and reconsider.

**Invariant 1 — Canon vs. perspective separation.** There is exactly one source of truth for "what is actually true in the world": the structured **FactStore**. Everything LLMs generate as free text is *interpretation* of canon, never canon itself. A kami's narrative description of a scene cannot mutate the world — only structured tool calls can. An agent's beliefs about the world are stored separately from canon and may legitimately diverge from it (this is how lies, mistakes, gossip, and outdated knowledge become possible).

**Invariant 2 — Lazy evaluation by event activation.** A kami is rendered on a tick if and only if (a) an agent is inside it, (b) a scheduled event fires, (c) an inbound propagated event has salience above its wake threshold, or (d) it is due for forced refresh (rare). Otherwise it stays idle. Idle periods are filled in by cheap consolidation summaries, not per-tick LLM calls.

## 1.4 Three failure modes the architecture is designed to prevent

1. **Canon drift.** LLMs hallucinate objects, forget what was where, contradict themselves over time. Prevented by FactStore + tool-call mediation + validation between proposes and commits.
2. **Behavioural looping / persona collapse.** Without reflection, agents become reactive automata that repeat patterns. Their voices drift toward generic LLM tone. Prevented by MemoryConsolidator (sleep-time reflection) + persona re-injection + importance-weighted memory retrieval.
3. **Cost explosion.** Naive implementations cost hundreds of dollars per simulation-day. Prevented by lazy kami activation, two-tier model routing (cheap/strong), batched cognition calls, prompt caching of stable prefixes, and adaptive time-stepping.

---

# PART II — ARCHITECTURE

## 2.1 Component map

```
                    ┌─────────────────────────┐
                    │   TickScheduler (BSP)   │
                    └────────────┬────────────┘
                                 │
        ┌────────────────────────┼────────────────────────┐
        │                        │                        │
        ▼                        ▼                        ▼
  ┌───────────┐          ┌─────────────┐          ┌──────────────┐
  │KamiWorker │◄─────────│ FactStore   │─────────►│AgentCognition│
  │  (LLM)    │  reads/  │ (canon DB)  │  reads   │   Worker     │
  │           │  writes  │             │  via     │   (LLM)      │
  │           │  via     │             │  tools   │              │
  │           │  tools   │             │          │              │
  └─────┬─────┘          └──────┬──────┘          └──────┬───────┘
        │                       │                        │
        │                       ▼                        │
        │                ┌────────────┐                  │
        │                │ EventBus   │                  │
        └───────────────►│            │◄─────────────────┘
                         └─────┬──────┘
                               │
                ┌──────────────┼───────────────┐
                ▼              ▼               ▼
         ┌───────────┐  ┌────────────┐  ┌─────────────┐
         │SpatialGr. │  │CommsLayer  │  │MemoryConsol.│
         │(NetworkX) │  │ (channels) │  │  (nightly)  │
         └───────────┘  └────────────┘  └─────────────┘
                               │
                               ▼
                       ┌──────────────┐
                       │WorldBuilder  │  (one-shot bootstrap)
                       └──────────────┘

                 ┌─────────────────────────┐
                 │   Frontend (React)      │
                 │   ← WebSocket stream    │
                 └─────────────────────────┘
```

## 2.2 FactStore — the canonical world state

**Purpose.** The single source of truth for what exists, where, in what state. All mutations happen through validated tool calls in transactions. Append-only with temporal versioning where it matters.

**Backend.** Postgres (SQLAlchemy + Alembic). For MVP, SQLite is acceptable. The schema is what matters, not the engine.

**Tables (logical schema).**

- `entities(entity_id, kind, canonical_name, archetype JSON, created_at_tick, created_by_event)` — registry of everything that exists. `kind` ∈ {agent, object, kami, animal, plant, vehicle, document, channel}.
- `locations(entity_id, kami_id, container_id NULL, since_tick, valid_until_tick NULL)` — temporal location. One row per entity has `valid_until_tick IS NULL` (the current location). On move, the old row gets `valid_until_tick` and a new one is inserted.
- `ownership(entity_id, owner_id, since_tick, valid_until_tick NULL)` — same temporal pattern.
- `physical_state(entity_id, attribute, value JSON, since_tick, valid_until_tick NULL)` — attribute ∈ controlled vocabulary {integrity, cleanliness, temperature, hp, hunger, fatigue, locked, ...}.
- `relations(from_entity, to_entity, rel_type, weight JSON, since_tick, valid_until_tick NULL)` — `rel_type` ∈ {knows, trusts, owes, married_to, employs, fears, has_contact_via, ...}.
- `events(event_id, tick, kami_id, event_type, participants JSON, payload JSON, salience FLOAT, narrative TEXT, causes JSON)` — append-only event log. Source of truth for replay.
- `agent_beliefs(belief_id, agent_id, kind, target_entity, attribute, believed_value JSON, confidence FLOAT, since_tick, source_event_id)` — subjective belief store, one per agent. Mirror structure to canon tables but per-perceiver. **This is what AgentCognitionWorker reads, never the canon directly.**
- `schedules(schedule_id, fires_at_tick, kami_id, event_template JSON)` — pre-planned events.
- `channels(channel_id, kind, participants JSON, subscribers JSON, medium_properties JSON, created_at_tick, metadata JSON)` — communication channels (see CommsLayer §2.7).
- `messages(message_id, channel_id, sender_id, content TEXT, sent_at_tick, salience FLOAT)`
- `read_receipts(message_id, agent_id, read_at_tick)`

**Concurrency contract.** All mutations go through the WriteCommitter (§2.5). FactStore tables use SERIALIZABLE isolation for safety, but the single-writer pattern means real conflicts are rare.

**Validation rules enforced at the FactStore tool level (not in LLM prompts).**
- An entity has at most one current `locations` row.
- `physical_state` transitions are validated against a small rule table for hard cases (`integrity: broken → intact` requires explicit `repair` event); soft attributes are unrestricted.
- `create_entity` is rate-limited per tick per kami to prevent LLM spam.
- Foreign-key references checked for every tool call.

## 2.3 KamiWorker — scene rendering as game master

**Purpose.** For each active kami on each tick, render the scene: collect context, call LLM, parse structured output into FactStore mutations and emitted events.

**Tool surface (the LLM may only call these — no free-text mutation).**
- `query_state(kami_id, filters)` — read entities, states, relations in scope.
- `move_entity(entity_id, to_kami_id, container_id?, reason_event_id)`
- `change_state(entity_id, attribute, new_value, reason_event_id)`
- `transfer_ownership(entity_id, new_owner, reason_event_id)`
- `create_entity(kind, archetype, initial_location, reason_event_id)` — quota-limited.
- `destroy_entity(entity_id, reason_event_id)` — soft delete.
- `update_relation(from, to, rel_type, weight_delta, reason_event_id)`
- `emit_event(event_type, participants, payload, salience, narrative)` — **mandatory at end of every tick**, even for `idle` events.
- `publish_broadcast(text, salience, sensory_channel)` — emit a compressed digest snippet for neighbour consumption (see §2.5 propagation).

**Context assembly (see §2.3.1 for the full prompt layout).** Built deterministically from FactStore + EventBus inputs. Never includes anything an LLM "remembers" from previous calls — every call is stateless.

**Model routing.** Default: cheap-tier model (Haiku-class). Escalate to strong-tier (Sonnet-class) when scene contains more than 2 agents *and* salience of pending intents is above a threshold. This split is critical for budget — most ticks should run on cheap.

### 2.3.1 KamiWorker prompt layout (in order)

The order matters for prompt caching. Stable prefix first, dynamic data last.

1. **System prompt** (~1.5k tokens, cached forever): role description, tool schemas, salience scale, narrative rules.
2. **Kami identity** (~300–600 tokens, cached for kami lifetime): canonical description from WorldBuilder.
3. **Kami long-term memory** (~400 tokens, cached between consolidations): hierarchically compressed history.
4. **Recent kami events** (~300–600 tokens): last 5–15 events as bullets.
5. **Neighbor digest** (~200–500 tokens): one-line broadcast snippets from each adjacent kami, filtered by salience and sensory channel.
6. **Present entities (YAML, structured)** (~400–800 tokens): authoritative inventory of agents, objects, animals, ambient. **This block is the anti-drift anchor — the LLM must respect it.** Generated mechanically from `query_state`, never by an LLM.
7. **Agent intents** (~100–300 tokens): collected from AgentCognitionWorker calls earlier in the tick.
8. **Pending external events** (~50–200 tokens): inbound from CommsLayer, schedules.
9. **Task instruction** (~100 tokens): "Resolve this tick. Call tools for state changes. Finish with `emit_event`."

Total typical input: 4–6k tokens. Output: 500–1500 tokens (tool calls + final narrative).

## 2.4 AgentCognitionWorker — subjective thinking

**Purpose.** For each active agent on each tick, generate thoughts and intents. Distinct from KamiWorker in one critical way: **agents must not know what they cannot know**. This is the hardest engineering problem in the system.

**Tool surface.**
- `recall(agent_id, query, k)` — vector retrieval from episodic memory.
- `perceive(agent_id)` — returns the *filtered* scene from the kami: only entities the agent can sense (visibility, lighting, attention, recognition), with names resolved only for agents in the social graph.
- `intend(action_type, target?, params)` — declares an intent. **Agents do not mutate canon directly.** The kami judges intents and applies them in its resolution phase.
- `update_belief(...)` — updates the agent's subjective model after perception.

**Context layout (in order).**

1. **System prompt** (~800 tokens, cached): "You are a person, not a narrator. Use only what is in YOU and WHAT_YOU_PERCEIVE. Do not reference names not in your social graph. Do not know things not in your memory."
2. **Persona** (~600–1000 tokens, cached for agent lifetime): name, age, background, traits, fears, desires, voice register, speech examples, personal beliefs about the world (which may be factually false).
3. **Goals hierarchy** (~200–400 tokens): life goals → seasonal → daily → current micro-goal.
4. **Emotional state** (~80 tokens, structured YAML): dominant emotion, intensity, physiology, last trigger.
5. **Relevant memories** (~400–800 tokens): top-k from episodic store, ranked by gibrid score (recency × relevance × importance × **social_salience**, where social_salience boosts memories involving people present in the current scene).
6. **Semantic insights** (~200 tokens): consolidated beliefs from MemoryConsolidator. Always included in full because they're few and dense.
7. **Social graph slice** (~150–400 tokens): only edges to people present in scene, in retrieved memories, or in pending comms.
8. **WHAT_YOU_PERCEIVE** (~300–600 tokens): filtered scene. Generated by KamiWorker, not by agent's LLM.
9. **Recent personal episodic buffer** (~200–400 tokens): last 3–5 ticks of this agent's own actions/thoughts.
10. **Pending communications** (~50–200 tokens): unread inbox digest.
11. **Task instruction** (~100 tokens): "Think as [name]. Brief inner monologue (1–3 sentences in their voice). Then call `intend`."

Total: 4–5k input, 300–600 output.

**Epistemic containment — three layers.** No single layer is sufficient.
- **Structural**: post-hoc validator checks that any name mentioned in agent narrative exists in the agent's social_graph or current perceive output. If not, regenerate.
- **Prompt-engineering**: explicit few-shot examples in the system prompt showing bad and good behaviour.
- **Model choice**: prefer mid-tier models for agent cognition. Less world-knowledge means less leakage. Counter-intuitively, weaker models often produce more in-character agents than frontier models.

**Model routing.** Default: cheap-tier. Reserve strong-tier for reflection (in MemoryConsolidator), not per-tick cognition.

## 2.5 TickScheduler — BSP-style coordination

**Purpose.** Coordinate parallel rendering of many kami and many agents per tick while preserving causal consistency.

**Two-phase tick model (Bulk Synchronous Parallel).**

For each tick `T`:

1. **READ phase.** All active kami and agents read a snapshot of FactStore + inbound events from EventBus. No writes. Everyone sees the same "past" — the state as committed at end of tick `T-1`.
2. **COMPUTE phase.** Parallel LLM calls. First all active agents in all active kami generate intents (parallel). Synchronization barrier. Then all active kami resolve their scenes given those intents (parallel). Each call returns a `propose-list` of tool calls — *not yet applied*.
3. **WRITE phase.** Single-threaded `WriteCommitter` consumes proposes from a queue and applies them in deterministic order. Conflict resolution rules:
   - **Initiative-based** for action ordering within a kami: function of agent reaction speed × (1 − fatigue) × surprise factor. Computed without LLM. Hash-based tiebreak.
   - **Ownership-based** for entities: each entity belongs to exactly one kami per tick (the kami it is currently located in). Only the owner kami may propose mutations on it. Cross-kami effects go through events with one-tick delay.
   - **Schema constraints** for create/destroy: failed proposes are returned to next-tick context as `intent_failed` feedback.
4. **PROPAGATE phase.** New events from this tick are dispatched to neighbouring kami via EventBus, with **a fixed +1 tick delay** (causality lag). Salience is attenuated by edge type (wall vs open space vs phone connection).

**Activity detection.** Before each tick, `ActivityDetector` walks FactStore + EventBus to determine the active set:
- Kami with at least one agent inside.
- Kami with a scheduled event firing this tick.
- Kami receiving an inbound propagated event with salience > wake threshold for that kami type.
- Kami due for `forced_refresh` (configurable, default once per 100 ticks).
- Agents in those kami, plus agents with pending CommsLayer wake-triggers.

**Adaptive time-stepping.** Two modes:
- **Dense mode** (1 in-sim minute per tick): default when global activity is high.
- **Sparse mode** (jump to next scheduled event): when no kami is active, the scheduler advances time directly to the next schedule entry. This collapses uneventful nights into a handful of ticks.

**Latency handling.** LLM calls have soft time-out. On time-out, the kami's propose-list defaults to empty, narrative to a cheap fallback ("It was quiet."), and a retry runs asynchronously. Never block the scheduler on a single slow call.

**Determinism harness.** Optional mode where all RNG, parallel completion order, and LLM temperature are seeded. Required for replay and debugging. Off by default.

**Implementation.** `asyncio` event loop with semaphores for rate limits. LLM calls are I/O-bound — use async, not threads. Single-consumer WriteCommitter task drains the proposal queue. Use Postgres `LISTEN/NOTIFY` (MVP) or Redis Streams (later) for the event bus.

**Movement between kami.** A transition takes exactly one tick. On tick `N`, agent declares intent to move to neighbour. On tick `N+1`, agent is in `kami_transit` (special non-rendered placeholder). On tick `N+2`, agent appears in destination kami. Multi-step paths cascade naturally. This is also physically realistic.

## 2.6 MemoryConsolidator — sleep-time reflection

**Purpose.** Compress raw episodic memory into semantic insights, evolve goals, prevent token-explosion. Runs once per in-sim day per agent (when the agent is asleep, or forced if non-sleeping).

**Four-level memory hierarchy per agent.**
- **L0 — raw episodic** (vector store, append-only): timestamps, scenes, perceptions, thoughts, actions, importance scores.
- **L1 — daily summaries**: ~150–300 tokens per day, generated nightly.
- **L2 — semantic insights**: hard cap (e.g. 30–50 active per agent), included in full in every cognition call. Beliefs about people, self, world. Each insight has a strength score and decays without reinforcement.
- **L3 — life narrative**: ~500–1000 tokens, the "who I am" block. Updated weekly or on major events.

**Consolidation phases (sequential per agent).**
1. **Daily summarization** (cheap LLM): takes raw episodes of the past day + persona + goals → daily summary + 3–7 candidate insights.
2. **Insight integration** (mid-tier LLM): for each candidate, decide: new insight, reinforcement, or contradiction-driven modification of an existing insight. Old insights are *modified with provenance*, not deleted, to preserve psychological inertia. Tools: `add_insight`, `strengthen_insight`, `modify_insight`, `archive_insight`.
3. **Goal reflection** (mid-tier LLM): given updated insights + current goal stack + day summary → produce *deltas* to the goal hierarchy.
4. **Emotional rebalancing** (algorithmic, no LLM): exponential decay of accumulated emotional load, with persistence for high-importance traumatic events.

**Phase 5 — Life narrative update (frontier LLM, weekly):** the most expensive call, rewrites the L3 block.

**Drift control.**
- Hard cap on active L2 insights with LRU eviction.
- Insights without reinforcement for N days decay and eventually archive.
- Weekly "challenge pass": LLM is given a random sample of old insights and asked "is this still true given recent events?" — counters confirmation bias.
- Embedding-based merge: similar insights get merged in phase 2 instead of accumulating duplicates.

**Kami consolidation (separate, simpler).** Once per in-sim day per kami: one cheap LLM call compresses the day's events into a paragraph for the kami's long-term memory. Plus an `imprint_on_kami(kami_id, fact)` tool for high-importance events that become permanent place properties ("the bar where someone was shot").

## 2.7 CommsLayer — non-local communication

**Purpose.** Enable phones, messages, broadcasts without breaking the locality-based economy of the kami model.

**Channels as first-class entities.** Not edges in the kami graph. A channel has its own lifecycle, participants, persistence, medium properties. Channel kinds: `phone_dm`, `sms`, `messenger_dm`, `group_chat`, `email`, `radio_broadcast`, `tv_broadcast`, `public_post`, `letter_mail`.

**Message lifecycle (chat-channel case).**
1. Sender's kami is active. Sender's agent calls `intend(send_message, channel_id, content)`.
2. Sender's KamiWorker validates (sender has channel in `has_contact_via` relations) and emits `propose: emit_message`.
3. WriteCommitter inserts message into `messages` table.
4. **Wake logic per recipient** (the heart of the economy):
   - **Active attention**: recipient is in active kami and currently using phone — delivered immediately, in their context next tick.
   - **Ambient awareness**: recipient is in active kami but not using phone — message becomes an ambient_event (notification sound) in their kami; the kami's standard perception logic decides whether the agent notices.
   - **Dormant**: recipient is in idle kami — message goes to `pending_inbox`. Kami stays idle. No wake. Will be read when the agent next becomes active naturally, or when a forced wake fires (high-priority caller, scheduled phone-check).
5. Read receipts created only when the agent's cognition actually processes the message. **Until read, the message content is not in the agent's `agent_beliefs`.** This prevents telepathy.

**Synchronous calls.** A `make_call` intent creates a channel in `ringing` state, force-wakes the recipient kami next tick (the phone is physically ringing — high-salience ambient event). If the recipient takes the call, both kami stay synchronously active for the duration. If not, the channel transitions to `missed_call` in the inbox.

**Broadcasts.**
- **Local broadcasts** (radio in cafe, TV in living room): emit `ambient_event` of high volume into the specific kami where the receiver device sits. No new mechanism needed.
- **Social media feeds**: pull-based. Agents do not get push notifications from feeds. They must `intend(check_feed, platform)` as an action, and only then receive a digest of recent posts in their next-tick context.

**Group chat rate limiting.** Channels have `max_active_participants_per_tick` and `conversation_cooldown` to prevent activation cascades from group chats. Latecomers see "the whole conversation" later, as in real life.

**Gossip diffusion** is the emergent phenomenon this layer is built for: information jumping across the social graph via messages, reaching distant parts of town within ticks. This is the payoff.

## 2.8 SpatialGraph

NetworkX graph held in memory, persisted to FactStore. Nodes = kami. Edges have type {adjacent, contains, transit_route} and properties for sensory channel attenuation (wall blocks visual, partially blocks audio; open door blocks nothing). Used by:
- TickScheduler for activity propagation.
- KamiWorker context assembly for neighbour digest selection.
- WorldBuilder for topology validation.
- Frontend for the main visualization.

## 2.9 EventBus

For MVP: Postgres `LISTEN/NOTIFY` plus an `events_outbox` table consumed by the propagator. Later: Redis Streams. Events are routed to (a) neighbouring kami in SpatialGraph (with salience attenuation), (b) channel participants in CommsLayer (with wake logic). Always with one-tick causality lag.

## 2.10 WorldBuilder — bootstrap pipeline

**Purpose.** Generate a complete, internally consistent starting world from a single user prompt.

**Five sequential cascades, with validation between each.**

1. **World seed** (1 frontier-model call): world bible — geography, history, economy, demographics, social rifts, cultural tone. ~1–2k tokens output. Cached as system prompt material for later cascades.
2. **Spatial decomposition** (recursive, mid-tier): town → districts → buildings → floors → rooms. Each call sees the bible + already-generated siblings + landmarks list, to maintain incremental consistency. Returns kami specs + relative topology. NetworkX graph validated for connectivity at each level.
   - **Step 2.5 — Slot inventory**: algorithmic walk over the kami tree counts capacity (residential slots, work slots, public slots) and generates a demographic budget for cascade 3.
3. **Population** (batched mid-tier, ~5 personas per call, ~20 calls for 100 agents): each batch sees the bible + already-generated personas (short summaries) + diversity quotas. Generates persona block + initial home + role + relationship slot-requirements. **Diversity injection**: explicit negative-space lists + persona seeds (rare hobbies, scars, quirks, language tics) randomly mixed in to combat archetype bias.
4. **Social fabric** (mixed): algorithmic part walks SpatialGraph and AgentRegistry to compute spatial proximity-based candidate edges (neighbours, coworkers). LLM part (cheap, batched 10–20 pairs per call) takes candidates with shared-context info and generates relationship type, strength, origin story.
5. **Backstory injection** (cheap, per-agent): generate 10–20 episodic memories per agent covering main relationships and current goals, plus initial L3 life narrative. Memories tagged `pre_sim`, with importance scores from rules.

**Validators between cascades.**
- After 2: graph connectivity, landmark materialization, no orphan branches.
- After 2.5: demographic budget matches bible.
- After 3: every persona has valid home, critical work slots filled, no phantom references.
- After 4: graph symmetry, no isolated clusters, plausible degree distribution.
- After 5: cheap LLM-judge sample-checks backstory consistency with persona and relationships.

**Cost.** ~$5–20 of API spend for a 100-agent town. One-time cost amortized over the entire simulation lifetime. **Do not cheap out here** — bootstrap quality determines simulation quality for thousands of subsequent ticks.

## 2.11 Frontend — research instrument

**NOT** a game UI. NOT a Sims-like map. A research tool with three usage modes (ethnographer, system observer, debugger), sharing a three-column layout:

- **Left**: kami tree, agent search, channel list, global time controls (play/pause/step/jump).
- **Centre**: kami graph visualization (Cytoscape.js, force-directed). Node size = agent count, colour = activity, shape = kami kind. Overlays: comms/info flow, agent trajectories.
- **Right**: inspector panel (the deepest part of the UI). Tabs depending on selection:
  - **Kami selected**: Now / Recent / History / Neighbors.
  - **Agent selected**: Persona / Mind / Memory / Social / Trace.
  - **Event selected**: causal graph up/down, participants, narrative, state changes.

**Time-travel**: the `events` table is append-only and complete, so any past tick can be reconstructed. Scrub backward. Branch from any past tick to fork the simulation.

**Global mood strip** (top): in-sim time, active kami count, active agents, events/tick, **tokens/tick**, **dollar counter**. The dollar counter is non-negotiable — it keeps the researcher honest about cost.

**Activity heatmap** (bottom): horizontal timeline with activity intensity. Click to jump. Optional per-agent multi-track filter.

**Stack.** React + TypeScript, Tailwind, Cytoscape.js for the graph, WebSocket stream from FastAPI backend, Zustand for state. **No 3D, no WebGL, no game engine.** Plain web.

**Interventions.** Researcher may inject events, inject messages, edit goals, force-wake a kami, or pause-and-edit FactStore. Every intervention is logged as an `intervention` event so post-mortem analysis can distinguish simulation-native behaviour from researcher tampering.

---

# PART III — IMPLEMENTATION PLAN

## 3.1 Repository structure

```
kami-sim/
├── pyproject.toml
├── README.md
├── KAMI_SIMULATION_SPEC.md          ← this file
├── backend/
│   ├── pyproject.toml
│   ├── kami_sim/
│   │   ├── __init__.py
│   │   ├── factstore/               ← Part II §2.2
│   │   │   ├── models.py            (SQLAlchemy)
│   │   │   ├── tools.py             (validated tool functions)
│   │   │   └── migrations/
│   │   ├── kami_worker/             ← §2.3
│   │   │   ├── worker.py
│   │   │   ├── prompt_builder.py
│   │   │   └── system_prompts/
│   │   ├── agent_worker/            ← §2.4
│   │   │   ├── worker.py
│   │   │   ├── prompt_builder.py
│   │   │   └── containment.py       (epistemic validators)
│   │   ├── scheduler/               ← §2.5
│   │   │   ├── tick_scheduler.py
│   │   │   ├── activity_detector.py
│   │   │   ├── write_committer.py
│   │   │   └── conflict_resolver.py
│   │   ├── memory/                  ← §2.6
│   │   │   ├── consolidator.py
│   │   │   ├── episodic_store.py    (vector store wrapper)
│   │   │   └── insight_manager.py
│   │   ├── comms/                   ← §2.7
│   │   │   ├── channels.py
│   │   │   ├── wake_logic.py
│   │   │   └── inbox.py
│   │   ├── spatial/                 ← §2.8
│   │   │   └── graph.py
│   │   ├── eventbus/                ← §2.9
│   │   │   └── bus.py
│   │   ├── world_builder/           ← §2.10
│   │   │   ├── cascades/
│   │   │   │   ├── seed.py
│   │   │   │   ├── decompose.py
│   │   │   │   ├── populate.py
│   │   │   │   ├── social.py
│   │   │   │   └── backstory.py
│   │   │   └── validators.py
│   │   ├── llm/
│   │   │   ├── client.py            (model router: cheap/strong tiers)
│   │   │   ├── caching.py           (prompt caching wrapper)
│   │   │   └── budget.py            (cost tracking)
│   │   ├── config.py
│   │   └── api/                     (FastAPI server)
│   │       ├── server.py
│   │       └── websocket.py
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── slice/                   (vertical slice harness)
└── frontend/                        ← §2.11
    ├── package.json
    ├── src/
    │   ├── App.tsx
    │   ├── components/
    │   │   ├── KamiGraph.tsx
    │   │   ├── Inspector/
    │   │   ├── TimeControls.tsx
    │   │   └── MoodStrip.tsx
    │   ├── stores/                  (Zustand)
    │   └── api/                     (WebSocket client)
    └── public/
```

## 3.2 Phased delivery — what to build, in what order

**Build the vertical slice first. Do not build the full system in one pass.** Each phase produces something runnable and testable on its own.

### Phase 0 — Project skeleton (½ day)
- Repo layout per §3.1.
- Python project with `uv` or `poetry`. Dependencies: `sqlalchemy`, `alembic`, `fastapi`, `uvicorn`, `anthropic` (or chosen provider SDK), `networkx`, `pydantic`, `pytest`, `chromadb` (or `qdrant-client`).
- Frontend skeleton with Vite + React + TS + Tailwind.
- `.env.example` with `ANTHROPIC_API_KEY`, DB URL, model names for cheap/strong tiers.

### Phase 1 — FactStore foundation (2–3 days)
- SQLAlchemy models for all tables in §2.2.
- Alembic migration for SQLite (MVP) and Postgres (production).
- Tool functions in `factstore/tools.py` with validation (single-current-row constraints, schema checks, transition rules).
- Unit tests for every tool: success case, validation failure case, concurrent-write resolution.
- **Deliverable**: a Python REPL session can create a kami, place an agent in it, move them, transfer an object, query state, and the assertions hold.

### Phase 2 — LLM client and budget (1 day)
- Wrapper around Anthropic API with two tier names (`cheap`, `strong`) configurable.
- Prompt-caching support (use `cache_control` markers on stable prefix segments).
- Cost tracker that records tokens in/out + dollar estimate per call, aggregated per-component (KamiWorker, AgentWorker, Consolidator, etc.).
- **Deliverable**: every LLM call passes through the wrapper, cost is logged.

### Phase 3 — Vertical slice MVP (1 week)
The single most important phase. Build the smallest possible end-to-end loop.

- **Hand-author**, do not generate, a minimal world: 5 kami in a small graph, 3 agents with hand-written personas, basic objects.
- KamiWorker: prompt builder (§2.3.1) and worker function. Use cheap-tier model only for now.
- AgentCognitionWorker: prompt builder (§2.4) and worker function. Cheap-tier.
- TickScheduler: minimal version. No adaptive time-stepping. No kami-transit. No CommsLayer. Single tick rate. Two-phase tick (READ/COMPUTE/WRITE/PROPAGATE).
- WriteCommitter with initiative-based ordering.
- A CLI `python -m kami_sim.run_slice --ticks 200` that runs the slice and dumps a JSON log of every tick.
- **Deliverable**: 200 ticks run end-to-end without crashing. The log is human-readable. **Run the validation tests in §3.3 before continuing.**

### Phase 4 — Memory and reflection (3–4 days)
- Episodic store with embeddings (chromadb).
- `recall()` tool with hybrid retrieval (recency + relevance + importance + social_salience).
- MemoryConsolidator with all four phases. Run nightly (in-sim) for each agent.
- Re-run vertical slice for 5 in-sim days. Read agent diaries. Validate they read like one person's life.

### Phase 5 — WorldBuilder (3–5 days)
- Five cascades, each as its own module.
- Validators between cascades.
- Diversity injection mechanism for cascade 3.
- CLI `python -m kami_sim.build_world --prompt "..." --output world.json` that produces a complete starting state, importable into FactStore.
- **Deliverable**: a 100-agent town generated in under 30 minutes for under $20.

### Phase 6 — Full TickScheduler (3–4 days)
- Adaptive time-stepping (dense/sparse modes).
- Activity detection with all four trigger types.
- Forced refresh for stale kami.
- Kami-transit one-tick delay for movement.
- Determinism harness (seed mode) for replay.
- Soft timeouts and fallbacks.

### Phase 7 — CommsLayer (2–3 days)
- Channels and messages tables already in FactStore — now wire up the logic.
- `send_message` and `make_call` intents.
- Wake logic with the four recipient states.
- Pull-based feeds for social media.
- Group chat rate limiting.
- Gossip diffusion test: plant a fact in one agent, measure its spread across the town over a week.

### Phase 8 — Frontend MVP (1 week)
- Triple-column layout.
- Cytoscape kami graph with active/inactive coloring.
- Inspector with `Now` and `Recent` tabs for kami and agent.
- Time controls.
- WebSocket stream from backend pushing tick deltas.
- Dollar counter in mood strip.
- **Do not build time-travel, branching, interventions, or causal graphs in this phase.** Those come later if needed.

### Phase 9 — Polish and observability (ongoing)
- Time-travel via event log replay.
- Branch comparison.
- Causal graph view in event inspector.
- Activity heatmap.
- Intervention tools.
- Persona drift detector (compare agent voice over long horizons).

## 3.3 Validation tests — run these before declaring a phase done

**After Phase 3 (vertical slice):**
- *Canon stability*: scan the 200-tick log for object count anomalies (objects appearing/disappearing without create/destroy events). Should be zero.
- *Narrative readability*: hand-read the kami narratives. Do they make sense as a continuous story?
- *Cost*: total dollars spent. Should be in single digits for 200 ticks × 5 kami × 3 agents.

**After Phase 4 (memory):**
- *Diary coherence*: read one agent's daily summaries for 5 in-sim days as one document. Does it read like one person's life or 5 disconnected episodes?
- *Insight integration*: do agents' insights about each other change after major interactions?
- *Persona drift*: at tick 1000, does the agent still sound like the persona block? (Compare embeddings of generated speech against persona description.)

**After Phase 5 (WorldBuilder):**
- *Demographic plausibility*: does the population distribution match the bible?
- *Graph connectivity*: every kami reachable from every other kami?
- *Phantom references*: zero references to entities that don't exist.
- *Diversity*: archetype clustering — no more than X% of agents in any single cluster.

**After Phase 7 (CommsLayer):**
- *Telepathy check*: agents never reference message content before their `read_at_tick`.
- *Activation cascade test*: a 30-person group chat does not blow the per-tick budget.
- *Gossip diffusion*: a planted fact reaches at least N% of socially-connected agents within M ticks.

## 3.4 Minimum dependencies (do not over-engineer)

- Postgres → start with SQLite, migrate later only if needed.
- Vector store → chromadb local. Don't add Qdrant/Pinecone until scale demands it.
- Event bus → Postgres LISTEN/NOTIFY or even just an in-memory queue for MVP. Redis Streams later.
- LLM provider → Anthropic Claude (cheap = Haiku, strong = Sonnet). Architecture-agnostic, but the prompt caching pattern in §2.3.1 is provider-specific and worth using.

---

# PART IV — RISKS, ANTI-PATTERNS, AND TUNING

## 4.1 Known high-risk areas

1. **Epistemic containment** of agents will leak more than the spec optimistically describes. Plan for it: post-hoc validators are mandatory, not optional. Some leaks are acceptable as long as they don't break narrative coherence.
2. **Canon drift** without strict validators will be worse than expected. Do not let LLMs propose state changes without schema-typed tool calls.
3. **MemoryConsolidator cost** at long horizons may exceed estimates. Profile aggressively after Phase 4.
4. **Salience threshold tuning** for neighbour propagation will need empirical fitting per edge type. Start conservative (high thresholds, less propagation) and lower as needed.
5. **YAML vs JSON** for the present_entities block — A/B test on the chosen model. Some models parse one better than the other.
6. **Group chat cascades** are the single biggest risk for cost blowup in CommsLayer. Aggressively rate-limit before deploying.

## 4.2 Anti-patterns to avoid

- **Letting LLMs write to FactStore via free text.** Always tool-mediated. Always validated.
- **Generating the whole world in one frontier call.** Use cascades.
- **Per-agent independent LLM loops.** Use the kami-as-GM pattern. The whole architecture's economy depends on this.
- **Pretty graphical map of the town.** The frontend is a research tool, not a Sims clone. Resist the urge.
- **Skipping the vertical slice.** Do not build the full system before running 200 ticks of the simplest possible version. Empirical calibration > a priori design.
- **Frontier-model agent cognition.** Cheap-tier is often *better* for agents because it leaks less world knowledge.
- **Optimizing bootstrap cost.** Bootstrap is one-time. Spend the money on quality.
- **Real-time playback as default.** Default to paused/step mode in the frontend. Researchers read, they don't watch.

## 4.3 Tuning knobs (collect them in `config.py`)

- `tick_in_sim_minutes` (default 1)
- `kami_wake_salience_threshold` (per kami kind)
- `forced_refresh_interval` (default 100 ticks)
- `cheap_model_name` / `strong_model_name`
- `kami_strong_model_threshold` (number of agents + intent salience that escalates a kami to strong-tier)
- `max_active_l2_insights_per_agent` (default 40)
- `insight_decay_days_without_reinforcement` (default 14)
- `consolidation_phase_5_interval_days` (default 7)
- `group_chat_max_active_per_tick` (default 5)
- `conversation_cooldown_threshold` (default 10 messages in 5 ticks)
- `entity_creation_quota_per_kami_per_tick` (default 3)
- `prompt_cache_enabled` (default true)

## 4.4 What "done" looks like for a research-grade v1

- A 100-agent town runs for 7 in-sim days under $50.
- An ethnographer can pick any agent and read a coherent week of their life.
- A system observer can see at least one emergent gossip propagation event in the comms overlay.
- A debugger can trace any specific event back through its causal chain.
- The dollar counter in the frontend matches the actual API bill within 5%.

When all five hold, the system is doing what it was designed to do.

---

## 5. Final note to the implementing agent

This is a research artefact, not a product. Optimise for:
1. **Empirical learning** from each phase before moving to the next.
2. **Architectural integrity** — the canon/perspective separation and lazy activation are non-negotiable.
3. **Honest cost tracking** — every LLM call passes through the budget tracker.
4. **Reading the simulation output as a human** — if it doesn't read well, no amount of clever architecture matters.

Build the vertical slice. Read its output. Calibrate. Then build the next layer. Re-read this document at the start of each phase. When in doubt about a design decision, the answer is almost always somewhere in Part I or Part IV of this spec — those parts encode the reasoning, the rest is just structure.
