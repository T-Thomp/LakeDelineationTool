# HY_Features Mapping — Lake Delineation Tool

Reference standard: [OGC WaterML 2 Part 3 — Surface Hydrology Features (14-111r6)](https://docs.ogc.org/is/14-111r6/14-111r6.html)

## Conformance target

This workflow targets OGC **Annex A.2 — implementation schema equivalence** (`/conf/hy_features_conceptual_model/mapping`), not full OGC product certification.

Per Clause 7.2, the HY_Features UML model is a **conceptual model** and is not directly persistable. This project therefore defines an explicit **implementation schema**: GeoPackage layers, JSON sidecars, and column names that document how each stored attribute implements a HY_Features property or association.

Mapping and profile scope are documented in
[`hy_features_conformance_profile.md`](hy_features_conformance_profile.md),
[`hy_features_traceability.md`](hy_features_traceability.md), and
[`hy_features_implementation_conventions.md`](hy_features_implementation_conventions.md).

## Enabling assembly

HY enrichment is **off by default**. Enable with:

```bash
export HY_FEATURES_ENABLED=1
```

or set `ENABLE_HY_FEATURES = True` in a pipeline script.

Assembly entry point:

```python
from hy_features.assemble import assemble_full_geofabric, export_full_geofabric
```

| Script | When assembly runs | Optional inputs |
|--------|-------------------|-----------------|
| `combiningBasins.py` | After reservoir merge | Gauges, HydroLAKES polygons |
| `cleanGeofabric.py` | After phantom-stream cleanup | Gauges, HydroLAKES, pour points (`outputs/final/pour_points.shp`) |

`pourPointsPass2.py`, `filterLakes.py`, and `getGauges.py` only add HY columns or intermediate GeoPackage layers; they do not run full assembly.

## Output products

All paths below are relative to `outputs/working/` unless noted.

| File | HY_Features role | Produced by |
|------|------------------|-------------|
| `geofabric.gpkg` | Spatial realization layers | `combiningBasins.py`, `cleanGeofabric.py` |
| `catchment_registry.json` | Catchment identity ↔ realization index | same |
| `hydrographic_network.json` | `HY_HydrographicNetwork` metadata + `HY_DendriticCatchment` table | same |

### GeoPackage layers

| Layer | HY_Features type(s) | Required | Notes |
|-------|---------------------|----------|-------|
| `catchment_area` | `HY_CatchmentArea` | yes | Basin polygons |
| `flowpath` | `HY_FlowPath` | yes | Stream reaches (TauDEM links) |
| `hydro_nexus` | `HY_HydroNexus`, optionally `HY_HydroLocation` | yes | Outflow nexuses at reach endpoints; pour points appended when provided |
| `waterbody` | `HY_Lake`, `HY_Impoundment` | no | HydroLAKES polygons when available |
| `hydrometric_feature` | `HY_HydrometricFeature` | no | Gauges in basin |
| `hydro_location` | `HY_HydroLocation` | no | Separate pour-point layer; only when `cleanGeofabric.py` receives pour points |

Every spatial feature carries:

| Column | Implements |
|--------|------------|
| `hyf_type` | HY_Features feature type code |
| `hyf_type_uri` | OGC Definitions Server URI |
| `feature_id` | GF_FeatureType identifier (unique, prefixed by layer) |
| `network_id` | Link to `HY_HydrographicNetwork.network_id` |

## Identity conventions

| Concept | Column / id | Source |
|---------|-------------|--------|
| Dendritic catchment `code` | `catchment_id` | TauDEM basin id (`DN`) or link id (`LINKNO`) after merge |
| Flowpath id | `flowpath_id` | TauDEM `LINKNO` |
| Outflow nexus | `outflow_nexus_id` | `nx_out_{catchment_id}` |
| Inflow nexus | `inflow_nexus_id` | `nx_out_{upstream_catchment_id}` (comma-separated if multiple) |
| Water body | `waterbody_id` | HydroLAKES `Hylak_id` / basin `lake_id` when `is_lake_catchment = 1` |
| Domain outlet | `lower_catchment_id = ""` | Downstream id `≤ 0` or preset outlet sentinel (`-9999` for MESH) |

**Important:** In this dendritic TauDEM network, each flowpath realizes one catchment, so `catchment_id` on the flowpath layer equals `flowpath_id`. Catchment **area** polygons use the same id (`catchment_id` = basin `DN`). This is distinct from `waterbody_id`, which identifies the open-water feature when the catchment is lake-dominated.

## Feature type mappings

### HY_DendriticCatchment (non-spatial)

Stored in `hydrographic_network.json` → `dendritic_catchment` and summarized in `catchment_registry.json` → `catchments`.

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| `code` | `catchment_id` | Same identifier used across realizations |
| `outflow` | `outflow_nexus_id` | Links to `HY_HydroNexus` at reach outlet |
| `inflow` | `inflow_nexus_id` | Nillable for headwaters |
| `lowerCatchment` | `lower_catchment_id` | Empty at domain outlet |
| `upperCatchment` | `upper_catchment_id` | Immediate upstream catchment(s); comma-separated confluences |
| `drainagePattern` | `drainage_pattern` | Fixed value `dendritic` |
| `networkWaterBody` (when applicable) | `waterbody_id` | Set for reservoir catchments |

Lake-dominated catchments link to a water body via `waterbody_id` and carry `waterbody_class` derived from the same HydroLAKES `Lake_type` as the polygon layer. The registry adds a second realization row using that class (`HY_Lake` or `HY_Impoundment`).

### HydroLAKES `Lake_type` → HY_Features

| HydroLAKES `Lake_type` | Meaning (HydroLAKES v1.0) | `waterbody_class` / registry |
|------------------------|---------------------------|------------------------------|
| `1` | Natural lake | `HY_Lake` |
| `2` | Reservoir (HydroLAKES name) | `HY_Impoundment` |
| `3` | Lake control (regulated natural lake) | `HY_Impoundment` |
| missing / invalid | — | `HY_Lake` (HydroLAKES default) |

Types `2` and `3` map to **`HY_Impoundment`**, an OGC **`HY_WaterBody` subtype** (water formed or held by a structure, e.g. a dam). HydroLAKES uses the word “reservoir” for type `2`, but OGC **`HY_Reservoir`** is a separate **storage-model** feature (operating levels, `storedWaterBody`) — not used as the polygon `hyf_type` in this workflow.

`is_lake_catchment` (`is_lake`) only flags that the catchment was merged with open water; **typing** comes from `lake_type` (written by `combiningBasins.py` from HydroLAKES).

### HY_CatchmentArea (`catchment_area` layer)

| HY_Features property / association | Implementation column | TauDEM / legacy source |
|-----------------------------------|----------------------|------------------------|
| Feature type | `hyf_type` = `HY_CatchmentArea` | added |
| `realizedCatchment` | `realizes_catchment` | equals `catchment_id` |
| Catchment code | `catchment_id` | `DN` |
| `outflow` | `outflow_nexus_id` | derived |
| `inflow` | `inflow_nexus_id` | derived |
| `upperCatchment` | `upper_catchment_id` | derived |
| Lake flag | `is_lake_catchment` | `is_lake` |
| HydroLAKES type | `lake_type` | from `Lake_type` on merge |
| Linked water body | `waterbody_id` | `lake_id` (empty when none) |
| Water-body class on catchment | `waterbody_class` | from `lake_type` when `is_lake_catchment`; else empty |
| Open-water area | `lake_area_m2` | `lake_area` |
| Fraction lake | `frac_lake` | passthrough |
| Gauge stations in basin | `station_code` | `STATION_NU` (comma-separated) |

### HY_FlowPath (`flowpath` layer)

| HY_Features property / association | Implementation column | TauDEM / legacy source |
|-----------------------------------|----------------------|------------------------|
| Feature type | `hyf_type` = `HY_FlowPath` | added |
| Flowpath id | `flowpath_id` | `LINKNO` |
| `realizedCatchment` | `realizes_catchment` | equals `catchment_id` |
| Catchment code | `catchment_id` | `LINKNO` (1:1 link–catchment) |
| `lowerCatchment` | `lower_catchment_id` | `DSLINKNO`; blanked at outlet sentinel |
| `outflow` | `outflow_nexus_id` | derived |
| `inflow` | `inflow_nexus_id` | derived |
| `upperCatchment` | `upper_catchment_id` | derived |
| `drainagePattern` | `drainage_pattern` | `dendritic` |

### HY_HydroNexus (`hydro_nexus` layer)

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| Identifier | `nexus_id` | `nx_out_{contributing_catchment_id}` |
| Feature type | `hyf_type` = `HY_HydroNexus` | |
| `contributingCatchment` | `contributing_catchment_id` | Catchment whose outflow this nexus realizes |
| `receivingCatchment` | `receiving_catchment_id` | Downstream catchment; empty at domain outlet |
| `realizedCatchment` | `realizes_catchment` | Same as contributing catchment |
| Geometry | point | Topologic outflow endpoint (from `DSLINKNO` / upstream links; TauDEM may store pour point at line start) |

When pour points are supplied, additional rows with `hyf_type = HY_HydroLocation` are **appended to this same layer** (see below).

### HY_HydroLocation (`hydro_location` layer and nexus append)

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| Feature type | `hyf_type` = `HY_HydroLocation` | |
| `hydroLocationType` | `hydro_loc_type` | Annex B.1 vocabulary (see table) |
| Linked water body | `waterbody_id` | From pour-point `lake_id` when present |
| Nexus id | `nexus_id` | From pour-point `name` or generated `nx_loc_*` |

| Pour-point `point_type` | `hydro_loc_type` (Annex B.1) |
|-------------------------|-------------------------------|
| `inflow` | `confluence` |
| `outflow` | `catchment outlet` |
| `gauge` | `hydrometric station` |

### HY_HydrometricFeature (`hydrometric_feature` layer)

Implements `positionOnRiver` via **`HY_IndirectPosition`** (Section 7.3.3).

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| Feature type | `hyf_type` = `HY_HydrometricFeature` | |
| Station identifier | `station_code` | `STATION_NUMBER` or `STATION_NU` |
| `hydroLocationType` | `hydro_loc_type` | `hydrometric station` |
| Host catchment | `catchment_id` | Basin polygon containing the gauge (`DN`) |
| `positionOnRiver` → linear element | `linear_element_id` | Same as `catchment_id` (dendritic: equals `flowpath_id`) |
| `positionOnRiver` → reference nexus | `reference_nexus_id` | Outflow nexus of host catchment |
| `positionOnRiver` → distance | `distance_from_outlet_m`, `distance_from_outlet_pct` | Along host flowpath (`catchment_id`) from outlet |
| Host reach | `host_flowpath_id` | Same as `catchment_id` (not nearest reach) |

**Export policy:** Gauges with no containing catchment fall back to the nearest flowpath within the search radius (default 5 km). Gauges that cannot be linked to a catchment flowpath are **omitted**
from `hydrometric_feature` so every exported row has a complete `positionOnRiver`.

### HY_WaterBody (`waterbody` layer)

HydroLAKES polygons use the same [`Lake_type` mapping](#hydrolakes-lake_type--hy_features) as lake catchments (`classify_waterbody` in `hy_features/schema.py`).

| HY_Features association | Implementation column | Notes |
|------------------------|----------------------|-------|
| Identifier | `waterbody_id` | `Hylak_id` |
| `upstreamWaterBody` | `upstream_waterbody_id` | Walk upstream through non-lake catchments |
| `downstreamWaterBody` | `downstream_waterbody_id` | Walk downstream through non-lake catchments |

### HY_HydrographicNetwork (metadata)

JSON record at `hydrographic_network.json` → `hydrographic_network`:

| HY_Features property | JSON field |
|--------------------|------------|
| Network identifier | `network_id` (default `study_hydrographic_network`) |
| Feature type | `hyf_type` = `HY_HydrographicNetwork` |
| `drainagePattern` | `drainage_pattern` |
| Flowpath members | `flowpath_members` |
| Water-body members | `waterbody_members` |

## Catchment registry

`catchment_registry.json` separates **holistic catchment identity** from geometric realizations (OGC Section 7.2):

- `catchments` — one entry per `catchment_id` with nexus and neighbour links
- `realizations` — rows linking each catchment to `HY_CatchmentArea`, `HY_FlowPath`, `HY_HydroNexus`, `HY_HydrometricFeature`, and (for lake catchments) the linked `HY_Lake` or `HY_Impoundment` water-body id

## Downstream model remapping

The canonical interchange product is `geofabric.gpkg`. Legacy TauDEM / MESH column names are **not** stored in the GeoPackage; regenerate them with [`remap_fields.py`](../remap_fields.py):

```bash
python remap_fields.py --list-presets
python remap_fields.py --preset mesh \
  --basins outputs/working/geofabric.gpkg \
  --streams outputs/working/geofabric.gpkg \
  --drop-metadata \
  --out-dir remapped_products/
```

Presets live in [`hy_features/model_presets.json`](../hy_features/model_presets.json). The `mesh` preset maps:

| Canonical (GeoPackage) | MESH / WATFLOOD output |
|------------------------|------------------------|
| `catchment_id` | `DN` or `LINKNO` (per layer) |
| `flowpath_id` | `LINKNO` |
| `lower_catchment_id` | `DSLINKNO` (outlet sentinel `-9999`) |
| `is_lake_catchment` | `is_lake` |
| `waterbody_id` | `lake_id` |
| `lake_area_m2` | `lake_area` |

Use `--preset taudem_raw` for minimal TauDEM naming. Add custom presets or `--override` for other routing models.

## Implementation source files

| Module | Role |
|--------|------|
| `hy_features/schema.py` | Column names, type codes, vocabulary |
| `hy_features/enrich.py` | Add HY columns to pipeline GeoDataFrames |
| `hy_features/network.py` | Nexuses, dendritic table, waterbody links, gauge positioning |
| `hy_features/assemble.py` | Full layer assembly and export |
| `hy_features/implementation_schema.json` | Machine-readable layer/column schema |
| `hy_features/stamp.py` | Profile metadata (`network_id`, `feature_id`, URIs) |
| `hy_features/field_remap.py` | Canonical → model-specific export |
