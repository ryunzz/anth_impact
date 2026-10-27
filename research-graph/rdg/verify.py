"""Run Lean on a graph's generated proofs and report, per theorem, which axioms it used.

`#print axioms` is the evidence: a surviving claim's theorem never lists the
challenged claim, because no derivation of it goes through that claim.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from .graph import ResearchGraph
from .lean_export import challenged, derivable, export, lean_name

NAMESPACE = "ResearchGraph"
TIMEOUT_S = 90
AXIOM_LINE = re.compile(r"'([^']+)' depends on axioms: \[([^\]]*)\]", re.DOTALL)


def lean_binary() -> str | None:
    found = shutil.which("lean")
    fallback = Path.home() / ".elan" / "bin" / "lean"
    return found or (str(fallback) if fallback.is_file() else None)


def _source_with_audit(graph: ResearchGraph) -> tuple[str, dict[str, tuple[str, str]]]:
    ok = derivable(graph)
    keys = {lean_name(k): k for k in ok}
    audit = [f"#print axioms {NAMESPACE}.{lean_name(k)}_stands" for k, v in ok.items() if v]
    return export(graph, NAMESPACE) + "\n".join(audit) + "\n", keys


def _axiom_to_key(name: str, keys: dict[str, tuple[str, str]]) -> dict[str, str] | None:
    short = name.strip().removeprefix(f"{NAMESPACE}.")
    for prefix, role in (("ax_", "established"), ("dep_", "dependency")):
        if short.startswith(prefix) and short[len(prefix):] in keys:
            pid, cid = keys[short[len(prefix):]]
            return {"key": f"{pid}#{cid}", "role": role}
    return None


def verify(graph: ResearchGraph) -> dict[str, Any]:
    lean = lean_binary()
    ok = derivable(graph)
    bad = sorted(f"{p}#{c}" for p, c in challenged(graph))
    refuted = [f"{p}#{c}" for (p, c), v in ok.items() if not v and (p, c) not in challenged(graph)]
    base: dict[str, Any] = {"challenged": bad, "noDerivation": refuted}
    if not lean:
        return {**base, "ok": False, "error": "Lean isn't installed on the server.", "theorems": []}
    source, keys = _source_with_audit(graph)
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "ResearchGraph.lean")
        Path(path).write_text(source, encoding="utf-8")
        started = time.monotonic()
        try:
            proc = subprocess.run([lean, path], capture_output=True, text=True, timeout=TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            return {**base, "ok": False, "error": f"Lean timed out after {TIMEOUT_S}s.", "theorems": []}
        elapsed = time.monotonic() - started
        version = subprocess.run([lean, "--version"], capture_output=True, text=True, timeout=30, check=False).stdout.strip()
    theorems = []
    for name, axioms in AXIOM_LINE.findall(proc.stdout):
        claim = name.removeprefix(f"{NAMESPACE}.").removesuffix("_stands")
        pid, cid = keys.get(claim, ("?", "?"))
        used = [a for a in (_axiom_to_key(x, keys) for x in axioms.split(",")) if a]
        theorems.append({"theorem": name, "claim": f"{pid}#{cid}", "uses": used})
    errors = [line for line in (proc.stdout + proc.stderr).splitlines() if "error" in line]
    return {
        **base,
        "ok": proc.returncode == 0,
        "exitCode": proc.returncode,
        "seconds": round(elapsed, 2),
        "version": version,
        "theorems": theorems,
        "errors": errors[:10],
        "source": source,
    }
