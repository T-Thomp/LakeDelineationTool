# HY_Features Implementation Conventions

Profile: **`LakeDelineationTool-DendriticGeofabric-1.0`**

This document states encoding conventions reviewers use when comparing GeoPackage and JSON exports to the [OGC HY_Features UML](https://docs.ogc.org/is/14-111r6/uml/).

## Profile assumptions (not full UML)

| Assumption | Rationale |
|------------|-----------|
| Dendritic TauDEM network | One `HY_FlowPath` per catchment; `catchment_id` = `flowpath_id` on the flowpath layer |
| Domain outlet | `lower_catchment_id` and `receiving_catchment_id` are empty; raw TauDEM uses sentinel `-9999` (MESH) before HY enrichment |
| Inflow nexus | `inflow_nexus_id` references the **outflow nexus** of upstream catchment(s): `nx_out_{upstream_id}` |
| Lake typing | HydroLAKES `Lake_type` 1 → `HY_Lake`; 2/3 → `HY_Impoundment`; OGC `HY_Reservoir` storage model is **out of profile** |
| `is_lake_catchment` | TauDEM merge flag only; not an HY feature type |

## Null and default values

| Context | Absent / nillable representation |
|---------|----------------------------------|
| GeoPackage string associations | Empty string `""` |
| JSON sidecars (`catchment_registry.json`, `hydrographic_network.json`) | JSON `null` for nillable associations |
| Domain outlet | Empty / `null` downstream catchment and receiving catchment |
| Optional layers | Layer omitted entirely when inputs unavailable (`waterbody`, `hydrometric_feature`, `hydro_location`) |

## Fixed defaults

| Field | Default |
|-------|---------|
| `network_id` | `study_hydrographic_network` |
| `drainage_pattern` | `dendritic` |
| MESH outlet sentinel (legacy remap only) | `-9999` |
| Gauge snap search radius | `5000` m |
| Missing HydroLAKES `Lake_type` | `HY_Lake` |

## Optional feature policies

### Hydrometric features

- Input gauges that cannot be snapped to a flowpath within the search radius are **omitted** from `hydrometric_feature`.
- Every exported `HY_HydrometricFeature` row has complete `positionOnRiver` / `HY_IndirectPosition` columns.
- Omission count is logged to the console during assembly.

### Water bodies

- `waterbody` layer is exported when HydroLAKES polygons are supplied.
- `upstream_waterbody_id` / `downstream_waterbody_id` are nillable when no lake exists upstream/downstream on the dendritic chain.
- `waterbody_members` in `hydrographic_network.json` includes ids from both the polygon layer and lake-dominated catchments.

### Hydro locations

- Exported when pour points are supplied to `cleanGeofabric.py`.
- `realized_nexus_id` equals `nexus_id` for each location row.

## JSON ↔ UML mapping

| JSON location | HY_Features type |
|---------------|------------------|
| `hydrographic_network.json` → `hydrographic_network` | `HY_HydrographicNetwork` |
| `hydrographic_network.json` → `dendritic_catchment[]` | `HY_DendriticCatchment` |
| `catchment_registry.json` → `catchments` | Holistic catchment identity (`HY_DendriticCatchment`) |
| `catchment_registry.json` → `realizations[]` | `catchmentRealization` / `nexusRealization` links to GeoPackage `feature_id` |
