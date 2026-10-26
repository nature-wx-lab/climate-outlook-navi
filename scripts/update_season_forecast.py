#!/usr/bin/env python3
"""Fetch, validate, and atomically promote JMA seasonal forecast derivatives."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import os
import shutil
import ssl
import tempfile
import subprocess
import sys
import time
import urllib.request
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Plain CLI invocations must preserve the same source hygiene as python -B.
sys.dont_write_bytecode = True

try:
    import certifi
except ImportError:  # pragma: no cover - system CA remains the fallback
    certifi = None


SCHEMA_VERSION = 2
GENERATOR_VERSION = "4.0.0"
COLLECTION_FILE = "collection-status.json"
CALENDAR_FILE = "issue-calendar.json"
REFERENCE_FILE = "collection-reference.json"
PRODUCT_URLS = {
    "P1M": "https://www.jma.go.jp/bosai/season/data/P1M.json",
    "P3M": "https://www.jma.go.jp/bosai/season/data/P3M.json",
    "P6M": "https://www.jma.go.jp/bosai/season/data/P6M.json",
}
REGIONS_URL = "https://www.jma.go.jp/bosai/common/const/geojson/class15s.json"
AREA_URL = "https://www.jma.go.jp/bosai/common/const/area.json"
FORECAST_ORDER = [
    "010100", "010101", "010102", "010103",
    "010200", "010201", "010202", "010203", "010204",
    "010300", "010400", "010500",
    "010600", "010601", "010602",
    "010700", "010701", "010702",
    "010800", "010900", "011001", "011002", "011000", "011100",
]
REGION_NAMES = {
    "010100": "北海道地方", "010101": "北海道地方（日本海側）",
    "010102": "北海道地方（オホーツク海側）", "010103": "北海道地方（太平洋側）",
    "010200": "東北地方", "010201": "東北地方（日本海側）", "010202": "東北地方（太平洋側）",
    "010203": "北東北", "010204": "南東北", "010300": "関東甲信地方",
    "010400": "東海地方", "010500": "北陸地方", "010600": "近畿地方",
    "010601": "近畿地方（太平洋側）", "010602": "近畿地方（日本海側）",
    "010700": "中国地方", "010701": "山陰", "010702": "山陽",
    "010800": "四国地方", "010900": "九州北部地方",
    "011000": "九州南部・奄美地方", "011001": "九州南部", "011002": "奄美地方",
    "011100": "沖縄地方",
}
TERM_LABELS = {
    "P1M": ["向こう1か月", "第1週", "第2週", "第3～4週"],
    "P3M": ["向こう3か月", "第1か月", "第2か月", "第3か月"],
    "WARM": ["夏（6～8月）", "梅雨の時期（奄美・沖縄は5～6月、その他は6～7月）"],
    "COLD": ["冬（12～2月）"],
}
ELEMENTS = {
    "temperature": {"name": "気温", "classes": ["低い", "平年並", "高い"]},
    "precipitation": {"name": "降水量", "classes": ["少ない", "平年並", "多い"]},
    "sunshine": {"name": "日照時間", "classes": ["少ない", "平年並", "多い"]},
    "snowfall": {"name": "降雪量", "classes": ["少ない", "平年並", "多い"]},
}
SUPPORTED_TERMS = {
    "WARM": {"temperature": (0,), "precipitation": (0, 1), "sunshine": (), "snowfall": ()},
    "COLD": {"temperature": (0,), "precipitation": (0,), "sunshine": (), "snowfall": (0,)},
    "P1M": {
        "temperature": (0, 1, 2, 3),
        "precipitation": (0,),
        "sunshine": (0,),
        "snowfall": (0,),
    },
    "P3M": {
        "temperature": (0, 1, 2, 3),
        "precipitation": (0, 1, 2, 3),
        "sunshine": (),
        "snowfall": (0,),
    },
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"Datetime lacks timezone: {value}")
    return parsed


def add_months(value: datetime, months: int) -> datetime:
    target_index = value.month - 1 + months
    year = value.year + target_index // 12
    month = target_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def fetch_json(url: str, timeout: int) -> tuple[dict[str, Any], dict[str, str | None], bytes]:
    error: Exception | None = None
    for attempt in range(3):
        try:
            return fetch_json_once(url, timeout)
        except Exception as exc:
            error = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError("official JSON fetch failed after three attempts") from error


def fetch_json_once(url: str, timeout: int) -> tuple[dict[str, Any], dict[str, str | None], bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": "NatureWxLab-climate-outlook-navi/1.0"})
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        raw = response.read()
        headers = {
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_length": response.headers.get("Content-Length"),
        }
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return payload, headers, raw


def descendants(area: dict[str, Any], level: str, codes: list[str]) -> list[str]:
    levels = ["centers", "offices", "class10s", "class15s"]
    start = levels.index(level)
    current = list(codes)
    for current_level in levels[start:-1]:
        next_codes: list[str] = []
        table = area[current_level]
        for code in current:
            node = table.get(code)
            if not node or not isinstance(node.get("children"), list):
                raise ValueError(f"Cannot expand {current_level}:{code}")
            next_codes.extend(str(child) for child in node["children"])
        current = next_codes
    return current


def build_recipes(area: dict[str, Any]) -> dict[str, list[str]]:
    d = lambda level, *codes: descendants(area, level, list(codes))
    recipes = {
        "010100": d("centers", "010100") + ["hoppo"],
        "010101": d("offices", "012000", "016000") + d("class10s", "017020") + ["011011", "011013"],
        "010102": d("offices", "013000") + ["011012"],
        "010103": d("offices", "014030", "014100", "015000") + d("class10s", "017010") + ["hoppo"],
        "010200": d("centers", "010200"),
        "010201": d("offices", "050000", "060000") + d("class10s", "020010", "070030"),
        "010202": d("offices", "030000", "040000") + d("class10s", "020020", "020030", "070010", "070020"),
        "010203": d("offices", "020000", "030000", "050000"),
        "010204": d("offices", "040000", "060000", "070000"),
        "010300": d("centers", "010300"),
        "010400": d("centers", "010400"),
        "010500": d("centers", "010500"),
        "010600": d("centers", "010600"),
        "010601": d("offices", "270000", "290000", "300000") + d("class10s", "250010", "260010", "280010"),
        "010602": d("class10s", "250020", "260020", "280020"),
        "010700": d("centers", "010700"),
        "010701": d("offices", "310000", "320000"),
        "010702": d("offices", "330000", "340000"),
        "010800": d("centers", "010800"),
        "010900": d("centers", "010900"),
        "011000": d("offices", "450000", "460100", "460040"),
        "011001": d("offices", "450000", "460100"),
        "011002": d("offices", "460040"),
        "011100": d("centers", "011100"),
    }
    return {code: list(dict.fromkeys(values)) for code, values in recipes.items()}


def normalize_triplet(value: Any, context: str) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError(f"Invalid probability triplet at {context}: {value!r}")
    try:
        numbers = [int(item) for item in value]
    except (TypeError, ValueError) as error:
        raise ValueError(f"Non-integer probability at {context}: {value!r}") from error
    if any(number < 0 or number > 100 for number in numbers) or sum(numbers) != 100:
        raise ValueError(f"Invalid probability sum/range at {context}: {numbers!r}")
    return numbers


def term_periods(product: str, payload: dict[str, Any]) -> list[dict[str, str]]:
    target = parse_datetime(payload["targetDatetime"])
    if product in ("WARM", "COLD"):
        if product == "WARM":
            summer = target.replace(month=6, day=1)
            rainy = target.replace(month=6, day=1)
            spans = [(summer, add_months(summer, 3) - timedelta(days=1)),
                     (rainy, add_months(rainy, 2) - timedelta(days=1))]
        else:
            winter = target.replace(month=12, day=1)
            spans = [(winter, add_months(winter, 3) - timedelta(days=1))]
        return [{"id": str(index), "label": TERM_LABELS[product][index],
                 "start": start.isoformat(), "end": end.replace(hour=23, minute=59, second=59).isoformat()}
                for index, (start, end) in enumerate(spans)]
    time_defines = [parse_datetime(value) for value in payload["timeDefines"]]
    if len(time_defines) != 3 or time_defines != sorted(time_defines):
        raise ValueError(f"{product} timeDefines must contain three ascending datetimes")
    if target != time_defines[0]:
        raise ValueError(f"{product} targetDatetime must equal timeDefines[0]")
    if product == "P1M":
        spans = [
            (target, add_months(target, 1) - timedelta(days=1)),
            (time_defines[0], time_defines[0] + timedelta(days=6)),
            (time_defines[1], time_defines[1] + timedelta(days=6)),
            (time_defines[2], time_defines[2] + timedelta(days=13)),
        ]
    else:
        spans = [(target, add_months(target, 3) - timedelta(days=1))]
        spans.extend((value, add_months(value, 1) - timedelta(days=1)) for value in time_defines)
    return [
        {
            "id": str(index),
            "label": TERM_LABELS[product][index],
            "start": start.isoformat(),
            "end": end.replace(hour=23, minute=59, second=59).isoformat(),
        }
        for index, (start, end) in enumerate(spans)
    ]


def build_element(
    product: str,
    element: str,
    table: dict[str, Any],
    periods: list[dict[str, str]],
    recipes: dict[str, list[str]],
) -> dict[str, Any]:
    supported_term_indices = SUPPORTED_TERMS[product][element]
    unknown_codes = set(table) - set(recipes)
    if unknown_codes:
        raise ValueError(f"{product}.{element} contains unknown forecast region codes: {sorted(unknown_codes)}")

    normalized_slots: dict[str, list[Any]] = {}
    for code, slots in table.items():
        if not isinstance(slots, list) or len(slots) > 4:
            raise ValueError(f"{product}.{element}.{code} must contain 0..4 slots")
        normalized_slots[code] = [
            normalize_triplet(
                slots[term_index] if term_index < len(slots) else None,
                f"{product}.{element}.{code}[{term_index}]",
            )
            for term_index in range(4)
        ]

    unsupported_term_indices = set(range(4)) - set(supported_term_indices)
    for code, slots in normalized_slots.items():
        unexpected = [term_index for term_index in unsupported_term_indices if slots[term_index] is not None]
        if unexpected:
            raise ValueError(
                f"{product}.{element}.{code} contains data in unsupported terms: {unexpected}"
            )

    if not supported_term_indices:
        return {
            "status": "unavailable",
            "supported": False,
            "unavailable_reason": "not_supported_by_product",
            "terms": [],
        }

    terms: list[dict[str, Any]] = []
    available_term_count = 0
    for term_index in supported_term_indices:
        resolved: dict[str, Any] = {}
        non_null_regions = 0
        for forecast_code in FORECAST_ORDER:
            triplet = normalized_slots.get(forecast_code, [None] * 4)[term_index]
            if triplet is None:
                continue
            non_null_regions += 1
            for class15_code in recipes[forecast_code]:
                if class15_code in resolved:
                    raise ValueError(
                        f"{product}.{element} term {term_index} maps class15 {class15_code} more than once"
                    )
                resolved[class15_code] = {
                    "forecast_region_code": forecast_code,
                    "forecast_region_name": REGION_NAMES[forecast_code],
                    "probabilities": triplet,
                }
                if product == "WARM" and term_index == 1 and forecast_code in ("011002", "011100"):
                    start = parse_datetime(periods[term_index]["start"]).replace(month=5)
                    resolved[class15_code].update(start=start.isoformat(), end=(add_months(start, 2) - timedelta(seconds=1)).isoformat())
        # JMA publishes temperature, precipitation and sunshine as nationwide
        # class15 coverage.  Snowfall is different: during the issue season the
        # official map colors only the forecast regions for which snowfall
        # probabilities are supplied, so partial class15 coverage is valid.
        partial = element == "snowfall" or (product == "WARM" and element == "precipitation" and term_index == 1)
        if not partial and resolved and len(resolved) != 376:
            raise ValueError(
                f"{product}.{element} term {term_index} resolves {len(resolved)} class15 codes, expected 376"
            )
        if resolved:
            available_term_count += 1
        terms.append({
            **periods[term_index],
            "source_non_null_region_count": non_null_regions,
            "resolved_class15_count": len(resolved),
            "regions": resolved,
        })

    if element != "snowfall" and available_term_count != len(supported_term_indices):
        raise ValueError(
            f"{product}.{element} has {available_term_count} available terms, expected {len(supported_term_indices)}"
        )
    if element == "snowfall" and available_term_count not in (0, len(supported_term_indices)):
        raise ValueError(f"{product}.snowfall is only partially available")

    available = available_term_count == len(supported_term_indices)
    return {
        "status": "available" if available else "unavailable",
        "supported": True,
        "unavailable_reason": None if available else "seasonal_not_issued",
        "terms": terms,
    }


def validate_product(product: str, payload: dict[str, Any], recipes: dict[str, list[str]]) -> dict[str, Any]:
    required = {
        *ELEMENTS,
        "reportDatetime", "targetDatetime", "targetDuration", "timeDefines", "durationType",
    }
    if product in ("WARM", "COLD"):
        required.discard("timeDefines")
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"{product} missing fields: {sorted(missing)}")
    expected_duration = {"WARM": "P6M", "COLD": "P5M"}.get(product, product)
    if payload["targetDuration"] != expected_duration:
        raise ValueError(f"Unexpected targetDuration for {product}: {payload['targetDuration']}")
    parse_datetime(payload["reportDatetime"])
    periods = term_periods(product, payload)
    elements: dict[str, Any] = {}
    for element in ELEMENTS:
        table = payload[element]
        if not isinstance(table, dict):
            raise ValueError(f"{product}.{element} must be an object")
        elements[element] = build_element(product, element, table, periods, recipes)

    end = parse_datetime(periods[0]["end"])
    report = parse_datetime(payload["reportDatetime"])
    now = datetime.now(timezone.utc).astimezone(end.tzinfo)
    maximum_report_age_days = 9 if product == "P1M" else 45 if product == "P3M" else 366
    report_age_days = (now - report).total_seconds() / 86_400
    status = "available" if now <= end and report_age_days <= maximum_report_age_days else "stale"
    return {
        "report_datetime": payload["reportDatetime"],
        "target_datetime": payload["targetDatetime"],
        "target_duration": payload["targetDuration"],
        "duration_type": payload["durationType"],
        "time_defines": payload.get("timeDefines") or [],
        "status": status,
        "freshness": {
            "method": "target-period-and-conservative-report-age",
            "maximum_report_age_days": maximum_report_age_days,
            "report_age_days_at_generation": round(report_age_days, 3),
        },
        "elements": elements,
    }


def write_json(path: Path, payload: object) -> bytes:
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def atomic_json(path: Path, payload: object) -> bytes:
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    if path.is_file() and path.read_bytes() == raw:
        return raw
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return raw


def dataset_generated_at(path: Path) -> str:
    try:
        return str(json.loads((path / "latest.json").read_text(encoding="utf-8"))["generated_at"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return ""


def prune_datasets(
    output_root: Path,
    active_id: str,
    keep_count: int,
    preferred_previous_ids: tuple[str, ...] = (),
) -> list[str]:
    if not 1 <= keep_count <= 5:
        raise ValueError("--keep-datasets must be between 1 and 5")
    datasets_root = output_root / "datasets"
    candidates = [
        path for path in datasets_root.iterdir()
        if path.is_dir() and not path.is_symlink()
    ]
    active = datasets_root / active_id
    if active not in candidates:
        raise ValueError("active dataset is missing after promotion")
    by_name = {path.name: path for path in candidates}
    preferred = [by_name[name] for name in preferred_previous_ids if name in by_name and name != active_id]
    remaining = sorted(
        (path for path in candidates if path != active and path not in preferred),
        key=lambda path: (dataset_generated_at(path), path.name),
        reverse=True,
    )
    previous = [*preferred, *remaining]
    keep = {active, *previous[: max(0, keep_count - 1)]}
    removed: list[str] = []
    for path in candidates:
        if path in keep:
            continue
        shutil.rmtree(path)
        removed.append(path.name)
    return sorted(removed)


def sanitize_position(position: object) -> list[float]:
    if not isinstance(position, list) or len(position) < 2:
        raise ValueError("season boundary position must contain longitude and latitude")
    lon, lat = position[:2]
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        raise ValueError("season boundary coordinates must be numeric")
    lon = float(lon)
    lat = float(lat)
    if not math.isfinite(lon) or not math.isfinite(lat) or not (100 <= lon <= 180) or not (0 <= lat <= 60):
        raise ValueError("season boundary coordinate is outside the Japan-region safety bounds")
    return [lon, lat]


def sanitize_regions(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized_features: list[dict[str, Any]] = []
    for feature in payload["features"]:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError("season boundary entry must be a GeoJSON Feature")
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or geometry.get("type") != "MultiPolygon":
            raise ValueError("season boundary geometry must be MultiPolygon")
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list) or not coordinates:
            raise ValueError("season boundary MultiPolygon is empty")
        sanitized_coordinates: list[list[list[list[float]]]] = []
        for polygon in coordinates:
            if not isinstance(polygon, list) or not polygon:
                raise ValueError("season boundary polygon is empty")
            sanitized_polygon: list[list[list[float]]] = []
            for ring in polygon:
                if not isinstance(ring, list) or len(ring) < 4:
                    raise ValueError("season boundary ring must contain at least four positions")
                sanitized_ring = [sanitize_position(position) for position in ring]
                if sanitized_ring[0] != sanitized_ring[-1]:
                    raise ValueError("season boundary ring is not closed")
                sanitized_polygon.append(sanitized_ring)
            sanitized_coordinates.append(sanitized_polygon)
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            raise ValueError("season boundary properties must be an object")
        code = properties.get("code")
        name = properties.get("name")
        if not isinstance(code, str) or not code or not isinstance(name, str) or not name or len(name) > 80:
            raise ValueError("season boundary code/name is invalid")
        sanitized_features.append({
            "type": "Feature",
            "properties": {"code": code, "name": name},
            "geometry": {"type": "MultiPolygon", "coordinates": sanitized_coordinates},
        })
    return {"type": "FeatureCollection", "features": sanitized_features}


def file_entry(root: Path, path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"path": path.relative_to(root).as_posix(), "bytes": len(raw), "sha256": sha256_bytes(raw)}


def archive_products(root: Path, products: dict[str, Any], regions: dict[str, Any],
                     sources: dict[str, Any], *, import_history: bool = False) -> dict[str, Any]:
    """Keep immutable issue/revision files for a rolling calendar year."""
    index_path = root / "history/index.json"
    index = json.loads(index_path.read_text()) if index_path.is_file() else {"schema_version": 1, "retention_months": 12, "editions": []}
    entries = {entry["id"]: entry for entry in index["editions"]}
    cutoff = add_months(datetime.now(timezone.utc), -12)

    def retain(bundle: dict[str, Any], geometry: dict[str, Any], provenance: dict[str, Any]) -> None:
        geometry_raw = (json.dumps(geometry, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        geometry_path = root / f"history/boundaries/{sha256_bytes(geometry_raw)[:16]}.geojson"
        if not geometry_path.exists():
            atomic_json(geometry_path, geometry)
        boundary_entry = file_entry(root, geometry_path)
        for name, product in bundle.items():
            if name not in SUPPORTED_TERMS or parse_datetime(product["report_datetime"]) < cutoff:
                continue
            source = provenance.get(name, {})
            stable = json.loads(json.dumps(product))
            stable["status"] = "available"
            stable.get("freshness", {}).pop("report_age_days_at_generation", None)
            source_hash = source.get("source_sha256") or sha256_bytes(json.dumps(stable, sort_keys=True).encode())
            edition_id = f"{name}-{parse_datetime(product['report_datetime']).strftime('%Y%m%dT%H%M')}-{source_hash[:12]}"
            if edition_id in entries:
                continue
            path = root / f"history/issues/{edition_id}.json"
            atomic_json(path, {"schema_version": 1, "product_id": name, "source_url": source.get("source_url", PRODUCT_URLS.get(name, PRODUCT_URLS["P6M"])),
                              "source_sha256": source_hash, "product": stable})
            entries[edition_id] = {"id": edition_id, "product": name, "report_datetime": product["report_datetime"],
                                   "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                                   "target_datetime": product["target_datetime"], **file_entry(root, path), "regions": boundary_entry}

    if import_history:
        repo = Path(__file__).resolve().parents[1]
        commits = subprocess.check_output(["git", "log", f"--since={cutoff.date().isoformat()}", "--format=%H", "--", "data/season/manifest.json"], cwd=repo, text=True).splitlines()
        for commit in reversed(commits):
            def historical(path: str) -> Any:
                return json.loads(subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=repo))
            manifest = historical("data/season/manifest.json")
            latest = historical("data/season/" + manifest["files"]["latest"]["path"])
            if latest.get("schema_version") != 2:
                continue
            retain(latest["products"], historical("data/season/" + manifest["files"]["regions"]["path"]), manifest["products"])
    # Capture the existing public issue before replacing it, even on first migration.
    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        old = json.loads(manifest_path.read_text())
        old_latest = json.loads((root / old["files"]["latest"]["path"]).read_text())
        retain(old_latest["products"], json.loads((root / old["files"]["regions"]["path"]).read_text()), old["products"])
    retain(products, regions, sources)
    retained = sorted((entry for entry in entries.values() if parse_datetime(entry["report_datetime"]) >= cutoff),
                      key=lambda entry: (entry["report_datetime"], entry.get("recorded_at", ""), entry["id"]), reverse=True)
    atomic_json(index_path, {"schema_version": 1, "retention_months": 12, "editions": retained})
    referenced = {"history/index.json"} | {entry["path"] for entry in retained} | {entry["regions"]["path"] for entry in retained}
    for path in (root / "history").rglob("*"):
        if path.is_file() and path.relative_to(root).as_posix() not in referenced:
            path.unlink()
    return file_entry(root, index_path)


def verified_json(root: Path, entry: dict[str, Any]) -> dict[str, Any]:
    relative = Path(entry["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe seasonal asset path")
    path = root / relative
    if path.is_symlink() or root not in path.resolve().parents:
        raise ValueError("seasonal asset escapes its root")
    raw = path.read_bytes()
    if len(raw) != entry["bytes"] or sha256_bytes(raw) != entry["sha256"]:
        raise ValueError("seasonal asset checksum mismatch")
    return json.loads(raw)


def validate_reference(reference: dict[str, Any]) -> dict[str, Any]:
    if reference.get("schema_version") != 1:
        raise ValueError("collection reference schema mismatch")
    regions = sanitize_regions(reference["regions"])
    codes = {feature["properties"]["code"] for feature in regions["features"]}
    if len(regions["features"]) != 385 or len(codes) != 376 or "hoppo" not in codes:
        raise ValueError("collection reference boundary coverage mismatch")
    recipes = reference["recipes"]
    if not isinstance(recipes, dict) or set(recipes) != set(FORECAST_ORDER):
        raise ValueError("collection reference recipe set mismatch")
    for recipe in recipes.values():
        if not isinstance(recipe, list) or len(recipe) != len(set(recipe)) or not set(recipe) <= codes:
            raise ValueError("collection reference recipe codes mismatch")
    major = ("010100", "010200", "010300", "010400", "010500", "010600", "010700", "010800", "010900", "011000", "011100")
    if set().union(*(set(recipes[code]) for code in major)) != codes:
        raise ValueError("collection reference major coverage mismatch")
    for key, url in (("boundaries", REGIONS_URL), ("area_hierarchy", AREA_URL)):
        source = reference[key]
        digest = source.get("source_sha256", "")
        if source.get("source_url") != url or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("collection reference provenance mismatch")
    return {**reference, "regions": regions}


def load_reference(root: Path, previous: dict[str, Any] | None, timeout: int,
                   errors: dict[str, str]) -> dict[str, Any]:
    try:
        regions, region_headers, region_raw = fetch_json(REGIONS_URL, timeout)
        area, area_headers, area_raw = fetch_json(AREA_URL, timeout)
        codes = {str(feature.get("properties", {}).get("code")) for feature in regions.get("features", [])}
        if set(area.get("class15s", {})) != codes - {"hoppo"}:
            raise ValueError("official area and boundary code sets differ")
        reference = validate_reference({
            "schema_version": 1, "regions": sanitize_regions(regions), "recipes": build_recipes(area),
            "boundaries": {"source_url": REGIONS_URL, "source_sha256": sha256_bytes(region_raw), "etag": region_headers.get("etag")},
            "area_hierarchy": {"source_url": AREA_URL, "source_sha256": sha256_bytes(area_raw), "etag": area_headers.get("etag")},
        })
    except Exception:
        errors["reference"] = "official_reference_unavailable"
        entry = (previous or {}).get("files", {}).get("reference")
        if entry is None:
            raise ValueError("no verified collection reference is available")
        reference = validate_reference(verified_json(root, entry))
    atomic_json(root / REFERENCE_FILE, reference)
    return reference


def validate_cached_product(name: str, product: dict[str, Any], codes: set[str]) -> dict[str, Any]:
    """Validate last-known-good content before mixing it with a new product."""
    value = deepcopy(product)
    parse_datetime(value["report_datetime"])
    parse_datetime(value["target_datetime"])
    if value["target_duration"] != {"WARM": "P6M", "COLD": "P5M"}.get(name, name):
        raise ValueError("cached product duration mismatch")
    if set(value["elements"]) != set(ELEMENTS):
        raise ValueError("cached product elements mismatch")
    for element, indices in SUPPORTED_TERMS[name].items():
        payload = value["elements"][element]
        terms = payload["terms"]
        if payload["supported"] is not bool(indices) or [term["id"] for term in terms] != [str(index) for index in indices]:
            raise ValueError("cached product term contract mismatch")
        for term in terms:
            resolved = term["regions"]
            if not isinstance(resolved, dict) or not set(resolved) <= codes or term["resolved_class15_count"] != len(resolved):
                raise ValueError("cached product region coverage mismatch")
            partial = element == "snowfall" or (name == "WARM" and element == "precipitation" and term["id"] == "1")
            if not partial and set(resolved) != codes:
                raise ValueError("cached full product region coverage mismatch")
            for region in resolved.values():
                triplet = region["probabilities"]
                if not isinstance(triplet, list) or len(triplet) != 3 or any(not isinstance(v, int) or not 0 <= v <= 100 for v in triplet) or sum(triplet) != 100:
                    raise ValueError("cached probability mismatch")
            parse_datetime(term["start"])
            parse_datetime(term["end"])
    first = value["elements"]["temperature"]["terms"][0]
    now = datetime.now(timezone.utc)
    age = (now - parse_datetime(value["report_datetime"])).total_seconds() / 86400
    limit = 9 if name == "P1M" else 45 if name == "P3M" else 366
    value["status"] = "available" if now <= parse_datetime(first["end"]) and age <= limit else "stale"
    value["freshness"]["report_age_days_at_generation"] = round(age, 3)
    return value


def collection_status(root: Path, started: str, issue_calendar: dict[str, Any] | None,
                      errors: dict[str, str], now: datetime,
                      review_checks: dict[str, str] | None = None) -> dict[str, Any]:
    from season_issue_calendar import expected_issues_since

    cutoff = max(parse_datetime(started), add_months(now, -12))
    archive_path = root / "history/index.json"
    editions = json.loads(archive_path.read_text())["editions"] if archive_path.is_file() else []
    expected = []
    if issue_calendar is not None:
        try:
            expected = expected_issues_since(cutoff, now, issue_calendar, grace_minutes=10)
        except Exception:
            errors["calendar"] = "calendar_coverage_unavailable"
    results = []
    for issue in expected:
        matches = [entry["id"] for entry in editions if entry["product"] == issue["product"] and parse_datetime(entry["report_datetime"]) == parse_datetime(issue["report_datetime"])]
        results.append({"product": issue["product"], "report_datetime": issue["report_datetime"],
                        "status": "captured" if matches else "missing", "edition_ids": sorted(matches)})
    jst = timezone(timedelta(hours=9))
    checked_through = max(parse_datetime(started), now.astimezone(jst).replace(hour=0, minute=0, second=0, microsecond=0),
                          *(parse_datetime(item["report_datetime"]) + timedelta(minutes=10) for item in expected))
    return {"schema_version": 1, "monitoring_started_at": started, "retention_months": 12,
            "coverage_start": cutoff.isoformat(timespec="seconds"),
            "checked_through": checked_through.isoformat(timespec="seconds"),
            "expected_issues": results, "missing_issue_count": sum(item["status"] == "missing" for item in results),
            "fetch_errors": dict(sorted(errors.items())),
            **({"review_checks": dict(sorted((product, when) for product, when in review_checks.items()
                                             if cutoff <= parse_datetime(when) <= now))} if review_checks else {})}


def collection_plan(status: dict[str, Any], previous: dict[str, Any] | None,
                    old_errors: dict[str, str], issue_calendar: dict[str, Any] | None,
                    now: datetime) -> dict[str, dict[str, Any]]:
    """Request only an uncollected latest issue or a previously failed source."""
    if previous is None or not {"collection", "calendar", "reference"} <= previous.get("files", {}).keys():
        return {source: {} for source in PRODUCT_URLS}
    latest = {}
    for issue in status["expected_issues"]:
        source = issue["product"] if issue["product"] in PRODUCT_URLS else "P6M"
        if source not in latest or parse_datetime(issue["report_datetime"]) > parse_datetime(latest[source]["report_datetime"]):
            latest[source] = issue
    plan = {source: {} for source in PRODUCT_URLS if source in old_errors}
    for source, issue in latest.items():
        names = ("WARM", "COLD") if source == "P6M" else (source,)
        current = [parse_datetime(previous["products"][name]["report_datetime"])
                   for name in names if name in previous["products"]]
        # An older missing edition cannot be fetched after a newer one has
        # replaced it upstream. Keep the gap visible without polling forever.
        if issue["status"] == "missing" and (not current or max(current) <= parse_datetime(issue["report_datetime"])):
            plan[source] = {"expected_report_datetime": issue["report_datetime"]}
    if issue_calendar is not None:
        from season_issue_calendar import review_issues_since
        checks = status.get("review_checks", {})
        for review in review_issues_since(status["coverage_start"], now, issue_calendar):
            when = parse_datetime(review["report_datetime"])
            if review["product"] in checks and parse_datetime(checks[review["product"]]) >= when:
                continue
            current = previous["products"].get(review["product"])
            if current and parse_datetime(current["report_datetime"]) >= when:
                continue
            if "P6M" in latest and parse_datetime(latest["P6M"]["report_datetime"]) > when:
                continue
            plan.setdefault("P6M", {}).update(review_product=review["product"], review_datetime=review["report_datetime"])
    return plan


def update(args: argparse.Namespace) -> dict[str, Any]:
    from season_issue_calendar import load_issue_calendar, validate_issue_calendar

    root = Path(args.output).resolve()
    previous = json.loads((root / "manifest.json").read_text()) if (root / "manifest.json").is_file() else None
    if previous and str(previous.get("generator_version", "")).startswith("4.") and not {"collection", "calendar", "reference"} <= previous.get("files", {}).keys():
        raise ValueError("collection controls are missing; refusing to reset monitoring")
    errors: dict[str, str] = {}
    now = datetime.now(timezone.utc)
    old_status = verified_json(root, previous["files"]["collection"]) if previous and "collection" in previous["files"] else None
    old_errors = (old_status or {}).get("fetch_errors", {})
    errors.update({source: code for source, code in old_errors.items() if source in PRODUCT_URLS})
    review_checks = deepcopy((old_status or {}).get("review_checks", {}))
    started = old_status["monitoring_started_at"] if old_status else now.isoformat(timespec="seconds")
    issue_calendar = None
    calendar_cache = None
    if previous and "calendar" in previous["files"]:
        issue_calendar = verified_json(root, previous["files"]["calendar"])
        calendar_cache = root / CALENDAR_FILE
    try:
        if issue_calendar is not None:
            try:
                validate_issue_calendar(issue_calendar, now)
            except ValueError:
                issue_calendar = None
        if getattr(args, "refresh_calendar", False) or issue_calendar is None or "calendar" in old_errors:
            issue_calendar = load_issue_calendar(cache_path=calendar_cache, now=now)
        if issue_calendar.get("acquisition_error"):
            errors["calendar"] = issue_calendar["acquisition_error"]
        atomic_json(root / CALENDAR_FILE, {key: value for key, value in issue_calendar.items() if not key.startswith("acquisition_")})
    except Exception:
        errors["calendar"] = "calendar_coverage_unavailable"
    if previous and "archive" in previous["files"]:
        verified_json(root, previous["files"]["archive"])
    status = collection_status(root, started, issue_calendar, errors, now, review_checks)
    plan = collection_plan(status, previous, old_errors, issue_calendar, now)
    if args.allow_report_regression:  # Existing explicit manual emergency override.
        plan = {source: {} for source in PRODUCT_URLS}
    calendar_entry = (previous or {}).get("files", {}).get("calendar")
    calendar_unchanged = calendar_entry is not None and file_entry(root, root / CALENDAR_FILE) == calendar_entry
    if old_status and calendar_unchanged and not errors and not old_errors and not plan and not getattr(args, "refresh_calendar", False) and not args.import_git_history:
        return {**previous, "changed": False, "removed_dataset_ids": [],
                "collection_ok": status["missing_issue_count"] == 0, "collection_status": status,
                "probe_skipped": True, "requested_sources": []}

    reference = (load_reference(root, previous, args.timeout, errors) if plan or "reference" in old_errors or previous is None
                 else validate_reference(verified_json(root, previous["files"]["reference"])))
    codes = {feature["properties"]["code"] for feature in reference["regions"]["features"]}
    products: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    if previous:
        cached = verified_json(root, previous["files"]["latest"])
        for name, product in cached["products"].items():
            products[name] = validate_cached_product(name, product, codes)
            sources[name] = deepcopy(previous["products"][name])
    fetched_at = now_iso()
    changed = False
    result = previous
    for source_name, url in PRODUCT_URLS.items():
        if source_name not in plan:
            continue
        errors.pop(source_name, None)
        try:
            payload, headers, raw = fetch_json(url, args.timeout)
            name = ("WARM" if parse_datetime(payload["reportDatetime"]).month in (2, 3, 4) else "COLD") if source_name == "P6M" else source_name
            validated = validate_product(name, payload, reference["recipes"])
            if plan[source_name].get("review_product") and name != plan[source_name]["review_product"]:
                errors[source_name] = "product_not_updated"
                continue
            expected_report = plan[source_name].get("expected_report_datetime")
            if expected_report and parse_datetime(validated["report_datetime"]) < parse_datetime(expected_report):
                errors[source_name] = "product_not_updated"
                continue
            previous_names = ("WARM", "COLD") if source_name == "P6M" else (name,)
            if not args.allow_report_regression and any(
                candidate in products and parse_datetime(validated["report_datetime"]) < parse_datetime(products[candidate]["report_datetime"])
                for candidate in previous_names
            ):
                raise ValueError("official report date regressed")
        except Exception:
            errors[source_name] = "product_fetch_or_validation_failed"
            continue
        if source_name == "P6M":
            other = "COLD" if name == "WARM" else "WARM"
            products.pop(other, None)
            sources.pop(other, None)
        products[name] = validated
        sources[name] = {"source_sha256": sha256_bytes(raw), **headers}
        if plan[source_name].get("review_product"):
            review_checks[plan[source_name]["review_product"]] = plan[source_name]["review_datetime"]
        # Checkpoint every good product BEFORE requesting the next one. A
        # different product's network/schema failure cannot discard this issue.
        atomic_json(root / COLLECTION_FILE, collection_status(root, started, issue_calendar, errors, now, review_checks))
        result = promote_snapshot(args, products, sources, reference, fetched_at,
                                  (started, issue_calendar, errors, now, review_checks))
        changed = changed or result["changed"]
    if not products or not {"P1M", "P3M"} <= products.keys():
        raise ValueError("no complete primary last-known-good product view is available")
    status = collection_status(root, started, issue_calendar, errors, now, review_checks)
    atomic_json(root / COLLECTION_FILE, status)
    result = promote_snapshot(args, products, sources, reference, fetched_at,
                              (started, issue_calendar, errors, now, review_checks))
    changed = changed or result["changed"]
    return {**result, "changed": changed, "collection_ok": not errors and status["missing_issue_count"] == 0,
            "collection_status": status, "probe_skipped": not plan, "requested_sources": list(plan)}


def promote_snapshot(args: argparse.Namespace, products: dict[str, Any],
                     sources: dict[str, Any], reference: dict[str, Any],
                     fetched_at: str, collection_context: tuple | None = None) -> dict[str, Any]:
    """Checkpoint a complete, validated merged view after each good product."""
    output_root = Path(args.output).resolve()
    manifest_path = output_root / "manifest.json"
    staging_parent = output_root / ".staging"
    previous_manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else None
    sanitized_regions = reference["regions"]
    features = sanitized_regions["features"]
    unique_codes = {feature["properties"]["code"] for feature in features}
    source_hashes = {name: source["source_sha256"] for name, source in sources.items()}
    source_headers = sources
    controls = {
        key: file_entry(output_root, output_root / name)
        for key, name in (("collection", COLLECTION_FILE), ("calendar", CALENDAR_FILE), ("reference", REFERENCE_FILE))
        if (output_root / name).is_file()
    }
    report_key = "|".join([
        GENERATOR_VERSION,
        *(products[product]["report_datetime"] for product in products),
        *(products[product]["status"] for product in products),
        *(source_hashes[product] for product in products),
        reference["boundaries"]["source_sha256"],
        reference["area_hierarchy"]["source_sha256"],
    ])
    dataset_id = hashlib.sha256(report_key.encode("utf-8")).hexdigest()[:16]
    archive_entry = archive_products(output_root, products, sanitized_regions,
        {name: {"source_url": PRODUCT_URLS.get(name, PRODUCT_URLS["P6M"]), "source_sha256": source_hashes[name]} for name in products},
        import_history=args.import_git_history)
    if collection_context is not None:
        atomic_json(output_root / COLLECTION_FILE, collection_status(output_root, *collection_context))
        controls["collection"] = file_entry(output_root, output_root / COLLECTION_FILE)
    final_dataset_dir = output_root / "datasets" / dataset_id
    if final_dataset_dir.is_dir() and manifest_path.is_file():
        current_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if current_manifest.get("dataset_id") == dataset_id:
            latest_entry = current_manifest.get("files", {}).get("latest", {})
            regions_entry = current_manifest.get("files", {}).get("regions", {})
            latest_path = output_root / str(latest_entry.get("path", ""))
            regions_path = output_root / str(regions_entry.get("path", ""))
            if (
                latest_path.is_file()
                and regions_path.is_file()
                and sha256_bytes(latest_path.read_bytes()) == latest_entry.get("sha256")
                and sha256_bytes(regions_path.read_bytes()) == regions_entry.get("sha256")
            ):
                archive_changed = current_manifest["files"].get("archive") != archive_entry or any(
                    current_manifest["files"].get(key) != value for key, value in controls.items()
                )
                current_manifest["files"].update(controls)
                current_manifest["files"]["archive"] = archive_entry
                if archive_changed:
                    atomic_json(manifest_path, current_manifest)
                current_manifest["changed"] = archive_changed
                current_manifest["removed_dataset_ids"] = prune_datasets(output_root, dataset_id, args.keep_datasets)
                return current_manifest
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix=f"{dataset_id}-", dir=staging_parent))
    try:
        regions_out = staging_dir / "regions.geojson"
        latest_out = staging_dir / "latest.json"
        regions_bytes = write_json(regions_out, sanitized_regions)
        latest_payload = {
            "schema_version": SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "generated_at": now_iso(),
            "fetched_at": fetched_at,
            "elements": ELEMENTS,
            "products": products,
            "resolution_note": "季節予報は気象庁の地域単位。1kmメッシュへ補間していません。",
        }
        latest_bytes = write_json(latest_out, latest_payload)
        if final_dataset_dir.exists():
            shutil.rmtree(staging_dir)
            regions_bytes = (final_dataset_dir / "regions.geojson").read_bytes()
            latest_bytes = (final_dataset_dir / "latest.json").read_bytes()
        else:
            final_dataset_dir.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_dir, final_dataset_dir)

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "generator_version": GENERATOR_VERSION,
            "dataset_id": dataset_id,
            "generated_at": now_iso(),
            "fetched_at": fetched_at,
            "status": "available" if all(product["status"] == "available" for product in products.values()) else "stale",
            "files": {
                **controls,
                "archive": archive_entry,
                "latest": {
                    "path": f"datasets/{dataset_id}/latest.json",
                    "bytes": len(latest_bytes),
                    "sha256": sha256_bytes(latest_bytes),
                },
                "regions": {
                    "path": f"datasets/{dataset_id}/regions.geojson",
                    "bytes": len(regions_bytes),
                    "sha256": sha256_bytes(regions_bytes),
                    "feature_count": len(features),
                    "unique_code_count": len(unique_codes),
                },
            },
            "products": {
                product: {
                    "status": products[product]["status"],
                    "report_datetime": products[product]["report_datetime"],
                    "target_datetime": products[product]["target_datetime"],
                    "elements": {
                        element: {
                            "status": products[product]["elements"][element]["status"],
                            "supported": products[product]["elements"][element]["supported"],
                            "unavailable_reason": products[product]["elements"][element]["unavailable_reason"],
                            "term_count": len(products[product]["elements"][element]["terms"]),
                            "available_term_count": sum(
                                bool(term["regions"])
                                for term in products[product]["elements"][element]["terms"]
                            ),
                        }
                        for element in ELEMENTS
                    },
                    "source_url": PRODUCT_URLS.get(product, PRODUCT_URLS["P6M"]),
                    "source_sha256": source_hashes[product],
                    "etag": source_headers[product]["etag"],
                    "last_modified": source_headers[product]["last_modified"],
                }
                for product in products
            },
            "boundaries": deepcopy(reference["boundaries"]),
            "area_hierarchy": deepcopy(reference["area_hierarchy"]),
            "validation": {
                "feature_count": len(features),
                "unique_class15_codes": len(unique_codes),
                "official_recipe_order": FORECAST_ORDER,
                "probability_triplets_sum_to_100": True,
                "resolved_class15_count_per_full_coverage_term": 376,
                "snowfall_coverage": "officially_provided_regions_only",
                "supported_terms": {
                    product: {
                        element: list(term_indices)
                        for element, term_indices in elements.items()
                    }
                    for product, elements in SUPPORTED_TERMS.items()
                },
                "duplicate_geometry_codes_preserved": len(features) - len(unique_codes),
                "dataset_retention": args.keep_datasets,
                "archive_retention_months": 12,
            },
        }
        output_root.mkdir(parents=True, exist_ok=True)
        atomic_json(manifest_path, manifest)
        manifest["changed"] = True
        previous_dataset_id = str(previous_manifest.get("dataset_id", "")) if previous_manifest else ""
        manifest["removed_dataset_ids"] = prune_datasets(
            output_root,
            dataset_id,
            args.keep_datasets,
            (previous_dataset_id,) if previous_dataset_id else (),
        )
        return manifest
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        try:
            staging_parent.rmdir()
        except OSError:
            # Keep a non-empty root only if another updater invocation owns it.
            pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/season")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--probe", action="store_true", help="compatibility alias; every normal invocation collects only pending or failed products")
    parser.add_argument("--refresh-calendar", action="store_true", help="refresh the official calendar once in the daily observation job")
    parser.add_argument("--import-git-history", action="store_true", help="import available verified forecast editions from this repository's last year")
    parser.add_argument(
        "--allow-report-regression",
        action="store_true",
        help="manual emergency override; permit an older JMA reportDatetime to replace the last-known-good dataset",
    )
    parser.add_argument(
        "--keep-datasets",
        type=int,
        default=2,
        help="retain the active dataset plus this many total recent versions (1-5; default: 2)",
    )
    return parser.parse_args()


def main() -> None:
    manifest = update(parse_args())
    print(json.dumps({
        "dataset_id": manifest["dataset_id"],
        "status": manifest["status"],
        "products": manifest["products"],
        "validation": manifest["validation"],
        "changed": manifest["changed"],
        "removed_dataset_ids": manifest["removed_dataset_ids"],
        "collection_ok": manifest.get("collection_ok", True),
        "collection_status": manifest.get("collection_status"),
        "probe_skipped": manifest.get("probe_skipped", False),
        "requested_sources": manifest.get("requested_sources", []),
    }, ensure_ascii=False, indent=2))
    if not manifest.get("collection_ok", True):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
