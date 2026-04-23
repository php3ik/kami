"""FastAPI server — spec §2.11, §3.2 Phase 8.

Provides REST API and WebSocket for the frontend.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

from ..config import config
from ..factstore import tools as fs
from ..factstore.models import Entity, init_db
from ..llm.budget import budget
from ..scheduler.tick_scheduler import TickScheduler
from ..oriv_world import build_oriv_world
from ..spatial.graph import SpatialGraph
from ..world_builder.build_world import build_world, load_world_into_db

logger = logging.getLogger(__name__)

# Global simulation state
sim_state: dict[str, Any] = {
    "scheduler": None,
    "session_factory": None,
    "spatial_graph": None,
    "running": False,
    "paused": True,
}

# WebSocket connections
ws_connections: set[WebSocket] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init DB and world
    db_url = config.database_url
    engine, session_factory = init_db(db_url)
    session = session_factory()

    from ..factstore.models import Entity
    count = session.query(Entity).count()

    if count == 0:
        logger.info("Database empty, building default Oriv world...")
        spatial_graph = build_oriv_world(session)
    else:
        logger.info(f"Database contains {count} entities, restoring simulation state...")
        from ..spatial.graph import SpatialGraph
        spatial_graph = SpatialGraph()
        
        import os
        if os.path.exists("sim_graph.json"):
            with open("sim_graph.json", "r", encoding="utf-8") as f:
                graph_data = json.load(f)
                for node in graph_data.get("nodes", []):
                    spatial_graph.add_kami(node["id"], name=node.get("name"), kind=node.get("kind"))
                for edge in graph_data.get("edges", []):
                    spatial_graph.add_edge(edge["source"], edge["target"], edge_type=edge.get("edge_type", "adjacent"))
        else:
            logger.warning("sim_graph.json not found, spatial graph edges will be missing!")
            kamis = session.query(Entity).filter(Entity.kind == "kami").all()
            for k in kamis:
                spatial_graph.add_kami(k.entity_id, name=k.canonical_name, kind=k.archetype.get("kami_kind", "location"))

    session.close()

    scheduler = TickScheduler(
        session_factory=session_factory,
        spatial_graph=spatial_graph,
    )

    sim_state["scheduler"] = scheduler
    sim_state["session_factory"] = session_factory
    sim_state["spatial_graph"] = spatial_graph

    logger.info("Simulation initialized")
    yield
    logger.info("Shutting down")


app = FastAPI(title="Kami Simulation", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
async def get_status():
    scheduler: TickScheduler = sim_state["scheduler"]
    return {
        "current_tick": scheduler.current_tick if scheduler else 0,
        "running": sim_state["running"],
        "paused": sim_state["paused"],
        "budget": budget.get_summary(),
    }


@app.get("/api/graph")
async def get_graph():
    sg: SpatialGraph = sim_state["spatial_graph"]
    return sg.to_dict() if sg else {"nodes": [], "edges": []}


@app.get("/api/kami/{kami_id}")
async def get_kami(kami_id: str):
    session = sim_state["session_factory"]()
    try:
        state = fs.query_kami_state(session, kami_id)
        events = fs.get_events(session, kami_id=kami_id, limit=1000)
        return {
            **state,
            "recent_events": [
                {
                    "event_id": e.event_id,
                    "tick": e.tick,
                    "event_type": e.event_type,
                    "narrative": e.narrative,
                    "salience": e.salience,
                    "participants": e.participants,
                }
                for e in events
            ],
        }
    finally:
        session.close()


@app.get("/api/agent/{agent_id}")
async def get_agent(agent_id: str):
    session = sim_state["session_factory"]()
    try:
        entity = session.get(Entity, agent_id)
        if not entity:
            return {"error": "not found"}

        location = fs.get_current_location(session, agent_id)
        states = fs.get_state(session, agent_id)
        relations = fs.get_relations(session, agent_id, direction="both")
        beliefs = fs.get_beliefs(session, agent_id)

        # Retrieve thoughts from recent volatile tick_log memory
        recent_thoughts = []
        if sim_state["scheduler"]:
            log = getattr(sim_state["scheduler"], "tick_log", [])
            for t in log[-20:]:
                if t.get("monologues") and agent_id in t["monologues"] and t["monologues"][agent_id]:
                    recent_thoughts.append({
                        "tick": t["tick"],
                        "thought": t["monologues"][agent_id]
                    })

        # Search for past events involving the agent
        recent_events = session.query(fs.Event).order_by(fs.Event.tick.desc()).limit(200).all()
        action_history = []
        for e in recent_events:
            comps = e.participants or []
            if agent_id in comps:
                action_history.append({
                    "tick": e.tick,
                    "event_type": e.event_type,
                    "narrative": e.narrative
                })
        action_history = action_history[:20]

        return {
            "entity_id": entity.entity_id,
            "name": entity.canonical_name,
            "archetype": entity.archetype,
            "location": {
                "kami_id": location.kami_id if location else None,
                "since_tick": location.since_tick if location else None,
            },
            "states": {s.attribute: s.value for s in states},
            "relations": [
                {
                    "from": r.from_entity,
                    "to": r.to_entity,
                    "type": r.rel_type,
                    "weight": r.weight,
                }
                for r in relations
            ],
            "beliefs": [{"kind": b.kind, "value": b.believed_value, "confidence": b.confidence} for b in beliefs],
            "beliefs_count": len(beliefs),
            "recent_thoughts": recent_thoughts,
            "action_history": action_history,
        }
    finally:
        session.close()


@app.get("/api/agents")
async def get_all_agents():
    session = sim_state["session_factory"]()
    try:
        from ..factstore.models import Entity
        from ..factstore.tools import get_current_location
        agents = session.query(Entity).filter(Entity.kind == "agent").all()
        results = []
        for a in agents:
            loc = get_current_location(session, a.entity_id)
            kami_id = loc.kami_id if loc else None
            results.append({
                "entity_id": a.entity_id, 
                "name": a.canonical_name, 
                "role": a.archetype.get("role", "Unknown"),
                "kami_id": kami_id
            })
        return results
    finally:
        session.close()


@app.get("/api/events")
async def get_events(
    since_tick: int = 0,
    until_tick: int | None = None,
    kami_id: str | None = None,
    limit: int = 50,
):
    session = sim_state["session_factory"]()
    try:
        events = fs.get_events(
            session, kami_id=kami_id, since_tick=since_tick,
            until_tick=until_tick, limit=limit,
        )
        return [
            {
                "event_id": e.event_id,
                "tick": e.tick,
                "kami_id": e.kami_id,
                "event_type": e.event_type,
                "narrative": e.narrative,
                "salience": e.salience,
                "participants": e.participants,
                "payload": e.payload,
                "causes": e.causes,
            }
            for e in events
        ]
    finally:
        session.close()


@app.post("/api/sim/step")
async def step_tick(ticks: int = 1):
    """Advance simulation by N ticks."""
    scheduler: TickScheduler = sim_state["scheduler"]
    if not scheduler:
        return {"error": "not initialized"}

    async def _progress(msg):
        await _broadcast(msg)

    results = await scheduler.run(num_ticks=ticks, progress_callback=_progress)

    # Broadcast to WebSocket clients
    for result in results:
        await _broadcast({"type": "tick", "data": result})

    return {"ticks_run": len(results), "results": results}


class CreateSimRequest(BaseModel):
    prompt: str
    agent_count: int = 10

@app.post("/api/sim/create")
async def create_sim(request: CreateSimRequest):
    sim_state["running"] = False
    sim_state["paused"] = True

    logger.info(f"Generating World: {request.prompt} ({request.agent_count} agents)")
    world_output = await build_world(request.prompt, agent_count=request.agent_count)
    
    db_url = f"sqlite:///./kami_custom_{uuid.uuid4().hex[:8]}.db"
    engine, session_factory = init_db(db_url)
    session = session_factory()
    
    spatial_graph = load_world_into_db(session, world_output)
    session.close()

    # Save graph for persistence
    import os
    with open("sim_graph.json", "w", encoding="utf-8") as f:
        json.dump(spatial_graph.to_dict(), f, indent=2)

    scheduler = TickScheduler(
        session_factory=session_factory,
        spatial_graph=spatial_graph,
    )

    sim_state["scheduler"] = scheduler
    sim_state["session_factory"] = session_factory
    sim_state["spatial_graph"] = spatial_graph

    # Update global config to point to new DB
    config.database_url = db_url

    return {"status": "success"}


@app.post("/api/sim/run")
async def start_run(ticks: int = 100):
    """Start continuous simulation in background."""
    sim_state["paused"] = False
    sim_state["running"] = True

    scheduler: TickScheduler = sim_state["scheduler"]

    async def run_loop():
        for _ in range(ticks):
            if sim_state["paused"]:
                break
            async def _progress(msg):
                await _broadcast(msg)
            results = await scheduler.run(num_ticks=1, progress_callback=_progress)
            for result in results:
                await _broadcast({"type": "tick", "data": result})
        sim_state["running"] = False

    asyncio.create_task(run_loop())
    return {"status": "started", "target_ticks": ticks}


@app.post("/api/sim/pause")
async def pause():
    sim_state["paused"] = True
    return {"status": "paused"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_connections.add(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            if msg.get("type") == "step":
                results = await sim_state["scheduler"].run(num_ticks=1)
                await websocket.send_json({"type": "tick", "data": results[0] if results else {}})
    except WebSocketDisconnect:
        ws_connections.discard(websocket)


async def _broadcast(message: dict):
    for ws in list(ws_connections):
        try:
            await ws.send_json(message)
        except Exception:
            ws_connections.discard(ws)
