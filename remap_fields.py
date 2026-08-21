#!/usr/bin/env python3
"""
Remap HY_Features canonical columns to downstream model field names.

Reads GeoPackage or shapefile inputs and writes shapefile (or GeoPackage) outputs
with column names defined by a named preset (mesh, taudem_raw, or your own).

Examples
--------
  python remap_fields.py --list-presets
  python remap_fields.py --list-mappings --preset mesh

  python remap_fields.py \\
      --preset mesh \\
      --basins merged_basins/geofabric.gpkg \\
      --streams merged_basins/geofabric.gpkg \\
      --drop-metadata \\
      --out-dir remapped_products/

  python remap_fields.py \\
      --preset my_model \\
      --preset-file my_model.json \\
      -i merged_basins/geofabric.gpkg \\
      -o remapped_products/streams.shp \\
      --layer-kind flowpath \\
      --input-layer flowpath \\
      --override lower_catchment_id=DOWNSTREAM_ID
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from hy_features.field_remap import (
    CANONICAL_COLUMNS_BY_LAYER,
    build_custom_mapping,
    get_model_mapping,
    list_available_mappings,
    list_model_names,
    load_model_presets,
    remap_vector_file,
)


def _parse_overrides(pairs: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise argparse.ArgumentTypeError(
                f"Override must be canonical=output_column, got: {item!r}"
            )
        canonical, output_name = item.split("=", 1)
        overrides[canonical.strip()] = output_name.strip()
    return overrides


def _write_mapping_sidecar(
    output_path: Path,
    layer_kind: str,
    preset: str,
    mapping: dict[str, str],
) -> None:
    sidecar = output_path.with_suffix(output_path.suffix + f".{preset}_mapping.json")
    sidecar.write_text(
        json.dumps({"preset": preset, "layer_kind": layer_kind, "mapping": mapping}, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Remap HY_Features columns to downstream model shapefile field names.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--list-mappings",
        action="store_true",
        help="Print column mappings for --preset and exit.",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="List available presets and exit.",
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="Alias for --list-presets.",
    )
    parser.add_argument(
        "--preset",
        default="mesh",
        help="Preset name (default: mesh). See hy_features/model_presets.json.",
    )
    parser.add_argument(
        "--model",
        dest="preset",
        help="Alias for --preset.",
    )
    parser.add_argument(
        "--preset-file",
        help="Optional JSON file with additional presets.",
    )
    parser.add_argument("--input", "-i", help="Input vector file (.gpkg or .shp).")
    parser.add_argument("--output", "-o", help="Output vector file path.")
    parser.add_argument(
        "--input-layer",
        help="GeoPackage layer name when reading --input.",
    )
    parser.add_argument("--basins", help="Basins input for batch mode (catchment_area).")
    parser.add_argument("--streams", help="Streams input for batch mode (flowpath).")
    parser.add_argument("--basins-layer", default="catchment_area")
    parser.add_argument("--streams-layer", default="flowpath")
    parser.add_argument("--out-dir", default="remapped_products")
    parser.add_argument(
        "--layer-kind",
        choices=["catchment_area", "flowpath"],
        help="Layer kind for single --input mode.",
    )
    parser.add_argument(
        "--override",
        action="append",
        default=[],
        metavar="CANONICAL=OUTPUT",
        help="Override a column mapping (repeatable).",
    )
    parser.add_argument(
        "--drop-metadata",
        action="store_true",
        help="Remove hyf_type, nexus ids, and other HY-only metadata columns.",
    )
    parser.add_argument(
        "--drop-hyf-metadata",
        action="store_true",
        help="Alias for --drop-metadata.",
    )
    parser.add_argument(
        "--keep-ids-as-string",
        action="store_true",
        help="Do not coerce ID columns to integers.",
    )
    parser.add_argument(
        "--outlet-sentinel",
        type=int,
        default=None,
        help="Downstream ID at domain outlet (default: from preset, e.g. mesh=-9999).",
    )
    parser.add_argument(
        "--write-sidecar",
        action="store_true",
        help="Write a .<preset>_mapping.json next to each output.",
    )

    args = parser.parse_args(argv)
    drop_metadata = args.drop_metadata or args.drop_hyf_metadata

    if args.list_presets or args.list_models:
        presets = load_model_presets(args.preset_file)
        print("Available presets:\n")
        for name in sorted(presets):
            spec = presets[name]
            desc = spec.get("description", "")
            print(f"  {name}")
            if desc:
                print(f"    {desc}")
            layers = spec.get("layers", spec)
            print(f"    layers: {', '.join(sorted(layers))}")
        print(f"\nEdit: {Path(__file__).parent / 'hy_features' / 'model_presets.json'}")
        if args.preset_file:
            print(f"  + custom: {args.preset_file}")
        return 0

    if args.list_mappings:
        print(f"Preset: {args.preset}\n")
        try:
            mappings = list_available_mappings(preset=args.preset, preset_path=args.preset_file)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        for layer_kind, mapping in sorted(mappings.items()):
            print(f"  [{layer_kind}]")
            for canonical, out_col in sorted(mapping.items()):
                print(f"    {canonical:24} -> {out_col}")
            print(f"  Canonical sources: {', '.join(CANONICAL_COLUMNS_BY_LAYER.get(layer_kind, []))}")
            print()
        try:
            _, sentinel = get_model_mapping("flowpath", preset=args.preset, preset_path=args.preset_file)
            print(f"  Outlet sentinel (downstream ID at domain outlet): {sentinel}")
        except ValueError:
            pass
        return 0

    overrides = _parse_overrides(args.override)

    def _mapping_for(kind: str) -> dict[str, str]:
        if overrides:
            return build_custom_mapping(
                kind, overrides, preset=args.preset, preset_path=args.preset_file,
            )
        mapping, _ = get_model_mapping(kind, preset=args.preset, preset_path=args.preset_file)
        return mapping

    if args.basins or args.streams:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        if args.basins:
            kind = "catchment_area"
            mapping = _mapping_for(kind)
            out_path = out_dir / f"basins_{args.preset}.shp"
            layer = args.basins_layer if str(args.basins).lower().endswith(".gpkg") else None
            remap_vector_file(
                args.basins, str(out_path), kind,
                mapping=mapping, preset=args.preset, preset_path=args.preset_file,
                input_layer=layer,
                drop_metadata=drop_metadata,
                coerce_ids_to_int=not args.keep_ids_as_string,
                outlet_sentinel=args.outlet_sentinel,
            )
            print(f"Wrote {out_path}")
            if args.write_sidecar:
                _write_mapping_sidecar(out_path, kind, args.preset, mapping)

        if args.streams:
            kind = "flowpath"
            mapping = _mapping_for(kind)
            out_path = out_dir / f"streams_{args.preset}.shp"
            layer = args.streams_layer if str(args.streams).lower().endswith(".gpkg") else None
            remap_vector_file(
                args.streams, str(out_path), kind,
                mapping=mapping, preset=args.preset, preset_path=args.preset_file,
                input_layer=layer,
                drop_metadata=drop_metadata,
                coerce_ids_to_int=not args.keep_ids_as_string,
                outlet_sentinel=args.outlet_sentinel,
            )
            print(f"Wrote {out_path}")
            if args.write_sidecar:
                _write_mapping_sidecar(out_path, kind, args.preset, mapping)
        return 0

    if not args.input or not args.output or not args.layer_kind:
        parser.error("Provide --input, --output, and --layer-kind, or use --basins/--streams.")

    mapping = _mapping_for(args.layer_kind)
    remap_vector_file(
        args.input, args.output, args.layer_kind,
        mapping=mapping, preset=args.preset, preset_path=args.preset_file,
        input_layer=args.input_layer,
        drop_metadata=drop_metadata,
        coerce_ids_to_int=not args.keep_ids_as_string,
        outlet_sentinel=args.outlet_sentinel,
    )
    print(f"Wrote {args.output}")
    if args.write_sidecar:
        _write_mapping_sidecar(Path(args.output), args.layer_kind, args.preset, mapping)
    return 0


if __name__ == "__main__":
    sys.exit(main())
