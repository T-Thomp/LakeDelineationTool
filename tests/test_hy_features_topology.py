"""Topology and conformance validation for HY_Features-enriched geofabric."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Polygon

from hy_features.assemble import assemble_full_geofabric
from hy_features.enrich import enrich_catchment_areas, enrich_flowpaths
from hy_features.schema import (
    CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    DISTANCE_FROM_OUTLET_M,
    FLOWPATH_ID,
    HOST_FLOWPATH_ID,
    HYF_TYPE,
    HY_CATCHMENT_AREA,
    HY_DENDRITIC_CATCHMENT,
    HY_FLOWPATH,
    HY_HYDRO_NEXUS,
    HY_HYDROMETRIC_FEATURE,
    LOWER_CATCHMENT_ID,
    NEXUS_ID,
    OUTFLOW_NEXUS_ID,
    REALIZES_CATCHMENT,
    REFERENCE_NEXUS_ID,
)
from hy_features.validate import validate_geofabric


def _minimal_raw_geofabric():
    basins = gpd.GeoDataFrame(
        {
            "DN": [1, 2],
            "is_lake": [0, 0],
            "lake_id": [-1, -1],
            "lake_area": [0.0, 0.0],
            "frac_lake": [0.0, 0.0],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            ],
        },
        crs="EPSG:3857",
    )
    streams = gpd.GeoDataFrame(
        {
            "LINKNO": [1, 2],
            "DSLINKNO": [2, -9999],
            "Length": [1000.0, 1000.0],
            "geometry": [
                LineString([(0.5, 0.5), (1.0, 0.5)]),
                LineString([(1.5, 0.5), (2.0, 0.5)]),
            ],
        },
        crs="EPSG:3857",
    )
    return basins, streams


def _minimal_geofabric():
    basins, streams = _minimal_raw_geofabric()
    return enrich_catchment_areas(basins), enrich_flowpaths(streams, outlet_sentinel=-9999)


def test_enrich_catchment_areas_columns():
    basins, _ = _minimal_geofabric()
    assert HYF_TYPE in basins.columns
    assert basins[HYF_TYPE].iloc[0] == HY_CATCHMENT_AREA
    assert basins[CATCHMENT_ID].iloc[0] == "1"
    assert basins[REALIZES_CATCHMENT].iloc[0] == "1"


def test_enrich_flowpaths_columns():
    _, streams = _minimal_geofabric()
    assert streams[HYF_TYPE].iloc[0] == HY_FLOWPATH
    assert streams[FLOWPATH_ID].iloc[0] == "1"
    assert streams[LOWER_CATCHMENT_ID].iloc[0] == "2"
    assert streams[LOWER_CATCHMENT_ID].iloc[1] == ""


def test_full_assembly_conformance():
    basins, streams = _minimal_raw_geofabric()
    assembled = assemble_full_geofabric(basins, streams)

    layers = assembled["layers"]
    assert "hydro_nexus" in layers
    assert not layers["hydro_nexus"].empty
    assert OUTFLOW_NEXUS_ID in layers["catchment_area"].columns
    assert assembled["validation"].conformant

    dendritic = assembled["dendritic_catchment"]
    assert (dendritic[HYF_TYPE] == HY_DENDRITIC_CATCHMENT).all()


def test_hydro_nexus_covers_all_flowpaths():
    basins, streams = _minimal_raw_geofabric()
    assembled = assemble_full_geofabric(basins, streams)
    nexus = assembled["layers"]["hydro_nexus"]
    hy_only = nexus[nexus[HYF_TYPE] == HY_HYDRO_NEXUS]
    contributing = set(hy_only[CONTRIBUTING_CATCHMENT_ID].astype(str))
    assert contributing == {"1", "2"}


def test_hydrometric_river_referencing():
    basins, streams = _minimal_raw_geofabric()
    gauges = gpd.GeoDataFrame(
        {
            "STATION_NUMBER": ["05AB001"],
            "geometry": [streams.geometry.iloc[0].interpolate(0.5, normalized=True)],
        },
        crs=streams.crs,
    )
    assembled = assemble_full_geofabric(basins, streams, gauges=gauges)
    hm = assembled["layers"]["hydrometric_feature"]
    assert hm[HYF_TYPE].iloc[0] == HY_HYDROMETRIC_FEATURE
    assert hm[HOST_FLOWPATH_ID].iloc[0] == "1"
    assert hm[REFERENCE_NEXUS_ID].iloc[0] == "nx_out_1"
    assert float(hm[DISTANCE_FROM_OUTLET_M].iloc[0]) >= 0


def test_validation_report_passes():
    basins, streams = _minimal_raw_geofabric()
    assembled = assemble_full_geofabric(basins, streams)
    report = validate_geofabric(
        assembled["layers"],
        assembled["dendritic_catchment"],
        assembled["registry"],
    )
    assert report.conformant
    assert not report.errors


def test_field_remap_catchment_area():
    from hy_features.field_remap import apply_field_remap

    basins, _ = _minimal_geofabric()
    remapped = apply_field_remap(basins, "catchment_area", preset="mesh")
    assert "DN" in remapped.columns
    assert "catchment_id" not in remapped.columns
    assert remapped["DN"].iloc[0] == 1


def test_field_remap_flowpath():
    from hy_features.field_remap import apply_field_remap

    _, streams = _minimal_geofabric()
    remapped = apply_field_remap(streams, "flowpath", preset="mesh", drop_metadata=True)
    assert "LINKNO" in remapped.columns
    assert "DSLINKNO" in remapped.columns
    assert remapped["LINKNO"].iloc[0] == 1
    assert remapped["DSLINKNO"].iloc[0] == 2
    assert remapped["DSLINKNO"].iloc[1] == -9999


def test_outlet_sentinel_preserved_from_legacy_downstream_id():
    from hy_features.field_remap import apply_field_remap

    _, streams = _minimal_geofabric()
    streams["DSLINKNO"] = [2, -9999]
    remapped = apply_field_remap(streams, "flowpath", preset="mesh", drop_metadata=True)
    assert remapped["DSLINKNO"].iloc[1] == -9999


def test_frac_lake_passes_through_on_basins():
    from hy_features.field_remap import apply_field_remap

    basins, _ = _minimal_geofabric()
    basins.loc[basins.index[0], "frac_lake"] = 0.42
    remapped = apply_field_remap(basins, "catchment_area", preset="mesh", drop_metadata=True)
    assert "frac_lake" in remapped.columns
    assert remapped["frac_lake"].iloc[0] == pytest.approx(0.42)


def test_custom_preset_override():
    from hy_features.field_remap import apply_field_remap, build_custom_mapping

    _, streams = _minimal_geofabric()
    mapping = build_custom_mapping("flowpath", {"catchment_id": "WSNO"}, preset="mesh")
    remapped = apply_field_remap(streams, "flowpath", mapping=mapping)
    assert "WSNO" in remapped.columns
    assert remapped["WSNO"].iloc[0] == 1
