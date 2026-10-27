"""Offline dataset: a retracted paper plus its most-cited citing works, from OpenAlex.

`python -m rdg.snapshot` fetches once and writes data/lesne_snapshot.json, so the
viewer works without network access. `load_snapshot()` turns that file into a graph.

Edge kinds use a simple rule on each citing work's abstract: one that names the
retracted paper's central entity is a SUBJECT dependency, otherwise a background
CITATION. A team's own program node is added on top with explicit dependencies.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from datetime import date
from pathlib import Path

from .graph import ResearchGraph
from .importer import OPENALEX_BASE, Fetch, http_get_json
from .models import Challenge, Claim, Dependency, Paper
from .models import DependencyKind as K

ROOT_DOI = "10.1038/nature04533"  # Lesné et al. 2006, retracted 2024
ALT_DOI = "10.1038/nm1782"  # Shankar et al. 2008, Aβ dimers from AD brains
SNAPSHOT = Path(__file__).resolve().parent.parent / "data" / "lesne_snapshot.json"
N_CITING = 10
SUBJECT_PATTERN = re.compile(r"A[βb](eta)?\s*\*\s*56|56[\s-]*kDa", re.IGNORECASE)
TEAM_NODE = "our-startup"


def _abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    words = sorted((pos, w) for w, positions in inv.items() for pos in positions)
    return " ".join(w for _, w in words)


def _short_id(openalex_id: str) -> str:
    return openalex_id.rsplit("/", 1)[-1]


def _work(w: dict) -> dict:
    authors = [a["author"]["display_name"] for a in w.get("authorships", [])]
    return {
        "id": _short_id(w["id"]),
        "title": w.get("title") or "",
        "year": w.get("publication_year"),
        "doi": (w.get("doi") or "").removeprefix("https://doi.org/") or None,
        "first_author": authors[0] if authors else None,
        "cited_by": w.get("cited_by_count", 0),
        "is_retracted": bool(w.get("is_retracted")),
        "abstract": _abstract(w.get("abstract_inverted_index")),
        "references": [_short_id(r) for r in w.get("referenced_works", [])],
    }


def fetch_snapshot(fetch: Fetch = http_get_json, n: int = N_CITING) -> dict:
    fields = "id,title,publication_year,doi,authorships,cited_by_count,is_retracted,abstract_inverted_index,referenced_works"
    root = _work(fetch(f"{OPENALEX_BASE}/works/doi:{ROOT_DOI}?select={fields}"))
    query = urllib.parse.urlencode(
        {"filter": f"cites:{root['id']}", "sort": "cited_by_count:desc", "per-page": n, "select": fields}
    )
    citing = [_work(w) for w in fetch(f"{OPENALEX_BASE}/works?{query}")["results"]]
    return {"source": "OpenAlex", "fetched": date.today().isoformat(), "root": root, "citing": citing}


def _label(w: dict) -> str:
    who = (w["first_author"] or "").split()[-1] if w["first_author"] else "?"
    return f"{who.lower()}-{w['year']}-{w['id'][-4:].lower()}"


def load_snapshot(path: Path = SNAPSHOT, retracted: bool = True) -> ResearchGraph:
    data = json.loads(path.read_text(encoding="utf-8"))
    root, citing = data["root"], data["citing"]
    ids = {w["id"]: _label(w) for w in [root, *citing]}
    papers = [
        Paper(ids[w["id"]], w["title"], (Claim("main", w["title"]),), doi=w["doi"])
        for w in [root, *citing]
    ]
    deps: list[Dependency] = []
    for w in citing:
        for ref in w["references"]:
            if ref not in ids or ref == w["id"]:
                continue
            kind = K.SUBJECT if ref == root["id"] and SUBJECT_PATTERN.search(w["abstract"]) else K.CITATION
            deps.append(Dependency(ids[w["id"]], ids[ref], kind))
    papers.append(_team_paper())
    deps.extend(_team_dependencies(ids[root["id"]], citing, ids))
    challenges = (
        (Challenge(ids[root["id"]], 0.0, "Retracted by Nature (2024)", source=f"https://doi.org/{root['doi']}"),)
        if retracted and root["is_retracted"] else ()
    )
    return ResearchGraph.build(papers, _acyclic(deps), challenges)


def _team_paper() -> Paper:
    return Paper(TEAM_NODE, "Example startup: oligomer-targeting Alzheimer's program", (
        Claim("h1", "Aβ*56 is the oligomer species driving memory loss"),
        Claim("h2", "Lowering soluble Aβ oligomers improves synaptic function"),
    ))


def _team_dependencies(root_label: str, citing: list[dict], ids: dict[str, str]) -> list[Dependency]:
    deps = [Dependency(TEAM_NODE, root_label, K.METHODOLOGY, dependent_claims=frozenset({"h1"}))]
    # h2 rests on the brain-derived dimer work, which cites the retracted paper only as background
    alt = next((w for w in citing if w["doi"] and w["doi"].lower() == ALT_DOI), None)
    if alt:
        deps.append(Dependency(TEAM_NODE, ids[alt["id"]], K.SUBJECT, dependent_claims=frozenset({"h2"})))
    return deps


def _acyclic(deps: list[Dependency]) -> list[Dependency]:
    """Drop edges that would close a cycle (works citing each other's preprints)."""
    kept: list[Dependency] = []
    for d in deps:
        upstream = {d.source_paper}
        frontier = [d.source_paper]
        while frontier:
            node = frontier.pop()
            for k in kept:
                if k.dependent_paper == node and k.source_paper not in upstream:
                    upstream.add(k.source_paper)
                    frontier.append(k.source_paper)
        if d.dependent_paper not in upstream:
            kept.append(d)
    return kept


def main() -> None:
    SNAPSHOT.parent.mkdir(exist_ok=True)
    SNAPSHOT.write_text(json.dumps(fetch_snapshot(), ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
