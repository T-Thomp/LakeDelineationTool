import matplotlib.pyplot as plt
import geopandas as gpd
import sqlite3
import pandas as pd
from shapely.geometry import LineString, Point, MultiPoint
from shapely.ops import split

# --- LOAD DATA ---
subbasins = gpd.read_file('delineation-product/original-delineated-watersheds.shp')
basin_dissolved = subbasins.dissolve()

# 1. Connect and Query
db_path = 'Hydat.sqlite3'
conn = sqlite3.connect(db_path)

query = """
SELECT DISTINCT
    s.STATION_NUMBER, 
    s.STATION_NAME, 
    s.LATITUDE, 
    s.LONGITUDE, 
    s.PROV_TERR_STATE_LOC as PROVINCE
FROM STATIONS s
JOIN STN_DATA_RANGE r ON s.STATION_NUMBER = r.STATION_NUMBER
WHERE s.HYD_STATUS = 'A' 
  AND r.DATA_TYPE = 'Q'
"""

df = pd.read_sql_query(query, conn)
conn.close()

# 2. Convert to GeoDataFrame
# We use WGS84 (EPSG:4326) which is the standard for Lat/Long coordinates
gdf = gpd.GeoDataFrame(
    df, 
    geometry=gpd.points_from_xy(df.LONGITUDE, df.LATITUDE),
    crs="EPSG:4326"
)
print(f"Found {len(df)} active flow stations.")

# 3. Load your flow gauges (from our previous SQL step)
gauges = gdf

# 4. Align Coordinate Systems (CRS)
# This ensures both the points and the polygon are in the same 'space'
if gauges.crs != basin_dissolved.crs:
    gauges = gauges.to_crs(basin_dissolved.crs)

# 5. Spatial Join (Point-in-Polygon)
# We use the dissolved study_area to clip the gauge list
final_gauges = gpd.sjoin(gauges, basin_dissolved, predicate='within')

final_gauges_reprojected = final_gauges.to_crs(subbasins.crs)

# 6. Export the results
final_gauges_reprojected.to_file("./points/gauges_in_basin.shp")

print(f"Dissolve complete. {len(final_gauges)} gauges found within the combined basin.")