# HY_Features Conformance Profile

Reference: [OGC 14-111r6](https://docs.ogc.org/is/14-111r6/14-111r6.html) · Annex A.2 conformance class `/conf/hy_features_conceptual_model`

This document defines the **declared HY_Features profile** for Lake Delineation Tool exports. The claim is scoped: the tool implements a subset of HY_Features feature types through a documented GeoPackage + JSON implementation schema. It is a self-assessment by inspection, not an OGC certification.

## Conformance claim

| Item | Value |
|------|--------|
| **Conformance class** | `/conf/hy_features_conceptual_model` (HY_Features implementation schema equivalence) |
| **Tests addressed** | `/conf/hy_features_conceptual_model/mapping`, `/req/hy_features_conceptual_model/GF_Feature` |
| **Requirements class** | `/req/hy_features_conceptual_model` |
| **Target type** | Implementation schema (GeoPackage + JSON sidecars) |
| **Profile name** | `LakeDelineationTool-DendriticGeofabric-1.0` |
| **Encoding** | OGC GeoPackage (primary); JSON metadata (network, registry) |
| **Definition URIs** | `https://www.opengis.net/def/appschema/hy_features/hyf/` |

Shapefiles (`basins.shp`, `streams.shp`, gauges, pour points) are **legacy TauDEM / MESH products** and are not part of the claim; they carry no HY_Features columns.

### In scope (feature types)

| HY_Features type | Delivery |
|------------------|----------|
| `HY_DendriticCatchment` | `hydrographic_network.json` → `dendritic_catchment` |
| `HY_CatchmentArea` | `geofabric.gpkg` → `catchment_area` |
| `HY_Flowpath` | `geofabric.gpkg` → `flowpath` |
| `HY_HydroNexus` | `geofabric.gpkg` → `hydro_nexus` |
| `HY_HydrographicNetwork` | `hydrographic_network.json` → `hydrographic_network` |
| `HY_Lake` / `HY_Impoundment` | `geofabric.gpkg` → `waterbody` (when HydroLAKES supplied) |
| `HY_HydrometricFeature` | `geofabric.gpkg` → `hydrometric_feature` (when gauges supplied) |
| `HY_HydroLocation` | `geofabric.gpkg` → `hydro_location` (when pour points supplied) |
| `HY_IndirectPosition` | Columns on `hydrometric_feature` (river referencing) |

Property-level mapping: [`hy_features_mapping.md`](hy_features_mapping.md)  
Implementation status: [`hy_features_traceability.md`](hy_features_traceability.md)  
Conventions: [`hy_features_implementation_conventions.md`](hy_features_implementation_conventions.md)

### Out of scope (explicit non-claims)

- `HY_Reservoir`, `HY_WaterBodyStratum` (storage model §7.4.4)
- `HY_River`, `HY_Canal`, `HY_Lagoon`, `HY_Estuary` as **waterbody** polygons (streams use `HY_Flowpath`)
- `HY_ChannelNetwork` as a separate feature (its `drainagePattern` value is recorded on the network metadata as a profile extension)
- `HY_CatchmentDivide`, `HY_CartographicRealization`, `HY_HydroNetwork` (non-dendritic realizations)
- `HY_InteriorCatchment`, `HY_CatchmentAggregate`, `HY_ExorheicDrainage`, etc.
- Nested catchment associations (`containingCatchment`, `conjointCatchment`, …)
- Groundwater, atmospheric, glacier catchment realizations
- GML instance encoding

## Known profile simplifications

- Multi-valued associations stored on a feature row (`upper_catchment_id`, `contributing_catchment_id`, `upstream_waterbody_id`) are comma-separated strings in the GeoPackage. The nexus ↔ contributing catchment links are also published one-per-record in `hydrographic_network.json` → `nexus_contributing_catchment`.
- A shared confluence nexus uses the outlet point of its first contributing reach as geometry. Contributors that enter a lake catchment at different shoreline points still share that single nexus.

## What this profile means

**Not** implementing the entire HY_Features UML model.

**Yes** implementing the properties and associations listed in the traceability matrix for **each type listed in “In scope”**, with documented mapping to GeoPackage layers and JSON sidecars.

Annex A test method for this class is **inspection** — compare stored attributes to the [normative UML](https://docs.ogc.org/is/14-111r6/uml/) using the mapping documents above.

## Related files

| File | Role |
|------|------|
| [`hy_features_mapping.md`](hy_features_mapping.md) | Column ↔ HY property mapping |
| [`hy_features_traceability.md`](hy_features_traceability.md) | UML element status |
| [`hy_features_implementation_conventions.md`](hy_features_implementation_conventions.md) | Null/default and optional-layer policy |
| `hy_features/implementation_schema.json` | Machine-readable schema |
