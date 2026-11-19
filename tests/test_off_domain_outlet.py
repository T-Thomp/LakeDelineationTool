"""Lakes that leave the geofabric keep a boundary outlet only when they die there."""

import geopandas as gpd
from shapely.geometry import LineString, box

from utilities import off_domain_boundary_outlet


def _streams(link, downstream, end):
    return gpd.GeoDataFrame(
        {
            "LINKNO": [link],
            "DSLINKNO": [downstream],
            "DOUTEND": [1.0],
            "geometry": [LineString([(9, 2), end])],
        }
    )


def test_terminal_lake_off_domain_uses_boundary_center():
    domain = box(0, 0, 10, 10)
    lake = box(8, -1, 12, 4)
    point, link, exits = off_domain_boundary_outlet(lake, domain, _streams(1, -1, (9, 0)))
    assert link == 1
    assert exits == {1}
    assert point.distance(domain.boundary) < 1e-6
    assert point.distance(domain) < 1e-6


def test_lake_inside_domain_is_unchanged():
    domain = box(0, 0, 10, 10)
    lake = box(1, 1, 3, 3)
    assert off_domain_boundary_outlet(lake, domain, _streams(1, -1, (2, 1))) is None


def test_stream_that_continues_is_unchanged():
    domain = box(0, 0, 10, 10)
    lake = box(8, -1, 12, 4)
    # End is inside the lake and inside the domain, and the link continues.
    assert off_domain_boundary_outlet(lake, domain, _streams(1, 5, (9, 1))) is None


def test_three_exits_use_the_downstream_boundary():
    domain = box(0, 0, 10, 10)
    lake = box(4, -2, 6, 2).union(box(8, 4, 12, 6)).union(box(4, 8, 6, 12))
    streams = gpd.GeoDataFrame(
        {
            "LINKNO": [1, 2],
            "DSLINKNO": [-1, -1],
            "DOUTEND": [1.0, 50.0],
            "geometry": [
                LineString([(5, 1), (5, 0)]),
                LineString([(9, 5), (10, 5)]),
            ],
        }
    )
    point, link, exits = off_domain_boundary_outlet(lake, domain, streams)
    assert link == 1
    assert exits == {1, 2}
    assert point.distance(domain.boundary) < 1e-6
    assert point.distance(domain) < 1e-6
    assert abs(point.y) < 1e-6
