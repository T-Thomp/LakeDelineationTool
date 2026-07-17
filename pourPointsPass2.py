import os
import numpy as np
import pandas as pd
import geopandas as gpd
from osgeo import gdal, ogr, osr
from shapely.geometry import Point
from scipy.ndimage import binary_erosion

# Enable GDAL exceptions to catch raster I/O issues cleanly
gdal.UseExceptions()

def load_gauges(gauges_path, target_crs):
    """Load gauge points, or return an empty frame if the file is missing."""
    if not os.path.exists(gauges_path):
        print(
            f"No gauge file found at {gauges_path}; "
            "skipping gauge pour points."
        )
        return gpd.GeoDataFrame(columns=['name', 'point_type', 'geometry'], crs=target_crs)
    return gpd.read_file(gauges_path)


def build_vector_lookup_tables(streams_path):
    """
    Pre-builds dictionaries for fast tracking of stream relationships
    using the vector stream network network attributes.
    """
    print("Pre-building vector attribute lookup maps...")
    gdf = gpd.read_file(streams_path)
    wsno_to_link = dict(zip(gdf['WSNO'], gdf['LINKNO']))
    link_to_downstream = dict(zip(gdf['LINKNO'], gdf['DSLINKNO']))
    return wsno_to_link, link_to_downstream

def get_d8_offset(fdr_val):
    """Maps TauDEM D8 flow codes to (delta_row, delta_col) offsets."""
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

# TauDEM reverse checking mapping for neighbor-to-cell inflow validation
REVERSE_D8 = {
    (0, 1): 5,   # Neighbor is East, must flow West
    (-1, 1): 6,  # Neighbor is NE, must flow SW
    (-1, 0): 7,  # Neighbor is North, must flow South
    (-1, -1): 8, # Neighbor is NW, must flow SE
    (0, -1): 1,  # Neighbor is West, must flow East
    (1, -1): 2,  # Neighbor is SW, must flow NE
    (1, 0): 3,   # Neighbor is South, must flow North
    (1, 1): 4    # Neighbor is SE, must flow NW
}

def extract_reservoir_io_points(paths):
    print("\n" + "="*40 + "\nTAUDEM RASTER POUR POINT EXTRACTION\n" + "="*40)
    
    # [1/5] Coordinate System & Data Setup
    print("\n[1/5] Loading datasets (Vectors & Rasters)...")
    streams_gdf = gpd.read_file(paths["river"])
    target_crs_wkt = streams_gdf.crs.to_wkt()
    
    wsno_to_link, link_to_downstream = build_vector_lookup_tables(paths["river"])
    lakes = gpd.read_file(paths["lakes"])
    gauges = load_gauges(paths["gauges"], streams_gdf.crs)
    
    ds_fdr = gdal.Open(paths["fdr"])
    ds_src = gdal.Open(paths["stream"])
    ds_w   = gdal.Open(paths["w_raster"])
    ds_fac = gdal.Open(paths["fac"]) 
    
    fdr_band = ds_fdr.GetRasterBand(1)
    src_band = ds_src.GetRasterBand(1)
    w_band   = ds_w.GetRasterBand(1)
    fac_band = ds_fac.GetRasterBand(1)
    
    gt = ds_fdr.GetGeoTransform()
    inv_gt = gdal.InvGeoTransform(gt)
    raster_proj = ds_fdr.GetProjection()
    
    lakes = lakes.to_crs(raster_proj)

    src_srs = osr.SpatialReference()
    src_srs.ImportFromWkt(raster_proj)
    
    tgt_srs = osr.SpatialReference()
    tgt_srs.ImportFromWkt(target_crs_wkt)
    
    src_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    tgt_srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    
    raster_to_stream_transform = osr.CoordinateTransformation(src_srs, tgt_srs)

    final_lake_records = []

    # [2/5] Processing Boundary Intersections & Localized Graph Logic
    print("\n[2/5] Identifying and filtering candidate points...")
    for idx, lake in lakes.iterrows():
        lake_id = str(lake.get('Hylak_id', idx)).strip()
        geom = lake.geometry
        
        min_x, min_y, max_x, max_y = geom.bounds
        px_min, py_max = gdal.ApplyGeoTransform(inv_gt, min_x, min_y)
        px_max, py_min = gdal.ApplyGeoTransform(inv_gt, max_x, max_y)
        
        raw_xoff = int(np.floor(min(px_min, px_max)))
        raw_yoff = int(np.floor(min(py_min, py_max)))
        raw_xsize = int(np.ceil(max(px_min, px_max)) - raw_xoff) + 1
        raw_ysize = int(np.ceil(max(py_min, py_max)) - raw_yoff) + 1
        
        PADDING = 6  
        xoff = max(0, raw_xoff - PADDING)
        yoff = max(0, raw_yoff - PADDING)
        
        xsize = min(ds_fdr.RasterXSize - xoff, raw_xsize + (raw_xoff - xoff) + PADDING)
        ysize = min(ds_fdr.RasterYSize - yoff, raw_ysize + (raw_yoff - yoff) + PADDING)
        
        if xsize <= 0 or ysize <= 0:
            continue
            
        fdr_win = fdr_band.ReadAsArray(xoff, yoff, xsize, ysize)
        src_win = src_band.ReadAsArray(xoff, yoff, xsize, ysize)
        w_win   = w_band.ReadAsArray(xoff, yoff, xsize, ysize)
        fac_win = fac_band.ReadAsArray(xoff, yoff, xsize, ysize)
        
        # Build local lake mask using an in-memory dataset
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
        
        # Binary erosion extracts a perfect 1-pixel perimeter ring
        eroded_mask = binary_erosion(lake_mask.astype(bool), structure=np.ones((3,3), dtype=bool))
        boundary_mask = (lake_mask == 1) & (~eroded_mask)
        
        boundary_pixels = np.argwhere(boundary_mask == 1)
        
        candidate_outflows = []
        inflow_candidates = []
        
        # Evaluate preliminary intersection points against direct D8 directional logic
        for r, c in boundary_pixels:
            if src_win[r, c] != 1:
                continue
                
            basin_id = w_win[r, c]
            link_no = wsno_to_link.get(basin_id, -1)
            if link_no == -1:
                continue
                
            # 1. OUTLET FILTERING: Check exactly 1 cell downstream via D8
            fdr_val = fdr_win[r, c]
            dr, dc = get_d8_offset(fdr_val)
            ds_r, ds_c = r + dr, c + dc
            
            if 0 <= ds_r < ysize and 0 <= ds_c < xsize:
                # If the immediate next downstream cell is outside the lake, save as a temporary candidate
                if lake_mask[ds_r, ds_c] == 0:
                    out_node = {'win_rc': (r, c), 'link_no': link_no, 'fac': fac_win[r, c]}
                    candidate_outflows.append(out_node)
            
            # 2. INFLOW FILTERING: Check if a neighboring external stream is flowing into it
            for (nbr_dr, nbr_dc), required_fdr in REVERSE_D8.items():
                nbr_r, nbr_c = r + nbr_dr, c + nbr_dc
                if 0 <= nbr_r < ysize and 0 <= nbr_c < xsize:
                    if (src_win[nbr_r, nbr_c] == 1 and 
                        lake_mask[nbr_r, nbr_c] == 0 and 
                        fdr_win[nbr_r, nbr_c] == required_fdr):
                        
                        inflow_candidates.append({'win_rc': (r, c), 'link_no': link_no, 'fac': fac_win[r, c]})
                        break 

        # =====================================================================
        # [3/5] Upstream/Downstream Network Tracing & Resolution
        # =====================================================================
        
        # --- 3.1 INFLOW RESOLUTION (Most Upstream Only) ---
        final_inflows = []
        for cand in inflow_candidates:
            is_farthest_us = True
            current_link = cand['link_no']
            
            for other in inflow_candidates:
                if other['link_no'] == current_link:
                    continue
                
                # Trace from the "other" candidate downwards to see if it leads to our current link
                trace_link = link_to_downstream.get(other['link_no'], -1)
                while trace_link != -1:
                    if trace_link == current_link:
                        is_farthest_us = False  # The other candidate is located upstream of this one
                        break
                    trace_link = link_to_downstream.get(trace_link, -1)
                if not is_farthest_us:
                    break
                    
            if is_farthest_us:
                # Keep the lowest accumulation cell if multiple boundary pixels match the same entry LINKNO
                same_link_cands = [i for i in inflow_candidates if i['link_no'] == current_link]
                best_us = min(same_link_cands, key=lambda x: x['fac'])
                if best_us not in final_inflows:
                    final_inflows.append(best_us)

        # --- 3.2 OUTFLOW RESOLUTION (Most Downstream Only) ---
        final_outflows = []
        
        # Deduplicate candidates sharing the exact same link first (trusting higher flow accumulation)
        unique_link_outflows = {}
        for out in candidate_outflows:
            lnk = out['link_no']
            if lnk not in unique_link_outflows or out['fac'] > unique_link_outflows[lnk]['fac']:
                unique_link_outflows[lnk] = out
        
        outflow_candidates = list(unique_link_outflows.values())
        
        # Trace downstream to drop any upper reservoir loop breakthroughs
        for cand in outflow_candidates:
            is_farthest_ds = True
            current_link = cand['link_no']
            
            for other in outflow_candidates:
                if other['win_rc'] == cand['win_rc']:
                    continue
                
                # Trace DOWNSTREAM from our current candidate. 
                # If it eventually hits the 'other' candidate's link, then
                # OUR current candidate is located upstream and must be discarded.
                trace_link = link_to_downstream.get(current_link, -1)
                while trace_link != -1:
                    if trace_link == other['link_no']:
                        is_farthest_ds = False # Current cand drains INTO the other cand; current is upstream!
                        break
                    trace_link = link_to_downstream.get(trace_link, -1)
                    
                if not is_farthest_ds:
                    break
                    
            if is_farthest_ds:
                final_outflows.append(cand)

        # --- 3.3 SAME-CELL CONFLICT RESOLUTION ---
        outflow_cells = {out['win_rc'] for out in final_outflows}
        resolved_inflows = []
        
        for inf in final_inflows:
            inf_rc = inf['win_rc']
            if inf_rc in outflow_cells:
                r_conf, c_conf = inf_rc
                moved_upstream = False
                
                # Relocate the inflow point 1 cell upstream outside of the reservoir block
                for (dr, dc), required_val in REVERSE_D8.items():
                    nbr_r, nbr_c = r_conf + dr, c_conf + dc
                    if 0 <= nbr_r < ysize and 0 <= nbr_c < xsize:
                        if (src_win[nbr_r, nbr_c] == 1 and 
                            lake_mask[nbr_r, nbr_c] == 0 and 
                            fdr_win[nbr_r, nbr_c] == required_val):
                            
                            inf['win_rc'] = (nbr_r, nbr_c)
                            inf['link_no'] = wsno_to_link.get(w_win[nbr_r, nbr_c], -1)
                            resolved_inflows.append(inf)
                            moved_upstream = True
                            break
                if not moved_upstream:
                    resolved_inflows.append(inf) 
            else:
                resolved_inflows.append(inf)

        # Save Projected Records using cell-centering offsets (+0.5)
        for out in final_outflows:
            r, c = out['win_rc']
            gx_raster = gt[0] + (xoff + c + 0.5) * gt[1] + (yoff + r + 0.5) * gt[2]
            gy_raster = gt[3] + (xoff + c + 0.5) * gt[4] + (yoff + r + 0.5) * gt[5]
            transformed_pt = raster_to_stream_transform.TransformPoint(gx_raster, gy_raster)
            final_lake_records.append({
                'lake_id': lake_id, 'flow_type': 'outflow', 'geometry': Point(transformed_pt[0], transformed_pt[1])
            })
            
        for inf in resolved_inflows:
            r, c = inf['win_rc']
            gx_raster = gt[0] + (xoff + c + 0.5) * gt[1] + (yoff + r + 0.5) * gt[2]
            gy_raster = gt[3] + (xoff + c + 0.5) * gt[4] + (yoff + r + 0.5) * gt[5]
            transformed_pt = raster_to_stream_transform.TransformPoint(gx_raster, gy_raster)
            final_lake_records.append({
                'lake_id': lake_id, 'flow_type': 'inflow', 'geometry': Point(transformed_pt[0], transformed_pt[1])
            })

    # Close raster datasets safely
    ds_fdr = None; ds_src = None; ds_w = None; ds_fac = None
    
    # [4/5] Formatting & Exporting Intermediary Lake Node File
    print("\n[4/5] Formatting and writing internal lake network features...")
    if final_lake_records:
        lake_pts = gpd.GeoDataFrame(final_lake_records, crs=streams_gdf.crs)
        lake_pts['name'] = 'Lake_' + lake_pts['lake_id'].astype(str)
        lake_pts = lake_pts.rename(columns={'flow_type': 'point_type'})[['name', 'point_type', 'geometry']]
    else:
        lake_pts = gpd.GeoDataFrame(columns=['name', 'point_type', 'geometry'], crs=streams_gdf.crs)

    os.makedirs(os.path.dirname(paths["out_lake_nodes"]), exist_ok=True)
    lake_pts.to_file(paths["out_lake_nodes"])
    print(f"  -> Successfully saved pure reservoir node intersections to: {paths['out_lake_nodes']}")

    # Process Gauges (optional)
    if gauges.empty:
        export_gdf = lake_pts
    else:
        gauge_col = 'STATION_NA' if 'STATION_NA' in gauges.columns else 'STATION_NAME'
        gauge_pts = gauges.rename(columns={gauge_col: 'name'}).copy()
        gauge_pts['point_type'] = 'gauge'
        gauge_pts = gauge_pts[['name', 'point_type', 'geometry']]

        if gauge_pts.crs != streams_gdf.crs:
            gauge_pts = gauge_pts.to_crs(streams_gdf.crs)

        export_gdf = gpd.GeoDataFrame(
            pd.concat([lake_pts, gauge_pts], ignore_index=True), crs=streams_gdf.crs,
        )

    # [5/5] Combining Features & Final Vector Layer Export
    print("\n[5/5] Merging hydrologic layers and creating final master shapefile...")

    os.makedirs(os.path.dirname(paths["out"]), exist_ok=True)
    export_gdf.to_file(paths["out"])
    
    print("\n" + "="*40 + f"\nFINISH: Processing complete.\n" + "="*40)


# =====================================================================
# RUNNING THE FUNCTION PART
# =====================================================================
if __name__ == "__main__":
    # Define your local files paths here
    input_paths = {
        "river": "delineation-product/intermediate-delineated-streams.shp",
        "lakes": "lakes/filtered_lakes.shp",
        "gauges": "./points/gauges_in_basin.shp",
        "fdr": "./taudem-interim-files/d8/fdr_centerline_all.tif",
        "stream": "./taudem-interim-files/d8/stream-network_elv-src.tif",
        "w_raster": "./taudem-interim-files/d8/intermediate-delineated-watersheds.tif",
        "fac": "./taudem-interim-files/d8/stream-network_elv-ad8.tif", 
        
        # Output locations
        "out_lake_nodes": "./points/reservoir_io_nodes.shp", 
        "out": "./points/pourPointsFinal.shp"                  
    }
    
    # Fire off execution
    extract_reservoir_io_points(input_paths)