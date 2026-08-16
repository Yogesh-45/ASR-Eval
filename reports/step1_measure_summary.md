# Step 1 -- Measure Summary

Frequency, WER, and gold-wrong flagging only. This is deliberately NOT the impact metric -- see `step2_impact_summary.md` (run_impact_metric.py) for whether each failure was actually costly.

## Overall
- Total calls: 49
- Overall mean WER (diagnostic only, not the business metric): 0.797
- Calls with Hindi-English script mixing: 43 (87.8%)

## Failure-mode frequency

| failure_mode                  |   total_calls |   affected_calls |   frequency_pct |
|:------------------------------|--------------:|-----------------:|----------------:|
| proper_noun                   |            49 |               11 |            22.4 |
| short_utterance_hallucination |            49 |                9 |            18.4 |

## Proper-noun failure frequency by use case

| use_case                        |   total_calls |   affected_calls |   frequency_pct |
|:--------------------------------|--------------:|-----------------:|----------------:|
| b2b sales outreach              |            17 |                3 |            17.6 |
| beverage order-taking           |             4 |                3 |            75   |
| admissions / lead qualification |             4 |                2 |            50   |
| verification call               |             3 |                2 |            66.7 |
| ecommerce support               |            21 |                1 |             4.8 |

## Top calls by raw failure count

| id                                   | use_case                        |   failure_count |   entity_failures | hallucination_candidate   |      wer |
|:-------------------------------------|:--------------------------------|----------------:|------------------:|:--------------------------|---------:|
| 09c68660-7db4-4b5b-9a72-39a12f18c9bd | beverage order-taking           |               5 |                 5 | False                     | 0.515152 |
| 5a7d6b58-8e53-4c52-8f96-0166236e8e0a | verification call               |               3 |                 3 | False                     | 0.863014 |
| e6a45e58-9fec-4691-8f2d-3b2ba5ad45d8 | ecommerce support               |               2 |                 1 | True                      | 1.4      |
| c1c6a53f-fd1e-428d-8198-22209c3e8082 | verification call               |               2 |                 2 | False                     | 0.759259 |
| 963cce78-ad9a-45e5-98c3-962a24bdb7bb | admissions / lead qualification |               2 |                 2 | False                     | 0.715789 |
| a8afca71-9dd8-464c-9ebf-04933f58e4a4 | beverage order-taking           |               2 |                 2 | False                     | 0.734234 |
| 7d743b92-bd32-4e39-afd4-a9da3b3c52bd | ecommerce support               |               1 |                 0 | True                      | 0.5      |
| 88d0e749-daf7-4f1f-90b3-a83b1e40cc5e | b2b sales outreach              |               1 |                 0 | True                      | 0.8      |
| 685ebfb1-06f5-4043-8443-b3390789e333 | ecommerce support               |               1 |                 0 | True                      | 1.33333  |
| 31fb72e8-80a7-47d2-a608-eadb0249953c | ecommerce support               |               1 |                 0 | True                      | 1.83333  |

## Top problematic entities

| canonical_entity   |   mentions |   correct |   corrupted |   dropped |   added |   failure_rate_pct |
|:-------------------|-----------:|----------:|------------:|----------:|--------:|-------------------:|
| Fabina             |          1 |         0 |           1 |         0 |       0 |              100   |
| Aquafina           |          1 |         0 |           0 |         1 |       0 |              100   |
| WhatsApp           |          1 |         0 |           0 |         1 |       0 |              100   |
| Silver Jewellery   |          1 |         0 |           0 |         1 |       0 |              100   |
| KC Nagar           |          1 |         0 |           0 |         1 |       0 |              100   |
| Tetra Pack         |          2 |         0 |           1 |         1 |       0 |              100   |
| Woxen University   |          1 |         0 |           0 |         1 |       0 |              100   |
| Slice              |          2 |         0 |           1 |         1 |       0 |              100   |
| Pronto             |          1 |         0 |           0 |         1 |       0 |              100   |
| Mountain Dew       |          1 |         0 |           1 |         0 |       0 |              100   |
| Delhi              |          3 |         1 |           0 |         2 |       0 |               66.7 |
| Seven Up           |          4 |         2 |           2 |         0 |       0 |               50   |
| Alibaba.com        |          1 |         1 |           0 |         0 |       0 |                0   |
| 2306150615         |          1 |         1 |           0 |         0 |       0 |                0   |
| BBA                |          1 |         1 |           0 |         0 |       0 |                0   |
| B.Tech             |          1 |         1 |           0 |         0 |       0 |                0   |
| Bombay             |          1 |         1 |           0 |         0 |       0 |                0   |
| Apple Watch        |          1 |         1 |           0 |         0 |       0 |                0   |
| Meera              |          1 |         1 |           0 |         0 |       0 |                0   |
| Mayura             |          1 |         1 |           0 |         0 |       0 |                0   |
| MBA                |          1 |         1 |           0 |         0 |       0 |                0   |
| Hyderabad          |          1 |         0 |           0 |         0 |       1 |                0   |
| Fruitz Litchi      |          1 |         1 |           0 |         0 |       0 |                0   |
| Gurgaon            |          1 |         1 |           0 |         0 |       0 |                0   |
| Fruitz Apple       |          1 |         1 |           0 |         0 |       0 |                0   |
| Fruitz             |          2 |         2 |           0 |         0 |       0 |                0   |
| Raval              |          1 |         0 |           0 |         0 |       1 |                0   |
| Pepsi              |          4 |         4 |           0 |         0 |       0 |                0   |
| Nitiya             |          1 |         0 |           0 |         0 |       1 |                0   |
| Neha               |          1 |         1 |           0 |         0 |       0 |                0   |
| Mirinda            |          2 |         2 |           0 |         0 |       0 |                0   |
| Moradabad          |          1 |         1 |           0 |         0 |       0 |                0   |
| Telugu             |          1 |         1 |           0 |         0 |       0 |                0   |
| Uttar Pradesh      |          1 |         0 |           0 |         0 |       1 |                0   |

## Gold/ASR script-variant matches (naive WER would misclassify these)

| call_id                              | canonical_entity   | gold_matched_span   | asr_matched_span   |
|:-------------------------------------|:-------------------|:--------------------|:-------------------|
| 2f5a58b5-3467-45cf-a57a-70c4ae0165ae | Gurgaon            | gurgaon             | गुड़गांव           |
| ebbb8d49-de7b-4d30-a845-ef68c74a980a | Delhi              | delhi               | दिल्ली             |
| f87b3e49-f5ad-4662-bfd0-16a5a833a146 | Neha               | neha                | नेहा               |
| 59cc61e3-478d-45e7-9c52-d8f877375c9e | Bombay             | बॉम्बे              | bombay             |
| a8afca71-9dd8-464c-9ebf-04933f58e4a4 | Fruitz Litchi      | fruitz litchi       | fruits लीची        |
