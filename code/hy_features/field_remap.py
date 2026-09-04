"""
Remap HY_Features canonical columns to downstream model / shapefile field names.

Presets define column renames per layer (catchment_area, flowpath, etc.).
Built-in presets live in model_presets.json; MESH is one preset, not the only target.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from hy_features.schema import (
    CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    DEFAULT_LAYER_ALIASES,
    DEFAULT_OUTLET_SENTINEL,
    DISTANCE_FROM_OUTLET_M,
    DISTANCE_FROM_OUTLET_PCT,
    DRAINAGE_PATTERN_COL,
    FLOWPATH_ID,
    FRAC_LAKE,
    HOST_FLOWPATH_ID,
    HYDRO_LOC_TYPE,
    HYF_TYPE,
    HYF_TYPE_URI,
    INFLOW_NEXUS_ID,
    IS_LAKE_CATCHMENT,
    LAKE_AREA_M2,
    LEGACY_BASIN_ID,
    LEGACY_FLOWPATH_ID,
    LEGACY_GAUGE_IDS,
    LEGACY_IS_LAKE,
    LEGACY_LAKE_AREA,
    LEGACY_LAKE_ID,
    LEGACY_LOWER_ID,
    LINEAR_ELEMENT_ID,
    LOWER_CATCHMENT_ID,
    NEXUS_ID,
    OUTFLOW_NEXUS_ID,
    REALIZES_CATCHMENT,
    RECEIVING_CATCHMENT_ID,
    REFERENCE_NEXUS_ID,
    STATION_CODE,
    UPPER_CATCHMENT_ID,
    WATERBODY_CLASS,
    WATERBODY_ID,
)

_PRESETS_PATH = Path(__file__).resolve().parent / "model_presets.json"

# Columns removed with drop_metadata / drop_hyf_metadata (HY_Features semantics only)
METADATA_COLUMNS = {
    HYF_TYPE,
    HYF_TYPE_URI,
    REALIZES_CATCHMENT,
    WATERBODY_CLASS,
    HYDRO_LOC_TYPE,
    HOST_FLOWPATH_ID,
    OUTFLOW_NEXUS_ID,
    INFLOW_NEXUS_ID,
    UPPER_CATCHMENT_ID,
    REFERENCE_NEXUS_ID,
    LINEAR_ELEMENT_ID,
    DISTANCE_FROM_OUTLET_M,
    DISTANCE_FROM_OUTLET_PCT,
    DRAINAGE_PATTERN_COL,
    NEXUS_ID,
    CONTRIBUTING_CATCHMENT_ID,
    RECEIVING_CATCHMENT_ID,
    STATION_CODE,
}

# Backward-compatible alias
HYF_METADATA_COLUMNS = METADATA_COLUMNS


def load_model_presets(path: Path | str | None = None) -> dict[str, dict[str, Any]]:
    """
    Load named model presets from JSON merged with built-in defaults.

    Each preset:
      - description (optional)
      - outlet_sentinel (optional, default -9999)
      - layers: { layer_kind: { canonical_col: output_col } }
    """
    presets: dict[str, dict[str, Any]] = {
        "mesh": {
            "description": "MESH / WATFLOOD TauDEM-style fields",
            "outlet_sentinel": DEFAULT_OUTLET_SENTINEL,
            "layers": copy.deepcopy(DEFAULT_LAYER_ALIASES),
        }
    }

    preset_path = Path(path) if path else _PRESETS_PATH
    if preset_path.is_file():
        file_data = json.loads(preset_path.read_text(encoding="utf-8"))
        for name, spec in file_data.items():
            presets[name] = spec

    return presets


def list_model_names(path: Path | str | None = None) -> list[str]:
    return sorted(load_model_presets(path).keys())


def get_model_mapping(
    layer_kind: str,
    preset: str = "mesh",
    preset_path: Path | str | None = None,
) -> tuple[dict[str, str], int]:
    """Return (canonical -> output column map, outlet_sentinel) for a layer."""
    presets = load_model_presets(preset_path)
    if preset not in presets:
        raise ValueError(
            f"Unknown preset '{preset}'. Choose from: {', '.join(sorted(presets))}"
        )
    spec = presets[preset]
    layers = spec.get("layers", spec)
    if layer_kind not in layers:
        raise ValueError(
            f"Preset '{preset}' has no mapping for layer '{layer_kind}'. "
            f"Available: {', '.join(sorted(layers))}"
        )
    sentinel = int(spec.get("outlet_sentinel", DEFAULT_OUTLET_SENTINEL))
    return dict(layers[layer_kind]), sentinel


def get_default_mapping(layer_kind: str, preset: str = "mesh") -> dict[str, str]:
    mapping, _ = get_model_mapping(layer_kind, preset=preset)
    return mapping


def list_available_mappings(
    preset: str = "mesh",
    preset_path: Path | str | None = None,
) -> dict[str, dict[str, str]]:
    presets = load_model_presets(preset_path)
    if preset not in presets:
        raise ValueError(f"Unknown preset '{preset}'")
    return copy.deepcopy(presets[preset].get("layers", presets[preset]))


def build_custom_mapping(
    layer_kind: str,
    overrides: dict[str, str],
    preset: str = "mesh",
    preset_path: Path | str | None = None,
) -> dict[str, str]:
    base, _ = get_model_mapping(layer_kind, preset=preset, preset_path=preset_path)
    base.update(overrides)
    return base


def _merge_downstream_ids(
    out: gpd.GeoDataFrame,
    canonical_col: str,
    target_col: str,
) -> gpd.GeoDataFrame:
    """Preserve existing downstream IDs when canonical lower_catchment_id is empty."""
    if canonical_col not in out.columns:
        return out

    canonical = out[canonical_col].astype(str).str.strip()
    empty = canonical.isin(("", "nan", "None", "none"))

    if target_col in out.columns and empty.any():
        out.loc[empty, canonical_col] = out.loc[empty, target_col].astype(str)

    if target_col in out.columns and target_col != canonical_col:
        out = out.drop(columns=[target_col])

    return out.rename(columns={canonical_col: target_col})


def _coerce_id_columns(
    out: gpd.GeoDataFrame,
    mapping: dict[str, str],
    downstream_col: str | None,
    sentinel: int,
) -> gpd.GeoDataFrame:
    """Coerce mapped ID columns to integers; apply outlet sentinel on downstream column."""
    id_targets = set(mapping.values())
    for col in id_targets:
        if col not in out.columns:
            continue
        numeric = pd.to_numeric(out[col], errors="coerce")
        if col == downstream_col:
            numeric = numeric.fillna(sentinel).replace(0, sentinel)
            empty_mask = out[col].astype(str).str.strip().isin(("", "nan", "None"))
            numeric = numeric.where(~empty_mask, sentinel)
        else:
            numeric = numeric.fillna(-1)
        out[col] = numeric.astype(int)
    return out


def apply_field_remap(
    gdf: gpd.GeoDataFrame,
    layer_kind: str,
    mapping: dict[str, str] | None = None,
    *,
    preset: str = "mesh",
    preset_path: Path | str | None = None,
    drop_metadata: bool = False,
    coerce_ids_to_int: bool = True,
    outlet_sentinel: int | None = None,
) -> gpd.GeoDataFrame:
    """
    Rename HY_Features canonical columns to a preset's output field names.

    Works with shapefile or GeoPackage output (``remap_vector_file``).
    """
    if mapping is None:
        mapping, default_sentinel = get_model_mapping(
            layer_kind, preset=preset, preset_path=preset_path,
        )
    else:
        _, default_sentinel = get_model_mapping(
            layer_kind, preset=preset, preset_path=preset_path,
        )

    sentinel = outlet_sentinel if outlet_sentinel is not None else default_sentinel
    out = gdf.copy()

    down_key = LOWER_CATCHMENT_ID
    down_target = mapping.get(down_key)
    mapping_without_down = {k: v for k, v in mapping.items() if k != down_key}

    for canonical, output_name in mapping_without_down.items():
        if canonical not in out.columns:
            continue
        if output_name in out.columns and output_name != canonical:
            out = out.drop(columns=[output_name])
        out = out.rename(columns={canonical: output_name})

    if down_target and down_key in out.columns:
        out = _merge_downstream_ids(out, down_key, down_target)

    if drop_metadata:
        drop_cols = [c for c in METADATA_COLUMNS if c in out.columns]
        out = out.drop(columns=drop_cols, errors="ignore")

    if coerce_ids_to_int:
        out = _coerce_id_columns(out, mapping, down_target, sentinel)

    return out


def apply_mesh_remap(*args, **kwargs):
    """Deprecated alias for :func:`apply_field_remap`."""
    if "model" in kwargs:
        kwargs["preset"] = kwargs.pop("model")
    if kwargs.pop("drop_hyf_metadata", False):
        kwargs["drop_metadata"] = True
    return apply_field_remap(*args, **kwargs)


def remap_vector_file(
    input_path: str,
    output_path: str,
    layer_kind: str,
    mapping: dict[str, str] | None = None,
    *,
    preset: str = "mesh",
    preset_path: Path | str | None = None,
    input_layer: str | None = None,
    drop_metadata: bool = False,
    coerce_ids_to_int: bool = True,
    outlet_sentinel: int | None = None,
    driver: str | None = None,
) -> gpd.GeoDataFrame:
    """Read a vector file, remap columns, write shapefile or GeoPackage."""
    read_kwargs: dict[str, Any] = {}
    if input_layer:
        read_kwargs["layer"] = input_layer

    gdf = gpd.read_file(input_path, **read_kwargs)
    remapped = apply_field_remap(
        gdf,
        layer_kind,
        mapping=mapping,
        preset=preset,
        preset_path=preset_path,
        drop_metadata=drop_metadata,
        coerce_ids_to_int=coerce_ids_to_int,
        outlet_sentinel=outlet_sentinel,
    )

    if driver is None:
        suffix = output_path.rsplit(".", 1)[-1].lower()
        driver = "GPKG" if suffix == "gpkg" else "ESRI Shapefile"

    remapped.to_file(output_path, driver=driver)
    return remapped


def remap_file(*args, **kwargs):
    """Deprecated alias for :func:`remap_vector_file`."""
    if "model" in kwargs:
        kwargs["preset"] = kwargs.pop("model")
    if kwargs.pop("drop_hyf_metadata", False):
        kwargs["drop_metadata"] = True
    return remap_vector_file(*args, **kwargs)


CANONICAL_COLUMNS_BY_LAYER: dict[str, list[str]] = {
    "catchment_area": [
        CATCHMENT_ID,
        IS_LAKE_CATCHMENT,
        WATERBODY_ID,
        LAKE_AREA_M2,
        FRAC_LAKE,
        STATION_CODE,
        LEGACY_GAUGE_IDS,
    ],
    "flowpath": [
        FLOWPATH_ID,
        CATCHMENT_ID,
        LOWER_CATCHMENT_ID,
        REALIZES_CATCHMENT,
    ],
}
