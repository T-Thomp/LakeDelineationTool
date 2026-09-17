"""
Aggregate small sub-basins and stream reaches for TauDEM / cleanGeofabric outputs.

Expected inputs (defaults match cleanGeofabric.py outputs):
  outputs/final/basins.shp
  outputs/final/streams.shp


"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Hashable, Optional

import geopandas as gpd
import numpy as np
import pandas as pd

from pipeline_paths import (
    FINAL_BASINS,
    FINAL_BASINS_AGG,
    FINAL_STREAMS,
    FINAL_STREAMS_AGG,
    WORKING,
    ensure_output_dirs,
)


# ==============================================================================
# COLUMN NAMES — legacy TauDEM/MESH fields on disk; canonical HY_Features names
# are added by hy_features.enrich. See docs/hy_features_mapping.md.
# ==============================================================================
from hy_features.schema import (
    FRAC_LAKE,
    LEGACY_BASIN_ID,
    LEGACY_FLOWPATH_ID,
    LEGACY_GAUGE_IDS,
    LEGACY_IS_LAKE,
    LEGACY_LAKE_AREA,
    LEGACY_LAKE_ID,
    LEGACY_LOWER_ID,
)

BASIN_ID = LEGACY_BASIN_ID
RIVER_ID = LEGACY_FLOWPATH_ID
NEXT_DOWN_ID = LEGACY_LOWER_ID
GAUGE_IDS = LEGACY_GAUGE_IDS
LAKE_FLAG = LEGACY_IS_LAKE
LAKE_ID = LEGACY_LAKE_ID
LAKE_AREA = LEGACY_LAKE_AREA
FRAC_LAKE_AREA = FRAC_LAKE

# Basin areas — see module docstring "Area columns"
#
# UNIT_AREA: optional shapefile column for *local* subbasin area (one polygon only).
#   None (default) — compute from basin polygon geometry (recommended for this pipeline).
#   "SomeCol"      — read from basins, or from rivers if joined by DN/LINKNO.
#   Values are multiplied by AREA_SCALE (1e-6) so m² fields become km² for MIN_SUB_AREA.
#   After aggregation, summed local areas are written as area_km2 (or UNIT_AREA name).
#
# UP_AREA (DSContArea): TauDEM *cumulative* drainage area at each pour point (m² on disk).
#   Not used for the "too small to keep" test — only for outlet / mask logic.
UNIT_AREA: Optional[str] = None
UP_AREA = "DSContArea"

# River hydraulics
SLOPE = "Slope"
LENGTH = "Length"                # reach length; converted with LENGTH_SCALE
STRM_DROP = "strmDrop"           # TauDEM elevation drop along reach (m); sum on merge

# Masking / special units
GAUGE_FLAG: Optional[str] = None  # numeric 0/1 column; None -> derive from GAUGE_IDS

# Extra river attributes carried through to aggregated output
STREAM_ORDER = "strmOrder"
HILLSLOPE: Optional[str] = None  # not present in TauDEM; left out of output if None

# Unit conversions applied after loading shapefiles
AREA_SCALE = 1e-6                # m² -> km² for TauDEM DSContArea / USContArea
LENGTH_SCALE = 1e-3              # m -> km for TauDEM Length


# ==============================================================================
# INPUT / OUTPUT PATHS AND THRESHOLDS
# ==============================================================================
INPUT_BASINS = str(FINAL_BASINS)
INPUT_RIVERS = str(FINAL_STREAMS)
OUTPUT_BASINS = str(FINAL_BASINS_AGG)
OUTPUT_RIVERS = str(FINAL_STREAMS_AGG)

MIN_SUB_AREA = 100.0          # km² — minimum local area (_unitarea) to keep a unit separate; smaller
                              # units may be absorbed. Merged aggregates may be much larger than this.
MIN_RIV_SLOPE = 0.0000001     # minimum accepted river slope (WATFLOOD manual)
MIN_RIV_LENGTH = 1.0          # km

# Final linear main-stem series merge (after headwater pass). Fractions of ``MIN_SUB_AREA``.
LINEAR_SERIES_MERGE_ENABLED = True
LINEAR_SERIES_MERGE_HALF_MIN_FRAC = 0.5   # either agg below this × min → merge (ignores combined cap)
LINEAR_SERIES_MERGE_MAX_COMBINED_FRAC = 2.0  # when both ≥ half-min, merge only if sum < this × min
# When True, skip area caps only (gauge/lake/post-lake barriers always apply).
LINEAR_MERGE_SKIP_AREA_RULES = False
# Per-round linear scan/absorb logging (very slow on large networks if True).
LINEAR_MERGE_VERBOSE = False

# Sentinel written to DSLINKNO for the most-downstream basin(s).
# Match this to MESH outlet_value (e.g. -9999).
OUTLET_VALUE = -9999


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


def _basin_pour_through_cols(basin: gpd.GeoDataFrame, id_col: str) -> list[str]:
  """TauDEM / geofabric basin columns to keep on aggregated pour rows (non-internal)."""
  skip = {
    "geometry",
    "agg",
    "aggdown",
    "Mask",
    id_col,
    "_unitarea",
    "_uparea",
    "_lake_cat",
    "_has_gauge",
  }
  return [c for c in basin.columns if c not in skip and not str(c).startswith("_")]


def _stream_dissolve_aggfunc(
  gdf: gpd.GeoDataFrame,
  *,
  riv_id_col: str = RIVER_ID,
  down_col: str = NEXT_DOWN_ID,
) -> dict[str, str]:
  """
  combiningBasins / cleanGeofabric-style dissolve: ``sum`` Length and strmDrop;
  ``max`` on other numeric TauDEM fields; ``first`` on remaining columns.
  ``Slope`` is recomputed after dissolve (not aggregated).

  See ``combiningBasins.build_stream_agg_logic`` and ``cleanGeofabric.bypass_phantom_streams``.
  """
  sum_cols = {LENGTH, "_lengthkm", "Length", STRM_DROP}
  skip = {
    "geometry",
    "agg",
    "mask",
    "_slope_weighted",
    SLOPE,
    riv_id_col,
    down_col,
  }
  logic: dict[str, str] = {}
  for col in gdf.columns:
    if col in skip:
      continue
    if col in sum_cols:
      logic[col] = "sum"
    elif pd.api.types.is_numeric_dtype(gdf[col]):
      logic[col] = "max"
    else:
      logic[col] = "first"
  return logic


def _internal_basin_columns(id_col: str, down_col: str) -> set[str]:
  return {
    "geometry",
    "agg",
    "aggdown",
    "Mask",
    id_col,
    down_col,
    "_unitarea",
    "_uparea",
    "_lake_cat",
    "_has_gauge",
  }


def _basin_columns_for_output(
  basin: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  original_basin_columns: list[str],
) -> list[str]:
  """All non-internal basin / TauDEM columns to attach to each aggregated polygon."""
  from_input = {
    c
    for c in original_basin_columns
    if c not in _internal_basin_columns(id_col, down_col) and c != "geometry"
  }
  from_table = set(_basin_pour_through_cols(basin, id_col))
  ordered = list(dict.fromkeys(_basin_attr_cols(basin) + list(from_input) + list(from_table)))
  skip = _internal_basin_columns(id_col, down_col) | {UP_AREA}
  if UNIT_AREA:
    skip.add(UNIT_AREA)
  return [c for c in ordered if c in basin.columns and c not in skip]


def _deduplicate_geodataframe_columns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
  """Shapefile export breaks when duplicate labels (e.g. two ``DSContArea`` columns)."""
  if gdf.columns.is_unique:
    return gdf
  return gdf.loc[:, ~gdf.columns.duplicated()].copy()


def _slope_from_strm_drop_and_length(
  length_m: pd.Series,
  strm_drop: pd.Series,
  min_riv_slope: float,
) -> pd.Series:
  """``Slope = strmDrop / Length`` (cleanGeofabric / combiningBasins convention)."""
  length_m = pd.to_numeric(length_m, errors="coerce")
  strm_drop = pd.to_numeric(strm_drop, errors="coerce").fillna(0.0)
  slope = strm_drop / length_m.replace(0, np.nan)
  slope = slope.fillna(min_riv_slope).clip(lower=min_riv_slope)
  slope = slope.mask(slope >= 1.0, min_riv_slope)
  return slope


def _one_row_per_agg(
  basin: gpd.GeoDataFrame,
  id_col: str,
  cols: list[str],
) -> pd.DataFrame:
  """
  One attribute row per aggregate id.

  Prefer the pour-point row (DN == agg); fall back to any row in the group when
  the survivor id is not present as a basin DN (common after headwater merges).
  """
  available = ["agg"] + [c for c in cols if c in basin.columns and c != "agg"]
  pour = basin.loc[basin[id_col] == basin["agg"], available].drop_duplicates(subset=["agg"])
  missing = set(basin["agg"].unique()) - set(pour["agg"])
  if missing:
    fallback = basin.loc[basin["agg"].isin(missing), available].drop_duplicates(subset=["agg"])
    pour = pd.concat([pour, fallback], ignore_index=True)
  return pour


def _is_outlet_id(down_id: object, outlet_value: int) -> bool:
  """Return True for outlet sentinels (configured value, or legacy <= 0)."""
  try:
    down = int(down_id)
  except (TypeError, ValueError):
    return True
  return down == int(outlet_value) or down <= 0


def _is_sentinel_object_id(obj_id: object, outlet_value: int) -> bool:
  """True if obj_id cannot be a basin/reach identifier (sentinel or negative)."""
  try:
    val = int(obj_id)
  except (TypeError, ValueError):
    return True
  return val == int(outlet_value) or val < 0


def _remap_aggdown_to_survivors(
  basin: gpd.GeoDataFrame,
  id_col: str,
  agg_col: str = "agg",
  aggdown_col: str = "aggdown",
  outlet_value: int = OUTLET_VALUE,
) -> gpd.GeoDataFrame:
  """
  Rewrite ``aggdown`` so every link targets a surviving aggregate id.

  Small basins are absorbed into a downstream ``agg`` id during aggregation, but
  lakes and other protected units can keep a stale ``aggdown`` that still points
  at the absorbed (now missing) id. Map each original id to its final ``agg`` and
  resolve ``aggdown`` through that map. Terminal / self-draining links become
  ``outlet_value``.
  """
  outlet_value = int(outlet_value)
  id_to_agg = {
    int(orig): int(agg)
    for orig, agg in zip(basin[id_col].to_numpy(), basin[agg_col].to_numpy())
  }
  survivors = set(id_to_agg.values())

  def remap_one(down_id: object, self_agg: int) -> int:
    try:
      down = int(down_id)
    except (TypeError, ValueError):
      return outlet_value
    if _is_outlet_id(down, outlet_value):
      return outlet_value

    seen: set[int] = set()
    while down not in survivors:
      if down not in id_to_agg or down in seen:
        # Leaves the aggregated domain — treat as outlet.
        return outlet_value
      seen.add(down)
      down = id_to_agg[down]

    if down == self_agg:
      return outlet_value
    return down

  out = basin.copy()
  out[aggdown_col] = [
    remap_one(down, int(self_agg))
    for down, self_agg in zip(out[aggdown_col].to_numpy(), out[agg_col].to_numpy())
  ]
  return out


def _validate_topology(
  basins: gpd.GeoDataFrame,
  rivers: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  outlet_value: int = OUTLET_VALUE,
) -> None:
  """Raise when any DSLINKNO points at a missing LINKNO / basin id."""
  basin_ids = set(basins[id_col].astype(int))
  river_ids = set(rivers[id_col].astype(int)) if id_col in rivers.columns else set()
  ids = basin_ids | river_ids
  outlet_value = int(outlet_value)

  def dangling(gdf: gpd.GeoDataFrame) -> list[int]:
    downs = set(int(d) for d in gdf[down_col].to_numpy())
    return sorted(
      d for d in downs
      if d not in ids and not _is_outlet_id(d, outlet_value)
    )

  bad_b = dangling(basins)
  bad_r = dangling(rivers)
  if bad_b or bad_r:
    raise ValueError(
      "Aggregated topology has DSLINKNO values with no matching LINKNO: "
      f"basins={bad_b[:10]}{'...' if len(bad_b) > 10 else ''}, "
      f"rivers={bad_r[:10]}{'...' if len(bad_r) > 10 else ''}. "
      "Downstream links were not fully remapped onto surviving aggregates."
    )


def _downstream_basin_ids_of_lakes(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  outlet_value: int = OUTLET_VALUE,
) -> set[int]:
  """LINKNO/DN ids for basins immediately downstream of a lake outlet link."""
  lake_ids = set(basin.loc[basin["_lake_cat"] > 0, id_col].astype(int))
  if not lake_ids:
    return set()

  protected: set[int] = set()
  link_series = river[riv_id_col].astype(int)
  for lake_id in lake_ids:
    down_rows = river.loc[link_series == lake_id, down_col]
    if down_rows.empty:
      continue
    down_id = down_rows.iloc[0]
    if _is_outlet_id(down_id, outlet_value):
      continue
    protected.add(int(down_id))
  return protected


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
  river[NEXT_DOWN_ID] = river[NEXT_DOWN_ID].fillna(OUTLET_VALUE).astype(int)

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
  if NEXT_DOWN_ID in basin.columns:
    basin[NEXT_DOWN_ID] = (
      pd.to_numeric(basin[NEXT_DOWN_ID], errors="coerce")
      .fillna(OUTLET_VALUE)
      .astype(int)
    )

  # Local subbasin area (km²) used for MIN_SUB_AREA merge decisions.
  if UNIT_AREA and UNIT_AREA in basin.columns:
    basin["_unitarea"] = _area_km2(basin[UNIT_AREA], AREA_SCALE)
  elif UNIT_AREA and UNIT_AREA in river.columns:
    basin["_unitarea"] = _area_km2(basin[UNIT_AREA], AREA_SCALE)
  else:
    basin["_unitarea"] = basin.geometry.area * AREA_SCALE

  # Cumulative upstream area at pour point (TauDEM DSContArea); separate from local area.
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


TOPOLOGY_CYCLES_SHP = WORKING / "aggregation_topology_cycles.shp"


def _warn_river_cycle(
    agg_id,
    cycle_links: list[int],
    agg_river: gpd.GeoDataFrame,
    down_col: str,
    riv_id_col: str,
) -> list[dict]:
    """
    Print a GIS-friendly warning for a cyclic DSLINKNO walk and return shapefile rows.

    Each output row is one stream link in the cycle (use LINKNO / aggregate_id to
    select in QGIS alongside outputs/final/streams.shp).
    """
    cycle_key = " -> ".join(str(link) for link in cycle_links)
    print("WARNING: cyclic DSLINKNO chain during river main-stem trace")
    print(f"  aggregate_id (agg): {agg_id}")
    print(f"  LINKNO cycle: {cycle_key}")
    print("  Links (select by LINKNO in streams.shp):")
    features: list[dict] = []
    for link_no in cycle_links[:-1]:
        rows = agg_river[agg_river[riv_id_col].astype(int) == int(link_no)]
        if rows.empty:
            print(f"    LINKNO {link_no}: (not found in river layer)")
            continue
        row = rows.iloc[0]
        ds_link = int(row[down_col]) if pd.notna(row[down_col]) else OUTLET_VALUE
        geom = row.geometry
        if geom is not None and not geom.is_empty:
            mid = geom.interpolate(0.5, normalized=True)
            print(
                f"    LINKNO {link_no} -> DSLINKNO {ds_link}  "
                f"midpoint ({mid.x:.2f}, {mid.y:.2f})"
            )
        else:
            print(f"    LINKNO {link_no} -> DSLINKNO {ds_link}")
        features.append({
            "aggregate_id": int(agg_id) if pd.notna(agg_id) else -1,
            "link_no": int(link_no),
            "ds_link_no": ds_link,
            "cycle_links": cycle_key,
            "geometry": geom,
        })
    print(f"  Cycle links also written to: {TOPOLOGY_CYCLES_SHP}")
    return features


def _mark_main_stem_upstream_chain(
  agg_river: gpd.GeoDataFrame,
  seed_idx: Hashable,
  riv_id_col: str,
  upstream_by_node: dict[int, list[int]],
) -> None:
  """Mark sole-upstream reaches above ``seed_idx`` (linear channel into the stem)."""
  link = int(agg_river.loc[seed_idx, riv_id_col])
  while True:
    ups = upstream_by_node.get(link, [])
    if len(ups) != 1:
      break
    up = int(ups[0])
    hit = agg_river.index[agg_river[riv_id_col].astype(int) == up]
    if hit.empty:
      break
    for idx in hit:
      agg_river.at[idx, "mask"] = 1
    link = up


def _mark_river_main_stems(
    agg_river: gpd.GeoDataFrame,
    down_col: str,
    riv_id_col: str,
    upstream_by_node: dict[int, list[int]] | None = None,
) -> tuple[gpd.GeoDataFrame, list[dict]]:
    """Pick highest-uparea main stem per aggregate; stop and warn on DSLINKNO cycles."""
    agg_river = agg_river.copy()
    agg_river["mask"] = 0
    cycle_features: list[dict] = []
    reported_cycles: set[tuple[int, ...]] = set()
    up_map = upstream_by_node or {}

    for agg_id in agg_river["agg"].dropna().unique():
        xx = agg_river.index[agg_river["agg"] == agg_id].tolist()
        visited_order: list[int] = []
        visited_set: set[int] = set()

        while xx:
            yy = agg_river.loc[xx, "_uparea"].idxmax()
            if up_map:
              _mark_main_stem_upstream_chain(agg_river, yy, riv_id_col, up_map)
            link_id = int(agg_river.loc[yy, riv_id_col])
            if link_id in visited_set:
                cycle_start = visited_order.index(link_id)
                cycle_links = visited_order[cycle_start:] + [link_id]
                cycle_tuple = tuple(cycle_links)
                if cycle_tuple not in reported_cycles:
                    reported_cycles.add(cycle_tuple)
                    cycle_features.extend(
                        _warn_river_cycle(
                            agg_id, cycle_links, agg_river, down_col, riv_id_col
                        )
                    )
                break
            visited_set.add(link_id)
            visited_order.append(link_id)
            agg_river.at[yy, "mask"] = 1
            downstream = agg_river.index[
                agg_river[down_col] == agg_river.loc[yy, riv_id_col]
            ].tolist()
            if not downstream:
                break
            xx = downstream

    return agg_river, cycle_features


def _export_topology_cycles(cycle_features: list[dict], crs) -> None:
    if not cycle_features:
        return
    TOPOLOGY_CYCLES_SHP.parent.mkdir(parents=True, exist_ok=True)
    gpd.GeoDataFrame(cycle_features, crs=crs).to_file(TOPOLOGY_CYCLES_SHP)
    print(f"Wrote {len(cycle_features)} cycle link feature(s) to {TOPOLOGY_CYCLES_SHP}")


# ==============================================================================
# INDEXED LOOKUPS — same writes as basin.loc[basin[col] == key]
# ==============================================================================
def _agg_index_by_agg_id(basin: gpd.GeoDataFrame) -> dict[int, list[Hashable]]:
  """Row labels grouped by ``agg`` (int keys for reliable absorb-table lookup)."""
  out: dict[int, list[Hashable]] = defaultdict(list)
  for lab, val in basin["agg"].items():
    try:
      out[int(val)].append(lab)
    except (TypeError, ValueError):
      continue
  return out


def _agg_id_series(series: pd.Series) -> pd.Series:
  """Nullable integer ids for consistent pandas merges (object vs int64)."""
  return pd.to_numeric(series, errors="coerce").astype("Int64")


def _gauge_pour_link_ids(
  basin: gpd.GeoDataFrame,
  id_col: str,
) -> set[int]:
  """Pour-point link ids for gauge units (Mask and/or station id columns)."""
  out: set[int] = set()
  if "Mask" in basin.columns:
    out.update(
      int(x)
      for x in basin.loc[basin["Mask"] == 2, id_col].astype(int).to_numpy()
    )
  if "_has_gauge" in basin.columns:
    out.update(
      int(x)
      for x in basin.loc[basin["_has_gauge"] > 0, id_col].astype(int).to_numpy()
    )
  return out


def _upstream_links_by_down_node(
  river: gpd.GeoDataFrame,
  down_col: str,
  riv_id_col: str,
) -> dict[int, list[int]]:
  out: dict[int, list[int]] = defaultdict(list)
  for link, down in zip(river[riv_id_col].to_numpy(), river[down_col].to_numpy()):
    try:
      out[int(down)].append(int(link))
    except (TypeError, ValueError):
      continue
  return dict(out)


def _headwater_link_ids(
  river: gpd.GeoDataFrame,
  riv_id_col: str,
  upstream_by_node: dict[int, list[int]],
) -> set[int]:
  """Reach ids with no upstream link on the river network (network tips)."""
  receives_inflow = set(upstream_by_node.keys())
  out: set[int] = set()
  for val in river[riv_id_col].to_numpy():
    try:
      lid = int(val)
    except (TypeError, ValueError):
      continue
    if lid not in receives_inflow:
      out.add(lid)
  return out


def _link_depth_from_headwaters(
  upstream_by_node: dict[int, list[int]],
  headwater_links: set[int],
) -> dict[int, int]:
  """
  Downstream step count from the nearest network tip (0 = headwater link).

  Propagates along the vector network so merges can be ordered upstream → DS.
  """
  depth: dict[int, int] = {int(hw): 0 for hw in headwater_links}
  if not upstream_by_node:
    return depth
  changed = True
  while changed:
    changed = False
    for down, ups in upstream_by_node.items():
      down_i = int(down)
      try:
        d = max(depth.get(int(u), 0) for u in ups) + 1
      except ValueError:
        continue
      if depth.get(down_i, -1) < d:
        depth[down_i] = d
        changed = True
  return depth


def _agg_min_link_depth(
  basin: gpd.GeoDataFrame,
  id_col: str,
  agg_id: int,
  link_depth: dict[int, int],
) -> int:
  pours = basin.loc[basin["agg"].astype(int) == int(agg_id), id_col].astype(int)
  if pours.empty:
    return 10**9
  return min(link_depth.get(int(p), 10**9) for p in pours)


def _pour_agg_by_link(
  basin: gpd.GeoDataFrame,
  id_col: str,
) -> dict[int, int]:
  out: dict[int, int] = {}
  for orig, agg in zip(
    basin[id_col].astype(int).to_numpy(),
    basin["agg"].astype(int).to_numpy(),
  ):
    o = int(orig)
    if o not in out:
      out[o] = int(agg)
  return out


def _id_to_agg_map(
  basin: gpd.GeoDataFrame,
  id_col: str,
) -> dict[int, int]:
  return {
    int(orig): int(agg)
    for orig, agg in zip(basin[id_col].to_numpy(), basin["agg"].to_numpy())
  }


def _link_is_continuing_river(link_id: int, upstream_by_node: dict[int, list[int]]) -> bool:
  """True if at least one other reach drains into ``link_id`` (not a network tip)."""
  return int(link_id) in upstream_by_node


def _junction_two_inflow_both_continuing(
  upstream_by_node: dict[int, list[int]],
  node_id: int,
) -> bool:
  """
  At a 2-inflow junction, True when both incoming reaches are continuing rivers.

  Matches the legacy two-way rule: do not fold either arm into the other here.
  """
  ups = upstream_by_node.get(int(node_id), [])
  if len(ups) != 2:
    return False
  try:
    u1, u2 = int(ups[0]), int(ups[1])
  except (TypeError, ValueError):
    return False
  return _link_is_continuing_river(u1, upstream_by_node) and _link_is_continuing_river(
    u2, upstream_by_node
  )


def _agg_survivor_pour_link(
  basin: gpd.GeoDataFrame,
  id_col: str,
  agg_id: int,
) -> int | None:
  """Outlet pour (``DN == agg``) when present, else any pour in the aggregate."""
  survivor = basin.loc[
    basin[id_col].astype(int) == basin["agg"].astype(int),
    [id_col, "agg"],
  ]
  survivor = survivor.loc[survivor["agg"].astype(int) == int(agg_id), id_col]
  if not survivor.empty:
    return int(survivor.iloc[0])
  fallback = basin.loc[basin["agg"].astype(int) == int(agg_id), id_col]
  if fallback.empty:
    return None
  return int(fallback.iloc[0])


def _pour_immediate_downstream_link(
  basin: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  link_id: int,
  outlet_value: int,
) -> int | None:
  """One basin/river hop downstream from pour ``link_id`` (``DSLINKNO``)."""
  rows = basin.loc[basin[id_col].astype(int) == int(link_id), down_col]
  if rows.empty:
    return None
  raw = rows.iloc[0]
  if _is_outlet_id(raw, outlet_value):
    return None
  try:
    return int(raw)
  except (TypeError, ValueError):
    return None


def _immediate_downstream_link(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  link_id: int,
  outlet_value: int,
) -> int | None:
  """Next downstream node from ``link_id`` (river ``LINKNO`` row, else basin pour)."""
  rows = river.loc[river[riv_id_col].astype(int) == int(link_id)]
  if not rows.empty:
    raw = rows.iloc[0][down_col]
    if not _is_outlet_id(raw, outlet_value):
      try:
        return int(raw)
      except (TypeError, ValueError):
        pass
  return _pour_immediate_downstream_link(
    basin, id_col, down_col, link_id, outlet_value
  )


def _headwater_confluence_target_pour_link(
  u_link: int,
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  upstream_by_node: dict[int, list[int]],
  lake_subs: set[int],
  outlet_value: int,
) -> int | None:
  """
  First **confluence** pour downstream of ``u_link`` on the river.

  Headwater folds happen only here (shared immediate downstream segment with
  another reach). Linear stems with no confluence return ``None`` (no headwater
  merge on that branch).
  """
  outlet_value = int(outlet_value)
  current = int(u_link)
  seen: set[int] = set()
  for _ in range(len(river) + len(basin) + 1):
    if current in seen:
      return None
    seen.add(current)
    ds = _immediate_downstream_link(
      basin, river, id_col, down_col, riv_id_col, current, outlet_value
    )
    if ds is None:
      return None
    if _link_is_lake_pour(basin, id_col, ds, lake_subs):
      return None
    ups = upstream_by_node.get(int(ds), [])
    if len(ups) >= 2:
      return int(ds)
    if len(ups) == 1 and int(ups[0]) == current:
      current = int(ds)
      continue
    return None
  return None


def _headwater_neighbor_upstream_agg_at_confluence(
  ds_link: int,
  aggold: int,
  upstream_by_node: dict[int, list[int]],
  pour_agg: dict[int, int],
) -> int | None:
  """
  At a confluence ``ds_link``, aggregate on the **other** upstream branch from
  ``aggold`` (e.g. B → A when A and B both enter C, not B → C).
  """
  ds_link = int(ds_link)
  aggold = int(aggold)
  ups = upstream_by_node.get(ds_link, [])
  if len(ups) < 2:
    return None
  neighbor_aggs: set[int] = set()
  for u in ups:
    a = pour_agg.get(int(u))
    if a is None:
      return None
    a = int(a)
    if a == aggold:
      continue
    neighbor_aggs.add(a)
  if len(neighbor_aggs) != 1:
    return None
  return next(iter(neighbor_aggs))


def _headwater_larger_basin_merges_into_smaller(
  basin: gpd.GeoDataFrame,
  aggold: int,
  target_agg: int,
) -> bool:
  """
  True when ``aggold`` should absorb into ``target_agg`` at a double-headwater
  confluence: larger local basin area → smaller; equal area → higher agg id → lower.
  """
  area_src = _agg_group_unit_area(basin, int(aggold))
  area_tgt = _agg_group_unit_area(basin, int(target_agg))
  if area_src > area_tgt:
    return True
  if area_src < area_tgt:
    return False
  return int(aggold) > int(target_agg)


def _build_aggregate_series_edges(
  basin: gpd.GeoDataFrame,
  id_col: str,
  upstream_by_node: dict[int, list[int]],
  pour_agg: dict[int, int] | None = None,
) -> list[tuple[int, int, int]]:
  """
  Merge topology after basin ``agg`` updates: edges ``(agg_u, agg_d, ds_link)`` where
  ``agg_d`` has exactly one distinct upstream aggregate on the river graph.
  """
  if pour_agg is None:
    pour_agg = _pour_agg_by_link(basin, id_col)
  edges: list[tuple[int, int, int]] = []
  seen: set[tuple[int, int]] = set()
  for ds_link, ups in upstream_by_node.items():
    if not ups:
      continue
    agg_d = _agg_at_pour_link(basin, id_col, int(ds_link), pour_agg)
    if agg_d is None:
      continue
    sole = _sole_upstream_aggregate_at_river_node(
      int(ds_link), upstream_by_node, pour_agg
    )
    if sole is None:
      continue
    agg_u, _u = sole
    agg_u, agg_d = int(agg_u), int(agg_d)
    if agg_u == agg_d:
      continue
    key = (agg_u, agg_d)
    if key in seen:
      continue
    seen.add(key)
    edges.append((agg_u, agg_d, int(ds_link)))
  return edges


def _agg_at_pour_link(
  basin: gpd.GeoDataFrame,
  id_col: str,
  link_id: int,
  pour_agg: dict[int, int],
) -> int | None:
  """Aggregate id at pour ``link_id`` (no downstream chain through other links)."""
  link_id = int(link_id)
  hit = pour_agg.get(link_id)
  if hit is not None:
    return int(hit)
  rows = basin.loc[basin[id_col].astype(int) == link_id, "agg"]
  if rows.empty:
    return None
  return int(rows.iloc[0])


def _sole_upstream_aggregate_at_river_node(
  ds_link: int,
  upstream_by_node: dict[int, list[int]],
  pour_agg: dict[int, int],
) -> tuple[int, int] | None:
  """
  One logical upstream aggregate into ``ds_link``, by current ``agg`` on each reach.

  The river graph is fixed, so headwater folds can leave **two or more reaches**
  into a node that still share a single upstream aggregate id. Those nodes are
  treated as linear for series merge (same as one physical inflow).
  """
  ds_link = int(ds_link)
  agg_d = pour_agg.get(ds_link)
  ups = upstream_by_node.get(ds_link, [])
  if not ups:
    return None
  up_by_agg: dict[int, int] = {}
  for u in ups:
    u_i = int(u)
    agg_u = pour_agg.get(u_i)
    if agg_u is None:
      continue
    agg_u = int(agg_u)
    if agg_d is not None and agg_u == int(agg_d):
      continue
    if agg_u not in up_by_agg:
      up_by_agg[agg_u] = u_i
  if len(up_by_agg) != 1:
    return None
  agg_u = next(iter(up_by_agg))
  return agg_u, up_by_agg[agg_u]


def _river_node_has_multiple_upstream_aggregates(
  ds_link: int,
  upstream_by_node: dict[int, list[int]],
  pour_agg: dict[int, int],
  agg_d: int | None = None,
) -> bool:
  """True when two or more distinct upstream aggregates still enter ``ds_link``."""
  ds_link = int(ds_link)
  ups = upstream_by_node.get(ds_link, [])
  uniq: set[int] = set()
  for u in ups:
    a = pour_agg.get(int(u))
    if a is None:
      return True
    a = int(a)
    if agg_d is not None and a == int(agg_d):
      continue
    uniq.add(a)
  return len(uniq) > 1


def _aggregate_outlet_is_confluence(
  basin: gpd.GeoDataFrame,
  id_col: str,
  agg_id: int,
  upstream_by_node: dict[int, list[int]],
) -> bool:
  """True when the aggregate outlet pour sits at a 2+ inflow stream junction."""
  link = _agg_survivor_pour_link(basin, id_col, agg_id)
  if link is None:
    return True
  return len(upstream_by_node.get(int(link), [])) >= 2


def _agg_group_unit_area(basin: gpd.GeoDataFrame, agg_id: int) -> float:
  group = basin.loc[basin["agg"].astype(int) == int(agg_id)]
  return float(group["_unitarea"].sum()) if not group.empty else 0.0


def _sort_absorb_table_upstream_first(
  basin: gpd.GeoDataFrame,
  id_col: str,
  link_depth: dict[int, int],
  xx: pd.DataFrame,
) -> pd.DataFrame:
  """
  Stable merge order: shallowest network depth first, then ``aggold``.

  Matches processing from the most upstream basins downstream along the vector
  network.
  """
  if xx.empty or "aggold" not in xx.columns:
    return xx
  order = sorted(
    range(len(xx)),
    key=lambda i: (
      _agg_min_link_depth(basin, id_col, int(xx["aggold"].iloc[i]), link_depth),
      int(xx["aggold"].iloc[i]),
    ),
  )
  return xx.iloc[order].reset_index(drop=True)


def _eligible_small_aggregate_ids(
  basin: gpd.GeoDataFrame,
  agg_basin: pd.DataFrame,
  id_col: str,
  post_lake_subs: set[int],
  min_sub_area: float,
) -> set[int]:
  """Small aggregates among all surviving pour groups (not only ``agg_basin`` rows)."""
  out: set[int] = set()
  for agg_val in basin["agg"].dropna().unique():
    try:
      agg_i = int(agg_val)
    except (TypeError, ValueError):
      continue
    if _agg_group_unit_area(basin, agg_i) >= min_sub_area:
      continue
    if not _aggregate_may_be_absorbed(basin, agg_i, id_col, post_lake_subs):
      continue
    if _agg_is_lake_group(basin, agg_i):
      continue
    out.add(agg_i)
  return out


def _frontier_headwater_aggregate_ids(
  basin: gpd.GeoDataFrame,
  id_col: str,
  link_depth: dict[int, int],
  eligible_small: set[int],
) -> set[int]:
  """
  Most-upstream small aggregates on the vector network for this iteration.

  Recomputed each merge round after prior folds — the ``headwater`` set moves
  downstream along each branch as upstream units are absorbed.
  """
  if not eligible_small:
    return set()
  depths = {
    a: _agg_min_link_depth(basin, id_col, a, link_depth) for a in eligible_small
  }
  min_d = min(depths.values())
  if min_d >= 10**9:
    return set()
  return {a for a, d in depths.items() if d == min_d}


def _link_is_gauge_pour(
  basin: gpd.GeoDataFrame,
  id_col: str,
  link_id: int,
) -> bool:
  rows = basin.loc[basin[id_col].astype(int) == int(link_id)]
  if rows.empty:
    return False
  if int(rows.iloc[0]["Mask"]) == 2:
    return True
  if "_has_gauge" in basin.columns and int(rows.iloc[0]["_has_gauge"]) > 0:
    return True
  return False


def _link_is_lake_pour(
  basin: gpd.GeoDataFrame,
  id_col: str,
  link_id: int,
  lake_subs: set[int],
) -> bool:
  rows = basin.loc[basin[id_col].astype(int) == int(link_id)]
  if not rows.empty and int(rows.iloc[0]["Mask"]) == 3:
    return True
  return int(link_id) in lake_subs


def _agg_is_gauge_group(basin: gpd.GeoDataFrame, agg_id: int) -> bool:
  group = basin.loc[basin["agg"].astype(int) == int(agg_id)]
  if group.empty:
    return False
  if (group["Mask"] == 2).any():
    return True
  if "_has_gauge" in group.columns and (group["_has_gauge"] > 0).any():
    return True
  return False


def _agg_is_lake_group(basin: gpd.GeoDataFrame, agg_id: int) -> bool:
  group = basin.loc[basin["agg"].astype(int) == int(agg_id)]
  return not group.empty and (group["Mask"] == 3).any()


def _linear_merge_may_apply_to_target(
  basin: gpd.GeoDataFrame,
  id_col: str,
  down_link: object,
  target_agg: int,
  lake_subs: set[int],
) -> bool:
  """Lakes never merge; upstream may merge into a gauge downstream target."""
  try:
    down = int(down_link)
    target = int(target_agg)
  except (TypeError, ValueError):
    return False
  if _link_is_lake_pour(basin, id_col, down, lake_subs):
    return False
  if _agg_is_lake_group(basin, target):
    return False
  return True


def _aggregate_may_be_absorbed(
  basin: gpd.GeoDataFrame,
  agg_id: int,
  id_col: str,
  post_lake_subs: set[int],
) -> bool:
  """False for units that must stay separate (gauge/lake sources, post-lake outlets)."""
  group = basin.loc[basin["agg"].astype(int) == int(agg_id)]
  if group.empty:
    return False
  if (group["Mask"] >= 2).any():
    return False
  if (group["Mask"] == 3).any():
    return False
  pour_ids = group[id_col].astype(int)
  if any(int(pid) in post_lake_subs for pid in pour_ids):
    return False
  return True


def _agg_aggdown_on_basin(basin: gpd.GeoDataFrame, agg_id: int) -> object:
  """``aggdown`` from any pour row for ``agg_id`` (survives filtered merge tables)."""
  rows = basin.loc[basin["agg"].astype(int) == int(agg_id), "aggdown"]
  if rows.empty:
    return pd.NA
  return rows.iloc[0]


def _aggdown_for_surviving_target(
  basin: gpd.GeoDataFrame,
  agg_basin: pd.DataFrame,
  id_col: str,
  down_col: str,
  target_agg: int,
  outlet_value: int,
) -> object:
  """``aggdown`` to assign to rows absorbed into ``target_agg``."""
  target_agg = int(target_agg)
  down = _agg_primary_aggdown(agg_basin, target_agg)
  if down is None or pd.isna(down):
    down = _agg_aggdown_on_basin(basin, target_agg)
  if down is None or pd.isna(down):
    link = _agg_survivor_pour_link(basin, id_col, target_agg)
    if link is not None:
      down = _pour_immediate_downstream_link(
        basin, id_col, down_col, link, outlet_value
      )
  if down is None or pd.isna(down):
    return int(outlet_value)
  return down


def _absorb_table_from_targets(
  rows: list[tuple[int, int]],
  agg_basin: pd.DataFrame,
  basin: gpd.GeoDataFrame | None = None,
  id_col: str = BASIN_ID,
  down_col: str = NEXT_DOWN_ID,
  outlet_value: int = OUTLET_VALUE,
) -> pd.DataFrame:
  if not rows:
    return pd.DataFrame(columns=["aggold", "agg", "aggdown"])
  pending = pd.DataFrame(rows, columns=["aggold", "target_agg"])
  pending["aggold"] = _agg_id_series(pending["aggold"])
  pending["target_agg"] = _agg_id_series(pending["target_agg"])
  targets = agg_basin[["agg", "aggdown"]].rename(columns={"aggdown": "new_aggdown"})
  targets["agg"] = _agg_id_series(targets["agg"])
  merged = pending.merge(
    targets,
    left_on="target_agg",
    right_on="agg",
    how="left",
  )
  aggdown = merged["new_aggdown"].copy()
  if basin is not None:
    for idx in merged.index:
      if not pd.isna(aggdown.loc[idx]):
        continue
      try:
        target_i = int(merged.loc[idx, "target_agg"])
      except (TypeError, ValueError):
        continue
      aggdown.loc[idx] = _aggdown_for_surviving_target(
        basin, agg_basin, id_col, down_col, target_i, outlet_value
      )
  return pd.DataFrame(
    {
      "aggold": merged["aggold"],
      "agg": merged["target_agg"],
      "aggdown": aggdown,
    }
  )


def _agg_primary_aggdown(agg_basin: pd.DataFrame, agg_id: int) -> object:
  rows = agg_basin.loc[agg_basin["agg"].astype(int) == int(agg_id), "aggdown"]
  if rows.empty:
    return None
  return rows.iloc[0]


def _agg_downstream_link(
  basin: gpd.GeoDataFrame,
  agg_basin: pd.DataFrame,
  agg_id: int,
) -> object:
  """Next downstream link id for ``agg_id`` (merge table, then pour rows)."""
  down = _agg_primary_aggdown(agg_basin, int(agg_id))
  if down is None or pd.isna(down):
    down = _agg_aggdown_on_basin(basin, int(agg_id))
  if pd.isna(down):
    return None
  return down


def build_headwater_driven_absorb_table(
  basin: gpd.GeoDataFrame,
  agg_basin: pd.DataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  lake_subs: set[int],
  post_lake_subs: set[int],
  upstream_by_node: dict[int, list[int]],
  link_depth: dict[int, int],
  min_sub_area: float,
  outlet_value: int = OUTLET_VALUE,
) -> pd.DataFrame:
  """
  One merge wave per call (caller loops until empty):

  * Find all small eligible aggregates, then take the **frontier**: those at the
    minimum network depth (most upstream on the graph this round).
  * Each frontier unit walks downstream to the first **confluence** (2+ river
    inflows). It merges into the **neighbor upstream** aggregate on that junction
    (tributary B into stem A at C → ``(A+B) → C``, not B into the pour at C).
  * Linear stems with no confluence are **not** merged in this pass.
  * At that pour, only **frontier** aggregates listed for that node merge.
  * **Three or more** upstream reaches at the same downstream link: no merge.
  * **Two frontier headwaters** at the same 2-way confluence: compare summed
    local basin area (``_unitarea``); the **larger** merges into the **smaller**.
    Equal area → higher ``agg`` id into lower ``agg`` id.
  * If the neighbor is **not** a frontier headwater at that confluence, the
    headwater merges into that neighbor with no area comparison.

  After applies, the next iteration rediscovers frontier headwaters further DS.

  Gauge units never merge downstream; upstream may merge into a gauge target.
  """
  outlet_value = int(outlet_value)
  pour_agg = _pour_agg_by_link(basin, id_col)
  eligible_small = _eligible_small_aggregate_ids(
    basin, agg_basin, id_col, post_lake_subs, min_sub_area
  )
  frontier = _frontier_headwater_aggregate_ids(
    basin, id_col, link_depth, eligible_small
  )
  if not frontier:
    return pd.DataFrame(columns=["aggold", "agg", "aggdown"])

  ds_triggers: dict[int, set[int]] = defaultdict(set)
  for agg_i in sorted(frontier):
    if _aggregate_outlet_is_confluence(
      basin, id_col, agg_i, upstream_by_node
    ):
      continue
    u_link = _agg_survivor_pour_link(basin, id_col, agg_i)
    if u_link is None:
      continue
    ds = _headwater_confluence_target_pour_link(
      u_link,
      basin,
      river,
      id_col,
      down_col,
      riv_id_col,
      upstream_by_node,
      lake_subs,
      outlet_value,
    )
    if ds is None:
      continue
    if _link_is_lake_pour(basin, id_col, ds, lake_subs):
      continue
    if len(upstream_by_node.get(ds, [])) >= 3:
      continue
    ds_triggers[ds].add(agg_i)

  rows: list[tuple[int, int]] = []
  seen_source: set[int] = set()

  for ds in sorted(ds_triggers.keys(), key=lambda d: (link_depth.get(int(d), 10**9), int(d))):
    hw_aggs = ds_triggers[ds]
    ups = upstream_by_node.get(int(ds), [])
    if len(ups) >= 3 or len(ups) < 2:
      continue

    sources: set[int] = set(int(a) for a in hw_aggs)

    for aggold in sorted(
      sources,
      key=lambda a: (_agg_min_link_depth(basin, id_col, int(a), link_depth), int(a)),
    ):
      if aggold in seen_source:
        continue
      if _aggregate_outlet_is_confluence(
        basin, id_col, aggold, upstream_by_node
      ):
        continue
      neighbor = _headwater_neighbor_upstream_agg_at_confluence(
        int(ds), aggold, upstream_by_node, pour_agg
      )
      if neighbor is None:
        continue
      target_i = int(neighbor)
      if target_i == aggold:
        continue
      if target_i in sources:
        if not _headwater_larger_basin_merges_into_smaller(
          basin, aggold, target_i
        ):
          continue
      if _agg_is_lake_group(basin, target_i):
        continue
      if not _linear_merge_may_apply_to_target(
        basin, id_col, ds, target_i, lake_subs
      ):
        continue
      seen_source.add(aggold)
      rows.append((aggold, target_i))

  table = _absorb_table_from_targets(
    rows, agg_basin, basin, id_col=id_col, down_col=down_col, outlet_value=outlet_value
  )
  return _sort_absorb_table_upstream_first(basin, id_col, link_depth, table)


def _linear_series_areas_may_merge(
  area_upstream: float,
  area_downstream: float,
  min_sub_area: float,
  half_min_frac: float,
  max_combined_frac: float,
) -> bool:
  """Area rules for adjacent main-stem aggregates (summed local ``_unitarea``)."""
  half_min = min_sub_area * half_min_frac
  cap = min_sub_area * max_combined_frac
  if min(area_upstream, area_downstream) < half_min:
    return True
  return (area_upstream + area_downstream) < cap


def _select_disjoint_linear_merge_pairs(
  rows: list[tuple[int, int]],
) -> tuple[list[tuple[int, int]], int]:
  """
  Pick pairs for one linear merge round without sharing aggregate ids.

  ``rows`` must already be ordered most-upstream first (e.g. by pour link depth).
  On a chain A→B→C, only (A, B) is taken this round; (B, C) waits for the next.
  """
  used: set[int] = set()
  selected: list[tuple[int, int]] = []
  deferred = 0
  for agg_u, agg_d in rows:
    if agg_u in used or agg_d in used:
      deferred += 1
      continue
    selected.append((agg_u, agg_d))
    used.add(agg_u)
    used.add(agg_d)
  return selected, deferred


def _rebuild_agg_basin_table(
  basin: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  min_sub_area: float,
  outlet_value: int,
) -> pd.DataFrame:
  """Summed local areas and pour attributes per surviving ``agg``."""
  outlet_value = int(outlet_value)

  def _drop_small_outlets(df: pd.DataFrame) -> pd.DataFrame:
    is_outlet = df["aggdown"].map(lambda d: _is_outlet_id(d, outlet_value))
    return df[(~(is_outlet & (df["_uparea"] < min_sub_area))) | (df["Mask"] == 3)]

  agg_basin = basin.drop(columns="geometry").groupby(["agg", "aggdown"], as_index=False).agg(
    {"_unitarea": "sum"}
  )
  pour = _one_row_per_agg(basin, id_col, ["_uparea", "Mask"])
  agg_basin = agg_basin.merge(pour, on="agg", how="left")
  return _drop_small_outlets(agg_basin)


def build_linear_main_stem_series_absorb_table(
  basin: gpd.GeoDataFrame,
  agg_basin: pd.DataFrame,
  id_col: str,
  down_col: str,
  upstream_by_node: dict[int, list[int]],
  link_depth: dict[int, int],
  lake_subs: set[int],
  post_lake_subs: set[int],
  min_sub_area: float,
  half_min_frac: float,
  max_combined_frac: float,
  outlet_value: int = OUTLET_VALUE,
) -> pd.DataFrame:
  """
  After headwater merging: merge upstream → downstream on **linear** reaches only.

  Uses an **aggregate series graph** rebuilt from current ``agg`` on each pour
  (sole upstream aggregate at each river node). Upstream may merge into a gauge
  downstream; a gauge unit never merges further downstream.

  Each call returns at most one pair per aggregate id (disjoint set), processing
  the most-upstream eligible pair on each linear chain first.
  """
  outlet_value = int(outlet_value)
  skip_area = LINEAR_MERGE_SKIP_AREA_RULES

  series_edges = _build_aggregate_series_edges(
    basin, id_col, upstream_by_node
  )
  series_edges.sort(
    key=lambda e: (link_depth.get(e[2], 10**9), e[0], e[1])
  )

  rows: list[tuple[int, int]] = []
  seen: set[tuple[int, int]] = set()
  skip: dict[str, int] = defaultdict(int)

  for agg_u, agg_d, ds_link in series_edges:
    pair = (agg_u, agg_d)
    if pair in seen:
      skip["duplicate_pair"] += 1
      continue
    if _agg_is_gauge_group(basin, agg_u):
      skip["source_is_gauge"] += 1
      continue
    if not _aggregate_may_be_absorbed(basin, agg_u, id_col, post_lake_subs):
      skip["source_may_not_be_absorbed"] += 1
      continue
    if _agg_is_lake_group(basin, agg_u) or _agg_is_lake_group(basin, agg_d):
      skip["lake_unit"] += 1
      continue
    if not _linear_merge_may_apply_to_target(
      basin, id_col, ds_link, agg_d, lake_subs
    ):
      skip["lake_or_gauge_downstream"] += 1
      continue
    if not skip_area:
      area_u = _agg_group_unit_area(basin, agg_u)
      area_d = _agg_group_unit_area(basin, agg_d)
      if not _linear_series_areas_may_merge(
        area_u, area_d, min_sub_area, half_min_frac, max_combined_frac
      ):
        skip["area_threshold"] += 1
        continue
    seen.add(pair)
    rows.append((agg_u, agg_d))

  n_eligible = len(rows)
  rows, deferred_same_round = _select_disjoint_linear_merge_pairs(rows)

  if LINEAR_MERGE_VERBOSE:
    mode = "no area cap" if skip_area else "area + barriers"
    print(
      f"  Linear merge scan ({mode}): {len(series_edges)} aggregate edge(s), "
      f"{n_eligible} eligible pair(s), {len(rows)} selected this round."
    )
    if deferred_same_round:
      print(
        f"  Linear deferred (agg already in a pair this round): "
        f"{deferred_same_round}"
      )
    if skip:
      parts = ", ".join(f"{k}={v}" for k, v in sorted(skip.items()))
      print(f"  Linear skip counts: {parts}")

  table = _absorb_table_from_targets(
    rows, agg_basin, basin, id_col=id_col, down_col=down_col, outlet_value=outlet_value
  )
  return _sort_absorb_table_upstream_first(basin, id_col, link_depth, table)


def _current_agg_for_basin_id(
  basin: gpd.GeoDataFrame,
  id_col: str,
  basin_id: object,
  survivor_ids: set[int] | None = None,
  gauge_link_ids: set[int] | None = None,
  pour_agg: dict[int, int] | None = None,
  id_to_agg: dict[int, int] | None = None,
) -> int | None:
  """
  Aggregate id for the basin whose pour-point id is ``basin_id``.

  When that pour point was already absorbed into a downstream group, returns
  the surviving ``agg`` (not the original link id). Used so headwater merges
  still attach after an intermediate confluence basin has been absorbed.

  Stops at gauge pour links so merge targets do not chain through a gauge
  into the aggregate below it on the same stem.
  """
  try:
    bid = int(basin_id)
  except (TypeError, ValueError):
    return None

  gauge_stop = gauge_link_ids if gauge_link_ids is not None else _gauge_pour_link_ids(
    basin, id_col
  )

  if pour_agg is not None and bid in pour_agg:
    return pour_agg[bid]

  rows = basin.loc[basin[id_col].astype(int) == bid, "agg"]
  if not rows.empty:
    return int(rows.iloc[0])

  if survivor_ids is None:
    survivor_ids = set(basin["agg"].astype(int).unique())
  if id_to_agg is None:
    id_to_agg = _id_to_agg_map(basin, id_col)
  down = bid
  seen: set[int] = set()
  while down not in survivor_ids:
    if down in gauge_stop:
      g_rows = basin.loc[basin[id_col].astype(int) == down, "agg"]
      if not g_rows.empty:
        return int(g_rows.iloc[0])
      return down
    if down not in id_to_agg or down in seen:
      return None
    seen.add(down)
    down = id_to_agg[down]
  return down


def absorb_merge_groups(
  basin: gpd.GeoDataFrame,
  xx_df: pd.DataFrame,
  outlet_value: int = OUTLET_VALUE,
  id_col: str = BASIN_ID,
  down_col: str = NEXT_DOWN_ID,
  post_lake_subs: set[int] | None = None,
  agg_basin: pd.DataFrame | None = None,
  enforce_barriers: bool = True,
) -> tuple[gpd.GeoDataFrame, int]:
  """Apply absorb rows: ``loc[agg==aggold] → new agg / aggdown``."""
  agg_index = _agg_index_by_agg_id(basin)
  post_lake = post_lake_subs or set()
  outlet_value = int(outlet_value)
  applied = 0
  skip: dict[str, int] = defaultdict(int)

  for i in range(len(xx_df)):
    aggold = xx_df["aggold"].iloc[i]
    new_agg = xx_df["agg"].iloc[i]
    new_aggdown = xx_df["aggdown"].iloc[i]
    if pd.isna(new_agg) or _is_sentinel_object_id(new_agg, outlet_value):
      skip["bad_target_agg"] += 1
      continue
    try:
      aggold_i = int(aggold)
      new_agg_i = int(new_agg)
    except (TypeError, ValueError):
      skip["non_integer_agg_id"] += 1
      continue
    if pd.isna(new_aggdown):
      if agg_basin is not None:
        new_aggdown = _aggdown_for_surviving_target(
          basin, agg_basin, id_col, down_col, new_agg_i, outlet_value
        )
      else:
        new_aggdown = _agg_aggdown_on_basin(basin, new_agg_i)
      if pd.isna(new_aggdown):
        skip["missing_aggdown"] += 1
        continue
    if enforce_barriers:
      if not _aggregate_may_be_absorbed(basin, aggold_i, id_col, post_lake):
        skip["source_barrier"] += 1
        continue
      if _agg_is_lake_group(basin, new_agg_i):
        skip["target_lake"] += 1
        continue
      if _agg_is_gauge_group(basin, aggold_i):
        skip["source_gauge"] += 1
        continue
    labels = agg_index.get(aggold_i)
    if not labels:
      skip["no_rows_for_source_agg"] += 1
      continue
    labels = list(labels)
    basin.loc[labels, "aggdown"] = new_aggdown
    basin.loc[labels, "agg"] = new_agg_i
    applied += 1
    if aggold_i != new_agg_i:
      agg_index[new_agg_i].extend(labels)
      del agg_index[aggold_i]
  return basin, applied, dict(skip)


def absorb_headwater_groups(
  basin: gpd.GeoDataFrame,
  xx_df: pd.DataFrame,
  outlet_value: int = OUTLET_VALUE,
  id_col: str = BASIN_ID,
  down_col: str = NEXT_DOWN_ID,
  post_lake_subs: set[int] | None = None,
  agg_basin: pd.DataFrame | None = None,
  enforce_barriers: bool = True,
) -> gpd.GeoDataFrame:
  basin, _applied, _skip = absorb_merge_groups(
    basin,
    xx_df,
    outlet_value=outlet_value,
    id_col=id_col,
    down_col=down_col,
    post_lake_subs=post_lake_subs,
    agg_basin=agg_basin,
    enforce_barriers=enforce_barriers,
  )
  return basin


# ==============================================================================
# CORE AGGREGATION (logic preserved from 01-pre-process-geospatial-fabric.ipynb)
# ==============================================================================
def basin_aggregation(
  input_basin: gpd.GeoDataFrame,
  input_river: gpd.GeoDataFrame,
  min_sub_area: float,
  min_riv_slope: float,
  min_riv_length: float,
  outlet_value: int = OUTLET_VALUE,
  linear_series_merge: bool = LINEAR_SERIES_MERGE_ENABLED,
  linear_series_half_min_frac: float = LINEAR_SERIES_MERGE_HALF_MIN_FRAC,
  linear_series_max_combined_frac: float = LINEAR_SERIES_MERGE_MAX_COMBINED_FRAC,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  """
  Aggregate basins and rivers based on drainage area, slope, and reservoir masking.

  ``min_sub_area`` is a **floor** for keeping a subbasin as its own aggregate
  (local ``_unitarea`` below it may be merged away). It is **not** a cap:
  absorbed polygons sum onto the downstream ``agg`` and the result may be
  well above ``min_sub_area``.

  Merge schedule: each round runs one **headwater** wave then one **linear**
  wave, rebuilds the merge table, and repeats until neither wave applies a row.

  Returns aggregated basin and river GeoDataFrames. Each basin is identified by
  BASIN_ID. Gauge IDs, lake flags/IDs, lake area, and fractional lake area are
  pour-point attributes only.

  Terminal basins receive ``outlet_value`` in ``DSLINKNO`` (default ``OUTLET_VALUE``).
  """
  outlet_value = int(outlet_value)
  original_basin_columns = list(input_basin.columns)
  original_river_columns = list(input_river.columns)
  basin, river = prepare_input_tables(input_basin, input_river)

  id_col = BASIN_ID
  down_col = NEXT_DOWN_ID
  riv_id_col = RIVER_ID

  river[SLOPE] = river[SLOPE].clip(lower=min_riv_slope)
  river.loc[river[SLOPE] >= 1.0, SLOPE] = min_riv_slope
  river["_lengthkm"] = river["_lengthkm"].clip(lower=min_riv_length)

  # Normalize any legacy outlet markers (-1, 0, ...) to the configured sentinel.
  river.loc[
    river[down_col].map(lambda d: _is_outlet_id(d, outlet_value)),
    down_col,
  ] = outlet_value

  basin["Mask"] = 0
  basin.loc[basin[down_col].map(lambda d: _is_outlet_id(d, outlet_value)), "Mask"] = 1
  basin.loc[basin["_has_gauge"] > 0, "Mask"] = 2
  basin.loc[basin["_lake_cat"] > 0, "Mask"] = 3
  basin["agg"] = basin[id_col]
  basin["aggdown"] = basin[down_col]
  basin.loc[
    basin["aggdown"].map(lambda d: _is_outlet_id(d, outlet_value)),
    "aggdown",
  ] = outlet_value

  agg_basin = _rebuild_agg_basin_table(
    basin, id_col, down_col, min_sub_area, outlet_value
  )
  lake_subs = set(basin.loc[basin["Mask"] == 3, "agg"].astype(int))
  post_lake_subs = _downstream_basin_ids_of_lakes(
    basin, river, id_col, down_col, riv_id_col, outlet_value
  )
  upstream_by_node = _upstream_links_by_down_node(river, down_col, riv_id_col)
  headwater_links = _headwater_link_ids(river, riv_id_col, upstream_by_node)
  link_depth = _link_depth_from_headwaters(upstream_by_node, headwater_links)
  n_agg_start = int(basin["agg"].nunique())
  max_merge_iters = max(len(basin) * 3, 1000)
  combine_round = 0
  total_hw_applied = 0
  total_linear_applied = 0

  print(
    f"Basin merge (headwater wave + linear wave until idle): "
    f"{len(basin)} pour point(s), {n_agg_start} aggregate id(s) initially, "
    f"max {max_merge_iters} round(s)."
  )
  if linear_series_merge:
    if LINEAR_MERGE_SKIP_AREA_RULES:
      print(
        "  Linear: single-upstream nodes; area caps off, "
        "gauge/lake/post-lake barriers on."
      )
    else:
      print(
        "  Linear: half-min="
        f"{linear_series_half_min_frac}× overrides cap; otherwise sum < "
        f"{linear_series_max_combined_frac}× MIN_SUB_AREA."
      )
  else:
    print("  Linear series merge disabled (headwater waves only).")

  while True:
    combine_round += 1
    if combine_round > max_merge_iters:
      raise RuntimeError(
        f"Basin merge did not converge after {max_merge_iters} round(s). "
        "Small subbasins may be oscillating without merging. "
        "Check DSLINKNO / LINKNO topology in outputs/final/streams.shp "
        f"(and {TOPOLOGY_CYCLES_SHP} if river cycles were detected)."
      )

    hw_applied = 0
    linear_applied = 0

    xx_hw = build_headwater_driven_absorb_table(
      basin,
      agg_basin,
      river,
      id_col=id_col,
      down_col=down_col,
      riv_id_col=riv_id_col,
      lake_subs=lake_subs,
      post_lake_subs=post_lake_subs,
      upstream_by_node=upstream_by_node,
      link_depth=link_depth,
      min_sub_area=min_sub_area,
      outlet_value=outlet_value,
    )
    if not xx_hw.empty:
      xx_hw = _sort_absorb_table_upstream_first(
        basin, id_col, link_depth, xx_hw
      )
      basin, hw_applied, _ = absorb_merge_groups(
        basin,
        xx_hw,
        outlet_value=outlet_value,
        id_col=id_col,
        down_col=down_col,
        post_lake_subs=post_lake_subs,
        agg_basin=agg_basin,
        enforce_barriers=True,
      )
      total_hw_applied += hw_applied

    if linear_series_merge:
      xx_series = build_linear_main_stem_series_absorb_table(
        basin,
        agg_basin,
        id_col=id_col,
        down_col=down_col,
        upstream_by_node=upstream_by_node,
        link_depth=link_depth,
        lake_subs=lake_subs,
        post_lake_subs=post_lake_subs,
        min_sub_area=min_sub_area,
        half_min_frac=linear_series_half_min_frac,
        max_combined_frac=linear_series_max_combined_frac,
        outlet_value=outlet_value,
      )
      if not xx_series.empty:
        n_aggs_before = int(basin["agg"].nunique())
        basin, linear_applied, abs_skip = absorb_merge_groups(
          basin,
          xx_series,
          outlet_value=outlet_value,
          id_col=id_col,
          down_col=down_col,
          post_lake_subs=post_lake_subs,
          agg_basin=agg_basin,
          enforce_barriers=True,
        )
        total_linear_applied += linear_applied
        n_aggs_after = int(basin["agg"].nunique())
        if LINEAR_MERGE_VERBOSE:
          print(
            f"  Round {combine_round} linear: {len(xx_series)} pair(s), "
            f"applied {linear_applied}; aggregate ids "
            f"{n_aggs_before} → {n_aggs_after}."
          )
          if abs_skip:
            parts = ", ".join(f"{k}={v}" for k, v in sorted(abs_skip.items()))
            print(f"  Linear absorb skips: {parts}")
        elif linear_applied == 0:
          print(
            "Warning: linear merge found absorb candidate(s) but applied none "
            f"(round {combine_round}; set LINEAR_MERGE_VERBOSE=True for details)."
          )

    if hw_applied > 0 or linear_applied > 0:
      agg_basin = _rebuild_agg_basin_table(
        basin, id_col, down_col, min_sub_area, outlet_value
      )
      if LINEAR_MERGE_VERBOSE:
        print(
          f"  Round {combine_round}: headwater applied {hw_applied}, "
          f"linear applied {linear_applied}."
        )
    else:
      break

  agg_basin = _rebuild_agg_basin_table(
    basin, id_col, down_col, min_sub_area, outlet_value
  )
  n_agg_ids = int(basin["agg"].nunique())
  n_series_edges = len(
    _build_aggregate_series_edges(basin, id_col, upstream_by_node)
  )
  print(
    f"Basin merge finished after {combine_round} round(s): "
    f"headwater {total_hw_applied} pair(s), linear {total_linear_applied} "
    f"pair(s) applied; {n_agg_ids} aggregate id(s) "
    f"(started {n_agg_start}), {n_series_edges} sole-upstream linear edge(s)."
  )

  sentinel_group = basin["agg"].map(lambda a: _is_sentinel_object_id(a, outlet_value))
  if sentinel_group.any():
    n_split = int(sentinel_group.sum())
    print(
      f"Warning: {n_split} unit(s) had aggregate id equal to the outlet "
      f"sentinel; leaving them unaggregated instead of dissolving as one."
    )
    basin.loc[sentinel_group, "agg"] = basin.loc[sentinel_group, id_col]

  # Lakes / gauges keep their original downstream ids; remap those onto the
  # surviving aggregate that absorbed each missing target.
  basin = _remap_aggdown_to_survivors(
    basin, id_col=id_col, outlet_value=outlet_value
  )
  pour_down = _one_row_per_agg(basin, id_col, ["aggdown"])
  basin = basin.drop(columns=["aggdown"]).merge(pour_down, on="agg", how="left")

  basin_out_cols = _basin_columns_for_output(
    basin, id_col, down_col, original_basin_columns
  )
  attr_cols = ["aggdown", "_uparea"] + [
    c for c in basin_out_cols if c != down_col
  ]
  pour_attrs = _one_row_per_agg(basin, id_col, attr_cols)

  agg_basin = basin.dissolve(by="agg", aggfunc={"_unitarea": "sum"}, as_index=False)
  agg_basin = agg_basin.merge(pour_attrs, on="agg", how="left")
  agg_basin = agg_basin.rename(columns={"agg": id_col, "aggdown": down_col})

  if id_col == riv_id_col:
    agg_river = river.merge(basin[[id_col, "agg"]].copy(), on=riv_id_col, how="left")
  else:
    agg_river = river.merge(
      basin[[id_col, "agg"]].copy(),
      left_on=riv_id_col,
      right_on=id_col,
      how="left",
    )
  agg_river, cycle_features = _mark_river_main_stems(
    agg_river, down_col, riv_id_col, upstream_by_node=upstream_by_node
  )
  _export_topology_cycles(cycle_features, agg_river.crs)
  agg_river = agg_river[agg_river["mask"] == 1].copy()
  agg_river["_slope_weighted"] = agg_river[SLOPE] * agg_river["_lengthkm"]

  stream_agg = _stream_dissolve_aggfunc(
    agg_river, riv_id_col=riv_id_col, down_col=down_col
  )
  stream_agg["_slope_weighted"] = "sum"
  agg_river = agg_river.dissolve(by="agg", aggfunc=stream_agg, as_index=False).rename(
    columns={"agg": riv_id_col}
  )

  length_m = agg_river["_lengthkm"] / LENGTH_SCALE
  agg_river[LENGTH] = length_m
  if STRM_DROP not in agg_river.columns:
    raise ValueError(
      f"River layer is missing '{STRM_DROP}' (expected from TauDEM / cleanGeofabric "
      f"after combiningBasins). Columns present: {list(agg_river.columns)}"
    )
  agg_river[SLOPE] = _slope_from_strm_drop_and_length(
    length_m, agg_river[STRM_DROP], min_riv_slope
  )
  basin_topo = agg_basin[[id_col, down_col, "_uparea"]].copy()
  if id_col != riv_id_col:
    basin_topo = basin_topo.rename(columns={id_col: riv_id_col})
  agg_river = agg_river.merge(basin_topo, on=riv_id_col, how="left")

  unit_out = UNIT_AREA or "area_km2"
  agg_basin = agg_basin.rename(columns={"_unitarea": unit_out, "_uparea": UP_AREA})
  if UNIT_AREA and AREA_SCALE != 1.0:
    agg_basin[unit_out] = agg_basin[unit_out] / AREA_SCALE
  if AREA_SCALE != 1.0:
    agg_basin[UP_AREA] = agg_basin[UP_AREA] / AREA_SCALE

  if "_uparea" in agg_river.columns:
    agg_river[UP_AREA] = agg_river["_uparea"] / AREA_SCALE
  drop_riv = ["_lengthkm", "_uparea", "_slope_weighted", "mask"]
  if id_col != riv_id_col and id_col in agg_river.columns:
    drop_riv.append(id_col)
  agg_river = agg_river.drop(columns=drop_riv, errors="ignore")

  missing_riv = [
    c
    for c in original_river_columns
    if c != "geometry" and c not in agg_river.columns
  ]
  if missing_riv:
    print(
      "Warning: aggregated streams missing column(s) from input rivers layer: "
      f"{missing_riv}"
    )

  missing_bas = [
    c
    for c in original_basin_columns
    if c != "geometry" and c not in agg_basin.columns
  ]
  if missing_bas:
    print(
      "Warning: aggregated basins missing column(s) from input basins layer: "
      f"{missing_bas}"
    )

  agg_basin[id_col] = agg_basin[id_col].astype("int64")
  agg_basin[down_col] = (
    pd.to_numeric(agg_basin[down_col], errors="coerce")
    .fillna(outlet_value)
    .astype("int64")
  )
  agg_river[riv_id_col] = agg_river[riv_id_col].astype("int64")
  agg_river[down_col] = (
    pd.to_numeric(agg_river[down_col], errors="coerce")
    .fillna(outlet_value)
    .astype("int64")
  )

  # Basin object ids (DN) match river LINKNO values after aggregation; expose a
  # single LINKNO key on both layers for MESH / topology tools.
  if id_col != riv_id_col:
    agg_basin = agg_basin.rename(columns={id_col: riv_id_col})
    id_col = riv_id_col

  _validate_topology(
    agg_basin,
    agg_river,
    id_col=id_col,
    down_col=down_col,
    outlet_value=outlet_value,
  )

  agg_basin = _deduplicate_geodataframe_columns(agg_basin)
  agg_river = _deduplicate_geodataframe_columns(agg_river)

  return agg_basin, agg_river


def run_aggregation(
  basins_path: str = INPUT_BASINS,
  rivers_path: str = INPUT_RIVERS,
  output_basins_path: str = OUTPUT_BASINS,
  output_rivers_path: str = OUTPUT_RIVERS,
  min_sub_area: float = MIN_SUB_AREA,
  min_riv_slope: float = MIN_RIV_SLOPE,
  min_riv_length: float = MIN_RIV_LENGTH,
  outlet_value: int = OUTLET_VALUE,
  linear_series_merge: bool = LINEAR_SERIES_MERGE_ENABLED,
  linear_series_half_min_frac: float = LINEAR_SERIES_MERGE_HALF_MIN_FRAC,
  linear_series_max_combined_frac: float = LINEAR_SERIES_MERGE_MAX_COMBINED_FRAC,
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
    outlet_value=outlet_value,
    linear_series_merge=linear_series_merge,
    linear_series_half_min_frac=linear_series_half_min_frac,
    linear_series_max_combined_frac=linear_series_max_combined_frac,
  )

  os.makedirs(os.path.dirname(output_basins_path) or ".", exist_ok=True)
  os.makedirs(os.path.dirname(output_rivers_path) or ".", exist_ok=True)

  print(f"Writing aggregated basins ({len(agg_basins)} features): {output_basins_path}")
  from hy_features.export import export_shapefile_legacy

  export_shapefile_legacy(agg_basins, output_basins_path)
  print(f"Writing aggregated rivers ({len(agg_rivers)} features): {output_rivers_path}")
  export_shapefile_legacy(agg_rivers, output_rivers_path)

  return agg_basins, agg_rivers


if __name__ == "__main__":
  ensure_output_dirs()
  run_aggregation()
