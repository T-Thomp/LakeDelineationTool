"""
Find active HYDAT stream-gauge stations inside the study basin.

Called by tau-dem-delineation-srun.slurm after TauDEM Pass 1 and before
rasterFlowpathEdit.py (gauges inform lake outlet ranking).

Queries HYDAT for active stations with discharge (Q) data, spatially filters
to the dissolved Pass 1 basin boundary, and exports a point shapefile for
downstream scripts.

Inputs
------
  outputs/interim/taudem_d8/original-delineated-watersheds.shp  (Pass 1 basins)
  Hydat.sqlite3                                           (HYDAT database)

Outputs
-------
  outputs/prep/gauges.shp
  outputs/prep/gauges.gpkg  (optional, when HY_Features enabled)
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd

from hy_features.config import hy_features_enabled
from pipeline_paths import INPUT_HYDAT_DB, PASS1_BASINS, PREP_GAUGES, PREP_GAUGES_GPKG, ensure_output_dirs

# ==============================================================================
# CONFIGURATION
# ==============================================================================
PATHS = {
    "basins": str(PASS1_BASINS),
    "hydat_db": str(INPUT_HYDAT_DB),
    "output_shp": str(PREP_GAUGES),
    "output_gpkg": str(PREP_GAUGES_GPKG),
}

ENABLE_HY_FEATURES = False  # overridden by HY_FEATURES_ENABLED env var if set

HYDAT_ACTIVE_FLOW_QUERY = """
SELECT DISTINCT
    s.STATION_NUMBER,
    s.STATION_NAME,
    s.LATITUDE,
    s.LONGITUDE,
    s.PROV_TERR_STATE_LOC AS PROVINCE
FROM STATIONS s
JOIN STN_DATA_RANGE r ON s.STATION_NUMBER = r.STATION_NUMBER
WHERE s.HYD_STATUS = 'A'
  AND r.DATA_TYPE = 'Q'
"""


# ==============================================================================
# PROCESSING
# ==============================================================================
def load_basin_layers(basins_path: str) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """Load Pass 1 basins and return raw + dissolved boundary layers."""
    subbasins = gpd.read_file(basins_path)
    basin_dissolved = subbasins.dissolve()
    return subbasins, basin_dissolved


def query_active_flow_gauges(db_path: str) -> gpd.GeoDataFrame:
    """Load active discharge gauges from HYDAT as a WGS84 point layer."""
    conn = sqlite3.connect(db_path)
    try:
        df = pd.read_sql_query(HYDAT_ACTIVE_FLOW_QUERY, conn)
    finally:
        conn.close()

    print(f"Found {len(df)} active flow stations in HYDAT.")
    return gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df.LONGITUDE, df.LATITUDE),
        crs="EPSG:4326",
    )


def clip_gauges_to_basin(
    gauges: gpd.GeoDataFrame,
    basin_dissolved: gpd.GeoDataFrame,
    subbasins: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Keep gauge points inside the dissolved basin; reproject to basin CRS."""
    if gauges.crs != basin_dissolved.crs:
        gauges = gauges.to_crs(basin_dissolved.crs)

    # clip (not sjoin) so index_right / DN never attach to gauge points
    clipped = gpd.clip(gauges, basin_dissolved)
    return clipped.to_crs(subbasins.crs)


def export_gauges(gauges: gpd.GeoDataFrame, paths: dict[str, str]) -> None:
    """Write shapefile and optional HY_Features GeoPackage sidecar."""
    from hy_features.export import export_geopackage, export_shapefile_legacy, strip_point_join_artifacts

    output_shp = paths["output_shp"]
    Path(output_shp).parent.mkdir(parents=True, exist_ok=True)

    gauges = strip_point_join_artifacts(gauges)

    if hy_features_enabled(default=ENABLE_HY_FEATURES):
        from hy_features.enrich import enrich_hydrometric_features

        gauges = enrich_hydrometric_features(gauges)
        export_geopackage({"hydrometric_feature": gauges}, paths["output_gpkg"])

    export_shapefile_legacy(gauges, output_shp)
    print(f"Saved {len(gauges)} gauges to {output_shp}")


def get_gauges_in_basin(paths: dict[str, str] | None = None) -> gpd.GeoDataFrame:
    """Run the full gauge extraction workflow."""
    paths = paths or PATHS
    ensure_output_dirs()

    subbasins, basin_dissolved = load_basin_layers(paths["basins"])
    gauges = query_active_flow_gauges(paths["hydat_db"])
    gauges_in_basin = clip_gauges_to_basin(gauges, basin_dissolved, subbasins)
    export_gauges(gauges_in_basin, paths)

    print(f"{len(gauges_in_basin)} gauge(s) within the combined basin.")
    return gauges_in_basin


def main() -> None:
    get_gauges_in_basin()


if __name__ == "__main__":
    main()
