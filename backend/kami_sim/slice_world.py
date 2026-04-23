"""Hand-authored minimal world for vertical slice (spec §3.2 Phase 3).

5 kami in a small graph, 3 agents with hand-written personas, basic objects.
Do NOT generate this — it is hand-crafted for testing.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .factstore import tools as fs
from .spatial.graph import SpatialGraph


def build_slice_world(session: Session) -> SpatialGraph:
    """Create the minimal test world. Returns the SpatialGraph."""

    # === KAMI (5 locations) ===
    kami_defs = [
        {
            "entity_id": "kami_cafe",
            "name": "The Kettle Cafe",
            "archetype": {
                "kami_kind": "commercial",
                "description": "A cozy neighborhood cafe with worn wooden tables, the smell of fresh coffee, and a chalkboard menu. Morning light streams through large front windows. A radio plays softly.",
                "ambiance": "warm, busy mornings, quiet afternoons",
                "capacity": 15,
            },
        },
        {
            "entity_id": "kami_park",
            "name": "Willow Park",
            "archetype": {
                "kami_kind": "public_outdoor",
                "description": "A small park with a central fountain, three benches, and a large willow tree. Pigeons gather near the fountain. The path connects the cafe side of town to the residential side.",
                "ambiance": "birdsong, rustling leaves, occasional traffic noise from the street",
                "capacity": 30,
            },
        },
        {
            "entity_id": "kami_apt_elena",
            "name": "Elena's Apartment (2B)",
            "archetype": {
                "kami_kind": "residential",
                "description": "A tidy one-bedroom apartment on the second floor. Bookshelves line the walls. A desk by the window is covered in notebooks and a laptop. The kitchen is small but well-organized.",
                "ambiance": "quiet, ticking clock, street sounds muffled",
                "capacity": 4,
            },
        },
        {
            "entity_id": "kami_apt_marcus",
            "name": "Marcus's Apartment (1A)",
            "archetype": {
                "kami_kind": "residential",
                "description": "A ground-floor studio apartment. Guitar cases lean against the wall. The kitchen counter has yesterday's dishes. A worn couch faces a small TV. Posters of jazz musicians on the walls.",
                "ambiance": "street noise from open window, neighbor's dog barking occasionally",
                "capacity": 4,
            },
        },
        {
            "entity_id": "kami_street",
            "name": "Elm Street",
            "archetype": {
                "kami_kind": "public_outdoor",
                "description": "A quiet residential street with old elm trees lining both sides. The apartment building is on the east side, the cafe is visible at the north end, and the park entrance is to the west.",
                "ambiance": "occasional car passing, leaves rustling, distant conversations",
                "capacity": 20,
            },
        },
    ]

    spatial_graph = SpatialGraph()

    for kd in kami_defs:
        fs.create_entity(
            session,
            kind="kami",
            canonical_name=kd["name"],
            tick=0,
            archetype=kd["archetype"],
            entity_id=kd["entity_id"],
        )
        spatial_graph.add_kami(kd["entity_id"], name=kd["name"], kind=kd["archetype"]["kami_kind"])

    # === SPATIAL EDGES ===
    # cafe <-> street (open, sound carries)
    spatial_graph.add_edge("kami_cafe", "kami_street", edge_type="adjacent",
                           visual_attenuation=0.1, audio_attenuation=0.3)
    # street <-> park (open)
    spatial_graph.add_edge("kami_street", "kami_park", edge_type="adjacent",
                           visual_attenuation=0.1, audio_attenuation=0.2)
    # street <-> elena's apt (wall + door)
    spatial_graph.add_edge("kami_street", "kami_apt_elena", edge_type="contains",
                           visual_attenuation=0.9, audio_attenuation=0.7)
    # street <-> marcus's apt (wall + open window)
    spatial_graph.add_edge("kami_street", "kami_apt_marcus", edge_type="contains",
                           visual_attenuation=0.8, audio_attenuation=0.5)

    # === AGENTS (3 personas) ===
    agents = [
        {
            "entity_id": "agent_elena",
            "name": "Elena Vasquez",
            "start_kami": "kami_apt_elena",
            "archetype": {
                "age": 34,
                "appearance": "dark hair in a loose bun, reading glasses, paint-stained fingers",
                "background": "Freelance translator and part-time poet. Moved here two years ago after a divorce. Originally from a bigger city. Keeps to herself but is slowly building a social life. Works from home most days.",
                "traits": ["introspective", "precise", "quietly warm", "overthinks"],
                "fears": ["loneliness", "being perceived as cold"],
                "desires": ["meaningful connection", "finishing her poetry collection", "a quiet life"],
                "voice": "Speaks carefully, sometimes pauses mid-sentence to find the right word. Uses metaphors naturally. Dry humor. Avoids small talk but lights up about books or language.",
                "goals": {
                    "life": "Publish a poetry collection and find belonging in this town",
                    "seasonal": "Finish translating the Neruda manuscript by month end",
                    "daily": "Work on translation, maybe go to the cafe for a change of scenery",
                    "current": "Start the morning, have coffee",
                },
                "emotion": {
                    "dominant": "calm",
                    "intensity": 0.3,
                    "physiology": "rested",
                    "last_trigger": "a good night's sleep",
                },
            },
        },
        {
            "entity_id": "agent_marcus",
            "name": "Marcus Cole",
            "start_kami": "kami_apt_marcus",
            "archetype": {
                "age": 28,
                "appearance": "lanky, curly brown hair, usually wearing a band t-shirt and jeans",
                "background": "Works part-time at the cafe as a barista. Plays guitar in a local band that hasn't gigged in months. Dropped out of music school. Has lived in this neighborhood his whole life. Knows everyone.",
                "traits": ["easygoing", "talkative", "scattered", "generous", "avoids conflict"],
                "fears": ["that he'll never make it as a musician", "being stuck forever"],
                "desires": ["a record deal or at least a steady gig", "someone who gets him"],
                "voice": "Casual, uses slang, trails off mid-thought. Makes pop culture references. Laughs easily. Says 'man' and 'like' a lot. Gets earnest when talking about music.",
                "goals": {
                    "life": "Make a living from music",
                    "seasonal": "Write three new songs for the band",
                    "daily": "Open shift at the cafe, maybe practice guitar after",
                    "current": "Get ready for work",
                },
                "emotion": {
                    "dominant": "slightly anxious",
                    "intensity": 0.4,
                    "physiology": "groggy, undercaffeinated",
                    "last_trigger": "alarm went off too early",
                },
            },
        },
        {
            "entity_id": "agent_june",
            "name": "June Park",
            "start_kami": "kami_park",
            "archetype": {
                "age": 67,
                "appearance": "short silver hair, round face, always wears a green cardigan and comfortable shoes",
                "background": "Retired schoolteacher. Widowed three years ago. Feeds the pigeons in the park every morning at 7am sharp. Knows the neighborhood gossip. Volunteers at the community garden. Has a cat named Miso at home.",
                "traits": ["observant", "nurturing", "stubborn", "surprisingly sharp-tongued", "lonely but won't admit it"],
                "fears": ["becoming irrelevant", "her memory getting worse"],
                "desires": ["to feel useful", "that the neighborhood stays the way it is", "more visitors"],
                "voice": "Warm but direct. Uses old-fashioned expressions. Calls younger people 'dear'. Can deliver a devastating observation disguised as a compliment. Speaks in complete sentences.",
                "goals": {
                    "life": "Leave the neighborhood a little better than she found it",
                    "seasonal": "Get the community garden spring planting done",
                    "daily": "Feed the pigeons, walk through the park, maybe stop by the cafe",
                    "current": "Morning pigeon feeding ritual",
                },
                "emotion": {
                    "dominant": "content",
                    "intensity": 0.4,
                    "physiology": "a bit stiff from the morning chill",
                    "last_trigger": "the first warm morning in weeks",
                },
            },
        },
    ]

    for agent_def in agents:
        entity = fs.create_entity(
            session,
            kind="agent",
            canonical_name=agent_def["name"],
            tick=0,
            archetype=agent_def["archetype"],
            entity_id=agent_def["entity_id"],
        )
        fs.place_entity(session, agent_def["entity_id"], agent_def["start_kami"], tick=0)

    # === OBJECTS ===
    objects = [
        ("obj_coffee_machine", "Coffee Machine", "kami_cafe", {"description": "A commercial espresso machine, well-maintained"}),
        ("obj_register", "Cash Register", "kami_cafe", {"description": "Old-fashioned register with a bell"}),
        ("obj_radio", "Cafe Radio", "kami_cafe", {"description": "A small radio playing soft jazz"}),
        ("obj_fountain", "Park Fountain", "kami_park", {"description": "A stone fountain with a cherub. Water trickles."}),
        ("obj_bench1", "Park Bench", "kami_park", {"description": "A wooden bench facing the fountain"}),
        ("obj_laptop", "Elena's Laptop", "kami_apt_elena", {"description": "A silver laptop with translation software open"}),
        ("obj_guitar", "Marcus's Guitar", "kami_apt_marcus", {"description": "A beat-up acoustic guitar with stickers on the case"}),
    ]

    for obj_id, name, kami_id, archetype in objects:
        fs.create_entity(session, kind="object", canonical_name=name, tick=0,
                         archetype=archetype, entity_id=obj_id)
        fs.place_entity(session, obj_id, kami_id, tick=0)

    # === RELATIONS ===
    # Marcus works at the cafe
    fs.update_relation(session, "agent_marcus", "kami_cafe", "works_at", tick=0,
                       weight={"role": "barista", "since": "2 years"})
    # Elena lives in her apartment
    fs.update_relation(session, "agent_elena", "kami_apt_elena", "lives_in", tick=0)
    # Marcus lives in his apartment
    fs.update_relation(session, "agent_marcus", "kami_apt_marcus", "lives_in", tick=0)
    # June and Marcus know each other (she's a regular at the cafe)
    fs.update_relation(session, "agent_june", "agent_marcus", "knows", tick=0,
                       weight={"strength": 0.6, "context": "cafe regular, friendly"})
    fs.update_relation(session, "agent_marcus", "agent_june", "knows", tick=0,
                       weight={"strength": 0.6, "context": "regular customer, nice lady"})
    # Elena and Marcus are neighbors, slight acquaintance
    fs.update_relation(session, "agent_elena", "agent_marcus", "knows", tick=0,
                       weight={"strength": 0.3, "context": "neighbor, occasional nod in the hallway"})
    fs.update_relation(session, "agent_marcus", "agent_elena", "knows", tick=0,
                       weight={"strength": 0.3, "context": "quiet neighbor upstairs"})
    # June has seen Elena but doesn't really know her
    fs.update_relation(session, "agent_june", "agent_elena", "knows", tick=0,
                       weight={"strength": 0.1, "context": "seen her in the park sometimes, don't know her name"})

    # === INITIAL STATES ===
    fs.change_state(session, "agent_elena", "fatigue", 0.2, tick=0)
    fs.change_state(session, "agent_elena", "hunger", 0.3, tick=0)
    fs.change_state(session, "agent_marcus", "fatigue", 0.5, tick=0)
    fs.change_state(session, "agent_marcus", "hunger", 0.4, tick=0)
    fs.change_state(session, "agent_june", "fatigue", 0.3, tick=0)
    fs.change_state(session, "agent_june", "hunger", 0.2, tick=0)
    fs.change_state(session, "kami_cafe", "open", False, tick=0)
    fs.change_state(session, "obj_fountain", "integrity", "working", tick=0)

    session.commit()

    return spatial_graph
