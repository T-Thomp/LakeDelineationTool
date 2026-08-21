"""
Validate HY_Features implementation schema conformance (Annex A.2 inspection).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import geopandas as gpd
import pandas as pd

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
    INFLOW_NEXUS_ID,
    LOWER_CATCHMENT_ID,
    NEXUS_ID,
    OUTFLOW_NEXUS_ID,
    REALIZES_CATCHMENT,
    RECEIVING_CATCHMENT_ID,
    REFERENCE_NEXUS_ID,
    LINEAR_ELEMENT_ID,
)


@dataclass
class ValidationReport:
    conformant: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checks_passed: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)
        self.conformant = False

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def ok(self, msg: str) -> None:
        self.checks_passed.append(msg)


def validation_report_to_dict(report: ValidationReport) -> dict:
    return {
        "conformant": report.conformant,
        "errors": report.errors,
        "warnings": report.warnings,
        "checks_passed": report.checks_passed,
    }


def validate_geofabric(
    layers: dict[str, gpd.GeoDataFrame],
    dendritic: pd.DataFrame,
    registry,
    outlet_sentinel: int = -9999,
) -> ValidationReport:
    """Run all HY_Features conformance checks on assembled outputs."""
    report = ValidationReport()

    _check_required_layers(layers, report)
    _check_catchment_area(layers.get("catchment_area"), report)
    _check_flowpath(layers.get("flowpath"), layers.get("catchment_area"), report, outlet_sentinel)
    _check_hydro_nexus(layers.get("hydro_nexus"), layers.get("flowpath"), report, outlet_sentinel)
    _check_dendritic_table(dendritic, report, outlet_sentinel)
    _check_hydrometric(layers.get("hydrometric_feature"), layers.get("flowpath"), report)
    _check_registry(registry, report)

    if report.conformant:
        report.ok("All mandatory HY_Features implementation schema checks passed")

    return report


def _check_required_layers(layers: dict, report: ValidationReport) -> None:
    required = ["catchment_area", "flowpath", "hydro_nexus"]
    for name in required:
        if name not in layers or layers[name] is None or layers[name].empty:
            report.error(f"Missing required layer: {name}")
        else:
            report.ok(f"Layer present: {name}")


def _check_catchment_area(gdf: gpd.GeoDataFrame | None, report: ValidationReport) -> None:
    if gdf is None or gdf.empty:
        return
    for col in (CATCHMENT_ID, HYF_TYPE, REALIZES_CATCHMENT, OUTFLOW_NEXUS_ID):
        if col not in gdf.columns:
            report.error(f"catchment_area missing mandatory column: {col}")
    if HYF_TYPE in gdf.columns and not (gdf[HYF_TYPE] == HY_CATCHMENT_AREA).all():
        report.error("catchment_area hyf_type must be HY_CatchmentArea")
    if CATCHMENT_ID in gdf.columns and REALIZES_CATCHMENT in gdf.columns:
        if not (gdf[CATCHMENT_ID] == gdf[REALIZES_CATCHMENT]).all():
            report.error("catchment_id must equal realizes_catchment on catchment_area")
        else:
            report.ok("catchment_area realizedCatchment association valid")


def _check_flowpath(
    streams: gpd.GeoDataFrame | None,
    basins: gpd.GeoDataFrame | None,
    report: ValidationReport,
    outlet_sentinel: int,
) -> None:
    if streams is None or streams.empty:
        return
    for col in (FLOWPATH_ID, CATCHMENT_ID, HYF_TYPE, REALIZES_CATCHMENT, OUTFLOW_NEXUS_ID):
        if col not in streams.columns:
            report.error(f"flowpath missing mandatory column: {col}")
    if HYF_TYPE in streams.columns and not (streams[HYF_TYPE] == HY_FLOWPATH).all():
        report.error("flowpath hyf_type must be HY_FlowPath")

    if FLOWPATH_ID not in streams.columns or LOWER_CATCHMENT_ID not in streams.columns:
        return

    ids = set(streams[FLOWPATH_ID].astype(str))
    for _, row in streams.iterrows():
        lower = str(row.get(LOWER_CATCHMENT_ID, ""))
        if lower and lower not in ids:
            report.warn(f"flowpath {row[FLOWPATH_ID]} lower_catchment_id {lower} not in network")

    if basins is not None and CATCHMENT_ID in basins.columns:
        basin_ids = set(basins[CATCHMENT_ID].astype(str))
        orphan = ids - basin_ids
        if orphan:
            report.warn(f"flowpath ids without catchment_area: {len(orphan)}")


def _check_hydro_nexus(
    nexus: gpd.GeoDataFrame | None,
    streams: gpd.GeoDataFrame | None,
    report: ValidationReport,
    outlet_sentinel: int,
) -> None:
    if nexus is None or nexus.empty:
        report.error("hydro_nexus layer is empty")
        return

    for col in (NEXUS_ID, HYF_TYPE, CONTRIBUTING_CATCHMENT_ID, RECEIVING_CATCHMENT_ID):
        if col not in nexus.columns:
            report.error(f"hydro_nexus missing mandatory column: {col}")

    hy_nexus = nexus[nexus[HYF_TYPE] == HY_HYDRO_NEXUS] if HYF_TYPE in nexus.columns else nexus
    if streams is not None and FLOWPATH_ID in streams.columns:
        expected = set(streams[FLOWPATH_ID].astype(str))
        contributing = set(hy_nexus[CONTRIBUTING_CATCHMENT_ID].astype(str))
        missing = expected - contributing
        if missing:
            report.error(f"hydro_nexus missing outflow nexuses for catchments: {missing}")
        else:
            report.ok("Every flowpath has a contributing hydro nexus")

    report.ok(f"hydro_nexus features: {len(nexus)}")


def _check_dendritic_table(dendritic: pd.DataFrame, report: ValidationReport) -> None:
    if dendritic.empty:
        report.error("dendritic_catchment table is empty")
        return
    for col in (CATCHMENT_ID, HYF_TYPE, OUTFLOW_NEXUS_ID):
        if col not in dendritic.columns:
            report.error(f"dendritic_catchment missing column: {col}")
    if HYF_TYPE in dendritic.columns and not (dendritic[HYF_TYPE] == HY_DENDRITIC_CATCHMENT).all():
        report.error("dendritic_catchment hyf_type must be HY_DendriticCatchment")
    report.ok(f"dendritic_catchment records: {len(dendritic)}")


def _check_hydrometric(
    gauges: gpd.GeoDataFrame | None,
    streams: gpd.GeoDataFrame | None,
    report: ValidationReport,
) -> None:
    if gauges is None or gauges.empty:
        report.warn("No hydrometric_feature layer (optional if no gauges in basin)")
        return

    for col in (HYF_TYPE, HOST_FLOWPATH_ID, REFERENCE_NEXUS_ID, LINEAR_ELEMENT_ID,
                DISTANCE_FROM_OUTLET_M):
        if col not in gauges.columns:
            report.error(f"hydrometric_feature missing river referencing column: {col}")

    if HYF_TYPE in gauges.columns and not (gauges[HYF_TYPE] == HY_HYDROMETRIC_FEATURE).all():
        report.error("hydrometric_feature hyf_type must be HY_HydrometricFeature")

    placed = gauges[HOST_FLOWPATH_ID].astype(str).str.len() > 0 if HOST_FLOWPATH_ID in gauges.columns else []
    if HOST_FLOWPATH_ID in gauges.columns:
        n_placed = int((gauges[HOST_FLOWPATH_ID].astype(str).str.len() > 0).sum())
        if n_placed == 0:
            report.warn("No gauges snapped to flowpaths for positionOnRiver")
        else:
            report.ok(f"hydrometric positionOnRiver assigned: {n_placed}/{len(gauges)}")


def _check_registry(registry, report: ValidationReport) -> None:
    if not registry.catchments:
        report.error("Catchment registry has no catchment entries")
        return
    if not registry.entries:
        report.error("Catchment registry has no realization entries")
        return
    report.ok(f"Registry: {len(registry.catchments)} catchments, {len(registry.entries)} realizations")
