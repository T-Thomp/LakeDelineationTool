"""
Raster flow-path editor for instream reservoirs.

Called by tau-dem-delineation-srun.slurm after TauDEM Pass 1 and the
filterLakes / getGauges steps. It reads the original TauDEM flow-direction
raster and, for each filtered lake polygon, rewrites flow directions inside
the lake so that all lake cells drain to a single chosen outlet along a
natural-looking centerline.

The output raster (fdr_centerline_all.tif) is used as the flow-direction
input for TauDEM Pass 2 and Pass 3 in the SLURM pipeline.

Problem being solved
--------------------
When a reservoir sits on a stream network, TauDEM's D8 flow directions
inside the flat lake surface are ambiguous: many boundary pixels look like
valid stream exits. This script:

  1. Identifies which boundary pixels are real stream exits.
  2. Picks one outlet per lake (using gauge proximity, basin topology, and
     optional manual overrides).
  3. Re-routes every lake interior cell so flow converges on that outlet
     through a lake "spine" (deepest interior path), producing a continuous
     instream backbone for downstream TauDEM delineation.

Inputs (TauDEM / upstream Python products)
------------------------------------------
  fdr (stream-network_elv-fdir.tif)  - D8 flow direction, edited in-place copy
  src (stream-network_elv-src.tif)   - stream source mask (1 = on-network cell)
  accum (stream-network_elv-ad8.tif) - contributing area per cell
  w_raster (original-delineated-watersheds.tif) - watershed ID (WSNO) per cell
  streams vector                     - TauDEM stream links with topology attrs
  lakes vector (filtered_lakes.shp)  - reservoir polygons from filterLakes.py
  gauges vector                      - stream gauges from getGauges.py
  overrides CSV (optional)             - manual outlet lat/lon per Hylak_id

Output
------
  fdr_centerline_all.tif - flow directions with lake interiors re-routed
"""

import os
import heapq
import numpy as np
import pandas as pd
import geopandas as gpd
from osgeo import gdal, ogr, osr
from shapely.geometry import Point
from scipy.ndimage import distance_transform_edt, binary_erosion

gdal.UseExceptions()

# ------------------------------------------------------------------------------
# D8 FLOW-DIRECTION CONSTANTS (TauDEM convention)
# ------------------------------------------------------------------------------
# Each cell stores an integer 1-8 indicating which neighbor it flows INTO.
# Offsets are (delta_row, delta_col) from current cell to that neighbor.
# Row 0 is north (top of raster); col 0 is west (left of raster).
D8_OFFSETS = {
    1: (0, 1), 2: (-1, 1), 3: (-1, 0), 4: (-1, -1),
    5: (0, -1), 6: (1, -1), 7: (1, 0), 8: (1, 1),
}
# Reverse map: given a (row, col) step from cell A toward cell B, return the
# D8 code that A must store so water flows toward B.
D8_REVERSE = {v: k for k, v in D8_OFFSETS.items()}
# All 8 neighbor directions, used by the Dijkstra routers below.
D8_DIRS = list(D8_OFFSETS.values())

# Cost weights for the two-pass centerline router (see route_centerline_to_target).
CENTERLINE_PENALTY_SCALE = 500.0   # how strongly to avoid lake margins (bank cells)
BACKBONE_COST = 0.01               # near-zero cost on the pre-computed spine
OFF_BACKBONE_BASE_COST = 10.0      # base cost off the spine (margins penalized further)
SPINE_STEP_BASE_COST = 0.1         # base step cost when building the spine

# Max steps along the override carve ray before falling back to algorithmic outlet
# selection. Each step is one cell along the outward direction from the lake shore.
MAX_OVERRIDE_BREAKOUT_STEPS = 100


def get_d8_offset(fdr_val):
    """Return (drow, dcol) neighbor offset for a TauDEM D8 flow-direction value."""
    return D8_OFFSETS.get(int(fdr_val), (0, 0))


def get_d8_direction(current_rc, parent_rc):
    """
    Compute the D8 code for current_rc so that flow points toward parent_rc.

    Used after Dijkstra routing: parent_rc is the upstream neighbor that
    drains into the current cell on the path back to the outlet.
    """
    dr = int(np.sign(parent_rc[0] - current_rc[0])) if parent_rc[0] != current_rc[0] else 0
    dc = int(np.sign(parent_rc[1] - current_rc[1])) if parent_rc[1] != current_rc[1] else 0
    return D8_REVERSE.get((dr, dc), 0)


def build_vector_lookup_tables(streams_path):
    """
    Pre-build dictionaries from the TauDEM stream shapefile.

    These let us connect raster WSNO values to vector stream topology without
    repeated spatial joins inside the per-lake loop:

      wsno_to_link   - watershed number at a pixel -> stream LINKNO
      link_to_dout   - LINKNO -> DOUTEND (distance to basin pour point)
      link_to_accum  - LINKNO -> DSContArea (downstream contributing area)
      link_to_downstream - LINKNO -> DSLINKNO (next link downstream)
    """
    print("Pre-building vector attribute lookup maps...")
    gdf = gpd.read_file(streams_path)
    return (
        dict(zip(gdf['WSNO'], gdf['LINKNO'])),
        dict(zip(gdf['LINKNO'], gdf['DOUTEND'])),
        dict(zip(gdf['LINKNO'], gdf['DSContArea'])),
        dict(zip(gdf['LINKNO'], gdf['DSLINKNO'])),
    )


def is_link_upstream_of(link_a, link_b, link_to_downstream):
    """
    Return True if stream link_a eventually flows into link_b.

    Walks the DSLINKNO chain from link_a. Used to drop upstream duplicate
    outlet candidates when multiple boundary pixels belong to the same
    stream branch (keep only the furthest-downstream exit).
    """
    nxt = link_to_downstream.get(link_a, -1)
    while nxt not in (-1, 0):
        if nxt == link_b:
            return True
        nxt = link_to_downstream.get(nxt, -1)
    return False


def pixel_to_point(gt, xoff, yoff, row, col):
    """Convert a row/col within a raster window to a projected map coordinate."""
    x = gt[0] + (xoff + col) * gt[1] + (yoff + row) * gt[2]
    y = gt[3] + (xoff + col) * gt[4] + (yoff + row) * gt[5]
    return Point(x, y)


def raster_window_from_bounds(geom_bounds, inv_gt, raster_size):
    """
    Compute the pixel window (xoff, yoff, xsize, ysize) covering a polygon bbox.

    Returns None if the lake falls entirely outside the raster extent, in which
    case the lake is skipped silently.
    """
    min_x, min_y, max_x, max_y = geom_bounds
    px_min, py_max = gdal.ApplyGeoTransform(inv_gt, min_x, min_y)
    px_max, py_min = gdal.ApplyGeoTransform(inv_gt, max_x, max_y)

    xoff = int(np.floor(min(px_min, px_max)))
    yoff = int(np.floor(min(py_min, py_max)))
    xsize = int(np.ceil(max(px_min, px_max)) - xoff) + 1
    ysize = int(np.ceil(max(py_min, py_max)) - yoff) + 1

    if xoff < 0 or yoff < 0 or (xoff + xsize) > raster_size[0] or (yoff + ysize) > raster_size[1]:
        return None
    return xoff, yoff, xsize, ysize


def rasterize_polygon_mask(geom, gt, xoff, yoff, xsize, ysize, raster_proj):
    """
    Burn a lake polygon into a boolean mask aligned to a raster window.

    ALL_TOUCHED=TRUE ensures pixels touched by the polygon edge are included,
    which matters for finding boundary exit cells.
    """
    mem_driver = gdal.GetDriverByName('MEM')
    mem_ds = mem_driver.Create('', xsize, ysize, 1, gdal.GDT_Byte)
    mem_ds.SetGeoTransform([
        gt[0] + xoff * gt[1], gt[1], gt[2],
        gt[3] + yoff * gt[5], gt[4], gt[5],
    ])

    ogr_ds = ogr.GetDriverByName('Memory').CreateDataSource('wrk')
    srs = osr.SpatialReference()
    srs.ImportFromWkt(raster_proj)
    ogr_lyr = ogr_ds.CreateLayer('poly', srs, geom_type=ogr.wkbPolygon)
    feat = ogr.Feature(ogr_lyr.GetLayerDefn())
    feat.SetGeometry(ogr.CreateGeometryFromWkt(geom.wkt))
    ogr_lyr.CreateFeature(feat)

    gdal.RasterizeLayer(mem_ds, [1], ogr_lyr, burn_values=[1], options=["ALL_TOUCHED=TRUE"])
    lake_mask = mem_ds.GetRasterBand(1).ReadAsArray().astype(bool)
    mem_ds = None
    ogr_ds = None
    return lake_mask


def route_centerline_to_target(fdr_win, lake_mask, target_rc, lock_target_value=False):
    """
    Rewrite D8 flow directions inside a lake so all cells drain to target_rc.

    This is the core hydrologic edit. TauDEM's original directions inside flat
    lakes are meaningless; we replace them with a coherent drainage pattern.

    Algorithm (two-pass Dijkstra on the lake mask):

    PASS 1 - Find the lake "spine" (centerline backbone)
      - distance_transform_edt gives each lake cell its distance to the nearest
        non-lake cell (i.e. distance to the "bank"). The cell farthest from the
        bank is the deepest interior point (lake thalweg proxy).
      - Dijkstra from that deepest point toward the outlet (target_rc), with
        higher cost near banks (centerline_penalty), finds the preferred
        centerline path through the lake.
      - Trace parent pointers back from outlet to deepest point to get
        backbone_cells.

    PASS 2 - Route all lake cells toward the outlet
      - Dijkstra outward from target_rc through all lake cells.
      - Cells on the backbone cost almost nothing; off-backbone cells pay a
        large base cost plus the bank penalty. This forces flow to follow the
        spine while still reaching every interior cell.
      - For each routed cell, write a D8 code pointing toward its Dijkstra
        parent (i.e. one step closer to the outlet).

    lock_target_value:
      - True  (normal case): preserve the outlet cell's original TauDEM FDR so
        it still connects to the external stream network.
      - False (override carve): allow the outlet FDR to be rewritten when a
        manual override forces flow through the lake wall.
    """
    h, w = fdr_win.shape
    r0, c0 = target_rc
    original_target_fdr = int(fdr_win[r0, c0])

    # Distance from each lake cell to the nearest bank. High values = deep interior.
    bank_dist = distance_transform_edt(lake_mask)
    max_bank = float(np.max(bank_dist))
    if max_bank <= 0:
        max_bank = 1.0
    # Penalty peaks at the shoreline (bank_dist=0) and drops to 0 at the center.
    # Power of 4 makes the trough narrow, encouraging a single spine.
    centerline_penalty = ((max_bank - bank_dist) / max_bank) ** 4 * CENTERLINE_PENALTY_SCALE

    # --- PASS 1: spine from deepest interior point to outlet ---
    dr_deep, dc_deep = divmod(int(np.argmax(bank_dist)), w)
    spine_dist = np.full((h, w), np.inf)
    spine_parent = np.full((h, w), -1, dtype=int)
    pq_spine = [(0.0, dr_deep, dc_deep)]
    spine_dist[dr_deep, dc_deep] = 0.0

    while pq_spine:
        curr_d, r, c = heapq.heappop(pq_spine)
        if (r, c) == (r0, c0):
            break  # reached the outlet; spine path is complete
        if curr_d > spine_dist[r, c]:
            continue  # stale queue entry
        for dr_move, dc_move in D8_DIRS:
            nr, nc = r + dr_move, c + dc_move
            if 0 <= nr < h and 0 <= nc < w and lake_mask[nr, nc]:
                new_d = curr_d + (SPINE_STEP_BASE_COST + centerline_penalty[nr, nc]) * np.hypot(dr_move, dc_move)
                if new_d < spine_dist[nr, nc]:
                    spine_dist[nr, nc] = new_d
                    spine_parent[nr, nc] = r * w + c  # flat index for compact storage
                    heapq.heappush(pq_spine, (new_d, nr, nc))

    # Walk parent chain from outlet back to deepest point to collect backbone cells.
    backbone_cells = {(r0, c0)}
    trace_r, trace_c = r0, c0
    while True:
        flat_p = spine_parent[trace_r, trace_c]
        if flat_p == -1:
            break
        trace_r, trace_c = divmod(flat_p, w)
        backbone_cells.add((trace_r, trace_c))
        if (trace_r, trace_c) == (dr_deep, dc_deep):
            break

    # --- PASS 2: route all lake cells toward outlet, preferring the backbone ---
    dist_grid = np.full((h, w), np.inf)
    parent_r = np.full((h, w), -1, dtype=int)
    parent_c = np.full((h, w), -1, dtype=int)
    pq = [(0.0, r0, c0)]
    dist_grid[r0, c0] = 0.0

    while pq:
        curr_dist, r, c = heapq.heappop(pq)
        if curr_dist > dist_grid[r, c]:
            continue
        for dr_move, dc_move in D8_DIRS:
            nr, nc = r + dr_move, c + dc_move
            if not (0 <= nr < h and 0 <= nc < w and lake_mask[nr, nc]):
                continue
            if (nr, nc) == (r0, c0):
                continue  # do not route back into the outlet itself
            cell_cost = BACKBONE_COST if (nr, nc) in backbone_cells else OFF_BACKBONE_BASE_COST + centerline_penalty[nr, nc]
            new_dist = curr_dist + cell_cost * np.hypot(dr_move, dc_move)
            if new_dist < dist_grid[nr, nc]:
                dist_grid[nr, nc] = new_dist
                parent_r[nr, nc] = r
                parent_c[nr, nc] = c
                heapq.heappush(pq, (new_dist, nr, nc))

    # Write new D8 values: each cell points toward its upstream parent on the
    # drainage path (parent is closer to the outlet in cost space).
    updated_fdr = fdr_win.copy()
    has_parent = parent_r >= 0
    rows, cols = np.where(has_parent)
    for r, c in zip(rows, cols):
        if (r, c) != (r0, c0):
            updated_fdr[r, c] = get_d8_direction((r, c), (parent_r[r, c], parent_c[r, c]))

    if lock_target_value:
        updated_fdr[r0, c0] = original_target_fdr
    return updated_fdr


def find_stream_exit_candidates(
    boundary_mask, src_win, fdr_win, lake_mask, acc_win, w_win,
    gt, xoff, yoff, wsno_to_link, link_to_dout, link_to_accum, gauges, gauge_radius_meters,
):
    """
    Find boundary pixels where an existing stream exits the lake.

    A cell is a valid exit candidate when ALL of these hold:
      - It lies on the lake shoreline (boundary_mask).
      - TauDEM marked it as part of the stream network (src_win == 1).
      - Its D8 flow direction points OUT of the lake (downstream neighbor is
        not inside lake_mask).

    For each candidate we also record attributes used later for ranking:
      link_no      - stream link at this pixel (via WSNO lookup)
      doutend      - how far this link is from the basin pour point
      dscontarea   - macro contributing area of the link
      local_accum  - raster contributing area at this pixel
      gauge_dist   - distance to nearest stream gauge within search radius
    """
    ysize, xsize = lake_mask.shape
    candidates = []

    for r, c in np.argwhere(boundary_mask & (src_win == 1)):
        dr, dc = get_d8_offset(fdr_win[r, c])
        nr, nc = r + dr, c + dc
        # Flow exits the lake if the downstream neighbor is outside the mask
        # (or off the window edge).
        exits_lake = not (0 <= nr < ysize and 0 <= nc < xsize) or not lake_mask[nr, nc]
        if not exits_lake:
            continue

        pt = pixel_to_point(gt, xoff, yoff, r, c)
        link_no = wsno_to_link.get(w_win[r, c], -1)
        gauge_distances = gauges.distance(pt)
        valid_gauges = gauge_distances[gauge_distances <= gauge_radius_meters]

        candidates.append({
            'win_rc': (r, c),
            'point': pt,
            'link_no': link_no,
            'doutend': link_to_dout.get(link_no, float('inf')),
            'dscontarea': link_to_accum.get(link_no, 0),
            'local_accum': acc_win[r, c],
            'gauge_dist': valid_gauges.min() if not valid_gauges.empty else float('inf'),
        })
    return candidates


def filter_upstream_duplicates(candidates, link_to_downstream):
    """
    Remove upstream duplicate exits on the same stream branch.

    When a lake spans multiple boundary pixels on one inflowing stream, only the
    furthest-downstream exit should survive. A candidate is dropped if:
      - Its link_no is upstream of another candidate's link_no on the same
        network, OR
      - It shares the same link_no but has lower local_accum (less core flow).
    """
    surviving = []
    for i, cand_a in enumerate(candidates):
        link_a = cand_a['link_no']
        is_duplicate = False
        for j, cand_b in enumerate(candidates):
            if i == j:
                continue
            link_b = cand_b['link_no']
            if link_a == -1 or link_b == -1:
                continue
            if is_link_upstream_of(link_a, link_b, link_to_downstream):
                is_duplicate = True
                break
            if link_a == link_b and cand_b['local_accum'] > cand_a['local_accum']:
                is_duplicate = True
                break
        if not is_duplicate:
            surviving.append(cand_a)
    return surviving


def _source_basin_wsno(target_rc, w_win, lake_mask):
    """
    WSNO of the basin the override outlet currently sits in.

    Uses the watershed ID at the shoreline target cell. If that cell is
    unassigned (<= 0), falls back to the most common valid WSNO among lake
    boundary pixels.
    """
    r, c = target_rc
    wsno = int(w_win[r, c])
    if wsno > 0:
        return wsno

    boundary_wsnos = [int(w_win[r, c]) for r, c in np.argwhere(lake_mask) if int(w_win[r, c]) > 0]
    if boundary_wsnos:
        return max(set(boundary_wsnos), key=boundary_wsnos.count)
    return wsno


def compute_override_breakout_path(target_rc, lake_mask, w_win, max_steps=MAX_OVERRIDE_BREAKOUT_STEPS):
    """
    Build a forced-exit path for manual override carve cases.

    When a user-supplied outlet coordinate does not snap to any clean stream
    exit, we place the outlet on the nearest shoreline pixel and carve flow
    through the lake wall:

      1. Find the 10 lake interior pixels closest to the target (shoreline) point.
      2. Their mean position approximates the local lake interior centroid.
      3. Step outward from the target along the vector away from that centroid.
      4. For each step outside lake_mask, record a (cell, parent) pair for FDR
         edits and check w_win (WSNO) to see if we have entered a different basin.
      5. Stop when a cell outside the lake has a valid WSNO different from the
         source basin, or when max_steps is reached.

    Returns:
        (breakout_path, succeeded) where breakout_path is a list of
        (current_rc, parent_rc) pairs and succeeded is True only if another
        basin was reached within max_steps.
    """
    target_r, target_c = target_rc
    ysize, xsize = lake_mask.shape
    interior = [
        (np.hypot(r - target_r, c - target_c), r, c)
        for r, c in np.argwhere(lake_mask)
        if (r, c) != target_rc
    ]
    if not interior:
        return [], False

    interior.sort(key=lambda item: item[0])
    centroid_r = np.mean([p[1] for p in interior[:10]])
    centroid_c = np.mean([p[2] for p in interior[:10]])
    dr_vec = target_r - centroid_r
    dc_vec = target_c - centroid_c
    vec_len = np.hypot(dr_vec, dc_vec)
    if vec_len == 0:
        return [], False

    source_wsno = _source_basin_wsno(target_rc, w_win, lake_mask)
    step_r, step_c = dr_vec / vec_len, dc_vec / vec_len
    curr_r, curr_c = float(target_r), float(target_c)
    breakout_path = []
    last_fixed_rc = target_rc

    for _ in range(1, max_steps + 1):
        curr_r += step_r
        curr_c += step_c
        next_r, next_c = int(np.round(curr_r)), int(np.round(curr_c))

        if not (0 <= next_r < ysize and 0 <= next_c < xsize):
            return breakout_path, False

        if not lake_mask[next_r, next_c]:
            breakout_path.append((last_fixed_rc, (next_r, next_c)))
            last_fixed_rc = (next_r, next_c)

            neighbor_wsno = int(w_win[next_r, next_c])
            if neighbor_wsno > 0 and neighbor_wsno != source_wsno:
                return breakout_path, True

    return breakout_path, False


def select_algorithmic_outlet(surviving_candidates):
    """
    Rank and return the best stream exit when no override carve is possible.

    Returns (chosen_outlet, selection_type, is_carved_override, breakout_path)
    or (None, ...) if there are no valid candidates.
    """
    if not surviving_candidates:
        return None, "skipped_isolated", False, []

    if len(surviving_candidates) == 1:
        return surviving_candidates[0], "algorithmic_stream", False, []

    ranked = sorted(surviving_candidates, key=lambda x: (
        x['gauge_dist'],
        x['doutend'],
        -x['local_accum'],
        -x['dscontarea'],
    ))
    return ranked[0], "algorithmic_stream", False, []


def select_outlet_for_lake(
    lake_id, surviving_candidates, overrides_gdf, boundary_pixels,
    lake_mask, w_win, cell_size,
):
    """
    Choose one outlet pixel for a lake using a tiered decision pipeline.

    Priority order:

    1. MANUAL OVERRIDE (if lake_id appears in outlet_overrides.csv):
       a. override_snapped  - override point is within 3 cells of a surviving
          stream exit; snap to the nearest clean exit.
       b. override_carved   - no nearby clean exit; place outlet on nearest
          shoreline pixel and carve outward until w_raster shows a different
          basin WSNO (up to MAX_OVERRIDE_BREAKOUT_STEPS cells).
       c. override_carve_failed - carve did not reach another basin; fall back
          to algorithmic stream selection for this lake.
       d. skipped_no_boundary - degenerate lake with no shoreline pixels; skip.

    2. ALGORITHMIC STREAM SELECTION (no override, or carve fallback):
       a. skipped_isolated  - lake has no valid stream exits; skip entirely.
       b. Single candidate  - use it directly.
       c. Multiple candidates - rank by gauge_dist, doutend, local_accum, dscontarea.

    Returns: (chosen_outlet_dict, selection_type, is_carved_override, breakout_path)
    """
    lake_override = overrides_gdf[overrides_gdf['lake_id'] == lake_id]
    if not lake_override.empty:
        override_pt = lake_override.iloc[0].geometry
        snap_threshold = cell_size * 3.0
        nearby = sorted(
            (c for c in surviving_candidates if c['point'].distance(override_pt) <= snap_threshold),
            key=lambda c: c['point'].distance(override_pt),
        )
        if nearby:
            print(f"Lake {lake_id}: Override coordinate snapped to a cleaned exiting branch.")
            return nearby[0], "override_snapped", False, []

        if not boundary_pixels:
            return None, "skipped_no_boundary", False, []

        closest = min(boundary_pixels, key=lambda item: item['point'].distance(override_pt))
        chosen = {'win_rc': closest['win_rc'], 'point': closest['point'], 'link_no': -1, 'local_accum': -1}
        print(f"Lake {lake_id}: Override isolated from clean branches. Carving toward adjacent basin...")
        breakout, succeeded = compute_override_breakout_path(chosen['win_rc'], lake_mask, w_win)
        if succeeded:
            return chosen, "override_carved", True, breakout

        print(
            f"Lake {lake_id}: Override carve did not reach another basin within "
            f"{MAX_OVERRIDE_BREAKOUT_STEPS} cells; using algorithmic outlet selection."
        )
        return select_algorithmic_outlet(surviving_candidates)

    return select_algorithmic_outlet(surviving_candidates)


def load_overrides(overrides_csv_path, raster_proj):
    """Load optional manual outlet overrides (lat/lon per lake_id) and reproject."""
    if not os.path.exists(overrides_csv_path):
        return gpd.GeoDataFrame(columns=['lake_id', 'lat', 'lon', 'geometry'], crs="EPSG:4326")

    overrides_df = pd.read_csv(overrides_csv_path)
    overrides_df['lake_id'] = overrides_df['lake_id'].astype(str).str.strip()
    geometry = gpd.points_from_xy(overrides_df['lon'], overrides_df['lat'])
    return gpd.GeoDataFrame(overrides_df, geometry=geometry, crs="EPSG:4326").to_crs(raster_proj)


def process_raster_reservoir_routing(
    fdr_raster_path,
    src_raster_path,
    accum_raster_path,
    w_raster_path,
    streams_vector_path,
    lakes_vector_path,
    gauges_vector_path,
    overrides_csv_path,
    output_fdr_path,
    output_outlets_path,
    gauge_radius_meters=750,
):
    """
    Main entry point: loop over all lakes and patch the flow-direction raster.

    Workflow per lake:
      1. Read a raster window around the lake bbox.
      2. Rasterize the lake polygon -> lake_mask; erode to get shoreline.
      3. Find stream exit candidates on the shoreline.
      4. Filter upstream duplicates.
      5. Select one outlet (override or algorithmic).
      6. Run centerline routing to rewrite FDR inside the lake.
      7. Apply breakout carve edits if needed.
      8. Write the patched window back into the output GeoTIFF.

    The output file is a full copy of the input FDR raster with only lake
    interiors modified. All non-lake cells are untouched.
    """
    wsno_to_link, link_to_dout, link_to_accum, link_to_downstream = build_vector_lookup_tables(streams_vector_path)

    lakes = gpd.read_file(lakes_vector_path)
    gauges = gpd.read_file(gauges_vector_path)
    total_lakes = len(lakes)

    # Start from a full copy of the TauDEM FDR raster so we can patch windows
    # in-place without affecting cells outside any lake.
    if os.path.exists(output_fdr_path):
        try:
            os.remove(output_fdr_path)
        except OSError:
            pass

    print("Creating mutable working copy of flow directions dataset...")
    driver = gdal.GetDriverByName("GTiff")
    src_ds_fdr = gdal.Open(fdr_raster_path)
    co_options = ["COMPRESS=LZW", "TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256"]
    out_ds = driver.CreateCopy(output_fdr_path, src_ds_fdr, strict=0, options=co_options)
    out_ds = None

    ds_fdr = gdal.Open(output_fdr_path, gdal.GA_Update)
    ds_src = gdal.Open(src_raster_path)
    ds_acc = gdal.Open(accum_raster_path)
    ds_w = gdal.Open(w_raster_path)

    fdr_band = ds_fdr.GetRasterBand(1)
    src_band = ds_src.GetRasterBand(1)
    acc_band = ds_acc.GetRasterBand(1)
    w_band = ds_w.GetRasterBand(1)

    gt = ds_fdr.GetGeoTransform()
    inv_gt = gdal.InvGeoTransform(gt)
    raster_proj = ds_fdr.GetProjection()
    raster_size = (ds_fdr.RasterXSize, ds_fdr.RasterYSize)
    cell_size = abs(gt[1])

    lakes = lakes.to_crs(raster_proj)
    gauges = gauges.to_crs(raster_proj)
    overrides_gdf = load_overrides(overrides_csv_path, raster_proj)

    print("\nBeginning processing run...")
    print("-" * 90)

    # Collected for optional outlet shapefile export (not currently written).
    outlet_records = []
    processed_count = 0

    for idx, lake in lakes.iterrows():
        processed_count += 1
        if processed_count % 50 == 0 or processed_count == total_lakes:
            print(f"Progress: {processed_count}/{total_lakes} reservoirs processed.")

        lake_id = str(lake.get('Hylak_id', idx)).strip()

        # --- Extract raster window for this lake ---
        window = raster_window_from_bounds(lake.geometry.bounds, inv_gt, raster_size)
        if window is None:
            continue
        xoff, yoff, xsize, ysize = window

        fdr_win = fdr_band.ReadAsArray(xoff, yoff, xsize, ysize)
        src_win = src_band.ReadAsArray(xoff, yoff, xsize, ysize)
        acc_win = acc_band.ReadAsArray(xoff, yoff, xsize, ysize)
        w_win = w_band.ReadAsArray(xoff, yoff, xsize, ysize)

        # --- Build lake mask and shoreline ---
        lake_mask = rasterize_polygon_mask(lake.geometry, gt, xoff, yoff, xsize, ysize, raster_proj)
        # 3x3 erosion: cells that survive are interior; boundary = lake minus interior.
        eroded_mask = binary_erosion(lake_mask, structure=np.ones((3, 3), dtype=bool))
        boundary_mask = lake_mask & ~eroded_mask

        boundary_pixels = [
            {'win_rc': (r, c), 'point': pixel_to_point(gt, xoff, yoff, r, c)}
            for r, c in np.argwhere(boundary_mask)
        ]

        # --- Outlet selection pipeline ---
        preliminary_outlets = find_stream_exit_candidates(
            boundary_mask, src_win, fdr_win, lake_mask, acc_win, w_win,
            gt, xoff, yoff, wsno_to_link, link_to_dout, link_to_accum, gauges, gauge_radius_meters,
        )
        surviving_candidates = filter_upstream_duplicates(preliminary_outlets, link_to_downstream)

        chosen_outlet, selection_type, is_carved_override, breakout_path = select_outlet_for_lake(
            lake_id, surviving_candidates, overrides_gdf, boundary_pixels, lake_mask, w_win, cell_size,
        )
        if chosen_outlet is None:
            continue  # isolated lake or degenerate geometry; leave FDR unchanged

        outlet_records.append({
            'lake_id': lake_id,
            'link_no': int(chosen_outlet.get('link_no', -1)),
            'sel_type': selection_type,
            'local_acc': float(chosen_outlet.get('local_accum', -1)),
            'geometry': chosen_outlet['point'],
        })

        # --- Rewrite flow directions inside the lake ---
        updated_fdr_win = route_centerline_to_target(
            fdr_win,
            lake_mask,
            chosen_outlet['win_rc'],
            lock_target_value=not is_carved_override,
        )

        # For carved overrides, overwrite shoreline cells along the breakout
        # path so flow punches through the lake wall to the outside raster.
        if is_carved_override and breakout_path:
            for current_node, parent_node in breakout_path:
                cr, cc = current_node
                updated_fdr_win[cr, cc] = get_d8_direction(current_node, parent_node)

        fdr_band.WriteArray(updated_fdr_win, xoff, yoff)

    fdr_band.FlushCache()
    ds_fdr = None
    ds_src = None
    ds_acc = None
    ds_w = None
    print("-" * 90)
    print("Process complete. Continuous instream backbones written successfully.")


if __name__ == "__main__":
    process_raster_reservoir_routing(
        fdr_raster_path="./taudem-interim-files/d8/stream-network_elv-fdir.tif",
        src_raster_path="./taudem-interim-files/d8/stream-network_elv-src.tif",
        accum_raster_path="./taudem-interim-files/d8/stream-network_elv-ad8.tif",
        w_raster_path="./taudem-interim-files/d8/original-delineated-watersheds.tif",
        streams_vector_path="./delineation-product/original-delineated-streams.shp",
        lakes_vector_path="./lakes/filtered_lakes.shp",
        gauges_vector_path="./points/gauges_in_basin.shp",
        overrides_csv_path="./outlet_overrides.csv",
        output_fdr_path="./taudem-interim-files/d8/fdr_centerline_all.tif",
        output_outlets_path="./points/selected_outlets.shp",
        gauge_radius_meters=750,
    )
