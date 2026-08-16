"""Load the entity gazetteer and provide fuzzy alias matching against a transcript."""

from dataclasses import dataclass

import yaml
from rapidfuzz import fuzz

from src.normalization import ngram_windows, normalize_text, tokenize


@dataclass
class GazetteerEntity:
    canonical: str
    type: str
    use_cases: list  # None means applies to every use_case
    aliases: list

    def applies_to(self, use_case: str) -> bool:
        return self.use_cases is None or use_case in self.use_cases


def load_gazetteer(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return [
        GazetteerEntity(
            canonical=e["canonical"],
            type=e.get("type", "entity"),
            use_cases=e.get("use_cases"),
            aliases=[normalize_text(a) for a in e["aliases"]],
        )
        for e in raw["entities"]
    ]


def best_alias_match(text: str, entity: GazetteerEntity) -> dict:
    """Best fuzzy score of any window in `text` against any alias of `entity`.

    Returns the score (0-100), the matched alias, and the transcript span that
    matched, so callers can tell a script-variant match (alias itself in
    Devanagari) apart from a same-script match.
    """

    tokens = tokenize(text)
    if not tokens:
        return {"score": 0.0, "alias": None, "matched_span": None}

    windows = ngram_windows(tokens, max_n=2)

    best_score, best_alias, best_span = 0.0, None, None
    for alias in entity.aliases:
        for window in windows:
            score = fuzz.ratio(alias, window)
            if score > best_score:
                best_score, best_alias, best_span = score, alias, window

    return {"score": best_score, "alias": best_alias, "matched_span": best_span}
