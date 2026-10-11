import argparse

import geopandas as gpd
import pandas as pd
from shapely.ops import linemerge, snap
from shapely.geometry import MultiLineString

from flowpath_geometry import fold_lineless_catchments, repair_flowpath_geometries
from pipeline_paths import (
    FINAL_BASINS,
    FINAL_STREAMS,
    PREP_GAUGES,
    WORKING_BASINS_MERGED,
    WORKING_STREAMS_MERGED,
    delete_interim_outputs,
    ensure_output_dirs,
)
from shapefile_io import export_shapefile, strip_point_join_artifacts

DELETE_INTERIM_FILES = False  # overridden by DELETE_INTERIM_FILES env var if set


def bypass_phantom_streams(streams_path, basins_path, output_path, streams=None, basins=None):
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
    if streams is None:
        streams = gpd.read_file(streams_path)
    else:
        streams = streams.copy()
    if basins is None:
        basins = gpd.read_file(basins_path)
    original_crs = streams.crs

    print(f"Initial stream segments: {len(streams)}")

    # Ensure indices are unique and set to LINKNO for direct modification
    streams = streams.set_index("LINKNO", drop=False)

    # ---------------------------------------------------------------
    # Identify phantom streams
    # ---------------------------------------------------------------
    valid_basins = set(
        pd.to_numeric(basins["DN"], errors="coerce").dropna().astype(int)
    )
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
        streams = repair_flowpath_geometries(streams)
        return streams.reset_index(drop=True)

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
    cleaned = repair_flowpath_geometries(cleaned)

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
    cleaned.set_crs(original_crs, allow_override=True, inplace=True)
    print(f"Removed phantom segments: {len(phantom_map)}")
    return cleaned



def dissolve_split_basins(basins: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Union polygons that share a DN into one basin."""
    print(f"Loaded basin dataset. Total initial features: {len(basins)}")
    print("Dissolving split polygons with matching DN values into MultiPolygons...")
    dissolved = basins.dissolve(by="DN", as_index=False)
    dissolved.set_crs(basins.crs, allow_override=True, inplace=True)
    print(f"Dissolve complete. Total consolidated basin entries: {len(dissolved)}")
    return dissolved


def _gauge_station_column(gauges: gpd.GeoDataFrame) -> str:
    for col in ("STATION_NO", "STATION_NU", "STATION_NUMBER", "STATION_NM", "STATION_NA", "STATION_NAME"):
        if col in gauges.columns:
            return col
    raise ValueError(
        "Gauge layer needs a station id column "
        "(STATION_NO, STATION_NUMBER, STATION_NU, STATION_NM, STATION_NA, or STATION_NAME)."
    )


def attach_gauge_ids(basins: gpd.GeoDataFrame, gauges: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Add STATION_NU from gauge points that fall in each basin."""
    if basins.crs != gauges.crs:
        gauges = gauges.to_crs(basins.crs)

    station_col = _gauge_station_column(gauges)
    basins = basins.drop(columns=["STATION_NU"], errors="ignore")
    basins_subset = basins[["DN", "geometry"]]
    gauges_subset = gauges[[station_col, "geometry"]].rename(
        columns={station_col: "STATION_NU"}
    )
    joined = gpd.sjoin(gauges_subset, basins_subset, how="inner", predicate="within")
    gauge_list_by_basin = (
        joined.groupby("DN")["STATION_NU"].apply(", ".join).reset_index()
    )
    basins = basins.merge(gauge_list_by_basin, on="DN", how="left")
    basins["STATION_NU"] = basins["STATION_NU"].fillna("")
    return basins


def add_gauge_info_to_basins(input_path, gauge_path, output_path):
    basins = gpd.read_file(input_path)
    gauges = strip_point_join_artifacts(gpd.read_file(gauge_path))
    export_shapefile(attach_gauge_ids(basins, gauges), output_path)


def run_clean_geofabric(*, delete_interim: bool | None = None) -> None:
    """Merge same-id basin polygons, fold catchments that have no channel, then clean streams."""
    ensure_output_dirs()

    input_streams = str(WORKING_STREAMS_MERGED)
    input_basins = str(WORKING_BASINS_MERGED)
    input_gauges = str(PREP_GAUGES)
    output_streams = str(FINAL_STREAMS)
    output_basins = str(FINAL_BASINS)

    basins = dissolve_split_basins(gpd.read_file(input_basins))
    gauges = strip_point_join_artifacts(gpd.read_file(input_gauges))
    basins = attach_gauge_ids(basins, gauges)
    streams = gpd.read_file(input_streams)
    # Fold catchments that have no channel before phantom streams are removed,
    # then again so a basin left without a stream is merged into one that has one.
    streams, basins = fold_lineless_catchments(streams, basins, gauges=gauges)
    streams = bypass_phantom_streams(
        input_streams,
        input_basins,
        output_streams,
        streams=streams,
        basins=basins,
    )
    streams, basins = fold_lineless_catchments(streams, basins, gauges=gauges)
    export_shapefile(basins, output_basins)
    export_shapefile(streams, output_streams)
    print(f"Cleaned network saved to: {output_streams}")
    print(f"Final basins written: {len(basins)}")
    print(f"Final stream segments written: {len(streams)}")

    delete_interim_outputs(enabled=delete_interim, default=DELETE_INTERIM_FILES)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean geofabric and optionally remove TauDEM interim files.")
    parser.add_argument(
        "--delete-interim",
        action="store_true",
        help="Remove outputs/interim/ after successful cleanup (also set DELETE_INTERIM_FILES=1)",
    )
    parser.add_argument(
        "--keep-interim",
        action="store_true",
        help="Keep interim TauDEM files even when DELETE_INTERIM_FILES=1 is exported",
    )
    args = parser.parse_args()

    delete_interim: bool | None = None
    if args.delete_interim:
        delete_interim = True
    elif args.keep_interim:
        delete_interim = False

    run_clean_geofabric(delete_interim=delete_interim)


if __name__ == "__main__":
    main()