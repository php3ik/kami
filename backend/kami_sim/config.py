"""Central configuration — all tuning knobs from spec §4.3."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
VALID_LLM_PROVIDERS = {"anthropic", "openai", "gemini"}
VALID_IMAGE_PROVIDERS = {"openai", "gemini"}


def _csv_env(name: str, default: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.getenv(name, default).split(",")
        if item.strip()
    )


def _non_negative_float_env(name: str, default: str = "0") -> float:
    try:
        value = float(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _positive_int_env(name: str, default: str) -> int:
    try:
        value = int(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


def _int_env(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _bool_env(name: str, default: str = "true") -> bool:
    value = os.getenv(name, default).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


@dataclass
class SimConfig:
    # Time
    tick_in_sim_minutes: int = 1
    adaptive_time_stepping: bool = field(
        default_factory=lambda: _bool_env("ADAPTIVE_TIME_STEPPING", "true")
    )

    # Reproducibility and provider resilience
    deterministic_mode: bool = field(
        default_factory=lambda: _bool_env("DETERMINISTIC_MODE", "false")
    )
    deterministic_seed: int = field(
        default_factory=lambda: _int_env("DETERMINISTIC_SEED", "0")
    )
    llm_soft_timeout_seconds: float = field(
        default_factory=lambda: _non_negative_float_env("LLM_SOFT_TIMEOUT_SECONDS", "45")
    )
    llm_retry_attempts: int = field(
        default_factory=lambda: _positive_int_env("LLM_RETRY_ATTEMPTS", "2")
    )
    llm_retry_base_delay_seconds: float = field(
        default_factory=lambda: _non_negative_float_env(
            "LLM_RETRY_BASE_DELAY_SECONDS", "0.25"
        )
    )

    # Activation
    kami_wake_salience_threshold: float = 0.3
    forced_refresh_interval: int = 100  # ticks

    # Models
    llm_provider: str = os.getenv("LLM_PROVIDER", "anthropic")
    cheap_model_name: str = os.getenv("CHEAP_MODEL", "claude-haiku-4-5-20251001")
    strong_model_name: str = os.getenv("STRONG_MODEL", "claude-sonnet-4-6")
    kami_strong_model_threshold_agents: int = 2
    kami_strong_model_threshold_salience: float = 0.7

    # Memory
    max_active_l2_insights_per_agent: int = 40
    insight_decay_days_without_reinforcement: int = 14
    consolidation_phase_5_interval_days: int = 7

    # Comms
    group_chat_max_active_per_tick: int = 5
    conversation_cooldown_threshold: int = 10  # messages in 5 ticks

    # Safety
    entity_creation_quota_per_kami_per_tick: int = 3
    simulation_budget_usd: float = field(
        default_factory=lambda: _non_negative_float_env("SIMULATION_BUDGET_USD")
    )

    # Caching
    prompt_cache_enabled: bool = True

    # Database
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./kami_sim.db")
    chroma_path: str = os.getenv("CHROMA_PATH", "./chroma_data")
    memory_vector_backend: str = os.getenv("MEMORY_VECTOR_BACKEND", "sql").strip().lower()

    # HTTP
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: _csv_env(
            "CORS_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173",
        )
    )
    api_token: str = field(default_factory=lambda: os.getenv("KAMI_API_TOKEN", "").strip())

    # API keys
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY", ""))
    image_provider: str = os.getenv("IMAGE_PROVIDER", os.getenv("LLM_PROVIDER", "openai"))
    cheap_image_model: str = os.getenv("CHEAP_IMAGE_MODEL", "gpt-image-1-mini")
    strong_image_model: str = os.getenv("STRONG_IMAGE_MODEL", "gpt-image-2")

    def llm_settings(self) -> dict:
        return {
            "provider": self.llm_provider,
            "cheap_model": self.cheap_model_name,
            "strong_model": self.strong_model_name,
            "tokens": {
                "anthropic": _token_status(self.anthropic_api_key),
                "openai": _token_status(self.openai_api_key),
                "gemini": _token_status(self.gemini_api_key),
            },
            "image": {
                "provider": self.image_provider,
                "cheap_model": self.cheap_image_model,
                "strong_model": self.strong_image_model,
                "supported_providers": sorted(VALID_IMAGE_PROVIDERS),
            },
            "supported_providers": sorted(VALID_LLM_PROVIDERS),
            "runtime": {
                "soft_timeout_seconds": self.llm_soft_timeout_seconds,
                "retry_attempts": self.llm_retry_attempts,
                "deterministic": self.deterministic_mode,
                "deterministic_seed": (
                    self.deterministic_seed if self.deterministic_mode else None
                ),
            },
        }

    def update_llm_settings(
        self,
        provider: str | None = None,
        cheap_model: str | None = None,
        strong_model: str | None = None,
        anthropic_api_key: str | None = None,
        openai_api_key: str | None = None,
        gemini_api_key: str | None = None,
        image_provider: str | None = None,
        cheap_image_model: str | None = None,
        strong_image_model: str | None = None,
    ) -> dict:
        updates: dict[str, str] = {}
        if provider is not None:
            normalized = provider.strip().lower()
            if normalized not in VALID_LLM_PROVIDERS:
                raise ValueError(f"Unsupported LLM provider: {provider}")
            self.llm_provider = normalized
            updates["LLM_PROVIDER"] = normalized
        if cheap_model is not None:
            value = cheap_model.strip()
            if not value:
                raise ValueError("Cheap model cannot be empty")
            self.cheap_model_name = value
            updates["CHEAP_MODEL"] = value
        if strong_model is not None:
            value = strong_model.strip()
            if not value:
                raise ValueError("Strong model cannot be empty")
            self.strong_model_name = value
            updates["STRONG_MODEL"] = value
        if anthropic_api_key is not None and anthropic_api_key.strip():
            self.anthropic_api_key = anthropic_api_key.strip()
            updates["ANTHROPIC_API_KEY"] = self.anthropic_api_key
        if openai_api_key is not None and openai_api_key.strip():
            self.openai_api_key = openai_api_key.strip()
            updates["OPENAI_API_KEY"] = self.openai_api_key
        if gemini_api_key is not None and gemini_api_key.strip():
            self.gemini_api_key = gemini_api_key.strip()
            updates["GEMINI_API_KEY"] = self.gemini_api_key
        if image_provider is not None:
            normalized = image_provider.strip().lower()
            if normalized not in VALID_IMAGE_PROVIDERS:
                raise ValueError(f"Unsupported image provider: {image_provider}")
            self.image_provider = normalized
            updates["IMAGE_PROVIDER"] = normalized
        if cheap_image_model is not None:
            value = cheap_image_model.strip()
            if not value:
                raise ValueError("Cheap image model cannot be empty")
            self.cheap_image_model = value
            updates["CHEAP_IMAGE_MODEL"] = value
        if strong_image_model is not None:
            value = strong_image_model.strip()
            if not value:
                raise ValueError("Strong image model cannot be empty")
            self.strong_image_model = value
            updates["STRONG_IMAGE_MODEL"] = value
        if updates:
            _write_env_values(updates)
            for key, value in updates.items():
                os.environ[key] = value
        return self.llm_settings()


def _token_status(value: str) -> dict:
    if not value:
        return {"configured": False, "masked": ""}
    if len(value) <= 10:
        masked = "configured"
    else:
        masked = f"{value[:6]}...{value[-4:]}"
    return {"configured": True, "masked": masked}


def _quote_env(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _write_env_values(updates: dict[str, str]) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    next_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            next_lines.append(f"{key}={_quote_env(updates[key])}")
            seen.add(key)
        else:
            next_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f"{key}={_quote_env(value)}")
    ENV_PATH.write_text("\n".join(next_lines) + "\n", encoding="utf-8")


# Global singleton
config = SimConfig()
