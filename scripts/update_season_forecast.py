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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import certifi
except ImportError:  # pragma: no cover - system CA remains the fallback
    certifi = None


SCHEMA_VERSION = 2
GENERATOR_VERSION = "2.0.0"
PRODUCT_URLS = {
    "P1M": "https://www.jma.go.jp/bosai/season/data/P1M.json",
    "P3M": "https://www.jma.go.jp/bosai/season/data/P3M.json",
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
}
ELEMENTS = {
    "temperature": {"name": "気温", "classes": ["低い", "平年並", "高い"]},
    "precipitation": {"name": "降水量", "classes": ["少ない", "平年並", "多い"]},
    "sunshine": {"name": "日照時間", "classes": ["少ない", "平年並", "多い"]},
    "snowfall": {"name": "降雪量", "classes": ["少ない", "平年並", "多い"]},
}
SUPPORTED_TERMS = {
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
        # JMA publishes temperature, precipitation and sunshine as nationwide
        # class15 coverage.  Snowfall is different: during the issue season the
        # official map colors only the forecast regions for which snowfall
        # probabilities are supplied, so partial class15 coverage is valid.
        if element != "snowfall" and resolved and len(resolved) != 376:
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
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"{product} missing fields: {sorted(missing)}")
    if payload["targetDuration"] != product:
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
    maximum_report_age_days = 9 if product == "P1M" else 45
    report_age_days = (now - report).total_seconds() / 86_400
    status = "available" if now <= end and report_age_days <= maximum_report_age_days else "stale"
    return {
        "report_datetime": payload["reportDatetime"],
        "target_datetime": payload["targetDatetime"],
        "target_duration": payload["targetDuration"],
        "duration_type": payload["durationType"],
        "time_defines": payload["timeDefines"],
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


def update(args: argparse.Namespace) -> dict[str, Any]:
    output_root = Path(args.output).resolve()
    manifest_path = output_root / "manifest.json"
    staging_parent = output_root / ".staging"
    try:
        staging_parent.rmdir()
    except OSError:
        pass
    previous_manifest = None
    if manifest_path.is_file():
        previous_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fetched_at = now_iso()
    products_raw: dict[str, dict[str, Any]] = {}
    source_headers: dict[str, Any] = {}
    source_hashes: dict[str, str] = {}
    for product, url in PRODUCT_URLS.items():
        payload, headers, raw = fetch_json(url, args.timeout)
        products_raw[product] = payload
        source_headers[product] = headers
        source_hashes[product] = sha256_bytes(raw)
    regions, regions_headers, regions_raw = fetch_json(REGIONS_URL, args.timeout)
    area, area_headers, area_raw = fetch_json(AREA_URL, args.timeout)

    features = regions.get("features")
    if regions.get("type") != "FeatureCollection" or not isinstance(features, list) or len(features) != 385:
        raise ValueError("class15s GeoJSON must contain 385 features")
    region_codes = [str(feature.get("properties", {}).get("code")) for feature in features]
    unique_codes = set(region_codes)
    if len(unique_codes) != 376 or "hoppo" not in unique_codes:
        raise ValueError("class15s GeoJSON must contain 376 unique codes including hoppo")
    if set(area.get("class15s", {})) != unique_codes - {"hoppo"}:
        raise ValueError("area.class15s and class15s GeoJSON code sets do not match")
    sanitized_regions = sanitize_regions(regions)
    features = sanitized_regions["features"]

    recipes = build_recipes(area)
    major_union = set().union(*(set(recipes[code]) for code in [
        "010100", "010200", "010300", "010400", "010500", "010600",
        "010700", "010800", "010900", "011000", "011100",
    ]))
    if major_union != unique_codes:
        raise ValueError(f"Major region recipes cover {len(major_union)} codes, expected 376")

    products = {product: validate_product(product, payload, recipes) for product, payload in products_raw.items()}
    if previous_manifest and not args.allow_report_regression:
        for product in ("P1M", "P3M"):
            previous_report = previous_manifest.get("products", {}).get(product, {}).get("report_datetime")
            if previous_report and parse_datetime(products[product]["report_datetime"]) < parse_datetime(previous_report):
                raise ValueError(f"{product} reportDatetime regressed; refusing to replace the last-known-good dataset")
    report_key = "|".join([
        GENERATOR_VERSION,
        *(products[product]["report_datetime"] for product in ("P1M", "P3M")),
        *(products[product]["status"] for product in ("P1M", "P3M")),
        *(source_hashes[product] for product in ("P1M", "P3M")),
        sha256_bytes(regions_raw),
        sha256_bytes(area_raw),
    ])
    dataset_id = hashlib.sha256(report_key.encode("utf-8")).hexdigest()[:16]
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
                current_manifest["changed"] = False
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
                    "source_url": PRODUCT_URLS[product],
                    "source_sha256": source_hashes[product],
                    "etag": source_headers[product]["etag"],
                    "last_modified": source_headers[product]["last_modified"],
                }
                for product in PRODUCT_URLS
            },
            "boundaries": {
                "source_url": REGIONS_URL,
                "source_sha256": sha256_bytes(regions_raw),
                "etag": regions_headers["etag"],
            },
            "area_hierarchy": {
                "source_url": AREA_URL,
                "source_sha256": sha256_bytes(area_raw),
                "etag": area_headers["etag"],
            },
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
            },
        }
        output_root.mkdir(parents=True, exist_ok=True)
        manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        with tempfile.NamedTemporaryFile("wb", dir=output_root, delete=False) as handle:
            handle.write(manifest_raw)
            temp_manifest = Path(handle.name)
        os.replace(temp_manifest, manifest_path)
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
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
