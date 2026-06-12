import geopandas as gpd
import pandas as pd

# ==========================================
# 1. LOAD LOCAL BASIN DATA
# ==========================================
print("Loading subbasins and streams...")
subbasins = gpd.read_file('delineation-product/original-delineated-watersheds.shp')
river = gpd.read_file('delineation-product/original-delineated-streams.shp')

# Dissolve subbasins to get a clean, unified basin boundary mask
print("Dissolving subbasins to create a unified basin boundary...")
basin_dissolved = subbasins.dissolve()
basin_geom = basin_dissolved.geometry.iloc[0]

# ==========================================
# 2. PREPARE SPATIAL INDEX BBOX (RAM Saver)
# ==========================================
# HydroLAKES is globally stored in WGS84 (EPSG:4326). 
# We re-project our basin boundary to WGS84 to find its global coordinates.
print("Calculating spatial bounding box in WGS84...")
basin_in_wgs84 = basin_dissolved.to_crs("EPSG:4326")

# CRITICAL FIX: Convert NumPy array to a standard Python tuple for pyogrio
bbox = tuple(basin_in_wgs84.total_bounds)  # (minx, miny, maxx, maxy)
print(f"Bounding box coordinates: {bbox}")

# ==========================================
# 3. STREAM & CLIP HYDROLAKES
# ==========================================
print("Streaming and clipping HydroLAKES via bounding box (ignoring the rest of the world)...")
lakes = gpd.read_file(
    '~/bow-bassano/delineation-product/hydrolakes/HydroLAKES_polys_v10.shp',
    bbox=bbox
)
print(f"Successfully loaded {len(lakes)} candidate lakes within the bounding box area.")

# ==========================================
# 4. RE-PROJECT & FILTER
# ==========================================
print("Re-projecting candidate lakes to match local stream CRS...")
lakes = lakes.to_crs(river.crs)

print("Applying attribute filter logic...")
# Condition A: Lake is larger than the 100.0 sq km threshold
MIN_AREA = 100.0 
cond_size = lakes['Lake_area'] > MIN_AREA

# Condition B: Lake is explicitly a managed reservoir (Lake_type != 1)
cond_reservoir = lakes['Lake_type'] != 1

# Combine conditions (OR logic)
lakes_filtered = lakes[cond_size | cond_reservoir].copy()

# Exact spatial clip using the precise, jagged basin geometry
print("Performing final exact spatial intersection with basin boundary...")
lakes_in_basin = lakes_filtered[lakes_filtered.geometry.intersects(basin_geom)].copy()

# ==========================================
# 5. SAVE RESULT
# ==========================================
output_path = 'lakes/filtered_lakes.shp'
print(f"Saving {len(lakes_in_basin)} filtered lakes to {output_path}...")
lakes_in_basin.to_file(output_path)

print("Process complete!")