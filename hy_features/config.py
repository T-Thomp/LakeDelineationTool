"""Runtime toggle for HY_Features enrichment and geofabric exports."""

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
    Return whether HY_Features enrichment and geofabric assembly should run.

    Priority:
      1. Environment variable ``HY_FEATURES_ENABLED`` (if set)
      2. ``default`` (per-script constant when env is unset)

    Examples::

        export HY_FEATURES_ENABLED=1   # enable for whole SLURM job
        export HY_FEATURES_ENABLED=0   # disable (default when env unset)
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
