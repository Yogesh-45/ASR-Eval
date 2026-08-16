"""Phase 9-10: short-utterance over-transcription / hallucination detection.

The dataset has no turn-level timestamps, so we cannot isolate individual
"short utterances" inside a call. We approximate the failure mode two ways
instead, matching the two concrete patterns seen in this dataset:

  1. Whole-call proxy: many calls in this set ARE a single short turn
     (e.g. "hello telugu telugu"). For those, a short gold length plus a
     much longer, low-overlap ASR output is exactly the described failure.
  2. Local repetition burst: in longer, multi-turn calls, hallucination shows
     up as a run of near-duplicate tokens the ASR inserted with no gold
     counterpart (e.g. gold "haan haan ji nahi kya" -> ASR "haan nahi haan
     neetiya neetiya neetiya neetiya neetiya neetiya"). This is detected by
     looking for a token (or 2-token cycle) repeated back-to-back more times
     than gold ever uses it -- NOT by raw repetition density. These are real
     voice-agent calls where genuine backchannel repetition ("yes yes yes",
     "ji ji ji") is common on both sides, and gold has it too; a density-only
     check (e.g. 1 - unique/total in a window) flags those constantly. Tying
     the check to gold support keeps it specific to repetition ASR added
     on its own.

Thresholds are heuristic (no labeled set to tune against yet); see README.
"""

from collections import Counter

from src.normalization import NUMBER_AND_QUANTITY_WORDS, normalize_text

SHORT_UTTERANCE_TOKENS = 6
LENGTH_RATIO_THRESHOLD = 1.5
INSERTION_RATE_THRESHOLD = 0.3
MIN_RUN_LENGTH = 3  # same token/cycle repeated back-to-back at least this many times
REPETITION_RATIO_THRESHOLD = 0.15  # excess (unjustified) repeated tokens / total asr tokens

AFFIRMATIVE = {"yes", "yeah", "yaa", "ya", "haan", "हाँ", "हां", "जी", "ji"}
NEGATIVE = {"no", "nope", "nahi", "नहीं", "na"}


def _insertion_rate(gold_tokens: list, asr_tokens: list) -> float:
    """Share of ASR tokens that do not appear in gold at all (a cheap proxy for
    alignment-based insertions, robust to reordering within a call)."""

    if not asr_tokens:
        return 0.0
    gold_counts = {}
    for t in gold_tokens:
        gold_counts[t] = gold_counts.get(t, 0) + 1

    remaining = dict(gold_counts)
    inserted = 0
    for t in asr_tokens:
        if remaining.get(t, 0) > 0:
            remaining[t] -= 1
        else:
            inserted += 1
    return inserted / len(asr_tokens)


def _consecutive_runs(tokens: list, n: int) -> list:
    """Runs of the same n-gram repeated back-to-back with no gap, e.g. for
    n=1: token[i] == token[i+1] == ...; for n=2: an A-B-A-B... cycle."""

    runs = []
    i = 0
    total = len(tokens)
    while i + n <= total:
        gram = tuple(tokens[i : i + n])
        cycles = 1
        j = i + n
        while j + n <= total and tuple(tokens[j : j + n]) == gram:
            cycles += 1
            j += n
        if cycles >= 2:
            runs.append({"gram": gram, "cycles": cycles, "tokens_covered": cycles * n, "start": i})
        i = j if cycles >= 2 else i + 1
    return runs


def _unjustified_repetition(gold_tokens: list, asr_tokens: list) -> dict:
    """Find ASR runs (unigram or bigram cycles) repeated more times than gold
    ever repeats that same gram, and report the excess as a share of the ASR
    length. Genuine backchannel repetition present in gold (e.g. gold also
    says "yes yes yes") is not counted -- only the surplus ASR adds."""

    gold_gram_counts = {1: Counter(), 2: Counter()}
    for n in (1, 2):
        for run in _consecutive_runs(gold_tokens, n):
            gold_gram_counts[n][run["gram"]] = max(gold_gram_counts[n][run["gram"]], run["cycles"])

    worst_excess_tokens = 0
    worst_run = None
    for n in (1, 2):
        for run in _consecutive_runs(asr_tokens, n):
            if run["cycles"] < MIN_RUN_LENGTH:
                continue
            justified_cycles = gold_gram_counts[n].get(run["gram"], 0)
            excess_cycles = max(0, run["cycles"] - justified_cycles)
            excess_tokens = excess_cycles * n
            if excess_tokens > worst_excess_tokens:
                worst_excess_tokens = excess_tokens
                worst_run = {"gram": " ".join(run["gram"]), "cycles": run["cycles"], "excess_cycles": excess_cycles}

    ratio = worst_excess_tokens / len(asr_tokens) if asr_tokens else 0.0
    return {"ratio": ratio, "run": worst_run}


def _polarity(tokens: list) -> set:
    present = set()
    token_set = set(tokens)
    if token_set & AFFIRMATIVE:
        present.add("affirmative")
    if token_set & NEGATIVE:
        present.add("negative")
    return present


def _quantity_phrases(tokens: list) -> list:
    """Maximal runs of consecutive number/unit words, e.g. ["forty", "five", "percent"]
    from "...yes हिंदी forty five percent" -- these are the quantity expressions whose
    exact value this impact metric checks for, not just "is there some number nearby"."""

    phrases, current = [], []
    for t in tokens:
        if t in NUMBER_AND_QUANTITY_WORDS:
            current.append(t)
        else:
            if current:
                phrases.append(tuple(current))
            current = []
    if current:
        phrases.append(tuple(current))
    return phrases


def _contains_subsequence(haystack: list, needle: tuple) -> bool:
    n = len(needle)
    return any(tuple(haystack[i : i + n]) == needle for i in range(len(haystack) - n + 1))


def _quantity_preservation(gold_tokens: list, asr_tokens: list):
    """Impact metric for the quantity half of this failure mode ("is the yes/no OR
    QUANTITY still correct"): for each distinct quantity phrase in gold (e.g. "forty
    five percent"), is that exact value still reproduced verbatim somewhere in the ASR
    output, despite any padding/hallucinated content around it? Requiring the full
    phrase (not just "some number word overlaps") matters because a wrong value
    ("forty five" replaced by "twenty five") shares individual number words with the
    right one but is not the same quantity -- a set-overlap check the way _polarity()
    does would wrongly call that "preserved"."""

    gold_phrases = _quantity_phrases(gold_tokens)
    if not gold_phrases:
        return True, gold_phrases, []
    missing = [p for p in gold_phrases if not _contains_subsequence(asr_tokens, p)]
    return (len(missing) == 0), gold_phrases, missing


def detect_hallucination(call_id: str, gold: str, asr: str) -> dict:
    gold_tokens = normalize_text(gold).split()
    asr_tokens = normalize_text(asr).split()

    gold_n, asr_n = len(gold_tokens), len(asr_tokens)
    length_ratio = asr_n / gold_n if gold_n else float("inf") if asr_n else 0.0
    insertion_rate = _insertion_rate(gold_tokens, asr_tokens)
    repetition = _unjustified_repetition(gold_tokens, asr_tokens)
    repetition_ratio = repetition["ratio"]

    short_call_hallucination = (
        gold_n > 0
        and gold_n <= SHORT_UTTERANCE_TOKENS
        and length_ratio >= LENGTH_RATIO_THRESHOLD
        and insertion_rate >= INSERTION_RATE_THRESHOLD
    )
    repetition_burst = repetition_ratio >= REPETITION_RATIO_THRESHOLD
    is_candidate = short_call_hallucination or repetition_burst

    gold_polarity = _polarity(gold_tokens)
    asr_polarity = _polarity(asr_tokens)
    # Impact metric for this failure mode, half 1: is the yes/no still recoverable
    # from the ASR output despite the padding/hallucinated content around it? Requires
    # EVERY polarity category present in gold to still be present in ASR (subset, not
    # overlap) -- a call can genuinely say both "yes" and "no" (e.g. gold "हाँ जी हाँ जी
    # ... नहीं नहीं", call b2a6f783), and if ASR drops the "no" entirely, the intersection
    # ({"affirmative"} & {"affirmative","negative"}) is still non-empty, so a plain overlap
    # check would wrongly call that "preserved" even though a real, distinct signal (the
    # negative) was completely lost.
    polarity_preserved = gold_polarity.issubset(asr_polarity)

    # Impact metric for this failure mode, half 2 (the assignment's "...or quantity is
    # still correct"): e.g. gold "yes हिंदी forty five percent" -> ASR "yes हिंदी english
    # yeah yeah yes yes forty five percent" (call 33720198) -- the padding is real, but
    # "forty five percent" survives verbatim, so the quantity this utterance was actually
    # reporting is still usable downstream.
    quantity_preserved, gold_quantities, missing_quantities = _quantity_preservation(gold_tokens, asr_tokens)

    return {
        "call_id": call_id,
        "gold_token_count": gold_n,
        "asr_token_count": asr_n,
        "length_ratio": round(length_ratio, 2) if length_ratio != float("inf") else None,
        "insertion_rate": round(insertion_rate, 3),
        "repetition_ratio": round(repetition_ratio, 3),
        "short_call_hallucination": short_call_hallucination,
        "repetition_burst": repetition_burst,
        "repetition_run": repetition["run"]["gram"] if repetition["run"] else None,
        "hallucination_candidate": is_candidate,
        "gold_polarity": ",".join(sorted(gold_polarity)) or None,
        "asr_polarity": ",".join(sorted(asr_polarity)) or None,
        "polarity_preserved": polarity_preserved,
        "gold_quantities": ",".join(" ".join(p) for p in gold_quantities) or None,
        "missing_quantities": ",".join(" ".join(p) for p in missing_quantities) or None,
        "quantity_preserved": quantity_preserved,
    }
