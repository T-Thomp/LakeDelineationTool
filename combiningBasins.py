import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
import numpy as np
import fiona

# --- 1. CONFIGURATION & HELPERS ---
GAUGE_SEARCH_RADIUS = 750  # meters
MIN_INTERNAL_STREAM_LEN = 180 # meters - threshold for forced merge

def get_validated_node_connection(point_geom, streams_gdf, search_dist, mode='inflow'):
    """Finds a segment pair at a point and confirms topological connection."""
    candidates = streams_gdf[streams_gdf.intersects(point_geom.buffer(search_dist))].copy()
    if candidates.empty: return None

    def get_node_dists(row):
        coords = list(row.geometry.coords)
        return pd.Series([point_geom.distance(Point(coords[-1])), 
                          point_geom.distance(Point(coords[0]))], 
                          index=['d_start', 'd_end'])

    dists = candidates.apply(get_node_dists, axis=1)
    candidates = pd.concat([candidates, dists], axis=1)
    
    potential_up = candidates.sort_values('d_end').iloc[0]
    potential_down = candidates.sort_values('d_start').iloc[0]

    return int(potential_down['LINKNO']) if mode == 'inflow' else int(potential_up['LINKNO'])

def find_nearest_link(target_point_geom, link_ids, streams_gdf):
    """Identifies which LINKNO in a list is closest to a spatial point."""
    subset = streams_gdf[streams_gdf['LINKNO'].isin(link_ids)].copy()
    if subset.empty: return None
    subset['dist'] = subset.geometry.apply(lambda x: target_point_geom.distance(Point(x.coords[-1])))
    return int(subset.sort_values('dist').iloc[0]['LINKNO'])

# --- 2. LOAD & ALIGN ---
print("Loading files...")
basins = gpd.read_file("delineation-product/final-delineated-watersheds.shp")
lakes = gpd.read_file("lakes/filtered_lakes.shp")
streams = gpd.read_file("delineation-product/final-delineated-streams.shp")
intersection = gpd.read_file("taudem-interim-files/final/snapped-outlets.shp")
gauges = gpd.read_file("points/gauges_in_basin.shp")

# Handle Naming Convention
if 'name' in intersection.columns:
    intersection['lake_id'] = (
        intersection['name']
        .str.extract(r'Lake_(\d+)')
        .astype(float)
        .fillna(-1)
        .astype(int)
    )
else:
    intersection['lake_id'] = pd.to_numeric(intersection['lake_id'], errors='coerce').fillna(-1).astype(int)

try:
    overrides_df = pd.read_csv("overrides.csv")
    if overrides_df['lake_id'].dtype == object:
        overrides_df['lake_id'] = overrides_df['lake_id'].str.extract(r'(\d+)').astype(int)
    manual_map = {row['lake_id']: (row['lat'], row['lon']) for _, row in overrides_df.iterrows()}
    print(f"Loaded {len(manual_map)} manual overrides.")
except (FileNotFoundError, KeyError):
    manual_map = {}

# Align all to Stream CRS
target_crs = streams.crs
lakes = lakes.to_crs(target_crs)
intersection = intersection.to_crs(target_crs)
basins = basins.to_crs(target_crs)
gauges = gauges.to_crs(target_crs)

coords = np.array(basins.geometry.iloc[0].exterior.coords)
dem_res = np.min(np.abs(np.diff(coords, axis=0))[np.abs(np.diff(coords, axis=0)) > 0])
buffer_dist = dem_res * 0.75

lakes['Hylak_id'] = pd.to_numeric(lakes['Hylak_id'], errors='coerce').fillna(-2).astype(int)
basins['DN'] = basins['DN'].astype(int)
streams['LINKNO'] = streams['LINKNO'].astype(int)

# --- 3. GLOBAL MAPPING ---
max_dn = basins['DN'].max()
LAKE_ID_OFFSET = 10 ** len(str(int(max_dn)))
down_map = streams.set_index("LINKNO")["DSLINKNO"].to_dict()
all_downstream_ids = set(streams['DSLINKNO'].unique())

lake_to_links, lake_to_outlet, lake_to_winner = {}, {}, {}
all_swallowed_ids, catchment_results = set(), []

# --- 4. PROCESSING LOOP ---
unique_lakes = intersection['lake_id'].unique()
print(f"Processing {len(unique_lakes)} lakes...")

for l_id in unique_lakes:
    if l_id < 0: continue
    
    pts = intersection[(intersection['lake_id'] == l_id) & (intersection['point_type'] == 'outflow')]
    in_pts = intersection[(intersection['lake_id'] == l_id) & (intersection['point_type'] == 'inflow')]
    lake_polys = lakes[lakes['Hylak_id'] == l_id]
    
    if lake_polys.empty or pts.empty: continue
    lake_geom = lake_polys.geometry.iloc[0]

    raw_candidates = {get_validated_node_connection(r.geometry, streams, buffer_dist, 'outflow') for _, r in pts.iterrows()}
    raw_candidates.discard(None)
    candidate_links = list(raw_candidates)
    
    if not candidate_links: continue

    winner_id = None
    if l_id in manual_map:
        lat, lon = manual_map[l_id]
        ov_pt = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs(target_crs).iloc[0]
        winner_id = find_nearest_link(ov_pt, candidate_links, streams)

    if winner_id is None:
        best_dist = float('inf')
        for link_no in candidate_links:
            out_feat = streams[streams['LINKNO'] == link_no].geometry.iloc[0]
            outlet_node = Point(out_feat.coords[-1])
            nearby = gauges[gauges.distance(outlet_node) <= GAUGE_SEARCH_RADIUS]
            if not nearby.empty:
                d = nearby.distance(outlet_node).min()
                if d < best_dist:
                    best_dist, winner_id = d, link_no

    if winner_id is None:
        winner_row = streams[streams['LINKNO'].isin(candidate_links)].sort_values('strmOrder', ascending=False).iloc[0]
        winner_id = int(winner_row['LINKNO'])

    lake_to_outlet[l_id] = down_map.get(winner_id, -1)
    lake_to_winner[l_id] = winner_id

    # --- 4. SWALLOW INTERNAL LINKS (ENHANCED LOGIC) ---
    res_internal = set()
    for _, pt_row in in_pts.iterrows():
        curr = get_validated_node_connection(pt_row.geometry, streams, buffer_dist, 'inflow')
        while curr and curr != -1 and curr not in res_internal:
            res_internal.add(curr)
            if curr in raw_candidates: break
            curr = down_map.get(curr, -1)

    candidates_intersect = streams[streams.intersects(lake_geom)]
    for _, row in candidates_intersect.iterrows():
        sid = int(row['LINKNO'])
        
        # Check 1: Length of intersection
        overlap_len = row.geometry.intersection(lake_geom).length
        
        # Check 2: Midpoint inside
        mid = row.geometry.interpolate(0.5, normalized=True)
        is_mid_inside = lake_geom.contains(mid)

        # Check 3: Start and End points inside
        coords_list = list(row.geometry.coords)
        start_pt = Point(coords_list[0])
        end_pt = Point(coords_list[-1])
        both_tips_inside = lake_geom.contains(start_pt) and lake_geom.contains(end_pt)
        
        # Combine triggers
        should_swallow = is_mid_inside or (overlap_len > MIN_INTERNAL_STREAM_LEN) or both_tips_inside
        
        if (sid not in all_downstream_ids and sid not in res_internal) and should_swallow:
            curr = sid
            while curr != -1 and curr not in res_internal:
                res_internal.add(curr)
                if curr in raw_candidates: break
                curr = down_map.get(curr, -1)

    lake_to_links[l_id] = list(res_internal)
    fb = basins[basins['DN'].isin(res_internal)]
    if not fb.empty:
        all_swallowed_ids.update(fb['DN'].tolist())
        catchment_results.append({
            'DN': int(l_id + LAKE_ID_OFFSET), 
            'lake_id': l_id, 
            'geometry': fb.geometry.union_all(), 
            'is_lake': 1
        })

# --- 5. DISSOLVE & CLEANUP ---
print("Merging segments and cleaning topology...")
streams_work = streams.copy()
lake_path_lengths = {}

for l_id, links in lake_to_links.items():
    if not links: continue
    internal_set, target_exit = set(links), lake_to_winner.get(l_id)
    
    inflows = [l for l in links if streams_work.loc[streams_work['LINKNO']==l, 'USLINKNO1'].values[0] not in internal_set]
    
    max_path = 0
    for start in inflows:
        curr, path_len = start, 0
        while curr in internal_set:
            row_data = streams_work.loc[streams_work['LINKNO']==curr]
            if row_data.empty: break
            path_len += row_data['Length'].values[0]
            if curr == target_exit: break
            curr = down_map.get(curr, -1)
        max_path = max(max_path, path_len)
    lake_path_lengths[l_id + LAKE_ID_OFFSET] = max_path

streams_work['merged_ID'] = streams_work['LINKNO']
swallowed_map = {}
for l_id, links in lake_to_links.items():
    new_id = l_id + LAKE_ID_OFFSET
    streams_work.loc[streams_work['LINKNO'].isin(links), 'merged_ID'] = new_id
    for link in links: swallowed_map[link] = new_id

agg_logic = {col: 'max' for col in streams.columns if col not in ['geometry', 'merged_ID', 'LINKNO', 'Length']}
agg_logic.update({'DSContArea': 'max', 'USContArea': 'min', 'Length': 'max'})

streams_dissolved = streams_work.dissolve(by='merged_ID', aggfunc=agg_logic).reset_index()
streams_dissolved = streams_dissolved.rename(columns={'merged_ID': 'LINKNO'})

for nid, plen in lake_path_lengths.items():
    streams_dissolved.loc[streams_dissolved['LINKNO'] == nid, 'Length'] = plen

for l_id, ds_id in lake_to_outlet.items():
    streams_dissolved.loc[streams_dissolved['LINKNO'] == (l_id + LAKE_ID_OFFSET), 'DSLINKNO'] = ds_id

streams_dissolved['DSLINKNO'] = streams_dissolved['DSLINKNO'].replace(swallowed_map)
streams_dissolved = streams_dissolved.drop(columns=['USLINKNO1', 'USLINKNO2'], errors='ignore')

# --- 6. EXPORT ---
def export_fixed(gdf, filename):
    export_gdf = gdf.copy()
    cols_to_fix = ['DSContArea', 'USContArea', 'Length']
    for col in cols_to_fix:
        if col in export_gdf.columns:
            export_gdf[col] = pd.to_numeric(export_gdf[col], errors='coerce').fillna(0).round(0)
    
    int_cols = ['LINKNO', 'DSLINKNO', 'DN', 'lake_id', 'is_lake']
    for col in int_cols:
        if col in export_gdf.columns:
            export_gdf[col] = export_gdf[col].fillna(-1).astype(int)

    schema = gpd.io.file.infer_schema(export_gdf)
    if 'DSContArea' in schema['properties']: schema['properties']['DSContArea'] = 'float:20.0'
    if 'USContArea' in schema['properties']: schema['properties']['USContArea'] = 'float:20.0'

    try:
        export_gdf.to_file(filename, driver="ESRI Shapefile", schema=schema, engine="fiona")
    except Exception as e:
        print(f"Fiona export failed for {filename}, attempting fallback. Error: {e}")
        for col in cols_to_fix:
            if col in export_gdf.columns: export_gdf[col] = export_gdf[col].astype(str)
        export_gdf.to_file(filename, driver="ESRI Shapefile")

final_geofabric = pd.concat([
    basins[~basins['DN'].isin(all_swallowed_ids)], 
    gpd.GeoDataFrame(catchment_results, crs=target_crs)
], ignore_index=True)

final_geofabric['is_lake'] = final_geofabric['is_lake'].fillna(0).astype(int)
final_geofabric['lake_id'] = final_geofabric['lake_id'].fillna(-1).astype(int)

export_fixed(final_geofabric, "merged_basins/saskTotalBasins.shp")
export_fixed(streams_dissolved, "merged_basins/saskTotalStreams.shp")

print("Processing Complete.")