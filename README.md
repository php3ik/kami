# Kami Simulation

Kami Simulation is an advanced, LLM-powered multi-agent social simulation environment. It models autonomous agents interacting within a spatial graph of distinct locations called Kamis.

The core philosophy of this project is to separate subjective cognition from objective reality. Agents hold beliefs and formulate intents, but they cannot directly modify the world. Instead, the Kami for a location acts as a local game master, resolves conflicting intents, and commits the objective outcome of each simulation tick.

---

## Core Concepts

### 1. Agents: Subjective Cognition

Agents are autonomous entities powered by Large Language Models through the Anthropic API. They maintain internal states, social relationships, and subjective beliefs about the world. During their turn, agents observe their surroundings, generate an inner monologue, update beliefs, and declare intents such as "talk to Oksana" or "walk to the Laboratory".

### 2. Kamis: Objective Reality and Game Masters

A Kami represents a distinct spatial location, such as a room, forest clearing, or space station module. Kamis act as localized game masters. After all agents in a Kami declare intents, the Kami receives the scene context, evaluates those intents, resolves conflicts, enforces physical rules, mutates world state, and emits a narrative event describing what actually happened.

### 3. Bulk Synchronous Parallel Tick Architecture

The simulation progresses in discrete ticks orchestrated by the `TickScheduler`.

1. Agent cognition: active agents process observations and declare intents.
2. Kami resolution: active Kamis process intents in their locations and propose state mutations.
3. Commit phase: proposed moves, state changes, and events are committed to the central database.
4. Propagation phase: events are broadcast to the frontend UI and agent memory systems.

### 4. FactStore Database

The ground truth of the simulation is stored in SQLite through SQLAlchemy. It acts as an entity-component system storing:

- Entities: agents, objects, and Kamis.
- Locations: current and historical entity placement.
- States and properties: key-value attributes of entities.
- Relations: social graphs and opinions between agents.
- Beliefs: subjective, potentially false facts held by agents.
- Events: the historical narrative log of the simulation.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js and npm
- An Anthropic API key

### Backend Setup

Navigate to the backend directory and install the Python dependencies:

```bash
cd backend
pip install -e ".[dev]"
```

Create an `.env` file in the root of the `backend` directory and add your Anthropic API key:

```env
ANTHROPIC_API_KEY=your_api_key_here
OPENAI_API_KEY=your_api_key_here
GEMINI_API_KEY=your_api_key_here
DATABASE_URL=sqlite:///./kami_sim.db
```

Select the LLM provider globally with `LLM_PROVIDER`:

```env
LLM_PROVIDER=anthropic
CHEAP_MODEL=claude-haiku-4-5-20251001
STRONG_MODEL=claude-sonnet-4-6
```

You can also override the provider per model tier by prefixing the model name:

```env
CHEAP_MODEL=openai:gpt-4.1-mini
STRONG_MODEL=gemini:gemini-2.5-pro
```

Start the FastAPI server:

```bash
python -m uvicorn kami_sim.api.server:app --host 0.0.0.0 --port 8000
```

### Frontend Setup

Navigate to the frontend directory and install the Node dependencies:

```bash
cd frontend
npm install
```

Start the Vite development server:

```bash
npm run dev
```

Open `http://localhost:5173`.

---

## Using the UI

The frontend provides a real-time "God-mode" view into the simulation:

- Top bar: current tick, simulation status, LLM call count, and API cost tracker.
- Time controls: step the simulation forward or run continuously.
- Kami graph: a force-directed graph of Kamis and the agents currently inside them.
- Agent activity board: live agent monologues and current locations.
- Inspectors: detailed Kami and agent database records, relationships, beliefs, and recent events.

## How the Logic Flows

When you click "Step 1" in the UI:

1. The frontend calls `/api/sim/step`.
2. `TickScheduler` initializes the next tick.
3. WebSockets stream progress events to the frontend.
4. Agent workers call the Anthropic API concurrently and return intents plus inner monologues.
5. The scheduler groups intents by Kami location.
6. Kami workers call the Anthropic API concurrently and produce structured tool calls.
7. The backend commits valid mutations to SQLite through the FactStore tool layer.
8. The backend broadcasts the final tick summary through WebSockets.
