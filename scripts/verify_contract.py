#!/usr/bin/env python3
"""Verify generated 気候ものさしナビ data and local release hygiene."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

import numpy as np


EXPECTED_MESH_COUNT = 387_717
EXPECTED_CHUNK_COUNT = 176
EXPECTED_RASTER_COUNT = 39
TEXT_SUFFIXES = {".html", ".css", ".js", ".json", ".geojson", ".md", ".py", ".svg", ".xml", ".yml", ".yaml", ".txt"}
BLOCKED_PATTERNS = {
    "absolute_user_path": re.compile(r"/" r"Users/"),
    "windows_user_path": re.compile(r"[A-Za-z]:\\\\Users\\\\", re.IGNORECASE),
    "personal_email": re.compile(r"[A-Za-z0-9._%+-]+@(?!users\.noreply\.github\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "private_key": re.compile(r"-----BEGIN " r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "credential_assignment": re.compile(
        r"\b(?:api[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|auth[_-]?token)\b"
        r"\s*[:=]\s*[\"']?[^\s\"']{8,}",
        re.IGNORECASE,
    ),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "bearer_token": re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{16,}", re.IGNORECASE),
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_climate(site_root: Path) -> dict[str, int | bool]:
    root = site_root / "data" / "climate"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["mesh_count"] == EXPECTED_MESH_COUNT, "climate mesh_count mismatch")
    require(len(manifest["chunks"]) == EXPECTED_CHUNK_COUNT, "chunk count mismatch")
    raster_count = sum(len(group) for group in manifest["rasters"]["files"].values())
    require(raster_count == EXPECTED_RASTER_COUNT, "raster count mismatch")
    require(manifest["validation"]["missing_values"] == 0, "manifest reports missing values")
    require(manifest["validation"]["window_row_counts_ok"], "source row counts failed")
    require(manifest["validation"]["mesh_order_matches_master"], "source mesh order failed")

    expected_files = {Path("manifest.json")}
    all_codes: list[np.ndarray] = []
    total_values = 0
    chunk_bytes = 0
    for prefix, entry in sorted(manifest["chunks"].items()):
        path = root / entry["path"]
        expected_files.add(Path(entry["path"]))
        require(path.is_file(), f"missing chunk {path}")
        require(path.stat().st_size == entry["bytes"], f"chunk byte mismatch {path}")
        require(sha256_path(path) == entry["sha256"], f"chunk checksum mismatch {path}")
        raw = path.read_bytes()
        magic, version, count, windows, months, scale, nodata = struct.unpack("<8sIIHHHh", raw[:24])
        require(magic.rstrip(b"\0") == b"NWCBCH1", f"chunk magic mismatch {path}")
        require((version, windows, months, scale, nodata) == (1, 2, 13, 100, -32768), f"chunk header mismatch {path}")
        require(count == entry["count"], f"chunk count mismatch {path}")
        expected_bytes = 24 + count * 12 + windows * months * count * 2
        require(len(raw) == expected_bytes, f"chunk layout mismatch {path}")
        codes = np.frombuffer(raw, dtype="<u4", count=count, offset=24)
        require(np.all(codes[1:] > codes[:-1]), f"chunk codes not ascending {path}")
        require(str(int(codes[0])).zfill(8).startswith(prefix), f"chunk prefix mismatch {path}")
        values_offset = 24 + count * 12
        values = np.frombuffer(raw, dtype="<i2", count=windows * months * count, offset=values_offset)
        require(not np.any(values == nodata), f"chunk contains nodata {path}")
        all_codes.append(codes.copy())
        total_values += int(values.size)
        chunk_bytes += len(raw)

    codes = np.concatenate(all_codes)
    require(codes.size == EXPECTED_MESH_COUNT, "combined chunk mesh count mismatch")
    require(np.unique(codes).size == EXPECTED_MESH_COUNT, "combined chunks contain duplicate mesh codes")
    require(total_values == EXPECTED_MESH_COUNT * 2 * 13, "combined chunk value count mismatch")

    raster_bytes = 0
    for group in manifest["rasters"]["files"].values():
        for entry in group.values():
            path = root / entry["path"]
            expected_files.add(Path(entry["path"]))
            require(path.is_file(), f"missing raster {path}")
            require(path.stat().st_size == entry["bytes"], f"raster byte mismatch {path}")
            require(sha256_path(path) == entry["sha256"], f"raster checksum mismatch {path}")
            raster_bytes += path.stat().st_size

    prefectures = manifest["static"]["prefectures"]
    require(prefectures is not None, "prefecture boundary metadata missing")
    pref_path = root / prefectures["path"]
    expected_files.add(Path(prefectures["path"]))
    require(sha256_path(pref_path) == prefectures["sha256"], "prefecture checksum mismatch")
    actual_files = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
    require(actual_files == expected_files, f"unexpected or missing climate files: {sorted(map(str, actual_files ^ expected_files))}")
    return {
        "mesh_count": int(codes.size),
        "value_count": total_values,
        "chunk_count": len(all_codes),
        "chunk_bytes": chunk_bytes,
        "raster_count": raster_count,
        "raster_bytes": raster_bytes,
        "checksums_ok": True,
    }


def verify_season(site_root: Path) -> dict[str, int | bool | str]:
    root = site_root / "data" / "season"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    latest_path = root / manifest["files"]["latest"]["path"]
    regions_path = root / manifest["files"]["regions"]["path"]
    require(sha256_path(latest_path) == manifest["files"]["latest"]["sha256"], "season latest checksum mismatch")
    require(sha256_path(regions_path) == manifest["files"]["regions"]["sha256"], "season regions checksum mismatch")
    latest = json.loads(latest_path.read_text(encoding="utf-8"))
    regions = json.loads(regions_path.read_text(encoding="utf-8"))
    require(latest["dataset_id"] == manifest["dataset_id"], "season dataset id mismatch")
    dataset_dirs = sorted(path for path in (root / "datasets").iterdir() if path.is_dir())
    retention = manifest["validation"].get("dataset_retention", 2)
    require(1 <= retention <= 5, "season dataset retention must be between 1 and 5")
    require(len(dataset_dirs) <= retention, "unreferenced season datasets exceed retention")
    require(latest_path.parent in dataset_dirs, "active season dataset directory missing")
    expected_files = {Path("manifest.json")}
    for dataset_dir in dataset_dirs:
        dataset_relative = dataset_dir.relative_to(root)
        expected_files.add(dataset_relative / "latest.json")
        expected_files.add(dataset_relative / "regions.geojson")
    actual_files = {path.relative_to(root) for path in root.rglob("*") if path.is_file()}
    require(actual_files == expected_files, f"unexpected or missing season files: {sorted(map(str, actual_files ^ expected_files))}")
    require(len(regions["features"]) == 385, "season feature count mismatch")
    require(len({feature["properties"]["code"] for feature in regions["features"]}) == 376, "season unique code mismatch")
    probability_triplets = 0
    for product_name in ("P1M", "P3M"):
        product = latest["products"][product_name]
        require(len(product["terms"]) == 4, f"{product_name} term count mismatch")
        for term in product["terms"]:
            require(len(term["regions"]) == 376, f"{product_name} region coverage mismatch")
            for value in term["regions"].values():
                probabilities = value["probabilities"]
                require(len(probabilities) == 3 and sum(probabilities) == 100, f"{product_name} probability mismatch")
                probability_triplets += 1
    return {
        "dataset_id": manifest["dataset_id"],
        "feature_count": len(regions["features"]),
        "unique_code_count": len({feature["properties"]["code"] for feature in regions["features"]}),
        "probability_triplets": probability_triplets,
        "dataset_count": len(dataset_dirs),
        "dataset_retention": retention,
        "checksums_ok": True,
    }


def verify_hygiene(site_root: Path) -> dict[str, int | bool]:
    hits: list[str] = []
    scanned = 0
    for path in site_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in BLOCKED_PATTERNS.items():
            if pattern.search(text):
                hits.append(f"{label}:{path.relative_to(site_root)}")
    require(not hits, f"release hygiene hits: {hits}")
    index = (site_root / "index.html").read_text(encoding="utf-8")
    require("./vendor/leaflet-1.9.4/leaflet.js" in index, "Leaflet must be locally vendored")
    require("unpkg.com" not in index, "external Leaflet CDN dependency remains")
    return {"text_files_scanned": scanned, "blocked_hits": len(hits), "local_leaflet": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-root", default=".")
    args = parser.parse_args()
    site_root = Path(args.site_root).resolve()
    result = {
        "site_root": site_root.name,
        "climate": verify_climate(site_root),
        "season": verify_season(site_root),
        "hygiene": verify_hygiene(site_root),
        "status": "ok",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
