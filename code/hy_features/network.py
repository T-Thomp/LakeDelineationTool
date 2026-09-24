"""
Build HY_Features network topology: nexuses, catchment associations, river referencing.
"""

from __future__ import annotations

from collections import defaultdict

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point
from shapely.ops import linemerge

from hy_features.schema import (
    CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    DEFAULT_OUTLET_SENTINEL,
    DISTANCE_DESCRIPTION,
    DISTANCE_DESCRIPTION_UPSTREAM,
    DISTANCE_FROM_OUTLET_M,
    DISTANCE_FROM_OUTLET_PCT,
    DOWNSTREAM_WATERBODY_ID,
    DRAINAGE_PATTERN,
    FLOWPATH_ID,
    HOST_FLOWPATH_ID,
    HYF_TYPE,
    HYF_TYPE_URI,
    HY_DENDRITIC_CATCHMENT,
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
    RECEIVING_CATCHMENT_ID,
    REFERENCE_NEXUS_ID,
    UPPER_CATCHMENT_ID,
    UPSTREAM_WATERBODY_ID,
    WATERBODY_ID,
    hyf_type_uri,
    inflow_nexus_id_for,
    normalize_id,
    outflow_nexus_id_for,
)

# Max distance (projected CRS metres) from a pour point to a reach outlet for realizedNexus
HYDRO_LOCATION_SNAP_M = 250.0


def _link_col(streams: gpd.GeoDataFrame) -> str:
    return FLOWPATH_ID if FLOWPATH_ID in streams.columns else LEGACY_FLOWPATH_ID


def _raw_down_col(streams: gpd.GeoDataFrame) -> str:
    """Original numeric downstream id column before enrichment blanking."""
    if LEGACY_LOWER_ID in streams.columns:
        return LEGACY_LOWER_ID
    return LOWER_CATCHMENT_ID


def _projected(gdf: gpd.GeoDataFrame, crs=None) -> gpd.GeoDataFrame:
    """Reproject to ``crs`` or, for geographic data, a local UTM zone (metre distances)."""
    if gdf.crs is None:
        return gdf
    if crs is not None:
        return gdf if gdf.crs == crs else gdf.to_crs(crs)
    if gdf.crs.is_geographic:
        return gdf.to_crs(gdf.estimate_utm_crs())
    return gdf


def _as_linestring(geom) -> LineString | None:
    """Normalize a flowpath geometry to a single LineString."""
    if geom is None or geom.is_empty:
        return None
    if geom.geom_type == "LineString":
        return geom
    if geom.geom_type == "MultiLineString":
        merged = linemerge(geom)
        if merged.geom_type == "LineString":
            return merged
        parts = [part for part in getattr(merged, "geoms", []) if part.length > 0]
        if not parts:
            return None
        return max(parts, key=lambda part: part.length)
    return None


def _line_endpoints(line: LineString) -> tuple[Point, Point]:
    coords = list(line.coords)
    return Point(coords[0]), Point(coords[-1])


def _parse_downstream_id(down_val: object, outlet_sentinel: int) -> str:
    """Downstream catchment id as text; ``""`` at the domain outlet."""
    text = normalize_id(down_val)
    try:
        down_num = float(text)
    except ValueError:
        return ""
    if down_num <= 0 or down_num == outlet_sentinel:
        return ""
    return text


def _flowpath_row(
    streams_indexed: gpd.GeoDataFrame,
    flowpath_id: object,
) -> pd.Series | None:
    """Look up a flowpath row by id (string or int index)."""
    for key in (flowpath_id, normalize_id(flowpath_id)):
        if key in streams_indexed.index:
            row = streams_indexed.loc[key]
            return row.iloc[0] if isinstance(row, pd.DataFrame) else row
    try:
        numeric = int(normalize_id(flowpath_id))
    except (TypeError, ValueError):
        return None
    if numeric in streams_indexed.index:
        row = streams_indexed.loc[numeric]
        return row.iloc[0] if isinstance(row, pd.DataFrame) else row
    return None


def build_lower_map(streams: gpd.GeoDataFrame, outlet_sentinel: int) -> dict[str, str]:
    """Map flowpath/catchment id -> receiving catchment id (``""`` at domain outlet)."""
    link_col = _link_col(streams)
    down_col = _raw_down_col(streams)
    if down_col not in streams.columns:
        return {normalize_id(fid): "" for fid in streams[link_col]}
    return {
        normalize_id(fid): _parse_downstream_id(down, outlet_sentinel)
        for fid, down in zip(streams[link_col], streams[down_col])
    }


def build_upstream_map(streams: gpd.GeoDataFrame, outlet_sentinel: int) -> dict[str, list[str]]:
    """Map downstream catchment id -> list of upstream flowpath/catchment ids."""
    upstream: dict[str, list[str]] = defaultdict(list)
    for fid, lower in build_lower_map(streams, outlet_sentinel).items():
        if lower:
            upstream[lower].append(fid)
    return {k: sorted(v, key=_id_sort_key) for k, v in upstream.items()}


def _id_sort_key(value: str) -> tuple[int, float, str]:
    try:
        return (0, float(value), value)
    except ValueError:
        return (1, 0.0, value)


def _outlet_point_for_reach(
    row: pd.Series,
    streams_indexed: gpd.GeoDataFrame,
    upstream_map: dict[str, list[str]],
    lower: str,
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

    if lower:
        down_row = _flowpath_row(streams_indexed, lower)
        if down_row is not None and down_row.geometry is not None:
            down_geom = down_row.geometry
            return end_a if end_a.distance(down_geom) <= end_b.distance(down_geom) else end_b

    upstream_ids = upstream_map.get(normalize_id(row.name), [])
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


def build_reach_outlets(
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> gpd.GeoDataFrame:
    """One point per reach at its topologic outlet, tagged with its outflow nexus id."""
    link_col = _link_col(streams)
    indexed = streams.copy()
    indexed.index = indexed[link_col].map(normalize_id)
    upstream_map = build_upstream_map(streams, outlet_sentinel)
    lower_map = build_lower_map(streams, outlet_sentinel)

    records: list[dict] = []
    for cid, row in indexed.iterrows():
        lower = lower_map.get(cid, "")
        pt = _outlet_point_for_reach(row, indexed, upstream_map, lower)
        if pt is None:
            continue
        records.append({
            CATCHMENT_ID: cid,
            NEXUS_ID: outflow_nexus_id_for(cid, lower),
            RECEIVING_CATCHMENT_ID: lower,
            "geometry": pt,
        })
    columns = [CATCHMENT_ID, NEXUS_ID, RECEIVING_CATCHMENT_ID, "geometry"]
    if not records:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=streams.crs)
    return gpd.GeoDataFrame(records, columns=columns, geometry="geometry", crs=streams.crs)


def build_hydro_nexus_layer(
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> gpd.GeoDataFrame:
    """
    Build HY_HydroNexus points (HY_Features Section 7.3.2).

    One nexus per receiving catchment, shared by every catchment that drains into
    it (``contributingCatchment`` 0..*), plus one terminal nexus per domain outlet.
    The point is the outlet of the first contributing reach (by id).
    """
    outlets = build_reach_outlets(streams, outlet_sentinel)
    columns = [NEXUS_ID, HYF_TYPE, HYF_TYPE_URI, CONTRIBUTING_CATCHMENT_ID,
               RECEIVING_CATCHMENT_ID, "geometry"]
    if outlets.empty:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=streams.crs)

    records: list[dict] = []
    for nexus_id, group in outlets.groupby(NEXUS_ID, sort=False):
        group = group.sort_values(CATCHMENT_ID, key=lambda s: s.map(_id_sort_key))
        records.append({
            NEXUS_ID: nexus_id,
            HYF_TYPE: HY_HYDRO_NEXUS,
            HYF_TYPE_URI: hyf_type_uri(HY_HYDRO_NEXUS),
            CONTRIBUTING_CATCHMENT_ID: ",".join(group[CATCHMENT_ID]),
            RECEIVING_CATCHMENT_ID: group[RECEIVING_CATCHMENT_ID].iloc[0],
            "geometry": group.geometry.iloc[0],
        })
    return gpd.GeoDataFrame(records, columns=columns, geometry="geometry", crs=streams.crs)


def _link_nexuses(
    gdf: gpd.GeoDataFrame,
    id_col: str,
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int,
    default_type: str,
) -> gpd.GeoDataFrame:
    out = gdf.copy()
    upstream_map = build_upstream_map(streams, outlet_sentinel)
    lower_map = build_lower_map(streams, outlet_sentinel)

    ids = out[id_col].map(normalize_id)
    out[OUTFLOW_NEXUS_ID] = [outflow_nexus_id_for(cid, lower_map.get(cid, "")) for cid in ids]
    out[INFLOW_NEXUS_ID] = [inflow_nexus_id_for(cid) if upstream_map.get(cid) else "" for cid in ids]
    out[UPPER_CATCHMENT_ID] = [",".join(upstream_map.get(cid, [])) for cid in ids]

    if HYF_TYPE in out.columns:
        out[HYF_TYPE_URI] = out[HYF_TYPE].map(hyf_type_uri)
    else:
        out[HYF_TYPE_URI] = hyf_type_uri(default_type)
    return out


def link_catchment_nexuses(
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> gpd.GeoDataFrame:
    """Add outflow/inflow nexus and upper catchment ids to catchment areas."""
    from hy_features.schema import HY_CATCHMENT_AREA

    basin_col = CATCHMENT_ID if CATCHMENT_ID in basins.columns else LEGACY_BASIN_ID
    return _link_nexuses(basins, basin_col, streams, outlet_sentinel, HY_CATCHMENT_AREA)


def link_flowpath_nexuses(
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> gpd.GeoDataFrame:
    """Add nexus ids to flowpath layer."""
    from hy_features.schema import HY_FLOWPATH

    return _link_nexuses(streams, _link_col(streams), streams, outlet_sentinel, HY_FLOWPATH)


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
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> gpd.GeoDataFrame:
    """
    HY_HydrometricFeature.positionOnRiver via HY_IndirectPosition (Section 7.3.3).

    Uses the containing catchment id (TauDEM ``DN``) for ``catchment_id``,
    ``host_flowpath_id``, and ``linear_element_id``. In this dendritic fabric each
    catchment is realized by the flowpath with the same id. Distance along the
    reach is measured on that host flowpath after projecting the gauge onto it.
    Distances and the search radius are evaluated in metres (geographic inputs are
    reprojected to a local UTM zone for the calculation only).
    """
    from hy_features.schema import HY_HYDROMETRIC_FEATURE

    out = gauges.copy()
    out[HOST_FLOWPATH_ID] = ""
    out[CATCHMENT_ID] = ""
    out[LINEAR_ELEMENT_ID] = ""
    out[REFERENCE_NEXUS_ID] = ""
    out[DISTANCE_FROM_OUTLET_M] = 0.0
    out[DISTANCE_FROM_OUTLET_PCT] = 0.0
    out[DISTANCE_DESCRIPTION] = ""
    out[HYF_TYPE_URI] = hyf_type_uri(HY_HYDROMETRIC_FEATURE)
    if out.empty or streams.empty:
        return out

    link_col = _link_col(streams)
    streams_w = _projected(streams)
    gauges_w = _projected(gauges, streams_w.crs)
    streams_indexed = streams_w.copy()
    streams_indexed.index = streams_indexed[link_col].map(normalize_id)
    upstream_map = build_upstream_map(streams, outlet_sentinel)
    lower_map = build_lower_map(streams, outlet_sentinel)

    valid = gauges_w.geometry.notna() & ~gauges_w.geometry.is_empty
    positions = [i for i, ok in enumerate(valid) if ok]
    if not positions:
        return out
    points = gauges_w.geometry.iloc[positions]

    containing: dict[int, str] = {}
    if basins is not None and not basins.empty:
        basin_col = CATCHMENT_ID if CATCHMENT_ID in basins.columns else LEGACY_BASIN_ID
        basins_w = _projected(basins, streams_w.crs)
        pt_idx, poly_idx = basins_w.sindex.query(points.values, predicate="within")
        for p, b in zip(pt_idx, poly_idx):
            containing.setdefault(positions[int(p)], normalize_id(basins_w.iloc[int(b)][basin_col]))

    nearest: dict[int, tuple[str, float]] = {}
    unplaced = [pos for pos in positions if pos not in containing]
    if unplaced:
        (pt_idx, line_idx), dists = streams_w.sindex.nearest(
            gauges_w.geometry.iloc[unplaced].values, return_all=False, return_distance=True,
        )
        for p, s, d in zip(pt_idx, line_idx, dists):
            nearest[unplaced[int(p)]] = (normalize_id(streams_w.iloc[int(s)][link_col]), float(d))

    for pos in positions:
        catchment_id = containing.get(pos)
        if catchment_id is None:
            fid, dist = nearest.get(pos, ("", float("inf")))
            if not fid or dist > search_radius_m:
                continue
            catchment_id = fid

        reach = _flowpath_row(streams_indexed, catchment_id)
        if reach is None:
            continue
        line = _as_linestring(reach.geometry)
        if line is None:
            continue
        lower = lower_map.get(catchment_id, "")
        outlet_pt = _outlet_point_for_reach(reach, streams_indexed, upstream_map, lower)
        if outlet_pt is None:
            continue

        pt = gauges_w.geometry.iloc[pos]
        snap = line.interpolate(line.project(pt))
        dist_m, dist_pct = _project_distance_from_outlet_m(line, snap, outlet_pt)

        idx = out.index[pos]
        out.at[idx, CATCHMENT_ID] = catchment_id
        out.at[idx, HOST_FLOWPATH_ID] = catchment_id
        out.at[idx, LINEAR_ELEMENT_ID] = catchment_id
        out.at[idx, REFERENCE_NEXUS_ID] = outflow_nexus_id_for(catchment_id, lower)
        out.at[idx, DISTANCE_FROM_OUTLET_M] = round(dist_m, 3)
        out.at[idx, DISTANCE_FROM_OUTLET_PCT] = round(dist_pct, 6)
        out.at[idx, DISTANCE_DESCRIPTION] = DISTANCE_DESCRIPTION_UPSTREAM

    return out


def link_waterbody_network(
    waterbodies: gpd.GeoDataFrame,
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> gpd.GeoDataFrame:
    """
    Add upstreamWaterBody / downstreamWaterBody associations (Section 7.4.2).

    Walks the dendritic catchment graph so non-lake catchments between lakes
    do not break upstream/downstream water-body links. ``upstream_waterbody_id``
    lists the nearest lake on every upstream branch (comma-separated).
    """
    from hy_features.schema import HYLAKES_ID, IS_LAKE_CATCHMENT, LEGACY_IS_LAKE, LEGACY_LAKE_ID

    out = waterbodies.copy()
    wb_col = WATERBODY_ID if WATERBODY_ID in out.columns else HYLAKES_ID
    out[UPSTREAM_WATERBODY_ID] = ""
    out[DOWNSTREAM_WATERBODY_ID] = ""

    basin_col = CATCHMENT_ID if CATCHMENT_ID in basins.columns else LEGACY_BASIN_ID
    lake_col = WATERBODY_ID if WATERBODY_ID in basins.columns else LEGACY_LAKE_ID
    is_lake_col = IS_LAKE_CATCHMENT if IS_LAKE_CATCHMENT in basins.columns else LEGACY_IS_LAKE

    lake_basins = basins[pd.to_numeric(basins[is_lake_col], errors="coerce").fillna(0) > 0]
    if lake_basins.empty:
        return out

    catchment_to_wb: dict[str, str] = {}
    for _, row in lake_basins.iterrows():
        wb = normalize_id(row.get(lake_col, ""))
        if wb and wb != "-1":
            catchment_to_wb[normalize_id(row[basin_col])] = wb

    lower_map = build_lower_map(streams, outlet_sentinel)
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

    def _upstream_waterbodies(start_cid: str) -> list[str]:
        found: list[str] = []
        visited: set[str] = set()
        stack = list(upstream_map.get(start_cid, []))
        while stack:
            cid = stack.pop()
            if cid in visited:
                continue
            visited.add(cid)
            if cid in catchment_to_wb:
                found.append(catchment_to_wb[cid])
                continue
            stack.extend(upstream_map.get(cid, []))
        return sorted(set(found), key=_id_sort_key)

    wb_downstream: dict[str, str] = {}
    wb_upstream: dict[str, str] = {}
    for cid, wb in catchment_to_wb.items():
        down_wb = _downstream_waterbody(cid)
        if down_wb:
            wb_downstream[wb] = down_wb
        up_wbs = _upstream_waterbodies(cid)
        if up_wbs:
            wb_upstream[wb] = ",".join(up_wbs)

    wb_ids = out[wb_col].map(normalize_id)
    out[DOWNSTREAM_WATERBODY_ID] = wb_ids.map(wb_downstream).fillna("")
    out[UPSTREAM_WATERBODY_ID] = wb_ids.map(wb_upstream).fillna("")

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


def link_hydro_locations_to_nexus(
    hydro_locations: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
    snap_distance_m: float = HYDRO_LOCATION_SNAP_M,
) -> gpd.GeoDataFrame:
    """
    Set ``realized_nexus_id`` on pour points (``HY_HydroLocation.realizedNexus``).

    Each point realizes the outflow nexus of the reach whose outlet is nearest,
    when that outlet lies within ``snap_distance_m``; otherwise it is left empty
    (a hydro location need not realize a nexus).
    """
    out = hydro_locations.copy()
    out[REALIZED_NEXUS_ID] = ""
    if out.empty:
        return out

    outlets = build_reach_outlets(streams, outlet_sentinel)
    if outlets.empty:
        return out
    outlets_w = _projected(outlets)
    locs_w = _projected(hydro_locations, outlets_w.crs)

    valid = locs_w.geometry.notna() & ~locs_w.geometry.is_empty
    positions = [i for i, ok in enumerate(valid) if ok]
    if not positions:
        return out
    (pt_idx, outlet_idx), dists = outlets_w.sindex.nearest(
        locs_w.geometry.iloc[positions].values, return_all=False, return_distance=True,
    )
    for p, o, d in zip(pt_idx, outlet_idx, dists):
        if float(d) <= snap_distance_m:
            out.at[out.index[positions[int(p)]], REALIZED_NEXUS_ID] = outlets_w.iloc[int(o)][NEXUS_ID]
    return out


def build_dendritic_catchment_table(
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> pd.DataFrame:
    """Non-spatial HY_DendriticCatchment attribute table."""
    basin_col = CATCHMENT_ID if CATCHMENT_ID in basins.columns else LEGACY_BASIN_ID
    upstream_map = build_upstream_map(streams, outlet_sentinel)
    lower_map = build_lower_map(streams, outlet_sentinel)

    records = []
    for _, row in basins.iterrows():
        cid = normalize_id(row[basin_col])
        lower = lower_map.get(cid, "")
        ups = upstream_map.get(cid, [])
        records.append({
            CATCHMENT_ID: cid,
            HYF_TYPE: HY_DENDRITIC_CATCHMENT,
            HYF_TYPE_URI: hyf_type_uri(HY_DENDRITIC_CATCHMENT),
            OUTFLOW_NEXUS_ID: outflow_nexus_id_for(cid, lower),
            INFLOW_NEXUS_ID: inflow_nexus_id_for(cid) if ups else "",
            LOWER_CATCHMENT_ID: lower,
            UPPER_CATCHMENT_ID: ",".join(ups),
            WATERBODY_ID: str(row.get(WATERBODY_ID, "")),
        })
    return pd.DataFrame(records)


def build_hydrographic_network_metadata(
    streams: gpd.GeoDataFrame,
    waterbodies: gpd.GeoDataFrame | None,
    network_id: str = "study_hydrographic_network",
    basins: gpd.GeoDataFrame | None = None,
    nexus: gpd.GeoDataFrame | None = None,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> dict:
    """HY_HydrographicNetwork metadata record (Section 7.4.2)."""
    link_col = _link_col(streams)
    flowpath_ids = streams[link_col].map(normalize_id).tolist()
    wb_ids: set[str] = set()
    if waterbodies is not None and WATERBODY_ID in waterbodies.columns:
        wb_ids.update(waterbodies[WATERBODY_ID].map(normalize_id).tolist())
    if basins is not None and WATERBODY_ID in basins.columns:
        for wb in basins[WATERBODY_ID].map(normalize_id):
            if wb and wb != "-1":
                wb_ids.add(wb)
    wb_ids.discard("")
    wb_list = sorted(wb_ids, key=_id_sort_key)

    lower_map = build_lower_map(streams, outlet_sentinel)
    outlet_catchments = sorted((cid for cid, lower in lower_map.items() if not lower), key=_id_sort_key)

    nexus_contributing: list[dict] = []
    if nexus is not None and not nexus.empty:
        for _, row in nexus.iterrows():
            for cid in str(row[CONTRIBUTING_CATCHMENT_ID]).split(","):
                if cid:
                    nexus_contributing.append({
                        NEXUS_ID: row[NEXUS_ID],
                        CONTRIBUTING_CATCHMENT_ID: cid,
                        RECEIVING_CATCHMENT_ID: row[RECEIVING_CATCHMENT_ID] or None,
                    })

    return {
        NETWORK_ID: network_id,
        "hyf_type": HY_HYDROGRAPHIC_NETWORK,
        "hyf_type_uri": hyf_type_uri(HY_HYDROGRAPHIC_NETWORK),
        "realized_catchment": outlet_catchments,
        "channel_network_drainage_pattern": DRAINAGE_PATTERN,
        "flowpath_members": flowpath_ids,
        "waterbody_members": wb_list,
        "flowpath_count": len(flowpath_ids),
        "waterbody_count": len(wb_list),
        "nexus_contributing_catchment": nexus_contributing,
    }
