"""
Build an OGC HY_Features GeoPackage from finished pipeline shapefiles.

This does not run inside the delineation workflow. Run it after
``cleanGeofabric.py`` (and, for the aggregated product, after
``basinAggregation.py``):

  python code/export_hy_features.py
  python code/export_hy_features.py --aggregated
  python code/export_hy_features.py --aggregated --skip-fine
"""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd

from hy_features.aggregate import export_aggregated_geofabric, membership_from_merge_log
from hy_features.assemble import assemble_full_geofabric, export_full_geofabric
from pipeline_paths import (
    FINAL_BASINS,
    FINAL_BASINS_AGG,
    FINAL_POUR_POINTS,
    FINAL_STREAMS,
    FINAL_STREAMS_AGG,
    PREP_GAUGES,
    PREP_LAKES,
    WORKING,
    WORKING_CATCHMENT_REGISTRY,
    WORKING_GEOFABRIC_AGG_GPKG,
    WORKING_GEOFABRIC_GPKG,
    WORKING_HYDRO_NETWORK_JSON,
    ensure_output_dirs,
)
from shapefile_io import strip_point_join_artifacts

MERGE_LOG = WORKING / "aggregation_merge_log.csv"
DEFAULT_OUTLET_SENTINEL = -9999


def _read_optional(path: Path, target_crs) -> gpd.GeoDataFrame | None:
    if not path.is_file():
        print(f"No layer at {path}; continuing without it.")
        return None
    try:
        frame = gpd.read_file(path)
    except Exception as exc:
        print(f"Could not read {path}: {exc}")
        return None
    if frame.empty:
        return None
    if target_crs is not None and frame.crs is not None and frame.crs != target_crs:
        frame = frame.to_crs(target_crs)
    return frame


def export_fine_geofabric(
    basins_path: Path,
    streams_path: Path,
    *,
    gauges_path: Path,
    lakes_path: Path,
    pour_points_path: Path,
    gpkg_path: Path,
    registry_path: Path,
    network_json_path: Path,
    outlet_sentinel: int,
) -> None:
    """Assemble geofabric.gpkg from the clean basins, streams, and optional points."""
    basins = gpd.read_file(basins_path)
    streams = gpd.read_file(streams_path)

    gauges = _read_optional(gauges_path, basins.crs)
    if gauges is not None:
        gauges = strip_point_join_artifacts(gauges)
    waterbodies = _read_optional(lakes_path, basins.crs)
    hydro_locations = _read_optional(pour_points_path, basins.crs)
    if hydro_locations is not None:
        hydro_locations = strip_point_join_artifacts(hydro_locations)

    assembled = assemble_full_geofabric(
        basins,
        streams,
        gauges=gauges,
        waterbodies=waterbodies,
        hydro_locations=hydro_locations,
        outlet_sentinel=outlet_sentinel,
    )
    export_full_geofabric(
        assembled,
        gpkg_path=gpkg_path,
        registry_path=registry_path,
        metadata_path=network_json_path,
    )


def export_aggregated_product(
    agg_basins_path: Path,
    agg_streams_path: Path,
    fine_basins_path: Path,
    merge_log_path: Path,
    gpkg_path: Path,
    *,
    outlet_sentinel: int,
) -> None:
    """Assemble geofabric_aggregated.gpkg and link it to the fine catchments."""
    agg_basins = gpd.read_file(agg_basins_path)
    agg_streams = gpd.read_file(agg_streams_path)
    fine_basins = gpd.read_file(fine_basins_path)
    membership = membership_from_merge_log(fine_basins, merge_log_path)
    export_aggregated_geofabric(
        agg_basins,
        agg_streams,
        membership,
        gpkg_path,
        outlet_sentinel=outlet_sentinel,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post-process finished shapefiles into an HY_Features GeoPackage.",
    )
    parser.add_argument("--basins", type=Path, default=FINAL_BASINS)
    parser.add_argument("--streams", type=Path, default=FINAL_STREAMS)
    parser.add_argument("--gauges", type=Path, default=PREP_GAUGES)
    parser.add_argument("--lakes", type=Path, default=PREP_LAKES)
    parser.add_argument("--pour-points", type=Path, default=FINAL_POUR_POINTS)
    parser.add_argument("--gpkg", type=Path, default=WORKING_GEOFABRIC_GPKG)
    parser.add_argument("--registry", type=Path, default=WORKING_CATCHMENT_REGISTRY)
    parser.add_argument("--network-json", type=Path, default=WORKING_HYDRO_NETWORK_JSON)
    parser.add_argument("--aggregated", action="store_true", help="Also write the aggregated geofabric.")
    parser.add_argument("--skip-fine", action="store_true", help="Skip geofabric.gpkg; use with --aggregated.")
    parser.add_argument("--agg-basins", type=Path, default=FINAL_BASINS_AGG)
    parser.add_argument("--agg-streams", type=Path, default=FINAL_STREAMS_AGG)
    parser.add_argument("--fine-basins", type=Path, default=FINAL_BASINS)
    parser.add_argument("--merge-log", type=Path, default=MERGE_LOG)
    parser.add_argument("--agg-gpkg", type=Path, default=WORKING_GEOFABRIC_AGG_GPKG)
    parser.add_argument("--outlet-sentinel", type=int, default=DEFAULT_OUTLET_SENTINEL)
    args = parser.parse_args()

    if args.skip_fine and not args.aggregated:
        parser.error("--skip-fine requires --aggregated")

    ensure_output_dirs()
    if not args.skip_fine:
        export_fine_geofabric(
            args.basins,
            args.streams,
            gauges_path=args.gauges,
            lakes_path=args.lakes,
            pour_points_path=args.pour_points,
            gpkg_path=args.gpkg,
            registry_path=args.registry,
            network_json_path=args.network_json,
            outlet_sentinel=args.outlet_sentinel,
        )
    if args.aggregated:
        export_aggregated_product(
            args.agg_basins,
            args.agg_streams,
            args.fine_basins,
            args.merge_log,
            args.agg_gpkg,
            outlet_sentinel=args.outlet_sentinel,
        )


if __name__ == "__main__":
    main()
