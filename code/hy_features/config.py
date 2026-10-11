"""Legacy toggle. The delineation scripts do not read this.

Build the GeoPackage with ``export_hy_features.py`` after the shapefiles exist.
"""

from __future__ import annotations

import os

ENV_VAR = "HY_FEATURES_ENABLED"

_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disabled"})


def _parse_flag(value: str) -> bool | None:
    v = value.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


def hy_features_enabled(*, default: bool = False) -> bool:
    """
    Return the legacy ``HY_FEATURES_ENABLED`` flag.

    The delineation scripts no longer call this. ``export_hy_features.py`` runs
    when you invoke it, and does not consult this flag.
    """
    raw = os.environ.get(ENV_VAR)
    if raw is not None:
        parsed = _parse_flag(raw)
        if parsed is None:
            raise ValueError(
                f"{ENV_VAR}={raw!r} is invalid; use 1/0, true/false, on/off, yes/no"
            )
        return parsed
    return default
