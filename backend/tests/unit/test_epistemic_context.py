from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kami_sim.agent_worker.prompt_builder import build_agent_prompt
from kami_sim.factstore import tools as fs
from kami_sim.factstore.models import Base


def test_agent_prompt_separates_perception_belief_and_private_social_state():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        kami = fs.create_entity(session, "kami", "Room", 0, entity_id="kami_room")
        ari = fs.create_entity(
            session,
            "agent",
            "Ari Vale",
            0,
            entity_id="agent_ari",
            archetype={"appearance": "short dark hair"},
        )
        ben = fs.create_entity(
            session,
            "agent",
            "Ben Moss",
            0,
            entity_id="agent_ben",
            archetype={"appearance": "a weathered coat"},
        )
        cora = fs.create_entity(
            session,
            "agent",
            "Cora Reed",
            0,
            entity_id="agent_cora",
            archetype={"appearance": "a person in a blue cap"},
        )
        fs.place_entity(session, ari.entity_id, kami.entity_id, 0)
        fs.place_entity(session, ben.entity_id, kami.entity_id, 0)
        fs.place_entity(session, cora.entity_id, kami.entity_id, 0)
        fs.change_state(session, ben.entity_id, "activity", "repairing", 1)
        fs.change_state(session, ben.entity_id, "fatigue", 0.9, 1)
        fs.update_relation(
            session,
            ari.entity_id,
            ben.entity_id,
            "knows",
            1,
            weight={"trust": 0.6, "context": "A private generated secret"},
        )
        fs.update_relation(
            session,
            ben.entity_id,
            ari.entity_id,
            "trusts",
            1,
            weight={"trust": 0.2, "context": "Ben privately distrusts Ari"},
        )
        fs.update_relation(
            session,
            cora.entity_id,
            ari.entity_id,
            "fears",
            1,
            weight={"context": "Cora is privately afraid of Ari"},
        )
        fs.update_belief(
            session,
            ari.entity_id,
            "state",
            2,
            target_entity=ben.entity_id,
            attribute="reliability",
            believed_value="uncertain",
            confidence=0.65,
            source_event_id="evt_argument",
        )
        session.commit()

        _, messages = build_agent_prompt(
            session,
            ari,
            kami.entity_id,
            fs.query_kami_state(session, kami.entity_id),
            tick=3,
        )
        prompt = messages[0]["content"]

        assert "[OBSERVED tick=3]" in prompt
        assert "activity: repairing" in prompt
        assert "fatigue: 0.9" not in prompt
        assert "[BELIEVED tick=2 confidence=0.65 source=evt_argument]" in prompt
        assert "reliability = 'uncertain'" in prompt
        assert "your relation to Ben Moss: knows (trust=0.6)" in prompt
        assert "Ben privately distrusts Ari" not in prompt
        assert "A private generated secret" not in prompt
        assert "known relation toward you from Ben Moss: trusts" not in prompt
        assert "Cora Reed" not in prompt
        assert "an unfamiliar person (a person in a blue cap)" in prompt
    finally:
        session.close()
        engine.dispose()
