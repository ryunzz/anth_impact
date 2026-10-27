"""Local server: serves ui/ and POST /api/chat. `python -m rdg.server [port]`.

Needs ANTHROPIC_API_KEY (or an `ant auth login` profile) in the environment.
Binds to 127.0.0.1 only.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Mapping
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Any, ClassVar

from .chat import ChatError, parse_request, reply
from .export_ui import OUT, antibody_scenario, lesne_scenario

log = logging.getLogger(__name__)

UI_DIR = OUT.parent
ENV_FILE = UI_DIR.parent / ".env"  # gitignored; holds ANTHROPIC_API_KEY
MAX_BODY = 200_000
DEFAULT_PORT = 8766


def load_scenarios() -> dict[str, Any]:
    return {"lesne": lesne_scenario(), "antibody": antibody_scenario()}


class Handler(SimpleHTTPRequestHandler):
    scenarios: ClassVar[Mapping[str, Any]] = MappingProxyType({})

    def do_POST(self) -> None:
        if self.path != "/api/chat":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_BODY:
                raise ChatError("request body too large or empty", 413)
            body = json.loads(self.rfile.read(length))
            req = parse_request(body, self.scenarios)
            self._json(200, {"reply": reply(req, self.scenarios)})
        except ChatError as exc:
            self._json(exc.status, {"error": str(exc)})
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "invalid JSON"})
        except Exception:  # always answer the browser; details go to the log
            log.exception("chat request failed")
            self._json(500, {"error": "Server error. Check the server log."})

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


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
    Handler.scenarios = MappingProxyType(load_scenarios())
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(UI_DIR)))
    log.info("serving %s on http://127.0.0.1:%d", UI_DIR, port)
    server.serve_forever()


if __name__ == "__main__":
    main()
