"""
Find stream-gauge stations inside the study basin.

Called by Delineation-Workflow.slurm after TauDEM Pass 1 and before
conditionLakes.py (gauges inform lake outlet ranking).

Default source is the WSC HYDAT SQLite database: active stations with
discharge (Q) data. Set GAUGE_SOURCE = "file" in study_settings.py to read a
CSV or a point shapefile instead, and map its columns with GAUGE_COLUMNS.

Stations are clipped to the dissolved Pass 1 basin and written for downstream
scripts. The station id is stored as STATION_NUMBER (STATION_NO in the
shapefile) and the name as STATION_NAME (STATION_NM).

Inputs
------
  outputs/interim/taudem_d8/original-delineated-watersheds.shp  (Pass 1 basins)
  INPUT_HYDAT_DB or INPUT_GAUGES                          (study_settings.py)

Outputs
-------
  outputs/prep/gauges.shp
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import study_cli

if __name__ == "__main__":
    study_cli.apply_settings_arg()

import geopandas as gpd
import pandas as pd

from pipeline_paths import (
    GAUGE_COLUMNS,
    GAUGE_SOURCE,
    INPUT_GAUGES,
    INPUT_HYDAT_DB,
    PASS1_BASINS,
    PREP_GAUGES,
    ensure_output_dirs,
)
from utilities import export_shapefile, strip_point_join_artifacts

# ==============================================================================
# CONFIGURATION
# ==============================================================================
PATHS = {
    "basins": str(PASS1_BASINS),
    "hydat_db": str(INPUT_HYDAT_DB) if INPUT_HYDAT_DB else "",
    "gauges": str(INPUT_GAUGES) if INPUT_GAUGES else "",
    "output_shp": str(PREP_GAUGES),
}

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


def _require_columns(frame: pd.DataFrame, columns: list[str], source: Path) -> None:
    missing = [name for name in columns if not name or name not in frame.columns]
    if missing:
        raise ValueError(
            f"{source} is missing column(s): {', '.join(missing)}. "
            "Set GAUGE_COLUMNS in study_settings.py to the names in this file. "
            f"Found columns: {list(frame.columns)}"
        )


def _with_station_fields(gauges: gpd.GeoDataFrame, id_column: str, name_column: str | None) -> gpd.GeoDataFrame:
    """Copy the mapped id and name onto the columns later scripts look for."""
    gauges = gauges.copy()
    gauges["STATION_NUMBER"] = gauges[id_column].astype(str)
    if name_column and name_column in gauges.columns:
        gauges["STATION_NAME"] = gauges[name_column].astype(str)
    elif "STATION_NAME" not in gauges.columns:
        gauges["STATION_NAME"] = gauges["STATION_NUMBER"]
    return gauges


def load_gauge_file(path: str, columns: dict[str, str]) -> gpd.GeoDataFrame:
    """Load a CSV (WGS84 lat/lon) or a point file and map its columns."""
    source = Path(path)
    id_column = columns.get("id") or ""
    name_column = columns.get("name") or ""
    lat_column = columns.get("latitude") or ""
    lon_column = columns.get("longitude") or ""
    print(f"Reading gauges from {source}...")

    if source.suffix.lower() == ".csv":
        table = pd.read_csv(source)
        table.columns = [str(name).strip() for name in table.columns]
        _require_columns(table, [id_column, lat_column, lon_column], source)
        gauges = gpd.GeoDataFrame(
            table,
            geometry=gpd.points_from_xy(table[lon_column], table[lat_column]),
            crs="EPSG:4326",
        )
    else:
        gauges = gpd.read_file(source)
        gauges.columns = [str(name).strip() for name in gauges.columns]
        _require_columns(gauges, [id_column], source)
        has_points = gauges.geometry.notna().any() and not gauges.geometry.is_empty.all()
        if not has_points:
            _require_columns(gauges, [lat_column, lon_column], source)
            gauges = gauges.set_geometry(gpd.points_from_xy(gauges[lon_column], gauges[lat_column]))
            gauges = gauges.set_crs("EPSG:4326")

    gauges = _with_station_fields(gauges, id_column, name_column)
    print(f"Loaded {len(gauges)} gauges from {source.name}.")
    return gauges


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
    """Write the gauge point shapefile."""
    output_shp = paths["output_shp"]
    Path(output_shp).parent.mkdir(parents=True, exist_ok=True)
    export_shapefile(strip_point_join_artifacts(gauges), output_shp)
    print(f"Saved {len(gauges)} gauges to {output_shp}")


def get_gauges_in_basin(paths: dict[str, str] | None = None) -> gpd.GeoDataFrame:
    """Run the full gauge extraction workflow."""
    paths = paths or PATHS
    ensure_output_dirs()

    subbasins, basin_dissolved = load_basin_layers(paths["basins"])
    if GAUGE_SOURCE == "hydat":
        gauges = query_active_flow_gauges(paths["hydat_db"])
    else:
        gauges = load_gauge_file(paths["gauges"], GAUGE_COLUMNS)
    gauges_in_basin = clip_gauges_to_basin(gauges, basin_dissolved, subbasins)
    export_gauges(gauges_in_basin, paths)

    print(f"{len(gauges_in_basin)} gauge(s) within the combined basin.")
    return gauges_in_basin


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clip stream gauges to the Pass 1 basin.",
    )
    study_cli.add_settings_argument(parser)
    parser.add_argument("--basins", type=Path, help="Pass 1 watershed polygons.")
    parser.add_argument("--hydat", type=Path, help="WSC HYDAT SQLite database. Used when GAUGE_SOURCE is hydat.")
    parser.add_argument("--gauges", type=Path, help="Gauge CSV or point file. Used when GAUGE_SOURCE is file.")
    parser.add_argument("--output", type=Path, help="Gauge point shapefile.")
    args = parser.parse_args()

    paths = dict(PATHS)
    if args.basins is not None:
        paths["basins"] = str(args.basins)
    if args.hydat is not None:
        paths["hydat_db"] = str(args.hydat)
    if args.gauges is not None:
        paths["gauges"] = str(args.gauges)
    if args.output is not None:
        paths["output_shp"] = str(args.output)
    print(f"basins: {paths['basins']}")
    if GAUGE_SOURCE == "hydat":
        print(f"hydat:  {paths['hydat_db']}")
    else:
        print(f"gauges: {paths['gauges']}")
    print(f"output: {paths['output_shp']}")
    get_gauges_in_basin(paths)


if __name__ == "__main__":
    main()
