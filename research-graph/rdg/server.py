"""Local server: serves ui/ plus the JSON API. `python -m rdg.server [port]`.

  POST /api/chat     researcher chat grounded in a scenario (Claude API)
  POST /api/analyze  dropped paper -> graph, streamed as NDJSON progress events
  POST /api/verify   run Lean on a scenario's proofs

Needs ANTHROPIC_API_KEY (here or in the gitignored .env) for chat. Binds to 127.0.0.1 only.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable, Mapping
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from . import snapshot
from .analyze import AnalyzeError, analyze, resolve
from .chat import ChatError, parse_request, reply
from .demo import demo_graph
from .export_ui import OUT, antibody_scenario, lesne_scenario
from .graph import ResearchGraph
from .verify import verify

log = logging.getLogger(__name__)

UI_DIR = OUT.parent
ENV_FILE = UI_DIR.parent / ".env"  # gitignored; holds ANTHROPIC_API_KEY
MAX_BODY = 200_000
MAX_UPLOAD = 30_000_000
DEFAULT_PORT = 8766


class Registry:
    """Scenarios the viewer can show, plus how to rebuild each one's graph for Lean."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._scenarios: dict[str, Any] = {}
        self._graphs: dict[str, Callable[[], ResearchGraph]] = {}

    def add(self, sid: str, scenario: dict[str, Any], graph: Callable[[], ResearchGraph]) -> None:
        with self._lock:
            self._scenarios = {**self._scenarios, sid: scenario}
            self._graphs = {**self._graphs, sid: graph}

    @property
    def scenarios(self) -> Mapping[str, Any]:
        return MappingProxyType(self._scenarios)

    def graph(self, sid: str) -> ResearchGraph | None:
        build = self._graphs.get(sid)
        return build() if build else None


def load_scenarios() -> dict[str, Any]:
    return {"lesne": lesne_scenario(), "antibody": antibody_scenario()}


def default_registry() -> Registry:
    reg = Registry()
    reg.add("lesne", lesne_scenario(), snapshot.load_snapshot)
    reg.add("antibody", antibody_scenario(), demo_graph)
    return reg


def scenario_id(doi: str) -> str:
    return "doi-" + re.sub(r"[^a-z0-9]+", "-", doi.lower()).strip("-")


class Handler(SimpleHTTPRequestHandler):
    registry: ClassVar[Registry] = Registry()

    def do_POST(self) -> None:
        routes = {"/api/chat": self._chat, "/api/analyze": self._analyze, "/api/verify": self._verify}
        route = routes.get(self.path)
        if not route:
            self._json(404, {"error": "not found"})
            return
        try:
            route()
        except (ChatError, AnalyzeError) as exc:
            self._json(getattr(exc, "status", 400), {"error": str(exc)})
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "invalid request"})
        except Exception:  # always answer the browser; details go to the log
            log.exception("%s failed", self.path)
            self._json(500, {"error": "Server error. Check the server log."})

    # ---- routes ----------------------------------------------------------

    def _chat(self) -> None:
        scenarios = self.registry.scenarios
        req = parse_request(self._read_json(), scenarios)
        self._json(200, {"reply": reply(req, scenarios)})

    def _verify(self) -> None:
        sid = self._read_json().get("scenario")
        graph = self.registry.graph(sid) if isinstance(sid, str) else None
        if graph is None:
            raise ChatError("unknown scenario", 404)
        self._json(200, verify(graph))

    def _analyze(self) -> None:
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip()
        filename = self.headers.get("X-Filename")
        if ctype == "application/pdf":
            source = resolve(pdf=self._read_body(MAX_UPLOAD), filename=filename)
        else:
            body = self._read_json()
            text = body.get("input")
            if not isinstance(text, str) or not text.strip():
                raise AnalyzeError("Drop a PDF or citation file, or paste a DOI or link.")
            source = resolve(text=text[:MAX_BODY], filename=body.get("filename"))
        self._stream(self._analysis_events(source))

    def _analysis_events(self, source: Any) -> Any:
        try:
            for event in analyze(source):
                if event["type"] == "result":
                    sid = scenario_id(source.doi)
                    scenario, data = event["scenario"], event["data"]
                    self.registry.add(sid, scenario, partial(snapshot.graph_from_data, data))
                    yield {"type": "result", "id": sid, "scenario": scenario}
                else:
                    yield event
        except AnalyzeError as exc:
            yield {"type": "error", "message": str(exc)}

    # ---- io --------------------------------------------------------------

    def _read_body(self, limit: int) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= limit:
            raise ChatError("request body too large or empty", 413)
        return self.rfile.read(length)

    def _read_json(self) -> dict[str, Any]:
        body = json.loads(self._read_body(MAX_BODY))
        if not isinstance(body, dict):
            raise ChatError("request body must be a JSON object")
        return body

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, events: Any) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        for event in events:
            self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines from a local .env; blank lines and # comments are skipped."""
    if not path.is_file():
        return {}
    pairs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key.strip()] = value.strip().strip("'\"")
    return pairs


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    for key, value in read_env_file(ENV_FILE).items():
        os.environ.setdefault(key, value)  # a real environment variable wins
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    Handler.registry = default_registry()
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(UI_DIR)))
    log.info("serving %s on http://127.0.0.1:%d", UI_DIR, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
