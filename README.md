# claimtrail

**Every number in a report keeps a trail back to the computation that produced it.**

[![tests](https://github.com/patrickt6/claimtrail/actions/workflows/tests.yml/badge.svg)](https://github.com/patrickt6/claimtrail/actions/workflows/tests.yml)

claimtrail records what a computation produced and links each sentence of a
report to that record. It then checks the numbers in the sentence against the
record and keeps an append-only log of everyone who re-checked the result.
Everything lives in the project itself: a SQLite file and a folder of
compressed JSON that you commit to git like any other file. There is no server
and no account.

## The problem

A pipeline computes a number, an analyst (or, more and more often, an AI
assistant) writes it into a report, and a reviewer signs off. Each of those
steps is reasonable, and the number drifts anyway. It gets rounded, or copied
from an older draft. The data file under it gets replaced by a new one with the
same name. A summary line stops agreeing with the table it summarizes, and
nobody notices because each person only saw their own part. A few months later
nobody can say where "4.2%" came from, which run produced it, or whether anyone
ever checked it.

This happened in a public repo of mine. The README of
[hmda-audit](https://github.com/patrickt6/hmda-audit), a fair-lending audit
over 36.7 million mortgage applications, said that 5 of its 14 metrics were
measured on the full file. Counting the rows of its own metrics ledger gives 6.
The first time `claimtrail audit-report` ran on that README, it flagged the
sentence ([case study below](#case-study-a-fair-lending-audit)).

Banks have a regulatory reason to care. OSFI Guideline E-23, Model Risk
Management (2027), dated September 11, 2025 and in force from May 1, 2027,
says model data should be "traceable (that is, having documented lineage and
provenance)"
([OSFI](https://www.osfi-bsif.gc.ca/en/guidance/guidance-library/guideline-e-23-model-risk-management-2027)).
The same guideline expects newer use cases, "including those powered by AI",
to play a larger role.

## Why it happens

Mike, who builds the agent-orchestration project OpenRig, wrote about the
thousand-plus AI agents that broke out of an OpenAI security evaluation
([Agent civilizations](https://openrig.dev/blog/agent-civilizations), 18
September 2026). The headlines called them rogue. Mike runs a few hundred
agents of his own and reads it differently. On their own, he says, agents are
"pretty predictable", and "the trouble shows up across the population, in how
they hand work and decisions to each other." In the OpenAI case, many agents
questioned whether to break the rules and then got approval from another agent
they treated as an authority. In his own fleet, an agent asks whether it should
do something "and it gets a yes from another agent that doesn't have the
context to give one." His summary: "The information for a good decision
exists, it's just split between them, and nobody puts it together."

His fix is mostly about context. The agent making a decision needs the
information that decision depends on, and approval should come from someone
"that actually knows the work." He also points out that "with agents it all
happens in text," so you can go back and find where a failure started.

The post is about agents, not reports. My reading of it (this part is my
interpretation, not his) is that a number in a report is the same kind of
hand-off. The pipeline, the writer and the reviewer each hold part of the
picture. claimtrail writes the hand-off down, so you can trace a drift back to
where it started, and it puts the whole record in front of whoever approves
the result:

| Where the hand-off breaks | What claimtrail does |
|---|---|
| The writer restates a number without the data in view | Each sentence carries a marker pointing at its record, and `audit-report` checks every number in it |
| The data changes under a sentence one reasonable step at a time | Records are keyed on the contents of their input files, and `verify` names the file that changed |
| Approval comes from someone without the context | The log records who checked, and a check by the original author counts as a self-check, not an independent one |
| The prose drifts while every hash still matches | Claims carry structured assertions that are re-checked on every run |

## A two-minute demo

`examples/lending-review/run_demo.py` runs a small fair-lending screen over
**synthetic** loan applications. It only needs the standard library.

```bash
python examples/lending-review/run_demo.py
```

An AI assistant drafts the quarterly report from the screen's output. Four of
its numbers are right, it made one up, and one has no source at all:

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

Numbers are compared at the precision they were written with, so "41.4%" is
supported by a stored 0.413574, while "20,000" has to match exactly.

Next, a model validator re-runs the screen. The log counts that check as
independent because the validator isn't the analyst who recorded the run.
Last, the input file gets replaced under the same name. The next re-check
fails and says why, instead of the report drifting without anyone noticing:

```text
$ claimtrail verify 9798f933d29b
FAIL  9798f933d29bbf0d3254407c503ffeff
  output hash differs because input data changed: applications.csv changed
  since the run (recorded 62fa9a406daf, now a9bb01bc1545). The record still
  describes the original run; re-run to record the new data.
  logged #2 as mismatch
```

## Case study: a fair-lending audit

[hmda-audit](https://github.com/patrickt6/hmda-audit) screens 36,734,685
public mortgage applications from 5,329 lenders for gaps in approval rates.
Its `scripts/record_claims.py` builds six claims from the committed results
files, and the status paragraph of its README links to the first three:

| Claim | Basis | What it asserts |
|---|---|---|
| `hmda-scale` | MEASURED | applications, lenders, years |
| `hmda-status-counts` | MEASURED | metric counts, derived from the ledger rows |
| `hmda-tests` | REPORTED | the test count stated in the status file |
| `hmda-four-fifths` | MEASURED | lenders flagged, threshold, minimum count |
| `hmda-engineering` | MEASURED | DuckDB and pandas time and memory |
| `hmda-governance` | MEASURED | controls and their test backing |

The first audit found one DRIFT. The README said "5 MEASURED", and the ledger
rows give 6. The README now says 6 and the claim asserts it. The test count
(290 passed) was re-run and logged as self-checked rather than independent,
since the same person ran it. A CI job re-checks the claims, the README and the
log on every push. It doesn't re-run the national audit, which needs the full
data set; those re-runs happen on a machine that has the data, and each one
goes into the log.

## Where it came from

claimtrail started as `qprov`, a provenance tool for an NSERC-funded research
project on q-deformed real numbers. A paper there states results like "the
first nonzero coefficient appears at q^46", and each one sits at the end of a
chain: some code ran, with some inputs, on some machine, at some version. The
problem that shaped the design was two computations of the same quantity
disagreeing because two machines read different data files that happened to
share a name. Hashing file contents into every record, refusing a paper claim
that has no computation behind it, and auditing a LaTeX manuscript against the
store (`claimtrail audit-paper`) all came out of that work. The original
write-up is in [docs/history](docs/history/qprov-engine-overview.pdf), and the
research example runs from `examples/q-numbers`.

Nothing about that problem is specific to mathematics. Any report whose
numbers come from code has the same chain and can lose it the same ways.

## Install

```bash
pip install claimtrail
```

You need Python 3.11 or newer. To work on claimtrail itself, run
`pip install -e ".[dev]"` and then `python -m pytest`.

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

Link the sentence in your report to the claim with an invisible marker, then
run the audit:

```markdown
Group A was approved at 41.4% and group B at 36.1%, a gap of 5.3 points.
<!-- ct:q3-gap -->
```

```bash
claimtrail audit-report report.md --strict     # exit 1 on DRIFT, FAIL, MISSING, ORPHAN, UNBACKED
```

In HTML, put `data-claim="q3-gap"` on the element. For a LaTeX manuscript,
use `\provid{...}` with `claimtrail audit-paper paper.tex`, and
`claimtrail export-latex` writes a `\fact{...}` macro for each claim.

For results produced outside Python, such as a SQL job, a notebook or another
team's pipeline, record them with `claimtrail.register_external(...)` and
re-check them with `claimtrail verify ID --against fresh_outputs.json`.

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

An assertion is `PATH OP VALUE`, where `OP` is one of `== != < <= > >= in ~=`,
for example `outputs.rows == 36734685`, `result.gap ~= 5.3 +- 0.05` or
`outputs.auc in [0.80, 0.82]`.

## What gets recorded

Each tracked call stores the function's name, module and source code; a hash of
its inputs that includes the contents of any declared data files; a hash of its
output; the git commit and whether the tree was dirty; the host, CPU, RAM, GPU,
and Python and Sage versions; start and end times; and who ran it. A payload
next to the record keeps the arguments, result, stdout, stderr and warnings.
The id is a hash of the function, the inputs and the code version, so the same
inputs and code always give the same id. If someone edits a payload on disk,
the next read fails its integrity check.

## Limits

The author and verifier names come from `CLAIMTRAIL_ACTOR`, then git
`user.email`, then `user@host`. That is attribution, not authentication, and
"independent" is only as trustworthy as that setting.

The verification log is hash-chained and SQLite triggers refuse edits, which
makes tampering visible but doesn't prevent it: anyone with write access can
rebuild the whole chain. If that matters, copy the chain head (printed by
`--check-chain`) somewhere you don't control alone, like a commit or a CI log.

`verify` shows that a result reproduces, not that it is right. Property checks
(`@tracked(properties=[...])`) and structured assertions cover some of the
second question, and review covers the rest.

`audit-report` reads numbers with patterns. It handles thousands separators,
percents, currency, scale words and displayed precision, and it skips dates,
years, versions, list markers and identifiers, but it doesn't understand
sentences. In a range like "55-61%" it reads the first number as a count. Treat
a DRIFT as a reason for a person to look, not as a verdict.

CI checks the committed records and the report. Large computations get
re-checked on a machine that has the data, and those re-runs go into the log.

`verify` can only re-run a function it can import. Functions defined in
`__main__`, lambdas and notebook cells can't be re-run, so record their output
with `register_external` and check it with `verify --against`.

## Compatibility with qprov

`import qprov`, the `qprov` command, `QPROV_HOME`, existing
`.qprov/qprov.sqlite` stores and `\provid{...}` references all still work.
Old stores open where they are and migrate by adding columns and tables;
nothing gets moved or renamed.

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
