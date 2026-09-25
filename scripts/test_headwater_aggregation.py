"""
Synthetic-network tests for code/basinAggregation.py merge rules.

Run from the repo root (needs study_settings.py there for pipeline_paths):
  python scripts/test_headwater_aggregation.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
os.environ.setdefault("LAKE_DELINEATION_ROOT", str(REPO))
sys.path.insert(0, str(REPO / "code"))

import geopandas as gpd  # noqa: E402
from shapely.geometry import LineString, box  # noqa: E402

import basinAggregation as ba  # noqa: E402

MIN = 100.0
OUT = ba.OUTLET_VALUE


def make_network(rows: list[dict]) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
  """rows: id, down, area, [length_km, lake, gauge, box]."""
  basins, streams = [], []
  for r in rows:
    geom = box(*r["box"]) if "box" in r else box(r["id"] * 10, 0, r["id"] * 10 + 1, 1)
    x0, y0, x1, y1 = geom.bounds
    basins.append({
      "DN": r["id"],
      "area_km2": r["area"],
      "is_lake": int(r.get("lake", 0)),
      "STATION_NU": r.get("gauge", ""),
      "geometry": geom,
    })
    streams.append({
      "LINKNO": r["id"],
      "DSLINKNO": r["down"],
      "USLINKNO1": -1,
      "USLINKNO2": -1,
      "Length": r.get("length_km", 1.0) * 1000.0,
      "strmDrop": 1.0,
      "Slope": 0.001,
      "DSContArea": 0.0,
      "geometry": LineString([(x0 + 0.1, y0 + 0.5), (x1 - 0.1, y0 + 0.5)]),
    })
  crs = "EPSG:3857"
  return gpd.GeoDataFrame(basins, crs=crs), gpd.GeoDataFrame(streams, crs=crs)


def aggregate(rows: list[dict], **kwargs):
  basins, streams = make_network(rows)
  agg_b, agg_r, log = ba.basin_aggregation(basins, streams, min_sub_area=MIN, **kwargs)
  groups = {r["id"]: {r["id"]} for r in rows}
  for entry in log:
    groups[entry["target"]] |= groups.pop(entry["source"])
  return groups, agg_b, agg_r, log


def check(name: str, rows: list[dict], expected: dict[int, set[int]], **kwargs) -> None:
  groups, agg_b, agg_r, _ = aggregate(rows, **kwargs)
  assert groups == expected, f"{name}: expected {expected}, got {groups}"
  ids = set(agg_b["LINKNO"])
  assert ids == set(expected), f"{name}: output ids {ids}"
  for down in agg_b["DSLINKNO"]:
    assert down == OUT or down in ids, f"{name}: dangling DSLINKNO {down}"
  total = sum(r["area"] for r in rows)
  outlets = agg_b[agg_b["DSLINKNO"] == OUT]
  assert abs(outlets["DSContArea"].sum() * ba.AREA_SCALE - total) < 1e-6, f"{name}: DSContArea"
  print(f"PASS {name}")


def main() -> None:
  check(
    "two headwaters: longer into shorter",
    [
      {"id": 1, "down": 3, "area": 30, "length_km": 5},
      {"id": 2, "down": 3, "area": 200, "length_km": 2},
      {"id": 3, "down": OUT, "area": 500},
    ],
    {2: {1, 2}, 3: {3}},
  )
  check(
    "3+ inflows, one non-headwater",
    [
      {"id": 10, "down": 11, "area": 300},
      {"id": 11, "down": 13, "area": 300},
      {"id": 12, "down": 13, "area": 20},
      {"id": 14, "down": 13, "area": 30},
      {"id": 13, "down": OUT, "area": 400},
    ],
    {10: {10}, 11: {11, 12, 14}, 13: {13}},
  )
  check(
    "several non-headwaters: most shared border",
    [
      {"id": 20, "down": 21, "area": 300},
      {"id": 21, "down": 26, "area": 300, "box": (0, 0, 1, 1)},
      {"id": 22, "down": 23, "area": 300},
      {"id": 23, "down": 26, "area": 300, "box": (1, 0, 2, 1)},
      {"id": 24, "down": 26, "area": 20, "box": (0.8, 1, 2, 2)},
      {"id": 26, "down": OUT, "area": 500},
    ],
    {20: {20}, 21: {21}, 22: {22}, 23: {23, 24}, 26: {26}},
  )
  check(
    "3+ all headwaters: into shortest",
    [
      {"id": 31, "down": 34, "area": 40, "length_km": 3},
      {"id": 32, "down": 34, "area": 150, "length_km": 1},
      {"id": 33, "down": 34, "area": 60, "length_km": 2},
      {"id": 34, "down": OUT, "area": 500},
    ],
    {32: {31, 32, 33}, 34: {34}},
  )
  check(
    "gauge headwater is not merged sideways",
    [
      {"id": 41, "down": 43, "area": 20, "gauge": "05AA001"},
      {"id": 42, "down": 43, "area": 20},
      {"id": 43, "down": OUT, "area": 500},
    ],
    {41: {41}, 42: {42}, 43: {43}},
  )
  check(
    "gauge absorbs upstream linear, but is not absorbed downstream",
    [
      {"id": 51, "down": 52, "area": 20},
      {"id": 52, "down": 53, "area": 300, "gauge": "05AA002"},
      {"id": 53, "down": OUT, "area": 20},
    ],
    {52: {51, 52}, 53: {53}},
  )
  check(
    "lakes are frozen",
    [
      {"id": 61, "down": 62, "area": 20},
      {"id": 62, "down": 63, "area": 300, "lake": 1},
      {"id": 63, "down": 66, "area": 20},
      {"id": 64, "down": 66, "area": 20, "lake": 1},
      {"id": 66, "down": OUT, "area": 500},
    ],
    {61: {61}, 62: {62}, 63: {63}, 64: {64}, 66: {66}},
  )
  check(
    "no sideways merge when the downstream basin is a lake",
    [
      {"id": 71, "down": 75, "area": 20, "length_km": 3},
      {"id": 72, "down": 75, "area": 30, "length_km": 1},
      {"id": 73, "down": 75, "area": 40, "length_km": 2},
      {"id": 74, "down": 75, "area": 300},
      {"id": 76, "down": 74, "area": 300},
      {"id": 75, "down": OUT, "area": 900, "lake": 1},
      {"id": 77, "down": 79, "area": 20, "length_km": 3},
      {"id": 78, "down": 79, "area": 30, "length_km": 1},
      {"id": 79, "down": OUT, "area": 900, "lake": 1},
    ],
    {i: {i} for i in (71, 72, 73, 74, 75, 76, 77, 78, 79)},
  )
  chain = [
    {"id": 81, "down": 82, "area": 80},
    {"id": 82, "down": 83, "area": 80},
    {"id": 83, "down": 84, "area": 80},
    {"id": 84, "down": OUT, "area": 80},
  ]
  check("linear capped: 2x cap", chain, {82: {81, 82}, 84: {83, 84}}, linear_mode="capped")
  check("linear all", chain, {84: {81, 82, 83, 84}}, linear_mode="all")
  check(
    "linear capped: below half-min ignores cap",
    [
      {"id": 85, "down": 86, "area": 40},
      {"id": 86, "down": OUT, "area": 900},
    ],
    {86: {85, 86}},
    linear_mode="capped",
  )
  check(
    "iterates: headwater -> linear -> new headwater",
    [
      {"id": 91, "down": 93, "area": 10, "length_km": 2},
      {"id": 92, "down": 93, "area": 10, "length_km": 1},
      {"id": 93, "down": 95, "area": 10, "length_km": 1},
      {"id": 94, "down": 95, "area": 300, "length_km": 5},
      {"id": 95, "down": OUT, "area": 500},
    ],
    {93: {91, 92, 93, 94}, 95: {95}},
  )
  print("All tests passed.")


if __name__ == "__main__":
  main()
