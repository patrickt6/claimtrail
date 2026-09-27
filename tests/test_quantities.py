"""The report extractor reads business prose the way a reader does."""
from __future__ import annotations

import pytest

from claimtrail.quantities import extract_quantities


def raw(text: str) -> list[str]:
    return [q.raw_text for q in extract_quantities(text)]


def values(text: str) -> list[float]:
    return [q.value for q in extract_quantities(text)]


def test_thousands_separators_are_one_number():
    assert values("36,734,685 applications from 5,329 lenders") == [36734685, 5329]


def test_year_ranges_and_bare_years_are_not_claims():
    assert values("Data from 2023-2025, first filed in 2019.") == []
    assert values("Data from 2023–2025") == []


def test_numeric_range_is_two_positive_numbers():
    assert values("between 10-20 lenders") == [10, 20]


def test_iso_dates_versions_and_code_spans_are_skipped():
    text = "Status 2026-09-14 on v0.4.0; run `hmda audit --n 500`. Took 64.17s."
    assert values(text) == [64.17]


def test_list_markers_headings_and_ids_are_skipped():
    assert values("1. Metric M1 has 3 parts") == [3]
    assert values("## 2. Results in Section 4.1 and Table 2") == []


def test_percent_currency_and_scale():
    qs = extract_quantities("Revenue $1.2M, margin 41%, 4.2 pts gap, 3 bn units, loss of -0.5")
    assert [(q.kind, q.value) for q in qs] == [
        ("scaled", 1_200_000), ("percent", 41), ("percent", 4.2),
        ("scaled", 3_000_000_000), ("decimal", -0.5),
    ]


def test_single_letter_scale_only_when_glued_on():
    # "5 m" might be metres, so it is read as a plain 5.
    assert values("a 5 m wall and a 5M budget") == [5, 5_000_000]


@pytest.mark.parametrize("stored,expected", [
    (4.237, True), (0.04237, True), (4.26, False), (0.0431, False), (4.3, False),
])
def test_percent_matches_at_displayed_precision(stored, expected):
    (q,) = extract_quantities("a 4.2% gap")
    assert q.matches(stored) is expected


def test_whole_percent_is_rounded_but_counts_are_exact():
    (pct,) = extract_quantities("41% smaller")
    assert pct.matches(0.4137) and pct.matches(40.6)
    (count,) = extract_quantities("36,734,685 rows")
    assert count.matches(36734685) and not count.matches(36734686)


def test_scaled_value_tolerance():
    (q,) = extract_quantities("$1.2M")
    assert q.matches(1_249_000) and not q.matches(1_260_000)


def test_markers_and_links_do_not_leak_numbers():
    text = "See [chart 7](https://x.test/a/42) <!-- ct:0a1b2c3d --> for 12 rows"
    assert values(text) == [12]
