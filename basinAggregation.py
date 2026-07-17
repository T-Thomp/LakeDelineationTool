"""
Aggregate small sub-basins and stream reaches for TauDEM / cleanGeofabric outputs.

Adapted from basin_aggregation() in 01-pre-process-geospatial-fabric.ipynb.
Column names are configured as globals at the top so the logic is not tied to
MERIT-Hydro field names (COMID, NextDownID, etc.).

Expected inputs (defaults match cleanGeofabric.py outputs):
  merged_basins/reservoirBasins_final.shp
  merged_basins/reservoirStreams_final.shp
"""

from __future__ import annotations

import os
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd


# ==============================================================================
# COLUMN NAMES — edit these to match your shapefile attribute tables
# ==============================================================================
# Shared topology
BASIN_ID = "LINKNO"             # primary basin object id
RIVER_ID = "LINKNO"             # stream reach ID
NEXT_DOWN_ID = "DSLINKNO"       # downstream link / basin ID (-1 = outlet)

# Basin areas (km² after conversion; see AREA_SCALE)
UNIT_AREA: Optional[str] = None  # local sub-basin area; None -> polygon area
UP_AREA = "DSContArea"           # cumulative drainage area at pour point

# River hydraulics
SLOPE = "Slope"
LENGTH = "Length"                # reach length; converted with LENGTH_SCALE

# Masking / special units
LAKE_FLAG = "is_lake"            # >0 marks reservoir/lake sub-basins (not aggregated)
GAUGE_FLAG: Optional[str] = None  # numeric 0/1 column; None -> derive from GAUGE_IDS
GAUGE_IDS = "STATION_NU"         # gauge attribute only (not the basin object id)

# Basin attributes carried through to aggregated output (from pour-point basin)
LAKE_ID = "lake_id"
LAKE_AREA = "lake_area"          # m² (shapefile-safe name, ≤10 chars)
FRAC_LAKE_AREA = "frac_lake"     # fraction of lake polygon inside the basin

# Extra river attributes carried through to aggregated output
STREAM_ORDER = "strmOrder"
HILLSLOPE: Optional[str] = None  # not present in TauDEM; left out of output if None

# Unit conversions applied after loading shapefiles
AREA_SCALE = 1e-6                # m² -> km² for TauDEM DSContArea / USContArea
LENGTH_SCALE = 1e-3              # m -> km for TauDEM Length


# ==============================================================================
# INPUT / OUTPUT PATHS AND THRESHOLDS
# ==============================================================================
INPUT_BASINS = "merged_basins/reservoirBasins_final.shp"
INPUT_RIVERS = "merged_basins/reservoirStreams_final.shp"
OUTPUT_BASINS = "final_basin/aggregated_basins.shp"
OUTPUT_RIVERS = "final_basin/aggregated_rivers.shp"

MIN_SUB_AREA = 100.0          # km²
MIN_RIV_SLOPE = 0.0000001     # minimum accepted river slope (WATFLOOD manual)
MIN_RIV_LENGTH = 1.0          # km


# ==============================================================================
# HELPERS
# ==============================================================================
def _require_columns(gdf: gpd.GeoDataFrame, columns: list[str], label: str) -> None:
  missing = [col for col in columns if col not in gdf.columns]
  if missing:
    raise ValueError(f"Missing columns in {label}: {missing}")


def _area_km2(series: pd.Series, scale: float) -> pd.Series:
  return pd.to_numeric(series, errors="coerce").fillna(0.0) * scale


def _length_km(series: pd.Series, scale: float) -> pd.Series:
  return pd.to_numeric(series, errors="coerce").fillna(0.0) * scale


def _basin_attr_cols(basin: gpd.GeoDataFrame) -> list[str]:
  """Attribute columns to carry from the pour-point basin into aggregated output."""
  candidates = [
    GAUGE_IDS,
    LAKE_FLAG,
    LAKE_ID,
    LAKE_AREA,
    FRAC_LAKE_AREA,
  ]
  return [c for c in candidates if c in basin.columns]


def prepare_input_tables(
  input_basin: gpd.GeoDataFrame,
  input_river: gpd.GeoDataFrame,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  """
  Validate, merge stream attributes into basins, and add derived area / flag columns.
  """
  basin = input_basin.copy()
  river = input_river.copy()

  _require_columns(basin, [BASIN_ID, "geometry"], "basin layer")
  _require_columns(
    river,
    [RIVER_ID, NEXT_DOWN_ID, SLOPE, LENGTH, UP_AREA],
    "river layer",
  )

  basin[BASIN_ID] = basin[BASIN_ID].astype(int)
  river[RIVER_ID] = river[RIVER_ID].astype(int)
  river[NEXT_DOWN_ID] = river[NEXT_DOWN_ID].fillna(-1).astype(int)

  join_cols = [
    c
    for c in river.columns
    if (c not in basin.columns or c == RIVER_ID) and c != "geometry"
  ]
  basin = basin.merge(
    river[join_cols].rename(columns={RIVER_ID: BASIN_ID}),
    on=BASIN_ID,
    how="left",
    suffixes=("", "_riv"),
  )

  if UNIT_AREA and UNIT_AREA in basin.columns:
    basin["_unitarea"] = _area_km2(basin[UNIT_AREA], AREA_SCALE)
  elif UNIT_AREA and UNIT_AREA in river.columns:
    basin["_unitarea"] = _area_km2(basin[UNIT_AREA], AREA_SCALE)
  else:
    basin["_unitarea"] = basin.geometry.area * AREA_SCALE

  if UP_AREA in basin.columns:
    basin["_uparea"] = _area_km2(basin[UP_AREA], AREA_SCALE)
  else:
    raise ValueError(
      f"Upstream area column '{UP_AREA}' not found after basin/river merge."
    )

  if LAKE_FLAG in basin.columns:
    basin["_lake_cat"] = pd.to_numeric(basin[LAKE_FLAG], errors="coerce").fillna(0)
  else:
    basin["_lake_cat"] = 0
    basin[LAKE_FLAG] = 0

  if LAKE_ID not in basin.columns:
    basin[LAKE_ID] = -1
  else:
    basin[LAKE_ID] = pd.to_numeric(basin[LAKE_ID], errors="coerce").fillna(-1).astype(int)

  if LAKE_AREA not in basin.columns:
    basin[LAKE_AREA] = 0.0
  else:
    basin[LAKE_AREA] = pd.to_numeric(basin[LAKE_AREA], errors="coerce").fillna(0.0)

  if FRAC_LAKE_AREA not in basin.columns:
    basin[FRAC_LAKE_AREA] = 0.0
  else:
    basin[FRAC_LAKE_AREA] = pd.to_numeric(basin[FRAC_LAKE_AREA], errors="coerce").fillna(0.0)

  if GAUGE_FLAG and GAUGE_FLAG in basin.columns:
    basin["_has_gauge"] = pd.to_numeric(basin[GAUGE_FLAG], errors="coerce").fillna(0)
  elif GAUGE_IDS in basin.columns:
    basin["_has_gauge"] = basin[GAUGE_IDS].fillna("").astype(str).str.strip().ne("").astype(int)
  else:
    basin["_has_gauge"] = 0
    basin[GAUGE_IDS] = ""

  if GAUGE_IDS in basin.columns:
    basin[GAUGE_IDS] = basin[GAUGE_IDS].fillna("").astype(str)

  river["_lengthkm"] = _length_km(river[LENGTH], LENGTH_SCALE)
  river["_uparea"] = _area_km2(river[UP_AREA], AREA_SCALE)

  return basin, river


# ==============================================================================
# CORE AGGREGATION (logic preserved from 01-pre-process-geospatial-fabric.ipynb)
# ==============================================================================
def basin_aggregation(
  input_basin: gpd.GeoDataFrame,
  input_river: gpd.GeoDataFrame,
  min_sub_area: float,
  min_riv_slope: float,
  min_riv_length: float,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  """
  Aggregate basins and rivers based on drainage area, slope, and reservoir masking.

  Returns aggregated basin and river GeoDataFrames. Each basin is identified by
  BASIN_ID. Gauge IDs, lake flags/IDs, lake area, and fractional lake area are
  pour-point attributes only.
  """
  basin, river = prepare_input_tables(input_basin, input_river)

  id_col = BASIN_ID
  down_col = NEXT_DOWN_ID
  riv_id_col = RIVER_ID

  river[SLOPE] = river[SLOPE].clip(lower=min_riv_slope)
  river.loc[river[SLOPE] >= 1.0, SLOPE] = min_riv_slope
  river["_lengthkm"] = river["_lengthkm"].clip(lower=min_riv_length)

  basin["Mask"] = 0
  basin.loc[basin[down_col] <= 0, "Mask"] = 1
  basin.loc[basin["_has_gauge"] > 0, "Mask"] = 2
  basin.loc[basin["_lake_cat"] > 0, "Mask"] = 3
  basin["agg"] = basin[id_col]
  basin["aggdown"] = basin[down_col]

  agg_basin = basin[["agg", "aggdown", "_unitarea", "_uparea", "Mask"]].copy()
  agg_basin = agg_basin[
    ~(((agg_basin["aggdown"] <= 0) & (agg_basin["_uparea"] < min_sub_area)) | (agg_basin["Mask"] == 3))
  ]
  lake_subs = basin[basin["Mask"] == 3]["agg"]
  no_subbasin = len(basin)

  while True:
    headwaters = (
      ~agg_basin["agg"].isin(agg_basin["aggdown"])
      & (agg_basin["_unitarea"] < min_sub_area)
      & (agg_basin["Mask"] < 2)
    )
    small_subbasin = agg_basin[headwaters]
    small_subbasin = small_subbasin[~small_subbasin["aggdown"].isin(lake_subs)].sort_values(
      by="_uparea", ascending=False
    )
    if not small_subbasin.empty:
      small_subbasin = small_subbasin.rename(columns={"agg": "aggold", "aggdown": "agg"})
      xx = small_subbasin.merge(agg_basin[["agg", "aggdown"]], on="agg", how="left")
      for i in range(len(xx)):
        basin.loc[basin["agg"] == xx["aggold"].iloc[i], "aggdown"] = xx["aggdown"].iloc[i]
        basin.loc[basin["agg"] == xx["aggold"].iloc[i], "agg"] = xx["agg"].iloc[i]
      agg_basin = basin.drop(columns="geometry").groupby(["agg", "aggdown"], as_index=False).agg(
        {"_unitarea": "sum"}
      )
      agg_basin = agg_basin.rename(columns={"agg": id_col, "aggdown": down_col})
      agg_basin = agg_basin.merge(basin[[id_col, "_uparea", "Mask"]], on=id_col, how="left")
      agg_basin = agg_basin.rename(columns={id_col: "agg", down_col: "aggdown"})
      agg_basin = agg_basin[
        ~(((agg_basin["aggdown"] <= 0) & (agg_basin["_uparea"] < min_sub_area)) | (agg_basin["Mask"] == 3))
      ]

    condition = (
      agg_basin["agg"].isin(agg_basin["aggdown"])
      & (agg_basin["_unitarea"] < min_sub_area)
      & (agg_basin["Mask"] != 3)
    )
    small_subbasin = agg_basin[condition].sort_values(by="_uparea", ascending=False)
    if not small_subbasin.empty:
      for i in range(len(small_subbasin)):
        xx = basin[basin[id_col] == small_subbasin["agg"].iloc[i]].index[0]
        if basin.loc[basin["agg"] == basin.loc[xx, "agg"], "_unitarea"].sum() < min_sub_area:
          xy = basin[basin[down_col] == basin.loc[xx, id_col]].index
          if not xy.empty:
            xz = basin.loc[xy, "_uparea"].idxmax()
            if basin.loc[xz, "Mask"] < 2:
              zz = basin[basin["aggdown"] == basin.loc[xz, "agg"]].index
              basin.loc[basin["agg"] == basin.loc[xz, "agg"], "agg"] = basin.loc[xx, "agg"]
              basin.loc[basin["agg"] == basin.loc[xz, "agg"], "aggdown"] = basin.loc[xx, "aggdown"]
              if not zz.empty:
                basin.loc[zz, "aggdown"] = basin.loc[xx, "agg"]
      agg_basin = basin.drop(columns="geometry").groupby(["agg", "aggdown"], as_index=False).agg(
        {"_unitarea": "sum"}
      )
      agg_basin = agg_basin.rename(columns={"agg": id_col, "aggdown": down_col})
      agg_basin = agg_basin.merge(basin[[id_col, "_uparea", "Mask"]], on=id_col, how="left")
      agg_basin = agg_basin.rename(columns={id_col: "agg", down_col: "aggdown"})
      agg_basin = agg_basin[
        ~(((agg_basin["aggdown"] <= 0) & (agg_basin["_uparea"] < min_sub_area)) | (agg_basin["Mask"] == 3))
      ]

    if len(agg_basin[agg_basin["_unitarea"] < min_sub_area]) == no_subbasin:
      break
    no_subbasin = len(agg_basin[agg_basin["_unitarea"] < min_sub_area])

  agg_basin = basin.dissolve(by="agg", aggfunc={"_unitarea": "sum"}, as_index=False).rename(
    columns={"agg": id_col}
  )

  # Carry pour-point attributes; object id remains BASIN_ID
  keep_cols = [id_col, "aggdown", "_uparea"] + _basin_attr_cols(basin)
  keep_cols = list(dict.fromkeys(keep_cols))
  agg_basin = agg_basin.merge(
    basin[keep_cols].copy(),
    on=id_col,
    how="left",
  ).rename(columns={"aggdown": down_col})

  if id_col == riv_id_col:
    agg_river = river.merge(basin[[id_col, "agg"]].copy(), on=riv_id_col, how="left")
  else:
    agg_river = river.merge(
      basin[[id_col, "agg"]].copy(),
      left_on=riv_id_col,
      right_on=id_col,
      how="left",
    )
  agg_river["mask"] = 0
  for agg_id in agg_river["agg"].dropna().unique():
    xx = agg_river.index[agg_river["agg"] == agg_id].tolist()
    while True:
      yy = agg_river.loc[xx, "_uparea"].idxmax()
      agg_river.at[yy, "mask"] = 1
      downstream = agg_river.index[
        agg_river[down_col] == agg_river.loc[yy, riv_id_col]
      ].tolist()
      if len(downstream) < 1:
        break
      xx = downstream

  agg_river = agg_river[agg_river["mask"] == 1].copy()
  agg_river["_slope_weighted"] = agg_river[SLOPE] * agg_river["_lengthkm"]

  agg_river = agg_river.dissolve(
    by="agg",
    aggfunc={"_lengthkm": "sum", "_slope_weighted": "sum"},
    as_index=False,
  ).rename(columns={"agg": riv_id_col})

  agg_river[SLOPE] = agg_river["_slope_weighted"] / agg_river["_lengthkm"].replace(0, np.nan)
  basin_topo = agg_basin[[id_col, down_col, "_uparea"]].copy()
  if id_col != riv_id_col:
    basin_topo = basin_topo.rename(columns={id_col: riv_id_col})
  agg_river = agg_river.merge(basin_topo, on=riv_id_col, how="left")

  extra_river_cols = [riv_id_col]
  if STREAM_ORDER in river.columns:
    extra_river_cols.append(STREAM_ORDER)
  if HILLSLOPE and HILLSLOPE in river.columns:
    extra_river_cols.append(HILLSLOPE)
  agg_river = agg_river.merge(river[extra_river_cols].copy(), on=riv_id_col, how="left")

  unit_out = UNIT_AREA or "unit_area_km2"
  agg_basin = agg_basin.rename(columns={"_unitarea": unit_out, "_uparea": UP_AREA})
  if UNIT_AREA and AREA_SCALE != 1.0:
    agg_basin[unit_out] = agg_basin[unit_out] / AREA_SCALE
  if AREA_SCALE != 1.0:
    agg_basin[UP_AREA] = agg_basin[UP_AREA] / AREA_SCALE

  agg_river[LENGTH] = agg_river["_lengthkm"] / LENGTH_SCALE
  agg_river[UP_AREA] = agg_river["_uparea"] / AREA_SCALE
  drop_riv = ["_lengthkm", "_uparea", "_slope_weighted", "mask"]
  if id_col != riv_id_col and id_col in agg_river.columns:
    drop_riv.append(id_col)
  agg_river = agg_river.drop(columns=drop_riv, errors="ignore")

  agg_basin[id_col] = agg_basin[id_col].astype("int64")
  agg_basin[down_col] = agg_basin[down_col].astype("int64")
  agg_river[riv_id_col] = agg_river[riv_id_col].astype("int64")
  agg_river[down_col] = agg_river[down_col].astype("int64")

  return agg_basin, agg_river


def run_aggregation(
  basins_path: str = INPUT_BASINS,
  rivers_path: str = INPUT_RIVERS,
  output_basins_path: str = OUTPUT_BASINS,
  output_rivers_path: str = OUTPUT_RIVERS,
  min_sub_area: float = MIN_SUB_AREA,
  min_riv_slope: float = MIN_RIV_SLOPE,
  min_riv_length: float = MIN_RIV_LENGTH,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  """Load cleanGeofabric outputs, aggregate, and write shapefiles."""
  print(f"Loading basins: {basins_path}")
  basins = gpd.read_file(basins_path)
  print(f"Loading rivers: {rivers_path}")
  rivers = gpd.read_file(rivers_path)

  agg_basins, agg_rivers = basin_aggregation(
    basins,
    rivers,
    min_sub_area,
    min_riv_slope,
    min_riv_length,
  )

  os.makedirs(os.path.dirname(output_basins_path) or ".", exist_ok=True)
  os.makedirs(os.path.dirname(output_rivers_path) or ".", exist_ok=True)

  print(f"Writing aggregated basins ({len(agg_basins)} features): {output_basins_path}")
  agg_basins.to_file(output_basins_path, driver="ESRI Shapefile")
  print(f"Writing aggregated rivers ({len(agg_rivers)} features): {output_rivers_path}")
  agg_rivers.to_file(output_rivers_path, driver="ESRI Shapefile")

  return agg_basins, agg_rivers


if __name__ == "__main__":
  run_aggregation()
