"""Cascade 3 — Population generation (spec §2.10).

Batched mid-tier calls, ~5 personas per call, diversity injection.
"""

from __future__ import annotations

import logging
import random

from ...llm.client import llm_client

logger = logging.getLogger(__name__)

# Diversity seeds for injection
QUIRK_SEEDS = [
    "collects unusual stamps", "has a limp from an old injury", "speaks with an accent",
    "always carries a notebook", "allergic to cats but loves them", "former amateur boxer",
    "writes letters to the editor", "has an identical twin elsewhere", "night owl by nature",
    "makes their own cheese", "afraid of birds", "used to live abroad",
    "has a secret hobby", "color blind", "left-handed", "an excellent cook",
    "terrible sense of direction", "reads palms at parties", "obsessed with weather",
    "keeps bees", "was briefly famous", "has a complicated past", "synesthete",
]


async def generate_population(
    world_seed: dict,
    kami_specs: list[dict],
    target_count: int = 100,
    batch_size: int = 5,
) -> list[dict]:
    """Generate population for the town.

    Returns list of agent specs with persona, home, role.
    """
    # Compute residential and work slots
    residential_kami = [k for k in kami_specs if k.get("kind") == "residential"]
    work_kami = [k for k in kami_specs if k.get("kind") in ("commercial", "industrial", "institutional")]

    all_agents = []
    existing_summaries = []

    num_batches = (target_count + batch_size - 1) // batch_size

    for batch_idx in range(num_batches):
        remaining = target_count - len(all_agents)
        if remaining <= 0:
            break
        count = min(batch_size, remaining)

        # Inject diversity seeds
        quirks = random.sample(QUIRK_SEEDS, min(count, len(QUIRK_SEEDS)))

        response = await llm_client.call(
            messages=[{
                "role": "user",
                "content": f"""Generate {count} distinct residents for {world_seed.get('town_name', 'the town')}.

World context:
{world_seed.get('raw_text', '')[:1500]}

Available homes: {', '.join(k['name'] for k in residential_kami[:10])}
Available workplaces: {', '.join(k.get('name', k['entity_id']) for k in work_kami[:10])}

Already created ({len(existing_summaries)} people): {'; '.join(existing_summaries[-10:])}

Diversity injection — incorporate these quirks: {', '.join(quirks[:count])}

For each person, provide:
---
NAME: [full name]
AGE: [number]
APPEARANCE: [1 sentence]
BACKGROUND: [2-3 sentences]
TRAITS: [4-5 comma-separated]
FEARS: [2 comma-separated]
DESIRES: [2-3 comma-separated]
VOICE: [1-2 sentences describing speech patterns]
HOME: [kami_id of residence]
WORK: [kami_id of workplace, or 'none']
ROLE: [job title or occupation]
GOALS_LIFE: [1 sentence]
GOALS_DAILY: [1 sentence]
---

Make them diverse in age, background, personality, and role. Avoid generic archetypes.""",
            }],
            system="You create realistic, distinct characters for a town simulation. Each person should feel like a real individual, not an archetype.",
            tier="strong",
            component="WorldBuilder",
            max_tokens=3000,
            temperature=0.9,
        )

        agents = _parse_agents(response.get("content", ""))
        for agent in agents:
            existing_summaries.append(
                f"{agent.get('name', '?')} ({agent.get('age', '?')}, {agent.get('role', '?')})"
            )
        all_agents.extend(agents)
        logger.info(f"Population batch {batch_idx + 1}/{num_batches}: {len(agents)} agents")

    return all_agents[:target_count]


def _parse_agents(text: str) -> list[dict]:
    """Parse LLM output into agent specs."""
    agents = []
    blocks = text.split("---")

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        agent = {}
        for line in block.split("\n"):
            line = line.strip()
            for field, key in [
                ("NAME:", "name"), ("AGE:", "age"), ("APPEARANCE:", "appearance"),
                ("BACKGROUND:", "background"), ("TRAITS:", "traits"),
                ("FEARS:", "fears"), ("DESIRES:", "desires"), ("VOICE:", "voice"),
                ("HOME:", "home"), ("WORK:", "work"), ("ROLE:", "role"),
                ("GOALS_LIFE:", "goals_life"), ("GOALS_DAILY:", "goals_daily"),
            ]:
                if line.startswith(field):
                    value = line[len(field):].strip()
                    if key == "age":
                        try:
                            value = int(value)
                        except ValueError:
                            value = 30
                    elif key in ("traits", "fears", "desires"):
                        value = [v.strip() for v in value.split(",")]
                    agent[key] = value

        if agent.get("name"):
            agents.append(agent)

    return agents
