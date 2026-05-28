import os
import heapq
import numpy as np
import pandas as pd
import geopandas as gpd
from osgeo import gdal, ogr, osr
from shapely.geometry import Point
from scipy.ndimage import distance_transform_edt, binary_erosion
from collections import defaultdict

gdal.UseExceptions()

# ==============================================================================
# 1. TOPOLOGY DICTIONARY BUILDER
# ==============================================================================
def build_vector_lookup_tables(streams_path):
    print("Pre-building vector attribute lookup maps...")
    gdf = gpd.read_file(streams_path)
    
    wsno_to_link = dict(zip(gdf['WSNO'], gdf['LINKNO']))
    link_to_dout = dict(zip(gdf['LINKNO'], gdf['DOUTEND']))
    link_to_accum = dict(zip(gdf['LINKNO'], gdf['DSContArea']))
    link_to_downstream = dict(zip(gdf['LINKNO'], gdf['DSLINKNO']))
    
    return wsno_to_link, link_to_dout, link_to_accum, link_to_downstream

# ==============================================================================
# 2. HELPER D8 FUNCTIONS
# ==============================================================================
def get_d8_offset(fdr_val):
    """Maps TauDEM/Standard D8 flow codes to (delta_row, delta_col) offsets."""
    D8_OFFSETS = {
        1: (0, 1),   # East
        2: (-1, 1),  # Northeast
        3: (-1, 0),  # North
        4: (-1, -1), # Northwest
        5: (0, -1),  # West
        6: (1, -1),  # Southwest
        7: (1, 0),   # South
        8: (1, 1)    # Southeast
    }
    return D8_OFFSETS.get(int(fdr_val), (0, 0))

def get_d8_direction(current_rc, parent_rc):
    """Calculates D8 value from current pixel pointing toward parent pixel."""
    dr = parent_rc[0] - current_rc[0]
    dc = parent_rc[1] - current_rc[1]
    
    if dr != 0: dr = int(np.sign(dr))
    if dc != 0: dc = int(np.sign(dc))
        
    D8_REVERSE = {
        (0, 1): 1, (-1, 1): 2, (-1, 0): 3, (-1, -1): 4,
        (0, -1): 5, (1, -1): 6, (1, 0): 7, (1, 1): 8
    }
    return D8_REVERSE.get((dr, dc), 0)

# ==============================================================================
# 3. CENTERLINE ROUTING ENGINE (TWO-PASS SPINE ENGINE FROM CODE 1)
# ==============================================================================
def route_centerline_to_target(fdr_win, lake_mask, target_rc, lock_target_value=False):
    h, w = fdr_win.shape
    r0, c0 = target_rc
    
    original_target_fdr = int(fdr_win[r0, c0])
    bank_dist = distance_transform_edt(lake_mask)
    max_bank_dist = np.max(bank_dist) if np.max(bank_dist) > 0 else 1.0
    
    normalized_trough = (max_bank_dist - bank_dist) / max_bank_dist
    centerline_penalty = (normalized_trough ** 4) * 500.0
    
    D8_DIRS = [(0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1)]
    
    deepest_idx = np.argmax(bank_dist)
    dr_deep, dc_deep = divmod(deepest_idx, w)
    
    spine_dist = np.full((h, w), np.inf)
    spine_parent = np.full((h, w), -1, dtype=int)
    
    pq_spine = [(0.0, dr_deep, dc_deep)]
    spine_dist[dr_deep, dc_deep] = 0.0
    
    while pq_spine:
        curr_d, r, c = heapq.heappop(pq_spine)
        if (r, c) == (r0, c0):
            break
        if curr_d > spine_dist[r, c]:
            continue
            
        for dr_move, dc_move in D8_DIRS:
            nr, nc = r + dr_move, c + dc_move
            if 0 <= nr < h and 0 <= nc < w and lake_mask[nr, nc]:
                step_w = np.sqrt(dr_move**2 + dc_move**2)
                cost = 0.1 + centerline_penalty[nr, nc]
                new_d = curr_d + (cost * step_w)
                
                if new_d < spine_dist[nr, nc]:
                    spine_dist[nr, nc] = new_d
                    spine_parent[nr, nc] = r * w + c
                    heapq.heappush(pq_spine, (new_d, nr, nc))
    
    backbone_cells = set()
    backbone_cells.add((r0, c0))
    trace_r, trace_c = r0, c0
    
    while True:
        flat_p = spine_parent[trace_r, trace_c]
        if flat_p == -1:
            break
        trace_r, trace_c = divmod(flat_p, w)
        backbone_cells.add((trace_r, trace_c))
        if (trace_r, trace_c) == (dr_deep, dc_deep):
            break

    dist_grid = np.full((h, w), np.inf)
    parent_r = np.full_like(lake_mask, -1, dtype=int)
    parent_c = np.full_like(lake_mask, -1, dtype=int)
    
    pq = [(0.0, r0, c0)]
    dist_grid[r0, c0] = 0.0
    
    while pq:
        curr_dist, r, c = heapq.heappop(pq)
        if curr_dist > dist_grid[r, c]:
            continue
            
        for dr_move, dc_move in D8_DIRS:
            nr, nc = r + dr_move, c + dc_move
            if 0 <= nr < h and 0 <= nc < w and lake_mask[nr, nc]:
                if (nr, nc) == (r0, c0):
                    continue
                
                step_weight = np.sqrt(dr_move**2 + dc_move**2)
                
                if (nr, nc) in backbone_cells:
                    cell_cost = 0.01  
                else:
                    cell_cost = 10.0 + centerline_penalty[nr, nc]
                
                new_dist = curr_dist + (cell_cost * step_weight)
                
                if new_dist < dist_grid[nr, nc]:
                    dist_grid[nr, nc] = new_dist
                    parent_r[nr, nc] = r
                    parent_c[nr, nc] = c
                    heapq.heappush(pq, (new_dist, nr, nc))
                    
    updated_fdr = fdr_win.copy()
    for r in range(h):
        for c in range(w):
            if (r, c) == (r0, c0):
                continue
            if parent_r[r, c] >= 0:
                updated_fdr[r, c] = get_d8_direction((r, c), (parent_r[r, c], parent_c[r, c]))
    
    if lock_target_value:
        updated_fdr[r0, c0] = original_target_fdr
                
    return updated_fdr

# ==============================================================================
# 4. CORE PROCESSING PIPELINE
# ==============================================================================
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
    gauge_radius_meters=750
):
    wsno_to_link, link_to_dout, link_to_accum, link_to_downstream = build_vector_lookup_tables(streams_vector_path)
    
    lakes = gpd.read_file(lakes_vector_path)
    gauges = gpd.read_file(gauges_vector_path)
    total_lakes = len(lakes)
    
    if os.path.exists(output_fdr_path):
        try: os.remove(output_fdr_path)
        except OSError: pass

    print("Creating mutable working copy of flow directions dataset...")
    driver = gdal.GetDriverByName("GTiff")
    src_ds_fdr = gdal.Open(fdr_raster_path)
    co_options = ["COMPRESS=LZW", "TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256"]
    out_ds = driver.CreateCopy(output_fdr_path, src_ds_fdr, strict=0, options=co_options)
    out_ds = None 

    ds_fdr = gdal.Open(output_fdr_path, gdal.GA_Update)
    ds_src = gdal.Open(src_raster_path)
    ds_acc = gdal.Open(accum_raster_path)
    ds_w   = gdal.Open(w_raster_path)
    
    fdr_band = ds_fdr.GetRasterBand(1)
    src_band = ds_src.GetRasterBand(1)
    acc_band = ds_acc.GetRasterBand(1)
    w_band   = ds_w.GetRasterBand(1)
    
    gt = ds_fdr.GetGeoTransform()
    inv_gt = gdal.InvGeoTransform(gt)
    raster_proj = ds_fdr.GetProjection()
    
    lakes = lakes.to_crs(raster_proj)
    gauges = gauges.to_crs(raster_proj)
    
    if os.path.exists(overrides_csv_path):
        overrides_df = pd.read_csv(overrides_csv_path)
        overrides_df['lake_id'] = overrides_df['lake_id'].astype(str).str.strip()
        geometry = gpd.points_from_xy(overrides_df['lon'], overrides_df['lat'])
        overrides_gdf = gpd.GeoDataFrame(overrides_df, geometry=geometry, crs="EPSG:4326").to_crs(raster_proj)
    else:
        overrides_gdf = gpd.GeoDataFrame(columns=['lake_id', 'lat', 'lon', 'geometry'], crs="EPSG:4326")

    print("\nBeginning processing run...")
    print("-" * 90)

    def is_link_upstream_of(link_a, link_b):
        """Returns True if link_a physically flows into link_b downstream."""
        nxt = link_to_downstream.get(link_a, -1)
        while nxt != -1 and nxt != 0:
            if nxt == link_b:
                return True
            nxt = link_to_downstream.get(nxt, -1)
        return False

    outlet_records = []
    processed_count = 0
    
    for idx, lake in lakes.iterrows():
        processed_count += 1
        if processed_count % 50 == 0 or processed_count == total_lakes:
            print(f"Progress: {processed_count}/{total_lakes} reservoirs processed.")
            
        lake_id = str(lake.get('Hylak_id', idx)).strip()
        geom = lake.geometry
        
        min_x, min_y, max_x, max_y = geom.bounds
        px_min, py_max = gdal.ApplyGeoTransform(inv_gt, min_x, min_y)
        px_max, py_min = gdal.ApplyGeoTransform(inv_gt, max_x, max_y)
        
        xoff = int(np.floor(min(px_min, px_max)))
        yoff = int(np.floor(min(py_min, py_max)))
        xsize = int(np.ceil(max(px_min, px_max)) - xoff) + 1
        ysize = int(np.ceil(max(py_min, py_max)) - yoff) + 1
        
        if xoff < 0 or yoff < 0 or (xoff + xsize) > ds_fdr.RasterXSize or (yoff + ysize) > ds_fdr.RasterYSize:
            continue
            
        fdr_win = fdr_band.ReadAsArray(xoff, yoff, xsize, ysize)
        src_win = src_band.ReadAsArray(xoff, yoff, xsize, ysize)  
        acc_win = acc_band.ReadAsArray(xoff, yoff, xsize, ysize)  
        w_win   = w_band.ReadAsArray(xoff, yoff, xsize, ysize)
        
        # Build regional lake mask
        mem_driver = gdal.GetDriverByName('MEM')
        mem_ds = mem_driver.Create('', xsize, ysize, 1, gdal.GDT_Byte)
        mem_ds.SetGeoTransform([gt[0] + xoff*gt[1], gt[1], gt[2], gt[3] + yoff*gt[5], gt[4], gt[5]])
        
        ogr_ds = ogr.GetDriverByName('Memory').CreateDataSource('wrk')
        srs = osr.SpatialReference()
        srs.ImportFromWkt(raster_proj)
        ogr_lyr = ogr_ds.CreateLayer('poly', srs, geom_type=ogr.wkbPolygon)
        feat = ogr.Feature(ogr_lyr.GetLayerDefn())
        feat.SetGeometry(ogr.CreateGeometryFromWkt(geom.wkt))
        ogr_lyr.CreateFeature(feat)
        
        gdal.RasterizeLayer(mem_ds, [1], ogr_lyr, burn_values=[1], options=["ALL_TOUCHED=TRUE"])
        lake_mask = mem_ds.GetRasterBand(1).ReadAsArray()
        mem_ds = None; ogr_ds = None
        
        eroded_mask = binary_erosion(lake_mask.astype(bool), structure=np.ones((3,3), dtype=bool))
        boundary_mask = (lake_mask == 1) & (~eroded_mask)
        
        src_intersections = np.argwhere((boundary_mask == 1) & (src_win == 1))
        
        preliminary_outlets = []
        all_boundary_pixels = []
        
        for r, c in np.argwhere(boundary_mask == 1):
            gx = gt[0] + (xoff + c) * gt[1] + (yoff + r) * gt[2]
            gy = gt[3] + (xoff + c) * gt[4] + (yoff + r) * gt[5]
            all_boundary_pixels.append({'win_rc': (r, c), 'point': Point(gx, gy)})

        # Isolate true stream-exiting cells
        for r, c in src_intersections:
            dr, dc = get_d8_offset(fdr_win[r, c])
            nr, nc = r + dr, c + dc
            
            is_exiting = False
            if not (0 <= nr < ysize and 0 <= nc < xsize) or lake_mask[nr, nc] == 0:
                is_exiting = True
                
            if is_exiting:
                gx = gt[0] + (xoff + c) * gt[1] + (yoff + r) * gt[2]
                gy = gt[3] + (xoff + c) * gt[4] + (yoff + r) * gt[5]
                pt = Point(gx, gy)
                
                pixel_wsno = w_win[r, c]
                link_no = wsno_to_link.get(pixel_wsno, -1)
                doutend = link_to_dout.get(link_no, float('inf'))
                dscontarea = link_to_accum.get(link_no, 0)
                
                g_distances = gauges.distance(pt)
                valid_g = g_distances[g_distances <= gauge_radius_meters]
                gauge_dist = valid_g.min() if not valid_g.empty else float('inf')
                
                preliminary_outlets.append({
                    'win_rc': (r, c),
                    'point': pt,
                    'link_no': link_no,
                    'doutend': doutend,
                    'dscontarea': dscontarea,
                    'local_accum': acc_win[r, c],
                    'gauge_dist': gauge_dist
                })
                
        chosen_outlet = None
        external_breakout_path = [] 
        is_carved_override = False 
        selection_type = "algorithmic_stream"
        
        # ==============================================================================
        # NEW STRATIFIED PIPELINE ORDER WITH ADDED CENTERLINE ROUTING
        # ==============================================================================
        
        # --- TIER 1: FILTER DOWN TO THE FURHEAD DOWNSTREAM CELL PER NETWORK ---
        surviving_candidates = []
        if preliminary_outlets:
            for i, cand_a in enumerate(preliminary_outlets):
                link_a = cand_a['link_no']
                is_upstream_duplicate = False
                
                for j, cand_b in enumerate(preliminary_outlets):
                    if i == j:
                        continue
                    link_b = cand_b['link_no']
                    
                    if link_a != -1 and link_b != -1:
                        if is_link_upstream_of(link_a, link_b):
                            is_upstream_duplicate = True
                            break
                        if link_a == link_b and cand_b['local_accum'] > cand_a['local_accum']:
                            is_upstream_duplicate = True
                            break
                            
                if not is_upstream_duplicate:
                    surviving_candidates.append(cand_a)

        # --- INTERMEDIATE LAYER: OVERRIDE EVALUATION ---
        lake_ov = overrides_gdf[overrides_gdf['lake_id'] == lake_id]
        
        if not lake_ov.empty:
            ov_pt = lake_ov.iloc[0].geometry
            cell_size = abs(gt[1])
            snap_threshold_meters = cell_size * 3.0  
            
            nearby_outlets = [out for out in surviving_candidates if out['point'].distance(ov_pt) <= snap_threshold_meters]
            
            if nearby_outlets:
                nearby_outlets.sort(key=lambda x: x['point'].distance(ov_pt))
                chosen_outlet = nearby_outlets[0]
                selection_type = "override_snapped"
                print(f"Lake {lake_id}: Override coordinate snapped to a cleaned exiting branch.")
            else:
                closest_boundary = min(all_boundary_pixels, key=lambda x: x['point'].distance(ov_pt))
                chosen_outlet = {'win_rc': closest_boundary['win_rc'], 'point': closest_boundary['point'], 'link_no': -1, 'local_accum': -1}
                is_carved_override = True
                selection_type = "override_carved"
                print(f"Lake {lake_id}: Override isolated from clean branches. Calculating forced breakout vectors...")
                
                target_r, target_c = chosen_outlet['win_rc']
                lake_indices = np.argwhere(lake_mask == 1)
                local_interior_pixels = []
                for r, c in lake_indices:
                    if (r != target_r or c != target_c):
                        dist = np.sqrt((r - target_r)**2 + (c - target_c)**2)
                        local_interior_pixels.append((dist, r, c))
                
                local_interior_pixels.sort(key=lambda x: x[0])
                top_10_inward = local_interior_pixels[:10]
                
                if top_10_inward:
                    centroid_r = np.mean([p[1] for p in top_10_inward])
                    centroid_c = np.mean([p[2] for p in top_10_inward])
                    dr_vec = target_r - centroid_r
                    dc_vec = target_c - centroid_c
                    vec_len = np.sqrt(dr_vec**2 + dc_vec**2)
                    step_r = dr_vec / vec_len if vec_len > 0 else 0.0
                    step_c = dc_vec / vec_len if vec_len > 0 else 0.0
                    
                    curr_r_float, curr_c_float = float(target_r), float(target_c)
                    last_fixed_rc = (target_r, target_c)
                    
                    for step_idx in range(1, 4):
                        curr_r_float += step_r
                        curr_c_float += step_c
                        next_r, next_c = int(np.round(curr_r_float)), int(np.round(curr_c_float))
                        if 0 <= next_r < ysize and 0 <= next_c < xsize:
                            if lake_mask[next_r, next_c] == 0:
                                external_breakout_path.append((last_fixed_rc, (next_r, next_c)))
                                last_fixed_rc = (next_r, next_c)

        # --- TIER 2: CHOOSE BETWEEN TOTALLY DISTINCT, COMPETITIVE BRANCHES ---
        if chosen_outlet is None:
            if not surviving_candidates:
                # SAFE EXIT FOR ISOLATED LAKES: Skip processing entirely if not on a stream network
                continue
                
            if len(surviving_candidates) == 1:
                chosen_outlet = surviving_candidates[0]
            else:
                # Code 2 Sorting Preference Logic
                surviving_candidates.sort(key=lambda x: (
                    x['gauge_dist'],        # Priority 1: Gauge proximity match
                    x['doutend'],           # Priority 2: Closest overall distance to absolute basin edge
                    -x['local_accum'],      # Priority 3: Localized core pixel intensity count
                    -x['dscontarea']        # Priority 4: Macro catchment drainage area context
                ))
                chosen_outlet = surviving_candidates[0]

        # Save record metrics
        outlet_records.append({
            'lake_id': lake_id,
            'link_no': int(chosen_outlet.get('link_no', -1)),
            'sel_type': selection_type,
            'local_acc': float(chosen_outlet.get('local_accum', -1)),
            'geometry': chosen_outlet['point']
        })
        
        # ==============================================================================
        # WRITE OUT ROUTING RE-ALIGNMENTS TO THE RASTER GEOTIFF MATRIX
        # ==============================================================================
        target_rc = chosen_outlet['win_rc']
        lock_target_value = not is_carved_override
        
        # Run Dijkstra Spine Engine to route the interior pixels of the instream lake
        updated_fdr_win = route_centerline_to_target(
            fdr_win, 
            lake_mask, 
            target_rc, 
            lock_target_value=lock_target_value
        )
        
        # Inject custom directional changes for manual override exit wall carves
        if is_carved_override and external_breakout_path:
            for current_node, parent_node in external_breakout_path:
                cr, cc = current_node
                updated_fdr_win[cr, cc] = get_d8_direction(current_node, parent_node)
        
        # Commit updates directly back to the GeoTIFF on disk
        fdr_band.WriteArray(updated_fdr_win, xoff, yoff)
        
    fdr_band.FlushCache()
    ds_fdr = None; ds_src = None; ds_acc = None; ds_w = None
    print("-" * 90)
    print("Process complete. Continuous instream backbones written successfully.")

    # ==============================================================================
    # EXPORT GIS LAYER
    # ==============================================================================
    # if outlet_records:
    #     print(f"\nCompiling and exporting {len(outlet_records)} chosen outlet pour points...")
    #     outlets_gdf = gpd.GeoDataFrame(outlet_records, crs=raster_proj)
    #     os.makedirs(os.path.dirname(output_outlets_path), exist_ok=True)
    #     outlets_gdf.to_file(output_outlets_path)
    #     print(f"Successfully generated outlet shapefile at: {output_outlets_path}")


if __name__ == "__main__":
    process_raster_reservoir_routing(
        fdr_raster_path="./taudem-interim-files/d8/stream-network_elv-fdir_sask.tif",
        src_raster_path="./taudem-interim-files/d8/stream-network_elv-src.tif",
        accum_raster_path="./taudem-interim-files/d8/stream-network_elv-ad8.tif", 
        w_raster_path="./taudem-interim-files/d8/original-delineated-watersheds.tif",
        streams_vector_path="./delineation-product/original-delineated-streams.shp",
        lakes_vector_path="./lakes/filtered_lakes.shp",
        gauges_vector_path="./points/gauges_in_basin.shp",
        overrides_csv_path="./outlet_overrides.csv",
        output_fdr_path="./taudem-interim-files/d8/fdr_centerline_all.tif",
        output_outlets_path="./points/selected_outlets.shp",
        gauge_radius_meters=750
    )