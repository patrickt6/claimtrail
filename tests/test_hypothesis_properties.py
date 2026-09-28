"""Property-based tests (Hypothesis) for claimtrail's core invariants.

Each test states an invariant claimtrail relies on elsewhere in the code
and checks it directly against generated inputs, rather than against one
or two hand-picked examples:

- hashing (``hash_file``, ``hash_value``) is deterministic and
  content-sensitive.
- recording the same computation twice collapses to one row
  (``@tracked``'s id convention, ``tracking.py:154-155``).
- changing a function body without changing its inputs raises
  ``ClaimtrailCollisionError`` (the ``payload_hash`` identity check,
  ``store.py:577-585``).
- a claim assertion ``path ~= value +- tol`` (``assertions.py``) accepts
  actual values inside the tolerance and rejects values outside it.
- a record round-trips through canonical JSON, and through the SQLite +
  gzipped-payload store, without losing content.

Tests that touch the on-disk store follow the pattern already used in
``test_properties.py::test_hypothesis_finds_counterexample``: the outer
pytest test function receives the (function-scoped, autouse) isolated
store fixture normally, and an inner ``@given``-decorated function runs
the Hypothesis sweep inside it. This avoids Hypothesis's
``function_scoped_fixture`` health check, which fires when a pytest
fixture is part of a ``@given``-decorated test function's own signature.
"""
from __future__ import annotations

import math

from hypothesis import given, settings, strategies as st

import claimtrail
from claimtrail import tracked
from claimtrail.assertions import Expectation, evaluate
from claimtrail.inputs import hash_file
from claimtrail.serialize import canonical_dumps, canonical_loads, hash_value
from claimtrail.store import ClaimtrailCollisionError, get_store


# ---------------------------------------------------------------------------
# Hashing: deterministic and content-sensitive
# ---------------------------------------------------------------------------

_json_leaf = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-10**9, max_value=10**9),
    st.text(max_size=20),
    st.floats(allow_nan=False, allow_infinity=False, width=32),
)
_json_value = st.recursive(
    _json_leaf,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=10), children, max_size=5),
    ),
    max_leaves=15,
)


@given(value=_json_value)
@settings(max_examples=100, deadline=None)
def test_hash_value_is_deterministic(value):
    """hash_value(x) called twice on the same value gives the same hash."""
    assert hash_value(value) == hash_value(value)


@given(a=_json_value, b=_json_value)
@settings(max_examples=200, deadline=None)
def test_hash_value_content_sensitive(a, b):
    """Different values hash differently (equal canonical JSON <=> equal hash)."""
    same_json = canonical_dumps(a) == canonical_dumps(b)
    same_hash = hash_value(a) == hash_value(b)
    assert same_json == same_hash


@given(content=st.binary(min_size=0, max_size=200))
@settings(max_examples=100, deadline=None)
def test_hash_file_is_deterministic(tmp_path_factory, content):
    d = tmp_path_factory.mktemp("hf")
    p = d / "f.bin"
    p.write_bytes(content)
    assert hash_file(p) == hash_file(p)


@given(a=st.binary(min_size=0, max_size=200), b=st.binary(min_size=0, max_size=200))
@settings(max_examples=100, deadline=None)
def test_hash_file_content_sensitive(tmp_path_factory, a, b):
    """Two files hash the same iff their bytes are identical."""
    d = tmp_path_factory.mktemp("hf")
    pa = d / "a.bin"
    pb = d / "b.bin"
    pa.write_bytes(a)
    pb.write_bytes(b)
    assert (hash_file(pa) == hash_file(pb)) == (a == b)


# ---------------------------------------------------------------------------
# Recording the same computation twice collapses to one row
# ---------------------------------------------------------------------------


def test_repeated_recording_collapses_to_one_row(isolated_store):
    """@tracked's id convention (function_name | input_hash | code_sha)
    means calling the same function with the same input twice must
    leave exactly one row in the store, for a range of inputs."""

    @given(x=st.integers(min_value=-1000, max_value=1000))
    @settings(max_examples=50, deadline=None)
    def _inner(x):
        # A unique function name per example keeps examples from
        # interfering with each other's row counts within this one
        # pytest test invocation (the store is not reset between
        # Hypothesis examples, only between pytest tests).
        @tracked(name=f"double_x_{x}_{x}")
        def double(n):
            return 2 * n

        double(x)
        double(x)
        double(x)
        rows = claimtrail.find(function=f"double_x_{x}_{x}")
        assert len(rows) == 1, f"x={x}: repeated identical calls must collapse to one row"
        assert rows[0].output_hash == hash_value(2 * x)

    _inner()


# ---------------------------------------------------------------------------
# A body change without an input change raises the collision error
# ---------------------------------------------------------------------------


def _version_a(n):
    return n + 1


def _version_b(n):
    # A different body, same result for every input: output_hash alone
    # cannot distinguish this from version_a, so only a payload-level
    # (function_source) comparison catches the change.
    result = n
    result = result + 1
    return result


def test_body_change_raises_collision_for_any_input(isolated_store):
    """For any integer input, recording version_a then version_b under
    the same declared name must raise ClaimtrailCollisionError, and must
    never corrupt the row version_a already wrote."""

    @given(x=st.integers(min_value=-1000, max_value=1000))
    @settings(max_examples=50, deadline=None)
    def _inner(x):
        name = f"collision_probe_{x}"
        f_a = tracked(name=name)(_version_a)
        f_b = tracked(name=name)(_version_b)

        f_a(x)
        rows_before = claimtrail.find(function=name)
        assert len(rows_before) == 1
        old_id = rows_before[0].id
        old_payload = get_store().read_payload(old_id)

        raised = False
        try:
            f_b(x)
        except ClaimtrailCollisionError:
            raised = True
        assert raised, f"x={x}: a body change must raise ClaimtrailCollisionError"

        # The older row must be untouched: same count, same id, and its
        # payload must still read back exactly as it did before f_b ran.
        rows_after = claimtrail.find(function=name)
        assert len(rows_after) == 1
        assert rows_after[0].id == old_id
        assert get_store().read_payload(old_id) == old_payload

    _inner()


# ---------------------------------------------------------------------------
# Claim assertions: x ~= v +- tol
# ---------------------------------------------------------------------------


@given(
    v=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    tol=st.floats(min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False),
    frac=st.floats(min_value=0.0, max_value=0.999, allow_nan=False, allow_infinity=False),
    sign=st.sampled_from([-1.0, 1.0]),
)
@settings(max_examples=200, deadline=None)
def test_tolerance_assertion_accepts_within_tolerance(v, tol, frac, sign):
    """actual within [v - tol, v + tol] must satisfy `path ~= v +- tol`."""
    actual = v + sign * frac * tol
    exp = Expectation(path="x", op="~=", value=v, tol=tol)
    result = evaluate(exp, {"x": actual})
    assert result.ok, (
        f"v={v!r} tol={tol!r} actual={actual!r} "
        f"(|actual - v| = {abs(actual - v)!r}) should be inside tolerance"
    )


@given(
    v=st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
    tol=st.floats(min_value=1e-3, max_value=1e6, allow_nan=False, allow_infinity=False),
    excess=st.floats(min_value=1e-3, max_value=1e3, allow_nan=False, allow_infinity=False),
    sign=st.sampled_from([-1.0, 1.0]),
)
@settings(max_examples=200, deadline=None)
def test_tolerance_assertion_rejects_outside_tolerance(v, tol, excess, sign):
    """actual strictly outside [v - tol, v + tol] must fail `path ~= v +- tol`.

    ``excess`` is a relative overshoot (tol * (1 + excess)) plus an
    absolute floor, so the margin past the tolerance boundary is always
    large enough to survive float rounding at the scale of ``v`` and
    ``tol`` - a boundary-adjacent overshoot would make this test flaky
    for reasons that have nothing to do with the invariant being
    checked.
    """
    margin = tol * excess + max(abs(v), 1.0) * 1e-9 + 1e-9
    actual = v + sign * (tol + margin)
    assert abs(actual - v) > tol
    exp = Expectation(path="x", op="~=", value=v, tol=tol)
    result = evaluate(exp, {"x": actual})
    assert not result.ok, (
        f"v={v!r} tol={tol!r} actual={actual!r} "
        f"(|actual - v| = {abs(actual - v)!r}) should be outside tolerance"
    )


# ---------------------------------------------------------------------------
# Round-trip through canonical JSON, and through the SQLite/JSON store
# ---------------------------------------------------------------------------


@given(value=_json_value)
@settings(max_examples=100, deadline=None)
def test_canonical_json_round_trip_preserves_content(value):
    assert canonical_loads(canonical_dumps(value)) == value


def test_store_round_trip_preserves_payload_content(isolated_store):
    """A payload written via Store.write_payload and read back via
    Store.read_payload must compare equal to the original, for a range
    of generated JSON-safe payload shapes."""

    @given(value=_json_value)
    @settings(max_examples=100, deadline=None)
    def _inner(value):
        store = get_store()
        payload = {"id": "roundtrip-probe", "result": value, "args": [], "kwargs": {}}
        store.write_payload("roundtrip-probe", payload)
        read_back = store.read_payload("roundtrip-probe")
        assert read_back == payload

    _inner()


@given(
    value=st.floats(allow_nan=True, allow_infinity=True),
)
@settings(max_examples=20, deadline=None)
def test_canonical_json_round_trips_nan_and_infinity(value):
    """canonical_dumps/canonical_loads round-trip NaN and +-inf exactly
    via the {"__qprov_type__": "float", ...} tag, unlike plain
    json.dumps which raises or silently emits non-JSON tokens.
    """
    restored = canonical_loads(canonical_dumps(value))
    if math.isnan(value):
        assert math.isnan(restored)
    else:
        assert restored == value
