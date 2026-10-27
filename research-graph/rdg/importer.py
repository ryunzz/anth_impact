"""Pull citations from Semantic Scholar and retraction status from OpenAlex.

Semantic Scholar's references endpoint gives, per citation, its *intents*
(methodology / background / result) and the citing sentences ("contexts"), which
map onto our edge kinds. Claim-level mapping needs full-text claim extraction
(an LLM step, not done here), so imported papers get a single "main" claim and
edges are unmapped, which makes the alerts ask the user to check the portion.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from .models import Challenge, Claim, Dependency, DependencyKind, Paper

log = logging.getLogger(__name__)

S2_BASE = "https://api.semanticscholar.org/graph/v1"
OPENALEX_BASE = "https://api.openalex.org"
TIMEOUT_S = 20
OPENALEX_BATCH = 50  # max ids per OpenAlex filter query
MAX_RETRIES = 4
BACKOFF_S = 2.0

Fetch = Callable[[str], dict]

INTENT_TO_KIND = {
    "methodology": DependencyKind.METHODOLOGY,
    "result": DependencyKind.SUBJECT,
    "background": DependencyKind.CITATION,
}
KIND_RANK = {DependencyKind.CITATION: 0, DependencyKind.SUBJECT: 1, DependencyKind.METHODOLOGY: 2}


def http_get_json(url: str, retries: int = MAX_RETRIES) -> dict:
    """GET JSON; backs off on 429 (S2's shared unauthenticated pool is often saturated)."""
    headers = {"User-Agent": "rdg/0.1 (research-dependency-graph)"}
    key = os.environ.get("S2_API_KEY")
    if key and url.startswith(S2_BASE):
        headers["x-api-key"] = key
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == retries:
                raise
            time.sleep(BACKOFF_S * 2**attempt)
    raise RuntimeError("unreachable")


@dataclass(frozen=True)
class ImportResult:
    papers: tuple[Paper, ...]
    dependencies: tuple[Dependency, ...]
    challenges: tuple[Challenge, ...]
    skipped: tuple[str, ...]  # references we couldn't identify


def kind_from_intents(intents: list[str] | None) -> DependencyKind:
    kinds = [INTENT_TO_KIND[i] for i in (intents or []) if i in INTENT_TO_KIND]
    return max(kinds, key=KIND_RANK.__getitem__, default=DependencyKind.CITATION)


def _paper_id(entry: dict) -> str | None:
    doi = (entry.get("externalIds") or {}).get("DOI")
    return f"doi:{doi.lower()}" if doi else entry.get("paperId")


def _to_paper(entry: dict, pid: str) -> Paper:
    title = entry.get("title") or pid
    doi = (entry.get("externalIds") or {}).get("DOI")
    return Paper(id=pid, title=title, claims=(Claim("main", title),), doi=doi)


def import_references(doi: str, fetch: Fetch = http_get_json, limit: int = 100) -> ImportResult:
    """One hop: the paper at `doi` plus everything it cites."""
    quoted = urllib.parse.quote(f"DOI:{doi}", safe=":")
    try:
        root = fetch(f"{S2_BASE}/paper/{quoted}?fields=title,externalIds")
        refs = fetch(
            f"{S2_BASE}/paper/{quoted}/references"
            f"?fields=title,externalIds,intents,contexts,isInfluential&limit={limit}"
        )
    except urllib.error.HTTPError as exc:
        if exc.code not in (404, 429):
            raise
        log.warning("semantic scholar unavailable for %s (%s); using openalex", doi, exc.code)
        return _openalex_references(doi, fetch, limit)
    root_id = _paper_id(root) or f"doi:{doi.lower()}"
    papers = {root_id: _to_paper(root, root_id)}
    deps: list[Dependency] = []
    skipped: list[str] = []
    items = refs.get("data") or []  # S2 returns null when a publisher withholds references
    if not items:
        return _openalex_references(doi, fetch, limit)
    for item in items:
        cited = item.get("citedPaper") or {}
        pid = _paper_id(cited)
        if not pid or pid == root_id:
            skipped.append(cited.get("title") or "<unknown>")
            continue
        papers.setdefault(pid, _to_paper(cited, pid))
        contexts = item.get("contexts") or []
        deps.append(
            Dependency(
                dependent_paper=root_id,
                source_paper=pid,
                kind=kind_from_intents(item.get("intents")),
                context=contexts[0] if contexts else None,
            )
        )
    challenges = tuple(retraction_challenges(tuple(papers.values()), fetch))
    return ImportResult(tuple(papers.values()), tuple(_dedupe(deps)), challenges, tuple(skipped))


def _openalex_references(doi: str, fetch: Fetch, limit: int) -> ImportResult:
    """Paper and reference list from OpenAlex. No citation intents there, so every edge is background."""
    work = fetch(f"{OPENALEX_BASE}/works/doi:{urllib.parse.quote(doi, safe='/')}?select=title,referenced_works")
    root_id = f"doi:{doi.lower()}"
    title = work.get("title") or root_id
    ref_ids = [r.rsplit("/", 1)[-1] for r in (work.get("referenced_works") or [])][: min(limit, OPENALEX_BATCH)]
    papers = {root_id: Paper(root_id, title, (Claim("main", title),), doi=doi)}
    deps: list[Dependency] = []
    if ref_ids:
        query = urllib.parse.urlencode({
            "filter": "openalex:" + "|".join(ref_ids),
            "per-page": len(ref_ids),
            "select": "id,title,doi",
        })
        for w in fetch(f"{OPENALEX_BASE}/works?{query}").get("results") or []:
            ref_doi = (w.get("doi") or "").removeprefix("https://doi.org/")
            pid = f"doi:{ref_doi.lower()}" if ref_doi else w["id"].rsplit("/", 1)[-1]
            if pid == root_id:
                continue
            title = w.get("title") or pid
            papers.setdefault(pid, Paper(pid, title, (Claim("main", title),), doi=ref_doi or None))
            deps.append(Dependency(root_id, pid, DependencyKind.CITATION))
    challenges = tuple(retraction_challenges(tuple(papers.values()), fetch))
    return ImportResult(tuple(papers.values()), tuple(_dedupe(deps)), challenges, ())


def _dedupe(deps: list[Dependency]) -> list[Dependency]:
    best: dict[tuple[str, str], Dependency] = {}
    for d in deps:
        k = (d.dependent_paper, d.source_paper)
        if k not in best or KIND_RANK[d.kind] > KIND_RANK[best[k].kind]:
            best[k] = d
    return list(best.values())


def retraction_challenges(papers: tuple[Paper, ...], fetch: Fetch = http_get_json):
    """Yield a full-strength Challenge for each paper OpenAlex marks as retracted."""
    for p in papers:
        if not p.doi:
            continue
        try:
            work = fetch(f"{OPENALEX_BASE}/works/doi:{urllib.parse.quote(p.doi, safe='/')}")
        except (OSError, ValueError) as exc:  # network, http error, bad json: record and keep importing
            log.warning("openalex lookup failed for %s: %s", p.doi, exc)
            continue
        if work.get("is_retracted"):
            yield Challenge(p.id, validity=0.0, evidence="retracted (OpenAlex)", source=work.get("id"))
