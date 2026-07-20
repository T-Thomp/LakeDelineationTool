# LakeDelineationTool

=============================================================================
TauDEM Lake Basin Delineation Pipeline
=============================================================================

Submit with:  sbatch tau-dem-delineation-srun.slurm

This job builds a stream network and watershed delineation for the Bow-Bassano
DEM, with special handling for instream reservoirs (lakes). TauDEM alone
cannot pick sensible outlets through flat lake surfaces, so Python scripts
edit flow directions and pour points between TauDEM passes.

PIPELINE OVERVIEW
-----------------

  DEM (bow-bassano-elv.tif)
       |
       v
  [Pass 1] TauDEM -- standard hydrologic conditioning + full network
       |              (no pour points; discovers all subbasins)
       v
  [Python] filterLakes.py      -- clip HydroLAKES reservoirs to basin
           getGauges.py        -- find stream gauges inside basin
           rasterFlowpathEdit.py -- fix flow dirs inside each lake
                                  (writes fdr_centerline_all.tif)
       v
  [Pass 2] TauDEM -- re-run network using corrected lake flow directions
       |
       v
  [Python] pourPointsPass2.py -- derive refined pour points at lake in/outflows
       v
  [Pass 3] TauDEM -- final delineation snapped to pour points
       |
       v
  [Python] combiningBasins.py  -- merge lake-adjacent subbasins
           cleanGeofabric.py   -- remove phantom stream links; attach gauges
           basinAggregation.py -- (optional) merge small headwater subbasins
       v
  delineation-product/         -- final streams, watersheds, outlets
  merged_basins/               -- reservoir + cleaned (+ optional aggregated) fabric

KEY DIRECTORIES (under HOME_DIR)
--------------------------------
  dem/                         -- input elevation raster
  taudem-interim-files/d8/     -- TauDEM rasters + intermediate shapefiles
  taudem-interim-files/final/  -- Pass 3 TauDEM outputs
  delineation-product/         -- published outputs copied here after each pass
  points/                      -- pour point shapefiles produced by Python
  lakes/                       -- filtered reservoir polygons (filterLakes.py)
  merged_basins/               -- combiningBasins / cleanGeofabric / aggregation outputs

SETUP CHECKLIST (paths & knobs to edit when adapting this workflow)
--------------------------------------------------------------------
Work top-to-bottom. Most Python scripts assume you run them from HOME_DIR
(this slurm cd's there). Relative paths below are from HOME_DIR.

[ ] tau-dem-delineation-srun.slurm  (this file)
      HOME_DIR           -- project root on the cluster
      DEM                -- input elevation GeoTIFF
      VENV               -- Python venv activate script (geopandas, etc.)
      STREAM_THRESHOLD   -- TauDEM stream source area (cell count)
      FLOWPATH_NCORES    -- MPI ranks for rasterFlowpathEdit.py
      #SBATCH --account / --ntasks / --mem-per-cpu / --time

[ ] filterLakes.py
      HydroLAKES .shp path (hardcoded near top)
      Pass-1 watersheds/streams under delineation-product/
      MIN_AREA           -- km² lake-size filter
      output             -- lakes/filtered_lakes.shp

[ ] getGauges.py
      Hydat.sqlite3 path (db_path)
      Pass-1 watersheds under delineation-product/
      output             -- points/gauges_in_basin.shp

[ ] rasterFlowpathEdit.py  (defaults near __main__ / argparse)
      FDR / SRC / AD8 / watershed rasters under taudem-interim-files/d8/
      streams            -- delineation-product/original-delineated-streams.shp
      lakes / gauges     -- lakes/filtered_lakes.shp, points/gauges_in_basin.shp
      outlet_overrides.csv
      outputs            -- taudem-interim-files/d8/fdr_centerline_all.tif
                            points/selected_outlets.shp

[ ] pourPointsPass2.py  (paths dict in __main__)
      intermediate streams/watersheds under delineation-product/ and d8/
      fdr_centerline_all.tif, lakes, gauges
      outputs            -- points/pourPointsFinal.shp
                            points/reservoir_io_nodes.shp

[ ] combiningBasins.py
      PATHS{}            -- basins, streams, lakes, snapped outlets, gauges
      OVERRIDES_CSV      -- outlet_overrides.csv
      OUTPUT_DIR         -- merged_basins/
      GAUGE_SEARCH_RADIUS, MIN_INTERNAL_STREAM_LEN

[ ] cleanGeofabric.py  (paths in __main__)
      input              -- merged_basins/reservoir{Basins,Streams}.shp
      gauges             -- points/gauges_in_basin.shp
      output             -- merged_basins/reservoir{Basins,Streams}_final.shp

[ ] basinAggregation.py  (optional; globals at top of file)
      INPUT_BASINS / INPUT_RIVERS / OUTPUT_BASINS / OUTPUT_RIVERS
      MIN_SUB_AREA       -- km² aggregation threshold (default 100)
      MIN_RIV_SLOPE, MIN_RIV_LENGTH
      column-name globals if your attribute table differs from TauDEM

[ ] External / one-time inputs to stage before running
      dem/*.tif          -- study DEM
      HydroLAKES polys   -- for filterLakes.py
      Hydat.sqlite3      -- for getGauges.py
      outlet_overrides.csv -- optional manual lake-outlet overrides

=============================================================================
