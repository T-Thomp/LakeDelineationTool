"""
Non-spatial GeoPackage tables: holistic catchments and one-row-per-link associations.

The comma-separated convenience columns on the spatial layers (``upper_catchment_id``,
``contributing_catchment_id``, ``upstream_waterbody_id``) are normalized here into
link tables so every 0..* HY_Features association has a proper relational encoding.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from hy_features.models import CatchmentRegistry
from hy_features.schema import (
    ASSOCIATION_ROLE,
    CATCHMENT_ID,
    CONTAINED_CATCHMENT_ID,
    CONTAINING_CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    FEATURE_ID,
    FEATURE_TYPE,
    HYF_TYPE,
    HYF_TYPE_URI,
    INFLOW_NEXUS_ID,
    LOWER_CATCHMENT_ID,
    NETWORK_ID,
    NEXUS_ID,
    OUTFLOW_NEXUS_ID,
    REALIZATION_TYPE,
    RECEIVING_CATCHMENT_ID,
    UPPER_CATCHMENT_ID,
    UPSTREAM_WATERBODY_ID,
    WATERBODY_ID,
    hyf_type_uri,
)

CATCHMENT_TABLE = "catchment"
REALIZATION_TABLE = "catchment_realization"
ASSOCIATION_TABLE = "catchment_association"
CONTAINMENT_TABLE = "catchment_containment"
UPPER_TABLE = "catchment_upper_catchment"
NEXUS_CONTRIBUTING_TABLE = "nexus_contributing_catchment"
WATERBODY_UPSTREAM_TABLE = "waterbody_upstream_waterbody"


def _split(value: object) -> list[str]:
    return [part for part in str(value or "").split(",") if part]


def build_catchment_table(registry: CatchmentRegistry, network_id: str) -> pd.DataFrame:
    """One row per holistic catchment (HY_DendriticCatchment / HY_CatchmentAggregate)."""
    columns = [FEATURE_ID, NETWORK_ID, CATCHMENT_ID, HYF_TYPE, HYF_TYPE_URI, OUTFLOW_NEXUS_ID,
               INFLOW_NEXUS_ID, LOWER_CATCHMENT_ID, UPPER_CATCHMENT_ID, WATERBODY_ID]
    records = [
        {
            FEATURE_ID: f"cat_{cid}",
            NETWORK_ID: network_id,
            CATCHMENT_ID: cid,
            HYF_TYPE: c.hyf_type,
            HYF_TYPE_URI: hyf_type_uri(c.hyf_type),
            OUTFLOW_NEXUS_ID: c.outflow_nexus_id or "",
            INFLOW_NEXUS_ID: c.inflow_nexus_id or "",
            LOWER_CATCHMENT_ID: c.lower_catchment_id or "",
            UPPER_CATCHMENT_ID: ",".join(c.upper_catchment_ids),
            WATERBODY_ID: c.waterbody_id or "",
        }
        for cid, c in registry.catchments.items()
    ]
    return pd.DataFrame(records, columns=columns)


def build_association_tables(
    registry: CatchmentRegistry,
    layers: dict[str, gpd.GeoDataFrame],
    network_id: str,
) -> dict[str, pd.DataFrame]:
    """All non-spatial tables written alongside the spatial layers in geofabric.gpkg."""
    tables: dict[str, pd.DataFrame] = {
        CATCHMENT_TABLE: build_catchment_table(registry, network_id),
        REALIZATION_TABLE: pd.DataFrame(
            [(e.catchment_id, e.realization_type, e.feature_id) for e in registry.entries],
            columns=[CATCHMENT_ID, REALIZATION_TYPE, FEATURE_ID],
        ),
        ASSOCIATION_TABLE: pd.DataFrame(
            [(a.catchment_id, a.feature_type, a.feature_id, a.role) for a in registry.associations],
            columns=[CATCHMENT_ID, FEATURE_TYPE, FEATURE_ID, ASSOCIATION_ROLE],
        ),
        CONTAINMENT_TABLE: pd.DataFrame(
            registry.containments,
            columns=[CONTAINING_CATCHMENT_ID, CONTAINED_CATCHMENT_ID],
        ),
        UPPER_TABLE: pd.DataFrame(
            [
                (cid, up)
                for cid, c in registry.catchments.items()
                for up in c.upper_catchment_ids
            ],
            columns=[CATCHMENT_ID, UPPER_CATCHMENT_ID],
        ),
    }

    nexus = layers.get("hydro_nexus")
    tables[NEXUS_CONTRIBUTING_TABLE] = pd.DataFrame(
        [
            (row[NEXUS_ID], cid, row[RECEIVING_CATCHMENT_ID])
            for _, row in (nexus.iterrows() if nexus is not None else [])
            for cid in _split(row[CONTRIBUTING_CATCHMENT_ID])
        ],
        columns=[NEXUS_ID, CONTRIBUTING_CATCHMENT_ID, RECEIVING_CATCHMENT_ID],
    )

    waterbody = layers.get("waterbody")
    tables[WATERBODY_UPSTREAM_TABLE] = pd.DataFrame(
        [
            (row[WATERBODY_ID], up)
            for _, row in (waterbody.iterrows() if waterbody is not None else [])
            for up in _split(row.get(UPSTREAM_WATERBODY_ID, ""))
        ],
        columns=[WATERBODY_ID, UPSTREAM_WATERBODY_ID],
    )
    return tables
