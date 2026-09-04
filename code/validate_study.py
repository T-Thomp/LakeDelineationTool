#!/usr/bin/env python3
"""Validate study_settings.py and print paths for the slurm workflow."""

from __future__ import annotations

import argparse
import shlex
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
    settings = PROJECT_ROOT / "study_settings.py"
    print(f"study_settings: {settings}")
    print(f"INPUT_DEM: {INPUT_DEM}")
    print(f"INPUT_HYDAT_DB: {INPUT_HYDAT_DB}")
    print(f"INPUT_HYDROLAKES: {INPUT_HYDROLAKES}")

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
        print("ERROR: missing input file(s):", file=sys.stderr)
        for line in missing:
            print(line, file=sys.stderr)
        print(f"Edit {settings} and re-run.", file=sys.stderr)
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--query",
        choices=("dem", "fdr"),
        help="Print one path for slurm (no validation)",
    )
    parser.add_argument(
        "--shell",
        action="store_true",
        help="Print bash export statements for DEM and FDR_RASTER",
    )
    args = parser.parse_args()

    if args.query == "dem":
        print(INPUT_DEM)
        return 0
    if args.query == "fdr":
        print(FDR_CENTERLINE)
        return 0
    if args.shell:
        if _validate() != 0:
            return 1
        print(f"export DEM={shlex.quote(str(INPUT_DEM))}")
        print(f"export FDR_RASTER={shlex.quote(str(FDR_CENTERLINE))}")
        return 0

    return _validate()


if __name__ == "__main__":
    sys.exit(main())
