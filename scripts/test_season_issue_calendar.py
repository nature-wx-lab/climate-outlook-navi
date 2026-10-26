"""Small calendar fixtures; no live network or generated fixture files."""

import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import season_issue_calendar as issue_calendar


def official_fixture(start_year=2026):
    # The first fixture transcribes the current official HTML's explicit dates.
    # The following-year fixture is deliberately synthetic for cache rollover.
    first = [(3, 24), (4, 21), (5, 19), (6, 23), (7, 21), (8, 25), (9, 18), (10, 20), (11, 24), (12, 22)]
    second = [(1, 19), (2, 22), (3, 23)]
    era = start_year - 2018
    month_text = "、".join(f"{month}月{day}日" for month, day in first)
    next_text = "、".join(f"{month}月{day}日" for month, day in second)
    return f"""<main><h1>季節予報の発表日（令和{era}年3月～令和{era + 1}年3月）</h1>
    <h2>２週間気温予報（14時30分発表）</h2><p>毎日</p>
    <h2>１か月予報（14時30分発表）</h2><p>毎週木曜日</p>
    <h2>３か月予報（14時発表）</h2><p>令和{era}年<br>{month_text}</p><p>令和{era + 1}年<br>{next_text}</p>
    <h2>寒候期予報（14時発表）</h2><p>令和{era}年9月18日　３か月予報と同時発表</p>
    <h2>暖候期予報（14時発表）</h2><p>令和{era + 1}年2月22日　３か月予報と同時発表</p>
    <h2>季節予報発表日カレンダー</h2><p>図</p></main>"""


class IssueCalendarTests(unittest.TestCase):
    def setUp(self):
        self.calendar = issue_calendar.parse_issue_calendar(official_fixture())

    def test_explicit_exception_dates_and_all_four_products(self):
        editions = self.calendar["issues"]
        self.assertIn({"product": "P3M", "report_datetime": "2026-09-18T14:00:00+09:00"}, editions)
        self.assertIn({"product": "P3M", "report_datetime": "2027-02-22T14:00:00+09:00"}, editions)
        self.assertIn({"product": "WARM", "report_datetime": "2027-02-22T14:00:00+09:00"}, editions)
        self.assertIn({"product": "COLD", "report_datetime": "2026-09-18T14:00:00+09:00"}, editions)
        self.assertEqual(len([x for x in editions if x["product"] == "P3M"]), 13)
        self.assertEqual(self.calendar["coverage_end"], "2027-03-31")

    def test_incomplete_monthly_or_changed_weekly_rules_are_rejected(self):
        with self.assertRaisesRegex(issue_calendar.CalendarError, "monthly forecast calendar is incomplete"):
            issue_calendar.parse_issue_calendar(official_fixture().replace("9月18日、", "", 1))
        with self.assertRaisesRegex(issue_calendar.CalendarError, "weekly issue rule changed"):
            issue_calendar.parse_issue_calendar(official_fixture().replace("毎週木曜日", "毎週木曜日、ただし祝日は除く"))

    def test_p3m_and_p1m_completion_grace_is_respected(self):
        start = "2026-10-20T00:00:00+09:00"
        self.assertEqual(issue_calendar.expected_issues_since(start, "2026-10-20T14:09:59+09:00", self.calendar), [])
        self.assertEqual([x["product"] for x in issue_calendar.expected_issues_since(start, "2026-10-20T14:10:00+09:00", self.calendar)], ["P3M"])
        self.assertFalse(issue_calendar.probe_due("2026-10-22T14:39:59+09:00", self.calendar))
        self.assertTrue(issue_calendar.probe_due("2026-10-22T14:40:00+09:00", self.calendar))

    def test_probe_uses_jst_day_and_does_not_repeat_next_day(self):
        self.assertTrue(issue_calendar.probe_due("2026-10-22T05:45:00+00:00", self.calendar))
        self.assertFalse(issue_calendar.probe_due("2026-10-22T15:10:00+00:00", self.calendar))
        self.assertFalse(issue_calendar.probe_due("2026-10-22T14:45:00+09:00", self.calendar, monitoring_start="2026-10-22T15:00:00+09:00"))

    def test_monitoring_starts_at_deployment_and_never_invents_old_coverage(self):
        self.assertEqual(issue_calendar.expected_issues_since("2026-10-26T15:00:00+09:00", "2026-10-26T18:00:00+09:00", self.calendar), [])
        with self.assertRaises(issue_calendar.CalendarCoverageError):
            issue_calendar.expected_issues_since("2026-02-28T15:00:00+09:00", "2026-10-26T18:00:00+09:00", self.calendar)
        with self.assertRaises(issue_calendar.CalendarCoverageError):
            issue_calendar.probe_due("2027-04-01T14:45:00+09:00", self.calendar)

    def test_failed_fetch_uses_valid_cache_and_only_a_fixed_error_code(self):
        with patch.object(Path, "read_text", return_value=json.dumps(self.calendar)):
            def unavailable(_):
                raise OSError("private diagnostic must not enter public JSON")
            result = issue_calendar.load_issue_calendar("unused", fetcher=unavailable, now=datetime.fromisoformat("2026-10-26T15:00:00+09:00"))
        self.assertEqual(result["acquisition_status"], "cached")
        self.assertEqual(result["acquisition_error"], "fetch_failed")
        self.assertNotIn("private diagnostic", json.dumps(result))

    def test_invalid_source_can_fallback_but_expired_cache_cannot(self):
        with patch.object(Path, "read_text", return_value=json.dumps(self.calendar)):
            result = issue_calendar.load_issue_calendar("unused", fetcher=lambda _: b"<h1>changed</h1>", now=datetime.fromisoformat("2026-10-26T15:00:00+09:00"))
            self.assertEqual(result["acquisition_error"], "source_invalid")
            with self.assertRaises(issue_calendar.CalendarCoverageError):
                issue_calendar.load_issue_calendar("unused", fetcher=lambda _: b"<h1>changed</h1>", now=datetime.fromisoformat("2027-04-01T15:00:00+09:00"))

    def test_fresh_load_and_rollover_preserve_past_calendar_dates(self):
        with patch.object(Path, "read_text", return_value=json.dumps(self.calendar)):
            result = issue_calendar.load_issue_calendar("unused", fetcher=lambda _: official_fixture(2027).encode(), now=datetime.fromisoformat("2027-04-01T15:00:00+09:00"))
        self.assertEqual(result["acquisition_status"], "fresh")
        self.assertEqual(result["coverage_start"], "2026-04-01")
        self.assertEqual(result["coverage_end"], "2028-03-31")
        self.assertIn({"product": "COLD", "report_datetime": "2026-09-18T14:00:00+09:00"}, result["issues"])

    def test_early_next_year_page_preserves_the_actual_previous_year_month(self):
        with patch.object(Path, "read_text", return_value=json.dumps(self.calendar)):
            first = issue_calendar.load_issue_calendar("unused", fetcher=lambda _: official_fixture(2027).encode(), now=datetime.fromisoformat("2027-04-01T15:00:00+09:00"))
        with patch.object(Path, "read_text", return_value=json.dumps(first)):
            early = issue_calendar.load_issue_calendar("unused", fetcher=lambda _: official_fixture(2028).encode(), now=datetime.fromisoformat("2028-02-01T15:00:00+09:00"))
        self.assertEqual(early["coverage_start"], "2027-02-01")
        expected = issue_calendar.expected_issues_since("2027-02-01T15:00:00+09:00", "2028-02-01T15:00:00+09:00", early)
        self.assertIn({"product": "WARM", "report_datetime": "2027-02-22T14:00:00+09:00"}, expected)

    def test_no_valid_source_or_cache_fails_closed(self):
        with self.assertRaises(issue_calendar.CalendarUnavailableError):
            issue_calendar.load_issue_calendar(None, fetcher=lambda _: b"{}", now=datetime.fromisoformat("2026-10-26T15:00:00+09:00"))
        with self.assertRaises(issue_calendar.CalendarCoverageError):
            issue_calendar.load_issue_calendar(None, fetcher=lambda _: official_fixture().encode(), now=datetime.fromisoformat("2027-04-01T15:00:00+09:00"))

    def test_default_html_fetch_has_one_bounded_retry(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = official_fixture().encode()
        with patch.object(issue_calendar.urllib.request, "urlopen", side_effect=[OSError("temporary"), response]) as request:
            with patch.object(issue_calendar.time, "sleep") as sleep:
                raw = issue_calendar._fetch_calendar_html()
        self.assertEqual(raw, official_fixture().encode())
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(0.5)


if __name__ == "__main__":
    unittest.main()
