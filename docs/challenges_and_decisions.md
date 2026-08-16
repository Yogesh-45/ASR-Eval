# Challenges & Decisions Log

A running record of non-obvious problems hit while building this pipeline and the
reasoning behind how each was resolved. Kept so the "why" behind the current code isn't
lost — append a new entry whenever a real obstacle or judgment call comes up, don't edit
past entries except to record their outcome.

---

### 2026-08-15 — Gazetteer approach doesn't scale past a hand-reviewed dataset

**Challenge:** the original proper-noun detector (`src/entities.py` +
`configs/entity_gazetteer.yaml`) only checks a fixed, hand-curated list of 9 entities. A
real corruption — `cuppa` (gold) transcribed as `copper` (ASR) in call
`ebbb8d49-de7b-4d30-a845-ef68c74a980a` — was completely invisible to it, because nobody
had added `Cuppa` to the list. It's the only call containing that word in the whole
49-row dataset, so it was never noticed while building the gazetteer.

**Decision:** move proper-noun detection to an LLM-based open-set extraction pass
(`src/llm_entities.py`) that finds entities the model judges salient, not just ones from a
predefined list. Keep the gazetteer, but repurpose it as optional grounding hints fed into
the prompt (so known aliases like `mirnda`/`morindo` still canonicalize to `Mirinda`
consistently) rather than as the detector itself. Comparison between gold/ASR extractions
stays deterministic Python (`src/entities_llm.py`), per the plan's guidance that the LLM
should extract facts, not judge correctness.

---

### 2026-08-15 — No working LLM API credentials in the execution sandbox

**Challenge:** building the LLM layer needs a real API call, but the sandbox had no
`OPENAI_API_KEY`/`ANTHROPIC_API_KEY` set, and once a key was added to `.env`
(`OPENAI_API_KEY`), the account had zero credits (`insufficient_quota` / 429 from the API).

**Decision:** asked the user directly rather than guessing — offered to act as the
extractor myself and cache results, wait for a funded key, or ship the code unexecuted.
User chose "build it against `.env`, they'd fund/run it later." When the OpenAI key turned
out to have no credits, stopped and waited rather than silently falling back to a stub.

---

### 2026-08-15 — Switched credential from OpenAI to OpenRouter mid-stream

**Challenge:** the user later added `OPENROUTER_API_KEY` to `.env` instead of fixing the
OpenAI key. OpenRouter is OpenAI-API-compatible but needs a different `base_url` and
provider-prefixed model names (`openai/gpt-4o-mini`, not `gpt-4o-mini`).

**Decision:** made `src/llm_entities.py` detect whichever key is present
(`OPENROUTER_API_KEY` preferred, `OPENAI_API_KEY` as fallback) and set `base_url` +
default model name accordingly, so no manual code changes are needed if the credential
source changes again. Verified with a real call (not just import-time checks) before
trusting it, including the strict `json_schema` structured-output mode the pipeline
actually uses (not just a plain chat completion), since schema strictness support is less
consistently supported across providers/models than basic chat.

---

### 2026-08-15 — Does the gazetteer still serve a purpose once an LLM does extraction?

**Challenge:** open question from the user — if the LLM extracts entities well on its own,
is the hand-curated list dead weight?

**Decision:** keep it, but only as optional grounding context, added a
`--llm-hints {gazetteer,none}` flag to make the choice explicit and comparable. Made the
extractor's cache key hint-mode-aware (`call_id:side` vs `call_id:side:no_hints`) so
toggling the flag can never silently return a result generated under the other mode.

---

### 2026-08-15 — Removing the hints looked like a big recall win, but wasn't (mostly)

**Challenge:** ran the full 49-call batch both with and without gazetteer hints.
Proper-noun affected calls went from 7/49 (14.3%) to 15/49 (30.6%) with hints removed —
looked like the grounding was suppressing real findings. Investigating individual calls
showed most of the gap was noise, not recall:

1. **Cross-call canonical drift** — the gold and ASR sides are extracted by two
   independent LLM calls with no shared context. Without an anchor list, the same
   real-world entity can get a different canonical name on each side (e.g. gold →
   `Bombay`, ASR → `Mumbai`, same literal word `bombay` correctly said on both sides).
   The deterministic comparator joins by exact canonical-string match, so this reads as a
   dropped/corrupted entity even though nothing was lost.
2. **Multi-word span fragmentation** — one side extracts a long compound span
   (`"Fru Fruit Apple"`), the other splits the same referent into several shorter spans
   (`"Apple"`, `"Litchi"`, `"Fruits"`) — again breaks the canonical-name join.
3. **Over-inclusive extraction bar** — without a reference list, the model also tagged
   generic descriptive phrases as "entities" (`clothing company`, `silver jewellery export
   leather saddlery goods`) that aren't proper nouns in any useful sense.

One genuine (non-noise) extra catch did surface: `Fabina`, a brand name dropped in call
`5a7d6b58`, found only without hints.

**Decision:** keep `--llm-hints gazetteer` as the default/authoritative backend (already
was). Treat the no-hints run as a diagnostic surface for finding gazetteer-hint candidates
(like `Fabina`), not as a replacement report. Full write-up:
`reports/hints_vs_no_hints_comparison.md`.

---

### 2026-08-15 — `gold_score`/`asr_score` mean different things depending on the match branch

**Challenge:** in the LLM backend, `gold_score` is always hardcoded to `100.0` (or `0.0`
for `ADDED` rows) — it's never actually computed, unlike the old gazetteer backend where
it was a real fuzzy score. `asr_score` is `100.0` by convention when a canonical-name
match is found (not a text-similarity measurement at all), and only a real
`rapidfuzz.fuzz.ratio` score when no canonical match was found and it falls back to
fuzzy-matching the gold span against raw ASR text. This produced a confusing case: the
Bombay/Mumbai drift above scores `asr_score=100.0` under `status=CORRUPTED`, because a
100% raw-text match doesn't get promoted back to `CORRECT` once the canonical-name check
has already failed.

**Decision:** not yet implemented — flagged to the user as worth fixing (reclassify a
near-100 fallback fuzzy score as `CORRECT` rather than `CORRUPTED`, or add a second,
fuzzy canonical-name matching pass before falling back to raw text). Logged here rather
than silently fixed so the tradeoff is visible before changing the classification logic.

---

### 2026-08-15 — `cuppa`/`copper` — the motivating case — is still not caught

**Challenge:** the entity corruption that originally motivated building the LLM layer is
still missed by both the hints and no-hints modes on the actual model used
(`openai/gpt-4o-mini`). Root cause isolated to the model's own confidence filter: out of
context, `cuppa` reads as an ordinary word, not an obviously-a-brand-name token, and the
prompt explicitly says "only return entities you are confident are genuinely named/proper
nouns."

**Decision:** left as a known limitation rather than forcing the prompt to be
maximally permissive, since a lower confidence bar would likely worsen the
over-extraction problem seen in the no-hints comparison above. Candidate fix (not yet
tried): explicitly instruct the model to flag words that seem semantically out of place
in the sentence as low-confidence candidates, with a separate confidence field, rather
than a binary include/exclude decision.

---

### 2026-08-15 — Rebuilt entity extraction around single-call cross-transcript alignment

**Challenge:** the two-independent-extractions-then-join-by-canonical-name design (see the
"cross-call canonical drift" entry above) was fundamentally fragile against script/spelling
variation, because there was no shared anchor forcing gold-side and ASR-side extraction to
agree on a name for the same real-world entity.

**Decision (user's proposal):** extract from both transcripts in a single call instead, and
have the model align entities across sides itself (world/script knowledge a string
algorithm can't reproduce), while keeping the actual CORRECT/CORRUPTED severity decision in
deterministic Python (`fuzz.ratio` on the aligned span pair against a threshold) — the LLM
supplies facts (including cross-script identity), Python still judges correctness. Rewrote
`src/llm_entities.py` (`extract_and_align_entities_llm`, one call per transcript pair instead
of two) and `src/entities_llm.py` (`detect_entity_events_llm` now consumes aligned records).
Halves API calls too (49 calls instead of 98).

Two real bugs surfaced while verifying the rewrite against the exact cases it was meant to
fix, both caught before shipping:

1. **Character-level fuzzy scoring silently broke script-variant handling.** `delhi`
   (gold) vs `दिल्ली` (ASR) — the flagship "gold isn't wrong, it's a different script" case
   from the assignment brief — started scoring `asr_score=0.0`/`CORRUPTED`, because
   Devanagari and Latin text share no Unicode code points, so `fuzz.ratio` across that
   boundary is always near-zero regardless of transliteration accuracy. Fixed: when the
   aligned gold/ASR spans differ in script, trust the model's alignment as `CORRECT`
   (`script_variant=True`) rather than fuzzy-scoring the raw text.
2. **Phantom hint-echo entities.** The model sometimes echoed a gazetteer-hinted entity
   name with `gold_span=null, asr_span=null` — i.e. reporting an entity's *absence* from a
   call rather than leaving it out — which the classifier then mis-scored as `ADDED` with a
   blank span. Fixed with a defensive filter (skip any record with both spans null) plus a
   tightened prompt telling the model explicitly not to report hinted entities that don't
   actually appear in that call.

**New known limitation surfaced by the fix for (1):** trusting *any* cross-script alignment
as automatically correct also hides a genuine corruption that happens to land in the other
script. Call `a8afca71`: gold `Seven Up` aligned with ASR `seven अब` (`अब` means "now" in
Hindi) is now reported `CORRECT`/script-variant — but this reads like an actual ASR
mishearing of "up," not a faithful transliteration like `दिल्ली` is for `delhi`. No cheap
fix without a real transliteration/phonetic-similarity check or a model-reported alignment
confidence; left as a documented tradeoff rather than solved.

---

### 2026-08-15 — Extraction-fidelity and script-detection bugs found from a single reported row

**Challenge:** inspecting one `entity_events.csv` row (call `2f5a58b5`, `Gurgaon`) surfaced
two compounding bugs. The model was asked to copy `gold_span`/`asr_span` verbatim from the
transcripts, but for this call it returned `gold_span="गurgaon"` -- one stray Devanagari
character (`ग`) glued onto an otherwise-Latin word -- when gold literally just says
`gurgaon`. That hallucinated span doesn't appear in either transcript at all. Worse, the
stray character then fooled the script-variant check (`_has_devanagari`, an `any()` over
characters): since both `"गurgaon"` and the real ASR span `"गुड़गांव"` "contain Devanagari,"
the classifier treated them as same-script and ran a character-level `fuzz.ratio` between
them, which scored near zero (they barely overlap) -- misclassifying a legitimate
script-variant match as `CORRUPTED`.

**Decision:** two independent fixes, both in `src/entities_llm.py`:
1. `_verify_span()` -- confirm each span the model returns is an actual (normalized)
   substring of its transcript; if not, recover the real substring via fuzzy n-gram search
   over the transcript, or drop the span (treat as unconfirmed) if even the best match is
   too weak to trust.
2. `_dominant_script()` -- replaced the `any()`-based Devanagari check with a majority vote
   over the span's alphabetic characters, so one stray character from extraction noise
   can't flip a whole span's script classification in either direction.

Re-ran the full batch afterward and confirmed the three flagship script-variant cases
(`Delhi`/`दिल्ली`, `Bombay`/`bombay`, `Gurgaon`/`गुड़गांव`) are all now correctly `CORRECT`.
Also swept every row for stray-character mixed-script spans post-fix; the handful still
mixed-script (`KC नगर`, `fruits लीची`) are genuine code-switched substrings verified against
the real transcript, not hallucinated artifacts.

---

### 2026-08-15 — Switched model to `openai/gpt-5.6-luna` (via OpenRouter)

**Challenge:** asked to use "gpt luna" instead of `gpt-4o-mini`, a model name not in my own
training data (post-dates knowledge cutoff). Rather than guess, queried OpenRouter's live
`/models` endpoint directly and found an exact match: `openai/gpt-5.6-luna` (and a `-pro`
variant). Switching immediately hit two operational issues that would have failed silently
or crashed the batch run:
1. The model defaults to requesting up to 65536 max completion tokens when `max_tokens` is
   left unspecified, which the OpenRouter account's balance couldn't cover (`402
   insufficient credit for requested max_tokens`) even though the actual output (a short
   JSON entity list) needs a tiny fraction of that.
2. OpenRouter's new-account tier caps this specific model at 10 requests/minute; running
   the 49-call batch without throttling hit `429` rate-limit errors partway through.

**Decision:** capped `max_tokens=4096` explicitly (comfortably above what this task's
output ever needs). Added retry-with-backoff (`_MAX_RETRIES=6`, `_RETRY_DELAY_SECONDS=8`)
around the rate-limit error specifically, rather than failing the whole batch on the first
429. Also made the cache key model-aware (`call_id:aligned:<model>`) so switching models
again in the future can never silently return a stale result cached under a different
model's name -- the same pattern already used for the hints/no-hints toggle.

---

### 2026-08-16 — Step 3 correction layer: an early version corrupted unrelated calls badly

**Challenge:** built a first version of the proper-noun correction layer (`src/correction.py`)
that fuzzy-matched every ASR window against every vocabulary alias and rewrote anything
scoring in a broad band. Running it over the full 49-call dataset before trusting it (rather
than just the held-out split) surfaced real damage: 16 edits applied to a single call,
including corrupting a spelled-out numeric ID (`2306150615`) and replacing the ordinary word
`liter` with `Slice`. Root causes, found by tracing each bad edit:
1. Bigram windows were compared against unigram aliases (`tetra` vs `tetra एक`), so
   "correcting" a match sometimes deleted an adjacent, unrelated word instead of just fixing
   a misspelling.
2. A correct bigram mention (`seven up`, scoring 100) didn't block its own two words from
   separately matching unrelated unigram aliases (`seven`->`sevenup`, `up`->`7up`), because
   only in-band [LOW,HIGH) candidates were tracked -- a perfect match was never added as a
   candidate at all, so it couldn't reserve its token span.

**Decision:** two structural fixes: only compare a window against an alias of the identical
token count (removes the insertion/deletion risk entirely, rather than trying to tune a
threshold around it), and track near-perfect (>=HIGH_SCORE) matches as "protect-only" --
they reserve their span before weaker candidates are considered, without themselves being
rewritten.

---

### 2026-08-16 — Extending the correction vocabulary by mining every call's gold entities was unsafe

**Challenge:** to reach entities beyond the original 9-item gazetteer (e.g. `Pronto`,
`WhatsApp`, `Silver Jewellery`, mined from the gold side of Step 1/2's entity events), an
early version added every entity the LLM had ever extracted in the training split as a new
correction target. Auditing the full-dataset sweep again found this corrupted a
`b2b sales outreach` call's `sir delivery` into `silver jewellery` -- an entity mined from a
*different* b2b call about an unrelated business. Unlike the beverage SKUs (the same ~9
products recur in every beverage-order call), most `other_proper_noun`/`brand` entities in
this dataset are one-off, call-specific names: a particular client's company, university, or
app, not a recurring catalog. Applying one call's mined name as a correction candidate to a
different call sharing only the same `use_case` is cross-call contamination, not
generalization.

**Decision:** restricted the correction vocabulary's canonicals to the existing gazetteer's
closed catalog only (9 beverage SKUs + 2 cities) -- confirmed by manual review to actually
recur across many calls, which is what makes applying one call's spelling to another call
sound in the first place. Separately, also dropped the gazetteer's own hand-curated alias
list (`sevenup`, `7up`, `due`, `dwew`, `mirnda`, `slyce`, ...) in favor of aliases mined only
from that canonical's own training-split gold text -- the hand-curated list was written as
*anticipated* ASR-side misspellings for the Step 1/2 detector, never confirmed against real
data, and `sevenup` (concatenated, no space) turned out to fuzzy-match a bare digit word like
"seven" in an unrelated quantity/phone-number reading well enough to wrongly rewrite it.

---

### 2026-08-16 — Single-word alias corrections: real catches and false positives score identically

**Challenge:** even after the fixes above, single-word alias matches (`dew`, `slice`,
`tetra`, ...) kept finding real corruptions (`due`->`dew`=66.7, `life`->`slice`=66.7,
`test`->`tetra`=66.7) alongside false positives at the *same* score: `size` (in "barah ka
pack size hai", gold-confirmed as literally the word "size") and `line` (in "aap line par
hain", i.e. "you're on the line") both scored 66.7 against `Slice`; `liche` (a mishearing of
"litchi"/lychee, not "Slice") scored 80.0; `testi` (unrelated) scored 60.0 against `Pepsi`.
Two guards were added and did narrow this (reject a window made entirely of number words, so
"seven half"/"seven fifty" -- both quantities, e.g. 7.5L/750ml -- stop fuzzy-matching "Seven
Up"; require a number/unit word immediately adjacent, confirmed against this dataset's actual
Hindi/Devanagari numeral spellings, not just English ones), but no threshold or rule found
could separate `life`/`test`/`due` (real) from `line`/`size`/`liche`/`testi` (false) -- they
are numerically identical by coincidence, not by signal.

**Decision:** rather than ship a rule set overfit to this exact ~9-example audit sample,
excluded single-word aliases from correction entirely (`MIN_ALIAS_TOKENS=2`). Multi-word
alias matches (`seven up`) had no false positive anywhere in the full-dataset audit once the
guards above were added, and remain the only correction target. This sacrifices real recall
(the `due`/`life`/`test` catches) for a much smaller but fully-audited, zero-false-positive
result -- a deliberate precision-over-recall call given the assignment's explicit warning
against "trading 10 errors for 8 new ones."

---

### 2026-08-16 — The held-out evaluation harness had its own bugs, separate from the corrector

**Challenge:** two bugs in `run_improve.py`'s scoring (not `src/correction.py`'s correction
logic) initially produced impossible results -- entities changing classification in calls
where zero corrections had been applied at all:
1. `score_gold_span_against_text` capped its n-gram search window at 3 tokens regardless of
   the gold span's real length, so a 9-token spelled-out numeric ID (`"two three zero six one
   five zero six one five"`) was compared against 3-token windows and scored near zero,
   making an untouched, already-CORRECT entity look freshly corrupted.
2. Every tracked entity in a call was rescored against the *whole* corrected transcript via
   brute-force fuzzy search, even entities no correction had touched. A bare `fuzz.ratio`
   scan doesn't know about `entities_llm.py`'s cross-script trust rule, so genuine
   script-variant CORRECT matches (`Delhi`/`दिल्ली`, `Gurgaon`/`गुड़गांव`, `Neha`/`नेहा`) came
   back "CORRUPTED"; and since some window in a 50-250 token transcript always scores
   nonzero against any short alias by chance, DROPPED entities (`Woxen University`, `Pronto`,
   ...) came back falsely "recovered."

**Decision:** removed the arbitrary max_n cap (transcripts here are short enough that
searching the full gold-span length is still cheap), and restricted rescoring to only the
specific entities a correction actually targeted (`{c["canonical"] for c in corrections}`) --
everything else in a call keeps its Step 1/2 status unchanged by construction, since nothing
about its text could have changed. This removed both bugs at the root instead of chasing
further threshold or rule tweaks in the rescoring function itself.

---

### 2026-08-16 — Final Step 3 result: correct, tiny, and the metric hides part of the truth

**Result after all fixes:** exactly 2 corrections fire across the full 49-call dataset (both
`seven of`/`seven a` -> `seven up`, in calls `09c68660` and `a8afca71`), zero false positives
found in an exhaustive audit of every call, and zero already-CORRECT entities corrupted on
either the training or held-out split. Both real corrections happened to land in the training
split under the seed=42 stratified split (`run_improve.py:SPLIT_SEED`), so the held-out
split's own numbers show 0 recovered / 0 corrupted -- a foreseen risk of only having 4
CORRUPTED-affected calls total, disclosed to and accepted by the user before this was built,
not something to reseed away.

A subtler finding: on the training split, both real fixes move `CORRUPTED` (score 75/80)
straight to `CORRECT`, but neither counts as `recovered` under the strict Step 2 metric,
because a CORRUPTED mention scoring >=65 already counts as `recoverable` -- the binary metric
doesn't distinguish "a human could probably infer this" from "this is now exactly right."
Reported both numbers (`recovered` and a separate `fixed_to_exact_correct`) rather than let
the metric alone hide a real, verified fix.

---

### 2026-08-17 — Step 1 and Step 2 were one script; split into two

**Challenge:** the user asked to verify whether Step 1 (Measure) and Step 2 (Impact metric)
had actually been implemented as separate deliverables, or quietly combined. Checking
confirmed the latter: `run_measure.py` computed WER, ran entity/hallucination detection, AND
computed `recoverable`/`polarity_preserved`/`impact_score` all in one pass, writing everything
into a single `per_call.csv`. This wasn't a mistake introduced later -- it was the original
design from before this session, and defensible on one reading (Step 1's own wording asks to
tie "cost" to whether the call's goal survives, which needs the same signal Step 2 formally
defines) -- but it meant there was no way to see "here's what Step 1 alone found" apart from
"here's Step 2's verdict," which is what the user actually wanted checkable.

**Decision:** split into two independent scripts, `run_measure.py` (Step 1) and the new
`run_impact_metric.py` (Step 2), each writing its own non-overlapping set of report files
(`step1_*` / `step2_*` prefixes). Both recompute entity/hallucination detection independently
from the same cached LLM extractions (`data/llm_entity_cache.json`) rather than one reading
the other's output files -- costs no extra API calls (the cache absorbs it) and keeps each
script genuinely standalone rather than two halves of one pipeline run. `src/aggregation.py`
got one builder function per step (`build_step1_*` vs `build_step2_*`) so which columns reach
which file is enforced in code, not just by convention. Verified the split didn't change any
number: re-ran both scripts and confirmed every headline figure (11/49 affected, 90.9% impact
rate, WER 0.797, etc.) matches what the combined version had produced.

---

### 2026-08-16 — Whole-span script vote misclassified a within-entity code-switch

**Challenge:** call `a8afca71`'s `Fruitz Litchi` mention was scored `CORRUPTED`/unrecoverable
(`asr_score=50.0`) even though the ASR text (`fruits लीची`) is a correct transliteration --
just split word-by-word across scripts (`fruits` Latin, `लीची` Devanagari) instead of the
whole mention switching script together. `_dominant_script()` in `src/entities_llm.py` votes
on the *whole span's* character counts (6 Latin vs 2 Devanagari alphabetic chars), so it
called this pair "same script" as gold's fully-Latin `fruitz litchi`, which routed it into the
same-script branch and ran a single character-level `fuzz.ratio` straight across a Latin/
Devanagari boundary -- exactly the "meaningless comparison" the cross-script branch exists to
avoid, just missed because the vote is per-span, not per-word.

**Decision:** added `_token_script_match()`: tokenize both spans, and only when at least one
*token pair* actually differs in script, score that pair 100 (unscorable across the boundary,
and the model's own alignment already confirmed same entity) while same-script token pairs
still get `fuzz.ratio`, combined by a character-length-weighted average (not a plain mean --
an early version weighted each token equally regardless of length, which regressed ordinary
corruptions like `seven up`/`seven a`, dragging a genuinely-80%-similar pair down to 50 just
because its short second word "up"/"a" scored 0 and got the same vote as the long, correct
first word). Falls back to the original whole-span `fuzz.ratio` whenever there's no
cross-script token pair to fix, or the two spans don't tokenize to equal word counts (position
pairing isn't reliable once a word is added/dropped) -- verified this fallback reproduces the
old score exactly for every previously-correct classification (`seven up`/`seven a`=80.0,
`seven up`/`seven of`=75.0, `slice`/`life`=66.7, `fruitz apple`/`fruits apple`=91.7), so the
fix only touches the specific bug.

**Result:** `Fruitz Litchi` now scores 91.7/`CORRECT`/recoverable. Re-ran the full pipeline
(`run_measure.py`, `run_impact_metric.py`, `run_improve.py`) since `run_improve.py` shares
`detect_entity_events_llm`. Step 2 headline moved from 10/11 (90.9%) to 9/11 (81.8%) affected
calls with an unrecoverable entity; total non-recoverable entity events across the dataset
dropped from 16 to 15 (the one `Fruitz Litchi` event). Step 3's 2 real corrections
(`seven of`/`seven a` -> `seven up`, calls `09c68660`/`a8afca71`) are untouched, since neither
involves a cross-script token. Noted in passing, not investigated further (out of scope for
this fix): the existing "known limitations" bullet in the README describing call `a8afca71`'s
`Seven Up` as aligned to ASR span `seven अब` and misclassified `CORRECT`/script-variant
doesn't match the current cached LLM extraction for that call (which aligns to `seven a` and
was already `CORRUPTED`, both before and after this fix) -- looks like stale documentation
from an earlier cache/prompt version, not something this change introduced or fixed.

---

### 2026-08-16 — `RECOVERABLE_THRESHOLD=65` made Step 3's real gain invisible on Step 2's own metric

**Challenge:** the user asked why `improve_train_events.csv` showed `Mountain Dew`
(`dew`/`due`, score 66.7) as `before_status=CORRUPTED, before_recoverable=True` and unchanged
after correction -- their intuition was that `dew` and `due` are genuinely different words, so
this should have started non-recoverable, and if a correction layer exists at all, something
like this should flip to recoverable after it runs. Auditing every `CORRUPTED` event in the
whole dataset (`Fabina`=36.4, `Mountain Dew`=66.7, `Tetra Pack`=66.7, `Slice`=66.7, `Seven
Up`=75.0 and 80.0) surfaced the real, dataset-wide version of that concern: 5 of those 6 --
including *both* of Step 3's real, verified corrections (`seven of`/`seven a` -> `seven up`) --
already scored above `RECOVERABLE_THRESHOLD=65` before any correction ran. So `recovered`
(before=False -> after=True) was structurally close to unfireable in this dataset: any
corruption clean enough for a safe fuzzy-match correction layer to fix was, almost by
construction, already clean enough to count as "recoverable" under a 65-point bar -- both
signals come from the same underlying fuzzy score. The two real fixes only ever showed up as
`fixed_to_exact_correct=True`, never as `recovered=True`, even though they demonstrably
worked. That's a problem given the assignment's explicit instruction to "actually reduce
[the highest-cost failure mode], measured on your step-2 metric... on calls you did not tune
on" -- a metric that can never move regardless of whether the correction layer works doesn't
satisfy that.

One important nuance surfaced while explaining this: `Mountain Dew`'s `dew`/`due` is a
single-word alias, which the correction layer deliberately never touches (`MIN_ALIAS_TOKENS=2`
-- see the entry above on single-word aliases scoring identically to real false positives like
`size`/`Slice`). So even under a stricter threshold, `Mountain Dew` stays non-recoverable
*after* correction too, same as before -- it was never a correction target, so "why build a
correction layer if this counts as fine" doesn't actually apply to that entity specifically.
The sharper version of the concern applies to the two `Seven Up` mentions, which the
correction layer *does* target and fix, yet still couldn't show a `recoverable` flip under the
65-point bar.

**Decision (confirmed with the user via three options -- raise the threshold, keep it and
reframe the write-up around `fixed_to_exact_correct`, or a user-specified value -- user chose
to raise it):** raised `RECOVERABLE_THRESHOLD` from 65 to 81 -- just under
`ASR_MATCH_THRESHOLD=82`, so `recoverable` stays technically distinct from `status=='CORRECT'`
(a hairline-close corruption could still qualify) without being a loose "a human could
probably guess it" bar. This reframes `recoverable` as "would the call's goal go through
*automatically*, without a human in the loop" -- a defensible reading for a voice-agent
order/booking flow where a downstream system needs the exact SKU/city string, not a fuzzy
human guess.

**Result:** re-ran the full pipeline (`run_measure.py` unaffected -- Step 1 doesn't use this
threshold; `run_impact_metric.py`, `run_improve.py`). Step 2 headline moved from 9/11 (81.8%)
to **11/11 (100%)** affected calls having an unrecoverable entity -- every `CORRUPTED` event in
this dataset happens to score below 81, so pre-correction, "affected" and "impacted" now
coincide for the proper-noun mode on this small dataset; Step 2's differentiating value shows
up in *which* mentions Step 3 can actually fix, not in separating frequency from impact
pre-correction. Total non-recoverable entity events: 20 (10 `DROPPED`, 6 `CORRUPTED`, 4
`ADDED`) -- up from 15, since all 5 previously-recoverable `CORRUPTED` events now count as
non-recoverable. Step 3's training-split result changed from a null result on the `recoverable`
metric to a genuine, verified **2/17 non-recoverable entities recovered (11.8%)** -- both real
fixes now demonstrably move `recoverable` from `False` to `True`, not just `status` from
`CORRUPTED` to `CORRECT`. Held-out still shows 0/3 recovered, unchanged, for the same
pre-existing reason: neither real fix happened to land in the held-out split under this run's
seed. Also cleaned up `run_improve.py`'s `improve_summary.md` boilerplate text, which
previously asserted "note this can be >0 while `recovered` above is 0" as if that were always
true -- no longer accurate now that `recovered` can be nonzero, so reworded to describe what
`fixed_to_exact_correct` vs `recovered` each track without assuming one is always zero.

---

### 2026-08-17 — Phonetic/orthographic distance doesn't separate single-word aliases either; added regression tests instead

**Challenge:** given the sub-600ms latency budget in the assignment, the user asked whether
phonetic distance (computed locally, no network call, unlike an LLM API call) could be added
to safely re-admit single-word aliases (`dew`, `slice`, `tetra`) into correction -- the bucket
`MIN_ALIAS_TOKENS=2` currently excludes because plain `fuzz.ratio` alone couldn't separate real
catches from false positives at the same score (see the `MIN_ALIAS_TOKENS` entry above).
Latency-wise this is a real fix (a local algorithm has none of the network round-trip risk an
LLM call would add), so it was worth testing properly rather than dismissing on cost grounds
alone.

Tested on this project's own audited real-catch/false-positive pairs (`dew`/`due`,
`slice`/`life`, `tetra`/`test` vs `pepsi`/`testi`, `slice`/`size`, `slice`/`line`,
`slice`/`liche`) across nine different single-word similarity signals: four phonetic
algorithms (Metaphone, Soundex, NYSIIS, Match Rating Codex, via the `jellyfish` package) plus
five simple heuristics (first-letter match, last-letter match, Jaro-Winkler similarity,
character-bigram Dice coefficient). None cleanly separated real catches from false positives:
Match Rating Codex, the best performer, still classified `slice`/`size` and `slice`/`line`
(both real false positives) as matches; every phonetic algorithm rejected `slice`/`life` (a
real catch) alongside the false positives; and bigram Dice actively **inverted** the signal --
`slice`/`liche` (the worst false positive) scored a higher similarity (0.50) than any real
catch (0.00-0.29). Likely root cause: these algorithms encode English pronunciation rules, but
the actual confusions come from an ASR model's acoustic behavior on Hindi-accented,
code-switched speech, which doesn't follow the same rules a US-English phonetic encoder
assumes.

**Decision:** stopped searching for a tenth metric that happens to fit this exact 7-pair
sample -- that's the overfitting risk `MIN_ALIAS_TOKENS`'s own docstring already warned
against, just with a new signal instead of a new threshold on the old one. `MIN_ALIAS_TOKENS`
stays at 2; single-word aliases remain excluded from correction, still an open, disclosed gap
(see README's "What's next"). Documenting this as a ruled-out approach with evidence, the same
way the earlier single-word-alias exclusion itself is documented, so nobody re-attempts the
same experiment without first checking here.

Since no correction-logic change came out of this, redirected the effort into something
genuinely useful regardless of the phonetic question: `tests/test_correction.py`, six
regression tests grounded in real calls from `data/dataset.xlsx` and the exact guards
documented in `src/correction.py` (both real `Seven Up` corrections still fire even when their
own call is excluded from the training vocabulary; `Mountain Dew`'s single-word `due`/`dew`
stays uncorrected by design; `build_correction_vocabulary` never leaks a held-out call's own
gold spelling -- caught via `dd0bb7ca`, the only call with "mirinda orange" rather than plain
"mirinda"; the number-word-only guard rejects "seven half"/"seven fifty"; the quantity-neighbor
guard requires an adjacent number/unit word for a product correction to fire). Every hand-tuned
rule in this file was justified by a manual audit that's expensive to redo -- these tests exist
so a future change to `LOW_SCORE`/`HIGH_SCORE`/`MIN_ALIAS_TOKENS`/the guard functions fails
loudly instead of silently reintroducing an already-found-and-fixed false positive.

---

### 2026-08-17 — Tried gating single-word aliases on the quantity-neighbor guard; reverted after finding an undetectable failure mode

**Challenge:** the user asked whether the *existing* quantity-neighbor guard (not a new
signal) might already be enough to safely re-admit single-word aliases, since it was
originally built to distinguish exactly this: real single-word catches (`due`, `life`,
`test`) from false positives (`size`, `line`, `testi`) at the same fuzzy score. The original
`MIN_ALIAS_TOKENS` audit had tried this guard and called it insufficient, but only tested it
against the 9 already-known examples in isolation, not by actually re-running the guarded
logic across the full 49-call dataset. Doing that (temporarily setting `MIN_ALIAS_TOKENS=1`
for `product`/`brand`/`packaging` types, which already carry the mandatory quantity-neighbor
check) surfaced 6 candidates instead of 9: `due`/`dwew`/`test`/`life` (real, already known),
plus **`merinda`/Mirinda** -- a second, previously uncounted real corruption in call
`09c68660` (gold repeats "Mirinda...Mirinda" and ASR garbles the second occurrence
differently from the one the LLM's entity alignment happened to pick as its representative
span) -- and only one false positive, `liche`/Slice, versus the original audit's four. A 5:1
real-to-false ratio, a genuine improvement over the original all-or-nothing finding.

Implemented it (`MIN_ALIAS_TOKENS_WITH_GUARD=1` for `QUANTITY_GUARDED_TYPES =
{"product","brand","packaging"}`, cities untouched since they never had a multi-word alias
mined anyway) and re-ran the full pipeline. Before treating it as done, traced the actual
*corrected text* for the one false positive rather than trusting the entity-level before/after
table alone -- and found something worse than "swaps one real product for another." Call
`8661511d`'s gold order never includes `Slice` at all (it's Seven Up, Pepsi, Tetra Pack,
Fruitz Apple, and Fruitz Litchi mentioned twice); ASR's `"fruit and liche bees water"` (a
second, untracked mishearing of "litchi") got rewritten to `"fruit and slice bees water"` --
**fabricating a SKU that was never ordered**, not misspelling one that was. Worse: this harm
is invisible to `run_improve.py`'s own `corrupted_by_correction` check, because that check only
rescores entities `entity_events_df` already tracks for a call, and this call's Step 1
extraction never separately tracked the second "litchi" mention as its own entity -- so
`improve_held_out_events.csv` reported "0 already-CORRECT entities corrupted" for this call
even though real harm occurred. A genuine gap in the evaluation methodology, not just an
accepted risk: any false positive that touches an untracked span reads as zero cost in our
numbers no matter how bad it is.

**Decision:** reverted to `MIN_ALIAS_TOKENS=2` (multi-word only, the original, unmodified
state) once this fuller picture was in hand -- confirmed by re-running the full 49-call
simulation and getting exactly the original 2 corrections back, and by `tests/test_correction.py`
passing unchanged. Fabricating an untracked phantom order item is a more severe failure than
the ratio alone suggested when the decision was first made, and it exposed a real blind spot
(evaluation only catches harm to already-tracked entities) that would need fixing before this
kind of change could be trusted, not just a threshold to accept or reject. `MIN_ALIAS_TOKENS`
stays at 2 for that reason, not because the 5:1 ratio wasn't real.

---

### 2026-08-17 — Assignment Step 2 was missing its "quantity" half; added, and found a latent scoring bug while at it

**Challenge:** a full pass against the assignment's exact wording ("for short utterances,
whether the yes/no **or quantity** is still correct") found `src/hallucination.py` only ever
implemented the yes/no half (`polarity_preserved`). No quantity-preservation check existed at
all, and the gap wasn't disclosed anywhere in the README. The assignment's own worked example
("yes हिंदी forty five percent" -> "...forty five percent") is literally a quantity, not a
yes/no -- confirmed it's real data, not just illustrative text: call `33720198` in this dataset
is exactly that example.

**Decision:** added `quantity_preserved` (`src/hallucination.py`): extract maximal runs of
consecutive number/unit words from gold (reusing `NUMBER_AND_QUANTITY_WORDS`, moved from
`src/correction.py` to `src/normalization.py` as a shared constant since two modules now need
it, extended with `percent`/`percentage`/`प्रतिशत`), then check each phrase is reproduced
**verbatim as a contiguous subsequence** somewhere in ASR -- not a per-word set-overlap the way
`_polarity()` works, because a wrong value can share individual words with the right one
("twenty **five** percent" vs "forty **five** percent") without being the same quantity.
Combined into `content_preserved = polarity_preserved and quantity_preserved`, since the
assignment's "or" means either one failing is a real content loss.

**A second, more consequential finding surfaced while verifying the change**, not by trusting
the summary numbers but by re-checking the WER-vs-impact_score correlation before/after (it
moved from 0.04 to 0.012, more than the new metric alone should cause): `build_step2_impact_table`'s
`impact_score` formula applied the `polarity_preserved` (now `content_preserved`) penalty to
**every call unconditionally**, not just calls flagged `hallucination_candidate` -- a latent gap
that already existed for `polarity_preserved` alone (call `a64ff8a2` was already being penalized
for a "lost" yes/no signal despite never being a hallucination candidate) but had never been
noticed because polarity words are common enough to rarely go missing by accident. Adding
`quantity_preserved` made it obvious: 6 heavily-corrupted beverage/verification calls
(`09c68660`, `a8afca71`, `5a7d6b58`, `c1c6a53f`, `8661511d`, `dd0bb7ca`) started failing
quantity preservation purely as a side effect of the SAME proper-noun corruption already
counted via `entity_non_recoverable` -- double-billing one real failure under two different
failure modes' scores. `build_failure_impact_summary` already correctly gated its own
affected/impacted split by `hallucination_candidate`; the per-call `impact_score` column just
never had the same gate. Fixed by gating the content-loss term the same way:
`per_call["hallucination_candidate"] & ~per_call["content_preserved"]`. Verified afterward that
every non-candidate call's `impact_score` now equals `entity_non_recoverable * 2` exactly, with
no residual hallucination-signal contribution. Re-ran the full pipeline; the headline
`short_utterance_hallucination` impact rate stayed 0/9 (0%) since the one call with a real gold
quantity, `33720198`, already preserves it -- but the per-call `impact_score` ranking and the
WER-correlation figure both changed slightly (0.04 -> 0.035) from the scope-bug fix, not from
the new metric itself. Added `tests/test_hallucination.py` (4 tests, including one against the
real `33720198` call and one specifically testing that a wrong value sharing a number word with
the right one is still correctly rejected) so this can't silently regress.

---

### 2026-08-17 — `polarity_preserved` used set overlap instead of subset; a real call exposed it

**Challenge:** the user spotted a specific row in `step2_hallucination_impact.csv` -- call
`b2a6f783`, `gold_polarity="affirmative,negative"`, `asr_polarity="affirmative"`,
`polarity_preserved=True` -- and asked why a call whose gold and ASR polarity sets don't match
was reported as preserved. Root cause: `polarity_preserved = (not gold_polarity) or
bool(gold_polarity & asr_polarity)` only checked whether the two sets *overlap at all*, not
whether every category in gold survives. This call's actual gold is `"हाँ जी हाँ जी क्या चीज का
order {noise} नहीं नहीं"` -- literally "yes yes ... no no" -- and the ASR completely drops the
trailing "नहीं नहीं", so `asr_polarity` is affirmative-only. The intersection
(`{"affirmative"} & {"affirmative","negative"}`) is still non-empty because "affirmative"
survived, so the old check said "preserved" even though a real, distinct signal (the "no") was
entirely lost.

Checked the blast radius before fixing: 21 calls in the dataset have `gold_polarity =
"affirmative,negative"`; 3 of them (`b2a6f783`, plus `a2632186` and `0de04f5e`) have ASR
capturing only one of the two categories, all previously mis-reported as `True`. None of the
3 are flagged `hallucination_candidate`, and all 9 actual hallucination candidates in this
dataset already have exact gold/ASR polarity matches -- so this bug never affected any reported
headline number (frequency, impact rate, `impact_score`, or the WER correlation, all confirmed
unchanged after the fix), but the underlying per-call metric was wrong regardless of whether it
happened to matter in this specific run.

**Decision:** changed to `polarity_preserved = gold_polarity.issubset(asr_polarity)` --
requires every category in gold to still be present in ASR, not just some overlap. This also
simplified the code: `set().issubset(anything)` is `True`, so the explicit `(not gold_polarity)
or ...` vacuous-case handling is no longer needed. Re-ran the full pipeline and confirmed the
prediction that no headline number would change (frequency 9/49, impact rate 0/9,
`impact_score` correlation still 0.035). Added two tests to `tests/test_hallucination.py`
against the two real calls that demonstrate the fix: `b2a6f783` (one signal dropped -> must be
`False`) and `e6a45e58` (both signals survive -> must stay `True`, confirming the fix isn't
overly strict).
