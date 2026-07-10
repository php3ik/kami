"""Durable daily memory and permanent imprints for spatial Kami."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import config
from ..factstore.models import (
    Entity,
    Event,
    KamiImprint,
    KamiMemoryProfile,
    KamiMemorySummary,
)
from ..llm.client import llm_client

logger = logging.getLogger(__name__)

AUTO_IMPRINT_SALIENCE = 0.9


def _utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, float(value)))


def imprint_on_kami(
    session: Session,
    kami_id: str,
    fact: str,
    tick: int,
    importance: float = 0.9,
    category: str = "event",
    source_event_id: str | None = None,
    simulation_id: str | None = None,
) -> KamiImprint:
    """Stage a permanent place fact inside the caller's transaction."""
    value = fact.strip()
    if not value:
        raise ValueError("Kami imprint fact cannot be empty")
    kami = session.get(Entity, kami_id)
    if kami is None or kami.kind != "kami":
        raise ValueError(f"Kami not found: {kami_id}")
    scope = simulation_id or kami.simulation_id
    if scope != kami.simulation_id:
        raise ValueError("Cannot imprint another simulation")
    seed = source_event_id or uuid.uuid4().hex
    imprint_id = "kimp_" + hashlib.sha256(
        f"{scope}:{kami_id}:{seed}".encode("utf-8")
    ).hexdigest()[:24]
    existing = session.get(KamiImprint, imprint_id)
    if existing is not None:
        if existing.simulation_id != scope or existing.kami_id != kami_id:
            raise ValueError("Kami imprint identifier belongs to another simulation")
        return existing
    existing_fact = (
        session.query(KamiImprint)
        .filter(
            KamiImprint.simulation_id == scope,
            KamiImprint.kami_id == kami_id,
            KamiImprint.fact == value[:4000],
        )
        .one_or_none()
    )
    if existing_fact is not None:
        existing_fact.importance = max(
            float(existing_fact.importance), _clamp(importance, 0.8)
        )
        return existing_fact
    row = KamiImprint(
        imprint_id=imprint_id,
        simulation_id=scope,
        kami_id=kami_id,
        tick=int(tick),
        fact=value[:4000],
        importance=_clamp(importance, 0.8),
        category=category.strip()[:100] or "event",
        source_event_id=source_event_id,
    )
    session.add(row)
    session.flush()
    return row


class KamiMemoryStore:
    def __init__(self, session_factory=None):
        self.session_factory = session_factory

    def stage_event_imprints(
        self, session: Session, simulation_id: str, events: list[dict]
    ) -> list[KamiImprint]:
        if self.session_factory is None:
            return []
        staged: list[KamiImprint] = []
        for event in events:
            kami_id = str(event.get("kami_id") or "")
            event_id = str(event.get("event_id") or "")
            if not kami_id or not event_id:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            explicit = payload.get("imprint")
            try:
                salience = _clamp(float(event.get("salience", 0.0)))
            except (TypeError, ValueError):
                salience = 0.0
            if not explicit and not payload.get("permanent") and salience < AUTO_IMPRINT_SALIENCE:
                continue
            fact = (
                explicit
                if isinstance(explicit, str) and explicit.strip()
                else str(event.get("narrative") or event.get("event_type") or "")
            )
            if not fact.strip():
                continue
            try:
                staged.append(
                    imprint_on_kami(
                        session,
                        kami_id,
                        fact,
                        int(event.get("tick", 0)),
                        importance=max(AUTO_IMPRINT_SALIENCE, salience),
                        category=str(event.get("event_type") or "event"),
                        source_event_id=event_id,
                        simulation_id=simulation_id,
                    )
                )
            except ValueError:
                logger.warning(
                    "Ignored out-of-scope Kami imprint for event %s", event_id
                )
        return staged

    async def consolidate_if_due(
        self, simulation_id: str, tick: int
    ) -> list[dict]:
        if self.session_factory is None:
            return []
        ticks_per_day = max(1, round(1440 / max(1, config.tick_in_sim_minutes)))
        if (tick + 1) % ticks_per_day != 0:
            return []
        start_tick = max(0, tick - ticks_per_day + 1)
        session = self.session_factory()
        try:
            rows = (
                session.query(Event.kami_id)
                .filter(
                    Event.simulation_id == simulation_id,
                    Event.tick >= start_tick,
                    Event.tick <= tick,
                    Event.kami_id.is_not(None),
                )
                .distinct()
                .all()
            )
            kami_ids = sorted(row[0] for row in rows if row[0])
        finally:
            session.close()
        results = []
        for kami_id in kami_ids:
            result = await self.consolidate_kami(
                simulation_id, kami_id, start_tick, tick
            )
            if result:
                results.append(result)
        return results

    async def consolidate_kami(
        self,
        simulation_id: str,
        kami_id: str,
        start_tick: int,
        end_tick: int,
    ) -> dict | None:
        session = self.session_factory()
        try:
            kami = session.get(Entity, kami_id)
            if (
                kami is None
                or kami.kind != "kami"
                or kami.simulation_id != simulation_id
            ):
                return None
            summary_id = self._summary_id(simulation_id, kami_id, end_tick)
            existing = session.get(KamiMemorySummary, summary_id)
            if existing is not None:
                return self._summary_dict(existing, idempotent=True)
            events = (
                session.query(Event)
                .filter(
                    Event.simulation_id == simulation_id,
                    Event.kami_id == kami_id,
                    Event.tick >= start_tick,
                    Event.tick <= end_tick,
                )
                .order_by(Event.tick.asc(), Event.event_id.asc())
                .limit(300)
                .all()
            )
            if not events:
                return None
            imprints = (
                session.query(KamiImprint)
                .filter(
                    KamiImprint.simulation_id == simulation_id,
                    KamiImprint.kami_id == kami_id,
                )
                .order_by(KamiImprint.importance.desc(), KamiImprint.tick.desc())
                .limit(20)
                .all()
            )
            name = kami.canonical_name
            identity = dict(kami.archetype or {})
            event_payload = [
                {
                    "tick": event.tick,
                    "type": event.event_type,
                    "salience": float(event.salience),
                    "narrative": event.narrative,
                }
                for event in events
            ]
            imprint_facts = [row.fact for row in imprints]
        finally:
            session.close()

        summary = await self._summarize_day(
            name, identity, event_payload, imprint_facts, end_tick
        )
        peak_salience = max(item["salience"] for item in event_payload)
        session = self.session_factory()
        try:
            row = KamiMemorySummary(
                summary_id=summary_id,
                simulation_id=simulation_id,
                kami_id=kami_id,
                tick=end_tick,
                summary=summary,
                event_count=len(event_payload),
                peak_salience=peak_salience,
            )
            session.add(row)
            session.flush()
            profile = session.get(KamiMemoryProfile, kami_id)
            if profile is None:
                profile = KamiMemoryProfile(
                    kami_id=kami_id,
                    simulation_id=simulation_id,
                )
                session.add(profile)
            profile.long_term_memory = self._compose_long_term(
                session, simulation_id, kami_id, pending_summary=row
            )
            profile.last_consolidation_tick = end_tick
            profile.updated_at = _utcnow_naive()
            session.commit()
            return self._summary_dict(row)
        except IntegrityError:
            session.rollback()
            existing = session.get(KamiMemorySummary, summary_id)
            return self._summary_dict(existing, idempotent=True) if existing else None
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_prompt_context(self, kami_id: str, simulation_id: str) -> str:
        if self.session_factory is None:
            return ""
        session = self.session_factory()
        try:
            kami = session.get(Entity, kami_id)
            if (
                kami is None
                or kami.kind != "kami"
                or kami.simulation_id != simulation_id
            ):
                return ""
            profile = session.get(KamiMemoryProfile, kami_id)
            composed = self._compose_long_term(session, simulation_id, kami_id)
            if composed:
                return composed
            if profile and profile.simulation_id == simulation_id:
                return profile.long_term_memory[:6000]
            return ""
        finally:
            session.close()

    async def _summarize_day(
        self,
        name: str,
        identity: dict,
        events: list[dict],
        imprints: list[str],
        tick: int,
    ) -> str:
        fallback = " ".join(item["narrative"].strip() for item in events if item["narrative"])
        fallback = fallback[:3000] or "The location remained quiet."
        if not self._llm_available():
            return fallback
        event_text = "\n".join(
            f"- [tick {item['tick']}] ({item['type']}, salience={item['salience']:.2f}) "
            f"{item['narrative']}"
            for item in events
        )
        try:
            response = await llm_client.call(
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Location: {name}\n"
                            f"Description: {identity.get('description', '')}\n"
                            f"Permanent imprints: {imprints}\n\n"
                            f"Canonical events:\n{event_text}\n\n"
                            "Compress this day into one grounded paragraph of 120-220 words. "
                            "Preserve causal changes, social consequences, and physical traces. "
                            "Do not invent events."
                        ),
                    }
                ],
                system="You write durable spatial memory for a simulated place.",
                tier="cheap",
                component="KamiConsolidator",
                tick=tick,
                max_tokens=350,
                temperature=0.2,
            )
            return str(response.get("content") or "").strip()[:4000] or fallback
        except Exception as exc:
            logger.warning("Kami consolidation failed for %s: %s", name, exc)
            return fallback

    def _compose_long_term(
        self,
        session: Session,
        simulation_id: str,
        kami_id: str,
        pending_summary: KamiMemorySummary | None = None,
    ) -> str:
        summaries = (
            session.query(KamiMemorySummary)
            .filter(
                KamiMemorySummary.simulation_id == simulation_id,
                KamiMemorySummary.kami_id == kami_id,
            )
            .order_by(KamiMemorySummary.tick.desc())
            .limit(7)
            .all()
        )
        if pending_summary is not None and all(
            item.summary_id != pending_summary.summary_id for item in summaries
        ):
            summaries.insert(0, pending_summary)
        imprints = (
            session.query(KamiImprint)
            .filter(
                KamiImprint.simulation_id == simulation_id,
                KamiImprint.kami_id == kami_id,
            )
            .order_by(KamiImprint.importance.desc(), KamiImprint.tick.desc())
            .limit(20)
            .all()
        )
        sections = []
        if imprints:
            sections.append(
                "Permanent imprints:\n"
                + "\n".join(
                    f"- [tick {item.tick}] {item.fact}" for item in imprints
                )
            )
        if summaries:
            sections.append(
                "Recent daily history:\n"
                + "\n".join(
                    f"- [tick {item.tick}] {item.summary}" for item in summaries[:7]
                )
            )
        return "\n\n".join(sections)[:6000]

    @staticmethod
    def _summary_id(simulation_id: str, kami_id: str, tick: int) -> str:
        digest = hashlib.sha256(
            f"{simulation_id}:{kami_id}:{tick}".encode("utf-8")
        ).hexdigest()[:24]
        return f"ksum_{digest}"

    @staticmethod
    def _summary_dict(row: KamiMemorySummary, idempotent: bool = False) -> dict:
        return {
            "summary_id": row.summary_id,
            "kami_id": row.kami_id,
            "tick": row.tick,
            "summary": row.summary,
            "event_count": row.event_count,
            "peak_salience": float(row.peak_salience),
            "idempotent": idempotent,
        }

    @staticmethod
    def _llm_available() -> bool:
        model = config.cheap_model_name
        provider = (
            model.split(":", 1)[0].strip().lower()
            if ":" in model
            else config.llm_provider.strip().lower()
        )
        return bool(
            {
                "anthropic": config.anthropic_api_key,
                "openai": config.openai_api_key,
                "gemini": config.gemini_api_key,
            }.get(provider)
        )
