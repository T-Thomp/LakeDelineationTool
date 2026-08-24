"""Stamp OGC conformance-profile metadata on assembled geofabric layers."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd

from hy_features.schema import (
    CATCHMENT_ID,
    FEATURE_ID,
    FEATURE_NAME,
    FLOWPATH_ID,
    HOST_FLOWPATH_ID,
    HYF_TYPE,
    HYF_TYPE_URI,
    HY_CATCHMENT_AREA,
    HY_FLOWPATH,
    HY_HYDRO_LOCATION,
    HY_HYDROMETRIC_FEATURE,
    HY_HYDRO_NEXUS,
    NETWORK_ID,
    NEXUS_ID,
    REALIZED_NEXUS_ID,
    STATION_CODE,
    WATERBODY_ID,
    hyf_type_uri,
)


def assign_hydro_location_nexus_ids(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Assign nexus_id and realizedNexus for pour-point hydro locations."""
    out = gdf.copy()
    if NEXUS_ID not in out.columns or out[NEXUS_ID].astype(str).str.len().eq(0).all():
        if "name" in out.columns:
            out[NEXUS_ID] = "nx_loc_" + out["name"].astype(str)
        elif WATERBODY_ID in out.columns:
            out[NEXUS_ID] = (
                "nx_loc_" + out[WATERBODY_ID].astype(str) + "_" + out.index.astype(str)
            )
        else:
            out[NEXUS_ID] = "nx_loc_" + out.index.astype(str)

    out[NEXUS_ID] = out[NEXUS_ID].astype(str)
    out[REALIZED_NEXUS_ID] = out[NEXUS_ID]
    return out


def _stamp_hyf_type_uri(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    out = gdf.copy()
    if HYF_TYPE not in out.columns:
        return out
    if HYF_TYPE_URI not in out.columns:
        out[HYF_TYPE_URI] = ""
    missing = out[HYF_TYPE_URI].astype(str).str.len() == 0
    out.loc[missing, HYF_TYPE_URI] = out.loc[missing, HYF_TYPE].map(hyf_type_uri)
    return out


def _stamp_identity(
    gdf: gpd.GeoDataFrame,
    *,
    network_id: str,
    id_series: pd.Series,
    prefix: str,
) -> gpd.GeoDataFrame:
    out = gdf.copy()
    out[NETWORK_ID] = network_id
    ids = id_series.astype(str)
    out[FEATURE_ID] = prefix + ids
    return _stamp_hyf_type_uri(out)


def stamp_geofabric_layers(
    layers: dict[str, gpd.GeoDataFrame],
    network_id: str,
) -> dict[str, gpd.GeoDataFrame]:
    """Add network_id, feature_id, and hyf_type_uri to all profile layers."""
    stamped: dict[str, gpd.GeoDataFrame] = {}

    if "catchment_area" in layers and layers["catchment_area"] is not None:
        basins = layers["catchment_area"]
        stamped["catchment_area"] = _stamp_identity(
            basins,
            network_id=network_id,
            id_series=basins[CATCHMENT_ID],
            prefix="ca_",
        )

    if "flowpath" in layers and layers["flowpath"] is not None:
        streams = layers["flowpath"]
        stamped["flowpath"] = _stamp_identity(
            streams,
            network_id=network_id,
            id_series=streams[FLOWPATH_ID],
            prefix="fp_",
        )

    if "hydro_nexus" in layers and layers["hydro_nexus"] is not None:
        nexus = layers["hydro_nexus"].copy()
        nexus[NETWORK_ID] = network_id
        nexus[NEXUS_ID] = nexus[NEXUS_ID].astype(str)
        nexus[FEATURE_ID] = nexus[NEXUS_ID]
        if REALIZED_NEXUS_ID not in nexus.columns:
            nexus[REALIZED_NEXUS_ID] = nexus[NEXUS_ID]
        else:
            empty = nexus[REALIZED_NEXUS_ID].astype(str).str.len() == 0
            nexus.loc[empty, REALIZED_NEXUS_ID] = nexus.loc[empty, NEXUS_ID]
        stamped["hydro_nexus"] = _stamp_hyf_type_uri(nexus)

    if "waterbody" in layers and layers["waterbody"] is not None:
        wb = layers["waterbody"].copy()
        wb = _stamp_identity(
            wb,
            network_id=network_id,
            id_series=wb[WATERBODY_ID],
            prefix="wb_",
        )
        stamped["waterbody"] = wb

    if "hydrometric_feature" in layers and layers["hydrometric_feature"] is not None:
        hm = layers["hydrometric_feature"].copy()
        hm[NETWORK_ID] = network_id
        codes = hm[STATION_CODE].astype(str)
        hm[FEATURE_ID] = "hm_" + codes
        stamped["hydrometric_feature"] = _stamp_hyf_type_uri(hm)

    if "hydro_location" in layers and layers["hydro_location"] is not None:
        loc = assign_hydro_location_nexus_ids(layers["hydro_location"])
        loc[NETWORK_ID] = network_id
        loc[FEATURE_ID] = "hl_" + loc[NEXUS_ID].astype(str)
        stamped["hydro_location"] = _stamp_hyf_type_uri(loc)

    for name, gdf in layers.items():
        if name not in stamped and gdf is not None:
            stamped[name] = gdf

    return stamped
