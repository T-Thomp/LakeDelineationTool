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
| **mpi4py 4.0.0** | `conditionLakes.py` only when `--ncores` > 1 — module load only |
| **Slurm** | `sbatch`, `srun` — TauDEM MPI passes. `conditionLakes.py` parallel uses `mpirun` |

TauDEM Pass 1–3 invoke MPI tools via `srun` with `#SBATCH --ntasks=250`. `conditionLakes.py` is separate: `--ncores 1` is a normal Python script (no mpi4py). Parallel lake processing uses `mpirun -np $FLOWPATH_NCORES`, not `srun`.

### Python venv (FIR)

```bash
module load StdEnv/2023 gdal/3.9.1 mpi4py/4.0.0

python -m venv ~/virtual-envs/scienv
source ~/virtual-envs/scienv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```


Day-to-day: `module restore scimods` then `source ~/virtual-envs/scienv/bin/activate`.

## Other HPC sites or local setups

Module names and versions differ by cluster (`module spider gdal`, `module spider mpi4py`). Serial `conditionLakes.py` (`--ncores 1`) does not need mpi4py. For a parallel run on a system **without** an Alliance-style mpi4py module, install MPI Python bindings yourself, for example:

```bash
pip install mpi4py==4.0.0   # after loading your site MPI compiler/module stack
```

Similarly, load or install a **GDAL build that matches** `GDAL==3.9.1` in `requirements.txt` before `pip install GDAL`, or adjust the pin to your system GDAL. Conda/mamba users may prefer `conda-forge` for `gdal`, `geopandas`, and `mpi4py` instead of the venv + module workflow above.

Adapt `module restore scimods` in `Delineation-Workflow.slurm` to your site’s module loads, or replace with explicit `module load` lines.

### Key pinned versions (pipeline)

| Package | Version | Used by |
|---------|---------|---------|
| **GDAL** | 3.9.1 | `osgeo` raster I/O (`conditionLakes.py`, `pourPointsPass2.py`) |
| **geopandas** | 1.0.1 | Vector scripts; shapefile / GeoPackage I/O |
| **pandas** | 2.2.3 | Attribute tables, registry JSON |
| **numpy** | 1.26.4 | Raster arrays, basin metrics |
| **scipy** | 1.15.2 | `ndimage` in `conditionLakes.py`, `pourPointsPass2.py` |
| **shapely** | 2.0.7 | Geometry ops |
| **fiona** | 1.10.1 | Shapefile driver (geopandas) |
| **pyogrio** | 0.10.0 | GeoPackage / fast vector I/O (geopandas) |
| **pyproj** | 3.7.1 | CRS transforms (geopandas) |
| **pytest** | 8.3.4 | `tests/test_hy_features_topology.py` (optional) |

**mpi4py 4.0.0** — required only for parallel `conditionLakes.py` (`--ncores` > 1). On **FIR**: `module load mpi4py/4.0.0` (not pip). Elsewhere: `pip install mpi4py` or your site’s equivalent. A one-core run does not import it.

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

• conditionLakes.py
    Correct flow directions through reservoirs
    Outputs:
        fdr_lakes.tif

• conditionStreams.py (optional)
    Force flow along user-defined valley paths
    Each path can set its own valley_weight and end_weight
    Edits fdr_lakes.tif in place

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

• reproject.py (optional)
    Reproject aggregated basins and streams

       │
       ▼
Final Products

```

---

# Project Directory Structure

Place `Delineation-Workflow.slurm` at your study root, or keep it with the repo and set **`LAKE_DELINEATION_ROOT`** to the study folder (see below). Python scripts live in `code/`. Data and output paths — including TauDEM rasters — resolve against that study root.

```text
study-root/                          ← where you run sbatch
│
├── Delineation-Workflow.slurm
├── study_settings.py                ← your paths (copy from study_settings.example.py)
├── study_settings.example.py
├── outlet_overrides.csv             optional
├── stream_conditioning.csv          optional
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

### `conditionLakes.py`

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
```

Options:

- `--ncores N` — worker count (default: `FLOWPATH_NCORES`, or 1). `1` is a normal Python script. `N>1` is capped at the `mpirun -np` process count and at the number of lakes being edited.
- `--option full|override` — `full` (default) rebuilds `fdr_lakes.tif` for every lake. `override` re-runs only the lakes listed in `--csv` and pastes them into the existing `fdr_lakes.tif`, first resetting each re-run lake (plus a small ring for old breakout cells) to the original flow directions.
- `--csv PATH` — outlet override CSV (`lake_id,lat,lon`; default `./outlet_overrides.csv`).

Serial, on a workstation or a compute node:

```bash
python3 code/conditionLakes.py --option override --csv outlet_overrides.csv --ncores 1
```

Parallel. This Open MPI build has no Slurm PMI support, so start it with `mpirun`:

```bash
mpirun -np 4 python3 code/conditionLakes.py --option override --csv my_fixes.csv --ncores 4
```

Re-run TauDEM Pass 2 / Pass 3 after an override-only run.

### `conditionStreams.py`

Fixes streams that TauDEM routes the wrong way (road fills, dams, DEM artifacts). Runs right after `conditionLakes.py` and edits `fdr_lakes.tif` in place.

Each row of `stream_conditioning.csv` is one path, from a start point (upstream) to an end point (downstream). `valley_weight` and `end_weight` are optional, and they are chosen per path. Leave a cell blank, or omit the columns, to keep the defaults (`valley_weight` 1.5, `end_weight` 0.1). A filled cell replaces that default for that row only.

```csv
id,start_lat,start_lon,end_lat,end_lon,valley_weight,end_weight
bow_fix,51.1784,-115.5708,51.1650,-115.5402,,
creek_2,50.9021,-114.8810,50.8893,-114.8467,4,0
lower,50.8893,-114.8467,50.8800,-114.8300,,
```

`valley_weight` is how strongly the path avoids high ground. Raise it when the path still climbs out of the valley; lower it when the path is too tightly pinned to the lowest cells. `end_weight` is the pull toward the end point, which keeps the path from wandering on flat ground. `0` turns that pull off. Non-numeric values are rejected.

Rows chain when one start lands in the same grid cell as another row's end. In the example, `lower` starts where `creek_2` ends, so they are one path. Intermediate ends are not sent looking for a downhill exit, and that continuing start is not snapped onto a stream. Only the last link (`lower`) finds a downhill way off its end.

For each row the script:

1. Reprojects both points to the flow-direction grid. Rows with a point outside the raster are skipped. A start that is not continuing another link is snapped onto a Pass 1 stream when one is within 10 cells.
2. Takes the rectangle around the two points plus a buffer (`BUFFER_CELLS`, default 50).
3. Builds a cost surface from the raw DEM: relative elevation in the window (0 on the valley floor, 1 at the highest cell), an uphill penalty, and a small pull toward the end point. The row's `valley_weight` and `end_weight` replace the defaults when the cells are filled in.
4. Finds the lowest-cost 8-direction path from start to end, so the path follows the valley the way water would.
5. Points each path cell's flow direction at the next cell, and points the cells on both sides into the path. For the last link in a chain (or a link that does not meet another), if the end cell's flow would run back onto the edits or stop, a mostly-downhill route (up to `END_MAX_EXIT_CELLS` cells) is found from the end to the nearest cell that drains away, and those cells are pointed along it. Lake cells are never changed.

A warning is printed if flow leaving that final end point runs back onto the path (move the end point further downstream). If the CSV does not exist, the step is skipped.

```bash
python3 conditionStreams.py --csv stream_conditioning.csv
```

Re-run it after any full `conditionLakes.py` run, which rebuilds `fdr_lakes.tif`.

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
| **`area_km2`** | from `basins.shp`, else polygon area × 10⁻⁶ | Local subbasin area (km²) used for the merge threshold |
| **`UP_AREA`** (`DSContArea`) | TauDEM column | Cumulative area at the pour point (m²); recomputed after merges |
| **`MIN_SUB_AREA`** | 100 km² | Subbasins with local area below this merge downstream |

Lakes are never merged. `frac_lake` and `lake_area` are copied from each surviving basin; they are not recomputed.

Outputs:

```text
outputs/final/
├── basins_aggregated.shp
└── streams_aggregated.shp
```

---

### `reproject.py` *(Optional)*

Standalone post-step. Not run by the slurm job. Reprojects the aggregated basins and streams to a CRS you pass with `--prj`.

Defaults: `outputs/final/basins_aggregated.shp` and `streams_aggregated.shp`. Writes copies next to those files (`basins_aggregated_wgs84.shp`, and so on) unless you set `--out-basins` / `--out-streams`.

```bash
python3 code/reproject.py --prj WGS84
python3 code/reproject.py --prj EPSG:3978
python3 code/reproject.py --prj EPSG4326 \
  --basins outputs/final/basins.shp \
  --streams outputs/final/streams.shp \
  --out-basins remapped/basins_wgs84.shp \
  --out-streams remapped/streams_wgs84.shp
```

`--prj` accepts `WGS84`, `EPSG:4326`, `EPSG4326`, or a PROJ string. Area columns (`area_km2`, `lake_area`) are left as written; they were computed in the source CRS.

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

## `LAKE_DELINEATION_ROOT`

This environment variable is the **study root**: the folder that holds `study_settings.py` and `outputs/`. Every Python script in `code/` resolves inputs and writes products from that folder (`outputs/final/`, `outputs/prep/`, and so on).

### Set it

```bash
export LAKE_DELINEATION_ROOT=/path/to/your/study-root
python3 /path/to/LakeDelineationTool/code/validate_study.py
```

The study folder needs `study_settings.py` (copy from `study_settings.example.py`). Relative paths in that file (`dem/your-dem.tif`) are resolved against the study root. Absolute paths are used as written.

The `code/` scripts do **not** have to live in the study folder. Point `LAKE_DELINEATION_ROOT` at the study and run the scripts from the repo (or any clone):

```bash
export LAKE_DELINEATION_ROOT=/project/6102189/tylerrt/my-bow-study
python3 ~/github-repos/LakeDelineationTool/code/basinAggregation.py
python3 ~/github-repos/LakeDelineationTool/code/reproject.py --prj WGS84
```

`pipeline_paths.py` then reads `/project/.../my-bow-study/study_settings.py` and writes under `/project/.../my-bow-study/outputs/`.

### Omit it

If the variable is unset, `pipeline_paths.py` uses `.` (the current working directory). That is enough when you have already `cd`'d to the study root:

```bash
cd /path/to/your/study-root
python3 code/validate_study.py
```

Do not `cd` into `code/` and run the scripts from there. `.` would then be `code/`, so the scripts would look for `code/study_settings.py` and write under `code/outputs/`.

### Slurm vs a one-off script

`Delineation-Workflow.slurm` uses the same variable. Uncomment this near the top of the slurm file (or export it before `sbatch`) to send **TauDEM and Python** products to that folder:

```bash
export LAKE_DELINEATION_ROOT="/project/6102189/tylerrt/my-bow-study"
```

That folder needs `study_settings.py`. `code/` can stay next to the slurm script (the submit directory). Leave the export unset and the job uses the directory where you ran `sbatch`, same as before. The slurm script `cd`s to the study root so `./outlet_overrides.csv` and `./stream_conditioning.csv` resolve there.

Job logs (`delineate_<jobid>.out`) still land in the submit directory, not the study folder.

---

## `Delineation-Workflow.slurm`

Update:

- `export PATH=...` — directory containing compiled TauDEM MPI binaries (see **Software requirements**)
- `VENV` — path to activated venv (`pip install -r requirements.txt`; see **Software requirements**)
- `STREAM_THRESHOLD`
- `FLOWPATH_NCORES`
- `LAKE_DELINEATION_ROOT` (optional) — study folder for `study_settings.py` and all outputs, including TauDEM. Leave unset to use the `sbatch` directory.

`CODE_DIR` is `code/` next to where you ran `sbatch` (or under the study root if that is the only copy). Submit from the repo or from a study root that contains `code/`:

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

## `conditionLakes.py`

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
```

## `conditionStreams.py`

Verify:

- `stream_conditioning.csv` (optional; step is skipped without it). Optional `valley_weight` and `end_weight` columns, one pair per path. Blank cells use the defaults (1.5 and 0.1). Links chain when one start cell is another row's end cell.
- Raw DEM on the same grid as `fdr_lakes.tif`
- Filtered lakes (lake cells are protected)

Outputs:

```text
outputs/interim/taudem_d8/fdr_lakes.tif   (edited in place)
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

- `area_km2` — local subbasin area (km²). Used if present; otherwise polygon area in m² × 10⁻⁶
- `DSContArea` — cumulative drainage at the pour point (m²); recomputed after merges
- `MIN_SUB_AREA` — merge threshold in km² applied to **local** area
- `MIN_RIV_SLOPE`
- `MIN_RIV_LENGTH`

Also ensure attribute names match your TauDEM outputs.

---

## `reproject.py`

Optional. Run after `basinAggregation.py` (or pass other shapefiles).

```bash
python3 code/reproject.py --prj WGS84
```

- `--prj` — required. `WGS84`, `EPSG:4326`, `EPSG4326`, or a PROJ string
- `--basins` / `--streams` — inputs (default: `outputs/final/basins_aggregated.shp` and `streams_aggregated.shp`)
- `--out-basins` / `--out-streams` — outputs (default: `<input_stem>_<crs>.shp` next to the input)

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
| `basins_aggregated_<crs>.shp` | Optional reprojected aggregated catchments (`reproject.py`) |
| `streams_aggregated_<crs>.shp` | Optional reprojected aggregated streams |
| `pour_points.shp` | Refined pour points for Pass 3 |

## `outputs/interim/`

TauDEM rasters and pass-specific vectors (single copy — not duplicated elsewhere).

## `outputs/working/`

Merged geofabric before final clean, plus HY sidecars when enabled:

- **`geofabric.gpkg`** — HY_Features profile GeoPackage (spatial layers plus catchment, nexus and link tables)
- **`catchment_registry.json`** — catchment identity, realization links, and associations
- **`hydrographic_network.json`** — dendritic catchment table + network metadata
- **`geofabric_aggregated.gpkg`** (+ `catchment_registry_aggregated.json`, `hydrographic_network_aggregated.json`) — same profile for the `basinAggregation.py` output, linked to `geofabric.gpkg` by containment

## `outputs/prep/`

Intermediate prep layers: lakes, gauges, selected outlets, reservoir IO nodes.

---

# OGC HY_Features alignment (in development / work in progress)

HY_Features enrichment is **off by default**. Enable it for `geofabric.gpkg` and JSON sidecars (shapefiles always keep TauDEM / MESH column names only):

```bash
export HY_FEATURES_ENABLED=1   # or set ENABLE_HY_FEATURES = True in a script
```

When enabled, outputs implement a scoped subset of the [OGC HY_Features conceptual model (14-111r6)](https://docs.ogc.org/is/14-111r6/14-111r6.html) as an **implementation schema** under profile **`LakeDelineationTool-DendriticGeofabric-1.0`** (self-assessed against conformance class `/conf/hy_features_conceptual_model`).

See [`docs/hy_features_conformance_profile.md`](docs/hy_features_conformance_profile.md), [`docs/hy_features_mapping.md`](docs/hy_features_mapping.md), and [`docs/hy_features_implementation_conventions.md`](docs/hy_features_implementation_conventions.md).

Every export ends with an automated conformance check. To re-run it on existing products:

```bash
cd code
python -m hy_features.validate ../outputs/working/geofabric.gpkg
python -m hy_features.validate ../outputs/working/geofabric_aggregated.gpkg --external ../outputs/working/geofabric.gpkg
```

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
