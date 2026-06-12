#!/bin/bash
# =============================================================================
# TauDEM South Saskatchewan Basin Delineation Pipeline
# =============================================================================
#
# Submit with:  sbatch tau-dem-delineation-srun.slurm
#
# This job builds a stream network and watershed delineation for the Bow-Bassano
# DEM, with special handling for instream reservoirs (lakes). TauDEM alone
# cannot pick sensible outlets through flat lake surfaces, so Python scripts
# edit flow directions and pour points between TauDEM passes.
#
# PIPELINE OVERVIEW
# -----------------
#
#   DEM (bow-bassano-elv.tif)
#        |
#        v
#   [Pass 1] TauDEM -- standard hydrologic conditioning + full network
#        |              (no pour points; discovers all subbasins)
#        v
#   [Python] filterLakes.py      -- clip HydroLAKES reservoirs to basin
#            getGauges.py        -- find stream gauges inside basin
#            rasterFlowpathEdit.py -- fix flow dirs inside each lake
#                                   (writes fdr_centerline_all.tif)
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
#            cleanGeofabric.py   -- remove phantom stream links
#        v
#   delineation-product/         -- final streams, watersheds, outlets
#
# KEY DIRECTORIES (under HOME_DIR)
# --------------------------------
#   dem/                         -- input elevation raster
#   taudem-interim-files/d8/     -- TauDEM rasters + intermediate shapefiles
#   taudem-interim-files/final/  -- Pass 3 TauDEM outputs
#   delineation-product/         -- published outputs copied here after each pass
#   points/                      -- pour point shapefiles produced by Python
#   lakes/                       -- filtered reservoir polygons (filterLakes.py)
#
# =============================================================================

#SBATCH --account=rrg-alpie
#SBATCH --job-name=TauDEM_Delineation
#SBATCH --output=delineate_%j.out
#SBATCH --ntasks=250
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=5G
#SBATCH --time=02:30:00

# Print a labelled section header to the job log.
log_section() {
    echo ""
    echo "=================================================================="
    echo ">> $1"
    echo "   $(date)"
    echo "=================================================================="
}

# Copy a TauDEM shapefile set (all sidecar files) into delineation-product/.
publish_shp() {
    local prefix="$1"
    cp "${prefix}".* "$OUT_DIR/"
}

echo "$(basename "$0"): Delineation started at $(date)"

# -----------------------------------------------------------------------------
# 0. Environment and paths
# -----------------------------------------------------------------------------
module purge
module restore scimods

HOME_DIR="/home/$USER/projects/rrg-alpie/tylerrt/southSaskDelineation"
INT_DIR="$HOME_DIR/taudem-interim-files/d8"      # Pass 1 & 2 interim rasters/shapefiles
FINAL_DIR="$HOME_DIR/taudem-interim-files/final" # Pass 3 outputs
OUT_DIR="$HOME_DIR/delineation-product"          # Published products for Python + GIS

# Contributing-area threshold for TauDEM stream definition (cell count).
# Applied identically in all three TauDEM passes.
STREAM_THRESHOLD=5555

DEM="$HOME_DIR/dem/Sask-mrdem-30-dtm.tif"
VENV="/home/$USER/virtual-envs/scienv/bin/activate"

mkdir -p "$INT_DIR" "$FINAL_DIR" "$OUT_DIR" "$HOME_DIR/points"
cd "$HOME_DIR" || exit 1

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
#   gridnet     -- stream order on the grid
#   threshold   -- flag stream source cells above area threshold
#   streamnet   -- trace streams and delineate all watersheds
# -----------------------------------------------------------------------------
log_section "Pass 1: TauDEM base network (no pour points)"

#srun pitremove -z "$DEM" -fel "$INT_DIR/stream-network_elv-fel.tif"
srun d8flowdir \
    -fel "$INT_DIR/stream-network_elv-fel.tif" \
    -sd8 "$INT_DIR/stream-network_elv-sd8.tif" \
    -p  "$INT_DIR/stream-network_elv-fdir.tif"
srun aread8 \
    -p  "$INT_DIR/stream-network_elv-fdir.tif" \
    -ad8 "$INT_DIR/stream-network_elv-ad8.tif" \
    -nc
srun gridnet \
    -p    "$INT_DIR/stream-network_elv-fdir.tif" \
    -gord "$INT_DIR/stream-network_elv-gord.tif"
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

# Convert watershed raster to polygon shapefile; publish for downstream Python.
gdal_polygonize.py \
    "$INT_DIR/original-delineated-watersheds.tif" \
    "$INT_DIR/original-delineated-watersheds.shp" \
    -overwrite
publish_shp "$INT_DIR/original-delineated-streams"
publish_shp "$INT_DIR/original-delineated-watersheds"

# -----------------------------------------------------------------------------
# 2. Python -- lake filtering, gauges, and flow-direction correction
# -----------------------------------------------------------------------------
# These scripts prepare reservoir-aware inputs for Pass 2:
#
#   filterLakes.py        -- select HydroLAKES polygons inside the basin
#   getGauges.py          -- locate stream gauges for outlet ranking
#   rasterFlowpathEdit.py -- per-lake outlet selection + centerline routing;
#                            writes INT_DIR/fdr_centerline_all.tif
# -----------------------------------------------------------------------------
log_section "Python: lake filtering, gauges, and raster flow-path edits"
source "$VENV"

python3 filterLakes.py
python3 getGauges.py
python3 rasterFlowpathEdit.py

# -----------------------------------------------------------------------------
# 3. TauDEM Pass 2 -- network using corrected lake flow directions
# -----------------------------------------------------------------------------
# Recomputes area accumulation and stream network using fdr_centerline_all.tif
# (lake interiors now drain through a single outlet). Still no pour points --
# this pass refreshes the network so pourPointsPass2.py can read updated
# stream/watershed geometry at lake boundaries.
# -----------------------------------------------------------------------------
log_section "Pass 2: TauDEM network with corrected lake flow directions"

srun aread8 \
    -p  "$INT_DIR/fdr_centerline_all.tif" \
    -ad8 "$INT_DIR/stream-network_elv-ad8.tif" \
    -nc
srun gridnet \
    -p    "$INT_DIR/fdr_centerline_all.tif" \
    -gord "$INT_DIR/stream-network_elv-gord.tif"
srun threshold \
    -ssa    "$INT_DIR/stream-network_elv-ad8.tif" \
    -src    "$INT_DIR/stream-network_elv-src.tif" \
    -thresh "$STREAM_THRESHOLD"
srun streamnet \
    -fel   "$INT_DIR/stream-network_elv-fel.tif" \
    -p     "$INT_DIR/fdr_centerline_all.tif" \
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
publish_shp "$INT_DIR/intermediate-delineated-streams"
publish_shp "$INT_DIR/intermediate-delineated-watersheds"

# -----------------------------------------------------------------------------
# 4. Python -- refined pour points for final delineation
# -----------------------------------------------------------------------------
# Reads Pass 2 outputs and fdr_centerline_all.tif to place pour points at
# lake inflows/outflows and other key network nodes. Writes pourPointsFinal.shp.
# -----------------------------------------------------------------------------
log_section "Python: pour point extraction (pourPointsPass2.py)"
python3 pourPointsPass2.py

# -----------------------------------------------------------------------------
# 5. TauDEM Pass 3 -- final watershed delineation at pour points
# -----------------------------------------------------------------------------
# Snaps pour points to the nearest stream cell, then delineates only the
# watersheds draining to those outlets.
#
#   moveoutletstostreams -- snap pourPointsFinal.shp to stream network
#   streamnet (-o ...)    -- delineate watersheds for snapped outlets only
# -----------------------------------------------------------------------------
log_section "Pass 3: TauDEM final delineation at pour points"

srun moveoutletstostreams \
    -p  "$INT_DIR/fdr_centerline_all.tif" \
    -src "$INT_DIR/stream-network_elv-src.tif" \
    -o   "$HOME_DIR/points/pourPointsFinal.shp" \
    -om  "$FINAL_DIR/snapped-outlets.shp" \
    -md  25
srun streamnet \
    -fel   "$INT_DIR/stream-network_elv-fel.tif" \
    -p     "$INT_DIR/fdr_centerline_all.tif" \
    -ad8   "$INT_DIR/stream-network_elv-ad8.tif" \
    -src   "$INT_DIR/stream-network_elv-src.tif" \
    -ord   "$INT_DIR/stream-network_elv-ord.tif" \
    -o     "$FINAL_DIR/snapped-outlets.shp" \
    -tree  "$FINAL_DIR/final-network-basin-tree.dat" \
    -coord "$FINAL_DIR/final-network-basin-coord.dat" \
    -net   "$FINAL_DIR/final-delineated-streams.shp" \
    -w     "$FINAL_DIR/final-delineated-watersheds.tif"

gdal_polygonize.py \
    "$FINAL_DIR/final-delineated-watersheds.tif" \
    "$FINAL_DIR/final-delineated-watersheds.shp" \
    -overwrite
publish_shp "$FINAL_DIR/final-delineated-streams"
publish_shp "$FINAL_DIR/final-delineated-watersheds"
publish_shp "$FINAL_DIR/snapped-outlets"

# -----------------------------------------------------------------------------
# 6. Python -- post-process final geofabric
# -----------------------------------------------------------------------------
#   combiningBasins.py -- merge subbasins split by lakes into unified units
#   cleanGeofabric.py  -- fix stream topology (remove phantom links)
# -----------------------------------------------------------------------------
log_section "Python: basin merging and geofabric cleanup"
python3 combiningBasins.py
python3 cleanGeofabric.py

echo ""
echo "$(basename "$0"): Delineation ended at $(date)"
