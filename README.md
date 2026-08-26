# Lake Delineation Tool

A hydrologic delineation workflow built around **TauDEM** for generating stream networks and watershed boundaries with **special handling for instream reservoirs (lakes)**.

Unlike a standard TauDEM workflow, this pipeline performs multiple delineation passes with custom Python preprocessing to ensure realistic flow paths through flat lake surfaces.

---

# Overview

Submit the workflow using:

```bash
sbatch tau-dem-delineation-srun.slurm
```

The workflow builds a stream network and watershed delineation for a selected DEM using three TauDEM passes with Python-based corrections between each pass.

---

# Software requirements

The pipeline uses a **self-compiled TauDEM MPI build**, the cluster **GDAL module** for `gdal_polygonize.py`, and a **Python virtual environment** for geoprocessing scripts. `tau-dem-delineation-srun.slurm` restores a saved module stack named `scimods` (GDAL only); recreate or adapt it on other HPC sites.

## TauDEM (self-built MPI)

Download and compile [TauDEM](https://github.com/dtarbot/TauDEM) with MPI enabled (not a cluster module). 

## HPC modules (GDAL)

Load before Python steps and before `gdal_polygonize.py` (or save as a module collection, e.g. `module save scimods`):

Example (load **GDAL 3.9.1** to match `GDAL==3.9.1` in `requirements.txt`; check `module spider gdal`):

```bash
module load StdEnv/2023
module load gdal/3.9.1
module save scimods
```

| Software | Purpose |
|----------|---------|
| **GDAL** | `gdal_polygonize.py` (watershed raster → polygon shapefile); must match `GDAL==3.9.1` in the venv |
| **Slurm** | `sbatch`, `srun` — TauDEM MPI passes and `rasterFlowpathEdit.py` |

TauDEM Pass 1–3 invoke MPI tools via `srun` with `#SBATCH --ntasks=250`. `rasterFlowpathEdit.py` uses a **separate** smaller `srun` launch (`FLOWPATH_NCORES`).

## Python virtual environment

Pinned package list: [`requirements.txt`](requirements.txt) 

### Key pinned versions (pipeline)

| Package | Version | Used by |
|---------|---------|---------|
| **GDAL** | 3.9.1 | `osgeo` raster I/O (`rasterFlowpathEdit.py`, `pourPointsPass2.py`) |
| **geopandas** | 1.0.1 | Vector scripts; shapefile / GeoPackage I/O |
| **pandas** | 2.2.3 | Attribute tables, registry JSON |
| **numpy** | 1.26.4 | Raster arrays, basin metrics |
| **scipy** | 1.15.2 | `ndimage` in `rasterFlowpathEdit.py`, `pourPointsPass2.py` |
| **shapely** | 2.0.7 | Geometry ops |
| **fiona** | 1.10.1 | Shapefile driver (geopandas) |
| **pyogrio** | 0.10.0 | GeoPackage / fast vector I/O (geopandas) |
| **pyproj** | 3.7.1 | CRS transforms (geopandas) |
| **mpi4py** | 4.0.0 | Parallel lakes in `rasterFlowpathEdit.py` |
| **pytest** | 8.3.4 | `tests/test_hy_features_topology.py` (optional) |

Stdlib only (no pip): `sqlite3` in `getGauges.py`, `hy_features/` JSON export.

HY_Features assembly (`hy_features/`) uses the geopandas stack above; no additional packages.

---

# Pipeline

```text
DEM (DEM.tif)
       │
       ▼
Pass 1 ─ TauDEM
Standard hydrologic conditioning and watershed delineation
(no pour points)

       │
       ▼
Python preprocessing

• filterLakes.py
    Filter HydroLAKES reservoirs to the study basin

• getGauges.py
    Find stream gauges inside the basin

• rasterFlowpathEdit.py
    Correct flow directions through reservoirs
    Outputs:
        fdr_centerline_all.tif

       │
       ▼
Pass 2 ─ TauDEM
Re-run delineation using corrected flow directions

       │
       ▼
pourPointsPass2.py

Generate refined pour points at lake inflow/outflow locations

       │
       ▼
Pass 3 ─ TauDEM
Final watershed delineation snapped to refined pour points

       │
       ▼
Post-processing

• combiningBasins.py
    Merge reservoir-adjacent subbasins

• cleanGeofabric.py
    Remove phantom stream links
    Attach stream gauges

• basinAggregation.py (optional)
    Merge small headwater subbasins

       │
       ▼
Final Products

```

---

# Project Directory Structure

All paths below are relative to `HOME_DIR`.

```text
HOME_DIR/
│
├── dem/
│   └── Input DEM
│
├── taudem-interim-files/
│   ├── d8/
│   │   ├── TauDEM rasters
│   │   ├── Intermediate shapefiles
│   │   └── fdr_centerline_all.tif
│   │
│   └── final/
│       └── Pass 3 TauDEM outputs
│
├── delineation-product/
│   ├── Final streams
│   ├── Watersheds
│   └── Outlets
│
├── points/
│   ├── Gauges
│   ├── Pour points
│   └── Reservoir IO nodes
│
├── lakes/
│   └── Filtered HydroLAKES polygons
│
├── merged_basins/
│   ├── Reservoir merged basins
│   ├── Cleaned geofabric
│   
└──final_basins/
    └── Optional aggregated basins

```

---

# Workflow

## Pass 1 – Initial TauDEM Delineation

Runs a standard TauDEM workflow:

- Fill depressions
- Compute flow directions
- Flow accumulation
- Stream extraction
- Watershed delineation

No pour points are used during this stage.

Outputs define the preliminary watershed network used by subsequent scripts.

---

## Reservoir Processing

### `filterLakes.py`

Filters HydroLAKES polygons to include only reservoirs intersecting the study basin.

Produces:

```text
lakes/
└── filtered_lakes.shp
```

---

### `getGauges.py`

Queries the HYDAT database to identify stream gauges located inside the basin.

Produces:

```text
points/
└── gauges_in_basin.shp
```

---

### `rasterFlowpathEdit.py`

Corrects TauDEM D8 flow directions across flat lake surfaces.

Uses:

- DEM flow directions
- Flow accumulation
- Stream raster
- HydroLAKES polygons
- Stream gauges

Produces:

```text
taudem-interim-files/d8/
└── fdr_centerline_all.tif

points/
└── selected_outlets.shp
```

---

## Pass 2 – Corrected Delineation

TauDEM is rerun using the corrected lake flow-direction raster.

This produces a more realistic stream network through reservoirs.

---

## `pourPointsPass2.py`

Computes refined pour points located at reservoir inflows and outflows.

Produces:

```text
points/
├── pourPointsFinal.shp
└── reservoir_io_nodes.shp
```

---

## Pass 3 – Final Delineation

TauDEM performs a final watershed delineation using the refined pour points.

Outputs are copied into:

```text
delineation-product/
```

---

## Post-processing

### `combiningBasins.py`

Merges subbasins surrounding reservoirs into unified watershed units.

Inputs include:

- Basins
- Streams
- Reservoir polygons
- Gauges
- Outlet overrides

Outputs:

```text
merged_basins/
```

---

### `cleanGeofabric.py`

Cleans the river network by:

- Removing phantom stream links
- Attaching stream gauges
- Producing a clean geofabric

Outputs:

```text
merged_basins/
├── reservoirBasins_final.shp
└── reservoirStreams_final.shp
```

---

### `basinAggregation.py` *(Optional)*

Aggregates small upstream subbasins into larger watershed units. The merge threshold **`MIN_SUB_AREA`** (default 100 km²) is applied to **local** subbasin area — the size of each catchment polygon alone — not cumulative upstream drainage.

| Setting | Default | Role |
|---------|---------|------|
| **`UNIT_AREA`** | `None` | Column name for local area; `None` uses polygon geometry |
| **`UP_AREA`** | `DSContArea` | TauDEM cumulative area at pour point (outlet masking) |
| **`MIN_SUB_AREA`** | 100 km² | Subbasins with local area below this merge downstream |

Outputs aggregated basin and river shapefiles.

---


### `basinTrimming.ipynb`

Uses the final delineation and trims it to the watershed of interest.

The notebook is used to post-process the full DEM-scale delineation by identifying the desired stream network and clipping all associated datasets to the selected basin.

---

# Configuration Checklist

Before adapting the workflow to another watershed, verify the following settings.

---

## `tau-dem-delineation-srun.slurm`

Update:

- `TAUDEM_BIN` — directory containing compiled TauDEM MPI binaries
- `HOME_DIR`
- `DEM`
- `VENV` — path to activated venv (`pip install -r requirements.txt`; see **Software requirements**)
- `STREAM_THRESHOLD`
- `FLOWPATH_NCORES`

Ensure `module restore scimods` loads **GDAL 3.9.1** and that `TAUDEM_BIN` is on `PATH` (see **Software requirements**).

Also verify:

- `#SBATCH --account`
- `#SBATCH --ntasks`
- `#SBATCH --mem-per-cpu`
- `#SBATCH --time`

---

## `filterLakes.py`

Update:

- HydroLAKES shapefile path
- Pass 1 watershed path
- Stream path
- `MIN_AREA`

Output:

```text
lakes/filtered_lakes.shp
```

---

## `getGauges.py`

Update:

- `Hydat.sqlite3`
- Watershed path

Output:

```text
points/gauges_in_basin.shp
```

---

## `rasterFlowpathEdit.py`

Verify:

- D8 flow-direction raster
- Flow accumulation raster
- Source raster
- Watershed raster
- Stream shapefile
- Filtered lakes
- Gauges
- `outlet_overrides.csv`

Outputs:

```text
taudem-interim-files/d8/fdr_centerline_all.tif

points/selected_outlets.shp
```

---

## `pourPointsPass2.py`

Update all path definitions for:

- Streams
- Watersheds
- Corrected flow directions
- Lakes
- Gauges

Outputs:

```text
points/pourPointsFinal.shp
points/reservoir_io_nodes.shp
```

---

## `combiningBasins.py`

Verify:

- `PATHS`
- `OVERRIDES_CSV`
- `OUTPUT_DIR`
- `GAUGE_SEARCH_RADIUS`
- `MIN_INTERNAL_STREAM_LEN`

---

## `cleanGeofabric.py`

Update:

- Input merged basins
- Input streams
- Gauge layer

Outputs:

```text
merged_basins/
├── reservoirBasins_final.shp
└── reservoirStreams_final.shp
```

---

## `basinAggregation.py`

Update:

- Input basin layer
- Input river layer
- Output filenames

Review:

- `UNIT_AREA` — local subbasin area column (`None` = from polygon; see script docstring)
- `UP_AREA` — cumulative drainage column (default `DSContArea`)
- `MIN_SUB_AREA` — merge threshold in km² applied to **local** area
- `MIN_RIV_SLOPE`
- `MIN_RIV_LENGTH`

Also ensure attribute names match your TauDEM outputs.

---


# Required External Data

Before running the workflow, stage the following datasets:

| Dataset | Purpose |
|----------|---------|
| DEM (`.tif`) | Elevation model |
| HydroLAKES polygons | Reservoir delineation |
| HYDAT (`Hydat.sqlite3`) | Stream gauge database |

---

# Outputs

## `delineation-product/`

Contains the final watershed products:

- Watersheds
- Stream network
- Snapped outlets

---

## `merged_basins/`

Contains the cleaned geofabric:

- Reservoir-merged basins
- Cleaned stream network
- **`geofabric.gpkg`** — full HY_Features GeoPackage (`catchment_area`, `flowpath`, `hydro_nexus`, `waterbody`, `hydrometric_feature`, `hydro_location`)
- **`catchment_registry.json`** — catchment identity ↔ realization links
- **`hydrographic_network.json`** — dendritic catchment table + network metadata
- Optional aggregated watershed products

---

# OGC HY_Features alignment (in development / work in progress)

HY_Features enrichment is **off by default**. Enable it for `geofabric.gpkg`, HY columns, and JSON sidecars:

```bash
export HY_FEATURES_ENABLED=1   # or set ENABLE_HY_FEATURES = True in a script
```

When enabled, outputs implement the [OGC HY_Features conceptual model (14-111r6)](https://docs.ogc.org/is/14-111r6/14-111r6.html) as an **implementation schema** under profile **`LakeDelineationTool-DendriticGeofabric-1.0`**.

See [`docs/hy_features_mapping.md`](docs/hy_features_mapping.md) and [`docs/hy_features_implementation_conventions.md`](docs/hy_features_implementation_conventions.md).

## Downstream model remapping

The canonical product is `geofabric.gpkg`. To export shapefiles for a specific routing model, use [`remap_fields.py`](remap_fields.py):

```bash
python remap_fields.py --list-presets
python remap_fields.py --list-mappings --preset mesh

python remap_fields.py \
  --preset mesh \
  --basins merged_basins/geofabric.gpkg \
  --streams merged_basins/geofabric.gpkg \
  --drop-metadata \
  --out-dir remapped_products/
```

Writes `remapped_products/basins_mesh.shp` and `streams_mesh.shp` (integer IDs; outlet sentinel from preset).

### Other models

Add a preset to [`hy_features/model_presets.json`](hy_features/model_presets.json) or use `--override`:

```bash
python remap_fields.py \
  --preset my_model \
  --preset-file my_model.json \
  --streams merged_basins/geofabric.gpkg \
  --streams-layer flowpath \
  --override lower_catchment_id=DOWN_ID \
  --output remapped_products/streams.shp \
  --drop-metadata
```

---
