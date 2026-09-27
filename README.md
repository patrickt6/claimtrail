# claimtrail

**Every number in a report keeps a trail back to the computation that produced it.**

[![tests](https://github.com/patrickt6/claimtrail/actions/workflows/tests.yml/badge.svg)](https://github.com/patrickt6/claimtrail/actions/workflows/tests.yml)

claimtrail records what a computation produced, links each sentence of a report
to that record, checks the numbers in the sentence against it, and keeps an
append-only log of who re-checked the result. Local first: a SQLite file and a
folder of compressed JSON that live in the project and travel with it in git.
No server, no account.

## The problem

A pipeline computes a number. An analyst, or more and more often an AI
assistant, writes it into a report. A reviewer approves the report. Each step
is reasonable on its own, and the number still drifts:

- it is rounded, re-stated, or copied from an older draft;
- the data under it is replaced by a file with the same name;
- a summary line disagrees with the table it summarizes, and nobody notices,
  because each person saw only their own part.

Months later nobody can say where "4.2%" came from, which run produced it, or
whether anyone ever checked it.

This is not a hypothetical. The public README of
[hmda-audit](https://github.com/patrickt6/hmda-audit), a fair-lending audit over
36.7 million mortgage applications, stated that 5 of its 14 metrics were
measured on the full file. Its own metrics ledger, row by row, shows 6. The
first `claimtrail audit-report` on that README flagged the sentence
([details](#case-study-a-fair-lending-audit)).

For banks this is also a regulatory question. OSFI Guideline E-23, Model Risk
Management (2027), dated September 11, 2025 and effective May 1, 2027, lists
the properties that model data should have, including "traceable (that is,
having documented lineage and provenance)"
([OSFI](https://www.osfi-bsif.gc.ca/en/guidance/guidance-library/guideline-e-23-model-risk-management-2027)).
The same guideline expects newer model use cases, "including those powered by
AI", to play a greater role.

## Why it happens: a coordination failure, not a bad actor

Mike, who builds the agent-orchestration project OpenRig, wrote about more than
a thousand AI agents that broke out of an OpenAI security evaluation
([Agent civilizations](https://openrig.dev/blog/agent-civilizations), 18 September
2026). The public story called them rogue. His reading comes from running a
few hundred agents of his own: on their own they are "pretty predictable", and
"the trouble shows up across the population, in how they hand work and
decisions to each other." In the OpenAI case, many agents questioned whether to
break the rules and then got approval from another agent they treated as an
authority. In his own fleet, an agent asks whether it should do something "and
it gets a yes from another agent that doesn't have the context to give one."
In his words: "The information for a good decision exists, it's just split
between them, and nobody puts it together."
His fix is mostly context: the agent making a decision needs the information
the decision depends on, and an approval should come from someone "that
actually knows the work." He also notes the good news: "with agents it all
happens in text," so the start of a failure can be traced.

*Interpretation, not taken from the post:* a number in a report is a hand-off
of exactly this kind. The pipeline, the writer (human or AI), and the reviewer
each hold part of the picture. claimtrail keeps the hand-off in text, so the
start of a drift can be found, and it puts the full record in front of the
person who approves:

| Coordination failure | What claimtrail does |
|---|---|
| The writer restates a number without the data in view | Each sentence carries a marker to its record; `audit-report` compares every number in it |
| The data changes under a sentence one reasonable step at a time | Records are keyed on input content; `verify` names the input file that changed |
| Approval comes from someone who lacks the context | The verification log records who checked, and counts a check by the author as a self-check, not an independent one |
| Prose drifts from data while every hash still matches | Claims carry structured assertions, re-checked on every run |

## What it does

| Step | Command | Result |
|---|---|---|
| Record a computation | `@claimtrail.tracked` or `register_external(...)` | Inputs (including file contents), outputs, code version, machine, author |
| State a claim | `claimtrail claim "..." --link ID --expect "outputs.rows == 36734685"` | Refused if the assertion is false |
| Audit a report | `claimtrail audit-report report.md` | MATCH, DRIFT, FAIL, MISSING, ORPHAN per linked block; UNBACKED with `--strict` |
| Re-check a result | `claimtrail verify ID` or `verify ID --against fresh.json` | Appended to a hash-chained, append-only log |
| Show the log | `claimtrail verifications [ID] [--check-chain]` | Who checked what, when; standing: independent, self-checked, author-unknown, unverified, failed |

## A two-minute demo

`examples/lending-review/run_demo.py` runs a small fair-lending screen over
**synthetic** loan applications, then walks through four scenes. It needs only
the standard library.

```bash
python examples/lending-review/run_demo.py
```

An AI assistant drafts the quarterly report from the screen's output. Four of
its numbers are right. One it made up. One has no source at all:

```text
$ claimtrail audit-report report.md --strict
  DRIFT=1  UNBACKED=1  MATCH=3

MATCH     line 8     f2869b618a78  basis=MEASURED  verified=unverified
          Group A was approved at 41.4% and group B at 36.1%, a gap of 5.3 points.

DRIFT     line 14    6196a65747f8  basis=MEASURED  verified=unverified
          Of the 20,000 applications, 6,120 came from group B.
          1 of 2 number(s) supported; not supported: 6,120

UNBACKED  line 17    -
          Branch managers reviewed 312 files by hand this quarter.
          1 number(s) with no claimtrail marker
```

Numbers are compared at the precision they are written with: "41.4%" is
supported by a stored 0.413574, and "20,000" must match exactly.

A model validator then re-runs the screen, and the log records the check as
independent, because the validator is not the analyst who recorded the run.
Finally, the input file is replaced under the same name, and the next re-check
fails loudly instead of the report drifting quietly:

```text
$ claimtrail verify 9798f933d29b
FAIL  9798f933d29bbf0d3254407c503ffeff
  output hash differs because input data changed: applications.csv changed
  since the run (recorded 62fa9a406daf, now a9bb01bc1545). The record still
  describes the original run; re-run to record the new data.
  logged #2 as mismatch
```

## Case study: a fair-lending audit

[hmda-audit](https://github.com/patrickt6/hmda-audit) screens 36,734,685 public
mortgage applications from 5,329 lenders for approval-rate disparity.
`scripts/record_claims.py` builds six claims from the committed results files,
and the README's status paragraph links to the first three:

| Claim | Basis | Assertions |
|---|---|---|
| `hmda-scale` | MEASURED | applications, lenders, years |
| `hmda-status-counts` | MEASURED | metric counts derived from the ledger rows |
| `hmda-tests` | REPORTED | test count as stated in the status file |
| `hmda-four-fifths` | MEASURED | lenders flagged, threshold, minimum count |
| `hmda-engineering` | MEASURED | DuckDB and pandas time and memory |
| `hmda-governance` | MEASURED | controls and their test backing |

The first audit reported one DRIFT: the README said "5 MEASURED" where the
ledger rows give 6. The README now says 6, and the claim asserts it. The
reported test count (290 passed) was re-run and is logged as self-checked, not
independent, because the same person ran it. A CI job re-checks the claims, the
README and the log on every push. CI does not re-run the national audit; that
happens on a machine with the data, and each re-run goes into the log.

## Where it came from: mathematics

claimtrail began as `qprov`, a provenance tool for an NSERC-funded research
project on q-deformed real numbers. A paper there states results such as "the
first nonzero coefficient appears at q^46", and each one is the end of a chain:
some code ran, with some inputs, on some machine, at some version. The failure
mode that shaped the design: two computations of the same quantity disagree,
because two machines read different data files that happen to share a name.
Hashing file contents into every record, refusing a paper claim with no backing
computation, and auditing a LaTeX manuscript against the store
(`claimtrail audit-paper`) all come from that work. The original write-up is in
[docs/history](docs/history/qprov-engine-overview.pdf), and the research example
runs from `examples/q-numbers`.

The failure mode is not specific to mathematics. Any report whose numbers come
from code has the same chain, and the same ways to lose it.

## Install

```bash
pip install "claimtrail @ git+https://github.com/patrickt6/claimtrail"
```

Python 3.11 or newer. For development: `pip install -e ".[dev]"` and
`python -m pytest`.

## Quickstart

```python
import claimtrail

@claimtrail.tracked(data_files=["csv_path"], tags={"basis": "MEASURED"})
def approval_screen(csv_path):
    ...  # read claimtrail.path_of(csv_path), return a dict of results

result = approval_screen("data/applications.csv")
(run,) = claimtrail.find(function="approval_screen")

claimtrail.claim(
    "Approval gap between the two groups, in percentage points",
    claim_id="q3-gap",
    computation_id=run.id,
    expect=["result.approval_gap_pts ~= 5.3 +- 0.05"],
)
```

Link the report sentence to the claim with an invisible marker, then audit:

```markdown
Group A was approved at 41.4% and group B at 36.1%, a gap of 5.3 points.
<!-- ct:q3-gap -->
```

```bash
claimtrail audit-report report.md --strict     # exit 1 on DRIFT, FAIL, MISSING, ORPHAN, UNBACKED
```

In HTML, `data-claim="q3-gap"` on an element works the same way. In LaTeX,
`\provid{...}` and `claimtrail audit-paper paper.tex` do the same for a
manuscript; `claimtrail export-latex` writes `\fact{...}` macros for each claim.

Results produced outside Python (a SQL job, a notebook, another team's
pipeline) are recorded with `claimtrail.register_external(...)` and re-checked
with `claimtrail verify ID --against fresh_outputs.json`.

## The command-line tool

```text
claimtrail init                          create .claimtrail/ in the current directory
claimtrail list | show ID | find --tag k=v
claimtrail claim "..." --link ID [--expect "PATH OP VALUE"] [--tag paper=SLUG]
claimtrail check [--paper SLUG]          re-check every structured assertion
claimtrail audit-report FILE [--strict]  numbers in a Markdown or HTML report
claimtrail audit-paper FILE.tex          \provid references in a LaTeX manuscript
claimtrail verify ID [--against FILE [--key PATH]] [--no-record]
claimtrail verifications [ID] [--check-chain]
claimtrail lint                          orphan, dangling, tampered, drifted and failing records
claimtrail export-latex | properties | gc
```

Assertion syntax: `PATH OP VALUE`, where `OP` is one of `== != < <= > >= in ~=`.
Examples: `outputs.rows == 36734685`, `result.gap ~= 5.3 +- 0.05`,
`outputs.auc in [0.80, 0.82]`.

## What is recorded

For every tracked call: the function name, module and source; a hash of the
inputs, with the contents of declared data files; a hash of the output; the git
commit and a dirty flag; the host, CPU, RAM, GPU, Python and Sage versions;
start, end and runtime; who ran it; and a payload with the arguments, result,
stdout, stderr and warnings. The id is a hash of the function, the inputs and
the code version, so the same inputs and code always give the same id. A
payload that is edited on disk fails its integrity check on the next read.

## Limits

- **Attribution, not authentication.** The author and verifier names come from
  `CLAIMTRAIL_ACTOR`, else git `user.email`, else `user@host`. Independence is
  as trustworthy as that configuration.
- **Tamper evidence, not prevention.** The verification log is hash-chained and
  SQLite triggers refuse edits, but anyone with write access can rebuild the
  whole chain. Anchor the chain head (printed by `--check-chain`) somewhere you
  do not control alone, such as a commit or a CI log.
- **Reproduced is not the same as valid.** `verify` shows that a result
  reproduces. Whether the result is correct is a separate question; property
  checks (`@tracked(properties=[...])`) and structured assertions cover part of
  it, review covers the rest.
- **Number reading is pattern-based.** `audit-report` reads thousands
  separators, percents, currency, scale words and displayed precision, and skips
  dates, years, versions, list markers and identifiers. It does not understand
  sentences. In a range such as "55-61%", the first number is read as a count.
  A DRIFT is a prompt for a person, not a verdict.
- **Heavy jobs are re-checked where the data lives.** CI checks the committed
  records and the report. Re-running a large computation happens on a machine
  that has the data, and the result goes into the log.
- **`verify` needs an importable function.** Functions defined in `__main__`,
  lambdas and notebook cells cannot be re-run; record them with
  `register_external` and check them with `verify --against`.

## Compatibility with qprov

`import qprov`, the `qprov` command, `QPROV_HOME`, existing `.qprov/qprov.sqlite`
stores and `\provid{...}` references keep working. Stores open in place and
migrate additively; nothing is moved or renamed.

## Project layout

```text
src/claimtrail/
  tracking.py      @tracked decorator and payload assembly
  external.py      register results produced outside Python
  store.py         SQLite plus gzipped JSON payloads, migrations
  claims.py        claims, the paper-tag gate, LaTeX export, check_claims
  assertions.py    structured claim assertions
  quantities.py    reading numbers from business prose
  audit_report.py  Markdown and HTML report audit
  audit_paper.py   LaTeX manuscript audit
  verify.py        re-run or compare, and log the check
  ledger.py        append-only, hash-chained verification log
  properties.py    property-based checks; contrib/qnumbers.py for the research domain
  cli.py           the claimtrail command
examples/
  lending-review/  synthetic fair-lending demo, four scenes
  q-numbers/       the original research example
```

## License

MIT. See [LICENSE](./LICENSE).
