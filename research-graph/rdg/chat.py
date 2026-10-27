"""Researcher chat grounded in the current graph, via the Claude API.

The model only sees what the engine computed (scores, paths, Lean results), and is
told to frame everything as guidance. The API key stays server-side.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MAX_TOKENS = 4000
MAX_TURNS = 40
MAX_CHARS = 4000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PREAMBLE = """You help researchers at a small biotech team understand how changes in the \
literature affect their own work. You are looking at a research dependency graph: papers, \
the claims inside them, typed dependencies (methodology, subject, background citation), \
challenges such as retractions, and propagated validity scores.

How to answer:
- Ground every statement in the graph data below. Cite claims as paper#claim. If the data \
doesn't say, say so rather than guessing.
- Scores are guidance, not verdicts. The researcher decides whether to keep building on \
something; never tell them to stop.
- "leanStands": true means Lean checked that the claim follows from the remaining premises \
even assuming the challenged findings are false. It proves the dependency model, not the \
biology. Say that when it matters.
- An "unmapped" dependency means we don't know which part of the source is relied on. Ask \
the researcher which part they use.
- Be brief and concrete. Plain prose, short lists when useful.

Graph data (JSON):
"""


class ChatError(Exception):
    """Raised with a message that is safe to show the user."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ChatRequest:
    scenario: str
    state: str
    selected: str | None
    messages: tuple[dict[str, str], ...]


class MessagesClient(Protocol):
    def create(self, **kwargs: Any) -> Any: ...


def parse_request(body: Any, scenarios: Mapping[str, Any]) -> ChatRequest:
    if not isinstance(body, dict):
        raise ChatError("request body must be a JSON object")
    scenario, state = body.get("scenario"), body.get("state")
    if scenario not in scenarios:
        raise ChatError("unknown scenario")
    if state not in ("before", "after"):
        raise ChatError("state must be 'before' or 'after'")
    selected = body.get("selected")
    if selected is not None and not isinstance(selected, str):
        raise ChatError("selected must be a string")
    raw = body.get("messages")
    if not isinstance(raw, list) or not raw or len(raw) > MAX_TURNS:
        raise ChatError(f"messages must be a list of 1-{MAX_TURNS} turns")
    messages = []
    for i, m in enumerate(raw):
        if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
            raise ChatError(f"message {i} has an invalid role")
        content = m.get("content")
        if not isinstance(content, str) or not content.strip() or len(content) > MAX_CHARS:
            raise ChatError(f"message {i} must be 1-{MAX_CHARS} characters")
        messages.append({"role": m["role"], "content": content})
    if messages[0]["role"] != "user" or messages[-1]["role"] != "user":
        raise ChatError("conversation must start and end with a user message")
    return ChatRequest(str(scenario), str(state), selected, tuple(messages))


def graph_context(scenario: dict[str, Any], state: str) -> str:
    snap = scenario[state]
    compact = {
        "dataset": scenario["label"],
        "source": scenario["source"],
        "state": state,
        "team_node": scenario["team"],
        "challenge": scenario["challenge"] if state == "after" else None,
        "papers": [
            {"id": p["id"], "title": p["title"], "claims": [
                {k: c[k] for k in ("id", "text", "validity", "status", "paths", "leanStands", "portionCheck")}
                for c in p["claims"]
            ]}
            for p in snap["papers"]
        ],
        "dependencies": [
            {k: e[k] for k in ("from", "to", "kind", "sourceClaims", "dependentClaims", "unmapped")}
            for e in snap["edges"]
        ],
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True)


def build_call(req: ChatRequest, scenarios: Mapping[str, Any]) -> dict[str, Any]:
    system = [{
        "type": "text",
        "text": SYSTEM_PREAMBLE + graph_context(scenarios[req.scenario], req.state),
        "cache_control": {"type": "ephemeral"},
    }]
    messages = [dict(m) for m in req.messages]
    if req.selected:
        last = messages[-1]
        messages[-1] = {**last, "content": f"(I'm looking at {req.selected}.)\n\n{last['content']}"}
    return {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": system,
        "messages": messages,
        "output_config": {"effort": "low"},
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
    }


def reply(req: ChatRequest, scenarios: Mapping[str, Any], client: MessagesClient | None = None) -> str:
    messages_api = client or anthropic.Anthropic().beta.messages
    try:
        response = messages_api.create(**build_call(req, scenarios))
    except anthropic.AuthenticationError as exc:
        raise ChatError("The Anthropic API key is missing or invalid on the server.", 503) from exc
    except anthropic.RateLimitError as exc:
        raise ChatError("Rate limited by the Claude API. Try again in a moment.", 429) from exc
    except anthropic.BadRequestError as exc:
        log.error("claude bad request: %s", exc)
        raise ChatError("The Claude API rejected the request.", 502) from exc
    except anthropic.APIStatusError as exc:
        log.error("claude api error %s: %s", exc.status_code, exc)
        raise ChatError("The Claude API returned an error. Try again.", 502) from exc
    except anthropic.APIConnectionError as exc:
        raise ChatError("Couldn't reach the Claude API.", 502) from exc
    except TypeError as exc:
        # the SDK raises TypeError when no credentials resolve at all
        if "authentication" not in str(exc):
            raise
        raise ChatError("No Anthropic API key on the server. Set ANTHROPIC_API_KEY and restart.", 503) from exc
    if response.stop_reason == "refusal":
        return "I can't help with that request."
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text").strip()
    return text or "I don't have an answer for that."
