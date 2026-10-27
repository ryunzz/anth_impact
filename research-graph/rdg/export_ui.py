"""Serialize engine output for the static UI: `python -m rdg.export_ui` writes ui/data.js."""
from __future__ import annotations

import json
from pathlib import Path

from . import snapshot as lesne
from .alerts import Subscription, notifications
from .demo import TEAM_NODE, demo_graph
from .graph import ResearchGraph
from .lean_export import derivable, export
from .propagation import assess, explain

OUT = Path(__file__).resolve().parent.parent / "ui" / "data.js"


def _key(k: tuple[str, str]) -> str:
    return f"{k[0]}#{k[1]}"


def snapshot(graph: ResearchGraph, team: str = TEAM_NODE) -> dict:
    results = assess(graph)
    lean_ok = derivable(graph)
    notes = notifications(results, (Subscription("team", team),))
    return {
        "papers": [
            {
                "id": p.id,
                "title": p.title,
                "doi": p.doi,
                "claims": [
                    {
                        "id": c.id,
                        "text": c.text,
                        "validity": round(results[(p.id, c.id)].validity, 4),
                        "status": results[(p.id, c.id)].status.value,
                        "portionCheck": results[(p.id, c.id)].needs_portion_check,
                        "roots": sorted(_key(r) for r in results[(p.id, c.id)].roots),
                        "paths": [[_key(k) for k in path] for path in explain(results, (p.id, c.id))],
                        "leanStands": lean_ok[(p.id, c.id)],
                    }
                    for c in p.claims
                ],
            }
            for p in graph.papers.values()
        ],
        "edges": [
            {
                "from": d.source_paper,
                "to": d.dependent_paper,
                "kind": d.kind.value,
                "sourceClaims": sorted(d.source_claims),
                "dependentClaims": sorted(d.dependent_claims),
                "unmapped": d.is_unmapped,
                "context": d.context,
            }
            for d in graph.dependencies
        ],
        "notifications": [
            {"key": _key(n.key), "status": n.status.value, "validity": round(n.validity, 4), "message": n.message}
            for n in notes
        ],
        "lean": export(graph),
    }


def _challenge(graph: ResearchGraph) -> dict:
    ch = graph.challenges[0]
    by = next((p.id for p in graph.papers.values() if p.doi and p.doi == ch.source), None)
    claims = sorted(ch.claim_ids or graph.papers[ch.paper_id].claim_ids)
    return {"paper": ch.paper_id, "claims": claims, "evidence": ch.evidence, "validity": ch.validity, "by": by}


def antibody_scenario() -> dict:
    return {
        "label": "Antibody specificity",
        "source": "Worked example",
        "team": TEAM_NODE,
        "challenge": _challenge(demo_graph()),
        "before": snapshot(demo_graph(challenged=False)),
        "after": snapshot(demo_graph(challenged=True)),
    }


def lesne_scenario() -> dict:
    meta = json.loads(lesne.SNAPSHOT.read_text(encoding="utf-8"))
    after = lesne.load_snapshot()
    return {
        "label": "Lesné 2006 (Aβ*56)",
        "source": f"{meta['source']} · {meta['fetched']}",
        "rootDoi": lesne.ROOT_DOI,
        "team": lesne.TEAM_NODE,
        "challenge": _challenge(after),
        "before": snapshot(lesne.load_snapshot(retracted=False), lesne.TEAM_NODE),
        "after": snapshot(after, lesne.TEAM_NODE),
    }


def main() -> None:
    data = {"scenarios": {"lesne": lesne_scenario(), "antibody": antibody_scenario()}}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("window.RDG_DATA = " + json.dumps(data, ensure_ascii=False, indent=1) + ";\n", encoding="utf-8")


if __name__ == "__main__":
    main()
