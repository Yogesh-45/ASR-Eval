"""Step 3 (Improve): a text-only post-ASR correction layer for the proper-noun
failure mode, and the deterministic scoring used to prove it helps.

Scope, decided from what Step 1/2 measurement actually showed (see
docs/challenges_and_decisions.md and README.md): of the 16 non-recoverable
entity events, 10 are DROPPED -- the entity is simply absent from the ASR
text, not garbled -- and a text-only layer cannot correct a word that was
never transcribed. This module only targets the CORRUPTED bucket: entities
that ARE present in the ASR output, just misspelled/mis-heard, which is a
genuine rewrite-the-text problem.

Two hard rules, both because this must reflect a realistic production setup:
  - Correction runs on ASR text alone. No gold transcript is available at
    inference time, so nothing here may read `gold_transcript`.
  - The correction vocabulary for a call must never be mined from that call's
    own gold transcript. build_correction_vocabulary() only accepts a
    training-split call_id set to mine aliases from, so held-out calls are
    genuinely unseen by the corrector, not just unseen by the LLM.
"""

from dataclasses import dataclass

from rapidfuzz import fuzz

from src.gazetteer import GazetteerEntity
from src.normalization import NUMBER_AND_QUANTITY_WORDS, ngram_windows, normalize_text, tokenize

# A candidate match scoring in [LOW_SCORE, HIGH_SCORE) is treated as "this is
# probably a garbled mention of this alias" and gets rewritten.
#
# MIN_ALIAS_TOKENS=2 (below) means this only ever fires for multi-word
# aliases ("seven up", "tetra pack", "mountain dew"). This was not the
# original design -- an earlier version also matched single-word aliases
# ("dew", "slice", "tetra", "pepsi") and, auditing every candidate correction
# across all 49 calls, found it caught real corruptions (due/dew=66.7,
# life/slice=66.7, test/tetra=66.7) but at THE SAME fuzzy-match score also
# rewrote real, unrelated words that happened to collide: "size" (in "pack
# size hai", gold-confirmed as literally "size") into Slice, "line" (in "aap
# line par hain", i.e. "you're on the line") into Slice, "liche" (a
# mishearing of "litchi"/lychee) into Slice, and "testi" (in "testi sub
# editor", unrelated) into Pepsi -- every one at a score statistically
# indistinguishable from the real catches. No threshold or context guard
# tried (quantity-neighbor check, number-word exclusion -- both still kept
# below for the bigram case) could separate the true from the false
# single-word matches; they are the same number by coincidence, not by
# signal. Rather than ship rules overfit to this exact 9-example audit
# sample, single-word aliases are excluded from correction entirely -- see
# docs/challenges_and_decisions.md for the full audit trail. Multi-word
# aliases don't have this problem: "seven of"/"seven up"=75.0 and "seven
# a"/"seven up"=80.0 are real, and no false positive was found for any
# bigram alias across the full-dataset audit once the two guards below were
# added.
#
# Revisited twice more, both attempts reverted -- see
# docs/challenges_and_decisions.md for the full evidence trail: phonetic/
# orthographic similarity (9 different metrics, none separated the real
# catches from the false positives either) and gating single-word matches on
# the existing quantity-neighbor guard (cuts the false-positive count from 4
# to 1, but that one remaining false positive -- "liche" (litchi) rewritten
# to "Slice" in call 8661511d -- fabricates a SKU that was never part of the
# order, a more severe failure than misspelling a real one, and one this
# module's own before/after entity tracking can't detect since it only
# checks entities already tracked for that call). MIN_ALIAS_TOKENS stays at
# 2 pending a fix for that detection gap, not because a better ratio wasn't
# found.
LOW_SCORE = 63
HIGH_SCORE = 90
MIN_ALIAS_TOKENS = 2

_DEVANAGARI_RANGE = ("ऀ", "ॿ")


def _is_latin_only(text: str) -> bool:
    lo, hi = _DEVANAGARI_RANGE
    return not any(lo <= ch <= hi for ch in text)


def _window_is_all_number_or_quantity_words(window_text: str) -> bool:
    """Reject e.g. "seven half" / "seven fifty" fuzzy-matching "Seven Up" --
    both are quantities (7.5, 750ml) that happen to start with the same
    number word the one number-prefixed product name in this catalog does,
    not a mishearing of the brand."""

    return all(t in NUMBER_AND_QUANTITY_WORDS for t in window_text.split())


def _has_quantity_neighbor(tokens: list, start: int, end: int) -> bool:
    """Require a number/unit word immediately before or after the candidate
    span, as corroborating evidence this is really an order/quantity mention
    and not an unrelated word that happens to fuzzy-match a short alias.
    Found necessary by auditing real corrections: "size" (in "pack size
    hai", gold-confirmed as literally the word "size") and "line" (in "aap
    line par hain", i.e. "you're on the line") both scored the same 66.7
    against "Slice" as the one genuine catch in this call ("life", sitting
    right next to "125 ml"/"एक सौ पच्चीस") -- fuzzy score alone can't tell
    them apart, but real product mentions in this dataset are always right
    next to a quantity and these two false positives weren't."""

    before = tokens[start - 1] if start > 0 else None
    after = tokens[end + 1] if end + 1 < len(tokens) else None
    return (before in NUMBER_AND_QUANTITY_WORDS) or (after in NUMBER_AND_QUANTITY_WORDS)


def build_correction_vocabulary(entity_events_df, train_call_ids: set, base_gazetteer: list) -> list:
    """Build the correction vocabulary: the closed catalog of canonical
    entities from the hand-curated gazetteer, but with each entity's alias
    list REPLACED by spellings actually attested in TRAINING-split gold text
    (never the held-out calls' gold, and never the gazetteer's own
    hand-curated misspelling list).

    Two decisions here, both made after running an early version over the
    full dataset and finding it corrupted unrelated calls (see
    docs/challenges_and_decisions.md):

    1. Canonicals are restricted to the existing gazetteer's closed catalog
       (9 beverage SKUs + 2 cities) -- NOT extended with every entity the LLM
       happened to find in training gold (e.g. `Pronto`, `WhatsApp`, `Woxen
       University`, `Silver Jewellery`, `Alibaba.com`). Those are one-off,
       call-specific names (a b2b call's particular client/product), not a
       recurring catalog -- e.g. mining "Silver Jewellery" from one b2b
       sales call and then applying it as a correction candidate to a
       DIFFERENT b2b sales call (which is about an unrelated business)
       corrupted "sir delivery" into "silver jewellery" there. Only the
       gazetteer's items are known, from manual review, to actually recur
       across many calls in the same use_case -- which is what makes
       cross-call correction sound in the first place.
    2. The gazetteer's own hand-curated aliases (`sevenup`, `7up`, `due`,
       `dwew`, `mirnda`, `slyce`, ...) are dropped in favour of forms
       actually seen in training gold text. Those hand-curated entries were
       written as *anticipated ASR-side misspellings* for the detector, not
       confirmed real spellings -- and several turned out to be dangerously
       short/generic as correction targets: `sevenup` (concatenated, no
       space) fuzzy-matches a bare digit word like "seven" in an unrelated
       phone-number or quantity reading well enough to wrongly rewrite it.
       Fuzzy matching (LOW_SCORE band) already catches nearby corruptions of
       the clean gold form without needing every anticipated misspelling
       listed explicitly.
    """

    train_events = entity_events_df[entity_events_df["call_id"].isin(train_call_ids)]

    mined_aliases = {}
    for _, row in train_events.iterrows():
        span = row.get("gold_matched_span")
        if not isinstance(span, str) or not span.strip():
            continue
        norm = normalize_text(span)
        if not norm or not _is_latin_only(norm):
            continue  # Devanagari/mixed-script gold text isn't usable by a Latin-text corrector
        mined_aliases.setdefault(row["canonical_entity"], set()).add(norm)

    vocabulary = []
    for e in base_gazetteer:
        aliases = mined_aliases.get(e.canonical, set())
        norm_canonical = normalize_text(e.canonical)
        if norm_canonical:
            aliases = aliases | {norm_canonical}
        if not aliases:
            continue  # never observed in training gold at all -- nothing safe to search for
        vocabulary.append(GazetteerEntity(canonical=e.canonical, type=e.type, use_cases=e.use_cases,
                                           aliases=sorted(aliases)))

    return vocabulary


def _windows_with_positions(tokens: list, max_n: int = 2) -> list:
    windows = [(t, i, i) for i, t in enumerate(tokens)]
    for n in range(2, max_n + 1):
        for i in range(len(tokens) - n + 1):
            windows.append((" ".join(tokens[i : i + n]), i, i + n - 1))
    return windows


def find_corrections(asr_text: str, use_case: str, vocabulary: list):
    """Find and apply high-confidence garbled-entity rewrites in `asr_text`.

    Returns (applied_corrections, corrected_text). Operates purely on
    normalized ASR tokens -- no gold access -- and only considers vocabulary
    entities scoped to `use_case`, exactly like the original gazetteer
    detector, to avoid e.g. rewriting "due" (as in "payment due") to
    "Mountain Dew" in a b2b sales call.

    Two guards, both added after an early version over-corrected badly on
    real data (see docs/challenges_and_decisions.md):
    1. A window is only compared against an alias of the SAME word count.
       Without this, a 1-word alias like "tetra" would also match 2-word
       windows like "tetra एक" ("tetra one") at a middling score, and
       "correcting" it would silently delete the neighbouring word "one" --
       inventing/removing content the ASR never actually got wrong.
    2. Any window that already scores >=HIGH_SCORE against an alias (i.e. is
       already a clean, correct mention) reserves its token span before
       lower-scoring candidates are considered, so a correct bigram like
       "seven up" can't have its two words separately "corrected" against
       unrelated unigram aliases ("seven"->"sevenup", "up"->"7up") once the
       bigram itself is already a perfect match.
    """

    tokens = tokenize(asr_text)
    if not tokens:
        return [], asr_text

    windows = _windows_with_positions(tokens, max_n=2)

    all_scored = []
    for entity in vocabulary:
        if not entity.applies_to(use_case):
            continue
        for alias in entity.aliases:
            if not _is_latin_only(alias):
                continue
            alias_len = len(alias.split())
            if alias_len < MIN_ALIAS_TOKENS:
                continue  # single-word aliases excluded -- see MIN_ALIAS_TOKENS note above
            for window_text, start, end in windows:
                if (end - start + 1) != alias_len:
                    continue
                if not _is_latin_only(window_text):
                    continue  # never force a code-switched Devanagari mention into Latin script
                score = fuzz.ratio(alias, window_text)
                if score < LOW_SCORE:
                    continue
                protect_only = score >= HIGH_SCORE
                if not protect_only:
                    # extra scrutiny only for imperfect matches actually being rewritten --
                    # a near-exact (protect_only) match doesn't need it.
                    if _window_is_all_number_or_quantity_words(window_text):
                        continue
                    if entity.type in {"product", "brand", "packaging"} and not _has_quantity_neighbor(
                        tokens, start, end
                    ):
                        continue
                all_scored.append(
                    {
                        "score": score,
                        "start": start,
                        "end": end,
                        "alias": alias,
                        "canonical": entity.canonical,
                        "original": window_text,
                        "protect_only": protect_only,
                    }
                )

    all_scored.sort(key=lambda c: (-c["score"], -(c["end"] - c["start"]), c["start"]))

    applied, used = [], set()
    for c in all_scored:
        span = set(range(c["start"], c["end"] + 1))
        if span & used:
            continue
        used |= span
        if not c["protect_only"]:
            applied.append(c)

    corrected_tokens = list(tokens)
    for c in sorted(applied, key=lambda c: -c["start"]):
        corrected_tokens[c["start"] : c["end"] + 1] = c["alias"].split()

    return applied, " ".join(corrected_tokens)


def score_gold_span_against_text(gold_span: str, text: str):
    """Best fuzzy-match score of `gold_span` (verbatim gold wording) against
    any window of `text`. Returns None if gold_span isn't usable (missing, or
    not Latin script -- this correction layer never touches Devanagari
    tokens, so a Devanagari gold mention's status is structurally unchanged
    by it and isn't worth rescoring).
    """

    if not isinstance(gold_span, str) or not gold_span.strip():
        return None
    norm_span = normalize_text(gold_span)
    if not norm_span or not _is_latin_only(norm_span):
        return None

    tokens = tokenize(text)
    if not tokens:
        return 0.0

    # No upper cap: gold spans can run long (e.g. a 9-token spelled-out numeric ID), and
    # capping this at a small max_n silently truncates the comparison to nonsense-length
    # windows, making an untouched entity look "corrupted" for a reason that has nothing to
    # do with the correction layer. Transcripts here are short enough (~50-250 tokens) that
    # this is still cheap.
    max_n = max(1, len(norm_span.split()))
    windows = ngram_windows(tokens, max_n=max_n)
    best_score = 0.0
    for window in windows:
        score = fuzz.ratio(norm_span, window)
        if score > best_score:
            best_score = score
    return best_score
