# Step 3 -- Improve: proper-noun correction layer

Held-out calls (10/49, never used to build the correction vocabulary): ['2b184bcb-9a7a-4da5-9b06-f8b5ba1711fe', '31fb72e8-80a7-47d2-a608-eadb0249953c', '6400c588-c623-472c-b097-4bb03d6edaf2', '7eb5f02c-881a-4d70-beaf-c68bcb2e8c22', '8661511d-f289-4919-b2da-0b4f78726e1f', '88d0e749-daf7-4f1f-90b3-a83b1e40cc5e', 'ae6caea0-a3b7-445d-a83e-6d02457af07b', 'c95edcdd-5ffe-4864-9722-3a88988365fd', 'ca20a6fe-6036-41e1-bf47-68a8205a4239', 'e6a45e58-9fec-4691-8f2d-3b2ba5ad45d8']

**This is the reported result** -- the held-out set is what the assignment means by "calls you did not tune on."

### Held-out (reported result)

- Entity mentions evaluated: 8
- Non-recoverable before correction: 3 -- of those, **0 recovered** (0.0%)
- Of 0 DROPPED mentions specifically, 0 recovered (expected to be 0 -- a text-only layer cannot correct a word that was never transcribed)
- CORRUPTED before correction: 1 -- of those, **0 fixed to exact CORRECT** (spelling restored, e.g. "seven of"/"seven a" -> "seven up"). Reported separately from `recovered` because they answer different questions: `fixed_to_exact_correct` is spelling fidelity (did this specific mention become an exact match), `recovered` is the Step 2 impact metric (did a mention cross from non-recoverable to recoverable). With RECOVERABLE_THRESHOLD requiring an almost-exact match, the two numbers usually move together now -- but a CORRUPTED mention that happened to already score >=RECOVERABLE_THRESHOLD before correction could still show fixed_to_exact_correct=True with recovered=False, so both are kept rather than assuming one implies the other.
- Already-CORRECT before correction: 5 -- of those, **0 corrupted by this change** (0.0%)

**Training-split numbers below are a sanity check only** (the vocabulary was built from these calls' own gold transcripts, so a gain here is expected and not evidence of generalization).

### Training split (sanity check, not the proof)

- Entity mentions evaluated: 38
- Non-recoverable before correction: 17 -- of those, **2 recovered** (11.8%)
- Of 10 DROPPED mentions specifically, 0 recovered (expected to be 0 -- a text-only layer cannot correct a word that was never transcribed)
- CORRUPTED before correction: 5 -- of those, **2 fixed to exact CORRECT** (spelling restored, e.g. "seven of"/"seven a" -> "seven up"). Reported separately from `recovered` because they answer different questions: `fixed_to_exact_correct` is spelling fidelity (did this specific mention become an exact match), `recovered` is the Step 2 impact metric (did a mention cross from non-recoverable to recoverable). With RECOVERABLE_THRESHOLD requiring an almost-exact match, the two numbers usually move together now -- but a CORRUPTED mention that happened to already score >=RECOVERABLE_THRESHOLD before correction could still show fixed_to_exact_correct=True with recovered=False, so both are kept rather than assuming one implies the other.
- Already-CORRECT before correction: 21 -- of those, **0 corrupted by this change** (0.0%)
