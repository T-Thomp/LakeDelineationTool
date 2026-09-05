#!/usr/bin/env python3
"""
Temporary diagnostic for lake override windows and FDR burn paths.

Writes a shapefile you can open in QGIS (window bbox, lake, override, outlet,
breakout carve path, and all FDR edit flow segments in the window).

Delete this file when you are done debugging.

Usage (from study root):
  export LAKE_DELINEATION_ROOT="$PWD"
  python3 diagnose_lake_override.py --lake-id 98207
  python3 diagnose_lake_override.py --all-overrides
  python3 diagnose_lake_override.py --lake-id 98207 --output outputs/prep/lake_98207_debug.shp
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
from osgeo import gdal
from scipy.ndimage import binary_erosion
from shapely.geometry import LineString, Point, Polygon

STUDY_ROOT = Path(os.environ.get("LAKE_DELINEATION_ROOT", ".")).resolve()
OVERRIDES_CSV = STUDY_ROOT / "outlet_overrides.csv"
DEFAULT_OUTPUT = STUDY_ROOT / "outputs/prep/lake_override_diagnostic.shp"

CODE_DIR = Path(__file__).resolve().parent / "code"
sys.path.insert(0, str(CODE_DIR) if CODE_DIR.is_dir() else str(Path(__file__).resolve().parent))

from outlet_overrides import load_overrides  # noqa: E402
from pipeline_paths import (  # noqa: E402
    PASS1_STREAMS,
    PASS1_WATERSHEDS_TIF,
    PREP_GAUGES,
    PREP_LAKES,
    TAUDEM_D8,
    ensure_output_dirs,
)
from rasterFlowpathEdit import (  # noqa: E402
    D8_OFFSETS,
    _lookup_tables_from_streams_gdf,
    build_lake_through_stream_wsnos,
    filter_upstream_duplicates,
    find_stream_exit_candidates,
    get_d8_direction,
    load_gauges,
    pixel_to_point,
    raster_window_from_bounds,
    rasterize_polygon_mask,
    route_centerline_to_target,
    select_outlet_for_lake,
)

GAUGE_RADIUS_M = 750


def _window_polygon(gt, xoff: int, yoff: int, xsize: int, ysize: int) -> Polygon:
    """Raster window extent as a map polygon (cell edges)."""
    x_ul = gt[0] + xoff * gt[1]
    y_ul = gt[3] + yoff * gt[5]
    x_ur = gt[0] + (xoff + xsize) * gt[1] + yoff * gt[2]
    y_ur = gt[3] + (xoff + xsize) * gt[4] + yoff * gt[5]
    x_lr = gt[0] + (xoff + xsize) * gt[1] + (yoff + ysize) * gt[2]
    y_lr = gt[3] + (xoff + xsize) * gt[4] + (yoff + ysize) * gt[5]
    x_ll = gt[0] + xoff * gt[1] + (yoff + ysize) * gt[2]
    y_ll = gt[3] + yoff * gt[4] + (yoff + ysize) * gt[5]
    return Polygon([(x_ul, y_ul), (x_ur, y_ur), (x_lr, y_lr), (x_ll, y_ll), (x_ul, y_ul)])


def _breakout_line(breakout_path, gt, xoff, yoff) -> LineString | None:
    """Convert breakout (current, parent) pairs to a map LineString."""
    if not breakout_path:
        return None
    nodes: list[tuple[int, int]] = [breakout_path[0][0]]
    for _current, parent in breakout_path:
        nodes.append(parent)
    coords = [pixel_to_point(gt, xoff, yoff, r, c) for r, c in nodes]
    if len(coords) < 2:
        return None
    return LineString([(p.x, p.y) for p in coords])


def _fdr_edit_lines(edit_mask, fdr_win, gt, xoff, yoff) -> list[LineString]:
    """One short segment per edited cell showing assigned D8 flow direction."""
    lines: list[LineString] = []
    for r, c in np.argwhere(edit_mask):
        d8 = int(fdr_win[r, c])
        offset = D8_OFFSETS.get(d8)
        if not offset:
            continue
        dr, dc = offset
        p0 = pixel_to_point(gt, xoff, yoff, int(r), int(c))
        p1 = pixel_to_point(gt, xoff, yoff, int(r + dr), int(c + dc))
        lines.append(LineString([(p0.x, p0.y), (p1.x, p1.y)]))
    return lines


def _process_lake_for_export(
    lake_id: str,
    geometry,
    *,
    fdr_band,
    src_band,
    acc_band,
    w_band,
    gt,
    inv_gt,
    raster_size,
    raster_proj,
    cell_size,
    lookup_tables,
    gauges,
    overrides_gdf,
    lake_through_wsnos,
) -> list[dict]:
    """Run one lake through outlet selection + routing; return shapefile feature dicts."""
    wsno_to_link, link_to_dout, link_to_accum, link_to_downstream = lookup_tables
    window = raster_window_from_bounds(geometry.bounds, inv_gt, raster_size)
    if window is None:
        return [{
            "lake_id": lake_id,
            "feat_type": "error",
            "sel_type": "outside_raster",
            "carve_ok": 0,
            "geometry": geometry.centroid,
        }]

    xoff, yoff, xsize, ysize = window
    lake_mask = rasterize_polygon_mask(geometry, gt, xoff, yoff, xsize, ysize, raster_proj)
    eroded = binary_erosion(lake_mask, structure=np.ones((3, 3), dtype=bool))
    boundary_mask = lake_mask & ~eroded

    fdr_win = fdr_band.ReadAsArray(xoff, yoff, xsize, ysize)
    src_win = src_band.ReadAsArray(xoff, yoff, xsize, ysize)
    acc_win = acc_band.ReadAsArray(xoff, yoff, xsize, ysize)
    w_win = w_band.ReadAsArray(xoff, yoff, xsize, ysize)

    boundary_pixels = [
        {"win_rc": (r, c), "point": pixel_to_point(gt, xoff, yoff, r, c)}
        for r, c in np.argwhere(boundary_mask)
    ]
    preliminary = find_stream_exit_candidates(
        boundary_mask, src_win, fdr_win, lake_mask, acc_win, w_win,
        gt, xoff, yoff, wsno_to_link, link_to_dout, link_to_accum,
        gauges, GAUGE_RADIUS_M,
    )
    surviving = filter_upstream_duplicates(preliminary, link_to_downstream)

    chosen, sel_type, is_carved, breakout_path = select_outlet_for_lake(
        lake_id, surviving, overrides_gdf, boundary_pixels,
        lake_mask, w_win, w_band, fdr_win, inv_gt, raster_size, cell_size,
        lake_through_wsnos,
    )

    features: list[dict] = [{
        "lake_id": lake_id,
        "feat_type": "window",
        "sel_type": "",
        "carve_ok": 0,
        "geometry": _window_polygon(gt, xoff, yoff, xsize, ysize),
    }, {
        "lake_id": lake_id,
        "feat_type": "lake",
        "sel_type": "",
        "carve_ok": 0,
        "geometry": geometry,
    }]

    ov = overrides_gdf[overrides_gdf["lake_id"] == str(lake_id).strip()]
    if not ov.empty:
        features.append({
            "lake_id": lake_id,
            "feat_type": "override_pt",
            "sel_type": "",
            "carve_ok": 0,
            "geometry": ov.iloc[0].geometry,
        })

    if chosen is None:
        features.append({
            "lake_id": lake_id,
            "feat_type": "error",
            "sel_type": sel_type,
            "carve_ok": 0,
            "geometry": geometry.centroid,
        })
        return features

    lock_target = sel_type in ("override_snapped", "algorithmic_stream")
    updated_fdr = route_centerline_to_target(
        fdr_win, lake_mask, chosen["win_rc"], lock_target_value=lock_target,
    )
    carve_ok = 0
    if is_carved and breakout_path:
        carve_ok = 1
        for current_node, parent_node in breakout_path:
            cr, cc = current_node
            updated_fdr[cr, cc] = get_d8_direction(current_node, parent_node)
        breakout = _breakout_line(breakout_path, gt, xoff, yoff)
        if breakout is not None:
        features.append({
            "lake_id": lake_id,
            "feat_type": "breakout_path",
            "sel_type": sel_type,
            "carve_ok": carve_ok,
            "geometry": breakout,
        })

    edit_mask = updated_fdr.astype(np.int32) != np.asarray(fdr_win, dtype=np.int32)
    for seg in _fdr_edit_lines(edit_mask, updated_fdr, gt, xoff, yoff):
        features.append({
            "lake_id": lake_id,
            "feat_type": "fdr_burn",
            "sel_type": sel_type,
            "carve_ok": carve_ok,
            "geometry": seg,
        })

    features.append({
        "lake_id": lake_id,
        "feat_type": "outlet_pt",
        "sel_type": sel_type,
        "carve_ok": carve_ok,
        "geometry": chosen["point"],
    })
    return features


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake-id", action="append", help="Hylak_id (repeatable)")
    parser.add_argument("--all-overrides", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    os.environ.setdefault("LAKE_DELINEATION_ROOT", str(STUDY_ROOT))
    ensure_output_dirs()

    for path in (PREP_LAKES, PASS1_STREAMS, PASS1_WATERSHEDS_TIF):
        if not path.is_file():
            print(f"ERROR: missing {path}", file=sys.stderr)
            return 1

    fdr_path = TAUDEM_D8 / "stream-network_elv-fdir.tif"
    src_path = TAUDEM_D8 / "stream-network_elv-src.tif"
    acc_path = TAUDEM_D8 / "stream-network_elv-ad8.tif"
    for path in (fdr_path, src_path, acc_path):
        if not path.is_file():
            print(f"ERROR: missing {path} (run TauDEM Pass 1 first)", file=sys.stderr)
            return 1

    lakes = gpd.read_file(PREP_LAKES)
    streams = gpd.read_file(PASS1_STREAMS)
    lake_through = build_lake_through_stream_wsnos(lakes, streams)
    overrides = load_overrides(str(OVERRIDES_CSV), lakes.crs)
    lookup = _lookup_tables_from_streams_gdf(streams)

    if args.all_overrides:
        lake_ids = overrides["lake_id"].astype(str).tolist()
    elif args.lake_id:
        lake_ids = [str(x).strip() for x in args.lake_id]
    else:
        parser.print_help()
        return 1

    ds_fdr = gdal.Open(str(fdr_path))
    ds_src = gdal.Open(str(src_path))
    ds_acc = gdal.Open(str(acc_path))
    ds_w = gdal.Open(str(PASS1_WATERSHEDS_TIF))
    gt = ds_fdr.GetGeoTransform()
    inv_gt = gdal.InvGeoTransform(gt)
    raster_proj = ds_fdr.GetProjection()
    raster_size = (ds_fdr.RasterXSize, ds_fdr.RasterYSize)
    cell_size = abs(gt[1])

    gauges = load_gauges(str(PREP_GAUGES), raster_proj) if PREP_GAUGES.is_file() else gpd.GeoDataFrame()

    all_features: list[dict] = []
    for lake_id in lake_ids:
        rows = lakes[lakes["Hylak_id"].astype(str).str.strip() == str(lake_id).strip()]
        if rows.empty:
            print(f"Lake {lake_id}: not in {PREP_LAKES}")
            continue
        row = rows.iloc[0]
        print(f"Processing lake {lake_id}...")
        all_features.extend(_process_lake_for_export(
            str(lake_id).strip(),
            row.geometry,
            fdr_band=ds_fdr.GetRasterBand(1),
            src_band=ds_src.GetRasterBand(1),
            acc_band=ds_acc.GetRasterBand(1),
            w_band=ds_w.GetRasterBand(1),
            gt=gt,
            inv_gt=inv_gt,
            raster_size=raster_size,
            raster_proj=raster_proj,
            cell_size=cell_size,
            lookup_tables=lookup,
            gauges=gauges,
            overrides_gdf=overrides,
            lake_through_wsnos=lake_through,
        ))

    if not all_features:
        print("No features to write.", file=sys.stderr)
        return 1

    gdf = gpd.GeoDataFrame(all_features, crs=lakes.crs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(args.output)
    print(f"Wrote {len(gdf)} features -> {args.output}")
    print("Layers (feat_type): window, lake, override_pt, outlet_pt, breakout_path, fdr_burn")
    print("Delete diagnose_lake_override.py when finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
