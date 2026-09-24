"""HY_Features dataclasses for the catchment registry sidecar."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hy_features.json_export import json_optional


@dataclass
class Catchment:
    """HY_DendriticCatchment — holistic catchment identity."""

    code: str
    hyf_type: str = "HY_DendriticCatchment"
    outflow_nexus_id: str | None = None
    inflow_nexus_id: str | None = None
    lower_catchment_id: str | None = None
    upper_catchment_ids: list[str] = field(default_factory=list)
    waterbody_id: str | None = None


@dataclass
class CatchmentRealization:
    """``catchmentRealization``: a geometric feature that realizes a holistic catchment."""

    catchment_id: str
    realization_type: str
    feature_id: str
    notes: str = ""


@dataclass
class CatchmentAssociation:
    """A feature linked to a catchment that is not one of its realizations."""

    catchment_id: str
    feature_type: str
    feature_id: str
    role: str


@dataclass
class CatchmentRegistry:
    """Registry of catchment identity separate from geometric realizations."""

    entries: list[CatchmentRealization] = field(default_factory=list)
    associations: list[CatchmentAssociation] = field(default_factory=list)
    catchments: dict[str, Catchment] = field(default_factory=dict)

    def add_catchment(
        self,
        catchment_id: str,
        *,
        lower_catchment_id: str | None = None,
        waterbody_id: str | None = None,
    ) -> Catchment:
        catchment = self.catchments.get(catchment_id)
        if catchment is None:
            catchment = Catchment(code=catchment_id)
            self.catchments[catchment_id] = catchment
        if lower_catchment_id:
            catchment.lower_catchment_id = lower_catchment_id
        if waterbody_id:
            catchment.waterbody_id = waterbody_id
        return catchment

    def add(
        self,
        catchment_id: str,
        realization_type: str,
        feature_id: str,
        notes: str = "",
    ) -> None:
        """Record (or update) the ``realization_type`` realization of ``catchment_id``."""
        self.add_catchment(catchment_id)
        for entry in self.entries:
            if entry.catchment_id == catchment_id and entry.realization_type == realization_type:
                entry.feature_id = feature_id
                if notes:
                    entry.notes = notes
                return
        self.entries.append(
            CatchmentRealization(
                catchment_id=catchment_id,
                realization_type=realization_type,
                feature_id=feature_id,
                notes=notes,
            )
        )

    def associate(
        self,
        catchment_id: str,
        feature_type: str,
        feature_id: str,
        role: str,
    ) -> bool:
        """Link a non-realization feature to a known catchment; unknown ids are ignored."""
        if catchment_id not in self.catchments:
            return False
        for assoc in self.associations:
            if (
                assoc.catchment_id == catchment_id
                and assoc.feature_id == feature_id
                and assoc.role == role
            ):
                return True
        self.associations.append(
            CatchmentAssociation(
                catchment_id=catchment_id,
                feature_type=feature_type,
                feature_id=feature_id,
                role=role,
            )
        )
        return True

    def to_full_payload(self) -> dict[str, Any]:
        return {
            "catchments": {
                k: {
                    "code": v.code,
                    "hyf_type": v.hyf_type,
                    "outflow_nexus_id": json_optional(v.outflow_nexus_id),
                    "inflow_nexus_id": json_optional(v.inflow_nexus_id),
                    "lower_catchment_id": json_optional(v.lower_catchment_id),
                    "upper_catchment_ids": list(v.upper_catchment_ids),
                    "waterbody_id": json_optional(v.waterbody_id),
                }
                for k, v in self.catchments.items()
            },
            "realizations": self.to_records(),
            "associations": [
                {
                    "catchment_id": a.catchment_id,
                    "feature_type": a.feature_type,
                    "feature_id": a.feature_id,
                    "role": a.role,
                }
                for a in self.associations
            ],
        }

    def to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "catchment_id": e.catchment_id,
                "realization_type": e.realization_type,
                "feature_id": e.feature_id,
                "notes": e.notes,
            }
            for e in self.entries
        ]
