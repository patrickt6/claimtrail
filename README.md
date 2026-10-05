# claimtrail

Every number in a report keeps a trail back to the computation that produced it.

[![tests](https://github.com/patrickt6/claimtrail/actions/workflows/tests.yml/badge.svg)](https://github.com/patrickt6/claimtrail/actions/workflows/tests.yml)
[![PyPI](https://img.shields.io/pypi/v/claimtrail.svg)](https://pypi.org/project/claimtrail/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

<p align="center">
  <img src="docs/img/hero.png" alt="A drafted report on the left with each sentence tagged MATCH, DRIFT or UNBACKED, and the claimtrail audit-report output that produced those tags on the right" width="100%">
</p>
<p align="center"><sub>Output of <code>examples/lending-review</code> on synthetic data: one number drifted from its source and one never had a source.</sub></p>

```bash
pip install claimtrail
python examples/lending-review/run_demo.py   # from a clone: the run in the picture
```

A pipeline computes a number, someone (often an AI assistant now) writes it
into a report, and someone else signs off. Somewhere along the way the number
gets rounded, copied from an old draft, or the data under it changes. claimtrail
records each computation, links report sentences to those records, and checks
the numbers still match. It's a SQLite file plus some JSON that you commit with
your project.

| Verdict | Meaning |
|---|---|
| `MATCH` | the sentence's numbers agree with the recorded run |
| `DRIFT` | the sentence points at a run, but a number in it doesn't match |
| `UNBACKED` | the sentence has a number and no record behind it |

`audit-report` also reports `FAIL`, `MISSING` and `ORPHAN` for failed assertions and broken markers (see `src/claimtrail/audit_report.py`).

It already caught one in my own work: the
[hmda-audit](https://github.com/patrickt6/hmda-audit) README said 5 of its 14
metrics were measured on the full file, and its own ledger says 6.

## Use it

```python
import claimtrail

@claimtrail.tracked(data_files=["csv_path"])
def approval_screen(csv_path):
    ...

approval_screen("data/applications.csv")
(run,) = claimtrail.find(function="approval_screen")
claimtrail.claim("Approval gap", claim_id="q3-gap", computation_id=run.id,
                 expect=["result.approval_gap_pts ~= 5.3 +- 0.05"])
```

Mark the sentence in your report and audit it:

```markdown
Group A was approved at 41.4% and group B at 36.1%, a gap of 5.3 points.
<!-- ct:q3-gap -->
```

```text
$ claimtrail audit-report report.md --strict
  DRIFT=1  UNBACKED=1  MATCH=3

DRIFT     line 14    6196a65747f8  basis=MEASURED  verified=unverified
          Of the 20,000 applications, 6,120 came from group B.
          1 of 2 number(s) supported; not supported: 6,120

UNBACKED  line 17    -
          Branch managers reviewed 312 files by hand this quarter.
          1 number(s) with no claimtrail marker
```

`claimtrail verify ID` re-runs a computation and logs who checked it, and it
tells you when an input file changed since the run. Results from outside
Python go in with `register_external` and get checked with `verify --against`.
For LaTeX papers there's `audit-paper`.

Try the full demo on synthetic data: `python examples/lending-review/run_demo.py`.

For a module-by-module walkthrough of the architecture, data model, and the hmda-audit story, see [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md). For what backs that up (the test suite, property tests, mutation testing, and two real bugs they found), see [docs/VERIFICATION.md](docs/VERIFICATION.md).

## Limits

Verifier names come from an env var or git config, so they're attribution, not
authentication. The log is hash-chained, which makes edits visible but doesn't
stop them. Number matching is pattern-based, so treat a DRIFT as "go look".

## Background

The idea owes a lot to Mike's post on
[agent civilizations](https://openrig.dev/blog/agent-civilizations): failures
come from hand-offs where "the information for a good decision exists, it's
just split between them, and nobody puts it together." A number in a report is
that kind of hand-off.

claimtrail started as `qprov`, the provenance tool for my NSERC research on
q-deformed numbers. `import qprov` and old `.qprov/` stores still work.

MIT license.
