import io
import json

import pytest

from rdg import snapshot
from rdg.analyze import AnalyzeError, analyze, doi_from_pdf, doi_from_text, resolve
from rdg.server import Registry, scenario_id
from rdg.verify import verify


@pytest.mark.parametrize("text,doi", [
    ("https://doi.org/10.1038/nature04533", "10.1038/nature04533"),
    ("doi:10.1038/NM1782.", "10.1038/nm1782"),
    ("@article{x, doi = {10.1038/nature04533},}", "10.1038/nature04533"),
    ("https://www.nature.com/articles/10.1038%2Fnature04533", "10.1038/nature04533"),
    ("no doi here", None),
])
def test_doi_from_text(text, doi):
    assert doi_from_text(text) == doi


def test_resolve_errors_without_doi():
    with pytest.raises(AnalyzeError):
        resolve(text="hello")


def test_doi_from_pdf_metadata():
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(100, 100)
    w.add_metadata({"/Subject": "doi:10.1038/nature04533"})
    buf = io.BytesIO()
    w.write(buf)
    assert doi_from_pdf(buf.getvalue()) == "10.1038/nature04533"


def _offline_fetch(url):
    raise OSError("offline")


def test_analyze_falls_back_to_saved_snapshot_offline():
    events = list(analyze(resolve(text="10.1038/nature04533"), fetch=_offline_fetch))
    assert any(e["type"] == "step" and e["status"] == "warn" and "snapshot" in e["detail"] for e in events)
    result = events[-1]
    assert result["type"] == "result"
    assert result["scenario"]["challenge"]["paper"].startswith("lesn")


def test_analyze_unknown_paper_offline_is_an_error():
    with pytest.raises(AnalyzeError):
        list(analyze(resolve(text="10.9999/unknown"), fetch=_offline_fetch))


def test_analyze_live_shape_with_fake_openalex():
    data = json.loads(snapshot.SNAPSHOT.read_text(encoding="utf-8"))
    raw_root = {"id": "https://openalex.org/" + data["root"]["id"], "title": "Root paper", "publication_year": 2020,
                "doi": "https://doi.org/10.1234/root", "authorships": [{"author": {"display_name": "Ada Lovelace"}}],
                "cited_by_count": 5, "is_retracted": False, "referenced_works": []}

    def fetch(url):
        return {"results": []} if "cites:" in url else raw_root

    events = list(analyze(resolve(text="10.1234/root"), fetch=fetch))
    result = events[-1]
    assert result["scenario"]["label"] == "Lovelace 2020"
    assert result["scenario"]["challenge"]["paper"] is None
    assert [p["claims"][0]["text"] for p in result["scenario"]["after"]["papers"] if p["id"] == "our-startup"]


def test_registry_and_ids():
    reg = Registry()
    reg.add("x", {"label": "x"}, snapshot.load_snapshot)
    assert "x" in reg.scenarios and reg.graph("x") is not None and reg.graph("nope") is None
    assert scenario_id("10.1038/Nature04533") == "doi-10-1038-nature04533"


def test_verify_reports_axioms_or_missing_lean(monkeypatch):
    result = verify(snapshot.load_snapshot())
    assert result["challenged"] and "our-startup#h1" in result["noDerivation"]
    if result.get("error"):
        pytest.skip(result["error"])
    assert result["ok"]
    h2 = next(t for t in result["theorems"] if t["claim"] == "our-startup#h2")
    assert all("lesn" not in u["key"] for u in h2["uses"])
