#!/usr/bin/env python3
"""Regression tests for the recent-temperature observation refresh."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

import update_recent_temperature_anomaly as updater


def dataset(dates: list[str], values: list[int | None]) -> dict[str, object]:
    return {
        "dates": dates,
        "stations": [{
            "obsdl_id": "a0001",
            "observed_tenths": values,
        }],
    }


class ObservationRefreshTests(unittest.TestCase):
    def refresh(
        self,
        payload: dict[str, object],
        target_end: date,
        retention_days: int,
        refreshed: list[int | None],
    ) -> bool:
        with patch.object(
            updater,
            "fetch_observations",
            return_value={"a0001": refreshed},
        ) as fetch:
            changed = updater.reconcile_retained_observations(
                payload,
                target_end,
                retention_days,
                0,
                object(),
            )
        fetch.assert_called_once()
        self.assertEqual(
            fetch.call_args.args[2],
            target_end - updater.timedelta(days=retention_days - 1),
        )
        self.assertEqual(fetch.call_args.args[3], target_end)
        return changed

    def test_same_end_backfills_and_revises_entire_retention_window(self) -> None:
        payload = dataset(
            ["2026-08-27", "2026-08-28", "2026-08-29"],
            [None, 250, 260],
        )
        changed = self.refresh(
            payload,
            date(2026, 8, 29),
            3,
            [240, 251, 260],
        )
        self.assertTrue(changed)
        self.assertEqual(
            payload["dates"],
            ["2026-08-27", "2026-08-28", "2026-08-29"],
        )
        self.assertEqual(
            payload["stations"][0]["observed_tenths"],
            [240, 251, 260],
        )

    def test_unchanged_window_is_a_noop_after_refresh(self) -> None:
        payload = dataset(
            ["2026-08-27", "2026-08-28", "2026-08-29"],
            [240, 250, 260],
        )
        changed = self.refresh(
            payload,
            date(2026, 8, 29),
            3,
            [240, 250, 260],
        )
        self.assertFalse(changed)

    def test_new_day_rebuilds_and_trims_to_retention_window(self) -> None:
        payload = dataset(
            ["2026-08-27", "2026-08-28", "2026-08-29"],
            [240, 250, 260],
        )
        changed = self.refresh(
            payload,
            date(2026, 8, 30),
            3,
            [251, 260, 270],
        )
        self.assertTrue(changed)
        self.assertEqual(
            payload["dates"],
            ["2026-08-28", "2026-08-29", "2026-08-30"],
        )
        self.assertEqual(
            payload["stations"][0]["observed_tenths"],
            [251, 260, 270],
        )

    def test_official_end_regression_fails_before_fetch(self) -> None:
        payload = dataset(
            ["2026-08-27", "2026-08-28", "2026-08-29"],
            [240, 250, 260],
        )
        with patch.object(updater, "fetch_observations") as fetch:
            with self.assertRaisesRegex(ValueError, "moved backwards"):
                updater.reconcile_retained_observations(
                    payload,
                    date(2026, 8, 28),
                    3,
                    0,
                    object(),
                )
        fetch.assert_not_called()

    def test_large_gap_self_heals_from_full_retention_window(self) -> None:
        payload = dataset(["2026-07-01"], [240])
        changed = self.refresh(
            payload,
            date(2026, 8, 1),
            3,
            [250, 260, 270],
        )
        self.assertTrue(changed)
        self.assertEqual(
            payload["dates"],
            ["2026-07-30", "2026-07-31", "2026-08-01"],
        )

    def test_existing_station_shape_is_validated_before_fetch(self) -> None:
        payload = dataset(
            ["2026-08-27", "2026-08-28", "2026-08-29"],
            [240, 250],
        )
        with patch.object(updater, "fetch_observations") as fetch:
            with self.assertRaisesRegex(ValueError, "length mismatch"):
                updater.reconcile_retained_observations(
                    payload,
                    date(2026, 8, 29),
                    3,
                    0,
                    object(),
                )
        fetch.assert_not_called()

    def test_missing_or_duplicate_obsdl_date_rows_are_rejected(self) -> None:
        expected = ["2026-08-28", "2026-08-29"]
        missing = "2026/8/28,25.0,8\n".encode("cp932")
        with self.assertRaisesRegex(ValueError, "missing OBS DL date rows"):
            updater.parse_obsdl(missing, ["a0001"], expected)

        duplicate = (
            "2026/8/28,25.0,8\n"
            "2026/8/28,25.1,8\n"
            "2026/8/29,25.2,8\n"
        ).encode("cp932")
        with self.assertRaisesRegex(ValueError, "duplicate OBS DL date row"):
            updater.parse_obsdl(duplicate, ["a0001"], expected)

    def test_five_day_period_uses_all_daily_normals_with_one_observation_gap(self) -> None:
        normal = [0] * len(updater.NORMAL_DAYS)
        for day, value in zip(
            ("08-25", "08-26", "08-27", "08-28", "08-29"),
            (100, 110, 120, 130, 140),
        ):
            normal[updater.NORMAL_DAY_INDEX[day]] = value
        five_day_normal = [0] * len(updater.NORMAL_DAYS)
        five_day_normal[updater.NORMAL_DAY_INDEX["08-25"]] = 130
        station = {
            "observed_tenths": [200, 220, None, 260, 280],
            "normal_tenths": normal,
            "normal_5day_tenths": five_day_normal,
        }
        dates = [
            "2026-08-25",
            "2026-08-26",
            "2026-08-27",
            "2026-08-28",
            "2026-08-29",
        ]
        self.assertEqual(
            updater.period_anomaly_tenths(station, dates, 0, 4),
            updater.Decimal("120"),
        )

    def test_memambetsu_missing_day_matches_official_five_day_anomaly(self) -> None:
        dates = [f"2026-09-{day}" for day in range(21, 26)]
        normal = [0] * len(updater.NORMAL_DAYS)
        for day, value in zip(dates, (151, 149, 146, 144, 142)):
            normal[updater.NORMAL_DAY_INDEX[day[5:]]] = value
        five_day_normal = [0] * len(updater.NORMAL_DAYS)
        five_day_normal[updater.NORMAL_DAY_INDEX["09-21"]] = 146
        station = {
            "observed_tenths": [189, 160, 156, 149, None],
            "normal_tenths": normal,
            "normal_5day_tenths": five_day_normal,
        }
        anomaly = updater.period_anomaly_tenths(station, dates, 0, 4)
        self.assertEqual(anomaly, updater.Decimal("17.5"))
        self.assertEqual(
            (anomaly / 10).quantize(updater.Decimal("0.1"), rounding=updater.ROUND_HALF_UP),
            updater.Decimal("1.8"),
        )
        station["observed_tenths"][0] = None
        self.assertIsNone(updater.period_anomaly_tenths(station, dates, 0, 4))

    def test_unreliable_daily_quality_is_not_promoted_to_valid(self) -> None:
        raw = "2026/9/25,13.3,4,1\n2026/9/26,12.3,8,1\n".encode("cp932")
        self.assertEqual(
            updater.parse_obsdl(raw, ["a1460"], ["2026-09-25", "2026-09-26"]),
            {"a1460": [None, 123]},
        )

    def test_hanamaki_missing_archive_normal_does_not_drop_valid_comparison(self) -> None:
        # Official OBS DL observations and daily normals, 2026-10-19..23.
        # tem00.csv published +1.8 C; the five-day archive has no 10-19 value.
        dates = updater.iso_dates(date(2026, 10, 19), date(2026, 10, 23))
        normal = [None] * len(updater.NORMAL_DAYS)
        for day, value in zip(dates, (121, 119, 117, 115, 114)):
            normal[updater.NORMAL_DAY_INDEX[day[5:]]] = value
        station = {
            "station_id": "33576",
            "observed_tenths": [151, 160, 163, 109, 93],
            "normal_tenths": normal,
            "normal_5day_tenths": [None] * len(updater.NORMAL_DAYS),
        }
        payload = {"dates": dates, "stations": [station]}
        with patch.object(updater, "MINIMUM_ACTIVE_STATIONS", 1):
            result = updater.verify_latest_five_day(
                payload, date(2026, 10, 23), {"33576": updater.Decimal("1.8")},
            )
        self.assertEqual(result["compared_station_count"], 1)
        self.assertEqual(result["maximum_absolute_difference_c"], 0)
        self.assertEqual(result["tolerance_c"], 0.1)
        # Existing non-null archive values must not restore a different
        # reference method on the next day.
        station["normal_5day_tenths"] = [109] * len(updater.NORMAL_DAYS)
        self.assertEqual(updater.period_anomaly_tenths(station, dates, 0, 4), updater.Decimal("18.2"))

        with patch.object(updater, "MINIMUM_ACTIVE_STATIONS", 1):
            with self.assertRaisesRegex(ValueError, "anomaly mismatches"):
                updater.verify_latest_five_day(
                    payload, date(2026, 10, 23), {"33576": updater.Decimal("2.0")},
                )
            with self.assertRaisesRegex(ValueError, "uncompared station IDs:.*99999"):
                updater.verify_latest_five_day(
                    payload, date(2026, 10, 23),
                    {"33576": updater.Decimal("1.8"), "99999": updater.Decimal("0")},
                )

        normal[updater.NORMAL_DAY_INDEX["10-23"]] = None
        self.assertIsNone(updater.period_anomaly_tenths(station, dates, 0, 4))

    def test_failed_crosscheck_never_writes_candidate_files(self) -> None:
        with patch.object(updater, "verify_latest_five_day", side_effect=ValueError("incomplete")):
            with patch.object(updater, "atomic_json") as write:
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    updater.write_dataset(
                        {"dates": ["2026-10-19", "2026-10-23"], "stations": [], "validation": {}},
                        125, date(2026, 10, 23), {},
                    )
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
