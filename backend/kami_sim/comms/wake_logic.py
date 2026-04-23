"""Wake logic for message delivery — spec §2.7.

Four recipient states: active_attention, ambient_awareness, dormant, forced_wake.
"""

from __future__ import annotations

from typing import Any


def determine_delivery_mode(
    agent_id: str,
    kami_is_active: bool,
    agent_using_phone: bool,
    message_salience: float,
    is_call: bool = False,
) -> str:
    """Determine how a message should be delivered to a recipient.

    Returns one of: 'immediate', 'ambient', 'dormant', 'force_wake'.
    """
    if is_call:
        # Phone calls always force-wake
        return "force_wake"

    if kami_is_active:
        if agent_using_phone:
            return "immediate"
        else:
            return "ambient"
    else:
        if message_salience > 0.8:
            return "force_wake"
        return "dormant"
