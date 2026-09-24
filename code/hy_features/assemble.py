"""
Assemble a HY_Features (OGC 14-111r6) implementation-schema geofabric from pipeline outputs.
"""

from __future__ import annotations

import json
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
from hy_features.json_export import clean_json_records, json_default
from hy_features.models import CatchmentRegistry
from hy_features.network import (
    assign_hydrometric_positions,
    build_dendritic_catchment_table,
    build_hydro_nexus_layer,
    build_hydrographic_network_metadata,
    filter_placed_hydrometric,
    link_catchment_nexuses,
    link_flowpath_nexuses,
    link_hydro_locations_to_nexus,
    link_waterbody_network,
)
from hy_features.schema import (
    CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    FEATURE_ID,
    FLOWPATH_ID,
    HY_CATCHMENT_AREA,
    HY_FLOWPATH,
    HY_HYDRO_NEXUS,
    HY_HYDROMETRIC_FEATURE,
    HYF_TYPE,
    INFLOW_NEXUS_ID,
    IS_LAKE_CATCHMENT,
    MESH_OUTLET_SENTINEL,
    OUTFLOW_NEXUS_ID,
    UPPER_CATCHMENT_ID,
    WATERBODY_ID,
)
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
    registry, hydrometric_skipped.
    """
    basins = enrich_catchment_areas(basins)
    streams = enrich_flowpaths(streams, outlet_sentinel=outlet_sentinel)

    basins = link_catchment_nexuses(basins, streams, outlet_sentinel=outlet_sentinel)
    streams = link_flowpath_nexuses(streams, outlet_sentinel=outlet_sentinel)

    nexus = build_hydro_nexus_layer(streams, outlet_sentinel=outlet_sentinel)
    if hydro_locations is not None and not hydro_locations.empty:
        hydro_locations = link_hydro_locations_to_nexus(
            enrich_hydro_locations(hydro_locations), streams, outlet_sentinel=outlet_sentinel,
        )

    hydrometric = None
    hydrometric_skipped = 0
    if gauges is not None and not gauges.empty:
        hydrometric = enrich_hydrometric_features(gauges)
        hydrometric = assign_hydrometric_positions(
            hydrometric, streams, basins=basins,
            search_radius_m=gauge_search_radius_m, outlet_sentinel=outlet_sentinel,
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
        nexus=layers["hydro_nexus"],
        outlet_sentinel=outlet_sentinel,
    )

    return {
        "layers": layers,
        "dendritic_catchment": dendritic,
        "hydrographic_network": network_meta,
        "registry": registry,
        "hydrometric_skipped": hydrometric_skipped,
    }


def _finalize_registry(
    registry: CatchmentRegistry,
    layers: dict[str, gpd.GeoDataFrame],
    dendritic: pd.DataFrame,
) -> None:
    """
    Sync the registry with stamped layers.

    ``realizations`` hold only catchmentRealization links (catchment area, flowpath).
    Nexuses, water bodies, and hydrometric features are recorded as ``associations``.
    """
    for _, row in dendritic.iterrows():
        catchment = registry.add_catchment(str(row[CATCHMENT_ID]))
        catchment.outflow_nexus_id = str(row.get(OUTFLOW_NEXUS_ID, "")) or None
        catchment.inflow_nexus_id = str(row.get(INFLOW_NEXUS_ID, "")) or None
        ups = str(row.get(UPPER_CATCHMENT_ID, ""))
        catchment.upper_catchment_ids = [u for u in ups.split(",") if u]

    basins = layers["catchment_area"]
    for cid, fid in zip(basins[CATCHMENT_ID], basins[FEATURE_ID]):
        registry.add(str(cid), HY_CATCHMENT_AREA, str(fid), notes="catchmentRealization")

    flowpaths = layers["flowpath"]
    for cid, fid in zip(flowpaths[FLOWPATH_ID], flowpaths[FEATURE_ID]):
        registry.add(str(cid), HY_FLOWPATH, str(fid), notes="catchmentRealization")

    nexus = layers.get("hydro_nexus")
    if nexus is not None:
        for nexus_id, contributing in zip(nexus[FEATURE_ID], nexus[CONTRIBUTING_CATCHMENT_ID]):
            for cid in str(contributing).split(","):
                if cid:
                    registry.associate(cid, HY_HYDRO_NEXUS, str(nexus_id), "outflow")

    hydrometric = layers.get("hydrometric_feature")
    if hydrometric is not None:
        for cid, fid in zip(hydrometric[CATCHMENT_ID], hydrometric[FEATURE_ID]):
            registry.associate(str(cid), HY_HYDROMETRIC_FEATURE, str(fid), "positionOnRiver")

    waterbodies = layers.get("waterbody")
    if waterbodies is not None:
        lake_basins = basins[pd.to_numeric(basins[IS_LAKE_CATCHMENT], errors="coerce").fillna(0) > 0]
        wb_to_catchment = {
            str(wb): str(cid)
            for wb, cid in zip(lake_basins[WATERBODY_ID], lake_basins[CATCHMENT_ID])
            if str(wb)
        }
        for wb_id, wb_type, fid in zip(
            waterbodies[WATERBODY_ID], waterbodies[HYF_TYPE], waterbodies[FEATURE_ID],
        ):
            cid = wb_to_catchment.get(str(wb_id))
            if cid:
                registry.associate(cid, str(wb_type), str(fid), "networkWaterBody")


def export_full_geofabric(
    assembled: dict[str, Any],
    gpkg_path: str | Path,
    registry_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
) -> None:
    """Write GeoPackage, registry JSON, and network metadata."""
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
    Path(meta_path).write_text(
        json.dumps(meta_payload, indent=2, default=json_default), encoding="utf-8",
    )

    print(f"Full HY_Features GeoPackage: {gpkg_path}")
