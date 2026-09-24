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
| Nexus vs. location | `hydro_nexus` is a non-spatial table. Its positions are `hydro_location` points with `realized_nexus_id` set: one per distinct place a contributor reaches the nexus (tributaries entering a lake at different shore points give several realizations of one nexus) |
| Study domain | Catchment `domain` (`HY_CatchmentAggregate`) contains every dendritic catchment; it is realized by the `HY_HydrographicNetwork` (`network_id`) and the `HY_ChannelNetwork` (`channel_network`) |
| Aggregated basins | Ids `agg_{LINKNO}`, domain `agg_domain`, network `study_hydrographic_network_aggregated`, all in `geofabric_aggregated.gpkg` |
| Gauge catchments | Catchment `gauge_{station}` (`HY_CatchmentAggregate`) contains the host catchment and every catchment upstream; its outflow `nx_gauge_{station}` is realized by the station (`hydrometric_feature.realized_nexus_id`, outlet-at-station) |
| Hydrometric networks | `hmn_{station}` realizes `gauge_{station}`; its stations are the outlet station and every station upstream of it (on the host reach, only stations further from the reach outlet) |
| Catchment divides | `dv_{catchment_id}`: the catchment polygon boundary. Shared pieces shorter than 1 m are ignored as vertex touches |
| Names | `feature_name` column = preferred name; every name is a `feature_name` table row. Extra `feature_name_<lang>` columns (for example `feature_name_fr`) add alternative names |
| Outlet sentinel | Raw downstream ids `≤ 0` or equal to the preset sentinel (`-9999` for MESH) mean “domain outlet” |
| Lake typing | HydroLAKES `Lake_type` 1 → `HY_Lake`; 2/3 → `HY_Impoundment`; OGC `HY_Reservoir` storage model is **out of profile** |
| `is_lake_catchment` | TauDEM merge flag only; not an HY feature type |

## Null and default values

| Context | Absent / nillable representation |
|---------|----------------------------------|
| GeoPackage string associations | Empty string `""` |
| JSON sidecars (`catchment_registry.json`, `hydrographic_network.json`) | JSON `null` for nillable associations |
| Domain outlet | Empty / `null` downstream catchment and receiving catchment |
| Optional layers | Layer omitted entirely when inputs unavailable (`waterbody`, `hydrometric_feature`) |
| Empty link tables | Not written (for example `waterbody_upstream_waterbody` without HydroLAKES) |

## Fixed defaults

| Field | Default |
|-------|---------|
| `network_id` | `study_hydrographic_network` |
| `domain` catchment id | `domain` |
| `channel_network.drainage_pattern` | `dendritic` |
| Name language (`assemble_full_geofabric(name_language=...)`) | `und` (ISO 639-2 undetermined) |
| Name `usage` (Annex B.4) / `preferred_by` | HydroLAKES names: `conventional` / `HydroLAKES`; HYDAT station names: `official` / `Water Survey of Canada (HYDAT)` |
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
- Every exported station gets a gauge catchment (`gauge_catchment` polygon, `area_km2`), a gauge nexus, and a hydrometric network.

### Water bodies

- `waterbody` layer is exported when HydroLAKES polygons are supplied.
- `upstream_waterbody_id` lists the nearest lake on every upstream branch (comma-separated); `downstream_waterbody_id` is the next lake downstream. Both are empty when no such lake exists.
- `waterbody_members` in `hydrographic_network.json` includes ids from both the polygon layer and lake-dominated catchments.

### Hydro locations

- Always exported: every nexus has at least one realization, typed `confluence` (two or more contributors meet), `river mouth` (flow entering a lake catchment), or `catchment outlet`.
- Pour points supplied to `cleanGeofabric.py` that lie within the snap distance of a nexus realization are dropped as duplicates (the count is logged). The rest are added with an empty `realized_nexus_id`, since a hydro location need not realize a nexus.
- `feature_name` carries the pour point `name`; it is empty on network-derived locations.

## GeoPackage tables ↔ UML mapping

| Table | HY_Features element |
|-------|---------------------|
| `catchment` | Holistic `HY_DendriticCatchment` / `HY_CatchmentAggregate` (`outflow`, `inflow`, `lowerCatchment`, `upperCatchment`) |
| `catchment_realization` | `catchmentRealization` → `HY_CatchmentArea`, `HY_CatchmentDivide`, `HY_Flowpath`, `HY_HydrographicNetwork`, `HY_ChannelNetwork`, `HY_HydrometricNetwork` |
| `catchment_divide_adjacency` | Neighbour across each part of a `HY_CatchmentDivide` (empty = domain boundary) |
| `hydrometric_network_station` | `networkStation` / `hydrometricNetwork` |
| `feature_name` | `HY_HydroFeatureName` |
| `catchment_association` | Non-realization links: `outflow` nexus, `networkWaterBody`, hydrometric `positionOnRiver` |
| `catchment_containment` | `containingCatchment` / `containedCatchment` |
| `catchment_upper_catchment` | `upperCatchment` (one row per link) |
| `nexus_contributing_catchment` | `HY_HydroNexus.contributingCatchment` / `receivingCatchment` |
| `waterbody_upstream_waterbody` | `upstreamWaterBody` |

## JSON ↔ UML mapping

The JSON sidecars repeat the GeoPackage tables for tools that do not read GeoPackage.

| JSON location | HY_Features type |
|---------------|------------------|
| `hydrographic_network.json` → `hydrographic_network` | `HY_HydrographicNetwork` (`realized_catchment` = `domain`) |
| `hydrographic_network.json` → `hydrographic_network.nexus_contributing_catchment[]` | `HY_HydroNexus.contributingCatchment` (one record per link) |
| `hydrographic_network.json` → `dendritic_catchment[]` | `HY_DendriticCatchment` |
| `catchment_registry.json` → `catchments` | Holistic catchment identity |
| `catchment_registry.json` → `realizations[]` | Same as `catchment_realization` |
| `catchment_registry.json` → `associations[]` | Same as `catchment_association` |
| `catchment_registry.json` → `containments[]` | Same as `catchment_containment` |
