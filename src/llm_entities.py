"""LLM-based open-set entity extraction + cross-transcript alignment (Phase 5-6 of the plan).

The hand-curated gazetteer (configs/entity_gazetteer.yaml) only detects entities someone
already thought to list -- a one-off brand name in a single call (e.g. "cuppa" garbled to
"copper" in a b2b sales call) is invisible to it.

An earlier version of this module extracted entities from gold and ASR in two independent
calls, then joined them by exact canonical-name string equality in Python. That join is
fragile: two independent extractions can pick different canonical names for the same
real-world entity (gold -> "Bombay", ASR -> "Mumbai", same word actually said correctly on
both sides), which reads as a dropped/corrupted entity even though nothing was lost.

This version extracts from both transcripts in a single call instead, and asks the model
to align each entity across the two sides (does this gold mention and that ASR mention
refer to the same real-world thing) -- something that genuinely needs world/script
knowledge a pure string-similarity join can't reproduce. The model does NOT grade whether
the ASR is correct: it only reports the aligned span pair (or a null on one side, meaning
"not mentioned there"). Whether an aligned pair counts as CORRECT vs CORRUPTED, and
whether it's recoverable, is still computed deterministically in Python from a fuzzy
text-similarity score between the two spans (src/entities_llm.py), per the plan's Phase 5
guidance that the LLM should extract facts, not judge correctness.

Results are cached to data/llm_entity_cache.json keyed by call_id so re-running the
pipeline doesn't re-spend API calls, and so the shipped reports are reproducible without a
live key.
"""

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Supports either a direct OpenAI key or an OpenRouter key (OpenRouter exposes an
# OpenAI-compatible /chat/completions endpoint at a different base_url and expects
# provider-prefixed model names, e.g. "openai/gpt-4o-mini").
_OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY")
_OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

if _OPENROUTER_KEY:
    _API_KEY = _OPENROUTER_KEY
    _BASE_URL = "https://openrouter.ai/api/v1"
    _DEFAULT_MODEL = "openai/gpt-5.6-luna"
elif _OPENAI_KEY:
    _API_KEY = _OPENAI_KEY
    _BASE_URL = None
    _DEFAULT_MODEL = "gpt-5.6-luna"
else:
    _API_KEY = None
    _BASE_URL = None
    _DEFAULT_MODEL = "gpt-5.6-luna"

MODEL = os.environ.get("LLM_ENTITY_MODEL", _DEFAULT_MODEL)
CACHE_PATH = Path(os.environ.get("LLM_ENTITY_CACHE", "data/llm_entity_cache.json"))

_MAX_RETRIES = 6
_RETRY_DELAY_SECONDS = 8  # this model's new-account OpenRouter tier caps at 10 requests/minute

_ALIGNED_ENTITY_SCHEMA = {
    "name": "entity_alignment",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "canonical": {
                            "type": "string",
                            "description": "Normalized name in Latin script for this real-world entity.",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["product", "brand", "city", "place", "id", "other_proper_noun"],
                        },
                        "gold_span": {
                            "type": ["string", "null"],
                            "description": "Exact text as it appears in the GOLD transcript, or null if this "
                            "entity is not mentioned in gold at all (ASR added it).",
                        },
                        "asr_span": {
                            "type": ["string", "null"],
                            "description": "Exact text as it appears in the ASR transcript, or null if this "
                            "entity is not mentioned in ASR at all (ASR dropped it).",
                        },
                    },
                    "required": ["canonical", "type", "gold_span", "asr_span"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["entities"],
        "additionalProperties": False,
    },
}

_SYSTEM_PROMPT = """You are given two transcripts of the same voice-agent call: a human \
GOLD transcript and the production ASR system's output for the same audio. Both may mix \
Hindi (Devanagari script), Hindi transliterated into Latin script, and English in the same \
sentence -- this is normal code-switching, not an error, and not itself something to flag.

Find every proper noun that matters to the call's business outcome: product/brand names, \
place names, company names, or other named entities (e.g. the item being ordered, the city \
being verified). Do not extract ordinary common nouns, standalone numbers, or generic words \
-- only genuine named entities.

For each such entity, report ONE object covering BOTH transcripts:
- canonical: a normalized name in Latin script for this real-world entity
- type: one of product, brand, city, place, id, other_proper_noun
- gold_span: the exact text as it appears in the GOLD transcript if mentioned there, else null
- asr_span: the exact text as it appears in the ASR transcript if mentioned there, else null

Critically, you must align entities across the two transcripts yourself: if the gold \
transcript says "delhi" and the ASR transcript says "दिल्ली", or gold says "dew" and ASR \
says "due", these refer to the SAME entity -- report ONE object with both spans filled in, \
not two separate objects. Only leave gold_span or asr_span null when that transcript truly \
does not mention the entity at all (dropped or added), not because the wording differs.

{hints_section}

Only report entities you are confident are genuinely named/proper nouns, AND that are \
actually mentioned in at least one of the two transcripts. Never output an entity with \
both gold_span and asr_span null -- if an entity (including one from the known-entities \
list below, if given) does not appear anywhere in either transcript, simply leave it out \
of your output entirely; do not report it as a way of confirming its absence. If none are \
present in either transcript, return an empty list."""

_HINTS_BLOCK = """Known entities that recur in this dataset -- use these exact canonical \
spellings if you see a variant of one, but this list is NOT exhaustive, and most of it will \
be IRRELEVANT to any given call -- only report a known entity if it actually appears in \
this call's gold or ASR text; do not report it just because it's on this list:
{known_entities}"""

_NO_HINTS_BLOCK = (
    "You are not given a predefined entity list for this dataset -- rely entirely on your "
    "own judgment to decide what counts as a salient named entity."
)


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_and_align_entities_llm(call_id: str, gold: str, asr: str, known_entities: list = None) -> list:
    """One joint call sees both transcripts and aligns entities across them, avoiding the
    cross-call canonical-name drift that two independent per-side extractions produced
    (see docstring above and reports/hints_vs_no_hints_comparison.md)."""

    known_entities = known_entities or []
    cache = _load_cache()
    hint_tag = "aligned" if known_entities else "aligned:no_hints"
    key = f"{call_id}:{hint_tag}:{MODEL}"
    if key in cache:
        return cache[key]

    from openai import OpenAI, RateLimitError  # imported lazily so the module loads without the
    # package for cached-only runs

    client = OpenAI(api_key=_API_KEY, base_url=_BASE_URL)
    hints_section = (
        _HINTS_BLOCK.format(known_entities=", ".join(known_entities)) if known_entities else _NO_HINTS_BLOCK
    )
    system = _SYSTEM_PROMPT.format(hints_section=hints_section)
    user_content = (
        f"GOLD:\n{gold if gold.strip() else '(empty)'}\n\nASR:\n{asr if asr.strip() else '(empty)'}"
    )

    for attempt in range(_MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_schema", "json_schema": _ALIGNED_ENTITY_SCHEMA},
                temperature=0,
                max_tokens=4096,  # this model defaults to requesting up to 65536 if unspecified,
                # which exceeds available credits long before the output (a short JSON entity
                # list) needs it
            )
            break
        except RateLimitError:
            if attempt == _MAX_RETRIES - 1:
                raise
            time.sleep(_RETRY_DELAY_SECONDS)
    result = json.loads(response.choices[0].message.content)["entities"]

    cache[key] = result
    _save_cache(cache)
    return result
