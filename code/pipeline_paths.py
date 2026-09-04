"""
Central output layout for Lake Delineation Tool.

All pipeline products live under ``outputs/``. Interim TauDEM rasters and
working geofabric steps are kept separate; ``outputs/final/`` holds the
deliverables only (no duplicate copies elsewhere).

Layout
------
outputs/
  interim/
    taudem_d8/          Pass 1–2 TauDEM rasters and vectors
    taudem_pass3/       Pass 3 TauDEM rasters and vectors
  prep/                 Lake/gauge prep and pour-point intermediates
  working/              Merged geofabric before final clean (+ HY sidecars)
  final/                Deliverables: basins, basins_aggregated, pour_points
                          (+ paired stream shapefiles)

Edit study_settings.py at your study root when changing study area:
  INPUT_DEM, INPUT_HYDAT_DB, INPUT_HYDROLAKES
  (copy from study_settings.example.py)
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path

# Study root: folder where you run sbatch (set by Delineation-Workflow.slurm).
PROJECT_ROOT = Path(
    os.environ.get("LAKE_DELINEATION_ROOT", ".")
).expanduser().resolve()


def _study_path(relative_or_absolute: str | Path) -> Path:
    """Resolve user inputs to absolute paths (follows symlinks)."""
    path = Path(relative_or_absolute).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _load_study_settings():
    settings_path = PROJECT_ROOT / "study_settings.py"
    if not settings_path.is_file():
        example = PROJECT_ROOT / "study_settings.example.py"
        raise FileNotFoundError(
            f"Missing {settings_path}. "
            f"Copy {example} to study_settings.py and set INPUT_DEM, "
            "INPUT_HYDAT_DB, and INPUT_HYDROLAKES."
        )

    spec = importlib.util.spec_from_file_location("study_settings", settings_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {settings_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    missing = [name for name in ("INPUT_DEM", "INPUT_HYDAT_DB", "INPUT_HYDROLAKES") if not hasattr(module, name)]
    if missing:
        raise AttributeError(
            f"{settings_path} must define: {', '.join(missing)}"
        )
    return module


_settings = _load_study_settings()

INPUT_DEM = _study_path(_settings.INPUT_DEM)
INPUT_HYDAT_DB = _study_path(_settings.INPUT_HYDAT_DB)
INPUT_HYDROLAKES = _study_path(_settings.INPUT_HYDROLAKES)

OUTPUT_ROOT = PROJECT_ROOT / "outputs"
INTERIM = OUTPUT_ROOT / "interim"
PREP = OUTPUT_ROOT / "prep"
WORKING = OUTPUT_ROOT / "working"
FINAL = OUTPUT_ROOT / "final"

TAUDEM_D8 = INTERIM / "taudem_d8"
TAUDEM_PASS3 = INTERIM / "taudem_pass3"

# Pass 1 / 2 TauDEM (single canonical copy — no delineation-product duplicates)
PASS1_BASINS = TAUDEM_D8 / "original-delineated-watersheds.shp"
PASS1_STREAMS = TAUDEM_D8 / "original-delineated-streams.shp"
PASS1_WATERSHEDS_TIF = TAUDEM_D8 / "original-delineated-watersheds.tif"

PASS2_BASINS = TAUDEM_D8 / "intermediate-delineated-watersheds.shp"
PASS2_STREAMS = TAUDEM_D8 / "intermediate-delineated-streams.shp"
PASS2_WATERSHEDS_TIF = TAUDEM_D8 / "intermediate-delineated-watersheds.tif"

FDR_CENTERLINE = TAUDEM_D8 / "fdr_lakes.tif"

# Pass 3 TauDEM
PASS3_BASINS = TAUDEM_PASS3 / "final-delineated-watersheds.shp"
PASS3_STREAMS = TAUDEM_PASS3 / "final-delineated-streams.shp"
PASS3_WATERSHEDS_TIF = TAUDEM_PASS3 / "final-delineated-watersheds.tif"
SNAPPED_OUTLETS = TAUDEM_PASS3 / "snapped-outlets.shp"

# Python preprocessing
PREP_LAKES = PREP / "lakes.shp"
PREP_LAKES_GPKG = PREP / "lakes.gpkg"
PREP_GAUGES = PREP / "gauges.shp"
PREP_GAUGES_GPKG = PREP / "gauges.gpkg"
PREP_SELECTED_OUTLETS = PREP / "selected_outlets.shp"
PREP_RESERVOIR_IO_NODES = PREP / "reservoir_io_nodes.shp"

# Post-processing working copies
WORKING_BASINS_MERGED = WORKING / "basins_merged.shp"
WORKING_STREAMS_MERGED = WORKING / "streams_merged.shp"
WORKING_GEOFABRIC_GPKG = WORKING / "geofabric.gpkg"
WORKING_CATCHMENT_REGISTRY = WORKING / "catchment_registry.json"
WORKING_HYDRO_NETWORK_JSON = WORKING / "hydrographic_network.json"

# Final deliverables
FINAL_BASINS = FINAL / "basins.shp"
FINAL_STREAMS = FINAL / "streams.shp"
FINAL_BASINS_AGG = FINAL / "basins_aggregated.shp"
FINAL_STREAMS_AGG = FINAL / "streams_aggregated.shp"
FINAL_POUR_POINTS = FINAL / "pour_points.shp"

PATHS: dict[str, str] = {
    "dem": str(INPUT_DEM),
    "hydat_db": str(INPUT_HYDAT_DB),
    "hydrolakes": str(INPUT_HYDROLAKES),
    "pass1_basins": str(PASS1_BASINS),
    "pass1_streams": str(PASS1_STREAMS),
    "pass1_watersheds_tif": str(PASS1_WATERSHEDS_TIF),
    "pass2_basins": str(PASS2_BASINS),
    "pass2_streams": str(PASS2_STREAMS),
    "pass2_watersheds_tif": str(PASS2_WATERSHEDS_TIF),
    "fdr_lakes": str(FDR_CENTERLINE),
    "pass3_basins": str(PASS3_BASINS),
    "pass3_streams": str(PASS3_STREAMS),
    "pass3_watersheds_tif": str(PASS3_WATERSHEDS_TIF),
    "snapped_outlets": str(SNAPPED_OUTLETS),
    "lakes": str(PREP_LAKES),
    "lakes_gpkg": str(PREP_LAKES_GPKG),
    "gauges": str(PREP_GAUGES),
    "gauges_gpkg": str(PREP_GAUGES_GPKG),
    "selected_outlets": str(PREP_SELECTED_OUTLETS),
    "reservoir_io_nodes": str(PREP_RESERVOIR_IO_NODES),
    "working_basins_merged": str(WORKING_BASINS_MERGED),
    "working_streams_merged": str(WORKING_STREAMS_MERGED),
    "geofabric_gpkg": str(WORKING_GEOFABRIC_GPKG),
    "catchment_registry": str(WORKING_CATCHMENT_REGISTRY),
    "hydro_network_json": str(WORKING_HYDRO_NETWORK_JSON),
    "final_basins": str(FINAL_BASINS),
    "final_streams": str(FINAL_STREAMS),
    "final_basins_aggregated": str(FINAL_BASINS_AGG),
    "final_streams_aggregated": str(FINAL_STREAMS_AGG),
    "final_pour_points": str(FINAL_POUR_POINTS),
}

DELETE_INTERIM_ENV = "DELETE_INTERIM_FILES"
_INTERIM_DELETE_DIRS = (TAUDEM_D8, TAUDEM_PASS3)

_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disabled"})


def delete_interim_enabled(*, default: bool = False) -> bool:
    """
    Return whether TauDEM interim products under ``outputs/interim/`` should be removed.

    Priority:
      1. Explicit ``enabled`` argument when calling ``delete_interim_outputs()``
      2. Environment variable ``DELETE_INTERIM_FILES`` (if set)
      3. ``default`` (per-script constant when env is unset)

    Examples::

        export DELETE_INTERIM_FILES=1   # enable for whole SLURM job
        export DELETE_INTERIM_FILES=0   # disable (default when env unset)
    """
    raw = os.environ.get(DELETE_INTERIM_ENV)
    if raw is not None:
        value = raw.strip().lower()
        if value in _TRUTHY:
            return True
        if value in _FALSY:
            return False
        raise ValueError(
            f"{DELETE_INTERIM_ENV}={raw!r} is invalid; use 1/0, true/false, on/off, yes/no"
        )
    return default


def delete_interim_outputs(*, enabled: bool | None = None, default: bool = False) -> list[Path]:
    """
    Remove TauDEM Pass 1–3 rasters and vectors under ``outputs/interim/``.

    Safe to call after ``cleanGeofabric.py`` — final and working products are kept.
    Returns the list of removed directory paths (empty when disabled or already absent).
    """
    if enabled is None:
        enabled = delete_interim_enabled(default=default)
    if not enabled:
        return []

    removed: list[Path] = []
    for path in _INTERIM_DELETE_DIRS:
        if not path.exists():
            continue
        shutil.rmtree(path)
        removed.append(path)
        print(f"Removed interim directory: {path}")

    if removed:
        print(f"Interim cleanup complete ({len(removed)} director{'y' if len(removed) == 1 else 'ies'} removed).")
    else:
        print("Interim cleanup enabled but no interim directories were present.")
    return removed


def ensure_output_dirs() -> None:
    """Create all output directories (idempotent)."""
    for path in (
        TAUDEM_D8,
        TAUDEM_PASS3,
        PREP,
        WORKING,
        FINAL,
    ):
        path.mkdir(parents=True, exist_ok=True)
