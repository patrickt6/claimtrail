"""A small fair-lending screen over synthetic loan applications.

Everything here is synthetic. No real applicant, lender or bank data is
used. The numbers exist only to show how claimtrail keeps a report honest.
"""
from __future__ import annotations

import csv
import random
from pathlib import Path

import claimtrail

GROUPS = ("group_a", "group_b")


def generate_applications(path: Path, n: int = 20_000, seed: int = 7) -> Path:
    """Write ``n`` synthetic applications to ``path`` and return it."""
    rng = random.Random(seed)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["application_id", "group", "income_k", "loan_k", "approved"])
        for i in range(n):
            group = GROUPS[rng.random() < 0.3]
            income = round(rng.lognormvariate(4.3, 0.4), 1)
            loan = round(income * rng.uniform(2.0, 4.5), 1)
            score = 0.8 - 0.12 * (loan / income) + (0.0 if group == "group_a" else -0.05)
            approved = int(rng.random() < max(0.05, min(0.97, score)))
            w.writerow([f"A{i:06d}", group, income, loan, approved])
    return path


@claimtrail.tracked(data_files=["csv_path"], tags={"report": "q3-lending-review", "basis": "MEASURED"})
def approval_screen(csv_path) -> dict:
    """Approval rate per group, the gap between them, and the adverse impact ratio.

    The adverse impact ratio is the lower group's approval rate divided by
    the higher group's. A ratio under 0.8 is the common four-fifths
    screening threshold. It is a screening signal, not a legal finding.
    """
    counts = {g: [0, 0] for g in GROUPS}
    with open(claimtrail.path_of(csv_path), newline="") as f:
        for row in csv.DictReader(f):
            counts[row["group"]][0] += 1
            counts[row["group"]][1] += int(row["approved"])
    rates = {g: round(a / n, 6) for g, (n, a) in counts.items()}
    high, low = max(rates.values()), min(rates.values())
    return {
        "applications": sum(n for n, _ in counts.values()),
        "applications_by_group": {g: n for g, (n, _) in counts.items()},
        "approval_rate": rates,
        "approval_gap_pts": round(100 * (high - low), 4),
        "adverse_impact_ratio": round(low / high, 6),
        "four_fifths_threshold": 0.8,
    }
