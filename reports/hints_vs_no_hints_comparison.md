# LLM entity extraction: gazetteer-hints vs. pure-judgment comparison

Both modes run the same model (`openai/gpt-4o-mini` via OpenRouter), same schema, same
deterministic comparison logic (`src/entities_llm.py`). The only difference: whether the
prompt includes the gazetteer's canonical names as grounding hints
(`configs/entity_gazetteer.yaml`) or not.

```
python run_measure.py --entity-backend llm --llm-hints gazetteer --output reports/
python run_measure.py --entity-backend llm --llm-hints none      --output reports_no_hints/
```

## Headline numbers

| | With hints (`reports/`) | No hints (`reports_no_hints/`) |
|---|---:|---:|
| Proper-noun affected calls | 7 / 49 (14.3%) | 15 / 49 (30.6%) |
| ...of which impact-failed | 5 (71.4%) | 14 (93.3%) |

Taken at face value this looks like removing the hints doubles recall. It does not —
most of the extra "failures" are artifacts of how the two independent extraction calls
(gold and ASR are extracted separately, then joined by canonical name) drift apart when
there's no shared anchor to pull them back together.

## Root causes of the gap (with evidence)

### 1. Cross-call canonical drift on the *same* real-world entity

Call `59cc61e3` (ecommerce support). Gold mentions "Bombay" twice (`बॉम्बे`, `bombay`);
ASR says `bombay` once too — genuinely the same word, correctly transcribed. But the two
extraction calls picked different canonical names for it:

```
GOLD (no hints): ..., {"span": "bombay",  "canonical": "Bombay"}
ASR  (no hints): ..., {"span": "bombay",  "canonical": "Mumbai"}
```

Because the comparator joins by exact canonical-name string, `"Bombay" != "Mumbai"` means
no match is found, and the gold mention falls through to a raw-text fuzzy match instead —
which still doesn't recover a clean `CORRECT`, so a *perfectly transcribed* word gets
reported as `DROPPED` + `CORRUPTED` in `top_entities.csv`. With hints, the same call
canonicalizes consistently (`Mumbai` on both sides, `CORRECT`) — see
[reports/entity_events.csv](entity_events.csv) row for `59cc61e3`.

### 2. Multi-word span fragmentation drift

Call `8661511d` (beverage order-taking). Gold says `fruitz apple` and `fru fruit apple`;
ASR says `fruits apple` and `fruit apple`/`fruit and liche` — same referents, ASR actually
tracks gold reasonably well here. No-hints extraction split these differently per side:

```
GOLD (no hints): "Fru Fruit Apple", "Fruit Litchi Fizz Water"
ASR  (no hints): "Apple", "Litchi", "Fruits"          <- three separate, shorter spans
```

One real product mention becomes 1 `CORRUPTED` + 1 `DROPPED` + 3 `ADDED` events for a call
where nothing was actually lost. With hints, `Fruitz` anchors both sides to the same
canonical name and this call comes back clean `CORRECT` (see
`reports/entity_events.csv`, call `8661511d`).

### 3. Over-inclusive extraction bar

No-hints mode also extracted plainly generic descriptive phrases as "entities":
`clothing company`, `silver jewellery export leather saddlery goods`, `forty six number
shirt`, `website`, `Water`, `Shirt` (see [reports_no_hints/top_entities.csv](../reports_no_hints/top_entities.csv)).
None of these are proper nouns in the sense the assignment cares about (a product,
brand, or place whose loss breaks the call). Having a reference list in the prompt appears
to anchor the model toward a tighter reading of "salient named entity" generally, not just
for the entities on that list.

## Is there anything no-hints mode genuinely caught that hints mode missed?

Yes, one clear case: call `5a7d6b58` (verification call) — no-hints mode extracted `Fabina`
(`फैबिना`, a business/brand name), `DROPPED` on the ASR side. Hints mode only reports
`Delhi DROPPED` for that call; `Fabina` isn't in the gazetteer and wasn't independently
noticed. This is a real, non-noise recall gain the freer prompt found.

`cuppa`/`copper` (the case that motivated building this LLM layer) is **still missed by
both modes** — confirmed by direct cache inspection earlier. This is a model-confidence
limit on an out-of-context ambiguous word, not something either hints setting fixes.

## Conclusion

The raw affected-calls counts are not directly comparable as-is: no-hints mode's number is
inflated mostly by a structural weakness of the "extract each side independently, join by
canonical string" design when nothing anchors the two sides to agree on a name. Grounding
with the gazetteer's known names is doing double duty — normalizing known SKUs/cities *and*
keeping the two independent extractions from drifting apart on entities near that list —
which is more load-bearing than "just helps with known entities."

**Recommendation:** keep `--llm-hints gazetteer` (the default) as the authoritative report.
Treat `reports_no_hints/` as a diagnostic surface for finding candidates (like `Fabina`) to
manually review and, if real, either add to the gazetteer hint list or fix at the join layer
(e.g. fuzzy-match canonical names between sides, not just exact string equality, before
falling back to raw-text matching) rather than as a drop-in replacement.
