#!/bin/bash
# =============================================================================
# TauDEM Reservoir Basin Delineation Pipeline
# =============================================================================
#
# Submit with:  sbatch Delineation-Workflow.slurm
#
# This job builds a stream network and watershed delineation for a selected
# DEM, with special handling for instream reservoirs (lakes). TauDEM alone
# cannot pick sensible outlets through flat lake surfaces, so Python scripts
# edit flow directions and pour points between TauDEM passes.
#
# PIPELINE OVERVIEW
# -----------------
#
#   DEM (DEM.tif)
#        |
#        v
#   [Pass 1] TauDEM -- standard hydrologic conditioning + full network
#        |              (no pour points; discovers all subbasins)
#        v
#   [Python] filterLakes.py      -- clip HydroLAKES reservoirs to basin
#            getGauges.py        -- find stream gauges inside basin
#            rasterFlowpathEdit.py -- fix flow dirs inside each lake (MPI-parallel;
#                                   writes fdr_lakes.tif; see FLOWPATH_NCORES)
#        v
#   [Pass 2] TauDEM -- re-run network using corrected lake flow directions
#        |
#        v
#   [Python] pourPointsPass2.py -- derive refined pour points at lake in/outflows
#        v
#   [Pass 3] TauDEM -- final delineation snapped to pour points
#        |
#        v
#   [Python] combiningBasins.py  -- merge lake-adjacent subbasins
#            cleanGeofabric.py   -- remove phantom stream links; attach gauges
#            basinAggregation.py -- (optional) merge small headwater subbasins
#        v
#   outputs/final/               -- deliverables: basins, basins_aggregated, pour_points
#   outputs/working/             -- merged geofabric before final clean (+ HY sidecars)
#   outputs/prep/                -- lakes, gauges, pour-point prep
#   outputs/interim/taudem_d8/   -- Pass 1–2 TauDEM rasters and vectors
#   outputs/interim/taudem_pass3/ -- Pass 3 TauDEM outputs
#
# KEY DIRECTORIES (auto-detected from this script's location)
# ------------------------------------------------------------
#   DATA_DIR                     -- folder containing this .slurm file (dem/, outputs/, …)
#   CODE_DIR                     -- DATA_DIR/code/ (Python scripts + pipeline_paths.py)
#
# SETUP CHECKLIST (paths & knobs to edit when adapting this workflow)
# --------------------------------------------------------------------
# Work top-to-bottom. Slurm cd's to DATA_DIR; Python runs from CODE_DIR.
# Relative paths in pipeline_paths.py resolve against DATA_DIR (LAKE_DELINEATION_ROOT).
#
# [ ] code/pipeline_paths.py  (edit USER INPUTS at top of file)
#       INPUT_DEM          -- study elevation GeoTIFF (under DATA_DIR/dem/)
#       INPUT_HYDAT_DB     -- HYDAT SQLite database
#       INPUT_HYDROLAKES   -- global HydroLAKES polygon shapefile
#
# [ ] Delineation-Workflow.slurm  (this file)
#       module restore scimods  -- GDAL + mpi4py (see SOFTWARE REQUIREMENTS)
#       PATH to TauDEM     -- export PATH="$HOME/taudem-build/taudem:$PATH" (§0)
#       VENV               -- Python venv activate (pip install -r requirements.txt)
#       STREAM_THRESHOLD   -- TauDEM stream source area (cell count)
#       FLOWPATH_NCORES    -- MPI worker ranks for rasterFlowpathEdit.py (see §0)
#       HY_FEATURES_ENABLED -- unset/off by default; export =1 to enable HY geofabric
#       #SBATCH --account / --ntasks / --mem-per-cpu / --time
#
# [ ] filterLakes.py
#       Pass-1 watersheds/streams under outputs/interim/taudem_d8/
#       MIN_AREA           -- km² lake-size filter
#       output             -- outputs/prep/lakes.shp
#
# [ ] getGauges.py
#       Pass-1 watersheds under outputs/interim/taudem_d8/
#       output             -- outputs/prep/gauges.shp
#
# [ ] rasterFlowpathEdit.py  (defaults in __main__ / --ncores)
#       Requires Pass 1 + filterLakes + getGauges first (see §2 below).
#       FDR / SRC / AD8 / watershed rasters under outputs/interim/taudem_d8/
#       streams            -- outputs/interim/taudem_d8/original-delineated-streams.shp
#       lakes / gauges     -- outputs/prep/lakes.shp, outputs/prep/gauges.shp
#       outlet_overrides.csv (optional manual lake outlets)
#       outputs            -- outputs/interim/taudem_d8/fdr_lakes.tif
#                             outputs/prep/selected_outlets.shp
#       Launch             -- MUST use srun/mpirun (mpi4py); see FLOWPATH_NCORES
#
# [ ] pourPointsPass2.py  (paths from pipeline_paths.py)
#       intermediate streams/watersheds under outputs/interim/taudem_d8/
#       fdr_lakes.tif, lakes, gauges
#       outputs            -- outputs/final/pour_points.shp
#                             outputs/prep/reservoir_io_nodes.shp
#
# [ ] combiningBasins.py
#       PATHS{}            -- basins, streams, lakes, snapped outlets, gauges
#       OVERRIDES_CSV      -- outlet_overrides.csv
#       OUTPUT_DIR         -- outputs/working/
#       GAUGE_SEARCH_RADIUS, MIN_INTERNAL_STREAM_LEN
#
# [ ] cleanGeofabric.py  (paths from pipeline_paths.py)
#       input              -- outputs/working/basins_merged.shp, streams_merged.shp
#       gauges             -- outputs/prep/gauges.shp
#       output             -- outputs/final/basins.shp, streams.shp
#       DELETE_INTERIM_FILES -- export =1 to remove outputs/interim/ after cleanup
#
# [ ] basinAggregation.py  (optional; paths from pipeline_paths.py)
#       INPUT_BASINS / INPUT_RIVERS / OUTPUT_BASINS / OUTPUT_RIVERS
#       MIN_SUB_AREA       -- km² aggregation threshold (default 100)
#       MIN_RIV_SLOPE, MIN_RIV_LENGTH
#       column-name globals if your attribute table differs from TauDEM
#
# [ ] External / one-time inputs to stage before running
#       INPUT_DEM path     -- in pipeline_paths.py (default under dem/)
#       INPUT_HYDROLAKES   -- in pipeline_paths.py
#       INPUT_HYDAT_DB     -- in pipeline_paths.py
#       outlet_overrides.csv -- optional manual lake-outlet overrides
#
# SOFTWARE REQUIREMENTS (modules + Python venv)
# ---------------------------------------------
# See README.md "Software requirements". Steps below are for Alliance FIR; adapt on other sites.
#
# HPC modules (this script: module restore scimods) — FIR-tested:
#   GDAL        -- gdal_polygonize.py (must match GDAL==3.9.1 in requirements.txt)
#   mpi4py      -- rasterFlowpathEdit.py MPI (FIR: module load only; not in requirements.txt)
#   Slurm       -- sbatch / srun (self-built TauDEM MPI + rasterFlowpathEdit MPI)
#
# TauDEM (not a cluster module): compile with MPI; add install dir to PATH in §0.
#
# Recreate scimods on Alliance FIR:
#   module load StdEnv/2023 gdal/3.9.1 mpi4py/4.0.0
#   module save scimods
#
# Other clusters / local: use site module loads or pip install mpi4py; match GDAL to requirements.txt.
#
# Python venv (VENV below): load modules, activate venv, pip install -r requirements.txt
#   GDAL 3.9.1, geopandas 1.0.1, pandas 2.2.3, numpy 1.26.4, scipy 1.15.2,
#   shapely 2.0.7, fiona 1.10.1, pyogrio 0.10.0, pyproj 3.7.1
#   mpi4py — FIR: module load mpi4py/4.0.0 only
# =============================================================================

#SBATCH --account=rrg-alpie
#SBATCH --job-name=TauDEM_Delineation
#SBATCH --output=delineate_%j.out
#SBATCH --ntasks=250
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=5G
#SBATCH --time=05:00:00

# Print a labelled section header to the job log.
log_section() {
    echo ""
    echo "=================================================================="
    echo ">> $1"
    echo "   $(date)"
    echo "=================================================================="
}

echo "$(basename "$0"): Delineation started at $(date)"

# -----------------------------------------------------------------------------
# 0. Environment and paths
# -----------------------------------------------------------------------------
# Modules: GDAL + mpi4py — see header "SOFTWARE REQUIREMENTS".
# TauDEM:  self-built MPI binaries; add compiled install dir to PATH below.
# Python:  source VENV before §2–§6 (geopandas, scipy, mpi4py, osgeo/gdal).
# -----------------------------------------------------------------------------
module purge
module restore scimods

# export PATH="$HOME/taudem-build/taudem:$PATH"   # Set taudem:PATH here or set it in .bashrc file

# Project root = directory containing this slurm script; code/ holds Python scripts.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR"
CODE_DIR="$SCRIPT_DIR/code"

OUTPUT_ROOT="$DATA_DIR/outputs"
INT_DIR="$OUTPUT_ROOT/interim/taudem_d8"       # Pass 1 & 2 TauDEM rasters/shapefiles
PASS3_DIR="$OUTPUT_ROOT/interim/taudem_pass3"  # Pass 3 TauDEM outputs
PREP_DIR="$OUTPUT_ROOT/prep"
WORKING_DIR="$OUTPUT_ROOT/working"
FINAL_DIR="$OUTPUT_ROOT/final"                 # Deliverables (basins, pour points)

# Contributing-area threshold for TauDEM stream definition (cell count).
# Applied identically in all three TauDEM passes.
STREAM_THRESHOLD=5555

# Worker MPI ranks for rasterFlowpathEdit.py (lake flow-path edits).
#
# rasterFlowpathEdit.py uses mpi4py: each rank processes a batch of lakes in
# parallel (partitioned by estimated lake cell count). Rank 0 builds the output
# GeoTIFF and merges masked FDR patches from all workers.
#
#   FLOWPATH_NCORES  -- active worker count (env + --ncores)
#   srun --ntasks    -- must be >= FLOWPATH_NCORES (extra ranks stay idle)
#
FLOWPATH_NCORES=16

# HY_Features enrichment + geofabric.gpkg (off by default; uncomment to enable)
# export HY_FEATURES_ENABLED=1

# Remove TauDEM interim rasters/vectors after cleanGeofabric.py (off by default)
# export DELETE_INTERIM_FILES=1

VENV="/home/$USER/virtual-envs/scienv/bin/activate"

mkdir -p "$INT_DIR" "$PASS3_DIR" "$PREP_DIR" "$WORKING_DIR" "$FINAL_DIR"
export LAKE_DELINEATION_ROOT="$DATA_DIR"
export PYTHONPATH="${CODE_DIR}${PYTHONPATH:+:$PYTHONPATH}"
cd "$DATA_DIR" || exit 1

if [[ ! -f "${CODE_DIR}/pipeline_paths.py" ]]; then
    echo "ERROR: pipeline_paths.py not found in CODE_DIR=${CODE_DIR}"
    echo "  Expected layout: ${DATA_DIR}/code/pipeline_paths.py"
    exit 1
fi

DEM="$(python3 -c "from pipeline_paths import INPUT_DEM; print(INPUT_DEM)" 2>&1)" || {
    echo "ERROR: failed to load INPUT_DEM from pipeline_paths.py"
    echo "$DEM"
    exit 1
}
python3 -c "from pipeline_paths import ensure_output_dirs; ensure_output_dirs()" || exit 1
FDR_RASTER="$(python3 -c "from pipeline_paths import FDR_CENTERLINE; print(FDR_CENTERLINE)" 2>&1)" || {
    echo "ERROR: failed to load FDR_CENTERLINE from pipeline_paths.py"
    echo "$FDR_RASTER"
    exit 1
}
if [[ ! -f "$DEM" ]]; then
    echo "ERROR: DEM not found: $DEM"
    echo "  Edit INPUT_DEM in ${CODE_DIR}/pipeline_paths.py or stage dem/ under DATA_DIR"
    exit 1
fi
echo "Using DEM: $DEM"

# Modules first, then venv (mpi4py module must be loaded before activate on FIR).
# numpy/scipy/geopandas come from the venv only — not from module load.
source "$VENV"

# -----------------------------------------------------------------------------
# 1. TauDEM Pass 1 -- base hydrology and unconstrained network
# -----------------------------------------------------------------------------
# Runs the standard TauDEM sequence on the raw DEM. No pour points are supplied,
# so streamnet delineates every subbasin in the domain. Python uses this full
# network to understand lake/stream intersections before we fix lake routing.
#
#   pitremove   -- fill pits/depressions in the DEM
#   d8flowdir   -- D8 flow direction and slope
#   aread8      -- upslope contributing area per cell
#   threshold   -- flag stream source cells above area threshold
#   streamnet   -- trace streams and delineate all watersheds (-ord writes order raster)
# -----------------------------------------------------------------------------
log_section "Pass 1: TauDEM base network (no pour points)"

 srun pitremove -z "$DEM" -fel "$INT_DIR/stream-network_elv-fel.tif"
 srun d8flowdir \
    -fel "$INT_DIR/stream-network_elv-fel.tif" \
    -sd8 "$INT_DIR/stream-network_elv-sd8.tif" \
    -p  "$INT_DIR/stream-network_elv-fdir.tif"
srun aread8 \
    -p  "$INT_DIR/stream-network_elv-fdir.tif" \
    -ad8 "$INT_DIR/stream-network_elv-ad8.tif" \
    -nc
srun threshold \
    -ssa    "$INT_DIR/stream-network_elv-ad8.tif" \
    -src    "$INT_DIR/stream-network_elv-src.tif" \
    -thresh "$STREAM_THRESHOLD"
srun streamnet \
    -fel   "$INT_DIR/stream-network_elv-fel.tif" \
    -p     "$INT_DIR/stream-network_elv-fdir.tif" \
    -ad8   "$INT_DIR/stream-network_elv-ad8.tif" \
    -src   "$INT_DIR/stream-network_elv-src.tif" \
    -ord   "$INT_DIR/stream-network_elv-ord.tif" \
    -tree  "$INT_DIR/stream-network-basin-tree.dat" \
    -coord "$INT_DIR/stream-network-basin-coord.dat" \
    -net   "$INT_DIR/original-delineated-streams.shp" \
    -w     "$INT_DIR/original-delineated-watersheds.tif"

# Convert watershed raster to polygon shapefile for downstream Python.
gdal_polygonize.py \
    "$INT_DIR/original-delineated-watersheds.tif" \
    "$INT_DIR/original-delineated-watersheds.shp" \
    -overwrite

# -----------------------------------------------------------------------------
# 2. Python -- lake filtering, gauges, and flow-direction correction
# -----------------------------------------------------------------------------
# These scripts prepare reservoir-aware inputs for TauDEM Pass 2.
#
#   filterLakes.py        -- select HydroLAKES polygons inside the Pass 1 basin
#   getGauges.py          -- locate Hydat stream gauges for outlet ranking
#
#   rasterFlowpathEdit.py -- rewrite D8 flow directions inside each filtered lake
#                            so all lake cells drain to one outlet along a
#                            centerline "spine" (see script header docstring).
#
# Why this step exists:
#   TauDEM Pass 1 leaves ambiguous flow on flat lake surfaces (many boundary
#   exits). Pass 2 and Pass 3 must use a corrected flow-direction raster or
#   streamnet will split basins incorrectly through reservoirs.
#
# Prerequisites (must exist before rasterFlowpathEdit runs):
#   outputs/interim/taudem_d8/stream-network_elv-{fdir,src,ad8}.tif
#   outputs/interim/taudem_d8/original-delineated-watersheds.tif
#   outputs/interim/taudem_d8/original-delineated-streams.shp
#   outputs/prep/lakes.shp          (from filterLakes.py)
#   outputs/prep/gauges.shp        (from getGauges.py; optional but recommended)
#   outlet_overrides.csv              (optional; manual lat/lon per Hylak_id)
#
# Outputs consumed by Pass 2 / Pass 3 / pourPointsPass2:
#   outputs/interim/taudem_d8/fdr_lakes.tif  -- full-domain FDR copy with
#                                                      lake interiors patched only
#   outputs/prep/selected_outlets.shp                 -- chosen outlet per lake
#
# MPI launch (required for parallel lake processing):
#   export FLOWPATH_NCORES=N
#   srun --ntasks=N --cpus-per-task=1 python3 rasterFlowpathEdit.py --ncores N
#
#   --ncores caps active workers at min(N, srun task count). If you omit srun,
#   mpi4py sees a single rank and runs serially regardless of --ncores.
#
# Re-run standalone (from DATA_DIR, after Pass 1 + filterLakes + getGauges):
#   source "$VENV"
#   export FLOWPATH_NCORES=16
#   srun --ntasks=16 --cpus-per-task=1 \
#       python3 rasterFlowpathEdit.py --ncores 16
#
# Serial debug (interactive node, no srun):
#   python3 rasterFlowpathEdit.py --ncores 1
# -----------------------------------------------------------------------------
log_section "Python: lake filtering, gauges, and raster flow-path edits"

python3 "$CODE_DIR/filterLakes.py"
python3 "$CODE_DIR/getGauges.py"

echo " before rasterFlowpathEdit.py $(date)"
export FLOWPATH_NCORES
srun --ntasks="${FLOWPATH_NCORES}" --cpus-per-task=1 \
    python3 "$CODE_DIR/rasterFlowpathEdit.py" --ncores "${FLOWPATH_NCORES}"
# Serial fallback (interactive debug on a login/compute node):
# python3 rasterFlowpathEdit.py --ncores 1
echo " after rasterFlowpathEdit.py $(date)"

# -----------------------------------------------------------------------------
# 3. TauDEM Pass 2 -- network using corrected lake flow directions
# -----------------------------------------------------------------------------
# Recomputes area accumulation and stream network using fdr_lakes.tif
# (lake interiors now drain through a single outlet). Still no pour points --
# this pass refreshes the network so pourPointsPass2.py can read updated
# stream/watershed geometry at lake boundaries.
# -----------------------------------------------------------------------------
log_section "Pass 2: TauDEM network with corrected lake flow directions"

srun aread8 \
    -p  "$FDR_RASTER" \
    -ad8 "$INT_DIR/stream-network_elv-ad8.tif" \
    -nc
srun threshold \
    -ssa    "$INT_DIR/stream-network_elv-ad8.tif" \
    -src    "$INT_DIR/stream-network_elv-src.tif" \
    -thresh "$STREAM_THRESHOLD"
srun streamnet \
    -fel   "$INT_DIR/stream-network_elv-fel.tif" \
    -p     "$FDR_RASTER" \
    -ad8   "$INT_DIR/stream-network_elv-ad8.tif" \
    -src   "$INT_DIR/stream-network_elv-src.tif" \
    -ord   "$INT_DIR/stream-network_elv-ord.tif" \
    -tree  "$INT_DIR/intermediate-network-basin-tree.dat" \
    -coord "$INT_DIR/intermediate-network-basin-coord.dat" \
    -net   "$INT_DIR/intermediate-delineated-streams.shp" \
    -w     "$INT_DIR/intermediate-delineated-watersheds.tif"

gdal_polygonize.py \
    "$INT_DIR/intermediate-delineated-watersheds.tif" \
    "$INT_DIR/intermediate-delineated-watersheds.shp" \
    -overwrite

# -----------------------------------------------------------------------------
# 4. Python -- refined pour points for final delineation
# -----------------------------------------------------------------------------
# Reads Pass 2 outputs and fdr_lakes.tif to place pour points at
# lake inflows/outflows and other key network nodes. Writes outputs/final/pour_points.shp.
# -----------------------------------------------------------------------------
log_section "Python: pour point extraction (pourPointsPass2.py)"
python3 "$CODE_DIR/pourPointsPass2.py"

# -----------------------------------------------------------------------------
# 5. TauDEM Pass 3 -- final watershed delineation at pour points
# -----------------------------------------------------------------------------
# Snaps pour points to the nearest stream cell, then delineates only the
# watersheds draining to those outlets.
#
#   moveoutletstostreams -- snap pour_points.shp to stream network
#   streamnet (-o ...)    -- delineate watersheds for snapped outlets only
# -----------------------------------------------------------------------------
log_section "Pass 3: TauDEM final delineation at pour points"

srun moveoutletstostreams \
    -p  "$FDR_RASTER" \
    -src "$INT_DIR/stream-network_elv-src.tif" \
    -o   "$FINAL_DIR/pour_points.shp" \
    -om  "$PASS3_DIR/snapped-outlets.shp" \
    -md  25
srun streamnet \
    -fel   "$INT_DIR/stream-network_elv-fel.tif" \
    -p     "$FDR_RASTER" \
    -ad8   "$INT_DIR/stream-network_elv-ad8.tif" \
    -src   "$INT_DIR/stream-network_elv-src.tif" \
    -ord   "$INT_DIR/stream-network_elv-ord.tif" \
    -o     "$PASS3_DIR/snapped-outlets.shp" \
    -tree  "$PASS3_DIR/final-network-basin-tree.dat" \
    -coord "$PASS3_DIR/final-network-basin-coord.dat" \
    -net   "$PASS3_DIR/final-delineated-streams.shp" \
    -w     "$PASS3_DIR/final-delineated-watersheds.tif"

gdal_polygonize.py \
    "$PASS3_DIR/final-delineated-watersheds.tif" \
    "$PASS3_DIR/final-delineated-watersheds.shp" \
    -overwrite

# -----------------------------------------------------------------------------
# 6. Python -- post-process final geofabric
# -----------------------------------------------------------------------------
#   combiningBasins.py   -- merge subbasins split by lakes into unified units;
#                          writes lake_id / is_lake / lake_area / frac_lake
#                          -> outputs/working/basins_merged.shp
#                             outputs/working/streams_merged.shp
#   cleanGeofabric.py    -- dissolve split basins, fix phantom stream links,
#                          attach STATION_NU gauge IDs
#                          -> outputs/final/basins.shp
#                             outputs/final/streams.shp
#                          optional: DELETE_INTERIM_FILES=1 removes outputs/interim/
#   basinAggregation.py  -- OPTIONAL: aggregate small headwater subbasins below
#                          MIN_SUB_AREA (default 100 km²), preserving gauges and
#                          lake units. Comment out if you want the unaggregated
#                          cleanGeofabric products only.
#                          -> outputs/final/basins_aggregated.shp
#                             outputs/final/streams_aggregated.shp
# -----------------------------------------------------------------------------
log_section "Python: basin merging and geofabric cleanup"
python3 "$CODE_DIR/combiningBasins.py"
python3 "$CODE_DIR/cleanGeofabric.py"

log_section "Python: optional basin aggregation (basinAggregation.py)"
python3 "$CODE_DIR/basinAggregation.py"

echo ""
echo "$(basename "$0"): Delineation ended at $(date)"
