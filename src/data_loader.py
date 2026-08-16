"""Load and validate the ASR call dataset (Phase 0)."""

import re
import pandas as pd

REQUIRED_COLUMNS = ["id", "use_case", "gold_transcript", "asr_output", "recording_url"]
DEVANAGARI_RE = re.compile(r"[ऀ-ॿ]")


def load_dataset(path: str) -> pd.DataFrame:
    df = pd.read_excel(path)

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing_cols:
        raise ValueError(f"Dataset at {path} is missing required columns: {missing_cols}")

    for col in ["gold_transcript", "asr_output"]:
        df[col] = df[col].fillna("").astype(str)

    return df


def profile_dataset(df: pd.DataFrame) -> dict:
    """Phase 0 profiling report: is one row one call, are ids unique, script mix, etc."""

    gold_tokens = df["gold_transcript"].str.split().apply(len)
    asr_tokens = df["asr_output"].str.split().apply(len)

    has_devanagari_gold = df["gold_transcript"].apply(lambda t: bool(DEVANAGARI_RE.search(t)))
    has_devanagari_asr = df["asr_output"].apply(lambda t: bool(DEVANAGARI_RE.search(t)))
    code_switched = has_devanagari_gold | has_devanagari_asr

    profile = {
        "num_rows": len(df),
        "unique_ids": df["id"].nunique(),
        "duplicate_ids": int(df["id"].duplicated().sum()),
        "unique_use_cases": df["use_case"].nunique(),
        "use_case_counts": df["use_case"].value_counts().to_dict(),
        "missing_values": df[REQUIRED_COLUMNS].isna().sum().to_dict(),
        "empty_gold_transcript": int((df["gold_transcript"].str.strip() == "").sum()),
        "empty_asr_output": int((df["asr_output"].str.strip() == "").sum()),
        "avg_gold_tokens": float(gold_tokens.mean()),
        "avg_asr_tokens": float(asr_tokens.mean()),
        "min_gold_tokens": int(gold_tokens.min()),
        "max_gold_tokens": int(gold_tokens.max()),
        "calls_with_devanagari_script": int(code_switched.sum()),
        "pct_calls_with_devanagari_script": float(code_switched.mean() * 100),
        "unique_recording_urls": df["recording_url"].nunique(),
    }
    return profile


def format_profile_report(profile: dict) -> str:
    lines = ["# Dataset Profile (Phase 0)", ""]
    lines.append(f"- Rows: {profile['num_rows']}")
    lines.append(f"- Unique ids: {profile['unique_ids']} (duplicates: {profile['duplicate_ids']})")
    lines.append(
        f"- Unique recording_urls: {profile['unique_recording_urls']} "
        "-> one row = one call (one recording), not one utterance"
    )
    lines.append(f"- Use cases ({profile['unique_use_cases']}): {profile['use_case_counts']}")
    lines.append(f"- Missing values per column: {profile['missing_values']}")
    lines.append(f"- Empty gold_transcript rows: {profile['empty_gold_transcript']}")
    lines.append(f"- Empty asr_output rows: {profile['empty_asr_output']}")
    lines.append(
        f"- Gold transcript length (tokens): avg={profile['avg_gold_tokens']:.1f}, "
        f"min={profile['min_gold_tokens']}, max={profile['max_gold_tokens']}"
    )
    lines.append(
        f"- Calls containing Devanagari script: {profile['calls_with_devanagari_script']} "
        f"({profile['pct_calls_with_devanagari_script']:.1f}%) -> confirms Hindi-English code-switching, "
        "naive WER will be inflated by script choice alone"
    )
    return "\n".join(lines)
