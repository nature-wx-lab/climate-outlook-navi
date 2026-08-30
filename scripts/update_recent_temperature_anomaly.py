#!/usr/bin/env python3
"""Build the maximum-density nationwide temperature-anomaly point dataset.

The public derivative combines official JMA sources:

- the current nationwide temperature table for the active station population;
- the AMeDAS station index plus daily and daily-five-day 1991-2020 normals;
- batched daily mean temperatures from the observation download service.

The population includes both surface observatories and AMeDAS temperature
stations. Only compact station metadata, normals, and recent observations are
published. Source archives and download responses remain transient.
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

NORMAL_DAILY_ARCHIVE_URL = (
    "https://www.data.jma.go.jp/stats/mdrr/normal/2020/data/normal_amedas_daily.zip"
)
NORMAL_FIVE_DAY_ARCHIVE_URL = (
    "https://www.data.jma.go.jp/stats/mdrr/normal/2020/data/normal_amedas_daily_5day.zip"
)
STATION_INDEX_ARCHIVE_URL = (
    "https://www.data.jma.go.jp/stats/mdrr/normal/2020/data/amedas_station_index.zip"
)
NORMAL_INDEX_URL = "https://www.data.jma.go.jp/stats/mdrr/normal/index.html"
OBSDL_INDEX_URL = "https://www.data.jma.go.jp/risk/obsdl/index.php"
OBSDL_STATION_URL = "https://www.data.jma.go.jp/risk/obsdl/top/station"
OBSDL_TABLE_URL = "https://www.data.jma.go.jp/risk/obsdl/show/table"
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
ACCEPTED_QUALITY_CODES = {5, 8}
MINIMUM_VALID_RATIO = 0.8
MINIMUM_ACTIVE_STATIONS = 850
MINIMUM_POPULATION_RATIO = 0.95
OBSERVATION_BATCH_SIZE = 50
OBSERVATION_REFRESH_STRATEGY = "full_retention_window"


@dataclass(frozen=True)
class PopulationStation:
    station_id: str
    name: str
    prefecture: str
    international_id: str


@dataclass(frozen=True)
class StationNormal:
    station_id: str
    obsdl_id: str
    station_type: str
    name: str
    prefecture: str
    latitude: float
    longitude: float
    elevation_m: float
    normal_tenths: tuple[int | None, ...]
    normal_5day_tenths: tuple[int | None, ...]


class ObsdlStationParser(HTMLParser):
    """Read active station identifiers and coordinates from an OBS DL panel."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stations: dict[str, dict[str, Any]] = {}
        self._current: dict[str, Any] | None = None
        self._depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "div":
            if self._current is not None:
                self._depth += 1
                return
            classes = set(attributes.get("class", "").split())
            if "station" in classes:
                self._current = {
                    "classes": classes,
                    "title": attributes.get("title", ""),
                    "values": {},
                }
                self._depth = 1
            return
        if tag == "input" and self._current is not None:
            name = attributes.get("name")
            if name:
                self._current["values"][name] = attributes.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag != "div" or self._current is None:
            return
        self._depth -= 1
        if self._depth:
            return
        classes = self._current["classes"]
        values = self._current["values"]
        station_id = values.get("stid", "")
        if station_id and "owata" not in classes:
            latitude, longitude = coordinates_from_title(self._current["title"])
            self.stations.setdefault(station_id, {
                "obsdl_id": station_id,
                "name": normalize_text(values.get("stname", "")),
                "latitude": latitude,
                "longitude": longitude,
            })
        self._current = None


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("\u3000", " ")).strip()


def coordinates_from_title(value: str) -> tuple[float | None, float | None]:
    latitude = re.search(r"北緯：(\d+)度([\d.]+)分", value)
    longitude = re.search(r"東経：(\d+)度([\d.]+)分", value)
    if not latitude or not longitude:
        return None, None
    return (
        float(latitude.group(1)) + float(latitude.group(2)) / 60,
        float(longitude.group(1)) + float(longitude.group(2)) / 60,
    )


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
        return int(
            (Decimal(match.group(0)) * 10).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
    except InvalidOperation:
        return None


def iso_dates(start: date, end: date) -> list[str]:
    return [
        (start + timedelta(days=offset)).isoformat()
        for offset in range((end - start).days + 1)
    ]


def population_hash(population: list[PopulationStation]) -> str:
    raw = "\n".join(station.station_id for station in population).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


def parse_latest_period_table(
    raw: bytes,
) -> tuple[date, list[PopulationStation], dict[str, Decimal]]:
    rows = csv.DictReader(io.StringIO(raw.decode("cp932")))
    end_date: date | None = None
    population: list[PopulationStation] = []
    values: dict[str, Decimal] = {}
    for row in rows:
        station_id = row["観測所番号"].strip()
        if re.fullmatch(r"\d{5}", station_id) is None:
            continue
        current_end = date(
            int(row["終了日（年）"]),
            int(row["終了日（月）"]),
            int(row["終了日（日）"]),
        )
        end_date = current_end if end_date is None else end_date
        if current_end != end_date:
            raise ValueError("official temperature table has mixed end dates")
        name = normalize_text(row["地点"].split("（", 1)[0])
        population.append(PopulationStation(
            station_id=station_id,
            name=name,
            prefecture=normalize_text(row["都道府県"]),
            international_id=row["国際地点番号"].strip(),
        ))
        anomaly = row["前5日間平均気温平年差（℃）"].strip()
        if anomaly not in {"", "///"}:
            values[station_id] = Decimal(anomaly.replace("+", ""))
    if end_date is None or len(population) < MINIMUM_ACTIVE_STATIONS:
        raise ValueError("official temperature table population is too small")
    station_ids = [station.station_id for station in population]
    if len(station_ids) != len(set(station_ids)):
        raise ValueError("official temperature table contains duplicate station IDs")
    return end_date, population, values


def station_index(raw: bytes) -> dict[str, dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        member = next(
            (name for name in archive.namelist() if name.endswith("amedas_station_index.csv")),
            None,
        )
        if member is None:
            raise ValueError("AMeDAS station index CSV is missing")
        rows = list(csv.reader(io.StringIO(archive.read(member).decode("cp932"))))
    stations: dict[str, dict[str, str]] = {}
    for row in rows[2:]:
        if len(row) < 15 or row[12].strip() != "1":
            continue
        station_id = row[0].strip()
        stations[station_id] = {
            "station_id": station_id,
            "name": normalize_text(row[1]),
            "latitude": str(float(row[4]) + float(row[5]) / 60),
            "longitude": str(float(row[6]) + float(row[7]) / 60),
            "elevation_m": row[8].strip(),
        }
    if len(stations) < MINIMUM_ACTIVE_STATIONS:
        raise ValueError(f"AMeDAS temperature-normal index is too small: {len(stations)}")
    return stations


def obsdl_opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(CookieJar()),
        urllib.request.HTTPSHandler(context=ssl_context()),
    )
    request_bytes(OBSDL_INDEX_URL, opener=opener, timeout=30)
    return opener


def fetch_obsdl_station_panels(
    opener: urllib.request.OpenerDirector,
    prefecture_codes: set[str],
) -> dict[str, list[dict[str, Any]]]:
    by_prefecture: dict[str, list[dict[str, Any]]] = {}
    for index, code in enumerate(sorted(prefecture_codes), 1):
        raw = request_bytes(
            OBSDL_STATION_URL,
            opener=opener,
            data=urllib.parse.urlencode({"pd": code}).encode("ascii"),
            timeout=60,
        )
        parser = ObsdlStationParser()
        parser.feed(raw.decode("utf-8", errors="replace"))
        by_prefecture[code] = list(parser.stations.values())
        print(
            f"[station-panel {index:02d}/{len(prefecture_codes):02d}] "
            f"{code} active={len(parser.stations)}",
            flush=True,
        )
    return by_prefecture


def resolve_obsdl_ids(
    population: list[PopulationStation],
    metadata: dict[str, dict[str, str]],
    opener: urllib.request.OpenerDirector,
) -> dict[str, str]:
    amedas = [station for station in population if not station.international_id]
    panels = fetch_obsdl_station_panels(
        opener,
        {station.station_id[:2] for station in amedas},
    )
    all_panel_stations = [
        candidate
        for candidates in panels.values()
        for candidate in candidates
    ]
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for station in population:
        if station.international_id:
            resolved[station.station_id] = f"s{station.international_id}"
            continue
        reference = metadata.get(station.station_id)
        if reference is None:
            unresolved.append(f"{station.station_id}:{station.name}:normal-index")
            continue
        names = {station.name, normalize_text(reference["name"])}
        candidates = [
            candidate
            for candidate in panels.get(station.station_id[:2], [])
            if candidate["obsdl_id"].startswith("a") and candidate["name"] in names
        ]
        if not candidates:
            candidates = [
                candidate
                for candidate in all_panel_stations
                if candidate["obsdl_id"].startswith("a")
                and candidate["name"] in names
            ]
        if not candidates:
            unresolved.append(f"{station.station_id}:{station.name}:obsdl")
            continue
        latitude = float(reference["latitude"])
        longitude = float(reference["longitude"])
        candidates.sort(key=lambda candidate: (
            (candidate["latitude"] - latitude) ** 2
            + (candidate["longitude"] - longitude) ** 2
            if candidate["latitude"] is not None and candidate["longitude"] is not None
            else 999
        ))
        candidate = candidates[0]
        if (
            candidate["latitude"] is not None
            and candidate["longitude"] is not None
            and (
                abs(candidate["latitude"] - latitude) > 0.06
                or abs(candidate["longitude"] - longitude) > 0.06
            )
        ):
            unresolved.append(f"{station.station_id}:{station.name}:coordinates")
            continue
        resolved[station.station_id] = candidate["obsdl_id"]
    if unresolved:
        raise ValueError(f"unresolved OBS DL stations ({len(unresolved)}): {unresolved[:20]}")
    return resolved


def archive_members(archive: zipfile.ZipFile, stem: str) -> dict[str, str]:
    pattern = re.compile(rf"{re.escape(stem)}_(\d{{5}})\.csv$")
    members: dict[str, str] = {}
    for member in archive.namelist():
        match = pattern.search(member)
        if match:
            members[match.group(1)] = member
    return members


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
            values[NORMAL_DAY_INDEX[f"{month:02d}-{day:02d}"]] = int(
                row[value_index].strip()
            )
    valid = sum(value is not None for value in values)
    if valid / len(values) < MINIMUM_VALID_RATIO:
        missing = len(values) - valid
        raise ValueError(
            f"temperature normal coverage is too small for {station_id}: {member} "
            f"({missing} days)"
        )
    return tuple(values)


def load_normals(
    population: list[PopulationStation],
    opener: urllib.request.OpenerDirector,
    station_index_archive: Path | None,
    normal_daily_archive: Path | None,
    normal_five_day_archive: Path | None,
) -> list[StationNormal]:
    index_raw = (
        station_index_archive.read_bytes()
        if station_index_archive
        else request_bytes(STATION_INDEX_ARCHIVE_URL)
    )
    metadata = station_index(index_raw)
    eligible_population = [
        station for station in population if station.station_id in metadata
    ]
    missing_metadata = [
        station.station_id for station in population if station.station_id not in metadata
    ]
    if len(eligible_population) < MINIMUM_ACTIVE_STATIONS:
        raise ValueError(
            f"too few current stations have 1991-2020 normals: "
            f"{len(eligible_population)}/{len(population)}"
        )
    if missing_metadata:
        print(
            f"[normal-population] eligible={len(eligible_population)} "
            f"without-normal={len(missing_metadata)} ids={missing_metadata}",
            flush=True,
        )
    obsdl_ids = resolve_obsdl_ids(eligible_population, metadata, opener)
    daily_raw = (
        normal_daily_archive.read_bytes()
        if normal_daily_archive
        else request_bytes(NORMAL_DAILY_ARCHIVE_URL)
    )
    five_day_raw = (
        normal_five_day_archive.read_bytes()
        if normal_five_day_archive
        else request_bytes(NORMAL_FIVE_DAY_ARCHIVE_URL)
    )
    normals: list[StationNormal] = []
    incomplete_normals: list[str] = []
    with (
        zipfile.ZipFile(io.BytesIO(daily_raw)) as daily_archive,
        zipfile.ZipFile(io.BytesIO(five_day_raw)) as five_day_archive,
    ):
        daily_members = archive_members(daily_archive, "nml_amd_d")
        five_day_members = archive_members(five_day_archive, "nml_amd_d5d")
        for station in eligible_population:
            station_id = station.station_id
            if station_id not in daily_members or station_id not in five_day_members:
                incomplete_normals.append(station_id)
                continue
            item = metadata[station_id]
            obsdl_id = obsdl_ids[station_id]
            try:
                daily = parse_normal_series(
                    daily_archive,
                    daily_members[station_id],
                    station_id,
                    "25",
                )
                five_day = parse_normal_series(
                    five_day_archive,
                    five_day_members[station_id],
                    station_id,
                    "27",
                )
            except ValueError:
                incomplete_normals.append(station_id)
                continue
            normals.append(StationNormal(
                station_id=station_id,
                obsdl_id=obsdl_id,
                station_type="surface" if obsdl_id.startswith("s") else "amedas",
                name=station.name,
                prefecture=station.prefecture,
                latitude=round(float(item["latitude"]), 5),
                longitude=round(float(item["longitude"]), 5),
                elevation_m=round(float(item["elevation_m"]), 1),
                normal_tenths=daily,
                normal_5day_tenths=five_day,
            ))
    if incomplete_normals:
        print(
            f"[normal-series] complete={len(normals)} "
            f"incomplete={len(incomplete_normals)} ids={incomplete_normals}",
            flush=True,
        )
    if len(normals) < MINIMUM_ACTIVE_STATIONS:
        raise ValueError(f"complete normal population is too small: {len(normals)}")
    return normals


def observation_payload(obsdl_ids: list[str], start: date, end: date) -> bytes:
    values = {
        "stationNumList": json.dumps(obsdl_ids, ensure_ascii=False),
        "aggrgPeriod": "1",
        "elementNumList": json.dumps([[OBSERVATION_ELEMENT, ""]], ensure_ascii=False),
        "interAnnualType": "1",
        "ymdList": json.dumps([
            str(start.year),
            str(end.year),
            str(start.month),
            str(end.month),
            str(start.day),
            str(end.day),
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


def parse_obsdl(
    raw: bytes,
    obsdl_ids: list[str],
    expected_dates: list[str],
) -> dict[str, list[int | None]]:
    expected_date_set = set(expected_dates)
    if len(expected_date_set) != len(expected_dates):
        raise ValueError("expected OBS DL dates are not unique")
    values: dict[str, dict[str, int | None]] = {
        station_id: {} for station_id in obsdl_ids
    }
    seen_dates: set[str] = set()
    for row in csv.reader(io.StringIO(raw.decode("cp932", errors="replace"))):
        if (
            not row
            or re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", row[0].strip()) is None
        ):
            continue
        year, month, day = (int(value) for value in row[0].split("/"))
        current = date(year, month, day).isoformat()
        if current not in expected_date_set:
            raise ValueError(f"unexpected OBS DL date row: {current}")
        if current in seen_dates:
            raise ValueError(f"duplicate OBS DL date row: {current}")
        seen_dates.add(current)
        for index, station_id in enumerate(obsdl_ids):
            value_index = 1 + index * 3
            quality_index = value_index + 1
            if quality_index >= len(row):
                raise ValueError(
                    f"OBS DL column count mismatch: {len(row)} for {len(obsdl_ids)} stations"
                )
            quality = int(row[quality_index].strip() or "0")
            values[station_id][current] = (
                as_tenths(row[value_index])
                if quality in ACCEPTED_QUALITY_CODES
                else None
            )
    missing_dates = expected_date_set - seen_dates
    if missing_dates:
        raise ValueError(
            f"missing OBS DL date rows ({len(missing_dates)}): "
            f"{sorted(missing_dates)[:10]}"
        )
    return {
        station_id: [values[station_id].get(current) for current in expected_dates]
        for station_id in obsdl_ids
    }


def fetch_observation_batch(
    opener: urllib.request.OpenerDirector,
    obsdl_ids: list[str],
    start: date,
    end: date,
    expected_dates: list[str],
) -> dict[str, list[int | None]]:
    error: Exception | None = None
    for attempt in range(3):
        try:
            raw = request_bytes(
                OBSDL_TABLE_URL,
                opener=opener,
                data=observation_payload(obsdl_ids, start, end),
            )
            return parse_obsdl(raw, obsdl_ids, expected_dates)
        except Exception as exc:  # network retry boundary
            error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    if len(obsdl_ids) > 1:
        middle = len(obsdl_ids) // 2
        return {
            **fetch_observation_batch(
                opener,
                obsdl_ids[:middle],
                start,
                end,
                expected_dates,
            ),
            **fetch_observation_batch(
                opener,
                obsdl_ids[middle:],
                start,
                end,
                expected_dates,
            ),
        }
    raise RuntimeError(f"failed to fetch observations for {obsdl_ids[0]}: {error}")


def fetch_observations(
    opener: urllib.request.OpenerDirector,
    obsdl_ids: list[str],
    start: date,
    end: date,
    request_delay: float,
) -> dict[str, list[int | None]]:
    dates = iso_dates(start, end)
    batches = [
        obsdl_ids[index:index + OBSERVATION_BATCH_SIZE]
        for index in range(0, len(obsdl_ids), OBSERVATION_BATCH_SIZE)
    ]
    observations: dict[str, list[int | None]] = {}
    for index, batch in enumerate(batches, 1):
        observations.update(
            fetch_observation_batch(opener, batch, start, end, dates)
        )
        print(
            f"[observation-batch {index:02d}/{len(batches):02d}] "
            f"stations={len(batch)} dates={len(dates)}",
            flush=True,
        )
        if request_delay > 0 and index < len(batches):
            time.sleep(request_delay)
    return observations


def bootstrap_dataset(
    normals: list[StationNormal],
    population: list[PopulationStation],
    start: date,
    end: date,
    request_delay: float,
    opener: urllib.request.OpenerDirector,
) -> dict[str, Any]:
    dates = iso_dates(start, end)
    observations = fetch_observations(
        opener,
        [station.obsdl_id for station in normals],
        start,
        end,
        request_delay,
    )
    stations: list[dict[str, Any]] = []
    for station in normals:
        observed = observations[station.obsdl_id]
        valid = sum(value is not None for value in observed)
        if valid / len(dates) >= MINIMUM_VALID_RATIO:
            stations.append({
                "station_id": station.station_id,
                "obsdl_id": station.obsdl_id,
                "station_type": station.station_type,
                "name": station.name,
                "prefecture": station.prefecture,
                "lat": station.latitude,
                "lon": station.longitude,
                "elevation_m": station.elevation_m,
                "normal_tenths": list(station.normal_tenths),
                "normal_5day_tenths": list(station.normal_5day_tenths),
                "observed_tenths": observed,
            })
    minimum_population = max(
        MINIMUM_ACTIVE_STATIONS,
        int(len(normals) * MINIMUM_POPULATION_RATIO),
    )
    if len(stations) < minimum_population:
        raise ValueError(
            f"active station coverage is too small: {len(stations)}/{len(normals)}"
        )
    return {
        "schema_version": 2,
        "dataset_id": "",
        "generated_at": "",
        "element": {
            "code": "201",
            "name": "日平均気温",
            "unit": "℃",
            "storage_scale": 10,
        },
        "normal": {
            "period": "1991-2020",
            "label": "気象庁 2020年平年値（第5版）",
            "source_url": NORMAL_INDEX_URL,
        },
        "observation": {
            "source": "気象庁 過去の気象データ・ダウンロード",
            "source_url": OBSDL_INDEX_URL,
            "station_population_url": LATEST_PERIOD_TABLE_URL,
        },
        "normal_days": list(NORMAL_DAYS),
        "dates": dates,
        "stations": stations,
        "validation": {
            "minimum_valid_ratio": MINIMUM_VALID_RATIO,
            "minimum_population_ratio": MINIMUM_POPULATION_RATIO,
            "accepted_obsdl_quality_codes": sorted(ACCEPTED_QUALITY_CODES),
            "official_population_station_count": len(population),
            "official_population_hash": population_hash(population),
            "normal_population_station_count": len(normals),
            "normal_population_hash": hashlib.sha256(
                "\n".join(station.station_id for station in normals).encode("ascii")
            ).hexdigest(),
        },
    }


def reconcile_retained_observations(
    dataset: dict[str, Any],
    target_end: date,
    retention_days: int,
    request_delay: float,
    opener: urllib.request.OpenerDirector,
) -> bool:
    """Replace the retained observation window with a fresh official snapshot."""
    existing_dates = dataset.get("dates")
    if not isinstance(existing_dates, list) or not existing_dates:
        raise ValueError("existing observation dates are missing")
    parsed_dates = [date.fromisoformat(value) for value in existing_dates]
    if any(
        current - previous != timedelta(days=1)
        for previous, current in zip(parsed_dates, parsed_dates[1:])
    ):
        raise ValueError("existing observation dates are not consecutive")
    existing_end = parsed_dates[-1]
    if target_end < existing_end:
        raise ValueError(
            f"official observation end moved backwards: {target_end} < {existing_end}"
        )
    # JMA daily observations can be completed or revised after their first
    # publication. Re-fetch the complete public retention window on every run
    # so a value that was initially missing (or later corrected) self-heals.
    # Normals and station metadata remain cached; only observations are read
    # again, keeping this substantially lighter than a full bootstrap.
    start = target_end - timedelta(days=retention_days - 1)
    refreshed_dates = iso_dates(start, target_end)
    stations = dataset.get("stations")
    if not isinstance(stations, list) or not stations:
        raise ValueError("existing observation stations are missing")
    obsdl_ids: list[str] = []
    for station in stations:
        obsdl_id = station.get("obsdl_id") if isinstance(station, dict) else None
        observed = station.get("observed_tenths") if isinstance(station, dict) else None
        if not isinstance(obsdl_id, str) or not obsdl_id:
            raise ValueError("existing station has an invalid OBS DL ID")
        if not isinstance(observed, list) or len(observed) != len(existing_dates):
            raise ValueError(f"existing observation length mismatch: {obsdl_id}")
        obsdl_ids.append(obsdl_id)
    if len(set(obsdl_ids)) != len(obsdl_ids):
        raise ValueError("existing station OBS DL IDs are not unique")

    observations = fetch_observations(
        opener,
        obsdl_ids,
        start,
        target_end,
        request_delay,
    )
    refreshed_by_id: dict[str, list[int | None]] = {}
    for obsdl_id in obsdl_ids:
        refreshed = observations.get(obsdl_id)
        if not isinstance(refreshed, list) or len(refreshed) != len(refreshed_dates):
            raise ValueError(f"refreshed observation length mismatch: {obsdl_id}")
        if any(
            value is not None and not isinstance(value, int)
            for value in refreshed
        ):
            raise ValueError(f"refreshed observation value mismatch: {obsdl_id}")
        valid = sum(value is not None for value in refreshed)
        if valid / len(refreshed_dates) < MINIMUM_VALID_RATIO:
            raise ValueError(
                f"refreshed observation coverage is too small: "
                f"{obsdl_id} {valid}/{len(refreshed_dates)}"
            )
        refreshed_by_id[obsdl_id] = refreshed
    existing_indexes = {value: index for index, value in enumerate(existing_dates)}
    added_days = sum(value not in existing_indexes for value in refreshed_dates)
    late_values = 0
    revised_values = 0
    withdrawn_values = 0
    changed = existing_dates != refreshed_dates
    for station in stations:
        obsdl_id = station["obsdl_id"]
        refreshed = refreshed_by_id[obsdl_id]
        previous = station["observed_tenths"]
        for new_index, current_date in enumerate(refreshed_dates):
            old_index = existing_indexes.get(current_date)
            if old_index is None:
                continue
            old_value = previous[old_index]
            new_value = refreshed[new_index]
            if old_value == new_value:
                continue
            changed = True
            if old_value is None and new_value is not None:
                late_values += 1
            elif old_value is not None and new_value is None:
                withdrawn_values += 1
            else:
                revised_values += 1
        station["observed_tenths"] = refreshed
    dataset["dates"] = refreshed_dates
    print(json.dumps({
        "observation_refresh_strategy": OBSERVATION_REFRESH_STRATEGY,
        "refresh_start": refreshed_dates[0],
        "refresh_end": refreshed_dates[-1],
        "refresh_date_count": len(refreshed_dates),
        "added_day_count": added_days,
        "late_value_count": late_values,
        "revised_value_count": revised_values,
        "withdrawn_value_count": withdrawn_values,
        "changed": changed,
    }, ensure_ascii=False, indent=2))
    return changed


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
        five_day_normal = station["normal_5day_tenths"][
            NORMAL_DAY_INDEX[start_date.strftime("%m-%d")]
        ]
        if five_day_normal is None:
            return None
        return (Decimal(sum(observed)) / len(observed)) - Decimal(five_day_normal)
    return (
        (Decimal(sum(observed)) / len(observed))
        - (Decimal(sum(normals)) / len(normals))
    )


def verify_latest_five_day(
    dataset: dict[str, Any],
    official_end: date,
    official: dict[str, Decimal],
) -> dict[str, Any]:
    dataset_end = date.fromisoformat(dataset["dates"][-1])
    if official_end != dataset_end:
        raise ValueError(
            f"official five-day end {official_end} != dataset end {dataset_end}"
        )
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
        actual = (anomaly_tenths / 10).quantize(
            Decimal("0.1"),
            rounding=ROUND_HALF_UP,
        )
        compared += 1
        difference = abs(actual - expected)
        maximum_difference = max(maximum_difference, difference)
        if difference == 0:
            exact_matches += 1
        elif difference > Decimal("0.1"):
            mismatches.append(f"{station['station_id']}:{actual}!={expected}")
    if compared < MINIMUM_ACTIVE_STATIONS:
        raise ValueError(f"too few latest five-day comparisons: {compared}")
    if compared != len(official):
        raise ValueError(
            f"incomplete latest five-day comparisons: {compared}/{len(official)}"
        )
    if mismatches:
        raise ValueError(
            f"latest five-day anomaly mismatches ({len(mismatches)}): "
            f"{mismatches[:20]}"
        )
    return {
        "official_end_date": official_end.isoformat(),
        "official_value_count": len(official),
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
    raw = json.dumps(
        candidate,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"recent-temperature-{hashlib.sha256(raw).hexdigest()[:16]}"


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
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


def write_dataset(
    dataset: dict[str, Any],
    retention_days: int,
    official_end: date,
    official_values: dict[str, Decimal],
) -> None:
    dataset["validation"].update({
        "observation_refresh_strategy": OBSERVATION_REFRESH_STRATEGY,
        "station_count": len(dataset["stations"]),
        "date_count": len(dataset["dates"]),
        "observation_start": dataset["dates"][0],
        "observation_end": dataset["dates"][-1],
        "retention_days": retention_days,
        "latest_graph_center_date": (
            date.fromisoformat(dataset["dates"][-1]) - timedelta(days=2)
        ).isoformat(),
        "latest_five_day_crosscheck": verify_latest_five_day(
            dataset,
            official_end,
            official_values,
        ),
    })
    dataset["generated_at"] = datetime.now(timezone.utc).isoformat(
        timespec="seconds"
    )
    dataset["dataset_id"] = canonical_dataset_id(dataset)
    atomic_json(LATEST_PATH, dataset)
    manifest = {
        "schema_version": 2,
        "dataset_id": dataset["dataset_id"],
        "generated_at": dataset["generated_at"],
        "element": dataset["element"],
        "normal_period": dataset["normal"]["period"],
        "observation_start": dataset["dates"][0],
        "observation_end": dataset["dates"][-1],
        "latest_graph_center_date": dataset["validation"][
            "latest_graph_center_date"
        ],
        "station_count": len(dataset["stations"]),
        "population_station_count": dataset["validation"][
            "normal_population_station_count"
        ],
        "official_population_station_count": dataset["validation"][
            "official_population_station_count"
        ],
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
            "station_population": LATEST_PERIOD_TABLE_URL,
            "station_index": STATION_INDEX_ARCHIVE_URL,
            "normal_daily": NORMAL_DAILY_ARCHIVE_URL,
            "normal_five_day": NORMAL_FIVE_DAY_ARCHIVE_URL,
            "observation": OBSDL_INDEX_URL,
            "crosscheck": LATEST_PERIOD_TABLE_URL,
        },
    }
    atomic_json(MANIFEST_PATH, manifest)
    print(json.dumps({
        "dataset_id": dataset["dataset_id"],
        "observation_start": dataset["dates"][0],
        "observation_end": dataset["dates"][-1],
        "latest_graph_center_date": dataset["validation"][
            "latest_graph_center_date"
        ],
        "station_count": len(dataset["stations"]),
        "population_station_count": dataset["validation"][
            "normal_population_station_count"
        ],
        "official_population_station_count": dataset["validation"][
            "official_population_station_count"
        ],
        "date_count": len(dataset["dates"]),
        "five_day_crosscheck": dataset["validation"][
            "latest_five_day_crosscheck"
        ],
    }, ensure_ascii=False, indent=2))


def load_existing() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    latest = json.loads(
        (DATA_ROOT / manifest["files"]["latest"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    if latest["dataset_id"] != manifest["dataset_id"]:
        raise ValueError("recent-temperature manifest and dataset IDs do not match")
    return latest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bootstrap",
        action="store_true",
        help="rebuild all recent observations from the JMA download service",
    )
    parser.add_argument("--retention-days", type=int, default=125)
    parser.add_argument("--request-delay", type=float, default=0.2)
    parser.add_argument(
        "--station-index-archive",
        type=Path,
        help="use an already downloaded official amedas_station_index.zip",
    )
    parser.add_argument(
        "--normal-daily-archive",
        type=Path,
        help="use an already downloaded official normal_amedas_daily.zip",
    )
    parser.add_argument(
        "--normal-five-day-archive",
        type=Path,
        help="use an already downloaded official normal_amedas_daily_5day.zip",
    )
    parser.add_argument(
        "--target-end",
        type=date.fromisoformat,
        help="last observation date (default: latest official table date)",
    )
    args = parser.parse_args()

    if not 93 <= args.retention_days <= 180:
        raise ValueError("retention-days must be between 93 and 180")
    if not 0 <= args.request_delay <= 10:
        raise ValueError("request-delay must be between 0 and 10 seconds")

    official_raw = request_bytes(LATEST_PERIOD_TABLE_URL)
    official_end, population, official_values = parse_latest_period_table(
        official_raw
    )
    target_end = args.target_end or official_end
    if target_end != official_end:
        raise ValueError(
            f"target end {target_end} must match latest official table {official_end}"
        )

    existing: dict[str, Any] | None = None
    if MANIFEST_PATH.is_file() and LATEST_PATH.is_file():
        existing = load_existing()
    needs_bootstrap = (
        args.bootstrap
        or existing is None
        or existing.get("schema_version") != 2
        or existing.get("validation", {}).get("official_population_hash")
        != population_hash(population)
    )

    opener = obsdl_opener()
    if needs_bootstrap:
        start = target_end - timedelta(days=args.retention_days - 1)
        dataset = bootstrap_dataset(
            load_normals(
                population,
                opener,
                args.station_index_archive,
                args.normal_daily_archive,
                args.normal_five_day_archive,
            ),
            population,
            start,
            target_end,
            args.request_delay,
            opener,
        )
        write_dataset(
            dataset,
            args.retention_days,
            official_end,
            official_values,
        )
        return

    dataset = existing
    changed = reconcile_retained_observations(
        dataset,
        target_end,
        args.retention_days,
        args.request_delay,
        opener,
    )
    metadata_changed = (
        dataset["validation"].get("observation_refresh_strategy")
        != OBSERVATION_REFRESH_STRATEGY
    )
    if changed or metadata_changed:
        write_dataset(
            dataset,
            args.retention_days,
            official_end,
            official_values,
        )
    else:
        crosscheck = verify_latest_five_day(
            dataset,
            official_end,
            official_values,
        )
        print(json.dumps({
            "dataset_id": dataset["dataset_id"],
            "changed": False,
            "observation_end": dataset["dates"][-1],
            "station_count": len(dataset["stations"]),
            "five_day_crosscheck": crosscheck,
        }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
