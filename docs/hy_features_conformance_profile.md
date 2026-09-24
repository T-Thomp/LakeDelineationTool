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
| `HY_DendriticCatchment` | `geofabric.gpkg` → `catchment` table (also `hydrographic_network.json` → `dendritic_catchment`) |
| `HY_CatchmentAggregate` | `geofabric.gpkg` → `catchment` row `domain` (the study domain containing every dendritic catchment) |
| `HY_CatchmentArea` | `geofabric.gpkg` → `catchment_area` |
| `HY_Flowpath` | `geofabric.gpkg` → `flowpath` |
| `HY_HydroNexus` | `geofabric.gpkg` → `hydro_nexus` table (topological, no geometry) |
| `HY_HydroLocation` | `geofabric.gpkg` → `hydro_location` (one or more per nexus as `nexusRealization`, plus pour points) |
| `HY_HydrographicNetwork` | `hydrographic_network.json` → `hydrographic_network` (realizes `domain`) |
| `HY_ChannelNetwork` | `geofabric.gpkg` → `channel_network` (realizes `domain`, carries `drainagePattern`) |
| `HY_Lake` / `HY_Impoundment` | `geofabric.gpkg` → `waterbody` (when HydroLAKES supplied) |
| `HY_HydrometricFeature` | `geofabric.gpkg` → `hydrometric_feature` (when gauges supplied) |
| `HY_IndirectPosition` | Columns on `hydrometric_feature` (river referencing) |

Every 0..* association is also stored one link per row in a non-spatial GeoPackage table: `catchment_realization`, `catchment_association`, `catchment_containment`, `catchment_upper_catchment`, `nexus_contributing_catchment`, `waterbody_upstream_waterbody`.

When `basinAggregation.py` runs with HY_Features enabled, `geofabric_aggregated.gpkg` carries the same profile for the aggregated basins (ids `agg_{LINKNO}`). Its `catchment_containment` links each aggregate (`containingCatchment`) to the `geofabric.gpkg` catchments merged into it (`containedCatchment`).

Property-level mapping: [`hy_features_mapping.md`](hy_features_mapping.md)  
Implementation status: [`hy_features_traceability.md`](hy_features_traceability.md)  
Conventions: [`hy_features_implementation_conventions.md`](hy_features_implementation_conventions.md)

### Out of scope (explicit non-claims)

- `HY_Reservoir`, `HY_WaterBodyStratum` (storage model §7.4.4)
- `HY_River`, `HY_Canal`, `HY_Lagoon`, `HY_Estuary` as **waterbody** polygons (streams use `HY_Flowpath`)
- `HY_CatchmentDivide`, `HY_CartographicRealization`, `HY_HydroNetwork` (non-dendritic realizations)
- `HY_InteriorCatchment`, `HY_ExorheicDrainage`, `HY_EndorheicDrainage`, etc.
- `conjointCatchment`, `encompassingCatchment` and other catchment associations besides `upperCatchment` / `lowerCatchment` / `containing` / `containedCatchment`
- `HY_HydrometricNetwork` and gauge catchments
- Groundwater, atmospheric, glacier catchment realizations
- GML instance encoding

## Known profile simplifications

- Spatial layers keep comma-separated convenience copies of multi-valued associations (`upper_catchment_id`, `contributing_catchment_id`, `upstream_waterbody_id`). The link tables are the normative encoding.
- The `domain` aggregate's `outflow_nexus_id` in the `catchment` table lists every terminal nexus (comma-separated); dendritic catchments always have exactly one.

## Automated conformance check

`python -m hy_features.validate outputs/working/geofabric.gpkg` repeats the inspection against `implementation_schema.json`. It checks required layers, tables and columns, allowed values, geometry types, `GF_Feature` identity (unique `feature_id`, `hyf_type_uri` matching `hyf_type`), and that every catchment / nexus / flowpath / waterbody reference resolves. It exits non-zero on errors. Pass `--external outputs/working/geofabric.gpkg` when validating `geofabric_aggregated.gpkg` so containment links to fine catchments resolve. The same check runs automatically after every GeoPackage export.

## What this profile means

**Not** implementing the entire HY_Features UML model.

**Yes** implementing the properties and associations listed in the traceability matrix for **each type listed in “In scope”**, with documented mapping to GeoPackage layers and JSON sidecars.

Annex A test method for this class is **inspection** — compare stored attributes to the [normative UML](https://docs.ogc.org/is/14-111r6/uml/) using the mapping documents above. `hy_features.validate` automates the parts of that inspection that can be checked mechanically.

## Related files

| File | Role |
|------|------|
| [`hy_features_mapping.md`](hy_features_mapping.md) | Column ↔ HY property mapping |
| [`hy_features_traceability.md`](hy_features_traceability.md) | UML element status |
| [`hy_features_implementation_conventions.md`](hy_features_implementation_conventions.md) | Null/default and optional-layer policy |
| `hy_features/implementation_schema.json` | Machine-readable schema |
| `hy_features/validate.py` | Automated conformance check |
