"""Read JMA's explicit seasonal-forecast calendar without guessing monthly dates.

The public calendar gives exact P3M/WARM/COLD dates and the Thursday P1M rule.
The caller owns persistence: this module only reads a cache and returns a
validated candidate. ``acquisition_status`` / ``acquisition_error`` are runtime
fields and may be omitted when the caller atomically saves the calendar.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import ssl
import time
import unicodedata
import urllib.request
from datetime import date, datetime, time as day_time, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Callable

try:
    import certifi
except ImportError:  # pragma: no cover
    certifi = None


CALENDAR_URL = "https://www.jma.go.jp/jma/kishou/know/kisetsu_riyou/calendar/index.html"
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "data/season/issue-calendar.json"
JST = timezone(timedelta(hours=9))
PRODUCTS = {"P1M", "P3M", "WARM", "COLD"}
RUNTIME_FIELDS = {"acquisition_status", "acquisition_error"}


class CalendarError(ValueError):
    """A calendar cannot safely establish the expected forecast editions."""


class CalendarUnavailableError(CalendarError):
    pass


class CalendarCoverageError(CalendarError):
    pass


def _aware(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        raise CalendarError("calendar datetime must include a timezone")
    return parsed.astimezone(JST)


def _text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    return unicodedata.normalize("NFKC", unescape(re.sub(r"<[^>]+>", "", fragment))).strip()


def _era_year(era: str, year: str) -> int:
    return {"令和": 2018, "平成": 1988}[era] + int(year)


def _explicit_dates(text: str) -> list[date]:
    year = None
    result = []
    for match in re.finditer(r"(令和|平成)(\d+)年|(\d+)月(\d+)日", text):
        if match[1]:
            year = _era_year(match[1], match[2])
        else:
            if year is None:
                raise CalendarError("calendar date is missing its explicit year")
            result.append(date(year, int(match[3]), int(match[4])))
    return result


def _issue(product: str, when: date) -> dict[str, str]:
    hour, minute = (14, 30) if product == "P1M" else (14, 0)
    return {"product": product, "report_datetime": datetime.combine(when, day_time(hour, minute), JST).isoformat()}


def _month_keys(start: date, end: date) -> set[tuple[int, int]]:
    result = set()
    while start <= end:
        result.add((start.year, start.month))
        start = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return result


def validate_issue_calendar(payload: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise CalendarError("issue calendar schema mismatch")
    if payload.get("source_url") != CALENDAR_URL or re.fullmatch(r"[0-9a-f]{64}", payload.get("source_sha256", "")) is None:
        raise CalendarError("issue calendar source mismatch")
    start, end = date.fromisoformat(payload["coverage_start"]), date.fromisoformat(payload["coverage_end"])
    if start > end or start.day != 1 or end.day != calendar.monthrange(end.year, end.month)[1]:
        raise CalendarError("issue calendar coverage must span complete months")
    if len(_month_keys(start, end)) > 36:
        raise CalendarError("issue calendar coverage is unexpectedly large")
    issues = payload.get("issues")
    if not isinstance(issues, list) or not issues:
        raise CalendarError("issue calendar editions are missing")
    by_product = {name: [] for name in PRODUCTS}
    seen = set()
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {"product", "report_datetime"} or issue.get("product") not in PRODUCTS:
            raise CalendarError("issue calendar edition is invalid")
        product = issue["product"]
        when = _aware(issue["report_datetime"])
        if issue != _issue(product, when.date()) or not start <= when.date() <= end:
            raise CalendarError("issue calendar edition time or coverage mismatch")
        key = (product, issue["report_datetime"])
        if key in seen:
            raise CalendarError("issue calendar has a duplicate edition")
        seen.add(key)
        by_product[product].append(when.date())
    thursdays = {start + timedelta(days=i) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() == 3}
    if set(by_product["P1M"]) != thursdays:
        raise CalendarError("weekly forecast calendar is incomplete")
    months = _month_keys(start, end)
    if len(by_product["P3M"]) != len(months) or {(d.year, d.month) for d in by_product["P3M"]} != months:
        raise CalendarError("monthly forecast calendar is incomplete")
    for product, month in (("WARM", 2), ("COLD", 9)):
        expected_years = {year for year, candidate in months if candidate == month}
        days = by_product[product]
        if len(days) != len(expected_years) or {d.year for d in days} != expected_years or any(d.month != month or d not in by_product["P3M"] for d in days):
            raise CalendarError("warm/cold issue calendar is incomplete")
    if now is not None and not start <= _aware(now).date() <= end:
        raise CalendarCoverageError("official issue calendar does not cover the current date")
    return payload


def parse_issue_calendar(html: str | bytes) -> dict[str, Any]:
    raw = html.encode("utf-8") if isinstance(html, str) else html
    source = raw.decode("utf-8")
    heading = re.search(r"<h1\b[^>]*>(.*?)</h1>", source, re.S | re.I)
    title = _text(heading[1]) if heading else ""
    coverage = re.findall(r"(令和|平成)(\d+)年(\d+)月", title)
    if len(coverage) != 2 or "季節予報の発表日" not in title:
        raise CalendarError("official calendar coverage heading is missing")
    (era1, year1, month1), (era2, year2, month2) = coverage
    start = date(_era_year(era1, year1), int(month1), 1)
    end_year, end_month = _era_year(era2, year2), int(month2)
    end = date(end_year, end_month, calendar.monthrange(end_year, end_month)[1])
    sections = {}
    for match in re.finditer(r"<h2\b[^>]*>(.*?)</h2>(.*?)(?=<h2\b|</main>)", source, re.S | re.I):
        name = _text(match[1])
        if any(label in name for label in ("1か月予報", "3か月予報", "暖候期予報", "寒候期予報")):
            sections[name] = _text(match[2])
    def section(label: str, time_label: str) -> str:
        values = [value for key, value in sections.items() if label in key and time_label in key]
        if len(values) != 1:
            raise CalendarError("official calendar product heading is missing or ambiguous")
        return values[0]
    if re.sub(r"\s+", "", section("1か月予報", "14時30分")) != "毎週木曜日":
        raise CalendarError("official weekly issue rule changed")
    issues = [_issue("P1M", start + timedelta(days=i)) for i in range((end - start).days + 1) if (start + timedelta(days=i)).weekday() == 3]
    for product, label in (("P3M", "3か月予報"), ("WARM", "暖候期予報"), ("COLD", "寒候期予報")):
        issues.extend(_issue(product, day) for day in _explicit_dates(section(label, "14時発表")))
    payload = {"schema_version": 1, "source_url": CALENDAR_URL, "source_sha256": hashlib.sha256(raw).hexdigest(),
               "coverage_start": start.isoformat(), "coverage_end": end.isoformat(),
               "issues": sorted(issues, key=lambda item: (item["report_datetime"], item["product"]))}
    return validate_issue_calendar(payload)


def _merge_calendars(previous: dict[str, Any] | None, current: dict[str, Any], now: datetime) -> dict[str, Any]:
    if previous is None:
        return current
    validate_issue_calendar(previous)
    old_start, old_end = date.fromisoformat(previous["coverage_start"]), date.fromisoformat(previous["coverage_end"])
    new_start, new_end = date.fromisoformat(current["coverage_start"]), date.fromisoformat(current["coverage_end"])
    if new_end < old_end:
        raise CalendarError("official calendar coverage regressed")
    if old_end + timedelta(days=1) < new_start:
        return current
    # The next fiscal-year page can be published before its first month.
    # Retain the past year relative to the actual check date, rather than
    # relative to that future page, so still-required February editions remain.
    today = _aware(now).date()
    keep_start = max(old_start, date(today.year - 1, today.month, 1))
    previous_issues = [item for item in previous["issues"] if keep_start <= _aware(item["report_datetime"]).date() < new_start]
    merged = {key: value for key, value in current.items() if key not in RUNTIME_FIELDS}
    merged.update(coverage_start=min(keep_start, new_start).isoformat(), issues=sorted(previous_issues + current["issues"], key=lambda item: (item["report_datetime"], item["product"])))
    return validate_issue_calendar(merged)


def _fetch_calendar_html() -> bytes:
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    request = urllib.request.Request(CALENDAR_URL, headers={"User-Agent": "NatureWxLab Climate Outlook Navi calendar collector"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=20, context=context) as response:
                return response.read()
        except (OSError, TimeoutError):
            if attempt:
                raise
            time.sleep(0.5)
    raise AssertionError("unreachable calendar fetch state")


def load_issue_calendar(cache_path: Path | str | None = DEFAULT_CACHE, *, fetcher: Callable[[str], bytes] | None = None,
                        now: datetime | None = None) -> dict[str, Any]:
    """Load a fresh official calendar, or an already validated cache on failure.

    The optional fetcher receives CALENDAR_URL; it owns its own bounded retry.
    A cache outside today's coverage never turns calendar failure into success.
    """
    now = now or datetime.now(timezone.utc)
    cached = None
    if cache_path is not None:
        try:
            cached = validate_issue_calendar(json.loads(Path(cache_path).read_text(encoding="utf-8")))
        except (OSError, ValueError, KeyError, TypeError):
            pass
    error_code = "fetch_failed"
    try:
        raw = fetcher(CALENDAR_URL) if fetcher else _fetch_calendar_html()
        error_code = "source_invalid"
        fresh = _merge_calendars(cached, parse_issue_calendar(raw), now)
        validate_issue_calendar(fresh, now)
        return {**fresh, "acquisition_status": "fresh", "acquisition_error": None}
    except CalendarCoverageError:
        if cached is None:
            raise
        validate_issue_calendar(cached, now)
        return {**{key: value for key, value in cached.items() if key not in RUNTIME_FIELDS}, "acquisition_status": "cached", "acquisition_error": "source_invalid"}
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, RuntimeError):
        if cached is None:
            raise CalendarUnavailableError(f"official issue calendar unavailable: {error_code}") from None
        validate_issue_calendar(cached, now)
        return {**{key: value for key, value in cached.items() if key not in RUNTIME_FIELDS}, "acquisition_status": "cached", "acquisition_error": error_code}


def expected_issues_since(start: datetime | str, now: datetime | str, issue_calendar: dict[str, Any], *, grace_minutes: int = 10) -> list[dict[str, str]]:
    """List required regular editions from monitoring start through now's grace.

    Callers retaining one year of coverage should pass the later of monitoring
    start and their one-year retention cutoff. Conditional WARM/COLD revisions
    are captured by probing P6M on P3M dates, not treated as mandatory editions.
    """
    start, now = _aware(start), _aware(now)
    if not 0 <= grace_minutes <= 120 or start > now:
        raise CalendarError("invalid expected-issue interval or grace")
    validate_issue_calendar(issue_calendar, now)
    if start.date() < date.fromisoformat(issue_calendar["coverage_start"]):
        raise CalendarCoverageError("official issue calendar does not cover the monitoring interval")
    return [dict(issue) for issue in issue_calendar["issues"] if start <= _aware(issue["report_datetime"]) <= now - timedelta(minutes=grace_minutes)]


def probe_due(now: datetime | str, issue_calendar: dict[str, Any], *, monitoring_start: datetime | str | None = None,
              grace_minutes: int = 10) -> bool:
    """Whether a scheduled extra probe is after an official issue today (JST)."""
    now = _aware(now)
    start = _aware(monitoring_start) if monitoring_start is not None else datetime.combine(now.date(), day_time.min, JST)
    start = max(start, datetime.combine(now.date(), day_time.min, JST))
    if start > now:
        return False
    return bool(expected_issues_since(start, now, issue_calendar, grace_minutes=grace_minutes))


def review_issues_since(start: datetime | str, now: datetime | str,
                        issue_calendar: dict[str, Any]) -> list[dict[str, str]]:
    """Conditional seasonal revisions: March/April WARM and October COLD.

    These are checks on the official P3M date, not mandatory new editions.
    A valid unchanged P6M response completes that day's conditional check.
    """
    return [{"product": "COLD" if _aware(issue["report_datetime"]).month == 10 else "WARM",
             "report_datetime": issue["report_datetime"]}
            for issue in expected_issues_since(start, now, issue_calendar)
            if issue["product"] == "P3M" and _aware(issue["report_datetime"]).month in (3, 4, 10)]
