"""Regression tests for src/correction.py, locking in the hand-audited guards documented
there (MIN_ALIAS_TOKENS, the number-word-only exclusion, the quantity-neighbor requirement)
and the no-leakage contract of build_correction_vocabulary. Every case here is either a real
call from data/dataset.xlsx or a minimal string built to exercise one specific guard named in
src/correction.py's own docstrings -- see docs/challenges_and_decisions.md for the audit that
produced these guards in the first place. If a future change to LOW_SCORE/HIGH_SCORE/
MIN_ALIAS_TOKENS/the guard functions breaks one of these, that's a signal to re-read the audit
trail before changing the assertion, not to just update the expected value.
"""

from pathlib import Path

import pandas as pd
import pytest

from src.correction import (
    _has_quantity_neighbor,
    _window_is_all_number_or_quantity_words,
    build_correction_vocabulary,
    find_corrections,
)
from src.gazetteer import load_gazetteer
from src.normalization import tokenize

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def dataset():
    return pd.read_excel(REPO_ROOT / "data" / "dataset.xlsx")


@pytest.fixture(scope="module")
def entity_events():
    return pd.read_csv(REPO_ROOT / "reports" / "step1_entity_events.csv")


@pytest.fixture(scope="module")
def gazetteer():
    return load_gazetteer(str(REPO_ROOT / "configs" / "entity_gazetteer.yaml"))


@pytest.fixture(scope="module")
def full_vocabulary(entity_events, gazetteer, dataset):
    return build_correction_vocabulary(entity_events, set(dataset["id"]), gazetteer)


@pytest.mark.parametrize(
    "call_id,expected_original",
    [
        ("a8afca71-9dd8-464c-9ebf-04933f58e4a4", "seven a"),
        ("09c68660-7db4-4b5b-9a72-39a12f18c9bd", "seven of"),
    ],
)
def test_seven_up_correction_fires_even_when_call_is_held_out(dataset, entity_events, gazetteer, call_id, expected_original):
    """Both real, audited corrections in this dataset must keep firing, and must not depend
    on the call's own gold text being in the vocabulary -- the alias 'seven up' is mined from
    OTHER training calls, so excluding this call entirely still has to work."""

    train_ids = set(dataset["id"]) - {call_id}
    vocabulary = build_correction_vocabulary(entity_events, train_ids, gazetteer)
    row = dataset[dataset["id"] == call_id].iloc[0]

    applied, _ = find_corrections(row["asr_output"], row["use_case"], vocabulary)

    matches = [c for c in applied if c["canonical"] == "Seven Up"]
    assert len(matches) == 1
    assert matches[0]["original"] == expected_original
    assert matches[0]["alias"] == "seven up"


def test_single_word_alias_stays_uncorrected(dataset, full_vocabulary):
    """Call 09c68660 has a real, human-confirmable corruption ('due' for Mountain Dew's
    'dew', score 66.7) that MIN_ALIAS_TOKENS=2 deliberately leaves uncorrected -- single-word
    aliases were found to score identically to real false positives elsewhere in this dataset
    ('size'/'line' against Slice) with no threshold able to separate them. This test isn't
    asking for a fix; it's pinning the current, deliberate scope so a future change to
    MIN_ALIAS_TOKENS doesn't silently re-admit single-word matching without redoing that audit."""

    row = dataset[dataset["id"] == "09c68660-7db4-4b5b-9a72-39a12f18c9bd"].iloc[0]
    applied, _ = find_corrections(row["asr_output"], row["use_case"], full_vocabulary)

    assert "Mountain Dew" not in [c["canonical"] for c in applied]
    assert [c["canonical"] for c in applied] == ["Seven Up"]


def test_vocabulary_never_leaks_held_out_gold_text(dataset, entity_events, gazetteer):
    """dd0bb7ca is the only call whose gold text has 'Mirinda' spelled as 'mirinda orange'
    (every other call just has 'mirinda'). Excluding it from the training set must remove
    'mirinda orange' from the mined vocabulary -- the correction layer's core promise is that
    a held-out call's own gold text is never used to build the vocabulary applied to it."""

    held_out_id = "dd0bb7ca-015a-4bca-ac7f-42df781d3928"
    train_ids = set(dataset["id"]) - {held_out_id}

    vocab_excluding = build_correction_vocabulary(entity_events, train_ids, gazetteer)
    vocab_including = build_correction_vocabulary(entity_events, set(dataset["id"]), gazetteer)

    mirinda_excluding = next(e for e in vocab_excluding if e.canonical == "Mirinda")
    mirinda_including = next(e for e in vocab_including if e.canonical == "Mirinda")

    assert "mirinda orange" not in mirinda_excluding.aliases
    assert "mirinda orange" in mirinda_including.aliases


def test_number_word_only_window_is_never_corrected(full_vocabulary):
    """'seven half' and 'seven fifty' are quantities (7.5, 750ml) that happen to fuzzy-match
    'seven up' inside the correction band (66.7 and 63.2, both >= LOW_SCORE=63) purely because
    they share the leading number word -- not because they're a mishearing of the brand. Real
    calls in this dataset were the source of this guard; asserting on crafted strings here
    exercises the exact rule directly, independent of which real calls it originally caught."""

    for score_check_text in ["seven half", "seven fifty"]:
        assert _window_is_all_number_or_quantity_words(score_check_text)

    applied, _ = find_corrections(
        "please note the seven half is not what we ordered", "beverage order-taking", full_vocabulary
    )
    assert applied == []

    applied, _ = find_corrections(
        "the total comes to seven fifty for this order", "beverage order-taking", full_vocabulary
    )
    assert applied == []


def test_quantity_neighbor_required_for_product_corrections(full_vocabulary):
    """A garbled product/brand/packaging mention is only corrected when it sits next to a
    number or unit word, as corroborating evidence this is really an order/quantity mention --
    added after real false positives ('size' in "pack size hai", 'line' in "aap line par hain")
    scored identically to genuine catches with no other distinguishing signal. Same corrupted
    span, only the presence of a neighboring quantity word differs."""

    tokens_without_neighbor = tokenize("we need seven of please for the order")
    tokens_with_neighbor = tokenize("we need seven of two bottles for the order")
    assert not _has_quantity_neighbor(tokens_without_neighbor, 2, 3)
    assert _has_quantity_neighbor(tokens_with_neighbor, 2, 3)

    applied_without, _ = find_corrections(
        "we need seven of please for the order", "beverage order-taking", full_vocabulary
    )
    assert applied_without == []

    applied_with, _ = find_corrections(
        "we need seven of two bottles for the order", "beverage order-taking", full_vocabulary
    )
    assert [c["canonical"] for c in applied_with] == ["Seven Up"]
