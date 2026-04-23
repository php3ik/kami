"""Central configuration — all tuning knobs from spec §4.3."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass
class SimConfig:
    # Time
    tick_in_sim_minutes: int = 1

    # Activation
    kami_wake_salience_threshold: float = 0.3
    forced_refresh_interval: int = 100  # ticks

    # Models
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

    # Caching
    prompt_cache_enabled: bool = True

    # Database
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./kami_sim.db")

    # API keys
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")


# Global singleton
config = SimConfig()
