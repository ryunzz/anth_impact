"""Turn assessments into heads-up notifications. Advisory only: nothing is ever blocked.

A team's active hypothesis/experiment is just another Paper node in the graph that
depends on the literature; the team subscribes to it (or to any paper they use).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .propagation import Assessment, ClaimKey, Status


class Choice(str, Enum):
    CONTINUE = "continue"  # keep using it, aware of the risk
    VERIFIED_UNAFFECTED = "verified_unaffected"  # checked: our part isn't the contested part
    STOP_USING = "stop_using"


@dataclass(frozen=True)
class Subscription:
    user: str
    paper_id: str
    claim_ids: frozenset[str] = frozenset()  # empty = all claims of the paper


@dataclass(frozen=True)
class Decision:
    """The user's call on a flagged claim, recorded at the validity they saw."""

    user: str
    key: ClaimKey
    choice: Choice
    at_validity: float
    roots: frozenset[ClaimKey]


@dataclass(frozen=True)
class Notification:
    user: str
    key: ClaimKey
    status: Status
    validity: float
    roots: frozenset[ClaimKey]
    needs_portion_check: bool
    message: str


# A previously dismissed alert re-fires only if validity drops at least this much more.
REFIRE_DROP = 0.1


def notifications(
    results: dict[ClaimKey, Assessment],
    subscriptions: tuple[Subscription, ...],
    decisions: tuple[Decision, ...] = (),
) -> tuple[Notification, ...]:
    out: list[Notification] = []
    for sub in subscriptions:
        for key, a in sorted(results.items()):
            if key[0] != sub.paper_id or (sub.claim_ids and key[1] not in sub.claim_ids):
                continue
            if a.status is Status.OK or _already_decided(sub.user, a, decisions):
                continue
            out.append(
                Notification(
                    user=sub.user,
                    key=key,
                    status=a.status,
                    validity=a.validity,
                    roots=a.roots,
                    needs_portion_check=a.needs_portion_check,
                    message=_message(a),
                )
            )
    return tuple(out)


def _already_decided(user: str, a: Assessment, decisions: tuple[Decision, ...]) -> bool:
    for d in decisions:
        if d.user != user or d.key != a.key:
            continue
        new_roots = a.roots - d.roots
        dropped_further = a.validity < d.at_validity - REFIRE_DROP
        if not new_roots and not dropped_further:
            return True
    return False


def _fmt(key: ClaimKey) -> str:
    return f"{key[0]}#{key[1]}"


def _message(a: Assessment) -> str:
    roots = ", ".join(_fmt(r) for r in sorted(a.roots)) or "an upstream source"
    lines = [
        f"Heads-up on {_fmt(a.key)}: estimated validity {a.validity:.0%} ({a.status.value}).",
        f"Traced to challenged finding(s): {roots}.",
    ]
    if a.needs_portion_check:
        lines.append(
            "We don't know which part of the source you rely on. Please check whether "
            "it is the contested portion."
        )
    lines.append("This is guidance, not a verdict. You decide whether to keep building on it.")
    return " ".join(lines)
