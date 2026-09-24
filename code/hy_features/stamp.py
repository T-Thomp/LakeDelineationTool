"""Stamp OGC conformance-profile metadata on assembled geofabric layers."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from hy_features.schema import (
    CATCHMENT_ID,
    FEATURE_ID,
    FLOWPATH_ID,
    HYF_TYPE,
    HYF_TYPE_URI,
    NETWORK_ID,
    NEXUS_ID,
    STATION_CODE,
    WATERBODY_ID,
    hyf_type_uri,
)


def _stamp_hyf_type_uri(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = gdf.copy()
    if HYF_TYPE not in out.columns:
        return out
    if HYF_TYPE_URI not in out.columns:
        out[HYF_TYPE_URI] = ""
    missing = out[HYF_TYPE_URI].astype(str).str.len() == 0
    out.loc[missing, HYF_TYPE_URI] = out.loc[missing, HYF_TYPE].map(hyf_type_uri)
    return out


def _unique_ids(ids: pd.Series) -> pd.Series:
    """Suffix repeated ids (``x``, ``x_2``, ``x_3``) so ``feature_id`` stays unique."""
    ids = ids.astype(str)
    counts = ids.groupby(ids).cumcount()
    return ids.where(counts == 0, ids + "_" + (counts + 1).astype(str))


def _stamp_identity(
    gdf: gpd.GeoDataFrame,
    *,
    network_id: str,
    id_series: pd.Series,
    prefix: str,
) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out[NETWORK_ID] = network_id
    out[FEATURE_ID] = _unique_ids(prefix + id_series.astype(str))
    return _stamp_hyf_type_uri(out)


def stamp_geofabric_layers(
    layers: dict[str, gpd.GeoDataFrame],
    network_id: str,
) -> dict[str, gpd.GeoDataFrame]:
    """Add network_id, feature_id, and hyf_type_uri to all profile layers."""
    id_columns = {
        "catchment_area": (CATCHMENT_ID, "ca_"),
        "flowpath": (FLOWPATH_ID, "fp_"),
        "hydro_nexus": (NEXUS_ID, ""),
        "waterbody": (WATERBODY_ID, "wb_"),
        "hydrometric_feature": (STATION_CODE, "hm_"),
    }

    stamped: dict[str, gpd.GeoDataFrame] = {}
    for name, gdf in layers.items():
        if gdf is None:
            continue
        if name in id_columns:
            col, prefix = id_columns[name]
            stamped[name] = _stamp_identity(
                gdf, network_id=network_id, id_series=gdf[col], prefix=prefix,
            )
        elif name == "hydro_location":
            seq = pd.Series(range(1, len(gdf) + 1), index=gdf.index)
            stamped[name] = _stamp_identity(
                gdf, network_id=network_id, id_series=seq, prefix="hl_",
            )
        else:
            stamped[name] = gdf
    return stamped
