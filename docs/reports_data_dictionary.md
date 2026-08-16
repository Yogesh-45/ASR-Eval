# `reports/` Data Dictionary

Reference for every file `run_measure.py` writes: what it represents, every
column, its type, and where it's computed. Ends with an explicit gap-check
against what Step 3 (Improve) will need next.

Regenerate with: `python run_measure.py --input data/dataset.xlsx --output reports/`

## How the files relate

```
entity_events.csv  ─┐
                     ├─▶ per_call.csv ─┬─▶ failure_mode_summary.csv
hallucination_events.csv ┘            ├─▶ top_calls.csv
                                       └─▶ (feeds concentration stat in measure_summary.md)

entity_events.csv ──▶ top_entities.csv
entity_events.csv ──▶ gold_script_variants.csv
```

Two files are **raw event logs** (`entity_events.csv`,
`hallucination_events.csv`). `per_call.csv` is those two joined onto one row
per call, plus WER. The other four are aggregates/rankings computed *from*
`per_call.csv` or `entity_events.csv` — nothing in them is independently
measured.

---

## `entity_events.csv`

**Granularity:** one row per **(call, gazetteer entity) mention actually
detected** — not one row per call. A call contributes zero rows if it never
mentions any of the 10 catalog entities; it contributes one row per entity
it does mention. **22 rows** currently, from 126 (call × applicable-entity)
checks attempted — most checks find nothing, so most produce no row.

**Source:** `detect_entity_events()` in [`src/entities.py`](../src/entities.py:37), assembled by [`build_entity_events_table()`](../src/aggregation.py:6).

| Column | Type | Values / range | Null? | Meaning |
|---|---|---|---|---|
| `call_id` | str | uuid | no | which call |
| `use_case` | str | one of 5 use cases | no | copied from the row, for filtering |
| `canonical_entity` | str | e.g. `Mirinda`, `Delhi` | no | the gazetteer entry that matched |
| `entity_type` | str | `product` / `city` / `packaging` | no | from `configs/entity_gazetteer.yaml` |
| `status` | str | `CORRECT` / `CORRUPTED` / `DROPPED` / `ADDED` | no | see classification logic below |
| `gold_score` | float | 0–100 | no | best fuzzy-match score of any gold text window against any alias |
| `gold_matched_span` | str | e.g. `mirinda` | can be None¹ | which chunk of gold text scored best |
| `asr_score` | float | 0–100 | no | same, against ASR text |
| `asr_matched_span` | str | e.g. `mirnda` | can be None¹ | which chunk of ASR text scored best |
| `recoverable` | bool | — | no | **the impact metric.** True for `CORRECT`, and for `CORRUPTED` scoring ≥65; False otherwise |
| `script_variant` | bool | — | no | True only when a `CORRECT` match paired a Latin alias in one transcript with a Devanagari alias in the other |

¹ `matched_span` is None only if the transcript tokenized to nothing at all (empty text) — see [`src/gazetteer.py:44-46`](../src/gazetteer.py#L44); doesn't occur in this dataset since no row has empty gold/asr.

**`status` decision logic** (thresholds in [`src/entities.py:19-22`](../src/entities.py#L19-L22)):

| gold_score ≥ 88? | asr_score ≥ 82? | asr_score ≥ 55? | → status |
|---|---|---|---|
| yes | yes | – | `CORRECT` |
| yes | no | yes | `CORRUPTED` |
| yes | no | no | `DROPPED` |
| no | yes | – | `ADDED` |
| no | no | – | *(no row written)* |

No `ADDED` rows exist in the current output — not a bug, just means no
call mentioned a catalog entity in ASR that gold never mentioned at all.

---

## `hallucination_events.csv`

**Granularity:** one row per call. **49 rows** — every call gets scored,
unlike entity events.

**Source:** `detect_hallucination()` in [`src/hallucination.py`](../src/hallucination.py:88).

| Column | Type | Values / range | Null? | Meaning |
|---|---|---|---|---|
| `call_id` | str | uuid | no | |
| `gold_token_count` | int | ≥0 | no | word count of normalized gold |
| `asr_token_count` | int | ≥0 | no | word count of normalized ASR |
| `length_ratio` | float | ≥0 | no | `asr_token_count / gold_token_count` |
| `insertion_rate` | float | 0–1 | no | share of ASR tokens with no matching occurrence anywhere in gold (bag-of-words, not aligned) |
| `repetition_ratio` | float | 0–1 | no | excess back-to-back repeated tokens/cycles in ASR beyond what gold justifies, as a share of ASR length |
| `short_call_hallucination` | bool | — | no | whole-call-is-short rule tripped (`gold_token_count≤6` and `length_ratio≥1.5` and `insertion_rate≥0.3`) |
| `repetition_burst` | bool | — | no | gold-aware repetition rule tripped (`repetition_ratio≥0.15`) |
| `repetition_run` | str | e.g. `neetiya`, `yes` | **null for 35/49 rows** | the specific token/cycle that triggered `repetition_burst`; null when it didn't trigger |
| `hallucination_candidate` | bool | — | no | **the headline flag** — OR of the two rules above |
| `gold_polarity` | str | `affirmative`, `negative`, `affirmative,negative`, or null | can be null | yes/no signal(s) detected in gold via the polarity lexicon |
| `asr_polarity` | str | same as above | can be null | same, for ASR |
| `polarity_preserved` | bool | — | no | **the impact metric** — is every polarity signal gold had still present somewhere in ASR (or gold had none to preserve) |

---

## `per_call.csv`

**Granularity:** one row per call. **49 rows.** This is the master table —
`entity_events.csv` and `hallucination_events.csv` folded onto one row per
call, plus the WER/CER diagnostic. Built by
[`build_per_call_table()`](../src/aggregation.py:30).

Contains **every column from `hallucination_events.csv` above** (renamed
`id` instead of `call_id`), plus:

| Column | Type | Values / range | Null? | Meaning |
|---|---|---|---|---|
| `use_case` | str | — | no | |
| `wer` | float | ≥0 (can exceed 1.0) | no in this dataset¹ | word error rate, diagnostic only |
| `cer` | float | ≥0 | no | character error rate |
| `substitutions`, `insertions`, `deletions`, `hits` | int | ≥0 | no | jiwer word-level alignment counts |
| `entity_mentions` | int | ≥0 | no (0 if none) | how many gazetteer entities were detected in this call at all (rows in `entity_events.csv` for this `call_id`) |
| `entity_failures` | int | ≥0 | no | how many of those weren't `CORRECT` |
| `entity_non_recoverable` | int | ≥0 | no | how many had `recoverable=False` |
| `entity_script_variants` | int | ≥0 | no | how many were script-variant `CORRECT` matches |
| `proper_noun_failure_flag` | bool | — | no | `entity_failures > 0` — this defines "affected by proper-noun mode" |
| `proper_noun_impact_flag` | bool | — | no | `entity_non_recoverable > 0` — this defines "impact-failed" for proper-noun mode |
| `impact_score` | int | ≥0 | no | `entity_non_recoverable×2 + hallucination_candidate×1 + (not polarity_preserved)×2` — the ranking number used everywhere below |

¹ `wer` would be null only if gold_transcript were empty ([`src/wer_baseline.py:19-26`](../src/wer_baseline.py#L19-L26)); no row in this dataset has empty gold, so it never triggers here — but it's a real code path, not a guarantee for future data.

---

## `failure_mode_summary.csv`

**Granularity:** one row per failure mode. **2 rows.** Built by
[`build_failure_mode_summary()`](../src/aggregation.py:71).

| Column | Type | Meaning |
|---|---|---|
| `failure_mode` | str | `proper_noun` or `short_utterance_hallucination` |
| `total_calls` | int | 49 for both rows |
| `affected_calls` | int | count of calls with `proper_noun_failure_flag`/`hallucination_candidate` true |
| `frequency_pct` | float | `affected_calls / total_calls × 100` — answers "how often does this occur" |
| `impact_failed_calls` | int | of the affected calls, how many also have `proper_noun_impact_flag`=true / `polarity_preserved`=false |
| `impact_failed_pct_of_affected` | float | `impact_failed_calls / affected_calls × 100` — the closest current proxy for "how often does it cause the call goal to fail" (see gap note below — this is **not** a true goal-failure label) |

---

## `top_calls.csv`

**Granularity:** one row per call **with `impact_score > 0`** — not all 49.
**13 rows**, sorted worst-first, capped at top 20 (`top_n` param). Built by
[`build_top_calls()`](../src/aggregation.py:108). Same columns as `per_call.csv`, narrowed to: `id`, `use_case`, `impact_score`, `entity_failures`, `entity_non_recoverable`, `hallucination_candidate`, `polarity_preserved`, `wer`.

Note `impact_score`'s weighting (entity harm ×2, lost polarity ×2,
hallucination flag ×1) means this ranking leans toward proper-noun failures
by construction — it hasn't been validated against any ground truth.

---

## `top_entities.csv`

**Granularity:** one row per gazetteer entity that had ≥1 event. **10
rows** (all 10 catalog entities appear at least once). Built by
[`build_top_entities()`](../src/aggregation.py:132), grouping `entity_events.csv`.

| Column | Type | Meaning |
|---|---|---|
| `canonical_entity` | str | |
| `mentions` | int | total events for this entity (all statuses) |
| `correct`, `corrupted`, `dropped`, `added` | int | counts by status |
| `failure_rate_pct` | float | `(corrupted + dropped) / (mentions - added) × 100` — failure rate among times the entity was genuinely in gold |

---

## `gold_script_variants.csv`

**Granularity:** one row per script-variant match. **2 rows**
(`Gurgaon`/`गुड़गांव`, `Delhi`/`दिल्ली`). Built by
[`build_gold_script_variants()`](../src/aggregation.py:161) — just
`entity_events.csv` filtered to `script_variant == True`.

| Column | Type | Meaning |
|---|---|---|
| `call_id`, `canonical_entity` | — | which call, which entity |
| `gold_matched_span`, `asr_matched_span` | str | the two differently-scripted spans that were judged equivalent |

**Important scope note:** this file does **not** mean "gold is verified
correct" or "gold is wrong" in general — it only catches the one narrow case
the assignment calls out (naive comparison blaming ASR for a script choice).
It says nothing about calls with no gazetteer entities at all, which is
most of the dataset.

---

## Gap check: what Step 3 (Improve) will need vs. what exists today

Going through the assignment's requirements for Step 3 (pick highest-cost
mode, implement a correction, prove the gain **on held-out calls**, report
**how often the fix corrupts an already-correct transcript**):

| Needed for Step 3 | Status | Where |
|---|---|---|
| Identify the highest-cost mode | **Ready** | `failure_mode_summary.csv` — proper nouns (75% impact-failed-of-affected) vs hallucination (0%) |
| Pre-correction baseline, per entity, per call | **Ready** | `entity_events.csv` + `per_call.csv` are exactly this snapshot |
| A set of currently-`CORRECT` entities to re-check for regressions after correction | **Ready** | filter `entity_events.csv` on `status == "CORRECT"` |
| Held-out vs tuning split of the 49 calls | **Missing** | no split column anywhere; every threshold above was set by inspecting the full 49, so there is currently no calls the pipeline hasn't "seen" |
| A diff tool: re-run detection after correction, compare against the baseline row-by-row | **Missing** | schema supports it (`call_id` + `canonical_entity` is a stable join key), but no `compare_entity_events(before, after)` function exists yet |
| True call-goal-failure label (`goal_survives`) | **Missing — proxy only** | `proper_noun_impact_flag` / `polarity_preserved` approximate it; no use-case slot schema exists (deferred, see `docs/implementation_status.md` §2.7) |
| Numbers/IDs failure mode | **Missing (optional)** | not started |
| Human-review queue | **Missing** | confirmed via grep, nothing implemented (see prior discussion) |
| Independent gold re-transcription (true gold-error detection, beyond script variants) | **Missing** | `gold_script_variants.csv` only covers the script-pairing case |

**Bottom line:** the measurement side is solid and directly reusable for
Step 3's "before" numbers. The one concrete blocker before Step 3 can honestly
claim results "on calls you did not tune on" is the missing held-out split —
worth deciding now, before writing any correction logic, since it determines
which calls are even allowed to inform threshold/rule choices from here on.
