"""Regression tests for src/hallucination.py's impact metrics: quantity_preserved (the
"...or quantity is still correct" half of the assignment's Step 2 ask for short utterances,
missing entirely until this test file's companion change added it) and polarity_preserved's
subset-not-overlap fix (a call can genuinely say both "yes" and "no"; losing one while keeping
the other is a real content loss that a plain set-intersection check missed). These tests exist
so a future change to either function can't silently regress back to a looser check."""

from pathlib import Path

import pandas as pd

from src.hallucination import detect_hallucination

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_quantity_preserved_on_real_assignment_example():
    """Call 33720198 IS the assignment's own worked example: gold "yes हिंदी forty five
    percent" -> ASR "yes हिंदी english yeah yeah yes yes forty five percent". The padding is
    real (hallucination_candidate must be True), but "forty five percent" survives verbatim,
    so quantity_preserved must be True -- this is the exact case the metric exists to catch."""

    df = pd.read_excel(REPO_ROOT / "data" / "dataset.xlsx")
    row = df[df["id"] == "33720198-f1f7-498b-915b-e836c5d61c5c"].iloc[0]

    result = detect_hallucination(row["id"], row["gold_transcript"], row["asr_output"])

    assert result["hallucination_candidate"] is True
    assert result["gold_quantities"] == "forty five percent"
    assert result["missing_quantities"] is None
    assert result["quantity_preserved"] is True


def test_quantity_not_preserved_when_the_value_actually_changes():
    """A wrong quantity ("twenty percent" instead of "forty five percent") shares no
    individual number word with the real one, so even a naive per-word overlap check would
    correctly reject it -- included as the simple baseline case."""

    result = detect_hallucination(
        "synthetic", "yes hindi forty five percent", "yes hindi yeah yeah twenty percent"
    )
    assert result["quantity_preserved"] is False
    assert result["missing_quantities"] == "forty five percent"


def test_quantity_not_preserved_when_only_some_number_words_overlap():
    """The harder case _quantity_phrases/_contains_subsequence is specifically built for:
    gold's quantity phrase and the ASR's wrong one SHARE a number word ("five" appears in both
    "forty five percent" and "twenty five percent"), which a naive set-overlap check (the way
    _polarity() works) would wrongly call "preserved" since the words individually match. The
    exact-contiguous-phrase requirement must still reject it, because it's a materially
    different, wrong quantity, not the same one."""

    result = detect_hallucination(
        "synthetic", "yes hindi forty five percent", "yes hindi yeah yeah twenty five percent"
    )
    assert result["quantity_preserved"] is False
    assert result["missing_quantities"] == "forty five percent"


def test_no_gold_quantity_is_vacuously_preserved():
    """No quantity claim in gold at all -> nothing to lose, same "empty set is a subset of
    anything" vacuous-true logic polarity_preserved uses for calls with no yes/no signal."""

    result = detect_hallucination("synthetic", "please stay on the line", "please stay on the line thanks")
    assert result["gold_quantities"] is None
    assert result["quantity_preserved"] is True


def test_polarity_not_preserved_when_one_of_two_gold_signals_is_dropped():
    """Real call b2a6f783: gold "हाँ जी हाँ जी क्या चीज का order {noise} नहीं नहीं" (yes yes
    ... no no) states BOTH an affirmative and a negative; ASR completely drops the trailing
    "नहीं नहीं", so asr_polarity is affirmative-only. A plain set-intersection check
    ({"affirmative"} & {"affirmative","negative"} is non-empty) would wrongly call this
    preserved -- gold_polarity must be a SUBSET of asr_polarity, not just overlap with it,
    since losing one of two genuinely distinct signals is a real content loss."""

    df = pd.read_excel(REPO_ROOT / "data" / "dataset.xlsx")
    row = df[df["id"] == "b2a6f783-90f7-4057-9d4c-ca589bd95034"].iloc[0]

    result = detect_hallucination(row["id"], row["gold_transcript"], row["asr_output"])

    assert result["gold_polarity"] == "affirmative,negative"
    assert result["asr_polarity"] == "affirmative"
    assert result["polarity_preserved"] is False


def test_polarity_preserved_when_both_gold_signals_survive():
    """Sanity check the fix isn't overly strict: when ASR keeps both categories gold has,
    it must still count as preserved (real call e6a45e58, gold and ASR both
    affirmative+negative)."""

    df = pd.read_excel(REPO_ROOT / "data" / "dataset.xlsx")
    row = df[df["id"] == "e6a45e58-9fec-4691-8f2d-3b2ba5ad45d8"].iloc[0]

    result = detect_hallucination(row["id"], row["gold_transcript"], row["asr_output"])

    assert result["gold_polarity"] == "affirmative,negative"
    assert result["polarity_preserved"] is True
