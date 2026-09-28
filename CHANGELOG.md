# Changelog

## 0.5.2 (unreleased, pending approval)

### Fixed

- `@tracked(data_files=[...])` computation ids are now content-only. A
  `canonical_file()` descriptor embeds a file's resolved absolute path
  and mtime alongside its content hash; the input hash previously
  included the whole descriptor, so two files with byte-identical
  content at different paths, or the same file touched without a
  content change, minted separate rows instead of collapsing onto one,
  contradicting the documented design. Fixed via a new
  `inputs.normalize_for_hash` helper, applied consistently in
  `@tracked`, `register_external`, and the CLI's id-drift check.
  See `docs/HOW-IT-WORKS.md`'s Limits and Design decisions sections.
- Fixed a related, independent bug in the write path: `Store.write_payload`
  overwrote a row's payload file before `Store.insert_computation` checked
  for a same-id conflict, so a genuine collision (for example, an edited
  function body under an unchanged declared name) corrupted the older
  row's payload before `ClaimtrailCollisionError` was raised. The wrapper
  now checks for an existing, content-equivalent row before writing
  anything.
- `claim()` now takes a `force` parameter, forwarded to
  `Store.insert_claim`. `INTEGRATION.md` said re-registering a stable
  `claim_id` "overwrites in place"; `claim()` actually raised
  `ClaimtrailCollisionError` on any content change, including in
  `INTEGRATION.md`'s own staged-claim back-attach example. The default
  behavior (no-op on identical content, collision error otherwise) is
  unchanged and is now documented accurately; `force=True` is the new,
  supported way to do a deliberate reviewed replacement.
- `docs/HOW-IT-WORKS.md`'s hmda-audit story no longer cites a commit hash
  or quotes text from the hmda-audit repository; that repository was
  deleted and recreated with a single commit, so the old history and
  files it quoted are no longer public. The story is unchanged: a stated
  count of full-file metrics drifted from the project's own metrics
  ledger, and claimtrail's `audit-report` caught it.

### Migration note

Existing stores are unaffected: rows already on disk keep their ids.
The first time a `data_files`-declared function runs again under 0.5.2,
it computes a new id (content-only, per the fix above), so it writes one
additional row rather than colliding with or replacing its pre-0.5.2
row. Older rows are never rewritten.

### Added

- Hypothesis property tests (`tests/test_hypothesis_properties.py`) for
  hashing determinism and content-sensitivity, recording idempotency,
  the body-change collision error, `~=` tolerance assertion boundaries,
  and canonical JSON / store round-trips.
- Around 25 regression tests added after mutation testing `inputs.py`,
  `serialize.py`, `store.py`, `assertions.py`, and `claims.py` with
  `mutmut` (score 72.7 percent to 74.8 percent on that run); see
  `docs/VERIFICATION.md` for what they cover.
- `docs/VERIFICATION.md`: an index of every verification check in this
  repo, what each one proves and does not prove, how to rerun it, and
  the current results.

## 0.5.1 and earlier

Not tracked in this file. See `git log`.
