"""
Study inputs — copy this file to study_settings.py and edit paths.

  cp study_settings.example.py study_settings.py

Place study_settings.py next to Delineation-Workflow.slurm (your study root).
Paths may be absolute or relative to that folder.
"""

from pathlib import Path

INPUT_DEM = Path("dem/your-dem.tif")
INPUT_HYDAT_DB = Path("Hydat.sqlite3")
INPUT_HYDROLAKES = Path("/path/to/HydroLAKES_polys_v10.shp")
