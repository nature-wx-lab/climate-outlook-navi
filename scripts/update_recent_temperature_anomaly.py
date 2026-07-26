#!/usr/bin/env python3
"""Build the recent nationwide temperature-anomaly point dataset.

The public derivative combines two official JMA sources:

- 1991-2020 daily normals from the surface-station normal archive.
- Daily mean temperatures from the observation download service for bootstrap,
  then the daily nationwide surface-station table for incremental updates.

Only compact station metadata, daily normals, and recent observations are
published. Source archives and responses remain transient.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import io
import json
import os
import re
import ssl
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any

import certifi


ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "data" / "recent-temperature"
MANIFEST_PATH = DATA_ROOT / "manifest.json"
LATEST_PATH = DATA_ROOT / "latest.json"

NORMAL_ARCHIVE_URL = "https://www.data.jma.go.jp/stats/mdrr/normal/2020/data/normal_surface.zip"
NORMAL_INDEX_URL = "https://www.data.jma.go.jp/stats/mdrr/normal/index.html"
OBSDL_INDEX_URL = "https://www.data.jma.go.jp/risk/obsdl/index.php"
OBSDL_TABLE_URL = "https://www.data.jma.go.jp/risk/obsdl/show/table"
SYNOPDAY_URL = "https://www.data.jma.go.jp/stats/mdrr/synopday/data{page}.html"
LATEST_PERIOD_TABLE_URL = "https://www.data.jma.go.jp/stats/mdrr/tenkou/alltable/tem00.csv"

USER_AGENT = "NatureWxLab Climate Outlook Navi public dataset builder"
NORMAL_ELEMENT = "0500"
OBSERVATION_ELEMENT = "201"
NORMAL_DAYS = tuple(
    f"{month:02d}-{day:02d}"
    for month in range(1, 13)
    for day in range(1, calendar.monthrange(2020, month)[1] + 1)
)
NORMAL_DAY_INDEX = {value: index for index, value in enumerate(NORMAL_DAYS)}
EXCLUDED_STATION_IDS = {
    "47639": "富士山（気象庁の天候の状況から除外）",
    "47821": "阿蘇山（観測終了）",
    "47991": "南鳥島（気象庁の天候の状況から除外）",
    "89532": "昭和（南極、気象庁の天候の状況から除外）",
}
ACCEPTED_QUALITY_CODES = {5, 8}
MINIMUM_VALID_RATIO = 0.8
MINIMUM_ACTIVE_STATIONS = 145
MAX_INCREMENTAL_GAP_DAYS = 29


@dataclass(frozen=True)
class StationNormal:
    station_id: str
    name: str
    latitude: float
    longitude: float
    elevation_m: float
    normal_tenths: tuple[int | None, ...]
    normal_5day_tenths: tuple[int | None, ...]


class TableParser(HTMLParser):
    """Extract text cells from HTML tables without third-party HTML parsers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(normalize_text("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("\u3000", " ")).strip()


def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def request_bytes(
    url: str,
    *,
    opener: urllib.request.OpenerDirector | None = None,
    data: bytes | None = None,
    timeout: int = 180,
) -> bytes:
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en;q=0.8",
        },
    )
    target = opener or urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl_context())
    )
    with target.open(request, timeout=timeout) as response:
        return response.read()


def as_tenths(value: str) -> int | None:
    text = normalize_text(value).replace("+", "")
    if not text or text in {"--", "///", "×"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return int((Decimal(match.group(0)) * 10).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def iso_dates(start: date, end: date) -> list[str]:
    return [(start + timedelta(days=offset)).isoformat() for offset in range((end - start).days + 1)]


def station_index(archive: zipfile.ZipFile) -> list[dict[str, str]]:
    raw = archive.read("normal_surface/surface_station_index.csv").decode("cp932")
    rows = list(csv.reader(io.StringIO(raw)))
    stations: list[dict[str, str]] = []
    for row in rows[2:]:
        if len(row) < 9:
            continue
        station_id = row[0].strip()
        if station_id in EXCLUDED_STATION_IDS:
            continue
        stations.append({
            "station_id": station_id,
            "name": row[1].strip().replace("\u3000", ""),
            "latitude": str(float(row[4]) + float(row[5]) / 60),
            "longitude": str(float(row[6]) + float(row[7]) / 60),
            "elevation_m": row[8].strip(),
        })
    return stations


def parse_normal_series(
    archive: zipfile.ZipFile,
    member: str,
    station_id: str,
    normal_type: str,
) -> tuple[int | None, ...]:
    rows = csv.reader(io.StringIO(archive.read(member).decode("cp932")))
    values: list[int | None] = [None] * len(NORMAL_DAYS)
    for row in rows:
        if (
            len(row) < 9
            or row[0].strip() != normal_type
            or row[2].strip() != NORMAL_ELEMENT
        ):
            continue
        month = int(row[6])
        for day in range(1, calendar.monthrange(2020, month)[1] + 1):
            value_index = 7 + (day - 1) * 2
            quality_index = value_index + 1
            if quality_index >= len(row):
                continue
            quality = int(row[quality_index].strip() or "0")
            if quality <= 0:
                continue
            values[NORMAL_DAY_INDEX[f"{month:02d}-{day:02d}"]] = int(row[value_index].strip())
    if any(value is None for value in values):
        missing = sum(value is None for value in values)
        raise ValueError(f"temperature normal is incomplete for {station_id}: {member} ({missing} days)")
    return tuple(values)


def parse_station_normal(archive: zipfile.ZipFile, metadata: dict[str, str]) -> StationNormal:
    station_id = metadata["station_id"]
    daily = parse_normal_series(
        archive,
        f"normal_surface/daily/nml_sfc_d_{station_id}.csv",
        station_id,
        "15",
    )
    five_day = parse_normal_series(
        archive,
        f"normal_surface/daily_5day/nml_sfc_d5d_{station_id}.csv",
        station_id,
        "17",
    )
    return StationNormal(
        station_id=station_id,
        name=metadata["name"],
        latitude=round(float(metadata["latitude"]), 5),
        longitude=round(float(metadata["longitude"]), 5),
        elevation_m=round(float(metadata["elevation_m"]), 1),
        normal_tenths=daily,
        normal_5day_tenths=five_day,
    )


def load_normals(archive_path: Path | None) -> list[StationNormal]:
    raw = archive_path.read_bytes() if archive_path else request_bytes(NORMAL_ARCHIVE_URL)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        normals = [parse_station_normal(archive, item) for item in station_index(archive)]
    if len(normals) < MINIMUM_ACTIVE_STATIONS:
        raise ValueError(f"surface-station normal coverage is too small: {len(normals)}")
    return normals


def obsdl_opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        urllib.request.HTTPSHandler(context=ssl_context()),
    )
    request_bytes(OBSDL_INDEX_URL, opener=opener, timeout=30)
    return opener


def observation_payload(station_id: str, start: date, end: date) -> bytes:
    values = {
        "stationNumList": json.dumps([f"s{station_id}"], ensure_ascii=False),
        "aggrgPeriod": "1",
        "elementNumList": json.dumps([[OBSERVATION_ELEMENT, ""]], ensure_ascii=False),
        "interAnnualType": "1",
        "ymdList": json.dumps([
            str(start.year), str(end.year), str(start.month), str(end.month),
            str(start.day), str(end.day),
        ]),
        "optionNumList": "[]",
        "downloadFlag": "true",
        "rmkFlag": "1",
        "disconnectFlag": "1",
        "youbiFlag": "0",
        "fukenFlag": "0",
        "kijiFlag": "0",
        "csvFlag": "1",
        "jikantaiFlag": "0",
        "jikantaiList": "[1,24]",
        "ymdLiteral": "1",
    }
    return urllib.parse.urlencode(values).encode("ascii")


def parse_obsdl(raw: bytes, expected_dates: list[str]) -> list[int | None]:
    rows = csv.reader(io.StringIO(raw.decode("cp932", errors="replace")))
    values: dict[str, int | None] = {}
    for row in rows:
        if len(row) < 3 or re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", row[0].strip()) is None:
            continue
        year, month, day = (int(value) for value in row[0].split("/"))
        current = date(year, month, day).isoformat()
        quality = int(row[2].strip() or "0")
        values[current] = as_tenths(row[1]) if quality in ACCEPTED_QUALITY_CODES else None
    return [values.get(current) for current in expected_dates]


def fetch_station_observations(
    opener: urllib.request.OpenerDirector,
    station: StationNormal,
    start: date,
    end: date,
    expected_dates: list[str],
) -> list[int | None]:
    error: Exception | None = None
    for attempt in range(3):
        try:
            raw = request_bytes(
                OBSDL_TABLE_URL,
                opener=opener,
                data=observation_payload(station.station_id, start, end),
            )
            values = parse_obsdl(raw, expected_dates)
            if len(values) != len(expected_dates):
                raise ValueError("observation date count mismatch")
            return values
        except Exception as exc:  # network retry boundary
            error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch observations for {station.station_id}: {error}")


def bootstrap_dataset(
    normals: list[StationNormal],
    start: date,
    end: date,
    request_delay: float,
) -> dict[str, Any]:
    dates = iso_dates(start, end)
    opener = obsdl_opener()
    stations: list[dict[str, Any]] = []
    for index, station in enumerate(normals, 1):
        observed = fetch_station_observations(opener, station, start, end, dates)
        valid = sum(value is not None for value in observed)
        if valid / len(dates) >= MINIMUM_VALID_RATIO:
            stations.append({
                "station_id": station.station_id,
                "name": station.name,
                "lat": station.latitude,
                "lon": station.longitude,
                "elevation_m": station.elevation_m,
                "normal_tenths": list(station.normal_tenths),
                "normal_5day_tenths": list(station.normal_5day_tenths),
                "observed_tenths": observed,
            })
        print(
            f"[{index:03d}/{len(normals):03d}] {station.station_id} {station.name} "
            f"valid={valid}/{len(dates)}",
            flush=True,
        )
        if request_delay > 0 and index < len(normals):
            time.sleep(request_delay)
    if len(stations) < MINIMUM_ACTIVE_STATIONS:
        raise ValueError(f"active station coverage is too small: {len(stations)}")
    return {
        "schema_version": 1,
        "dataset_id": "",
        "generated_at": "",
        "element": {"code": "201", "name": "日平均気温", "unit": "℃", "storage_scale": 10},
        "normal": {
            "period": "1991-2020",
            "label": "気象庁 2020年平年値（第5版）",
            "source_url": NORMAL_INDEX_URL,
        },
        "observation": {
            "source": "気象庁 過去の気象データ・ダウンロード／毎日の全国データ一覧表",
            "source_url": OBSDL_INDEX_URL,
            "incremental_source_url": SYNOPDAY_URL.format(page=2),
        },
        "normal_days": list(NORMAL_DAYS),
        "dates": dates,
        "stations": stations,
        "validation": {
            "minimum_valid_ratio": MINIMUM_VALID_RATIO,
            "accepted_obsdl_quality_codes": sorted(ACCEPTED_QUALITY_CODES),
            "excluded_stations": EXCLUDED_STATION_IDS,
        },
    }


def parse_synopday(raw: bytes) -> tuple[date, dict[str, int | None]]:
    html = raw.decode("utf-8", errors="replace")
    match = re.search(r"日別値:(\d{4})年(\d{2})月(\d{2})日", html)
    if not match:
        raise ValueError("synopday observation date is missing")
    observed_date = date(*map(int, match.groups()))
    parser = TableParser()
    parser.feed(html)
    values: dict[str, int | None] = {}
    for row in parser.rows:
        if len(row) < 6 or row[0] in {"地点", ""}:
            continue
        value = as_tenths(row[3])
        if value is not None or row[3] in {"--", "///"}:
            values[row[0]] = value
    return observed_date, values


def incremental_update(dataset: dict[str, Any], target_end: date, retention_days: int) -> bool:
    existing_end = date.fromisoformat(dataset["dates"][-1])
    if target_end <= existing_end:
        return False
    gap = (target_end - existing_end).days
    if gap > MAX_INCREMENTAL_GAP_DAYS:
        raise ValueError(
            f"incremental gap is {gap} days; rerun with --bootstrap "
            f"(maximum {MAX_INCREMENTAL_GAP_DAYS})"
        )
    today_jst = target_end + timedelta(days=1)
    stations_by_name = {station["name"]: station for station in dataset["stations"]}
    for current in (existing_end + timedelta(days=offset) for offset in range(1, gap + 1)):
        page = (today_jst - current).days + 1
        page_date, values = parse_synopday(request_bytes(SYNOPDAY_URL.format(page=page)))
        if page_date != current:
            raise ValueError(f"synopday page {page} returned {page_date}, expected {current}")
        matches = set(stations_by_name) & set(values)
        if len(matches) < MINIMUM_ACTIVE_STATIONS:
            raise ValueError(f"synopday station coverage is too small for {current}: {len(matches)}")
        dataset["dates"].append(current.isoformat())
        for name, station in stations_by_name.items():
            station["observed_tenths"].append(values.get(name))

    trim = max(0, len(dataset["dates"]) - retention_days)
    if trim:
        dataset["dates"] = dataset["dates"][trim:]
        for station in dataset["stations"]:
            station["observed_tenths"] = station["observed_tenths"][trim:]
    return True


def normal_for_date(station: dict[str, Any], value: date) -> int | None:
    return station["normal_tenths"][NORMAL_DAY_INDEX[value.strftime("%m-%d")]]


def period_anomaly_tenths(
    station: dict[str, Any],
    dates: list[str],
    start_index: int,
    end_index: int,
) -> Decimal | None:
    observed: list[int] = []
    normals: list[int] = []
    for index in range(start_index, end_index + 1):
        actual = station["observed_tenths"][index]
        normal = normal_for_date(station, date.fromisoformat(dates[index]))
        if actual is None or normal is None:
            continue
        observed.append(actual)
        normals.append(normal)
    expected = end_index - start_index + 1
    if len(observed) / expected < MINIMUM_VALID_RATIO:
        return None
    if expected == 5 and len(observed) == 5:
        start_date = date.fromisoformat(dates[start_index])
        five_day_normal = station["normal_5day_tenths"][NORMAL_DAY_INDEX[start_date.strftime("%m-%d")]]
        if five_day_normal is None:
            return None
        return (Decimal(sum(observed)) / len(observed)) - Decimal(five_day_normal)
    return (Decimal(sum(observed)) / len(observed)) - (Decimal(sum(normals)) / len(normals))


def latest_official_five_day() -> tuple[date, dict[str, Decimal]]:
    rows = csv.DictReader(io.StringIO(request_bytes(LATEST_PERIOD_TABLE_URL).decode("cp932")))
    end_date: date | None = None
    values: dict[str, Decimal] = {}
    for row in rows:
        station_id = row["国際地点番号"].strip()
        if not station_id or station_id in EXCLUDED_STATION_IDS:
            continue
        current_end = date(
            int(row["終了日（年）"]),
            int(row["終了日（月）"]),
            int(row["終了日（日）"]),
        )
        end_date = current_end if end_date is None else end_date
        if current_end != end_date:
            raise ValueError("official five-day table has mixed end dates")
        raw = row["前5日間平均気温平年差（℃）"].strip()
        if raw not in {"", "///"}:
            values[station_id] = Decimal(raw.replace("+", ""))
    if end_date is None:
        raise ValueError("official five-day table is empty")
    return end_date, values


def verify_latest_five_day(dataset: dict[str, Any]) -> dict[str, Any]:
    official_end, official = latest_official_five_day()
    dataset_end = date.fromisoformat(dataset["dates"][-1])
    if official_end != dataset_end:
        raise ValueError(f"official five-day end {official_end} != dataset end {dataset_end}")
    start_index = len(dataset["dates"]) - 5
    mismatches: list[str] = []
    exact_matches = 0
    maximum_difference = Decimal("0")
    compared = 0
    for station in dataset["stations"]:
        expected = official.get(station["station_id"])
        if expected is None:
            continue
        anomaly_tenths = period_anomaly_tenths(
            station,
            dataset["dates"],
            start_index,
            len(dataset["dates"]) - 1,
        )
        if anomaly_tenths is None:
            continue
        actual = (anomaly_tenths / 10).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        compared += 1
        difference = abs(actual - expected)
        maximum_difference = max(maximum_difference, difference)
        if difference == 0:
            exact_matches += 1
        elif difference > Decimal("0.1"):
            mismatches.append(f"{station['station_id']}:{actual}!={expected}")
    if compared < MINIMUM_ACTIVE_STATIONS:
        raise ValueError(f"too few latest five-day comparisons: {compared}")
    if mismatches:
        raise ValueError(f"latest five-day anomaly mismatches: {mismatches[:12]}")
    return {
        "official_end_date": official_end.isoformat(),
        "compared_station_count": compared,
        "exact_match_count": exact_matches,
        "difference_over_tolerance_count": 0,
        "tolerance_c": 0.1,
        "maximum_absolute_difference_c": float(maximum_difference),
        "source_url": LATEST_PERIOD_TABLE_URL,
    }


def canonical_dataset_id(dataset: dict[str, Any]) -> str:
    candidate = deepcopy(dataset)
    candidate["dataset_id"] = ""
    candidate["generated_at"] = ""
    raw = json.dumps(candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"recent-temperature-{hashlib.sha256(raw).hexdigest()[:16]}"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o644)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_dataset(dataset: dict[str, Any], retention_days: int) -> None:
    dataset["validation"].update({
        "station_count": len(dataset["stations"]),
        "date_count": len(dataset["dates"]),
        "observation_start": dataset["dates"][0],
        "observation_end": dataset["dates"][-1],
        "retention_days": retention_days,
        "latest_graph_center_date": (
            date.fromisoformat(dataset["dates"][-1]) - timedelta(days=2)
        ).isoformat(),
        "latest_five_day_crosscheck": verify_latest_five_day(dataset),
    })
    dataset["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    dataset["dataset_id"] = canonical_dataset_id(dataset)
    atomic_json(LATEST_PATH, dataset)
    manifest = {
        "schema_version": 1,
        "dataset_id": dataset["dataset_id"],
        "generated_at": dataset["generated_at"],
        "element": dataset["element"],
        "normal_period": dataset["normal"]["period"],
        "observation_start": dataset["dates"][0],
        "observation_end": dataset["dates"][-1],
        "latest_graph_center_date": dataset["validation"]["latest_graph_center_date"],
        "station_count": len(dataset["stations"]),
        "date_count": len(dataset["dates"]),
        "minimum_valid_ratio": MINIMUM_VALID_RATIO,
        "files": {
            "latest": {
                "path": "latest.json",
                "bytes": LATEST_PATH.stat().st_size,
                "sha256": sha256_path(LATEST_PATH),
            }
        },
        "sources": {
            "normal": NORMAL_INDEX_URL,
            "observation": OBSDL_INDEX_URL,
            "latest_daily": SYNOPDAY_URL.format(page=2),
            "crosscheck": LATEST_PERIOD_TABLE_URL,
        },
    }
    atomic_json(MANIFEST_PATH, manifest)
    print(json.dumps({
        "dataset_id": dataset["dataset_id"],
        "observation_start": dataset["dates"][0],
        "observation_end": dataset["dates"][-1],
        "latest_graph_center_date": dataset["validation"]["latest_graph_center_date"],
        "station_count": len(dataset["stations"]),
        "date_count": len(dataset["dates"]),
        "five_day_crosscheck": dataset["validation"]["latest_five_day_crosscheck"],
    }, ensure_ascii=False, indent=2))


def load_existing() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    latest = json.loads((DATA_ROOT / manifest["files"]["latest"]["path"]).read_text(encoding="utf-8"))
    if latest["dataset_id"] != manifest["dataset_id"]:
        raise ValueError("recent-temperature manifest and dataset IDs do not match")
    return latest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="rebuild recent observations from the JMA download service")
    parser.add_argument("--retention-days", type=int, default=125)
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument("--normal-archive", type=Path, help="use an already downloaded official normal_surface.zip")
    parser.add_argument("--target-end", type=date.fromisoformat, help="last observation date (default: yesterday JST)")
    args = parser.parse_args()

    if not 93 <= args.retention_days <= 180:
        raise ValueError("retention-days must be between 93 and 180")
    now_jst = datetime.now(timezone(timedelta(hours=9))).date()
    target_end = args.target_end or (now_jst - timedelta(days=1))

    if args.bootstrap or not (MANIFEST_PATH.is_file() and LATEST_PATH.is_file()):
        start = target_end - timedelta(days=args.retention_days - 1)
        dataset = bootstrap_dataset(
            load_normals(args.normal_archive),
            start,
            target_end,
            args.request_delay,
        )
        write_dataset(dataset, args.retention_days)
        return

    dataset = load_existing()
    changed = incremental_update(dataset, target_end, args.retention_days)
    if changed:
        write_dataset(dataset, args.retention_days)
    else:
        crosscheck = verify_latest_five_day(dataset)
        print(json.dumps({
            "dataset_id": dataset["dataset_id"],
            "changed": False,
            "observation_end": dataset["dates"][-1],
            "five_day_crosscheck": crosscheck,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
