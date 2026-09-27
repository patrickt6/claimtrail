"""End-to-end demo: an AI-drafted lending report, checked against the record.

Synthetic data only. Run from anywhere:

    python examples/lending-review/run_demo.py

Four scenes:
  1. An analyst runs the approval screen. claimtrail records the run and
     three claims, each with a structured assertion.
  2. An AI assistant drafts the quarterly report. audit-report checks every
     number and catches one the assistant made up, plus one with no source.
  3. A model validator re-runs the screen. The check goes into the
     append-only log and counts as independent.
  4. The input file is quietly replaced under the same name. The next
     verify fails loudly instead of the report silently drifting.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STORE = HERE / ".claimtrail"
DATA = HERE / "data"
sys.path.insert(0, str(HERE))

shutil.rmtree(STORE, ignore_errors=True)
shutil.rmtree(DATA, ignore_errors=True)
DATA.mkdir()

import claimtrail  # noqa: E402

claimtrail.set_store_root(STORE)
import pipeline  # noqa: E402


def cli(*args: str, actor: str | None = None) -> int:
    env = dict(os.environ, CLAIMTRAIL_HOME=str(STORE))
    if actor:
        env["CLAIMTRAIL_ACTOR"] = actor
    print(f"$ claimtrail {' '.join(args)}")
    proc = subprocess.run(
        [sys.executable, "-m", "claimtrail.cli", *args], env=env, cwd=HERE,
        capture_output=True, text=True,
    )
    print((proc.stdout + proc.stderr).rstrip(), "\n")
    return proc.returncode


def expect(code: int, wanted: int, what: str) -> None:
    if code != wanted:
        sys.exit(f"demo expectation failed: {what} exited {code}, expected {wanted}")


def scene(n: int, title: str) -> None:
    print(f"\n=== Scene {n}: {title} ===\n")


scene(1, "the analyst runs the screen and records claims")
os.environ["CLAIMTRAIL_ACTOR"] = "analyst@example.com"
csv_path = pipeline.generate_applications(DATA / "applications.csv")
result = pipeline.approval_screen(str(csv_path))
(run,) = claimtrail.find(function="approval_screen")
ids = {
    "apps": claimtrail.claim(
        "Applications screened this quarter", computation_id=run.id,
        expect=["result.applications == 20000"], tags={"paper": "q3-lending-review"},
    ),
    "gap": claimtrail.claim(
        "Approval gap between group A and group B, in percentage points", computation_id=run.id,
        expect=["result.approval_gap_pts ~= 5.3 +- 0.05"], tags={"paper": "q3-lending-review"},
    ),
    "air": claimtrail.claim(
        "The adverse impact ratio clears the four-fifths screen", computation_id=run.id,
        expect=["result.adverse_impact_ratio >= 0.8"], tags={"paper": "q3-lending-review"},
    ),
}
print(f"recorded run {run.id[:12]} by {run.recorded_by}: {result}")
expect(cli("check", "--paper", "q3-lending-review"), 0, "check")

scene(2, "an AI assistant drafts the report; audit-report checks it")
report = HERE / "report.md"
report.write_text((HERE / "report_draft.md").read_text().format(**ids))
expect(cli("audit-report", "report.md", "--strict"), 1, "audit-report on the AI draft")

scene(3, "a model validator re-runs the screen")
expect(cli("verify", run.id[:12], actor="validator@example.com"), 0, "independent verify")
cli("verifications", run.id[:12])

scene(4, "the input file is replaced under the same name")
pipeline.generate_applications(DATA / "applications.csv", seed=8)
expect(cli("verify", run.id[:12], actor="validator@example.com"), 2, "verify after the data swap")
expect(cli("verifications", "--check-chain"), 0, "chain check")
print("demo finished: every scene behaved as described")
