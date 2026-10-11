"""
HY_Features product for the basinAggregation fabric.

Each aggregated basin is an ``HY_DendriticCatchment`` at a coarser scale that
``containedCatchment``-links the geofabric.gpkg catchments merged into it
(containingCatchment / containedCatchment, 14-111r6 Table 6: "is-in" hierarchy of
management and reporting units). Aggregate ids are prefixed ``agg_`` so they never
collide with the fine-scale catchment ids they contain.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from hy_features.schema import (
    CATCHMENT_ID,
    CONTAINED_CATCHMENT_ID,
    CONTAINING_CATCHMENT_ID,
    DEFAULT_NETWORK_ID,
    DEFAULT_OUTLET_SENTINEL,
    FLOWPATH_ID,
    LEGACY_FLOWPATH_ID,
    LEGACY_LOWER_ID,
    LOWER_CATCHMENT_ID,
    normalize_id,
)
from hy_features.tables import CONTAINMENT_TABLE

AGGREGATE_PREFIX = "agg_"


def membership_from_merge_log(
    fine_basins: gpd.GeoDataFrame,
    merge_log_path: str | Path,
) -> pd.DataFrame:
    """
    Rebuild which fine basin was absorbed into which aggregate.

    ``basinAggregation.py`` writes ``aggregation_merge_log.csv`` with ``source``
    absorbed into ``target`` (``target`` keeps its id). Following that log from
    each fine ``DN`` yields the surviving aggregate ``LINKNO``.
    """
    id_col = "DN" if "DN" in fine_basins.columns else "LINKNO"
    if id_col not in fine_basins.columns:
        raise ValueError(
            "Fine basins need a DN or LINKNO column to rebuild aggregation membership."
        )
    path = Path(merge_log_path)
    if not path.is_file():
        raise FileNotFoundError(
            f"Merge log not found: {path}. Run basinAggregation.py first; "
            "it writes outputs/working/aggregation_merge_log.csv."
        )
    log = pd.read_csv(path)
    missing = [col for col in ("source", "target") if col not in log.columns]
    if missing:
        raise ValueError(f"{path} is missing columns: {missing}")

    parent: dict[int, int] = {}
    for source, target in zip(log["source"], log["target"]):
        parent[int(source)] = int(target)

    def find(node: int) -> int:
        seen: list[int] = []
        visited: set[int] = set()
        while node in parent:
            if node in visited:
                raise ValueError(f"Cycle in merge log at basin {node}")
            visited.add(node)
            seen.append(node)
            node = parent[node]
        for item in seen:
            parent[item] = node
        return node

    rows = [
        {"member": member, "agg": find(member)}
        for member in (int(raw) for raw in pd.unique(fine_basins[id_col].dropna()))
    ]
    return pd.DataFrame(rows, columns=["member", "agg"])


def aggregate_catchment_id(value: object) -> str:
    return f"{AGGREGATE_PREFIX}{normalize_id(value)}"


def _downstream_aggregate(value: object, outlet_sentinel: int) -> str:
    text = normalize_id(value)
    try:
        num = float(text)
    except ValueError:
        return ""
    if num <= 0 or num == outlet_sentinel:
        return ""
    return aggregate_catchment_id(text)


def assemble_aggregated_geofabric(
    agg_basins: gpd.GeoDataFrame,
    agg_rivers: gpd.GeoDataFrame,
    membership: pd.DataFrame,
    *,
    network_id: str = f"{DEFAULT_NETWORK_ID}_aggregated",
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> dict[str, Any]:
    """
    Assemble the aggregated fabric with the geofabric.gpkg schema.

    ``membership`` has columns ``member`` (fine catchment id) and ``agg`` (the
    aggregate's LINKNO).
    """
    from hy_features.assemble import assemble_full_geofabric

    basins = agg_basins.copy()
    basins[CATCHMENT_ID] = basins[LEGACY_FLOWPATH_ID].map(aggregate_catchment_id)

    rivers = agg_rivers.copy()
    rivers[FLOWPATH_ID] = rivers[LEGACY_FLOWPATH_ID].map(aggregate_catchment_id)
    rivers[LOWER_CATCHMENT_ID] = rivers[LEGACY_LOWER_ID].map(
        lambda v: _downstream_aggregate(v, outlet_sentinel)
    )
    # Topology must come from the prefixed ids, not the raw integer DSLINKNO.
    basins = basins.drop(columns=[LEGACY_LOWER_ID], errors="ignore")
    rivers = rivers.drop(columns=[LEGACY_LOWER_ID])

    assembled = assemble_full_geofabric(
        basins,
        rivers,
        outlet_sentinel=outlet_sentinel,
        network_id=network_id,
        domain_catchment_id=f"{AGGREGATE_PREFIX}domain",
    )

    links = pd.DataFrame({
        CONTAINING_CATCHMENT_ID: membership["agg"].map(aggregate_catchment_id),
        CONTAINED_CATCHMENT_ID: membership["member"].map(normalize_id),
    }).drop_duplicates()
    registry = assembled["registry"]
    registry.containments.extend(links.itertuples(index=False, name=None))
    tables = assembled["tables"]
    tables[CONTAINMENT_TABLE] = pd.concat([tables[CONTAINMENT_TABLE], links], ignore_index=True)
    assembled["external_catchment_ids"] = set(links[CONTAINED_CATCHMENT_ID])
    return assembled


def export_aggregated_geofabric(
    agg_basins: gpd.GeoDataFrame,
    agg_rivers: gpd.GeoDataFrame,
    membership: pd.DataFrame,
    gpkg_path: str | Path,
    *,
    outlet_sentinel: int = DEFAULT_OUTLET_SENTINEL,
) -> dict[str, Any]:
    from hy_features.assemble import export_full_geofabric

    gpkg_path = Path(gpkg_path)
    assembled = assemble_aggregated_geofabric(
        agg_basins, agg_rivers, membership, outlet_sentinel=outlet_sentinel,
    )
    export_full_geofabric(
        assembled,
        gpkg_path=gpkg_path,
        registry_path=gpkg_path.with_name("catchment_registry_aggregated.json"),
        metadata_path=gpkg_path.with_name("hydrographic_network_aggregated.json"),
    )
    return assembled
