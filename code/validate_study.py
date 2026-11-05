#!/usr/bin/env python3
"""Check study_settings.py inputs exist. Used by Delineation-Workflow.slurm."""

from __future__ import annotations

import argparse
import sys

import study_cli

if __name__ == "__main__":
    study_cli.apply_settings_arg()

from pipeline_paths import (
    FDR_CENTERLINE,
    GAUGE_SOURCE,
    INPUT_DEM,
    INPUT_GAUGES,
    INPUT_HYDAT_DB,
    INPUT_LAKES,
    MIN_SUB_AREA,
    PROJECT_ROOT,
    SETTINGS_FILE,
    STREAM_THRESHOLD,
    ensure_output_dirs,
)


def _validate() -> int:
    ensure_output_dirs()

    missing: list[str] = []
    checks: list[tuple[str, object]] = [
        ("DEM", INPUT_DEM),
        ("lakes", INPUT_LAKES),
    ]
    if GAUGE_SOURCE == "hydat":
        checks.append(("HYDAT", INPUT_HYDAT_DB))
    else:
        checks.append(("gauges", INPUT_GAUGES))

    for label, path in checks:
        if path is None or not path.is_file():
            missing.append(f"  {label}: {path}")

    if missing:
        print("ERROR: missing input file(s):", file=sys.stderr)
        for line in missing:
            print(line, file=sys.stderr)
        print(f"Edit {SETTINGS_FILE} and re-run.", file=sys.stderr)
        return 1

    print(f"Study root: {PROJECT_ROOT}")
    print(f"DEM found: {INPUT_DEM}")
    print(f"Lakes found: {INPUT_LAKES}")
    if GAUGE_SOURCE == "hydat":
        print(f"HYDAT found: {INPUT_HYDAT_DB}")
    else:
        print(f"Gauges found: {INPUT_GAUGES}")
    print(f"Stream threshold (cells): {STREAM_THRESHOLD}")
    print(f"Aggregation threshold (km²): {MIN_SUB_AREA:g}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    study_cli.add_settings_argument(parser)
    parser.add_argument(
        "--query",
        choices=(
            "dem",
            "lakes",
            "hydat",
            "gauges",
            "gauge-source",
            "fdr",
            "stream-threshold",
            "root",
        ),
        help="Print one path or setting (used by the slurm script)",
    )
    args = parser.parse_args()

    if args.query == "root":
        print(PROJECT_ROOT)
        return 0
    if args.query == "dem":
        print(INPUT_DEM)
        return 0
    if args.query == "lakes":
        print(INPUT_LAKES)
        return 0
    if args.query == "hydat":
        print(INPUT_HYDAT_DB or "")
        return 0
    if args.query == "gauges":
        print(INPUT_GAUGES or "")
        return 0
    if args.query == "gauge-source":
        print(GAUGE_SOURCE)
        return 0
    if args.query == "fdr":
        print(FDR_CENTERLINE)
        return 0
    if args.query == "stream-threshold":
        print(STREAM_THRESHOLD)
        return 0

    return _validate()


if __name__ == "__main__":
    sys.exit(main())
