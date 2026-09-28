"""Claim recording and LaTeX export."""
from __future__ import annotations

import pytest

import claimtrail
from claimtrail import tracked
from claimtrail.claims import claim, export_latex
from claimtrail.store import ClaimtrailCollisionError, get_store


@pytest.fixture
def computation_id():
    @tracked(tags={"constant": "pi"})
    def f(N):
        return [k**2 for k in range(N)]
    f(10)
    return claimtrail.find()[0].id


def test_claim_with_link(computation_id):
    cid = claim(
        "The first nonzero coefficient of [pi]_q after q^45 is at q^46",
        computation_id=computation_id,
        value_numeric=46,
    )
    assert cid
    rec = get_store().get_claim(cid)
    assert rec.text.startswith("The first nonzero")
    assert rec.computation_id == computation_id
    assert rec.value_numeric == 46.0


def test_claim_unlinked():
    cid = claim("a qualitative observation")
    rec = get_store().get_claim(cid)
    assert rec.computation_id is None
    assert rec.value_numeric is None


def test_claim_rejects_empty_text():
    with pytest.raises(ValueError):
        claim("")


def test_export_latex_renders_fact_macros(computation_id):
    claim("R([sqrt2]_q) = 0.531213", computation_id=computation_id, value_numeric=0.531213)
    claim("c_45([pi]_q) = 0", computation_id=computation_id, value_numeric=0)
    out = export_latex()
    assert r"\fact{R([sqrt2]_q) = 0.531213}" in out
    assert r"\fact{c_45([pi]_q) = 0}" in out
    assert r"\provid{" + computation_id + "}" in out


def test_export_latex_writes_file(tmp_path, computation_id):
    claim("anything", computation_id=computation_id)
    target = tmp_path / "claims.tex"
    text = export_latex(output=str(target))
    assert target.is_file()
    assert target.read_text(encoding="utf-8") == text


def test_export_latex_filters_by_computation(computation_id):
    @tracked
    def g(N):
        return N * 3
    g(5)
    other = claimtrail.find(function="g")[0].id

    claim("about pi", computation_id=computation_id)
    claim("about g", computation_id=other)

    out = export_latex(computation_id=computation_id)
    assert "about pi" in out
    assert "about g" not in out


def test_latexify_brackets_multidigit_exponents():
    from claimtrail.claims import latexify
    assert latexify("$q^10$") == "$q^{10}$"
    assert latexify("$X^15 + q^123$") == "$X^{15} + q^{123}$"


def test_latexify_leaves_single_digit_exponents():
    from claimtrail.claims import latexify
    assert latexify("$q^2$") == "$q^2$"
    assert latexify("$X^3 + q^9$") == "$X^3 + q^9$"


def test_latexify_strips_sage_multiplication():
    from claimtrail.claims import latexify
    assert latexify("$1 - X + X*q + X*q^2 - X^2*q$") == "$1 - X + X q + X q^2 - X^2 q$"


def test_latexify_combined_real_validation_polynomial():
    """The shape of a typical MGO validation claim after latexify."""
    from claimtrail.claims import latexify
    raw = "$P(X,q) = 1 + q^2 - X + X*q^3 - X^2*q^2$"
    expected = "$P(X,q) = 1 + q^2 - X + X q^3 - X^2 q^2$"
    assert latexify(raw) == expected


def test_latexify_preserves_text_outside_math():
    from claimtrail.claims import latexify
    assert latexify("at bidegree $(d_X, d_q) = (6,50)$ no annihilator exists") == \
        "at bidegree $(d_X, d_q) = (6,50)$ no annihilator exists"


def test_latexify_is_idempotent():
    from claimtrail.claims import latexify
    s = "$1 - X + X*q^2 - X^2*q^15$"
    assert latexify(latexify(s)) == latexify(s)


def test_export_latex_applies_latexify():
    cid = claim(
        "$P(X,q) = 1 - X + X*q^2 - X^2*q^15$ is the recurrence",
        deterministic_id=True,
    )
    out = export_latex()
    # Sage-style asterisks gone
    assert "X*q" not in out
    # Multi-digit exponent bracketed
    assert "q^{15}" in out
    # Single-digit exponent untouched
    assert "q^2" in out


def test_latex_escape_passes_math_through():
    cid = claim("R([\\sqrt{2}]_q) = 0.531213", value_numeric=0.531213)
    out = export_latex()
    # math content must survive verbatim
    assert "\\sqrt{2}" in out
    # but lone % gets escaped
    cid2 = claim("growth rate is 50% per N", value_numeric=50)
    out2 = export_latex()
    assert "50\\%" in out2


# ---------------------------------------------------------------------------
# claim_id collisions: default is a safety net, force=True is the deliberate
# escape valve. See INTEGRATION.md's "Claim-id collisions and reviewed
# replacement" section, which this pins.
# ---------------------------------------------------------------------------


def test_notes_are_persisted():
    """notes was not asserted by any existing test, so a mutant that
    dropped it (notes=None) on the way into the Claim record survived."""
    cid = claim("a claim with notes", value_numeric=1, notes="context for a reviewer")
    rec = get_store().get_claim(cid)
    assert rec.notes == "context for a reviewer"


def test_deterministic_id_distinguishes_value_numeric():
    """deterministic_id derives the id from (text, computation_id,
    value_numeric); two calls with the same text but different
    value_numeric must get different ids, not collapse onto one."""
    id_a = claim("same text", deterministic_id=True, value_numeric=1)
    id_b = claim("same text", deterministic_id=True, value_numeric=2)
    assert id_a != id_b


def test_deterministic_id_distinguishes_computation_id(computation_id):
    id_a = claim("same text", deterministic_id=True, computation_id=computation_id)
    id_b = claim("same text", deterministic_id=True, computation_id=None)
    assert id_a != id_b


def test_deterministic_id_reruns_collapse_to_one_row():
    """The whole point of deterministic_id: an unchanged rerun is a
    no-op, not a new row."""
    claim("stable text", deterministic_id=True, value_numeric=5)
    claim("stable text", deterministic_id=True, value_numeric=5)
    claim("stable text", deterministic_id=True, value_numeric=5)
    out = export_latex()
    assert out.count("stable text") == 1


def test_default_claim_id_is_random_per_call():
    """Without claim_id or deterministic_id, two calls with identical
    text must NOT collapse to the same id (a mutant flipped the
    deterministic_id default to True, which would collapse them)."""
    id_a = claim("identical text", value_numeric=1)
    id_b = claim("identical text", value_numeric=1)
    assert id_a != id_b


def test_reregistering_same_claim_id_with_identical_content_is_a_noop():
    claim("stable text", claim_id="stable_claim", value_numeric=1)
    claim("stable text", claim_id="stable_claim", value_numeric=1)
    rec = get_store().get_claim("stable_claim")
    assert rec.text == "stable text"
    assert rec.value_numeric == 1


def test_reregistering_same_claim_id_with_different_content_raises_by_default():
    claim("first version", claim_id="drifting_claim", value_numeric=1)
    with pytest.raises(ClaimtrailCollisionError):
        claim("second version", claim_id="drifting_claim", value_numeric=2)
    # The original row must be untouched.
    rec = get_store().get_claim("drifting_claim")
    assert rec.text == "first version"
    assert rec.value_numeric == 1


def test_force_true_replaces_an_existing_claim_row(computation_id):
    claim("first version", claim_id="reviewed_claim", value_numeric=1)
    claim(
        "second version",
        claim_id="reviewed_claim",
        value_numeric=2,
        computation_id=computation_id,
        force=True,
    )
    rec = get_store().get_claim("reviewed_claim")
    assert rec.text == "second version"
    assert rec.value_numeric == 2
    assert rec.computation_id == computation_id


def test_back_attaching_a_staged_claim_needs_force():
    """The exact INTEGRATION.md workflow: stage unbacked, then back-attach
    a real computation_id under the same claim_id. computation_id is going
    from None to a real id, which is a content change, so this must raise
    without force=True and succeed with it."""

    @tracked(tags={"constant": "e"})
    def g(N):
        return N + 1
    g(5)
    comp_id = claimtrail.find()[0].id

    claim(
        "staged fact",
        tags={"paper": "p"},
        allow_unbacked=True,
        claim_id="staged_claim",
    )
    with pytest.raises(ClaimtrailCollisionError):
        claim(
            "staged fact",
            tags={"paper": "p"},
            computation_id=comp_id,
            claim_id="staged_claim",
        )
    claim(
        "staged fact",
        tags={"paper": "p"},
        computation_id=comp_id,
        claim_id="staged_claim",
        force=True,
    )
    rec = get_store().get_claim("staged_claim")
    assert rec.computation_id == comp_id
