from rdg.demo import TEAM_NODE, demo_graph
from rdg.importer import import_references, kind_from_intents
from rdg.lean_export import derivable, export, lean_name
from rdg.models import DependencyKind as K


def test_lean_derivability_matches_scenario():
    ok = derivable(demo_graph())
    assert ok[(TEAM_NODE, "h1")]  # antibody-free route + citation isn't a premise
    assert not ok[(TEAM_NODE, "h2")]
    assert not ok[(TEAM_NODE, "h3")]
    assert ok[("lee-2021", "aff")]
    assert not ok[("lee-2021", "spec")]


def test_lean_export_emits_theorem_for_survivors_only():
    src = export(demo_graph())
    assert f"theorem {lean_name((TEAM_NODE, 'h1'))}_stands" in src
    assert f"theorem {lean_name((TEAM_NODE, 'h2'))}_stands" not in src
    assert f"(_h0 : ¬ {lean_name(('lee-2021', 'spec'))})" in src
    assert src.strip().endswith("end ResearchGraph")


def test_kind_from_intents():
    assert kind_from_intents(["background", "methodology"]) is K.METHODOLOGY
    assert kind_from_intents(["result"]) is K.SUBJECT
    assert kind_from_intents([]) is K.CITATION
    assert kind_from_intents(None) is K.CITATION


def test_import_references_with_fake_api():
    def fetch(url):
        if "openalex" in url:
            if "retracted" in url:
                return {"is_retracted": True, "id": "W1"}
            raise OSError("404")
        if url.endswith("/references?fields=title,externalIds,intents,contexts,isInfluential&limit=100"):
            return {"data": [
                {"citedPaper": {"title": "Method", "externalIds": {"DOI": "10.1/method"}}, "intents": ["methodology"], "contexts": ["we used X [1]"]},
                {"citedPaper": {"title": "Bad", "externalIds": {"DOI": "10.1/retracted"}}, "intents": ["background"]},
                {"citedPaper": {"title": "Bad", "externalIds": {"DOI": "10.1/retracted"}}, "intents": ["result"]},
                {"citedPaper": {"title": None, "externalIds": None, "paperId": None}},
            ]}
        return {"title": "Root", "externalIds": {"DOI": "10.1/root"}}

    res = import_references("10.1/root", fetch=fetch)
    kinds = {d.source_paper: d.kind for d in res.dependencies}
    assert kinds == {"doi:10.1/method": K.METHODOLOGY, "doi:10.1/retracted": K.SUBJECT}
    assert [c.paper_id for c in res.challenges] == ["doi:10.1/retracted"]
    assert len(res.skipped) == 1
    assert all(d.is_unmapped for d in res.dependencies)


def test_import_falls_back_to_openalex_when_s2_withholds_references():
    def fetch(url):
        if "semanticscholar" in url and "/references" in url:
            return {"data": None}
        if "semanticscholar" in url:
            return {"title": "Root", "externalIds": {"DOI": "10.1/root"}}
        if "select=title,referenced_works" in url:
            return {"title": "Root", "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"]}
        if "filter=openalex" in url:
            return {"results": [
                {"id": "https://openalex.org/W1", "title": "One", "doi": "https://doi.org/10.1/one"},
                {"id": "https://openalex.org/W2", "title": "Two", "doi": None},
            ]}
        return {"is_retracted": False}

    res = import_references("10.1/root", fetch=fetch)
    assert {d.source_paper for d in res.dependencies} == {"doi:10.1/one", "W2"}
    assert all(d.kind is K.CITATION for d in res.dependencies)


def test_import_uses_openalex_when_s2_rate_limits():
    import urllib.error

    def fetch(url):
        if "semanticscholar" in url:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)
        if "select=title,referenced_works" in url:
            return {"title": "Root", "referenced_works": ["https://openalex.org/W1"]}
        if "filter=openalex" in url:
            return {"results": [{"id": "https://openalex.org/W1", "title": "One", "doi": None}]}
        return {"is_retracted": False}

    res = import_references("10.1/root", fetch=fetch)
    assert [p.title for p in res.papers] == ["Root", "One"]
    assert [d.source_paper for d in res.dependencies] == ["W1"]
