# Lake Delineation Tool

A hydrologic delineation workflow built around **TauDEM** for generating stream networks and watershed boundaries with **special handling for instream reservoirs (lakes)**.

Unlike a standard TauDEM workflow, this pipeline performs multiple delineation passes with custom Python preprocessing to ensure realistic flow paths through flat lake surfaces.

---

# Overview

Submit the workflow using:

```bash
sbatch tau-dem-delineation-srun.slurm
```

The workflow builds a stream network and watershed delineation for the **Bow–Bassano DEM** using three TauDEM passes with Python-based corrections between each pass.

---

# Pipeline

```text
DEM (bow-bassano-elv.tif)
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

Aggregates small upstream subbasins into larger watershed units.

Default aggregation threshold:

```text
100 km²
```

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

- `HOME_DIR`
- `DEM`
- `VENV`
- `STREAM_THRESHOLD`
- `FLOWPATH_NCORES`

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

- `MIN_SUB_AREA`
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
- Optional aggregated watershed products

---
