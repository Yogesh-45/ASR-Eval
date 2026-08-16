"""Phase 14-17: turn per-call/per-entity records into the aggregate report tables.

Split into two groups of builders, matching the assignment's own Step 1/Step 2
boundary, and used by two separate scripts (run_measure.py, run_impact_metric.py)
that write two separate sets of report files:

  STEP 1 (measure) -- raw detection only: does an entity mention exist and does
  it match (status), WER/CER, hallucination-candidate frequency, and gold-wrong
  (script-variant) flagging. Nothing here judges whether a failure was costly.

  STEP 2 (impact metric) -- the recoverable/polarity_preserved judgment layered
  on top of Step 1's detections, reported per call, before any correction.

Both groups read from the SAME underlying entity_events/hallucination_df records
(src/entities_llm.py and src/hallucination.py compute status+recoverable, and
hallucination-candidate+polarity_preserved, together in one pass each -- doing
so is just a threshold check on data already produced by a single LLM call /
a single alignment pass, so recomputing it twice would waste API calls for no
benefit). The separation enforced here is about which columns reach which
step's OUTPUT FILES, not about running detection twice.
"""

import pandas as pd


def build_entity_events_table(all_events: list) -> pd.DataFrame:
    if not all_events:
        return pd.DataFrame(
            columns=[
                "call_id",
                "use_case",
                "canonical_entity",
                "entity_type",
                "status",
                "gold_score",
                "gold_matched_span",
                "asr_score",
                "asr_matched_span",
                "recoverable",
                "script_variant",
            ]
        )
    return pd.DataFrame(all_events)


def build_hallucination_table(all_results: list) -> pd.DataFrame:
    return pd.DataFrame(all_results)


# ----------------------------------------------------------------------------
# STEP 1 -- Measure: frequency, WER, gold-wrong flagging. No recoverability.
# ----------------------------------------------------------------------------

def build_step1_entity_events(entity_events: pd.DataFrame) -> pd.DataFrame:
    """Step 1's view of entity detection: does the mention exist and match
    (status), plus the gold-wrong (script_variant) flag the assignment
    explicitly asks Step 1 to surface. Drops `recoverable` -- that judgment is
    Step 2's, not Step 1's."""

    if entity_events.empty:
        return entity_events
    return entity_events.drop(columns=["recoverable"])


def build_step1_hallucination_events(hallucination_df: pd.DataFrame) -> pd.DataFrame:
    """Step 1's view of hallucination detection: the candidate flag and the
    raw signals that produced it, plus the raw gold/ASR polarity words and gold
    quantity phrases found (facts, not judgments). Drops `polarity_preserved` and
    `quantity_preserved` -- whether the yes/no or quantity survived is Step 2's
    impact judgment, per the assignment's "whether the yes/no or quantity is
    still correct" wording."""

    if hallucination_df.empty:
        return hallucination_df
    return hallucination_df.drop(columns=["polarity_preserved", "quantity_preserved"])


def build_step1_measure_table(base_df: pd.DataFrame, wer_df: pd.DataFrame, entity_events: pd.DataFrame,
                               hallucination_df: pd.DataFrame) -> pd.DataFrame:
    """One row per call: WER/CER and raw failure-mode detection counts only.
    No `entity_non_recoverable`, no `polarity_preserved`, no `impact_score` --
    those are Step 2's per-call impact table, not this one."""

    per_call = base_df[["id", "use_case"]].copy()
    per_call = per_call.merge(wer_df, left_on="id", right_on="call_id", how="left").drop(columns=["call_id"])

    halluc_cols = [c for c in hallucination_df.columns if c != "polarity_preserved"]
    per_call = per_call.merge(hallucination_df[halluc_cols], left_on="id", right_on="call_id", how="left").drop(
        columns=["call_id"]
    )

    if not entity_events.empty:
        non_correct = entity_events[entity_events["status"] != "CORRECT"]
        script_variant = entity_events[entity_events["script_variant"]]

        agg = entity_events.groupby("call_id").size().rename("entity_mentions")
        agg_bad = non_correct.groupby("call_id").size().rename("entity_failures")
        agg_variant = script_variant.groupby("call_id").size().rename("entity_script_variants")

        per_call = per_call.merge(agg, left_on="id", right_index=True, how="left")
        per_call = per_call.merge(agg_bad, left_on="id", right_index=True, how="left")
        per_call = per_call.merge(agg_variant, left_on="id", right_index=True, how="left")
    else:
        for col in ["entity_mentions", "entity_failures", "entity_script_variants"]:
            per_call[col] = 0

    for col in ["entity_mentions", "entity_failures", "entity_script_variants"]:
        per_call[col] = per_call[col].fillna(0).astype(int)

    per_call["proper_noun_failure_flag"] = per_call["entity_failures"] > 0

    return per_call


def build_failure_frequency_summary(step1_table: pd.DataFrame) -> pd.DataFrame:
    """How often each failure mode happens -- frequency only, no cost/impact."""

    total_calls = len(step1_table)
    affected = step1_table[step1_table["proper_noun_failure_flag"]]
    affected_h = step1_table[step1_table["hallucination_candidate"]]

    return pd.DataFrame(
        [
            {
                "failure_mode": "proper_noun",
                "total_calls": total_calls,
                "affected_calls": len(affected),
                "frequency_pct": round(len(affected) / total_calls * 100, 1) if total_calls else 0,
            },
            {
                "failure_mode": "short_utterance_hallucination",
                "total_calls": total_calls,
                "affected_calls": len(affected_h),
                "frequency_pct": round(len(affected_h) / total_calls * 100, 1) if total_calls else 0,
            },
        ]
    )


def build_step1_proper_noun_by_use_case(step1_table: pd.DataFrame) -> pd.DataFrame:
    """Frequency of the proper-noun failure mode, split per use_case. Which
    use cases actually drive the affected-call count (not yet weighted by
    cost -- see Step 2's version for the impact-weighted breakdown)."""

    rows = []
    for use_case, group in step1_table.groupby("use_case"):
        total = len(group)
        affected = group[group["proper_noun_failure_flag"]]
        rows.append(
            {
                "use_case": use_case,
                "total_calls": total,
                "affected_calls": len(affected),
                "frequency_pct": round(len(affected) / total * 100, 1) if total else 0,
            }
        )
    return pd.DataFrame(rows).sort_values("affected_calls", ascending=False)


def build_top_calls_by_frequency(step1_table: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    """Which calls drive the raw failure count -- ranked by how many failure
    events occurred, not by how costly they were (that ranking is Step 2's
    top_calls_by_impact)."""

    scored = step1_table.copy()
    scored["failure_count"] = scored["entity_failures"] + scored["hallucination_candidate"].astype(int)
    ranked = scored[scored["failure_count"] > 0].sort_values("failure_count", ascending=False)
    return ranked.head(top_n)[
        ["id", "use_case", "failure_count", "entity_failures", "hallucination_candidate", "wer"]
    ]


def build_top_entities(entity_events: pd.DataFrame) -> pd.DataFrame:
    """Per-entity CORRECT/CORRUPTED/DROPPED/ADDED counts and failure rate --
    a frequency-of-corruption view, not a recoverability judgment, so this
    stays a Step 1 output."""

    if entity_events.empty:
        return pd.DataFrame(columns=["canonical_entity", "mentions", "correct", "corrupted", "dropped", "added",
                                       "failure_rate_pct"])

    grouped = entity_events.groupby("canonical_entity")
    rows = []
    for entity, group in grouped:
        mentions = len(group)
        correct = (group["status"] == "CORRECT").sum()
        corrupted = (group["status"] == "CORRUPTED").sum()
        dropped = (group["status"] == "DROPPED").sum()
        added = (group["status"] == "ADDED").sum()
        failures = corrupted + dropped
        gold_mentions = mentions - added
        rows.append(
            {
                "canonical_entity": entity,
                "mentions": mentions,
                "correct": int(correct),
                "corrupted": int(corrupted),
                "dropped": int(dropped),
                "added": int(added),
                "failure_rate_pct": round(failures / gold_mentions * 100, 1) if gold_mentions else 0.0,
            }
        )
    return pd.DataFrame(rows).sort_values("failure_rate_pct", ascending=False)


def build_gold_script_variants(entity_events: pd.DataFrame) -> pd.DataFrame:
    """Step 1's "flag where the gold itself is wrong" deliverable: matches
    resolved only by pairing a Latin alias in one transcript with a
    Devanagari alias in the other -- not a real ASR error."""

    if entity_events.empty:
        return entity_events
    return entity_events[entity_events["script_variant"]][
        ["call_id", "canonical_entity", "gold_matched_span", "asr_matched_span"]
    ]


# ----------------------------------------------------------------------------
# STEP 2 -- Impact metric: recoverable / polarity_preserved, per call, before
# any correction. Builds on Step 1's detections but never re-exposes WER/CER
# or raw detection signals as its own output -- those belong to Step 1.
# ----------------------------------------------------------------------------

def build_step2_entity_recoverability(entity_events: pd.DataFrame) -> pd.DataFrame:
    """Per-entity-mention impact judgment: is it recoverable, with just
    enough context (status, score, spans) to see why."""

    if entity_events.empty:
        return entity_events
    return entity_events[
        ["call_id", "use_case", "canonical_entity", "status", "gold_matched_span", "asr_matched_span", "asr_score",
         "recoverable"]
    ]


def build_step2_hallucination_impact(hallucination_df: pd.DataFrame) -> pd.DataFrame:
    """Per-call impact judgment for the hallucination failure mode: is the
    yes/no signal still detectable despite the padding, and separately, is any
    quantity gold reported (e.g. "forty five percent") still reproduced verbatim
    -- the assignment's two halves of "is the yes/no or quantity still correct,"
    kept as two visible signals rather than pre-blended into one."""

    if hallucination_df.empty:
        return hallucination_df
    return hallucination_df[
        ["call_id", "gold_polarity", "asr_polarity", "polarity_preserved",
         "gold_quantities", "missing_quantities", "quantity_preserved"]
    ]


def build_step2_impact_table(base_df: pd.DataFrame, entity_events: pd.DataFrame,
                              hallucination_df: pd.DataFrame) -> pd.DataFrame:
    """One row per call: the impact metric only -- `recoverable` rolled up as
    entity_non_recoverable/proper_noun_impact_flag, `polarity_preserved` and
    `quantity_preserved` kept as two visible signals plus their combined
    `content_preserved` (both must hold -- the assignment's "or" means either
    one failing is a real content loss), and the combined impact_score. This is
    Step 2's actual deliverable: "report per call, before any change." No WER,
    no raw entity/hallucination detection counts -- those are Step 1's."""

    per_call = base_df[["id", "use_case"]].copy()

    halluc_cols = ["call_id", "hallucination_candidate", "polarity_preserved", "quantity_preserved"]
    per_call = per_call.merge(hallucination_df[halluc_cols], left_on="id", right_on="call_id", how="left").drop(
        columns=["call_id"]
    )
    per_call["content_preserved"] = per_call["polarity_preserved"] & per_call["quantity_preserved"]

    if not entity_events.empty:
        non_recoverable = entity_events[~entity_events["recoverable"]]
        agg_unrecoverable = non_recoverable.groupby("call_id").size().rename("entity_non_recoverable")
        per_call = per_call.merge(agg_unrecoverable, left_on="id", right_index=True, how="left")
    else:
        per_call["entity_non_recoverable"] = 0

    per_call["entity_non_recoverable"] = per_call["entity_non_recoverable"].fillna(0).astype(int)
    per_call["proper_noun_impact_flag"] = per_call["entity_non_recoverable"] > 0

    # The content-loss penalty only applies to calls actually flagged as hallucination
    # candidates -- gated the same way build_failure_impact_summary already gates its own
    # affected/impacted split. Without this gate, a long multi-item order call with dozens of
    # quantities (e.g. a beverage order) can fail quantity_preserved purely as a side effect of
    # the SAME proper-noun corruption already counted in entity_non_recoverable, double-billing
    # one real failure under two different failure modes' scores. Caught by comparing the
    # WER-vs-impact_score correlation before/after adding quantity_preserved and finding it had
    # moved for calls that were never hallucination candidates in the first place.
    per_call["impact_score"] = (
        per_call["entity_non_recoverable"] * 2
        + per_call["hallucination_candidate"].astype(int)
        + (per_call["hallucination_candidate"] & ~per_call["content_preserved"]).astype(int) * 2
    )

    return per_call.drop(columns=["hallucination_candidate"])


def build_failure_impact_summary(step1_table: pd.DataFrame, step2_table: pd.DataFrame) -> pd.DataFrame:
    """How costly each failure mode is, GIVEN Step 1's affected-call count --
    Step 2 necessarily builds on Step 1's frequency denominator (you can't
    report "% of affected calls that were costly" without Step 1's "affected"
    set), but never re-derives or re-exposes Step 1's own WER/frequency
    numbers as if they were new Step 2 output."""

    merged = step1_table[["id", "proper_noun_failure_flag", "hallucination_candidate"]].merge(
        step2_table[["id", "proper_noun_impact_flag", "content_preserved"]], on="id"
    )

    affected = merged[merged["proper_noun_failure_flag"]]
    impacted = affected[affected["proper_noun_impact_flag"]]
    affected_h = merged[merged["hallucination_candidate"]]
    impacted_h = affected_h[~affected_h["content_preserved"]]

    return pd.DataFrame(
        [
            {
                "failure_mode": "proper_noun",
                "affected_calls": len(affected),
                "impact_failed_calls": len(impacted),
                "impact_failed_pct_of_affected": round(len(impacted) / len(affected) * 100, 1) if len(affected) else 0,
            },
            {
                "failure_mode": "short_utterance_hallucination",
                "affected_calls": len(affected_h),
                "impact_failed_calls": len(impacted_h),
                "impact_failed_pct_of_affected": round(len(impacted_h) / len(affected_h) * 100, 1)
                if len(affected_h)
                else 0,
            },
        ]
    )


def build_step2_proper_noun_by_use_case(step1_table: pd.DataFrame, step2_table: pd.DataFrame) -> pd.DataFrame:
    """Impact-weighted version of Step 1's frequency-by-use-case: given the
    calls Step 1 flagged as affected in each use_case, how many were actually
    costly."""

    merged = step1_table[["id", "use_case", "proper_noun_failure_flag"]].merge(
        step2_table[["id", "proper_noun_impact_flag"]], on="id"
    )
    rows = []
    for use_case, group in merged.groupby("use_case"):
        affected = group[group["proper_noun_failure_flag"]]
        impacted = affected[affected["proper_noun_impact_flag"]]
        rows.append(
            {
                "use_case": use_case,
                "affected_calls": len(affected),
                "impact_failed_calls": len(impacted),
                "impact_failed_pct_of_affected": round(len(impacted) / len(affected) * 100, 1)
                if len(affected)
                else 0,
            }
        )
    return pd.DataFrame(rows).sort_values("impact_failed_calls", ascending=False)


def build_top_calls_by_impact(step2_table: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    ranked = step2_table[step2_table["impact_score"] > 0].sort_values("impact_score", ascending=False)
    return ranked.head(top_n)[
        ["id", "use_case", "impact_score", "entity_non_recoverable", "polarity_preserved", "quantity_preserved"]
    ]


def impact_concentration_stat(step2_table: pd.DataFrame, top_n: int = 20) -> dict:
    ranked = step2_table.sort_values("impact_score", ascending=False)
    total_impact = ranked["impact_score"].sum()
    top_impact = ranked.head(top_n)["impact_score"].sum()
    pct = round(top_impact / total_impact * 100, 1) if total_impact else 0.0
    return {"top_n": top_n, "pct_of_total_impact": pct, "total_impact": int(total_impact)}
