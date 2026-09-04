"""JSON helpers for HY sidecar exports."""

from __future__ import annotations

from typing import Any


def json_optional(value: Any) -> Any:
    """Map absent GeoPackage values to JSON null for nillable associations."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("", "nan", "none"):
        return None
    return value


def clean_json_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize a flat record for JSON sidecars (empty strings → null)."""
    return {key: json_optional(value) for key, value in record.items()}


def clean_json_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [clean_json_record(record) for record in records]
