"""Scene-level guardrails that keep kami resolutions from looping.

These helpers do not decide the story. They surface hard constraints to the
LLM and provide deterministic post-processing when a scene violates basic
simulation physics: no concrete target, too many simultaneous speakers, or a
conversation that keeps asking the same question without consequence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.orm import Session

from ..eventbus.bus import EventBus
from ..factstore import tools as fs
from ..factstore.models import ConversationThread, Entity, Event
from ..spatial.graph import SpatialGraph


QUESTION_MARKERS = (
    "?",
    "who",
    "what",
    "why",
    "where",
    "куди",
    "кого",
    "кому",
    "що",
    "чого",
    "навіщо",
    "по що",
    "до кого",
    "скажіть",
    "кажіть",
)

UNKNOWN_TARGET_MARKERS = (
    "unknown",
    "unfamiliar",
    "stranger",
    "чуж",
    "незнай",
    "новий",
    "нова людина",
)


@dataclass
class SceneDynamics:
    present_ids: set[str]
    adjacent_kami_ids: set[str]
    invalid_intents: list[dict]
    repeated_question_count: int
    loop_break_required: bool
    speaking_budget: int
    active_threads: list[ConversationThread]
    repeated_topic_hint: str

    def to_prompt_block(self) -> str:
        lines = [
            "Scene constraints are authoritative. Use them to resolve the tick.",
            f"- Speaking budget: at most {self.speaking_budget} agents may speak in this kami this tick; others can watch, move, work, or think silently.",
            "- Every conversation beat must change state: answer, refusal, disclosure, physical movement, relationship shift, thread pause/resolution, or a named blocker.",
            "- Do not repeat an already-asked question unless the repetition itself causes a consequence.",
        ]
        if self.invalid_intents:
            lines.append("- Invalid targets detected. Do not pretend these targets exist:")
            for item in self.invalid_intents:
                lines.append(
                    f"  - intent_id={item.get('intent_id') or '?'} agent={item.get('agent_id')} "
                    f"target={item.get('target')!r}: {item.get('reason')}"
                )
            lines.append(
                "  Mark invalid-target intents as blocked, or create a concrete entity first if the world truly needs that person/object."
            )
        if self.loop_break_required:
            lines.append(
                f"- LOOP BREAK REQUIRED: similar open questions have repeated {self.repeated_question_count} times recently"
                + (f" around {self.repeated_topic_hint!r}" if self.repeated_topic_hint else "")
                + ". This tick must answer, refuse, escalate, leave, pause, or resolve the thread."
            )
        return "\n".join(lines)


def analyze_scene_dynamics(
    session: Session,
    kami_id: str,
    tick: int,
    agent_intents: list[dict],
    recent_events: list[Event],
    active_threads: list[ConversationThread],
    spatial_graph: SpatialGraph,
) -> SceneDynamics:
    state = fs.query_kami_state(session, kami_id)
    present_ids = {entity["entity_id"] for entity in state.get("entities", [])}
    adjacent_kami_ids = set(spatial_graph.get_neighbors(kami_id))

    invalid_intents = [
        item for item in (
            _validate_intent_target(intent, present_ids, adjacent_kami_ids, session)
            for intent in agent_intents
        )
        if item is not None
    ]

    current_questions = [
        _intent_question_text(intent)
        for intent in agent_intents
        if intent.get("action_type") == "talk" and _looks_like_question(_intent_question_text(intent))
    ]
    topic_hint = _topic_hint(current_questions, active_threads)
    repeated = _count_repeated_questions(recent_events, current_questions, active_threads)

    # A scene with an unanswered open thread that is still being questioned has
    # less room for chatter; one person should press or close it, not a chorus.
    speaking_budget = 1 if repeated >= 3 else 2

    return SceneDynamics(
        present_ids=present_ids,
        adjacent_kami_ids=adjacent_kami_ids,
        invalid_intents=invalid_intents,
        repeated_question_count=repeated,
        loop_break_required=repeated >= 3,
        speaking_budget=speaking_budget,
        active_threads=active_threads,
        repeated_topic_hint=topic_hint,
    )


def apply_scene_guardrails(
    result: dict,
    dynamics: SceneDynamics,
    kami_id: str,
    tick: int,
    agent_intents: list[dict],
) -> dict:
    """Add deterministic consequences when the LLM leaves loops unresolved."""
    mutations = result.setdefault("mutations", [])
    events = result.setdefault("events", [])

    _block_invalid_target_intents(mutations, dynamics)
    _trim_excess_speakers(events, dynamics)
    _force_loop_consequence(mutations, events, dynamics, kami_id, tick, agent_intents)
    _calibrate_event_salience(events, dynamics)
    return result


def _validate_intent_target(
    intent: dict,
    present_ids: set[str],
    adjacent_kami_ids: set[str],
    session: Session,
) -> dict | None:
    target = str(intent.get("target") or "").strip()
    if not target:
        return None

    action = intent.get("action_type", "")
    if action == "move":
        if target in adjacent_kami_ids:
            return None
        return {**intent, "reason": "movement target is not an adjacent kami id"}

    if target in present_ids:
        return None
    if session.get(Entity, target) is not None:
        return None

    if action in {"talk", "observe", "use_object", "work"}:
        if _looks_like_unknown_target(target):
            return {**intent, "reason": "ambiguous unknown target is not a concrete entity id"}
        return {**intent, "reason": "target is not present in this kami"}
    return None


def _block_invalid_target_intents(mutations: list[dict], dynamics: SceneDynamics) -> None:
    existing = {
        mutation.get("intent_id")
        for mutation in mutations
        if mutation.get("type") == "record_intent_result"
    }
    for intent in dynamics.invalid_intents:
        intent_id = intent.get("intent_id")
        if not intent_id or intent_id in existing:
            continue
        mutations.append({
            "type": "record_intent_result",
            "intent_id": intent_id,
            "status": "blocked",
            "summary": f"Target {intent.get('target')!r} was not a concrete present entity.",
            "blockers": ["target_not_present"],
        })


def _trim_excess_speakers(events: list[dict], dynamics: SceneDynamics) -> None:
    for event in events:
        if event.get("event_type") not in {"conversation", "action"}:
            continue
        participants = [p for p in event.get("participants", []) if p in dynamics.present_ids]
        if len(participants) <= dynamics.speaking_budget:
            continue
        kept = participants[: dynamics.speaking_budget]
        event["participants"] = kept
        payload = dict(event.get("payload") or {})
        payload["speaking_budget_applied"] = True
        payload["observers"] = participants[dynamics.speaking_budget :]
        event["payload"] = payload


def _force_loop_consequence(
    mutations: list[dict],
    events: list[dict],
    dynamics: SceneDynamics,
    kami_id: str,
    tick: int,
    agent_intents: list[dict],
) -> None:
    if not dynamics.loop_break_required:
        return

    has_thread_consequence = any(
        mutation.get("type") == "update_conversation_thread"
        and mutation.get("status") in {"paused", "resolved", "ruptured", "blocked"}
        for mutation in mutations
    )
    has_material_change = any(
        mutation.get("type") in {"move_entity", "create_entity", "update_relation", "change_state"}
        for mutation in mutations
    )
    if has_thread_consequence or has_material_change:
        return

    participants = _intent_participants(agent_intents, dynamics.present_ids)
    thread = dynamics.active_threads[0] if dynamics.active_threads else None
    mutations.append({
        "type": "update_conversation_thread",
        "kami_id": kami_id,
        "thread_id": thread.thread_id if thread else None,
        "participants": participants,
        "topic": thread.topic if thread else dynamics.repeated_topic_hint or "stalled exchange",
        "status": "paused",
        "summary": "The repeated question stops producing new information, so the scene pauses until someone brings a concrete answer or action.",
        "tension": min(1.0, (thread.tension if thread else 0.45) + 0.1),
        "momentum": 0.15,
        "open_question": None,
    })
    for intent in agent_intents:
        if intent.get("intent_id") and intent.get("action_type") == "talk":
            mutations.append({
                "type": "record_intent_result",
                "intent_id": intent["intent_id"],
                "status": "blocked",
                "summary": "The question has already been pressed several times; the scene needs a new tactic or answer.",
                "blockers": ["conversation_loop_requires_consequence"],
            })

    if events:
        event = events[0]
        event["event_type"] = "conversation"
        event["participants"] = participants[: dynamics.speaking_budget]
        event["narrative"] = _append_once(
            event.get("narrative", ""),
            "The repeated questioning reaches a limit; without a concrete answer, the exchange pauses and the people present have to change tactics.",
        )
        payload = dict(event.get("payload") or {})
        payload["loop_break_applied"] = True
        event["payload"] = payload


def _calibrate_event_salience(events: list[dict], dynamics: SceneDynamics) -> None:
    for event in events:
        salience = float(event.get("salience", 0.3))
        payload = event.get("payload") or {}
        narrative = event.get("narrative", "")
        has_change = payload.get("loop_break_applied") or any(
            marker in narrative.lower()
            for marker in ("leaves", "moves", "answers", "refuses", "виріш", "відмов", "йде", "пауза")
        )
        if dynamics.loop_break_required and not has_change:
            salience = min(salience, 0.52)
        if event.get("event_type") == "idle":
            salience = min(salience, 0.2)
        event["salience"] = max(0.05, min(0.95, salience))


def _intent_question_text(intent: dict) -> str:
    params = intent.get("params") or {}
    return " ".join(
        str(part)
        for part in (
            intent.get("utterance", ""),
            params.get("speech", ""),
            intent.get("expected_outcome", ""),
            intent.get("goal", ""),
        )
        if part
    )


def _looks_like_question(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in QUESTION_MARKERS)


def _looks_like_unknown_target(target: str) -> bool:
    lower = target.lower()
    return any(marker in lower for marker in UNKNOWN_TARGET_MARKERS)


def _count_repeated_questions(
    recent_events: list[Event],
    current_questions: list[str],
    active_threads: list[ConversationThread],
) -> int:
    if not current_questions:
        return 0

    count = 0
    current = " ".join(current_questions)
    for event in recent_events:
        narrative = event.narrative or ""
        if _looks_like_question(narrative) and _similar_enough(current, narrative):
            count += 1
    for thread in active_threads:
        if thread.open_question and _similar_enough(current, thread.open_question):
            count += 1
        if thread.summary and _similar_enough(current, thread.summary):
            count += 1
    return count


def _similar_enough(left: str, right: str) -> bool:
    left_norm = _normalize_dialogue(left)
    right_norm = _normalize_dialogue(right)
    if not left_norm or not right_norm:
        return False
    overlap = len(set(left_norm.split()) & set(right_norm.split()))
    return overlap >= 3 or SequenceMatcher(None, left_norm, right_norm).ratio() >= 0.42


def _normalize_dialogue(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\sа-яіїєґ'-]", " ", text, flags=re.IGNORECASE)
    stop = {
        "and", "the", "you", "your", "says", "opens", "concrete", "exchange",
        "і", "й", "та", "то", "ну", "вже", "ви", "він", "вона", "вони", "це",
        "одне", "одним", "реченням",
    }
    return " ".join(word for word in text.split() if len(word) > 2 and word not in stop)


def _topic_hint(current_questions: list[str], active_threads: list[ConversationThread]) -> str:
    if active_threads:
        return active_threads[0].topic or active_threads[0].open_question or ""
    return current_questions[0][:100] if current_questions else ""


def _intent_participants(agent_intents: list[dict], present_ids: set[str]) -> list[str]:
    participants: list[str] = []
    for intent in agent_intents:
        agent_id = intent.get("agent_id")
        target = intent.get("target")
        if agent_id in present_ids:
            participants.append(agent_id)
        if target in present_ids:
            participants.append(target)
    return list(dict.fromkeys(participants))


def _append_once(text: str, addition: str) -> str:
    text = (text or "").strip()
    if addition in text:
        return text
    return f"{text} {addition}".strip()
