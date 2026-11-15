"""Small undirected road graph; no graph library required."""

import heapq
import math

from fleet.models import Field


class Navigation:
    """Route deterministically over unblocked roads using Dijkstra."""

    def __init__(self, nodes: dict[str, Field], edges: list[dict] | None = None):
        self.nodes = nodes
        self.edges: dict[tuple[str, str], float] = {}
        self.blocked: set[tuple[str, str]] = set()
        self.adjacency: dict[str, dict[str, float]] = {node: {} for node in nodes}
        if edges is None:
            self._connect_neighbours()
        else:
            for edge in edges:
                self._add(edge["a"], edge["b"], edge["length"])

    def _add(self, a: str, b: str, length: float) -> None:
        key = tuple(sorted((a, b)))
        if a not in self.nodes or b not in self.nodes or a == b:
            raise ValueError(f"Invalid road endpoints: {a}, {b}")
        if isinstance(length, bool) or not isinstance(length, (int, float)):
            raise ValueError("Road length must be numeric")
        if not math.isfinite(length) or length <= 0 or key in self.edges:
            raise ValueError(f"Invalid or duplicate road: {a}, {b}")
        self.edges[key] = float(length)
        self.adjacency[a][b] = float(length)
        self.adjacency[b][a] = float(length)

    def _connect_neighbours(self) -> None:
        for a in sorted(self.nodes):
            nearest = sorted(
                (math.hypot(self.nodes[a].x - node.x, self.nodes[a].y - node.y), b)
                for b, node in self.nodes.items() if b != a
            )
            for distance, b in nearest[:3]:
                if tuple(sorted((a, b))) not in self.edges:
                    self._add(a, b, max(distance, 1e-6))
        # Nearest-neighbour graphs can be disconnected; bridge components.
        while True:
            root = min(self.nodes)
            reached = {node for node in self.nodes if self.route(root, node)[0]}
            if len(reached) == len(self.nodes):
                break
            distance, a, b = min(
                (math.hypot(self.nodes[a].x - self.nodes[b].x,
                            self.nodes[a].y - self.nodes[b].y), a, b)
                for a in sorted(reached) for b in sorted(self.nodes.keys() - reached)
            )
            self._add(a, b, max(distance, 1e-6))

    def route(self, a: str, b: str) -> tuple[list[str], float]:
        """Return a shortest path and length, or ([], infinity) if unreachable."""
        if a not in self.nodes or b not in self.nodes:
            return [], math.inf
        queue = [(0.0, (a,))]
        best = {a: 0.0}
        while queue:
            distance, path = heapq.heappop(queue)
            node = path[-1]
            if distance > best[node]:
                continue
            if node == b:
                return list(path), distance
            for neighbour, length in sorted(self.adjacency[node].items()):
                if tuple(sorted((node, neighbour))) in self.blocked:
                    continue
                total = distance + length
                if total < best.get(neighbour, math.inf):
                    best[neighbour] = total
                    heapq.heappush(queue, (total, (*path, neighbour)))
        return [], math.inf

    def block(self, a: str, b: str) -> None:
        """Close a known road to new entries."""
        key = tuple(sorted((a, b)))
        if key not in self.edges:
            raise ValueError(f"Unknown road: {a}, {b}")
        self.blocked.add(key)

    def unblock(self, a: str, b: str) -> None:
        """Reopen a known road."""
        key = tuple(sorted((a, b)))
        if key not in self.edges:
            raise ValueError(f"Unknown road: {a}, {b}")
        self.blocked.discard(key)
