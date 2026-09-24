"""Topology and assembly tests for HY_Features-enriched geofabric."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import LineString, Point, Polygon

from hy_features.assemble import assemble_full_geofabric
from hy_features.enrich import (
    enrich_catchment_areas,
    enrich_flowpaths,
    enrich_hydrometric_features,
    enrich_waterbodies,
)
from hy_features.network import (
    assign_hydrometric_positions,
    filter_placed_hydrometric,
    link_waterbody_network,
)

from hy_features.schema import (
    CATCHMENT_ID,
    CONTRIBUTING_CATCHMENT_ID,
    DISTANCE_DESCRIPTION,
    DISTANCE_FROM_OUTLET_M,
    DOWNSTREAM_WATERBODY_ID,
    FLOWPATH_ID,
    HOST_FLOWPATH_ID,
    HYF_TYPE,
    HY_CATCHMENT_AREA,
    HY_DENDRITIC_CATCHMENT,
    HY_FLOWPATH,
    HY_HYDRO_NEXUS,
    HY_HYDROMETRIC_FEATURE,
    HY_IMPOUNDMENT,
    HY_LAKE,
    LINEAR_ELEMENT_ID,
    LOWER_CATCHMENT_ID,
    NEXUS_ID,
    OUTFLOW_NEXUS_ID,
    REALIZED_NEXUS_ID,
    REALIZES_CATCHMENT,
    RECEIVING_CATCHMENT_ID,
    REFERENCE_NEXUS_ID,
    NETWORK_ID,
    FEATURE_ID,
    UPSTREAM_WATERBODY_ID,
    WATERBODY_CLASS,
    WATERBODY_ID,
    classify_waterbody,
)


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


def test_classify_waterbody_from_hydrolakes_type():
    assert classify_waterbody(1) == HY_LAKE
    assert classify_waterbody(2) == HY_IMPOUNDMENT
    assert classify_waterbody(3) == HY_IMPOUNDMENT
    assert classify_waterbody(None) == HY_LAKE


def test_lake_catchment_uses_hydrolakes_type_not_reservoir_default():
    basins = gpd.GeoDataFrame(
        {
            "DN": [10],
            "is_lake": [1],
            "lake_id": [42],
            "lake_type": [1],
            "lake_area": [1e6],
            "frac_lake": [0.8],
            "geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        },
        crs="EPSG:3857",
    )
    enriched = enrich_catchment_areas(basins)
    assert enriched[WATERBODY_CLASS].iloc[0] == HY_LAKE

    basins["lake_type"] = [2]
    enriched = enrich_catchment_areas(basins)
    assert enriched[WATERBODY_CLASS].iloc[0] == HY_IMPOUNDMENT


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


def test_full_assembly():
    basins, streams = _minimal_raw_geofabric()
    assembled = assemble_full_geofabric(basins, streams)

    layers = assembled["layers"]
    assert "hydro_nexus" in layers
    assert not layers["hydro_nexus"].empty
    assert OUTFLOW_NEXUS_ID in layers["catchment_area"].columns

    assert NETWORK_ID in layers["catchment_area"].columns
    assert FEATURE_ID in layers["flowpath"].columns
    assert layers["catchment_area"][NETWORK_ID].iloc[0] == "study_hydrographic_network"

    dendritic = assembled["dendritic_catchment"]
    assert (dendritic[HYF_TYPE] == HY_DENDRITIC_CATCHMENT).all()


def test_hydro_nexus_covers_all_flowpaths():
    basins, streams = _minimal_raw_geofabric()
    assembled = assemble_full_geofabric(basins, streams)
    nexus = assembled["layers"]["hydro_nexus"]
    hy_only = nexus[nexus[HYF_TYPE] == HY_HYDRO_NEXUS]
    contributing = set(hy_only[CONTRIBUTING_CATCHMENT_ID].astype(str))
    assert contributing == {"1", "2"}


def test_hydro_nexus_at_topologic_outlet_when_line_is_reversed():
    """TauDEM lines often store the pour point at the first vertex."""
    from hy_features.network import build_hydro_nexus_layer

    streams = gpd.GeoDataFrame(
        {
            "LINKNO": [1, 2],
            "DSLINKNO": [2, -9999],
            "Length": [1000.0, 1000.0],
            "geometry": [
                LineString([(1.0, 0.5), (0.5, 0.5)]),
                LineString([(2.0, 0.5), (1.5, 0.5)]),
            ],
        },
        crs="EPSG:3857",
    )
    nexus = build_hydro_nexus_layer(streams, outlet_sentinel=-9999)
    n1 = nexus[nexus[CONTRIBUTING_CATCHMENT_ID] == "1"].iloc[0]
    n2 = nexus[nexus[CONTRIBUTING_CATCHMENT_ID] == "2"].iloc[0]
    assert n1.geometry.equals_exact(Point(1.0, 0.5), tolerance=1e-6)
    assert n2.geometry.equals_exact(Point(2.0, 0.5), tolerance=1e-6)


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
    assert hm[CATCHMENT_ID].iloc[0] == "1"
    assert hm[HOST_FLOWPATH_ID].iloc[0] == "1"
    assert hm[LINEAR_ELEMENT_ID].iloc[0] == "1"
    assert hm[REFERENCE_NEXUS_ID].iloc[0] == "nx_2"
    assert hm[DISTANCE_DESCRIPTION].iloc[0] == "upstream"
    assert float(hm[DISTANCE_FROM_OUTLET_M].iloc[0]) >= 0


def test_hydrometric_ids_use_containing_catchment_not_nearest_reach():
    """host_flowpath_id follows basin catchment_id, not the geometrically nearest link."""
    basins, streams = _minimal_raw_geofabric()
    basins = enrich_catchment_areas(basins)
    streams = enrich_flowpaths(streams, outlet_sentinel=-9999)
    # Inside basin 2 (DN=2) but much closer to link 1 than link 2.
    gauge_pt = Point(1.01, 0.5)
    gauges = gpd.GeoDataFrame(
        {"STATION_NUMBER": ["05AB002"], "geometry": [gauge_pt]},
        crs=streams.crs,
    )
    placed = assign_hydrometric_positions(
        enrich_hydrometric_features(gauges), streams, basins=basins,
    )
    assert placed[CATCHMENT_ID].iloc[0] == "2"
    assert placed[HOST_FLOWPATH_ID].iloc[0] == "2"
    assert placed[LINEAR_ELEMENT_ID].iloc[0] == "2"


def test_waterbody_layer():
    basins, streams = _minimal_raw_geofabric()
    lakes = gpd.GeoDataFrame(
        {
            "Hylak_id": [100],
            "Lake_type": [1],
            "Lake_name": ["Test Lake"],
            "geometry": [Polygon([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)])],
        },
        crs=streams.crs,
    )
    assembled = assemble_full_geofabric(basins, streams, waterbodies=lakes)
    wb = assembled["layers"]["waterbody"]
    assert wb[HYF_TYPE].iloc[0] == HY_LAKE


def test_waterbody_links_skip_non_lake_catchments():
    basins = gpd.GeoDataFrame(
        {
            "DN": [1, 2, 3],
            "is_lake": [1, 0, 1],
            "lake_id": [100, -1, 300],
            "lake_area": [1e6, 0.0, 2e6],
            "frac_lake": [0.9, 0.0, 0.9],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
                Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
            ],
        },
        crs="EPSG:3857",
    )
    streams = gpd.GeoDataFrame(
        {
            "LINKNO": [1, 2, 3],
            "DSLINKNO": [2, 3, -9999],
            "Length": [1000.0, 1000.0, 1000.0],
            "geometry": [
                LineString([(0.5, 0.5), (1.0, 0.5)]),
                LineString([(1.5, 0.5), (2.0, 0.5)]),
                LineString([(2.5, 0.5), (3.0, 0.5)]),
            ],
        },
        crs="EPSG:3857",
    )
    basins = enrich_catchment_areas(basins)
    streams = enrich_flowpaths(streams, outlet_sentinel=-9999)
    lakes = enrich_waterbodies(
        gpd.GeoDataFrame(
            {
                "Hylak_id": [100, 300],
                "Lake_type": [1, 1],
                "Lake_name": ["Upper", "Lower"],
                "geometry": [
                    Polygon([(0.2, 0.2), (0.8, 0.2), (0.8, 0.8), (0.2, 0.8)]),
                    Polygon([(2.2, 0.2), (2.8, 0.2), (2.8, 0.8), (2.2, 0.8)]),
                ],
            },
            crs=streams.crs,
        )
    )
    linked = link_waterbody_network(lakes, basins, streams, outlet_sentinel=-9999)
    upper = linked[linked[WATERBODY_ID].astype(str) == "100"].iloc[0]
    lower = linked[linked[WATERBODY_ID].astype(str) == "300"].iloc[0]
    assert upper[DOWNSTREAM_WATERBODY_ID] == "300"
    assert lower[UPSTREAM_WATERBODY_ID] == "100"


def test_registry_realizations_and_nexus_associations():
    basins, streams = _minimal_raw_geofabric()
    assembled = assemble_full_geofabric(basins, streams)
    registry = assembled["registry"]

    realization_types = {e.realization_type for e in registry.entries}
    assert realization_types == {HY_CATCHMENT_AREA, HY_FLOWPATH}
    assert set(registry.catchments) == {"1", "2"}

    nexus_links = {
        (a.catchment_id, a.feature_id) for a in registry.associations
        if a.feature_type == HY_HYDRO_NEXUS
    }
    assert nexus_links == {("1", "nx_2"), ("2", "nx_out_2")}
    assert registry.catchments["1"].outflow_nexus_id == "nx_2"
    assert registry.catchments["2"].inflow_nexus_id == "nx_2"

    catchment_area = assembled["layers"]["catchment_area"]
    ca_entry = next(e for e in registry.entries if e.realization_type == HY_CATCHMENT_AREA)
    assert ca_entry.feature_id == catchment_area[FEATURE_ID].iloc[0]


def test_unplaced_gauges_omitted_from_hydrometric_layer():
    basins, streams = _minimal_raw_geofabric()
    gauges = gpd.GeoDataFrame(
        {
            "STATION_NUMBER": ["far-away"],
            "geometry": [Point(50_000.0, 50_000.0)],
        },
        crs=streams.crs,
    )
    enriched = enrich_hydrometric_features(gauges)
    placed = assign_hydrometric_positions(
        enriched, streams, basins=basins, search_radius_m=5000.0,
    )
    filtered, skipped = filter_placed_hydrometric(placed)
    assert skipped == 1
    assert filtered is None

    assembled = assemble_full_geofabric(basins, streams, gauges=gauges)
    assert "hydrometric_feature" not in assembled["layers"]
    assert assembled["hydrometric_skipped"] == 1


def test_strip_point_join_artifacts_drops_dn_and_index_right():
    from hy_features.export import strip_point_join_artifacts

    pts = gpd.GeoDataFrame(
        {
            "STATION_NUMBER": ["05AB001"],
            "DN": [999],
            "index_right": [0],
            "geometry": [Point(0.5, 0.5)],
        },
        crs="EPSG:3857",
    )
    cleaned = strip_point_join_artifacts(pts)
    assert "DN" not in cleaned.columns
    assert "index_right" not in cleaned.columns
    assert cleaned["STATION_NUMBER"].iloc[0] == "05AB001"


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


def _confluence_streams():
    """Links 1 and 2 join and drain into 3 (domain outlet)."""
    return gpd.GeoDataFrame(
        {
            "LINKNO": [1, 2, 3],
            "DSLINKNO": [3, 3, -9999],
            "geometry": [
                LineString([(1.0, 1.0), (0.0, 2.0)]),
                LineString([(1.0, 1.0), (2.0, 2.0)]),
                LineString([(1.0, 0.0), (1.0, 1.0)]),
            ],
        },
        crs="EPSG:3857",
    )


def _confluence_basins():
    return gpd.GeoDataFrame(
        {
            "DN": [1, 2, 3],
            "is_lake": [1, 1, 1],
            "lake_id": [100, 200, 300],
            "lake_area": [1.0, 1.0, 1.0],
            "frac_lake": [0.5, 0.5, 0.5],
            "geometry": [
                Polygon([(0, 1), (1, 1), (1, 3), (0, 3)]),
                Polygon([(1, 1), (2, 1), (2, 3), (1, 3)]),
                Polygon([(0, 0), (2, 0), (2, 1), (0, 1)]),
            ],
        },
        crs="EPSG:3857",
    )


def test_confluence_shares_one_nexus():
    from hy_features.network import build_hydro_nexus_layer

    nexus = build_hydro_nexus_layer(_confluence_streams(), outlet_sentinel=-9999)
    assert set(nexus[NEXUS_ID]) == {"nx_3", "nx_out_3"}
    shared = nexus[nexus[NEXUS_ID] == "nx_3"].iloc[0]
    assert shared[CONTRIBUTING_CATCHMENT_ID] == "1,2"
    assert shared[RECEIVING_CATCHMENT_ID] == "3"
    assert REALIZES_CATCHMENT not in nexus.columns

    assembled = assemble_full_geofabric(_confluence_basins(), _confluence_streams())
    ca = assembled["layers"]["catchment_area"].set_index(CATCHMENT_ID)
    assert ca.loc["1", OUTFLOW_NEXUS_ID] == "nx_3"
    assert ca.loc["2", OUTFLOW_NEXUS_ID] == "nx_3"
    assert ca.loc["3", "inflow_nexus_id"] == "nx_3"
    assert ca.loc["3", "upper_catchment_id"] == "1,2"


def test_upstream_waterbody_lists_every_branch():
    basins = enrich_catchment_areas(_confluence_basins())
    streams = enrich_flowpaths(_confluence_streams(), outlet_sentinel=-9999)
    lakes = enrich_waterbodies(
        gpd.GeoDataFrame(
            {"Hylak_id": [100, 200, 300], "Lake_type": [1, 1, 1],
             "geometry": list(_confluence_basins().geometry)},
            crs="EPSG:3857",
        )
    )
    linked = link_waterbody_network(lakes, basins, streams, outlet_sentinel=-9999)
    lower = linked[linked[WATERBODY_ID] == "300"].iloc[0]
    assert lower[UPSTREAM_WATERBODY_ID] == "100,200"


def test_float_ids_are_normalized():
    basins, streams = _minimal_raw_geofabric()
    basins["DN"] = basins["DN"].astype(float)
    streams["LINKNO"] = streams["LINKNO"].astype(float)
    streams["DSLINKNO"] = streams["DSLINKNO"].astype(float)
    assert enrich_catchment_areas(basins)[CATCHMENT_ID].tolist() == ["1", "2"]
    enriched = enrich_flowpaths(streams, outlet_sentinel=-9999)
    assert enriched[FLOWPATH_ID].tolist() == ["1", "2"]
    assert enriched[LOWER_CATCHMENT_ID].tolist() == ["2", ""]


def test_shapefile_frame_drops_hy_columns_without_collisions():
    from hy_features.export import check_shapefile_columns, prepare_shapefile_frame

    basins, streams = _minimal_raw_geofabric()
    assembled = assemble_full_geofabric(basins, streams)
    for layer in ("catchment_area", "flowpath"):
        frame = prepare_shapefile_frame(assembled["layers"][layer], f"{layer}.shp")
        assert CATCHMENT_ID not in frame.columns
        assert WATERBODY_ID not in frame.columns
        assert all(len(c) <= 10 for c in frame.columns)
    assert "DN" in prepare_shapefile_frame(assembled["layers"]["catchment_area"], "b.shp").columns

    clash = gpd.GeoDataFrame(
        {"distance_a_long": [1], "distance_a_other": [2], "geometry": [Point(0, 0)]},
        crs="EPSG:3857",
    )
    with pytest.raises(ValueError):
        check_shapefile_columns(clash, "clash.shp")


def test_sidecars_serialize_numpy_values():
    import json

    import numpy as np

    from hy_features.json_export import json_default

    basins, streams = _minimal_raw_geofabric()
    assembled = assemble_full_geofabric(basins, streams)
    payload = {
        "registry": assembled["registry"].to_full_payload(),
        "network": assembled["hydrographic_network"],
        "extra": [np.int64(3), np.bool_(True), np.array([1, 2])],
    }
    text = json.dumps(payload, default=json_default)
    assert '"extra": [3, true, [1, 2]]' in text


def test_gauge_station_code_read_from_shapefile_name():
    gauges = gpd.GeoDataFrame(
        {"STATION_NO": ["05AB001"], "STATION_NM": ["Test Creek"], "geometry": [Point(0.5, 0.5)]},
        crs="EPSG:3857",
    )
    enriched = enrich_hydrometric_features(gauges)
    assert enriched["station_code"].iloc[0] == "05AB001"
    assert enriched["feature_name"].iloc[0] == "Test Creek"


def test_multilinestring_flowpath_is_merged():
    from hy_features.network import _as_linestring
    from shapely.geometry import MultiLineString

    merged = _as_linestring(MultiLineString([[(0, 0), (1, 0)], [(1, 0), (2, 0)]]))
    assert merged.geom_type == "LineString"
    assert merged.length == pytest.approx(2.0)

    disjoint = _as_linestring(MultiLineString([[(0, 0), (1, 0)], [(5, 0), (8, 0)]]))
    assert disjoint.length == pytest.approx(3.0)


def test_hydro_locations_realize_network_nexus():
    basins, streams = _minimal_raw_geofabric()
    pour_points = gpd.GeoDataFrame(
        {
            "name": ["Lake_1", "Lake_1"],
            "point_type": ["outflow", "inflow"],
            "geometry": [Point(1.0, 0.5), Point(400.0, 400.0)],
        },
        crs="EPSG:3857",
    )
    assembled = assemble_full_geofabric(basins, streams, hydro_locations=pour_points)
    loc = assembled["layers"]["hydro_location"]
    assert loc["realized_nexus_id"].tolist() == ["nx_2", ""]
    assert loc["hydro_loc_type"].tolist() == ["catchment outlet", "river mouth"]
    assert loc[FEATURE_ID].is_unique
    nexus = assembled["layers"]["hydro_nexus"]
    assert (nexus[HYF_TYPE] == HY_HYDRO_NEXUS).all()
