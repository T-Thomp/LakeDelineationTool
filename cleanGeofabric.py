import geopandas as gpd
import pandas as pd
from shapely.ops import linemerge, snap
from shapely.geometry import MultiLineString

def bypass_phantom_streams(streams_path, basins_path, output_path):
    """
    Removes TauDEM phantom streams by merging them into upstream reaches.

    Variables:
        Length      -> summed
        strmDrop    -> summed
        DSContArea  -> minimum value
        DOUTEND     -> downstream phantom value
        DOUTMID     -> recalculated
        StraightL   -> recalculated
        Slope       -> strmDrop / Length
        DSLINKNO    -> bypass phantom routing

    Also removes empty/zero-length phantom reaches.
    """
    # ---------------------------------------------------------------
    # Load data
    # ---------------------------------------------------------------
    streams = gpd.read_file(streams_path)
    basins = gpd.read_file(basins_path)
    original_crs = streams.crs

    print(f"Initial stream segments: {len(streams)}")

    # Ensure indices are unique and set to LINKNO for direct modification
    streams = streams.set_index("LINKNO", drop=False)

    # ---------------------------------------------------------------
    # Identify phantom streams
    # ---------------------------------------------------------------
    valid_basins = set(basins["DN"].dropna().astype(int))
    phantom = streams[~streams["LINKNO"].isin(valid_basins)].copy()

    # ---------------------------------------------------------------
    # Remove empty/zero-length phantom streams
    # ---------------------------------------------------------------
    empty_phantom_ids = set(
        phantom.loc[
            phantom.geometry.is_empty | (phantom.geometry.length == 0),
            "LINKNO"
        ]
    )
    
    if empty_phantom_ids:
        print(f"Removing {len(empty_phantom_ids)} empty/zero-length phantom segments")
        streams = streams.drop(index=empty_phantom_ids, errors="ignore")
        phantom = phantom.drop(index=empty_phantom_ids, errors="ignore")

    phantom_map = dict(zip(phantom["LINKNO"], phantom["DSLINKNO"]))
    print(f"Phantom segments remaining: {len(phantom_map)}")

    if len(phantom_map) == 0:
        streams.to_file(output_path, driver="ESRI Shapefile")
        return

    # Use a dictionary of lookups for faster access during the merge loop
    lookup_dict = streams.to_dict(orient="index")

    # ---------------------------------------------------------------
    # Merge phantom geometry and attributes
    # ---------------------------------------------------------------
    # To prevent modifying-while-iterating errors, write updates to a dict
    updates = {}

    for current_link, row in streams.iterrows():
        # Skip phantom reaches (they will be deleted later)
        if current_link in phantom_map:
            continue

        geom = row.geometry
        next_link = row["DSLINKNO"]
        
        # Keep track of visited nodes to prevent infinite routing loops
        visited = {current_link} 

        length_added = 0.0
        strm_drop_added = 0.0
        ds_cont_area = row.get("DSContArea", None)
        dout_end = row.get("DOUTEND", None)

        while next_link in phantom_map:
            if next_link in visited:
                print(f"Warning: Cyclic dependency detected at stream {current_link} pointing to {next_link}. Breaking loop.")
                break
            visited.add(next_link)

            phantom_seg = lookup_dict[next_link]

            # Safety check for bad geometry
            if (
                phantom_seg["geometry"] is None or
                phantom_seg["geometry"].is_empty or
                phantom_seg["geometry"].length == 0
            ):
                next_link = phantom_seg["DSLINKNO"]
                continue

            # Merge geometry
            try:
                snapped = snap(phantom_seg["geometry"], geom, 0.01)
                merged = linemerge([geom, snapped])
            except Exception:
                try:
                    merged = MultiLineString([geom, phantom_seg["geometry"]])
                except Exception:
                    merged = geom

            if isinstance(merged, MultiLineString):
                try:
                    merged = linemerge(merged)
                except Exception:
                    pass

            geom = merged

            # Accumulate attribute updates
            if "Length" in streams.columns:
                length_added += phantom_seg["Length"]

            if "strmDrop" in streams.columns:
                strm_drop_added += phantom_seg["strmDrop"]

            if "DSContArea" in streams.columns:
                val = phantom_seg["DSContArea"]
                ds_cont_area = min(ds_cont_area, val) if ds_cont_area is not None else val

            if "DOUTEND" in streams.columns:
                dout_end = phantom_seg["DOUTEND"]

            # Step downstream
            next_link = phantom_seg["DSLINKNO"]

        # If any changes were made, queue them up
        updates[current_link] = {
            "geometry": geom,
            "DSLINKNO": next_link,
            "Length": row["Length"] + length_added if "Length" in streams.columns else None,
            "strmDrop": row["strmDrop"] + strm_drop_added if "strmDrop" in streams.columns else None,
            "DSContArea": ds_cont_area,
            "DOUTEND": dout_end
        }

    # Apply the queued updates to the DataFrame
    for link_no, up in updates.items():
        streams.at[link_no, "geometry"] = up["geometry"]
        streams.at[link_no, "DSLINKNO"] = up["DSLINKNO"]
        if up["Length"] is not None:
            streams.at[link_no, "Length"] = up["Length"]
        if up["strmDrop"] is not None:
            streams.at[link_no, "strmDrop"] = up["strmDrop"]
        if up["DSContArea"] is not None:
            streams.at[link_no, "DSContArea"] = up["DSContArea"]
        if up["DOUTEND"] is not None:
            streams.at[link_no, "DOUTEND"] = up["DOUTEND"]

    # ---------------------------------------------------------------
    # Remove remaining phantom rows
    # ---------------------------------------------------------------
    cleaned = streams[~streams["LINKNO"].isin(phantom_map.keys())].copy()

    # ---------------------------------------------------------------
    # Recalculate geometry-derived fields
    # ---------------------------------------------------------------
    for idx, row in cleaned.iterrows():
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue

        if geom.geom_type == "MultiLineString":
            coords = []
            for line in geom.geoms:
                coords.extend(list(line.coords))
        else:
            coords = list(geom.coords)

        if not coords:
            continue

        start = coords[0]
        end = coords[-1]

        # Straight length
        if "StraightL" in cleaned.columns:
            cleaned.at[idx, "StraightL"] = ((end[0] - start[0]) ** 2 + (end[1] - start[1]) ** 2) ** 0.5

        # DOUTMID
        if "DOUTSTART" in cleaned.columns and "DOUTEND" in cleaned.columns and "DOUTMID" in cleaned.columns:
            cleaned.at[idx, "DOUTMID"] = (cleaned.at[idx, "DOUTSTART"] + cleaned.at[idx, "DOUTEND"]) / 2

        # Slope
        if "Slope" in cleaned.columns and "Length" in cleaned.columns and "strmDrop" in cleaned.columns:
            length_val = cleaned.at[idx, "Length"]
            cleaned.at[idx, "Slope"] = (cleaned.at[idx, "strmDrop"] / length_val) if length_val > 0 else 0

    # ---------------------------------------------------------------
    # Clean fields for shapefile export
    # ---------------------------------------------------------------
    numeric_fields = ["DSContArea", "USContArea", "Length", "strmDrop", "StraightL", "Slope"]
    for col in numeric_fields:
        if col in cleaned.columns:
            cleaned[col] = pd.to_numeric(cleaned[col], errors="coerce").fillna(0).round(6)

    for col in ["LINKNO", "DSLINKNO"]:
        if col in cleaned.columns:
            cleaned[col] = cleaned[col].fillna(-1).astype(int)

    # Reset index back to normal before saving
    cleaned = cleaned.reset_index(drop=True)

    # ---------------------------------------------------------------
    # Fiona schema export
    # ---------------------------------------------------------------
    schema = None
    try:
        # Avoid direct call to gpd.io.file.infer_schema if it throws errors in newer versions
        from geopandas.io.file import infer_schema
        schema = infer_schema(cleaned)
        
        for col in ["DSContArea", "USContArea", "Length", "strmDrop"]:
            if col in schema["properties"]:
                schema["properties"][col] = "float:20.6"
    except Exception as schema_err:
        print(f"Could not construct custom fiona schema: {schema_err}. Defaulting to automated schema export.")

    cleaned.set_crs(original_crs, allow_override=True, inplace=True)

    # ---------------------------------------------------------------
    # Write shapefile
    # ---------------------------------------------------------------

    try:
        cleaned.to_file(output_path, driver="ESRI Shapefile")
        print(f"Cleaned network successfully saved to: {output_path}")
    except Exception as e:
        print(f"Export failed: {e}. Attempting string fallback...")
        # Fallback to string types if the DBF driver complains about numbers
        for col in cols_to_fix:
            if col in cleaned.columns: 
                cleaned[col] = cleaned[col].astype(str)
        cleaned.to_file(output_path, driver="ESRI Shapefile")
    
    print(f"Final stream segments written: {len(cleaned)}")
    print(f"Removed phantom segments: {len(phantom_map)}")



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

def add_gauge_info_to_basins(input_path, gauge_path, output_path):
    # 1. Load your data
    basins = gpd.read_file(input_path)
    gauges = gpd.read_file(gauge_path)

    # 2. Align coordinate systems
    if basins.crs != gauges.crs:
        gauges = gauges.to_crs(basins.crs)

    # 3. FIX: Strip out all extra columns except geometry and the specific IDs
    basins_subset = basins[["DN", "geometry"]]
    gauges_subset = gauges[["STATION_NU", "geometry"]]

    # 4. Spatial join (only joins 'DN' and 'STATION_NU')
    joined = gpd.sjoin(gauges_subset, basins_subset, how="inner", predicate="within")

    # 5. Group by subbasin ID and concatenate station numbers
    gauge_list_by_basin = (
        joined.groupby("DN")["STATION_NU"].apply(", ".join).reset_index()
    )

    # 6. Merge the text string directly back into your original basin file
    basins = basins.merge(gauge_list_by_basin, on="DN", how="left")

    # 7. Fill basins without stations with an empty string
    basins["STATION_NU"] = basins["STATION_NU"].fillna("")

    # 8. Save output
    basins.to_file(output_path)
    
# --- Execution ---
if __name__ == "__main__":
    input_streams = "merged_basins/reservoirStreams.shp"
    input_basins = "merged_basins/reservoirBasins.shp"
    input_gauges = "points/gauges_in_basin.shp"
    output_streams = "merged_basins/reservoirStreams_final.shp"
    output_basins = "merged_basins/reservoirBasins_final.shp"
    
    
    dissolve_split_basins(input_basins, output_basins)
    bypass_phantom_streams(input_streams, input_basins, output_streams)
    add_gauge_info_to_basins(input_basins, input_gauges, output_basins)