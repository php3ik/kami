"""Prompt caching utilities — spec §2.3.1.

Stable prefix segments get cache_control markers. Dynamic data comes last.
"""

from __future__ import annotations


def make_cached_system_blocks(
    system_prompt: str,
    identity_block: str = "",
    long_term_memory: str = "",
) -> list[dict]:
    """Build system prompt blocks with cache_control on stable segments."""
    blocks = []

    # System prompt — cached forever
    blocks.append({
        "type": "text",
        "text": system_prompt,
        "cache_control": {"type": "ephemeral"},
    })

    # Identity — cached for entity lifetime
    if identity_block:
        blocks.append({
            "type": "text",
            "text": identity_block,
            "cache_control": {"type": "ephemeral"},
        })

    # Long-term memory — cached between consolidations
    if long_term_memory:
        blocks.append({
            "type": "text",
            "text": long_term_memory,
            "cache_control": {"type": "ephemeral"},
        })

    return blocks
