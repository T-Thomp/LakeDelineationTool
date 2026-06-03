#!/bin/bash
#SBATCH --account=rrg-alpie 
#SBATCH --job-name=TauDEM_Delineation
#SBATCH --output=delineate_%j.out
#SBATCH --ntasks=4              
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=6G
#SBATCH --time=01:00:00         

echo "$(basename $0): Delineation started at $(date)"

# --- 1. Environment & Paths ---
module purge
module restore scimods

HOME_DIR="/home/$USER/projects/rrg-alpie/tylerrt/southSaskDelineation"
INT_DIR="$HOME_DIR/taudem-interim-files/d8"
FINAL_DIR="$HOME_DIR/taudem-interim-files/final"  # New directory for 2nd pass
OUT_DIR="$HOME_DIR/delineation-product"

mkdir -p "$INT_DIR" "$FINAL_DIR" "$OUT_DIR"
cd "$HOME_DIR"

# --- 2. Initial TauDEM Pass (Generating the Network for Python) ---
echo ">> Pass 1: Generating base network for intersection analysis"

srun pitremove -z $HOME_DIR/dem/bow-bassano-elv.tif -fel $INT_DIR/stream-network_elv-fel.tif
srun d8flowdir -fel $INT_DIR/stream-network_elv-fel.tif -sd8 $INT_DIR/stream-network_elv-sd8.tif -p $INT_DIR/stream-network_elv-fdir_sask.tif
srun aread8 -p $INT_DIR/stream-network_elv-fdir_sask.tif -ad8 $INT_DIR/stream-network_elv-ad8.tif -nc
srun gridnet -p $INT_DIR/stream-network_elv-fdir_sask.tif -gord $INT_DIR/stream-network_elv-gord.tif
srun threshold -ssa $INT_DIR/stream-network_elv-ad8.tif -src $INT_DIR/stream-network_elv-src.tif -thresh 5555

# Run streamnet without outlets to get the full network for Python to use
srun streamnet \
  -fel "$INT_DIR/stream-network_elv-fel.tif" \
  -p "$INT_DIR/stream-network_elv-fdir_sask.tif" \
  -ad8 "$INT_DIR/stream-network_elv-ad8.tif" \
  -src "$INT_DIR/stream-network_elv-src.tif" \
  -ord "$INT_DIR/stream-network_elv-ord.tif" \
  -tree "$INT_DIR/stream-network-basin-tree.dat" \
  -coord "$INT_DIR/stream-network-basin-coord.dat" \
  -net "$INT_DIR/original-delineated-streams.shp" \
  -w "$INT_DIR/original-delineated-watersheds.tif"

echo ">> Finalizing Products"

gdal_polygonize.py \
  $INT_DIR/original-delineated-watersheds.tif \
  $INT_DIR/original-delineated-watersheds.shp \
  -overwrite

# Copy to the product folder so Python can find them
cp $INT_DIR/original-delineated-streams.* $OUT_DIR/
cp $INT_DIR/original-delineated-watersheds.* $OUT_DIR/

mkdir -p "$HOME_DIR/points"

# --- 3. Python Analysis: Generate Pour Points ---
echo ">> Activating Virtual Environment and Running Python Script... $(date)"
source /home/$USER/virtual-envs/scienv/bin/activate

python3 filterLakes.py

echo " --------------------------------------------------- $(date) ---------------------------------------------------"

python3 getGauges.py

echo " --------------------------------------------------- $(date) ---------------------------------------------------"

python3 rasterFlowpathEdit-May26.py

echo " --------------------------------------------------- $(date) ---------------------------------------------------"

# --- 4. Intermediate TauDEM Pass (Delineating Specific Outlets) ---
echo ">> Pass 2: Delineating based on new flow directions $(date)"

# Calculate contributing area based on the FIXED reservoir FDR
srun aread8 \
  -p "$INT_DIR/fdr_centerline_all.tif" \
  -ad8 "$INT_DIR/stream-network_elv-ad8.tif" \
  -nc

# Calculate grid network order
srun gridnet \
  -p "$INT_DIR/fdr_centerline_all.tif" \
  -gord "$INT_DIR/stream-network_elv-gord.tif"

# Apply threshold to define new stream sources
srun threshold \
  -ssa "$INT_DIR/stream-network_elv-ad8.tif" \
  -src "$INT_DIR/stream-network_elv-src.tif" \
  -thresh 5555

# Run streamnet with the snapped outlets
srun streamnet \
  -fel "$INT_DIR/stream-network_elv-fel.tif" \
  -p "$INT_DIR/fdr_centerline_all.tif" \
  -ad8 "$INT_DIR/stream-network_elv-ad8.tif" \
  -src "$INT_DIR/stream-network_elv-src.tif" \
  -ord "$INT_DIR/stream-network_elv-ord.tif" \
  -tree "$INT_DIR/intermediate-network-basin-tree.dat" \
  -coord "$INT_DIR/intermediate-network-basin-coord.dat" \
  -net "$INT_DIR/intermediate-delineated-streams.shp" \
  -w "$INT_DIR/intermediate-delineated-watersheds.tif"

echo ">> Finalizing Products"

gdal_polygonize.py \
  $INT_DIR/intermediate-delineated-watersheds.tif \
  $INT_DIR/intermediate-delineated-watersheds.shp \
  -overwrite

# Copy to the product folder so Python can find them
cp $INT_DIR/intermediate-delineated-streams.* $OUT_DIR/
cp $INT_DIR/intermediate-delineated-watersheds.* $OUT_DIR/

echo " --------------------------------------------------- $(date) ---------------------------------------------------"

python3 pourPointsPass2.py

echo " --------------------------------------------------- $(date) ---------------------------------------------------"


# --- 4. Final TauDEM Pass (Delineating Specific Outlets) ---
echo ">> Pass 3: Delineating specific watersheds based on Python pour points"

srun moveoutletstostreams \
  -p $INT_DIR/fdr_centerline_all.tif \
  -src $INT_DIR/stream-network_elv-src.tif \
  -o "$HOME_DIR/points/pourPointsFinal.shp" \
  -om $FINAL_DIR/snapped-outlets.shp \
  -md 25

# Run streamnet with the snapped outlets
srun streamnet \
  -fel "$INT_DIR/stream-network_elv-fel.tif" \
  -p "$INT_DIR/fdr_centerline_all.tif" \
  -ad8 "$INT_DIR/stream-network_elv-ad8.tif" \
  -src "$INT_DIR/stream-network_elv-src.tif" \
  -ord "$INT_DIR/stream-network_elv-ord.tif" \
  -o "$FINAL_DIR/snapped-outlets.shp" \
  -tree "$FINAL_DIR/final-network-basin-tree.dat" \
  -coord "$FINAL_DIR/final-network-basin-coord.dat" \
  -net "$FINAL_DIR/final-delineated-streams.shp" \
  -w "$FINAL_DIR/final-delineated-watersheds.tif"

# --- 5. Polygonize & Finalization ---
echo ">> Finalizing Products"

gdal_polygonize.py \
  $FINAL_DIR/final-delineated-watersheds.tif \
  $FINAL_DIR/final-delineated-watersheds.shp \
  -overwrite

# Copy final products
cp $FINAL_DIR/final-delineated-streams.* $OUT_DIR/
cp $FINAL_DIR/final-delineated-watersheds.* $OUT_DIR/
cp $FINAL_DIR/snapped-outlets.* $OUT_DIR/

echo " --------------------------------------------------- $(date) ---------------------------------------------------"
echo ">> Running Python Script combiningBasins"
python3 combiningBasins.py

echo "$(basename $0): Delineation ended at $(date)"