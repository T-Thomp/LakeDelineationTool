"""
Aggregate small sub-basins and stream reaches from cleanGeofabric outputs.

Inputs (cleanGeofabric.py):
  outputs/final/basins.shp   (DN, area_km2, is_lake, STATION_NU, ...)
  outputs/final/streams.shp  (LINKNO, DSLINKNO, Length, strmDrop, DSContArea, ...)

Outputs:
  outputs/final/basins_aggregated.shp
  outputs/final/streams_aggregated.shp
  outputs/working/aggregation_merge_log.csv

Algorithm (repeat until an iteration merges nothing):

  1. Headwater pass (sideways). A headwater is an aggregate that no other
     aggregate drains into. Junctions (aggregates with 2+ inflows) are visited
     most-upstream first. Headwaters with area_km2 < MIN_SUB_AREA merge into a
     sibling that shares the same DSLINKNO:
       * 2 inflows, both headwaters, either one small: the shorter stream
         dissolves into the longer stream.
       * 3+ inflows, all headwaters: small headwaters merge into the longest.
       * one non-headwater sibling: small headwaters merge into it.
       * several non-headwater siblings: each small headwater merges into the
         one it shares the most border with.
     The absorbed headwater's reach is dropped from the stream output.

  2. Linear pass (in series). Where a basin has exactly one upstream basin, the
     upstream basin merges into it, walking each chain downstream from the most
     upstream edge. LINEAR_MERGE_MODE:
       "all"    - merge regardless of size.
       "capped" - merge when either basin < LINEAR_HALF_MIN_FRAC * MIN_SUB_AREA,
                  otherwise only when the combined area is
                  <= LINEAR_MAX_COMBINED_FRAC * MIN_SUB_AREA.

  Protection: lakes are never merged (as source or target). Gauged basins are
  never absorbed and never a sideways target, but may absorb their single
  upstream basin in the linear pass (the gauge keeps its id and outlet).

The surviving aggregate always keeps its original LINKNO, DSLINKNO and pour-point
attributes. DSContArea is recomputed from the aggregated network.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from typing import Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely

from hy_features.config import hy_features_enabled
from pipeline_paths import (
  FINAL_BASINS,
  FINAL_BASINS_AGG,
  FINAL_STREAMS,
  FINAL_STREAMS_AGG,
  WORKING,
  WORKING_GEOFABRIC_AGG_GPKG,
  ensure_output_dirs,
)

ENABLE_HY_FEATURES = False  # overridden by HY_FEATURES_ENABLED env var if set

# ==============================================================================
# COLUMN NAMES — legacy TauDEM/MESH fields on disk. See docs/hy_features_mapping.md.
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

BASIN_ID = LEGACY_BASIN_ID        # DN
RIVER_ID = LEGACY_FLOWPATH_ID     # LINKNO
NEXT_DOWN_ID = LEGACY_LOWER_ID    # DSLINKNO
GAUGE_IDS = LEGACY_GAUGE_IDS      # STATION_NU
LAKE_FLAG = LEGACY_IS_LAKE
LAKE_ID = LEGACY_LAKE_ID
LAKE_AREA = LEGACY_LAKE_AREA
FRAC_LAKE_AREA = FRAC_LAKE

AREA_KM2 = "area_km2"             # local subbasin area (km²)
UP_AREA = "DSContArea"            # cumulative drainage area at the pour point (m²)
US_AREA = "USContArea"            # cumulative area at the upstream end of the reach (m²)
SLOPE = "Slope"
LENGTH = "Length"                 # reach length (m)
STRAIGHT_LEN = "StraightL"        # straight-line distance between reach endpoints (m)
STRM_DROP = "strmDrop"            # reach elevation drop (m)
US_LINK_COLS = ("USLINKNO1", "USLINKNO2")

AREA_SCALE = 1e-6                 # m² -> km²
LENGTH_SCALE = 1e-3               # m -> km


# ==============================================================================
# SETTINGS
# ==============================================================================
INPUT_BASINS = str(FINAL_BASINS)
INPUT_RIVERS = str(FINAL_STREAMS)
OUTPUT_BASINS = str(FINAL_BASINS_AGG)
OUTPUT_RIVERS = str(FINAL_STREAMS_AGG)
MERGE_LOG_CSV = str(WORKING / "aggregation_merge_log.csv")

MIN_SUB_AREA = 100.0              # km² — headwaters below this merge sideways
MIN_RIV_SLOPE = 0.0000001         # minimum accepted river slope (WATFLOOD manual)
MIN_RIV_LENGTH = 1.0              # km — floor applied to output reach length

LINEAR_MERGE_ENABLED = True
LINEAR_MERGE_MODE = "capped"      # "all" or "capped"
LINEAR_HALF_MIN_FRAC = 0.5        # capped: either basin below this × MIN_SUB_AREA always merges
LINEAR_MAX_COMBINED_FRAC = 2.0    # capped: otherwise merge only if sum <= this × MIN_SUB_AREA

OUTLET_VALUE = -9999              # DSLINKNO for outlet basins (match MESH outlet_value)


# ==============================================================================
# INPUT PREPARATION
# ==============================================================================
def _require_columns(gdf: gpd.GeoDataFrame, columns: list[str], label: str) -> None:
  missing = [col for col in columns if col not in gdf.columns]
  if missing:
    raise ValueError(f"Missing columns in {label}: {missing}")


def _is_outlet_id(down_id: object, outlet_value: int) -> bool:
  """True for outlet sentinels (configured value, NaN, or legacy <= 0)."""
  try:
    down = int(down_id)
  except (TypeError, ValueError):
    return True
  return down == int(outlet_value) or down <= 0


def prepare_input_tables(
  input_basin: gpd.GeoDataFrame,
  input_river: gpd.GeoDataFrame,
  outlet_value: int = OUTLET_VALUE,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  """Validate inputs, join stream attributes onto basins, add area / lake / gauge flags."""
  basin = input_basin.copy()
  river = input_river.copy()

  _require_columns(basin, [BASIN_ID, "geometry"], "basin layer")
  _require_columns(river, [RIVER_ID, NEXT_DOWN_ID, LENGTH], "river layer")

  basin[BASIN_ID] = basin[BASIN_ID].astype(int)
  river[RIVER_ID] = river[RIVER_ID].astype(int)
  river[NEXT_DOWN_ID] = pd.to_numeric(river[NEXT_DOWN_ID], errors="coerce")
  river[NEXT_DOWN_ID] = [
    int(outlet_value) if _is_outlet_id(d, outlet_value) else int(d)
    for d in river[NEXT_DOWN_ID]
  ]
  river[LENGTH] = pd.to_numeric(river[LENGTH], errors="coerce").fillna(0.0)
  if STRM_DROP not in river.columns:
    slope = pd.to_numeric(river.get(SLOPE, 0.0), errors="coerce").fillna(0.0)
    river[STRM_DROP] = slope * river[LENGTH]

  join_cols = [
    c for c in river.columns
    if (c not in basin.columns or c == RIVER_ID) and c != "geometry"
  ]
  basin = basin.merge(
    river[join_cols].rename(columns={RIVER_ID: BASIN_ID}),
    on=BASIN_ID,
    how="left",
  )
  basin[NEXT_DOWN_ID] = [
    int(outlet_value) if _is_outlet_id(d, outlet_value) else int(d)
    for d in pd.to_numeric(basin[NEXT_DOWN_ID], errors="coerce")
  ]

  if AREA_KM2 in basin.columns:
    basin["_unitarea"] = pd.to_numeric(basin[AREA_KM2], errors="coerce")
    missing = basin["_unitarea"].isna()
    basin.loc[missing, "_unitarea"] = basin.loc[missing, "geometry"].area * AREA_SCALE
  else:
    basin["_unitarea"] = basin.geometry.area * AREA_SCALE

  if LAKE_FLAG in basin.columns:
    basin["_is_lake"] = pd.to_numeric(basin[LAKE_FLAG], errors="coerce").fillna(0) > 0
  else:
    basin["_is_lake"] = False

  if GAUGE_IDS in basin.columns:
    basin[GAUGE_IDS] = basin[GAUGE_IDS].fillna("").astype(str)
    basin["_is_gauge"] = basin[GAUGE_IDS].str.strip().ne("")
  else:
    basin["_is_gauge"] = False

  return basin, river


def _resolve_down_to_basins(
  basin_ids: set[int],
  raw_down: dict[int, int],
  river_down: dict[int, int],
  outlet_value: int,
) -> dict[int, int]:
  """Follow stream DSLINKNO past reaches with no basin so every link targets a basin."""
  resolved: dict[int, int] = {}
  for bid, down in raw_down.items():
    seen: set[int] = set()
    while not _is_outlet_id(down, outlet_value) and down not in basin_ids:
      if down in seen or down not in river_down:
        down = outlet_value
        break
      seen.add(down)
      down = river_down[down]
    resolved[bid] = outlet_value if (_is_outlet_id(down, outlet_value) or down == bid) else int(down)
  return resolved


def shared_border_lengths(basin: gpd.GeoDataFrame, id_col: str = BASIN_ID) -> dict[int, dict[int, float]]:
  """Shared boundary length between every pair of touching basin polygons."""
  geoms = basin[[id_col, "geometry"]].dissolve(by=id_col, as_index=False)
  border: dict[int, dict[int, float]] = defaultdict(dict)
  if len(geoms) < 2:
    return border
  pairs = gpd.sjoin(geoms, geoms, predicate="intersects", how="inner")
  left_ids = pairs[f"{id_col}_left"].to_numpy()
  right_ids = pairs[f"{id_col}_right"].to_numpy()
  keep = left_ids < right_ids
  left_ids, right_ids = left_ids[keep], right_ids[keep]
  if len(left_ids) == 0:
    return border
  geom_by_id = dict(zip(geoms[id_col].to_numpy(), geoms.geometry.to_numpy()))
  left_bound = shapely.boundary(np.array([geom_by_id[i] for i in left_ids]))
  right_bound = shapely.boundary(np.array([geom_by_id[i] for i in right_ids]))
  lengths = shapely.length(shapely.intersection(left_bound, right_bound))
  for a, b, length in zip(left_ids, right_ids, lengths):
    if length > 0:
      border[int(a)][int(b)] = float(length)
      border[int(b)][int(a)] = float(length)
  return border


# ==============================================================================
# AGGREGATE GRAPH
# ==============================================================================
class AggregateGraph:
  """
  In-memory network of aggregates keyed by the surviving LINKNO.

  ``down`` is the only topology source; ``inflows`` and ``hops`` are rebuilt from
  it at the start of every pass (``rebuild``) and kept current during merges.
  """

  def __init__(
    self,
    down: dict[int, int],
    area: dict[int, float],
    length: dict[int, float],
    is_lake: dict[int, bool],
    is_gauge: dict[int, bool],
    border: dict[int, dict[int, float]],
    outlet_value: int = OUTLET_VALUE,
  ) -> None:
    self.outlet_value = int(outlet_value)
    self.down = dict(down)
    self.area = dict(area)
    self.length = dict(length)
    self.is_lake = dict(is_lake)
    self.is_gauge = dict(is_gauge)
    self.members: dict[int, list[int]] = {a: [a] for a in self.down}
    self.stream_members: dict[int, list[int]] = {a: [a] for a in self.down}
    self.border: dict[int, dict[int, float]] = {
      a: {b: v for b, v in border.get(a, {}).items() if b in self.down}
      for a in self.down
    }
    self.inflows: dict[int, list[int]] = {}
    self.hops: dict[int, int] = {}
    self.rebuild()

  def is_outlet(self, a: int) -> bool:
    return self.down[a] == self.outlet_value

  def rebuild(self) -> None:
    """Recompute inflows and hops-to-outlet from ``down``."""
    self.inflows = {a: [] for a in self.down}
    for a, d in self.down.items():
      if d != self.outlet_value:
        self.inflows[d].append(a)
    for ups in self.inflows.values():
      ups.sort()

    self.hops = {}
    for start in self.down:
      path: list[int] = []
      on_path: set[int] = set()
      node = start
      while node not in self.hops:
        if node in on_path:
          raise RuntimeError(
            f"DSLINKNO cycle detected through {sorted(on_path)[:10]}; fix streams topology."
          )
        path.append(node)
        on_path.add(node)
        if self.down[node] == self.outlet_value:
          break
        node = self.down[node]
      if node in self.hops:
        base = self.hops[node]
      else:
        path.pop()
        self.hops[node] = base = 0
      for n in reversed(path):
        base += 1
        self.hops[n] = base

  def upstream_first(self, ids) -> list[int]:
    return sorted(ids, key=lambda a: (-self.hops[a], a))

  def is_headwater(self, a: int) -> bool:
    return not self.inflows[a]

  def shared_border(self, a: int, b: int) -> float:
    return self.border.get(a, {}).get(b, 0.0)

  def merge(self, source: int, target: int, *, add_stream: bool) -> None:
    """Absorb ``source`` into ``target``; ``target`` keeps its id and DSLINKNO."""
    self.members[target].extend(self.members.pop(source))
    source_streams = self.stream_members.pop(source)
    source_length = self.length.pop(source)
    if add_stream:
      self.stream_members[target].extend(source_streams)
      self.length[target] += source_length
    self.area[target] += self.area.pop(source)

    source_border = self.border.pop(source)
    target_border = self.border[target]
    for other, length in source_border.items():
      self.border[other].pop(source, None)
      if other == target:
        continue
      target_border[other] = target_border.get(other, 0.0) + length
      self.border[other][target] = target_border[other]

    source_down = self.down.pop(source)
    if source_down != self.outlet_value:
      self.inflows[source_down].remove(source)
    for up in self.inflows.pop(source):
      self.down[up] = target
      self.inflows[target].append(up)
    self.inflows[target].sort()

    self.hops.pop(source, None)
    self.is_lake.pop(source)
    self.is_gauge.pop(source)


def build_graph(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  outlet_value: int = OUTLET_VALUE,
) -> AggregateGraph:
  per_id = basin.groupby(BASIN_ID).agg(
    down=(NEXT_DOWN_ID, "first"),
    area=("_unitarea", "sum"),
    is_lake=("_is_lake", "max"),
    is_gauge=("_is_gauge", "max"),
  )
  basin_ids = set(int(i) for i in per_id.index)
  river_down = dict(zip(river[RIVER_ID].astype(int), river[NEXT_DOWN_ID].astype(int)))
  down = _resolve_down_to_basins(
    basin_ids,
    {int(i): int(d) for i, d in per_id["down"].items()},
    river_down,
    outlet_value,
  )
  river_len_km = dict(zip(river[RIVER_ID].astype(int), river[LENGTH] * LENGTH_SCALE))
  return AggregateGraph(
    down=down,
    area={int(i): float(v) for i, v in per_id["area"].items()},
    length={i: float(river_len_km.get(i, 0.0)) for i in basin_ids},
    is_lake={int(i): bool(v) for i, v in per_id["is_lake"].items()},
    is_gauge={int(i): bool(v) for i, v in per_id["is_gauge"].items()},
    border=shared_border_lengths(basin),
    outlet_value=outlet_value,
  )


# ==============================================================================
# MERGE PASSES
# ==============================================================================
def _log_merge(log, g: AggregateGraph, iteration: int, pass_name: str,
               source: int, target: int, rule: str) -> None:
  log.append({
    "iteration": iteration,
    "pass": pass_name,
    "source": source,
    "target": target,
    "rule": rule,
    "source_area": round(g.area[source], 4),
    "target_area": round(g.area[target], 4),
  })


def headwater_pass(g: AggregateGraph, min_sub_area: float, iteration: int, log: list) -> int:
  """One sideways pass over every junction, most upstream first."""
  g.rebuild()
  headwaters = {a for a in g.down if g.is_headwater(a)}

  def sideways_ok(a: int) -> bool:
    return not g.is_lake[a] and not g.is_gauge[a]

  def is_small(a: int) -> bool:
    return g.area[a] < min_sub_area

  def by_length(a: int) -> tuple:
    return (g.length[a], -g.area[a], a)

  # Inflows to a lake enter at different points on its shoreline, so they are
  # never merged sideways with each other.
  junctions = [
    j for j, ups in g.inflows.items() if len(ups) >= 2 and not g.is_lake[j]
  ]
  merged = 0
  for junction in g.upstream_first(junctions):
    inflows = list(g.inflows[junction])
    hw = [a for a in inflows if a in headwaters]
    non_hw = [a for a in inflows if a not in headwaters]
    if not hw:
      continue

    if len(inflows) == 2 and len(hw) == 2:
      a, b = hw
      if not (is_small(a) or is_small(b)) or not (sideways_ok(a) and sideways_ok(b)):
        continue
      shorter, longer = sorted(hw, key=by_length)
      _log_merge(log, g, iteration, "headwater", shorter, longer, "two_headwater_shorter_into_longer")
      g.merge(shorter, longer, add_stream=False)
      merged += 1
      continue

    if not non_hw:
      targets = [a for a in hw if sideways_ok(a)]
      if not targets:
        continue
      target = max(targets, key=by_length)
      for src in hw:
        if src != target and sideways_ok(src) and is_small(src):
          _log_merge(log, g, iteration, "headwater", src, target, "all_headwater_into_longest")
          g.merge(src, target, add_stream=False)
          merged += 1
      continue

    targets = [a for a in non_hw if sideways_ok(a)]
    if not targets:
      continue
    for src in hw:
      if not (sideways_ok(src) and is_small(src)):
        continue
      if len(targets) == 1:
        target, rule = targets[0], "into_non_headwater"
      else:
        target = max(targets, key=lambda t: (g.shared_border(src, t), g.area[t], -t))
        rule = "into_most_shared_border"
      _log_merge(log, g, iteration, "headwater", src, target, rule)
      g.merge(src, target, add_stream=False)
      merged += 1
  return merged


def linear_pass(
  g: AggregateGraph,
  min_sub_area: float,
  mode: str,
  half_min_frac: float,
  max_combined_frac: float,
  iteration: int,
  log: list,
) -> int:
  """Merge single-upstream chains downstream, starting from the most upstream edge."""
  if mode not in ("all", "capped"):
    raise ValueError(f"LINEAR_MERGE_MODE must be 'all' or 'capped', got {mode!r}")
  g.rebuild()
  half_min = half_min_frac * min_sub_area
  max_combined = max_combined_frac * min_sub_area

  def can_merge(up: int, dn: int) -> Optional[str]:
    if g.is_lake[up] or g.is_gauge[up] or g.is_lake[dn]:
      return None
    if mode == "all":
      return "linear_all"
    if g.area[up] < half_min or g.area[dn] < half_min:
      return "linear_below_half_min"
    if g.area[up] + g.area[dn] <= max_combined:
      return "linear_within_max_combined"
    return None

  merged = 0
  for start in g.upstream_first(list(g.down)):
    current = start
    while current in g.down and not g.is_outlet(current):
      dn = g.down[current]
      if len(g.inflows[dn]) != 1:
        break
      rule = can_merge(current, dn)
      if rule is None:
        break
      _log_merge(log, g, iteration, "linear", current, dn, rule)
      g.merge(current, dn, add_stream=True)
      merged += 1
      current = dn
  return merged


def run_merges(
  g: AggregateGraph,
  min_sub_area: float = MIN_SUB_AREA,
  linear_merge: bool = LINEAR_MERGE_ENABLED,
  linear_mode: str = LINEAR_MERGE_MODE,
  half_min_frac: float = LINEAR_HALF_MIN_FRAC,
  max_combined_frac: float = LINEAR_MAX_COMBINED_FRAC,
) -> list[dict]:
  """Alternate headwater and linear passes until an iteration merges nothing."""
  log: list[dict] = []
  n_start = len(g.down)
  linear_desc = (
    f"linear '{linear_mode}'" if linear_merge else "linear disabled"
  )
  print(f"Basin merge: {n_start} basin(s), MIN_SUB_AREA={min_sub_area:g} km², {linear_desc}.")
  # Every productive iteration removes at least one aggregate, so this bound is never hit
  # unless something is badly wrong.
  for iteration in range(1, n_start + 2):
    n_hw = headwater_pass(g, min_sub_area, iteration, log)
    n_lin = 0
    if linear_merge:
      n_lin = linear_pass(
        g, min_sub_area, linear_mode, half_min_frac, max_combined_frac, iteration, log
      )
    print(f"  iter {iteration}: headwater {n_hw}, linear {n_lin} -> {len(g.down)} aggregate(s)")
    if n_hw == 0 and n_lin == 0:
      break
  else:
    raise RuntimeError("Basin merge did not converge.")
  g.rebuild()

  small = [a for a in g.down if g.area[a] < min_sub_area]
  if small:
    print(
      f"  {len(small)} aggregate(s) remain below {min_sub_area:g} km² "
      "(lakes, gauges, outlets, or not a headwater / linear candidate)."
    )
  return log


# ==============================================================================
# OUTPUT
# ==============================================================================
def _stream_dissolve_aggfunc(gdf: gpd.GeoDataFrame) -> dict[str, str]:
  """Sum Length and strmDrop; max other numerics; first for the rest."""
  sum_cols = {LENGTH, STRM_DROP}
  skip = {"geometry", "agg", SLOPE, RIVER_ID, NEXT_DOWN_ID}
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


def _slope_from_strm_drop_and_length(
  length_m: pd.Series, strm_drop: pd.Series, min_riv_slope: float
) -> pd.Series:
  length_m = pd.to_numeric(length_m, errors="coerce")
  strm_drop = pd.to_numeric(strm_drop, errors="coerce").fillna(0.0)
  slope = strm_drop / length_m.replace(0, np.nan)
  slope = slope.fillna(min_riv_slope).clip(lower=min_riv_slope)
  return slope.mask(slope >= 1.0, min_riv_slope)


def _cumulative_area_km2(g: AggregateGraph) -> dict[int, float]:
  total: dict[int, float] = {}
  for a in g.upstream_first(list(g.down)):
    total[a] = g.area[a] + sum(total[u] for u in g.inflows[a])
  return total


def _merge_lines(geom):
  if geom is None or geom.geom_type != "MultiLineString":
    return geom
  merged = shapely.line_merge(geom)
  return merged if not merged.is_empty else geom


def _straight_length(geom) -> float:
  """Distance between the first and last vertex of a reach."""
  if geom is None or geom.is_empty:
    return 0.0
  if geom.geom_type == "MultiLineString":
    coords = [xy for part in geom.geoms for xy in part.coords]
  elif geom.geom_type == "LineString":
    coords = list(geom.coords)
  else:
    return 0.0
  if len(coords) < 2:
    return 0.0
  (x0, y0), (x1, y1) = coords[0][:2], coords[-1][:2]
  return float(((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5)


def _validate_topology(
  basins: gpd.GeoDataFrame, rivers: gpd.GeoDataFrame, outlet_value: int
) -> None:
  ids = set(basins[RIVER_ID].astype(int)) | set(rivers[RIVER_ID].astype(int))

  def dangling(gdf: gpd.GeoDataFrame) -> list[int]:
    return sorted(
      d for d in set(gdf[NEXT_DOWN_ID].astype(int))
      if d not in ids and not _is_outlet_id(d, outlet_value)
    )

  bad_b, bad_r = dangling(basins), dangling(rivers)
  if bad_b or bad_r:
    raise ValueError(
      "Aggregated topology has DSLINKNO values with no matching LINKNO: "
      f"basins={bad_b[:10]}, rivers={bad_r[:10]}"
    )


def build_outputs(
  g: AggregateGraph,
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  original_basin_columns: list[str],
  original_river_columns: list[str],
  min_riv_slope: float = MIN_RIV_SLOPE,
  min_riv_length: float = MIN_RIV_LENGTH,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  outlet_value = g.outlet_value
  owner = {m: a for a, ms in g.members.items() for m in ms}
  stream_owner = {m: a for a, ms in g.stream_members.items() for m in ms}
  up_area_km2 = _cumulative_area_km2(g)

  def us_links(a: int) -> list[int]:
    ups = sorted(g.inflows[a], key=lambda u: -up_area_km2[u])
    return (ups + [-1, -1])[:2]

  # --- Basins: dissolve members; attributes from the survivor's own row ---
  basin = basin.copy()
  basin["agg"] = basin[BASIN_ID].map(owner)
  geom = basin[["agg", "geometry"]].dissolve(by="agg")
  keep_cols = [
    c for c in original_basin_columns
    if c in basin.columns and c not in ("geometry", BASIN_ID)
  ]
  for c in (NEXT_DOWN_ID, UP_AREA):
    if c in basin.columns and c not in keep_cols:
      keep_cols.append(c)
  attrs = basin.loc[basin[BASIN_ID] == basin["agg"]].drop_duplicates("agg").set_index("agg")[keep_cols]
  agg_basin = gpd.GeoDataFrame(attrs.join(geom), geometry="geometry", crs=basin.crs)
  agg_basin.index.name = RIVER_ID
  agg_basin = agg_basin.reset_index()
  agg_basin[AREA_KM2] = agg_basin[RIVER_ID].map(g.area)
  agg_basin[NEXT_DOWN_ID] = agg_basin[RIVER_ID].map(g.down).astype("int64")
  agg_basin[UP_AREA] = agg_basin[RIVER_ID].map(up_area_km2) / AREA_SCALE
  agg_basin[RIVER_ID] = agg_basin[RIVER_ID].astype("int64")
  if FRAC_LAKE_AREA in agg_basin.columns and LAKE_AREA in agg_basin.columns:
    lake_area = pd.to_numeric(agg_basin[LAKE_AREA], errors="coerce").fillna(0.0)
    agg_basin[FRAC_LAKE_AREA] = (lake_area / agg_basin[AREA_KM2].replace(0, np.nan)).fillna(0.0)
  agg_basin = agg_basin[[RIVER_ID] + [c for c in agg_basin.columns if c != RIVER_ID]]

  # --- Streams: dissolve each aggregate's channel reaches ---
  river = river.copy()
  river["agg"] = river[RIVER_ID].map(stream_owner)
  n_dropped = int(river["agg"].isna().sum())
  river = river[river["agg"].notna()].copy()
  river["agg"] = river["agg"].astype("int64")
  agg_river = river.dissolve(by="agg", aggfunc=_stream_dissolve_aggfunc(river), as_index=False)
  agg_river = agg_river.rename(columns={"agg": RIVER_ID})
  agg_river["geometry"] = agg_river.geometry.map(_merge_lines)
  if STRAIGHT_LEN in agg_river.columns:
    agg_river[STRAIGHT_LEN] = agg_river.geometry.map(_straight_length)
  agg_river[NEXT_DOWN_ID] = agg_river[RIVER_ID].map(g.down).astype("int64")
  agg_river[SLOPE] = _slope_from_strm_drop_and_length(
    agg_river[LENGTH], agg_river[STRM_DROP], min_riv_slope
  )
  agg_river[LENGTH] = agg_river[LENGTH].clip(lower=min_riv_length / LENGTH_SCALE)
  agg_river[UP_AREA] = agg_river[RIVER_ID].map(up_area_km2) / AREA_SCALE
  if US_AREA in agg_river.columns:
    agg_river[US_AREA] = (
      agg_river[RIVER_ID].map(up_area_km2) - agg_river[RIVER_ID].map(g.area)
    ) / AREA_SCALE
  for i, col in enumerate(US_LINK_COLS):
    if col in agg_river.columns:
      agg_river[col] = agg_river[RIVER_ID].map(lambda a: us_links(a)[i]).astype("int64")
  ordered = [c for c in original_river_columns if c in agg_river.columns]
  agg_river = agg_river[ordered + [c for c in agg_river.columns if c not in ordered]]
  if n_dropped:
    print(f"  Dropped {n_dropped} side-channel / basinless reach(es) from stream output.")

  _validate_topology(agg_basin, agg_river, outlet_value)
  return agg_basin, agg_river


def _membership_frame(g: AggregateGraph) -> pd.DataFrame:
  """Map every input basin id to the aggregate LINKNO that absorbed it."""
  rows = [
    {"member": int(member), "agg": int(agg)}
    for agg, members in g.members.items()
    for member in members
  ]
  return pd.DataFrame(rows, columns=["member", "agg"])


# ==============================================================================
# ENTRY POINTS
# ==============================================================================
def basin_aggregation(
  input_basin: gpd.GeoDataFrame,
  input_river: gpd.GeoDataFrame,
  min_sub_area: float = MIN_SUB_AREA,
  min_riv_slope: float = MIN_RIV_SLOPE,
  min_riv_length: float = MIN_RIV_LENGTH,
  outlet_value: int = OUTLET_VALUE,
  linear_merge: bool = LINEAR_MERGE_ENABLED,
  linear_mode: str = LINEAR_MERGE_MODE,
  half_min_frac: float = LINEAR_HALF_MIN_FRAC,
  max_combined_frac: float = LINEAR_MAX_COMBINED_FRAC,
  return_membership: bool = False,
):
  """Aggregate basins and streams; returns (basins, streams, merge log).

  With ``return_membership`` a fourth DataFrame maps every input basin
  (``member``) to the aggregate that absorbed it (``agg``).
  """
  basin, river = prepare_input_tables(input_basin, input_river, outlet_value)
  g = build_graph(basin, river, outlet_value)
  log = run_merges(
    g, min_sub_area, linear_merge, linear_mode, half_min_frac, max_combined_frac
  )
  agg_basin, agg_river = build_outputs(
    g, basin, river,
    list(input_basin.columns), list(input_river.columns),
    min_riv_slope, min_riv_length,
  )
  if return_membership:
    return agg_basin, agg_river, log, _membership_frame(g)
  return agg_basin, agg_river, log


def write_merge_log(log: list[dict], path: str = MERGE_LOG_CSV) -> None:
  os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
  fields = ["iteration", "pass", "source", "target", "rule", "source_area", "target_area"]
  with open(path, "w", newline="") as fh:
    writer = csv.DictWriter(fh, fieldnames=fields)
    writer.writeheader()
    writer.writerows(log)
  print(f"Wrote merge log ({len(log)} merge(s)): {path}")


def run_aggregation(
  basins_path: str = INPUT_BASINS,
  rivers_path: str = INPUT_RIVERS,
  output_basins_path: str = OUTPUT_BASINS,
  output_rivers_path: str = OUTPUT_RIVERS,
  merge_log_path: str = MERGE_LOG_CSV,
  **kwargs,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  """Load cleanGeofabric outputs, aggregate, and write shapefiles + merge log."""
  print(f"Loading basins: {basins_path}")
  basins = gpd.read_file(basins_path)
  print(f"Loading rivers: {rivers_path}")
  rivers = gpd.read_file(rivers_path)

  want_membership = hy_features_enabled(default=ENABLE_HY_FEATURES)
  if want_membership:
    agg_basins, agg_rivers, log, membership = basin_aggregation(
      basins, rivers, return_membership=True, **kwargs
    )
  else:
    agg_basins, agg_rivers, log = basin_aggregation(basins, rivers, **kwargs)

  from hy_features.export import export_shapefile_legacy

  os.makedirs(os.path.dirname(output_basins_path) or ".", exist_ok=True)
  os.makedirs(os.path.dirname(output_rivers_path) or ".", exist_ok=True)
  print(f"Writing aggregated basins ({len(agg_basins)} features): {output_basins_path}")
  export_shapefile_legacy(agg_basins, output_basins_path)
  print(f"Writing aggregated rivers ({len(agg_rivers)} features): {output_rivers_path}")
  export_shapefile_legacy(agg_rivers, output_rivers_path)
  write_merge_log(log, merge_log_path)

  if want_membership:
    from hy_features.aggregate import export_aggregated_geofabric

    export_aggregated_geofabric(
      agg_basins,
      agg_rivers,
      membership,
      WORKING_GEOFABRIC_AGG_GPKG,
      outlet_sentinel=int(kwargs.get("outlet_value", OUTLET_VALUE)),
    )

  return agg_basins, agg_rivers


if __name__ == "__main__":
  ensure_output_dirs()
  run_aggregation()
