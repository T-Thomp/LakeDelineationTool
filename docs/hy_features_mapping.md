# HY_Features Mapping — Lake Delineation Tool

Conformance target: **implementation schema** per OGC WaterML 2 Part 3 — Surface Hydrology Features (14-111r6), Annex A.2.

Reference standard: [OGC 14-111r6](https://docs.ogc.org/is/14-111r6/14-111r6.html)

## Conformance class

This workflow implements the `/req/hy_features_conceptual_model/mapping` requirements class with an explicit **implementation schema** profile covering:

- `HY_DendriticCatchment` (attribute table + registry)
- `HY_CatchmentArea`, `HY_FlowPath`, `HY_HydroNexus`
- `HY_HydrographicNetwork` (metadata record)
- `HY_WaterBody` / `HY_Lake` / `HY_Impoundment` / `HY_Reservoir`
- `HY_HydroLocation`, `HY_HydrometricFeature`
- `HY_IndirectPosition` (river referencing on gauges)

Validation report: `merged_basins/hy_features_validation.json` (produced by `cleanGeofabric.py`).

## Output products

| File | Contents |
|------|----------|
| `merged_basins/geofabric.gpkg` | All spatial layers (see below) |
| `merged_basins/catchment_registry.json` | Catchment identity ↔ realizations |
| `merged_basins/hydrographic_network.json` | Network metadata + dendritic catchment table |
| `merged_basins/hy_features_validation.json` | Conformance inspection report |

### GeoPackage layers

| Layer | HY_Features type |
|-------|------------------|
| `catchment_area` | `HY_CatchmentArea` |
| `flowpath` | `HY_FlowPath` |
| `hydro_nexus` | `HY_HydroNexus` (+ pour-point `HY_HydroLocation`) |
| `waterbody` | `HY_Lake` / `HY_Impoundment` |
| `hydrometric_feature` | `HY_HydrometricFeature` |
| `hydro_location` | `HY_HydroLocation` |

## HY_DendriticCatchment

| Property | Column | Notes |
|----------|--------|-------|
| `code` | `catchment_id` | Same as pour-point link / `DN` |
| `outflow` | `outflow_nexus_id` | `nx_out_{catchment_id}` |
| `inflow` | `inflow_nexus_id` | Upstream outflow nexus; nillable for headwaters |
| `lowerCatchment` | `lower_catchment_id` | Empty at domain outlet |
| `upperCatchment` | `upper_catchment_id` | Immediate upstream catchment(s) |
| Drainage | `drainage_pattern` | `dendritic` |

Full table in `hydrographic_network.json` → `dendritic_catchment`.

## HY_HydroNexus

| Property | Column |
|----------|--------|
| Identifier | `nexus_id` |
| `contributingCatchment` | `contributing_catchment_id` |
| `receivingCatchment` | `receiving_catchment_id` (nillable at outlet) |
| Geometry | downstream endpoint of flowpath |

## HY_IndirectPosition (hydrometric)

| Property | Column |
|----------|--------|
| Linear element | `linear_element_id` (= host flowpath) |
| Reference location | `reference_nexus_id` (= outflow nexus of host reach) |
| Distance expression | `distance_from_outlet_m`, `distance_from_outlet_pct` |
| Host reach | `host_flowpath_id` |

## HY_WaterBody network

| Property | Column |
|----------|--------|
| `upstreamWaterBody` | `upstream_waterbody_id` |
| `downstreamWaterBody` | `downstream_waterbody_id` |

Linked for consecutive reservoir catchments on the same flow network.

## Catchment area / flowpath (summary)

See prior mapping tables; all features include `hyf_type` and `hyf_type_uri` (OGC Definitions Server).

Legacy model columns (`DN`, `LINKNO`, `DSLINKNO`) can be produced via [`remap_fields.py`](../remap_fields.py) using the `mesh` preset (or any custom preset in [`model_presets.json`](../hy_features/model_presets.json)).

## Downstream shapefile remapping

[`remap_fields.py`](../remap_fields.py) converts canonical `geofabric.gpkg` layers to model-specific shapefiles:

```bash
python remap_fields.py --list-presets
python remap_fields.py --preset mesh --basins merged_basins/geofabric.gpkg \
  --streams merged_basins/geofabric.gpkg --drop-metadata --out-dir remapped_products/
```

## Assembly entry point

Full conformance assembly is performed by:

```python
from hy_features.assemble import assemble_full_geofabric, export_full_geofabric
```

Called automatically from `cleanGeofabric.py` and `combiningBasins.py`.
