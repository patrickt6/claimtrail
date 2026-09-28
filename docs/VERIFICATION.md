# Verification

An index of every check in this repository: what it proves, what it does
not prove, how to rerun it, and the result as of the commit noted at the
bottom.

## 1. The pytest suite

**What it is.** `tests/*.py`, run with `pytest`. Covers hashing and
content fingerprinting (`inputs.py`, `serialize.py`), the SQLite +
gzipped-JSON store (`store.py`), the `@tracked` decorator (`tracking.py`),
claim recording and LaTeX export (`claims.py`), structured assertions
(`assertions.py`), verification and re-running (`verify.py`), the
append-only ledger (`ledger.py`), report and paper auditing
(`audit_report.py`, `audit_paper.py`), number extraction from prose
(`quantities.py`), the CLI (`cli.py`), and the qnumbers property registry
(`contrib/qnumbers.py`).

**What it proves.** That every scenario a test author thought to write
down still behaves as documented, on every run. Regressions in those
specific scenarios are caught immediately.

**What it does not prove.** Behavior in scenarios nobody wrote a test
for. A passing suite is evidence the tested paths work, not evidence the
whole surface is bug-free; see the mutation testing section below for one
way that gap gets measured, not closed.

**How to rerun.**

```bash
pip install -e ".[dev]"
python -m pytest
```

**Result.** 245 passed, 1 skipped (`test_sage_integration.py`, skipped
when Sage is not importable - true in this environment and in CI).

## 2. Hypothesis property tests

**What it is.** `tests/test_hypothesis_properties.py`, plus the
Hypothesis-driven property in `tests/test_properties.py`
(`test_hypothesis_finds_counterexample`) and the qnumbers metamorphic
properties in `contrib/qnumbers.py` (used by `@tracked(properties=...)`,
exercised by `tests/test_properties.py`). Hypothesis generates inputs
within declared strategies and checks that a stated invariant holds for
every one it tries (100 to 200 examples per property here), shrinking any
failure to a small counterexample.

**Properties checked, and why each one matters:**

- `hash_value` and `hash_file` are deterministic (same input, same hash,
  every time) and content-sensitive (different input almost always
  yields a different hash; verified directly by comparing canonical-JSON
  equality against hash equality, and by comparing byte-for-byte file
  content against file-hash equality). This is the assumption every
  computation id and every `canonical_file` descriptor rests on.
- Recording the same computation twice, for a swept range of inputs,
  collapses to one row (`@tracked`'s content-addressed id).
- Recording a function under a fixed name, then recording a different
  function body under the same name with the same input, raises
  `ClaimtrailCollisionError` for a swept range of inputs, and never
  corrupts the row already on disk. This is the property that would have
  caught the write-order bug described in section 4 below.
- A `path ~= value +- tol` claim assertion accepts every generated
  `actual` inside `[value - tol, value + tol]` and rejects every
  generated `actual` outside it, including at the boundary (a dedicated
  non-Hypothesis test also checks the exact boundary, since a naive
  random sample rarely lands exactly on it).
- A value round-trips through canonical JSON (`canonical_dumps` /
  `canonical_loads`), and through the SQLite + gzipped-payload store
  (`Store.write_payload` / `Store.read_payload`), unchanged - including
  NaN and +/-infinity, which plain `json` cannot represent at all.

**What it proves.** That the stated invariant holds for every input
Hypothesis actually tried, which for a well-chosen strategy is a much
wider net than a handful of hand-picked examples, and that a violation
gets reported with a minimal reproducing example.

**What it does not prove.** Universal correctness. Each strategy has
bounds (`min_value`/`max_value` on floats and integers, `max_size` on
strings and containers, `max_examples` on the sweep); an invariant could
still fail outside those bounds, or on an input shape the strategy cannot
generate (for example, a Sage type - the Sage-specific encode/decode
paths in `serialize.py` are exercised only by `test_sage_integration.py`,
which is skipped without Sage installed).

**How to rerun.**

```bash
python -m pytest tests/test_hypothesis_properties.py tests/test_properties.py -v
```

## 3. Mutation testing (mutmut)

**What it is.** `mutmut` rewrites the source of a targeted file one
change at a time (flip a comparison, drop a default argument, swap a
constant) and reruns the test suite against each mutant. A mutant the
suite catches (a test fails) is "killed"; a mutant the suite does not
notice is a "survivor" - a line whose exact behavior is invisible to the
tests, not necessarily a bug, but always a gap in coverage.

**Scope.** The five modules the task called "core": hashing
(`inputs.py`, `serialize.py`), the records store (`store.py`), and claim
checking / assertion evaluation (`assertions.py`, `claims.py`). Run with
`mutate_only_covered_lines = true` (skip lines no test exercises at all -
otherwise schema DDL strings and defensive branches dominate the mutant
count without saying anything about test quality) and
`timeout_multiplier = 6.0` (default 15.0; lowered to keep a ~1,800-mutant
run tractable - see the caveat on timeouts below).

**Run in an isolated git worktree**, not the working tree: mutmut copies
mutated source into a `mutants/` directory and runs the suite against
that copy, which is disruptive to do inside the repo you are also
editing.

```bash
git worktree add -b mutmut-testing ../claimtrail-mutmut main
cd ../claimtrail-mutmut
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" mutmut
```

Add this section to that worktree's `pyproject.toml` (not part of the
committed config, since it is a scoping decision for one exercise, not a
property of the package):

```toml
[tool.mutmut]
source_paths = ["src"]
only_mutate = [
    "src/claimtrail/inputs.py",
    "src/claimtrail/serialize.py",
    "src/claimtrail/store.py",
    "src/claimtrail/assertions.py",
    "src/claimtrail/claims.py",
]
mutate_only_covered_lines = true
timeout_multiplier = 6.0
timeout_constant = 1.0
```

```bash
python -m mutmut run
python -m mutmut results           # list survivors/timeouts
python -m mutmut show <mutant-name>  # view one mutant's diff
```

**What it proves.** That a one-line behavioral change in the mutated
region has a measurable chance (the score) of being caught by the test
suite. A killed mutant is strong local evidence the corresponding line
matters to some test's outcome.

**What it does not prove.** Mutation testing only applies single,
mechanical mutations (flip an operator, drop an argument, swap a string)
one at a time; it does not construct multi-line or semantically
equivalent bugs, and a mutant that changes nothing observable (dead code,
a log message nobody asserts on, an equivalent rewrite of the same logic
- SQLite is case-insensitive on keywords, so `SELECT` vs `select`
mutants below are exactly this) will always survive regardless of test
quality. `mutate_only_covered_lines` means genuinely untested code is
silently excluded from the denominator rather than counted as a failure,
so the score describes the tested surface, not the whole file. Modules
outside the five in scope (`tracking.py`'s wrapper beyond what the suite
exercises, `audit_report.py`, `audit_paper.py`, `cli.py`, `verify.py`,
`ledger.py`, `quantities.py`, `properties.py`, `contrib/qnumbers.py`)
were not mutated at all here.

**Results.**

| Run | Mutants | Killed | Survived | Timeout | Score (killed/total) |
|---|---|---|---|---|---|
| Baseline (after the canonical_file id fix in section 4, before the hardening tests below) | 1,809 | 1,316 | 493 | 0 | 72.7% |
| After adding the regression tests below | 1,852 | 1,386 | 430 | 36 | 74.8% (76.8% counting timeouts as caught) |

The mutant count differs slightly between runs because `claims.py`'s
source changed (the `force` parameter added in section 4) and
`mutate_only_covered_lines` recomputes which lines are covered on each
run.

**Regression tests added to kill real, distinguishable survivors** (not
every survivor - see below): `Store.insert_computation` was silently
dropping a non-default `output_hash_algorithm` and a `True` `code_dirty`
value (`comp.output_hash_algorithm or PAYLOAD_HASH_ALGORITHM` and
`int(comp.code_dirty) if comp.code_dirty is not None else None` both had
survivors that only a non-default, truthy value could distinguish, and
no existing test used one); `Store.insert_claim`'s fallback from
`claim.paper_tag` to `claim.tags.get("paper")` is the only thing that
populates the `paper_tag` column for every claim made through the public
`claim()` function, since `claim()` never sets the attribute directly,
and nothing was pinning that fallback; `assertions.resolve_path`'s bounds
and type guards use `or` so that either condition alone should raise -
a survivor showed the exact-boundary case (`index == len(list)`) and the
non-sequence case were both untested; the `~=` tolerance comparison is
inclusive at the boundary (`<=`) and must reject a `bool` actual (`bool`
is an `int` subclass in Python, so this needs an explicit test, not just
type-based reasoning); `claim()`'s `notes` field and the way
`deterministic_id` actually varies with `value_numeric` and
`computation_id` had no assertions anywhere; and
`inputs.normalize_for_hash` needed direct tests of its `{tag, sha}`
output shape, its namedtuple guard (`isinstance(value, tuple) and
hasattr(value, "_fields")`, which a survivor showed could not be told
apart from `or` without an object that has `_fields` but is not a
tuple), and its depth-limit boundary. All of these are now covered in
`tests/test_store.py`, `tests/test_assertions.py`, `tests/test_claims.py`
and `tests/test_canonical_file.py`.

**Remaining survivors, triaged but not individually chased.** Of the 430
survivors in the final run, a large majority fall into two low-value
categories visible directly in their diffs (`mutmut show <name>`):
SQL keyword re-casing (`SELECT` to `select`, column names upper-cased),
which SQLite treats identically either way, and log or error-message
text changes (a docstring, an f-string's static wording, a truncation
index inside an error message) that do not change any return value,
raised exception type, or stored data. The highest-count remaining
functions are `collect_data_hashes` (53, `inputs.py`, an older, more
heavily branched visitor than `normalize_for_hash`), `_row_to_computation`
and `_row_to_claim` (`store.py`, straight-line field-mapping code with
many independent columns, each survivor typically one column's mutant),
and `_to_jsonable` (`serialize.py`, the Sage-specific encode branches,
most of which only run when Sage is installed). None of these were
individually re-triaged for this task; a future pass through
`collect_data_hashes` in particular would likely find a similar density
of real gaps to the ones `normalize_for_hash` had, since the two
functions share most of their traversal logic.

**36 timeout results**, all in `normalize_for_hash`, `insert_computation`,
and `_row_to_computation` in the final run, are ambiguous: mutmut's
timeout is `timeout_constant + timeout_multiplier * baseline_time`, set
low here (`timeout_multiplier = 6.0`) to keep the run tractable, and
these did not reproduce as hangs when spot-checked by hand - the more
likely explanation is contention between the many parallel worker
processes mutmut runs, not an actual infinite loop introduced by the
mutation. They are not counted as killed in the score above, and not
individually investigated.

## 4. Real bug found and fixed: canonical_file ids were not content-only

**What HOW-IT-WORKS.md documented and what a direct test showed.**
`canonical_file()`'s docstring, and `@tracked`'s, both stated that two
files with identical content at different paths, or the same file
touched without a content change, would collapse onto the same
computation id ("content-addressed"). A direct experiment (two files with
byte-identical content in different directories, and one file touched
with `os.utime` and no content change) showed the opposite: both cases
minted a new row, because `input_hash` was computed from the whole
`canonical_file()` descriptor dict, which includes the resolved absolute
`path` and `mtime`, not just the content hash (`sha`).

**Decision.** The documented behavior was the design intent (stated
directly, more than once, in the module and function docstrings), so the
code was fixed to match the documentation rather than the other way
around. `inputs.normalize_for_hash` (added) strips a `canonical_file`
descriptor down to `{tag, sha}` before it is hashed for the id;
`tracking.py`, `external.register_external`, and `cli._check_id_drift`
all apply it consistently, so the id, the retroactive-registration path,
and the drift check agree.

**A second, independent bug this surfaced.** Before the id fix, two
calls that now legitimately collapse onto the same id (same content,
different path) had never been able to reach `Store.insert_computation`'s
collision check with a same-id, different-payload row, because the old,
buggy id made that impossible. After the fix, they could - and doing so
exposed that `Store.write_payload` overwrites the payload file at a
given id unconditionally, before `insert_computation` checks whether
that id's existing row is a legitimate match. On a genuine collision
(confirmed directly: two functions with the same declared name and a
different body, called with the same input), the older row's payload was
already overwritten by the second write's bytes before
`ClaimtrailCollisionError` was raised, so a subsequent `read_payload` on
the older id raised `PayloadTamperedError` - not because anyone tampered
with anything, but because the store had overwritten its own prior
write. Fixed with `tracking._skip_write_or_raise`, which checks for a
content-equivalent existing row before writing anything: skip the write
if the content already matches (first writer wins), raise before
touching disk if it genuinely differs.

**Regression tests**: `tests/test_canonical_file.py`
(`test_tracked_collapses_id_across_different_paths_same_basename`,
`test_tracked_does_not_mint_new_row_on_mtime_only_touch`),
`tests/test_tracking.py`
(`test_collision_does_not_corrupt_existing_row`), and the Hypothesis
sweeps in `tests/test_hypothesis_properties.py` described in section 2.

## 5. Real bug found and fixed: claim_id collision behavior did not match its docs

`INTEGRATION.md` said a stable `claim_id` "overwrites in place" on
re-registration, including in its own worked example (stage an unbacked
claim, then back-attach a real `computation_id` under the same
`claim_id`). `claim()` never exposed a `force` parameter, so it actually
raised `ClaimtrailCollisionError` on any content change under an
existing `claim_id` - including that same back-attach example, confirmed
by running it directly. `hmda-audit`'s `scripts/record_claims.py` had
already found and worked around this by calling
`store.insert_claim(rec, force=True)` directly, with a comment explaining
why.

**Decision.** The collision safety is the deliberate, cross-cutting v3
store design (the same protection `insert_computation` has, and load-
bearing for the "a changed function body raises a collision" property in
section 2), so it was kept. `claim()` was given its own `force`
parameter, forwarding to `insert_claim`, so the documented "reviewed
replacement" pattern is a first-class, tested part of the public API
instead of something every caller has to reinvent via store internals.
`INTEGRATION.md` was rewritten to describe the actual default (no-op on
identical content, collision error on any other change) and the
`force=True` escape valve, with its worked examples corrected to match.

**Regression tests**: `tests/test_claims.py`
(`test_reregistering_same_claim_id_with_identical_content_is_a_noop`,
`test_reregistering_same_claim_id_with_different_content_raises_by_default`,
`test_force_true_replaces_an_existing_claim_row`,
`test_back_attaching_a_staged_claim_needs_force`).

## What this document does not cover

CI (`.github/workflows/tests.yml`) runs the same pytest suite on Python
3.11, 3.12, and 3.13, plus the two `examples/` scripts end to end - that
is a breadth check (does it work on every supported Python, does the
documented demo actually run), not a separate verification technique, so
it is not re-described here; see the workflow file directly. This
document also does not cover `audit_report.py`, `audit_paper.py`,
`cli.py`, `verify.py`, `ledger.py`, or `contrib/qnumbers.py` beyond what
the pytest suite already exercises for them - they were not in the
mutation-testing scope for this pass (section 3 explains why), and no
new property tests were written against them here.

---

Source state measured at commit `efa6d37` on `main` (the "after
adding the regression tests below" row in section 3, and sections 4 and
5, describe that commit). Commits after it in this repository's history
are documentation-only (fixing a stale citation, adding this file, and a
version bump) and do not change the numbers above. Rerun the commands in
each section against a later commit to get current numbers; this file
states a result, not a live badge.
