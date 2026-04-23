"""Cascade 1 — World seed generation (spec §2.10).

One frontier-model call to generate the world bible.
"""

from __future__ import annotations

import logging

from ...llm.client import llm_client

logger = logging.getLogger(__name__)


async def generate_world_seed(prompt: str) -> dict:
    """Generate world bible from user prompt.

    Returns dict with geography, history, economy, demographics, social rifts, cultural tone.
    """
    response = await llm_client.call(
        messages=[{
            "role": "user",
            "content": f"""Create a detailed world bible for a small town simulation based on this premise:

{prompt}

Include the following sections (output as structured text):

TOWN_NAME: [name]
GEOGRAPHY: [2-3 sentences about location, terrain, climate]
HISTORY: [3-4 sentences about founding, key events, how the town got to where it is]
ECONOMY: [2-3 sentences about main industries, employment, economic health]
DEMOGRAPHICS: [population ~100 people, age distribution, diversity, notable groups]
SOCIAL_RIFTS: [2-3 key tensions or divisions in the community]
CULTURAL_TONE: [2-3 sentences about the feel, values, daily rhythm of life]
LANDMARKS: [list 5-8 key locations/buildings]
DISTRICTS: [list 3-5 distinct areas of town]

Be specific and grounded. This is a realistic small town, not a fantasy setting.""",
        }],
        system="You are a world-builder creating the foundation for a realistic small-town simulation. Be specific, consistent, and grounded.",
        tier="strong",
        component="WorldBuilder",
        max_tokens=2000,
        temperature=0.8,
    )

    text = response.get("content", "")

    # Parse sections
    sections = {}
    current_key = None
    current_lines = []

    for line in text.split("\n"):
        stripped = line.strip()
        # Check for section headers
        for key in ["TOWN_NAME", "GEOGRAPHY", "HISTORY", "ECONOMY", "DEMOGRAPHICS",
                     "SOCIAL_RIFTS", "CULTURAL_TONE", "LANDMARKS", "DISTRICTS"]:
            if stripped.upper().startswith(key):
                if current_key:
                    sections[current_key] = "\n".join(current_lines).strip()
                current_key = key.lower()
                rest = stripped[len(key):].lstrip(":").strip()
                current_lines = [rest] if rest else []
                break
        else:
            if current_key:
                current_lines.append(stripped)

    if current_key:
        sections[current_key] = "\n".join(current_lines).strip()

    sections["raw_text"] = text
    return sections
