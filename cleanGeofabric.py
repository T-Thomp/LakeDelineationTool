import geopandas as gpd
import pandas as pd
import fiona

def bypass_phantom_streams(streams_path, basins_path, output_path):
    """
    Reroutes the stream network topology using only LINKNO and DSLINKNO
    by deleting phantom streams missing from the basin layer.
    """
    
    # 1. Load the TauDEM stream and basin shapefiles
    streams = gpd.read_file(streams_path)
    basins = gpd.read_file(basins_path)
    
    original_crs = streams.crs
    print(f"Initial stream segments: {len(streams)}")
    
    # 2. Get a set of all valid Basin IDs (DN)
    # TauDEM maps the basin 'DN' to match the stream 'LINKNO'
    valid_basin_dns = set(basins['DN'].dropna().astype(int))
    
    # 3. Identify phantom streams
    # They exist in the stream file but their LINKNO is missing from the basin DNs
    phantom_mask = ~streams['LINKNO'].isin(valid_basin_dns)
    phantom_streams = streams[phantom_mask]
    
    # Create a quick lookup of {phantom_linkno: its_downstream_dslinkno}
    phantom_routing_map = dict(zip(phantom_streams['LINKNO'], phantom_streams['DSLINKNO']))
    
    print(f"Identified {len(phantom_routing_map)} phantom stream segments to remove.")
    
    # 4. Update the routing (DSLINKNO) for the upstream segments
    def reroute(row):
        current_ds = row['DSLINKNO']
        # If this stream points to a phantom stream, bypass it
        # We loop just in case there are rare back-to-back phantom pixels
        while current_ds in phantom_routing_map:
            current_ds = phantom_routing_map[current_ds]
        return current_ds

    # Apply the rerouting logic to all valid streams
    streams['DSLINKNO'] = streams.apply(reroute, axis=1)
    
    # 5. Remove the phantom stream rows entirely from the network
    cleaned_streams = streams[~streams['LINKNO'].isin(phantom_routing_map.keys())].copy()
    
    # 6. Prepare data and use Fiona Schema Export to handle large field widths
    cols_to_fix = ['DSContArea', 'USContArea', 'Length']
    for col in cols_to_fix:
        if col in cleaned_streams.columns:
            cleaned_streams[col] = pd.to_numeric(cleaned_streams[col], errors='coerce').fillna(0).round(0)
            
    int_cols = ['LINKNO', 'DSLINKNO']
    for col in int_cols:
        if col in cleaned_streams.columns:
            cleaned_streams[col] = cleaned_streams[col].fillna(-1).astype(int)

    # Infer the schema from your cleaned dataset
    schema = gpd.io.file.infer_schema(cleaned_streams)
    
    # Inject wide floating-point support to prevent DBF truncation errors
    if 'DSContArea' in schema['properties']: schema['properties']['DSContArea'] = 'float:20.0'
    if 'USContArea' in schema['properties']: schema['properties']['USContArea'] = 'float:20.0'

    # Set CRS metadata back to original
    cleaned_streams.set_crs(original_crs, allow_override=True, inplace=True)

    try:
        cleaned_streams.to_file(output_path, driver="ESRI Shapefile", schema=schema, engine="fiona")
        print(f"Cleaned network successfully saved via Fiona to: {output_path}")
    except Exception as e:
        print(f"Fiona export failed for {output_path}, attempting fallback. Error: {e}")
        for col in cols_to_fix:
            if col in cleaned_streams.columns: 
                cleaned_streams[col] = cleaned_streams[col].astype(str)
        cleaned_streams.to_file(output_path, driver="ESRI Shapefile")

    print(f"Final stream segments written: {len(cleaned_streams)}")
    print(phantom_routing_map)

def dissolve_split_basins(input_path, output_path):
    """
    Loads a basin shapefile, groups rows by 'DN', and aggregates disjoint or 
    diagonal floating polygons into clean, unified MultiPolygon entries.
    """
    # 1. Load the basin shapefile
    basins = gpd.read_file(input_path)
    original_crs = basins.crs
    print(f"Loaded basin dataset. Total initial features: {len(basins)}")

    # 2. Execute the dissolve operation
    print("Dissolving split polygons with matching DN values into MultiPolygons...")
    # as_index=False keeps 'DN' as a normal data column
    dissolved_basins = basins.dissolve(by='DN', as_index=False)
    
    print(f"Dissolve complete. Total consolidated basin entries: {len(dissolved_basins)}")

    # 3. Restore coordinate metadata and save standard export
    dissolved_basins.set_crs(original_crs, allow_override=True, inplace=True)
    dissolved_basins.to_file(output_path, driver="ESRI Shapefile")
    print(f"Successfully saved dissolved basins to {output_path}")


# --- Execution ---
if __name__ == "__main__":
    input_streams = "merged_basins/reservoirStreams.shp"
    input_basins = "merged_basins/reservoirBasins.shp"
    output_streams = "merged_basins/reservoirStreams_final.shp"
    output_basins = "merged_basins/reservoirBasins_final.shp"

    dissolve_split_basins(input_basins, output_basins)
    bypass_phantom_streams(input_streams, input_basins, output_streams)