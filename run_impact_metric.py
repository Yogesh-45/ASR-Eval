"""Entry point for Step 2 (Impact metric): recoverable (proper nouns) and
polarity_preserved + quantity_preserved (short utterances, combined into
content_preserved), reported per call, before any correction.

Runs independently of run_measure.py -- it recomputes entity/hallucination
detection itself (reusing the cached LLM extractions in
data/llm_entity_cache.json, so this costs no new API calls) rather than
reading Step 1's report files, so it stands on its own as Step 2's
deliverable rather than depending on Step 1 having been run first. It writes
its own set of report files and never re-exposes WER, CER, or raw
detection-frequency counts -- those belong to Step 1 (run_measure.py).

Usage:
    python run_impact_metric.py --input data/dataset.xlsx --output reports/
"""

import argparse
import os

import pandas as pd

from src.aggregation import (
    build_entity_events_table,
    build_failure_impact_summary,
    build_hallucination_table,
    build_step1_measure_table,
    build_step2_entity_recoverability,
    build_step2_hallucination_impact,
    build_step2_impact_table,
    build_step2_proper_noun_by_use_case,
    build_top_calls_by_impact,
    impact_concentration_stat,
)
from src.data_loader import load_dataset
from src.entities_llm import RECOVERABLE_THRESHOLD, detect_entity_events_llm
from src.gazetteer import load_gazetteer
from src.hallucination import detect_hallucination
from src.llm_entities import extract_and_align_entities_llm
from src.wer_baseline import compute_wer_cer

GAZETTEER_PATH = os.path.join(os.path.dirname(__file__), "configs", "entity_gazetteer.yaml")


def run(input_path: str, output_dir: str) -> None:
    os.makedirs(output_dir, exist_ok=True)

    df = load_dataset(input_path)
    gazetteer = load_gazetteer(GAZETTEER_PATH)
    known_entity_names = sorted({e.canonical for e in gazetteer})

    entity_events, hallucination_rows, wer_rows = [], [], []
    for _, row in df.iterrows():
        call_id, use_case = row["id"], row["use_case"]
        gold, asr = row["gold_transcript"], row["asr_output"]

        aligned = extract_and_align_entities_llm(call_id, gold, asr, known_entity_names)
        entity_events.extend(detect_entity_events_llm(call_id, use_case, gold, asr, aligned))
        hallucination_rows.append(detect_hallucination(call_id, gold, asr))
        # WER is Step 1's metric, not Step 2's -- computed here only so build_step1_measure_table
        # (used internally to get proper_noun_failure_flag as the affected-call denominator, see
        # build_failure_impact_summary) has the same shape as Step 1's own table. Never written out.
        w = compute_wer_cer(gold, asr)
        w["call_id"] = call_id
        wer_rows.append(w)

    entity_events_df = build_entity_events_table(entity_events)
    hallucination_df = build_hallucination_table(hallucination_rows)
    wer_df = pd.DataFrame(wer_rows)

    step1_table = build_step1_measure_table(df, wer_df, entity_events_df, hallucination_df)
    step2_table = build_step2_impact_table(df, entity_events_df, hallucination_df)

    entity_recoverability = build_step2_entity_recoverability(entity_events_df)
    hallucination_impact = build_step2_hallucination_impact(hallucination_df)
    failure_impact = build_failure_impact_summary(step1_table, step2_table)
    proper_noun_impact_by_use_case = build_step2_proper_noun_by_use_case(step1_table, step2_table)
    top_calls = build_top_calls_by_impact(step2_table)
    concentration = impact_concentration_stat(step2_table)

    step2_table.to_csv(os.path.join(output_dir, "step2_per_call_impact.csv"), index=False)
    entity_recoverability.to_csv(os.path.join(output_dir, "step2_entity_recoverability.csv"), index=False)
    hallucination_impact.to_csv(os.path.join(output_dir, "step2_hallucination_impact.csv"), index=False)
    failure_impact.to_csv(os.path.join(output_dir, "step2_failure_impact_summary.csv"), index=False)
    proper_noun_impact_by_use_case.to_csv(
        os.path.join(output_dir, "step2_proper_noun_impact_by_use_case.csv"), index=False
    )
    top_calls.to_csv(os.path.join(output_dir, "step2_top_calls_by_impact.csv"), index=False)

    write_summary_md(output_dir, step2_table, failure_impact, proper_noun_impact_by_use_case, top_calls,
                      concentration)

    print(f"Wrote Step 2 (Impact metric) reports to {output_dir}/")
    print(failure_impact.to_string(index=False))


def write_summary_md(output_dir, step2_table, failure_impact, proper_noun_impact_by_use_case, top_calls,
                      concentration) -> None:
    lines = ["# Step 2 -- Impact Metric Summary", ""]
    lines.append(
        "Reported per call, before any correction (Step 3). This is deliberately NOT the "
        "frequency/WER measurement -- see `step1_measure_summary.md` (run_measure.py) for how "
        "often each failure mode happens.\n"
    )

    lines.append("## Metric definitions")
    lines.append("")
    lines.append(
        "- **Proper nouns -- `recoverable`**: `True` for `CORRECT`, and for `CORRUPTED` mentions "
        f"scoring above `RECOVERABLE_THRESHOLD={RECOVERABLE_THRESHOLD}` (a human or downstream "
        "system could still recover the intended entity); `False` for `DROPPED`, `ADDED`, and "
        "low-confidence `CORRUPTED` mentions."
    )
    lines.append(
        "- **Short utterances -- `content_preserved`** (`polarity_preserved` AND "
        "`quantity_preserved`): whether an affirmative/negative signal present in gold is still "
        "detectable anywhere in the ASR output despite the padding, AND whether every quantity "
        "phrase in gold (e.g. \"forty five percent\") is still reproduced verbatim somewhere in "
        "the ASR output. Either one failing counts as a real content loss -- the assignment's "
        "\"whether the yes/no or quantity is still correct.\" Both signals are reported "
        "individually too (`step2_hallucination_impact.csv`), not just the combined verdict."
    )
    lines.append("")

    lines.append("## Impact-failure rate (given Step 1's affected-call count)")
    lines.append("")
    lines.append(failure_impact.to_markdown(index=False))
    lines.append("")

    lines.append("## Proper-noun impact rate by use case")
    lines.append("")
    lines.append(proper_noun_impact_by_use_case.to_markdown(index=False))
    lines.append("")

    lines.append("## Concentration of impact")
    lines.append(
        f"- Top {concentration['top_n']} calls account for {concentration['pct_of_total_impact']}% "
        "of total impact score."
    )
    lines.append("")

    lines.append("## Top calls by impact (cost), before any correction")
    lines.append("")
    lines.append(top_calls.head(10).to_markdown(index=False))
    lines.append("")

    with open(os.path.join(output_dir, "step2_impact_summary.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Step 2 (Impact metric): recoverable / polarity_preserved+quantity_preserved."
    )
    parser.add_argument("--input", default="data/dataset.xlsx")
    parser.add_argument("--output", default="reports/")
    args = parser.parse_args()
    run(args.input, args.output)
