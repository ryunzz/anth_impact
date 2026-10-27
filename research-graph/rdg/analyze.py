"""Turn a dropped paper (PDF, citation file, DOI or link) into a graph, reporting each step.

`analyze()` yields progress events as each real step finishes, then a final result.
If OpenAlex can't be reached and the paper is the one we keep a snapshot of, the
saved snapshot is used and the step says so.
"""
from __future__ import annotations

import io
import json
import logging
import re
import urllib.error
import urllib.parse
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

from . import snapshot
from .export_ui import scenario_from_data
from .importer import Fetch, http_get_json

log = logging.getLogger(__name__)

DOI_PATTERN = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>{}]+)", re.IGNORECASE)
TRAILING = ".,;:)]}"
PDF_PAGES_SCANNED = 2


class AnalyzeError(Exception):
    """Raised with a message that is safe to show the user."""


@dataclass(frozen=True)
class Source:
    doi: str
    found_in: str  # human description of where the DOI came from


def doi_from_text(text: str) -> str | None:
    text = urllib.parse.unquote(text)
    match = DOI_PATTERN.search(text)
    return match.group(1).rstrip(TRAILING).lower() if match else None


def doi_from_pdf(data: bytes) -> str | None:
    from pypdf import PdfReader  # imported lazily; only PDF drops need it
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(io.BytesIO(data))
        meta: dict[str, Any] = dict(reader.metadata or {})
        for key in ("/doi", "/DOI", "/Subject", "/Keywords"):
            found = doi_from_text(str(meta.get(key) or ""))
            if found:
                return found
        for page in reader.pages[:PDF_PAGES_SCANNED]:
            found = doi_from_text(page.extract_text() or "")
            if found:
                return found
    except (PdfReadError, ValueError, KeyError) as exc:
        log.warning("pdf parse failed: %s", exc)
    return doi_from_text(data[:200_000].decode("latin-1"))


def resolve(text: str | None = None, pdf: bytes | None = None, filename: str | None = None) -> Source:
    if pdf is not None:
        doi = doi_from_pdf(pdf)
        where = f"PDF {filename}" if filename else "the PDF"
    else:
        doi = doi_from_text(text or "")
        where = f"file {filename}" if filename else "the link"
    if not doi:
        raise AnalyzeError("Couldn't find a DOI in that. Try the paper's DOI or doi.org link.")
    return Source(doi, where)


def _step(step_id: str, label: str, detail: str = "", status: str = "done") -> dict[str, Any]:
    return {"type": "step", "id": step_id, "label": label, "detail": detail, "status": status}


def _fetch_data(doi: str, fetch: Fetch) -> Iterator[dict[str, Any]]:
    """Yields step events; the last event is {"type": "data", ...}."""
    yield _step("paper", "Fetching paper metadata", "OpenAlex", "running")
    try:
        root = snapshot.fetch_root(doi, fetch)
        yield _step("paper", "Fetched paper metadata", f"{root['title']} ({root['year']}) · cited {root['cited_by']:,} times")
        yield _step("citing", "Finding work that builds on it", "most-cited citing papers", "running")
        citing = snapshot.fetch_citing(root["id"], fetch)
        yield _step("citing", "Found work that builds on it", f"{len(citing)} papers, with their reference lists")
        data = {"source": "OpenAlex", "fetched": date.today().isoformat(), "root": root, "citing": citing}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        if doi != snapshot.ROOT_DOI or not snapshot.SNAPSHOT.is_file():
            raise AnalyzeError(f"Couldn't reach OpenAlex ({exc}). Check the connection and retry.") from exc
        data = json.loads(snapshot.SNAPSHOT.read_text(encoding="utf-8"))
        yield _step("paper", "OpenAlex unreachable", f"using saved OpenAlex snapshot from {data['fetched']}", "warn")
    yield {"type": "data", "data": data}


def analyze(source: Source, fetch: Fetch = http_get_json) -> Iterator[dict[str, Any]]:
    yield _step("resolve", "Identified paper", f"DOI {source.doi} from {source.found_in}")
    data: dict[str, Any] | None = None
    for event in _fetch_data(source.doi, fetch):
        if event["type"] == "data":
            data = event["data"]
        else:
            yield event
    if data is None:
        raise AnalyzeError("No data returned.")
    root = data["root"]
    retracted = [w for w in [root, *data["citing"]] if w["is_retracted"]]
    yield _step(
        "retractions", "Checked retraction status",
        ("retracted: " + "; ".join(w["title"][:70] for w in retracted)) if retracted else "no retraction flags found",
        "warn" if retracted else "done",
    )
    scenario = scenario_from_data(data)
    n_claims = sum(len(p["claims"]) for p in scenario["after"]["papers"])
    n_edges = len(scenario["after"]["edges"])
    yield _step("graph", "Built dependency graph", f"{len(scenario['after']['papers'])} papers · {n_edges} dependencies · {n_claims} claims")
    flagged = len(scenario["after"]["notifications"])
    yield _step("score", "Scored validity", f"{flagged} heads-up for your program" if flagged else "nothing your program relies on is challenged")
    yield {"type": "result", "scenario": scenario, "data": data}
