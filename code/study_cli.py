"""Load ``--settings`` before ``pipeline_paths`` reads the study file.

Call ``apply_settings_arg()`` at the top of a script, before importing
``pipeline_paths``. Then add ``--settings`` to that script's argument parser
so the flag is documented and accepted.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

SETTINGS_PATH: Path | None = None


def apply_settings_arg(argv: list[str] | None = None) -> Path | None:
    """Point this process at the study_settings.py named on the command line."""
    global SETTINGS_PATH
    args = sys.argv if argv is None else argv
    raw: str | None = None
    for index, arg in enumerate(args):
        if arg == "--settings":
            if index + 1 >= len(args) or args[index + 1].startswith("-"):
                sys.exit("error: --settings requires a path to study_settings.py")
            raw = args[index + 1]
            break
        if arg.startswith("--settings="):
            raw = arg.split("=", 1)[1]
            break
    if raw is None:
        return None
    path = Path(raw).expanduser().resolve()
    if not path.is_file():
        sys.exit(f"error: --settings file not found: {path}")
    os.environ["LAKE_DELINEATION_SETTINGS"] = str(path)
    SETTINGS_PATH = path
    return path


def add_settings_argument(parser) -> None:
    parser.add_argument(
        "--settings",
        type=Path,
        help=(
            "study_settings.py for this run. The DEM, lakes, and gauges come "
            "from that file. Other files default to pipeline_paths.py. "
            "Pass --fdr, --lakes, or another file flag to use a different path."
        ),
    )
