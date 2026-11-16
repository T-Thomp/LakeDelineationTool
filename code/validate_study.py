#!/usr/bin/env python3
"""Check study_settings.py inputs exist. Used by Delineation-Workflow.slurm."""

from __future__ import annotations

import argparse
import sys

from pipeline_paths import (
    FDR_CENTERLINE,
    INPUT_DEM,
    INPUT_HYDAT_DB,
    INPUT_HYDROLAKES,
    PROJECT_ROOT,
    ensure_output_dirs,
)


def _validate() -> int:
    ensure_output_dirs()

    missing: list[str] = []
    for label, path in (
        ("DEM", INPUT_DEM),
        ("HYDAT", INPUT_HYDAT_DB),
        ("HydroLAKES", INPUT_HYDROLAKES),
    ):
        if not path.is_file():
            missing.append(f"  {label}: {path}")

    if missing:
        settings = PROJECT_ROOT / "study_settings.py"
        print("ERROR: missing input file(s):", file=sys.stderr)
        for line in missing:
            print(line, file=sys.stderr)
        print(f"Edit {settings} and re-run.", file=sys.stderr)
        return 1

    print(f"DEM found: {INPUT_DEM}")
    print(f"HYDAT found: {INPUT_HYDAT_DB}")
    print(f"HydroLAKES found: {INPUT_HYDROLAKES}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        choices=("dem", "fdr"),
        help="Print one path (used by slurm after validation)",
    )
    args = parser.parse_args()

    if args.query == "dem":
        print(INPUT_DEM)
        return 0
    if args.query == "fdr":
        print(FDR_CENTERLINE)
        return 0

    return _validate()


if __name__ == "__main__":
    sys.exit(main())
