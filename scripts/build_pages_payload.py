#!/usr/bin/env python3
"""Build an allowlisted GitHub Pages payload with complete file checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
CONTROL_FILES: tuple[str, ...] = ()

PUBLIC_FILES = (
    "app.js",
    "data.js",
    "index.html",
    "map.js",
    "robots.txt",
    "sitemap.xml",
    "styles.css",
    "vendor/leaflet-1.9.4/LICENSE",
    "vendor/leaflet-1.9.4/images/marker-icon-2x.png",
    "vendor/leaflet-1.9.4/images/marker-icon.png",
    "vendor/leaflet-1.9.4/images/marker-shadow.png",
    "vendor/leaflet-1.9.4/leaflet.css",
    "vendor/leaflet-1.9.4/leaflet.js",
)


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_commit() -> str:
    override = os.environ.get("SOURCE_COMMIT", "").strip()
    if override:
        return override
    return subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()


def safe_manifest_path(prefix: str, raw_path: object) -> str:
    if not isinstance(raw_path, str) or "\\" in raw_path:
        raise ValueError("manifest path must be a forward-slash relative string")
    path = PurePosixPath(raw_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe manifest path: {raw_path!r}")
    return f"{prefix}/{path.as_posix()}"


def verify_inventory_entry(relative_path: str, entry: object) -> None:
    if not isinstance(entry, dict):
        raise ValueError(f"asset inventory entry must be an object: {relative_path}")
    expected_bytes = entry.get("bytes")
    expected_sha = entry.get("sha256")
    if not isinstance(expected_bytes, int) or expected_bytes < 0:
        raise ValueError(f"asset inventory byte count is invalid: {relative_path}")
    if not isinstance(expected_sha, str) or re.fullmatch(r"[0-9a-f]{64}", expected_sha) is None:
        raise ValueError(f"asset inventory checksum is invalid: {relative_path}")
    source = ROOT / relative_path
    if not source.is_file():
        raise FileNotFoundError(f"cataloged asset is missing: {relative_path}")
    if source.stat().st_size != expected_bytes:
        raise ValueError(f"cataloged asset byte count mismatch: {relative_path}")
    if sha256_path(source) != expected_sha:
        raise ValueError(f"cataloged asset checksum mismatch: {relative_path}")


def catalog_climate_paths(catalog: dict[str, object]) -> set[str]:
    if catalog.get("schema_version") != 1:
        raise ValueError("unsupported climate catalog schema")
    assets = catalog.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise ValueError("climate catalog asset inventory is missing")
    paths = {"data/climate/catalog.json"}
    for raw_path, entry in assets.items():
        relative_path = safe_manifest_path("data/climate", raw_path)
        if relative_path == "data/climate/catalog.json":
            raise ValueError("climate catalog must not inventory itself")
        verify_inventory_entry(relative_path, entry)
        paths.add(relative_path)
    actual = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "data/climate").rglob("*")
        if path.is_file()
    }
    if actual != paths:
        raise ValueError(f"climate catalog file set mismatch: {sorted(actual ^ paths)}")
    return paths


def manifest_data_paths() -> tuple[str, ...]:
    season = json.loads((ROOT / "data/season/manifest.json").read_text(encoding="utf-8"))
    catalog_path = ROOT / "data/climate/catalog.json"
    if catalog_path.is_file():
        climate_paths = catalog_climate_paths(json.loads(catalog_path.read_text(encoding="utf-8")))
    else:
        climate = json.loads((ROOT / "data/climate/manifest.json").read_text(encoding="utf-8"))
        climate_paths = {"data/climate/manifest.json"}
        climate_paths.update(safe_manifest_path("data/climate", entry["path"]) for entry in climate["chunks"].values())
        for group in climate["rasters"]["files"].values():
            climate_paths.update(safe_manifest_path("data/climate", entry["path"]) for entry in group.values())
        climate_paths.add(safe_manifest_path("data/climate", climate["static"]["prefectures"]["path"]))
    season_paths = {"data/season/manifest.json"}
    season_paths.update(safe_manifest_path("data/season", entry["path"]) for entry in season["files"].values())
    return tuple(sorted(climate_paths | season_paths))


def copy_relative(relative_path: str, output: Path) -> None:
    source = (ROOT / relative_path).resolve()
    if ROOT not in source.parents:
        raise ValueError(f"source path escapes repository: {relative_path}")
    if not source.is_file():
        raise FileNotFoundError(f"required public file is missing: {relative_path}")
    destination = (output / relative_path).resolve()
    if output not in destination.parents:
        raise ValueError(f"destination path escapes Pages payload: {relative_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "_site")
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or ROOT not in output.parents:
        raise ValueError("Pages output must be a child directory of the repository")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    for name in (*CONTROL_FILES, *PUBLIC_FILES, *manifest_data_paths()):
        copy_relative(name, output)

    climate_catalog_path = output / "data/climate/catalog.json"
    climate_manifest_path = output / "data/climate/manifest.json"
    climate_metadata = json.loads(
        (climate_catalog_path if climate_catalog_path.is_file() else climate_manifest_path).read_text(encoding="utf-8")
    )
    season_manifest = json.loads((output / "data/season/manifest.json").read_text(encoding="utf-8"))
    files: dict[str, dict[str, int | str]] = {}
    control_files: dict[str, dict[str, int | str]] = {}
    for path in sorted(output.rglob("*")):
        if path.is_file():
            rel = path.relative_to(output).as_posix()
            entry = {"bytes": path.stat().st_size, "sha256": sha256_path(path)}
            if rel in CONTROL_FILES:
                control_files[rel] = entry
            else:
                files[rel] = entry
    deployment = {
        "schema_version": 2,
        "source_commit": source_commit(),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "climate_dataset_id": climate_metadata["dataset_id"],
        "season_dataset_id": season_manifest["dataset_id"],
        "file_count_without_deployment_manifest": len(files) + len(control_files),
        "total_bytes_without_deployment_manifest": sum(
            int(entry["bytes"]) for entry in (*files.values(), *control_files.values())
        ),
        "publicly_verifiable_file_count": len(files),
        "control_files": control_files,
        "files": files,
    }
    (output / "deployment.json").write_text(
        json.dumps(deployment, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "source_commit": deployment["source_commit"],
        "climate_dataset_id": deployment["climate_dataset_id"],
        "season_dataset_id": deployment["season_dataset_id"],
        "file_count": deployment["file_count_without_deployment_manifest"] + 1,
        "total_bytes": deployment["total_bytes_without_deployment_manifest"] + (output / "deployment.json").stat().st_size,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
