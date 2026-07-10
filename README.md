# Kami Simulation

Kami Simulation is an LLM-powered multi-agent world simulation environment. It models autonomous characters moving through a graph of spatial locations called **Kamis**, where each Kami acts as a local game master for its scene.

The core idea is to separate subjective cognition from objective reality. Agents can observe, think, form beliefs, and declare intentions, but they do not directly mutate the world. Kamis resolve the local scene, apply physical constraints, commit world-state changes, and emit narrative events.

## Current Capabilities

- Dynamic world creation from a natural-language prompt, including characters, Kamis, objects, relationships, biographies, and local history.
- Multiple saved simulation worlds with switching, deletion, population/count/cost summaries, and isolated world data inside the shared database.
- Atomic tick commits with durable idempotency records, failed-attempt recovery, and restart-safe clock restoration.
- Multi-provider LLM routing for Anthropic, OpenAI, and Gemini, with cheap/strong model tiers.
- In-app settings panel for LLM provider, model names, API tokens, and image-generation model settings.
- Tick-based simulation with agent cognition, Kami scene resolution, state mutation, event logging, and WebSocket updates.
- Graph view showing spatial Kamis, live agent positions, activity density, selected entities, and optional edge visibility.
- Timeline preview for agents or Kamis, with per-tick inspection.
- Generated world-map view: create a detailed top-view/cutaway map from the Kami graph, detect Kami bounding boxes, and anchor graph nodes to the generated image during pan/zoom.
- Entity reference inspection: object/entity ids in logs become hover/clickable references with details, location, state, and recent events.
- Agent and Kami inspectors with current state, recent thoughts/events, biographies, goals, relations, objects, and scene context.

## Architecture

### Agents

Agents are subjective actors. They maintain internal state, relationships, beliefs, goals, and personality context. During a tick they observe their current Kami, generate an inner signal, and declare an intent such as speaking, moving, inspecting an object, waiting, or performing work.

### Kamis

A Kami is an objective spatial scene: a room, station module, clearing, village square, corridor, tent, or any other world location. Kamis receive the intents of agents currently inside them, resolve conflicts, enforce local physics and constraints, update objects/entities, and produce the event that becomes reality.

### FactStore

The ground truth is stored in SQLite through SQLAlchemy as an entity-component timeline:

- `Entity`: agents, Kamis, objects, animals, vehicles, documents, channels.
- `Location`: current and historical placement of entities inside Kamis or containers.
- `PhysicalState`: key-value state over time.
- `Relation`: social, spatial, ownership, and semantic relationships.
- `AgentBelief`: subjective facts that may differ from reality.
- `AgentIntentRecord`: proposed actions and their resolution state.
- `ConversationThread`: ongoing local conversations.
- `Event`: committed narrative history.

### Tick Flow

When the UI steps the simulation:

1. The frontend calls `/api/sim/step`.
2. `TickScheduler` opens the next tick.
3. Agent workers observe current state and produce thoughts/intents through the configured LLM provider.
4. The scheduler groups intents by Kami.
5. Kami workers resolve each active scene and produce structured mutations.
6. FactStore validates and commits accepted mutations.
7. Events, thoughts, costs, and activity updates stream to the UI.

## LLM Providers

The backend supports:

- Anthropic
- OpenAI
- Gemini

The provider can be configured globally with `LLM_PROVIDER`, or per model tier by prefixing a model with `provider:model-name`.

Example:

```env
LLM_PROVIDER=openai
CHEAP_MODEL=gpt-5.4-mini
STRONG_MODEL=gpt-5.5
```

Per-tier override:

```env
CHEAP_MODEL=openai:gpt-5.4-mini
STRONG_MODEL=gemini:gemini-2.5-pro
```

Image generation settings are also configurable for generated world maps:

```env
IMAGE_PROVIDER=openai
CHEAP_IMAGE_MODEL=gpt-image-1-mini
STRONG_IMAGE_MODEL=gpt-image-2
```

These settings can also be updated from the UI settings panel.

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js and npm
- At least one API key for Anthropic, OpenAI, or Gemini

### Environment

Create `.env` from `.env.example` and fill in the providers you plan to use:

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
DATABASE_URL=sqlite:///./kami_sim.db
KAMI_API_TOKEN=
SIMULATION_BUDGET_USD=0

LLM_PROVIDER=openai
CHEAP_MODEL=gpt-5.4-mini
STRONG_MODEL=gpt-5.5
```

### Backend

```bash
cd backend
pip install -e ".[dev]"
python -m uvicorn kami_sim.api.server:app --host 0.0.0.0 --port 8000
```

Apply database migrations before starting the API:

```bash
cd backend
alembic upgrade head
```

The baseline migration detects and adopts a complete pre-Alembic Kami schema.
Back up an existing SQLite database before the first migration. A partially
initialized legacy schema is rejected instead of being modified implicitly.
On first startup, records from `simulations_registry.json` are imported into
the `simulations` table. The JSON file is not used for subsequent runtime writes.
Committed ticks are recorded in `simulation_ticks`; state mutations, canonical
events, the tick result, and the next clock value are committed together.

Set `KAMI_API_TOKEN` to require authentication for REST and WebSocket access.
When it is empty, authentication is disabled for local development. The browser
keeps an entered operator token in `sessionStorage` only.

Backend API:

```text
http://127.0.0.1:8000
```

Useful endpoints:

- `GET /api/status`
- `GET /api/simulations`
- `POST /api/sim/create`
- `POST /api/sim/step`
- `GET /api/graph`
- `GET /api/settings/llm`
- `PUT /api/settings/llm`
- `GET /api/simulations/{simulation_id}/budget`
- `PUT /api/simulations/{simulation_id}/budget`
- `POST /api/world-map/generate`
- `GET /api/entity/{entity_id}`

### Frontend

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0
```

Open:

```text
http://localhost:5173
```

## Using the UI

- Use **Create Simulation** to build a new world from a text prompt and population count.
- Use the world switcher to select, inspect, or delete saved worlds.
- Use **Step 1**, **Step 10**, **Step 100**, or **Run 100** to advance the simulation.
- Use **Graph** mode for spatial understanding of Kamis and live agent positions.
- Use **Timeline** mode to inspect agents or Kamis across ticks.
- Generate a detailed world map from Graph mode, then pan/zoom with graph nodes anchored to detected Kami regions.
- Toggle graph links when the generated map is easier to read without edges.
- Click agents, Kamis, or entity references inside logs to inspect details.
- Open the LLM settings panel from the top controls to change providers, model tiers, image models, and API tokens.

## Generated World Maps

The world-map generator builds a detailed prompt from the current simulation graph, Kami descriptions, objects, connections, and setting. The generated image is saved under backend-generated assets and served from `/generated/...`.

After generation, the backend performs a vision pass to detect normalized bounding boxes for each Kami. The frontend uses those boxes to position graph nodes inside the corresponding area of the image. The map image and graph overlay share the same pan/zoom transform, so node positions stay stable while navigating.

## Development Notes

- Runtime-generated maps and presentation audit outputs are ignored by Git.
- The SQLite database is used as local simulation state.
- Avoid committing API keys or local `.env` files.
- Run `npm run build` in `frontend` to validate TypeScript and production build.
- Run `python -m compileall backend/kami_sim` for a quick backend syntax check.

## License

This project is licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE) for details.
