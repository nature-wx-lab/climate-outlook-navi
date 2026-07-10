#!/usr/bin/env python3
"""Copy the already-vendored Leaflet distribution into this standalone tool."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path


REQUIRED = (
    "leaflet.js",
    "leaflet.css",
    "LICENSE",
    "images/marker-icon.png",
    "images/marker-icon-2x.png",
    "images/marker-shadow.png",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="Existing Leaflet 1.9.4 vendor directory")
    parser.add_argument(
        "--output",
        default="vendor/leaflet-1.9.4",
        help="Standalone tool vendor directory",
    )
    args = parser.parse_args()
    source = Path(args.source).resolve()
    output = Path(args.output).resolve()
    missing = [relative for relative in REQUIRED if not (source / relative).is_file()]
    if missing:
        raise FileNotFoundError(f"Leaflet vendor is incomplete: {missing}")
    if output.exists():
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, output)
    print(f"Copied Leaflet 1.9.4 vendor ({len(REQUIRED)} required assets verified).")


if __name__ == "__main__":
    main()
