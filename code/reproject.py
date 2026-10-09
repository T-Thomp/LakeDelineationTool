"""
Reproject aggregated basins and streams to a user-specified CRS.

Defaults to the basinAggregation products
(outputs/final/basins_aggregated.shp and streams_aggregated.shp).
Writes copies next to those files unless --out-basins / --out-streams are set.

  python3 code/reproject.py --prj WGS84
  python3 code/reproject.py --prj EPSG:3978 --basins outputs/final/basins.shp
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import geopandas as gpd
from pyproj import CRS

from hy_features.export import export_shapefile
from pipeline_paths import FINAL_BASINS_AGG, FINAL_STREAMS_AGG, ensure_output_dirs


def parse_crs(text: str) -> CRS:
    """Accept WGS84, EPSG:4326, EPSG4326, or any pyproj CRS string."""
    raw = text.strip()
    if re.fullmatch(r"(?i)EPSG\d+", raw):
        raw = f"EPSG:{raw[4:]}"
    try:
        return CRS.from_user_input(raw)
    except Exception as exc:
        raise argparse.ArgumentTypeError(f"unrecognized CRS {text!r}: {exc}") from exc


def _crs_suffix(crs: CRS) -> str:
    """Short label for default output filenames: wgs84, epsg4326, epsg3978."""
    if crs.to_epsg() == 4326:
        return "wgs84"
    code = crs.to_epsg()
    if code is not None:
        return f"epsg{code}"
    return re.sub(r"[^A-Za-z0-9]+", "_", crs.to_string()).strip("_").lower()[:20]


def _default_output(input_path: Path, suffix: str) -> Path:
    return input_path.with_name(f"{input_path.stem}_{suffix}{input_path.suffix}")


def reproject_shapefile(src: Path, dst: Path, crs: CRS) -> None:
    if not src.is_file():
        raise FileNotFoundError(src)
    gdf = gpd.read_file(src)
    if gdf.crs is None:
        raise ValueError(f"{src} has no CRS")
    if gdf.crs != crs:
        gdf = gdf.to_crs(crs)
    dst.parent.mkdir(parents=True, exist_ok=True)
    export_shapefile(gdf, dst)
    print(f"Wrote {dst}  ({len(gdf)} features, {crs.to_string()})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reproject aggregated basins and streams.",
    )
    parser.add_argument(
        "--prj",
        type=parse_crs,
        required=True,
        help="Target CRS: WGS84, EPSG:4326, EPSG4326, or a PROJ string.",
    )
    parser.add_argument(
        "--basins",
        type=Path,
        default=FINAL_BASINS_AGG,
        help="Input basins shapefile (default: basinAggregation basins_aggregated.shp).",
    )
    parser.add_argument(
        "--streams",
        type=Path,
        default=FINAL_STREAMS_AGG,
        help="Input streams shapefile (default: basinAggregation streams_aggregated.shp).",
    )
    parser.add_argument(
        "--out-basins",
        type=Path,
        default=None,
        help="Output basins shapefile. Default: <input_stem>_<crs>.shp next to the input.",
    )
    parser.add_argument(
        "--out-streams",
        type=Path,
        default=None,
        help="Output streams shapefile. Default: <input_stem>_<crs>.shp next to the input.",
    )
    args = parser.parse_args()

    ensure_output_dirs()
    label = _crs_suffix(args.prj)
    out_basins = args.out_basins or _default_output(args.basins, label)
    out_streams = args.out_streams or _default_output(args.streams, label)

    print(f"Target CRS: {args.prj.to_string()}")
    try:
        reproject_shapefile(args.basins, out_basins, args.prj)
        reproject_shapefile(args.streams, out_streams, args.prj)
    except FileNotFoundError as exc:
        print(f"ERROR: missing input {exc}", file=sys.stderr)
        print("  Run basinAggregation.py first, or pass --basins / --streams.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
