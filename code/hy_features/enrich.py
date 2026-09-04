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
)


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

    out[CATCHMENT_ID] = out[basin_col].astype(str)
    out[HYF_TYPE] = HY_CATCHMENT_AREA
    out[REALIZES_CATCHMENT] = out[CATCHMENT_ID]

    if LEGACY_IS_LAKE in out.columns:
        out[IS_LAKE_CATCHMENT] = pd.to_numeric(out[LEGACY_IS_LAKE], errors="coerce").fillna(0).astype(int)
    elif IS_LAKE_CATCHMENT not in out.columns:
        out[IS_LAKE_CATCHMENT] = 0

    lake_id_col = WATERBODY_ID if WATERBODY_ID in out.columns else LEGACY_LAKE_ID
    if lake_id_col in out.columns:
        out[WATERBODY_ID] = out[lake_id_col].apply(
            lambda x: "" if pd.isna(x) or int(x) < 0 else str(int(x))
        )
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

    out[FLOWPATH_ID] = out[link_col].astype(str)
    out[CATCHMENT_ID] = out[FLOWPATH_ID]
    out[REALIZES_CATCHMENT] = out[CATCHMENT_ID]
    out[HYF_TYPE] = HY_FLOWPATH

    if down_col in out.columns:
        out[LOWER_CATCHMENT_ID] = out[down_col].apply(
            lambda x: "" if pd.isna(x) or int(x) <= 0 or int(x) == outlet_sentinel else str(int(x))
        )
    else:
        out[LOWER_CATCHMENT_ID] = ""

    return out


def enrich_waterbodies(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add HY_Features columns to HydroLAKES polygons."""
    from hy_features.schema import FEATURE_NAME, HYLAKES_ID, HYLAKES_LAKE_NAME, HYLAKES_LAKE_TYPE

    out = gdf.copy()
    if HYLAKES_ID in out.columns:
        out[WATERBODY_ID] = out[HYLAKES_ID].astype(str)
    elif WATERBODY_ID not in out.columns:
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
    """Add HY_Features columns to pour-point / nexus point layers."""
    from hy_features.stamp import assign_hydro_location_nexus_ids

    out = gdf.copy()
    out[HYF_TYPE] = HY_HYDRO_LOCATION

    if point_type_col in out.columns:
        out[HYDRO_LOC_TYPE] = out[point_type_col].map(POINT_TYPE_TO_HYDRO_LOC).fillna(
            out[point_type_col]
        )
    elif HYDRO_LOC_TYPE not in out.columns:
        out[HYDRO_LOC_TYPE] = ""

    if "lake_id" in out.columns:
        out[WATERBODY_ID] = out["lake_id"].apply(
            lambda x: "" if pd.isna(x) or int(x) < 0 else str(int(x))
        )

    return assign_hydro_location_nexus_ids(out)


def enrich_hydrometric_features(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add HY_Features columns to gauge point layer."""
    from hy_features.schema import HYDRO_LOC_HYDROMETRIC

    out = gdf.copy()
    out[HYF_TYPE] = HY_HYDROMETRIC_FEATURE
    out[HYDRO_LOC_TYPE] = HYDRO_LOC_HYDROMETRIC

    if "STATION_NUMBER" in out.columns:
        out[STATION_CODE] = out["STATION_NUMBER"].astype(str)
    elif "STATION_NU" in out.columns:
        out[STATION_CODE] = out["STATION_NU"].astype(str)
    elif STATION_CODE not in out.columns:
        out[STATION_CODE] = ""

    if HOST_FLOWPATH_ID not in out.columns:
        out[HOST_FLOWPATH_ID] = ""

    if CATCHMENT_ID not in out.columns:
        out[CATCHMENT_ID] = ""

    return out


def build_catchment_registry_from_geofabric(
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
) -> "CatchmentRegistry":
    """Build catchment registry from enriched basin and stream layers."""
    from hy_features.models import CatchmentRegistry

    registry = CatchmentRegistry()
    basin_col = CATCHMENT_ID if CATCHMENT_ID in basins.columns else LEGACY_BASIN_ID
    link_col = FLOWPATH_ID if FLOWPATH_ID in streams.columns else LEGACY_FLOWPATH_ID
    down_col = LOWER_CATCHMENT_ID if LOWER_CATCHMENT_ID in streams.columns else LEGACY_LOWER_ID

    down_map = {}
    if link_col in streams.columns and down_col in streams.columns:
        down_map = dict(zip(streams[link_col].astype(str), streams[down_col].astype(str)))

    for _, row in basins.iterrows():
        cid = str(row[basin_col])
        wb = str(row.get(WATERBODY_ID, row.get(LEGACY_LAKE_ID, "")))
        wb = wb if wb and wb not in ("-1", "nan", "") else None
        is_lake = int(row.get(IS_LAKE_CATCHMENT, row.get(LEGACY_IS_LAKE, 0)) or 0)
        lower = down_map.get(cid, None)
        if lower in ("-1", "", "nan", None):
            lower = None

        registry.add(cid, HY_CATCHMENT_AREA, cid, waterbody_id=wb, lower_catchment_id=lower)
        if is_lake and wb:
            wb_class = classify_waterbody(_lake_type_from_row(row))
            registry.add(cid, wb_class, wb, waterbody_id=wb, notes="merged lake catchment")

    for _, row in streams.iterrows():
        fid = str(row[link_col])
        registry.add(fid, HY_FLOWPATH, fid, lower_catchment_id=down_map.get(fid))

    return registry
