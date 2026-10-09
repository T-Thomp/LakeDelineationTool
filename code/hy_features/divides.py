"""
HY_CatchmentDivide realizations (Section 7.3.2).

A catchment divide realizes one catchment as its boundary line; the standard allows
"a composition of succeeding curves or a polygon ring", so each divide is the full
boundary of the catchment-area polygon. Which neighbour sits across each part of
that boundary is recorded in ``catchment_divide_adjacency``.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from hy_features.network import _projected
from hy_features.schema import (
    ADJACENT_CATCHMENT_ID,
    CATCHMENT_ID,
    HYF_TYPE,
    HYF_TYPE_URI,
    HY_CATCHMENT_DIVIDE,
    REALIZES_CATCHMENT,
    SHARED_LENGTH_M,
    hyf_type_uri,
    normalize_id,
)

# Shared boundary pieces shorter than this (metres) are vertex touches or raster
# slivers, not a divide between the two catchments.
MIN_SHARED_LENGTH_M = 1.0


def build_catchment_divides(
    basins: gpd.GeoDataFrame,
    min_shared_length_m: float = MIN_SHARED_LENGTH_M,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """
    Return ``(catchment_divide layer, catchment_divide_adjacency table)``.

    Adjacency rows with an empty ``adjacent_catchment_id`` are the part of the divide
    on the study-domain boundary.
    """
    columns = [CATCHMENT_ID, REALIZES_CATCHMENT, HYF_TYPE, HYF_TYPE_URI, ADJACENT_CATCHMENT_ID,
               "perimeter_m", "geometry"]
    adj_columns = [CATCHMENT_ID, ADJACENT_CATCHMENT_ID, SHARED_LENGTH_M]
    valid = basins[basins.geometry.notna() & ~basins.geometry.is_empty]
    if valid.empty:
        return (
            gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=basins.crs),
            pd.DataFrame(columns=adj_columns),
        )

    ids = valid[CATCHMENT_ID].map(normalize_id).to_numpy()
    work = _projected(valid)
    polys = work.geometry.to_numpy()
    rings = shapely.boundary(polys)

    left, right = work.sindex.query(polys, predicate="intersects")
    keep = left < right
    left, right = left[keep], right[keep]
    shared = shapely.length(shapely.intersection(rings[left], rings[right]))
    touching = shared >= min_shared_length_m
    left, right, shared = left[touching], right[touching], shared[touching]

    domain_ring = shapely.boundary(shapely.union_all(polys))
    outer = shapely.length(shapely.intersection(rings, domain_ring))

    records: list[tuple[str, str, float]] = []
    for a, b, length in zip(left, right, shared):
        records.append((ids[a], ids[b], round(float(length), 3)))
        records.append((ids[b], ids[a], round(float(length), 3)))
    for i in np.flatnonzero(outer >= min_shared_length_m):
        records.append((ids[i], "", round(float(outer[i]), 3)))
    adjacency = pd.DataFrame(records, columns=adj_columns)

    neighbours = (
        adjacency[adjacency[ADJACENT_CATCHMENT_ID] != ""]
        .groupby(CATCHMENT_ID)[ADJACENT_CATCHMENT_ID]
        .agg(lambda s: ",".join(sorted(s)))
    )
    divides = gpd.GeoDataFrame(
        {
            CATCHMENT_ID: ids,
            REALIZES_CATCHMENT: ids,
            HYF_TYPE: HY_CATCHMENT_DIVIDE,
            HYF_TYPE_URI: hyf_type_uri(HY_CATCHMENT_DIVIDE),
            ADJACENT_CATCHMENT_ID: [neighbours.get(cid, "") for cid in ids],
            "perimeter_m": np.round(shapely.length(rings), 3),
            "geometry": shapely.boundary(valid.geometry.to_numpy()),
        },
        geometry="geometry",
        crs=basins.crs,
    )
    return divides, adjacency
