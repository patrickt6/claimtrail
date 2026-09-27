"""Structured assertions: the part of a claim a machine can check.

A claim's text is prose. "The approval gap is 4.2 points across 36,734,685
applications" can drift from the data it came from without any hash
changing. An assertion states the same fact as a check against a field of
the linked computation's payload:

    outputs.approval_gap ~= 4.2 +- 0.05
    outputs.rows == 36734685
    result.coefficients[10] >= 1
    outputs.auc in [0.80, 0.82]

A claim with assertions is checked when it is recorded (it is refused if
an assertion is false) and again on every ``claimtrail check`` and
``claimtrail lint``, so a changed payload or a re-linked computation fails
loudly instead of leaving the prose quietly wrong.
"""
from __future__ import annotations

import dataclasses
import json
import math
import re
from typing import Any, Iterable

_OPS = ("==", "!=", "<=", ">=", "<", ">", "~=", "in")
_PATH_TOKEN_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
_EXPR_RE = re.compile(
    r"^\s*(?P<path>[A-Za-z_][\w.\[\]]*)\s*"
    r"(?P<op>==|!=|<=|>=|~=|<|>|\bin\b)\s*"
    r"(?P<value>.+?)\s*$"
)
_PLUS_MINUS_RE = re.compile(r"\s*(?:\+-|\+/-|±)\s*")


class ClaimAssertionError(ValueError):
    """Raised when a claim is recorded with an assertion its computation
    does not satisfy."""


@dataclasses.dataclass(frozen=True)
class Expectation:
    """``path op value``, with ``tol`` for the ``~=`` operator."""

    path: str
    op: str
    value: Any
    tol: float | None = None

    def __post_init__(self) -> None:
        if self.op not in _OPS:
            raise ValueError(f"unknown operator {self.op!r}; use one of {_OPS}")
        if self.op == "~=" and self.tol is None:
            raise ValueError("~= needs a tolerance, e.g. 'x ~= 4.2 +- 0.05'")
        if self.op == "in" and not (
            isinstance(self.value, (list, tuple)) and len(self.value) == 2
        ):
            raise ValueError("'in' needs a [low, high] pair")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"path": self.path, "op": self.op, "value": self.value}
        if self.tol is not None:
            d["tol"] = self.tol
        return d

    def __str__(self) -> str:
        if self.op == "~=":
            return f"{self.path} ~= {self.value} +- {self.tol}"
        return f"{self.path} {self.op} {json.dumps(self.value)}"


@dataclasses.dataclass(frozen=True)
class ExpectationResult:
    expectation: Expectation
    ok: bool
    actual: Any
    message: str


def parse_expectation(spec: str | dict | Expectation) -> Expectation:
    """Build an :class:`Expectation` from a string, a dict, or itself."""
    if isinstance(spec, Expectation):
        return spec
    if isinstance(spec, dict):
        return Expectation(
            path=spec["path"], op=spec["op"], value=spec["value"], tol=spec.get("tol")
        )
    m = _EXPR_RE.match(spec)
    if m is None:
        raise ValueError(
            f"cannot parse assertion {spec!r}; expected 'PATH OP VALUE', "
            f"for example 'outputs.rows == 36734685'"
        )
    path, op, raw = m.group("path"), m.group("op"), m.group("value")
    tol: float | None = None
    if op == "~=":
        parts = _PLUS_MINUS_RE.split(raw, maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"~= needs a tolerance in {spec!r}, e.g. '~= 4.2 +- 0.05'")
        raw, tol = parts[0], float(parts[1])
    return Expectation(path=path, op=op, value=_parse_value(raw), tol=tol)


def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip().strip("'\"")


def resolve_path(payload: Any, path: str) -> Any:
    """Follow ``outputs.table[2].rate`` through nested dicts and lists."""
    node = payload
    for name, index in _PATH_TOKEN_RE.findall(path):
        if index:
            if not isinstance(node, (list, tuple)) or int(index) >= len(node):
                raise KeyError(f"{path}: no element [{index}]")
            node = node[int(index)]
        else:
            if not isinstance(node, dict) or name not in node:
                raise KeyError(f"{path}: no field {name!r}")
            node = node[name]
    return node


def evaluate(expectation: Expectation, payload: Any) -> ExpectationResult:
    """Check one expectation against a computation payload."""
    exp = expectation
    try:
        actual = resolve_path(payload, exp.path)
    except KeyError as exc:
        return ExpectationResult(exp, False, None, f"path not found: {exc.args[0]}")
    try:
        ok = _compare(actual, exp)
    except TypeError as exc:
        return ExpectationResult(exp, False, actual, f"cannot compare: {exc}")
    verdict = "holds" if ok else "fails"
    return ExpectationResult(exp, ok, actual, f"{exp} {verdict} (actual: {actual!r})")


def _compare(actual: Any, exp: Expectation) -> bool:
    op, expected = exp.op, exp.value
    if op == "==":
        return actual == expected
    if op == "!=":
        return actual != expected
    if op == "~=":
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            raise TypeError(f"~= needs a number, got {type(actual).__name__}")
        return math.isfinite(actual) and abs(actual - float(expected)) <= float(exp.tol)
    if op == "in":
        low, high = expected
        return low <= actual <= high
    return {"<": actual < expected, "<=": actual <= expected,
            ">": actual > expected, ">=": actual >= expected}[op]


def evaluate_all(
    expectations: Iterable[Expectation], payload: Any
) -> list[ExpectationResult]:
    return [evaluate(e, payload) for e in expectations]


def dumps(expectations: Iterable[Expectation]) -> str | None:
    items = [e.to_dict() for e in expectations]
    return json.dumps(items, sort_keys=True) if items else None


def loads(text: str | None) -> list[Expectation]:
    if not text:
        return []
    return [parse_expectation(d) for d in json.loads(text)]


__all__ = [
    "ClaimAssertionError",
    "Expectation",
    "ExpectationResult",
    "parse_expectation",
    "resolve_path",
    "evaluate",
    "evaluate_all",
]
