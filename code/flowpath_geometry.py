"""Normalize TauDEM reach geometries after a line dissolve.

A dissolve can return a GeometryCollection, a branch, or several pieces that do
not connect. Connected pieces that form one path are joined into a LineString
with the pour point kept at the first vertex. Branches and gaps stay a
MultiLineString.

A reach that still has no line is not given a stand-in geometry. Its basin is
unioned into the next downstream basin that has a channel, and the empty reach
is dropped. A basin with no stream row is folded the same way. One with no
downstream channel joins the upstream main channel. A gauge basin joins that
upstream channel when it has one, so the pour point stays where it is.
"""

from __future__ import annotations

from collections import defaultdict

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, MultiLineString
from shapely.ops import unary_union

# TauDEM / MESH id columns, with the GeoPackage names accepted when present.
FLOWPATH_ID = "flowpath_id"
LINKNO = "LINKNO"
DSLINKNO = "DSLINKNO"
LOWER_CATCHMENT_ID = "lower_catchment_id"
CATCHMENT_ID = "catchment_id"
BASIN_ID = "DN"
DEFAULT_OUTLET_SENTINEL = -9999
_US_LINK_COLS = ("USLINKNO1", "USLINKNO2")
_AREA_COLS = ("area_km2", "lake_area")
_GAUGE_COLS = ("STATION_NU", "STATION_NO", "STATION_NUMBER")


def normalize_id(value: object) -> str:
    """Render an identifier as text, collapsing integral floats (``1.0`` -> ``"1"``)."""
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) or type(value).__name__.startswith("float"):
        f = float(value)
        return str(int(f)) if f.is_integer() else str(f)
    text = str(value).strip()
    if text.lower() in ("nan", "none", "<na>"):
        return ""
    try:
        f = float(text)
    except ValueError:
        return text
    return str(int(f)) if f.is_integer() and "." in text else text


def _link_col(streams: gpd.GeoDataFrame) -> str:
    return FLOWPATH_ID if FLOWPATH_ID in streams.columns else LINKNO


def _down_col(frame: gpd.GeoDataFrame) -> str | None:
    if DSLINKNO in frame.columns:
        return DSLINKNO
    if LOWER_CATCHMENT_ID in frame.columns:
        return LOWER_CATCHMENT_ID
    return None


def _line_parts(geom) -> list[LineString]:
    """Non-empty line parts, including lines stored inside a GeometryCollection."""
    if geom is None:
        return []
    try:
        if pd.isna(geom):
            return []
    except (TypeError, ValueError):
        pass
    if getattr(geom, "is_empty", True):
        return []
    gtype = getattr(geom, "geom_type", "")
    if gtype == "LineString":
        return [geom] if geom.length > 0 else []
    if gtype in ("MultiLineString", "GeometryCollection"):
        parts: list[LineString] = []
        for part in geom.geoms:
            parts.extend(_line_parts(part))
        return parts
    return []


def _coord_key(coord) -> tuple[float, float]:
    return (round(float(coord[0]), 6), round(float(coord[1]), 6))


def _merge_single_path(parts: list[LineString]) -> LineString | None:
    """
    Join parts that form one path.

    A branch (a vertex where three or more ends meet) or a gap (more than one
    path) is left alone. The merged line starts at the pour point: the path end
    that is a part's first vertex and not another part's last vertex.
    """
    ends: list[tuple[tuple, tuple]] = []
    for part in parts:
        coords = list(part.coords)
        if len(coords) < 2:
            return None
        ends.append((coords[0], coords[-1]))

    degree: dict[tuple[float, float], int] = defaultdict(int)
    incidents: dict[tuple[float, float], list[tuple[int, bool]]] = defaultdict(list)
    for index, (start, end) in enumerate(ends):
        start_key = _coord_key(start)
        end_key = _coord_key(end)
        if start_key == end_key:
            return None
        degree[start_key] += 1
        degree[end_key] += 1
        incidents[start_key].append((index, True))
        incidents[end_key].append((index, False))

    if any(count > 2 for count in degree.values()):
        return None
    endpoints = [key for key, count in degree.items() if count == 1]
    if len(endpoints) != 2:
        return None

    start_keys = {_coord_key(start) for start, _ in ends}
    end_keys = {_coord_key(end) for _, end in ends}
    pour_points = [key for key in endpoints if key in start_keys and key not in end_keys]
    if len(pour_points) != 1:
        return None

    visited: set[int] = set()
    merged: list[tuple] = []
    key = pour_points[0]
    while True:
        choices = [item for item in incidents[key] if item[0] not in visited]
        if not choices:
            break
        if len(choices) != 1:
            return None
        index, at_start = choices[0]
        visited.add(index)
        coords = list(parts[index].coords)
        if not at_start:
            coords = list(reversed(coords))
        merged.extend(coords if not merged else coords[1:])
        key = _coord_key(ends[index][1] if at_start else ends[index][0])

    if len(visited) != len(parts) or len(merged) < 2:
        return None
    return LineString(merged)


def coerce_flowpath_geometry(geom):
    """
    LineString or MultiLineString for a flowpath.

    GeometryCollections are reduced to their line parts. One continuous path is
    joined, with its pour point at the first vertex. Disconnected parts and
    branches are returned as a MultiLineString in their original directions.
    Returns None when no line remains.
    """
    parts = _line_parts(geom)
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    merged = _merge_single_path(parts)
    if merged is not None:
        return merged
    return MultiLineString(parts)


def _as_linestring(geom) -> LineString | None:
    """One line for point placement. Branches are not joined; the longest part is used."""
    coerced = coerce_flowpath_geometry(geom)
    if coerced is None:
        return None
    if coerced.geom_type == "LineString":
        return coerced
    parts = _line_parts(coerced)
    if not parts:
        return None
    return max(parts, key=lambda part: part.length)


def _apply_coerce(streams: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = streams.copy()
    geoms = [coerce_flowpath_geometry(geom) for geom in out.geometry]
    return out.set_geometry(gpd.GeoSeries(geoms, index=out.index, crs=out.crs))


def _parse_downstream_id(down_val: object, outlet_sentinel: int) -> str:
    """Downstream catchment id as text; ``""`` at the domain outlet."""
    text = normalize_id(down_val)
    try:
        down_num = float(text)
    except ValueError:
        return text
    if down_num <= 0 or down_num == outlet_sentinel:
        return ""
    return text


def _basin_id_column(basins: gpd.GeoDataFrame, stream_ids: set[str]) -> str | None:
    best = None
    best_n = 0
    for col in (LINKNO, BASIN_ID, CATCHMENT_ID, FLOWPATH_ID):
        if col not in basins.columns:
            continue
        matched = sum(normalize_id(value) in stream_ids for value in basins[col])
        if matched > best_n:
            best, best_n = col, matched
    return best


def _id_value(text: str) -> int | str:
    try:
        number = float(text)
    except ValueError:
        return text
    return int(number) if number.is_integer() else text


def _show_ids(ids: list[str]) -> str:
    shown = ", ".join(ids[:8])
    extra = f" … ({len(ids)} total)" if len(ids) > 8 else ""
    return shown + extra


def _station_text(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none"):
        return ""
    return text


def _add_station(hits: dict[str, list[str]], basin_id: str, text: str) -> None:
    parts = hits.setdefault(basin_id, [])
    for piece in text.split(","):
        piece = piece.strip()
        if piece and piece not in parts:
            parts.append(piece)


def _gauge_hits(
    basins: gpd.GeoDataFrame,
    basin_col: str,
    gauges: gpd.GeoDataFrame | None,
) -> dict[str, list[str]]:
    """Basin id to station ids. A basin with a gauge and no readable id is included."""
    hits: dict[str, list[str]] = {}
    for column in _GAUGE_COLS:
        if column not in basins.columns:
            continue
        for basin_id, value in zip(basins[basin_col], basins[column]):
            text = _station_text(value)
            if text:
                _add_station(hits, normalize_id(basin_id), text)
    if gauges is None or getattr(gauges, "empty", True):
        return hits
    points = gauges
    if points.crs is not None and basins.crs is not None and points.crs != basins.crs:
        points = points.to_crs(basins.crs)
    station_col = next((column for column in _GAUGE_COLS if column in points.columns), None)
    point_cols = [points.geometry.name] + ([station_col] if station_col else [])
    joined = gpd.sjoin(
        points[point_cols],
        basins[[basin_col, basins.geometry.name]],
        how="inner",
        predicate="intersects",
    )
    if station_col is None:
        for basin_id in joined[basin_col]:
            hits.setdefault(normalize_id(basin_id), [])
        return hits
    for basin_id, value in zip(joined[basin_col], joined[station_col]):
        basin_key = normalize_id(basin_id)
        text = _station_text(value)
        if text:
            _add_station(hits, basin_key, text)
        else:
            hits.setdefault(basin_key, [])
    return hits


def _merge_station_ids(
    basins: gpd.GeoDataFrame,
    target_at: int,
    source_at: list[int],
    hits: dict[str, list[str]],
    basin_ids_at: dict[int, str],
) -> None:
    """Append gauge station ids from the absorbed basins onto the receiver."""
    column = next((name for name in _GAUGE_COLS if name in basins.columns), "STATION_NU")
    if column not in basins.columns:
        basins[column] = ""
    parts: list[str] = []
    for index in (target_at, *source_at):
        for piece in _station_text(basins.iloc[index][column]).split(","):
            piece = piece.strip()
            if piece and piece not in parts:
                parts.append(piece)
        for piece in hits.get(basin_ids_at.get(index, ""), []):
            if piece not in parts:
                parts.append(piece)
    basins.at[basins.index[target_at], column] = ", ".join(parts)


def _polygon(geom):
    """Polygon used for overlap tests. Invalid rings are repaired first."""
    if geom is None or getattr(geom, "is_empty", True):
        return None
    if not getattr(geom, "is_valid", True):
        try:
            from shapely import make_valid
            geom = make_valid(geom)
        except Exception:
            geom = geom.buffer(0)
    if geom is None or getattr(geom, "is_empty", True):
        return None
    return geom


def _adjacent_channel(
    basins: gpd.GeoDataFrame,
    positions: dict[str, int],
    basin_id: str,
    receivers: set[str],
) -> str | None:
    """Channelled basin that this catchment overlaps, borders, or touches."""
    index = positions.get(basin_id)
    if index is None or not receivers:
        return None
    geom = _polygon(basins.geometry.iloc[index])
    if geom is None:
        return None
    border = geom.boundary
    best: str | None = None
    best_score = (0.0, 0.0)
    nearest: str | None = None
    nearest_dist = None
    for channel_id in receivers:
        if channel_id == basin_id:
            continue
        other_at = positions.get(channel_id)
        if other_at is None:
            continue
        other = _polygon(basins.geometry.iloc[other_at])
        if other is None:
            continue
        overlap = float(geom.intersection(other).area or 0.0)
        shared = border.intersection(other.boundary)
        length = float(getattr(shared, "length", 0.0) or 0.0)
        score = (overlap, length)
        if score > best_score:
            best_score = score
            best = channel_id
        dist = float(geom.distance(other))
        if nearest_dist is None or dist < nearest_dist:
            nearest_dist = dist
            nearest = channel_id
    if best is not None and best_score > (0.0, 0.0):
        return best
    if nearest is None or nearest_dist is None:
        return None
    # A corner touch, or a seam that is only a short gap after dissolve.
    reach = geom.length or 0.0
    tolerance = max(1.0, reach * 1e-4)
    if basins.crs is not None and getattr(basins.crs, "is_geographic", False):
        tolerance = 1e-4
    if nearest_dist <= tolerance:
        return nearest
    return None


def _collapse_duplicate_basins(basins: gpd.GeoDataFrame, basin_col: str) -> gpd.GeoDataFrame:
    """One polygon per catchment id. Split parts with the same id are unioned."""
    keys = [normalize_id(value) for value in basins[basin_col]]
    counts: dict[str, int] = defaultdict(int)
    for key in keys:
        if key:
            counts[key] += 1
    dupes = {key for key, count in counts.items() if count > 1}
    if not dupes:
        return basins
    extra = sum(counts[key] - 1 for key in dupes)
    print(f"Joined {extra} extra basin part(s) that shared a catchment id")
    work = basins.copy()
    work["_fid"] = keys
    singles = work[~work["_fid"].isin(dupes)].drop(columns="_fid")
    pieces = []
    for _fid, group in work[work["_fid"].isin(dupes)].groupby("_fid", sort=False):
        row = group.iloc[0].drop(labels=["_fid"]).copy()
        geoms = [
            geom for geom in group.geometry
            if geom is not None and not getattr(geom, "is_empty", True)
        ]
        if len(geoms) >= 2:
            row["geometry"] = unary_union(geoms)
        for column in _AREA_COLS:
            if column in group.columns:
                row[column] = float(pd.to_numeric(group[column], errors="coerce").fillna(0).sum())
        pieces.append(row)
    merged = gpd.GeoDataFrame(pieces, geometry="geometry", crs=basins.crs)
    out = pd.concat([singles, merged], ignore_index=True)
    return gpd.GeoDataFrame(out, geometry="geometry", crs=basins.crs)


def fold_lineless_catchments(
    streams: gpd.GeoDataFrame,
    basins: gpd.GeoDataFrame,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
    gauges: gpd.GeoDataFrame | None = None,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Union each basin that has no channel into a basin that does.

    Candidates are reaches with no line and basins whose id is not a channel
    with a line, including basins that have no stream row. The empty reach is
    removed and no replacement line is drawn. A gauge basin joins the upstream
    main channel, so that channel's pour point stays put and only area and
    station ids move. Any other catchment joins the next downstream channel
    that has both a line and a basin. A catchment with no downstream channel
    joins the upstream main channel, so that channel becomes the outlet and its
    pour point stays put. A basin that still has nowhere to go joins the
    channelled basin it overlaps or touches. Inflows of a removed reach are
    pointed past it. A reach is not left behind as a basin with no stream.
    """
    streams = _apply_coerce(streams)
    basins = basins.copy().reset_index(drop=True)
    if streams is None or streams.empty:
        return streams, basins

    link_col = _link_col(streams)
    down_col = _down_col(streams)
    if down_col is None or link_col not in streams.columns:
        return streams, basins

    link_ids = [normalize_id(value) for value in streams[link_col]]
    down_ids = [
        _parse_downstream_id(value, outlet_sentinel) for value in streams[down_col]
    ]
    has_line = [coerce_flowpath_geometry(geom) is not None for geom in streams.geometry]
    known_channels = {link_ids[i] for i, present in enumerate(has_line) if present and link_ids[i]}
    stream_lineless = {link_ids[i] for i, present in enumerate(has_line) if not present and link_ids[i]}
    stream_ids = set(link_ids)
    basin_col = _basin_id_column(basins, stream_ids)
    if basin_col:
        basins = _collapse_duplicate_basins(basins, basin_col)
    basin_ids = [
        normalize_id(value) for value in basins[basin_col]
    ] if basin_col else []
    # Basins with no channel are folded first, including ones that have no stream
    # row at all. Those are the catchments that would otherwise outnumber streams.
    orphan_basins = {basin_id for basin_id in basin_ids if basin_id and basin_id not in known_channels}
    lineless = stream_lineless | orphan_basins
    if not lineless:
        return streams, basins

    basin_id_set = {basin_id for basin_id in basin_ids if basin_id}
    # Area only moves onto a reach that still has a line and a basin polygon.
    receivers = (known_channels & basin_id_set) if basin_id_set else set(known_channels)

    downstream = dict(zip(link_ids, down_ids))
    basin_down = _down_col(basins)
    if basin_col and basin_down:
        for basin_id, down in zip(basins[basin_col], basins[basin_down]):
            key = normalize_id(basin_id)
            if key and key not in downstream:
                downstream[key] = _parse_downstream_id(down, outlet_sentinel)
    for column in _US_LINK_COLS:
        if column not in streams.columns:
            continue
        for link, upstream in zip(link_ids, streams[column]):
            upstream_id = normalize_id(upstream)
            link_id = normalize_id(link)
            if upstream_id and upstream_id not in downstream and link_id in known_channels:
                downstream[upstream_id] = link_id
    link_pos = {link_ids[i]: i for i in range(len(link_ids)) if link_ids[i]}
    inflows: dict[str, list[str]] = defaultdict(list)
    for source, target in downstream.items():
        if source and target:
            inflows[target].append(source)

    gauge_hits = _gauge_hits(basins, basin_col, gauges) if basin_col else {}
    gauge_ids = set(gauge_hits)
    area_pos = {}
    if basin_col is not None and "area_km2" in basins.columns:
        area_pos = {
            normalize_id(basin_id): float(area) if pd.notna(area) else 0.0
            for basin_id, area in zip(
                basins[basin_col],
                pd.to_numeric(basins["area_km2"], errors="coerce"),
            )
            if normalize_id(basin_id)
        }

    def _weight(fid: str) -> float:
        position = link_pos.get(fid)
        if position is not None and "DSContArea" in streams.columns:
            value = pd.to_numeric(streams.iloc[position]["DSContArea"], errors="coerce")
            if pd.notna(value):
                return float(value)
        return area_pos.get(fid, 0.0)

    def channel_below(fid: str) -> str | None:
        seen = set()
        current = fid
        while current and current not in receivers:
            if current in seen:
                return None
            seen.add(current)
            nxt = downstream.get(current, "")
            if not nxt:
                return None
            current = nxt
        return current or None

    def channel_above(fid: str) -> str | None:
        """Upstream main channel: the inflow with the largest drainage area."""
        seen = set()
        current = fid
        while current not in seen:
            seen.add(current)
            upstreams = [up for up in inflows.get(current, []) if up not in seen]
            if not upstreams:
                return None
            main = max(upstreams, key=lambda up: (_weight(up), up))
            if main in receivers:
                return main
            if main not in lineless:
                return None
            current = main
        return None

    # Receiver of area and station ids. skip_to is where inflows are pointed
    # after the empty reach is removed. For a gauge those differ: area goes
    # upstream, topology skips ahead to the gauge's old downstream link.
    fold_to: dict[str, str] = {}
    skip_to: dict[str, str] = {}
    unresolved_down: list[str] = []
    unresolved_gauge: list[str] = []
    for fid in sorted(lineless):
        if fid in gauge_ids:
            target = channel_above(fid)
            if target is not None:
                fold_to[fid] = target
                skip_to[fid] = channel_below(fid) or ""
                continue
            target = channel_below(fid)
            if target is None:
                unresolved_gauge.append(fid)
                continue
            fold_to[fid] = target
            skip_to[fid] = target
        else:
            target = channel_below(fid)
            if target is None:
                # No channel downstream. Keep the area on the upstream main
                # channel; that channel's pour point stays where it is.
                target = channel_above(fid)
                if target is None:
                    unresolved_down.append(fid)
                    continue
            fold_to[fid] = target
            skip_to[fid] = target

    if (unresolved_down or unresolved_gauge) and basin_col is not None:
        positions_now = {
            normalize_id(value): index
            for index, value in enumerate(basins[basin_col])
            if normalize_id(value)
        }
        still_down: list[str] = []
        still_gauge: list[str] = []
        for fid in unresolved_down + unresolved_gauge:
            target = _adjacent_channel(basins, positions_now, fid, receivers)
            if target is None:
                if fid in unresolved_gauge:
                    still_gauge.append(fid)
                else:
                    still_down.append(fid)
                continue
            fold_to[fid] = target
            skip_to[fid] = target
        unresolved_down = still_down
        unresolved_gauge = still_gauge

    if unresolved_down:
        print(
            "Left catchment(s) with no channel and no downstream channel; "
            f"no line was added: {_show_ids(unresolved_down)}"
        )
    if unresolved_gauge:
        print(
            "Left gauge catchment(s) with no channel and no upstream channel; "
            f"no line was added: {_show_ids(unresolved_gauge)}"
        )
    if not fold_to:
        return streams, basins

    def resolve(fid: str) -> str:
        seen = set()
        current = fid
        while current in skip_to:
            if current in seen:
                break
            seen.add(current)
            current = skip_to[current]
            if not current:
                return ""
        return current

    def rewire(frame: gpd.GeoDataFrame) -> None:
        column = _down_col(frame)
        if column is None:
            return
        id_col = next(
            (name for name in (FLOWPATH_ID, LINKNO, BASIN_ID, CATCHMENT_ID) if name in frame.columns),
            None,
        )
        own_ids = [normalize_id(value) for value in frame[id_col]] if id_col else [""] * len(frame)
        updated = []
        for own, value in zip(own_ids, frame[column]):
            parsed = _parse_downstream_id(value, outlet_sentinel)
            if parsed not in skip_to:
                updated.append(value)
                continue
            resolved = resolve(parsed)
            # The channel that absorbed an outlet must not point at itself.
            if not resolved or resolved == own:
                updated.append(outlet_sentinel)
            else:
                updated.append(_id_value(resolved))
        frame[column] = updated

    rewire(streams)
    rewire(basins)

    if basin_col is not None:
        positions = {
            normalize_id(value): index
            for index, value in enumerate(basins[basin_col])
            if normalize_id(value)
        }
        grouped: dict[str, list[str]] = defaultdict(list)
        for source, target in fold_to.items():
            grouped[target].append(source)

        geometries = list(basins.geometry)
        for target, sources in grouped.items():
            target_at = positions.get(target)
            source_at = [positions[source] for source in sources if source in positions]
            if target_at is None:
                continue
            pieces = [geometries[target_at], *[geometries[index] for index in source_at]]
            pieces = [
                geom for geom in pieces
                if geom is not None and not getattr(geom, "is_empty", True)
            ]
            if len(pieces) >= 2:
                geometries[target_at] = unary_union(pieces)
            for column in _AREA_COLS:
                if column not in basins.columns or not source_at:
                    continue
                added = pd.to_numeric(basins.iloc[source_at][column], errors="coerce").fillna(0).sum()
                current = pd.to_numeric(basins.iloc[target_at][column], errors="coerce")
                current = 0.0 if pd.isna(current) else float(current)
                basins.at[basins.index[target_at], column] = current + float(added)
            if {"frac_lake", "lake_area", "area_km2"} <= set(basins.columns):
                area_m2 = float(basins.iloc[target_at]["area_km2"]) * 1e6
                lake_m2 = pd.to_numeric(basins.iloc[target_at]["lake_area"], errors="coerce")
                lake_m2 = 0.0 if pd.isna(lake_m2) else float(lake_m2)
                if area_m2 > 0:
                    basins.at[basins.index[target_at], "frac_lake"] = max(0.0, min(1.0, lake_m2 / area_m2))
            _merge_station_ids(
                basins, target_at, source_at, gauge_hits,
                {index: basin_id for basin_id, index in positions.items()},
            )

        basins = basins.set_geometry(geometries, crs=basins.crs)
        drop_at = [
            positions[source]
            for target, sources in grouped.items()
            if target in positions
            for source in sources
            if source in positions
        ]
        if drop_at:
            basins = basins.drop(basins.index[drop_at]).reset_index(drop=True)

    for column in _US_LINK_COLS:
        if column not in streams.columns:
            continue
        streams[column] = [
            -1 if normalize_id(value) in fold_to else value
            for value in streams[column]
        ]

    streams = streams.loc[~streams[link_col].map(normalize_id).isin(fold_to)].reset_index(drop=True)
    folded_down = sorted(fid for fid in fold_to if fid not in gauge_ids)
    folded_gauge = sorted(fid for fid in fold_to if fid in gauge_ids)
    if folded_down:
        print(
            f"Folded {len(folded_down)} catchment(s) with no channel into the downstream channel: "
            f"{_show_ids(folded_down)}"
        )
    if folded_gauge:
        print(
            f"Folded {len(folded_gauge)} gauge catchment(s) into the upstream channel: "
            f"{_show_ids(folded_gauge)}"
        )
    return streams, basins


def repair_flowpath_geometries(
    streams: gpd.GeoDataFrame,
    basins: gpd.GeoDataFrame | None = None,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
    gauges: gpd.GeoDataFrame | None = None,
):
    """
    Normalize reach geometries.

    With ``basins``, catchments that have no line are merged into a channelled
    catchment and the empty reach is removed. Gauge catchments merge upstream.
    The return value is ``(streams, basins)``. Without ``basins``, geometries are
    only normalized and the stream frame is returned. Empty reaches are not given
    a new line.
    """
    if streams is None or streams.empty:
        return (streams, basins) if basins is not None else streams
    if basins is None:
        return _apply_coerce(streams)
    return fold_lineless_catchments(
        streams, basins, outlet_sentinel=outlet_sentinel, gauges=gauges,
    )
