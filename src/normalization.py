"""Text normalization shared by WER, entity matching, and hallucination detection.

Rationale (see README for the full write-up):
  - Gold and ASR text mix Devanagari and Latin script for the same spoken words
    (code-switched Hindi-English calls). We do NOT transliterate everything to one
    script for WER, because that would hide real recognition mistakes. Instead we:
      1. Strip noise markers and punctuation/case artifacts that carry no meaning.
      2. Leave Devanagari text as-is (never transliterate/drop non-English content).
      3. Handle script-equivalence (delhi <-> दिल्ली) at the entity level via an
         explicit alias catalog, not by force-normalizing all text to one script.
  - This keeps WER a legitimate (if noisy) diagnostic, and pushes the
    business-relevant comparison (proper nouns, numbers) to matching that is aware
    of script/spelling variants.
"""

import re
import unicodedata

import regex

# Python's built-in `re` module's Unicode `\w` excludes combining marks (Mn/Mc),
# which are exactly the vowel signs/nukta that make Devanagari words well-formed
# (e.g. गुड़गांव). Stripping punctuation with plain `re` silently mangles every
# Devanagari word in the dataset. The third-party `regex` module's `\w` is
# Unicode-property-aware and keeps combining marks, so it is used here instead.
NOISE_TAG_RE = re.compile(r"\{\s*noise\s*\}", re.IGNORECASE)
PUNCT_RE = regex.compile(r"[^\w\s]", regex.UNICODE)
MULTISPACE_RE = re.compile(r"\s+")

# Cosmetic ASR/gold spelling variants that are not meaningful differences.
# Left intentionally small: only variants observed in this dataset.
CONTRACTION_MAP = {
    "ma'am": "maam",
    "mam": "maam",
    "i'm": "im",
    "don't": "dont",
}


def strip_noise_tags(text: str) -> str:
    return NOISE_TAG_RE.sub(" ", text)


def normalize_unicode(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def expand_known_variants(text: str) -> str:
    for src, dst in CONTRACTION_MAP.items():
        text = re.sub(re.escape(src), dst, text, flags=re.IGNORECASE)
    return text


def normalize_text(text: str, lowercase: bool = True, strip_punct: bool = True) -> str:
    """General-purpose normalization used for WER/CER and token-level matching."""

    text = normalize_unicode(text)
    text = strip_noise_tags(text)
    text = expand_known_variants(text)
    if lowercase:
        text = text.lower()
    if strip_punct:
        text = PUNCT_RE.sub(" ", text)
    text = MULTISPACE_RE.sub(" ", text).strip()
    return text


def tokenize(text: str) -> list:
    return normalize_text(text).split()


def ngram_windows(tokens: list, max_n: int = 2) -> list:
    """Unigrams and bigrams, used so multi-word aliases (e.g. 'seven up') can match."""

    windows = list(tokens)
    for n in range(2, max_n + 1):
        for i in range(len(tokens) - n + 1):
            windows.append(" ".join(tokens[i : i + n]))
    return windows


# Numerals (English + the Hindi/Hinglish transliterations actually observed in this
# dataset) plus the units these calls quote quantities in. Originally built for
# src/correction.py's number-word guards (see docs/challenges_and_decisions.md for
# the audit that produced it), and reused by src/hallucination.py's quantity-
# preservation check -- both need the same "is this token part of a quantity
# expression" judgment, so it lives here as the one shared definition rather than
# being duplicated (and risking drift) in two modules.
NUMBER_AND_QUANTITY_WORDS = {
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "half", "dozen", "point", "percent", "percentage",
    "ek", "do", "teen", "char", "chaar", "paanch", "panch", "chhe", "chhah", "saat", "aath",
    "nau", "das", "gyarah", "barah", "bara", "tera", "chaudah", "pandrah", "solah", "satrah",
    "atharah", "unnis", "bees", "bis", "tees", "chalis", "pachas", "pachaas", "sau", "hazaar",
    "hazar", "sadhe", "saadhe", "aadha", "adha", "paun",
    "liter", "litre", "liters", "litres", "ml", "bottle", "bottles", "case", "cases", "pack",
    "packs", "piece", "pieces", "kg", "gram", "grams",
    # Devanagari-script equivalents -- these calls code-switch mid-sentence, so the number
    # word right next to an English brand mention is often Devanagari, not transliterated
    # Latin. Listed here as normalize_text() never transliterates Devanagari; this dataset's
    # own token frequencies were used to find the actual spellings in use rather than
    # assuming standard ones.
    "एक", "दो", "तीन", "चार", "पांच", "पाँच", "छह", "छे", "सात", "आठ", "नौ", "दस",
    "ग्यारह", "बारह", "तेरह", "चौदह", "पंद्रह", "सोलह", "सत्रह", "अठारह", "उन्नीस", "बीस",
    "तीस", "चालीस", "पचास", "साठ", "सत्तर", "अस्सी", "नब्बे", "सौ", "हज़ार", "हजार",
    "साढ़े", "आधा", "पौना", "सवा", "पच्चीस", "प्रतिशत",
    "लीटर", "बोतल",
}
