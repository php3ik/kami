"""Integration tests for the slice world setup."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from kami_sim.factstore.models import Base
from kami_sim.factstore import tools as fs
from kami_sim.slice_world import build_slice_world


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_slice_world_builds(session):
    graph = build_slice_world(session)

    # 5 kami
    kami = [e for e in session.query(fs.Entity).filter(fs.Entity.kind == "kami").all()]
    assert len(kami) == 5

    # 3 agents
    agents = [e for e in session.query(fs.Entity).filter(fs.Entity.kind == "agent").all()]
    assert len(agents) == 3

    # 7 objects
    objects = [e for e in session.query(fs.Entity).filter(fs.Entity.kind == "object").all()]
    assert len(objects) == 7

    # Graph is connected
    assert graph.is_connected()


def test_agents_placed_correctly(session):
    build_slice_world(session)

    # Elena in her apartment
    loc = fs.get_current_location(session, "agent_elena")
    assert loc.kami_id == "kami_apt_elena"

    # Marcus in his apartment
    loc = fs.get_current_location(session, "agent_marcus")
    assert loc.kami_id == "kami_apt_marcus"

    # June in the park
    loc = fs.get_current_location(session, "agent_june")
    assert loc.kami_id == "kami_park"


def test_relations_exist(session):
    build_slice_world(session)

    # Marcus knows June
    rels = fs.get_relations(session, "agent_marcus", rel_type="knows")
    targets = {r.to_entity for r in rels}
    assert "agent_june" in targets
    assert "agent_elena" in targets

    # Marcus works at cafe
    rels = fs.get_relations(session, "agent_marcus", rel_type="works_at")
    assert len(rels) == 1
    assert rels[0].to_entity == "kami_cafe"


def test_physical_states_set(session):
    build_slice_world(session)

    states = fs.get_state(session, "agent_marcus")
    attr_map = {s.attribute: s.value for s in states}
    assert "fatigue" in attr_map
    assert "hunger" in attr_map
    assert attr_map["fatigue"] == 0.5


def test_kami_state_query(session):
    build_slice_world(session)

    # Park should have June + fountain + bench
    state = fs.query_kami_state(session, "kami_park")
    assert state["entity_count"] >= 3
    names = {e["name"] for e in state["entities"]}
    assert "June Park" in names
    assert "Park Fountain" in names
