import geopandas as gpd
import pandas as pd

# --- LOAD DATA ---
river = gpd.read_file('delineation-product/original-delineated-streams.shp')
lakes = gpd.read_file('~/bow-bassano/delineation-product/hydrolakes/HydroLAKES_polys_v10.shp')
subbasins = gpd.read_file('delineation-product/original-delineated-watersheds.shp')

# Dissolve subbasins to get a clean basin boundary mask
basin_dissolved = subbasins.dissolve()
basin_geom = basin_dissolved.geometry.iloc[0]

# --- RE-PROJECT LAKES FIRST (For accurate spatial filtering) ---
lakes = lakes.to_crs(river.crs)

# --- FILTER LOGIC ---
# Define minimum area threshold for natural lakes (x.x sq km)
MIN_AREA = 5.0 

# Condition A: Lake is larger than the threshold
cond_size = lakes['Lake_area'] > MIN_AREA

# Condition B: Lake is explicitly a managed reservoir (Lake_type == 2 in HydroLAKES)
cond_reservoir = lakes['Lake_type'] != 1

# Combine conditions: Keep if it passes the size check OR if it's a reservoir
lakes_filtered = lakes[cond_size | cond_reservoir].copy()

# --- SPATIAL CLIP ---
# Keep only lakes that physically intersect your basin boundary
lakes_in_basin = lakes_filtered[lakes_filtered.geometry.intersects(basin_geom)]

# Save output
lakes_in_basin.to_file('lakes/filtered_lakes.shp')

print(f"Filtering complete. Total lakes/reservoirs kept: {len(lakes_in_basin)}")