# HY_Features Implementation Conventions

Profile: **`LakeDelineationTool-DendriticGeofabric-1.0`**

This document states encoding conventions reviewers use when comparing GeoPackage and JSON exports to the [OGC HY_Features UML](https://docs.ogc.org/is/14-111r6/uml/).

## Profile assumptions (not full UML)

| Assumption | Rationale |
|------------|-----------|
| Dendritic TauDEM network | One `HY_Flowpath` per catchment; `catchment_id` = `flowpath_id` on the flowpath layer |
| Identifiers | Ids are integer strings (`"12"`, never `"12.0"`) regardless of how the source column was typed |
| Shared nexus | All catchments draining into catchment `X` share one nexus `nx_X` (their `outflow_nexus_id`, and `X`'s `inflow_nexus_id`) |
| Domain outlet | A catchment with no receiving catchment drains to a terminal nexus `nx_out_{id}`; `lower_catchment_id` and `receiving_catchment_id` are empty |
| Outlet sentinel | Raw downstream ids `≤ 0` or equal to the preset sentinel (`-9999` for MESH) mean “domain outlet” |
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
| `channel_network_drainage_pattern` (network metadata) | `dendritic` |
| MESH outlet sentinel (legacy remap only) | `-9999` |
| Gauge snap search radius | `5000` m (geographic inputs are measured in a local UTM zone) |
| Pour point → nexus snap distance | `250` m |
| Missing HydroLAKES `Lake_type` | `HY_Lake` |

## Shapefiles

Shapefiles written by the pipeline keep only TauDEM / MESH columns (`DN`, `LINKNO`, `DSLINKNO`, `lake_id`, …). HY_Features columns are published in `geofabric.gpkg` only, because the 10-character DBF name limit truncates them into colliding names (for example `waterbody_id` / `waterbody_class` → `waterbody_`). Writes fail fast if any remaining names would collide after truncation, and string values longer than 254 characters are truncated with a warning.

## Optional feature policies

### Hydrometric features

- Input gauges that cannot be snapped to a flowpath within the search radius are **omitted** from `hydrometric_feature`.
- Every exported `HY_HydrometricFeature` row has complete `positionOnRiver` / `HY_IndirectPosition` columns, including `distance_description = upstream`.
- Omission count is logged to the console during assembly.

### Water bodies

- `waterbody` layer is exported when HydroLAKES polygons are supplied.
- `upstream_waterbody_id` lists the nearest lake on every upstream branch (comma-separated); `downstream_waterbody_id` is the next lake downstream. Both are empty when no such lake exists.
- `waterbody_members` in `hydrographic_network.json` includes ids from both the polygon layer and lake-dominated catchments.

### Hydro locations

- Exported when pour points are supplied to `cleanGeofabric.py`, in the `hydro_location` layer only (not duplicated into `hydro_nexus`).
- `realized_nexus_id` is the outflow nexus of the reach whose outlet is nearest the point, within the snap distance; otherwise empty (a hydro location need not realize a nexus).

## JSON ↔ UML mapping

| JSON location | HY_Features type |
|---------------|------------------|
| `hydrographic_network.json` → `hydrographic_network` | `HY_HydrographicNetwork` |
| `hydrographic_network.json` → `hydrographic_network.nexus_contributing_catchment[]` | `HY_HydroNexus.contributingCatchment` (one record per link) |
| `hydrographic_network.json` → `dendritic_catchment[]` | `HY_DendriticCatchment` |
| `catchment_registry.json` → `catchments` | Holistic catchment identity (`HY_DendriticCatchment`) |
| `catchment_registry.json` → `realizations[]` | `catchmentRealization` links (`HY_CatchmentArea`, `HY_Flowpath`) to GeoPackage `feature_id` |
| `catchment_registry.json` → `associations[]` | Non-realization links: outflow nexus, `networkWaterBody`, hydrometric `positionOnRiver` |
