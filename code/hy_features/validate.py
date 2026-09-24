"""
Automated inspection of a HY_Features geofabric against implementation_schema.json.

Annex A of OGC 14-111r6 defines conformance by inspection; this module makes that
inspection repeatable. It checks required layers / columns, allowed values,
geometry types, GF_Feature identity (unique ``feature_id``, ``hyf_type_uri``), and
referential integrity of every catchment / nexus / waterbody association.

Usage::

    python -m hy_features.validate outputs/working/geofabric.gpkg
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import geopandas as gpd
import pandas as pd

from hy_features.schema import hyf_type_uri

SCHEMA_PATH = Path(__file__).resolve().parent / "implementation_schema.json"

GEOMETRY_FAMILIES = {
    "Point": {"Point"},
    "LineString": {"LineString", "MultiLineString"},
    "MultiLineString": {"LineString", "MultiLineString"},
    "Polygon": {"Polygon", "MultiPolygon"},
}


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_schema(path: Path | str | None = None) -> dict[str, Any]:
    return json.loads(Path(path or SCHEMA_PATH).read_text(encoding="utf-8"))


def _blank(series: pd.Series) -> pd.Series:
    return series.isna() | series.astype(str).str.strip().isin(("", "nan", "None"))


def _ids(frame: pd.DataFrame | None, col: str) -> set[str]:
    if frame is None or col not in frame.columns:
        return set()
    values = frame.loc[~_blank(frame[col]), col].astype(str)
    return {part for value in values for part in value.split(",") if part}


def _missing(values: Iterable[str], known: set[str]) -> list[str]:
    return sorted(set(values) - known)


def _sample(values: list[str], n: int = 5) -> str:
    return ", ".join(values[:n]) + (" …" if len(values) > n else "")


def _check_columns(name: str, frame: pd.DataFrame, spec: dict, report: ValidationReport) -> None:
    for col in spec.get("columns", []):
        col_name = col["name"]
        if col_name == "geometry":
            continue
        if col_name not in frame.columns:
            if col.get("required"):
                report.errors.append(f"{name}: missing required column '{col_name}'")
            continue
        blank = _blank(frame[col_name])
        if col.get("required") and not col.get("nillable") and col.get("type") == "string" and blank.any():
            report.errors.append(f"{name}.{col_name}: {int(blank.sum())} empty value(s) in a required field")
        allowed = col.get("allowed_values")
        if allowed:
            present = set(frame.loc[~blank, col_name].astype(str))
            bad = sorted(present - set(allowed))
            if bad:
                report.errors.append(f"{name}.{col_name}: values not in {allowed}: {_sample(bad)}")


def _check_geometry(name: str, frame: pd.DataFrame, spec: dict, report: ValidationReport) -> None:
    expected = spec.get("geometry_type")
    if not expected:
        return
    if not isinstance(frame, gpd.GeoDataFrame) or frame.geometry.isna().all():
        report.errors.append(f"{name}: expected {expected} geometry, found none")
        return
    allowed = GEOMETRY_FAMILIES.get(expected, {expected})
    types = set(frame.geometry.dropna().geom_type)
    bad = sorted(types - allowed)
    if bad:
        report.errors.append(f"{name}: geometry types {bad} not allowed for {expected}")
    if frame.geometry.isna().any():
        report.errors.append(f"{name}: {int(frame.geometry.isna().sum())} feature(s) without geometry")


def _check_identity(name: str, frame: pd.DataFrame, report: ValidationReport) -> None:
    if "feature_id" in frame.columns:
        dup = frame["feature_id"][frame["feature_id"].duplicated()].astype(str).tolist()
        if dup:
            report.errors.append(f"{name}.feature_id: duplicate ids {_sample(dup)}")
    if "hyf_type" in frame.columns and "hyf_type_uri" in frame.columns:
        expected = frame["hyf_type"].astype(str).map(hyf_type_uri)
        wrong = int((frame["hyf_type_uri"].astype(str) != expected).sum())
        if wrong:
            report.errors.append(f"{name}.hyf_type_uri: {wrong} value(s) do not match hyf_type")


def _check_references(
    frames: dict[str, pd.DataFrame],
    report: ValidationReport,
    external_catchment_ids: set[str],
) -> None:
    catchment = frames.get("catchment")
    area = frames.get("catchment_area")
    flowpath = frames.get("flowpath")
    nexus = frames.get("hydro_nexus")
    location = frames.get("hydro_location")
    hydrometric = frames.get("hydrometric_feature")
    waterbody = frames.get("waterbody")

    catchments = _ids(catchment, "catchment_id") or _ids(area, "catchment_id")
    nexuses = _ids(nexus, "nexus_id")
    flowpaths = _ids(flowpath, "flowpath_id")
    feature_layers = ("catchment", "catchment_area", "catchment_divide", "flowpath", "hydro_nexus",
                      "hydro_location", "channel_network", "waterbody", "hydrometric_feature",
                      "gauge_catchment", "hydrometric_network")
    feature_ids = set().union(*(_ids(frames.get(name), "feature_id") for name in feature_layers))
    feature_ids |= _ids(catchment, "network_id")

    def expect(label: str, values: set[str], known: set[str], *, warn: bool = False) -> None:
        missing = _missing(values, known)
        if missing:
            msg = f"{label}: {len(missing)} unknown id(s): {_sample(missing)}"
            (report.warnings if warn else report.errors).append(msg)

    for name, frame in (("catchment_area", area), ("flowpath", flowpath)):
        if frame is None:
            continue
        expect(f"{name}.catchment_id", _ids(frame, "catchment_id"), catchments)
        expect(f"{name}.outflow_nexus_id", _ids(frame, "outflow_nexus_id"), nexuses)
        expect(f"{name}.inflow_nexus_id", _ids(frame, "inflow_nexus_id"), nexuses)
        expect(f"{name}.upper_catchment_id", _ids(frame, "upper_catchment_id"), catchments)

    if flowpath is not None:
        expect("flowpath.lower_catchment_id", _ids(flowpath, "lower_catchment_id"), catchments)

    if nexus is not None:
        expect("hydro_nexus.contributing_catchment_id", _ids(nexus, "contributing_catchment_id"), catchments)
        expect("hydro_nexus.receiving_catchment_id", _ids(nexus, "receiving_catchment_id"), catchments)
        realized = _ids(location, "realized_nexus_id")
        expect("hydro_location.realized_nexus_id", realized, nexuses)
        station_realized = _ids(hydrometric, "realized_nexus_id")
        expect("hydrometric_feature.realized_nexus_id", station_realized, nexuses)
        unrealized = _missing(nexuses, realized | station_realized)
        if unrealized:
            report.warnings.append(
                f"hydro_nexus: {len(unrealized)} nexus(es) without a HY_HydroLocation realization: "
                f"{_sample(unrealized)}"
            )

    if catchment is not None and "hyf_type" in catchment.columns:
        dendritic = catchment[catchment["hyf_type"] == "HY_DendriticCatchment"]
        no_outflow = dendritic.loc[_blank(dendritic["outflow_nexus_id"]), "catchment_id"].astype(str).tolist()
        if no_outflow:
            report.errors.append(f"catchment: {len(no_outflow)} dendritic catchment(s) without outflow: {_sample(no_outflow)}")
        multi = dendritic.loc[dendritic["outflow_nexus_id"].astype(str).str.contains(","), "catchment_id"]
        if not multi.empty:
            report.errors.append(f"catchment: dendritic catchment(s) with more than one outflow: {_sample(multi.astype(str).tolist())}")
        expect("catchment.outflow_nexus_id", _ids(catchment, "outflow_nexus_id"), nexuses)
        realized_ca = _ids(frames.get("catchment_realization"), "catchment_id")
        expect("catchment (without any catchmentRealization)", _ids(dendritic, "catchment_id"), realized_ca)

    if hydrometric is not None:
        # catchment_id is the host dendritic catchment (positionOnRiver), not the gauge catchment
        expect("hydrometric_feature.catchment_id", _ids(hydrometric, "catchment_id"), catchments)
        expect("hydrometric_feature.linear_element_id", _ids(hydrometric, "linear_element_id"), flowpaths)
        expect("hydrometric_feature.reference_nexus_id", _ids(hydrometric, "reference_nexus_id"), nexuses)

    divide = frames.get("catchment_divide")
    expect("catchment_divide.catchment_id", _ids(divide, "catchment_id"), catchments)
    adjacency = frames.get("catchment_divide_adjacency")
    for col in ("catchment_id", "adjacent_catchment_id"):
        expect(f"catchment_divide_adjacency.{col}", _ids(adjacency, col), catchments)

    gauge_area = frames.get("gauge_catchment")
    expect("gauge_catchment.catchment_id", _ids(gauge_area, "catchment_id"), catchments)
    expect("gauge_catchment.outflow_nexus_id", _ids(gauge_area, "outflow_nexus_id"), nexuses)
    hm_network = frames.get("hydrometric_network")
    expect("hydrometric_network.realizes_catchment", _ids(hm_network, "realizes_catchment"), catchments)
    stations = frames.get("hydrometric_network_station")
    expect("hydrometric_network_station.hydrometric_network_id",
           _ids(stations, "hydrometric_network_id"), _ids(hm_network, "feature_id"))
    expect("hydrometric_network_station.hydrometric_feature_id",
           _ids(stations, "hydrometric_feature_id"), _ids(hydrometric, "feature_id"))

    expect("feature_name.named_feature_id", _ids(frames.get("feature_name"), "named_feature_id"), feature_ids)

    if waterbody is not None:
        wbs = _ids(waterbody, "waterbody_id")
        expect("waterbody.upstream_waterbody_id", _ids(waterbody, "upstream_waterbody_id"), wbs, warn=True)
        expect("waterbody.downstream_waterbody_id", _ids(waterbody, "downstream_waterbody_id"), wbs, warn=True)

    for table, id_cols in (
        ("catchment_realization", ("catchment_id",)),
        ("catchment_association", ("catchment_id",)),
        ("catchment_upper_catchment", ("catchment_id", "upper_catchment_id")),
        ("nexus_contributing_catchment", ("contributing_catchment_id",)),
    ):
        frame = frames.get(table)
        for col in id_cols:
            expect(f"{table}.{col}", _ids(frame, col), catchments)
        if frame is not None and "feature_id" in frame.columns:
            expect(f"{table}.feature_id", _ids(frame, "feature_id"), feature_ids)

    containment = frames.get("catchment_containment")
    expect("catchment_containment.containing_catchment_id", _ids(containment, "containing_catchment_id"), catchments)
    expect(
        "catchment_containment.contained_catchment_id",
        _ids(containment, "contained_catchment_id"), catchments | external_catchment_ids,
    )

    link = frames.get("nexus_contributing_catchment")
    if link is not None:
        expect("nexus_contributing_catchment.nexus_id", _ids(link, "nexus_id"), nexuses)


def validate_frames(
    frames: dict[str, pd.DataFrame],
    schema: dict[str, Any] | None = None,
    *,
    external_catchment_ids: set[str] | None = None,
) -> ValidationReport:
    """
    Validate in-memory layers / tables keyed by GeoPackage layer name.

    ``external_catchment_ids`` are catchments defined in another product (the
    fine-scale geofabric.gpkg for the aggregated fabric's containment links).
    """
    schema = schema or load_schema()
    report = ValidationReport()
    frames = {k: v for k, v in frames.items() if v is not None and not v.empty}

    product = schema["products"]["geofabric.gpkg"]
    for name in product.get("required_layers", []) + product.get("required_tables", []):
        if name not in frames:
            report.errors.append(f"missing required layer/table '{name}'")

    specs = {**schema.get("geopackage_layers", {}), **schema.get("geopackage_tables", {})}
    for name, frame in frames.items():
        spec = specs.get(name)
        if spec is None:
            report.warnings.append(f"{name}: not described in implementation_schema.json")
            continue
        _check_columns(name, frame, spec, report)
        _check_geometry(name, frame, spec, report)
        if spec.get("gf_feature"):
            _check_identity(name, frame, report)

    _check_references(frames, report, external_catchment_ids or set())
    return report


def validate_assembled(assembled: dict[str, Any], schema: dict[str, Any] | None = None) -> ValidationReport:
    return validate_frames(
        {**assembled["layers"], **assembled.get("tables", {})},
        schema,
        external_catchment_ids=assembled.get("external_catchment_ids"),
    )


def _read_geopackage(path: Path | str) -> dict[str, pd.DataFrame]:
    import pyogrio

    return {
        layer_name: gpd.read_file(str(path), layer=layer_name)
        for layer_name, _geom_type in pyogrio.list_layers(str(path))
    }


def validate_geopackage(
    path: Path | str,
    schema: dict[str, Any] | None = None,
    *,
    external: Path | str | None = None,
) -> ValidationReport:
    """Validate a GeoPackage; ``external`` is the fine-scale product for containment links."""
    external_ids: set[str] = set()
    if external is not None:
        catchment = _read_geopackage(external).get("catchment")
        external_ids = _ids(catchment, "catchment_id")
    return validate_frames(_read_geopackage(path), schema, external_catchment_ids=external_ids)


def print_report(report: ValidationReport, label: str = "geofabric") -> None:
    status = "PASS" if report.ok else "FAIL"
    print(
        f"HY_Features conformance check ({label}): {status} — "
        f"{len(report.errors)} error(s), {len(report.warnings)} warning(s)"
    )
    for msg in report.errors:
        print(f"  ERROR   {msg}")
    for msg in report.warnings:
        print(f"  WARNING {msg}")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check a geofabric GeoPackage against the HY_Features profile.")
    parser.add_argument("gpkg", nargs="+", help="GeoPackage(s) to validate")
    parser.add_argument(
        "--external",
        help="Fine-scale geofabric.gpkg whose catchments the aggregated product's containment links reference",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    failed = False
    for path in args.gpkg:
        report = validate_geopackage(path, external=args.external)
        print_report(report, label=path)
        failed |= not report.ok
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
