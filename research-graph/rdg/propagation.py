"""Propagate challenges down the DAG into per-claim validity scores.

For each claim, in topological order:

    validity = base * prod over supporting source claims s of (1 - w * (1 - validity(s)))

`base` is the lowest validity any direct challenge assigns that claim (1.0 if none).
`w` is the edge weight (methodology > subject > citation). A disproven methodology
dependency (w=1, v=0) zeroes the claim; a disproven background citation (w=0.3)
only drops it to 0.7. Scores are guidance, never verdicts.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .graph import ResearchGraph
from .models import Dependency, DependencyKind

ClaimKey = tuple[str, str]  # (paper_id, claim_id)

REVIEW_THRESHOLD = 0.8
AT_RISK_THRESHOLD = 0.5
NEGLIGIBLE = 1e-9


class Status(str, Enum):
    OK = "ok"
    REVIEW_SUGGESTED = "review_suggested"
    AT_RISK = "at_risk"
    CONTESTED = "contested"  # directly challenged, not just inherited


@dataclass(frozen=True)
class Cause:
    """One upstream claim that lowered this claim's validity."""

    source: ClaimKey
    kind: DependencyKind
    source_validity: float
    factor: float  # multiplier applied to this claim
    unmapped: bool  # True => we guessed which part of the source is relied on


@dataclass(frozen=True)
class Assessment:
    key: ClaimKey
    validity: float
    base_validity: float
    status: Status
    causes: tuple[Cause, ...]
    roots: frozenset[ClaimKey]  # the directly challenged claims behind this score

    @property
    def needs_portion_check(self) -> bool:
        """The user should confirm whether the part they rely on is the contested part."""
        return any(c.unmapped for c in self.causes)


def classify(validity: float, directly_challenged: bool) -> Status:
    if directly_challenged:
        return Status.CONTESTED
    if validity < AT_RISK_THRESHOLD:
        return Status.AT_RISK
    if validity < REVIEW_THRESHOLD:
        return Status.REVIEW_SUGGESTED
    return Status.OK


def assess(graph: ResearchGraph) -> dict[ClaimKey, Assessment]:
    """Score every claim in the graph."""
    base = _base_validity(graph)
    results: dict[ClaimKey, Assessment] = {}
    for pid in graph.topological_order():
        paper = graph.papers[pid]
        incoming = graph.upstream_of(pid)
        for claim in paper.claims:
            key = (pid, claim.id)
            results[key] = _assess_claim(graph, key, base.get(key, 1.0), incoming, results)
    return results


def _base_validity(graph: ResearchGraph) -> dict[ClaimKey, float]:
    base: dict[ClaimKey, float] = {}
    for ch in graph.challenges:
        targets = ch.claim_ids or graph.papers[ch.paper_id].claim_ids
        for cid in targets:
            key = (ch.paper_id, cid)
            base[key] = min(base.get(key, 1.0), ch.validity)
    return base


def _supporting_sources(
    graph: ResearchGraph, dep: Dependency, results: dict[ClaimKey, Assessment]
) -> tuple[ClaimKey, ...]:
    if dep.source_claims:
        return tuple((dep.source_paper, cid) for cid in sorted(dep.source_claims))
    # unmapped: assume we rely on the weakest part of the source, and flag it
    keys = [(dep.source_paper, c.id) for c in graph.papers[dep.source_paper].claims]
    return (min(keys, key=lambda k: (results[k].validity, k)),)


def _assess_claim(
    graph: ResearchGraph,
    key: ClaimKey,
    base: float,
    incoming: tuple[Dependency, ...],
    results: dict[ClaimKey, Assessment],
) -> Assessment:
    validity = base
    causes: list[Cause] = []
    roots: set[ClaimKey] = {key} if base < 1.0 else set()
    for dep in incoming:
        if dep.dependent_claims and key[1] not in dep.dependent_claims:
            continue
        for src in _supporting_sources(graph, dep, results):
            upstream = results[src]
            factor = 1.0 - dep.effective_weight * (1.0 - upstream.validity)
            if factor >= 1.0 - NEGLIGIBLE:
                continue
            validity *= factor
            roots |= upstream.roots
            causes.append(Cause(src, dep.kind, upstream.validity, factor, dep.is_unmapped))
    return Assessment(
        key=key,
        validity=validity,
        base_validity=base,
        status=classify(validity, base < 1.0),
        causes=tuple(causes),
        roots=frozenset(roots),
    )


def explain(results: dict[ClaimKey, Assessment], key: ClaimKey) -> list[list[ClaimKey]]:
    """Every path from `key` back up to a directly challenged claim."""
    node = results[key]
    if not node.causes:
        return [[key]] if node.base_validity < 1.0 else []
    paths = [[key]] if node.base_validity < 1.0 else []
    for cause in node.causes:
        paths.extend([key] + p for p in explain(results, cause.source))
    return paths
