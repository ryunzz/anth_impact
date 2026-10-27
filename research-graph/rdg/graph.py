"""The research DAG: papers, dependency edges, challenges. Immutable; `with_*` returns a new graph."""
from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .models import Challenge, Dependency, Paper


class CycleError(ValueError):
    pass


@dataclass(frozen=True)
class ResearchGraph:
    papers: Mapping[str, Paper] = field(default_factory=lambda: MappingProxyType({}))
    dependencies: tuple[Dependency, ...] = ()
    challenges: tuple[Challenge, ...] = ()

    @classmethod
    def build(
        cls,
        papers: Iterable[Paper],
        dependencies: Iterable[Dependency] = (),
        challenges: Iterable[Challenge] = (),
    ) -> ResearchGraph:
        """Bulk constructor; validates everything once."""
        paper_map: dict[str, Paper] = {}
        for p in papers:
            if p.id in paper_map:
                raise ValueError(f"duplicate paper id {p.id!r}")
            paper_map[p.id] = p
        graph = cls(MappingProxyType(paper_map), tuple(dependencies), tuple(challenges))
        for d in graph.dependencies:
            graph._validate_dependency(d)
        for c in graph.challenges:
            graph._validate_challenge(c)
        graph.topological_order()  # raises CycleError
        return graph

    # ---- immutable updates -------------------------------------------------

    def with_paper(self, paper: Paper) -> ResearchGraph:
        if paper.id in self.papers:
            raise ValueError(f"duplicate paper id {paper.id!r}")
        papers = MappingProxyType({**self.papers, paper.id: paper})
        return ResearchGraph(papers, self.dependencies, self.challenges)

    def with_dependency(self, dep: Dependency) -> ResearchGraph:
        self._validate_dependency(dep)
        if self._reaches(dep.source_paper, dep.dependent_paper):
            raise CycleError(
                f"{dep.dependent_paper!r} -> {dep.source_paper!r} would create a cycle"
            )
        return ResearchGraph(self.papers, self.dependencies + (dep,), self.challenges)

    def with_challenge(self, challenge: Challenge) -> ResearchGraph:
        self._validate_challenge(challenge)
        return ResearchGraph(self.papers, self.dependencies, self.challenges + (challenge,))

    # ---- queries -----------------------------------------------------------

    def upstream_of(self, paper_id: str) -> tuple[Dependency, ...]:
        """Edges where `paper_id` is the dependent (what it builds on)."""
        return tuple(d for d in self.dependencies if d.dependent_paper == paper_id)

    def downstream_of(self, paper_id: str) -> tuple[Dependency, ...]:
        """Edges where `paper_id` is the source (who builds on it)."""
        return tuple(d for d in self.dependencies if d.source_paper == paper_id)

    def topological_order(self) -> tuple[str, ...]:
        """Foundational papers first. Raises CycleError if the graph isn't a DAG."""
        indegree = {pid: 0 for pid in self.papers}
        for d in self.dependencies:
            indegree[d.dependent_paper] += 1
        queue = deque(sorted(pid for pid, n in indegree.items() if n == 0))
        order: list[str] = []
        while queue:
            pid = queue.popleft()
            order.append(pid)
            for d in self.downstream_of(pid):
                indegree[d.dependent_paper] -= 1
                if indegree[d.dependent_paper] == 0:
                    queue.append(d.dependent_paper)
        if len(order) != len(self.papers):
            stuck = sorted(set(self.papers) - set(order))
            raise CycleError(f"dependency cycle among: {stuck}")
        return tuple(order)

    # ---- internals ---------------------------------------------------------

    def _reaches(self, start: str, target: str) -> bool:
        """True if walking upstream (towards foundations) from `start` reaches `target`."""
        seen, stack = set(), [start]
        while stack:
            pid = stack.pop()
            if pid == target:
                return True
            if pid in seen:
                continue
            seen.add(pid)
            stack.extend(d.source_paper for d in self.upstream_of(pid))
        return False

    def _validate_dependency(self, d: Dependency) -> None:
        for pid, claims in ((d.dependent_paper, d.dependent_claims), (d.source_paper, d.source_claims)):
            if pid not in self.papers:
                raise KeyError(f"unknown paper {pid!r}")
            unknown = claims - self.papers[pid].claim_ids
            if unknown:
                raise KeyError(f"unknown claims {sorted(unknown)} on paper {pid!r}")

    def _validate_challenge(self, c: Challenge) -> None:
        if c.paper_id not in self.papers:
            raise KeyError(f"unknown paper {c.paper_id!r}")
        unknown = c.claim_ids - self.papers[c.paper_id].claim_ids
        if unknown:
            raise KeyError(f"unknown claims {sorted(unknown)} on paper {c.paper_id!r}")
