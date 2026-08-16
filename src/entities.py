"""Phase 6-7: proper-noun / product entity failure detection.

For every gazetteer entity applicable to a call's use_case, decide whether the
entity is mentioned in gold, and if so, whether the ASR preserved it:

    CORRECT   - ASR contains a confident match for the same entity
    CORRUPTED - ASR contains a garbled but recognizable variant
    DROPPED   - ASR contains nothing resembling the entity
    ADDED     - ASR mentions the entity but gold never did (hallucinated mention)

Thresholds below are heuristic starting points (explicitly flagged as such --
see Phase 10 of the plan). We have no human-labeled set to tune them against,
so they are chosen conservatively from manual inspection of this dataset and
should be the first thing revisited if a labeled set becomes available.
"""

from src.gazetteer import best_alias_match

GOLD_PRESENCE_THRESHOLD = 88  # confident the entity is genuinely mentioned in gold
ASR_MATCH_THRESHOLD = 82  # confident ASR preserved the entity
ASR_CORRUPT_THRESHOLD = 55  # something ASR-mangled is plausibly there
RECOVERABLE_THRESHOLD = 65  # a corrupted mention still recoverable by a human/downstream system


def _is_script_variant(gold_alias: str, asr_alias: str) -> bool:
    """True when gold and ASR matched via different-script aliases of the same
    entity (e.g. gold matched 'delhi', ASR matched 'दिल्ली'): a same-meaning,
    different-script pair that naive string comparison would flag as an error."""

    if gold_alias is None or asr_alias is None:
        return False
    gold_has_dev = any("ऀ" <= ch <= "ॿ" for ch in gold_alias)
    asr_has_dev = any("ऀ" <= ch <= "ॿ" for ch in asr_alias)
    return gold_has_dev != asr_has_dev


def detect_entity_events(call_id: str, use_case: str, gold: str, asr: str, gazetteer: list) -> list:
    events = []

    for entity in gazetteer:
        if not entity.applies_to(use_case):
            continue

        gold_match = best_alias_match(gold, entity)
        asr_match = best_alias_match(asr, entity)

        gold_present = gold_match["score"] >= GOLD_PRESENCE_THRESHOLD
        asr_present = asr_match["score"] >= ASR_MATCH_THRESHOLD

        if not gold_present and not asr_present:
            continue

        if gold_present and asr_present:
            status = "CORRECT"
        elif gold_present and asr_match["score"] >= ASR_CORRUPT_THRESHOLD:
            status = "CORRUPTED"
        elif gold_present:
            status = "DROPPED"
        else:
            status = "ADDED"

        recoverable = status == "CORRECT" or (
            status == "CORRUPTED" and asr_match["score"] >= RECOVERABLE_THRESHOLD
        )

        events.append(
            {
                "call_id": call_id,
                "use_case": use_case,
                "canonical_entity": entity.canonical,
                "entity_type": entity.type,
                "status": status,
                "gold_score": round(gold_match["score"], 1),
                "gold_matched_span": gold_match["matched_span"],
                "asr_score": round(asr_match["score"], 1),
                "asr_matched_span": asr_match["matched_span"],
                "recoverable": recoverable,
                "script_variant": status == "CORRECT"
                and _is_script_variant(gold_match["alias"], asr_match["alias"]),
            }
        )

    return events
