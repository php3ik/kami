"""Simulation-wide language contracts for diegetic text."""

from __future__ import annotations

import re
from typing import Any, Literal

from sqlalchemy.orm import Session

from .factstore.models import Simulation


ContentLanguage = Literal["en", "uk"]
SUPPORTED_CONTENT_LANGUAGES = frozenset({"en", "uk"})
DEFAULT_CONTENT_LANGUAGE: ContentLanguage = "en"

LANGUAGE_NAMES: dict[ContentLanguage, str] = {
    "en": "English",
    "uk": "Ukrainian",
}

_MESSAGES: dict[str, dict[ContentLanguage, str]] = {
    "agent_fallback": {
        "en": "I pause, unable to gather my thoughts clearly this moment.",
        "uk": "Я завмираю, бо цієї миті не можу чітко зібратися з думками.",
    },
    "timeout": {"en": "LLM request timed out", "uk": "Час очікування LLM вичерпано"},
    "llm_error": {"en": "LLM request failed", "uk": "Запит до LLM завершився помилкою"},
    "idle_tick": {
        "en": "The place passes through an uneventful minute.",
        "uk": "У цьому місці минає спокійна хвилина без помітних подій.",
    },
    "location_quiet": {
        "en": "The location remained quiet.",
        "uk": "У цьому місці було тихо.",
    },
    "uneventful_day": {
        "en": "An uneventful day.",
        "uk": "День минув без помітних подій.",
    },
    "day_passed": {"en": "The day passed.", "uk": "День минув."},
    "someone": {"en": "Someone", "uk": "Хтось"},
}


def normalize_content_language(value: str | None) -> ContentLanguage:
    normalized = str(value or DEFAULT_CONTENT_LANGUAGE).strip().lower().replace("_", "-")
    aliases = {
        "en": "en",
        "en-us": "en",
        "en-gb": "en",
        "english": "en",
        "uk": "uk",
        "uk-ua": "uk",
        "ua": "uk",
        "ukrainian": "uk",
        "українська": "uk",
    }
    return aliases.get(normalized, DEFAULT_CONTENT_LANGUAGE)  # type: ignore[return-value]


def language_instruction(value: str | None) -> str:
    language = normalize_content_language(value)
    name = LANGUAGE_NAMES[language]
    return (
        f"CONTENT LANGUAGE CONTRACT: All diegetic and user-visible text must be in {name}. "
        "This includes names where culturally appropriate, descriptions, histories, thoughts, "
        "dialogue, messages, memories, event summaries, and narrative prose. Keep JSON keys, "
        "entity IDs, tool names, enum values, and machine-readable action types in English. "
        "Do not switch languages unless exact quoted source text requires it."
    )


def get_simulation_language(
    session: Session | None, simulation_id: str | None
) -> ContentLanguage:
    if session is None or not simulation_id:
        return DEFAULT_CONTENT_LANGUAGE
    try:
        simulation = session.get(Simulation, simulation_id)
    except Exception:
        return DEFAULT_CONTENT_LANGUAGE
    return normalize_content_language(
        simulation.content_language if simulation is not None else None
    )


def message(key: str, language: str | None, **values: object) -> str:
    normalized = normalize_content_language(language)
    template = _MESSAGES[key][normalized]
    return template.format(**values)


def choose(language: str | None, *, en: str, uk: str) -> str:
    return uk if normalize_content_language(language) == "uk" else en


def text_matches_language(
    text: str, language: str | None, *, minimum_letters: int = 12
) -> bool:
    """Reject substantial prose written in the other supported script."""
    latin = len(re.findall(r"[A-Za-z]", text or ""))
    cyrillic = len(re.findall(r"[А-Яа-яІіЇїЄєҐґ]", text or ""))
    total = latin + cyrillic
    if total < minimum_letters:
        return True
    if normalize_content_language(language) == "uk":
        return cyrillic / total >= 0.6
    return latin / total >= 0.7


def structured_text_matches_language(payload: Any, language: str | None) -> bool:
    prose: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                collect(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item)
        elif isinstance(value, str) and len(value) >= 12 and any(
            character.isspace() for character in value
        ):
            prose.append(value)

    collect(payload)
    return text_matches_language("\n".join(prose), language)
