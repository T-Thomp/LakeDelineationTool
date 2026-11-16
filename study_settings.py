"""
Study inputs and run settings.

Edit the values below.

The workflow has three inputs: the DEM, the lake polygons, and the gauges
(one or more SQLite, CSV, and point files). Everything else — Pass 1 streams, the flow
direction raster, prep lakes, final basins — has a default path in
pipeline_paths.py under the study root.

Run a step with --settings. Add a file flag only when that one file should
come from somewhere else:

  python3 code/filterLakes.py --settings study_settings.py
  python3 code/conditionLakes.py --settings study_settings.py --fdr /other/fdr.tif

Put this file in the directory where you run sbatch, or next to
Delineation-Workflow.slurm. The job uses the one in the sbatch directory when
both exist. Paths below may be absolute, or relative to LAKE_DELINEATION_ROOT.
Leave a setting out and the pipeline keeps the default shown here.
INPUT_HYDROLAKES is still accepted as another name for INPUT_LAKES.

LAKE_DELINEATION_ROOT
    Folder that receives outputs/ and that relative paths below are based on.
    None uses the folder that contains this file. That is the study folder
    when you sbatch from the directory that holds this file.
    Set a path to keep this file with the code and write the study elsewhere:
        LAKE_DELINEATION_ROOT = Path("/project/your-study")
    A relative path is resolved from this file's folder.
    Exporting LAKE_DELINEATION_ROOT in the shell overrides this value.

INPUT_DEM
    Elevation GeoTIFF.

STREAM_THRESHOLD
    TauDEM stream-source area, in cells. The same value is used in all three passes.

MIN_SUB_AREA
    basinAggregation.py merges headwater subbasins smaller than this
    (local area, km²).

INPUT_LAKES
    Lake polygons GeoPandas can read (.shp, .gpkg, ...). HydroLAKES by default.

LAKE_COLUMNS
    Your column name for each value the workflow needs.
      id    integer lake identifier (HydroLAKES: Hylak_id)
      area  lake area in km² (HydroLAKES: Lake_area)
      type  classification column, or None when the file has no class column.
            HydroLAKES Lake_type is 1 (lake), 2 (reservoir), 3 (regulated).
    Field names must be 10 characters or fewer (shapefile limit).

LAKE_MIN_AREA_SQKM
    Keep lakes larger than this (km²). Compared with ">".

LAKE_KEEP_TYPES
    Values of the type column to keep. None applies the size limit only.
    Default (2, 3) keeps HydroLAKES reservoirs and regulated lakes at any size.
    Every other type (natural lakes, type 1) must exceed LAKE_MIN_AREA_SQKM.

LAKE_TYPES_MUST_MATCH
    False (default): listed types are kept at any size, and other types must
    exceed the size limit.
    True: keep only the listed types, and only when they also exceed the size limit.

INPUT_GAUGES
    Every gauge file for this study. Each item has a name, a path, and columns.
    The suffix chooses the reader, so a study can list several of each:
      .sqlite / .sqlite3 / .db   WSC HYDAT (active stations with discharge).
                                 columns are not used; HYDAT's own names are.
      .csv                       table of stations. latitude and longitude are
                                 WGS84.
      anything else              point file (.shp, .gpkg, ...). latitude and
                                 longitude are used only when the file has no points.
    columns:
      id         station id, written onward as STATION_NUMBER
      name       station name, written onward as STATION_NAME
      latitude   required for a CSV
      longitude  required for a CSV
    Leave columns out and those four HYDAT names are used.
"""

from pathlib import Path

LAKE_DELINEATION_ROOT = None

INPUT_DEM = Path("dem/your-dem.tif")
STREAM_THRESHOLD = 5555
MIN_SUB_AREA = 100.0

INPUT_LAKES = Path("/path/to/HydroLAKES_polys_v10.shp")
LAKE_COLUMNS = {
    "id": "Hylak_id",
    "area": "Lake_area",
    "type": "Lake_type",
}
LAKE_MIN_AREA_SQKM = 5.0
LAKE_KEEP_TYPES = (2, 3)
LAKE_TYPES_MUST_MATCH = False

INPUT_GAUGES = [
    {
        "name": "HYDAT",
        "path": Path("Hydat.sqlite3"),
    },
    # {"name": "provincial", "path": Path("provincial_gauges.shp"),
    #  "columns": {"id": "STN_ID", "name": "STN_NAME"}},
    # {"name": "extra", "path": Path("extra_gauges.csv"),
    #  "columns": {"id": "id", "name": "station", "latitude": "lat", "longitude": "lon"}},
]
