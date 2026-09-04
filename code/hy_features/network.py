"""
Build HY_Features network topology: nexuses, catchment associations, river referencing.
"""

from __future__ import annotations

from collections import defaultdict

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point

from hy_features.schema import (
    CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    DISTANCE_FROM_OUTLET_M,
    DISTANCE_FROM_OUTLET_PCT,
    DOWNSTREAM_WATERBODY_ID,
    DRAINAGE_PATTERN,
    DRAINAGE_PATTERN_COL,
    FLOWPATH_ID,
    HOST_FLOWPATH_ID,
    HYF_TYPE,
    HYF_TYPE_URI,
    HY_DENDRITIC_CATCHMENT,
    HY_FLOWPATH,
    HY_HYDRO_NEXUS,
    HY_HYDROGRAPHIC_NETWORK,
    INFLOW_NEXUS_ID,
    LEGACY_BASIN_ID,
    LEGACY_FLOWPATH_ID,
    LEGACY_LOWER_ID,
    LINEAR_ELEMENT_ID,
    LOWER_CATCHMENT_ID,
    NEXUS_ID,
    NETWORK_ID,
    OUTFLOW_NEXUS_ID,
    REALIZED_NEXUS_ID,
    REALIZES_CATCHMENT,
    RECEIVING_CATCHMENT_ID,
    REFERENCE_NEXUS_ID,
    UPPER_CATCHMENT_ID,
    UPSTREAM_WATERBODY_ID,
    WATERBODY_ID,
    hyf_type_uri,
    outflow_nexus_id_for,
)


def _link_col(streams: gpd.GeoDataFrame) -> str:
    return FLOWPATH_ID if FLOWPATH_ID in streams.columns else LEGACY_FLOWPATH_ID


def _down_col(streams: gpd.GeoDataFrame) -> str:
    return LOWER_CATCHMENT_ID if LOWER_CATCHMENT_ID in streams.columns else LEGACY_LOWER_ID


def _raw_down_col(streams: gpd.GeoDataFrame) -> str:
    """Original numeric downstream id column before enrichment blanking."""
    if LEGACY_LOWER_ID in streams.columns:
        return LEGACY_LOWER_ID
    return LOWER_CATCHMENT_ID


def _as_linestring(geom) -> LineString | None:
    """Normalize a flowpath geometry to a single LineString."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "MultiLineString":
        lines = [part for part in geom.geoms if part.length > 0]
        if not lines:
            return None
        return LineString([c for line in lines for c in line.coords])
    if geom.geom_type == "LineString":
        return geom
    return None


def _line_endpoints(line: LineString) -> tuple[Point, Point]:
    coords = list(line.coords)
    return Point(coords[0]), Point(coords[-1])


def _parse_downstream_id(down_val: object, outlet_sentinel: int) -> int | None:
    try:
        down_int = int(down_val)
    except (TypeError, ValueError):
        return None
    if down_int <= 0 or down_int == outlet_sentinel:
        return None
    return down_int


def _flowpath_row(
    streams_indexed: gpd.GeoDataFrame,
    flowpath_id: object,
) -> pd.Series | None:
    """Look up a flowpath row by id (string or int index)."""
    for key in (flowpath_id, str(flowpath_id)):
        if key in streams_indexed.index:
            return streams_indexed.loc[key]
    try:
        numeric = int(flowpath_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if numeric in streams_indexed.index:
        return streams_indexed.loc[numeric]
    return None


def _outlet_point_for_reach(
    row: pd.Series,
    streams_indexed: gpd.GeoDataFrame,
    upstream_map: dict[str, list[str]],
    link_col: str,
    down_col: str,
    outlet_sentinel: int,
) -> Point | None:
    """
    Locate the catchment outflow (downstream end) of a reach.

    TauDEM ``stream_net`` lines store the pour point at ``coords[0]`` (downstream).
    Prefer ``DSLINKNO`` / upstream topology when available so nexus placement stays
    correct if a reach was reversed during post-processing; otherwise use ``coords[0]``.
    """
    line = _as_linestring(row.geometry)
    if line is None:
        return None
    end_a, end_b = _line_endpoints(line)  # end_a = coords[0], TauDEM downstream / pour point
    link_id = str(row[link_col])

    lower_int = _parse_downstream_id(row[down_col], outlet_sentinel)
    if lower_int is not None:
        down_row = _flowpath_row(streams_indexed, lower_int)
        if down_row is not None and down_row.geometry is not None:
            down_geom = down_row.geometry
            return end_a if end_a.distance(down_geom) <= end_b.distance(down_geom) else end_b

    upstream_ids = upstream_map.get(link_id, [])
    if upstream_ids:
        inflow_a = float("inf")
        inflow_b = float("inf")
        for up_id in upstream_ids:
            up_row = _flowpath_row(streams_indexed, up_id)
            if up_row is None or up_row.geometry is None:
                continue
            up_geom = up_row.geometry
            inflow_a = min(inflow_a, end_a.distance(up_geom))
            inflow_b = min(inflow_b, end_b.distance(up_geom))
        if inflow_a < float("inf") or inflow_b < float("inf"):
            return end_b if inflow_a <= inflow_b else end_a

    # Single-segment / unresolved topology: TauDEM pour point at line start
    return end_a


def build_upstream_map(streams: gpd.GeoDataFrame, outlet_sentinel: int) -> dict[str, list[str]]:
    """Map downstream catchment id -> list of upstream flowpath/catchment ids."""
    link_col = _link_col(streams)
    down_col = _raw_down_col(streams)
    upstream: dict[str, list[str]] = defaultdict(list)
    for _, row in streams.iterrows():
        fid = str(row[link_col])
        down = row[down_col]
        try:
            down_int = int(down)
        except (TypeError, ValueError):
            continue
        if down_int <= 0 or down_int == outlet_sentinel:
            continue
        upstream[str(down_int)].append(fid)
    return dict(upstream)


def build_hydro_nexus_layer(
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = -9999,
) -> gpd.GeoDataFrame:
    """
    Build HY_HydroNexus points at flowpath outlets (HY_Features Section 7.3.2).

    Each dendritic catchment outflow is realized as a nexus connecting to the
    receiving downstream catchment (nillable at domain outlet).
    """
    link_col = _link_col(streams)
    down_col = _raw_down_col(streams)
    crs = streams.crs
    streams_indexed = streams.set_index(link_col, drop=False)
    upstream_map = build_upstream_map(streams, outlet_sentinel)
    records: list[dict] = []

    for _, row in streams.iterrows():
        cid = str(row[link_col])
        ds_pt = _outlet_point_for_reach(
            row, streams_indexed, upstream_map, link_col, down_col, outlet_sentinel,
        )
        if ds_pt is None:
            continue

        lower = row[down_col]
        receiving = ""
        lower_int = _parse_downstream_id(lower, outlet_sentinel)
        if lower_int is not None:
            receiving = str(lower_int)

        records.append({
            NEXUS_ID: outflow_nexus_id_for(cid),
            HYF_TYPE: HY_HYDRO_NEXUS,
            HYF_TYPE_URI: hyf_type_uri(HY_HYDRO_NEXUS),
            CONTRIBUTING_CATCHMENT_ID: cid,
            RECEIVING_CATCHMENT_ID: receiving,
            REALIZES_CATCHMENT: cid,
            "geometry": ds_pt,
        })

    if not records:
        return gpd.GeoDataFrame(
            columns=[NEXUS_ID, HYF_TYPE, HYF_TYPE_URI, CONTRIBUTING_CATCHMENT_ID,
                     RECEIVING_CATCHMENT_ID, REALIZES_CATCHMENT, "geometry"],
            crs=crs,
        )
    return gpd.GeoDataFrame(records, crs=crs)


def link_catchment_nexuses(
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = -9999,
) -> gpd.GeoDataFrame:
    """Add outflow/inflow nexus and upper catchment ids to catchment areas."""
    from hy_features.schema import HY_CATCHMENT_AREA

    out = basins.copy()
    basin_col = CATCHMENT_ID if CATCHMENT_ID in out.columns else LEGACY_BASIN_ID
    upstream_map = build_upstream_map(streams, outlet_sentinel)

    out[OUTFLOW_NEXUS_ID] = out[basin_col].astype(str).map(outflow_nexus_id_for)
    out[INFLOW_NEXUS_ID] = ""
    out[UPPER_CATCHMENT_ID] = ""

    for idx, row in out.iterrows():
        cid = str(row[basin_col])
        ups = upstream_map.get(cid, [])
        if len(ups) == 1:
            out.at[idx, UPPER_CATCHMENT_ID] = ups[0]
            out.at[idx, INFLOW_NEXUS_ID] = outflow_nexus_id_for(ups[0])
        elif len(ups) > 1:
            out.at[idx, UPPER_CATCHMENT_ID] = ",".join(ups)
            out.at[idx, INFLOW_NEXUS_ID] = ",".join(outflow_nexus_id_for(u) for u in ups)

    if HYF_TYPE in out.columns:
        out[HYF_TYPE_URI] = out[HYF_TYPE].map(hyf_type_uri)
    else:
        out[HYF_TYPE_URI] = hyf_type_uri(HY_CATCHMENT_AREA)
    return out


def link_flowpath_nexuses(
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = -9999,
) -> gpd.GeoDataFrame:
    """Add nexus ids to flowpath layer."""
    from hy_features.schema import HY_FLOWPATH

    out = streams.copy()
    link_col = _link_col(out)
    upstream_map = build_upstream_map(out, outlet_sentinel)

    out[OUTFLOW_NEXUS_ID] = out[link_col].astype(str).map(outflow_nexus_id_for)
    out[INFLOW_NEXUS_ID] = ""
    out[UPPER_CATCHMENT_ID] = ""
    out[DRAINAGE_PATTERN_COL] = DRAINAGE_PATTERN

    for idx, row in out.iterrows():
        cid = str(row[link_col])
        ups = upstream_map.get(cid, [])
        if len(ups) == 1:
            out.at[idx, UPPER_CATCHMENT_ID] = ups[0]
            out.at[idx, INFLOW_NEXUS_ID] = outflow_nexus_id_for(ups[0])
        elif len(ups) > 1:
            out.at[idx, UPPER_CATCHMENT_ID] = ",".join(ups)
            out.at[idx, INFLOW_NEXUS_ID] = ",".join(outflow_nexus_id_for(u) for u in ups)

    if HYF_TYPE in out.columns:
        out[HYF_TYPE_URI] = out[HYF_TYPE].map(hyf_type_uri)
    else:
        out[HYF_TYPE_URI] = hyf_type_uri(HY_FLOWPATH)
    return out


def _project_distance_from_outlet_m(
    line: LineString,
    point: Point,
    outlet_pt: Point,
) -> tuple[float, float]:
    """Distance along the reach from the topologic outlet to ``point``."""
    if line is None or line.is_empty or point is None or outlet_pt is None:
        return 0.0, 0.0
    total = line.length
    if total <= 0:
        return 0.0, 0.0
    outlet_dist = line.project(outlet_pt)
    point_dist = line.project(point)
    dist_from_outlet = abs(point_dist - outlet_dist)
    pct = dist_from_outlet / total
    return dist_from_outlet, pct


def assign_hydrometric_positions(
    gauges: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    basins: gpd.GeoDataFrame | None = None,
    search_radius_m: float = 5000.0,
) -> gpd.GeoDataFrame:
    """
    HY_HydrometricFeature.positionOnRiver via HY_IndirectPosition (Section 7.3.3).

    Uses the containing catchment id (TauDEM ``DN``) for ``catchment_id``,
    ``host_flowpath_id``, and ``linear_element_id``. In this dendritic fabric each
    catchment is realized by the flowpath with the same id. Distance along the
    reach is measured on that host flowpath after projecting the gauge onto it.
    """
    from hy_features.schema import HY_HYDROMETRIC_FEATURE

    out = gauges.copy()
    link_col = _link_col(streams)
    down_col = _raw_down_col(streams)
    outlet_sentinel = -9999
    streams_indexed = streams.set_index(link_col, drop=False)
    upstream_map = build_upstream_map(streams, outlet_sentinel)

    out[HOST_FLOWPATH_ID] = ""
    out[CATCHMENT_ID] = ""
    out[LINEAR_ELEMENT_ID] = ""
    out[REFERENCE_NEXUS_ID] = ""
    out[DISTANCE_FROM_OUTLET_M] = 0.0
    out[DISTANCE_FROM_OUTLET_PCT] = 0.0
    out[HYF_TYPE_URI] = hyf_type_uri(HY_HYDROMETRIC_FEATURE)

    basin_col = None
    if basins is not None:
        basin_col = CATCHMENT_ID if CATCHMENT_ID in basins.columns else LEGACY_BASIN_ID

    for idx, gauge in out.iterrows():
        pt = gauge.geometry
        if pt is None or pt.is_empty:
            continue

        catchment_id: str | None = None
        if basin_col and basins is not None:
            joined = basins[basins.geometry.contains(pt)]
            if not joined.empty:
                catchment_id = str(joined.iloc[0][basin_col])

        nearest_fid: str | None = None
        nearest_dist = float("inf")
        for _, reach in streams.iterrows():
            geom = reach.geometry
            if geom is None or geom.is_empty:
                continue
            snap_dist = pt.distance(geom)
            if snap_dist < nearest_dist:
                nearest_dist = snap_dist
                nearest_fid = str(reach[link_col])

        if catchment_id is None:
            if nearest_fid is None or nearest_dist > search_radius_m:
                continue
            catchment_id = nearest_fid

        reach = _flowpath_row(streams_indexed, catchment_id)
        if reach is None:
            continue

        line = _as_linestring(reach.geometry)
        if line is None:
            continue

        outlet_pt = _outlet_point_for_reach(
            reach, streams_indexed, upstream_map, link_col, down_col, outlet_sentinel,
        )
        if outlet_pt is None:
            continue

        snap = line.interpolate(line.project(pt))
        dist_m, dist_pct = _project_distance_from_outlet_m(line, snap, outlet_pt)

        cid = str(catchment_id)
        out.at[idx, CATCHMENT_ID] = cid
        out.at[idx, HOST_FLOWPATH_ID] = cid
        out.at[idx, LINEAR_ELEMENT_ID] = cid
        out.at[idx, REFERENCE_NEXUS_ID] = outflow_nexus_id_for(cid)
        out.at[idx, DISTANCE_FROM_OUTLET_M] = round(dist_m, 3)
        out.at[idx, DISTANCE_FROM_OUTLET_PCT] = round(dist_pct, 6)

    return out


def link_waterbody_network(
    waterbodies: gpd.GeoDataFrame,
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = -9999,
) -> gpd.GeoDataFrame:
    """
    Add upstreamWaterBody / downstreamWaterBody associations (Section 7.4.2).

    Walks the dendritic catchment graph so non-lake catchments between lakes
    do not break upstream/downstream water-body links.
    """
    from hy_features.schema import HYLAKES_ID, IS_LAKE_CATCHMENT, LEGACY_IS_LAKE, LEGACY_LAKE_ID

    out = waterbodies.copy()
    wb_col = WATERBODY_ID if WATERBODY_ID in out.columns else HYLAKES_ID
    out[UPSTREAM_WATERBODY_ID] = ""
    out[DOWNSTREAM_WATERBODY_ID] = ""

    basin_col = CATCHMENT_ID if CATCHMENT_ID in basins.columns else LEGACY_BASIN_ID
    lake_col = WATERBODY_ID if WATERBODY_ID in basins.columns else LEGACY_LAKE_ID
    is_lake_col = IS_LAKE_CATCHMENT if IS_LAKE_CATCHMENT in basins.columns else LEGACY_IS_LAKE
    link_col = _link_col(streams)

    lake_basins = basins[pd.to_numeric(basins[is_lake_col], errors="coerce").fillna(0) > 0].copy()
    if lake_basins.empty:
        return out

    catchment_to_wb: dict[str, str] = {}
    wb_to_catchment: dict[str, str] = {}
    for _, row in lake_basins.iterrows():
        wb = str(row.get(lake_col, row.get(WATERBODY_ID, "")))
        if wb and wb not in ("-1", "nan", ""):
            cid = str(row[basin_col])
            catchment_to_wb[cid] = wb
            wb_to_catchment[wb] = cid

    lower_map: dict[str, str] = {}
    for _, row in streams.iterrows():
        fid = str(row[link_col])
        lower = str(row.get(LOWER_CATCHMENT_ID, ""))
        if not lower or lower in ("nan", ""):
            raw = row.get(_raw_down_col(streams))
            try:
                raw_int = int(raw)
                lower = "" if raw_int <= 0 or raw_int == outlet_sentinel else str(raw_int)
            except (TypeError, ValueError):
                lower = ""
        lower_map[fid] = lower

    upstream_map = build_upstream_map(streams, outlet_sentinel)

    def _downstream_waterbody(start_cid: str) -> str:
        cid = lower_map.get(start_cid, "")
        visited: set[str] = set()
        while cid and cid not in visited:
            visited.add(cid)
            if cid in catchment_to_wb:
                return catchment_to_wb[cid]
            cid = lower_map.get(cid, "")
        return ""

    def _upstream_waterbody(start_cid: str) -> str:
        visited: set[str] = set()
        stack = list(upstream_map.get(start_cid, []))
        while stack:
            cid = stack.pop()
            if cid in visited:
                continue
            visited.add(cid)
            if cid in catchment_to_wb:
                return catchment_to_wb[cid]
            stack.extend(upstream_map.get(cid, []))
        return ""

    wb_downstream: dict[str, str] = {}
    wb_upstream: dict[str, str] = {}
    for cid, wb in catchment_to_wb.items():
        down_wb = _downstream_waterbody(cid)
        if down_wb:
            wb_downstream[wb] = down_wb
        up_wb = _upstream_waterbody(cid)
        if up_wb:
            wb_upstream[wb] = up_wb

    for idx, row in out.iterrows():
        wb_id = str(row[wb_col])
        if wb_id in wb_downstream:
            out.at[idx, DOWNSTREAM_WATERBODY_ID] = wb_downstream[wb_id]
        if wb_id in wb_upstream:
            out.at[idx, UPSTREAM_WATERBODY_ID] = wb_upstream[wb_id]

    if HYF_TYPE in out.columns:
        out[HYF_TYPE_URI] = out[HYF_TYPE].map(hyf_type_uri)

    return out


def filter_placed_hydrometric(
    hydrometric: gpd.GeoDataFrame | None,
) -> tuple[gpd.GeoDataFrame | None, int]:
    """
    Keep only gauges with a complete positionOnRiver (host reach assigned).

    Unplaced gauges are omitted from ``hydrometric_feature`` export so mandatory
    HY_IndirectPosition associations are never empty on exported features.
    """
    if hydrometric is None or hydrometric.empty:
        return hydrometric, 0
    mask = hydrometric[HOST_FLOWPATH_ID].astype(str).str.len() > 0
    placed = hydrometric[mask].copy()
    skipped = int((~mask).sum())
    if placed.empty:
        return None, skipped
    return placed, skipped


def merge_hydro_locations_into_nexus(
    nexus_gdf: gpd.GeoDataFrame,
    hydro_locations: gpd.GeoDataFrame | None,
) -> gpd.GeoDataFrame:
    """Append pour-point hydro locations as additional nexus realizations."""
    if hydro_locations is None or hydro_locations.empty:
        return nexus_gdf

    from hy_features.schema import HY_HYDRO_LOCATION

    extra = hydro_locations.copy()
    if NEXUS_ID not in extra.columns or extra[NEXUS_ID].astype(str).str.len().eq(0).all():
        if "name" in extra.columns:
            extra[NEXUS_ID] = "nx_loc_" + extra["name"].astype(str)
        elif WATERBODY_ID in extra.columns:
            extra[NEXUS_ID] = "nx_loc_" + extra[WATERBODY_ID].astype(str) + "_" + extra.index.astype(str)
        else:
            extra[NEXUS_ID] = "nx_loc_" + extra.index.astype(str)

    if REALIZED_NEXUS_ID not in extra.columns:
        extra[REALIZED_NEXUS_ID] = extra[NEXUS_ID].astype(str)

    extra[HYF_TYPE] = HY_HYDRO_LOCATION
    extra[HYF_TYPE_URI] = hyf_type_uri(HY_HYDRO_LOCATION)
    if CONTRIBUTING_CATCHMENT_ID not in extra.columns:
        extra[CONTRIBUTING_CATCHMENT_ID] = ""
    if RECEIVING_CATCHMENT_ID not in extra.columns:
        extra[RECEIVING_CATCHMENT_ID] = ""

    for col in nexus_gdf.columns:
        if col not in extra.columns:
            extra[col] = "" if col != "geometry" else None

    aligned = extra[list(nexus_gdf.columns)]
    return gpd.GeoDataFrame(
        pd.concat([nexus_gdf, aligned], ignore_index=True),
        crs=nexus_gdf.crs,
    )


def build_dendritic_catchment_table(
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = -9999,
) -> pd.DataFrame:
    """Non-spatial HY_DendriticCatchment attribute table."""
    basin_col = CATCHMENT_ID if CATCHMENT_ID in basins.columns else LEGACY_BASIN_ID
    down_col = LOWER_CATCHMENT_ID if LOWER_CATCHMENT_ID in basins.columns else LEGACY_LOWER_ID
    upstream_map = build_upstream_map(streams, outlet_sentinel)

    down_map = {}
    if _link_col(streams) in streams.columns:
        lc = _link_col(streams)
        dc = _raw_down_col(streams)
        for _, row in streams.iterrows():
            down_map[str(row[lc])] = row[dc]

    records = []
    for _, row in basins.iterrows():
        cid = str(row[basin_col])
        lower = str(row.get(down_col, "")) if down_col in row.index else ""
        if lower in ("", "nan"):
            raw = down_map.get(cid, "")
            try:
                raw_int = int(raw)
                lower = "" if raw_int <= 0 or raw_int == outlet_sentinel else str(raw_int)
            except (TypeError, ValueError):
                lower = ""

        ups = upstream_map.get(cid, [])
        records.append({
            CATCHMENT_ID: cid,
            HYF_TYPE: HY_DENDRITIC_CATCHMENT,
            HYF_TYPE_URI: hyf_type_uri(HY_DENDRITIC_CATCHMENT),
            OUTFLOW_NEXUS_ID: outflow_nexus_id_for(cid),
            INFLOW_NEXUS_ID: outflow_nexus_id_for(ups[0]) if len(ups) == 1 else (
                ",".join(outflow_nexus_id_for(u) for u in ups) if ups else ""
            ),
            LOWER_CATCHMENT_ID: lower,
            UPPER_CATCHMENT_ID: ups[0] if len(ups) == 1 else (",".join(ups) if ups else ""),
            WATERBODY_ID: str(row.get(WATERBODY_ID, "")),
            DRAINAGE_PATTERN_COL: DRAINAGE_PATTERN,
        })
    return pd.DataFrame(records)


def build_hydrographic_network_metadata(
    streams: gpd.GeoDataFrame,
    waterbodies: gpd.GeoDataFrame | None,
    network_id: str = "study_hydrographic_network",
    basins: gpd.GeoDataFrame | None = None,
) -> dict:
    """HY_HydrographicNetwork metadata record (Section 7.4.2)."""
    link_col = _link_col(streams)
    flowpath_ids = streams[link_col].astype(str).tolist()
    wb_ids: set[str] = set()
    if waterbodies is not None and WATERBODY_ID in waterbodies.columns:
        wb_ids.update(waterbodies[WATERBODY_ID].astype(str).tolist())
    if basins is not None and WATERBODY_ID in basins.columns:
        for wb in basins[WATERBODY_ID].astype(str):
            if wb and wb not in ("-1", "nan", ""):
                wb_ids.add(wb)
    wb_list = sorted(wb_ids)

    return {
        NETWORK_ID: network_id,
        "hyf_type": HY_HYDROGRAPHIC_NETWORK,
        "hyf_type_uri": hyf_type_uri(HY_HYDROGRAPHIC_NETWORK),
        "drainage_pattern": DRAINAGE_PATTERN,
        "flowpath_members": flowpath_ids,
        "waterbody_members": wb_list,
        "flowpath_count": len(flowpath_ids),
        "waterbody_count": len(wb_list),
    }
