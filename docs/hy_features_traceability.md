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
| Unique feature identifier | `feature_id` (prefixed GF id) + type-specific codes | **Done** |
| Geometry (`shape`) | GeoPackage geometry column | **Done** |
| Feature type code | `hyf_type` | **Done** |
| Definitions Server URI | `hyf_type_uri` | **Done** |

---

## HY_DendriticCatchment

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `code` | `catchment_id` in `dendritic_catchment` | **Done** |
| `outflow` | `outflow_nexus_id` | **Done** |
| `inflow` | `inflow_nexus_id` | **Done** |
| `lowerCatchment` | `lower_catchment_id` | **Done** |
| `upperCatchment` | `upper_catchment_id` | **Done** |
| `containingCatchment` | — | **N/A** (nested management units not modeled) |
| `containedCatchment` | — | **N/A** |
| `conjointCatchment` | — | **N/A** |
| `encompassingCatchment` | — | **N/A** |
| `catchmentRealization` | `catchment_registry.json` → `realizations` | **Done** |
| `single-Outflow` / nillable outlet | Empty `receiving_catchment_id` at domain outlet; sentinel `-9999` documented | **Done** |

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

## HY_FlowPath

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `shape` | line geometry | **Done** |
| `realizedCatchment` | `realizes_catchment` | **Done** |
| Downstream catchment | `lower_catchment_id` | **Done** |
| Outflow nexus | `outflow_nexus_id` | **Done** |
| `drainagePattern` | `drainage_pattern` = `dendritic` | **Done** |

---

## HY_HydroNexus

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `contributingCatchment` | `contributing_catchment_id` | **Done** |
| `receivingCatchment` | `receiving_catchment_id` (nillable at outlet) | **Done** |
| `nexusRealization` | `hydro_nexus` geometry + `catchment_registry.json` realizations (`notes`: `nexusRealization`) | **Done** |
| Feature identifier | `nexus_id` | **Done** |

---

## HY_HydrographicNetwork

| UML property | Implementation | Status |
|--------------|----------------|--------|
| Network identifier | `network_id` in JSON | **Done** |
| `drainagePattern` | `drainage_pattern` | **Done** |
| Flowpath members | `flowpath_members` | **Done** |
| `networkWaterBody` | `waterbody_members` + `network_id` on waterbody layer | **Done** |
| `realizedCatchment` | — | **N/A** (network spans whole study domain) |
| Member link on features | `network_id` column on layers | **Done** |

---

## HY_Lake / HY_Impoundment (HY_WaterBody subtypes)

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `shape` | polygon geometry | **Done** |
| `name` | `feature_name` from HydroLAKES `Lake_name` | **Done** |
| `hyf_type` | `HY_Lake` / `HY_Impoundment` from `Lake_type` | **Done** |
| Feature identifier | `waterbody_id`, `feature_id` | **Done** |
| `upstreamWaterBody` | `upstream_waterbody_id` | **Done** — graph walk via dendritic catchments |
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
| `hydroLocationType` | `hydro_loc_type` (Annex B.1 subset) | **Done** |
| `realizedNexus` | `realized_nexus_id` (= `nexus_id`) | **Done** |
| `referencedPosition` | — | **N/A** (gauges use hydrometric layer) |

---

## HY_IndirectPosition (via hydrometric)

| UML property | Implementation | Status |
|--------------|----------------|--------|
| `linearElement` | `linear_element_id` | **Done** |
| `referenceLocation` | `reference_nexus_id` | **Done** |
| Distance expression | `distance_from_outlet_m`, `distance_from_outlet_pct` | **Done** |

---

## Catchment registry (cross-cutting)

| Concept | Implementation | Status |
|---------|----------------|--------|
| Catchment identity | `catchments` map | **Done** |
| Realization index | `realizations` list | **Done** |
| Waterbody realization type | `HY_Lake` / `HY_Impoundment` from `lake_type` | **Done** |

---

## Related documentation

| Area | Reference |
|------|-----------|
| Property mapping | [`hy_features_mapping.md`](hy_features_mapping.md) |
| Machine-readable schema | `hy_features/implementation_schema.json` |
