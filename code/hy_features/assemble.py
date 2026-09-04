"""
Assemble a fully HY_Features-conformant geofabric from pipeline outputs.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

from hy_features.enrich import (
    build_catchment_registry_from_geofabric,
    enrich_catchment_areas,
    enrich_flowpaths,
    enrich_hydro_locations,
    enrich_hydrometric_features,
    enrich_waterbodies,
)
from hy_features.export import export_geopackage, export_registry_json
from hy_features.models import Catchment, CatchmentRegistry
from hy_features.network import (
    assign_hydrometric_positions,
    build_dendritic_catchment_table,
    build_hydro_nexus_layer,
    build_hydrographic_network_metadata,
    filter_placed_hydrometric,
    link_catchment_nexuses,
    link_flowpath_nexuses,
    link_waterbody_network,
    merge_hydro_locations_into_nexus,
)
from hy_features.schema import MESH_OUTLET_SENTINEL
from hy_features.stamp import stamp_geofabric_layers


def assemble_full_geofabric(
    basins: gpd.GeoDataFrame,
    streams: gpd.GeoDataFrame,
    *,
    gauges: gpd.GeoDataFrame | None = None,
    waterbodies: gpd.GeoDataFrame | None = None,
    hydro_locations: gpd.GeoDataFrame | None = None,
    outlet_sentinel: int = MESH_OUTLET_SENTINEL,
    network_id: str = "study_hydrographic_network",
    gauge_search_radius_m: float = 5000.0,
) -> dict[str, Any]:
    """
    Build all HY_Features layers with mandatory associations populated.

    Returns dict with keys: layers, dendritic_catchment, hydrographic_network,
    registry.
    """
    basins = enrich_catchment_areas(basins)
    streams = enrich_flowpaths(streams, outlet_sentinel=outlet_sentinel)

    basins = link_catchment_nexuses(basins, streams, outlet_sentinel=outlet_sentinel)
    streams = link_flowpath_nexuses(streams, outlet_sentinel=outlet_sentinel)

    nexus = build_hydro_nexus_layer(streams, outlet_sentinel=outlet_sentinel)
    if hydro_locations is not None and not hydro_locations.empty:
        hydro_locations = enrich_hydro_locations(hydro_locations)
        nexus = merge_hydro_locations_into_nexus(nexus, hydro_locations)

    hydrometric = None
    hydrometric_skipped = 0
    if gauges is not None and not gauges.empty:
        hydrometric = enrich_hydrometric_features(gauges)
        hydrometric = assign_hydrometric_positions(
            hydrometric, streams, basins=basins, search_radius_m=gauge_search_radius_m,
        )
        hydrometric, hydrometric_skipped = filter_placed_hydrometric(hydrometric)
        if hydrometric_skipped:
            print(
                f"HY_Features: omitted {hydrometric_skipped} gauge(s) without "
                f"positionOnRiver (beyond {gauge_search_radius_m:.0f} m search radius)"
            )

    waterbody_layer = None
    if waterbodies is not None and not waterbodies.empty:
        waterbody_layer = enrich_waterbodies(waterbodies)
        waterbody_layer = link_waterbody_network(
            waterbody_layer, basins, streams, outlet_sentinel=outlet_sentinel,
        )

    dendritic = build_dendritic_catchment_table(basins, streams, outlet_sentinel=outlet_sentinel)

    registry = build_catchment_registry_from_geofabric(basins, streams)

    layers: dict[str, gpd.GeoDataFrame] = {
        "catchment_area": basins,
        "flowpath": streams,
        "hydro_nexus": nexus,
    }
    if hydrometric is not None and not hydrometric.empty:
        layers["hydrometric_feature"] = hydrometric
    if waterbody_layer is not None and not waterbody_layer.empty:
        layers["waterbody"] = waterbody_layer
    if hydro_locations is not None and not hydro_locations.empty:
        layers["hydro_location"] = hydro_locations

    layers = stamp_geofabric_layers(layers, network_id)
    _finalize_registry(registry, layers, dendritic)

    network_meta = build_hydrographic_network_metadata(
        layers["flowpath"],
        layers.get("waterbody"),
        network_id=network_id,
        basins=layers["catchment_area"],
    )

    return {
        "layers": layers,
        "dendritic_catchment": dendritic,
        "hydrographic_network": network_meta,
        "registry": registry,
        "hydrometric_skipped": hydrometric_skipped,
    }


def _upsert_realization(
    registry: CatchmentRegistry,
    catchment_id: str,
    realization_type: str,
    feature_id: str,
    *,
    waterbody_id: str | None = None,
    notes: str = "",
) -> None:
    for entry in registry.entries:
        if entry.catchment_id == catchment_id and entry.realization_type == realization_type:
            entry.feature_id = feature_id
            if waterbody_id:
                entry.waterbody_id = waterbody_id
            if notes:
                entry.notes = notes
            return
    registry.add(
        catchment_id,
        realization_type,
        feature_id,
        waterbody_id=waterbody_id,
        notes=notes,
    )


def _finalize_registry(
    registry: CatchmentRegistry,
    layers: dict[str, gpd.GeoDataFrame],
    dendritic: pd.DataFrame,
) -> None:
    """Sync registry realizations with stamped layers (incl. nexusRealization)."""
    from hy_features.schema import (
        CATCHMENT_ID,
        CONTRIBUTING_CATCHMENT_ID,
        FEATURE_ID,
        FLOWPATH_ID,
        HY_CATCHMENT_AREA,
        HY_DENDRITIC_CATCHMENT,
        HY_FLOWPATH,
        HY_HYDRO_LOCATION,
        HY_HYDRO_NEXUS,
        HY_HYDROMETRIC_FEATURE,
        HYF_TYPE,
        NEXUS_ID,
        STATION_CODE,
        WATERBODY_ID,
    )

    for _, row in dendritic.iterrows():
        cid = str(row[CATCHMENT_ID])
        wb = str(row.get(WATERBODY_ID, ""))
        wb = wb if wb and wb not in ("", "nan") else None
        if cid not in registry.catchments:
            registry.catchments[cid] = Catchment(code=cid, waterbody_id=wb)
        catchment = registry.catchments[cid]
        catchment.hyf_type = HY_DENDRITIC_CATCHMENT
        catchment.outflow_nexus_id = str(row.get("outflow_nexus_id", ""))
        catchment.inflow_nexus_id = str(row.get("inflow_nexus_id", "")) or None
        catchment.lower_catchment_id = str(row.get("lower_catchment_id", "")) or None
        if wb:
            catchment.waterbody_id = wb

    basins = layers.get("catchment_area")
    if basins is not None and FEATURE_ID in basins.columns:
        for _, row in basins.iterrows():
            cid = str(row[CATCHMENT_ID])
            _upsert_realization(
                registry, cid, HY_CATCHMENT_AREA, str(row[FEATURE_ID]),
            )

    flowpaths = layers.get("flowpath")
    if flowpaths is not None and FEATURE_ID in flowpaths.columns:
        for _, row in flowpaths.iterrows():
            fid = str(row[FLOWPATH_ID])
            _upsert_realization(registry, fid, HY_FLOWPATH, str(row[FEATURE_ID]))

    nexus = layers.get("hydro_nexus")
    if nexus is not None and FEATURE_ID in nexus.columns:
        for _, row in nexus.iterrows():
            hyf = row.get(HYF_TYPE)
            feature_id = str(row[FEATURE_ID])
            nexus_id = str(row[NEXUS_ID])
            contrib = str(row.get(CONTRIBUTING_CATCHMENT_ID, ""))
            if hyf == HY_HYDRO_NEXUS:
                _upsert_realization(
                    registry,
                    contrib,
                    HY_HYDRO_NEXUS,
                    feature_id,
                    notes="nexusRealization",
                )
            elif hyf == HY_HYDRO_LOCATION:
                owner = contrib if contrib else nexus_id
                _upsert_realization(
                    registry,
                    owner,
                    HY_HYDRO_LOCATION,
                    feature_id,
                    notes="nexusRealization",
                )

    hydrometric = layers.get("hydrometric_feature")
    if hydrometric is not None and FEATURE_ID in hydrometric.columns:
        for _, row in hydrometric.iterrows():
            cid = str(row.get(CATCHMENT_ID, ""))
            _upsert_realization(
                registry,
                cid,
                HY_HYDROMETRIC_FEATURE,
                str(row[FEATURE_ID]),
            )

    waterbodies = layers.get("waterbody")
    if waterbodies is not None and FEATURE_ID in waterbodies.columns:
        for _, row in waterbodies.iterrows():
            wb_id = str(row[WATERBODY_ID])
            _upsert_realization(
                registry,
                wb_id,
                str(row[HYF_TYPE]),
                str(row[FEATURE_ID]),
                waterbody_id=wb_id,
            )

    hydro_loc = layers.get("hydro_location")
    if hydro_loc is not None and FEATURE_ID in hydro_loc.columns:
        for _, row in hydro_loc.iterrows():
            nexus_id = str(row[NEXUS_ID])
            _upsert_realization(
                registry,
                nexus_id,
                HY_HYDRO_LOCATION,
                str(row[FEATURE_ID]),
                notes="nexusRealization",
            )


def export_full_geofabric(
    assembled: dict[str, Any],
    gpkg_path: str | Path,
    registry_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> None:
    """Write GeoPackage, registry JSON, and network metadata."""
    import json

    from hy_features.json_export import clean_json_records

    gpkg_path = Path(gpkg_path)
    export_geopackage(assembled["layers"], gpkg_path)

    if registry_path:
        export_registry_json(assembled["registry"], registry_path)

    meta_path = metadata_path or gpkg_path.with_name("hydrographic_network.json")
    meta_payload = {
        "hydrographic_network": assembled["hydrographic_network"],
        "dendritic_catchment": clean_json_records(
            assembled["dendritic_catchment"].to_dict(orient="records")
        ),
    }
    Path(meta_path).write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

    print(f"Full HY_Features GeoPackage: {gpkg_path}")
