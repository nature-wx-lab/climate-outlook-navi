#!/usr/bin/env python3
"""Build an allowlisted GitHub Pages payload with complete file checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def manifest_data_paths() -> tuple[str, ...]:
    climate = json.loads((ROOT / "data/climate/manifest.json").read_text(encoding="utf-8"))
    season = json.loads((ROOT / "data/season/manifest.json").read_text(encoding="utf-8"))
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

    climate_manifest = json.loads((output / "data/climate/manifest.json").read_text(encoding="utf-8"))
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
        "climate_dataset_id": climate_manifest["dataset_id"],
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
