"""HY_Features dataclasses mirroring implemented UML types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Catchment:
    """HY_DendriticCatchment — holistic catchment identity."""

    code: str
    hyf_type: str = "HY_DendriticCatchment"
    outflow_nexus_id: str | None = None
    inflow_nexus_id: str | None = None
    lower_catchment_id: str | None = None
    waterbody_id: str | None = None


@dataclass
class CatchmentRealization:
    """Links a geometric feature back to its holistic catchment."""

    catchment_id: str
    realization_type: str
    feature_id: str
    waterbody_id: str | None = None
    notes: str = ""


@dataclass
class FlowPath:
    """HY_FlowPath — one-dimensional catchment realization."""

    flowpath_id: str
    catchment_id: str
    lower_catchment_id: str | None
    hyf_type: str = "HY_FlowPath"


@dataclass
class HydroLocation:
    """HY_HydroLocation — nexus or significant network point."""

    code: str
    hydro_location_type: str
    catchment_id: str | None = None
    waterbody_id: str | None = None
    hyf_type: str = "HY_HydroLocation"


@dataclass
class HydrometricFeature:
    """HY_HydrometricFeature — monitoring station on the network."""

    station_code: str
    host_flowpath_id: str | None = None
    catchment_id: str | None = None
    hyf_type: str = "HY_HydrometricFeature"


@dataclass
class CatchmentRegistry:
    """Registry of catchment identity separate from geometric realizations."""

    entries: list[CatchmentRealization] = field(default_factory=list)
    catchments: dict[str, Catchment] = field(default_factory=dict)

    def add(
        self,
        catchment_id: str,
        realization_type: str,
        feature_id: str,
        waterbody_id: str | None = None,
        notes: str = "",
        lower_catchment_id: str | None = None,
    ) -> None:
        self.entries.append(
            CatchmentRealization(
                catchment_id=catchment_id,
                realization_type=realization_type,
                feature_id=feature_id,
                waterbody_id=waterbody_id,
                notes=notes,
            )
        )
        if catchment_id not in self.catchments:
            self.catchments[catchment_id] = Catchment(
                code=catchment_id,
                lower_catchment_id=lower_catchment_id,
                waterbody_id=waterbody_id,
                outflow_nexus_id=f"nx_out_{catchment_id}",
            )
        elif lower_catchment_id:
            self.catchments[catchment_id].lower_catchment_id = lower_catchment_id

    def to_full_payload(self) -> dict[str, Any]:
        return {
            "catchments": {k: v.__dict__ for k, v in self.catchments.items()},
            "realizations": self.to_records(),
        }

    def to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "catchment_id": e.catchment_id,
                "realization_type": e.realization_type,
                "feature_id": e.feature_id,
                "waterbody_id": e.waterbody_id or "",
                "notes": e.notes,
            }
            for e in self.entries
        ]
