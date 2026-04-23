# Kami Simulation

Kami Simulation is an advanced, LLM-powered multi-agent social simulation environment. It models complex, autonomous agents interacting within a spatial graph of distinct locations (called **Kamis**). 

The core philosophy of this project is to separate **subjective cognition** from **objective reality**. Agents possess their own beliefs and formulate "intents," but they cannot directly modify the world. Instead, the Kami (the "Game Master" of a specific location) resolves all conflicting intents and dictates the objective outcome of a simulation tick.

---

## 🧠 Core Concepts

### 1. Agents (Subjective Cognition)
Agents are autonomous entities powered by Large Language Models (Anthropic API). They maintain internal states, social relationships, and subjective beliefs about the world. During their turn, agents observe their surroundings, generate an inner monologue, update their beliefs, and declare **intents** (e.g., "I intend to talk to Oksana" or "I intend to walk to the Laboratory").

### 2. Kamis (Objective Reality & Game Masters)
A Kami represents a distinct spatial location (e.g., a room, a forest clearing, a space station module). Kamis act as localized Game Masters. After all agents in a Kami declare their intents, the Kami is prompted with the entire scene's context. The Kami evaluates the intents, resolves conflicts, enforces physical rules, mutates the world state, and outputs a coherent narrative event describing what actually happened.

### 3. Bulk Synchronous Parallel (BSP) Tick Architecture
The simulation progresses in discrete time steps (ticks) orchestrated by the `TickScheduler`.
1. **Agent Cognition (Parallel):** All agents process their observations and declare intents simultaneously.
2. **Kami Resolution (Parallel):** All active Kamis process the intents within their borders and propose state mutations.
3. **Commit Phase:** Proposed mutations (moves, state changes, new events) are committed to the central database.
4. **Propagation Phase:** Events are broadcasted to the frontend UI and to agent memories.

### 4. FactStore Database
The ground truth of the simulation is stored in a relational database (SQLite via SQLAlchemy). It acts as an Entity-Component system storing:
- **Entities**: Agents, Objects, and Kamis.
- **Locations**: Tracking which entity is inside which Kami.
- **States & Properties**: Key-value attributes of entities.
- **Relations**: Social graphs and opinions between agents.
- **Beliefs**: Subjective, potentially false facts held by agents.
- **Events**: The historical narrative log of the simulation.

---

## 🚀 Getting Started

### Prerequisites
- Python 3.10+
- Node.js & npm
- An [Anthropic API Key](https://console.anthropic.com/)

### 1. Backend Setup

Navigate to the backend directory and install the Python dependencies:

```bash
cd backend
pip install -r requirements.txt
```

Create an `.env` file in the root of the `backend` directory and add your Anthropic API key:
```env
ANTHROPIC_API_KEY=your_api_key_here
```

Start the FastAPI server:
```bash
python -m uvicorn kami_sim.api.server:app --host 0.0.0.0 --port 8000
```

### 2. Frontend Setup

Navigate to the frontend directory and install the Node dependencies:

```bash
cd frontend
npm install
```

Start the Vite development server:
```bash
npm run dev
```

Open your browser to `http://localhost:5173`.

---

## 🎮 Using the UI

The frontend provides a real-time "God-mode" view into the simulation:

- **Top Bar:** Displays the current Tick, Simulation Time, LLM call count, and API Cost tracker.
- **Time Controls:** Step the simulation forward by 1, 10, or 100 ticks.
- **Kami Graph (Center):** A dynamic, force-directed graph of the world. Kamis are large dark nodes, and Agents are small purple circles positioned directly inside their current Kami. As agents travel, you will see them move between nodes.
- **Agent Activity Board (Bottom):** A horizontally scrolling dashboard of all agents. It shows their current location badge and streams their inner monologues in real-time as the simulation computes.
- **Inspectors (Right):** Click on any Kami node or Agent node in the graph to view their detailed internal database records, including traits, states, relationships, and historical event logs.

## ⚙️ How the Logic Flows (Under the Hood)

When you click "Step 1" in the UI:
1. The frontend hits the `/api/sim/step` endpoint.
2. The `TickScheduler` initializes the tick.
3. WebSockets stream a `progress` event to the frontend, marking agents as "Pondering...".
4. `asyncio.gather` fires off Anthropic API calls for all agents simultaneously.
5. Agents return their `intents` and `inner_monologues`. Monologues are streamed live to the UI.
6. The scheduler groups intents by Kami location.
7. `asyncio.gather` fires off Anthropic API calls for all active Kamis, providing them with the agent intents and current FactStore state.
8. Kamis return tool calls (e.g., `move_entity`, `emit_event`).
9. The backend commits these changes to the SQLite database.
10. The backend returns a final `tick` summary via WebSocket, prompting the UI to refresh the agent positions on the graph and log the narrative events.
