"""Bounded regressions for seasonal probes and publication quality gates."""

from __future__ import annotations

import os
import re
import subprocess
import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/update-and-deploy.yml"


def step(name: str) -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index(f"      - name: {name}\n")
    end = text.find("\n      - name:", start + 1)
    return text[start:end if end >= 0 else None]


def run_script(name: str) -> str:
    lines = step(name).splitlines()
    start = lines.index("        run: |") + 1
    script = []
    for line in lines[start:]:
        if line and not line.startswith("          "):
            break
        script.append(line[10:] if line else "")
    return "\n".join(script)


def condition(name: str, event: str, schedule: str = "", *, refresh: bool = True,
              observations: bool = True) -> bool:
    expression = next(
        line.removeprefix("        if: ")
        for line in step(name).splitlines()
        if line.startswith("        if: ")
    )
    for key, value in (
        ("github.event_name", event),
        ("github.event.schedule", schedule),
        ("inputs.refresh_observations", observations),
        ("inputs.refresh", refresh),
    ):
        expression = expression.replace(key, repr(value))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    return bool(eval(expression, {"__builtins__": {}}, {}))


def shell(script: str, **values: str) -> str:
    # Execute the actual workflow branch scripts with in-process shell
    # functions replacing Python and Git. No updater, Git mutation, network
    # call, or test file is created by these checks.
    mocks = """
python() { printf '%s\n' "$*"; }
git() {
  case "$1" in
    status) if test -n "$TEST_STATUS"; then printf '%s\n' "$TEST_STATUS"; fi ;;
    rev-parse) printf '%s\n' fixture-verified-commit ;;
    config|add|diff|commit) return 0 ;;
    *) return 1 ;;
  esac
}
"""
    env = {
        "PATH": os.defpath,
        "GITHUB_OUTPUT": "/dev/stdout",
        "TEST_STATUS": "",
        **values,
    }
    return subprocess.run(
        ["bash", "-e", "-c", mocks + script],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


class SeasonCollectionWorkflowTests(unittest.TestCase):
    def test_daily_observations_and_three_conditional_retry_slots(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(
            re.findall(r'    - cron: "([^"]+)"', text),
            ["5 6 * * *", "45 5 * * *", "5 7 * * *", "5 9 * * *"],
        )
        self.assertIn("cancel-in-progress: false", text)

    def test_observations_run_only_on_daily_or_explicit_manual_refresh(self):
        name = "Refresh validated recent-temperature derivative"
        self.assertTrue(condition(name, "schedule", "5 6 * * *"))
        for cron in ("45 5 * * *", "5 7 * * *", "5 9 * * *"):
            with self.subTest(cron=cron):
                self.assertFalse(condition(name, "schedule", cron))
        self.assertFalse(condition(name, "push"))
        self.assertTrue(condition(name, "workflow_dispatch"))
        self.assertFalse(condition(name, "workflow_dispatch", observations=False))
        self.assertFalse(condition(name, "workflow_dispatch", refresh=False))

    def test_every_slot_uses_pending_products_only_and_calendar_refresh_is_daily(self):
        name = "Refresh validated seasonal forecast derivative"
        self.assertTrue(condition(name, "schedule", "45 5 * * *"))
        self.assertTrue(condition(name, "schedule", "5 6 * * *"))
        self.assertFalse(condition(name, "push"))
        self.assertFalse(condition(name, "workflow_dispatch", refresh=False))
        script = run_script(name)
        self.assertEqual(
            shell(script, REFRESH_SEASON_CALENDAR="true").strip(),
            "-B scripts/update_season_forecast.py --refresh-calendar",
        )
        self.assertEqual(
            shell(script, REFRESH_SEASON_CALENDAR="false").strip(),
            "-B scripts/update_season_forecast.py",
        )

    def test_only_an_entirely_unchanged_probe_can_skip_candidate_validation(self):
        script = run_script("Determine whether candidate validation is needed")
        self.assertIn("verify_candidate=false", shell(script, SEASON_PROBE="true"))
        self.assertIn("verify_candidate=true", shell(script, SEASON_PROBE="false"))
        for status in (
            " M data/season/history/index.json",
            "?? data/season/collection-state.json",
            " M data/recent-temperature/manifest.json",
            "?? unexpected.json",
        ):
            with self.subTest(status=status):
                self.assertIn(
                    "verify_candidate=true",
                    shell(script, SEASON_PROBE="true", TEST_STATUS=status),
                )

    def test_noop_probe_cannot_start_a_payload_or_deployment(self):
        script = run_script("Commit changed outlook data locally")
        env = {"EVENT_NAME": "schedule", "REPOSITORY_VISIBILITY": "public", "MANUAL_DEPLOY": ""}
        output = shell(script, **env)
        self.assertIn("changed=false", output)
        self.assertIn("should_deploy=false", output)
        output = shell(script, TEST_STATUS=" M data/season/history/index.json", **env)
        self.assertIn("changed=true", output)
        self.assertIn("should_deploy=true", output)

    def test_independent_successes_still_pass_full_quality_checks_before_publication(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        seasonal = step("Refresh validated seasonal forecast derivative")
        self.assertIn("        id: season_refresh", seasonal)
        self.assertIn("        continue-on-error: true", seasonal)
        checks = step("Verify candidate before commit")
        self.assertIn("if: steps.candidate.outputs.verify_candidate == 'true'", checks)
        self.assertNotIn("continue-on-error", checks)
        for command in (
            "python scripts/verify_contract.py",
            "python scripts/privacy_gate.py",
            "git diff --check",
        ):
            self.assertIn(command, checks)
        self.assertLess(
            text.index("- name: Verify candidate before commit"),
            text.index("- name: Commit changed outlook data locally"),
        )
        self.assertIn("season_refresh_failed: ${{ steps.season_refresh.outcome == 'failure' }}", text)
        self.assertIn("  season-status:\n    needs: [prepare, verify-public]", text)
        self.assertIn("if: always() && needs.prepare.outputs.season_refresh_failed == 'true'", text)
        for command in (
            "python scripts/verify_contract.py --site-root _site",
            "python scripts/privacy_gate.py --root _site --skip-history",
            "python scripts/verify_deployed_pages.py",
        ):
            self.assertIn(command, text)


if __name__ == "__main__":
    unittest.main()
