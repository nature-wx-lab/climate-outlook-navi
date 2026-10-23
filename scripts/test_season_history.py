"""Seasonal periods and immutable one-year forecast history regressions."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import update_season_forecast as season


class SeasonalPeriodTests(unittest.TestCase):
    def test_warm_period_is_summer_not_the_whole_six_month_envelope(self):
        periods = season.term_periods("WARM", {"targetDatetime": "2026-03-01T00:00:00+09:00"})
        self.assertEqual(periods[0]["start"][:10], "2026-06-01")
        self.assertEqual(periods[0]["end"][:10], "2026-08-31")
        self.assertEqual(periods[1]["end"][:10], "2026-07-31")

    def test_cold_period_crosses_year_and_handles_leap_year(self):
        periods = season.term_periods("COLD", {"targetDatetime": "2027-10-01T00:00:00+09:00"})
        self.assertEqual(periods[0]["start"][:10], "2027-12-01")
        self.assertEqual(periods[0]["end"][:10], "2028-02-29")

    def test_warm_rainy_period_is_regional_and_allows_no_hokkaido_forecast(self):
        periods = season.term_periods("WARM", {"targetDatetime": "2026-03-01T00:00:00+09:00"})
        recipes = {"010100": [str(i) for i in range(376)], "011100": ["okinawa"]}
        result = season.build_element("WARM", "precipitation", {"010100": [[30, 40, 30]], "011100": [None, [20, 30, 50]]}, periods, recipes)
        rainy = result["terms"][1]["regions"]["okinawa"]
        self.assertEqual(rainy["start"][:10], "2026-05-01")
        self.assertEqual(rainy["end"][:10], "2026-06-30")
        self.assertEqual(rainy["probabilities"], [20, 30, 50])

    def test_weekly_terms_and_official_probability_validation(self):
        payload = {"targetDatetime": "2026-10-24T00:00:00+09:00", "timeDefines": ["2026-10-24T00:00:00+09:00", "2026-10-31T00:00:00+09:00", "2026-11-07T00:00:00+09:00"]}
        periods = season.term_periods("P1M", payload)
        self.assertEqual([p["end"][:10] for p in periods], ["2026-11-23", "2026-10-30", "2026-11-06", "2026-11-20"])
        with self.assertRaises(ValueError):
            season.normalize_triplet([30, 30, 30], "test")


class ArchiveTests(unittest.TestCase):
    def test_deduplication_revision_and_one_year_cutoff(self):
        parent = Path(__file__).resolve().parents[1] / "tmp"
        parent.mkdir(exist_ok=True)
        try:
            with tempfile.TemporaryDirectory(prefix="season-test-", dir=parent) as directory:
                root = Path(directory)
                now = datetime.now(timezone.utc)
                report = season.add_months(now, -6).isoformat()
                product = {"report_datetime": report, "target_datetime": report, "elements": {}, "status": "available"}
                geometry = {"type": "FeatureCollection", "features": []}
                source = {"P1M": {"source_sha256": "f" * 64}}
                season.archive_products(root, {"P1M": product}, geometry, source)
                first = (root / "history/index.json").read_bytes()
                season.archive_products(root, {"P1M": product}, geometry, source)
                self.assertEqual(first, (root / "history/index.json").read_bytes())
                source["P1M"]["source_sha256"] = "a" * 64
                season.archive_products(root, {"P1M": product}, geometry, source)
                index = json.loads((root / "history/index.json").read_text())
                self.assertEqual(len(index["editions"]), 2)
                self.assertTrue(index["editions"][0]["id"].endswith("a" * 12))
                self.assertNotEqual(index["editions"][0]["path"], index["editions"][1]["path"])
                product["report_datetime"] = season.add_months(now, -13).isoformat()
                source["P1M"]["source_sha256"] = "c" * 64
                season.archive_products(root, {"P1M": product}, geometry, source)
                self.assertEqual(len(json.loads((root / "history/index.json").read_text())["editions"]), 2)
                class FutureClock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return season.add_months(now, 8)
                with patch.object(season, "datetime", FutureClock):
                    season.archive_products(root, {}, geometry, {})
                self.assertEqual(json.loads((root / "history/index.json").read_text())["editions"], [])
                self.assertEqual(list((root / "history/issues").glob("*.json")), [])
        finally:
            try:
                parent.rmdir()
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
