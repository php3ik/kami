"""Durable episodic memory with a rebuildable Chroma retrieval index."""

from __future__ import annotations

import hashlib
import logging
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..factstore.models import AgentMemoryProfile, Entity, EpisodicMemoryRecord

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 128
VALID_VECTOR_BACKENDS = {"sql", "chroma"}


@dataclass
class EpisodicMemory:
    """A single L0 memory entry."""

    memory_id: str
    agent_id: str
    tick: int
    content: str
    importance: float = 0.5
    participants: list[str] = field(default_factory=list)
    location: str = ""
    event_type: str = ""
    embedding: list[float] | None = None
    simulation_id: str = "default"
    source_event_id: str | None = None


def deterministic_embedding(text: str) -> list[float]:
    """Generate a stable local hashing embedding without model downloads."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    tokens = re.findall(r"[\w'-]+", text.casefold(), flags=re.UNICODE)
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        return [value / norm for value in vector]
    return vector


class EpisodicStore:
    """SQL-backed episodic store with simulation-owned Chroma collections."""

    def __init__(
        self,
        session_factory=None,
        chroma_path: str | Path | None = None,
        vector_backend: str = "sql",
    ):
        vector_backend = vector_backend.strip().lower()
        if vector_backend not in VALID_VECTOR_BACKENDS:
            raise ValueError(f"Unsupported memory vector backend: {vector_backend}")
        self.session_factory = session_factory
        self.chroma_path = Path(chroma_path) if chroma_path else None
        self.vector_backend = vector_backend
        self._client = None
        self._memories: dict[str, list[EpisodicMemory]] = {}

    def add_memory(
        self, memory: EpisodicMemory, session: Session | None = None
    ) -> EpisodicMemory:
        if self.session_factory is None and session is None:
            self._memories.setdefault(memory.agent_id, []).append(memory)
            return memory

        owns_session = session is None
        db = session or self.session_factory()
        try:
            agent = db.get(Entity, memory.agent_id)
            if agent is None or agent.kind != "agent":
                raise ValueError(f"Agent not found: {memory.agent_id}")
            scope = memory.simulation_id or agent.simulation_id
            if agent.simulation_id != scope:
                raise ValueError("Cannot store memory in another simulation")
            existing = db.get(EpisodicMemoryRecord, memory.memory_id)
            if existing is not None:
                if (
                    existing.simulation_id != scope
                    or existing.agent_id != memory.agent_id
                ):
                    raise ValueError(
                        "Memory identifier belongs to another agent or simulation"
                    )
            else:
                db.add(
                    EpisodicMemoryRecord(
                        memory_id=memory.memory_id,
                        simulation_id=scope,
                        agent_id=memory.agent_id,
                        tick=memory.tick,
                        content=memory.content,
                        importance=max(0.0, min(1.0, float(memory.importance))),
                        participants=list(memory.participants),
                        location=memory.location,
                        event_type=memory.event_type,
                        source_event_id=memory.source_event_id,
                    )
                )
            if owns_session:
                db.commit()
                self._index_memories(scope, [memory])
            else:
                db.flush()
            memory.simulation_id = scope
            return memory
        except Exception:
            if owns_session:
                db.rollback()
            raise
        finally:
            if owns_session:
                db.close()

    def stage_event_memories(
        self,
        session: Session,
        simulation_id: str,
        events: list[dict],
    ) -> list[EpisodicMemory]:
        """Stage participant memories inside the canonical tick transaction."""
        staged: list[EpisodicMemory] = []
        for event in events:
            event_id = event.get("event_id")
            if not event_id:
                continue
            participant_ids = set(event.get("participants") or [])
            payload = event.get("payload") or {}
            participant_ids.update(payload.get("observers") or [])
            for agent_id in sorted(participant_ids):
                agent = session.get(Entity, agent_id)
                if (
                    agent is None
                    or agent.kind != "agent"
                    or agent.simulation_id != simulation_id
                ):
                    continue
                memory_id = "mem_" + hashlib.sha256(
                    f"{simulation_id}:{agent_id}:{event_id}".encode("utf-8")
                ).hexdigest()[:24]
                memory = EpisodicMemory(
                    memory_id=memory_id,
                    simulation_id=simulation_id,
                    agent_id=agent_id,
                    tick=int(event.get("tick", 0)),
                    content=str(event.get("narrative") or event.get("event_type") or ""),
                    importance=float(event.get("salience", 0.3)),
                    participants=sorted(participant_ids),
                    location=str(event.get("kami_id") or ""),
                    event_type=str(event.get("event_type") or ""),
                    source_event_id=event_id,
                )
                self.add_memory(memory, session=session)
                staged.append(memory)
        return staged

    def index_committed(self, memories: list[EpisodicMemory]) -> None:
        by_scope: dict[str, list[EpisodicMemory]] = {}
        for memory in memories:
            by_scope.setdefault(memory.simulation_id, []).append(memory)
        for scope, scoped_memories in by_scope.items():
            self._index_memories(scope, scoped_memories)

    def recall(
        self,
        agent_id: str,
        query: str = "",
        k: int = 5,
        present_agents: list[str] | None = None,
        current_tick: int = 0,
        simulation_id: str | None = None,
    ) -> list[EpisodicMemory]:
        if self.session_factory is None:
            memories = list(self._memories.get(agent_id, []))
            return self._rank(memories, query, k, present_agents, current_tick)

        scope, memories = self._load_agent_memories(agent_id, simulation_id)
        if not memories:
            return []
        candidates = memories
        if query and self.chroma_path and self.vector_backend == "chroma":
            try:
                self._index_memories(scope, memories)
                collection = self._collection(scope)
                result = collection.query(
                    query_embeddings=[deterministic_embedding(query)],
                    n_results=min(len(memories), max(k * 4, k)),
                    where={"agent_id": agent_id},
                    include=["distances"],
                )
                candidate_ids = (result.get("ids") or [[]])[0]
                by_id = {memory.memory_id: memory for memory in memories}
                candidates = [by_id[item] for item in candidate_ids if item in by_id]
            except Exception:
                logger.exception("Chroma recall failed; using SQL fallback")
                candidates = memories
        return self._rank(candidates, query, k, present_agents, current_tick)

    def get_recent(
        self, agent_id: str, n: int = 10, simulation_id: str | None = None
    ) -> list[EpisodicMemory]:
        if self.session_factory is None:
            memories = self._memories.get(agent_id, [])
            return sorted(memories, key=lambda memory: memory.tick, reverse=True)[:n]
        _, memories = self._load_agent_memories(agent_id, simulation_id)
        return sorted(memories, key=lambda memory: memory.tick, reverse=True)[:n]

    def get_day_memories(
        self,
        agent_id: str,
        day_start_tick: int,
        day_end_tick: int,
        simulation_id: str | None = None,
    ) -> list[EpisodicMemory]:
        memories = self.get_recent(agent_id, n=100_000, simulation_id=simulation_id)
        return [
            memory
            for memory in memories
            if day_start_tick <= memory.tick <= day_end_tick
        ]

    def agents_with_memories(
        self, simulation_id: str, start_tick: int, end_tick: int
    ) -> list[str]:
        if self.session_factory is None:
            return sorted(self._memories)
        session = self.session_factory()
        try:
            rows = (
                session.query(EpisodicMemoryRecord.agent_id)
                .filter(
                    EpisodicMemoryRecord.simulation_id == simulation_id,
                    EpisodicMemoryRecord.tick >= start_tick,
                    EpisodicMemoryRecord.tick <= end_tick,
                )
                .distinct()
                .all()
            )
            return sorted(row[0] for row in rows)
        finally:
            session.close()

    def count(self, agent_id: str, simulation_id: str | None = None) -> int:
        if self.session_factory is None:
            return len(self._memories.get(agent_id, []))
        _, memories = self._load_agent_memories(agent_id, simulation_id)
        return len(memories)

    def delete_simulation(self, simulation_id: str) -> None:
        if self.vector_backend != "chroma":
            return
        try:
            client = self._chroma_client()
            client.delete_collection(self._collection_name(simulation_id))
        except Exception as exc:
            if "does not exist" not in str(exc).lower():
                logger.warning("Could not delete Chroma memory collection: %s", exc)

    def _load_agent_memories(
        self, agent_id: str, simulation_id: str | None
    ) -> tuple[str, list[EpisodicMemory]]:
        session = self.session_factory()
        try:
            agent = session.get(Entity, agent_id)
            if agent is None or agent.kind != "agent":
                return simulation_id or "default", []
            scope = simulation_id or agent.simulation_id
            if scope != agent.simulation_id:
                return scope, []
            rows = (
                session.query(EpisodicMemoryRecord)
                .filter(
                    EpisodicMemoryRecord.simulation_id == scope,
                    EpisodicMemoryRecord.agent_id == agent_id,
                )
                .order_by(EpisodicMemoryRecord.tick.desc())
                .all()
            )
            return scope, [self._from_row(row) for row in rows]
        finally:
            session.close()

    @staticmethod
    def _from_row(row: EpisodicMemoryRecord) -> EpisodicMemory:
        return EpisodicMemory(
            memory_id=row.memory_id,
            simulation_id=row.simulation_id,
            agent_id=row.agent_id,
            tick=row.tick,
            content=row.content,
            importance=float(row.importance),
            participants=list(row.participants or []),
            location=row.location,
            event_type=row.event_type,
            source_event_id=row.source_event_id,
        )

    @staticmethod
    def _rank(
        memories: list[EpisodicMemory],
        query: str,
        k: int,
        present_agents: list[str] | None,
        current_tick: int,
    ) -> list[EpisodicMemory]:
        present = set(present_agents or [])
        query_words = set(re.findall(r"[\w'-]+", query.casefold()))
        query_vector = deterministic_embedding(query) if query_words else []

        def score(memory: EpisodicMemory) -> float:
            age = max(1, current_tick - memory.tick)
            recency = 1.0 / (1.0 + age * 0.01)
            social = 1.0 + len(set(memory.participants) & present) * 0.5
            relevance = 0.5
            if query_words:
                content_words = set(re.findall(r"[\w'-]+", memory.content.casefold()))
                lexical = min(1.0, 0.3 + len(query_words & content_words) * 0.2)
                memory_vector = deterministic_embedding(memory.content)
                cosine = sum(
                    left * right for left, right in zip(query_vector, memory_vector)
                )
                relevance = max(0.1, lexical * 0.6 + max(0.0, cosine) * 0.4)
            return recency * relevance * memory.importance * social

        return sorted(memories, key=score, reverse=True)[: max(0, k)]

    def _index_memories(
        self, simulation_id: str, memories: list[EpisodicMemory]
    ) -> None:
        if (
            self.vector_backend != "chroma"
            or not self.chroma_path
            or not memories
        ):
            return
        collection = self._collection(simulation_id)
        collection.upsert(
            ids=[memory.memory_id for memory in memories],
            documents=[memory.content for memory in memories],
            embeddings=[
                memory.embedding or deterministic_embedding(memory.content)
                for memory in memories
            ],
            metadatas=[
                {
                    "simulation_id": simulation_id,
                    "agent_id": memory.agent_id,
                    "tick": memory.tick,
                    "importance": float(memory.importance),
                }
                for memory in memories
            ],
        )

    def _chroma_client(self):
        if self._client is None:
            import chromadb

            self.chroma_path.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self.chroma_path))
        return self._client

    def _collection(self, simulation_id: str):
        return self._chroma_client().get_or_create_collection(
            name=self._collection_name(simulation_id),
            metadata={"simulation_id": simulation_id, "owner": "kami"},
        )

    @staticmethod
    def _collection_name(simulation_id: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", simulation_id).strip("_-")
        safe = safe[:35] or "default"
        digest = hashlib.sha256(simulation_id.encode("utf-8")).hexdigest()[:10]
        return f"kami_memory_{safe}_{digest}"


def seed_archetype_memories(
    session: Session,
    agent: Entity,
    memories: list[Any],
    life_narrative: str = "",
) -> list[EpisodicMemory]:
    """Stage deterministic initial memories from generated agent backstories."""
    store = EpisodicStore()
    staged: list[EpisodicMemory] = []
    for index, raw in enumerate(memories):
        item = raw if isinstance(raw, dict) else {"content": str(raw)}
        content = str(item.get("content") or item.get("memory") or "").strip()
        if not content:
            continue
        memory_id = "mem_" + hashlib.sha256(
            f"{agent.simulation_id}:{agent.entity_id}:seed:{index}:{content}".encode("utf-8")
        ).hexdigest()[:24]
        memory = EpisodicMemory(
            memory_id=memory_id,
            simulation_id=agent.simulation_id,
            agent_id=agent.entity_id,
            tick=0,
            content=content,
            importance=float(item.get("importance", 0.5)),
            participants=list(item.get("participants") or []),
            event_type="backstory",
        )
        store.add_memory(memory, session=session)
        staged.append(memory)
    if life_narrative:
        profile = session.get(AgentMemoryProfile, agent.entity_id)
        if profile is None:
            profile = AgentMemoryProfile(
                agent_id=agent.entity_id,
                simulation_id=agent.simulation_id,
            )
            session.add(profile)
        profile.life_narrative = life_narrative
        session.flush()
    return staged
