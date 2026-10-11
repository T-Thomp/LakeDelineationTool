"""Export HY_Features-aligned GeoPackage products.

Shapefile writing lives in ``shapefile_io``. The names re-exported below let
field remapping drop GeoPackage-only columns before a shapefile write.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import pandas as pd

from hy_features.json_export import json_default
from hy_features.models import CatchmentRegistry
from shapefile_io import (
    HY_ONLY_COLUMNS,
    SHAPEFILE_COLUMN_RENAMES,
    SHAPEFILE_MAX_NAME,
    SHAPEFILE_MAX_STRING,
    WIDE_AREA_FLOAT_COLS,
    check_shapefile_columns,
    export_shapefile,
    prepare_shapefile_frame,
    rename_shapefile_columns,
    strip_hy_columns,
    strip_point_join_artifacts,
)

__all__ = [
    "HY_ONLY_COLUMNS",
    "SHAPEFILE_COLUMN_RENAMES",
    "SHAPEFILE_MAX_NAME",
    "SHAPEFILE_MAX_STRING",
    "WIDE_AREA_FLOAT_COLS",
    "check_shapefile_columns",
    "export_geopackage",
    "export_registry_json",
    "export_shapefile",
    "prepare_shapefile_frame",
    "rename_shapefile_columns",
    "strip_hy_columns",
    "strip_point_join_artifacts",
]


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
