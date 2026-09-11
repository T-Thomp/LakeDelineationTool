"""
Aggregate small sub-basins and stream reaches for TauDEM / cleanGeofabric outputs.

Expected inputs (defaults match cleanGeofabric.py outputs):
  outputs/final/basins.shp
  outputs/final/streams.shp


"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
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
    headwater_links: set[int] | None = None,
) -> tuple[gpd.GeoDataFrame, list[dict]]:
    """Pick highest-uparea main stem per aggregate; stop and warn on DSLINKNO cycles."""
    agg_river = agg_river.copy()
    agg_river["mask"] = 0
    cycle_features: list[dict] = []
    reported_cycles: set[tuple[int, ...]] = set()
    up_map = upstream_by_node or {}
    hw_links = headwater_links or set()

    for agg_id in agg_river["agg"].dropna().unique():
        xx = agg_river.index[agg_river["agg"] == agg_id].tolist()
        visited_order: list[int] = []
        visited_set: set[int] = set()

        while xx:
            seed_link = _two_headwater_main_seed_link_for_agg(
              agg_river, agg_id, up_map, hw_links, riv_id_col
            )
            if seed_link is not None:
              seed_hit = xx
              seed_idx = [
                i
                for i in seed_hit
                if int(agg_river.loc[i, riv_id_col]) == int(seed_link)
              ]
              yy = seed_idx[0] if seed_idx else agg_river.loc[xx, "_uparea"].idxmax()
            else:
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
def _index_labels_by_value(series: pd.Series) -> dict[Any, list[Hashable]]:
  out: dict[Any, list[Hashable]] = defaultdict(list)
  for lab, val in series.items():
    out[val].append(lab)
  return out


def _index_first_label(series: pd.Series) -> dict[Any, Hashable]:
  out: dict[Any, Hashable] = {}
  for lab, val in series.items():
    if val not in out:
      out[val] = lab
  return out


def _reassign_aggdown(
  basin: gpd.GeoDataFrame,
  aggdown_index: dict[Any, list[Hashable]],
  labels: list[Hashable],
  new_val: object,
) -> None:
  if not labels:
    return
  old_vals = basin.loc[labels, "aggdown"]
  for lab, old in old_vals.items():
    bucket = aggdown_index.get(old)
    if not bucket:
      continue
    try:
      bucket.remove(lab)
    except ValueError:
      pass
    if not bucket:
      del aggdown_index[old]
  basin.loc[labels, "aggdown"] = new_val
  aggdown_index[new_val].extend(labels)


def _agg_id_series(series: pd.Series) -> pd.Series:
  """Nullable integer ids for consistent pandas merges (object vs int64)."""
  return pd.to_numeric(series, errors="coerce").astype("Int64")


def _link_ids_with_upstream(
  river: gpd.GeoDataFrame,
  down_col: str,
  outlet_value: int = OUTLET_VALUE,
) -> set[int]:
  """LINKNO/DN ids that have at least one upstream reach (fixed river topology)."""
  outlet_value = int(outlet_value)
  out: set[int] = set()
  for down_id in river[down_col].to_numpy():
    if _is_outlet_id(down_id, outlet_value):
      continue
    try:
      out.add(int(down_id))
    except (TypeError, ValueError):
      continue
  return out


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


def _links_immediately_upstream_of_gauge(
  basin: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
) -> set[int]:
  """Reach ids whose immediate downstream pour point is a gauge (never headwaters)."""
  out: set[int] = set()
  for gauge_id in _gauge_pour_link_ids(basin, id_col):
    ups = basin.loc[basin[down_col].astype(int) == int(gauge_id), id_col].astype(int)
    out.update(int(u) for u in ups)
  return out


def _river_next_down_id(
  river: gpd.GeoDataFrame,
  riv_id_col: str,
  down_col: str,
  link_id: int,
  outlet_value: int,
) -> int | None:
  rows = river.loc[river[riv_id_col].astype(int) == int(link_id)]
  if rows.empty:
    return None
  down_id = rows.iloc[0][down_col]
  if _is_outlet_id(down_id, outlet_value):
    return None
  try:
    return int(down_id)
  except (TypeError, ValueError):
    return None


def _basin_next_down_id(
  basin: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  link_id: int,
  outlet_value: int,
) -> int | None:
  rows = basin.loc[basin[id_col].astype(int) == int(link_id)]
  if rows.empty:
    return None
  down_id = rows.iloc[0][down_col]
  if _is_outlet_id(down_id, outlet_value):
    return None
  try:
    return int(down_id)
  except (TypeError, ValueError):
    return None


def _next_down_link(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  link_id: int,
  outlet_value: int,
) -> int | None:
  n = _river_next_down_id(river, riv_id_col, down_col, link_id, outlet_value)
  if n is not None:
    return n
  return _basin_next_down_id(basin, id_col, down_col, link_id, outlet_value)


def _build_combined_next_down_map(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  outlet_value: int,
) -> dict[int, int]:
  """Immediate downstream link id (river topology overrides basin when both exist)."""
  outlet_value = int(outlet_value)
  nxt: dict[int, int] = {}
  for link, down in zip(basin[id_col].to_numpy(), basin[down_col].to_numpy()):
    if _is_outlet_id(down, outlet_value):
      continue
    try:
      nxt[int(link)] = int(down)
    except (TypeError, ValueError):
      continue
  for link, down in zip(river[riv_id_col].to_numpy(), river[down_col].to_numpy()):
    if _is_outlet_id(down, outlet_value):
      continue
    try:
      nxt[int(link)] = int(down)
    except (TypeError, ValueError):
      continue
  return nxt


@dataclass
class _BasinMergeTopology:
  """Cached downstream walks (fixed DSLINKNO; safe across merge iterations)."""

  next_down: dict[int, int]
  _reach_cache: dict[tuple[int, int], bool] = field(default_factory=dict)

  @classmethod
  def from_frames(
    cls,
    basin: gpd.GeoDataFrame,
    river: gpd.GeoDataFrame,
    id_col: str,
    down_col: str,
    riv_id_col: str,
    outlet_value: int,
  ) -> _BasinMergeTopology:
    return cls(
      _build_combined_next_down_map(
        basin, river, id_col, down_col, riv_id_col, outlet_value
      )
    )

  def reaches_downstream(self, from_link: int, to_link: int) -> bool:
    from_link = int(from_link)
    to_link = int(to_link)
    if from_link == to_link:
      return True
    key = (from_link, to_link)
    hit = self._reach_cache.get(key)
    if hit is not None:
      return hit
    current: int | None = from_link
    seen: set[int] = set()
    while current is not None and current not in seen:
      if current == to_link:
        self._reach_cache[key] = True
        return True
      seen.add(current)
      current = self.next_down.get(current)
    self._reach_cache[key] = False
    return False


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


def _is_two_headwater_confluence(
  upstream_by_node: dict[int, list[int]],
  ds: int,
  headwater_links: set[int],
) -> bool:
  ups = upstream_by_node.get(int(ds), [])
  if len(ups) != 2:
    return False
  try:
    u1, u2 = int(ups[0]), int(ups[1])
  except (TypeError, ValueError):
    return False
  return u1 in headwater_links and u2 in headwater_links


def _river_link_uparea(
  agg_river: gpd.GeoDataFrame,
  riv_id_col: str,
  link_id: int,
) -> float:
  rows = agg_river.loc[agg_river[riv_id_col].astype(int) == int(link_id), "_uparea"]
  return float(rows.max()) if not rows.empty else 0.0


def _two_headwater_main_seed_link_for_agg(
  agg_river: gpd.GeoDataFrame,
  agg_id: object,
  upstream_by_node: dict[int, list[int]],
  headwater_links: set[int],
  riv_id_col: str,
) -> int | None:
  """
  At a two-headwater confluence inside ``agg_id``, return the upstream link id
  with larger cumulative area (main channel seed).
  """
  agg_rows = agg_river.loc[agg_river["agg"] == agg_id]
  if agg_rows.empty or not headwater_links:
    return None
  link_set = set(agg_rows[riv_id_col].astype(int))
  best_uparea = -1.0
  best_link: int | None = None
  for _ds, ups in upstream_by_node.items():
    if len(ups) != 2:
      continue
    u1, u2 = int(ups[0]), int(ups[1])
    if u1 not in headwater_links or u2 not in headwater_links:
      continue
    if u1 not in link_set or u2 not in link_set:
      continue
    a1 = _river_link_uparea(agg_river, riv_id_col, u1)
    a2 = _river_link_uparea(agg_river, riv_id_col, u2)
    winner = u1 if a1 >= a2 else u2
    aw = max(a1, a2)
    if aw > best_uparea:
      best_uparea = aw
      best_link = winner
  return best_link


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


def _aggregate_ids_with_headwater_pour(
  basin: gpd.GeoDataFrame,
  id_col: str,
  headwater_links: set[int],
) -> set[int]:
  """Aggregate ids that still include at least one network headwater pour point."""
  out: set[int] = set()
  for agg, link in zip(
    basin["agg"].astype(int).to_numpy(),
    basin[id_col].astype(int).to_numpy(),
  ):
    if int(link) in headwater_links:
      out.add(int(agg))
  return out


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


def _agg_to_link_ids(
  basin: gpd.GeoDataFrame,
  id_col: str,
) -> dict[int, tuple[int, ...]]:
  grouped = basin.groupby(basin["agg"].astype(int))[id_col].agg(
    lambda s: tuple(int(x) for x in s.astype(int).unique())
  )
  return {int(k): v for k, v in grouped.items()}


def _topology_reaches_downstream_of(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  from_link: int,
  to_link: int,
  outlet_value: int,
  topo: _BasinMergeTopology | None = None,
) -> bool:
  """True if ``to_link`` is reachable downstream from ``from_link`` (river, then basin)."""
  if topo is not None:
    return topo.reaches_downstream(from_link, to_link)
  outlet_value = int(outlet_value)
  current: int | None = int(from_link)
  seen: set[int] = set()
  while current is not None and current not in seen:
    if current == int(to_link):
      return True
    seen.add(current)
    current = _next_down_link(
      basin, river, id_col, down_col, riv_id_col, current, outlet_value
    )
  return False


def _river_reaches_downstream_of(
  river: gpd.GeoDataFrame,
  riv_id_col: str,
  down_col: str,
  from_link: int,
  to_link: int,
  outlet_value: int,
) -> bool:
  """True if ``to_link`` is on the downstream walk from ``from_link`` on the river graph."""
  outlet_value = int(outlet_value)
  current: int | None = int(from_link)
  seen: set[int] = set()
  while current is not None and current not in seen:
    if current == int(to_link):
      return True
    seen.add(current)
    current = _river_next_down_id(river, riv_id_col, down_col, current, outlet_value)
  return False


def _absorb_crosses_gauge_barrier(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  source_agg: int,
  target_agg: int,
  outlet_value: int = OUTLET_VALUE,
  topo: _BasinMergeTopology | None = None,
  gauge_links: set[int] | None = None,
  agg_links: dict[int, tuple[int, ...]] | None = None,
) -> bool:
  """
  True when absorbing ``source_agg`` into ``target_agg`` would fold reaches
  upstream of a gauge into the aggregate below that gauge on the same stem.
  """
  gauge_links = gauge_links if gauge_links is not None else _gauge_pour_link_ids(
    basin, id_col
  )
  if not gauge_links:
    return False

  if agg_links is not None:
    src_links = agg_links.get(int(source_agg), ())
    tgt_links = agg_links.get(int(target_agg), ())
  else:
    src_links = tuple(
      basin.loc[basin["agg"].astype(int) == int(source_agg), id_col]
      .astype(int)
      .unique()
    )
    tgt_links = tuple(
      basin.loc[basin["agg"].astype(int) == int(target_agg), id_col]
      .astype(int)
      .unique()
    )

  reach = topo.reaches_downstream if topo is not None else (
    lambda a, b: _topology_reaches_downstream_of(
      basin, river, id_col, down_col, riv_id_col, a, b, outlet_value
    )
  )

  for g in gauge_links:
    for s in src_links:
      if int(s) == g:
        continue
      if not reach(int(s), g):
        continue
      for t in tgt_links:
        if int(t) == g:
          continue
        if reach(g, int(t)):
          return True
  return False


def _merge_target_crosses_gauge_from_upstream_arms(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  upstream_link_ids: list[int],
  target_agg: int,
  gauge_links: set[int],
  outlet_value: int = OUTLET_VALUE,
  topo: _BasinMergeTopology | None = None,
  agg_links: dict[int, tuple[int, ...]] | None = None,
) -> bool:
  """
  True when folding tributaries at a junction into ``target_agg`` would bypass
  a gauge (any arm upstream of the gauge, target aggregate below it).
  """
  if not gauge_links:
    return False
  if agg_links is not None:
    tgt_links = agg_links.get(int(target_agg), ())
  else:
    tgt_links = tuple(
      basin.loc[basin["agg"].astype(int) == int(target_agg), id_col]
      .astype(int)
      .unique()
    )

  reach = topo.reaches_downstream if topo is not None else (
    lambda a, b: _topology_reaches_downstream_of(
      basin, river, id_col, down_col, riv_id_col, a, b, outlet_value
    )
  )

  for g in gauge_links:
    target_below_gauge = any(t != g and reach(g, int(t)) for t in tgt_links)
    if not target_below_gauge:
      continue
    for u in upstream_link_ids:
      if int(u) == g:
        continue
      if reach(int(u), g):
        return True
  return False


def _link_has_upstream(link_id: object, links_with_upstream: set[int]) -> bool:
  """True if any stream reach drains immediately into ``link_id``."""
  try:
    return int(link_id) in links_with_upstream
  except (TypeError, ValueError):
    return True


def _confluence_node_ids(
  river: gpd.GeoDataFrame,
  down_col: str,
  outlet_value: int = OUTLET_VALUE,
) -> set[int]:
  """Junction link ids with two or more immediate upstream reaches."""
  outlet_value = int(outlet_value)
  counts: dict[int, int] = defaultdict(int)
  for down_id in river[down_col].to_numpy():
    if _is_outlet_id(down_id, outlet_value):
      continue
    try:
      counts[int(down_id)] += 1
    except (TypeError, ValueError):
      continue
  return {node for node, n in counts.items() if n >= 2}


def _upstream_links_at_node(
  river: gpd.GeoDataFrame,
  down_col: str,
  riv_id_col: str,
  node_id: int,
) -> list[int]:
  node = int(node_id)
  ups = river.loc[river[down_col].astype(int) == node, riv_id_col]
  out: list[int] = []
  for val in ups.to_numpy():
    try:
      out.append(int(val))
    except (TypeError, ValueError):
      continue
  return out


def _agg_group_unit_area(basin: gpd.GeoDataFrame, agg_id: int) -> float:
  group = basin.loc[basin["agg"].astype(int) == int(agg_id)]
  return float(group["_unitarea"].sum()) if not group.empty else 0.0


def _pour_uparea(basin: gpd.GeoDataFrame, id_col: str, link_id: int) -> float:
  rows = basin.loc[basin[id_col].astype(int) == int(link_id), "_uparea"]
  return float(rows.iloc[0]) if not rows.empty else 0.0


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
  out: set[int] = set()
  for agg_val in agg_basin["agg"].dropna().unique():
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


def _link_is_gauge_or_lake_pour(
  basin: gpd.GeoDataFrame,
  id_col: str,
  link_id: int,
  lake_subs: set[int],
) -> bool:
  return _link_is_gauge_pour(basin, id_col, link_id) or _link_is_lake_pour(
    basin, id_col, link_id, lake_subs
  )


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
  """Lakes never merge; gauges may receive upstream merges but not merge downstream."""
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


def _confluence_has_gauge_or_lake_upstream(
  basin: gpd.GeoDataFrame,
  id_col: str,
  upstream_link_ids: list[int],
  lake_subs: set[int],
) -> bool:
  """True if any immediate upstream arm at a junction is a gauge or reservoir."""
  return any(
    _link_is_gauge_or_lake_pour(basin, id_col, int(u), lake_subs)
    for u in upstream_link_ids
  )


def _headwater_blocked_by_confluence_partner(
  basin: gpd.GeoDataFrame,
  river: gpd.GeoDataFrame,
  id_col: str,
  down_col: str,
  riv_id_col: str,
  headwater_link: int,
  down_link: int,
  lake_subs: set[int],
  outlet_value: int = OUTLET_VALUE,
) -> bool:
  """
  Do not absorb a headwater into a downstream link that is a confluence
  with a gauge or reservoir on another upstream arm.
  """
  outlet_value = int(outlet_value)
  try:
    down = int(down_link)
    hw = int(headwater_link)
  except (TypeError, ValueError):
    return True
  if down not in _confluence_node_ids(river, down_col, outlet_value):
    return False
  ups = _upstream_links_at_node(river, down_col, riv_id_col, down)
  for u in ups:
    if int(u) == hw:
      continue
    if _link_is_gauge_or_lake_pour(basin, id_col, int(u), lake_subs):
      return True
  return False


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


def _absorb_table_from_targets(
  rows: list[tuple[int, int]],
  agg_basin: pd.DataFrame,
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
  return pd.DataFrame(
    {
      "aggold": merged["aggold"],
      "agg": merged["target_agg"],
      "aggdown": merged["new_aggdown"],
    }
  )


def _agg_primary_aggdown(agg_basin: pd.DataFrame, agg_id: int) -> object:
  rows = agg_basin.loc[agg_basin["agg"].astype(int) == int(agg_id), "aggdown"]
  if rows.empty:
    return None
  return rows.iloc[0]


def build_headwater_driven_absorb_table(
  basin: gpd.GeoDataFrame,
  agg_basin: pd.DataFrame,
  id_col: str,
  lake_subs: set[int],
  post_lake_subs: set[int],
  upstream_by_node: dict[int, list[int]],
  headwater_links: set[int],
  link_depth: dict[int, int],
  min_sub_area: float,
  outlet_value: int = OUTLET_VALUE,
) -> pd.DataFrame:
  """
  One merge wave per call (caller loops until empty):

  * Find all small eligible aggregates, then take the **frontier**: those at the
    minimum network depth (most upstream on the graph this round).
  * Each frontier unit merges into the aggregate at its ``aggdown`` downstream
    link, ordered shallowest-first along the network.
  * Other **small** upstream arms sharing that downstream link join the same
    target (basin area only).
  * **Two headwater** reaches at the same downstream link: when this junction
    merges, **both** upstream aggregates join the downstream target; streams
    follow the higher-``_uparea`` headwater through the downstream segment.
  * **Three or more** upstream reaches at the same downstream link: no merge.

  After applies, the next iteration rediscovers frontier headwaters further DS.

  Lakes and gauge units are never absorbed; upstream may merge into a gauge.
  """
  outlet_value = int(outlet_value)
  survivors = set(agg_basin["agg"].astype(int))
  gauge_links = _gauge_pour_link_ids(basin, id_col)
  pour_agg = _pour_agg_by_link(basin, id_col)
  id_to_agg = _id_to_agg_map(basin, id_col)
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
    ds_raw = _agg_primary_aggdown(agg_basin, agg_i)
    if ds_raw is None or _is_outlet_id(ds_raw, outlet_value):
      continue
    try:
      ds = int(ds_raw)
    except (TypeError, ValueError):
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
    if len(upstream_by_node.get(ds, [])) >= 3:
      continue
    target = _current_agg_for_basin_id(
      basin,
      id_col,
      ds,
      survivor_ids=survivors,
      gauge_link_ids=gauge_links,
      pour_agg=pour_agg,
      id_to_agg=id_to_agg,
    )
    if target is None:
      continue
    target_i = int(target)
    if _agg_is_lake_group(basin, target_i):
      continue
    if not _linear_merge_may_apply_to_target(
      basin, id_col, ds, target_i, lake_subs
    ):
      continue

    sources: set[int] = set(int(a) for a in hw_aggs)
    for u in upstream_by_node.get(ds, []):
      u_agg = _current_agg_for_basin_id(
        basin,
        id_col,
        u,
        survivor_ids=survivors,
        gauge_link_ids=gauge_links,
        pour_agg=pour_agg,
        id_to_agg=id_to_agg,
      )
      if u_agg is None:
        continue
      u_agg_i = int(u_agg)
      if u_agg_i == target_i or u_agg_i in sources:
        continue
      if _agg_group_unit_area(basin, u_agg_i) >= min_sub_area:
        continue
      if not _aggregate_may_be_absorbed(basin, u_agg_i, id_col, post_lake_subs):
        continue
      if _agg_is_lake_group(basin, u_agg_i):
        continue
      sources.add(u_agg_i)

    if _is_two_headwater_confluence(upstream_by_node, ds, headwater_links):
      for u in upstream_by_node.get(ds, []):
        u_agg = _current_agg_for_basin_id(
          basin,
          id_col,
          u,
          survivor_ids=survivors,
          gauge_link_ids=gauge_links,
          pour_agg=pour_agg,
          id_to_agg=id_to_agg,
        )
        if u_agg is None:
          continue
        u_agg_i = int(u_agg)
        if u_agg_i == target_i:
          continue
        if _agg_is_lake_group(basin, u_agg_i):
          continue
        if not _aggregate_may_be_absorbed(basin, u_agg_i, id_col, post_lake_subs):
          continue
        sources.add(u_agg_i)

    for aggold in sorted(
      sources,
      key=lambda a: (_agg_min_link_depth(basin, id_col, int(a), link_depth), int(a)),
    ):
      if aggold == target_i or aggold in seen_source:
        continue
      seen_source.add(aggold)
      rows.append((aggold, target_i))

  table = _absorb_table_from_targets(rows, agg_basin)
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


def absorb_headwater_groups(
  basin: gpd.GeoDataFrame,
  xx_df: pd.DataFrame,
  outlet_value: int = OUTLET_VALUE,
  links_with_upstream: set[int] | None = None,
  allow_with_upstream: bool = False,
  id_col: str = BASIN_ID,
  post_lake_subs: set[int] | None = None,
  river: gpd.GeoDataFrame | None = None,
  down_col: str = NEXT_DOWN_ID,
  riv_id_col: str = RIVER_ID,
  topo: _BasinMergeTopology | None = None,
  gauge_links: set[int] | None = None,
  agg_links: dict[int, tuple[int, ...]] | None = None,
) -> gpd.GeoDataFrame:
  """Same as: loc[agg==aggold, aggdown]=...; loc[agg==aggold, agg]=..."""
  agg_index = _index_labels_by_value(basin["agg"])
  upstream_links = links_with_upstream or set()
  post_lake = post_lake_subs or set()
  if river is not None and gauge_links is None:
    gauge_links = _gauge_pour_link_ids(basin, id_col)
  if river is not None and agg_links is None:
    agg_links = _agg_to_link_ids(basin, id_col)
  for i in range(len(xx_df)):
    aggold = xx_df["aggold"].iloc[i]
    new_agg = xx_df["agg"].iloc[i]
    new_aggdown = xx_df["aggdown"].iloc[i]
    if not allow_with_upstream and _link_has_upstream(aggold, upstream_links):
      continue
    if pd.isna(new_agg) or _is_sentinel_object_id(new_agg, outlet_value):
      continue
    try:
      aggold_i = int(aggold)
      new_agg_i = int(new_agg)
    except (TypeError, ValueError):
      continue
    if not _aggregate_may_be_absorbed(basin, aggold_i, id_col, post_lake):
      continue
    if _agg_is_lake_group(basin, new_agg_i):
      continue
    if _agg_is_gauge_group(basin, aggold_i):
      continue
    labels = agg_index.get(aggold)
    if not labels:
      continue
    labels = list(labels)
    basin.loc[labels, "aggdown"] = new_aggdown
    basin.loc[labels, "agg"] = new_agg
    if aggold != new_agg:
      agg_index[new_agg].extend(labels)
      del agg_index[aggold]
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
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  """
  Aggregate basins and rivers based on drainage area, slope, and reservoir masking.

  ``min_sub_area`` is a **floor** for keeping a subbasin as its own aggregate
  (local ``_unitarea`` below it may be merged away). It is **not** a cap:
  absorbed polygons sum onto the downstream ``agg`` and the result may be
  well above ``min_sub_area``.

  Returns aggregated basin and river GeoDataFrames. Each basin is identified by
  BASIN_ID. Gauge IDs, lake flags/IDs, lake area, and fractional lake area are
  pour-point attributes only.

  Terminal basins receive ``outlet_value`` in ``DSLINKNO`` (default ``OUTLET_VALUE``).
  """
  outlet_value = int(outlet_value)
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

  def _drop_small_outlets(df: pd.DataFrame) -> pd.DataFrame:
    """Drop terminal subbasins too small to keep; lakes always stay in the merge graph."""
    is_outlet = df["aggdown"].map(lambda d: _is_outlet_id(d, outlet_value))
    return df[(~(is_outlet & (df["_uparea"] < min_sub_area))) | (df["Mask"] == 3)]

  agg_basin = basin[["agg", "aggdown", "_unitarea", "_uparea", "Mask"]].copy()
  agg_basin = _drop_small_outlets(agg_basin)
  lake_subs = set(basin.loc[basin["Mask"] == 3, "agg"].astype(int))
  post_lake_subs = _downstream_basin_ids_of_lakes(
    basin, river, id_col, down_col, riv_id_col, outlet_value
  )
  upstream_by_node = _upstream_links_by_down_node(river, down_col, riv_id_col)
  headwater_links = _headwater_link_ids(river, riv_id_col, upstream_by_node)
  link_depth = _link_depth_from_headwaters(upstream_by_node, headwater_links)
  no_subbasin = len(basin)
  max_merge_iters = max(len(basin) * 2, 1000)
  merge_iter = 0
  print(
    f"Basin merge (frontier headwaters → DS): {len(basin)} pour point(s), "
    f"max {max_merge_iters} iteration(s)."
  )

  while True:
    merge_iter += 1
    if merge_iter > max_merge_iters:
      raise RuntimeError(
        f"Basin merge loop did not converge after {max_merge_iters} iterations. "
        "Small subbasins may be oscillating without merging. "
        "Check DSLINKNO / LINKNO topology in outputs/final/streams.shp "
        f"(and {TOPOLOGY_CYCLES_SHP} if river cycles were detected)."
      )
    applied_any = False
    xx = build_headwater_driven_absorb_table(
      basin,
      agg_basin,
      id_col=id_col,
      lake_subs=lake_subs,
      post_lake_subs=post_lake_subs,
      upstream_by_node=upstream_by_node,
      headwater_links=headwater_links,
      link_depth=link_depth,
      min_sub_area=min_sub_area,
      outlet_value=outlet_value,
    )
    if not xx.empty:
      xx = _sort_absorb_table_upstream_first(basin, id_col, link_depth, xx)
      basin = absorb_headwater_groups(
        basin,
        xx,
        outlet_value=outlet_value,
        allow_with_upstream=True,
        id_col=id_col,
        post_lake_subs=post_lake_subs,
      )
      applied_any = True
      agg_basin = basin.drop(columns="geometry").groupby(["agg", "aggdown"], as_index=False).agg(
        {"_unitarea": "sum"}
      )
      agg_basin = agg_basin.rename(columns={"agg": id_col, "aggdown": down_col})
      agg_basin = agg_basin.merge(basin[[id_col, "_uparea", "Mask"]], on=id_col, how="left")
      agg_basin = agg_basin.rename(columns={id_col: "agg", down_col: "aggdown"})
      agg_basin = _drop_small_outlets(agg_basin)

    if not applied_any:
      break

    if len(agg_basin[agg_basin["_unitarea"] < min_sub_area]) == no_subbasin:
      break
    no_subbasin = len(agg_basin[agg_basin["_unitarea"] < min_sub_area])

  print(f"Basin merge loop finished after {merge_iter} iteration(s).")

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

  attr_cols = ["aggdown", "_uparea"] + _basin_attr_cols(basin)
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
    agg_river,
    down_col,
    riv_id_col,
    upstream_by_node=upstream_by_node,
    headwater_links=headwater_links,
  )
  _export_topology_cycles(cycle_features, agg_river.crs)
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

  unit_out = UNIT_AREA or "area_km2"
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
