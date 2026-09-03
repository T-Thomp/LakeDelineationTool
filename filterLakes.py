"""
Filter HydroLAKES reservoirs to those intersecting the study basin.

Called by tau-dem-delineation-srun.slurm after TauDEM Pass 1 and before
rasterFlowpathEdit.py.

Uses Pass 1 watershed polygons to define the basin mask, streams HydroLAKES
via a WGS84 bounding box (memory-efficient), then applies area/reservoir
filters and an exact spatial intersect.

Inputs
------
  outputs/interim/taudem_d8/original-delineated-watersheds.shp  (Pass 1 basins)
  outputs/interim/taudem_d8/original-delineated-streams.shp     (Pass 1 streams, CRS)
  HydroLAKES_polys_v10.shp                                 (global lake polygons)

Outputs
-------
  outputs/prep/lakes.shp
  outputs/prep/lakes.gpkg  (optional, when HY_Features enabled)
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd

from hy_features.config import hy_features_enabled
from pipeline_paths import (
    PASS1_BASINS,
    PASS1_STREAMS,
    PREP_LAKES,
    PREP_LAKES_GPKG,
    ensure_output_dirs,
)

# ==============================================================================
# CONFIGURATION
# ==============================================================================
PATHS = {
    "basins": str(PASS1_BASINS),
    "streams": str(PASS1_STREAMS),
    "hydrolakes": "~/bow-bassano/delineation-product/hydrolakes/HydroLAKES_polys_v10.shp",
    "output_shp": str(PREP_LAKES),
    "output_gpkg": str(PREP_LAKES_GPKG),
}

MIN_AREA_SQKM = 5.0  # keep lakes larger than this (sq km), or any managed reservoir
ENABLE_HY_FEATURES = False  # overridden by HY_FEATURES_ENABLED env var if set


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
    """Stream-read HydroLAKES inside bbox."""
    print("Streaming HydroLAKES via bounding box...")
    lakes = gpd.read_file(Path(hydrolakes_path).expanduser(), bbox=bbox)
    print(f"Loaded {len(lakes)} candidate lakes within bounding box.")
    return lakes


def filter_lakes_by_attributes(lakes: gpd.GeoDataFrame, min_area_sqkm: float) -> gpd.GeoDataFrame:
    """Keep large natural lakes or any non-natural (managed) reservoir."""
    print("Applying attribute filter...")
    cond_size = lakes["Lake_area"] > min_area_sqkm
    cond_reservoir = lakes["Lake_type"] != 1
    return lakes[cond_size | cond_reservoir].copy()


def clip_lakes_to_basin(lakes: gpd.GeoDataFrame, basin_geom, target_crs) -> gpd.GeoDataFrame:
    """Exact intersect clip against the dissolved basin boundary."""
    lakes = lakes.to_crs(target_crs)
    print("Performing final spatial intersection with basin boundary...")
    return lakes[lakes.geometry.intersects(basin_geom)].copy()


def export_filtered_lakes(lakes: gpd.GeoDataFrame, paths: dict[str, str]) -> None:
    """Write shapefile and optional HY_Features GeoPackage sidecar."""
    output_shp = paths["output_shp"]
    Path(output_shp).parent.mkdir(parents=True, exist_ok=True)

    if hy_features_enabled(default=ENABLE_HY_FEATURES):
        from hy_features.enrich import enrich_waterbodies
        from hy_features.export import export_geopackage

        lakes = enrich_waterbodies(lakes)
        export_geopackage({"waterbody": lakes}, paths["output_gpkg"])

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
    filter_lakes_to_basin()


if __name__ == "__main__":
    main()
