"""Unit tests for FactStore tools — spec §3.2 Phase 1."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kami_sim.factstore.models import Base, init_db
from kami_sim.factstore import tools as fs


@pytest.fixture
def session():
    """Create a fresh in-memory database for each test."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def world(session):
    """Create a minimal world with one kami and one agent."""
    kami = fs.create_entity(session, "kami", "Test Kami", tick=0, entity_id="kami_1")
    agent = fs.create_entity(session, "agent", "Test Agent", tick=0, entity_id="agent_1")
    fs.place_entity(session, "agent_1", "kami_1", tick=0)
    session.commit()
    return {"kami": kami, "agent": agent}


class TestEntityCreation:
    def test_create_entity(self, session):
        entity = fs.create_entity(session, "agent", "Alice", tick=0)
        assert entity.kind == "agent"
        assert entity.canonical_name == "Alice"

    def test_invalid_kind_raises(self, session):
        with pytest.raises(ValueError, match="Invalid entity kind"):
            fs.create_entity(session, "invalid_kind", "Bad", tick=0)

    def test_quota_enforcement(self, session):
        tracker = {}
        fs.create_entity(session, "kami", "K", tick=0, entity_id="k1")
        for i in range(3):
            fs.create_entity(
                session, "object", f"Obj{i}", tick=1,
                kami_id="k1", quota_tracker=tracker,
            )
        with pytest.raises(ValueError, match="quota exceeded"):
            fs.create_entity(
                session, "object", "Obj3", tick=1,
                kami_id="k1", quota_tracker=tracker,
            )


class TestLocationTracking:
    def test_place_and_query(self, session, world):
        loc = fs.get_current_location(session, "agent_1")
        assert loc is not None
        assert loc.kami_id == "kami_1"

    def test_move_entity(self, session, world):
        fs.create_entity(session, "kami", "Kami 2", tick=0, entity_id="kami_2")
        fs.move_entity(session, "agent_1", "kami_2", tick=5)
        session.commit()

        loc = fs.get_current_location(session, "agent_1")
        assert loc.kami_id == "kami_2"
        assert loc.since_tick == 5

    def test_single_current_location(self, session, world):
        fs.create_entity(session, "kami", "Kami 2", tick=0, entity_id="kami_2")
        fs.move_entity(session, "agent_1", "kami_2", tick=5)
        session.commit()

        from kami_sim.factstore.models import Location
        current = session.query(Location).filter(
            Location.entity_id == "agent_1",
            Location.valid_until_tick.is_(None),
        ).all()
        assert len(current) == 1

    def test_move_nonexistent_entity_raises(self, session, world):
        with pytest.raises(ValueError, match="not found"):
            fs.move_entity(session, "nonexistent", "kami_1", tick=1)

    def test_move_to_nonexistent_kami_raises(self, session, world):
        with pytest.raises(ValueError, match="not found"):
            fs.move_entity(session, "agent_1", "nonexistent_kami", tick=1)


class TestPhysicalState:
    def test_change_and_query(self, session, world):
        fs.change_state(session, "agent_1", "hunger", 0.5, tick=1)
        session.commit()

        states = fs.get_state(session, "agent_1", "hunger")
        assert len(states) == 1
        assert states[0].value == 0.5

    def test_state_transition_validation(self, session, world):
        fs.change_state(session, "agent_1", "integrity", "broken", tick=1)
        session.commit()

        with pytest.raises(ValueError, match="broken to intact"):
            fs.change_state(session, "agent_1", "integrity", "intact", tick=2)

    def test_temporal_versioning(self, session, world):
        fs.change_state(session, "agent_1", "hunger", 0.3, tick=1)
        fs.change_state(session, "agent_1", "hunger", 0.6, tick=5)
        session.commit()

        current = fs.get_state(session, "agent_1", "hunger")
        assert len(current) == 1
        assert current[0].value == 0.6
        assert current[0].since_tick == 5


class TestOwnership:
    def test_transfer(self, session, world):
        obj = fs.create_entity(session, "object", "Sword", tick=0, entity_id="obj_1")
        fs.transfer_ownership(session, "obj_1", "agent_1", tick=1)
        session.commit()

        from kami_sim.factstore.models import Ownership
        own = session.query(Ownership).filter(
            Ownership.entity_id == "obj_1",
            Ownership.valid_until_tick.is_(None),
        ).first()
        assert own.owner_id == "agent_1"


class TestRelations:
    def test_create_relation(self, session, world):
        agent2 = fs.create_entity(session, "agent", "Bob", tick=0, entity_id="agent_2")
        fs.update_relation(session, "agent_1", "agent_2", "knows", tick=0,
                           weight={"strength": 0.5})
        session.commit()

        rels = fs.get_relations(session, "agent_1", rel_type="knows")
        assert len(rels) == 1
        assert rels[0].to_entity == "agent_2"

    def test_update_closes_old(self, session, world):
        agent2 = fs.create_entity(session, "agent", "Bob", tick=0, entity_id="agent_2")
        fs.update_relation(session, "agent_1", "agent_2", "knows", tick=0,
                           weight={"strength": 0.3})
        fs.update_relation(session, "agent_1", "agent_2", "knows", tick=5,
                           weight={"strength": 0.8})
        session.commit()

        rels = fs.get_relations(session, "agent_1", rel_type="knows")
        assert len(rels) == 1
        assert rels[0].weight["strength"] == 0.8


class TestEvents:
    def test_emit_and_query(self, session, world):
        evt = fs.emit_event(session, tick=1, kami_id="kami_1",
                            event_type="conversation",
                            narrative="They talked about the weather.",
                            salience=0.4)
        session.commit()

        events = fs.get_events(session, kami_id="kami_1")
        assert len(events) == 1
        assert events[0].event_type == "conversation"

    def test_event_ordering(self, session, world):
        for i in range(5):
            fs.emit_event(session, tick=i, kami_id="kami_1",
                          event_type="tick", narrative=f"Tick {i}")
        session.commit()

        events = fs.get_events(session, since_tick=2, until_tick=4)
        ticks = [e.tick for e in events]
        assert all(2 <= t <= 4 for t in ticks)


class TestKamiStateQuery:
    def test_query_kami_state(self, session, world):
        fs.change_state(session, "agent_1", "hunger", 0.5, tick=1)
        obj = fs.create_entity(session, "object", "Table", tick=0, entity_id="obj_1")
        fs.place_entity(session, "obj_1", "kami_1", tick=0)
        session.commit()

        state = fs.query_kami_state(session, "kami_1")
        assert state["entity_count"] == 2  # agent + object
        names = {e["name"] for e in state["entities"]}
        assert "Test Agent" in names
        assert "Table" in names


class TestAgentBeliefs:
    def test_create_belief(self, session, world):
        belief = fs.update_belief(
            session, agent_id="agent_1", kind="location",
            tick=1, target_entity="agent_2",
            attribute="location", believed_value="kami_1",
            confidence=0.9,
        )
        session.commit()

        beliefs = fs.get_beliefs(session, "agent_1")
        assert len(beliefs) == 1
        assert beliefs[0].believed_value == "kami_1"
