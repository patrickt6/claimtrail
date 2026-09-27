# claimtrail

Every number in a report keeps a trail back to the computation that produced it.

[![tests](https://github.com/patrickt6/claimtrail/actions/workflows/tests.yml/badge.svg)](https://github.com/patrickt6/claimtrail/actions/workflows/tests.yml)

```bash
pip install claimtrail
```

A pipeline computes a number, someone (often an AI assistant now) writes it
into a report, and someone else signs off. Somewhere along the way the number
gets rounded, copied from an old draft, or the data under it changes. claimtrail
records each computation, links report sentences to those records, and checks
the numbers still match. It's a SQLite file plus some JSON that you commit with
your project.

It already caught one in my own work: the
[hmda-audit](https://github.com/patrickt6/hmda-audit) README said 5 of its 14
metrics were measured on the full file, and its own ledger says 6.

The idea owes a lot to Mike's post on
[agent civilizations](https://openrig.dev/blog/agent-civilizations): failures
come from hand-offs where "the information for a good decision exists, it's
just split between them, and nobody puts it together." A number in a report is
that kind of hand-off.

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

## Limits

Verifier names come from an env var or git config, so they're attribution, not
authentication. The log is hash-chained, which makes edits visible but doesn't
stop them. Number matching is pattern-based, so treat a DRIFT as "go look".

## Background

claimtrail started as `qprov`, the provenance tool for my NSERC research on
q-deformed numbers. `import qprov` and old `.qprov/` stores still work.

MIT license.
