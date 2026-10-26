"""Seasonal acquisition recovery tests with bounded, offline official-shape fixtures."""

from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import call, patch

import season_issue_calendar as issue_calendar
import update_season_forecast as season
import verify_contract as contract


JST = timezone(timedelta(hours=9))
SEED_TIME = datetime(2026, 10, 26, 15, 0, tzinfo=JST)
ISSUE_TIME = datetime(2026, 10, 29, 15, 0, tzinfo=JST)


def clock(now: datetime):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz is not None else now.replace(tzinfo=None)

    return patch.object(season, "datetime", FrozenDatetime)


def encoded(payload: dict) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def fixture_reference() -> tuple[dict, dict]:
    codes = ["hoppo", *(f"{index:06}" for index in range(375))]
    features = []
    for code in [*codes, *codes[:9]]:
        features.append({
            "type": "Feature",
            "properties": {"code": code, "name": f"region-{code}"},
            "geometry": {"type": "MultiPolygon", "coordinates": [[[
                [130, 30], [131, 30], [131, 31], [130, 30],
            ]]]},
        })
    regions = {"type": "FeatureCollection", "features": features}
    area = {"class15s": {code: {} for code in codes if code != "hoppo"}}
    reference = {
        "schema_version": 1,
        "regions": regions,
        "recipes": {code: list(codes) for code in season.FORECAST_ORDER},
        "boundaries": {"source_url": season.REGIONS_URL, "source_sha256": season.sha256_bytes(encoded(regions)), "etag": None},
        "area_hierarchy": {"source_url": season.AREA_URL, "source_sha256": season.sha256_bytes(encoded(area)), "etag": None},
    }
    return season.validate_reference(reference), area


def fixture_calendar() -> dict:
    issues = [{
        "product": "P1M",
        "report_datetime": f"2026-10-{day:02}T14:30:00+09:00",
    } for day in (1, 8, 15, 22, 29)]
    issues.append({"product": "P3M", "report_datetime": "2026-10-20T14:00:00+09:00"})
    return issue_calendar.validate_issue_calendar({
        "schema_version": 1,
        "source_url": issue_calendar.CALENDAR_URL,
        "source_sha256": "a" * 64,
        "coverage_start": "2026-10-01",
        "coverage_end": "2026-10-31",
        "issues": sorted(issues, key=lambda issue: (issue["report_datetime"], issue["product"])),
    }, SEED_TIME)


def fixture_product(product: str, report: str) -> dict:
    reported = datetime.fromisoformat(report)
    if product == "P1M":
        target = (reported + timedelta(days=2)).replace(hour=0, minute=0, second=0)
        times = [target + timedelta(days=offset) for offset in (0, 7, 14)]
    elif product == "P3M":
        target = reported.replace(month=11, day=1, hour=0, minute=0, second=0)
        times = [season.add_months(target, offset) for offset in range(3)]
    else:
        target = reported.replace(month=10, day=1, hour=0, minute=0, second=0)
        times = []
    result = {
        "reportDatetime": report,
        "targetDatetime": target.isoformat(),
        "targetDuration": {"COLD": "P5M"}.get(product, product),
        "durationType": "all",
        "timeDefines": [value.isoformat() for value in times],
    }
    for element in season.ELEMENTS:
        indices = season.SUPPORTED_TERMS[product][element]
        # One official forecast region covers all class15 cells in this small
        # fixture. Other recipes exist but are not simultaneously populated.
        result[element] = (
            {"010100": [[30, 40, 30] for _ in indices]}
            if indices and element != "snowfall" else {}
        )
    return result


class SeasonCollectionTests(unittest.TestCase):
    def setUp(self):
        self.parent = Path(__file__).resolve().parents[1] / "tmp"
        self.parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="season-collection-test-", dir=self.parent)
        self.site_root = Path(self.temporary.name)
        self.root = self.site_root / "data" / "season"
        self.root.mkdir(parents=True)
        self.args = argparse.Namespace(
            output=str(self.root), timeout=1, probe=False,
            allow_report_regression=False, keep_datasets=2, import_git_history=False,
        )
        self.reference, self.area = fixture_reference()
        self.calendar = fixture_calendar()
        self.payloads = {
            "P1M": fixture_product("P1M", "2026-10-22T14:30:00+09:00"),
            "P3M": fixture_product("P3M", "2026-10-20T14:00:00+09:00"),
            "COLD": fixture_product("COLD", "2026-09-18T14:00:00+09:00"),
        }

    def tearDown(self):
        self.temporary.cleanup()
        try:
            self.parent.rmdir()
        except OSError:
            pass

    def seed(self):
        with clock(SEED_TIME):
            season.atomic_json(self.root / season.REFERENCE_FILE, self.reference)
            season.atomic_json(self.root / season.CALENDAR_FILE, self.calendar)
            status = season.collection_status(self.root, SEED_TIME.isoformat(), self.calendar, {}, SEED_TIME)
            season.atomic_json(self.root / season.COLLECTION_FILE, status)
            products = {name: season.validate_product(name, payload, self.reference["recipes"]) for name, payload in self.payloads.items()}
            sources = {name: {"source_sha256": season.sha256_bytes(encoded(payload)), "etag": None, "last_modified": None} for name, payload in self.payloads.items()}
            season.promote_snapshot(
                self.args, products, sources, self.reference, SEED_TIME.isoformat(),
                (SEED_TIME.isoformat(), self.calendar, {}, SEED_TIME),
            )

    def latest(self) -> dict:
        manifest = json.loads((self.root / "manifest.json").read_text())
        return season.verified_json(self.root, manifest["files"]["latest"])

    def editions(self) -> list[dict]:
        return json.loads((self.root / "history/index.json").read_text())["editions"]

    def collected(self, product: str, report: str) -> bool:
        return any(entry["product"] == product and entry["report_datetime"] == report for entry in self.editions())

    def fetcher(self, *, failures=None, on_fetch=None):
        failures = failures or {}

        def fetch(url, timeout):
            if on_fetch is not None:
                on_fetch(url)
            if url in failures:
                raise failures[url]
            if url == season.REGIONS_URL:
                payload = self.reference["regions"]
            elif url == season.AREA_URL:
                payload = self.area
            else:
                source = next(name for name, value in season.PRODUCT_URLS.items() if value == url)
                payload = self.payloads["COLD" if source == "P6M" else source]
            raw = encoded(payload)
            return deepcopy(payload), {"etag": None, "last_modified": None, "content_length": str(len(raw))}, raw

        return fetch

    def update(self, *, now=ISSUE_TIME, failures=None, on_fetch=None):
        fresh_calendar = {**deepcopy(self.calendar), "acquisition_status": "fresh", "acquisition_error": None}
        with ExitStack() as stack:
            stack.enter_context(clock(now))
            stack.enter_context(patch.object(issue_calendar, "load_issue_calendar", return_value=fresh_calendar))
            stack.enter_context(patch.object(season, "build_recipes", return_value=deepcopy(self.reference["recipes"])))
            fetch = stack.enter_context(patch.object(season, "fetch_json", side_effect=self.fetcher(failures=failures, on_fetch=on_fetch)))
            result = season.update(self.args)
        return result, fetch

    def new_weekly(self):
        self.payloads["P1M"] = fixture_product("P1M", "2026-10-29T14:30:00+09:00")

    def replace_collection_status_with_valid_manifest_hash(self, status):
        path = self.root / season.COLLECTION_FILE
        season.atomic_json(path, status)
        manifest_path = self.root / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["files"]["collection"] = season.file_entry(self.root, path)
        season.atomic_json(manifest_path, manifest)

    def test_normal_collection_passes_the_public_season_contract(self):
        self.seed()
        self.new_weekly()
        result, _ = self.update()
        verified = contract.verify_season(self.site_root)
        self.assertTrue(result["collection_ok"])
        self.assertTrue(verified["checksums_ok"])
        self.assertTrue(verified["collection"]["complete"])
        self.assertEqual(verified["collection"]["missing_issue_count"], 0)

    def test_partial_collection_passes_data_contract_with_incomplete_status(self):
        self.seed()
        self.new_weekly()
        result, _ = self.update(failures={season.PRODUCT_URLS["P3M"]: TimeoutError("monthly unavailable")})
        verified = contract.verify_season(self.site_root)
        self.assertFalse(result["collection_ok"])
        self.assertTrue(verified["checksums_ok"])
        self.assertFalse(verified["collection"]["complete"])
        self.assertEqual(verified["collection"]["fetch_error_count"], 1)
        self.assertTrue(self.collected("P1M", "2026-10-29T14:30:00+09:00"))

    def test_omitting_expected_issue_cannot_hide_a_gap_even_with_matching_hash(self):
        self.seed()
        result, _ = self.update()
        self.assertFalse(contract.verify_season(self.site_root)["collection"]["complete"])
        status = deepcopy(result["collection_status"])
        self.assertEqual(status["missing_issue_count"], 1)
        status["expected_issues"] = []
        status["missing_issue_count"] = 0
        self.replace_collection_status_with_valid_manifest_hash(status)
        with self.assertRaisesRegex(AssertionError, "expected-issue calendar coverage mismatch"):
            contract.verify_season(self.site_root)

    def test_forged_missing_count_fails_contract_even_with_matching_hash(self):
        self.seed()
        result, _ = self.update()
        self.assertFalse(contract.verify_season(self.site_root)["collection"]["complete"])
        status = deepcopy(result["collection_status"])
        self.assertEqual(status["missing_issue_count"], 1)
        status["missing_issue_count"] = 0
        self.replace_collection_status_with_valid_manifest_hash(status)
        with self.assertRaisesRegex(AssertionError, "missing issue count mismatch"):
            contract.verify_season(self.site_root)

    def test_good_weekly_issue_is_checkpointed_before_monthly_fetch_fails(self):
        self.seed()
        self.new_weekly()
        witnessed = []

        def before_fetch(url):
            if url == season.PRODUCT_URLS["P3M"]:
                self.assertEqual(self.latest()["products"]["P1M"]["report_datetime"], "2026-10-29T14:30:00+09:00")
                self.assertTrue(self.collected("P1M", "2026-10-29T14:30:00+09:00"))
                manifest = json.loads((self.root / "manifest.json").read_text())
                season.verified_json(self.root, manifest["files"]["archive"])
                witnessed.append(True)

        result, _ = self.update(
            failures={season.PRODUCT_URLS["P3M"]: TimeoutError("monthly unavailable")},
            on_fetch=before_fetch,
        )
        self.assertEqual(witnessed, [True])
        self.assertFalse(result["collection_ok"])
        self.assertIn("P3M", result["collection_status"]["fetch_errors"])
        self.assertEqual(self.latest()["products"]["P3M"]["report_datetime"], "2026-10-20T14:00:00+09:00")

    def test_invalid_monthly_probabilities_are_not_archived_or_blocking_weekly(self):
        self.seed()
        self.new_weekly()
        self.payloads["P3M"]["temperature"]["010100"][0] = [30, 30, 30]
        bad_digest = season.sha256_bytes(encoded(self.payloads["P3M"]))
        result, _ = self.update()
        self.assertFalse(result["collection_ok"])
        self.assertTrue(self.collected("P1M", "2026-10-29T14:30:00+09:00"))
        self.assertFalse(any(entry["id"].endswith(bad_digest[:12]) for entry in self.editions()))
        for term in self.latest()["products"]["P3M"]["elements"]["temperature"]["terms"]:
            self.assertTrue(all(sum(region["probabilities"]) == 100 for region in term["regions"].values()))

    def test_expected_issue_missing_state_recovers_after_the_next_collection(self):
        self.seed()
        first, _ = self.update()
        self.assertFalse(first["collection_ok"])
        self.assertEqual(first["collection_status"]["missing_issue_count"], 1)
        self.assertEqual(first["collection_status"]["expected_issues"][0]["status"], "missing")
        self.new_weekly()
        second, _ = self.update(now=ISSUE_TIME + timedelta(minutes=15))
        self.assertTrue(second["collection_ok"])
        self.assertEqual(second["collection_status"]["missing_issue_count"], 0)
        issue = second["collection_status"]["expected_issues"][0]
        self.assertEqual(issue["status"], "captured")
        self.assertTrue(issue["edition_ids"])

    def test_probe_on_an_off_day_never_fetches_products_or_references(self):
        self.seed()
        self.args.probe = True
        result, fetch = self.update(now=datetime(2026, 10, 27, 16, 5, tzinfo=JST))
        self.assertTrue(result["collection_ok"])
        self.assertTrue(result["probe_skipped"])
        self.assertFalse(result["changed"])
        fetch.assert_not_called()

    def test_verified_reference_fallback_still_checkpoints_good_new_products(self):
        self.seed()
        self.new_weekly()
        result, _ = self.update(failures={season.REGIONS_URL: OSError("reference unavailable")})
        self.assertTrue(self.collected("P1M", "2026-10-29T14:30:00+09:00"))
        self.assertFalse(result["collection_ok"])
        self.assertEqual(result["collection_status"]["fetch_errors"]["reference"], "official_reference_unavailable")
        self.assertEqual(self.latest()["products"]["P1M"]["report_datetime"], "2026-10-29T14:30:00+09:00")

    def test_tampered_reference_cache_fails_before_any_product_is_accepted(self):
        self.seed()
        self.new_weekly()
        path = self.root / season.REFERENCE_FILE
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            self.update(failures={season.REGIONS_URL: OSError("reference unavailable")})
        self.assertFalse(self.collected("P1M", "2026-10-29T14:30:00+09:00"))

    def test_atomic_replace_failure_preserves_old_file_and_removes_temporary_file(self):
        path = self.root / "atomic.json"
        old = season.atomic_json(path, {"value": "old"})
        before = set(self.root.iterdir())
        with patch.object(season.os, "replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                season.atomic_json(path, {"value": "new"})
        self.assertEqual(path.read_bytes(), old)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_json_fetch_retries_only_three_times_and_recovers(self):
        expected = ({"ok": True}, {}, b"{}")
        failure = TimeoutError("offline fixture")
        with patch.object(season, "fetch_json_once", side_effect=[failure, failure, expected]) as once:
            with patch.object(season.time, "sleep") as sleep:
                self.assertEqual(season.fetch_json("https://example.invalid/fixture", 1), expected)
        self.assertEqual(once.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1), call(2)])
        with patch.object(season, "fetch_json_once", side_effect=failure) as once:
            with patch.object(season.time, "sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "three attempts"):
                    season.fetch_json("https://example.invalid/fixture", 1)
        self.assertEqual(once.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(1), call(2)])


if __name__ == "__main__":
    unittest.main()
