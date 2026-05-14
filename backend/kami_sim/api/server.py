"""FastAPI server — spec §2.11, §3.2 Phase 8.

Provides REST API and WebSocket for the frontend.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import uuid

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func

from ..config import config
from ..factstore import tools as fs
from ..factstore.models import (
    AgentBelief,
    AgentIntentRecord,
    AgentNeed,
    ConversationThread,
    Entity,
    Event,
    Location,
    PhysicalState,
    Relation,
    init_db,
)
from ..llm.budget import budget
from ..llm.client import llm_client
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
    "active_simulation_id": None,
}

# WebSocket connections
ws_connections: set[WebSocket] = set()

REGISTRY_PATH = Path("simulations_registry.json")
GENERATED_ASSETS_PATH = Path("generated_assets")
WORLD_MAP_STYLES = {
    "realism": "photorealistic architectural top-down world map, grounded natural lighting, physical materials, believable scale",
    "cinematic": "cinematic top-down production design map, dramatic but readable lighting, realistic textures, premium concept art",
    "anime": "high-detail anime background art, clean linework, vibrant but readable colors, Studio-quality environment design",
    "game": "high-end game environment map, isometric/top-down playable level art, readable paths and interiors",
    "pixel": "detailed pixel art world map, crisp tiles, readable interior rooms, rich object detail",
    "blueprint": "architectural blueprint mixed with realistic material callouts, precise labels avoided, clean readable plan",
    "watercolor": "hand-painted watercolor map, delicate textures, readable top-down layout, gentle environmental atmosphere",
    "ink": "dark ink cartography with subtle color washes, precise linework, top-down and slightly angled spatial plan",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_registry() -> dict:
    if not REGISTRY_PATH.exists():
        return {"active_id": None, "simulations": []}
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"active_id": None, "simulations": []}


def _write_registry(registry: dict) -> None:
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")


def _sqlite_path_from_url(db_url: str) -> Path:
    if db_url.startswith("sqlite:///./"):
        return Path(db_url.replace("sqlite:///./", "", 1))
    if db_url.startswith("sqlite:///"):
        return Path(db_url.replace("sqlite:///", "", 1))
    return Path(db_url)


def _load_graph_from_data(graph_data: dict | None, session) -> SpatialGraph:
    graph = SpatialGraph()
    if graph_data:
        for node in graph_data.get("nodes", []):
            graph.add_kami(node["id"], name=node.get("name"), kind=node.get("kind"))
        for edge in graph_data.get("edges", []):
            graph.add_edge(
                edge["source"],
                edge["target"],
                edge_type=edge.get("edge_type", "adjacent"),
                visual_attenuation=edge.get("visual_attenuation", 0.5),
                audio_attenuation=edge.get("audio_attenuation", 0.5),
            )
        return graph

    return _load_graph_from_path(None, session)


def _load_graph_from_path(graph_path: str | None, session) -> SpatialGraph:
    graph = SpatialGraph()
    path = Path(graph_path) if graph_path else Path("sim_graph.json")
    if path.exists():
        graph_data = json.loads(path.read_text(encoding="utf-8"))
        for node in graph_data.get("nodes", []):
            graph.add_kami(node["id"], name=node.get("name"), kind=node.get("kind"))
        for edge in graph_data.get("edges", []):
            graph.add_edge(
                edge["source"],
                edge["target"],
                edge_type=edge.get("edge_type", "adjacent"),
                visual_attenuation=edge.get("visual_attenuation", 0.5),
                audio_attenuation=edge.get("audio_attenuation", 0.5),
            )
        return graph

    kamis = session.query(Entity).filter(Entity.kind == "kami").all()
    for kami in kamis:
        graph.add_kami(
            kami.entity_id,
            name=kami.canonical_name,
            kind=(kami.archetype or {}).get("kami_kind", "location"),
        )
    return graph


def _record_kami_ids(record: dict) -> list[str]:
    graph_data = record.get("graph_data") or {}
    if graph_data.get("nodes"):
        return [node["id"] for node in graph_data.get("nodes", [])]
    graph_path = record.get("graph_path")
    if graph_path and Path(graph_path).exists():
        try:
            data = json.loads(Path(graph_path).read_text(encoding="utf-8"))
            return [node["id"] for node in data.get("nodes", [])]
        except Exception:
            return []
    return []


def _active_kami_ids() -> list[str]:
    graph: SpatialGraph | None = sim_state.get("spatial_graph")
    return graph.all_kami_ids() if graph else []


def _record_entity_ids(record: dict, session) -> list[str]:
    kami_ids = _record_kami_ids(record)
    if not kami_ids:
        return []
    loc_rows = (
        session.query(Location)
        .filter(Location.kami_id.in_(kami_ids))
        .all()
    )
    return list({*(kami_ids), *(row.entity_id for row in loc_rows)})


def _world_map_payload() -> dict:
    registry = _read_registry()
    active_id = sim_state.get("active_simulation_id") or registry.get("active_id")
    record = next((item for item in registry.get("simulations", []) if item.get("id") == active_id), {})
    graph_data = record.get("graph_data") or (sim_state["spatial_graph"].to_dict() if sim_state.get("spatial_graph") else {})
    nodes = graph_data.get("nodes") or []
    edges = graph_data.get("edges") or []
    node_ids = [node.get("id") for node in nodes if node.get("id")]

    session = sim_state["session_factory"]()
    try:
        entities = {
            entity.entity_id: entity
            for entity in session.query(Entity).filter(Entity.entity_id.in_(node_ids)).all()
        }
        object_rows = (
            session.query(Entity, Location)
            .join(Location, Location.entity_id == Entity.entity_id)
            .filter(Location.valid_until_tick.is_(None))
            .filter(Location.kami_id.in_(node_ids))
            .filter(Entity.kind.notin_(["agent", "kami"]))
            .all()
        )
    finally:
        session.close()

    objects_by_kami: dict[str, list[Entity]] = {}
    for entity, location in object_rows:
        objects_by_kami.setdefault(location.kami_id, []).append(entity)

    locations = []
    for node in nodes:
        node_id = node.get("id")
        entity = entities.get(node_id)
        archetype = dict(entity.archetype or {}) if entity else {}
        objects = []
        for obj in objects_by_kami.get(node_id, [])[:12]:
            obj_arch = obj.archetype or {}
            objects.append({
                "name": obj.canonical_name,
                "kind": obj.kind,
                "description": obj_arch.get("description", ""),
                "condition": obj_arch.get("condition", ""),
            })
        locations.append({
            "id": node_id,
            "name": (entity.canonical_name if entity else node.get("name")) or node_id,
            "kind": archetype.get("kind") or archetype.get("kami_kind") or node.get("kind"),
            "district": archetype.get("district", ""),
            "description": archetype.get("description", ""),
            "history": archetype.get("history", ""),
            "current_situation": archetype.get("current_situation", ""),
            "ambient_objects": archetype.get("ambient_objects", []),
            "important_objects": objects,
        })

    node_name = {item["id"]: item["name"] for item in locations}
    edge_lines = [
        f"{node_name.get(edge.get('source'), edge.get('source'))} <-> "
        f"{node_name.get(edge.get('target'), edge.get('target'))} "
        f"({edge.get('edge_type', 'adjacent')})"
        for edge in edges
    ]
    world_payload = {
        "world_name": record.get("name") or "Kami world",
        "world_prompt": record.get("prompt", ""),
        "locations": locations,
        "connections": edge_lines,
    }
    return world_payload


def _build_world_map_prompt(style_id: str) -> str:
    world_payload = _world_map_payload()
    return f"""Create a SUPER-DETAILED illustrated world map from this simulation graph.

Style: {WORLD_MAP_STYLES.get(style_id, WORLD_MAP_STYLES["realism"])}.

Camera and composition:
- Top-down or slightly angled top-view map, like a realistic cutaway site plan.
- Show every listed location at once. No cropping. No hidden locations.
- Show both exterior structure and interior contents: use cutaway roofs, open walls, transparent ceilings, or readable room interiors where needed.
- Respect the graph connections: connected locations must be visibly adjacent or linked by corridors, doors, paths, hatches, trails, roads, tubes, or other plausible connectors.
- Make the world spatially coherent and inspectable, not an abstract node graph.
- The image must contain NO people, NO characters, NO astronauts, NO animals, NO portraits, NO silhouettes.
- Do not draw numbered markers, map pins, colored circles, icons, UI overlays, legends, callouts, or readable labels.
- Include only places, architecture, terrain, fixtures, furniture, equipment, objects, signs of use, lighting, surfaces, tools, storage, machines, paths, rooms, and environmental details.
- Avoid readable text labels; use visual landmarks instead of words.
- Keep the whole map legible at a glance but packed with micro-detail.

World data:
{json.dumps(world_payload, ensure_ascii=False, indent=2)[:22000]}
"""


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return (1536, 1024)
    return (int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big"))


def _metadata_path_for_image(path: Path) -> Path:
    return path.with_suffix(".map.json")


def _fallback_world_map_bboxes(locations: list[dict]) -> list[dict]:
    count = max(len(locations), 1)
    cols = max(2, math.ceil(math.sqrt(count)))
    rows = max(1, math.ceil(count / cols))
    bboxes = []
    for idx, location in enumerate(locations):
        col = idx % cols
        row = idx // cols
        cell_w = 0.84 / cols
        cell_h = 0.76 / rows
        x = 0.08 + col * cell_w + cell_w * 0.1
        y = 0.12 + row * cell_h + cell_h * 0.1
        bboxes.append({
            "id": location["id"],
            "bbox": {
                "x": round(x, 4),
                "y": round(y, 4),
                "w": round(cell_w * 0.8, 4),
                "h": round(cell_h * 0.8, 4),
            },
            "confidence": 0.2,
            "source": "fallback_grid",
        })
    return bboxes


def _coerce_world_map_bboxes(raw: Any, locations: list[dict]) -> list[dict]:
    valid_ids = {location["id"] for location in locations}
    items = raw.get("kami_bboxes") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        items = []

    seen: set[str] = set()
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kami_id = item.get("id") or item.get("kami_id")
        bbox = item.get("bbox") or item
        if kami_id not in valid_ids or not isinstance(bbox, dict):
            continue
        try:
            x = float(bbox.get("x"))
            y = float(bbox.get("y"))
            w = float(bbox.get("w", bbox.get("width")))
            h = float(bbox.get("h", bbox.get("height")))
        except (TypeError, ValueError):
            continue
        if w <= 0 or h <= 0:
            continue
        x = max(0.0, min(1.0, x))
        y = max(0.0, min(1.0, y))
        w = max(0.01, min(1.0 - x, w))
        h = max(0.01, min(1.0 - y, h))
        result.append({
            "id": kami_id,
            "bbox": {
                "x": round(x, 4),
                "y": round(y, 4),
                "w": round(w, 4),
                "h": round(h, 4),
            },
            "confidence": float(item.get("confidence", 0.65) or 0.65),
            "source": item.get("source", "vision"),
        })
        seen.add(kami_id)

    for fallback in _fallback_world_map_bboxes([location for location in locations if location["id"] not in seen]):
        result.append(fallback)
    return result


def _extract_json_object(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


async def _detect_world_map_bboxes(image_path: Path, locations: list[dict]) -> tuple[list[dict], str]:
    if not config.openai_api_key:
        return _fallback_world_map_bboxes(locations), "fallback_no_openai_key"

    from openai import AsyncOpenAI

    image_bytes = image_path.read_bytes()
    image_data = base64.b64encode(image_bytes).decode("ascii")
    model = config.strong_model_name
    if ":" in model:
        provider, model = model.split(":", 1)
        if provider.strip().lower() != "openai":
            model = "gpt-5.5"

    payload = {
        "locations": [
            {
                "id": location["id"],
                "name": location["name"],
                "kind": location.get("kind"),
                "description": location.get("description", "")[:700],
                "important_objects": location.get("important_objects", [])[:8],
            }
            for location in locations
        ]
    }
    prompt = f"""Find the visual bounding box for every listed kami/location in this generated world-map image.

Return ONLY JSON with this shape:
{{
  "kami_bboxes": [
    {{"id": "location id", "bbox": {{"x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1}}, "confidence": 0.0}}
  ]
}}

Coordinates must be normalized against the full image: x/y are top-left, w/h are width/height, all in 0..1.
Each bbox should contain the visible room/zone/area for that kami, not a label, marker, or decorative object.
If a location is hard to identify, make the best spatial estimate from graph adjacency and visual landmarks.

Locations:
{json.dumps(payload, ensure_ascii=False, indent=2)[:16000]}
"""
    client = AsyncOpenAI(api_key=config.openai_api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise visual map annotator. Return strict JSON only."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}},
                ],
            },
        ],
        "max_completion_tokens": 3000,
        "response_format": {"type": "json_object"},
    }
    try:
        response = await client.chat.completions.create(**kwargs)
    except Exception as exc:
        logger.warning("World map bbox detection failed, using fallback grid: %s", exc)
        return _fallback_world_map_bboxes(locations), "fallback_detection_error"

    content = response.choices[0].message.content or "{}"
    try:
        parsed = _extract_json_object(content)
        return _coerce_world_map_bboxes(parsed, locations), f"openai:{model}"
    except Exception as exc:
        logger.warning("World map bbox JSON parse failed, using fallback grid: %s", exc)
        return _fallback_world_map_bboxes(locations), "fallback_parse_error"


async def _build_world_map_metadata(
    image_path: Path,
    style_id: str | None,
    provider: str | None,
    model: str | None,
    usage: dict | None = None,
) -> dict:
    width, height = _png_dimensions(image_path)
    world_payload = _world_map_payload()
    locations = world_payload.get("locations", [])
    bboxes, bbox_source = await _detect_world_map_bboxes(image_path, locations)
    metadata = {
        "url": f"/generated/{image_path.name}",
        "style": style_id,
        "provider": provider,
        "model": model,
        "created_at": image_path.stat().st_mtime,
        "image_width": width,
        "image_height": height,
        "bboxes": bboxes,
        "bbox_source": bbox_source,
        "usage": usage or {},
    }
    _metadata_path_for_image(image_path).write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return metadata


async def _load_or_build_world_map_metadata(
    image_path: Path,
    style_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    metadata_path = _metadata_path_for_image(image_path)
    if metadata_path.exists():
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return await _build_world_map_metadata(image_path, style_id, provider, model)


async def _generate_openai_world_map(
    prompt: str,
    model: str,
    size: str,
    quality: str,
    output_path: Path,
) -> dict:
    if not config.openai_api_key:
        raise ValueError("OpenAI API key is not configured")
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config.openai_api_key)
    kwargs: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": size,
        "quality": quality,
    }
    if model.startswith("gpt-image"):
        kwargs["output_format"] = "png"
    response = await client.images.generate(**kwargs)
    item = response.data[0]
    if getattr(item, "b64_json", None):
        output_path.write_bytes(base64.b64decode(item.b64_json))
    elif getattr(item, "url", None):
        import urllib.request

        output_path.write_bytes(
            await asyncio.to_thread(lambda: urllib.request.urlopen(item.url, timeout=60).read())
        )
    else:
        raise ValueError("OpenAI did not return image data")
    usage = getattr(response, "usage", None)
    return usage.model_dump() if hasattr(usage, "model_dump") else {}


async def _generate_gemini_world_map(prompt: str, model: str, output_path: Path) -> dict:
    if not config.gemini_api_key:
        raise ValueError("Gemini API key is not configured")
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=config.gemini_api_key)

    def _generate() -> bytes:
        if model.startswith("imagen"):
            response = client.models.generate_images(
                model=model,
                prompt=prompt,
                config=types.GenerateImagesConfig(number_of_images=1),
            )
            if not response.generated_images:
                raise ValueError("Gemini Imagen did not return image data")
            return response.generated_images[0].image.image_bytes
        response = client.models.generate_content(
            model=model,
            contents=[prompt],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        parts = getattr(response, "parts", None) or response.candidates[0].content.parts
        for part in parts:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return inline.data
        raise ValueError("Gemini did not return image data")

    image_bytes = await asyncio.to_thread(_generate)
    output_path.write_bytes(image_bytes)
    return {}


def _simulation_stats(record: dict) -> dict:
    session = sim_state["session_factory"]() if sim_state.get("session_factory") else None
    if session is None:
        engine, session_factory = init_db(config.database_url)
        session = session_factory()
        dispose_engine = engine
    else:
        dispose_engine = None
    try:
        kami_ids = _record_kami_ids(record)
        entity_ids = _record_entity_ids(record, session)
        event_q = session.query(Event)
        if kami_ids:
            event_q = event_q.filter(Event.kami_id.in_(kami_ids))
        max_tick = event_q.with_entities(func.max(Event.tick)).scalar()
        return {
            "ticks": (int(max_tick) + 1) if max_tick is not None else 0,
            "population": session.query(Entity).filter(Entity.kind == "agent", Entity.entity_id.in_(entity_ids)).count() if entity_ids else 0,
            "kami_count": len(kami_ids) if kami_ids else session.query(Entity).filter(Entity.kind == "kami").count(),
            "event_count": event_q.count(),
        }
    finally:
        session.close()
        if dispose_engine:
            dispose_engine.dispose()


def _enrich_simulation_record(record: dict) -> dict:
    stats = _simulation_stats(record)
    return {
        **record,
        **stats,
        "total_cost_usd": round(float(record.get("total_cost_usd", 0.0)), 6),
    }


def _register_or_update(record: dict, active: bool = False) -> dict:
    registry = _read_registry()
    sims = registry.get("simulations", [])
    existing = next((idx for idx, item in enumerate(sims) if item["id"] == record["id"]), None)
    if existing is None:
        sims.append(record)
    else:
        sims[existing] = {**sims[existing], **record}
    registry["simulations"] = sims
    if active:
        registry["active_id"] = record["id"]
    _write_registry(registry)
    return registry


def _active_record() -> dict | None:
    registry = _read_registry()
    active_id = registry.get("active_id")
    return next((item for item in registry.get("simulations", []) if item.get("id") == active_id), None)


def _scope_legacy_id(simulation_id: str, entity_id: str | None) -> str | None:
    if not entity_id:
        return entity_id
    prefix = f"sim_{simulation_id}__"
    return entity_id if entity_id.startswith(prefix) else f"{prefix}{entity_id}"


def _migrate_legacy_file_record(record: dict, main_session) -> dict:
    """Import an older per-file simulation into the single shared database."""
    if record.get("graph_data"):
        return record
    db_url = record.get("db_url")
    db_path = Path(record.get("db_path", ""))
    if not db_url or db_url == config.database_url or not db_path.exists():
        return record

    sim_id = record["id"]
    prefix = f"sim_{sim_id}__"
    if main_session.query(Entity).filter(Entity.entity_id.like(f"{prefix}%")).first():
        return record

    legacy_engine, legacy_factory = init_db(db_url)
    legacy = legacy_factory()
    try:
        id_map: dict[str, str] = {}
        for entity in legacy.query(Entity).all():
            new_id = _scope_legacy_id(sim_id, entity.entity_id)
            id_map[entity.entity_id] = new_id
            archetype = dict(entity.archetype or {})
            archetype["entity_id"] = new_id
            archetype["simulation_id"] = sim_id
            main_session.add(Entity(
                entity_id=new_id,
                kind=entity.kind,
                canonical_name=entity.canonical_name,
                archetype=archetype,
                created_at_tick=entity.created_at_tick,
                created_by_event=entity.created_by_event,
            ))
        main_session.flush()

        def mid(value):
            return id_map.get(value, _scope_legacy_id(sim_id, value))

        for row in legacy.query(Location).all():
            main_session.add(Location(
                entity_id=mid(row.entity_id),
                kami_id=mid(row.kami_id),
                container_id=mid(row.container_id),
                since_tick=row.since_tick,
                valid_until_tick=row.valid_until_tick,
            ))
        for row in legacy.query(PhysicalState).all():
            main_session.add(PhysicalState(
                entity_id=mid(row.entity_id),
                attribute=row.attribute,
                value=row.value,
                since_tick=row.since_tick,
                valid_until_tick=row.valid_until_tick,
            ))
        for row in legacy.query(Relation).all():
            main_session.add(Relation(
                from_entity=mid(row.from_entity),
                to_entity=mid(row.to_entity),
                rel_type=row.rel_type,
                weight=row.weight,
                since_tick=row.since_tick,
                valid_until_tick=row.valid_until_tick,
            ))
        for row in legacy.query(AgentBelief).all():
            main_session.add(AgentBelief(
                belief_id=f"{prefix}{row.belief_id}",
                agent_id=mid(row.agent_id),
                kind=row.kind,
                target_entity=mid(row.target_entity),
                attribute=row.attribute,
                believed_value=row.believed_value,
                confidence=row.confidence,
                since_tick=row.since_tick,
                source_event_id=f"{prefix}{row.source_event_id}" if row.source_event_id else None,
            ))
        for row in legacy.query(AgentNeed).all():
            main_session.add(AgentNeed(
                agent_id=mid(row.agent_id),
                need=row.need,
                value=row.value,
                since_tick=row.since_tick,
                valid_until_tick=row.valid_until_tick,
            ))
        for row in legacy.query(AgentIntentRecord).all():
            main_session.add(AgentIntentRecord(
                intent_id=f"{prefix}{row.intent_id}",
                tick=row.tick,
                agent_id=mid(row.agent_id),
                kami_id=mid(row.kami_id),
                action_type=row.action_type,
                target=mid(row.target) if row.target in id_map else row.target,
                params=row.params,
                salience=row.salience,
                status=row.status,
                result_event_id=f"{prefix}{row.result_event_id}" if row.result_event_id else None,
                result_summary=row.result_summary,
                blockers=row.blockers,
                pressure=row.pressure,
                created_at=row.created_at,
            ))
        for row in legacy.query(ConversationThread).all():
            main_session.add(ConversationThread(
                thread_id=f"{prefix}{row.thread_id}",
                kami_id=mid(row.kami_id),
                participants=[mid(item) for item in (row.participants or [])],
                topic=row.topic,
                status=row.status,
                tension=row.tension,
                momentum=row.momentum,
                last_event_id=f"{prefix}{row.last_event_id}" if row.last_event_id else None,
                last_tick=row.last_tick,
                summary=row.summary,
                open_question=row.open_question,
                created_at_tick=row.created_at_tick,
            ))
        for row in legacy.query(Event).all():
            main_session.add(Event(
                event_id=f"{prefix}{row.event_id}",
                tick=row.tick,
                kami_id=mid(row.kami_id),
                event_type=row.event_type,
                participants=[mid(item) for item in (row.participants or [])],
                payload=row.payload,
                salience=row.salience,
                narrative=row.narrative,
                causes=[f"{prefix}{item}" for item in (row.causes or [])],
            ))

        graph_data = None
        if record.get("graph_path") and Path(record["graph_path"]).exists():
            graph_data = json.loads(Path(record["graph_path"]).read_text(encoding="utf-8"))
        if graph_data:
            graph_data = {
                "nodes": [{**node, "id": mid(node["id"])} for node in graph_data.get("nodes", [])],
                "edges": [
                    {**edge, "source": mid(edge["source"]), "target": mid(edge["target"])}
                    for edge in graph_data.get("edges", [])
                ],
            }
        main_session.commit()
        record = {
            **record,
            "db_url": config.database_url,
            "db_path": str(_sqlite_path_from_url(config.database_url)),
            "graph_path": None,
            "graph_data": graph_data,
            "updated_at": _now_iso(),
        }
        logger.info("Migrated legacy simulation %s into shared database", record.get("name"))
        return record
    finally:
        legacy.close()
        legacy_engine.dispose()


def _migrate_legacy_registry_records(session) -> None:
    registry = _read_registry()
    changed = False
    migrated = []
    for record in registry.get("simulations", []):
        new_record = _migrate_legacy_file_record(record, session)
        migrated.append(new_record)
        changed = changed or new_record != record
    if changed:
        registry["simulations"] = migrated
        _write_registry(registry)


def _set_active_simulation(record: dict) -> None:
    db_url = config.database_url
    session_factory = sim_state.get("session_factory")
    if session_factory is None:
        engine, session_factory = init_db(db_url)
    else:
        engine = None
    session = session_factory()
    try:
        graph_data = record.get("graph_data")
        if not graph_data and record.get("graph_path"):
            path = Path(record["graph_path"])
            if path.exists():
                graph_data = json.loads(path.read_text(encoding="utf-8"))
                record["graph_data"] = graph_data
        spatial_graph = _load_graph_from_data(graph_data, session)
        scheduler = TickScheduler(session_factory=session_factory, spatial_graph=spatial_graph)
        scheduler.current_tick = _next_tick_for_record(session, record)
    finally:
        session.close()

    sim_state["scheduler"] = scheduler
    sim_state["session_factory"] = session_factory
    sim_state["spatial_graph"] = spatial_graph
    sim_state["active_simulation_id"] = record["id"]


def _update_active_cost(delta: float = 0.0) -> None:
    active_id = sim_state.get("active_simulation_id")
    if not active_id:
        return
    registry = _read_registry()
    for record in registry.get("simulations", []):
        if record.get("id") == active_id:
            record["total_cost_usd"] = float(record.get("total_cost_usd", 0.0)) + float(delta)
            record["updated_at"] = _now_iso()
            break
    _write_registry(registry)


def _next_tick_from_db(session) -> int:
    """Resume after the highest committed event tick in the current database."""
    max_tick = session.query(func.max(Event.tick)).scalar()
    return (max_tick + 1) if max_tick is not None else 0


def _next_tick_for_record(session, record: dict) -> int:
    kami_ids = _record_kami_ids(record)
    if not kami_ids:
        return _next_tick_from_db(session)
    max_tick = (
        session.query(func.max(Event.tick))
        .filter(Event.kami_id.in_(kami_ids))
        .scalar()
    )
    return (max_tick + 1) if max_tick is not None else 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    engine, session_factory = init_db(config.database_url)
    sim_state["session_factory"] = session_factory
    session = session_factory()
    try:
        _migrate_legacy_registry_records(session)
    finally:
        session.close()

    active = _active_record()
    if active:
        logger.info(f"Restoring active simulation: {active.get('name')}")
        _set_active_simulation(active)
    else:
        db_url = config.database_url
        session = session_factory()
        count = session.query(Entity).count()
        if count == 0:
            logger.info("Database empty, building default Oriv world...")
            spatial_graph = build_oriv_world(session)
            session.commit()
        else:
            logger.info(f"Database contains {count} entities, restoring default simulation state...")
            spatial_graph = _load_graph_from_path("sim_graph.json", session)
        session.close()

        default_id = "default"
        graph_path = "sim_graph.json"
        graph_data = spatial_graph.to_dict()
        record = {
            "id": default_id,
            "name": "Default World",
            "prompt": "Default restored world",
            "db_url": db_url,
            "db_path": str(_sqlite_path_from_url(db_url)),
            "graph_path": graph_path,
            "graph_data": graph_data,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "total_cost_usd": 0.0,
        }
        _register_or_update(record, active=True)
        _set_active_simulation(record)

    logger.info("Simulation initialized")
    yield
    logger.info("Shutting down")
    engine.dispose()


app = FastAPI(title="Kami Simulation", lifespan=lifespan)
GENERATED_ASSETS_PATH.mkdir(exist_ok=True)
app.mount("/generated", StaticFiles(directory=GENERATED_ASSETS_PATH), name="generated")

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
        "active_simulation_id": sim_state.get("active_simulation_id"),
    }


class LLMSettingsRequest(BaseModel):
    provider: str | None = None
    cheap_model: str | None = None
    strong_model: str | None = None
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    image_provider: str | None = None
    cheap_image_model: str | None = None
    strong_image_model: str | None = None


@app.get("/api/settings/llm")
async def get_llm_settings():
    return config.llm_settings()


@app.put("/api/settings/llm")
async def update_llm_settings(request: LLMSettingsRequest):
    try:
        settings = config.update_llm_settings(
            provider=request.provider,
            cheap_model=request.cheap_model,
            strong_model=request.strong_model,
            anthropic_api_key=request.anthropic_api_key,
            openai_api_key=request.openai_api_key,
            gemini_api_key=request.gemini_api_key,
            image_provider=request.image_provider,
            cheap_image_model=request.cheap_image_model,
            strong_image_model=request.strong_image_model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    llm_client.reset_clients()
    return settings


@app.get("/api/simulations")
async def list_simulations():
    registry = _read_registry()
    simulations = []
    for record in registry.get("simulations", []):
        try:
            simulations.append(_enrich_simulation_record(record))
        except Exception as e:
            simulations.append({**record, "error": str(e)})
    return {
        "active_id": registry.get("active_id"),
        "simulations": sorted(simulations, key=lambda item: item.get("updated_at", ""), reverse=True),
    }


@app.post("/api/simulations/{simulation_id}/switch")
async def switch_simulation(simulation_id: str):
    registry = _read_registry()
    record = next((item for item in registry.get("simulations", []) if item.get("id") == simulation_id), None)
    if not record:
        return {"error": "not found"}

    sim_state["running"] = False
    sim_state["paused"] = True
    record["updated_at"] = _now_iso()
    _register_or_update(record, active=True)
    _set_active_simulation(record)
    await _broadcast({"type": "simulation_switched", "data": {"id": simulation_id}})
    return {"status": "success", "active_id": simulation_id}


@app.delete("/api/simulations/{simulation_id}")
async def delete_simulation(simulation_id: str):
    registry = _read_registry()
    if registry.get("active_id") == simulation_id:
        return {"error": "cannot delete active simulation"}
    simulations = registry.get("simulations", [])
    record = next((item for item in simulations if item.get("id") == simulation_id), None)
    if not record:
        return {"error": "not found"}

    session = sim_state["session_factory"]()
    try:
        entity_ids = _record_entity_ids(record, session)
        if entity_ids:
            session.query(AgentIntentRecord).filter(AgentIntentRecord.agent_id.in_(entity_ids)).delete(synchronize_session=False)
            session.query(fs.AgentBelief).filter(fs.AgentBelief.agent_id.in_(entity_ids)).delete(synchronize_session=False)
            session.query(fs.AgentNeed).filter(fs.AgentNeed.agent_id.in_(entity_ids)).delete(synchronize_session=False)
            session.query(fs.ConversationThread).filter(fs.ConversationThread.kami_id.in_(entity_ids)).delete(synchronize_session=False)
            session.query(fs.Relation).filter(
                (fs.Relation.from_entity.in_(entity_ids)) | (fs.Relation.to_entity.in_(entity_ids))
            ).delete(synchronize_session=False)
            session.query(PhysicalState).filter(PhysicalState.entity_id.in_(entity_ids)).delete(synchronize_session=False)
            session.query(Location).filter(Location.entity_id.in_(entity_ids)).delete(synchronize_session=False)
            session.query(Event).filter(Event.kami_id.in_(entity_ids)).delete(synchronize_session=False)
            session.query(Entity).filter(Entity.entity_id.in_(entity_ids)).delete(synchronize_session=False)
            session.commit()
    finally:
        session.close()
    registry["simulations"] = [item for item in simulations if item.get("id") != simulation_id]
    _write_registry(registry)
    return {"status": "deleted"}


@app.get("/api/graph")
async def get_graph():
    sg: SpatialGraph = sim_state["spatial_graph"]
    return sg.to_dict() if sg else {"nodes": [], "edges": []}


class GenerateWorldMapRequest(BaseModel):
    style: str = "realism"
    tier: str = "strong"
    provider: str | None = None
    model: str | None = None
    size: str = "1536x1024"
    quality: str = "medium"


@app.get("/api/world-map/styles")
async def world_map_styles():
    return {
        "styles": [
            {"id": style_id, "label": style_id.replace("_", " ").title(), "description": description}
            for style_id, description in WORLD_MAP_STYLES.items()
        ]
    }


@app.get("/api/world-map/latest")
async def latest_world_map():
    active_id = sim_state.get("active_simulation_id") or "world"
    files = sorted(
        GENERATED_ASSETS_PATH.glob(f"{active_id}_*.png"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        return {"url": None}
    latest = files[0]
    style = next(
        (style_id for style_id in WORLD_MAP_STYLES if latest.stem.endswith(f"_{style_id}")),
        None,
    )
    return await _load_or_build_world_map_metadata(latest, style)


@app.post("/api/world-map/generate")
async def generate_world_map(request: GenerateWorldMapRequest):
    style_id = request.style if request.style in WORLD_MAP_STYLES else "realism"
    provider = (request.provider or config.image_provider or "openai").strip().lower()
    if provider not in {"openai", "gemini"}:
        raise HTTPException(status_code=422, detail=f"Unsupported image provider: {provider}")
    model = request.model or (
        config.cheap_image_model if request.tier == "cheap" else config.strong_image_model
    )
    prompt = _build_world_map_prompt(style_id)
    filename = f"{sim_state.get('active_simulation_id') or 'world'}_{uuid.uuid4().hex[:8]}_{style_id}.png"
    output_path = GENERATED_ASSETS_PATH / filename
    try:
        if provider == "openai":
            usage = await _generate_openai_world_map(prompt, model, request.size, request.quality, output_path)
        else:
            usage = await _generate_gemini_world_map(prompt, model, output_path)
    except Exception as exc:
        logger.error("World map generation failed", exc_info=True)
        raise HTTPException(status_code=422, detail=f"World map generation failed: {exc}") from exc
    metadata = await _build_world_map_metadata(output_path, style_id, provider, model, usage)
    return {**metadata, "prompt": prompt}


@app.get("/api/entity/{entity_id:path}")
async def get_entity(entity_id: str):
    session = sim_state["session_factory"]()
    try:
        entity = session.get(Entity, entity_id)
        if not entity:
            raise HTTPException(status_code=404, detail="Entity not found")

        location = fs.get_current_location(session, entity_id)
        kami_entity = session.get(Entity, location.kami_id) if location and location.kami_id else None
        container_entity = session.get(Entity, location.container_id) if location and location.container_id else None
        states = fs.get_state(session, entity_id)
        relations = fs.get_relations(session, entity_id, direction="both")
        recent_events = (
            session.query(Event)
            .order_by(Event.tick.desc())
            .limit(300)
            .all()
        )
        relevant_events = [
            event for event in recent_events
            if entity_id in (event.participants or [])
            or (event.kami_id == entity_id)
            or (isinstance(event.payload, dict) and entity_id in json.dumps(event.payload, ensure_ascii=False))
        ][:12]

        return {
            "entity_id": entity.entity_id,
            "name": entity.canonical_name,
            "kind": entity.kind,
            "archetype": entity.archetype or {},
            "created_at_tick": entity.created_at_tick,
            "created_by_event": entity.created_by_event,
            "location": {
                "kami_id": location.kami_id if location else None,
                "kami_name": kami_entity.canonical_name if kami_entity else None,
                "container_id": location.container_id if location else None,
                "container_name": container_entity.canonical_name if container_entity else None,
                "since_tick": location.since_tick if location else None,
            },
            "states": {state.attribute: state.value for state in states},
            "relations": [
                {
                    "from": relation.from_entity,
                    "to": relation.to_entity,
                    "type": relation.rel_type,
                    "weight": relation.weight,
                }
                for relation in relations[:20]
            ],
            "recent_events": [
                {
                    "event_id": event.event_id,
                    "tick": event.tick,
                    "event_type": event.event_type,
                    "kami_id": event.kami_id,
                    "narrative": event.narrative,
                    "participants": event.participants,
                }
                for event in relevant_events
            ],
        }
    finally:
        session.close()


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


def _max_tick(session) -> int:
    kami_ids = _active_kami_ids()
    q = session.query(func.max(Event.tick))
    if kami_ids:
        q = q.filter(Event.kami_id.in_(kami_ids))
    max_tick = q.scalar()
    return int(max_tick) if max_tick is not None else 0


def _location_at_tick(session, entity_id: str, tick: int):
    return (
        session.query(Location)
        .filter(
            Location.entity_id == entity_id,
            Location.since_tick <= tick,
            ((Location.valid_until_tick.is_(None)) | (Location.valid_until_tick > tick)),
        )
        .order_by(Location.since_tick.desc())
        .first()
    )


def _states_at_tick(session, entity_id: str, tick: int) -> dict:
    rows = (
        session.query(PhysicalState)
        .filter(
            PhysicalState.entity_id == entity_id,
            PhysicalState.since_tick <= tick,
            ((PhysicalState.valid_until_tick.is_(None)) | (PhysicalState.valid_until_tick > tick)),
        )
        .order_by(PhysicalState.attribute, PhysicalState.since_tick.desc())
        .all()
    )
    states = {}
    for row in rows:
        if row.attribute not in states:
            states[row.attribute] = row.value
    return states


def _thought_at_tick(agent_id: str, tick: int) -> str | None:
    scheduler = sim_state.get("scheduler")
    if not scheduler:
        return None
    for row in getattr(scheduler, "tick_log", []):
        if row.get("tick") == tick:
            return (row.get("monologues") or {}).get(agent_id)
    return None


@app.get("/api/timeline")
async def get_timeline(
    mode: str = Query("agents", pattern="^(agents|kami)$"),
    since_tick: int = 0,
    until_tick: int | None = None,
):
    session = sim_state["session_factory"]()
    try:
        max_tick = _max_tick(session)
        end_tick = max_tick if until_tick is None else min(until_tick, max_tick)
        start_tick = max(0, min(since_tick, end_tick))
        ticks = list(range(start_tick, end_tick + 1))

        if mode == "agents":
            kami_ids = _active_kami_ids()
            agent_ids = [
                row.entity_id
                for row in session.query(Location.entity_id)
                .filter(Location.kami_id.in_(kami_ids), Location.valid_until_tick.is_(None))
                .all()
            ]
            entities = (
                session.query(Entity)
                .filter(Entity.kind == "agent", Entity.entity_id.in_(agent_ids))
                .order_by(Entity.canonical_name)
                .all()
            )
        else:
            kami_ids = _active_kami_ids()
            entities = (
                session.query(Entity)
                .filter(Entity.kind == "kami", Entity.entity_id.in_(kami_ids))
                .order_by(Entity.canonical_name)
                .all()
            )

        rows = []
        events = (
            session.query(Event)
            .filter(Event.tick >= start_tick, Event.tick <= end_tick)
            .filter(Event.kami_id.in_(_active_kami_ids()))
            .order_by(Event.tick.asc())
            .all()
        )
        intents = []
        if mode == "agents":
            intents = (
                session.query(AgentIntentRecord)
                .filter(AgentIntentRecord.tick >= start_tick, AgentIntentRecord.tick <= end_tick)
                .all()
            )

        for entity in entities:
            cells: dict[int, dict] = {}
            for event in events:
                involved = (
                    event.kami_id == entity.entity_id
                    if mode == "kami"
                    else entity.entity_id in (event.participants or [])
                )
                if not involved:
                    continue
                cell = cells.setdefault(event.tick, {
                    "tick": event.tick,
                    "event_count": 0,
                    "salience": 0.0,
                    "event_types": [],
                    "narratives": [],
                })
                cell["event_count"] += 1
                cell["salience"] = max(cell["salience"], event.salience or 0.0)
                if event.event_type not in cell["event_types"]:
                    cell["event_types"].append(event.event_type)
                if event.narrative:
                    cell["narratives"].append(event.narrative)

            if mode == "agents":
                for intent in intents:
                    if intent.agent_id != entity.entity_id:
                        continue
                    cell = cells.setdefault(intent.tick, {
                        "tick": intent.tick,
                        "event_count": 0,
                        "salience": intent.salience or 0.2,
                        "event_types": [],
                        "narratives": [],
                    })
                    cell["intent"] = {
                        "action_type": intent.action_type,
                        "target": intent.target,
                        "status": intent.status,
                        "summary": intent.result_summary,
                    }
                    cell["salience"] = max(cell["salience"], intent.salience or 0.2)
                    if "intent" not in cell["event_types"]:
                        cell["event_types"].append("intent")

                for tick in ticks:
                    thought = _thought_at_tick(entity.entity_id, tick)
                    if thought:
                        cell = cells.setdefault(tick, {
                            "tick": tick,
                            "event_count": 0,
                            "salience": 0.25,
                            "event_types": ["thought"],
                            "narratives": [],
                        })
                        cell["thought"] = thought

            rows.append({
                "id": entity.entity_id,
                "name": entity.canonical_name,
                "kind": entity.kind,
                "archetype": entity.archetype or {},
                "cells": sorted(cells.values(), key=lambda item: item["tick"]),
            })

        return {"mode": mode, "ticks": ticks, "rows": rows}
    finally:
        session.close()


@app.get("/api/timeline/snapshot/{kind}/{entity_id}")
async def get_timeline_snapshot(kind: str, entity_id: str, tick: int):
    session = sim_state["session_factory"]()
    try:
        entity = session.get(Entity, entity_id)
        if not entity:
            return {"error": "not found"}

        if kind == "agent":
            location = _location_at_tick(session, entity_id, tick)
            states = _states_at_tick(session, entity_id, tick)
            events = (
                session.query(Event)
                .filter(Event.tick <= tick)
                .order_by(Event.tick.desc())
                .limit(250)
                .all()
            )
            action_history = [
                {
                    "tick": e.tick,
                    "event_type": e.event_type,
                    "narrative": e.narrative,
                    "salience": e.salience,
                }
                for e in events
                if entity_id in (e.participants or [])
            ][:20]
            thought = _thought_at_tick(entity_id, tick)
            recent_thoughts = [{"tick": tick, "thought": thought}] if thought else []
            return {
                "snapshot_tick": tick,
                "entity_id": entity.entity_id,
                "name": entity.canonical_name,
                "archetype": entity.archetype,
                "location": {
                    "kami_id": location.kami_id if location else None,
                    "since_tick": location.since_tick if location else None,
                },
                "states": states,
                "relations": [
                    {
                        "from": r.from_entity,
                        "to": r.to_entity,
                        "type": r.rel_type,
                        "weight": r.weight,
                    }
                    for r in fs.get_relations(session, entity_id, direction="both")
                ],
                "beliefs": [{"kind": b.kind, "value": b.believed_value, "confidence": b.confidence} for b in fs.get_beliefs(session, entity_id)],
                "recent_thoughts": recent_thoughts,
                "action_history": action_history,
            }

        if kind == "kami":
            state = fs.query_kami_state(session, entity_id)
            state["entities"] = []
            locations = (
                session.query(Location)
                .filter(
                    Location.kami_id == entity_id,
                    Location.since_tick <= tick,
                    ((Location.valid_until_tick.is_(None)) | (Location.valid_until_tick > tick)),
                )
                .all()
            )
            for loc in locations:
                contained = session.get(Entity, loc.entity_id)
                if not contained:
                    continue
                state["entities"].append({
                    "entity_id": contained.entity_id,
                    "kind": contained.kind,
                    "name": contained.canonical_name,
                    "archetype": contained.archetype,
                    "states": _states_at_tick(session, contained.entity_id, tick),
                })
            state["entity_count"] = len(state["entities"])
            state["snapshot_tick"] = tick
            state["recent_events"] = [
                {
                    "event_id": e.event_id,
                    "tick": e.tick,
                    "event_type": e.event_type,
                    "narrative": e.narrative,
                    "salience": e.salience,
                    "participants": e.participants,
                }
                for e in fs.get_events(session, kami_id=entity_id, until_tick=tick, limit=50)
            ]
            return state

        return {"error": "invalid kind"}
    finally:
        session.close()


@app.get("/api/agents")
async def get_all_agents():
    session = sim_state["session_factory"]()
    try:
        from ..factstore.models import Entity
        from ..factstore.tools import get_current_location
        agents = session.query(Entity).filter(Entity.kind == "agent").all()
        kami_ids = _active_kami_ids()
        active_locations = {
            row.entity_id: row
            for row in session.query(Location)
            .filter(Location.kami_id.in_(kami_ids), Location.valid_until_tick.is_(None))
            .all()
        }
        results = []
        recent_events = (
            session.query(fs.Event)
            .filter(fs.Event.kami_id.in_(kami_ids))
            .order_by(fs.Event.tick.desc())
            .limit(500)
            .all()
        )
        for a in agents:
            if a.entity_id not in active_locations:
                continue
            loc = active_locations.get(a.entity_id)
            kami_id = loc.kami_id if loc else None
            kami_entity = session.get(Entity, kami_id) if kami_id else None
            latest_event = None
            for e in recent_events:
                if a.entity_id in (e.participants or []):
                    latest_event = {
                        "tick": e.tick,
                        "event_type": e.event_type,
                        "narrative": e.narrative,
                    }
                    break
            results.append({
                "entity_id": a.entity_id, 
                "name": a.canonical_name, 
                "role": a.archetype.get("role", "Unknown"),
                "kami_id": kami_id,
                "location_name": kami_entity.canonical_name if kami_entity else None,
                "latest_event": latest_event,
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
        if kami_id is None:
            active = set(_active_kami_ids())
            events = [event for event in events if event.kami_id in active]
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
    _update_active_cost(sum(float(result.get("tick_cost_usd", 0.0)) for result in results))

    # Broadcast to WebSocket clients
    for result in results:
        await _broadcast({"type": "tick", "data": result})

    return {"ticks_run": len(results), "results": results}


class CreateSimRequest(BaseModel):
    prompt: str
    agent_count: int = 10
    name: str | None = None

@app.post("/api/sim/create")
async def create_sim(request: CreateSimRequest):
    sim_state["running"] = False
    sim_state["paused"] = True

    logger.info(f"Generating World: {request.prompt} ({request.agent_count} agents)")
    try:
        world_output = await build_world(
            request.prompt,
            agent_count=request.agent_count,
            name=request.name,
        )
    except Exception as exc:
        logger.error("World generation failed", exc_info=True)
        raise HTTPException(
            status_code=422,
            detail=f"World generation failed: {exc}",
        ) from exc
    
    sim_id = uuid.uuid4().hex[:10]
    db_url = config.database_url
    session_factory = sim_state["session_factory"]
    session = session_factory()
    
    spatial_graph = load_world_into_db(session, world_output, simulation_id=sim_id)
    session.close()
    graph_data = spatial_graph.to_dict()

    record = {
        "id": sim_id,
        "name": request.name or (request.prompt[:48] + ("..." if len(request.prompt) > 48 else "")),
        "prompt": request.prompt,
        "db_url": db_url,
        "db_path": str(_sqlite_path_from_url(db_url)),
        "graph_path": None,
        "graph_data": graph_data,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        "total_cost_usd": 0.0,
    }
    _register_or_update(record, active=True)
    _set_active_simulation(record)
    await _broadcast({"type": "simulation_switched", "data": {"id": sim_id}})

    return {"status": "success", "simulation": _enrich_simulation_record(record)}


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
            _update_active_cost(sum(float(result.get("tick_cost_usd", 0.0)) for result in results))
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
