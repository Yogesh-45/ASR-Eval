# Step 2 -- Impact Metric Summary

Reported per call, before any correction (Step 3). This is deliberately NOT the frequency/WER measurement -- see `step1_measure_summary.md` (run_measure.py) for how often each failure mode happens.

## Metric definitions

- **Proper nouns -- `recoverable`**: `True` for `CORRECT`, and for `CORRUPTED` mentions scoring above `RECOVERABLE_THRESHOLD=81` (a human or downstream system could still recover the intended entity); `False` for `DROPPED`, `ADDED`, and low-confidence `CORRUPTED` mentions.
- **Short utterances -- `content_preserved`** (`polarity_preserved` AND `quantity_preserved`): whether an affirmative/negative signal present in gold is still detectable anywhere in the ASR output despite the padding, AND whether every quantity phrase in gold (e.g. "forty five percent") is still reproduced verbatim somewhere in the ASR output. Either one failing counts as a real content loss -- the assignment's "whether the yes/no or quantity is still correct." Both signals are reported individually too (`step2_hallucination_impact.csv`), not just the combined verdict.

## Impact-failure rate (given Step 1's affected-call count)

| failure_mode                  |   affected_calls |   impact_failed_calls |   impact_failed_pct_of_affected |
|:------------------------------|-----------------:|----------------------:|--------------------------------:|
| proper_noun                   |               11 |                    11 |                             100 |
| short_utterance_hallucination |                9 |                     0 |                               0 |

## Proper-noun impact rate by use case

| use_case                        |   affected_calls |   impact_failed_calls |   impact_failed_pct_of_affected |
|:--------------------------------|-----------------:|----------------------:|--------------------------------:|
| b2b sales outreach              |                3 |                     3 |                             100 |
| beverage order-taking           |                3 |                     3 |                             100 |
| admissions / lead qualification |                2 |                     2 |                             100 |
| verification call               |                2 |                     2 |                             100 |
| ecommerce support               |                1 |                     1 |                             100 |

## Concentration of impact
- Top 20 calls account for 100.0% of total impact score.

## Top calls by impact (cost), before any correction

| id                                   | use_case                        |   impact_score |   entity_non_recoverable | polarity_preserved   | quantity_preserved   |
|:-------------------------------------|:--------------------------------|---------------:|-------------------------:|:---------------------|:---------------------|
| 09c68660-7db4-4b5b-9a72-39a12f18c9bd | beverage order-taking           |             10 |                        5 | True                 | False                |
| 5a7d6b58-8e53-4c52-8f96-0166236e8e0a | verification call               |              6 |                        3 | True                 | False                |
| 963cce78-ad9a-45e5-98c3-962a24bdb7bb | admissions / lead qualification |              4 |                        2 | True                 | True                 |
| c1c6a53f-fd1e-428d-8198-22209c3e8082 | verification call               |              4 |                        2 | True                 | False                |
| a8afca71-9dd8-464c-9ebf-04933f58e4a4 | beverage order-taking           |              4 |                        2 | True                 | False                |
| e6a45e58-9fec-4691-8f2d-3b2ba5ad45d8 | ecommerce support               |              3 |                        1 | True                 | True                 |
| 8661511d-f289-4919-b2da-0b4f78726e1f | beverage order-taking           |              2 |                        1 | True                 | False                |
| d8676f59-e3d8-49c6-a135-3b77eda03e02 | admissions / lead qualification |              2 |                        1 | True                 | True                 |
| 47629b83-b3af-4492-bc4a-5a6aa0a2aa8e | b2b sales outreach              |              2 |                        1 | True                 | True                 |
| c95edcdd-5ffe-4864-9722-3a88988365fd | b2b sales outreach              |              2 |                        1 | True                 | True                 |
