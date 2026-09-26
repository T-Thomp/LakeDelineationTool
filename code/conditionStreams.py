"""
Stream flow-direction conditioning along user-defined valley paths.

Called by Delineation-Workflow.slurm right after conditionLakes.py and before
TauDEM Pass 2. For each start/end coordinate pair in a CSV, it finds the
lowest-cost 8-direction path between the two points across a DEM cost surface
that favours valley bottoms and downhill steps,
then rewrites flow directions in ``fdr_lakes.tif`` so water follows that path
from start to end. Cells on either side of the path are pointed into it, so
flow cannot jump across a diagonal step.

Use it where TauDEM routes a stream the wrong way (e.g. through a road fill,
dam, or DEM artifact) and you know where the stream should go.

Inputs
------
  stream_conditioning.csv (optional) - one path per row:
      id,start_lat,start_lon,end_lat,end_lon     (id is optional)
  INPUT_DEM (raw DEM)                - cost surface; must share the FDR grid
  fdr_lakes.tif                      - edited in place
  lakes.shp (optional)               - lake cells are never changed, so the
                                       reservoir edits from conditionLakes.py
                                       are preserved

The start (inflow) point is snapped onto the nearest Pass 1 stream when that
stream is within INFLOW_SNAP_CELLS, so the new path diverts the existing
channel. A start farther away is used as given. Rows whose start or end point
is outside the raster are skipped silently.
Rows are processed top to bottom; where paths overlap, later rows win.

Re-run this script after any full conditionLakes.py run, which rebuilds
fdr_lakes.tif from the original TauDEM flow directions.

  python3 conditionStreams.py --csv stream_conditioning.csv
"""

import argparse
import heapq
import os
import sys

import numpy as np
import pandas as pd
import geopandas as gpd
from osgeo import gdal
from shapely.geometry import box

from conditionLakes import D8_DIRS, get_d8_direction, get_d8_offset, rasterize_geometry
from pipeline_paths import FDR_CENTERLINE, INPUT_DEM, PASS1_STREAMS, PREP_LAKES

gdal.UseExceptions()

# ------------------------------------------------------------------------------
# COST SURFACE WEIGHTS
# ------------------------------------------------------------------------------
# Relative elevation rel = (z - window_min) / (window_max - window_min) runs from
# 0 on the valley floor to 1 at the highest cell in the window. Per-cell cost is
# BASE_COST + VALLEY_SCALE * rel ** VALLEY_POWER, so ridges are expensive and the
# valley bottom is cheap (same idea as the reservoir centerline penalty).
BUFFER_CELLS = 50            # cells added around the start/end bounding box
# Snap the upstream (inflow) point onto a Pass 1 stream when one is this close,
# so the new path leaves the existing channel. Farther points are used as given.
INFLOW_SNAP_CELLS = 10
BASE_COST = 1.0              # cost of one straight step on the valley floor
VALLEY_SCALE = 400.0         # how strongly high ground is avoided
VALLEY_POWER = 2.0           # >1 keeps the valley floor cheap and walls steep
# Climbing costs UPHILL_PENALTY per full window relief, so water-like downhill
# routes win over shortcuts that go up and over a rise.
UPHILL_PENALTY = 1000.0
# Small pull toward the end point so the path does not wander on flat ground.
# Added per cell as DIST_WEIGHT * distance_to_end / max_distance. Set to 0 to disable.
DIST_WEIGHT = 0.5

# Max D8 steps traced downstream from the end cell when checking for flow loops.
MAX_LOOP_TRACE_STEPS = 1000
# Max cells at and past the end point re-pointed downslope when the original
# flow there would run back into the new path or its banks.
END_SLOPE_CELLS = 5

DEFAULT_CSV = "./stream_conditioning.csv"
REQUIRED_COLUMNS = ("start_lat", "start_lon", "end_lat", "end_lon")

D8_STEPS = [(dr, dc, float(np.hypot(dr, dc))) for dr, dc in D8_DIRS]


def load_paths_csv(csv_path, target_crs):
    """Read the CSV and return (labels, start_points, end_points) in target_crs."""
    df = pd.read_csv(csv_path)
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"{csv_path} is missing column(s): {', '.join(missing)}")

    labels = []
    for i, row in df.iterrows():
        has_id = "id" in df.columns and pd.notna(row["id"]) and str(row["id"]).strip()
        labels.append(str(row["id"]).strip() if has_id else f"row {i + 1}")

    starts = gpd.GeoSeries(
        gpd.points_from_xy(df["start_lon"], df["start_lat"]), crs="EPSG:4326",
    ).to_crs(target_crs)
    ends = gpd.GeoSeries(
        gpd.points_from_xy(df["end_lon"], df["end_lat"]), crs="EPSG:4326",
    ).to_crs(target_crs)
    return labels, list(starts), list(ends)


def point_to_rc(point, inv_gt, raster_size):
    """Return (row, col) of the cell containing point, or None if off-raster."""
    px, py = gdal.ApplyGeoTransform(inv_gt, point.x, point.y)
    col = int(np.floor(px))
    row = int(np.floor(py))
    if not (0 <= col < raster_size[0] and 0 <= row < raster_size[1]):
        return None
    return row, col


def path_window(start_rc, end_rc, raster_size, buffer_cells=BUFFER_CELLS):
    """Pixel window (xoff, yoff, xsize, ysize) around both cells plus a buffer."""
    row_min = max(0, min(start_rc[0], end_rc[0]) - buffer_cells)
    row_max = min(raster_size[1] - 1, max(start_rc[0], end_rc[0]) + buffer_cells)
    col_min = max(0, min(start_rc[1], end_rc[1]) - buffer_cells)
    col_max = min(raster_size[0] - 1, max(start_rc[1], end_rc[1]) + buffer_cells)
    return col_min, row_min, col_max - col_min + 1, row_max - row_min + 1


def window_geotransform(gt, xoff, yoff):
    return (
        gt[0] + xoff * gt[1], gt[1], gt[2],
        gt[3] + yoff * gt[5], gt[4], gt[5],
    )


def build_lake_mask(lakes, win_gt, xsize, ysize, raster_proj):
    """Boolean mask of lake cells in the window (empty if no lakes)."""
    mask = np.zeros((ysize, xsize), dtype=np.uint8)
    if lakes is None or lakes.empty:
        return mask.astype(bool)

    x0, y0 = win_gt[0], win_gt[3]
    x1 = x0 + xsize * win_gt[1]
    y1 = y0 + ysize * win_gt[5]
    window_box = box(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
    for idx in lakes.sindex.query(window_box, predicate="intersects"):
        rasterize_geometry(mask, lakes.geometry.iloc[idx], win_gt, raster_proj)
    return mask.astype(bool)


def build_cell_cost(dem, valid, end_rc):
    """Per-cell entry cost: relative-elevation valley term plus distance-to-end pull."""
    z_min = float(dem[valid].min())
    z_max = float(dem[valid].max())
    z_range = max(z_max - z_min, 1e-6)

    rel = np.zeros(dem.shape, dtype=np.float64)
    rel[valid] = (dem[valid] - z_min) / z_range
    cell_cost = BASE_COST + VALLEY_SCALE * rel ** VALLEY_POWER

    if DIST_WEIGHT > 0:
        rows, cols = np.indices(dem.shape)
        dist_to_end = np.hypot(rows - end_rc[0], cols - end_rc[1])
        cell_cost += DIST_WEIGHT * dist_to_end / max(float(dist_to_end.max()), 1.0)

    cell_cost[~valid] = np.inf
    return cell_cost, z_range


def route_valley_path(dem, valid, start_rc, end_rc):
    """
    Lowest-cost 8-direction path from start_rc to end_rc (Dijkstra).

    Step cost = entry cell cost * step length, plus an uphill penalty scaled by
    the elevation gained relative to the window relief. Returns the list of
    (row, col) cells from start to end, or None if the end is unreachable.
    """
    h, w = dem.shape
    cell_cost, z_range = build_cell_cost(dem, valid, end_rc)
    uphill_scale = UPHILL_PENALTY / z_range

    dist = np.full((h, w), np.inf)
    parent = np.full((h, w), -1, dtype=np.int64)
    sr, sc = start_rc
    dist[sr, sc] = 0.0
    pq = [(0.0, sr, sc)]

    while pq:
        curr_d, r, c = heapq.heappop(pq)
        if (r, c) == end_rc:
            break
        if curr_d > dist[r, c]:
            continue
        z_here = dem[r, c]
        for dr, dc, step_len in D8_STEPS:
            nr, nc = r + dr, c + dc
            if not (0 <= nr < h and 0 <= nc < w) or not valid[nr, nc]:
                continue
            step = cell_cost[nr, nc] * step_len
            rise = dem[nr, nc] - z_here
            if rise > 0:
                step += uphill_scale * rise
            new_d = curr_d + step
            if new_d < dist[nr, nc]:
                dist[nr, nc] = new_d
                parent[nr, nc] = r * w + c
                heapq.heappush(pq, (new_d, nr, nc))

    if not np.isfinite(dist[end_rc]):
        return None

    path = [end_rc]
    while path[-1] != start_rc:
        flat = parent[path[-1]]
        if flat < 0:
            return None
        path.append(divmod(int(flat), w))
    path.reverse()
    return path


def flanking_cells(curr, nxt):
    """Cells on either side of one path step.

    A diagonal step's other two corners can flow across the channel. An
    orthogonal step uses the cells directly beside it.
    """
    dr = nxt[0] - curr[0]
    dc = nxt[1] - curr[1]
    if dr != 0 and dc != 0:
        return [(curr[0] + dr, curr[1]), (curr[0], curr[1] + dc)]
    return [
        (curr[0] - dc, curr[1] + dr),
        (curr[0] + dc, curr[1] - dr),
        (nxt[0] - dc, nxt[1] + dr),
        (nxt[0] + dc, nxt[1] - dr),
    ]


def flows_back(fdr_win, start_rc, edited, max_steps=MAX_LOOP_TRACE_STEPS):
    """True if D8 flow from start_rc lands on an edited cell, loops, or stops."""
    h, w = fdr_win.shape
    row, col = start_rc
    seen = set()
    for _ in range(max_steps):
        if (row, col) in edited or (row, col) in seen:
            return True
        seen.add((row, col))
        dr, dc = get_d8_offset(fdr_win[row, col])
        if dr == 0 and dc == 0:
            return True
        row, col = row + dr, col + dc
        if not (0 <= row < h and 0 <= col < w):
            return False
    return False


def exit_end_downslope(updated, end_rc, edited, dem, valid, lake_mask):
    """
    Make flow leave the end point instead of sinking there.

    If the end cell's current direction already drains away from the edits, it
    is kept. Otherwise the end cell, and up to END_SLOPE_CELLS cells after it,
    are pointed at their steepest-descent neighbour outside the edits until the
    flow no longer comes back.
    """
    h, w = updated.shape
    dr, dc = get_d8_offset(updated[end_rc])
    nxt = (end_rc[0] + dr, end_rc[1] + dc)
    if (dr, dc) != (0, 0) and not (0 <= nxt[0] < h and 0 <= nxt[1] < w):
        return
    if (dr, dc) != (0, 0) and nxt not in edited and not flows_back(updated, nxt, edited):
        return

    current = end_rc
    for _ in range(END_SLOPE_CELLS):
        if lake_mask[current]:
            return
        best = None
        for dr, dc in D8_DIRS:
            nr, nc = current[0] + dr, current[1] + dc
            if not (0 <= nr < h and 0 <= nc < w) or not valid[nr, nc] or (nr, nc) in edited:
                continue
            drop = (dem[current] - dem[nr, nc]) / np.hypot(dr, dc)
            if best is None or drop > best[0]:
                best = (drop, (nr, nc))
        if best is None:
            return
        nxt = best[1]
        updated[current] = get_d8_direction(current, nxt)
        edited.add(current)
        if lake_mask[nxt] or not flows_back(updated, nxt, edited):
            return
        current = nxt


def apply_path_to_fdr(fdr_win, path, lake_mask, dem, valid):
    """Point the path downstream, point both banks into it, then drain the end downslope."""
    updated = fdr_win.copy()
    for current_rc, next_rc in zip(path[:-1], path[1:]):
        if lake_mask[current_rc]:
            continue
        updated[current_rc] = get_d8_direction(current_rc, next_rc)

    on_path = set(path)
    edited = set(path)
    order = {rc: i for i, rc in enumerate(path)}
    h, w = updated.shape
    for curr, nxt in zip(path[:-1], path[1:]):
        for side in flanking_cells(curr, nxt):
            r, c = side
            if not (0 <= r < h and 0 <= c < w) or lake_mask[r, c] or side in on_path:
                continue
            choices = [
                (r + dr, c + dc)
                for dr, dc in D8_DIRS
                if (r + dr, c + dc) in order
            ]
            if not choices:
                continue
            target = max(choices, key=order.get)
            updated[r, c] = get_d8_direction(side, target)
            edited.add(side)

    exit_end_downslope(updated, path[-1], edited, dem, valid, lake_mask)
    return updated


def end_flow_reenters_path(fdr_win, path, max_steps=MAX_LOOP_TRACE_STEPS):
    """True if D8 flow traced downstream from the end cell lands back on the path."""
    path_cells = set(path[:-1])
    h, w = fdr_win.shape
    row, col = path[-1]
    for _ in range(max_steps):
        dr, dc = get_d8_offset(fdr_win[row, col])
        if dr == 0 and dc == 0:
            return False
        row, col = row + dr, col + dc
        if not (0 <= row < h and 0 <= col < w):
            return False
        if (row, col) in path_cells:
            return True
    return False


def load_streams(streams_path, raster_proj):
    """Pass 1 stream lines used to snap inflow points, or None if unavailable."""
    if not os.path.exists(streams_path):
        print(f"No streams file at {streams_path}; inflow points will not be snapped.")
        return None
    streams = gpd.read_file(streams_path).to_crs(raster_proj)
    streams = streams[streams.geometry.notna() & ~streams.geometry.is_empty]
    if streams.empty:
        return None
    return streams.reset_index(drop=True)


def snap_inflow_to_stream(point, streams, max_dist):
    """
    Return (point, snapped).

    When a stream is within max_dist, the point is moved onto that line so the
    conditioned path diverts the existing channel. Otherwise the point is unchanged.
    """
    if streams is None or streams.empty:
        return point, False
    (_, line_idx), dists = streams.sindex.nearest(
        [point], return_all=False, return_distance=True,
    )
    dist = float(np.asarray(dists).ravel()[0])
    if dist > max_dist:
        return point, False
    line = streams.geometry.iloc[int(np.asarray(line_idx).ravel()[0])]
    return line.interpolate(line.project(point)), True


def load_lakes(lakes_path, raster_proj):
    if not os.path.exists(lakes_path):
        print(f"No lakes file at {lakes_path}; lake cells will not be protected.")
        return None
    return gpd.read_file(lakes_path).to_crs(raster_proj)


def condition_streams(csv_path, dem_path, fdr_path, lakes_path, streams_path):
    """Condition fdr_path along every CSV path. Returns a process exit code."""
    if not os.path.exists(csv_path):
        print(f"No stream conditioning file at {csv_path}; skipping stream conditioning.")
        return 0
    if not os.path.exists(fdr_path):
        print(f"ERROR: {fdr_path} not found; run conditionLakes.py first.")
        return 1

    ds_fdr = gdal.Open(fdr_path, gdal.GA_Update)
    ds_dem = gdal.Open(dem_path)
    gt = ds_fdr.GetGeoTransform()
    raster_proj = ds_fdr.GetProjection()
    raster_size = (ds_fdr.RasterXSize, ds_fdr.RasterYSize)

    dem_size = (ds_dem.RasterXSize, ds_dem.RasterYSize)
    if dem_size != raster_size or not np.allclose(ds_dem.GetGeoTransform(), gt):
        print(
            f"ERROR: DEM grid {dem_size} / {ds_dem.GetGeoTransform()} does not match "
            f"flow-direction grid {raster_size} / {gt}."
        )
        return 1

    inv_gt = gdal.InvGeoTransform(gt)
    fdr_band = ds_fdr.GetRasterBand(1)
    dem_band = ds_dem.GetRasterBand(1)
    dem_nodata = dem_band.GetNoDataValue()

    labels, starts, ends = load_paths_csv(csv_path, raster_proj)
    lakes = load_lakes(lakes_path, raster_proj)
    streams = load_streams(streams_path, raster_proj)
    cell_size = max(abs(gt[1]), abs(gt[5]))
    snap_dist = INFLOW_SNAP_CELLS * cell_size
    print(f"Conditioning {len(labels)} stream path(s) from {csv_path}...")

    conditioned = 0
    skipped = 0
    total_changed = 0

    for label, start_pt, end_pt in zip(labels, starts, ends):
        start_pt, snapped = snap_inflow_to_stream(start_pt, streams, snap_dist)
        if snapped:
            print(f"  {label}: inflow snapped to the nearest stream.")
        elif streams is not None:
            print(
                f"  {label}: inflow is farther than {INFLOW_SNAP_CELLS} cells "
                "from a stream; using the given point."
            )
        start_abs = point_to_rc(start_pt, inv_gt, raster_size)
        end_abs = point_to_rc(end_pt, inv_gt, raster_size)
        if start_abs is None or end_abs is None:
            skipped += 1
            continue
        if start_abs == end_abs:
            print(f"  {label}: start and end fall in the same cell; skipped.")
            skipped += 1
            continue

        xoff, yoff, xsize, ysize = path_window(start_abs, end_abs, raster_size)
        start_rc = (start_abs[0] - yoff, start_abs[1] - xoff)
        end_rc = (end_abs[0] - yoff, end_abs[1] - xoff)

        dem = dem_band.ReadAsArray(xoff, yoff, xsize, ysize).astype(np.float64)
        valid = np.isfinite(dem)
        if dem_nodata is not None:
            valid &= dem != dem_nodata
        if not (valid[start_rc] and valid[end_rc]):
            print(f"  {label}: start or end cell has no DEM value; skipped.")
            skipped += 1
            continue

        path = route_valley_path(dem, valid, start_rc, end_rc)
        if path is None:
            print(f"  {label}: no path found between start and end; skipped.")
            skipped += 1
            continue

        win_gt = window_geotransform(gt, xoff, yoff)
        lake_mask = build_lake_mask(lakes, win_gt, xsize, ysize, raster_proj)
        fdr_win = fdr_band.ReadAsArray(xoff, yoff, xsize, ysize)
        updated_fdr = apply_path_to_fdr(fdr_win, path, lake_mask, dem, valid)

        if end_flow_reenters_path(updated_fdr, path):
            print(
                f"  WARNING {label}: flow leaving the end point runs back onto the new "
                "path (flow loop). Move the end point further downstream."
            )

        changed = int(np.count_nonzero(updated_fdr != fdr_win))
        if changed:
            fdr_band.WriteArray(updated_fdr, xoff, yoff)
        conditioned += 1
        total_changed += changed
        print(f"  {label}: {len(path)}-cell path, {changed} cell(s) changed.")

    fdr_band.FlushCache()
    ds_fdr = None
    ds_dem = None

    print("-" * 90)
    print(
        f"Stream conditioning complete. Rows read: {len(labels)}, conditioned: "
        f"{conditioned}, skipped: {skipped}, cells changed: {total_changed}."
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Condition flow directions along valley paths between CSV start/end points.",
    )
    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV,
        help=(
            "CSV with start_lat,start_lon,end_lat,end_lon (optional id) per path. "
            f"Default: {DEFAULT_CSV}"
        ),
    )
    args = parser.parse_args()

    sys.exit(condition_streams(
        csv_path=args.csv,
        dem_path=str(INPUT_DEM),
        fdr_path=str(FDR_CENTERLINE),
        lakes_path=str(PREP_LAKES),
        streams_path=str(PASS1_STREAMS),
    ))
