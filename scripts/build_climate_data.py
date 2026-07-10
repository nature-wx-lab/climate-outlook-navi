#!/usr/bin/env python3
"""Build public derived assets for 気候ものさしナビ.

The shared climate database is a read-only input. This builder writes only
derived, browser-oriented files below the selected output directory.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import shutil
import struct
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage


ELEMENT = "201"
WINDOWS = ("1991_2020", "1996_2025")
MONTHS = tuple(range(1, 14))
EXPECTED_MESH_COUNT = 387_717
VALUE_SCALE = 100
NODATA = -32_768
GENERATOR_VERSION = "1.0.0"
SCHEMA_VERSION = 1
RASTER_ZOOM = 7
RASTER_PADDING = 3

RAW_BREAKS = [-27, -24, -21, -18, -15, -12, -9, -6, -3, 0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30]
RAW_COLORS = [
    "#4b146c", "#5c28a7", "#4457c4", "#2a7bd1", "#2da9d2",
    "#42c7c5", "#64d69b", "#91df70", "#c4e85d", "#eced72",
    "#ffe36a", "#ffc44d", "#ff9f36", "#fb792c", "#ef5128",
    "#d92d25", "#b8172b", "#921338", "#6e123f", "#4c123f", "#2c102f",
]
DIFF_BREAKS = [-2.0, -1.5, -1.0, -0.6, -0.3, -0.1, 0.1, 0.3, 0.6, 1.0, 1.5, 2.0]
DIFF_COLORS = [
    "#2d2a78", "#4149a8", "#5978c7", "#79a4dc", "#a8c9e8", "#d9e8f2",
    "#f2f2ee", "#f7ddd0", "#efb8a1", "#e98b70", "#d95f4f", "#b9363e", "#7f1d35",
]


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def color_rgba(hex_color: str) -> tuple[int, int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4)) + (255,)


def mercator_world(lat: np.ndarray, lon: np.ndarray, zoom: int) -> tuple[np.ndarray, np.ndarray]:
    size = 256 * (2**zoom)
    x = (lon + 180.0) / 360.0 * size
    y = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2.0 * size
    return x, y


def world_to_lat(y: float, zoom: int) -> float:
    size = 256 * (2**zoom)
    n = math.pi - (2.0 * math.pi * y / size)
    return math.degrees(math.atan(math.sinh(n)))


def world_to_lon(x: float, zoom: int) -> float:
    size = 256 * (2**zoom)
    return x / size * 360.0 - 180.0


def load_master(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    codes: list[int] = []
    lats: list[float] = []
    lons: list[float] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        expected = [
            "mesh_code", "center_lat", "center_lon", "elev_mean_m",
            "elev_max_m", "elev_min_m", "slope_max_deg", "slope_mean_deg",
        ]
        if reader.fieldnames != expected:
            raise ValueError(f"Unexpected master schema: {reader.fieldnames}")
        for row in reader:
            codes.append(int(row["mesh_code"]))
            lats.append(float(row["center_lat"]))
            lons.append(float(row["center_lon"]))
    code_array = np.asarray(codes, dtype="<u4")
    if code_array.size != EXPECTED_MESH_COUNT:
        raise ValueError(f"Expected {EXPECTED_MESH_COUNT} meshes, got {code_array.size}")
    if np.unique(code_array).size != code_array.size:
        raise ValueError("mesh_code contains duplicates")
    return code_array, np.asarray(lats, dtype="<f4"), np.asarray(lons, dtype="<f4")


def load_window(path: Path, master_codes: np.ndarray) -> tuple[np.ndarray, dict[str, object]]:
    count = master_codes.size
    values = np.full((len(MONTHS), count), NODATA, dtype="<i2")
    month_counts = np.zeros(len(MONTHS), dtype=np.int64)
    min_values = np.full(len(MONTHS), np.inf)
    max_values = np.full(len(MONTHS), -np.inf)
    max_rounding_error = 0.0

    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != ["mesh_code", "month", "value"]:
            raise ValueError(f"Unexpected data schema in {path.name}: {reader.fieldnames}")
        for row in reader:
            month = int(row["month"])
            if month not in MONTHS:
                raise ValueError(f"Unexpected month {month} in {path.name}")
            month_idx = month - 1
            mesh_idx = int(month_counts[month_idx])
            if mesh_idx >= count:
                raise ValueError(f"Too many rows for month {month} in {path.name}")
            code = int(row["mesh_code"])
            if code != int(master_codes[mesh_idx]):
                raise ValueError(
                    f"Mesh order mismatch in {path.name}, month {month}, row {mesh_idx}: "
                    f"{code} != {int(master_codes[mesh_idx])}"
                )
            value = float(row["value"])
            if not math.isfinite(value):
                raise ValueError(f"Non-finite value in {path.name}")
            quantized = int(round(value * VALUE_SCALE))
            if not -32_767 <= quantized <= 32_767:
                raise ValueError(f"Value outside int16 range in {path.name}: {value}")
            values[month_idx, mesh_idx] = quantized
            month_counts[month_idx] += 1
            min_values[month_idx] = min(min_values[month_idx], value)
            max_values[month_idx] = max(max_values[month_idx], value)
            max_rounding_error = max(max_rounding_error, abs(quantized / VALUE_SCALE - value))

    if not np.all(month_counts == count):
        raise ValueError(f"Month counts mismatch in {path.name}: {month_counts.tolist()}")
    return values, {
        "row_count": int(month_counts.sum()),
        "month_counts": {str(month): int(month_counts[month - 1]) for month in MONTHS},
        "min": {str(month): round(float(min_values[month - 1]), 2) for month in MONTHS},
        "max": {str(month): round(float(max_values[month - 1]), 2) for month in MONTHS},
        "max_quantization_error_c": round(max_rounding_error, 8),
    }


def write_chunk(
    path: Path,
    codes: np.ndarray,
    lats: np.ndarray,
    lons: np.ndarray,
    values: np.ndarray,
) -> None:
    count = codes.size
    header = struct.pack(
        "<8sIIHHHh",
        b"NWCBCH1\0",
        SCHEMA_VERSION,
        count,
        len(WINDOWS),
        len(MONTHS),
        VALUE_SCALE,
        NODATA,
    )
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(np.asarray(codes, dtype="<u4").tobytes(order="C"))
        handle.write(np.asarray(lats, dtype="<f4").tobytes(order="C"))
        handle.write(np.asarray(lons, dtype="<f4").tobytes(order="C"))
        handle.write(np.asarray(values, dtype="<i2").tobytes(order="C"))


def render_assets(
    image_dir: Path,
    lats: np.ndarray,
    lons: np.ndarray,
    all_values: np.ndarray,
) -> tuple[dict[str, object], dict[str, object]]:
    world_x, world_y = mercator_world(lats.astype(np.float64), lons.astype(np.float64), RASTER_ZOOM)
    left = math.floor(float(world_x.min())) - RASTER_PADDING
    right = math.ceil(float(world_x.max())) + RASTER_PADDING + 1
    top = math.floor(float(world_y.min())) - RASTER_PADDING
    bottom = math.ceil(float(world_y.max())) + RASTER_PADDING + 1
    width = right - left
    height = bottom - top
    px = np.rint(world_x - left).astype(np.int32)
    py = np.rint(world_y - top).astype(np.int32)
    flat = py.astype(np.int64) * width + px
    unique_pixels = int(np.unique(flat).size)

    point_mask = np.zeros((height, width), dtype=bool)
    point_mask[py, px] = True
    distance, nearest = ndimage.distance_transform_edt(~point_mask, return_indices=True)
    render_mask = distance <= 1.35
    mask_y, mask_x = np.nonzero(render_mask)
    nearest_y = nearest[0, mask_y, mask_x]
    nearest_x = nearest[1, mask_y, mask_x]

    raw_palette = np.asarray([color_rgba(color) for color in RAW_COLORS], dtype=np.uint8)
    diff_palette = np.asarray([color_rgba(color) for color in DIFF_COLORS], dtype=np.uint8)
    files: dict[str, object] = {window: {} for window in WINDOWS}
    files["difference"] = {}

    for window_idx, window in enumerate(WINDOWS):
        for month_idx, month in enumerate(MONTHS):
            data = all_values[window_idx, month_idx].astype(np.float32) / VALUE_SCALE
            bins = np.digitize(data, RAW_BREAKS, right=False)
            point_colors = np.zeros((height, width, 4), dtype=np.uint8)
            point_colors[py, px] = raw_palette[bins]
            rgba = np.zeros((height, width, 4), dtype=np.uint8)
            rgba[mask_y, mask_x] = point_colors[nearest_y, nearest_x]
            filename = f"tmean_{window}_m{month:02d}.webp"
            output_path = image_dir / filename
            Image.fromarray(rgba, mode="RGBA").save(output_path, "WEBP", lossless=True, method=4)
            files[window][str(month)] = {
                "path": f"rasters/{filename}",
                "bytes": output_path.stat().st_size,
                "sha256": sha256_path(output_path),
            }

    for month_idx, month in enumerate(MONTHS):
        diff = (all_values[1, month_idx].astype(np.int32) - all_values[0, month_idx].astype(np.int32)) / VALUE_SCALE
        bins = np.digitize(diff, DIFF_BREAKS, right=False)
        point_colors = np.zeros((height, width, 4), dtype=np.uint8)
        point_colors[py, px] = diff_palette[bins]
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[mask_y, mask_x] = point_colors[nearest_y, nearest_x]
        filename = f"tmean_difference_m{month:02d}.webp"
        output_path = image_dir / filename
        Image.fromarray(rgba, mode="RGBA").save(output_path, "WEBP", lossless=True, method=4)
        files["difference"][str(month)] = {
            "path": f"rasters/{filename}",
            "bytes": output_path.stat().st_size,
            "sha256": sha256_path(output_path),
        }

    bounds = {
        "north": world_to_lat(top, RASTER_ZOOM),
        "south": world_to_lat(bottom, RASTER_ZOOM),
        "west": world_to_lon(left, RASTER_ZOOM),
        "east": world_to_lon(right, RASTER_ZOOM),
    }
    render_meta = {
        "projection": "EPSG:3857",
        "native_zoom": RASTER_ZOOM,
        "width": width,
        "height": height,
        "bounds": bounds,
        "source_mesh_count": int(lats.size),
        "unique_center_pixels": unique_pixels,
        "center_pixel_collisions": int(lats.size - unique_pixels),
        "display_note": "全国表示用の描画縮約。地点参照バイナリは全1kmメッシュを保持。",
    }
    return files, render_meta


def write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def build(args: argparse.Namespace) -> dict[str, object]:
    db_root = Path(args.db_root).expanduser().resolve()
    data_root = db_root / "02_基盤データ"
    output_root = Path(args.output).resolve()
    master_path = data_root / "mesh1km_master.csv.gz"
    source_paths = {window: data_root / f"mesh1km_{ELEMENT}_{window}.csv.gz" for window in WINDOWS}
    for path in (master_path, *source_paths.values()):
        if not path.is_file():
            raise FileNotFoundError(path)

    codes, lats, lons = load_master(master_path)
    all_values = np.empty((len(WINDOWS), len(MONTHS), codes.size), dtype="<i2")
    source_audit: dict[str, object] = {}
    for window_idx, window in enumerate(WINDOWS):
        window_values, audit = load_window(source_paths[window], codes)
        all_values[window_idx] = window_values
        source_audit[window] = {
            **audit,
            "logical_source_id": f"climate-mesh-1km/{ELEMENT}/{window}",
            "sha256": sha256_path(source_paths[window]),
        }

    sort_order = np.argsort(codes, kind="stable")
    input_was_sorted = bool(np.all(sort_order == np.arange(codes.size)))
    codes = codes[sort_order]
    lats = lats[sort_order]
    lons = lons[sort_order]
    all_values = all_values[:, :, sort_order]
    if np.any(codes[1:] <= codes[:-1]):
        raise ValueError("Sorted public mesh index is not strictly ascending")

    output_root.mkdir(parents=True, exist_ok=True)
    chunks_dir = output_root / "chunks"
    rasters_dir = output_root / "rasters"
    static_dir = output_root / "static"
    for directory in (chunks_dir, rasters_dir, static_dir):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)

    prefixes = np.asarray([int(str(int(code))[:4]) for code in codes], dtype=np.int32)
    chunks: dict[str, object] = {}
    for prefix in np.unique(prefixes):
        idx = np.flatnonzero(prefixes == prefix)
        filename = f"mesh_{prefix:04d}.bin"
        chunk_path = chunks_dir / filename
        write_chunk(chunk_path, codes[idx], lats[idx], lons[idx], all_values[:, :, idx])
        chunks[str(prefix)] = {
            "path": f"chunks/{filename}",
            "count": int(idx.size),
            "first_mesh_code": str(int(codes[idx[0]])).zfill(8),
            "last_mesh_code": str(int(codes[idx[-1]])).zfill(8),
            "bytes": chunk_path.stat().st_size,
            "sha256": sha256_path(chunk_path),
        }

    raster_files, render_meta = render_assets(rasters_dir, lats, lons, all_values)

    prefecture_meta = None
    if args.prefectures:
        prefectures_path = Path(args.prefectures).resolve()
        if not prefectures_path.is_file():
            raise FileNotFoundError(prefectures_path)
        destination = static_dir / "prefectures.geojson"
        shutil.copyfile(prefectures_path, destination)
        prefecture_meta = {
            "path": "static/prefectures.geojson",
            "bytes": destination.stat().st_size,
            "sha256": sha256_path(destination),
        }

    representative_indices = [0, codes.size // 4, codes.size // 2, (codes.size * 3) // 4, codes.size - 1]
    representatives = []
    for index in representative_indices:
        representatives.append({
            "mesh_code": str(int(codes[index])).zfill(8),
            "center_lat": round(float(lats[index]), 6),
            "center_lon": round(float(lons[index]), 6),
            "month_7": {
                WINDOWS[0]: round(float(all_values[0, 6, index]) / VALUE_SCALE, 2),
                WINDOWS[1]: round(float(all_values[1, 6, index]) / VALUE_SCALE, 2),
            },
            "annual": {
                WINDOWS[0]: round(float(all_values[0, 12, index]) / VALUE_SCALE, 2),
                WINDOWS[1]: round(float(all_values[1, 12, index]) / VALUE_SCALE, 2),
            },
        })

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "dataset_id": f"climate-baseline-201-{datetime.now().strftime('%Y%m%dT%H%M%S')}",
        "generated_at": iso_now(),
        "source": {
            "logical_database_id": "naturewxlab-climate-foundation-db",
            "database_version": args.db_version,
            "master_logical_id": "climate-mesh-1km/master",
            "master_sha256": sha256_path(master_path),
            "source_audit": source_audit,
        },
        "element": {
            "code": ELEMENT,
            "name": "平均気温",
            "unit": "℃",
            "value_scale": VALUE_SCALE,
            "nodata": NODATA,
        },
        "windows": [
            {"id": WINDOWS[0], "label": "1991–2020年平均（独自算出・気象庁平年期間）"},
            {"id": WINDOWS[1], "label": "1996–2025年平均（独自算出）"},
        ],
        "months": [{"id": month, "label": "年平均" if month == 13 else f"{month}月"} for month in MONTHS],
        "mesh_count": int(codes.size),
        "mesh_unique_count": int(np.unique(codes).size),
        "chunk_format": {
            "magic": "NWCBCH1",
            "version": SCHEMA_VERSION,
            "header_bytes": 24,
            "layout": "codes[u32], lat[f32], lon[f32], values[i16 window-major/month-major]",
            "window_order": list(WINDOWS),
            "month_order": list(MONTHS),
        },
        "chunks": chunks,
        "rasters": {
            "render": render_meta,
            "raw_legend": {"breaks": RAW_BREAKS, "colors": RAW_COLORS},
            "difference_legend": {"breaks": DIFF_BREAKS, "colors": DIFF_COLORS},
            "files": raster_files,
        },
        "static": {"prefectures": prefecture_meta},
        "validation": {
            "master_count_ok": codes.size == EXPECTED_MESH_COUNT,
            "master_duplicates": int(codes.size - np.unique(codes).size),
            "input_master_was_sorted": input_was_sorted,
            "public_chunk_codes_sorted": True,
            "window_row_counts_ok": all(source_audit[w]["row_count"] == EXPECTED_MESH_COUNT * 13 for w in WINDOWS),
            "mesh_order_matches_master": True,
            "missing_values": int(np.count_nonzero(all_values == NODATA)),
            "representative_values": representatives,
        },
        "wording": {
            "difference": "30年平均値の更新差（1996–2025 − 1991–2020）",
            "resolution": "気候平均は独自算出の全国陸域1kmメッシュ。季節予報は公式の地域単位。",
        },
    }
    write_json_atomic(output_root / "manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db-root",
        default=os.environ.get("NATUREWXLAB_DB_ROOT"),
        help="Root of the read-only shared climate database (or set NATUREWXLAB_DB_ROOT)",
    )
    parser.add_argument(
        "--output",
        default="data/climate",
        help="Derived public output directory",
    )
    parser.add_argument("--prefectures", help="Optional prefecture GeoJSON copied as a public derived asset")
    parser.add_argument("--db-version", default="v2.9", help="Logical source DB version for the manifest")
    args = parser.parse_args()
    if not args.db_root:
        parser.error("--db-root or NATUREWXLAB_DB_ROOT is required")
    return args


def main() -> None:
    manifest = build(parse_args())
    summary = {
        "dataset_id": manifest["dataset_id"],
        "mesh_count": manifest["mesh_count"],
        "chunk_count": len(manifest["chunks"]),
        "raster_count": sum(len(group) for group in manifest["rasters"]["files"].values()),
        "validation": manifest["validation"],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
