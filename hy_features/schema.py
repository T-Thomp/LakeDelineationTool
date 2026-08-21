"""
OGC HY_Features column names, feature-type codes, and MESH legacy aliases.

Reference: OGC WaterML 2 Part 3 — Surface Hydrology Features (14-111r6)
https://docs.ogc.org/is/14-111r6/14-111r6.html
"""

from __future__ import annotations

from typing import Final

# OGC Definitions Server base (human-readable URIs in metadata)
HYF_NS: Final[str] = "https://www.opengis.net/def/appschema/hy_features/hyf/"

# ---------------------------------------------------------------------------
# HY_Features feature type codes (short form for GeoPackage attributes)
# ---------------------------------------------------------------------------
HY_DENDRITIC_CATCHMENT: Final[str] = "HY_DendriticCatchment"
HY_CATCHMENT_AREA: Final[str] = "HY_CatchmentArea"
HY_FLOWPATH: Final[str] = "HY_FlowPath"
HY_HYDRO_LOCATION: Final[str] = "HY_HydroLocation"
HY_HYDROMETRIC_FEATURE: Final[str] = "HY_HydrometricFeature"
HY_LAKE: Final[str] = "HY_Lake"
HY_IMPOUNDMENT: Final[str] = "HY_Impoundment"
HY_HYDRO_NEXUS: Final[str] = "HY_HydroNexus"
HY_HYDROGRAPHIC_NETWORK: Final[str] = "HY_HydrographicNetwork"
HY_INDIRECT_POSITION: Final[str] = "HY_IndirectPosition"

# ---------------------------------------------------------------------------
# Canonical output column names (GeoPackage-friendly, >10 chars OK)
# ---------------------------------------------------------------------------
HYF_TYPE: Final[str] = "hyf_type"
CATCHMENT_ID: Final[str] = "catchment_id"
FLOWPATH_ID: Final[str] = "flowpath_id"
LOWER_CATCHMENT_ID: Final[str] = "lower_catchment_id"
REALIZES_CATCHMENT: Final[str] = "realizes_catchment"
WATERBODY_ID: Final[str] = "waterbody_id"
WATERBODY_CLASS: Final[str] = "waterbody_class"
HYDRO_LOC_TYPE: Final[str] = "hydro_loc_type"
STATION_CODE: Final[str] = "station_code"
IS_LAKE_CATCHMENT: Final[str] = "is_lake_catchment"
LAKE_AREA_M2: Final[str] = "lake_area_m2"
FRAC_LAKE: Final[str] = "frac_lake"
HOST_FLOWPATH_ID: Final[str] = "host_flowpath_id"

# HY_HydroNexus associations (Section 7.3.2)
NEXUS_ID: Final[str] = "nexus_id"
OUTFLOW_NEXUS_ID: Final[str] = "outflow_nexus_id"
INFLOW_NEXUS_ID: Final[str] = "inflow_nexus_id"
CONTRIBUTING_CATCHMENT_ID: Final[str] = "contributing_catchment_id"
RECEIVING_CATCHMENT_ID: Final[str] = "receiving_catchment_id"

# HY_DendriticCatchment neighbour association
UPPER_CATCHMENT_ID: Final[str] = "upper_catchment_id"

# HY_WaterBody network navigation (Section 7.4.2)
UPSTREAM_WATERBODY_ID: Final[str] = "upstream_waterbody_id"
DOWNSTREAM_WATERBODY_ID: Final[str] = "downstream_waterbody_id"

# HY_IndirectPosition / river referencing (Section 7.3.3)
REFERENCE_NEXUS_ID: Final[str] = "reference_nexus_id"
LINEAR_ELEMENT_ID: Final[str] = "linear_element_id"
DISTANCE_FROM_OUTLET_M: Final[str] = "distance_from_outlet_m"
DISTANCE_FROM_OUTLET_PCT: Final[str] = "distance_from_outlet_pct"

# Network and metadata
DRAINAGE_PATTERN_COL: Final[str] = "drainage_pattern"
HYF_TYPE_URI: Final[str] = "hyf_type_uri"
NETWORK_ID: Final[str] = "network_id"

# Legacy TauDEM / MESH column names (kept as optional aliases)
LEGACY_BASIN_ID: Final[str] = "DN"
LEGACY_FLOWPATH_ID: Final[str] = "LINKNO"
LEGACY_LOWER_ID: Final[str] = "DSLINKNO"
LEGACY_LAKE_ID: Final[str] = "lake_id"
LEGACY_IS_LAKE: Final[str] = "is_lake"
LEGACY_LAKE_AREA: Final[str] = "lake_area"
LEGACY_GAUGE_IDS: Final[str] = "STATION_NU"

# HydroLAKES source columns
HYLAKES_ID: Final[str] = "Hylak_id"
HYLAKES_LAKE_TYPE: Final[str] = "Lake_type"
HYLAKES_NATURAL_LAKE: Final[int] = 1

# Annex B.1 — hydroLocationType vocabulary (subset used by this workflow)
HYDRO_LOC_POUR_POINT: Final[str] = "pour point"
HYDRO_LOC_CONFLUENCE: Final[str] = "confluence"
HYDRO_LOC_OUTLET_STRUCTURE: Final[str] = "outlet structure"
HYDRO_LOC_HYDROMETRIC: Final[str] = "hydrometric station"
HYDRO_LOC_CATCHMENT_OUTLET: Final[str] = "catchment outlet"

# Map pour-point point_type values to Annex B.1 terms
POINT_TYPE_TO_HYDRO_LOC: Final[dict[str, str]] = {
    "inflow": HYDRO_LOC_CONFLUENCE,
    "outflow": HYDRO_LOC_CATCHMENT_OUTLET,
    "gauge": HYDRO_LOC_HYDROMETRIC,
}

# Drainage pattern for the study network
DRAINAGE_PATTERN: Final[str] = "dendritic"

# MESH / WATFLOOD outlet sentinel (documented as nillable outflow nexus)
DEFAULT_OUTLET_SENTINEL: Final[int] = -9999
MESH_OUTLET_SENTINEL: Final[int] = DEFAULT_OUTLET_SENTINEL  # backward compatible

# ---------------------------------------------------------------------------
# Default layer column aliases (mesh preset); extend via model_presets.json
# ---------------------------------------------------------------------------
DEFAULT_LAYER_ALIASES: Final[dict[str, dict[str, str]]] = {
    "catchment_area": {
        CATCHMENT_ID: LEGACY_BASIN_ID,
        IS_LAKE_CATCHMENT: LEGACY_IS_LAKE,
        WATERBODY_ID: LEGACY_LAKE_ID,
        LAKE_AREA_M2: LEGACY_LAKE_AREA,
        LEGACY_GAUGE_IDS: LEGACY_GAUGE_IDS,
    },
    "flowpath": {
        FLOWPATH_ID: LEGACY_FLOWPATH_ID,
        CATCHMENT_ID: LEGACY_FLOWPATH_ID,
        LOWER_CATCHMENT_ID: LEGACY_LOWER_ID,
        REALIZES_CATCHMENT: LEGACY_FLOWPATH_ID,
    },
}

# Backward-compatible alias
MESH_FIELD_ALIASES: Final[dict[str, dict[str, str]]] = DEFAULT_LAYER_ALIASES

# Reverse lookup: output name -> canonical (for documentation)
OUTPUT_TO_CANONICAL: Final[dict[str, dict[str, str]]] = {
    layer: {v: k for k, v in mapping.items()}
    for layer, mapping in DEFAULT_LAYER_ALIASES.items()
}
MESH_TO_CANONICAL = OUTPUT_TO_CANONICAL  # backward compatible


def hyf_type_uri(short_code: str) -> str:
    """Return OGC Definitions Server URI for a HY_Features type code."""
    return f"{HYF_NS}{short_code}"


def classify_waterbody(lake_type: int | float | None, is_lake_catchment: bool = False) -> str:
    """Map HydroLAKES Lake_type to HY_Lake / HY_Impoundment / HY_Reservoir."""
    if is_lake_catchment:
        return HY_RESERVOIR
    try:
        lt = int(lake_type)
    except (TypeError, ValueError):
        return HY_LAKE
    if lt == HYLAKES_NATURAL_LAKE:
        return HY_LAKE
    return HY_IMPOUNDMENT
