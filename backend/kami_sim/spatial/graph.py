"""SpatialGraph — NetworkX graph of kami topology (spec §2.8).

Nodes = kami. Edges have type and sensory attenuation properties.
"""

from __future__ import annotations

from typing import Any

import networkx as nx


class SpatialGraph:
    """In-memory graph of kami spatial relationships."""

    def __init__(self):
        self.graph = nx.Graph()

    def add_kami(self, kami_id: str, **attrs):
        """Add a kami node."""
        self.graph.add_node(kami_id, **attrs)

    def add_edge(
        self,
        kami_a: str,
        kami_b: str,
        edge_type: str = "adjacent",
        visual_attenuation: float = 0.0,
        audio_attenuation: float = 0.0,
        **attrs,
    ):
        """Add an edge between kami with sensory properties."""
        self.graph.add_edge(
            kami_a,
            kami_b,
            edge_type=edge_type,
            visual_attenuation=visual_attenuation,
            audio_attenuation=audio_attenuation,
            **attrs,
        )

    def get_neighbors(self, kami_id: str) -> list[str]:
        """Get adjacent kami IDs."""
        if kami_id not in self.graph:
            return []
        return list(self.graph.neighbors(kami_id))

    def get_edge_data(self, kami_a: str, kami_b: str) -> dict | None:
        """Get edge properties between two kami."""
        return self.graph.edges.get((kami_a, kami_b))

    def get_neighbor_edges(self, kami_id: str) -> list[tuple[str, dict]]:
        """Get neighbors with edge data for salience attenuation."""
        result = []
        for neighbor in self.get_neighbors(kami_id):
            edge_data = self.graph.edges[kami_id, neighbor]
            result.append((neighbor, edge_data))
        return result

    def is_connected(self) -> bool:
        """Check if every kami is reachable from every other."""
        return nx.is_connected(self.graph)

    def shortest_path(self, from_kami: str, to_kami: str) -> list[str]:
        """Get shortest path between two kami."""
        return nx.shortest_path(self.graph, from_kami, to_kami)

    def all_kami_ids(self) -> list[str]:
        return list(self.graph.nodes)

    def to_dict(self) -> dict:
        """Serialize for persistence / frontend."""
        return {
            "nodes": [
                {"id": n, **self.graph.nodes[n]} for n in self.graph.nodes
            ],
            "edges": [
                {"source": u, "target": v, **d}
                for u, v, d in self.graph.edges(data=True)
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> SpatialGraph:
        """Reconstruct from serialized form."""
        sg = cls()
        for node in data["nodes"]:
            nid = node.pop("id")
            sg.add_kami(nid, **node)
        for edge in data["edges"]:
            src = edge.pop("source")
            tgt = edge.pop("target")
            sg.add_edge(src, tgt, **edge)
        return sg
