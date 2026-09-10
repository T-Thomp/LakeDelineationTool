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

MIN_SUB_AREA = 100.0          # km² — merge subbasins whose local area (_unitarea) is below this
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


def _mark_river_main_stems(
    agg_river: gpd.GeoDataFrame,
    down_col: str,
    riv_id_col: str,
) -> tuple[gpd.GeoDataFrame, list[dict]]:
    """Pick highest-uparea main stem per aggregate; stop and warn on DSLINKNO cycles."""
    agg_river = agg_river.copy()
    agg_river["mask"] = 0
    cycle_features: list[dict] = []
    reported_cycles: set[tuple[int, ...]] = set()

    for agg_id in agg_river["agg"].dropna().unique():
        xx = agg_river.index[agg_river["agg"] == agg_id].tolist()
        visited_order: list[int] = []
        visited_set: set[int] = set()

        while xx:
            yy = agg_river.loc[xx, "_uparea"].idxmax()
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


def _current_agg_for_basin_id(
  basin: gpd.GeoDataFrame,
  id_col: str,
  basin_id: object,
  survivor_ids: set[int] | None = None,
) -> int | None:
  """
  Aggregate id for the basin whose pour-point id is ``basin_id``.

  When that pour point was already absorbed into a downstream group, returns
  the surviving ``agg`` (not the original link id). Used so headwater merges
  still attach after an intermediate confluence basin has been absorbed.
  """
  try:
    bid = int(basin_id)
  except (TypeError, ValueError):
    return None

  rows = basin.loc[basin[id_col].astype(int) == bid, "agg"]
  if not rows.empty:
    return int(rows.iloc[0])

  if survivor_ids is None:
    survivor_ids = set(basin["agg"].astype(int).unique())
  id_to_agg = {
    int(orig): int(agg)
    for orig, agg in zip(basin[id_col].to_numpy(), basin["agg"].to_numpy())
  }
  down = bid
  seen: set[int] = set()
  while down not in survivor_ids:
    if down not in id_to_agg or down in seen:
      return None
    seen.add(down)
    down = id_to_agg[down]
  return down


def build_headwater_absorb_table(
  small_subbasin: pd.DataFrame,
  agg_basin: pd.DataFrame,
  basin: gpd.GeoDataFrame,
  id_col: str,
) -> pd.DataFrame:
  """
  Rows for ``absorb_headwater_groups``: map each headwater onto the current
  survivor aggregate of its immediate downstream basin (not a stale agg id).
  """
  if small_subbasin.empty:
    return pd.DataFrame(columns=["aggold", "agg", "aggdown"])

  survivors = set(agg_basin["agg"].astype(int))
  hw = small_subbasin.rename(columns={"agg": "aggold"}).copy()
  hw["target_agg"] = _agg_id_series(
    pd.Series(
      [
        _current_agg_for_basin_id(basin, id_col, down, survivor_ids=survivors)
        for down in hw["aggdown"].tolist()
      ],
      index=hw.index,
    )
  )
  hw["aggold"] = _agg_id_series(hw["aggold"])
  # Headwater ``aggdown`` is the downstream link id; merge target group's ``aggdown``
  # from agg_basin only (avoid pandas _x/_y suffix collision).
  targets = agg_basin[["agg", "aggdown"]].rename(columns={"aggdown": "new_aggdown"})
  targets["agg"] = _agg_id_series(targets["agg"])
  hw = hw[hw["target_agg"].notna()]
  if hw.empty:
    return pd.DataFrame(columns=["aggold", "agg", "aggdown"])
  merged = hw[["aggold", "target_agg"]].merge(
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


def build_survivor_downstream_absorb_table(
  candidates: pd.DataFrame,
  agg_basin: pd.DataFrame,
  basin: gpd.GeoDataFrame,
  id_col: str,
) -> pd.DataFrame:
  """
  Small basins that are not headwaters but drain to a link whose aggregate
  was already absorbed (e.g. sibling left behind after internal confluence merge).
  """
  if candidates.empty:
    return pd.DataFrame(columns=["aggold", "agg", "aggdown"])

  survivors = set(agg_basin["agg"].astype(int))
  rows: list[dict[str, object]] = []
  targets = agg_basin[["agg", "aggdown"]].rename(columns={"aggdown": "new_aggdown"})

  for aggold, down_link in zip(
    candidates["agg"].tolist(),
    candidates["aggdown"].tolist(),
  ):
    target = _current_agg_for_basin_id(
      basin, id_col, down_link, survivor_ids=survivors
    )
    if target is None or int(target) == int(aggold):
      continue
    rows.append({"aggold": aggold, "target_agg": target})

  if not rows:
    return pd.DataFrame(columns=["aggold", "agg", "aggdown"])

  pending = pd.DataFrame(rows)
  pending["aggold"] = _agg_id_series(pending["aggold"])
  pending["target_agg"] = _agg_id_series(pending["target_agg"])
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


def absorb_headwater_groups(
  basin: gpd.GeoDataFrame,
  xx_df: pd.DataFrame,
  outlet_value: int = OUTLET_VALUE,
) -> gpd.GeoDataFrame:
  """Same as: loc[agg==aggold, aggdown]=...; loc[agg==aggold, agg]=..."""
  agg_index = _index_labels_by_value(basin["agg"])
  for i in range(len(xx_df)):
    aggold = xx_df["aggold"].iloc[i]
    new_agg = xx_df["agg"].iloc[i]
    new_aggdown = xx_df["aggdown"].iloc[i]
    if pd.isna(new_agg) or _is_sentinel_object_id(new_agg, outlet_value):
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


def absorb_internal_groups(
  basin: gpd.GeoDataFrame,
  small_subbasin: pd.DataFrame,
  id_col: str,
  down_col: str,
  min_sub_area: float,
) -> gpd.GeoDataFrame:
  """Same statements and read-after-write order as the original internal-merge loop."""
  id_first = _index_first_label(basin[id_col])
  down_index = _index_labels_by_value(basin[down_col])
  agg_index = _index_labels_by_value(basin["agg"])
  aggdown_index = _index_labels_by_value(basin["aggdown"])

  for i in range(len(small_subbasin)):
    cand = small_subbasin["agg"].iloc[i]
    if cand not in id_first:
      raise IndexError(
        f"internal merge: no row with {id_col}=={cand!r} (matches original .index[0] failure)"
      )
    xx = id_first[cand]
    xx_agg = basin.at[xx, "agg"]
    group_xx = agg_index.get(xx_agg, [])
    group_area = (
      float(basin.loc[group_xx, "_unitarea"].sort_index().sum()) if group_xx else 0.0
    )
    if group_area >= min_sub_area:
      continue

    xx_id = basin.at[xx, id_col]
    xy = down_index.get(xx_id)
    if not xy:
      continue

    xz = basin.loc[xy, "_uparea"].idxmax()
    if not (basin.at[xz, "Mask"] < 2):
      continue

    xx_aggdown = basin.at[xx, "aggdown"]
    xz_agg_before = basin.at[xz, "agg"]
    xz_main_labels = list(agg_index.get(xz_agg_before, ()))

    # All-or-nothing at this confluence: every mergeable upstream trib joins
    # together with the confluence unit, or none do (avoids one branch merged
    # while a sibling stays separate on the same junction).
    to_merge: list[tuple[Hashable, Any, list[Hashable], float]] = []
    total_area = group_area
    for y_label in xy:
      y_agg_before = basin.at[y_label, "agg"]
      if y_agg_before == xx_agg:
        continue
      y_group = list(agg_index.get(y_agg_before, ()))
      y_area = (
        float(basin.loc[y_group, "_unitarea"].sort_index().sum()) if y_group else 0.0
      )
      if y_area >= min_sub_area or not (basin.at[y_label, "Mask"] < 2):
        continue
      to_merge.append((y_label, y_agg_before, y_group, y_area))
      total_area += y_area

    if not to_merge or total_area >= min_sub_area:
      continue

    for _y_label, y_agg_before, _y_group, _y_area in to_merge:
      pos1 = list(agg_index.get(y_agg_before, ()))
      if pos1:
        basin.loc[pos1, "agg"] = xx_agg
        if y_agg_before != xx_agg:
          agg_index[xx_agg].extend(pos1)
          del agg_index[y_agg_before]

      zz_labels = list(aggdown_index.get(y_agg_before, ()))
      if zz_labels:
        _reassign_aggdown(basin, aggdown_index, zz_labels, xx_agg)

    if xz_main_labels:
      _reassign_aggdown(basin, aggdown_index, xz_main_labels, xx_aggdown)

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
  no_subbasin = len(basin)
  max_merge_iters = max(len(basin) * 2, 1000)
  merge_iter = 0

  while True:
    merge_iter += 1
    if merge_iter > max_merge_iters:
      raise RuntimeError(
        f"Basin merge loop did not converge after {max_merge_iters} iterations. "
        "Small subbasins may be oscillating without merging. "
        "Check DSLINKNO / LINKNO topology in outputs/final/streams.shp "
        f"(and {TOPOLOGY_CYCLES_SHP} if river cycles were detected)."
      )
    headwaters = (
      ~agg_basin["agg"].isin(agg_basin["aggdown"])
      & (agg_basin["_unitarea"] < min_sub_area)
      & (agg_basin["Mask"] < 2)
    )
    small_subbasin = agg_basin[headwaters]
    small_subbasin = small_subbasin[
      ~small_subbasin["aggdown"].isin(lake_subs)
      # Post-lake basins may receive upstream headwaters (aggdown -> them) but must
      # not themselves be absorbed into a basin further downstream (agg -> blocked).
      & ~small_subbasin["agg"].isin(post_lake_subs)
    ].sort_values(by="_uparea", ascending=False)
    if not small_subbasin.empty:
      xx = build_headwater_absorb_table(
        small_subbasin, agg_basin, basin, id_col=id_col
      )
      basin = absorb_headwater_groups(basin, xx, outlet_value=outlet_value)
      agg_basin = basin.drop(columns="geometry").groupby(["agg", "aggdown"], as_index=False).agg(
        {"_unitarea": "sum"}
      )
      agg_basin = agg_basin.rename(columns={"agg": id_col, "aggdown": down_col})
      agg_basin = agg_basin.merge(basin[[id_col, "_uparea", "Mask"]], on=id_col, how="left")
      agg_basin = agg_basin.rename(columns={id_col: "agg", down_col: "aggdown"})
      agg_basin = _drop_small_outlets(agg_basin)

    # Non-headwater small basins whose immediate downstream link already belongs
    # to another aggregate (typical confluence sibling left after internal merge).
    survivor_cands = agg_basin[
      agg_basin["agg"].isin(agg_basin["aggdown"])
      & (agg_basin["_unitarea"] < min_sub_area)
      & (agg_basin["Mask"] < 2)
      & ~agg_basin["aggdown"].isin(lake_subs)
      & ~agg_basin["agg"].isin(post_lake_subs)
    ]
    if not survivor_cands.empty:
      xx_surv = build_survivor_downstream_absorb_table(
        survivor_cands, agg_basin, basin, id_col=id_col
      )
      if not xx_surv.empty:
        basin = absorb_headwater_groups(basin, xx_surv, outlet_value=outlet_value)
        agg_basin = basin.drop(columns="geometry").groupby(
          ["agg", "aggdown"], as_index=False
        ).agg({"_unitarea": "sum"})
        agg_basin = agg_basin.rename(columns={"agg": id_col, "aggdown": down_col})
        agg_basin = agg_basin.merge(
          basin[[id_col, "_uparea", "Mask"]], on=id_col, how="left"
        )
        agg_basin = agg_basin.rename(columns={id_col: "agg", down_col: "aggdown"})
        agg_basin = _drop_small_outlets(agg_basin)

    condition = (
      agg_basin["agg"].isin(agg_basin["aggdown"])
      & (agg_basin["_unitarea"] < min_sub_area)
      & (agg_basin["Mask"] != 3)
    )
    small_subbasin = agg_basin[condition].sort_values(by="_uparea", ascending=False)
    if not small_subbasin.empty:
      basin = absorb_internal_groups(
        basin,
        small_subbasin,
        id_col=id_col,
        down_col=down_col,
        min_sub_area=min_sub_area,
      )
      agg_basin = basin.drop(columns="geometry").groupby(["agg", "aggdown"], as_index=False).agg(
        {"_unitarea": "sum"}
      )
      agg_basin = agg_basin.rename(columns={"agg": id_col, "aggdown": down_col})
      agg_basin = agg_basin.merge(basin[[id_col, "_uparea", "Mask"]], on=id_col, how="left")
      agg_basin = agg_basin.rename(columns={id_col: "agg", down_col: "aggdown"})
      agg_basin = _drop_small_outlets(agg_basin)

    if len(agg_basin[agg_basin["_unitarea"] < min_sub_area]) == no_subbasin:
      break
    no_subbasin = len(agg_basin[agg_basin["_unitarea"] < min_sub_area])

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
  agg_river, cycle_features = _mark_river_main_stems(agg_river, down_col, riv_id_col)
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
