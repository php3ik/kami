"""Cascade 5 — Backstory injection (spec §2.10).

Generate episodic memories and initial life narrative per agent.
"""

from __future__ import annotations

import logging

from ...llm.client import llm_client

logger = logging.getLogger(__name__)


async def generate_backstory(
    agent: dict,
    relationships: list[dict],
    world_seed: dict,
) -> dict:
    """Generate backstory for one agent.

    Returns dict with 'memories' (list), 'life_narrative' (str).
    """
    rel_text = ""
    for rel in relationships:
        names = rel.get("names", [])
        if agent["name"] in names:
            other = [n for n in names if n != agent["name"]]
            if other:
                rel_text += f"- {other[0]}: {rel.get('rel_type', 'knows')} ({rel.get('origin', '')})\n"

    try:
        response = await llm_client.call(
            messages=[{
                "role": "user",
                "content": f"""Generate a backstory for this person.

Name: {agent['name']}
Age: {agent.get('age', '?')}
Background: {agent.get('background', '')}
Traits: {agent.get('traits', [])}
Role: {agent.get('role', '')}
Town: {world_seed.get('town_name', 'Town')}

Key relationships:
{rel_text or 'No established relationships yet.'}

Generate:
1. LIFE_NARRATIVE (500-800 words): A first-person reflection on who they are, written as if by the person themselves. Include formative experiences, how they came to live here, their hopes and regrets.

2. MEMORIES (10-20 specific episodic memories): Each should be a concrete scene (not abstract). Format:
MEMORY: [1-2 sentences describing a specific moment]
IMPORTANCE: [0.0-1.0]
PARTICIPANTS: [comma-separated names involved]

The memories should cover:
- Key relationship moments
- Professional milestones
- Personal turning points
- Recent daily events""",
            }],
            system="You create authentic backstories for simulated people. Write in a grounded, specific style.",
            tier="cheap",
            component="WorldBuilder",
            max_tokens=2000,
        )

        text = response.get("content", "")
        return _parse_backstory(text)

    except Exception as e:
        logger.error(f"Backstory generation failed for {agent['name']}: {e}")
        return {
            "life_narrative": f"I am {agent['name']}. I live here and do my best.",
            "memories": [],
        }


def _parse_backstory(text: str) -> dict:
    """Parse backstory output into structured data."""
    life_narrative = ""
    memories = []

    # Split into narrative and memories sections
    if "LIFE_NARRATIVE" in text:
        parts = text.split("MEMORIES")
        narrative_part = parts[0]
        life_narrative = narrative_part.split("LIFE_NARRATIVE")[-1].strip().lstrip(":").strip()

        if len(parts) > 1:
            memory_part = parts[1]
            current_memory = None
            for line in memory_part.split("\n"):
                line = line.strip()
                if line.startswith("MEMORY:"):
                    if current_memory:
                        memories.append(current_memory)
                    current_memory = {"content": line[7:].strip(), "importance": 0.5, "participants": []}
                elif line.startswith("IMPORTANCE:") and current_memory:
                    try:
                        current_memory["importance"] = float(line[11:].strip())
                    except ValueError:
                        pass
                elif line.startswith("PARTICIPANTS:") and current_memory:
                    current_memory["participants"] = [
                        p.strip() for p in line[13:].strip().split(",")
                    ]
            if current_memory:
                memories.append(current_memory)
    else:
        life_narrative = text

    return {
        "life_narrative": life_narrative,
        "memories": memories,
    }
