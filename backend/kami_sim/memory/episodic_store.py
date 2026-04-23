"""Episodic memory store — vector store wrapper (spec §2.6).

L0 raw episodic memory with embedding-based retrieval.
Hybrid scoring: recency * relevance * importance * social_salience.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EpisodicMemory:
    """A single episodic memory entry."""

    memory_id: str
    agent_id: str
    tick: int
    content: str
    importance: float = 0.5
    participants: list[str] = field(default_factory=list)
    location: str = ""
    event_type: str = ""
    embedding: list[float] | None = None


class EpisodicStore:
    """In-memory episodic store. ChromaDB integration for Phase 4."""

    def __init__(self):
        self._memories: dict[str, list[EpisodicMemory]] = {}  # agent_id -> memories

    def add_memory(self, memory: EpisodicMemory):
        if memory.agent_id not in self._memories:
            self._memories[memory.agent_id] = []
        self._memories[memory.agent_id].append(memory)

    def recall(
        self,
        agent_id: str,
        query: str = "",
        k: int = 5,
        present_agents: list[str] | None = None,
        current_tick: int = 0,
    ) -> list[EpisodicMemory]:
        """Retrieve top-k memories using hybrid scoring.

        Score = recency * relevance * importance * social_salience
        For MVP (no embeddings), uses keyword matching for relevance.
        """
        memories = self._memories.get(agent_id, [])
        if not memories:
            return []

        present = set(present_agents or [])

        def score(m: EpisodicMemory) -> float:
            # Recency: exponential decay
            age = max(1, current_tick - m.tick)
            recency = 1.0 / (1.0 + age * 0.01)

            # Importance
            importance = m.importance

            # Social salience: boost if participants are present
            social = 1.0
            if present:
                overlap = len(set(m.participants) & present)
                social = 1.0 + overlap * 0.5

            # Relevance: keyword match (placeholder for embedding similarity)
            relevance = 0.5
            if query:
                query_words = set(query.lower().split())
                content_words = set(m.content.lower().split())
                overlap = len(query_words & content_words)
                relevance = min(1.0, 0.3 + overlap * 0.2)

            return recency * relevance * importance * social

        scored = sorted(memories, key=score, reverse=True)
        return scored[:k]

    def get_recent(self, agent_id: str, n: int = 10) -> list[EpisodicMemory]:
        memories = self._memories.get(agent_id, [])
        return sorted(memories, key=lambda m: m.tick, reverse=True)[:n]

    def get_day_memories(
        self, agent_id: str, day_start_tick: int, day_end_tick: int
    ) -> list[EpisodicMemory]:
        memories = self._memories.get(agent_id, [])
        return [m for m in memories if day_start_tick <= m.tick <= day_end_tick]

    def count(self, agent_id: str) -> int:
        return len(self._memories.get(agent_id, []))
