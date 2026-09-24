"""
Gauge catchments realized by HY_HydrometricNetwork (Section 7.5).

For every placed hydrometric station, the catchment upstream of the station
(``gauge_{station}``) is an ``HY_CatchmentAggregate`` of the dendritic catchments that
drain to it. Following the outlet-at-station constraint, its outflow is a nexus
``nx_gauge_{station}`` realized by the station itself. The catchment is realized by

* an ``HY_CatchmentArea`` polygon in ``gauge_catchment`` (union of its members), and
* an ``HY_HydrometricNetwork`` whose ``networkStation`` members are the station and
  every station upstream of it.

The station sits partway along its host reach, but the host catchment is not split,
so the gauge catchment includes the whole host catchment.
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import pandas as pd
import shapely

from hy_features.models import CatchmentRegistry
from hy_features.network import _projected, build_upstream_map
from hy_features.schema import (
    CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    DEFAULT_OUTLET_SENTINEL,
    DISTANCE_FROM_OUTLET_M,
    FEATURE_ID,
    FEATURE_NAME,
    HOST_CATCHMENT_ID,
    HYDROMETRIC_FEATURE_ID,
    HYDROMETRIC_NETWORK_ID,
    HYF_TYPE,
    HYF_TYPE_URI,
    HY_CATCHMENT_AGGREGATE,
    HY_CATCHMENT_AREA,
    HY_HYDRO_NEXUS,
    HY_HYDROMETRIC_NETWORK,
    NETWORK_ID,
    NEXUS_ID,
    OUTFLOW_NEXUS_ID,
    REALIZED_NEXUS_ID,
    REALIZES_CATCHMENT,
    RECEIVING_CATCHMENT_ID,
    STATION_CODE,
    gauge_catchment_id_for,
    gauge_nexus_id_for,
    hyf_type_uri,
)


def _upstream_members(host: str, upstream_map: dict[str, list[str]]) -> list[str]:
    members: list[str] = []
    seen: set[str] = set()
    stack = [host]
    while stack:
        cid = stack.pop()
        if cid in seen:
            continue
        seen.add(cid)
        members.append(cid)
        stack.extend(upstream_map.get(cid, []))
    return members


def _union(polygons: list) -> Any:
    merged = shapely.coverage_union_all(polygons)
    if merged.is_empty or not merged.is_valid:
        merged = shapely.union_all(polygons)
    return merged


def build_gauge_catchments(
    layers: dict[str, pd.DataFrame],
    network_id: str,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> dict[str, Any] | None:
    """
    Build gauge catchments from the stamped layers.

    Returns ``None`` without placed gauges, otherwise a dict with the updated
    ``hydrometric_feature`` layer, the ``gauge_catchment`` layer, gauge ``nexus`` rows,
    the ``hydrometric_network`` table, its ``stations`` link table, and ``catchments``
    (one record per gauge catchment for the registry).
    """
    hydrometric = layers.get("hydrometric_feature")
    if hydrometric is None or hydrometric.empty:
        return None

    basins = layers["catchment_area"]
    upstream_map = build_upstream_map(layers["flowpath"], outlet_sentinel)
    geom_by_catchment = dict(zip(basins[CATCHMENT_ID].astype(str), basins.geometry))

    stations = hydrometric.copy()
    stations["_code"] = stations[FEATURE_ID].astype(str).str.removeprefix("hm_")
    stations[REALIZED_NEXUS_ID] = stations["_code"].map(gauge_nexus_id_for)

    members_by_code: dict[str, list[str]] = {}
    area_rows: list[dict] = []
    nexus_rows: list[dict] = []
    network_rows: list[dict] = []
    catchments: list[dict] = []
    for _, row in stations.iterrows():
        code = row["_code"]
        host = str(row[CATCHMENT_ID])
        members = _upstream_members(host, upstream_map)
        members_by_code[code] = members
        polygons = [geom_by_catchment[m] for m in members if m in geom_by_catchment]
        if not polygons:
            continue
        gauge_cid = gauge_catchment_id_for(code)
        nexus_id = gauge_nexus_id_for(code)
        network_feature = f"hmn_{code}"

        area_rows.append({
            FEATURE_ID: f"gca_{code}",
            NETWORK_ID: network_id,
            CATCHMENT_ID: gauge_cid,
            HYF_TYPE: HY_CATCHMENT_AREA,
            HYF_TYPE_URI: hyf_type_uri(HY_CATCHMENT_AREA),
            REALIZES_CATCHMENT: gauge_cid,
            OUTFLOW_NEXUS_ID: nexus_id,
            STATION_CODE: row[STATION_CODE],
            HYDROMETRIC_FEATURE_ID: row[FEATURE_ID],
            HOST_CATCHMENT_ID: host,
            "member_count": len(members),
            "geometry": _union(polygons),
        })
        nexus_rows.append({
            NEXUS_ID: nexus_id,
            FEATURE_ID: nexus_id,
            NETWORK_ID: network_id,
            HYF_TYPE: HY_HYDRO_NEXUS,
            HYF_TYPE_URI: hyf_type_uri(HY_HYDRO_NEXUS),
            CONTRIBUTING_CATCHMENT_ID: gauge_cid,
            RECEIVING_CATCHMENT_ID: host,
        })
        network_rows.append({
            FEATURE_ID: network_feature,
            NETWORK_ID: network_id,
            HYDROMETRIC_NETWORK_ID: network_feature,
            HYF_TYPE: HY_HYDROMETRIC_NETWORK,
            HYF_TYPE_URI: hyf_type_uri(HY_HYDROMETRIC_NETWORK),
            REALIZES_CATCHMENT: gauge_cid,
            "outlet_station_id": row[FEATURE_ID],
            FEATURE_NAME: row.get(FEATURE_NAME, ""),
        })
        catchments.append({
            CATCHMENT_ID: gauge_cid,
            OUTFLOW_NEXUS_ID: nexus_id,
            "members": members,
            "area_feature_id": f"gca_{code}",
            "network_feature_id": network_feature,
            "nexus_id": nexus_id,
            "station_feature_id": row[FEATURE_ID],
        })

    if not area_rows:
        return None
    built = {record["nexus_id"] for record in catchments}
    stations[REALIZED_NEXUS_ID] = stations[REALIZED_NEXUS_ID].where(
        stations[REALIZED_NEXUS_ID].isin(built), "",
    )

    area = gpd.GeoDataFrame(area_rows, geometry="geometry", crs=basins.crs)
    area["area_km2"] = (_projected(area).geometry.area / 1e6).round(6).to_numpy()

    host_of = dict(zip(stations["_code"], stations[CATCHMENT_ID].astype(str)))
    distance_of = dict(zip(
        stations["_code"], pd.to_numeric(stations[DISTANCE_FROM_OUTLET_M], errors="coerce").fillna(0.0),
    ))
    feature_of = dict(zip(stations["_code"], stations[FEATURE_ID]))
    station_rows: list[tuple[str, str]] = []
    for net in network_rows:
        code = net[FEATURE_ID][len("hmn_"):]
        members = set(members_by_code[code])
        for other, other_host in host_of.items():
            if other_host not in members:
                continue
            if other_host == host_of[code] and distance_of[other] < distance_of[code]:
                continue  # downstream of this station on the same reach
            station_rows.append((net[FEATURE_ID], feature_of[other]))

    return {
        "hydrometric_feature": stations.drop(columns=["_code"]),
        "gauge_catchment": area,
        "nexus": pd.DataFrame(nexus_rows),
        "hydrometric_network": pd.DataFrame(network_rows),
        "stations": pd.DataFrame(station_rows, columns=[HYDROMETRIC_NETWORK_ID, HYDROMETRIC_FEATURE_ID]),
        "catchments": catchments,
    }


def register_gauge_catchments(registry: CatchmentRegistry, gauges: dict[str, Any]) -> None:
    for record in gauges["catchments"]:
        cid = record[CATCHMENT_ID]
        catchment = registry.add_catchment(cid)
        catchment.hyf_type = HY_CATCHMENT_AGGREGATE
        catchment.outflow_nexus_id = record[OUTFLOW_NEXUS_ID]
        for member in record["members"]:
            registry.contain(cid, member)
        registry.add(cid, HY_CATCHMENT_AREA, record["area_feature_id"], notes="catchmentRealization")
        registry.add(cid, HY_HYDROMETRIC_NETWORK, record["network_feature_id"], notes="catchmentRealization")
        registry.associate(cid, HY_HYDRO_NEXUS, record["nexus_id"], "outflow")
