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
HY_FLOWPATH: Final[str] = "HY_Flowpath"
HY_HYDRO_LOCATION: Final[str] = "HY_HydroLocation"
HY_HYDROMETRIC_FEATURE: Final[str] = "HY_HydrometricFeature"
HY_LAKE: Final[str] = "HY_Lake"
HY_IMPOUNDMENT: Final[str] = "HY_Impoundment"
HY_RESERVOIR: Final[str] = "HY_Reservoir"
HY_HYDRO_NEXUS: Final[str] = "HY_HydroNexus"
HY_HYDROGRAPHIC_NETWORK: Final[str] = "HY_HydrographicNetwork"
HY_CHANNEL_NETWORK: Final[str] = "HY_ChannelNetwork"
HY_CATCHMENT_AGGREGATE: Final[str] = "HY_CatchmentAggregate"
HY_CATCHMENT_DIVIDE: Final[str] = "HY_CatchmentDivide"
HY_HYDROMETRIC_NETWORK: Final[str] = "HY_HydrometricNetwork"
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

# HY_Catchment containingCatchment / containedCatchment ("is-in" hierarchy)
CONTAINING_CATCHMENT_ID: Final[str] = "containing_catchment_id"
CONTAINED_CATCHMENT_ID: Final[str] = "contained_catchment_id"

# Registry tables (catchmentRealization and non-realization links)
REALIZATION_TYPE: Final[str] = "realization_type"
FEATURE_TYPE: Final[str] = "feature_type"
ASSOCIATION_ROLE: Final[str] = "role"

# HY_ChannelNetwork realization of the study-domain catchment
CHANNEL_NETWORK_ID: Final[str] = "channel_network_id"

# HY_CatchmentDivide neighbours (catchment_divide_adjacency table)
ADJACENT_CATCHMENT_ID: Final[str] = "adjacent_catchment_id"
SHARED_LENGTH_M: Final[str] = "shared_length_m"

# Gauge catchments realized by HY_HydrometricNetwork (Section 7.5)
HYDROMETRIC_NETWORK_ID: Final[str] = "hydrometric_network_id"
HYDROMETRIC_FEATURE_ID: Final[str] = "hydrometric_feature_id"
HOST_CATCHMENT_ID: Final[str] = "host_catchment_id"

# HY_HydroFeatureName (Section 7.3.1, Table 5); ``language`` is a profile extension
NAMED_FEATURE_ID: Final[str] = "named_feature_id"
NAME: Final[str] = "name"
NAME_LANGUAGE: Final[str] = "language"
NAME_USAGE: Final[str] = "usage"
NAME_PREFERRED_BY: Final[str] = "preferred_by"
NAMES_PART: Final[str] = "names_part"
VARIANT_SPELLING: Final[str] = "variant_spelling"
UNDETERMINED_LANGUAGE: Final[str] = "und"  # ISO 639-2 "undetermined"

# Annex B.4 — HY_NameUsage vocabulary
NAME_USAGE_CONVENTIONAL: Final[str] = "conventional"
NAME_USAGE_HISTORICAL: Final[str] = "historical"
NAME_USAGE_OFFICIAL: Final[str] = "official"
NAME_USAGE_VERNACULAR: Final[str] = "vernacular"

# Study domain: HY_CatchmentAggregate of every dendritic catchment in the network,
# realized by the HY_HydrographicNetwork and HY_ChannelNetwork records.
DEFAULT_DOMAIN_CATCHMENT_ID: Final[str] = "domain"

# HY_WaterBody network navigation (Section 7.4.2)
UPSTREAM_WATERBODY_ID: Final[str] = "upstream_waterbody_id"
DOWNSTREAM_WATERBODY_ID: Final[str] = "downstream_waterbody_id"

# HY_IndirectPosition / river referencing (Section 7.3.3)
REFERENCE_NEXUS_ID: Final[str] = "reference_nexus_id"
LINEAR_ELEMENT_ID: Final[str] = "linear_element_id"
DISTANCE_FROM_OUTLET_M: Final[str] = "distance_from_outlet_m"
DISTANCE_FROM_OUTLET_PCT: Final[str] = "distance_from_outlet_pct"
DISTANCE_DESCRIPTION: Final[str] = "distance_description"

# Network and metadata
DRAINAGE_PATTERN_COL: Final[str] = "drainage_pattern"
HYF_TYPE_URI: Final[str] = "hyf_type_uri"
NETWORK_ID: Final[str] = "network_id"
DEFAULT_NETWORK_ID: Final[str] = "study_hydrographic_network"
CONFORMANCE_PROFILE: Final[str] = "LakeDelineationTool-DendriticGeofabric-1.0"

# GF_Feature / HY_HydroFeature metadata
FEATURE_ID: Final[str] = "feature_id"
FEATURE_NAME: Final[str] = "feature_name"
REALIZED_NEXUS_ID: Final[str] = "realized_nexus_id"

# Legacy TauDEM / MESH column names (kept as optional aliases)
LEGACY_BASIN_ID: Final[str] = "DN"
LEGACY_FLOWPATH_ID: Final[str] = "LINKNO"
LEGACY_LOWER_ID: Final[str] = "DSLINKNO"
LEGACY_LAKE_ID: Final[str] = "lake_id"
LEGACY_IS_LAKE: Final[str] = "is_lake"
LEGACY_LAKE_AREA: Final[str] = "lake_area"
LEGACY_GAUGE_IDS: Final[str] = "STATION_NU"

# HydroLAKES source columns (Lake_type: 1=lake, 2=reservoir, 3=lake control)
HYLAKES_ID: Final[str] = "Hylak_id"
HYLAKES_LAKE_TYPE: Final[str] = "Lake_type"
HYLAKES_LAKE_NAME: Final[str] = "Lake_name"
HYLAKES_NATURAL_LAKE: Final[int] = 1
HYLAKES_RESERVOIR: Final[int] = 2
HYLAKES_LAKE_CONTROL: Final[int] = 3
LAKE_TYPE: Final[str] = "lake_type"
LEGACY_LAKE_TYPE: Final[str] = "Lake_type"

# Annex B.1 — hydroLocationType vocabulary (subset used by this workflow)
HYDRO_LOC_POUR_POINT: Final[str] = "pour point"
HYDRO_LOC_CONFLUENCE: Final[str] = "confluence"
HYDRO_LOC_RIVER_MOUTH: Final[str] = "river mouth"
HYDRO_LOC_HYDROMETRIC: Final[str] = "hydrometric station"
HYDRO_LOC_CATCHMENT_OUTLET: Final[str] = "catchment outlet"

# Map pour-point point_type values to Annex B.1 terms
POINT_TYPE_TO_HYDRO_LOC: Final[dict[str, str]] = {
    "inflow": HYDRO_LOC_RIVER_MOUTH,
    "outflow": HYDRO_LOC_CATCHMENT_OUTLET,
    "gauge": HYDRO_LOC_HYDROMETRIC,
}

# Annex B.2 — distanceDescription for positions measured upstream from a reach outlet
DISTANCE_DESCRIPTION_UPSTREAM: Final[str] = "upstream"

# Drainage pattern of the study channel network (HY_ChannelNetwork.drainagePattern)
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


def inflow_nexus_id_for(catchment_id: str) -> str:
    """Nexus where every upstream catchment drains into ``catchment_id`` (``nx_{id}``)."""
    return f"nx_{catchment_id}"


def terminal_nexus_id_for(catchment_id: str) -> str:
    """Domain-outlet nexus of a catchment with no receiving catchment (``nx_out_{id}``)."""
    return f"nx_out_{catchment_id}"


def gauge_catchment_id_for(station_code: str) -> str:
    """Catchment upstream of a hydrometric station (``gauge_{station}``)."""
    return f"gauge_{station_code}"


def gauge_nexus_id_for(station_code: str) -> str:
    """Outflow nexus of a gauge catchment, realized by the station (``nx_gauge_{station}``)."""
    return f"nx_gauge_{station_code}"


def outflow_nexus_id_for(catchment_id: str, lower_catchment_id: str | None = None) -> str:
    """
    Outflow nexus of ``catchment_id``.

    Catchments draining to the same receiving catchment share one nexus
    (``nx_{lower}``); domain outlets get a terminal nexus (``nx_out_{id}``).
    """
    if lower_catchment_id:
        return inflow_nexus_id_for(lower_catchment_id)
    return terminal_nexus_id_for(catchment_id)


def normalize_id(value: object) -> str:
    """Render an identifier as text, collapsing integral floats (``1.0`` -> ``"1"``)."""
    if value is None:
        return ""
    try:
        if value != value:  # NaN
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) or type(value).__name__.startswith("float"):
        f = float(value)
        return str(int(f)) if f.is_integer() else str(f)
    text = str(value).strip()
    if text.lower() in ("nan", "none", "<na>"):
        return ""
    try:
        f = float(text)
    except ValueError:
        return text
    return str(int(f)) if f.is_integer() and "." in text else text


def classify_waterbody(lake_type: int | float | None) -> str:
    """Map HydroLAKES ``Lake_type`` to ``HY_Lake`` / ``HY_Impoundment`` (``HY_WaterBody`` subtypes)."""
    try:
        lt = int(lake_type)
    except (TypeError, ValueError):
        return HY_LAKE
    if lt in (HYLAKES_RESERVOIR, HYLAKES_LAKE_CONTROL):
        return HY_IMPOUNDMENT
    return HY_LAKE


WATERBODY_HYF_TYPES: Final[frozenset[str]] = frozenset({HY_LAKE, HY_IMPOUNDMENT})
