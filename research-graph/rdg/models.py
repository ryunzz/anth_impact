"""Core immutable data types for the research dependency graph."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DependencyKind(str, Enum):
    """How a dependent paper relies on a source paper."""

    METHODOLOGY = "methodology"  # uses its method, assay, model, protocol
    SUBJECT = "subject"  # studies the same target / mechanism / cell line
    CITATION = "citation"  # background citation only


# How strongly a problem in the source carries into the dependent, per kind.
DEFAULT_KIND_WEIGHT: dict[DependencyKind, float] = {
    DependencyKind.METHODOLOGY: 1.0,
    DependencyKind.SUBJECT: 0.6,
    DependencyKind.CITATION: 0.3,
}


@dataclass(frozen=True)
class Claim:
    """One separable finding inside a paper, so invalidation can be partial."""

    id: str
    text: str


@dataclass(frozen=True)
class Paper:
    id: str
    title: str
    claims: tuple[Claim, ...]
    doi: str | None = None

    def __post_init__(self) -> None:
        if not self.claims:
            raise ValueError(f"paper {self.id!r} needs at least one claim")
        ids = [c.id for c in self.claims]
        if len(ids) != len(set(ids)):
            raise ValueError(f"paper {self.id!r} has duplicate claim ids")

    @property
    def claim_ids(self) -> frozenset[str]:
        return frozenset(c.id for c in self.claims)


@dataclass(frozen=True)
class Dependency:
    """`dependent_paper` builds on `source_paper`.

    Empty `source_claims` means we don't know which part of the source is relied on
    ("unmapped"); the system then assumes the weakest part and asks the user to check.
    Empty `dependent_claims` means every claim of the dependent is affected.
    """

    dependent_paper: str
    source_paper: str
    kind: DependencyKind
    source_claims: frozenset[str] = frozenset()
    dependent_claims: frozenset[str] = frozenset()
    weight: float | None = None
    context: str | None = None  # the citing sentence, when known

    def __post_init__(self) -> None:
        if self.dependent_paper == self.source_paper:
            raise ValueError("a paper cannot depend on itself")
        if self.weight is not None and not 0.0 <= self.weight <= 1.0:
            raise ValueError("weight must be in [0, 1]")

    @property
    def effective_weight(self) -> float:
        return DEFAULT_KIND_WEIGHT[self.kind] if self.weight is None else self.weight

    @property
    def is_unmapped(self) -> bool:
        return not self.source_claims


@dataclass(frozen=True)
class Challenge:
    """Evidence against (part of) a paper: retraction, failed replication, rebuttal.

    `validity` is the remaining confidence in the challenged claims:
    0.0 = disproven, 1.0 = unaffected. Empty `claim_ids` = the whole paper.
    """

    paper_id: str
    validity: float
    evidence: str
    claim_ids: frozenset[str] = frozenset()
    source: str | None = None  # DOI / URL of the challenging work

    def __post_init__(self) -> None:
        if not 0.0 <= self.validity <= 1.0:
            raise ValueError("validity must be in [0, 1]")
