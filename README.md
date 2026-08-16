# ASR Failure Analysis

Status: **All four assignment tasks — Measure, Impact metric, Improve, and the
reasoning paragraph — are implemented.** See "What's next" for open items (a
labeled validation set, and two candidate directions for further improving
proper-noun correction).

## Quick summary

- **Highest-cost failure mode:** proper nouns/product names — 11/49 calls
  (22.4%) affected, and every one of those has at least one unrecoverable
  entity mention (`RECOVERABLE_THRESHOLD` requires an almost-exact match; see
  ["What replaces WER"](#what-replaces-wer)).
- **The metric that replaces WER:** `recoverable` (proper nouns) and
  `content_preserved` (short utterances), both tied to whether the call's
  goal survives, not string distance. Correlation between WER and the
  combined `impact_score` across all 49 calls: **r = 0.035** — essentially
  zero, i.e. WER and business cost are measuring different things.
- **The fix (Step 3):** a rule-based, training-data-mined correction layer —
  2 real corrections found via exhaustive audit, **0 already-correct
  transcripts corrupted**, 2/17 (11.8%) non-recoverable entities recovered on
  the training split; 0/3 on held-out — a disclosed limitation, not a hidden
  one (see ["Step 3: Improve"](#step-3-improve--correcting-the-proper-noun-failure-mode)).
- **Latency:** the correction layer measures 0.04ms median / 1.6ms max per
  call, trivially inside the 600ms turn budget (see
  ["Reasoning"](#reasoning-sub-600ms-latency-budget-sync-vs-async-and-what-id-change-first)
  for what I'd change first with more infra access).

## Table of contents

1. [The task](#the-task)
2. [The data](#the-data)
3. [Why not raw WER](#why-not-raw-wer)
4. [What replaces WER](#what-replaces-wer)
5. [Architecture: Measure and Impact metric as separate scripts](#architecture-measure-and-impact-metric-as-separate-scripts)
6. [Failure mode 1: proper nouns / product names](#failure-mode-1-proper-nouns--product-names)
7. [Step 3: Improve — correcting the proper-noun failure mode](#step-3-improve--correcting-the-proper-noun-failure-mode)
8. [Failure mode 2: short utterances over-transcribed / hallucinated](#failure-mode-2-short-utterances-over-transcribed--hallucinated)
9. [`impact_score`: how the two failure modes combine into one per-call number](#impact_score-how-the-two-failure-modes-combine-into-one-per-call-number)
10. [What we deliberately did not build yet](#what-we-deliberately-did-not-build-yet)
11. [Tools/libraries used](#toolslibraries-used)
12. [Running it](#running-it)
13. [Reasoning: sub-600ms latency budget, sync vs async, and what I'd change first](#reasoning-sub-600ms-latency-budget-sync-vs-async-and-what-id-change-first)
14. [What's next](#whats-next)

## The task

A take-home assignment, answered end to end in this repo: given ~50 real
voice-agent calls (each with a gold transcript and the current ASR output),

1. **Measure** — for each of two real ASR failure modes, quantify how often
   it happens and how much it costs, tied to whether the call's goal
   survives, not raw WER. Show which calls/entities drive it, and flag
   where the gold itself is wrong (a different script, not a real error).
2. **Impact metric** — implement a per-call metric for that cost: for
   proper nouns, whether the entity is still recoverable; for short
   utterances, whether the yes/no or quantity is still correct. Reported
   before any correction.
3. **Improve** — pick the highest-cost failure mode and actually reduce it,
   measured on the Step 2 metric, on calls not tuned on, without corrupting
   already-correct transcripts.
4. **Reasoning** — one paragraph on the sub-600ms turn-latency budget, sync
   vs async, and the single highest-leverage change with more infra access.

The two failure modes built here are the two the assignment requires:
**proper nouns/product names mangled or dropped**, and **short utterances
over-transcribed or hallucinated**. The optional third mode (numbers/IDs) is
out of scope — see ["What we deliberately did not build yet"](#what-we-deliberately-did-not-build-yet).

## The data

49 real voice-agent calls (`data/dataset.xlsx`): `id`, `use_case`, `gold_transcript`,
`asr_output`, `recording_url`. One row is one full call, not one utterance
(49 unique ids, 49 unique recording URLs). Use cases: ecommerce support (21),
b2b sales outreach (17), admissions/lead qualification (4), beverage
order-taking (4), verification call (3). 88% of calls (avg gold length 43
tokens) mix Devanagari and Latin script for the same spoken content —
confirmed by `reports/dataset_profile.md`.

This is real customer data under NDA — used only for this assignment; delete
`data/` and `reports/` when done.

## Why not raw WER

Two concrete problems with naive string WER on this data, both directly
visible in the calls:

1. **Script choice is not an error.** `delhi` (gold) vs `दिल्ली` (ASR) is the
   same city, correctly recognized — see call `ebbb8d49...`. Naive WER
   counts this as a full substitution on both tokens. Same for `gurgaon` /
   `गुड़गांव` in call `2f5a58b5...`.
2. **A single dropped or garbled proper noun barely moves WER but can break
   the call.** In a beverage order, "aquafina" going missing entirely is one
   word out of ~200 (call `09c68660...`) — under 1% WER impact — but it
   means that SKU never gets ordered.

WER also has a real bug hazard here: Python's built-in `re` module's `\w`
under Unicode does **not** include Devanagari combining vowel signs/nukta
(Unicode categories Mn/Mc). A naive `re.sub(r"[^\w\s]", "", text)`
punctuation-stripper silently deletes those marks and corrupts every
Devanagari word before comparison (`गुड़गांव` → `ग ड ग व`). This project uses
the `regex` package instead, whose `\w` is Unicode-property-aware, verified
by inspecting normalized output against source text
(`src/normalization.py`). Any pipeline on this data that uses plain `re` for
"strip punctuation" is measuring the wrong thing without knowing it.

**What we do about it:**

- Normalization (`src/normalization.py`): NFC unicode normalization, strip
  `{noise}` markers, lowercase, strip punctuation (Unicode-aware, keeps
  Devanagari intact), collapse whitespace. Devanagari text is never
  transliterated or dropped.
- WER/CER (`src/wer_baseline.py`, via `jiwer`) is computed on this normalized
  text and reported **only as a diagnostic baseline** — it is not used to
  rank calls or entities anywhere downstream.
- Script/spelling equivalence (`delhi` = `दिल्ली`) is handled explicitly at
  the entity level (below), not by forcing all text into one script. Cases
  resolved this way are logged separately (`reports/gold_script_variants.csv`)
  precisely so we can show where naive WER would have misled — this is also
  the "gold is wrong" case the assignment describes: not the gold text being
  false, but naive comparison blaming a script choice as if it were an ASR
  error.

## What replaces WER

Nothing computes a single number to swap in for WER — the replacement is
**two purpose-built metrics, one per failure mode**, both defined in terms of
whether the call's goal survived rather than string distance:

- **Proper nouns → `recoverable`**: `True` if the entity is `CORRECT`, or
  `CORRUPTED` but still an almost-exact match (fuzzy score ≥`RECOVERABLE_THRESHOLD=81`,
  just under the `CORRECT`/`CORRUPTED` cutoff of 82); `False` if it's
  `DROPPED`, `ADDED`, or not close enough to match automatically. This
  directly answers "did the SKU/city/company name survive well enough for
  the call's goal to go through *without* a human stepping in" — not "how
  many characters differ from gold." See below for why this bar is set
  close to exact, not a looser "a human could probably guess it" line.
- **Short utterances → `content_preserved`** (`polarity_preserved` AND
  `quantity_preserved`): `polarity_preserved` is `True` only if **every**
  affirmative/negative category present in gold is still detectable in the
  padded/hallucinated ASR output (gold's polarity set must be a *subset* of
  ASR's, not just overlap with it) — a call can genuinely say both "yes" and
  "no" (real call `b2a6f783`, gold `"हाँ जी हाँ जी...नहीं नहीं"`), and if ASR
  drops the "no" entirely, a plain overlap check would wrongly call that
  preserved since "yes" is still shared between the two sets.
  `quantity_preserved` is `True` if every quantity phrase in gold
  (e.g. `"forty five percent"`) is still reproduced **verbatim** somewhere in
  the ASR output — not just "some number word overlaps," since a wrong value
  can share individual number words with the right one (`"twenty five
  percent"` shares "five" with `"forty five percent"` but is a different,
  wrong quantity) without being the same claim. Either one failing counts as
  real content loss, per the assignment's "whether the yes/no **or**
  quantity is still correct." Both signals are reported individually too
  (`step2_hallucination_impact.csv`), not just the combined verdict.

Concretely, this is why WER fails on this data (see "Why not raw WER"
above) and these two don't: call `09c68660` has WER 0.515 — below this
dataset's median — yet three SKUs (`Aquafina`, `Slice`, `Tetra Pack`) never
made it into the order; `recoverable=False` catches this immediately, WER
doesn't. Call `ebbb8d49` has WER 0.4 purely from `delhi`/`दिल्ली` being
different scripts; `recoverable=True` (correctly) ignores it. Across all 49
calls, the Pearson correlation between WER and the combined `impact_score`
these two metrics produce is **r = 0.035** — essentially zero.

## Architecture: Measure and Impact metric as separate scripts

Frequency/cost measurement and the impact-metric judgment are two
independent scripts, each writing its own non-overlapping set of report
files — keeping "what we measured" (how often) structurally separate from
"the metric's verdict" (how costly), matching the assignment's own two
distinct asks:

- **`run_measure.py` (Step 1)** — frequency of each failure mode, WER/CER as
  a diagnostic, which calls/entities drive the raw failure count, and the
  gold-wrong (script-variant) flag. Never computes or writes `recoverable`,
  `polarity_preserved`, `quantity_preserved`, or `impact_score`.
- **`run_impact_metric.py` (Step 2)** — `recoverable`, `polarity_preserved`,
  and `quantity_preserved`, rolled up per call into `impact_score`, reported
  before any correction. Never writes `wer`/`cer` or raw detection-frequency
  counts.

Both scripts independently recompute entity/hallucination detection from the
same cached LLM extractions (`data/llm_entity_cache.json`) rather than one
reading the other's output files — so each is a fully self-contained,
independently-runnable deliverable, not two halves of a single pipeline run.
`src/aggregation.py` has one clearly-named builder function per step
(`build_step1_*` / `build_step2_*`) enforcing which columns can reach which
step's report files.

## Failure mode 1: proper nouns / product names

**Approach:** LLM-based open-set extraction and cross-transcript alignment
(`src/llm_entities.py`, `src/entities_llm.py`, default backend). One model
call per call sees **both** transcripts at once and returns every proper
noun it judges business-relevant (product/brand/place/id/other), aligning
each entity across gold and ASR itself — e.g. it knows gold `delhi` and ASR
`दिल्ली` are the same city, something a string-similarity join can't do
across a script boundary. The model only reports facts (which spans exist,
which entity they belong to); `CORRECT`/`CORRUPTED`/`DROPPED`/`ADDED` and
`recoverable` are computed deterministically in Python from those aligned
spans (`src/entities_llm.py`), keeping model judgment (what exists) and
deterministic scoring (was it preserved) separate.

**Verification and script handling:**
- Every span the model returns is checked against the actual transcript
  (`_verify_span`) — the model is asked to copy spans verbatim but
  occasionally doesn't (e.g. can return a stray Devanagari character glued
  onto an otherwise-Latin word that isn't in the transcript at all).
  Unverifiable spans are recovered via fuzzy n-gram search over the
  transcript, or dropped if even the best match is too weak to trust.
- Script comparison uses a majority vote over a span's alphabetic characters
  (`_dominant_script`), not "contains any," so one stray character can't
  flip a whole span's script classification.
- When an aligned pair's two spans are in different scripts, it's trusted as
  `CORRECT` (`script_variant=True`) rather than character-fuzzy-scored —
  Devanagari and Latin text share no code points, so a same-boundary
  `fuzz.ratio` is meaningless and would misclassify every genuine script
  variant (`delhi`/`दिल्ली`) as `CORRUPTED`.
- A defensive filter drops any record with both spans null — the model
  sometimes echoes a hinted entity that doesn't actually appear in a call,
  reporting its *absence* rather than omitting it.

**Why LLM-based extraction, and why keep the gazetteer at all:** a
closed-vocabulary gazetteer alone only catches entities on its predefined
list — `cuppa` (gold) transcribed as `copper` (ASR) in call `ebbb8d49`
reads as an ordinary word out of context, isn't on any fixed brand list, and
is the only call containing that word in the whole dataset, illustrating why
a fixed catalog doesn't scale to novel entities. Open-set LLM extraction
instead finds whatever the model judges to be a salient proper noun. The
gazetteer (`configs/entity_gazetteer.yaml`, 7 beverage SKUs + packaging, 2
cities) is still useful, just not as the detector: kept as **optional
grounding hints** appended to the prompt (`--llm-hints gazetteer`, the
default) so known aliases canonicalize consistently — without shared
grounding, independent extractions can drift to different canonical names
for the same real entity (see the Bombay/Mumbai example below). A
`--llm-hints none` mode and a legacy `--entity-backend gazetteer` mode (the
closed-vocabulary fuzzy-match detector, `src/gazetteer.py` + `src/entities.py`)
remain available for comparison.

**Single-call joint alignment:** the model sees both transcripts in one
call and aligns entities across them itself, rather than extracting from
each transcript independently and joining by name afterward. A name-based
join is fragile against script/spelling variation: gold `बॉम्बे`/`bombay`
and ASR `bombay` (the same word, correctly transcribed) have no shared
context forcing agreement on a canonical name, so an independent-extraction
join could label one side `Bombay` and the other `Mumbai`, then read that as
a dropped/corrupted entity even though nothing was actually lost. Joint
extraction also halves API calls (49 instead of 98 per run).

**Model:** `openai/gpt-5.6-luna` via OpenRouter (`src/llm_entities.py` reads
whichever of `OPENROUTER_API_KEY`/`OPENAI_API_KEY` is set in `.env` and picks
`base_url`/model prefix accordingly). Structured output is enforced via
strict `json_schema` mode (`temperature=0`, `max_tokens=4096` capped
explicitly — this model otherwise defaults to requesting up to 65536, more
than a new OpenRouter account's balance covers for a short JSON entity
list). Results are cached to `data/llm_entity_cache.json`, keyed by
`call_id:hint_mode:model`, so re-running the pipeline doesn't re-spend API
calls and the shipped reports are reproducible without a live key.

**Classification per aligned entity** (Step 1 output, `reports/step1_entity_events.csv`):
both spans present + different script → `CORRECT` (script variant); both
spans present + same script → `CORRECT` if `fuzz.ratio` ≥
`ASR_MATCH_THRESHOLD=82` else `CORRUPTED`; gold span only → `DROPPED`; ASR
span only → `ADDED`. These thresholds are heuristic starting points from
manual inspection, **not tuned against a labeled set** (none exists yet —
see "What's next").

Same-script comparison is computed **token-by-token**
(`_token_script_match` in `src/entities_llm.py`), not over the whole span:
a multi-word entity can code-switch word-by-word rather than as a whole
mention — e.g. gold `Fruitz Litchi` transcribed as `fruits लीची` (first word
Latin, second word Devanagari). A per-token comparison lets a cross-script
token pair score as a match (unscorable character-by-character across the
boundary, and the model's own alignment already confirmed it's the same
entity), while same-script tokens still use `fuzz.ratio`, combined by a
character-length-weighted average. Falls back to a single whole-span
`fuzz.ratio` when the two spans don't tokenize to the same word count, since
positional pairing isn't reliable once a word has been added or dropped.

**Impact metric (assignment step 2, `reports/step2_entity_recoverability.csv`):**
`recoverable` — `True` for `CORRECT`, and for `CORRUPTED` mentions whose
match score is still ≥ `RECOVERABLE_THRESHOLD=81`; `False` for `DROPPED` and
`CORRUPTED` mentions further off than that. This is the per-call,
pre-correction cost number the assignment asks for, rolled up per call in
`reports/step2_per_call_impact.csv` — a separate file from Step 1's,
produced by a separate script (`run_impact_metric.py`, not
`run_measure.py`), so the frequency measurement and the cost judgment are
never blended into one table.

**Why `recoverable` requires an almost-exact match, not a looser "a human
could probably guess it" line:** `RECOVERABLE_THRESHOLD=81`, just under
`ASR_MATCH_THRESHOLD=82`, so `recoverable` answers whether the call's goal
would go through **automatically**, without a human in the loop — the more
defensible reading for a voice-agent order/booking flow, where a downstream
system needs the exact SKU/city string to act on, not a fuzzy human guess.
A looser bar would count most of this dataset's real corruptions as already
"fine" before any correction runs — including the two mentions Step 3
actually fixes — which would make Step 3's real, verified gain invisible on
the one metric the assignment asks to prove it against. At this bar, a
genuine fix (`seven of`/`seven a` → `seven up`) shows up as an actual
before→after `recoverable` change, not just a secondary
`fixed_to_exact_correct` flag (see Step 3 below).

**Result on this data:** 11/49 calls (22.4%) have a non-`CORRECT` entity
event (Step 1's frequency count); all 11 have at least one unrecoverable
mention (100% impact rate given affected — Step 2's cost judgment). Every
`CORRUPTED` entity in this dataset scores below `RECOVERABLE_THRESHOLD`
before any correction runs, so pre-correction, proper-noun impact and
frequency coincide here; Step 2's differentiating value shows up in *which*
of those mentions Step 3 can actually fix (below), not in separating
"affected" from "impacted" pre-correction. Concretely: `Aquafina` dropped
entirely from one order, `Slice`/`Tetra Pack`/`Mountain Dew`/`Fabina`/`Seven
Up` corrupted or dropped, and `Delhi` dropped from verification calls where
an NCR location check depends on it (`reports/step1_top_entities.csv`,
`reports/step2_top_calls_by_impact.csv`).

**Known limitations, disclosed rather than silently hidden:**
- `cuppa`/`copper` — the motivating case for building this layer — is
  **still missed**, on both hints and no-hints prompts. Root cause: out of
  context, `cuppa` reads as an ordinary word, not an obviously-a-brand
  token, and the prompt says to only report entities the model is confident
  are genuinely proper nouns. A lower confidence bar would likely worsen
  the over-extraction problem the hints/no-hints comparison found (generic
  phrases like "clothing company" getting tagged as entities).
- Trusting any cross-script alignment as automatically `CORRECT` can hide a
  genuine corruption that happens to land in the other script: call
  `a8afca71`, gold `Seven Up` aligned with ASR `seven अब` (Hindi for "now")
  is reported `CORRECT`/script-variant, but reads like an actual mishearing
  of "up," not a faithful transliteration the way `दिल्ली` is for `delhi`.
  No cheap fix without a real transliteration/phonetic-similarity check or
  a model-reported alignment confidence.
- `gold_score`/`asr_score` in `step1_entity_events.csv`/`step2_entity_recoverability.csv`
  mean different things depending on the match branch: for aligned/script-variant
  pairs they're hardcoded to `100.0` (not measured), and `asr_score` is only
  a real `fuzz.ratio` in the same-script fallback branch. Flagged rather
  than silently reclassified, since fixing it changes classification
  semantics.

## Step 3: Improve — correcting the proper-noun failure mode

**Why proper nouns, and why a text-only correction layer.** Proper nouns is the
highest-cost mode by impact-failure rate (100% vs hallucination's 0%). Of the
20 non-recoverable entity events, 10 are `DROPPED` — the word is completely
absent from the ASR output, not garbled — and a layer that only edits existing
text cannot correct a word that was never transcribed; that requires
re-transcribing the audio with ASR-side vocabulary boosting, which needs vendor
API access this project doesn't have (see "What's next"). This step targets
the other bucket: `CORRUPTED` entities that are present, just misspelled or
misheard.

**Design:** `src/correction.py` builds a correction vocabulary from the
existing 9-SKU/2-city gazetteer, but replaces its aliases with spellings
actually attested in the **training split's** gold text only (never the
held-out calls', and never the gazetteer's own hand-curated misspelling
list — see below for why). `find_corrections()` then fuzzy-matches ASR
n-gram windows against that vocabulary and rewrites high-confidence garbled
mentions, using only the ASR text — no gold access, matching a real
production setup.

**What was considered and rejected, and why:**
- **Extending the vocabulary beyond the gazetteer's closed catalog** by mining
  every entity the LLM found in training gold (`Pronto`, `WhatsApp`, `Silver
  Jewellery`, ...) — corrupts an unrelated `b2b sales outreach` call's "sir
  delivery" into "silver jewellery" mined from a *different* b2b call about a
  different business. Most non-SKU entities in this dataset are one-off,
  call-specific names, not a recurring catalog; applying one call's name to
  another call sharing only the same `use_case` is cross-call contamination,
  not generalization. Vocabulary stays restricted to the 9 SKUs + 2 cities.
- **The gazetteer's own hand-curated aliases** (`sevenup`, `7up`, `due`,
  `dwew`, `mirnda`, `slyce`, ...) — anticipated misspellings, never
  confirmed against real data. `sevenup` (concatenated) fuzzy-matches a bare
  digit word like "seven" in an unrelated quantity/phone-number reading well
  enough to wrongly rewrite it. Replaced with aliases mined only from each
  entity's own training-split gold text.
- **Single-word alias corrections** (`dew`, `slice`, `tetra`, ...) — real
  catches (`due`→`dew`=66.7, `life`→`slice`=66.7, `test`→`tetra`=66.7) score
  *identically* to real false positives (`size`, in "barah ka pack size hai",
  gold-confirmed as literally the word "size", scores 66.7 against `Slice`;
  `line`, in "aap line par hain" / "you're on the line", scores 66.7 against
  `Slice`; `liche`, a mishearing of "litchi", scores 80.0 against `Slice`) —
  numerically identical by coincidence, not by signal, and no fuzzy-match
  threshold or context rule separates them. A phonetic/orthographic
  similarity signal was also evaluated as a way to safely re-admit
  single-word matching (9 different metrics: 4 phonetic algorithms via
  `jellyfish`, plus 5 simple heuristics), and none separate the real catches
  from the false positives either — one metric even scores the worst false
  positive higher than every real catch, because standard phonetic
  algorithms encode English pronunciation rules, while the actual confusions
  here come from an ASR model's behavior on Hindi-accented, code-switched
  speech. Correction is restricted to **multi-word alias matches only**
  (`MIN_ALIAS_TOKENS=2`) — sacrifices the single-word catches, but is the
  only bar with zero false positives across the full-dataset audit.
  `tests/test_correction.py` locks in this scope and the other hand-audited
  guards (number-word-only exclusion, quantity-neighbor requirement, no
  vocabulary leakage from held-out gold) as regression tests — run with
  `python -m pytest tests/`.

**Result:** exactly 2 corrections fire across all 49 calls (`seven of`/`seven
a` → `seven up`, in calls `09c68660` and `a8afca71`) — small, but the product
of an exhaustive manual audit of every candidate correction the method
proposes, not a threshold picked in the abstract. Held out from vocabulary
construction (`run_improve.py`, stratified 80/20 split, seed 42): 10 calls,
including one with a `CORRUPTED` entity. On the held-out set: **0
already-CORRECT entities corrupted**, 0 recovered — both real corrections
happen to land in the training split under this split, a foreseen risk given
only 4 `CORRUPTED`-affected calls exist in the whole dataset. On the training
split (a sanity check, since the vocabulary is built from these calls' own
gold text): 0 already-CORRECT entities corrupted, and both real corrections
move a `CORRUPTED` mention (score 75/80) to exact `CORRECT`, crossing
`RECOVERABLE_THRESHOLD` from below — a genuine, verified **2/17
non-recoverable entities recovered (11.8%)**. The held-out split's 0/3
recovered reflects that neither real fix landed in held-out calls under this
particular split, not that the metric can't detect a gain — it demonstrably
does, on the split where a fixable error exists. Full numbers in
`reports/improve_summary.md`.

**Running it:** `python run_improve.py --input data/dataset.xlsx --output
reports/` writes `improve_summary.md`, `improve_held_out_events.csv`,
`improve_train_events.csv`.

## Failure mode 2: short utterances over-transcribed / hallucinated

The dataset has no turn timestamps, so individual short "turns" inside a
call can't be isolated — the finest granularity available is the whole-call
transcript. Two detectors approximate the described pattern at that
granularity (`src/hallucination.py`):

1. **Whole-call proxy.** Several calls in this set *are* effectively a
   single short turn (e.g. gold `"haan haan nahi kya"`). For those
   (`gold_token_count <= 6`), a much longer ASR with a high proportion of
   words absent from gold (`length_ratio >= 1.5` and `insertion_rate >= 0.3`)
   is exactly the described failure.
2. **Local repetition burst**, for hallucination inside longer calls. A
   naive "how repetitive is the ASR text" (1 − unique/total tokens) check
   triggers constantly on this data, because these are real voice-agent
   calls where genuine backchannel repetition ("yes yes yes", "ji ji ji")
   happens on both sides and gold has it too. The fix: only count
   repetition ASR added *beyond* what gold already justifies — find a token
   or 2-token cycle repeated back-to-back at least 3 times in ASR, subtract
   however many times gold repeats that same token/cycle, and flag when the
   leftover excess is large relative to the call
   (`REPETITION_RATIO_THRESHOLD=0.15`). This is what catches gold `"haan
   haan ji nahi kya"` → ASR `"haan nahi haan neetiya neetiya neetiya neetiya
   neetiya neetiya"` (`neetiya` has zero gold support) without flagging
   ordinary repeated backchannel.

**Impact metric:** two signals, combined into `content_preserved`, covering
both halves of the assignment's "whether the yes/no **or** quantity is still
correct" (`src/hallucination.py`):
- `polarity_preserved` — whether every affirmative/negative signal present in
  gold (small English+Hindi/Hinglish lexicon: yes/yeah/haan/हाँ/जी vs
  no/nahi/नहीं) is still detectable anywhere in the ASR output despite the
  padding.
- `quantity_preserved` — whether every quantity phrase in gold (a maximal
  run of consecutive number/unit words, e.g. `"forty five percent"`) is
  still reproduced **verbatim, as a contiguous subsequence**, somewhere in
  the ASR output. Exact-phrase matching, not per-word overlap, is
  deliberate: `"twenty five percent"` shares the word "five" with `"forty
  five percent"` but is a different, wrong value, and a set-overlap check
  would wrongly call that "preserved." Reuses the same
  `NUMBER_AND_QUANTITY_WORDS` list (`src/normalization.py`) shared with Step
  3's correction guards, extended with `percent`/`percentage`/`प्रतिशत`
  since that's the unit this dataset's own worked example uses.

`content_preserved = polarity_preserved and quantity_preserved`; either one
failing counts as real content loss.

**Result on this data:** 9/49 calls (18.4%) flagged as hallucination
candidates. Only one of them, `33720198`, has a gold quantity claim at all —
it's the dataset's version of the assignment's own worked example: gold
`"yes हिंदी forty five percent"` → ASR `"yes हिंदी english yeah yeah yes yes
forty five percent"`. The padding is real, but `"forty five percent"`
survives verbatim, so `quantity_preserved=True`. Combined with polarity, none
of the 9 candidates lost either signal (`impact_failed_pct_of_affected = 0`
for both) — the padding is real but the underlying content survived in-band
every time it was checked here; this should be read as a small-N observation
(9 calls, only 1 with a quantity to check), not a general claim.

## `impact_score`: how the two failure modes combine into one per-call number

`impact_score` (`step2_per_call_impact.csv`, `build_step2_impact_table()` in
`src/aggregation.py`) rolls both failure modes' impact judgments into a single
per-call cost number, used for `step2_top_calls_by_impact.csv` and the
concentration stat:

```
impact_score = (entity_non_recoverable × 2)
             + (hallucination_candidate × 1)
             + ((hallucination_candidate AND NOT content_preserved) × 2)
```

- **`entity_non_recoverable × 2`** — 2 points for every proper-noun mention in
  the call that's `DROPPED`, `ADDED`, or `CORRUPTED` below
  `RECOVERABLE_THRESHOLD`. Uncapped, so a call with 5 non-recoverable
  entities contributes 10, not a flat penalty.
- **`hallucination_candidate × 1`** — a flat point just for being flagged a
  hallucination candidate at all (padding/repetition detected), independent
  of whether the content survived.
- **`(hallucination_candidate AND NOT content_preserved) × 2`** — an
  *additional* 2 points, but only when the call is a hallucination candidate
  **and** the yes/no or quantity was actually lost. Gated on
  `hallucination_candidate` deliberately: without that gate, a long
  multi-item order call could fail `quantity_preserved` purely as a side
  effect of proper-noun corruption already counted in the first term,
  double-billing one real failure under two different failure modes' scores.

**Worked example** — call `09c68660`: `entity_non_recoverable=5`,
`hallucination_candidate=False` → `impact_score = 5×2 + 0×1 + 0×2 = 10`.

**Caveat, stated plainly:** the weights (2, 1, 2) are a hand-picked
heuristic, not tuned against any labeled ground truth — same status as
`LOW_SCORE`/`RECOVERABLE_THRESHOLD`/the other thresholds in this project.
There's no evidence "one non-recoverable entity" and "content lost in a
hallucination candidate" should be weighted equally; it's a reasonable
default, not a validated one. Also worth knowing: the third term has never
actually fired in this dataset — all 9 hallucination candidates currently
have `content_preserved=True`, so `impact_score` today is effectively driven
entirely by the first two terms.

## What we deliberately did not build yet

- **Audio features** (duration, energy, SNR) for the hallucination detector
  — text/alignment signals already reproduce the dataset's actual
  hallucination examples without needing to download and process 49 audio
  files. Worth adding if the goal shifts to a learned classifier rather
  than a rule-based one.
- **Independent re-transcription for gold validation** — flagging genuine
  gold transcription errors (not just script variants) needs a second ASR
  pass over the audio, a bigger, separate task. What's built here only
  flags the specific case the assignment itself calls out: places where
  naive comparison would blame ASR for a script/spelling choice gold
  happened to make (`reports/gold_script_variants.csv`).
- **Full use-case slot schemas / goal-survival engine** — the assignment's
  impact metric ("is the entity recoverable", "is the yes/no or quantity
  still correct") is answered directly per failure mode above without
  needing a general slot-extraction pipeline. A formal goal schema per
  `use_case` would generalize this further but is more machinery than this
  step needs.
- Numbers/IDs (the optional third failure mode) — the data does show a
  clear example (a retailer PIN spoken as different digit sequences in gold
  vs ASR in a beverage call), but effort went to making the two required
  modes correct first.

## Tools/libraries used

`pandas`, `openpyxl` (load), `jiwer` (WER/CER diagnostic), `rapidfuzz`
(fuzzy entity matching / span verification), `regex` (Unicode-correct
punctuation stripping), `PyYAML` (gazetteer hints config), `tabulate`
(markdown report tables), `openai` Python client + `python-dotenv` (LLM
entity extraction via OpenRouter, `openai/gpt-5.6-luna`, strict
`json_schema` structured output), `pytest` (regression tests,
`tests/test_correction.py`, `tests/test_hallucination.py`). No audio was
downloaded/processed. `jellyfish` (phonetic algorithms) was evaluated for
the single-word-alias correction question but not adopted — see "Step 3:
Improve" above — so it's not a project dependency.

## Running it

Step 1 (Measure), Step 2 (Impact metric), and Step 3 (Improve) are three
separate scripts, each writing its own set of report files.

```bash
pip install -r requirements.txt
python run_measure.py --input data/dataset.xlsx --output reports/        # Step 1
python run_impact_metric.py --input data/dataset.xlsx --output reports/  # Step 2
python run_improve.py --input data/dataset.xlsx --output reports/        # Step 3
python -m pytest tests/                                                  # regression tests
```

Needs `OPENROUTER_API_KEY` (or `OPENAI_API_KEY`) in `.env` for the entity
extraction step — or just rely on `data/llm_entity_cache.json`, which
already has every call cached, so a fresh checkout reproduces `reports/`
without any key or API spend. `run_measure.py` flags:

- `--entity-backend {llm,gazetteer}` — `llm` (default, open-set extraction)
  or `gazetteer` (legacy closed-vocabulary fuzzy-match detector).
- `--llm-hints {gazetteer,none}` — only applies to the `llm` backend;
  `gazetteer` (default) passes known canonical names as grounding hints,
  `none` is pure model judgment with no predefined list.

**Step 1 outputs** (`run_measure.py`): `dataset_profile.md`,
`step1_measure_summary.md` (WER, failure-mode frequency, top calls by raw
failure count, top entities, use-case breakdown), `step1_per_call.csv`,
`step1_entity_events.csv`, `step1_hallucination_events.csv`,
`step1_failure_frequency_summary.csv`, `step1_proper_noun_by_use_case.csv`,
`step1_top_calls_by_frequency.csv`, `step1_top_entities.csv`,
`gold_script_variants.csv`. None of these contain `recoverable`,
`polarity_preserved`, `quantity_preserved`, or `impact_score`.

**Step 2 outputs** (`run_impact_metric.py`): `step2_impact_summary.md`
(metric definitions, impact-failure rates, top calls by cost),
`step2_per_call_impact.csv`, `step2_entity_recoverability.csv`,
`step2_hallucination_impact.csv`, `step2_failure_impact_summary.csv`,
`step2_proper_noun_impact_by_use_case.csv`, `step2_top_calls_by_impact.csv`.
None of these contain `wer`, `cer`, or raw detection-frequency counts.

**Step 3 outputs** (`run_improve.py`): `improve_summary.md`,
`improve_held_out_events.csv`, `improve_train_events.csv`.

## Reasoning: sub-600ms latency budget, sync vs async, and what I'd change first

The correction layer runs fully synchronously, in the same turn as the ASR
output it corrects — `find_corrections()` is pure string comparison, no
network call or model inference, measuring 0.04ms median / 1.6ms max per
call, orders of magnitude inside the 600ms budget with room for ASR
streaming, NLU, and response generation in the same turn. It has to be
synchronous, since the agent needs the corrected entity immediately to act
on the order before the turn ends; anything genuinely slower (an LLM call, a
trained model) belongs after the turn — which is why the LLM-based ideas
below (background vocabulary growth, an NER model on synthetic data) are
async proposals, not synchronous ones. With fuller API/infra access, the
single thing I'd change first is ASR-side vocabulary/keyword boosting
(re-transcribing with the vendor's vocabulary feature), not a better
correction algorithm: half of all non-recoverable proper-noun failures here
(10 of 20 events) are `DROPPED` — never transcribed at all — which no
post-hoc text correction, rule-based or ML, can ever fix, since there's no
text left to correct. Boosting fixes it at the acoustic source instead of
reconstructing a missing word after the fact, and is the only lever here
that reaches that bucket at all.

## What's next

1. Add a small human-labeled validation set (even 20-30 examples) to tune
   the fuzzy-match and repetition thresholds against, and to report actual
   precision/recall rather than face-valid heuristics.
2. **ASR-side vocabulary/keyword boosting** (re-transcribing with the
   vendor's custom-vocabulary feature, needs the scoped API access the
   assignment offers) — the only lever that reaches the `DROPPED` bucket
   (10/20 non-recoverable events, half the disclosed cost), which is
   structurally out of reach for any text-only post-processing. See the
   reasoning paragraph above for why this is the first thing, not a
   nice-to-have.
3. **Train a small NER/disambiguation model on LLM-synthesized data**, to
   revisit single-word alias matching (`due`/`life`/`test` — real catches,
   currently excluded because they score identically to real false
   positives like `size`/`line`, and because 49 calls is too little data to
   train or validate any model against — see "Step 3: Improve" above for
   the fuzzy-match and phonetic-distance audits that hit the same root
   cause). The fix for "too little data" is more data, not a cleverer rule
   on the same tiny sample — but it only works if the synthetic corruptions
   are generated *conditioned on the real confusions already found* (the
   9-example audit trail described under "Step 3: Improve" above), not
   invented generically, and validated against real held-out examples
   before being trusted — an LLM asked to "imagine ASR errors" with no
   grounding in this specific ASR engine's actual acoustic behavior on
   Hindi-accented, code-switched speech risks teaching the model a
   plausible-looking but wrong error distribution, which would look great
   on its own synthetic held-out set and still fail on real calls.
4. A background job that keeps mining new aliases (and, more cautiously, new
   recurring canonical entities) from live traffic via async LLM
   extraction — the direct fix for this project's data-scarcity ceiling,
   but needs a real "gold" substitute in production (no human transcript
   exists per-call the way this dataset has one), a recurrence threshold
   before trusting a new entity (guarding against the cross-call
   contamination risk noted under "Step 3: Improve" above), and versioned
   vocabulary snapshots so a bad update is traceable and revertible.
5. The single-word alias matches this layer deliberately excludes
   (`due`/`life`/`test`) would need a phonetic/acoustic similarity signal
   instead of plain text fuzzy-matching to safely re-admit — evaluated with
   9 different metrics and ruled out (see "Step 3: Improve" above); items
   3-4 above are the two remaining paths that could still work.
