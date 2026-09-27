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

    def test_five_day_period_uses_full_period_normal_with_one_gap(self) -> None:
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
            updater.Decimal("110"),
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


if __name__ == "__main__":
    unittest.main()
