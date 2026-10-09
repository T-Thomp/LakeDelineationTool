"""
HY_HydroFeatureName records (Section 7.3.1, Table 5).

Each name given to a feature becomes one row of the ``feature_name`` table with the
five HY_HydroFeatureName attributes (``name``, ``names_part``, ``preferred_by``,
``usage``, ``variant_spelling``) plus a ``language`` profile extension. The
``feature_name`` column on each layer stays as a convenience copy of the preferred name.

Alternative names are picked up from ``feature_name_<lang>`` columns (for example
``feature_name_fr``) on any named layer.
"""

from __future__ import annotations

import re

import pandas as pd

from hy_features.schema import (
    FEATURE_ID,
    FEATURE_NAME,
    HYF_TYPE,
    NAME,
    NAME_LANGUAGE,
    NAME_PREFERRED_BY,
    NAME_USAGE,
    NAME_USAGE_CONVENTIONAL,
    NAME_USAGE_OFFICIAL,
    NAMED_FEATURE_ID,
    NAMES_PART,
    UNDETERMINED_LANGUAGE,
    VARIANT_SPELLING,
)

# Layer -> (usage, preferred_by) for the names in its ``feature_name`` column.
# Pour-point names (``Lake_1``) are generated labels, not names, and are skipped.
NAME_SOURCES: dict[str, tuple[str, str]] = {
    "waterbody": (NAME_USAGE_CONVENTIONAL, "HydroLAKES"),
    "hydrometric_feature": (NAME_USAGE_OFFICIAL, "Water Survey of Canada (HYDAT)"),
    "hydrometric_network": (NAME_USAGE_OFFICIAL, "Water Survey of Canada (HYDAT)"),
}

_ALT_NAME_COL = re.compile(rf"^{FEATURE_NAME}_([a-z]{{2,3}})$")

COLUMNS = [NAMED_FEATURE_ID, HYF_TYPE, NAME, NAME_LANGUAGE, NAME_USAGE, NAME_PREFERRED_BY,
           NAMES_PART, VARIANT_SPELLING]


def build_feature_name_table(
    layers: dict[str, pd.DataFrame],
    language: str = UNDETERMINED_LANGUAGE,
    sources: dict[str, tuple[str, str]] | None = None,
) -> pd.DataFrame:
    """"
    One HY_HydroFeatureName row per (feature, name).

    ``language`` (ISO 639 code) applies to the ``feature_name`` column; only that name
    is marked preferred (``preferred_by`` set).
    """
    records: list[dict] = []
    for layer, (usage, preferred_by) in (sources or NAME_SOURCES).items():
        frame = layers.get(layer)
        if frame is None or frame.empty or FEATURE_ID not in frame.columns:
            continue
        name_cols = [(FEATURE_NAME, language, True)] if FEATURE_NAME in frame.columns else []
        for col in frame.columns:
            match = _ALT_NAME_COL.match(str(col))
            if match:
                name_cols.append((col, match.group(1), False))

        for col, lang, preferred in name_cols:
            for fid, hyf_type, value in zip(frame[FEATURE_ID], frame[HYF_TYPE], frame[col]):
                text = "" if pd.isna(value) else str(value).strip()
                if not text:
                    continue
                records.append({
                    NAMED_FEATURE_ID: fid,
                    HYF_TYPE: hyf_type,
                    NAME: text,
                    NAME_LANGUAGE: lang,
                    NAME_USAGE: usage,
                    NAME_PREFERRED_BY: preferred_by if preferred else "",
                    NAMES_PART: "false",
                    VARIANT_SPELLING: "false",
                })
    return pd.DataFrame(records, columns=COLUMNS).drop_duplicates(
        subset=[NAMED_FEATURE_ID, NAME, NAME_LANGUAGE],
    )
