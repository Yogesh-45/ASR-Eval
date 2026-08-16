"""Phase 6-7 (open-set): classify LLM-aligned gold/ASR entity pairs.

src/llm_entities.py returns one record per real-world entity, already aligned across gold
and ASR by the model (handles script/spelling variation itself -- e.g. "delhi" and
"दिल्ली" are one record, not two). This module only computes the severity classification
deterministically from that alignment, per the plan's Phase 5 guidance that the LLM should
extract facts (including cross-transcript identity, which needs world knowledge a string
match can't reproduce) but not judge correctness itself:

    both spans present, different script -> CORRECT (the model's alignment already
                                             confirmed same entity; Devanagari and Latin
                                             text share no characters, so a character-level
                                             fuzzy score is meaningless across that boundary
                                             and would misclassify every correct script
                                             variant -- e.g. "delhi" vs "दिल्ली" -- as
                                             CORRUPTED)
    both spans present, same script     -> CORRECT if a close textual match, else CORRUPTED
    gold_span only                       -> DROPPED
    asr_span only                        -> ADDED
    neither span present                 -> not a real event, dropped (the model sometimes
                                             echoes a hinted entity from the gazetteer list
                                             even when it appears in neither transcript)

Before any of the above, each span is verified against the actual transcript text
(_verify_span): the model is asked to copy spans verbatim but sometimes doesn't -- e.g. it
once returned gold_span="गurgaon" (one stray Devanagari character glued onto an otherwise
Latin word) when gold literally just says "gurgaon". A single stray character like that
also fools a naive "does this span contain Devanagari" check into treating a same-script
pair as cross-script or vice versa. _verify_span corrects such spans back to the real
substring of the transcript (or drops them if unrecoverable), and script comparison uses a
majority vote over the span's alphabetic characters rather than "contains any," so one
stray character can't flip the classification either way.

A whole-span majority vote is still the wrong unit for a *multi-word* entity that
code-switches word-by-word rather than as a whole mention -- e.g. gold "Fruitz Litchi"
transcribed as "fruits लीची" (first word Latin, second word Devanagari). Character-counting
the whole span votes "latin" for both sides (more Latin letters than Devanagari letters), so
it falls into the same-script branch and runs a single character-level fuzz.ratio across a
script boundary -- exactly the "meaningless" comparison the cross-script branch above exists
to avoid, and it tanks the score even though every word is individually a correct
transliteration. _token_script_match() re-does the comparison per aligned token instead: a
token pair that differs in script is scored 100 (can't be compared char-wise, and the model's
overall span alignment already established these are the same entity), a token pair in the
same script is scored by fuzz.ratio as before. Falls back to a single whole-span fuzz.ratio
when the two spans don't have the same token count, since positional pairing isn't reliable
once a word has been added or dropped.
"""

from rapidfuzz import fuzz

from src.normalization import ngram_windows, normalize_text, tokenize

ASR_MATCH_THRESHOLD = 82  # aligned spans this close: ASR preserved the entity as CORRECT
# A CORRUPTED mention this close to CORRECT still counts as recoverable -- kept just below
# ASR_MATCH_THRESHOLD so the concept stays technically distinct from CORRECT (a hairline-close
# corruption could still qualify), but NOT set to the 65 originally used for a looser
# "a human could still guess it" reading. At 65, every CORRUPTED entity this project's
# correction layer can safely fix (seven up/seven a=80.0, seven up/seven of=75.0) was already
# above the bar before any correction ran, so `recoverable` could never flip False->True from
# a real fix -- the two real Step 3 corrections only ever showed up as `fixed_to_exact_correct`,
# never as `recovered`, even though the fix demonstrably worked. Raised to require an
# almost-exact match instead: a downstream order-fulfillment system needs the SKU name to
# actually match its catalog, not just be human-guessable, so "recoverable" now tracks whether
# the call's goal survives automatically -- and Step 3's real gain becomes visible on this
# metric instead of hiding behind it. See docs/challenges_and_decisions.md (2026-08-16 entry).
RECOVERABLE_THRESHOLD = 81
SPAN_VERIFY_THRESHOLD = 70  # a recovered span below this fuzzy score is treated as unverifiable

_DEVANAGARI_RANGE = ("ऀ", "ॿ")


def _dominant_script(text: str) -> str:
    """Majority-vote script over the alphabetic characters in text. A single stray
    character (extraction noise) shouldn't flip a whole span's script classification."""

    lo, hi = _DEVANAGARI_RANGE
    dev_count = sum(1 for ch in text if lo <= ch <= hi)
    latin_count = sum(1 for ch in text if ch.isalpha() and not (lo <= ch <= hi))
    return "devanagari" if dev_count > latin_count else "latin"


def _verify_span(span, transcript: str):
    """Confirm `span` genuinely appears in `transcript`; if the model garbled the span
    text itself, recover the real substring via fuzzy n-gram search, or drop it (return
    None) if even the best match is too weak to trust."""

    if not span:
        return None

    norm_span = normalize_text(span)
    norm_transcript = normalize_text(transcript)
    if norm_span and norm_span in norm_transcript:
        return span

    span_tokens = tokenize(span)
    text_tokens = tokenize(transcript)
    if not span_tokens or not text_tokens:
        return None

    windows = ngram_windows(text_tokens, max_n=max(1, min(3, len(span_tokens))))
    best_score, best_window = 0.0, None
    for window in windows:
        score = fuzz.ratio(norm_span, window)
        if score > best_score:
            best_score, best_window = score, window

    return best_window if best_score >= SPAN_VERIFY_THRESHOLD else None


def _token_script_match(gold_span: str, asr_span: str):
    """Score a same-dominant-script span pair token-by-token instead of as one string,
    so a word-by-word code-switch inside a multi-word entity (one word Latin, the next
    Devanagari) doesn't get penalized by a meaningless cross-script character comparison.

    Returns (score, any_cross_script_token). Falls back to a plain whole-span fuzz.ratio
    (and any_cross_script_token=False) whenever there's no actual cross-script token pair
    to fix -- either because the spans don't tokenize to the same length (positional
    pairing isn't reliable once a word was added or dropped) or because every token pair
    already shares a script, in which case the whole-span comparison is not just fine but
    *better calibrated*: fuzz.ratio weights each token's contribution by character length,
    while a naive per-token mean would weight "seven"/"seven" the same as a mismatched
    single-letter word like "a" vs "up", tanking scores for ordinary (non-script-related)
    corruptions that were previously classified correctly."""

    gold_tokens = tokenize(gold_span)
    asr_tokens = tokenize(asr_span)

    same_script_fallback = fuzz.ratio(normalize_text(gold_span), normalize_text(asr_span))

    if not gold_tokens or len(gold_tokens) != len(asr_tokens):
        return same_script_fallback, False

    cross_script_pairs = [
        _dominant_script(g) != _dominant_script(a) for g, a in zip(gold_tokens, asr_tokens)
    ]
    if not any(cross_script_pairs):
        return same_script_fallback, False

    total_weight, weighted_score = 0.0, 0.0
    for (gold_tok, asr_tok), is_cross_script in zip(zip(gold_tokens, asr_tokens), cross_script_pairs):
        weight = max(len(gold_tok), len(asr_tok))
        token_score = 100.0 if is_cross_script else fuzz.ratio(gold_tok, asr_tok)
        weighted_score += token_score * weight
        total_weight += weight

    return (weighted_score / total_weight if total_weight else 0.0), True


def detect_entity_events_llm(call_id: str, use_case: str, gold: str, asr: str, aligned_entities: list) -> list:
    events = []

    for e in aligned_entities:
        gold_span = _verify_span(e.get("gold_span"), gold)
        asr_span = _verify_span(e.get("asr_span"), asr)

        if not gold_span and not asr_span:
            continue  # phantom hint echo, or an unverifiable hallucinated span on both sides

        script_variant = False

        if gold_span and asr_span:
            if _dominant_script(gold_span) != _dominant_script(asr_span):
                status = "CORRECT"
                gold_score, asr_score = 100.0, 100.0
                script_variant = True
            else:
                score, any_cross_script_token = _token_script_match(gold_span, asr_span)
                status = "CORRECT" if score >= ASR_MATCH_THRESHOLD else "CORRUPTED"
                gold_score, asr_score = 100.0, round(score, 1)
                # Only a real "gold vs ASR script variant," not an ASR error, when the
                # token-level script switch is what made the match come out CORRECT.
                script_variant = any_cross_script_token and status == "CORRECT"
        elif gold_span and not asr_span:
            status = "DROPPED"
            gold_score, asr_score = 100.0, 0.0
        else:  # asr_span and not gold_span
            status = "ADDED"
            gold_score, asr_score = 0.0, 100.0

        recoverable = status == "CORRECT" or (status == "CORRUPTED" and asr_score >= RECOVERABLE_THRESHOLD)

        events.append(
            {
                "call_id": call_id,
                "use_case": use_case,
                "canonical_entity": e["canonical"],
                "entity_type": e["type"],
                "status": status,
                "gold_score": gold_score,
                "gold_matched_span": gold_span,
                "asr_score": asr_score,
                "asr_matched_span": asr_span,
                "recoverable": recoverable,
                "script_variant": script_variant,
            }
        )

    return events
