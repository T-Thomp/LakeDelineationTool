"""Stream-conditioning weights and chained valley links."""

import sys
from types import ModuleType

import numpy as np
import pytest


def _ensure_osgeo():
    """Allow these tests to import on a machine without the GDAL Python bindings."""
    try:
        from osgeo import gdal  # noqa: F401
    except ModuleNotFoundError:
        osgeo = ModuleType("osgeo")
        for name in ("gdal", "ogr", "osr"):
            module = ModuleType(f"osgeo.{name}")
            setattr(osgeo, name, module)
            sys.modules[f"osgeo.{name}"] = module
        osgeo.gdal.UseExceptions = lambda: None
        sys.modules["osgeo"] = osgeo


_ensure_osgeo()

from conditionStreams import (
    DIST_WEIGHT,
    VALLEY_SCALE,
    apply_path_to_fdr,
    build_cell_cost,
    continuation_mask,
    drain_end_mask,
    load_paths_csv,
    parse_weight,
)


def test_blank_and_missing_weights_use_defaults(tmp_path):
    csv_path = tmp_path / "streams.csv"
    csv_path.write_text(
        "id,start_lat,start_lon,end_lat,end_lon,valley_weight,end_weight\n"
        "blank,51.0,-115.0,51.1,-115.1,,\n"
        "spaces,51.0,-115.0,51.2,-115.2, , \n"
        "custom,51.0,-115.0,51.3,-115.3,12.5,3\n",
        encoding="utf-8",
    )
    labels, _starts, _ends, valley_weights, end_weights = load_paths_csv(
        csv_path, "EPSG:4326",
    )
    assert labels == ["blank", "spaces", "custom"]
    assert valley_weights == [VALLEY_SCALE, VALLEY_SCALE, 12.5]
    assert end_weights == [DIST_WEIGHT, DIST_WEIGHT, 3.0]


def test_omitted_weight_columns_use_defaults(tmp_path):
    csv_path = tmp_path / "streams.csv"
    csv_path.write_text(
        "start_lat,start_lon,end_lat,end_lon\n"
        "51.0,-115.0,51.1,-115.1\n",
        encoding="utf-8",
    )
    labels, _starts, _ends, valley_weights, end_weights = load_paths_csv(
        csv_path, "EPSG:4326",
    )
    assert labels == ["row 1"]
    assert valley_weights == [VALLEY_SCALE]
    assert end_weights == [DIST_WEIGHT]


def test_non_numeric_weight_is_rejected(tmp_path):
    csv_path = tmp_path / "streams.csv"
    csv_path.write_text(
        "id,start_lat,start_lon,end_lat,end_lon,valley_weight,end_weight\n"
        "bad,51.0,-115.0,51.1,-115.1,steep,\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="valley_weight"):
        load_paths_csv(csv_path, "EPSG:4326")


def test_parse_weight_blank_cases():
    assert parse_weight(None, 4.0, "valley_weight", "row 1") == 4.0
    assert parse_weight("  ", 4.0, "valley_weight", "row 1") == 4.0
    assert parse_weight(np.nan, 4.0, "end_weight", "row 1") == 4.0
    assert parse_weight("0", 4.0, "end_weight", "row 1") == 0.0


def test_chain_drains_only_the_final_link():
    starts = [(0, 0), (5, 1), (9, 2)]
    ends = [(5, 1), (9, 2), (12, 4)]
    routed = [True, True, True]
    assert continuation_mask(starts, ends) == [False, True, True]
    assert drain_end_mask(starts, ends, routed) == [False, False, True]


def test_unrelated_links_both_drain():
    starts = [(0, 0), (3, 3)]
    ends = [(1, 0), (4, 3)]
    assert continuation_mask(starts, ends) == [False, False]
    assert drain_end_mask(starts, ends, [True, True]) == [True, True]


def test_failed_continuation_still_lets_the_upstream_link_drain():
    starts = [(0, 0), (4, 1)]
    ends = [(4, 1), (8, 2)]
    assert continuation_mask(starts, ends) == [False, True]
    assert drain_end_mask(starts, ends, [True, False]) == [True, True]


def test_a_link_does_not_chain_to_itself():
    starts = [(2, 2)]
    ends = [(2, 2)]
    assert continuation_mask(starts, ends) == [False]
    assert drain_end_mask(starts, ends, [True]) == [True]


def test_valley_weight_raises_the_cost_of_high_ground():
    dem = np.array([[0.0, 0.0], [0.0, 10.0]], dtype=np.float64)
    valid = np.ones(dem.shape, dtype=bool)
    plain, _ = build_cell_cost(dem, valid, (0, 0), valley_weight=0, end_weight=0)
    steep, _ = build_cell_cost(dem, valid, (0, 0), valley_weight=400, end_weight=0)
    assert steep[1, 1] > plain[1, 1]
    assert steep[0, 0] == pytest.approx(plain[0, 0])


def test_end_weight_costs_more_farther_from_the_end():
    dem = np.zeros((3, 3), dtype=np.float64)
    valid = np.ones(dem.shape, dtype=bool)
    flat, _ = build_cell_cost(dem, valid, (1, 1), valley_weight=0, end_weight=0)
    pulled, _ = build_cell_cost(dem, valid, (1, 1), valley_weight=0, end_weight=5)
    assert pulled[1, 1] == pytest.approx(flat[1, 1])
    assert pulled[0, 0] > flat[0, 0]


def _valley_grid():
    """A west-flowing grid whose east side already drains off the path."""
    fdr = np.full((5, 5), 5, dtype=np.uint8)
    fdr[2, 3] = 1
    fdr[2, 4] = 1
    dem = np.zeros((5, 5), dtype=np.float64)
    valid = np.ones(dem.shape, dtype=bool)
    lakes = np.zeros(dem.shape, dtype=bool)
    path = [(2, 0), (2, 1), (2, 2)]
    return fdr, path, lakes, dem, valid


def test_intermediate_link_does_not_search_downhill():
    fdr, path, lakes, dem, valid = _valley_grid()
    updated, drained = apply_path_to_fdr(
        fdr, path, lakes, dem, valid, drain_end=False,
    )
    assert drained
    assert updated[2, 2] == fdr[2, 2]
    assert updated[2, 3] == fdr[2, 3]
    assert updated[2, 0] != fdr[2, 0]
    assert updated[2, 1] != fdr[2, 1]


def test_final_link_points_its_end_downhill():
    fdr, path, lakes, dem, valid = _valley_grid()
    updated, drained = apply_path_to_fdr(
        fdr, path, lakes, dem, valid, drain_end=True,
    )
    assert drained
    assert updated[2, 2] == 1
    assert updated[2, 2] != fdr[2, 2]
