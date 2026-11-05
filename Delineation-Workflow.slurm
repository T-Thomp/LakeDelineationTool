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
#            conditionLakes.py   -- fix flow dirs inside each lake
#                                   (serial, or mpirun when FLOWPATH_NCORES>1;
#                                   writes fdr_lakes.tif)
#            conditionStreams.py -- (optional) force flow along CSV valley paths
#                                   in fdr_lakes.tif
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
#   outputs/working/             -- merged geofabric before final clean
#   outputs/prep/                -- lakes, gauges, pour-point prep
#   outputs/interim/taudem_d8/   -- Pass 1–2 TauDEM rasters and vectors
#   outputs/interim/taudem_pass3/ -- Pass 3 TauDEM outputs
#
# KEY DIRECTORIES
# ------------------------------------------------------------
#   DATA_DIR / LAKE_DELINEATION_ROOT
#       Study folder: outputs/ (TauDEM + Python) and relative input paths.
#       Default: the folder that contains the study_settings.py that was loaded
#       (LAKE_DELINEATION_ROOT = None). Set a path in that file to use another
#       folder. An exported LAKE_DELINEATION_ROOT still overrides the file.
#   study_settings.py
#       The directory where you ran sbatch, if that file is there.
#       Otherwise the copy next to this slurm script.
#   CODE_DIR
#       Python scripts next to this slurm script. If this script was started
#       from the repo, $SUBMIT_DIR/code is the same folder.
#
# SETUP CHECKLIST (paths & knobs to edit when adapting this workflow)
# --------------------------------------------------------------------
# Work top-to-bottom. Slurm cd's to DATA_DIR so ./ CSVs resolve there.
# Python and TauDEM both write under DATA_DIR/outputs (LAKE_DELINEATION_ROOT).
#
# [ ] study_settings.py
#       LAKE_DELINEATION_ROOT -- None, or the folder for outputs and relative paths
#       INPUT_DEM          -- elevation GeoTIFF
#       INPUT_LAKES        -- lake polygons (HydroLAKES by default; column map in the file)
#       GAUGE_SOURCE       -- hydat (WSC HYDAT) or file (CSV / point shapefile)
#       STREAM_THRESHOLD   -- TauDEM stream source area (cell count)
#       MIN_SUB_AREA       -- km² aggregation threshold (default 100)
#       LAKE_MIN_AREA_SQKM / LAKE_KEEP_TYPES -- which lakes to keep
#
# [ ] Delineation-Workflow.slurm  (this file)
#       module restore scimods  -- GDAL + mpi4py (see SOFTWARE REQUIREMENTS)
#       PATH to TauDEM     -- export PATH="$HOME/taudem-build/taudem:$PATH" (§0)
#       VENV               -- Python venv activate (pip install -r requirements.txt)
#       FLOWPATH_NCORES    -- conditionLakes.py workers (1 = plain python; >1 = mpirun)
#       #SBATCH --account / --ntasks / --mem-per-cpu / --time
#
# [ ] filterLakes.py
#       Pass-1 watersheds/streams under outputs/interim/taudem_d8/
#       lake file, size, and type values -- study_settings.py
#       output             -- outputs/prep/lakes.shp
#
# [ ] getGauges.py
#       Pass-1 watersheds under outputs/interim/taudem_d8/
#       output             -- outputs/prep/gauges.shp
#
# [ ] conditionLakes.py  (defaults in __main__ / --ncores)
#       Requires Pass 1 + filterLakes + getGauges first (see §2 below).
#       FDR / SRC / AD8 / watershed rasters under outputs/interim/taudem_d8/
#       streams            -- outputs/interim/taudem_d8/original-delineated-streams.shp
#       lakes / gauges     -- outputs/prep/lakes.shp, outputs/prep/gauges.shp
#       outlet_overrides.csv (optional manual lake outlets)
#       outputs            -- outputs/interim/taudem_d8/fdr_lakes.tif
#       Launch             -- --ncores 1: python3 directly. N>1: mpirun -np N (not srun)
#
# [ ] conditionStreams.py  (--csv; runs right after conditionLakes.py)
#       stream_conditioning.csv (optional; id,start_lat,start_lon,end_lat,end_lon,
#                                 valley_weight,end_weight; blank keeps defaults.
#                                 A start in another row's end cell chains; only
#                                 the last link searches downhill.)
#       INPUT_DEM          -- raw DEM used for the valley cost surface
#       outputs            -- edits outputs/interim/taudem_d8/fdr_lakes.tif in place
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
#       MIN_SUB_AREA       -- km² aggregation threshold, set in study_settings.py
#       MIN_RIV_SLOPE, MIN_RIV_LENGTH
#       column-name globals if your attribute table differs from TauDEM
#
# [ ] External / one-time inputs to stage before running
#       INPUT_DEM path     -- in study_settings.py
#       INPUT_LAKES        -- in study_settings.py (HydroLAKES, or your own polygons)
#       gauges             -- INPUT_HYDAT_DB, or INPUT_GAUGES when GAUGE_SOURCE=file
#       outlet_overrides.csv -- optional manual lake-outlet overrides
#
# SOFTWARE REQUIREMENTS (modules + Python venv)
# ---------------------------------------------
# See README.md "Software requirements". Steps below are for Alliance FIR; adapt on other sites.
#
# HPC modules (this script: module restore scimods) — FIR-tested:
#   GDAL        -- gdal_polygonize.py (must match GDAL==3.9.1 in requirements.txt)
#   mpi4py      -- conditionLakes.py only when FLOWPATH_NCORES>1 (FIR: module load only)
#   Slurm       -- sbatch / srun for TauDEM. conditionLakes parallel uses mpirun
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
#   mpi4py — only if FLOWPATH_NCORES>1. FIR: module load mpi4py/4.0.0 only
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
# Resolve directories before "module purge" so scontrol is still on PATH.

# Directory where you ran sbatch. Slurm starts the job here.
if [[ -n "${SLURM_SUBMIT_DIR:-}" ]]; then
    SUBMIT_DIR="$SLURM_SUBMIT_DIR"
else
    SUBMIT_DIR="$(pwd)"
fi

# Folder that holds this slurm script and code/. Under sbatch the running
# copy is in Slurm's spool, so recover the path that was passed to sbatch.
# Do this before "module purge" so scontrol is still on PATH.
SCRIPT_DIR=""
if [[ -n "${SLURM_JOB_ID:-}" ]] && command -v scontrol >/dev/null 2>&1; then
    SCRIPT_SRC="$(scontrol show job "$SLURM_JOB_ID" 2>/dev/null | sed -n 's/^[[:space:]]*Command=//p' | head -n 1)"
    SCRIPT_SRC="${SCRIPT_SRC%% *}"
    if [[ -n "$SCRIPT_SRC" && "$SCRIPT_SRC" != /* ]]; then
        SCRIPT_SRC="$SUBMIT_DIR/$SCRIPT_SRC"
    fi
    if [[ -n "$SCRIPT_SRC" && -f "$SCRIPT_SRC" ]]; then
        SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_SRC")" && pwd)"
    fi
fi
if [[ -z "$SCRIPT_DIR" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

# study_settings.py in the working directory wins. Otherwise use the copy
# next to this script. An already-exported LAKE_DELINEATION_SETTINGS wins.
if [[ -z "${LAKE_DELINEATION_SETTINGS:-}" ]]; then
    if [[ -f "$SUBMIT_DIR/study_settings.py" ]]; then
        export LAKE_DELINEATION_SETTINGS="$SUBMIT_DIR/study_settings.py"
    elif [[ -f "$SCRIPT_DIR/study_settings.py" ]]; then
        export LAKE_DELINEATION_SETTINGS="$SCRIPT_DIR/study_settings.py"
    else
        echo "ERROR: study_settings.py not found"
        echo "  looked in: $SUBMIT_DIR/study_settings.py"
        echo "  and:       $SCRIPT_DIR/study_settings.py"
        exit 1
    fi
fi
SETTINGS="$LAKE_DELINEATION_SETTINGS"

if [[ -d "$SCRIPT_DIR/code" ]]; then
    CODE_DIR="$SCRIPT_DIR/code"
elif [[ -d "$SUBMIT_DIR/code" ]]; then
    CODE_DIR="$SUBMIT_DIR/code"
else
    CODE_DIR=""
fi

module purge
module restore scimods

# export PATH="$HOME/taudem-build/taudem:$PATH"   # Set taudem:PATH here or set it in .bashrc file

# STREAM_THRESHOLD is read from study_settings.py after the study check below.
# It is the TauDEM stream-source area in cells, applied in all three passes.

# Workers for conditionLakes.py (lake flow-path edits).
#
# FLOWPATH_NCORES=1 runs as a normal Python script and does not import mpi4py.
# FLOWPATH_NCORES>1 partitions lakes across MPI ranks (by estimated cell count).
# Rank 0 builds the output GeoTIFF and merges masked FDR patches.
# Launch that case with mpirun -np N. srun direct-launch aborts in MPI_Init
# because this Open MPI build has no Slurm PMI support.
#
FLOWPATH_NCORES=16

# Remove TauDEM interim rasters/vectors after cleanGeofabric.py (off by default)
# export DELETE_INTERIM_FILES=1

VENV="/home/$USER/virtual-envs/scienv/bin/activate"

if [[ -z "$CODE_DIR" ]]; then
    echo "ERROR: no code/ directory next to the slurm script or the submit directory"
    echo "  looked in: $SCRIPT_DIR/code"
    echo "  and:       $SUBMIT_DIR/code"
    exit 1
fi

# Study folder comes from the chosen study_settings.py. None there means the
# folder that contains that file. A path already exported in the shell wins.
if [[ -z "${LAKE_DELINEATION_ROOT:-}" ]]; then
    if ! ROOT_FROM_SETTINGS="$(
        python3 "$CODE_DIR/validate_study.py" --settings "$SETTINGS" --query root
    )"; then
        echo "ERROR: could not read LAKE_DELINEATION_ROOT from $SETTINGS"
        exit 1
    fi
    if [[ -n "$ROOT_FROM_SETTINGS" ]]; then
        export LAKE_DELINEATION_ROOT="$ROOT_FROM_SETTINGS"
    fi
fi

if [[ -n "${LAKE_DELINEATION_ROOT:-}" ]]; then
    if [[ ! -d "${LAKE_DELINEATION_ROOT}" ]]; then
        echo "ERROR: LAKE_DELINEATION_ROOT is not a directory: ${LAKE_DELINEATION_ROOT}"
        exit 1
    fi
    DATA_DIR="$(cd "${LAKE_DELINEATION_ROOT}" && pwd)"
else
    DATA_DIR="$(cd "$(dirname "$SETTINGS")" && pwd)"
fi
export LAKE_DELINEATION_ROOT="$DATA_DIR"

OUTPUT_ROOT="$DATA_DIR/outputs"
INT_DIR="$OUTPUT_ROOT/interim/taudem_d8"       # Pass 1 & 2 TauDEM rasters/shapefiles
PASS3_DIR="$OUTPUT_ROOT/interim/taudem_pass3"  # Pass 3 TauDEM outputs
PREP_DIR="$OUTPUT_ROOT/prep"
WORKING_DIR="$OUTPUT_ROOT/working"
FINAL_DIR="$OUTPUT_ROOT/final"                 # Deliverables (basins, pour points)

mkdir -p "$INT_DIR" "$PASS3_DIR" "$PREP_DIR" "$WORKING_DIR" "$FINAL_DIR"
cd "$DATA_DIR" || exit 1
echo "Study settings:                    $SETTINGS"
echo "Study root (LAKE_DELINEATION_ROOT): $DATA_DIR"
echo "Python scripts (CODE_DIR):          $CODE_DIR"

if [[ ! -f "$SETTINGS" ]]; then
    echo "ERROR: study_settings.py not found: $SETTINGS"
    echo "  Edit INPUT_DEM, INPUT_LAKES, and the gauge source, then re-submit."
    exit 1
fi

python3 "$CODE_DIR/validate_study.py" --settings "$SETTINGS" || exit 1
DEM="$(python3 "$CODE_DIR/validate_study.py" --settings "$SETTINGS" --query dem)"
FDR_RASTER="$(python3 "$CODE_DIR/validate_study.py" --settings "$SETTINGS" --query fdr)"
STREAM_THRESHOLD="$(python3 "$CODE_DIR/validate_study.py" --settings "$SETTINGS" --query stream-threshold)"
echo "Stream threshold (cells): $STREAM_THRESHOLD"

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
#   conditionLakes.py     -- rewrite D8 flow directions inside each filtered lake
#                            so all lake cells drain to one outlet along a
#                            centerline "spine" (see script header docstring).
#   conditionStreams.py   -- for each start/end pair in stream_conditioning.csv,
#                            route the lowest-cost valley path on the DEM and
#                            point flow along it (lake cells are left alone).
#
# Why this step exists:
#   TauDEM Pass 1 leaves ambiguous flow on flat lake surfaces (many boundary
#   exits). Pass 2 and Pass 3 must use a corrected flow-direction raster or
#   streamnet will split basins incorrectly through reservoirs.
#
# Prerequisites (must exist before conditionLakes runs):
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
#
# Serial (workstation, login node, or compute node). No mpirun, srun, or mpi4py:
#   python3 code/conditionLakes.py --ncores 1
#
# Parallel, from inside this allocation. Use mpirun, not srun:
#   mpirun -np 16 python3 code/conditionLakes.py --ncores 16
#
# --ncores 1 always wins, even if FLOWPATH_NCORES is larger.
# --ncores N>1 is capped at the mpirun process count and at the number of lakes.
#
# Re-run standalone (from the study root, after Pass 1 + filterLakes + getGauges):
#   source "$VENV"
#   python3 code/conditionLakes.py --option override --csv outlet_overrides.csv --ncores 1
#
# Override-only re-run (fix individual lakes without a full rebuild):
#   Re-runs only the lakes listed in the CSV and pastes them into the existing
#   fdr_lakes.tif. Workers are capped at the number of lakes in the CSV.
#   Re-run TauDEM Pass 2 / Pass 3 afterwards for the change to take effect.
#   python3 code/conditionLakes.py --option override --csv my_fixes.csv --ncores 1
#   mpirun -np 4 python3 code/conditionLakes.py --option override --csv my_fixes.csv --ncores 4
#
# Stream conditioning (serial; skipped if the CSV does not exist):
#   python3 conditionStreams.py --csv stream_conditioning.csv
#   Re-run it after any full conditionLakes.py run, which rebuilds fdr_lakes.tif.
# -----------------------------------------------------------------------------
log_section "Python: lake filtering, gauges, and lake/stream conditioning"

python3 "$CODE_DIR/filterLakes.py" --settings "$SETTINGS"
python3 "$CODE_DIR/getGauges.py" --settings "$SETTINGS"

echo " before conditionLakes.py $(date)"
export FLOWPATH_NCORES
if [[ "${FLOWPATH_NCORES}" -le 1 ]]; then
    python3 "$CODE_DIR/conditionLakes.py" --settings "$SETTINGS" --ncores 1
else
    mpirun -np "${FLOWPATH_NCORES}" \
        python3 "$CODE_DIR/conditionLakes.py" --settings "$SETTINGS" \
        --ncores "${FLOWPATH_NCORES}"
fi
echo " after conditionLakes.py $(date)"

python3 "$CODE_DIR/conditionStreams.py" --settings "$SETTINGS"

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
python3 "$CODE_DIR/pourPointsPass2.py" --settings "$SETTINGS"

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
#                          MIN_SUB_AREA from study_settings.py (default 100 km²), preserving gauges and
#                          lake units. Comment out if you want the unaggregated
#                          cleanGeofabric products only.
#                          -> outputs/final/basins_aggregated.shp
#                             outputs/final/streams_aggregated.shp
# -----------------------------------------------------------------------------
log_section "Python: basin merging and geofabric cleanup"
python3 "$CODE_DIR/combiningBasins.py" --settings "$SETTINGS"
python3 "$CODE_DIR/cleanGeofabric.py" --settings "$SETTINGS"

log_section "Python: optional basin aggregation (basinAggregation.py)"
python3 "$CODE_DIR/basinAggregation.py" --settings "$SETTINGS"

echo ""
echo "$(basename "$0"): Delineation ended at $(date)"
