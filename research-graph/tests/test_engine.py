import pytest

from rdg.alerts import Choice, Decision, Subscription, notifications
from rdg.demo import TEAM_NODE, demo_graph, demo_papers
from rdg.graph import CycleError, ResearchGraph
from rdg.models import Challenge, Claim, Dependency, Paper
from rdg.models import DependencyKind as K
from rdg.propagation import Status, assess, explain


def p(pid, *claims):
    return Paper(pid, pid, tuple(Claim(c, c) for c in (claims or ("main",))))


# ---- graph -----------------------------------------------------------------

def test_cycle_rejected_on_add():
    g = ResearchGraph.build([p("a"), p("b")], [Dependency("b", "a", K.CITATION)])
    with pytest.raises(CycleError):
        g.with_dependency(Dependency("a", "b", K.CITATION))


def test_cycle_rejected_on_build():
    with pytest.raises(CycleError):
        ResearchGraph.build([p("a"), p("b")], [Dependency("b", "a", K.CITATION), Dependency("a", "b", K.CITATION)])


def test_unknown_paper_and_claim_rejected():
    g = ResearchGraph.build([p("a"), p("b")])
    with pytest.raises(KeyError):
        g.with_dependency(Dependency("b", "zzz", K.CITATION))
    with pytest.raises(KeyError):
        g.with_dependency(Dependency("b", "a", K.CITATION, source_claims=frozenset({"nope"})))
    with pytest.raises(KeyError):
        g.with_challenge(Challenge("a", 0.0, "x", claim_ids=frozenset({"nope"})))


def test_with_methods_do_not_mutate():
    g = ResearchGraph.build([p("a")])
    g2 = g.with_paper(p("b")).with_dependency(Dependency("b", "a", K.CITATION))
    assert set(g.papers) == {"a"} and g.dependencies == ()
    assert set(g2.papers) == {"a", "b"}


def test_model_validation():
    with pytest.raises(ValueError):
        Paper("x", "x", ())
    with pytest.raises(ValueError):
        Dependency("a", "a", K.CITATION)
    with pytest.raises(ValueError):
        Challenge("a", 1.5, "x")


def test_topological_order_foundations_first():
    order = demo_graph().topological_order()
    assert order.index("lee-2021") < order.index("ortiz-2022") < order.index("chen-2023") < order.index(TEAM_NODE)


# ---- propagation -----------------------------------------------------------

def test_no_challenges_everything_ok():
    r = assess(demo_graph(challenged=False))
    assert all(a.status is Status.OK and a.validity == 1.0 for a in r.values())


def test_partial_invalidation_only_hits_contested_claim():
    r = assess(demo_graph())
    assert r[("lee-2021", "spec")].status is Status.CONTESTED
    assert r[("lee-2021", "aff")].validity == 1.0


def test_methodology_chain_propagates_multi_hop():
    r = assess(demo_graph())
    assert r[("ortiz-2022", "protocol")].validity == pytest.approx(0.1)
    assert r[("chen-2023", "mech")].validity == pytest.approx(0.1)
    assert r[("chen-2023", "mech")].status is Status.AT_RISK
    assert r[(TEAM_NODE, "h3")].roots == frozenset({("lee-2021", "spec")})


def test_antibody_free_branch_survives():
    r = assess(demo_graph())
    assert r[("chen-2023", "dose")].validity == 1.0
    assert r[(TEAM_NODE, "h2")].status is Status.AT_RISK


def test_unmapped_citation_is_damped_and_flagged():
    r = assess(demo_graph())
    review = r[("rao-2024", "summary")]
    assert review.validity == pytest.approx(1 - 0.3 * 0.9)
    assert review.status is Status.REVIEW_SUGGESTED
    assert review.needs_portion_check
    # h1 only touches the challenge through a background citation of the review
    h1 = r[(TEAM_NODE, "h1")]
    assert h1.validity > 0.9 and h1.status is Status.OK


def test_explain_traces_paths_to_root():
    paths = explain(assess(demo_graph()), (TEAM_NODE, "h3"))
    assert paths == [[(TEAM_NODE, "h3"), ("chen-2023", "mech"), ("ortiz-2022", "protocol"), ("lee-2021", "spec")]]


def test_multiple_challenges_take_min():
    g = ResearchGraph.build([p("a")], challenges=[Challenge("a", 0.6, "x"), Challenge("a", 0.2, "y")])
    assert assess(g)[("a", "main")].validity == 0.2


# ---- alerts ----------------------------------------------------------------

def test_subscriber_gets_heads_up_not_block():
    r = assess(demo_graph())
    notes = notifications(r, (Subscription("ana", TEAM_NODE),))
    flagged = {n.key[1] for n in notes}
    assert flagged == {"h2", "h3"}
    assert all("not a verdict" in n.message for n in notes)


def test_decision_silences_until_new_evidence():
    g = demo_graph()
    r = assess(g)
    a = r[(TEAM_NODE, "h2")]
    dec = Decision("ana", a.key, Choice.CONTINUE, a.validity, a.roots)
    subs = (Subscription("ana", TEAM_NODE, frozenset({"h2"})),)
    assert notifications(r, subs, (dec,)) == ()
    # a second, independent challenge on the same path re-fires
    g2 = g.with_paper(p("z")).with_dependency(Dependency(TEAM_NODE, "z", K.METHODOLOGY, dependent_claims=frozenset({"h2"})))
    g2 = g2.with_challenge(Challenge("z", 0.5, "new"))
    assert len(notifications(assess(g2), subs, (dec,))) == 1


def test_demo_papers_are_unique():
    ids = [x.id for x in demo_papers()]
    assert len(ids) == len(set(ids))
