"""
Filter HydroLAKES reservoirs to those intersecting the study basin.

Called by Delineation-Workflow.slurm after TauDEM Pass 1 and before
conditionLakes.py.

Uses Pass 1 watershed polygons to define the basin mask, reads the lake
file from study_settings.py (HydroLAKES by default) inside the basin's
bounding box, then applies the size and type filters from that file.

Inputs
------
  outputs/interim/taudem_d8/original-delineated-watersheds.shp  (Pass 1 basins)
  outputs/interim/taudem_d8/original-delineated-streams.shp     (Pass 1 streams, CRS)
  INPUT_LAKES (study_settings.py)                          (lake polygons)

Outputs
-------
  outputs/prep/lakes.shp
"""

from __future__ import annotations

import argparse
from pathlib import Path

import study_cli

if __name__ == "__main__":
    study_cli.apply_settings_arg()

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from pipeline_paths import (
    INPUT_LAKES,
    LAKE_AREA_COLUMN,
    LAKE_ID_COLUMN,
    LAKE_KEEP_TYPES,
    LAKE_MIN_AREA_SQKM,
    LAKE_TYPE_COLUMN,
    LAKE_TYPES_MUST_MATCH,
    PASS1_BASINS,
    PASS1_STREAMS,
    PREP_LAKES,
    ensure_output_dirs,
)

# ==============================================================================
# CONFIGURATION
# ==============================================================================
PATHS = {
    "basins": str(PASS1_BASINS),
    "streams": str(PASS1_STREAMS),
    "hydrolakes": str(INPUT_LAKES),
    "output_shp": str(PREP_LAKES),
}

MIN_AREA_SQKM = LAKE_MIN_AREA_SQKM


# ==============================================================================
# PROCESSING
# ==============================================================================
def load_basin_mask(basins_path: str, streams_path: str) -> tuple[gpd.GeoDataFrame, object, tuple[float, ...], object]:
    """Load Pass 1 basins/streams and return dissolved geometry plus WGS84 bbox."""
    print("Loading subbasins and streams...")
    subbasins = gpd.read_file(basins_path)
    streams = gpd.read_file(streams_path)

    print("Dissolving subbasins to create a unified basin boundary...")
    basin_dissolved = subbasins.dissolve()
    basin_geom = basin_dissolved.geometry.iloc[0]

    print("Calculating spatial bounding box in WGS84...")
    basin_wgs84 = basin_dissolved.to_crs("EPSG:4326")
    bbox = tuple(basin_wgs84.total_bounds)
    print(f"Bounding box coordinates: {bbox}")

    return subbasins, basin_geom, bbox, streams.crs


def load_hydrolakes_in_bbox(hydrolakes_path: str, bbox: tuple[float, ...]) -> gpd.GeoDataFrame:
    """Read the study lake file inside the basin bounding box.

    ``bbox`` is WGS84 (west, south, east, north). It is reprojected into the
    lake file CRS before the read, so a projected shapefile still clips.
    """
    path = Path(hydrolakes_path).expanduser()
    print(f"Reading lakes from {path}...")
    sample = gpd.read_file(path, rows=1)
    read_bbox = bbox
    if sample.crs is not None:
        window = gpd.GeoDataFrame(geometry=[box(*bbox)], crs="EPSG:4326")
        if sample.crs != window.crs:
            window = window.to_crs(sample.crs)
        read_bbox = tuple(window.total_bounds)
    lakes = gpd.read_file(path, bbox=read_bbox)
    print(f"Loaded {len(lakes)} candidate lakes within bounding box.")
    return lakes


def _values_match(series: pd.Series, keep: tuple) -> pd.Series:
    """True where a cell equals one of ``keep`` (numbers or text)."""
    numeric = pd.to_numeric(series, errors="coerce")
    text = series.astype("string").str.strip()
    mask = pd.Series(False, index=series.index)
    for value in keep:
        as_text = str(value).strip()
        try:
            as_number = float(value)
        except (TypeError, ValueError):
            as_number = None
        if as_number is not None:
            mask = mask | (numeric == as_number)
        mask = mask | (text == as_text)
    return mask.fillna(False)


def _require_lake_columns(lakes: gpd.GeoDataFrame) -> None:
    needed = [LAKE_ID_COLUMN, LAKE_AREA_COLUMN]
    if LAKE_TYPE_COLUMN:
        needed.append(LAKE_TYPE_COLUMN)
    missing = [name for name in needed if name not in lakes.columns]
    if missing:
        found = [column for column in lakes.columns if column != lakes.geometry.name]
        raise ValueError(
            "Lake file is missing "
            + ", ".join(missing)
            + f". Set LAKE_COLUMNS in study_settings.py. Found columns: {found}"
        )
    too_long = [name for name in needed if len(str(name)) > 10]
    if too_long:
        raise ValueError(
            "Lake column name(s) "
            + ", ".join(repr(name) for name in too_long)
            + " are longer than 10 characters. The prep lake shapefile can only "
            "store 10-character field names. Rename the field and update LAKE_COLUMNS."
        )


def filter_lakes_by_attributes(
    lakes: gpd.GeoDataFrame,
    min_area_sqkm: float,
    area_column: str = LAKE_AREA_COLUMN,
    type_column: str | None = LAKE_TYPE_COLUMN,
    keep_types: tuple | None = LAKE_KEEP_TYPES,
    types_must_match: bool = LAKE_TYPES_MUST_MATCH,
) -> gpd.GeoDataFrame:
    """Apply the study_settings size and type rules.

    Default (types_must_match False): keep a lake when its area exceeds
    ``min_area_sqkm`` or its type is one of ``keep_types``. That is the
    HydroLAKES rule for types 2 and 3 at any size, and other types only
    above the size limit.

    types_must_match True: keep only listed types that also exceed the size limit.
    """
    _require_lake_columns(lakes)
    print("Applying attribute filter...")
    area = pd.to_numeric(lakes[area_column], errors="coerce")
    large = area > min_area_sqkm
    if type_column is None or keep_types is None:
        chosen = large
        print(f"  keep {area_column} > {min_area_sqkm:g} km²")
    elif types_must_match:
        chosen = large & _values_match(lakes[type_column], keep_types)
        print(
            f"  keep {area_column} > {min_area_sqkm:g} km²"
            f" and {type_column} in {keep_types}"
        )
    else:
        chosen = large | _values_match(lakes[type_column], keep_types)
        print(
            f"  keep {area_column} > {min_area_sqkm:g} km²"
            f", or {type_column} in {keep_types} at any size"
        )
    return lakes[chosen].copy()


def clip_lakes_to_basin(lakes: gpd.GeoDataFrame, basin_geom, target_crs) -> gpd.GeoDataFrame:
    """Exact intersect clip against the dissolved basin boundary."""
    lakes = lakes.to_crs(target_crs)
    print("Performing final spatial intersection with basin boundary...")
    return lakes[lakes.geometry.intersects(basin_geom)].copy()


def export_filtered_lakes(lakes: gpd.GeoDataFrame, paths: dict[str, str]) -> None:
    """Write the filtered lake shapefile."""
    output_shp = paths["output_shp"]
    Path(output_shp).parent.mkdir(parents=True, exist_ok=True)
    print(f"Saving {len(lakes)} filtered lakes to {output_shp}...")
    lakes.to_file(output_shp)


def filter_lakes_to_basin(
    paths: dict[str, str] | None = None,
    min_area_sqkm: float | None = None,
) -> gpd.GeoDataFrame:
    """Run the full HydroLAKES filter workflow."""
    paths = paths or PATHS
    min_area_sqkm = MIN_AREA_SQKM if min_area_sqkm is None else min_area_sqkm
    ensure_output_dirs()

    _, basin_geom, bbox, stream_crs = load_basin_mask(paths["basins"], paths["streams"])
    lakes = load_hydrolakes_in_bbox(paths["hydrolakes"], bbox)
    lakes = filter_lakes_by_attributes(lakes, min_area_sqkm)
    lakes_in_basin = clip_lakes_to_basin(lakes, basin_geom, stream_crs)
    export_filtered_lakes(lakes_in_basin, paths)
    print("Process complete!")
    return lakes_in_basin


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clip the study lake file to the Pass 1 basin.",
    )
    study_cli.add_settings_argument(parser)
    parser.add_argument("--basins", type=Path, help="Pass 1 watershed polygons.")
    parser.add_argument("--streams", type=Path, help="Pass 1 streams, used for the basin CRS.")
    parser.add_argument("--lakes", type=Path, help="Lake polygons. Default: INPUT_LAKES in --settings.")
    parser.add_argument("--output", type=Path, help="Filtered lake shapefile.")
    args = parser.parse_args()

    paths = dict(PATHS)
    if args.basins is not None:
        paths["basins"] = str(args.basins)
    if args.streams is not None:
        paths["streams"] = str(args.streams)
    if args.lakes is not None:
        paths["hydrolakes"] = str(args.lakes)
    if args.output is not None:
        paths["output_shp"] = str(args.output)
    print(f"basins: {paths['basins']}")
    print(f"streams: {paths['streams']}")
    print(f"lakes:  {paths['hydrolakes']}")
    print(f"output: {paths['output_shp']}")
    filter_lakes_to_basin(paths)


if __name__ == "__main__":
    main()
