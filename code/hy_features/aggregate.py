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
