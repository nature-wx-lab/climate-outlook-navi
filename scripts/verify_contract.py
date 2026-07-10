#!/usr/bin/env python3
"""Verify generated 気候ものさしナビ data and local release hygiene."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path, PurePosixPath

import numpy as np


EXPECTED_MESH_COUNT = 387_717
EXPECTED_CHUNK_COUNT = 176
EXPECTED_RASTER_COUNT = 39
CLIMATE_ELEMENT_ORDER = ("201", "202", "203", "101", "401", "501", "503", "610")
CLIMATE_FORECAST_ELEMENTS = {
    "201": "temperature",
    "202": None,
    "203": None,
    "101": "precipitation",
    "401": "sunshine",
    "501": None,
    "503": "snowfall",
    "610": None,
}
CLIMATE_STORAGE = {
    "201": (b"NWCBCH1", 100, -32768, 1),
    "202": (b"NWCBCH2", 100, -32768, 1),
    "203": (b"NWCBCH2", 100, -32768, 1),
    "101": (b"NWCBCH2", 100, 0xFFFFFFFF, 3),
    "401": (b"NWCBCH2", 100, 0xFFFFFFFF, 3),
    "501": (b"NWCBCH2", 100, 65535, 2),
    "503": (b"NWCBCH2", 100, 0xFFFFFFFF, 3),
    "610": (b"NWCBCH2", 100, 65535, 2),
}
CLIMATE_FIXTURES = {
    "201": (("53394611", 7, (26.34, 26.67)), ("53394611", 13, (16.59, 16.75))),
    "202": (("53394611", 7, (30.03, 30.57)), ("53394611", 13, (20.45, 20.79))),
    "203": (("53394611", 7, (23.43, 23.68)), ("53394611", 13, (13.21, 13.28))),
    "101": (("53394611", 7, (154.81, 157.68)), ("53394611", 13, (1583.43, 1588.96))),
    "401": (("53394611", 7, (151.50, 162.90)), ("53394611", 13, (1926.68, 1970.26))),
    "501": (
        ("53394611", 7, (0.00, 0.00)),
        ("53394611", 13, (3.11, 2.97)),
        ("60407677", 2, (378.14, 395.98)),
        ("60407677", 13, (84.70, 88.31)),
    ),
    "503": (("53394611", 7, (0.00, 0.00)), ("53394611", 13, (8.28, 7.31))),
    "610": (("53394611", 1, (9.72, 9.90)), ("53394611", 7, (16.44, 17.19))),
}
SEASON_ELEMENTS = {
    "temperature": ("\u4f4e\u3044", "\u5e73\u5e74\u4e26", "\u9ad8\u3044"),
    "precipitation": ("\u5c11\u306a\u3044", "\u5e73\u5e74\u4e26", "\u591a\u3044"),
    "sunshine": ("\u5c11\u306a\u3044", "\u5e73\u5e74\u4e26", "\u591a\u3044"),
    "snowfall": ("\u5c11\u306a\u3044", "\u5e73\u5e74\u4e26", "\u591a\u3044"),
}
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


def verify_chunk(
    path: Path,
    entry: dict[str, object],
    manifest: dict[str, object],
) -> tuple[np.ndarray, int, int]:
    raw = path.read_bytes()
    magic = raw[:8].rstrip(b"\0")
    element = manifest.get("element")
    windows_meta = manifest.get("windows")
    months_meta = manifest.get("months")
    require(isinstance(element, dict), f"element metadata missing for {path}")
    require(isinstance(windows_meta, list), f"window metadata missing for {path}")
    require(isinstance(months_meta, list), f"month metadata missing for {path}")
    expected_windows = len(windows_meta)
    expected_months = len(months_meta)
    element_code = str(element.get("code", ""))
    require(element_code in CLIMATE_STORAGE, f"unknown climate element for {path}")
    expected_magic, expected_scale, expected_nodata, expected_dtype_code = CLIMATE_STORAGE[element_code]
    require(magic == expected_magic, f"chunk contract does not match element {element_code}: {path}")

    if magic == b"NWCBCH1":
        require(len(raw) >= 24, f"truncated v1 chunk {path}")
        _, version, count, windows, months, scale, header_nodata = struct.unpack("<8sIIHHHh", raw[:24])
        dtype = np.dtype("<i2")
        nodata = header_nodata
        header_bytes = 24
        require(element_code == "201", f"v1 chunk is reserved for element 201: {path}")
        require(
            (version, windows, months, scale, header_nodata) == (1, 2, 13, 100, -32768),
            f"v1 chunk header mismatch {path}",
        )
    elif magic == b"NWCBCH2":
        require(len(raw) >= 28, f"truncated v2 chunk {path}")
        (
            _, version, count, windows, months, scale, dtype_code, reserved, header_nodata_bits,
        ) = struct.unpack("<8sIIHHHBBI", raw[:28])
        dtype_by_code = {1: np.dtype("<i2"), 2: np.dtype("<u2"), 3: np.dtype("<u4")}
        require(dtype_code in dtype_by_code, f"unsupported v2 dtype code {dtype_code}: {path}")
        dtype = dtype_by_code[dtype_code]
        header_bytes = 28
        require(version == 2 and reserved == 0, f"v2 chunk version/reserved mismatch {path}")
        require(element_code != "201", f"element 201 must retain the v1 chunk contract: {path}")
        require(dtype_code == expected_dtype_code, f"v2 dtype mismatch for element {element_code}: {path}")
        require(scale == expected_scale == element.get("value_scale"), f"v2 value scale mismatch {path}")
        manifest_nodata = element.get("nodata")
        if dtype_code == 1:
            require(header_nodata_bits <= 0xFFFF, f"v2 int16 nodata bits overflow {path}")
            nodata = int(np.asarray([header_nodata_bits], dtype="<u2").view("<i2")[0])
        elif dtype_code == 2:
            require(header_nodata_bits <= 0xFFFF, f"v2 uint16 nodata bits overflow {path}")
            nodata = header_nodata_bits
        else:
            nodata = header_nodata_bits
        require(nodata == manifest_nodata, f"v2 nodata bit pattern mismatch {path}")
        require(manifest_nodata == expected_nodata, f"manifest nodata mismatch for element {element_code}")
    else:
        raise AssertionError(f"chunk magic mismatch {path}")

    require(scale == expected_scale, f"chunk scale mismatch for element {element_code}: {path}")
    require(nodata == expected_nodata, f"chunk nodata mismatch for element {element_code}: {path}")
    require(windows == expected_windows and months == expected_months, f"chunk dimensions mismatch {path}")
    require(count == entry.get("count"), f"chunk count mismatch {path}")
    require(path.stat().st_size == entry.get("bytes"), f"chunk byte mismatch {path}")
    require(sha256_path(path) == entry.get("sha256"), f"chunk checksum mismatch {path}")
    values_offset = header_bytes + count * 12
    value_count = windows * months * count
    expected_bytes = values_offset + value_count * dtype.itemsize
    require(len(raw) == expected_bytes, f"chunk layout mismatch {path}")
    codes = np.frombuffer(raw, dtype="<u4", count=count, offset=header_bytes)
    require(np.all(codes[1:] > codes[:-1]), f"chunk codes not ascending {path}")
    values = np.frombuffer(raw, dtype=dtype, count=value_count, offset=values_offset)
    require(not np.any(values == nodata), f"chunk contains nodata {path}")
    return codes.copy(), int(values.size), len(raw)


def verify_climate_legacy(site_root: Path) -> dict[str, int | bool]:
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
        codes, value_count, byte_count = verify_chunk(path, entry, manifest)
        require(str(int(codes[0])).zfill(8).startswith(prefix), f"chunk prefix mismatch {path}")
        all_codes.append(codes)
        total_values += value_count
        chunk_bytes += byte_count

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


def safe_climate_path(raw_path: object) -> PurePosixPath:
    require(isinstance(raw_path, str) and "\\" not in raw_path, "climate path must use forward slashes")
    path = PurePosixPath(raw_path)
    require(
        not path.is_absolute()
        and bool(path.parts)
        and all(part not in {"", ".", ".."} for part in path.parts),
        f"unsafe climate path: {raw_path!r}",
    )
    return path


def resolve_climate_path(base: PurePosixPath, raw_path: object) -> PurePosixPath:
    relative = safe_climate_path(raw_path)
    combined = base / relative
    require(all(part not in {"", ".", ".."} for part in combined.parts), "resolved climate path is unsafe")
    return combined


def verify_catalog_assets(root: Path, catalog: dict[str, object]) -> dict[str, dict[str, object]]:
    raw_assets = catalog.get("assets")
    require(isinstance(raw_assets, dict) and raw_assets, "climate catalog assets missing")
    assets: dict[str, dict[str, object]] = {}
    for raw_path, raw_entry in raw_assets.items():
        path = safe_climate_path(raw_path)
        require(path.as_posix() != "catalog.json", "catalog must not inventory itself")
        require(isinstance(raw_entry, dict), f"catalog asset metadata invalid: {path}")
        byte_count = raw_entry.get("bytes")
        checksum = raw_entry.get("sha256")
        require(isinstance(byte_count, int) and byte_count >= 0, f"catalog asset bytes invalid: {path}")
        require(
            isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum) is not None,
            f"catalog asset checksum invalid: {path}",
        )
        require(path.as_posix() not in assets, f"duplicate catalog asset path: {path}")
        file_path = root / Path(*path.parts)
        require(file_path.is_file(), f"catalog asset missing: {path}")
        require(file_path.stat().st_size == byte_count, f"catalog asset byte mismatch: {path}")
        require(sha256_path(file_path) == checksum, f"catalog asset checksum mismatch: {path}")
        assets[path.as_posix()] = raw_entry
    expected_files = {"catalog.json", *assets}
    actual_files = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    require(actual_files == expected_files, f"climate catalog file set mismatch: {sorted(actual_files ^ expected_files)}")
    return assets


def verify_asset_reference(
    root: Path,
    raw_entry: object,
    assets: dict[str, dict[str, object]],
    context: str,
    *,
    base: PurePosixPath = PurePosixPath(),
) -> Path:
    require(isinstance(raw_entry, dict), f"{context} asset entry invalid")
    relative = resolve_climate_path(base, raw_entry.get("path"))
    catalog_entry = assets.get(relative.as_posix())
    require(catalog_entry is not None, f"{context} is absent from catalog: {relative}")
    require(raw_entry.get("bytes") == catalog_entry.get("bytes"), f"{context} byte metadata mismatch")
    require(raw_entry.get("sha256") == catalog_entry.get("sha256"), f"{context} checksum metadata mismatch")
    return root / Path(*relative.parts)


def nested_asset_entries(value: object) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    if isinstance(value, dict):
        if {"path", "bytes", "sha256"} <= set(value):
            entries.append(value)
        else:
            for nested in value.values():
                entries.extend(nested_asset_entries(nested))
    elif isinstance(value, list):
        for nested in value:
            entries.extend(nested_asset_entries(nested))
    return entries


def verify_legend(value: object, context: str) -> None:
    require(isinstance(value, dict), f"{context} legend missing")
    breaks = value.get("breaks")
    colors = value.get("colors")
    labels = value.get("labels")
    require(
        isinstance(breaks, list)
        and breaks == sorted(breaks)
        and len(set(breaks)) == len(breaks)
        and all(isinstance(number, (int, float)) for number in breaks),
        f"{context} legend breaks invalid",
    )
    require(
        isinstance(colors, list)
        and len(colors) == len(breaks) + 1
        and all(isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) for color in colors),
        f"{context} legend colors invalid",
    )
    require(
        isinstance(labels, dict)
        and set(labels) == {"low", "middle", "high"}
        and all(isinstance(label, str) and label for label in labels.values()),
        f"{context} legend labels invalid",
    )


def verify_element_legends(code: str, rasters: dict[str, object]) -> None:
    legends = rasters.get("legends")
    require(isinstance(legends, dict) and set(legends) == {"monthly", "annual"}, f"element {code} legend profiles mismatch")
    monthly = legends["monthly"]
    require(isinstance(monthly, dict) and set(monthly) == {"absolute", "difference"}, f"element {code} monthly legends mismatch")
    verify_legend(monthly["absolute"], f"element {code} monthly absolute")
    verify_legend(monthly["difference"], f"element {code} monthly difference")
    annual = legends["annual"]
    if code == "610":
        require(annual is None, "element 610 must not publish an annual legend")
    else:
        require(isinstance(annual, dict) and set(annual) == {"absolute", "difference"}, f"element {code} annual legends mismatch")
        verify_legend(annual["absolute"], f"element {code} annual absolute")
        verify_legend(annual["difference"], f"element {code} annual difference")


def chunk_point_values(
    root: Path,
    manifest: dict[str, object],
    manifest_base: PurePosixPath,
    mesh_code: str,
    month: int,
) -> tuple[float, ...]:
    chunks = manifest["chunks"]
    entry = chunks.get(mesh_code[:4])
    require(isinstance(entry, dict), f"fixture chunk missing for mesh {mesh_code}")
    relative = resolve_climate_path(manifest_base, entry.get("path"))
    raw = (root / Path(*relative.parts)).read_bytes()
    magic = raw[:8].rstrip(b"\0")
    if magic == b"NWCBCH1":
        _, _, count, windows, months, scale, _ = struct.unpack("<8sIIHHHh", raw[:24])
        header_bytes = 24
        dtype = np.dtype("<i2")
    elif magic == b"NWCBCH2":
        _, _, count, windows, months, scale, dtype_code, _, _ = struct.unpack("<8sIIHHHBBI", raw[:28])
        dtype = {1: np.dtype("<i2"), 2: np.dtype("<u2"), 3: np.dtype("<u4")}[dtype_code]
        header_bytes = 28
    else:
        raise AssertionError(f"fixture chunk magic mismatch: {relative}")
    month_ids = [int(item["id"]) for item in manifest["months"]]
    require(month in month_ids, f"fixture month {month} missing for element {manifest['element']['code']}")
    month_index = month_ids.index(month)
    codes = np.frombuffer(raw, dtype="<u4", count=count, offset=header_bytes)
    code_value = int(mesh_code)
    mesh_index = int(np.searchsorted(codes, code_value))
    require(mesh_index < count and int(codes[mesh_index]) == code_value, f"fixture mesh missing: {mesh_code}")
    values_offset = header_bytes + count * 12
    values = np.frombuffer(raw, dtype=dtype, count=windows * months * count, offset=values_offset)
    values = values.reshape(windows, months, count)
    return tuple(float(values[window_index, month_index, mesh_index]) / scale for window_index in range(windows))


def verify_element_manifest(
    root: Path,
    manifest: dict[str, object],
    assets: dict[str, dict[str, object]],
    manifest_base: PurePosixPath,
) -> dict[str, int | str | bool]:
    element = manifest.get("element")
    require(isinstance(element, dict), "climate element metadata missing")
    code = str(element.get("code", ""))
    require(code in CLIMATE_ELEMENT_ORDER, f"unknown climate element: {code}")
    expected_schema = 1 if code == "201" else 2
    require(manifest.get("schema_version") == expected_schema, f"element {code} manifest schema mismatch")
    require(manifest.get("mesh_count") == EXPECTED_MESH_COUNT, f"element {code} mesh count mismatch")
    require(manifest.get("mesh_unique_count") == EXPECTED_MESH_COUNT, f"element {code} unique mesh count mismatch")
    require(element.get("value_scale") == CLIMATE_STORAGE[code][1], f"element {code} scale mismatch")
    require(element.get("nodata") == CLIMATE_STORAGE[code][2], f"element {code} nodata mismatch")
    require(element.get("forecast_element") == CLIMATE_FORECAST_ELEMENTS[code], f"element {code} forecast mapping mismatch")
    annual = element.get("annual")
    require(isinstance(annual, dict), f"element {code} annual metadata missing")
    require(annual.get("available") is (code != "610"), f"element {code} annual metadata mismatch")
    require(annual.get("derived_from_monthly_grid") is False, f"element {code} annual derivation flag mismatch")

    windows = manifest.get("windows")
    months = manifest.get("months")
    require(isinstance(windows, list) and len(windows) == 2, f"element {code} window metadata mismatch")
    require([item.get("id") for item in windows] == ["1991_2020", "1996_2025"], f"element {code} window order mismatch")
    require(isinstance(months, list), f"element {code} month metadata missing")
    month_ids = [int(item["id"]) for item in months]
    expected_month_ids = list(range(1, 13 if code == "610" else 14))
    require(month_ids == expected_month_ids, f"element {code} month order mismatch")
    require((13 not in month_ids) is (code == "610"), f"element {code} annual availability mismatch")
    expected_value_count = EXPECTED_MESH_COUNT * 2 * len(month_ids)
    require(manifest.get("value_count") == expected_value_count, f"element {code} manifest value count mismatch")

    chunk_format = manifest.get("chunk_format")
    require(isinstance(chunk_format, dict), f"element {code} chunk format missing")
    expected_dtype = {1: "int16", 2: "uint16", 3: "uint32"}[CLIMATE_STORAGE[code][3]]
    require(chunk_format.get("magic") == ("NWCBCH1" if code == "201" else "NWCBCH2"), f"element {code} chunk magic metadata mismatch")
    require(chunk_format.get("version") == expected_schema, f"element {code} chunk version metadata mismatch")
    require(chunk_format.get("header_bytes") == (24 if code == "201" else 28), f"element {code} header size metadata mismatch")
    require(chunk_format.get("dtype") == expected_dtype, f"element {code} dtype metadata mismatch")
    require(chunk_format.get("dtype_code") == CLIMATE_STORAGE[code][3], f"element {code} dtype code metadata mismatch")
    require(chunk_format.get("value_scale") == 100, f"element {code} chunk scale metadata mismatch")
    require(chunk_format.get("nodata") == CLIMATE_STORAGE[code][2], f"element {code} chunk nodata metadata mismatch")
    require(chunk_format.get("window_order") == ["1991_2020", "1996_2025"], f"element {code} chunk window order mismatch")
    require(chunk_format.get("month_order") == month_ids, f"element {code} chunk month order mismatch")

    validation = manifest.get("validation")
    require(isinstance(validation, dict), f"element {code} validation missing")
    require(validation.get("missing_values") == 0, f"element {code} reports missing values")
    require(validation.get("window_row_counts_ok") is True, f"element {code} source row count failed")
    require(validation.get("mesh_order_matches_master") is True, f"element {code} source mesh order failed")
    representative_values = validation.get("representative_values")
    require(isinstance(representative_values, list) and representative_values, f"element {code} fixtures missing")

    source = manifest.get("source")
    require(isinstance(source, dict), f"element {code} source metadata missing")
    source_audit = source.get("source_audit")
    require(isinstance(source_audit, dict) and set(source_audit) == {"1991_2020", "1996_2025"}, f"element {code} source audit mismatch")
    expected_rows = EXPECTED_MESH_COUNT * len(month_ids)
    for window, audit in source_audit.items():
        require(isinstance(audit, dict), f"element {code}/{window} source audit invalid")
        require(audit.get("row_count") == expected_rows, f"element {code}/{window} row count mismatch")
        require(audit.get("max_quantization_error") == 0, f"element {code}/{window} quantization is lossy")
        counts = audit.get("month_counts")
        require(
            isinstance(counts, dict)
            and set(counts) == {str(month) for month in month_ids}
            and all(count == EXPECTED_MESH_COUNT for count in counts.values()),
            f"element {code}/{window} month counts mismatch",
        )

    chunks = manifest.get("chunks")
    require(isinstance(chunks, dict) and len(chunks) == EXPECTED_CHUNK_COUNT, f"element {code} chunk count mismatch")
    all_codes: list[np.ndarray] = []
    chunk_bytes = 0
    value_count = 0
    for prefix, entry in sorted(chunks.items()):
        path = verify_asset_reference(
            root, entry, assets, f"element {code} chunk {prefix}", base=manifest_base
        )
        require(path.suffix == ".bin", f"element {code} chunk suffix mismatch")
        codes, values, byte_count = verify_chunk(path, entry, manifest)
        require(str(int(codes[0])).zfill(8).startswith(prefix), f"element {code} chunk prefix mismatch")
        all_codes.append(codes)
        value_count += values
        chunk_bytes += byte_count
    codes = np.concatenate(all_codes)
    require(codes.size == EXPECTED_MESH_COUNT, f"element {code} combined mesh count mismatch")
    require(np.all(codes[1:] > codes[:-1]), f"element {code} combined mesh index is not ascending")
    require(value_count == expected_value_count, f"element {code} value count mismatch")
    mesh_index_sha256 = hashlib.sha256(codes.astype("<u4", copy=False).tobytes()).hexdigest()

    rasters = manifest.get("rasters")
    require(isinstance(rasters, dict), f"element {code} raster metadata missing")
    verify_element_legends(code, rasters)
    raster_entries = nested_asset_entries(rasters.get("files"))
    require(len(raster_entries) == len(month_ids) * 3, f"element {code} raster count mismatch")
    raster_bytes = 0
    for index, entry in enumerate(raster_entries):
        path = verify_asset_reference(
            root, entry, assets, f"element {code} raster {index}", base=manifest_base
        )
        require(path.suffix == ".webp", f"element {code} raster suffix mismatch")
        raster_bytes += path.stat().st_size

    for static_name, entry in (manifest.get("static") or {}).items():
        if entry is not None:
            verify_asset_reference(
                root, entry, assets, f"element {code} static {static_name}", base=manifest_base
            )

    for mesh_code, month, expected in CLIMATE_FIXTURES[code]:
        actual = chunk_point_values(root, manifest, manifest_base, mesh_code, month)
        require(actual == expected, f"element {code} fixture mismatch at {mesh_code}/m{month}: {actual} != {expected}")
    for fixture in representative_values:
        require(isinstance(fixture, dict), f"element {code} fixture entry invalid")
        mesh_code = fixture.get("mesh_code")
        fixture_values = fixture.get("values")
        require(
            isinstance(mesh_code, str)
            and re.fullmatch(r"[0-9]{8}", mesh_code) is not None
            and isinstance(fixture_values, dict)
            and fixture_values,
            f"element {code} fixture metadata invalid",
        )
        for month_text, window_values in fixture_values.items():
            require(
                isinstance(month_text, str)
                and month_text.isdigit()
                and isinstance(window_values, dict)
                and set(window_values) == {"1991_2020", "1996_2025"},
                f"element {code} fixture values invalid at {mesh_code}",
            )
            expected = tuple(float(window_values[window]) for window in ("1991_2020", "1996_2025"))
            actual = chunk_point_values(root, manifest, manifest_base, mesh_code, int(month_text))
            require(actual == expected, f"element {code} declared fixture mismatch at {mesh_code}/m{month_text}")

    return {
        "mesh_count": int(codes.size),
        "value_count": value_count,
        "chunk_count": len(chunks),
        "chunk_bytes": chunk_bytes,
        "raster_count": len(raster_entries),
        "raster_bytes": raster_bytes,
        "mesh_index_sha256": mesh_index_sha256,
        "fixtures_ok": True,
    }


def verify_climate_catalog(site_root: Path) -> dict[str, object]:
    root = site_root / "data/climate"
    catalog = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    require(catalog.get("schema_version") == 1, "climate catalog schema mismatch")
    require(catalog.get("mesh_count") == EXPECTED_MESH_COUNT, "climate catalog mesh count mismatch")
    require(isinstance(catalog.get("dataset_id"), str) and catalog["dataset_id"], "climate catalog dataset id missing")
    windows = catalog.get("windows")
    require(isinstance(windows, list) and [item.get("id") for item in windows] == ["1991_2020", "1996_2025"], "climate catalog windows mismatch")
    assets = verify_catalog_assets(root, catalog)
    elements = catalog.get("elements")
    require(isinstance(elements, list), "climate catalog elements missing")
    require([str(entry.get("code")) for entry in elements] == list(CLIMATE_ELEMENT_ORDER), "climate catalog element order mismatch")
    catalog_validation = catalog.get("validation")
    require(isinstance(catalog_validation, dict), "climate catalog validation missing")
    require(catalog_validation.get("element_count") == len(CLIMATE_ELEMENT_ORDER), "climate catalog element count validation mismatch")
    require(catalog_validation.get("mesh_count_ok") is True, "climate catalog mesh validation failed")
    require(catalog_validation.get("temperature_order_violations") == 0, "climate temperature order validation failed")
    require(catalog_validation.get("asset_count") == len(assets), "climate catalog asset count mismatch")
    require(catalog_validation.get("max_input_quantization_error") == 0, "climate catalog quantization error mismatch")
    require(catalog_validation.get("all_inputs_quantized_at_0_01") is True, "climate catalog quantization validation failed")
    catalog_static = catalog.get("static")
    require(isinstance(catalog_static, dict) and set(catalog_static) == {"prefectures"}, "climate catalog static metadata mismatch")
    verify_asset_reference(root, catalog_static["prefectures"], assets, "catalog prefecture boundary")

    element_results: dict[str, dict[str, int | str | bool]] = {}
    mesh_index_sha256 = None
    for catalog_entry in elements:
        require(isinstance(catalog_entry, dict), "climate catalog element entry invalid")
        code = str(catalog_entry["code"])
        require(catalog_entry.get("forecast_element") == CLIMATE_FORECAST_ELEMENTS[code], f"element {code} forecast mapping mismatch")
        manifest_entry = catalog_entry.get("manifest")
        manifest_path = verify_asset_reference(root, manifest_entry, assets, f"element {code} manifest")
        require(manifest_path.suffix == ".json", f"element {code} manifest suffix mismatch")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        require(str(manifest.get("element", {}).get("code")) == code, f"element {code} manifest identity mismatch")
        catalog_element_metadata = {key: value for key, value in catalog_entry.items() if key != "manifest"}
        require(manifest.get("element") == catalog_element_metadata, f"element {code} catalog/manifest metadata mismatch")
        manifest_relative = safe_climate_path(manifest_entry["path"])
        result = verify_element_manifest(root, manifest, assets, manifest_relative.parent)
        if mesh_index_sha256 is None:
            mesh_index_sha256 = result["mesh_index_sha256"]
        else:
            require(result["mesh_index_sha256"] == mesh_index_sha256, f"element {code} mesh index differs")
        element_results[code] = result

    total_value_count = sum(int(result["value_count"]) for result in element_results.values())
    require(total_value_count == 79_869_702, "climate catalog total value count mismatch")
    require(catalog_validation.get("total_value_count") == total_value_count, "climate catalog value validation mismatch")
    require(sum(int(result["chunk_count"]) for result in element_results.values()) == 1_408, "climate catalog total chunk count mismatch")
    require(sum(int(result["raster_count"]) for result in element_results.values()) == 309, "climate catalog total raster count mismatch")

    return {
        "schema_version": 1,
        "dataset_id": catalog["dataset_id"],
        "mesh_count": EXPECTED_MESH_COUNT,
        "element_count": len(element_results),
        "value_count": total_value_count,
        "chunk_count": sum(int(result["chunk_count"]) for result in element_results.values()),
        "chunk_bytes": sum(int(result["chunk_bytes"]) for result in element_results.values()),
        "raster_count": sum(int(result["raster_count"]) for result in element_results.values()),
        "raster_bytes": sum(int(result["raster_bytes"]) for result in element_results.values()),
        "catalog_asset_count": len(assets),
        "elements": element_results,
        "checksums_ok": True,
        "fixtures_ok": True,
    }


def verify_climate(site_root: Path) -> dict[str, object]:
    if (site_root / "data/climate/catalog.json").is_file():
        return verify_climate_catalog(site_root)
    return verify_climate_legacy(site_root)


def verify_probability_regions(
    regions: object,
    context: str,
    boundary_codes: set[str],
    *,
    require_full_coverage: bool,
) -> int:
    require(isinstance(regions, dict), f"{context} regions must be an object")
    if not regions:
        return 0
    region_codes = set(regions)
    require(region_codes <= boundary_codes, f"{context} contains unknown class15 codes")
    if require_full_coverage:
        require(region_codes == boundary_codes, f"{context} region coverage mismatch")
    for code, value in regions.items():
        require(isinstance(code, str) and code, f"{context} region code is invalid")
        require(isinstance(value, dict), f"{context}.{code} must be an object")
        probabilities = value.get("probabilities")
        require(
            isinstance(probabilities, list)
            and len(probabilities) == 3
            and all(isinstance(number, int) and 0 <= number <= 100 for number in probabilities)
            and sum(probabilities) == 100,
            f"{context}.{code} probability mismatch",
        )
    return len(regions)


def verify_season_v1(latest: dict[str, object], boundary_codes: set[str]) -> tuple[int, int]:
    probability_triplets = 0
    available_terms = 0
    products = latest.get("products")
    require(isinstance(products, dict), "season v1 products missing")
    for product_name in ("P1M", "P3M"):
        product = products.get(product_name)
        require(isinstance(product, dict), f"season v1 {product_name} missing")
        terms = product.get("terms")
        require(isinstance(terms, list) and len(terms) == 4, f"{product_name} term count mismatch")
        for term_index, term in enumerate(terms):
            require(isinstance(term, dict), f"{product_name} term {term_index} invalid")
            probability_triplets += verify_probability_regions(
                term.get("regions"),
                f"{product_name}.temperature[{term_index}]",
                boundary_codes,
                require_full_coverage=True,
            )
            available_terms += 1
    return probability_triplets, available_terms


def verify_season_v2(
    manifest: dict[str, object], latest: dict[str, object], boundary_codes: set[str]
) -> tuple[int, int]:
    descriptors = latest.get("elements")
    require(isinstance(descriptors, dict), "season v2 element descriptors missing")
    require(set(descriptors) == set(SEASON_ELEMENTS), "season v2 element descriptor set mismatch")
    for element, classes in SEASON_ELEMENTS.items():
        descriptor = descriptors[element]
        require(isinstance(descriptor, dict), f"season descriptor {element} invalid")
        require(tuple(descriptor.get("classes", ())) == classes, f"season classes mismatch for {element}")

    validation = manifest.get("validation")
    require(isinstance(validation, dict), "season v2 validation missing")
    supported_terms = validation.get("supported_terms")
    require(isinstance(supported_terms, dict), "season v2 supported_terms missing")
    products = latest.get("products")
    manifest_products = manifest.get("products")
    require(isinstance(products, dict) and isinstance(manifest_products, dict), "season v2 products missing")

    probability_triplets = 0
    available_terms = 0
    for product_name in ("P1M", "P3M"):
        product = products.get(product_name)
        product_summary = manifest_products.get(product_name)
        support = supported_terms.get(product_name)
        require(
            isinstance(product, dict) and isinstance(product_summary, dict) and isinstance(support, dict),
            f"season v2 {product_name} contract missing",
        )
        elements = product.get("elements")
        element_summaries = product_summary.get("elements")
        require(
            isinstance(elements, dict)
            and isinstance(element_summaries, dict)
            and set(elements) == set(SEASON_ELEMENTS)
            and set(element_summaries) == set(SEASON_ELEMENTS),
            f"season v2 {product_name} element set mismatch",
        )
        for element in SEASON_ELEMENTS:
            expected_indices = support.get(element)
            require(
                isinstance(expected_indices, list)
                and expected_indices == sorted(set(expected_indices))
                and all(isinstance(index, int) and 0 <= index <= 3 for index in expected_indices),
                f"season v2 {product_name}.{element} support indices invalid",
            )
            payload = elements[element]
            summary = element_summaries[element]
            require(
                isinstance(payload, dict) and isinstance(summary, dict),
                f"season v2 {product_name}.{element} metadata invalid",
            )
            supported = bool(expected_indices)
            require(payload.get("supported") is supported, f"{product_name}.{element} supported mismatch")
            require(summary.get("supported") is supported, f"{product_name}.{element} summary support mismatch")
            terms = payload.get("terms")
            require(isinstance(terms, list), f"{product_name}.{element} terms missing")
            require(len(terms) == len(expected_indices), f"{product_name}.{element} term count mismatch")
            require(summary.get("term_count") == len(terms), f"{product_name}.{element} summary term count mismatch")

            element_available_terms = 0
            for expected_index, term in zip(expected_indices, terms, strict=True):
                require(isinstance(term, dict), f"{product_name}.{element}[{expected_index}] invalid")
                require(term.get("id") == str(expected_index), f"{product_name}.{element} term id mismatch")
                region_count = verify_probability_regions(
                    term.get("regions"),
                    f"{product_name}.{element}[{expected_index}]",
                    boundary_codes,
                    require_full_coverage=element != "snowfall",
                )
                if region_count:
                    require(
                        term.get("resolved_class15_count") == region_count,
                        f"{product_name}.{element}[{expected_index}] resolved count mismatch",
                    )
                    element_available_terms += 1
                    probability_triplets += region_count
                else:
                    require(
                        element == "snowfall" and payload.get("status") == "unavailable",
                        f"only seasonally unavailable snowfall terms may have empty regions: "
                        f"{product_name}.{element}[{expected_index}]",
                    )
                    require(
                        term.get("resolved_class15_count") == 0,
                        f"{product_name}.{element}[{expected_index}] empty resolved count mismatch",
                    )
            available_terms += element_available_terms
            require(
                summary.get("available_term_count") == element_available_terms,
                f"{product_name}.{element} available term summary mismatch",
            )
            require(summary.get("status") == payload.get("status"), f"{product_name}.{element} status mismatch")
            require(
                summary.get("unavailable_reason") == payload.get("unavailable_reason"),
                f"{product_name}.{element} unavailable reason mismatch",
            )
            if not supported:
                require(
                    payload.get("status") == "unavailable"
                    and payload.get("unavailable_reason") == "not_supported_by_product"
                    and not terms,
                    f"{product_name}.{element} unsupported contract mismatch",
                )
            elif element_available_terms == len(expected_indices):
                require(
                    payload.get("status") == "available" and payload.get("unavailable_reason") is None,
                    f"{product_name}.{element} available contract mismatch",
                )
            else:
                require(
                    element == "snowfall"
                    and element_available_terms == 0
                    and payload.get("status") == "unavailable"
                    and payload.get("unavailable_reason") == "seasonal_not_issued",
                    f"{product_name}.{element} unavailable contract mismatch",
                )
    return probability_triplets, available_terms


def verify_season(site_root: Path) -> dict[str, int | bool | str]:
    root = site_root / "data" / "season"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    latest_path = root / manifest["files"]["latest"]["path"]
    regions_path = root / manifest["files"]["regions"]["path"]
    for label, path in (("latest", latest_path), ("regions", regions_path)):
        entry = manifest["files"][label]
        require(path.is_file(), f"season {label} missing")
        require(path.stat().st_size == entry["bytes"], f"season {label} byte count mismatch")
        require(sha256_path(path) == entry["sha256"], f"season {label} checksum mismatch")
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
    boundary_codes = {feature["properties"]["code"] for feature in regions["features"]}
    require(len(boundary_codes) == 376, "season unique code mismatch")
    schema_version = latest.get("schema_version")
    require(schema_version == manifest.get("schema_version"), "season schema version mismatch")
    if schema_version == 1:
        probability_triplets, available_terms = verify_season_v1(latest, boundary_codes)
    elif schema_version == 2:
        probability_triplets, available_terms = verify_season_v2(manifest, latest, boundary_codes)
    else:
        raise AssertionError(f"unsupported season schema version: {schema_version}")
    return {
        "schema_version": int(schema_version),
        "dataset_id": manifest["dataset_id"],
        "feature_count": len(regions["features"]),
        "unique_code_count": len(boundary_codes),
        "probability_triplets": probability_triplets,
        "available_terms": available_terms,
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
