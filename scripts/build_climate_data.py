#!/usr/bin/env python3
"""Build the complete public climate catalog for 気候ものさしナビ.

The shared climate database is a read-only input.  The builder validates every
source row, creates browser-oriented derivatives in a sibling staging tree and
promotes the complete tree atomically.  No source CSV or local absolute path is
written to the public output.
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
from typing import Any

import numpy as np
from PIL import Image
from scipy import ndimage


WINDOWS = ("1991_2020", "1996_2025")
WINDOW_LABELS = {
    "1991_2020": "1991–2020年平均（独自算出・気象庁平年期間）",
    "1996_2025": "1996–2025年平均（独自算出）",
}
EXPECTED_MESH_COUNT = 387_717
EXPECTED_PREFIX_COUNT = 176
VALUE_SCALE = 100
GENERATOR_VERSION = "2.0.1"
CATALOG_SCHEMA_VERSION = 1
RASTER_ZOOM = 7
RASTER_PADDING = 3
DIFFERENCE_WORDING = "30年平均値の更新差（1996–2025 − 1991–2020）"

TEMP_COLORS = [
    "#4b146c", "#5c28a7", "#4457c4", "#2a7bd1", "#2da9d2",
    "#42c7c5", "#64d69b", "#91df70", "#c4e85d", "#eced72",
    "#ffe36a", "#ffc44d", "#ff9f36", "#fb792c", "#ef5128",
    "#d92d25", "#b8172b", "#921338", "#6e123f", "#4c123f", "#2c102f",
]
DIFF_COLORS = [
    "#2d2a78", "#4149a8", "#5978c7", "#79a4dc", "#a8c9e8", "#d9e8f2",
    "#f2f2ee", "#f7ddd0", "#efb8a1", "#e98b70", "#d95f4f", "#b9363e", "#7f1d35",
]
WATER_COLORS = [
    "#ffffe5", "#f7fcb9", "#d9f0a3", "#addd8e", "#78c679", "#41ab5d",
    "#238443", "#41b6c4", "#2c7fb8", "#253494", "#225ea8", "#0c2c84", "#081d58",
]
SUN_COLORS = [
    "#f7f7f7", "#d9e2e8", "#b8d4dc", "#8fc6c8", "#70b59b", "#91bd72",
    "#c8cf65", "#f0d75e", "#f8c24e", "#f6a13a", "#ef7d32", "#d95b2b", "#a6362a",
]
SNOW_COLORS = [
    "#f7fbff", "#eaf4fb", "#d8ebf7", "#bddff1", "#91c9e8", "#65addd",
    "#438ccc", "#356bb5", "#3d4b9a", "#513480", "#64236d", "#711653", "#650b35",
]
SOLAR_COLORS = [
    "#352a87", "#3659a8", "#2d85b8", "#2eabb8", "#52c6a8", "#86d68f",
    "#c1df76", "#eee06f", "#ffd15c", "#fdae46", "#f47a32", "#d94827", "#9f2525",
]


def legend(title: str, breaks: list[float], colors: list[str], low: str, middle: str, high: str) -> dict[str, Any]:
    if len(colors) != len(breaks) + 1:
        raise ValueError(f"Legend {title!r} has {len(breaks)} breaks and {len(colors)} colors")
    return {
        "title": title,
        "breaks": breaks,
        "colors": colors,
        "labels": {"low": low, "middle": middle, "high": high},
    }


def temperature_legends(raw_breaks: list[float], raw_low: str, raw_mid: str, raw_high: str,
                        diff_breaks: list[float], diff_low: str, diff_high: str) -> dict[str, Any]:
    raw = legend("気温", raw_breaks, TEMP_COLORS, raw_low, raw_mid, raw_high)
    diff = legend("30年平均値の更新差", diff_breaks, DIFF_COLORS, diff_low, "0℃", diff_high)
    return {"monthly": {"absolute": raw, "difference": diff}, "annual": {"absolute": raw, "difference": diff}}


ELEMENTS: dict[str, dict[str, Any]] = {
    "201": {
        "name": "平均気温", "short_name": "平均気温", "definition": "日平均気温の30年平均",
        "unit": "℃", "months": tuple(range(1, 14)), "annual_label": "年平均",
        "annual_definition": "12か月を通した年平均", "forecast_element": "temperature",
        "dtype": "int16", "dtype_code": 1, "nodata": -32768, "schema": 1, "stem": "tmean",
        "quality_note": "観測値等をもとにNature Wx Labが独自算出・独自内挿した1km面です。",
        "legends": temperature_legends(
            list(range(-27, 33, 3)), "−27℃以下", "0℃", "30℃超",
            [-2.0, -1.5, -1.0, -0.6, -0.3, -0.1, 0.1, 0.3, 0.6, 1.0, 1.5, 2.0],
            "−2.0℃以下", "+2.0℃超",
        ),
    },
    "202": {
        "name": "日最高気温の平均", "short_name": "最高気温の平均",
        "definition": "日最高気温の月平均。月間の最高気温ではありません。",
        "unit": "℃", "months": tuple(range(1, 14)), "annual_label": "年平均",
        "annual_definition": "日最高気温の年平均", "forecast_element": None,
        "dtype": "int16", "dtype_code": 1, "nodata": -32768, "schema": 2, "stem": "tmaxmean",
        "quality_note": "季節予報の平均気温とは同一要素ではないため、直接重ねません。",
        "legends": temperature_legends(
            list(range(-27, 33, 3)), "−27℃以下", "0℃", "30℃超",
            [-3.0, -2.0, -1.2, -0.7, -0.3, -0.1, 0.1, 0.3, 0.7, 1.2, 2.0, 3.0],
            "−3.0℃以下", "+3.0℃超",
        ),
    },
    "203": {
        "name": "日最低気温の平均", "short_name": "最低気温の平均",
        "definition": "日最低気温の月平均。月間の最低気温ではありません。",
        "unit": "℃", "months": tuple(range(1, 14)), "annual_label": "年平均",
        "annual_definition": "日最低気温の年平均", "forecast_element": None,
        "dtype": "int16", "dtype_code": 1, "nodata": -32768, "schema": 2, "stem": "tminmean",
        "quality_note": "監査時LOOCVは1.06℃。他の気温要素より不確実性に注意してください。季節予報の平均気温とは直接対応しません。",
        "legends": temperature_legends(
            list(range(-30, 30, 3)), "−30℃以下", "0℃", "27℃超",
            [-6.0, -4.0, -2.5, -1.5, -0.7, -0.2, 0.2, 0.7, 1.5, 2.5, 4.0, 6.0],
            "−6.0℃以下", "+6.0℃超",
        ),
    },
    "101": {
        "name": "降水量合計", "short_name": "降水量", "definition": "月または年の降水量合計",
        "unit": "mm", "months": tuple(range(1, 14)), "annual_label": "年降水量・年合計",
        "annual_definition": "12か月の降水量の年合計", "forecast_element": "precipitation",
        "dtype": "uint32", "dtype_code": 3, "nodata": 4294967295, "schema": 2, "stem": "precip",
        "quality_note": "季節予報は地域平均の3階級確率であり、この1km値へ加算しません。",
        "legends": {
            "monthly": {
                "absolute": legend("月降水量", [0.01, 25, 50, 75, 100, 150, 200, 250, 300, 400, 500, 700], WATER_COLORS, "0mm", "200mm", "700mm超"),
                "difference": legend("30年平均値の更新差", [-400, -300, -200, -100, -50, -20, 20, 50, 100, 200, 300, 400], DIFF_COLORS, "−400mm以下", "0mm", "+400mm超"),
            },
            "annual": {
                "absolute": legend("年降水量", [250, 500, 750, 1000, 1250, 1500, 1750, 2000, 2500, 3000, 3500, 4000], WATER_COLORS, "250mm以下", "1,750mm", "4,000mm超"),
                "difference": legend("30年平均値の更新差", [-400, -300, -200, -100, -50, -20, 20, 50, 100, 200, 300, 400], DIFF_COLORS, "−400mm以下", "0mm", "+400mm超"),
            },
        },
    },
    "401": {
        "name": "日照時間", "short_name": "日照時間", "definition": "月または年の日照時間合計",
        "unit": "h", "months": tuple(range(1, 14)), "annual_label": "年間日照時間・年合計",
        "annual_definition": "12か月の日照時間の年合計", "forecast_element": "sunshine",
        "dtype": "uint32", "dtype_code": 3, "nodata": 4294967295, "schema": 2, "stem": "sunshine",
        "quality_note": "3か月予報には日照時間の直接対応商品がないため、1か月予報だけを重ねます。",
        "legends": {
            "monthly": {
                "absolute": legend("月間日照時間", [25, 50, 75, 100, 125, 150, 175, 200, 225, 250, 275, 300], SUN_COLORS, "25h以下", "175h", "300h超"),
                "difference": legend("30年平均値の更新差", [-100, -75, -50, -30, -15, -5, 5, 15, 30, 50, 75, 100], DIFF_COLORS, "−100h以下", "0h", "+100h超"),
            },
            "annual": {
                "absolute": legend("年間日照時間", [500, 750, 1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750, 3000, 3250], SUN_COLORS, "500h以下", "2,000h", "3,250h超"),
                "difference": legend("30年平均値の更新差", [-300, -200, -100, -50, -25, -10, 10, 25, 50, 100, 200, 300], DIFF_COLORS, "−300h以下", "0h", "+300h超"),
            },
        },
    },
    "501": {
        "name": "最深積雪", "short_name": "最深積雪", "definition": "月別の月平均最深積雪",
        "unit": "cm", "months": tuple(range(1, 14)), "annual_label": "年値（最大月平均）",
        "annual_definition": "観測地点ごとに12個の月別平均最深積雪の最大を求め、その地点値を月別面とは別に1km内挿した値。寒候年ごとの年最深積雪の平均でも、1km月別面12枚の最大でもありません。",
        "forecast_element": None, "dtype": "uint16", "dtype_code": 2, "nodata": 65535,
        "schema": 2, "stem": "snowdepth", "quality_note": "雪の観測地点が疎な地域では独自内挿の不確実性が大きくなります。季節予報の降雪量とは直接対応しません。",
        "legends": {
            "monthly": {
                "absolute": legend("月平均最深積雪", [0.01, 1, 5, 10, 20, 40, 60, 80, 100, 150, 200, 300], SNOW_COLORS, "0cm", "60cm", "300cm超"),
                "difference": legend("30年平均値の更新差", [-100, -75, -50, -25, -10, -2, 2, 10, 25, 50, 75, 100], DIFF_COLORS, "−100cm以下", "0cm", "+100cm超"),
            },
            "annual": {
                "absolute": legend("年値（最大月平均）", [0.01, 1, 5, 10, 20, 30, 40, 60, 80, 100, 150, 200], SNOW_COLORS, "0cm", "40cm", "200cm超"),
                "difference": legend("30年平均値の更新差", [-100, -75, -50, -25, -10, -2, 2, 10, 25, 50, 75, 100], DIFF_COLORS, "−100cm以下", "0cm", "+100cm超"),
            },
        },
    },
    "503": {
        "name": "降雪の深さ合計", "short_name": "降雪量", "definition": "月または年の降雪の深さ合計",
        "unit": "cm", "months": tuple(range(1, 14)), "annual_label": "年間降雪量・年合計",
        "annual_definition": "12か月の降雪の深さの年合計", "forecast_element": "snowfall",
        "dtype": "uint32", "dtype_code": 3, "nodata": 4294967295, "schema": 2, "stem": "snowfall",
        "quality_note": "雪の観測地点が疎な地域では独自IDW内挿の不確実性が大きくなります。0は欠測ではなく有効値です。",
        "legends": {
            "monthly": {
                "absolute": legend("月降雪量", [0.01, 1, 5, 10, 20, 40, 60, 100, 150, 200, 250, 300], SNOW_COLORS, "0cm", "60cm", "300cm超"),
                "difference": legend("30年平均値の更新差", [-50, -40, -30, -20, -10, -2, 2, 10, 20, 30, 40, 50], DIFF_COLORS, "−50cm以下", "0cm", "+50cm超"),
            },
            "annual": {
                "absolute": legend("年間降雪量", [1, 10, 25, 50, 100, 200, 300, 400, 500, 750, 1000, 1250], SNOW_COLORS, "1cm以下", "300cm", "1,250cm超"),
                "difference": legend("30年平均値の更新差", [-50, -40, -30, -20, -10, -2, 2, 10, 20, 30, 40, 50], DIFF_COLORS, "−50cm以下", "0cm", "+50cm超"),
            },
        },
    },
    "610": {
        "name": "全天日射量（日平均）", "short_name": "全天日射量",
        "definition": "Angstrom–Prescott式で推定した月別の日平均全天日射量。月合計ではありません。",
        "unit": "MJ/㎡/日", "months": tuple(range(1, 13)), "annual_label": None,
        "annual_definition": None, "forecast_element": None, "dtype": "uint16", "dtype_code": 2,
        "nodata": 65535, "schema": 2, "stem": "solar", "quality_note": "年値はありません。画面側で12か月から合成しません。対応する季節予報要素もありません。",
        "legends": {
            "monthly": {
                "absolute": legend("全天日射量（日平均）", [4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24, 26], SOLAR_COLORS, "4MJ/㎡/日以下", "16MJ/㎡/日", "26MJ/㎡/日超"),
                "difference": legend("30年平均値の更新差", [-2.0, -1.5, -1.0, -0.5, -0.2, -0.05, 0.05, 0.2, 0.5, 1.0, 1.5, 2.0], DIFF_COLORS, "−2.0MJ/㎡/日以下", "0MJ/㎡/日", "+2.0MJ/㎡/日超"),
            },
            "annual": None,
        },
    },
}


DTYPES = {
    "int16": np.dtype("<i2"),
    "uint16": np.dtype("<u2"),
    "uint32": np.dtype("<u4"),
}
STAGING_FORBIDDEN_NAMES = {".DS_Store", ".env"}
STAGING_FORBIDDEN_PARTS = {"__pycache__", ".cache", ".staging", "private", "screenshots"}
EXPECTED_FIXTURES: dict[str, tuple[tuple[str, int, tuple[float, float]], ...]] = {
    "201": (("53394611", 7, (26.34, 26.67)), ("53394611", 13, (16.59, 16.75))),
    "202": (("53394611", 7, (30.03, 30.57)), ("53394611", 13, (20.45, 20.79))),
    "203": (("53394611", 7, (23.43, 23.68)), ("53394611", 13, (13.21, 13.28))),
    "101": (("53394611", 7, (154.81, 157.68)), ("53394611", 13, (1583.43, 1588.96))),
    "401": (("53394611", 7, (151.50, 162.90)), ("53394611", 13, (1926.68, 1970.26))),
    "501": (
        ("53394611", 7, (0.00, 0.00)), ("53394611", 13, (3.11, 2.97)),
        ("60407677", 2, (378.14, 395.98)), ("60407677", 13, (84.70, 88.31)),
    ),
    "503": (("53394611", 7, (0.00, 0.00)), ("53394611", 13, (8.28, 7.31))),
    "610": (("53394611", 1, (9.72, 9.90)), ("53394611", 7, (16.44, 17.19))),
}


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def generator_config_hash() -> str:
    """Hash every setting that can change a public climate byte or meaning."""
    return sha256_json({
        "elements": ELEMENTS,
        "windows": WINDOWS,
        "value_scale": VALUE_SCALE,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "raster_zoom": RASTER_ZOOM,
        "raster_padding": RASTER_PADDING,
        "difference_wording": DIFFERENCE_WORDING,
        "chunk_v1_format": "<8sIIHHHh",
        "chunk_v2_format": "<8sIIHHHBBI",
    })


def iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def color_rgba(hex_color: str) -> tuple[int, int, int, int]:
    value = hex_color.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4)) + (255,)


def mercator_world(lat: np.ndarray, lon: np.ndarray, zoom: int) -> tuple[np.ndarray, np.ndarray]:
    size = 256 * (2**zoom)
    x = (lon + 180.0) / 360.0 * size
    y = (1.0 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2.0 * size
    return x, y


def world_to_lat(y: float, zoom: int) -> float:
    size = 256 * (2**zoom)
    return math.degrees(math.atan(math.sinh(math.pi - 2.0 * math.pi * y / size)))


def world_to_lon(x: float, zoom: int) -> float:
    return x / (256 * (2**zoom)) * 360.0 - 180.0


def load_master(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    codes: list[int] = []
    lats: list[float] = []
    lons: list[float] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        expected = [
            "mesh_code", "center_lat", "center_lon", "elev_mean_m", "elev_max_m",
            "elev_min_m", "slope_max_deg", "slope_mean_deg",
        ]
        header = next(reader, None)
        if header != expected:
            raise ValueError(f"Unexpected master schema: {header}")
        for row in reader:
            if len(row) != len(expected):
                raise ValueError("Unexpected master row width")
            codes.append(int(row[0]))
            lats.append(float(row[1]))
            lons.append(float(row[2]))
    code_array = np.asarray(codes, dtype="<u4")
    if code_array.size != EXPECTED_MESH_COUNT:
        raise ValueError(f"Expected {EXPECTED_MESH_COUNT} meshes, got {code_array.size}")
    if np.unique(code_array).size != code_array.size:
        raise ValueError("mesh_code contains duplicates")
    return code_array, np.asarray(lats, dtype="<f4"), np.asarray(lons, dtype="<f4")


def load_window(path: Path, master_codes: np.ndarray, spec: dict[str, Any]) -> tuple[np.ndarray, dict[str, Any]]:
    months = spec["months"]
    dtype = DTYPES[spec["dtype"]]
    nodata = spec["nodata"]
    count = master_codes.size
    values = np.full((len(months), count), nodata, dtype=dtype)
    month_counts = np.zeros(len(months), dtype=np.int64)
    minima = np.full(len(months), np.inf)
    maxima = np.full(len(months), -np.inf)
    max_rounding_error = 0.0
    info = np.iinfo(dtype)
    minimum_allowed = info.min + (1 if nodata == info.min else 0)
    maximum_allowed = info.max - (1 if nodata == info.max else 0)

    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if header != ["mesh_code", "month", "value"]:
            raise ValueError(f"Unexpected data schema in {path.name}: {header}")
        for row in reader:
            if len(row) != 3:
                raise ValueError(f"Unexpected row width in {path.name}")
            code_text, month_text, value_text = row
            month = int(month_text)
            if month not in months:
                raise ValueError(f"Unexpected month {month} in {path.name}")
            month_index = month - 1
            mesh_index = int(month_counts[month_index])
            if mesh_index >= count:
                raise ValueError(f"Too many rows for month {month} in {path.name}")
            code = int(code_text)
            if code != int(master_codes[mesh_index]):
                raise ValueError(
                    f"Mesh order mismatch in {path.name}, month {month}, row {mesh_index}: "
                    f"{code} != {int(master_codes[mesh_index])}"
                )
            value = float(value_text)
            if not math.isfinite(value):
                raise ValueError(f"Non-finite value in {path.name}")
            quantized = int(round(value * VALUE_SCALE))
            if not minimum_allowed <= quantized <= maximum_allowed:
                raise ValueError(f"Value outside {spec['dtype']} range in {path.name}: {value}")
            values[month_index, mesh_index] = quantized
            month_counts[month_index] += 1
            minima[month_index] = min(minima[month_index], value)
            maxima[month_index] = max(maxima[month_index], value)
            max_rounding_error = max(max_rounding_error, abs(quantized / VALUE_SCALE - value))

    if not np.all(month_counts == count):
        raise ValueError(f"Month counts mismatch in {path.name}: {month_counts.tolist()}")
    if max_rounding_error > 1e-9:
        raise ValueError(f"{path.name} contains values that are not exact at the public 0.01 scale")
    return values, {
        "file": path.name,
        "sha256": sha256_path(path),
        "row_count": int(month_counts.sum()),
        "month_counts": {str(month): int(month_counts[index]) for index, month in enumerate(months)},
        "min": {str(month): round(float(minima[index]), 2) for index, month in enumerate(months)},
        "max": {str(month): round(float(maxima[index]), 2) for index, month in enumerate(months)},
        "max_quantization_error": round(max_rounding_error, 8),
    }


def write_chunk(path: Path, codes: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                values: np.ndarray, spec: dict[str, Any]) -> None:
    count = codes.size
    months = spec["months"]
    if spec["schema"] == 1:
        header = struct.pack(
            "<8sIIHHHh", b"NWCBCH1\0", 1, count, len(WINDOWS), len(months), VALUE_SCALE, spec["nodata"],
        )
    else:
        dtype = DTYPES[spec["dtype"]]
        unsigned_view = np.dtype("<u2") if dtype.itemsize == 2 else np.dtype("<u4")
        nodata_bits = int(np.asarray([spec["nodata"]], dtype=dtype).view(unsigned_view)[0])
        header = struct.pack(
            "<8sIIHHHBBI", b"NWCBCH2\0", 2, count, len(WINDOWS), len(months), VALUE_SCALE,
            spec["dtype_code"], 0, nodata_bits,
        )
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(np.asarray(codes, dtype="<u4").tobytes(order="C"))
        handle.write(np.asarray(lats, dtype="<f4").tobytes(order="C"))
        handle.write(np.asarray(lons, dtype="<f4").tobytes(order="C"))
        handle.write(np.asarray(values, dtype=DTYPES[spec["dtype"]]).tobytes(order="C"))


def render_geometry(lats: np.ndarray, lons: np.ndarray) -> dict[str, Any]:
    world_x, world_y = mercator_world(lats.astype(np.float64), lons.astype(np.float64), RASTER_ZOOM)
    left = math.floor(float(world_x.min())) - RASTER_PADDING
    right = math.ceil(float(world_x.max())) + RASTER_PADDING + 1
    top = math.floor(float(world_y.min())) - RASTER_PADDING
    bottom = math.ceil(float(world_y.max())) + RASTER_PADDING + 1
    width, height = right - left, bottom - top
    px = np.rint(world_x - left).astype(np.int32)
    py = np.rint(world_y - top).astype(np.int32)
    point_mask = np.zeros((height, width), dtype=bool)
    point_mask[py, px] = True
    distance, nearest = ndimage.distance_transform_edt(~point_mask, return_indices=True)
    mask_y, mask_x = np.nonzero(distance <= 1.35)
    return {
        "px": px, "py": py, "mask_y": mask_y, "mask_x": mask_x,
        "nearest_y": nearest[0, mask_y, mask_x], "nearest_x": nearest[1, mask_y, mask_x],
        "width": width, "height": height,
        "bounds": {
            "north": world_to_lat(top, RASTER_ZOOM), "south": world_to_lat(bottom, RASTER_ZOOM),
            "west": world_to_lon(left, RASTER_ZOOM), "east": world_to_lon(right, RASTER_ZOOM),
        },
        "source_mesh_count": int(lats.size),
        "unique_render_pixels": int(np.unique(py.astype(np.int64) * width + px).size),
    }


def render_one(path: Path, data: np.ndarray, config: dict[str, Any], geometry: dict[str, Any]) -> None:
    palette = np.asarray([color_rgba(color) for color in config["colors"]], dtype=np.uint8)
    bins = np.digitize(data, config["breaks"], right=False)
    point_colors = np.zeros((geometry["height"], geometry["width"], 4), dtype=np.uint8)
    point_colors[geometry["py"], geometry["px"]] = palette[bins]
    rgba = np.zeros_like(point_colors)
    rgba[geometry["mask_y"], geometry["mask_x"]] = point_colors[
        geometry["nearest_y"], geometry["nearest_x"]
    ]
    Image.fromarray(rgba, mode="RGBA").save(path, "WEBP", lossless=True, method=4)


def render_assets(directory: Path, values: np.ndarray, spec: dict[str, Any], geometry: dict[str, Any]) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    files: dict[str, dict[str, Any]] = {window: {} for window in WINDOWS}
    files["difference"] = {}
    for window_index, window in enumerate(WINDOWS):
        for month_index, month in enumerate(spec["months"]):
            profile = "annual" if month == 13 else "monthly"
            config = spec["legends"][profile]["absolute"]
            data = values[window_index, month_index].astype(np.float64) / VALUE_SCALE
            filename = f"{spec['stem']}_{window}_m{month:02d}.webp"
            path = directory / filename
            render_one(path, data, config, geometry)
            files[window][str(month)] = {"path": f"rasters/{filename}", "bytes": path.stat().st_size, "sha256": sha256_path(path)}
    for month_index, month in enumerate(spec["months"]):
        profile = "annual" if month == 13 else "monthly"
        config = spec["legends"][profile]["difference"]
        difference = (
            values[1, month_index].astype(np.int64) - values[0, month_index].astype(np.int64)
        ) / VALUE_SCALE
        filename = f"{spec['stem']}_difference_m{month:02d}.webp"
        path = directory / filename
        render_one(path, difference, config, geometry)
        files["difference"][str(month)] = {"path": f"rasters/{filename}", "bytes": path.stat().st_size, "sha256": sha256_path(path)}
    return files


def element_metadata(code: str, spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": code,
        "name": spec["name"],
        "short_name": spec["short_name"],
        "definition": spec["definition"],
        "unit": spec["unit"],
        "decimal_places": 2,
        "value_scale": VALUE_SCALE,
        "nodata": spec["nodata"],
        "annual": {
            "available": 13 in spec["months"],
            "label": spec["annual_label"],
            "definition": spec["annual_definition"],
            "derived_from_monthly_grid": False,
        },
        "forecast_element": spec["forecast_element"],
        "quality_note": spec["quality_note"],
    }


def representative_values(code: str, codes: np.ndarray, values: np.ndarray, spec: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[tuple[str, tuple[int, ...]]] = [("53394611", (7, 13) if 13 in spec["months"] else (7,))]
    if code == "501":
        checks.extend([("60407677", (2, 13)), ("39272554", (1, 13))])
    result: list[dict[str, Any]] = []
    for mesh_code, months in checks:
        index = int(np.searchsorted(codes, int(mesh_code)))
        if index >= codes.size or int(codes[index]) != int(mesh_code):
            raise ValueError(f"Representative mesh {mesh_code} is missing")
        month_values: dict[str, Any] = {}
        for month in months:
            month_index = spec["months"].index(month)
            month_values[str(month)] = {
                window: round(float(values[window_index, month_index, index]) / VALUE_SCALE, 2)
                for window_index, window in enumerate(WINDOWS)
            }
        result.append({"mesh_code": mesh_code, "values": month_values})
    return result


def validate_expected_fixtures(code: str, codes: np.ndarray, values: np.ndarray, spec: dict[str, Any]) -> None:
    for mesh_code, month, expected in EXPECTED_FIXTURES[code]:
        index = int(np.searchsorted(codes, int(mesh_code)))
        if index >= codes.size or int(codes[index]) != int(mesh_code):
            raise ValueError(f"Fixture mesh {mesh_code} is missing for element {code}")
        month_index = spec["months"].index(month)
        actual = tuple(
            round(float(values[window_index, month_index, index]) / VALUE_SCALE, 2)
            for window_index in range(len(WINDOWS))
        )
        if actual != expected:
            raise ValueError(f"Fixture mismatch for element {code} at {mesh_code}/m{month}: {actual} != {expected}")


def build_element(code: str, spec: dict[str, Any], data_root: Path, output_root: Path,
                  source_codes: np.ndarray, codes: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                  sort_order: np.ndarray, geometry: dict[str, Any], master_sha: str,
                  source_hashes: dict[str, str], db_version: str,
                  generated_at: str) -> tuple[dict[str, Any], np.ndarray]:
    source_audit: dict[str, Any] = {}
    windows: list[np.ndarray] = []
    for window in WINDOWS:
        path = data_root / f"mesh1km_{code}_{window}.csv.gz"
        if not path.is_file():
            raise FileNotFoundError(path)
        window_values, audit = load_window(path, source_codes, spec)
        if audit["sha256"] != source_hashes[path.name]:
            raise ValueError(f"Source changed while building: {path.name}")
        windows.append(window_values[:, sort_order])
        source_audit[window] = audit
    values = np.stack(windows, axis=0)
    if np.any(values == spec["nodata"]):
        raise ValueError(f"Element {code} contains nodata after full source load")
    validate_expected_fixtures(code, codes, values, spec)

    element_root = output_root if code == "201" else output_root / "elements" / code
    chunks_dir = element_root / "chunks"
    rasters_dir = element_root / "rasters"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    prefixes = np.asarray([int(str(int(mesh)).zfill(8)[:4]) for mesh in codes], dtype=np.int32)
    unique_prefixes = np.unique(prefixes)
    if unique_prefixes.size != EXPECTED_PREFIX_COUNT:
        raise ValueError(f"Expected {EXPECTED_PREFIX_COUNT} chunk prefixes, got {unique_prefixes.size}")
    chunks: dict[str, Any] = {}
    for prefix in unique_prefixes:
        indices = np.flatnonzero(prefixes == prefix)
        filename = f"mesh_{prefix:04d}.bin"
        path = chunks_dir / filename
        write_chunk(path, codes[indices], lats[indices], lons[indices], values[:, :, indices], spec)
        chunks[str(prefix)] = {
            "path": f"chunks/{filename}", "count": int(indices.size),
            "first_mesh_code": str(int(codes[indices[0]])).zfill(8),
            "last_mesh_code": str(int(codes[indices[-1]])).zfill(8),
            "bytes": path.stat().st_size, "sha256": sha256_path(path),
        }
    raster_files = render_assets(rasters_dir, values, spec, geometry)

    source_signature = {
        "generator_version": GENERATOR_VERSION,
        "generator_config_sha256": generator_config_hash(),
        "master_sha256": master_sha,
        "element": code,
        "windows": {window: source_audit[window]["sha256"] for window in WINDOWS},
    }
    dataset_id = f"climate-baseline-{code}-{sha256_json(source_signature)[:16]}"
    header_bytes = 24 if spec["schema"] == 1 else 28
    chunk_format = {
        "magic": f"NWCBCH{spec['schema']}", "version": spec["schema"], "header_bytes": header_bytes,
        "layout": f"codes[u32], lat[f32], lon[f32], values[{spec['dtype']} window-major/month-major]",
        "dtype": spec["dtype"], "dtype_code": spec["dtype_code"], "value_scale": VALUE_SCALE,
        "nodata": spec["nodata"], "window_order": list(WINDOWS), "month_order": list(spec["months"]),
    }
    manifest: dict[str, Any] = {
        "schema_version": spec["schema"], "generator_version": GENERATOR_VERSION,
        "dataset_id": dataset_id, "generated_at": generated_at,
        "source": {
            "logical_database_id": "naturewxlab-climate-foundation-db", "database_version": db_version,
            "master_logical_id": "climate-mesh-1km/master", "master_sha256": master_sha,
            "source_audit": source_audit,
        },
        "element": element_metadata(code, spec),
        "windows": [{"id": window, "label": WINDOW_LABELS[window]} for window in WINDOWS],
        "months": [
            {"id": month, "label": spec["annual_label"] if month == 13 else f"{month}月"}
            for month in spec["months"]
        ],
        "mesh_count": int(codes.size), "mesh_unique_count": int(np.unique(codes).size),
        "value_count": int(values.size), "chunk_format": chunk_format, "chunks": chunks,
        "rasters": {
            "render": {
                "projection": "EPSG:3857", "native_zoom": RASTER_ZOOM,
                "width": geometry["width"], "height": geometry["height"], "bounds": geometry["bounds"],
                "source_mesh_count": geometry["source_mesh_count"],
                "unique_render_pixels": geometry["unique_render_pixels"],
                "note": "全国表示用の描画縮約。地点値は全1kmメッシュを保持するchunkから取得。",
            },
            "legends": spec["legends"], "files": raster_files,
        },
        "validation": {
            "master_count_ok": codes.size == EXPECTED_MESH_COUNT, "master_duplicates": 0,
            "public_chunk_codes_sorted": True,
            "window_row_counts_ok": all(
                source_audit[window]["row_count"] == EXPECTED_MESH_COUNT * len(spec["months"])
                for window in WINDOWS
            ),
            "mesh_order_matches_master": True, "missing_values": 0,
            "representative_values": representative_values(code, codes, values, spec),
        },
        "wording": {
            "difference": DIFFERENCE_WORDING,
            "resolution": "気候平均は独自算出の全国陸域1kmメッシュ。季節予報は公式の地域単位。",
        },
    }
    if code == "201":
        # Preserve the v1 manifest keys for an older cached frontend while the
        # catalog-aware frontend uses the profile-aware `legends` object.
        manifest["rasters"]["raw_legend"] = spec["legends"]["monthly"]["absolute"]
        manifest["rasters"]["difference_legend"] = spec["legends"]["monthly"]["difference"]
        manifest["static"] = {"prefectures": None}
    write_json(element_root / "manifest.json", manifest)
    return manifest, values


def catalog_assets_valid(output: Path, catalog: dict[str, Any]) -> bool:
    assets = catalog.get("assets")
    if not isinstance(assets, dict):
        return False
    actual = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*") if path.is_file() and path.name != "catalog.json"
    }
    if actual != set(assets):
        return False
    for relative, entry in assets.items():
        path = output / relative
        if not isinstance(entry, dict) or path.stat().st_size != entry.get("bytes") or sha256_path(path) != entry.get("sha256"):
            return False
    return True


def expected_staging_assets(manifests: dict[str, dict[str, Any]]) -> set[str]:
    expected = {"static/prefectures.geojson"}
    for code, manifest in manifests.items():
        prefix = "" if code == "201" else f"elements/{code}/"
        expected.add(f"{prefix}manifest.json")
        expected.update(f"{prefix}{entry['path']}" for entry in manifest["chunks"].values())
        for group in manifest["rasters"]["files"].values():
            expected.update(f"{prefix}{entry['path']}" for entry in group.values())
    return expected


def remove_finder_metadata(root: Path) -> None:
    for path in root.rglob(".DS_Store"):
        path.unlink(missing_ok=True)


def validate_staging_file_set(root: Path, expected: set[str]) -> None:
    symlinks = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_symlink()]
    if symlinks:
        raise ValueError(f"Symlinks are forbidden in climate staging: {sorted(symlinks)}")
    actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    forbidden = [
        relative for relative in actual
        if Path(relative).name in STAGING_FORBIDDEN_NAMES
        or any(part in STAGING_FORBIDDEN_PARTS or part.startswith(".") for part in Path(relative).parts)
    ]
    if forbidden:
        raise ValueError(f"Forbidden files appeared in climate staging: {sorted(forbidden)}")
    if actual != expected:
        raise ValueError(f"Climate staging file set mismatch: {sorted(actual ^ expected)}")


def decode_staged_fixture(element_root: Path, manifest: dict[str, Any], mesh_code: str, month: int) -> tuple[float, float]:
    entry = manifest["chunks"][mesh_code[:4]]
    raw = (element_root / entry["path"]).read_bytes()
    magic = raw[:8].rstrip(b"\0")
    if magic == b"NWCBCH1":
        _, version, count, window_count, month_count, scale, nodata = struct.unpack("<8sIIHHHh", raw[:24])
        header_bytes = 24
        dtype = np.dtype("<i2")
        if version != 1:
            raise ValueError("Unexpected v1 chunk version")
    elif magic == b"NWCBCH2":
        _, version, count, window_count, month_count, scale, dtype_code, reserved, nodata_bits = struct.unpack(
            "<8sIIHHHBBI", raw[:28]
        )
        if version != 2 or reserved != 0 or dtype_code not in {1, 2, 3}:
            raise ValueError("Unexpected v2 chunk header")
        header_bytes = 28
        dtype = {1: np.dtype("<i2"), 2: np.dtype("<u2"), 3: np.dtype("<u4")}[dtype_code]
        nodata = int(np.asarray([nodata_bits], dtype="<u4").astype(dtype)[0])
    else:
        raise ValueError(f"Unexpected staged chunk magic: {magic!r}")
    if window_count != len(WINDOWS) or month_count != len(manifest["months"]) or scale != VALUE_SCALE:
        raise ValueError("Unexpected staged chunk dimensions")
    codes = np.frombuffer(raw, dtype="<u4", count=count, offset=header_bytes)
    index = int(np.searchsorted(codes, int(mesh_code)))
    if index >= count or int(codes[index]) != int(mesh_code):
        raise ValueError(f"Fixture mesh {mesh_code} missing from staged chunk")
    values_offset = header_bytes + count * 12
    values = np.frombuffer(raw, dtype=dtype, count=window_count * month_count * count, offset=values_offset)
    month_ids = [entry["id"] for entry in manifest["months"]]
    month_index = month_ids.index(month)
    result = []
    for window_index in range(window_count):
        value = int(values[(window_index * month_count + month_index) * count + index])
        if value == nodata:
            raise ValueError("Fixture unexpectedly decoded as nodata")
        result.append(round(value / scale, 2))
    return tuple(result)  # type: ignore[return-value]


def validate_staged_fixtures(root: Path, manifests: dict[str, dict[str, Any]]) -> None:
    for code, fixtures in EXPECTED_FIXTURES.items():
        element_root = root if code == "201" else root / "elements" / code
        for mesh_code, month, expected in fixtures:
            actual = decode_staged_fixture(element_root, manifests[code], mesh_code, month)
            if actual != expected:
                raise ValueError(f"Staged fixture mismatch for {code}/{mesh_code}/m{month}: {actual} != {expected}")


def source_catalog_id(data_root: Path, master_path: Path, prefectures_path: Path, db_version: str) -> tuple[str, dict[str, str]]:
    hashes = {"mesh1km_master.csv.gz": sha256_path(master_path)}
    for code, spec in ELEMENTS.items():
        for window in WINDOWS:
            path = data_root / f"mesh1km_{code}_{window}.csv.gz"
            if not path.is_file():
                raise FileNotFoundError(path)
            hashes[path.name] = sha256_path(path)
    hashes["prefectures.geojson"] = sha256_path(prefectures_path)
    identity = {
        "generator_version": GENERATOR_VERSION, "database_version": db_version,
        "generator_config_sha256": generator_config_hash(),
        "inputs": hashes,
    }
    return f"climate-baseline-all-{sha256_json(identity)[:16]}", hashes


def build(args: argparse.Namespace) -> dict[str, Any]:
    db_root = Path(args.db_root).expanduser().resolve()
    data_root = db_root / "02_基盤データ"
    master_path = data_root / "mesh1km_master.csv.gz"
    if not master_path.is_file():
        raise FileNotFoundError(master_path)
    output = Path(args.output).expanduser().resolve()
    if output == db_root or db_root in output.parents or output in db_root.parents:
        raise ValueError("Public output must not be the shared database, its child, or its ancestor")
    if (output / ".git").exists():
        raise ValueError("Public output must be a data directory, not a Git repository root")
    prefectures_path = Path(args.prefectures).expanduser().resolve() if args.prefectures else output / "static" / "prefectures.geojson"
    if not prefectures_path.is_file():
        raise FileNotFoundError("A prefecture GeoJSON is required via --prefectures or the existing output tree")

    catalog_id, input_hashes = source_catalog_id(data_root, master_path, prefectures_path, args.db_version)
    existing_catalog_path = output / "catalog.json"
    if existing_catalog_path.is_file():
        remove_finder_metadata(output)
        existing = json.loads(existing_catalog_path.read_text(encoding="utf-8"))
        if (
            existing.get("dataset_id") == catalog_id
            and existing.get("generator_version") == GENERATOR_VERSION
            and catalog_assets_valid(output, existing)
        ):
            return {**existing, "changed": False}

    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.staging-", dir=output.parent))
    backup: Path | None = None
    try:
        source_codes, source_lats, source_lons = load_master(master_path)
        if sha256_path(master_path) != input_hashes["mesh1km_master.csv.gz"]:
            raise ValueError("Master source changed while building")
        sort_order = np.argsort(source_codes, kind="stable")
        codes = source_codes[sort_order]
        lats = source_lats[sort_order]
        lons = source_lons[sort_order]
        if np.any(codes[1:] <= codes[:-1]):
            raise ValueError("Sorted public mesh index is not strictly ascending")
        geometry = render_geometry(lats, lons)
        static_dir = staging / "static"
        static_dir.mkdir(parents=True)
        shutil.copyfile(prefectures_path, static_dir / "prefectures.geojson")
        if sha256_path(static_dir / "prefectures.geojson") != input_hashes["prefectures.geojson"]:
            raise ValueError("Prefecture boundary changed while building")
        generated_at = iso_now()
        manifests: dict[str, dict[str, Any]] = {}
        temperature_values: dict[str, np.ndarray] = {}
        for code, spec in ELEMENTS.items():
            manifest, values = build_element(
                code, spec, data_root, staging, source_codes, codes, lats, lons, sort_order,
                geometry, input_hashes["mesh1km_master.csv.gz"], input_hashes,
                args.db_version, generated_at,
            )
            manifests[code] = manifest
            if code in {"201", "202", "203"}:
                temperature_values[code] = values
            if code == "203":
                violations = int(np.count_nonzero(
                    (temperature_values["203"].astype(np.int32) > temperature_values["201"].astype(np.int32))
                    | (temperature_values["201"].astype(np.int32) > temperature_values["202"].astype(np.int32))
                ))
                if violations:
                    raise ValueError(f"Temperature invariant 203 <= 201 <= 202 failed at {violations} values")
                temperature_values.clear()

        prefecture_output = staging / "static" / "prefectures.geojson"
        prefecture_entry = {
            "path": "static/prefectures.geojson", "bytes": prefecture_output.stat().st_size,
            "sha256": sha256_path(prefecture_output),
        }
        manifests["201"]["static"]["prefectures"] = prefecture_entry
        write_json(staging / "manifest.json", manifests["201"])

        catalog_elements: list[dict[str, Any]] = []
        for code, spec in ELEMENTS.items():
            relative = "manifest.json" if code == "201" else f"elements/{code}/manifest.json"
            path = staging / relative
            catalog_elements.append({
                **element_metadata(code, spec),
                "manifest": {"path": relative, "bytes": path.stat().st_size, "sha256": sha256_path(path)},
            })
        # Build the catalog from the manifests' exact allowlist, never from an
        # open-ended directory walk. Finder can recreate .DS_Store between two
        # statements, so clean and validate both before and after cataloging.
        expected_assets = expected_staging_assets(manifests)
        remove_finder_metadata(staging)
        validate_staging_file_set(staging, expected_assets)
        assets = {
            relative: {"bytes": (staging / relative).stat().st_size, "sha256": sha256_path(staging / relative)}
            for relative in sorted(expected_assets)
        }
        quantization_errors = [
            float(audit["max_quantization_error"])
            for manifest in manifests.values()
            for audit in manifest["source"]["source_audit"].values()
        ]
        maximum_quantization_error = max(quantization_errors, default=math.inf)
        catalog = {
            "schema_version": CATALOG_SCHEMA_VERSION, "generator_version": GENERATOR_VERSION,
            "dataset_id": catalog_id, "generated_at": generated_at,
            "source": {
                "logical_database_id": "naturewxlab-climate-foundation-db", "database_version": args.db_version,
                "input_file_count": len(input_hashes), "input_sha256": input_hashes,
                "generator_config_sha256": generator_config_hash(),
            },
            "mesh_count": EXPECTED_MESH_COUNT,
            "windows": [{"id": window, "label": WINDOW_LABELS[window]} for window in WINDOWS],
            "elements": catalog_elements, "static": {"prefectures": prefecture_entry},
            "assets": assets,
            "validation": {
                "element_count": len(ELEMENTS), "mesh_count_ok": True,
                "temperature_order_violations": 0,
                "total_value_count": sum(int(manifest["value_count"]) for manifest in manifests.values()),
                "asset_count": len(assets),
                "max_input_quantization_error": maximum_quantization_error,
                "all_inputs_quantized_at_0_01": maximum_quantization_error <= 1e-9,
            },
            "wording": {"difference": DIFFERENCE_WORDING},
        }
        write_json(staging / "catalog.json", catalog)

        remove_finder_metadata(staging)
        validate_staging_file_set(staging, expected_assets | {"catalog.json"})
        if not catalog_assets_valid(staging, catalog):
            raise ValueError("Climate catalog assets failed the final staged checksum check")
        validate_staged_fixtures(staging, manifests)

        if output.exists():
            remove_finder_metadata(staging)
            validate_staging_file_set(staging, expected_assets | {"catalog.json"})
            if not catalog_assets_valid(staging, catalog):
                raise ValueError("Climate staging changed before atomic promotion")
            backup = output.parent / f".{output.name}.backup-{os.getpid()}"
            if backup.exists():
                shutil.rmtree(backup)
            os.replace(output, backup)
        try:
            os.replace(staging, output)
        except Exception:
            if backup is not None and backup.exists() and not output.exists():
                os.replace(backup, output)
            raise
        try:
            remove_finder_metadata(output)
            validate_staging_file_set(output, expected_assets | {"catalog.json"})
            if not catalog_assets_valid(output, catalog):
                raise ValueError("Promoted climate catalog failed its final checksum check")
        except Exception:
            if output.exists():
                shutil.rmtree(output)
            if backup is not None and backup.exists():
                os.replace(backup, output)
            raise
        if backup is not None and backup.exists():
            shutil.rmtree(backup)
        return {**catalog, "changed": True}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-root", default=os.environ.get("NATUREWXLAB_DB_ROOT"), help="Read-only shared climate database root")
    parser.add_argument("--output", default="data/climate", help="Derived public output directory")
    parser.add_argument("--prefectures", help="Prefecture GeoJSON; defaults to the existing output asset")
    parser.add_argument("--db-version", default="v2.9", help="Logical source DB version")
    args = parser.parse_args()
    if not args.db_root:
        parser.error("--db-root or NATUREWXLAB_DB_ROOT is required")
    return args


def main() -> None:
    result = build(parse_args())
    print(json.dumps({
        "dataset_id": result["dataset_id"], "changed": result["changed"],
        "element_count": result["validation"]["element_count"],
        "mesh_count": result["mesh_count"], "total_value_count": result["validation"]["total_value_count"],
        "asset_count": result["validation"]["asset_count"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
