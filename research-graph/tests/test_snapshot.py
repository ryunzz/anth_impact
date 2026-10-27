import pytest

from rdg.graph import ResearchGraph
from rdg.lean_export import derivable
from rdg.models import Challenge, Claim, Dependency, Paper
from rdg.models import DependencyKind as K
from rdg.propagation import Status, assess
from rdg.snapshot import TEAM_NODE, _acyclic, load_snapshot


def p(pid):
    return Paper(pid, pid, (Claim("main", pid),))


def test_diamond_counts_root_once():
    # d reaches a through b and c: should match a single path, not compound
    g = ResearchGraph.build(
        [p("a"), p("b"), p("c"), p("d")],
        [Dependency("b", "a", K.CITATION), Dependency("c", "a", K.CITATION),
         Dependency("d", "b", K.METHODOLOGY), Dependency("d", "c", K.METHODOLOGY)],
        [Challenge("a", 0.0, "x")],
    )
    assert assess(g)[("d", "main")].validity == pytest.approx(0.7)


def test_acyclic_drops_back_edge():
    deps = [Dependency("b", "a", K.CITATION), Dependency("a", "b", K.CITATION)]
    assert _acyclic(deps) == [deps[0]]


def test_lesne_snapshot_story():
    g = load_snapshot()
    r, ok = assess(g), derivable(g)
    root = next(k for k, a in r.items() if a.status is Status.CONTESTED)
    assert root[0].startswith("lesn")
    assert r[(TEAM_NODE, "h1")].status is Status.AT_RISK
    assert r[(TEAM_NODE, "h2")].status is Status.OK and ok[(TEAM_NODE, "h2")]
    citing = [a for k, a in r.items() if k[0] not in (root[0], TEAM_NODE)]
    assert citing and all(a.validity == pytest.approx(0.7) for a in citing)


def test_snapshot_without_retraction_is_clean():
    assert all(a.status is Status.OK for a in assess(load_snapshot(retracted=False)).values())
