# Lake Delineation Tool

A hydrologic delineation workflow built around **TauDEM** for generating stream networks and watershed boundaries with **special handling for instream reservoirs (lakes)**.

Unlike a standard TauDEM workflow, this pipeline performs multiple delineation passes with custom Python preprocessing to ensure realistic flow paths through flat lake surfaces.

---

# Overview

Submit the workflow using:

```bash
sbatch Delineation-Workflow.slurm
```

The workflow builds a stream network and watershed delineation for a selected DEM using three TauDEM passes with Python-based corrections between each pass.

---

# Software requirements

The pipeline uses a **self-compiled TauDEM MPI build**, a **Python virtual environment** ([`requirements.txt`](requirements.txt)), and **MPI + GDAL** for raster work. Setup below is **tested on Alliance FIR**; other clusters, workstations, or conda environments may need different module names, GDAL linkage, or a `pip install mpi4py` instead of a cluster module.

## TauDEM (self-built MPI)

Download and compile [TauDEM](https://github.com/dtarb/taudem) with MPI enabled (not a cluster module).

In `Delineation-Workflow.slurm`, point `PATH` at your build:

```bash
export PATH="$HOME/taudem-build/taudem:$PATH"   # edit: your compiled TauDEM install
```

## Alliance FIR (tested setup)

On FIR, load HPC modules **before** activating the venv. **`mpi4py` is not in `requirements.txt`** — use the cluster module only ([Alliance mpi4py docs](https://docs.alliancecan.ca/wiki/MPI4py)). Do not `pip install mpi4py` on FIR.

```bash
module load StdEnv/2023
module load gdal/3.9.1
module load mpi4py/4.0.0
module save scimods    # optional; restored by Delineation-Workflow.slurm
```

| Software | Purpose (FIR) |
|----------|----------------|
| **GDAL 3.9.1** | `gdal_polygonize.py`; pip `GDAL==3.9.1` must match the loaded module |
| **mpi4py 4.0.0** | `rasterFlowpathEdit.py` MPI — module load only |
| **Slurm** | `sbatch`, `srun` — TauDEM MPI passes and `rasterFlowpathEdit.py` |

TauDEM Pass 1–3 invoke MPI tools via `srun` with `#SBATCH --ntasks=250`. `rasterFlowpathEdit.py` uses a **separate** smaller `srun` launch (`FLOWPATH_NCORES`).

### Python venv (FIR)

```bash
module load StdEnv/2023 gdal/3.9.1 mpi4py/4.0.0

python -m venv ~/virtual-envs/scienv
source ~/virtual-envs/scienv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

If `import mpi4py` fails, deactivate, load `mpi4py/4.0.0`, and re-activate the venv.

Day-to-day: `module restore scimods` then `source ~/virtual-envs/scienv/bin/activate`.

## Other HPC sites or local setups

Module names and versions differ by cluster (`module spider gdal`, `module spider mpi4py`). On systems **without** an Alliance-style mpi4py module, install MPI Python bindings yourself, for example:

```bash
pip install mpi4py==4.0.0   # after loading your site MPI compiler/module stack
```

Similarly, load or install a **GDAL build that matches** `GDAL==3.9.1` in `requirements.txt` before `pip install GDAL`, or adjust the pin to your system GDAL. Conda/mamba users may prefer `conda-forge` for `gdal`, `geopandas`, and `mpi4py` instead of the venv + module workflow above.

Adapt `module restore scimods` in `Delineation-Workflow.slurm` to your site’s module loads, or replace with explicit `module load` lines.

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
| **pytest** | 8.3.4 | `tests/test_hy_features_topology.py` (optional) |

**mpi4py 4.0.0** — required by `rasterFlowpathEdit.py`. On **FIR**: `module load mpi4py/4.0.0` (not pip). Elsewhere: `pip install mpi4py` or your site’s equivalent.

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
        fdr_lakes.tif

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

Place `Delineation-Workflow.slurm` at your study root. Python scripts live in `code/`; data paths resolve against the slurm script's directory (`LAKE_DELINEATION_ROOT`).

```text
study-root/                          ← where you run sbatch
│
├── Delineation-Workflow.slurm
├── study_settings.py                ← your paths (copy from study_settings.example.py)
├── study_settings.example.py
├── outlet_overrides.csv             optional
│
├── code/                            ← pipeline scripts (do not edit for new studies)
│   ├── pipeline_paths.py
│   └── …
│
├── dem/
│   └── Input DEM
│
└── outputs/
    ├── interim/
    │   ├── taudem_d8/       Pass 1–2 TauDEM rasters and vectors
    │   └── taudem_pass3/    Pass 3 TauDEM outputs
    ├── prep/                Lakes, gauges, outlet selection, IO nodes
    ├── working/             Merged geofabric (+ HY sidecars when enabled)
    └── final/               Deliverables: basins, basins_aggregated, pour_points
```

See `study_settings.py` for inputs and `code/pipeline_paths.py` for output layout constants.

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
outputs/prep/
└── lakes.shp
```

---

### `getGauges.py`

Queries the HYDAT database to identify stream gauges located inside the basin.

Produces:

```text
outputs/prep/
└── gauges.shp
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
outputs/interim/taudem_d8/
└── fdr_lakes.tif

outputs/prep/
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
outputs/final/
└── pour_points.shp

outputs/prep/
└── reservoir_io_nodes.shp
```

---

## Pass 3 – Final Delineation

TauDEM performs a final watershed delineation using the refined pour points.

Pass 3 vectors are written once under `outputs/interim/taudem_pass3/` (no duplicate copies).

---

## Post-processing

### `combiningBasins.py`

Merges subbasins surrounding reservoirs into unified watershed units.

Inputs include Pass 3 basins/streams, lakes, gauges, and snapped outlets.

Outputs:

```text
outputs/working/
├── basins_merged.shp
└── streams_merged.shp
```

---

### `cleanGeofabric.py`

Cleans the river network by:

- Removing phantom stream links
- Attaching stream gauges
- Producing a clean geofabric

Outputs:

```text
outputs/final/
├── basins.shp
└── streams.shp
```

---

### `basinAggregation.py` *(Optional)*

Aggregates small upstream subbasins into larger watershed units. The merge threshold **`MIN_SUB_AREA`** (default 100 km²) is applied to **local** subbasin area — the size of each catchment polygon alone — not cumulative upstream drainage.

| Setting | Default | Role |
|---------|---------|------|
| **`UNIT_AREA`** | `None` | Column name for local area; `None` uses polygon geometry |
| **`UP_AREA`** | `DSContArea` | TauDEM cumulative area at pour point (outlet masking) |
| **`MIN_SUB_AREA`** | 100 km² | Subbasins with local area below this merge downstream |

Outputs:

```text
outputs/final/
├── basins_aggregated.shp
└── streams_aggregated.shp
```

---


### `basinTrimming.ipynb`

Uses the final delineation and trims it to the watershed of interest.

The notebook is used to post-process the full DEM-scale delineation by identifying the desired stream network and clipping all associated datasets to the selected basin.

---

# Configuration Checklist

Before adapting the workflow to another watershed, verify the following settings.

---

## `study_settings.py` (one file to edit per study)

```bash
cp study_settings.example.py study_settings.py
```

Set these three paths (relative to study root or absolute):

- `INPUT_DEM` — elevation GeoTIFF
- `INPUT_HYDAT_DB` — HYDAT SQLite database
- `INPUT_HYDROLAKES` — HydroLAKES polygon shapefile

Example for data in another project folder:

```python
from pathlib import Path

INPUT_DEM = Path("/project/6102189/m58song/ABLakeDelineation/dem/AB2_mrdem-30-dtm.tif")
INPUT_HYDAT_DB = Path("/project/6102189/m58song/ABLakeDelineation/Hydat.sqlite3")
INPUT_HYDROLAKES = Path("/project/6102189/m58song/ABLakeDelineation/hydrolake/HydroLAKES_polys_v10.shp")
```

Test before submitting:

```bash
export LAKE_DELINEATION_ROOT="$PWD"
python3 code/validate_study.py
```

All pipeline scripts read these via `code/pipeline_paths.py` automatically.

---

## `Delineation-Workflow.slurm`

Update:

- `export PATH=...` — directory containing compiled TauDEM MPI binaries (see **Software requirements**)
- `VENV` — path to activated venv (`pip install -r requirements.txt`; see **Software requirements**)
- `STREAM_THRESHOLD`
- `FLOWPATH_NCORES`

`DATA_DIR` and `CODE_DIR` are set from `SLURM_SUBMIT_DIR` (the folder where you run `sbatch`), not from Slurm’s internal job spool copy. Submit from your study root:

```bash
cd /path/to/your/study-root
sbatch Delineation-Workflow.slurm
```

Ensure `module restore scimods` matches your cluster setup (FIR: **GDAL 3.9.1** + **mpi4py 4.0.0** modules) and that compiled TauDEM is on `PATH`.

Also verify:

- `#SBATCH --account`
- `#SBATCH --ntasks`
- `#SBATCH --mem-per-cpu`
- `#SBATCH --time`

---

## `filterLakes.py`

Update:

- `MIN_AREA` (in script; lake-size filter in km²)

Input/output paths come from `pipeline_paths.py`.

Output:

```text
outputs/prep/lakes.shp
```

---

## `getGauges.py`

Input/output paths come from `pipeline_paths.py`.

Output:

```text
outputs/prep/gauges.shp
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
outputs/interim/taudem_d8/fdr_lakes.tif
outputs/prep/selected_outlets.shp
```

---

## `pourPointsPass2.py`

Paths are defined in `pipeline_paths.py` (used by default in `__main__`).

Outputs:

```text
outputs/final/pour_points.shp
outputs/prep/reservoir_io_nodes.shp
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
outputs/final/
├── basins.shp
└── streams.shp
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

## `outputs/final/` (deliverables)

| File | Description |
|------|-------------|
| `basins.shp` | Clean, lake-merged catchments (non-aggregated) |
| `streams.shp` | Paired stream network |
| `basins_aggregated.shp` | Optional aggregated catchments (`basinAggregation.py`) |
| `streams_aggregated.shp` | Optional aggregated streams |
| `pour_points.shp` | Refined pour points for Pass 3 |

## `outputs/interim/`

TauDEM rasters and pass-specific vectors (single copy — not duplicated elsewhere).

## `outputs/working/`

Merged geofabric before final clean, plus HY sidecars when enabled:

- **`geofabric.gpkg`** — full HY_Features GeoPackage
- **`catchment_registry.json`** — catchment identity ↔ realization links
- **`hydrographic_network.json`** — dendritic catchment table + network metadata

## `outputs/prep/`

Intermediate prep layers: lakes, gauges, selected outlets, reservoir IO nodes.

---

# OGC HY_Features alignment (in development / work in progress)

HY_Features enrichment is **off by default**. Enable it for `geofabric.gpkg`, HY columns, and JSON sidecars:

```bash
export HY_FEATURES_ENABLED=1   # or set ENABLE_HY_FEATURES = True in a script
```

When enabled, outputs implement the [OGC HY_Features conceptual model (14-111r6)](https://docs.ogc.org/is/14-111r6/14-111r6.html) as an **implementation schema** under profile **`LakeDelineationTool-DendriticGeofabric-1.0`**.

See [`docs/hy_features_mapping.md`](docs/hy_features_mapping.md) and [`docs/hy_features_implementation_conventions.md`](docs/hy_features_implementation_conventions.md).

## Downstream model remapping

The canonical product is `geofabric.gpkg`. To export shapefiles for a specific routing model, use [`code/remap_fields.py`](code/remap_fields.py) from your study root:

```bash
python code/remap_fields.py --list-presets
python code/remap_fields.py --list-mappings --preset mesh

python code/remap_fields.py \
  --preset mesh \
  --basins outputs/working/geofabric.gpkg \
  --streams outputs/working/geofabric.gpkg \
  --drop-metadata \
  --out-dir remapped_products/
```

Writes `remapped_products/basins_mesh.shp` and `streams_mesh.shp` (integer IDs; outlet sentinel from preset).

### Other models

Add a preset to [`code/hy_features/model_presets.json`](code/hy_features/model_presets.json) or use `--override`:

```bash
python code/remap_fields.py \
  --preset my_model \
  --preset-file my_model.json \
  --streams outputs/working/geofabric.gpkg \
  --streams-layer flowpath \
  --override lower_catchment_id=DOWN_ID \
  --output remapped_products/streams.shp \
  --drop-metadata
```

---
