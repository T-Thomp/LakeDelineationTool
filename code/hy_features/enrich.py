"""Add OGC HY_Features semantic columns to GeoDataFrames."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from hy_features.schema import (
    CATCHMENT_ID,
    FLOWPATH_ID,
    FRAC_LAKE,
    HOST_FLOWPATH_ID,
    HYDRO_LOC_TYPE,
    HYF_TYPE,
    HY_CATCHMENT_AREA,
    HY_FLOWPATH,
    HY_HYDRO_LOCATION,
    HY_HYDROMETRIC_FEATURE,
    IS_LAKE_CATCHMENT,
    LAKE_TYPE,
    LEGACY_LAKE_TYPE,
    LAKE_AREA_M2,
    LEGACY_BASIN_ID,
    LEGACY_FLOWPATH_ID,
    LEGACY_GAUGE_IDS,
    LEGACY_IS_LAKE,
    LEGACY_LAKE_AREA,
    LEGACY_LAKE_ID,
    LEGACY_LOWER_ID,
    LOWER_CATCHMENT_ID,
    POINT_TYPE_TO_HYDRO_LOC,
    REALIZES_CATCHMENT,
    STATION_CODE,
    WATERBODY_CLASS,
    WATERBODY_ID,
    classify_waterbody,
    normalize_id,
)

# Station id / name columns as written by HYDAT queries and shapefile renames
GAUGE_ID_COLUMNS = ("STATION_NUMBER", "STATION_NO", "STATION_NU")
GAUGE_NAME_COLUMNS = ("STATION_NAME", "STATION_NM", "STATION_NA")


def _positive_id(value: object, outlet_sentinel: int | None = None) -> str:
    """Normalized id, or ``""`` for missing, non-positive, or sentinel values."""
    text = normalize_id(value)
    if not text:
        return ""
    try:
        num = float(text)
    except ValueError:
        return text
    if num <= 0 or (outlet_sentinel is not None and num == outlet_sentinel):
        return ""
    return text


def _lake_type_from_row(row: pd.Series) -> int | None:
    """Read HydroLAKES Lake_type from basin row (canonical or legacy column)."""
    for col in (LAKE_TYPE, LEGACY_LAKE_TYPE):
        if col in row.index:
            try:
                val = int(row[col])
            except (TypeError, ValueError):
                continue
            if val > 0:
                return val
    return None


def _waterbody_class_for_catchment(row: pd.Series) -> str:
    if not row.get(IS_LAKE_CATCHMENT, 0):
        return ""
    return classify_waterbody(_lake_type_from_row(row))


def enrich_catchment_areas(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add HY_Features columns to basin / catchment-area polygons."""
    out = gdf.copy()
    basin_col = CATCHMENT_ID if CATCHMENT_ID in out.columns else LEGACY_BASIN_ID
    if basin_col not in out.columns:
        raise ValueError(f"Catchment area layer missing id column ({CATCHMENT_ID} or {LEGACY_BASIN_ID})")

    out[CATCHMENT_ID] = out[basin_col].map(normalize_id)
    out[HYF_TYPE] = HY_CATCHMENT_AREA
    out[REALIZES_CATCHMENT] = out[CATCHMENT_ID]

    if LEGACY_IS_LAKE in out.columns:
        out[IS_LAKE_CATCHMENT] = pd.to_numeric(out[LEGACY_IS_LAKE], errors="coerce").fillna(0).astype(int)
    elif IS_LAKE_CATCHMENT not in out.columns:
        out[IS_LAKE_CATCHMENT] = 0

    lake_id_col = WATERBODY_ID if WATERBODY_ID in out.columns else LEGACY_LAKE_ID
    if lake_id_col in out.columns:
        out[WATERBODY_ID] = out[lake_id_col].map(_positive_id)
        out[WATERBODY_CLASS] = out.apply(_waterbody_class_for_catchment, axis=1)
    else:
        out[WATERBODY_ID] = ""
        out[WATERBODY_CLASS] = ""

    area_col = LAKE_AREA_M2 if LAKE_AREA_M2 in out.columns else LEGACY_LAKE_AREA
    if area_col in out.columns and LAKE_AREA_M2 not in out.columns:
        out[LAKE_AREA_M2] = pd.to_numeric(out[area_col], errors="coerce").fillna(0.0)

    if LEGACY_GAUGE_IDS in out.columns and STATION_CODE not in out.columns:
        out[STATION_CODE] = out[LEGACY_GAUGE_IDS].fillna("").astype(str)

    return out


def enrich_flowpaths(gdf: gpd.GeoDataFrame, outlet_sentinel: int = -9999) -> gpd.GeoDataFrame:
    """Add HY_Features columns to stream link / flowpath layer."""
    out = gdf.copy()
    link_col = FLOWPATH_ID if FLOWPATH_ID in out.columns else LEGACY_FLOWPATH_ID
    down_col = LOWER_CATCHMENT_ID if LOWER_CATCHMENT_ID in out.columns else LEGACY_LOWER_ID

    if link_col not in out.columns:
        raise ValueError(f"Flowpath layer missing id column ({FLOWPATH_ID} or {LEGACY_FLOWPATH_ID})")

    out[FLOWPATH_ID] = out[link_col].map(normalize_id)
    out[CATCHMENT_ID] = out[FLOWPATH_ID]
    out[REALIZES_CATCHMENT] = out[CATCHMENT_ID]
    out[HYF_TYPE] = HY_FLOWPATH

    if down_col in out.columns:
        out[LOWER_CATCHMENT_ID] = out[down_col].map(
            lambda x: _positive_id(x, outlet_sentinel)
        )
    else:
        out[LOWER_CATCHMENT_ID] = ""

    return out


def enrich_waterbodies(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add HY_Features columns to HydroLAKES polygons."""
    from hy_features.schema import FEATURE_NAME, HYLAKES_ID, HYLAKES_LAKE_NAME, HYLAKES_LAKE_TYPE

    out = gdf.copy()
    if HYLAKES_ID in out.columns:
        out[WATERBODY_ID] = out[HYLAKES_ID].map(normalize_id)
    elif WATERBODY_ID in out.columns:
        out[WATERBODY_ID] = out[WATERBODY_ID].map(normalize_id)
    else:
        out[WATERBODY_ID] = out.index.astype(str)

    if HYLAKES_LAKE_TYPE in out.columns:
        out[WATERBODY_CLASS] = out[HYLAKES_LAKE_TYPE].apply(classify_waterbody)
    else:
        from hy_features.schema import HY_LAKE
        out[WATERBODY_CLASS] = HY_LAKE

    out[HYF_TYPE] = out[WATERBODY_CLASS]

    if HYLAKES_LAKE_NAME in out.columns:
        out[FEATURE_NAME] = out[HYLAKES_LAKE_NAME].fillna("").astype(str)
    elif FEATURE_NAME not in out.columns:
        out[FEATURE_NAME] = ""

    return out


def enrich_hydro_locations(
    gdf: gpd.GeoDataFrame,
    point_type_col: str = "point_type",
) -> gpd.GeoDataFrame:
    """Add HY_Features columns to pour-point layers (``HY_HydroLocation``)."""
    out = gdf.copy()
    out[HYF_TYPE] = HY_HYDRO_LOCATION

    if point_type_col in out.columns:
        out[HYDRO_LOC_TYPE] = out[point_type_col].map(POINT_TYPE_TO_HYDRO_LOC).fillna(
            out[point_type_col]
        )
    elif HYDRO_LOC_TYPE not in out.columns:
        out[HYDRO_LOC_TYPE] = ""

    if LEGACY_LAKE_ID in out.columns:
        out[WATERBODY_ID] = out[LEGACY_LAKE_ID].map(_positive_id)

    from hy_features.schema import FEATURE_NAME

    if "name" in out.columns:
        out[FEATURE_NAME] = out["name"].fillna("").astype(str)

    return out


def enrich_hydrometric_features(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add HY_Features columns to gauge point layer."""
    from hy_features.schema import FEATURE_NAME, HYDRO_LOC_HYDROMETRIC

    out = gdf.copy()
    out[HYF_TYPE] = HY_HYDROMETRIC_FEATURE
    out[HYDRO_LOC_TYPE] = HYDRO_LOC_HYDROMETRIC

    id_col = next((c for c in GAUGE_ID_COLUMNS if c in out.columns), None)
    if id_col is not None:
        out[STATION_CODE] = out[id_col].fillna("").astype(str).str.strip()
    elif STATION_CODE not in out.columns:
        out[STATION_CODE] = ""

    missing = out[STATION_CODE].astype(str).str.len() == 0
    if missing.any():
        out.loc[missing, STATION_CODE] = [f"unnamed_{i}" for i in range(int(missing.sum()))]

    name_col = next((c for c in GAUGE_NAME_COLUMNS if c in out.columns), None)
    if name_col is not None:
        out[FEATURE_NAME] = out[name_col].fillna("").astype(str)
    elif FEATURE_NAME not in out.columns:
        out[FEATURE_NAME] = ""

    if HOST_FLOWPATH_ID not in out.columns:
        out[HOST_FLOWPATH_ID] = ""

    if CATCHMENT_ID not in out.columns:
        out[CATCHMENT_ID] = ""

    return out


def build_catchment_registry_from_geofabric(
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
) -> "CatchmentRegistry":
    """
    Register one holistic catchment per basin / flowpath id (enriched layers).

    Realizations and associations are attached later from the stamped layers.
    """
    from hy_features.models import CatchmentRegistry

    registry = CatchmentRegistry()
    down_map = dict(zip(streams[FLOWPATH_ID], streams[LOWER_CATCHMENT_ID]))

    for _, row in basins.iterrows():
        cid = str(row[CATCHMENT_ID])
        is_lake = int(row.get(IS_LAKE_CATCHMENT, 0) or 0)
        wb = str(row.get(WATERBODY_ID, "")) if is_lake else ""
        registry.add_catchment(
            cid,
            lower_catchment_id=down_map.get(cid) or None,
            waterbody_id=wb or None,
        )

    for fid, lower in down_map.items():
        registry.add_catchment(str(fid), lower_catchment_id=lower or None)

    return registry
