"""Export HY_Features-aligned GeoPackage products and optional shapefiles."""

from __future__ import annotations

import json
import os
from pathlib import Path

import geopandas as gpd
import pandas as pd

from hy_features.models import CatchmentRegistry
from hy_features.schema import LEGACY_BASIN_ID

# ESRI Shapefile limits: 10-character column names; narrow default DBF numeric width.
SHAPEFILE_COLUMN_RENAMES: dict[str, str] = {
    "STATION_NUMBER": "STATION_NO",
    "STATION_NAME": "STATION_NM",
    "unit_area_km2": "area_km2",
    "lake_area_m2": "lake_area",
}

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


def rename_shapefile_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Rename columns to ≤10-character shapefile-safe names before export."""
    renames = {k: v for k, v in SHAPEFILE_COLUMN_RENAMES.items() if k in gdf.columns}
    if not renames:
        return gdf
    out = gdf.copy()
    for old, new in renames.items():
        if new in out.columns and new != old:
            out = out.drop(columns=[new])
    return out.rename(columns=renames)


def export_geopackage(
    layers: dict[str, gpd.GeoDataFrame],
    output_path: str | Path,
) -> None:
    """Write multiple named layers to a single GeoPackage."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    for i, (layer_name, gdf) in enumerate(layers.items()):
        if gdf is None or gdf.empty:
            continue
        mode = "w" if i == 0 else "a"
        gdf.to_file(output_path, layer=layer_name, driver="GPKG", mode=mode)


def export_registry_json(registry: CatchmentRegistry, output_path: str | Path) -> None:
    """Write catchment registry as JSON for cross-dataset linking."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = registry.to_full_payload()
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def export_shapefile_legacy(gdf: gpd.GeoDataFrame, filename: str | Path) -> None:
    """
    Write shapefile with short column names and wide DBF floats (Fiona schema).

    Compatible with TauDEM/MESH tools (DSContArea, LINKNO, etc.).
    """
    export_gdf = rename_shapefile_columns(gdf.copy())
    float_cols = export_gdf.select_dtypes(include=["float64", "float32"]).columns
    for col in float_cols:
        if col != "Slope":
            export_gdf[col] = pd.to_numeric(export_gdf[col], errors="coerce").fillna(0.0).round(3)

    if "Slope" in export_gdf.columns:
        export_gdf["Slope"] = pd.to_numeric(export_gdf["Slope"], errors="coerce").fillna(0.0)

    int_cols = export_gdf.select_dtypes(include=["int64", "int32"]).columns
    for col in int_cols:
        if col in (CATCHMENT_ID, FLOWPATH_ID, REALIZES_CATCHMENT, LOWER_CATCHMENT_ID):
            continue
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
        print(f"Fiona export failed for {filename}, attempting fallback. Error: {exc}")
        for col in list(float_cols) + list(int_cols):
            if col in export_gdf.columns:
                export_gdf[col] = export_gdf[col].astype(str)
        export_gdf.to_file(filename, driver="ESRI Shapefile")


# Avoid circular import at module level for string cols check
from hy_features.schema import CATCHMENT_ID, FLOWPATH_ID, LOWER_CATCHMENT_ID, REALIZES_CATCHMENT  # noqa: E402
