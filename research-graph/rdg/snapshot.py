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


FIELDS = "id,title,publication_year,doi,authorships,cited_by_count,is_retracted,abstract_inverted_index,referenced_works"


def fetch_root(doi: str, fetch: Fetch = http_get_json) -> dict:
    return _work(fetch(f"{OPENALEX_BASE}/works/doi:{urllib.parse.quote(doi, safe='/')}?select={FIELDS}"))


def fetch_citing(root_id: str, fetch: Fetch = http_get_json, n: int = N_CITING) -> list[dict]:
    query = urllib.parse.urlencode(
        {"filter": f"cites:{root_id}", "sort": "cited_by_count:desc", "per-page": n, "select": FIELDS}
    )
    return [_work(w) for w in fetch(f"{OPENALEX_BASE}/works?{query}").get("results") or []]


def fetch_snapshot(fetch: Fetch = http_get_json, n: int = N_CITING, doi: str = ROOT_DOI) -> dict:
    root = fetch_root(doi, fetch)
    return {"source": "OpenAlex", "fetched": date.today().isoformat(), "root": root, "citing": fetch_citing(root["id"], fetch, n)}


def _label(w: dict) -> str:
    who = (w["first_author"] or "").split()[-1] if w["first_author"] else "?"
    return f"{who.lower()}-{w['year']}-{w['id'][-4:].lower()}"


def load_snapshot(path: Path = SNAPSHOT, retracted: bool = True) -> ResearchGraph:
    return graph_from_data(json.loads(path.read_text(encoding="utf-8")), retracted)


def graph_from_data(data: dict, retracted: bool = True) -> ResearchGraph:
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
    lesne = (root["doi"] or "").lower() == ROOT_DOI
    papers.append(_team_paper() if lesne else _generic_team_paper(root, citing))
    deps.extend(_team_dependencies(ids[root["id"]], citing, ids, lesne))
    evidence = "Retracted by Nature (2024)" if lesne else "Retracted (OpenAlex)"
    challenges = (
        (Challenge(ids[root["id"]], 0.0, evidence, source=f"https://doi.org/{root['doi']}"),)
        if retracted and root["is_retracted"] else ()
    )
    return ResearchGraph.build(papers, _acyclic(deps), challenges)


def _team_paper() -> Paper:
    return Paper(TEAM_NODE, "Example startup: oligomer-targeting Alzheimer's program", (
        Claim("h1", "Aβ*56 is the oligomer species driving memory loss"),
        Claim("h2", "Lowering soluble Aβ oligomers improves synaptic function"),
    ))


def _cite(w: dict) -> str:
    who = (w["first_author"] or "Unknown").split()[-1]
    return f"{who} {w['year']}"


def _generic_team_paper(root: dict, citing: list[dict]) -> Paper:
    alt = _alternative(citing, lesne=False)
    claims = [Claim("h1", f"Builds directly on the main finding of {_cite(root)}")]
    if alt:
        claims.append(Claim("h2", f"Uses the result of {_cite(alt)}"))
    return Paper(TEAM_NODE, "Your research program", tuple(claims))


def _alternative(citing: list[dict], lesne: bool) -> dict | None:
    if lesne:
        # h2 rests on the brain-derived dimer work, which cites the retracted paper only as background
        return next((w for w in citing if w["doi"] and w["doi"].lower() == ALT_DOI), None)
    return next((w for w in citing if not w["is_retracted"]), None)


def _team_dependencies(root_label: str, citing: list[dict], ids: dict[str, str], lesne: bool = True) -> list[Dependency]:
    deps = [Dependency(TEAM_NODE, root_label, K.METHODOLOGY, dependent_claims=frozenset({"h1"}))]
    alt = _alternative(citing, lesne)
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
