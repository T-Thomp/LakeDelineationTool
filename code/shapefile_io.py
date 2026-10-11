"""Write pipeline shapefiles with short column names and wide DBF floats."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

# ESRI Shapefile limits: 10-character column names, 254-character strings.
SHAPEFILE_MAX_NAME = 10
SHAPEFILE_MAX_STRING = 254

LAKE_AREA_M2 = "lake_area_m2"
LEGACY_LAKE_AREA = "lake_area"
LEGACY_BASIN_ID = "DN"

SHAPEFILE_COLUMN_RENAMES: dict[str, str] = {
    "STATION_NUMBER": "STATION_NO",
    "STATION_NAME": "STATION_NM",
    "unit_area_km2": "area_km2",
    LAKE_AREA_M2: LEGACY_LAKE_AREA,
}

# Columns that belong on a GeoPackage profile, not a TauDEM / MESH shapefile.
# GDAL would truncate several of these into the same 10-character name.
HY_ONLY_COLUMNS: frozenset[str] = frozenset({
    "hyf_type",
    "hyf_type_uri",
    "feature_id",
    "network_id",
    "catchment_id",
    "flowpath_id",
    "lower_catchment_id",
    "realizes_catchment",
    "is_lake_catchment",
    "waterbody_id",
    "waterbody_class",
    "station_code",
    "host_flowpath_id",
    "hydro_loc_type",
    "nexus_id",
    "outflow_nexus_id",
    "inflow_nexus_id",
    "upper_catchment_id",
    "contributing_catchment_id",
    "receiving_catchment_id",
    "realized_nexus_id",
    "reference_nexus_id",
    "linear_element_id",
    "distance_from_outlet_m",
    "distance_from_outlet_pct",
    "distance_description",
    "upstream_waterbody_id",
    "downstream_waterbody_id",
    "feature_name",
    "drainage_pattern",
})

# TauDEM cumulative/local areas in m² can exceed 1e10.
WIDE_AREA_FLOAT_COLS = frozenset({"DSContArea", "USContArea", "lake_area", "lake_area_m2"})


def strip_point_join_artifacts(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Drop spatial-join bookkeeping and basin ids from point layers."""
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
    """Drop GeoPackage-only columns before a shapefile write."""
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
    """Extra columns removed, short names applied, strings capped at the DBF limit."""
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


def _materialize_frame(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Copy column values into a new frame so later writes are not on a slice."""
    geom_name = gdf.geometry.name
    data: dict[str, object] = {}
    for col in gdf.columns:
        if col == geom_name:
            continue
        series = gdf[col]
        if isinstance(series, pd.DataFrame):
            series = series.iloc[:, 0]
        data[col] = series.to_numpy(copy=True)
    # Keep the original index and pass geometry as a bare array. A dict of arrays
    # gets a fresh 0..n-1 index, and a GeoSeries would then be aligned to it by
    # label, which nulls or swaps geometries when the input index is not 0..n-1.
    frame = pd.DataFrame(data, index=gdf.index)
    geometry = gpd.array.from_shapely(list(gdf.geometry), crs=gdf.crs)
    frame[geom_name] = geometry
    return gpd.GeoDataFrame(frame, geometry=geom_name, crs=gdf.crs)


def export_shapefile(gdf: gpd.GeoDataFrame, filename: str | Path) -> None:
    """Write a shapefile with short column names and wide DBF floats (Fiona schema)."""
    export_gdf = prepare_shapefile_frame(gdf, filename)
    if not export_gdf.columns.is_unique:
        export_gdf = export_gdf.loc[:, ~export_gdf.columns.duplicated()]
    # New columns from copied arrays. Wrapping the same frame keeps pandas'
    # "copy of a slice" flag and warns on the next column write.
    export_gdf = _materialize_frame(export_gdf)

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
