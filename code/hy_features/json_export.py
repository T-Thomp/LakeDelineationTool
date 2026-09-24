"""JSON helpers for HY sidecar exports."""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def to_json_scalar(value: Any) -> Any:
    """Convert NumPy / pandas scalars to native Python types; NaN becomes ``None``."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def json_optional(value: Any) -> Any:
    """Map absent GeoPackage values to JSON null for nillable associations."""
    value = to_json_scalar(value)
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("", "nan", "none"):
        return None
    return value


def json_default(value: Any) -> Any:
    """``json.dumps(default=...)`` hook for NumPy scalars and arrays."""
    if isinstance(value, np.ndarray):
        return [to_json_scalar(v) for v in value.tolist()]
    if isinstance(value, np.generic):
        return to_json_scalar(value)
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def clean_json_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a flat record for JSON sidecars (empty strings → null)."""
    return {key: json_optional(value) for key, value in record.items()}


def clean_json_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [clean_json_record(record) for record in records]
