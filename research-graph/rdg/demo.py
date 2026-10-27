"""The antibody scenario from the design meeting, as a ready-made graph.

A replication study finds antibody mAb-7 cross-reacts, so its *specificity* claim is
overturned but its measured *affinity* stands. What downstream still holds?
"""
from __future__ import annotations

from .graph import ResearchGraph
from .models import Challenge, Claim, Dependency, Paper
from .models import DependencyKind as K

TEAM_NODE = "our-program"


def demo_papers() -> tuple[Paper, ...]:
    return (
        Paper("lee-2021", "mAb-7: a selective antibody against KRAS G12D", (
            Claim("spec", "mAb-7 binds KRAS G12D and not wild-type KRAS"),
            Claim("aff", "mAb-7 binds G12D with Kd ≈ 4 nM"),
        ), doi="10.0000/demo.lee2021"),
        Paper("ortiz-2022", "A pull-down assay for mutant KRAS using mAb-7", (
            Claim("protocol", "mAb-7 pull-down isolates G12D-bound complexes"),
        )),
        Paper("chen-2023", "G12D signals through the PX pathway", (
            Claim("mech", "G12D complexes recruit PX adaptor (via mAb-7 pull-down)"),
            Claim("dose", "PX knockout reduces G12D tumour growth (CRISPR, antibody-free)"),
        )),
        Paper("rao-2024", "Review: targeting mutant KRAS", (
            Claim("summary", "Survey of G12D-selective tools and targets"),
        )),
        Paper("kim-2025", "mAb-7 cross-reacts with wild-type KRAS", (
            Claim("finding", "Independent replication: mAb-7 is not G12D-selective"),
        ), doi="10.0000/demo.kim2025"),
        Paper(TEAM_NODE, "Our program: PX inhibition in G12D tumours", (
            Claim("h1", "Inhibiting PX slows G12D tumour growth"),
            Claim("h2", "mAb-7 staining can stratify G12D patients"),
            Claim("h3", "PX adaptor binding is the druggable step"),
        )),
    )


def demo_dependencies() -> tuple[Dependency, ...]:
    return (
        Dependency("ortiz-2022", "lee-2021", K.METHODOLOGY, frozenset({"spec"}),
                   context="We used the G12D-selective antibody mAb-7 [Lee 2021]."),
        Dependency("chen-2023", "ortiz-2022", K.METHODOLOGY, frozenset({"protocol"}), frozenset({"mech"})),
        Dependency("rao-2024", "lee-2021", K.CITATION),  # unmapped: which part does the review rely on?
        Dependency(TEAM_NODE, "chen-2023", K.SUBJECT, frozenset({"dose"}), frozenset({"h1"})),
        Dependency(TEAM_NODE, "lee-2021", K.METHODOLOGY, frozenset({"spec"}), frozenset({"h2"})),
        Dependency(TEAM_NODE, "chen-2023", K.METHODOLOGY, frozenset({"mech"}), frozenset({"h3"})),
        Dependency(TEAM_NODE, "rao-2024", K.CITATION, dependent_claims=frozenset({"h1"})),
    )


def demo_challenge() -> Challenge:
    return Challenge(
        "lee-2021", validity=0.1, claim_ids=frozenset({"spec"}),
        evidence="Kim 2025 replication: cross-reactivity with wild-type KRAS",
        source="10.0000/demo.kim2025",
    )


def demo_graph(challenged: bool = True) -> ResearchGraph:
    return ResearchGraph.build(
        demo_papers(), demo_dependencies(), (demo_challenge(),) if challenged else ()
    )
