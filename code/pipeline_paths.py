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
  working/              Merged geofabric before final clean
  final/                Deliverables: basins, basins_aggregated, pour_points
                          (+ paired stream shapefiles)

The three study inputs live in study_settings.py: INPUT_DEM, INPUT_LAKES,
and the gauges listed in INPUT_GAUGES. This module
turns those into absolute paths and defines the default product paths under
the study root. A script flag such as --fdr or --lakes replaces one of those
defaults for that run.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from pathlib import Path


def _study_path(relative_or_absolute: str | Path) -> Path:
    """Resolve user inputs to absolute paths (follows symlinks)."""
    path = Path(relative_or_absolute).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _settings_candidates() -> list[Path]:
    """Where study_settings.py may live, most specific first."""
    candidates: list[Path] = []
    explicit = os.environ.get("LAKE_DELINEATION_SETTINGS")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    root_env = os.environ.get("LAKE_DELINEATION_ROOT")
    if root_env:
        candidates.append(Path(root_env).expanduser() / "study_settings.py")
    candidates.append(Path("study_settings.py"))
    return candidates


def _find_settings_file() -> Path:
    for path in _settings_candidates():
        if path.is_file():
            return path.resolve()
    looked = ", ".join(str(path) for path in _settings_candidates())
    raise FileNotFoundError(
        "Missing study_settings.py. Add it in the study root and edit it. "
        f"Looked for: {looked}"
    )


def _load_study_settings(settings_path: Path):
    spec = importlib.util.spec_from_file_location("study_settings", settings_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {settings_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _root_from_settings(module, settings_file: Path) -> Path | None:
    """Path set in study_settings.py. None means the file's own folder."""
    if not hasattr(module, "LAKE_DELINEATION_ROOT"):
        return None
    raw = module.LAKE_DELINEATION_ROOT
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = settings_file.parent / path
    return path.resolve()


SETTINGS_FILE = _find_settings_file()
_settings = _load_study_settings(SETTINGS_FILE)

# An exported LAKE_DELINEATION_ROOT wins. Otherwise the value in
# study_settings.py, and if that is None, the folder that contains the file.
_env_root = os.environ.get("LAKE_DELINEATION_ROOT")
if _env_root:
    PROJECT_ROOT = Path(_env_root).expanduser().resolve()
else:
    PROJECT_ROOT = _root_from_settings(_settings, SETTINGS_FILE) or SETTINGS_FILE.parent


def _require(module, name: str):
    if not hasattr(module, name):
        raise AttributeError(f"{SETTINGS_FILE} must define {name}")
    return getattr(module, name)


def _positive_int(value, name: str) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive whole number, got {value!r}") from exc
    if not number.is_integer() or number <= 0:
        raise ValueError(f"{name} must be a positive whole number, got {value!r}")
    return int(number)


def _positive_float(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number greater than 0, got {value!r}") from exc
    if number <= 0:
        raise ValueError(f"{name} must be a number greater than 0, got {value!r}")
    return number


def _as_tuple(value, name: str) -> tuple | None:
    """One value, or a list of values. None stays None. A string is one value."""
    if value is None:
        return None
    if isinstance(value, (str, bytes, int, float)):
        return (value,)
    try:
        return tuple(value)
    except TypeError as exc:
        raise ValueError(f"{name} must be a list of values, got {value!r}") from exc


INPUT_DEM = _study_path(_require(_settings, "INPUT_DEM"))

_lake_raw = getattr(_settings, "INPUT_LAKES", None)
if _lake_raw is None:
    _lake_raw = getattr(_settings, "INPUT_HYDROLAKES", None)
if _lake_raw is None:
    raise AttributeError(
        f"{SETTINGS_FILE} must define INPUT_LAKES "
        "(or INPUT_HYDROLAKES)"
    )
INPUT_LAKES = _study_path(_lake_raw)
INPUT_HYDROLAKES = INPUT_LAKES

_DEFAULT_LAKE_COLUMNS = {"id": "Hylak_id", "area": "Lake_area", "type": "Lake_type"}
_lake_columns = dict(_DEFAULT_LAKE_COLUMNS)
_lake_columns.update(getattr(_settings, "LAKE_COLUMNS", None) or {})
LAKE_ID_COLUMN = _lake_columns.get("id") or _DEFAULT_LAKE_COLUMNS["id"]
LAKE_AREA_COLUMN = _lake_columns.get("area") or _DEFAULT_LAKE_COLUMNS["area"]
_type_column = _lake_columns.get("type")
LAKE_TYPE_COLUMN = _type_column if _type_column else None
LAKE_MIN_AREA_SQKM = _positive_float(
    getattr(_settings, "LAKE_MIN_AREA_SQKM", 5.0), "LAKE_MIN_AREA_SQKM"
)
LAKE_KEEP_TYPES = _as_tuple(getattr(_settings, "LAKE_KEEP_TYPES", (2, 3)), "LAKE_KEEP_TYPES")
LAKE_TYPES_MUST_MATCH = bool(getattr(_settings, "LAKE_TYPES_MUST_MATCH", False))
if LAKE_KEEP_TYPES and LAKE_TYPE_COLUMN is None:
    raise ValueError(
        "LAKE_KEEP_TYPES is set but LAKE_COLUMNS['type'] is empty. "
        "Name the type column, or set LAKE_KEEP_TYPES = None."
    )

STREAM_THRESHOLD = _positive_int(getattr(_settings, "STREAM_THRESHOLD", 5555), "STREAM_THRESHOLD")
MIN_SUB_AREA = _positive_float(getattr(_settings, "MIN_SUB_AREA", 100.0), "MIN_SUB_AREA")

_DEFAULT_GAUGE_COLUMNS = {
    "id": "STATION_NUMBER",
    "name": "STATION_NAME",
    "latitude": "LATITUDE",
    "longitude": "LONGITUDE",
}
_SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}


def gauge_kind(path: str | Path) -> str:
    """SQLite is a HYDAT database. CSV is a table. Anything else is a point file."""
    suffix = Path(path).suffix.lower()
    if suffix in _SQLITE_SUFFIXES:
        return "hydat"
    if suffix == ".csv":
        return "csv"
    return "points"


def gauge_entry(
    path: str | Path,
    name: str | None = None,
    columns: dict | None = None,
) -> dict:
    """One INPUT_GAUGES item, with the file type taken from its suffix."""
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = _study_path(resolved)
    else:
        resolved = resolved.resolve()
    kind = gauge_kind(resolved)
    mapped = dict(_DEFAULT_GAUGE_COLUMNS)
    if columns:
        mapped.update(columns)
    label = str(name).strip() if name else resolved.stem
    if kind != "hydat" and not mapped.get("id"):
        raise ValueError(f"{label} needs columns['id']")
    return {"name": label, "path": resolved, "kind": kind, "columns": mapped}


def _parse_gauge_entries(raw) -> list[dict]:
    if raw is None or raw == "":
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    entries: list[dict] = []
    for index, item in enumerate(items, start=1):
        if isinstance(item, dict):
            path = item.get("path")
            if not path:
                raise ValueError(f"INPUT_GAUGES entry {index} is missing its path")
            entries.append(gauge_entry(path, item.get("name"), item.get("columns")))
        else:
            entries.append(gauge_entry(item))
    return entries


_gauge_raw = getattr(_settings, "INPUT_GAUGES", None)
if _gauge_raw:
    INPUT_GAUGES = _parse_gauge_entries(_gauge_raw)
else:
    # Older settings named the database separately. One HYDAT file is the same list.
    _legacy_columns = dict(_DEFAULT_GAUGE_COLUMNS)
    _legacy_columns.update(getattr(_settings, "GAUGE_COLUMNS", None) or {})
    _hydat_raw = getattr(_settings, "INPUT_HYDAT_DB", None)
    if not _hydat_raw:
        raise AttributeError(f"{SETTINGS_FILE} must define INPUT_GAUGES")
    INPUT_GAUGES = [gauge_entry(_hydat_raw, "HYDAT", _legacy_columns)]

INPUT_HYDAT_DB = next(
    (item["path"] for item in INPUT_GAUGES if item["kind"] == "hydat"),
    None,
)

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
PREP_GAUGES = PREP / "gauges.shp"
PREP_RESERVOIR_IO_NODES = PREP / "reservoir_io_nodes.shp"

# Post-processing working copies
WORKING_BASINS_MERGED = WORKING / "basins_merged.shp"
WORKING_STREAMS_MERGED = WORKING / "streams_merged.shp"

# Final deliverables
FINAL_BASINS = FINAL / "basins.shp"
FINAL_STREAMS = FINAL / "streams.shp"
FINAL_BASINS_AGG = FINAL / "basins_aggregated.shp"
FINAL_STREAMS_AGG = FINAL / "streams_aggregated.shp"
FINAL_POUR_POINTS = FINAL / "pour_points.shp"

PATHS: dict[str, str] = {
    "dem": str(INPUT_DEM),
    "hydat_db": str(INPUT_HYDAT_DB) if INPUT_HYDAT_DB else "",
    "gauges_source": ";".join(str(item["path"]) for item in INPUT_GAUGES),
    "hydrolakes": str(INPUT_LAKES),
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
    "gauges": str(PREP_GAUGES),
    "reservoir_io_nodes": str(PREP_RESERVOIR_IO_NODES),
    "working_basins_merged": str(WORKING_BASINS_MERGED),
    "working_streams_merged": str(WORKING_STREAMS_MERGED),
    "final_basins": str(FINAL_BASINS),
    "final_streams": str(FINAL_STREAMS),
    "final_basins_aggregated": str(FINAL_BASINS_AGG),
    "final_streams_aggregated": str(FINAL_STREAMS_AGG),
    "final_pour_points": str(FINAL_POUR_POINTS),
}

DELETE_INTERIM_ENV = "DELETE_INTERIM_FILES"

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
    Remove ``outputs/interim/``, including the folder itself.

    Safe to call after ``cleanGeofabric.py``. Final products and ``outputs/working/``
    are kept. Returns the removed path, or an empty list when cleanup is off or
    the folder is already gone.
    """
    if enabled is None:
        enabled = delete_interim_enabled(default=default)
    if not enabled:
        return []
    if not INTERIM.exists():
        print("Interim cleanup enabled but outputs/interim/ was not present.")
        return []

    shutil.rmtree(INTERIM)
    print(f"Removed interim directory: {INTERIM}")
    return [INTERIM]


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
