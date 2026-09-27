"""Audit a Markdown or HTML report against the claimtrail store.

:mod:`claimtrail.audit_paper` checks LaTeX manuscripts. This module checks
the documents that businesses actually circulate: a Markdown summary, a
generated HTML report, a model card, a status page. The question is the
same one: does every number in the document still agree with the record
of the computation that produced it?

A block of the report (a paragraph, a list item, a table row) is linked to
the store with an invisible marker:

    Approval rates differ by 4.2 points across 36,734,685 applications.
    <!-- ct:7f3a91c2 -->

In HTML, ``data-claim="7f3a91c2"`` on an element works the same way. The
id may be a claim id or a computation id, or a unique prefix of either.

Each linked block gets one status:

- ``MATCH``    every number in the block is supported by the linked record,
               within the precision the number was written with.
- ``DRIFT``    at least one number is not supported. The report or the
               record is wrong; a person decides which.
- ``FAIL``     the linked claim carries structured assertions and at least
               one of them is false against its computation's payload.
- ``MISSING``  the marker does not resolve to anything in the store.
- ``ORPHAN``   the marker resolves to a claim with no backing computation.

Each linked entry also shows ``basis`` (a ``basis`` tag on the claim or
computation, for example MEASURED or SAMPLE_BASED) and ``verified``, the
computation's standing in the verification log (independent, self-checked,
author-unknown, unverified or failed).

With ``strict=True``, a block that states numbers but carries no marker is
reported as ``UNBACKED``. That is the check to run in CI on a report that
is meant to be fully traced.
"""
from __future__ import annotations

import dataclasses
import html
import json
import re
from pathlib import Path
from typing import Literal, Optional

from . import assertions as _assertions
from . import ledger
from .audit_paper import _payload_numbers, _resolve_provid
from .quantities import Quantity, extract_quantities
from .store import Claim, Computation, Store

Status = Literal["MATCH", "DRIFT", "FAIL", "MISSING", "ORPHAN", "UNBACKED"]
STATUSES: tuple[Status, ...] = ("FAIL", "DRIFT", "MISSING", "ORPHAN", "UNBACKED", "MATCH")
DEFAULT_FAIL_ON: tuple[Status, ...] = ("FAIL", "DRIFT", "MISSING", "ORPHAN")

MARKER_RE = re.compile(r"<!--\s*(?:ct|claimtrail|provid)\s*:\s*([\w-]+)\s*-->")
_DATA_CLAIM_RE = re.compile(r"""<([a-zA-Z][\w-]*)([^>]*?)\sdata-claim\s*=\s*["']([\w-]+)["']([^>]*)>""")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_HTML_BLOCK_TAGS = (
    "p", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "div", "section",
    "article", "figcaption", "caption", "blockquote", "dd", "dt", "table",
    "thead", "tbody", "ul", "ol", "header", "footer", "main", "br",
)
_HTML_DROP_RE = re.compile(r"<(script|style|svg|code|pre)\b.*?</\1\s*>", re.S | re.I)
_HTML_BLOCK_RE = re.compile(
    r"</?(?:" + "|".join(_HTML_BLOCK_TAGS) + r")\b[^>]*>", re.I
)
_HTML_CELL_RE = re.compile(r"</?t[dh]\b[^>]*>", re.I)
_HTML_TAG_RE = re.compile(r"<(?!!--)[^>]+>")
_BLOCK_SEP = "\x1e"


@dataclasses.dataclass
class Block:
    text: str
    line: int


@dataclasses.dataclass
class ReportEntry:
    line: int
    text: str
    status: Status
    detail: str
    ids: list[str] = dataclasses.field(default_factory=list)
    basis: Optional[str] = None
    verified: Optional[str] = None
    quantities: list[Quantity] = dataclasses.field(default_factory=list)
    unsupported: list[Quantity] = dataclasses.field(default_factory=list)
    failed_assertions: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class ReportAudit:
    path: Path
    entries: list[ReportEntry]
    summary: dict[str, int]

    @classmethod
    def from_entries(cls, path: Path, entries: list[ReportEntry]) -> "ReportAudit":
        summary = {s: 0 for s in STATUSES}
        for e in entries:
            summary[e.status] += 1
        return cls(path=path, entries=entries, summary=summary)

    def failed(self, fail_on: tuple[str, ...] = DEFAULT_FAIL_ON) -> bool:
        return any(self.summary.get(s, 0) for s in fail_on)


# ---------------------------------------------------------------------------
# Splitting a document into blocks
# ---------------------------------------------------------------------------


def split_markdown(text: str) -> list[Block]:
    """Paragraphs, list items and table rows, each as its own block.

    Fenced code blocks are skipped: numbers in code are not claims. A
    marker on its own line attaches to the block directly above it.
    """
    blocks: list[Block] = []
    buf: list[str] = []
    start = 1
    in_fence = False

    def flush() -> None:
        nonlocal buf
        if buf:
            blocks.append(Block("".join(buf).strip(), start))
            buf = []

    for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
        if _FENCE_RE.match(line):
            flush()
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if MARKER_RE.fullmatch(stripped) and not buf and blocks:
            # A marker alone on a line, after a blank line, still belongs
            # to the block above it.
            blocks[-1].text += " " + stripped
            continue
        starts_item = bool(_LIST_ITEM_RE.match(line)) or stripped.startswith("|")
        if starts_item or (buf and buf[-1].lstrip().startswith("|")):
            flush()
        if not buf:
            start = lineno
        buf.append(line)
    flush()
    return [b for b in blocks if not _is_table_rule(b.text)]


def _is_table_rule(text: str) -> bool:
    return bool(re.fullmatch(r"\|?[\s:|-]+\|?", text))


def split_html(text: str) -> list[Block]:
    """Turn HTML into text blocks, keeping claim markers.

    ``data-claim="ID"`` becomes a ``<!-- ct:ID -->`` marker inside the
    element, block-level tags become block breaks, table cells become
    `` | `` separators, and every other tag is dropped.
    """
    text = _DATA_CLAIM_RE.sub(
        lambda m: f"<{m.group(1)}{m.group(2)}{m.group(4)}><!-- ct:{m.group(3)} -->", text
    )
    text = _HTML_DROP_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    text = _HTML_CELL_RE.sub(" | ", text)
    # Block tags become a separator that is not a newline, so the line
    # numbers reported for each block stay true to the source file.
    text = _HTML_BLOCK_RE.sub(_BLOCK_SEP, text)
    text = _HTML_TAG_RE.sub("", text)
    blocks: list[Block] = []
    line = 1
    for chunk in re.split(r"(\x1e|\n[ \t]*\n)", text):
        if chunk == _BLOCK_SEP:
            continue
        lead = chunk[: len(chunk) - len(chunk.lstrip())].count("\n")
        clean = html.unescape(re.sub(r"\s+", " ", chunk).strip())
        clean = re.sub(r"^(?:\|\s*)+|(?:\s*\|)+$", "", clean).strip()
        if clean:
            blocks.append(Block(clean, line + lead))
        line += chunk.count("\n")
    return blocks


# ---------------------------------------------------------------------------
# Auditing
# ---------------------------------------------------------------------------


def audit_report(
    path: str | Path,
    db: Store,
    *,
    strict: bool = False,
    fmt: Literal["markdown", "html"] | None = None,
) -> ReportAudit:
    """Audit every linked block of a Markdown or HTML report.

    ``fmt`` defaults to the file extension (``.html``/``.htm`` read as
    HTML, anything else as Markdown).
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    if fmt is None:
        fmt = "html" if path.suffix.lower() in (".html", ".htm") else "markdown"
    blocks = split_html(text) if fmt == "html" else split_markdown(text)
    entries: list[ReportEntry] = []
    for block in blocks:
        entry = audit_block(block, db, strict=strict)
        if entry is not None:
            entries.append(entry)
    return ReportAudit.from_entries(path, entries)


def audit_block(block: Block, db: Store, *, strict: bool = False) -> Optional[ReportEntry]:
    ids = MARKER_RE.findall(block.text)
    prose = MARKER_RE.sub(" ", block.text)
    quantities = extract_quantities(prose)
    if not ids:
        if strict and quantities:
            return ReportEntry(
                line=block.line, text=prose.strip(), status="UNBACKED",
                detail=f"{len(quantities)} number(s) with no claimtrail marker",
                quantities=quantities, unsupported=list(quantities),
            )
        return None

    supported_values: list[float] = []
    basis: set[str] = set()
    standings: set[str] = set()
    problems: list[tuple[Status, str]] = []
    failed_assertions: list[str] = []
    for ident in ids:
        claim, comp = _resolve_provid(ident, db)
        if claim is None and comp is None:
            problems.append(("MISSING", f"{ident}: not found in the store"))
            continue
        if claim is not None and claim.tags.get("basis"):
            basis.add(claim.tags["basis"])
        if comp is None:
            reason = (
                "claim has no backing computation" if claim.computation_id is None
                else f"linked computation {claim.computation_id[:12]} is not in the store"
            )
            problems.append(("ORPHAN", f"{ident}: {reason}"))
            continue
        try:
            payload = db.read_payload(comp.id)
        except Exception as exc:  # missing file or tampered payload
            problems.append(("ORPHAN", f"{ident}: payload unreadable: {exc}"))
            continue
        if comp.tags.get("basis"):
            basis.add(comp.tags["basis"])
        standings.add(ledger.standing(comp, store=db).status)
        supported_values.extend(v for _, v in _payload_numbers(_payload_subset(payload)))
        if claim is not None:
            supported_values.extend(q.value for q in extract_quantities(claim.text))
            if claim.value_numeric is not None:
                supported_values.append(claim.value_numeric)
            for result in _assertions.evaluate_all(_assertions.loads(claim.assertions), payload):
                if not result.ok:
                    failed_assertions.append(f"{ident}: {result.message}")

    unsupported = [
        q for q in quantities if not any(q.matches(v) for v in supported_values)
    ]
    if failed_assertions:
        status: Status = "FAIL"
        detail = "; ".join(failed_assertions)
    elif problems:
        status = _worst(p[0] for p in problems)
        detail = "; ".join(p[1] for p in problems)
    elif unsupported:
        status = "DRIFT"
        detail = (
            f"{len(quantities) - len(unsupported)} of {len(quantities)} number(s) supported; "
            f"not supported: " + ", ".join(q.raw_text for q in unsupported[:8])
        )
    else:
        status = "MATCH"
        detail = f"{len(quantities)} number(s) supported by the linked record"
    return ReportEntry(
        line=block.line, text=prose.strip(), status=status, detail=detail, ids=ids,
        basis=", ".join(sorted(basis)) or None,
        verified=", ".join(sorted(standings)) or None, quantities=quantities,
        unsupported=unsupported, failed_assertions=failed_assertions,
    )


def _payload_subset(payload: dict) -> dict:
    return {k: payload[k] for k in ("inputs", "outputs", "args", "kwargs", "result") if k in payload}


def _worst(statuses) -> Status:
    order = {s: i for i, s in enumerate(STATUSES)}
    return min(statuses, key=lambda s: order[s])


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(report: ReportAudit, output_format: str = "text") -> str:
    if output_format == "text":
        return _render_text(report)
    if output_format == "json":
        return _render_json(report)
    if output_format == "markdown":
        return _render_markdown(report)
    raise ValueError(f"unknown output_format {output_format!r}")


def _summary_line(report: ReportAudit) -> str:
    return "  ".join(f"{s}={report.summary[s]}" for s in STATUSES if report.summary[s])


def _render_text(report: ReportAudit) -> str:
    lines = [f"claimtrail audit-report {report.path}", f"  {_summary_line(report) or 'nothing linked'}", ""]
    for e in report.entries:
        ids = ",".join(i[:12] for i in e.ids) or "-"
        basis = f"  basis={e.basis}" if e.basis else ""
        verified = f"  verified={e.verified}" if e.verified else ""
        lines.append(f"{e.status:<9} line {e.line:<5} {ids}{basis}{verified}")
        lines.append(f"          {_truncate(e.text, 110)}")
        if e.status != "MATCH":
            lines.append(f"          {e.detail}")
        lines.append("")
    return "\n".join(lines)


def _render_json(report: ReportAudit) -> str:
    def entry(e: ReportEntry) -> dict:
        return {
            "line": e.line, "status": e.status, "ids": e.ids, "basis": e.basis,
            "verified": e.verified,
            "detail": e.detail, "text": e.text,
            "numbers": [q.raw_text for q in e.quantities],
            "unsupported": [q.raw_text for q in e.unsupported],
            "failed_assertions": e.failed_assertions,
        }
    return json.dumps(
        {"path": str(report.path), "summary": report.summary,
         "entries": [entry(e) for e in report.entries]},
        indent=2,
    )


def _render_markdown(report: ReportAudit) -> str:
    lines = [
        "# claimtrail audit-report",
        "",
        f"- **Report**: `{report.path}`",
        f"- **Summary**: {_summary_line(report) or 'nothing linked'}",
        "",
        "| Status | Line | Record | Basis | Verified | Detail |",
        "|---|---|---|---|---|---|",
    ]
    for e in report.entries:
        ids = ", ".join(f"`{i[:12]}`" for i in e.ids) or "none"
        detail = e.detail.replace("|", "\\|")
        lines.append(f"| {e.status} | {e.line} | {ids} | {e.basis or ''} | {e.verified or ''} | {detail} |")
    return "\n".join(lines) + "\n"


def _truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 3] + "..."


__all__ = [
    "audit_report",
    "audit_block",
    "split_markdown",
    "split_html",
    "render",
    "ReportAudit",
    "ReportEntry",
    "Block",
    "MARKER_RE",
    "STATUSES",
    "DEFAULT_FAIL_ON",
]
