"""Export HY_Features-aligned GeoPackage products and optional shapefiles."""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

from hy_features.json_export import json_default
from hy_features.models import CatchmentRegistry
from hy_features.schema import (
    CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    DISTANCE_DESCRIPTION,
    DISTANCE_FROM_OUTLET_M,
    DISTANCE_FROM_OUTLET_PCT,
    DOWNSTREAM_WATERBODY_ID,
    FEATURE_ID,
    FEATURE_NAME,
    FLOWPATH_ID,
    HOST_FLOWPATH_ID,
    HYDRO_LOC_TYPE,
    HYF_TYPE,
    HYF_TYPE_URI,
    INFLOW_NEXUS_ID,
    IS_LAKE_CATCHMENT,
    LAKE_AREA_M2,
    LEGACY_BASIN_ID,
    LEGACY_LAKE_AREA,
    LINEAR_ELEMENT_ID,
    LOWER_CATCHMENT_ID,
    NETWORK_ID,
    NEXUS_ID,
    OUTFLOW_NEXUS_ID,
    REALIZED_NEXUS_ID,
    REALIZES_CATCHMENT,
    RECEIVING_CATCHMENT_ID,
    REFERENCE_NEXUS_ID,
    STATION_CODE,
    UPPER_CATCHMENT_ID,
    UPSTREAM_WATERBODY_ID,
    WATERBODY_CLASS,
    WATERBODY_ID,
)

# ESRI Shapefile limits: 10-character column names, 254-character strings.
SHAPEFILE_MAX_NAME = 10
SHAPEFILE_MAX_STRING = 254

SHAPEFILE_COLUMN_RENAMES: dict[str, str] = {
    "STATION_NUMBER": "STATION_NO",
    "STATION_NAME": "STATION_NM",
    "unit_area_km2": "area_km2",
    LAKE_AREA_M2: LEGACY_LAKE_AREA,
}

# HY_Features canonical columns live only in geofabric.gpkg; shapefiles keep the
# TauDEM / MESH names (DN, LINKNO, DSLINKNO, lake_id, ...). GDAL would otherwise
# truncate these to colliding 10-character names (waterbody_id / waterbody_class).
HY_ONLY_COLUMNS: frozenset[str] = frozenset({
    HYF_TYPE,
    HYF_TYPE_URI,
    FEATURE_ID,
    NETWORK_ID,
    CATCHMENT_ID,
    FLOWPATH_ID,
    LOWER_CATCHMENT_ID,
    REALIZES_CATCHMENT,
    IS_LAKE_CATCHMENT,
    WATERBODY_ID,
    WATERBODY_CLASS,
    STATION_CODE,
    HOST_FLOWPATH_ID,
    HYDRO_LOC_TYPE,
    NEXUS_ID,
    OUTFLOW_NEXUS_ID,
    INFLOW_NEXUS_ID,
    UPPER_CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    RECEIVING_CATCHMENT_ID,
    REALIZED_NEXUS_ID,
    REFERENCE_NEXUS_ID,
    LINEAR_ELEMENT_ID,
    DISTANCE_FROM_OUTLET_M,
    DISTANCE_FROM_OUTLET_PCT,
    DISTANCE_DESCRIPTION,
    UPSTREAM_WATERBODY_ID,
    DOWNSTREAM_WATERBODY_ID,
    FEATURE_NAME,
    "drainage_pattern",
})

# TauDEM cumulative/local areas in m² can exceed 1e10.
WIDE_AREA_FLOAT_COLS = frozenset({"DSContArea", "USContArea", "lake_area", "lake_area_m2"})


def strip_point_join_artifacts(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Drop spatial-join bookkeeping and basin ids from point layers.

    ``DN`` on gauge/pour-point exports comes from early Pass 1 joins and does not
    belong on point features; use ``STATION_NUMBER`` / ``station_code`` or the
    final ``hydrometric_feature`` / ``catchment_id`` in ``geofabric.gpkg``.
    """
    drop = {
        LEGACY_BASIN_ID,
        "index_right",
        "index_left",
    }
    cols = [c for c in gdf.columns if c in drop or c.startswith("index_")]
    if not cols:
        return gdf
    return gdf.drop(columns=cols)


def strip_hy_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Drop HY_Features canonical columns (GeoPackage-only) before a shapefile write."""
    cols = [c for c in gdf.columns if c in HY_ONLY_COLUMNS and c != LAKE_AREA_M2]
    return gdf.drop(columns=cols) if cols else gdf


def rename_shapefile_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Rename columns to ≤10-character shapefile-safe names; existing targets win."""
    out = gdf
    for old, new in SHAPEFILE_COLUMN_RENAMES.items():
        if old not in out.columns or old == new:
            continue
        if new in out.columns:
            out = out.drop(columns=[old])
        else:
            out = out.rename(columns={old: new})
    return out


def check_shapefile_columns(gdf: gpd.GeoDataFrame, filename: str | Path) -> None:
    """Fail fast on names GDAL would truncate into duplicates."""
    seen: dict[str, str] = {}
    clashes: list[str] = []
    for col in gdf.columns:
        if col == gdf.geometry.name:
            continue
        key = str(col)[:SHAPEFILE_MAX_NAME].lower()
        if key in seen:
            clashes.append(f"{seen[key]} / {col}")
        seen[key] = str(col)
    if clashes:
        raise ValueError(
            f"{filename}: columns collide after 10-character shapefile truncation: "
            + ", ".join(clashes)
            + ". Rename them or write a GeoPackage instead."
        )


def prepare_shapefile_frame(gdf: gpd.GeoDataFrame, filename: str | Path) -> gpd.GeoDataFrame:
    """HY columns removed, short names applied, strings capped at the DBF limit."""
    out = rename_shapefile_columns(strip_hy_columns(gdf.copy()))
    check_shapefile_columns(out, filename)
    for col in out.select_dtypes(include=["object", "string"]).columns:
        if col == out.geometry.name:
            continue
        text = out[col].where(out[col].isna(), out[col].astype(str))
        too_long = text.str.len() > SHAPEFILE_MAX_STRING
        if too_long.any():
            print(
                f"Warning: {int(too_long.sum())} value(s) in '{col}' exceed "
                f"{SHAPEFILE_MAX_STRING} characters and were truncated in {filename}"
            )
            out[col] = text.where(~too_long, text.str.slice(0, SHAPEFILE_MAX_STRING))
    return out


def _has_geometry(frame: pd.DataFrame) -> bool:
    if not isinstance(frame, gpd.GeoDataFrame):
        return False
    try:
        frame.geometry  # noqa: B018
    except AttributeError:
        return False
    return True


def export_geopackage(
    layers: dict[str, pd.DataFrame],
    output_path: str | Path,
) -> None:
    """Write named spatial layers and attribute-only tables to a single GeoPackage."""
    import pyogrio

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    written = 0
    for layer_name, frame in layers.items():
        if frame is None or frame.empty:
            continue
        if _has_geometry(frame):
            frame.to_file(output_path, layer=layer_name, driver="GPKG", mode="w" if written == 0 else "a")
        else:
            table = pd.DataFrame(frame).copy()
            for col in table.select_dtypes(include=["object"]).columns:
                table[col] = table[col].fillna("").astype(str)
            pyogrio.write_dataframe(
                table, output_path, layer=layer_name, driver="GPKG", append=written > 0,
            )
        written += 1


def export_registry_json(registry: CatchmentRegistry, output_path: str | Path) -> None:
    """Write catchment registry as JSON for cross-dataset linking."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = registry.to_full_payload()
    output_path.write_text(json.dumps(payload, indent=2, default=json_default), encoding="utf-8")


def export_shapefile_legacy(gdf: gpd.GeoDataFrame, filename: str | Path) -> None:
    """
    Write shapefile with short column names and wide DBF floats (Fiona schema).

    Compatible with TauDEM/MESH tools (DSContArea, LINKNO, etc.). HY_Features
    columns are dropped; they are published in ``geofabric.gpkg`` only.
    """
    export_gdf = prepare_shapefile_frame(gdf, filename)
    if not export_gdf.columns.is_unique:
        export_gdf = export_gdf.loc[:, ~export_gdf.columns.duplicated()].copy()

    float_cols = export_gdf.select_dtypes(include=["float64", "float32"]).columns
    for col in float_cols:
        if col != "Slope":
            series = export_gdf[col]
            if isinstance(series, pd.DataFrame):
                series = series.iloc[:, 0]
            export_gdf[col] = pd.to_numeric(series, errors="coerce").fillna(0.0).round(3)

    if "Slope" in export_gdf.columns:
        export_gdf["Slope"] = pd.to_numeric(export_gdf["Slope"], errors="coerce").fillna(0.0)

    int_cols = export_gdf.select_dtypes(include=["int64", "int32"]).columns
    for col in int_cols:
        export_gdf[col] = export_gdf[col].fillna(-1).astype(int)

    try:
        schema = gpd.io.file.infer_schema(export_gdf)
        for col in float_cols:
            if col not in schema["properties"] or col == "Slope":
                continue
            if col in WIDE_AREA_FLOAT_COLS:
                schema["properties"][col] = "float:24.1"
            else:
                schema["properties"][col] = "float:24.3"
        export_gdf.to_file(filename, driver="ESRI Shapefile", schema=schema, engine="fiona")
    except Exception as exc:
        print(
            f"Fiona export failed for {filename} ({exc}); writing with the default engine. "
            "Very large float values may lose precision in the narrow default DBF width."
        )
        export_gdf.to_file(filename, driver="ESRI Shapefile")
