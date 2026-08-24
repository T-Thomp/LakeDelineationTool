# HY_Features Conformance Profile

Reference: [OGC 14-111r6](https://docs.ogc.org/is/14-111r6/14-111r6.html) · Annex A.2 `/conf/hy_features_conceptual_model/mapping`

This document defines the **declared HY_Features profile** for Lake Delineation Tool exports. Conformance is scoped: the tool implements mandatory UML elements for **each in-scope type** via a documented GeoPackage + JSON implementation schema.

## Conformance claim

| Item | Value |
|------|--------|
| **Conformance class** | `/conf/hy_features_conceptual_model/mapping` |
| **Requirements class** | `/req/hy_features_conceptual_model` |
| **Target type** | Implementation schema (GeoPackage + JSON sidecars) |
| **Profile name** | `LakeDelineationTool-DendriticGeofabric-1.0` |
| **Encoding** | OGC GeoPackage (primary); JSON metadata (network, registry) |
| **Definition URIs** | `https://www.opengis.net/def/appschema/hy_features/hyf/` |

### In scope (feature types)

| HY_Features type | Delivery |
|------------------|----------|
| `HY_DendriticCatchment` | `hydrographic_network.json` → `dendritic_catchment` |
| `HY_CatchmentArea` | `geofabric.gpkg` → `catchment_area` |
| `HY_FlowPath` | `geofabric.gpkg` → `flowpath` |
| `HY_HydroNexus` | `geofabric.gpkg` → `hydro_nexus` |
| `HY_HydrographicNetwork` | `hydrographic_network.json` → `hydrographic_network` |
| `HY_Lake` / `HY_Impoundment` | `geofabric.gpkg` → `waterbody` (when HydroLAKES supplied) |
| `HY_HydrometricFeature` | `geofabric.gpkg` → `hydrometric_feature` (when gauges supplied) |
| `HY_HydroLocation` | `geofabric.gpkg` → `hydro_location` and/or rows on `hydro_nexus` |
| `HY_IndirectPosition` | Columns on `hydrometric_feature` (river referencing) |

Property-level mapping: [`hy_features_mapping.md`](hy_features_mapping.md)  
Implementation status: [`hy_features_traceability.md`](hy_features_traceability.md)  
Conventions: [`hy_features_implementation_conventions.md`](hy_features_implementation_conventions.md)

### Out of scope (explicit non-claims)

- `HY_Reservoir`, `HY_WaterBodyStratum` (storage model §7.4.4)
- `HY_River`, `HY_Canal`, `HY_Lagoon`, `HY_Estuary` as **waterbody** polygons (streams use `HY_FlowPath`)
- `HY_CatchmentDivide`, `HY_CartographicRealization`, `HY_HydroNetwork` (non-dendritic realizations)
- `HY_InteriorCatchment`, `HY_CatchmentAggregate`, `HY_ExorheicDrainage`, etc.
- Groundwater, atmospheric, glacier catchment realizations
- GML instance encoding

## What this profile means

**Not** implementing the entire HY_Features UML model.

**Yes** implementing every **mandatory** property and association for **each type listed in “In scope”**, with documented mapping to GeoPackage layers and JSON sidecars.

Annex A test method for this class is **inspection** — compare stored attributes to the [normative UML](https://docs.ogc.org/is/14-111r6/uml/) using the mapping documents above.

## Related files

| File | Role |
|------|------|
| [`hy_features_mapping.md`](hy_features_mapping.md) | Column ↔ HY property mapping |
| [`hy_features_traceability.md`](hy_features_traceability.md) | UML mandatory element status |
| [`hy_features_implementation_conventions.md`](hy_features_implementation_conventions.md) | Null/default and optional-layer policy |
| `hy_features/implementation_schema.json` | Machine-readable schema |
