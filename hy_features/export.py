"""Export HY_Features-aligned GeoPackage products and optional shapefiles."""

from __future__ import annotations

import json
import os
from pathlib import Path

import geopandas as gpd
import pandas as pd

from hy_features.models import CatchmentRegistry


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
    Write shapefile with numeric formatting compatible with TauDEM/MESH tools.

    Mirrors combiningBasins.export_shapefile behavior for float/int columns.
    """
    export_gdf = gdf.copy()
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
            if col in schema["properties"] and col != "Slope":
                width = "float:24.1" if col in ("lake_area", "lake_area_m2") else "float:24.3"
                schema["properties"][col] = width
        export_gdf.to_file(filename, driver="ESRI Shapefile", schema=schema, engine="fiona")
    except Exception as exc:
        print(f"Fiona export failed for {filename}, attempting fallback. Error: {exc}")
        for col in list(float_cols) + list(int_cols):
            if col in export_gdf.columns:
                export_gdf[col] = export_gdf[col].astype(str)
        export_gdf.to_file(filename, driver="ESRI Shapefile")


# Avoid circular import at module level for string cols check
from hy_features.schema import CATCHMENT_ID, FLOWPATH_ID, LOWER_CATCHMENT_ID, REALIZES_CATCHMENT  # noqa: E402
