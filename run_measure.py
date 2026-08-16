"""Entry point for Step 1 (Measure): quantify how often each failure mode
happens, WER as a diagnostic baseline, which calls/entities drive the raw
failure count, and where the gold itself is wrong (script variants).

This script does NOT compute or report recoverable / polarity_preserved /
quantity_preserved / impact_score -- that is Step 2's job (run_impact_metric.py),
which is a separate script producing a separate set of report files. See
docs/challenges_and_decisions.md for why these were originally combined and
why they were split out.

Usage:
    python run_measure.py --input data/dataset.xlsx --output reports/
"""

import argparse
import os

import pandas as pd

from src.aggregation import (
    build_entity_events_table,
    build_failure_frequency_summary,
    build_gold_script_variants,
    build_hallucination_table,
    build_step1_entity_events,
    build_step1_hallucination_events,
    build_step1_measure_table,
    build_step1_proper_noun_by_use_case,
    build_top_calls_by_frequency,
    build_top_entities,
)
from src.data_loader import format_profile_report, load_dataset, profile_dataset
from src.entities import detect_entity_events
from src.entities_llm import detect_entity_events_llm
from src.gazetteer import load_gazetteer
from src.hallucination import detect_hallucination
from src.llm_entities import extract_and_align_entities_llm
from src.wer_baseline import compute_wer_cer

GAZETTEER_PATH = os.path.join(os.path.dirname(__file__), "configs", "entity_gazetteer.yaml")


def run(input_path: str, output_dir: str, entity_backend: str = "llm", llm_use_hints: bool = True) -> None:
    os.makedirs(output_dir, exist_ok=True)

    df = load_dataset(input_path)
    profile = profile_dataset(df)
    with open(os.path.join(output_dir, "dataset_profile.md"), "w", encoding="utf-8") as f:
        f.write(format_profile_report(profile))

    gazetteer = load_gazetteer(GAZETTEER_PATH)
    known_entity_names = sorted({entity.canonical for entity in gazetteer}) if llm_use_hints else []

    wer_rows, entity_events, hallucination_rows = [], [], []
    for _, row in df.iterrows():
        call_id, use_case = row["id"], row["use_case"]
        gold, asr = row["gold_transcript"], row["asr_output"]

        wer_result = compute_wer_cer(gold, asr)
        wer_result["call_id"] = call_id
        wer_rows.append(wer_result)

        if entity_backend == "llm":
            aligned_entities = extract_and_align_entities_llm(call_id, gold, asr, known_entity_names)
            entity_events.extend(detect_entity_events_llm(call_id, use_case, gold, asr, aligned_entities))
        else:
            entity_events.extend(detect_entity_events(call_id, use_case, gold, asr, gazetteer))

        hallucination_rows.append(detect_hallucination(call_id, gold, asr))

    wer_df = pd.DataFrame(wer_rows)
    entity_events_df = build_entity_events_table(entity_events)
    hallucination_df = build_hallucination_table(hallucination_rows)

    step1_table = build_step1_measure_table(df, wer_df, entity_events_df, hallucination_df)
    failure_frequency = build_failure_frequency_summary(step1_table)
    proper_noun_freq_by_use_case = build_step1_proper_noun_by_use_case(step1_table)
    top_calls = build_top_calls_by_frequency(step1_table)
    top_entities = build_top_entities(entity_events_df)
    gold_variants = build_gold_script_variants(entity_events_df)

    step1_entity_events = build_step1_entity_events(entity_events_df)
    step1_hallucination_events = build_step1_hallucination_events(hallucination_df)

    step1_table.to_csv(os.path.join(output_dir, "step1_per_call.csv"), index=False)
    step1_entity_events.to_csv(os.path.join(output_dir, "step1_entity_events.csv"), index=False)
    step1_hallucination_events.to_csv(os.path.join(output_dir, "step1_hallucination_events.csv"), index=False)
    failure_frequency.to_csv(os.path.join(output_dir, "step1_failure_frequency_summary.csv"), index=False)
    proper_noun_freq_by_use_case.to_csv(os.path.join(output_dir, "step1_proper_noun_by_use_case.csv"), index=False)
    top_calls.to_csv(os.path.join(output_dir, "step1_top_calls_by_frequency.csv"), index=False)
    top_entities.to_csv(os.path.join(output_dir, "step1_top_entities.csv"), index=False)
    gold_variants.to_csv(os.path.join(output_dir, "gold_script_variants.csv"), index=False)

    write_summary_md(output_dir, profile, step1_table, failure_frequency, proper_noun_freq_by_use_case, top_calls,
                      top_entities, gold_variants)

    print(f"Wrote Step 1 (Measure) reports to {output_dir}/")
    print(failure_frequency.to_string(index=False))


def write_summary_md(output_dir, profile, step1_table, failure_frequency, proper_noun_freq_by_use_case, top_calls,
                      top_entities, gold_variants) -> None:
    overall_wer = step1_table["wer"].dropna().mean()
    lines = ["# Step 1 -- Measure Summary", ""]
    lines.append(
        "Frequency, WER, and gold-wrong flagging only. This is deliberately NOT the impact "
        "metric -- see `step2_impact_summary.md` (run_impact_metric.py) for whether each "
        "failure was actually costly.\n"
    )
    lines.append("## Overall")
    lines.append(f"- Total calls: {profile['num_rows']}")
    lines.append(f"- Overall mean WER (diagnostic only, not the business metric): {overall_wer:.3f}")
    lines.append(
        f"- Calls with Hindi-English script mixing: {profile['calls_with_devanagari_script']} "
        f"({profile['pct_calls_with_devanagari_script']:.1f}%)"
    )
    lines.append("")

    lines.append("## Failure-mode frequency")
    lines.append("")
    lines.append(failure_frequency.to_markdown(index=False))
    lines.append("")

    lines.append("## Proper-noun failure frequency by use case")
    lines.append("")
    lines.append(proper_noun_freq_by_use_case.to_markdown(index=False))
    lines.append("")

    lines.append("## Top calls by raw failure count")
    lines.append("")
    lines.append(top_calls.head(10).to_markdown(index=False))
    lines.append("")

    lines.append("## Top problematic entities")
    lines.append("")
    lines.append(top_entities.to_markdown(index=False))
    lines.append("")

    lines.append("## Gold/ASR script-variant matches (naive WER would misclassify these)")
    lines.append("")
    if gold_variants.empty:
        lines.append("None detected.")
    else:
        lines.append(gold_variants.to_markdown(index=False))
    lines.append("")

    with open(os.path.join(output_dir, "step1_measure_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run Step 1 (Measure): frequency, WER, gold-wrong flagging.")
    parser.add_argument("--input", default="data/dataset.xlsx")
    parser.add_argument("--output", default="reports/")
    parser.add_argument(
        "--entity-backend",
        choices=["llm", "gazetteer"],
        default="llm",
        help="llm: open-set extraction (default). gazetteer: legacy closed-vocabulary detector.",
    )
    parser.add_argument(
        "--llm-hints",
        choices=["gazetteer", "none"],
        default="gazetteer",
        help="Only applies to --entity-backend llm. gazetteer: pass known canonical names as "
        "grounding hints (default). none: pure LLM judgment, no predefined entity list.",
    )
    args = parser.parse_args()
    run(args.input, args.output, entity_backend=args.entity_backend, llm_use_hints=args.llm_hints == "gazetteer")
