# Dataset Profile (Phase 0)

- Rows: 49
- Unique ids: 49 (duplicates: 0)
- Unique recording_urls: 49 -> one row = one call (one recording), not one utterance
- Use cases (5): {'ecommerce support': 21, 'b2b sales outreach': 17, 'admissions / lead qualification': 4, 'beverage order-taking': 4, 'verification call': 3}
- Missing values per column: {'id': 0, 'use_case': 0, 'gold_transcript': 0, 'asr_output': 0, 'recording_url': 0}
- Empty gold_transcript rows: 0
- Empty asr_output rows: 0
- Gold transcript length (tokens): avg=43.4, min=2, max=243
- Calls containing Devanagari script: 43 (87.8%) -> confirms Hindi-English code-switching, naive WER will be inflated by script choice alone