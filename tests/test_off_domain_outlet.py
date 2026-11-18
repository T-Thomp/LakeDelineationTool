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
    point, link = off_domain_boundary_outlet(lake, domain, _streams(1, -1, (9, 0)))
    assert link == 1
    assert point.distance(domain.boundary) < 1e-6


def test_lake_inside_domain_is_unchanged():
    domain = box(0, 0, 10, 10)
    lake = box(1, 1, 3, 3)
    assert off_domain_boundary_outlet(lake, domain, _streams(1, -1, (2, 1))) is None


def test_stream_that_continues_is_unchanged():
    domain = box(0, 0, 10, 10)
    lake = box(8, -1, 12, 4)
    assert off_domain_boundary_outlet(lake, domain, _streams(1, 5, (9, 0))) is None
