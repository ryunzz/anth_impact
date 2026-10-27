# Research dependency graph (Layer 3)

A DAG of papers and their *claims*. When a finding is challenged (retraction, failed
replication, rebuttal), validity propagates downstream and anyone building on it gets a
heads-up. Nothing is blocked.

```
python -m venv .venv && ./.venv/bin/pip install pytest pytest-cov
./.venv/bin/pytest -q --cov=rdg          # 20 tests
./.venv/bin/python -m rdg.export_ui      # regenerate ui/data.js
python3 -m http.server 8765 --directory ui
```

| Module | What it does |
|---|---|
| `models.py` | `Paper` → `Claim`s; `Dependency` edges typed methodology / subject / citation; `Challenge` |
| `graph.py` | Immutable DAG, rejects cycles and unknown ids, topological order |
| `propagation.py` | Per-claim validity: `base × Π(1 − w·(1 − v_source))`, statuses, `explain()` paths back to the root |
| `alerts.py` | Subscriptions (a team's hypothesis is a node), heads-up messages, decisions that silence repeats until new evidence arrives |
| `importer.py` | Semantic Scholar references → edges (citation intents → kind) + OpenAlex retraction flags → challenges |
| `lean_export.py` | Lean 4 file proving which claims still stand without the challenged findings (see `LEAN.md`) |
| `ui/` | Static viewer: before/after toggle, alerts, claim detail, generated Lean |

**Partial invalidation.** Challenges target claims, not whole papers. If an edge says
*which* claim it relies on, only that claim matters. If it doesn't ("unmapped"), the
weakest claim is assumed and the user is asked to confirm which part they use.

**Edge weights** (`DEFAULT_KIND_WEIGHT`): methodology 1.0, subject 0.6, citation 0.3.
These are first guesses, not calibrated values.

**Known gaps**
- Claim extraction from full text isn't built. Imported papers get one `main` claim.
- The live Semantic Scholar path is unverified. During testing it returned 429 and then
  404. Set `S2_API_KEY` (free) for a reliable rate limit. OpenAlex responds fine.
- Real citation data can contain cycles (papers that cite each other's preprints). These
  are currently rejected with `CycleError`.
- The generated Lean has not been compiled yet.
