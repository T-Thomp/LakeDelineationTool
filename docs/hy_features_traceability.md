# HY_Features Traceability Matrix

Profile: **`LakeDelineationTool-DendriticGeofabric-1.0`** — see [`hy_features_conformance_profile.md`](hy_features_conformance_profile.md).

Status legend:

| Status | Meaning |
|--------|---------|
| **Done** | Implemented and documented in mapping doc |
| **Partial** | Column or logic exists; incomplete |
| **Gap** | Required for profile; not yet implemented |
| **N/A** | Out of profile scope |

---

## GF_FeatureType (all spatial features)

| UML / requirement | Implementation | Status |
|-------------------|----------------|--------|
| Unique feature identifier | `feature_id` (prefixed GF id) + type-specific codes | **Done** (checked by `hy_features.validate`) |
| Geometry (`shape`) | GeoPackage geometry column | **Done** |
| Feature type code | `hyf_type` | **Done** |
| Definitions Server URI | `hyf_type_uri` | **Done** (checked against `hyf_type`) |

---

## HY_DendriticCatchment

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `code` | `catchment_id` in the `catchment` table | **Done** |
| `outflow` | `outflow_nexus_id` | **Done** |
| `inflow` | `inflow_nexus_id` | **Done** |
| `lowerCatchment` | `lower_catchment_id` | **Done** |
| `upperCatchment` | `catchment_upper_catchment` table | **Done** |
| `containingCatchment` | `catchment_containment` — `domain`; in the aggregated product, the fine catchments' `agg_*` aggregate | **Done** |
| `containedCatchment` | `catchment_containment` (aggregated product: `agg_*` → fine catchments) | **Done** |
| `conjointCatchment` | — | **N/A** |
| `encompassingCatchment` | — | **N/A** |
| `catchmentRealization` | `catchment_realization` table | **Done** |
| `single-Outflow` | One `outflow_nexus_id` per catchment; domain outlets drain to terminal nexus `nx_out_{id}` | **Done** (checked by `hy_features.validate`) |

---

## HY_CatchmentAggregate (study domain)

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `code` | `catchment_id` = `domain` | **Done** |
| `containedCatchment` | `catchment_containment` → every dendritic catchment | **Done** |
| `outflow` | every terminal `nx_out_*` nexus | **Done** |
| `catchmentRealization` | `HY_HydrographicNetwork`, `HY_ChannelNetwork` | **Done** |

---

## HY_CatchmentArea

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `shape` | polygon geometry | **Done** |
| `realizedCatchment` | `realizes_catchment` (= `catchment_id`) | **Done** |
| Outflow nexus link | `outflow_nexus_id` | **Done** |
| Inflow / upper neighbour | `inflow_nexus_id`, `upper_catchment_id` | **Done** |
| Lake / waterbody link | `waterbody_id`, `waterbody_class`, `is_lake_catchment` | **Done** |

---

## HY_Flowpath

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `shape` | line geometry | **Done** |
| `realizedCatchment` | `realizes_catchment` | **Done** |
| Downstream catchment | `lower_catchment_id` | **Done** |
| Outflow nexus | `outflow_nexus_id` | **Done** |

---

## HY_HydroNexus

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `contributingCatchment` (0..*) | `nexus_contributing_catchment` table | **Done** |
| `receivingCatchment` | `receiving_catchment_id` (nillable at outlet) | **Done** |
| `nexusRealization` (0..*) | `hydro_location` rows with `realized_nexus_id`; at least one per nexus | **Done** |
| Feature identifier | `nexus_id` | **Done** |
| No own geometry (topological) | `hydro_nexus` is a non-spatial table | **Done** |

---

## HY_HydrographicNetwork

| UML property | Implementation | Status |
|--------------|----------------|--------|
| Network identifier | `network_id` in JSON | **Done** |
| `realizedCatchment` | `realized_catchment` = `domain` (`HY_CatchmentAggregate`) | **Done** |
| Flowpath members | `flowpath_members` | **Done** |
| `networkWaterBody` | `waterbody_members` + `network_id` on waterbody layer | **Done** |
| Member link on features | `network_id` column on layers | **Done** |

---

## HY_ChannelNetwork

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `shape` | MultiLineString of all flowpaths | **Done** |
| `realizedCatchment` | `realizes_catchment` = `domain` | **Done** |
| `drainagePattern` | `drainage_pattern` = `dendritic` (Annex B.3) | **Done** |

---

## HY_Lake / HY_Impoundment (HY_WaterBody subtypes)

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `shape` | polygon geometry | **Done** |
| `name` | `feature_name` from HydroLAKES `Lake_name` | **Done** |
| `hyf_type` | `HY_Lake` / `HY_Impoundment` from `Lake_type` | **Done** |
| Feature identifier | `waterbody_id`, `feature_id` | **Done** |
| `upstreamWaterBody` (0..*) | `upstream_waterbody_id` (comma-separated, one per upstream branch) | **Done** — graph walk via dendritic catchments |
| `downstreamWaterBody` | `downstream_waterbody_id` | **Done** |
| `hydrographicNetwork` | `network_id` | **Done** |

---

## HY_HydrometricFeature

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `shape` | point geometry | **Done** |
| `positionOnRiver` → linear element | `linear_element_id`, `host_flowpath_id` | **Done** |
| `positionOnRiver` → reference nexus | `reference_nexus_id` | **Done** |
| `positionOnRiver` → distance | `distance_from_outlet_m`, `distance_from_outlet_pct` | **Done** |
| Station identifier | `station_code` | **Done** |
| `hydrometricNetwork` | — | **N/A** (single stations, no network aggregate) |
| Placement coverage | Only placed gauges exported; unplaced omitted with warning | **Done** |

---

## HY_HydroLocation

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `shape` | point geometry | **Done** |
| `hydroLocationType` | `hydro_loc_type` (Annex B.1: `confluence`, `river mouth`, `catchment outlet`, `hydrometric station`) | **Done** |
| `realizedNexus` | `realized_nexus_id` — set on every network-derived location; empty on pour points not near a nexus | **Done** |
| `referencedPosition` | — | **N/A** (gauges use hydrometric layer) |

---

## HY_IndirectPosition (via hydrometric)

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `linearElement` | `linear_element_id` | **Done** |
| `referenceLocation` | `reference_nexus_id` | **Done** |
| `distanceExpression` | `distance_from_outlet_m`, `distance_from_outlet_pct` | **Done** |
| `distanceDescription` | `distance_description` = `upstream` (Annex B.2) | **Done** |

---

## Catchment registry (cross-cutting)

| Concept | Implementation | Status |
|---------|----------------|--------|
| Catchment identity | `catchment` table / `catchments` map | **Done** |
| Realization index | `catchment_realization` / `realizations` (`HY_CatchmentArea`, `HY_Flowpath`, `HY_HydrographicNetwork`, `HY_ChannelNetwork`) | **Done** |
| Non-realization links | `catchment_association` / `associations` (outflow nexus, `networkWaterBody`, `positionOnRiver`) | **Done** |
| Nesting | `catchment_containment` / `containments` | **Done** |
| Referential integrity | `hy_features.validate` | **Done** |

---

## Related documentation

| Area | Reference |
|------|-----------|
| Property mapping | [`hy_features_mapping.md`](hy_features_mapping.md) |
| Machine-readable schema | `hy_features/implementation_schema.json` |
