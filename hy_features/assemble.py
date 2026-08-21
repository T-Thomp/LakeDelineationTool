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
from hy_features.models import CatchmentRegistry
from hy_features.network import (
    assign_hydrometric_positions,
    build_dendritic_catchment_table,
    build_hydro_nexus_layer,
    build_hydrographic_network_metadata,
    link_catchment_nexuses,
    link_flowpath_nexuses,
    link_waterbody_network,
    merge_hydro_locations_into_nexus,
)
from hy_features.schema import (
    HY_HYDRO_NEXUS,
    HY_HYDROGRAPHIC_NETWORK,
    MESH_OUTLET_SENTINEL,
    NETWORK_ID,
)
from hy_features.validate import validate_geofabric, validation_report_to_dict


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
    registry, validation.
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
    if gauges is not None and not gauges.empty:
        hydrometric = enrich_hydrometric_features(gauges)
        hydrometric = assign_hydrometric_positions(
            hydrometric, streams, basins=basins, search_radius_m=gauge_search_radius_m,
        )

    waterbody_layer = None
    if waterbodies is not None and not waterbodies.empty:
        waterbody_layer = enrich_waterbodies(waterbodies)
        waterbody_layer = link_waterbody_network(waterbody_layer, basins, streams)

    dendritic = build_dendritic_catchment_table(basins, streams, outlet_sentinel=outlet_sentinel)
    network_meta = build_hydrographic_network_metadata(streams, waterbody_layer, network_id=network_id)

    registry = build_catchment_registry_from_geofabric(basins, streams)
    _extend_registry(registry, nexus, hydrometric, waterbody_layer, dendritic)

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

    validation = validate_geofabric(layers, dendritic, registry, outlet_sentinel=outlet_sentinel)

    return {
        "layers": layers,
        "dendritic_catchment": dendritic,
        "hydrographic_network": network_meta,
        "registry": registry,
        "validation": validation,
    }


def _extend_registry(
    registry: CatchmentRegistry,
    nexus: gpd.GeoDataFrame,
    hydrometric: gpd.GeoDataFrame | None,
    waterbodies: gpd.GeoDataFrame | None,
    dendritic: pd.DataFrame,
) -> None:
    from hy_features.schema import (
        CATCHMENT_ID,
        HY_DENDRITIC_CATCHMENT,
        HY_HYDROMETRIC_FEATURE,
        NEXUS_ID,
        WATERBODY_ID,
    )

    for _, row in nexus.iterrows():
        if row.get("hyf_type") == HY_HYDRO_NEXUS:
            registry.add(
                str(row.get("contributing_catchment_id", "")),
                HY_HYDRO_NEXUS,
                str(row[NEXUS_ID]),
                notes="outflow nexus",
            )

    if hydrometric is not None:
        for _, row in hydrometric.iterrows():
            registry.add(
                str(row.get(CATCHMENT_ID, "")),
                HY_HYDROMETRIC_FEATURE,
                str(row.get("station_code", "")),
            )

    if waterbodies is not None:
        from hy_features.schema import HYF_TYPE
        for _, row in waterbodies.iterrows():
            registry.add(
                str(row.get(WATERBODY_ID, "")),
                str(row.get(HYF_TYPE, "HY_WaterBody")),
                str(row.get(WATERBODY_ID, "")),
                waterbody_id=str(row.get(WATERBODY_ID, "")),
            )

    for _, row in dendritic.iterrows():
        cid = str(row[CATCHMENT_ID])
        if cid in registry.catchments:
            c = registry.catchments[cid]
            c.hyf_type = HY_DENDRITIC_CATCHMENT
            c.outflow_nexus_id = str(row.get("outflow_nexus_id", ""))
            c.inflow_nexus_id = str(row.get("inflow_nexus_id", "")) or None
            c.lower_catchment_id = str(row.get("lower_catchment_id", "")) or None


def export_full_geofabric(
    assembled: dict[str, Any],
    gpkg_path: str | Path,
    registry_path: str | Path | None = None,
    metadata_path: str | Path | None = None,
    validation_path: str | Path | None = None,
) -> None:
    """Write GeoPackage, registry JSON, network metadata, and validation report."""
    import json

    gpkg_path = Path(gpkg_path)
    export_geopackage(assembled["layers"], gpkg_path)

    if registry_path:
        export_registry_json(assembled["registry"], registry_path)

    meta_path = metadata_path or gpkg_path.with_name("hydrographic_network.json")
    meta_payload = {
        "hydrographic_network": assembled["hydrographic_network"],
        "dendritic_catchment": assembled["dendritic_catchment"].to_dict(orient="records"),
    }
    Path(meta_path).write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")

    val_path = validation_path or gpkg_path.with_name("hy_features_validation.json")
    val_payload = validation_report_to_dict(assembled["validation"])
    Path(val_path).write_text(json.dumps(val_payload, indent=2), encoding="utf-8")

    v = assembled["validation"]
    if v.errors:
        print(f"HY_Features validation: {len(v.errors)} error(s), {len(v.warnings)} warning(s)")
        for err in v.errors:
            print(f"  ERROR: {err}")
    else:
        print(f"HY_Features validation passed ({len(v.warnings)} warning(s))")

    print(f"Full HY_Features GeoPackage: {gpkg_path}")
