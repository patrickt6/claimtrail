"""Read the numbers a human-facing report asserts.

The LaTeX extractor in :mod:`claimtrail.audit_paper` reads math prose, where
``36,734,685`` does not occur and ``$`` opens math mode. Business reports
are different. They group thousands with commas, round to the displayed
precision, write percents, currency and scale words, and mix in years,
dates, versions and list markers that are not claims at all.

:func:`extract_quantities` reads that kind of text. Each result carries the
tolerance implied by the way the number was written: ``4.2%`` means a value
in ``[4.15, 4.25)``, so a payload value of ``4.237`` or ``0.04237`` supports
it, while ``4.3`` does not.
"""
from __future__ import annotations

import dataclasses
import math
import re
from typing import Literal

Kind = Literal["int", "decimal", "percent", "scaled"]

_SCALE_WORDS = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mn": 1e6, "mm": 1e6, "million": 1e6, "millions": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9, "billions": 1e9,
    "t": 1e12, "tn": 1e12, "trillion": 1e12,
}

# A number token: optional sign, optional currency, digits with optional
# thousands groups, optional decimals, then an optional percent sign or
# scale word. The sign counts only when it is not glued to a preceding
# digit, so "2023-2025" reads as two years, not 2023 and -2025.
_QUANTITY_RE = re.compile(
    r"""
    (?<![\w.,/])                       # not inside a word, decimal, or path
    (?P<sign>(?<![\d])[-\u2212])?  # minus, only if not after a digit
    (?P<cur>[$\u20ac\u00a3])?  # $, EUR, GBP
    (?P<num>
        \d{1,3}(?:,\d{3})+(?:\.\d+)?   # 36,734,685 or 1,234.5
      | \d+\.\d+                       # 0.86
      | \d+                            # 14
    )
    (?:
        \s?(?P<pct>%|percent\b|pct\b|pp\b|pts?\b|points?\b)
      | (?P<scale>[kKmMbBtT][nN]?)\b   # 1.2M, 3bn: a letter only when glued on
      | \s(?P<scaleword>thousand|millions?|billions?|trillion|bn|mn)\b
    )?
    """,
    re.VERBOSE,
)

# Spans that never carry claims. Each is blanked (same length) before the
# scan so offsets stay valid.
_SKIP_SPAN_RES = (
    re.compile(r"<!--.*?-->", re.S),                         # markers, comments
    re.compile(r"`[^`\n]*`"),                                # inline code
    re.compile(r"\]\([^)\s]*\)"),                            # markdown link targets
    re.compile(r"https?://\S+"),                             # bare URLs
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?\b"),  # ISO dates, timestamps
    re.compile(r"\bv?\d+\.\d+\.\d+(?:\.\d+)*\b"),            # versions, dotted ids
    re.compile(r"(?m)^\s{0,3}#{1,6}\s+(?:\d+(?:\.\d+)*\.?\s)?"),  # heading numbers
    re.compile(r"(?m)^\s*(?:\d+[.)]|[-*+])\s+"),             # list markers
    re.compile(r"\b[A-Za-z]+\d+[A-Za-z\d]*\b"),              # ids like M1, p003, sha1
    re.compile(r"\b(?:19|20)\d{2}\s?[-\u2013/]\s?(?:19|20)?\d{2}\b"),  # year ranges
    re.compile(r"\u00a7\s*\d+(?:\.\d+)*"),                   # section sign: \u00a73.2
    re.compile(r"\b(?:Q[1-4]|H[12]|FY)\s?'?\d{2,4}\b"),      # fiscal periods
    re.compile(r"(?:Section|Table|Figure|Fig\.|Chart|Exhibit|Step|Appendix|Phase|Part)\s+\d+(?:\.\d+)*", re.I),
)

_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")


@dataclasses.dataclass(frozen=True)
class Quantity:
    """One number a report asserts, with the tolerance its display implies."""

    value: float
    raw_text: str
    offset: int
    kind: Kind
    tolerance: float

    def candidates(self) -> tuple[float, ...]:
        """Values in a payload that could stand behind this number.

        A percent ``4.2%`` can be stored as ``4.2`` or as the fraction
        ``0.042``. Every other kind stands for itself.
        """
        if self.kind == "percent":
            return (self.value, self.value / 100.0)
        return (self.value,)

    def matches(self, stored: float) -> bool:
        for target in self.candidates():
            scale = 1.0 if target == self.value else 0.01
            if abs(stored - target) <= self.tolerance * scale + 1e-12:
                return True
        return False


def _display_tolerance(digits: str, multiplier: float, rounded: bool) -> float:
    """Half a unit in the last displayed place, times any scale word.

    ``1.2M`` is 1,200,000 give or take 50,000, and ``41%`` is 41 give or
    take 0.5. A plain integer such as ``36,734,685`` is read as a count,
    so it must match exactly.
    """
    if "." in digits:
        places = len(digits.split(".", 1)[1])
        return 0.5 * 10 ** (-places) * multiplier
    if not rounded:
        return 0.0
    return 0.5 * multiplier


def _blank(text: str, pattern: re.Pattern) -> str:
    return pattern.sub(lambda m: " " * len(m.group(0)), text)


def extract_quantities(text: str, *, skip_years: bool = True) -> list[Quantity]:
    """Return the numeric assertions in ``text``, in reading order.

    Years (1900 to 2099 written as a bare four-digit integer) are skipped
    by default because a report mentions many of them and a payload rarely
    stores them as results. Pass ``skip_years=False`` to keep them.
    """
    scrubbed = text
    for pattern in _SKIP_SPAN_RES:
        scrubbed = _blank(scrubbed, pattern)

    out: list[Quantity] = []
    for m in _QUANTITY_RE.finditer(scrubbed):
        digits = m.group("num")
        plain = digits.replace(",", "")
        scale = m.group("scale") or m.group("scaleword")
        if skip_years and m.group("pct") is None and scale is None \
                and m.group("cur") is None and _YEAR_RE.match(plain):
            continue
        value = float(plain)
        multiplier = 1.0
        kind: Kind = "decimal" if "." in plain else "int"
        if m.group("pct"):
            kind = "percent"
        elif scale:
            word = scale.lower()
            multiplier = _SCALE_WORDS.get(word, 1.0)
            if multiplier != 1.0:
                kind = "scaled"
        value *= multiplier
        if m.group("sign"):
            value = -value
        if not math.isfinite(value):
            continue
        out.append(Quantity(
            value=value,
            raw_text=m.group(0).strip(),
            offset=m.start(),
            kind=kind,
            tolerance=_display_tolerance(plain, multiplier, rounded=kind != "int"),
        ))
    return out


__all__ = ["Quantity", "extract_quantities"]
