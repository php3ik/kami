"""Unit tests for SpatialGraph."""

from kami_sim.spatial.graph import SpatialGraph


def test_add_and_query():
    g = SpatialGraph()
    g.add_kami("a", name="Room A")
    g.add_kami("b", name="Room B")
    g.add_edge("a", "b", edge_type="adjacent", visual_attenuation=0.5)

    assert g.get_neighbors("a") == ["b"]
    assert g.is_connected()


def test_shortest_path():
    g = SpatialGraph()
    for n in ["a", "b", "c"]:
        g.add_kami(n)
    g.add_edge("a", "b")
    g.add_edge("b", "c")

    path = g.shortest_path("a", "c")
    assert path == ["a", "b", "c"]


def test_serialization_roundtrip():
    g = SpatialGraph()
    g.add_kami("x", kind="outdoor")
    g.add_kami("y", kind="indoor")
    g.add_edge("x", "y", edge_type="adjacent", audio_attenuation=0.3)

    data = g.to_dict()
    g2 = SpatialGraph.from_dict(data)

    assert g2.get_neighbors("x") == ["y"]
    assert g2.is_connected()


def test_disconnected_graph():
    g = SpatialGraph()
    g.add_kami("a")
    g.add_kami("b")
    assert not g.is_connected()
