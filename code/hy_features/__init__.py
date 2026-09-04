"""OGC HY_Features (14-111r6) alignment for Lake Delineation Tool outputs."""

from hy_features.config import ENV_VAR, hy_features_enabled
from hy_features.schema import (
    CATCHMENT_ID,
    DEFAULT_LAYER_ALIASES,
    FLOWPATH_ID,
    HYF_TYPE,
    LOWER_CATCHMENT_ID,
    MESH_FIELD_ALIASES,
    REALIZES_CATCHMENT,
    WATERBODY_CLASS,
    WATERBODY_ID,
)

__all__ = [
    "ENV_VAR",
    "hy_features_enabled",
    "CATCHMENT_ID",
    "DEFAULT_LAYER_ALIASES",
    "FLOWPATH_ID",
    "HYF_TYPE",
    "LOWER_CATCHMENT_ID",
    "MESH_FIELD_ALIASES",
    "REALIZES_CATCHMENT",
    "WATERBODY_CLASS",
    "WATERBODY_ID",
]
