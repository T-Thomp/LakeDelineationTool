# HY_Features Mapping — Lake Delineation Tool

Reference standard: [OGC WaterML 2 Part 3 — Surface Hydrology Features (14-111r6)](https://docs.ogc.org/is/14-111r6/14-111r6.html)

## Conformance target

This workflow targets OGC **Annex A.2 — HY_Features implementation schema equivalence**, conformance class `/conf/hy_features_conceptual_model` (tests `/mapping` and `/GF_Feature`), as a self-assessed profile rather than OGC product certification.

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
| `basinAggregation.py` | After aggregation | — (writes `geofabric_aggregated.gpkg`) |

`pourPointsPass2.py`, `filterLakes.py`, and `getGauges.py` only write intermediate GeoPackage layers; they do not run full assembly.

Shapefile outputs (`basins.shp`, `streams.shp`, gauges, lakes, pour points) never carry HY_Features columns. The 10-character DBF limit would truncate them into colliding names, so HY attributes are published in `geofabric.gpkg` only.

## Output products

All paths below are relative to `outputs/working/` unless noted.

| File | HY_Features role | Produced by |
|------|------------------|-------------|
| `geofabric.gpkg` | Spatial realization layers + holistic catchment and link tables | `combiningBasins.py`, `cleanGeofabric.py` |
| `catchment_registry.json` | Catchment identity ↔ realization index (mirrors the GeoPackage tables) | same |
| `hydrographic_network.json` | `HY_HydrographicNetwork` metadata + `HY_DendriticCatchment` table | same |
| `geofabric_aggregated.gpkg` (+ `*_aggregated.json`) | Same profile for the aggregated basins, with containment links to `geofabric.gpkg` | `basinAggregation.py` |

### GeoPackage layers (spatial)

| Layer | HY_Features type(s) | Required | Notes |
|-------|---------------------|----------|-------|
| `catchment_area` | `HY_CatchmentArea` | yes | Basin polygons |
| `flowpath` | `HY_Flowpath` | yes | Stream reaches (TauDEM links) |
| `hydro_location` | `HY_HydroLocation` | yes | `nexusRealization` of every nexus, plus pour points not already on a nexus |
| `channel_network` | `HY_ChannelNetwork` | yes | One MultiLineString of all flowpaths realizing the `domain` catchment |
| `waterbody` | `HY_Lake`, `HY_Impoundment` | no | HydroLAKES polygons when available |
| `hydrometric_feature` | `HY_HydrometricFeature` | no | Gauges in basin |

### GeoPackage tables (non-spatial)

| Table | HY_Features element | Required |
|-------|---------------------|----------|
| `hydro_nexus` | `HY_HydroNexus` | yes |
| `catchment` | `HY_DendriticCatchment`, `HY_CatchmentAggregate` (holistic catchments) | yes |
| `catchment_realization` | `catchmentRealization` | yes |
| `nexus_contributing_catchment` | `contributingCatchment` / `receivingCatchment` | yes |
| `catchment_association` | outflow nexus, `networkWaterBody`, `positionOnRiver` | when non-empty |
| `catchment_containment` | `containingCatchment` / `containedCatchment` | when non-empty |
| `catchment_upper_catchment` | `upperCatchment` | when non-empty |
| `waterbody_upstream_waterbody` | `upstreamWaterBody` | when non-empty |

Every feature (spatial layers, `hydro_nexus`, `catchment`) carries:

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
| Outflow nexus | `outflow_nexus_id` | `nx_{lower_catchment_id}`; `nx_out_{catchment_id}` at a domain outlet |
| Inflow nexus | `inflow_nexus_id` | `nx_{catchment_id}` when the catchment has upstream catchments; empty for headwaters |
| Water body | `waterbody_id` | HydroLAKES `Hylak_id` / basin `lake_id` when `is_lake_catchment = 1` |
| Domain outlet | `lower_catchment_id = ""` | Downstream id `≤ 0` or preset outlet sentinel (`-9999` for MESH) |

**Important:** In this dendritic TauDEM network, each flowpath realizes one catchment, so `catchment_id` on the flowpath layer equals `flowpath_id`. Catchment **area** polygons use the same id (`catchment_id` = basin `DN`). This is distinct from `waterbody_id`, which identifies the open-water feature when the catchment is lake-dominated.

## Feature type mappings

### HY_DendriticCatchment (`catchment` table)

Stored in the `catchment` table (`feature_id` = `cat_{catchment_id}`), and repeated in `hydrographic_network.json` → `dendritic_catchment` and `catchment_registry.json` → `catchments`.

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| `code` | `catchment_id` | Same identifier used across realizations |
| `outflow` | `outflow_nexus_id` | Links to `HY_HydroNexus` at reach outlet |
| `inflow` | `inflow_nexus_id` | Nillable for headwaters |
| `lowerCatchment` | `lower_catchment_id` | Empty at domain outlet |
| `upperCatchment` | `upper_catchment_id` | Immediate upstream catchment(s); comma-separated confluences |
| Linked water body (profile extension) | `waterbody_id` | Set for lake-merged catchments |

Lake-dominated catchments link to a water body via `waterbody_id` and carry `waterbody_class` derived from the same HydroLAKES `Lake_type` as the polygon layer. The registry records the water body as an **association** (`networkWaterBody`), not as a catchment realization.

### HY_CatchmentAggregate (study domain)

The `catchment` row `domain` (`hyf_type` = `HY_CatchmentAggregate`) is the encompassing catchment of the dendritic network.

| HY_Features property / association | Implementation |
|-----------------------------------|----------------|
| `containedCatchment` | `catchment_containment` rows `domain` → every dendritic catchment |
| `outflow` | `outflow_nexus_id` = every terminal `nx_out_*` nexus (comma-separated) |
| `catchmentRealization` | `catchment_realization` rows → `HY_HydrographicNetwork` (`network_id`) and `HY_ChannelNetwork` (`channel_network`) |

### Aggregated basins (`geofabric_aggregated.gpkg`)

Each aggregated basin is an `HY_DendriticCatchment` with id `agg_{LINKNO}`, in a network `study_hydrographic_network_aggregated` whose domain is `agg_domain`. `catchment_containment` rows `agg_{LINKNO}` → fine `catchment_id` implement `containedCatchment`; the fine catchments are defined in `geofabric.gpkg`.

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

### HY_Flowpath (`flowpath` layer)

| HY_Features property / association | Implementation column | TauDEM / legacy source |
|-----------------------------------|----------------------|------------------------|
| Feature type | `hyf_type` = `HY_Flowpath` | added |
| Flowpath id | `flowpath_id` | `LINKNO` |
| `realizedCatchment` | `realizes_catchment` | equals `catchment_id` |
| Catchment code | `catchment_id` | `LINKNO` (1:1 link–catchment) |
| `lowerCatchment` | `lower_catchment_id` | `DSLINKNO`; blanked at outlet sentinel |
| `outflow` | `outflow_nexus_id` | derived |
| `inflow` | `inflow_nexus_id` | derived |
| `upperCatchment` | `upper_catchment_id` | derived |

### HY_HydroNexus (`hydro_nexus` table)

The nexus is topological (Section 7.3.2) and has no geometry; its positions are the `hydro_location` points that realize it.

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| Identifier | `nexus_id` | `nx_{receiving_catchment_id}`; `nx_out_{catchment_id}` for a domain outlet |
| Feature type | `hyf_type` = `HY_HydroNexus` | |
| `contributingCatchment` (0..*) | `nexus_contributing_catchment` table | One row per link; `contributing_catchment_id` on the nexus row is a comma-separated copy |
| `receivingCatchment` | `receiving_catchment_id` | Downstream catchment; empty at domain outlet |
| `nexusRealization` (0..*) | `hydro_location.realized_nexus_id` | At least one per nexus |

### HY_HydroLocation (`hydro_location` layer)

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| Feature type | `hyf_type` = `HY_HydroLocation` | |
| Identifier | `feature_id` | `hl_{n}` |
| `hydroLocationType` | `hydro_loc_type` | Annex B.1 vocabulary (see tables) |
| `realizedNexus` | `realized_nexus_id` | Set on network-derived locations; empty on pour points not near a nexus |
| Contributors at this point | `contributing_catchment_id` | Network-derived locations only |
| Linked water body | `waterbody_id` | Receiving lake for `river mouth`; pour-point `lake_id` otherwise |
| Name | `feature_name` | Pour-point `name` |

Network-derived locations sit at the topologic outlet of the contributing reaches (from `DSLINKNO` / upstream links; TauDEM may store the pour point at line start). Contributors that meet at the same point share one location; contributors that enter a lake catchment at different shore points each get their own realization of the same nexus.

| Network situation | `hydro_loc_type` |
|-------------------|------------------|
| Receiving catchment is lake-dominated | `river mouth` |
| Two or more contributors meet | `confluence` |
| Single contributor, or domain outlet | `catchment outlet` |

Pour points within 250 m of a network location are dropped as duplicates; the rest are typed by `point_type`:

| Pour-point `point_type` | `hydro_loc_type` (Annex B.1) |
|-------------------------|-------------------------------|
| `inflow` | `river mouth` (river discharging into the lake) |
| `outflow` | `catchment outlet` |
| `gauge` | `hydrometric station` |

### HY_HydrometricFeature (`hydrometric_feature` layer)

Implements `positionOnRiver` via **`HY_IndirectPosition`** (Section 7.3.3).

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| Feature type | `hyf_type` = `HY_HydrometricFeature` | |
| Station identifier | `station_code` | `STATION_NUMBER`, `STATION_NO` (shapefile), or `STATION_NU` |
| Name | `feature_name` | `STATION_NAME` / `STATION_NM` |
| `hydroLocationType` | `hydro_loc_type` | `hydrometric station` |
| Host catchment | `catchment_id` | Basin polygon containing the gauge (`DN`) |
| `positionOnRiver` → linear element | `linear_element_id` | Same as `catchment_id` (dendritic: equals `flowpath_id`) |
| `positionOnRiver` → reference nexus | `reference_nexus_id` | Outflow nexus of host catchment |
| `positionOnRiver` → distanceExpression | `distance_from_outlet_m`, `distance_from_outlet_pct` | Along host flowpath (`catchment_id`) from outlet |
| `positionOnRiver` → distanceDescription | `distance_description` | `upstream` (Annex B.2) of the reference nexus |
| Host reach | `host_flowpath_id` | Same as `catchment_id` (not nearest reach) |

**Export policy:** Gauges with no containing catchment fall back to the nearest flowpath within the search radius (default 5 km). Gauges that cannot be linked to a catchment flowpath are **omitted**
from `hydrometric_feature` so every exported row has a complete `positionOnRiver`.

### HY_WaterBody (`waterbody` layer)

HydroLAKES polygons use the same [`Lake_type` mapping](#hydrolakes-lake_type--hy_features) as lake catchments (`classify_waterbody` in `hy_features/schema.py`).

| HY_Features association | Implementation column | Notes |
|------------------------|----------------------|-------|
| Identifier | `waterbody_id` | `Hylak_id` |
| `upstreamWaterBody` (0..*) | `upstream_waterbody_id` | Nearest lake on every upstream branch (comma-separated), walking through non-lake catchments |
| `downstreamWaterBody` | `downstream_waterbody_id` | Walk downstream through non-lake catchments |

### HY_HydrographicNetwork (metadata)

JSON record at `hydrographic_network.json` → `hydrographic_network`:

| HY_Features property | JSON field |
|--------------------|------------|
| Network identifier | `network_id` (default `study_hydrographic_network`) |
| Feature type | `hyf_type` = `HY_HydrographicNetwork` |
| `realizedCatchment` | `realized_catchment` = `domain` |
| Domain outlets | `outlet_catchments` |
| Flowpath members | `flowpath_members` |
| Water-body members | `waterbody_members` |
| Channel network | `channel_network_id` |
| `HY_HydroNexus.contributingCatchment` links | `nexus_contributing_catchment` |

### HY_ChannelNetwork (`channel_network` layer)

| HY_Features property / association | Implementation column | Notes |
|-----------------------------------|----------------------|-------|
| Feature type | `hyf_type` = `HY_ChannelNetwork` | |
| Identifier | `channel_network_id` = `feature_id` | `{network_id}_channels` |
| `realizedCatchment` | `realizes_catchment` | `domain` |
| `drainagePattern` | `drainage_pattern` | `dendritic` (Annex B.3) |
| Member count | `flowpath_count` | |
| Shape | MultiLineString | All flowpath geometries |

## Catchment registry

`catchment_registry.json` separates **holistic catchment identity** from geometric realizations (OGC Section 7.2). The GeoPackage tables carry the same content.

- `catchments` — one entry per `catchment_id` (dendritic catchments and the `domain` aggregate) with nexus and neighbour links
- `realizations` — `catchmentRealization` rows: `HY_CatchmentArea` and `HY_Flowpath` per catchment; `HY_HydrographicNetwork` and `HY_ChannelNetwork` for `domain`
- `associations` — non-realization links: each catchment's outflow `HY_HydroNexus`, lake catchments' `HY_Lake` / `HY_Impoundment` (`networkWaterBody`), and `HY_HydrometricFeature` positions
- `containments` — `containingCatchment` → `containedCatchment` pairs

## Conformance check

```bash
python -m hy_features.validate outputs/working/geofabric.gpkg
python -m hy_features.validate outputs/working/geofabric_aggregated.gpkg --external outputs/working/geofabric.gpkg
```

The check also runs after every export and prints `PASS` / `FAIL` with each error and warning.

## Downstream model remapping

The canonical interchange product is `geofabric.gpkg`. It also keeps the TauDEM source columns (`DN`, `LINKNO`, `DSLINKNO`, …) alongside the canonical ones; regenerate a model-specific product with [`remap_fields.py`](../remap_fields.py):

```bash
python remap_fields.py --list-presets
python remap_fields.py --preset mesh \
  --basins outputs/working/geofabric.gpkg \
  --streams outputs/working/geofabric.gpkg \
  --drop-metadata \
  --out-dir remapped_products/
```

Presets live in [`code/hy_features/model_presets.json`](../code/hy_features/model_presets.json). The `mesh` preset maps:

| Canonical (GeoPackage) | MESH / WATFLOOD output |
|------------------------|------------------------|
| `catchment_id` | `DN` on `catchment_area`, `LINKNO` on `flowpath` |
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
| `hy_features/network.py` | Nexuses and their hydro locations, channel network, dendritic table, waterbody links, gauge positioning |
| `hy_features/tables.py` | Holistic catchment and link tables |
| `hy_features/assemble.py` | Full layer assembly and export |
| `hy_features/aggregate.py` | Aggregated-basin product with containment links |
| `hy_features/validate.py` | Automated conformance check |
| `hy_features/implementation_schema.json` | Machine-readable layer/column schema |
| `hy_features/stamp.py` | Profile metadata (`network_id`, `feature_id`, URIs) |
| `hy_features/field_remap.py` | Canonical → model-specific export |
