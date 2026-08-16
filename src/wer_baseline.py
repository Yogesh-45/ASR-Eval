"""Phase 3: WER/CER as a diagnostic baseline only (never the business metric)."""

import jiwer

from src.normalization import normalize_text

_WORD_TRANSFORM = jiwer.Compose(
    [
        jiwer.RemoveMultipleSpaces(),
        jiwer.Strip(),
        jiwer.ReduceToListOfListOfWords(),
    ]
)


def compute_wer_cer(gold: str, asr: str) -> dict:
    gold_norm = normalize_text(gold)
    asr_norm = normalize_text(asr)

    if not gold_norm.strip():
        return {
            "wer": None,
            "cer": None,
            "substitutions": None,
            "insertions": None,
            "deletions": None,
            "hits": None,
        }

    word_out = jiwer.process_words(
        gold_norm, asr_norm, reference_transform=_WORD_TRANSFORM, hypothesis_transform=_WORD_TRANSFORM
    )
    cer_value = jiwer.cer(gold_norm, asr_norm)

    return {
        "wer": word_out.wer,
        "cer": cer_value,
        "substitutions": word_out.substitutions,
        "insertions": word_out.insertions,
        "deletions": word_out.deletions,
        "hits": word_out.hits,
    }
