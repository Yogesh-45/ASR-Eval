"""Entry point for the Improve step (assignment step 3): apply the text-only
proper-noun correction layer and measure its gain, on calls held out from
building it, plus how often it breaks an already-correct mention.

The held-out split is computed fresh each run from the current entity
classification (Step 1/2 pipeline), stratified so the small CORRUPTED-affected
stratum (4 calls) is represented in both the training and held-out sets, not
just the much larger unaffected stratum. See src/correction.py and
docs/challenges_and_decisions.md for why the correction only targets CORRUPTED
entities (not DROPPED) and why the vocabulary is built only from the training
split.

Usage:
    python run_improve.py --input data/dataset.xlsx --output reports/
"""

import argparse
import os
import random

import pandas as pd

from src.correction import build_correction_vocabulary, find_corrections, score_gold_span_against_text
from src.data_loader import load_dataset
from src.entities_llm import ASR_MATCH_THRESHOLD, RECOVERABLE_THRESHOLD, detect_entity_events_llm
from src.gazetteer import load_gazetteer
from src.llm_entities import extract_and_align_entities_llm

GAZETTEER_PATH = os.path.join(os.path.dirname(__file__), "configs", "entity_gazetteer.yaml")
SPLIT_SEED = 42
HOLD_OUT_FRACTION = 0.2


def build_baseline_events(df: pd.DataFrame, gazetteer: list) -> pd.DataFrame:
    """Re-run the Step 1/2 classification (BEFORE state) -- reuses the cached
    LLM extractions in data/llm_entity_cache.json, so this costs no new API
    calls as long as the dataset/model/hints haven't changed."""

    known_entity_names = sorted({e.canonical for e in gazetteer})
    all_events = []
    for _, row in df.iterrows():
        call_id, use_case = row["id"], row["use_case"]
        gold, asr = row["gold_transcript"], row["asr_output"]
        aligned = extract_and_align_entities_llm(call_id, gold, asr, known_entity_names)
        all_events.extend(detect_entity_events_llm(call_id, use_case, gold, asr, aligned))
    return pd.DataFrame(all_events)


def compute_split(events_df: pd.DataFrame, call_ids: list) -> tuple:
    """Stratified 80/20 split by whether a call has >=1 CORRUPTED entity --
    the only bucket this correction layer targets. Only 4/49 calls qualify,
    so a plain random split risks putting zero of them in the held-out set;
    stratifying guarantees at least one lands there. Fixed seed for a
    reproducible, documented split."""

    corrupted_calls = set(events_df[events_df["status"] == "CORRUPTED"]["call_id"])
    corrupted = [c for c in call_ids if c in corrupted_calls]
    clean = [c for c in call_ids if c not in corrupted_calls]

    rng = random.Random(SPLIT_SEED)
    rng.shuffle(corrupted)
    rng.shuffle(clean)

    n_hold_corrupted = max(1, round(len(corrupted) * HOLD_OUT_FRACTION))
    n_hold_clean = round(len(clean) * HOLD_OUT_FRACTION)

    held_out = set(corrupted[:n_hold_corrupted]) | set(clean[:n_hold_clean])
    train = set(call_ids) - held_out
    return train, held_out


def classify(score) -> tuple:
    if score >= ASR_MATCH_THRESHOLD:
        return "CORRECT", True
    if score >= RECOVERABLE_THRESHOLD:
        return "CORRUPTED", True
    return ("CORRUPTED" if score > 0 else "DROPPED"), False


def evaluate_split(events_df: pd.DataFrame, call_asr: dict, call_use_case: dict, call_ids: set,
                    vocabulary: list) -> pd.DataFrame:
    rows = []
    for call_id in call_ids:
        use_case = call_use_case[call_id]
        asr = call_asr[call_id]
        corrections, corrected_text = find_corrections(asr, use_case, vocabulary)
        corrected_canonicals = {c["canonical"] for c in corrections}

        call_events = events_df[events_df["call_id"] == call_id]
        for _, ev in call_events.iterrows():
            gold_span = ev["gold_matched_span"]
            before_status, before_recoverable = ev["status"], bool(ev["recoverable"])

            if ev["canonical_entity"] not in corrected_canonicals:
                # No correction touched this entity's text at all -- its status cannot
                # have changed. Rescoring it anyway (via a brute-force fuzzy search over
                # the whole transcript) is what caused the harness's own false positives
                # during development: e.g. Delhi/Gurgaon/Neha (gold Latin, ASR Devanagari
                # script-variant CORRECT matches) came back "CORRUPTED" with zero
                # corrections applied to their calls, because a bare fuzz.ratio search
                # doesn't know about the cross-script trust rule entities_llm.py uses, and
                # DROPPED entities (Woxen University, WhatsApp, Pronto, ...) came back
                # "recovered" because SOME window in a ~50-250 token transcript always
                # scores nonzero against any short alias by chance. Restricting rescoring
                # to only the entities a correction actually targeted removes both bugs at
                # the root instead of chasing further threshold tweaks.
                after_status, after_recoverable = before_status, before_recoverable
            else:
                after_score = score_gold_span_against_text(gold_span, corrected_text)
                if after_score is None:
                    after_status, after_recoverable = before_status, before_recoverable
                else:
                    after_status, after_recoverable = classify(after_score)

            rows.append(
                {
                    "call_id": call_id,
                    "use_case": use_case,
                    "canonical_entity": ev["canonical_entity"],
                    "before_status": before_status,
                    "before_recoverable": before_recoverable,
                    "after_status": after_status,
                    "after_recoverable": after_recoverable,
                    "recovered": (not before_recoverable) and after_recoverable,
                    "corrupted_by_correction": before_status == "CORRECT" and after_status != "CORRECT",
                    "fixed_to_exact_correct": before_status == "CORRUPTED" and after_status == "CORRECT",
                    "num_corrections_applied_to_call": len(corrections),
                }
            )
    return pd.DataFrame(rows)


def write_summary(output_dir: str, split_name: str, result_df: pd.DataFrame) -> str:
    total = len(result_df)
    recovered = int(result_df["recovered"].sum())
    corrupted_by_us = int(result_df["corrupted_by_correction"].sum())
    was_non_recoverable = int((~result_df["before_recoverable"]).sum())
    was_correct = int((result_df["before_status"] == "CORRECT").sum())
    dropped_before = result_df[result_df["before_status"] == "DROPPED"]
    dropped_recovered = int(dropped_before["recovered"].sum())
    fixed_to_exact = int(result_df["fixed_to_exact_correct"].sum())
    was_corrupted = int((result_df["before_status"] == "CORRUPTED").sum())

    lines = [f"### {split_name}", ""]
    lines.append(f"- Entity mentions evaluated: {total}")
    lines.append(
        f"- Non-recoverable before correction: {was_non_recoverable} -- of those, "
        f"**{recovered} recovered** ({round(recovered / was_non_recoverable * 100, 1) if was_non_recoverable else 0}%)"
    )
    lines.append(
        f"- Of {len(dropped_before)} DROPPED mentions specifically, {dropped_recovered} recovered "
        "(expected to be 0 -- a text-only layer cannot correct a word that was never transcribed)"
    )
    lines.append(
        f"- CORRUPTED before correction: {was_corrupted} -- of those, **{fixed_to_exact} fixed to exact "
        "CORRECT** (spelling restored, e.g. \"seven of\"/\"seven a\" -> \"seven up\"). Reported separately "
        "from `recovered` because they answer different questions: `fixed_to_exact_correct` is spelling "
        "fidelity (did this specific mention become an exact match), `recovered` is the Step 2 impact "
        "metric (did a mention cross from non-recoverable to recoverable). With RECOVERABLE_THRESHOLD "
        "requiring an almost-exact match, the two numbers usually move together now -- but a CORRUPTED "
        "mention that happened to already score >=RECOVERABLE_THRESHOLD before correction could still show "
        "fixed_to_exact_correct=True with recovered=False, so both are kept rather than assuming one implies "
        "the other."
    )
    lines.append(
        f"- Already-CORRECT before correction: {was_correct} -- of those, "
        f"**{corrupted_by_us} corrupted by this change** "
        f"({round(corrupted_by_us / was_correct * 100, 1) if was_correct else 0}%)"
    )
    lines.append("")
    return "\n".join(lines)


def run(input_path: str, output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)
    df = load_dataset(input_path)
    gazetteer = load_gazetteer(GAZETTEER_PATH)

    events_df = build_baseline_events(df, gazetteer)
    call_ids = list(df["id"])
    train_ids, held_out_ids = compute_split(events_df, call_ids)

    vocabulary = build_correction_vocabulary(events_df, train_ids, gazetteer)

    call_asr = dict(zip(df["id"], df["asr_output"]))
    call_use_case = dict(zip(df["id"], df["use_case"]))

    held_out_result = evaluate_split(events_df, call_asr, call_use_case, held_out_ids, vocabulary)
    train_result = evaluate_split(events_df, call_asr, call_use_case, train_ids, vocabulary)

    held_out_result.to_csv(os.path.join(output_dir, "improve_held_out_events.csv"), index=False)
    train_result.to_csv(os.path.join(output_dir, "improve_train_events.csv"), index=False)

    with open(os.path.join(output_dir, "improve_summary.md"), "w", encoding="utf-8") as f:
        f.write("# Step 3 -- Improve: proper-noun correction layer\n\n")
        f.write(
            f"Held-out calls ({len(held_out_ids)}/{len(call_ids)}, never used to build the correction "
            f"vocabulary): {sorted(held_out_ids)}\n\n"
        )
        f.write(
            "**This is the reported result** -- the held-out set is what the assignment means by "
            '"calls you did not tune on."\n\n'
        )
        f.write(write_summary(output_dir, "Held-out (reported result)", held_out_result))
        f.write(
            "\n**Training-split numbers below are a sanity check only** (the vocabulary was built from "
            "these calls' own gold transcripts, so a gain here is expected and not evidence of "
            "generalization).\n\n"
        )
        f.write(write_summary(output_dir, "Training split (sanity check, not the proof)", train_result))

    print(f"Wrote Step 3 reports to {output_dir}/")
    print(f"Held-out calls: {sorted(held_out_ids)}")
    print(write_summary(output_dir, "Held-out", held_out_result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the ASR proper-noun correction (Step 3: Improve).")
    parser.add_argument("--input", default="data/dataset.xlsx")
    parser.add_argument("--output", default="reports/")
    args = parser.parse_args()
    run(args.input, args.output)
