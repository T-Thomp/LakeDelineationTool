"""
Find stream-gauge stations inside the study basin.

Called by Delineation-Workflow.slurm after TauDEM Pass 1 and before
conditionLakes.py (gauges inform lake outlet ranking).

INPUT_GAUGES is a list. Each item names a file and its columns. A SQLite
file is read as WSC HYDAT (active stations with discharge). A CSV uses
latitude and longitude. A shapefile or other point file uses its geometry.

Stations are clipped to the dissolved Pass 1 basin and written for downstream
scripts. The station id is stored as STATION_NUMBER (STATION_NO in the
shapefile) and the name as STATION_NAME (STATION_NM).

Inputs
------
  outputs/interim/taudem_d8/original-delineated-watersheds.shp  (Pass 1 basins)
  INPUT_GAUGES                                            (study_settings.py)

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
    INPUT_GAUGES,
    PASS1_BASINS,
    PREP_GAUGES,
    ensure_output_dirs,
    gauge_entry,
)
from utilities import export_shapefile, strip_point_join_artifacts

# ==============================================================================
# CONFIGURATION
# ==============================================================================
PATHS = {
    "basins": str(PASS1_BASINS),
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
            "Set columns for this file in INPUT_GAUGES. "
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


def _load_one_source(entry: dict) -> gpd.GeoDataFrame:
    print(f"{entry['name']} ({entry['kind']}): {entry['path']}")
    if entry["kind"] == "hydat":
        return query_active_flow_gauges(str(entry["path"]))
    return load_gauge_file(str(entry["path"]), entry["columns"])


def _combine_gauge_frames(frames: list[gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """Stack gauge layers. Several sources keep the station id and name."""
    if len(frames) == 1:
        return frames[0]
    pieces: list[gpd.GeoDataFrame] = []
    for frame in frames:
        keep = [column for column in ("STATION_NUMBER", "STATION_NAME") if column in frame.columns]
        piece = frame[keep + [frame.geometry.name]]
        if piece.geometry.name != "geometry":
            piece = piece.rename_geometry("geometry")
        pieces.append(piece)
    combined = gpd.GeoDataFrame(
        pd.concat(pieces, ignore_index=True),
        geometry="geometry",
        crs=pieces[0].crs,
    )
    return combined


def get_gauges_in_basin(
    paths: dict[str, str] | None = None,
    entries: list[dict] | None = None,
) -> gpd.GeoDataFrame:
    """Run the full gauge extraction workflow."""
    paths = paths or PATHS
    entries = list(INPUT_GAUGES if entries is None else entries)
    if not entries:
        raise ValueError("No gauge sources configured")
    ensure_output_dirs()

    subbasins, basin_dissolved = load_basin_layers(paths["basins"])
    clipped: list[gpd.GeoDataFrame] = []
    for entry in entries:
        gauges = _load_one_source(entry)
        clipped.append(clip_gauges_to_basin(gauges, basin_dissolved, subbasins))
    gauges_in_basin = _combine_gauge_frames(clipped)
    export_gauges(gauges_in_basin, paths)

    print(f"{len(gauges_in_basin)} gauge(s) within the combined basin.")
    return gauges_in_basin


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clip stream gauges to the Pass 1 basin.",
    )
    study_cli.add_settings_argument(parser)
    parser.add_argument("--basins", type=Path, help="Pass 1 watershed polygons.")
    parser.add_argument(
        "--gauges",
        type=Path,
        action="append",
        help=(
            "Gauge file. Repeat for another file. The suffix picks the reader: "
            "SQLite is HYDAT, CSV uses latitude and longitude, and other files are points. "
            "CSV and point files passed here use STATION_NUMBER, STATION_NAME, LATITUDE, "
            "and LONGITUDE. Replaces INPUT_GAUGES for this run."
        ),
    )
    parser.add_argument("--output", type=Path, help="Gauge point shapefile.")
    args = parser.parse_args()

    paths = dict(PATHS)
    if args.basins is not None:
        paths["basins"] = str(args.basins)
    if args.output is not None:
        paths["output_shp"] = str(args.output)
    entries = [gauge_entry(path) for path in args.gauges] if args.gauges else None
    print(f"basins: {paths['basins']}")
    print(f"output: {paths['output_shp']}")
    get_gauges_in_basin(paths, entries)


if __name__ == "__main__":
    main()
