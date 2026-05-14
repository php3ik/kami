from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kami_sim.factstore.models import Base
from kami_sim.factstore import tools as fs
from kami_sim.world_builder.build_world import load_world_into_db
from kami_sim.world_builder.dynamic_world import _normalize_world, _validate_world


def test_dynamic_world_normalizes_and_loads_objects():
    raw = {
        "world_seed": {"name": "Custom Camp"},
        "kami_specs": [
            {
                "entity_id": "kami_gate",
                "name": "Gate",
                "kind": "public_outdoor",
                "description": "The gate is a worked wooden threshold with a rope latch, a damp notice board, boot prints, a hanging lantern, and a clear line of sight toward the camp path. People pause here before entering because the place feels official without being formal. The ground is packed down by arrivals and supply runs. Small signs of the morning inspection are still visible.",
                "history": "The gate was built by last year's patrol and still carries their carved initials. A late arrival once caused a search party to form here, so the leaders treat this spot as a quiet accountability point.",
                "sensory": {"sights": ["rope latch"], "sounds": ["branches"], "smells": ["wet pine"], "textures": ["rough bark"]},
                "ambient_objects": ["rope latch", "notice board", "lantern", "boot scraper", "patrol sign", "muddy path"],
            },
            {
                "entity_id": "kami_tent",
                "name": "Tent",
                "kind": "residential",
                "description": "The sleeping tent is tight, canvas-warm, and crowded with rolled sleeping bags, private jokes, damp socks, and the awkward geometry of too many packs. The entrance flap never closes quite straight. A folded duty list is pinned near the center pole. This is where bravado thins out after lights-out.",
                "history": "The tent has already seen one argument about missing cord and one whispered apology after everyone pretended to sleep. Its small disorder tells more truth than the official schedule.",
                "sensory": {"sights": ["canvas seams"], "sounds": ["fabric shifting"], "smells": ["wool socks"], "textures": ["packed dirt"]},
                "ambient_objects": ["sleeping bags", "packs", "duty list", "wet socks", "flashlight", "tent pegs"],
            },
            {
                "entity_id": "kami_kitchen",
                "name": "Kitchen",
                "kind": "utility",
                "description": "The camp kitchen sits under a sagging tarp with pots, ladles, crates of buckwheat, onions, a water barrel, and one table that rocks unless someone wedges a stone under the leg. Work here is public and impossible to fake. Smoke hangs low when the wind turns. Every meal leaves evidence.",
                "history": "Yesterday's burnt porridge is still a joke and a warning. The leaders know kitchen duty reveals who can cooperate under pressure better than any formal exercise.",
                "sensory": {"sights": ["blackened pot"], "sounds": ["ladle clatter"], "smells": ["smoke"], "textures": ["sticky table"]},
                "ambient_objects": ["pots", "ladles", "buckwheat crate", "water barrel", "onions", "knife board"],
            },
            {
                "entity_id": "kami_fire",
                "name": "Fire Circle",
                "kind": "public_outdoor",
                "description": "The fire circle is a ring of stones with damp logs under a tarp, blackened sticks from the previous night, song sheets weighted by pebbles, and stumps arranged unevenly around the ash. During the day it feels unfinished; at night it becomes the emotional center. Secrets travel differently here. The smoke marks every sleeve.",
                "history": "The first evening's song broke the tension, but someone also cried quietly behind the woodpile. The place now carries both ceremony and the risk of honesty.",
                "sensory": {"sights": ["ash ring"], "sounds": ["distant birds"], "smells": ["cold smoke"], "textures": ["charred wood"]},
                "ambient_objects": ["stone ring", "logs", "song sheets", "pebbles", "stumps", "ash shovel"],
            },
        ],
        "spatial_graph": {"edges": [{"source": "kami_gate", "target": "kami_tent"}]},
        "agents": [
            {
                "entity_id": "agent_one",
                "name": "One",
                "age": 20,
                "role": "leader",
                "home": "kami_tent",
                "work": "kami_gate",
                "background": "One grew up learning that leadership meant noticing who had gone quiet before anyone else did. As a child they helped an exhausted parent keep a household running, which made them competent early and secretly resentful of people who call competence natural. In this camp they want to be steady, but they are afraid the younger participants will see the strain behind the calm voice. They remember being the child who needed one adult to ask a second question, and that memory makes every duty list feel personal. They dream of leaving this place better than they found it, not in a heroic way, but in the small visible order of dry socks, safe tools, and people who know where to go when they are scared.",
                "private_history": ["Still keeps an old apology letter", "Checks exits in every room", "Wants praise but distrusts it"],
                "memories": [
                    {"content": "One once stayed up through rain to retie a collapsing tarp while pretending not to be cold.", "importance": 0.8, "participants": ["One"]},
                    {"content": "A younger camper thanked One for finding a lost cup, and the simple trust stayed with them.", "importance": 0.6, "participants": ["One"]},
                    {"content": "One watched an older leader humiliate someone and promised never to lead that way.", "importance": 0.9, "participants": ["One"]},
                ],
                "traits": ["calm", "focused", "warm", "tired"],
                "fears": ["failure", "fire"],
                "desires": ["trust", "rest"],
                "goals": {"current": "Check the camp"},
            },
            {
                "entity_id": "agent_two",
                "name": "Two",
                "age": 14,
                "role": "participant",
                "home": "kami_tent",
                "work": "kami_kitchen",
                "background": "Two arrived with a pack that looked more organized than they felt. At home they are treated as capable only when convenient, so they have learned to volunteer fast and then panic privately about doing things wrong. The camp smells like smoke and wet grass, which makes everything feel more real than school ever does. They want the older scouts to trust them with something that matters, but they are also afraid that one mistake will become their whole identity. Their dream is not grand: they want one story from this camp that proves they were brave in a way nobody can laugh off.",
                "private_history": ["Misses home at night", "Practiced knots secretly", "Is afraid of being useless"],
                "memories": [
                    {"content": "Two hid behind a joke after dropping a pot and later cleaned it alone.", "importance": 0.7, "participants": ["Two"]},
                    {"content": "Two heard singing at the fire circle and almost joined before losing nerve.", "importance": 0.5, "participants": ["Two"]},
                    {"content": "Two found a dry matchbox and felt absurdly proud of saving it.", "importance": 0.4, "participants": ["Two"]},
                ],
                "traits": ["eager", "messy", "funny", "nervous"],
                "fears": ["being mocked", "dark"],
                "desires": ["respect", "adventure"],
                "goals": {"current": "Help with breakfast"},
            },
        ],
        "relationships": [{
            "names": ["One", "Two"],
            "rel_type": "mentor",
            "trust": 0.6,
            "story": "One is trying to give Two real responsibility.",
        }],
        "objects": [{
            "entity_id": "obj_logbook",
            "name": "Logbook",
            "kind": "document",
            "kami_id": "kami_gate",
            "description": "A camp logbook.",
            "condition": "creased cover, damp corners",
            "uses": ["record duties", "check attendance"],
            "story": "Someone corrected yesterday's duty list in a different pencil.",
            "state": {"open": False},
        }] + [
            {
                "entity_id": f"obj_extra_{i}",
                "name": f"Extra object {i}",
                "kind": "object",
                "kami_id": ["kami_gate", "kami_tent", "kami_kitchen", "kami_fire"][i % 4],
                "description": "A useful physical prop with a specific place in camp life.",
                "condition": "used",
                "uses": ["notice", "move"],
                "story": "It hints at routine work.",
                "state": {},
            }
            for i in range(1, 12)
        ],
    }
    world = _normalize_world(raw, "custom prompt", 2, "Custom Camp")
    assert _validate_world(world, 2) == []

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        graph = load_world_into_db(session, world, simulation_id="test")
        assert graph.is_connected()
        assert session.query(fs.Entity).filter(fs.Entity.kind == "agent").count() == 2
        obj = session.query(fs.Entity).filter(fs.Entity.kind == "document").one()
        assert obj.canonical_name == "Logbook"
        assert fs.get_current_location(session, obj.entity_id).kami_id == "sim_test__kami_gate"
    finally:
        session.close()
