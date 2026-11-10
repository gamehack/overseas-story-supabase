"""Static checks for the GitHub Actions runtime contract."""

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_weekly_schedule_matches_monday_0330_shanghai(self):
        workflow = (ROOT / ".github/workflows/weekly-pipeline.yml").read_text()
        self.assertIn('cron: "30 19 * * 0"', workflow)
        self.assertIn("workflow_dispatch:", workflow)

    def test_weekly_workflow_runs_m1_through_m4_in_order(self):
        workflow = (ROOT / ".github/workflows/weekly-pipeline.yml").read_text()
        commands = [
            "python -m overseas_stories.cli sync --source all --ai-limit 5",
            "python -m overseas_stories.editorial --apply",
            "python -m overseas_stories.localize --apply --limit 5",
        ]
        positions = [workflow.index(command) for command in commands]
        self.assertEqual(positions, sorted(positions))

    def test_weekly_workflow_has_no_server_runtime_dependency(self):
        workflow = (ROOT / ".github/workflows/weekly-pipeline.yml").read_text().lower()
        self.assertNotIn("docker compose", workflow)
        self.assertNotIn("systemd", workflow)
        self.assertIn("runs-on: ubuntu-latest", workflow)

    def test_production_requires_explicit_cutover(self):
        workflow = (ROOT / ".github/workflows/weekly-pipeline.yml").read_text()
        self.assertIn("vars.OVERSEAS_STORIES_PRODUCTION_ENABLED == 'true'", workflow)

    def test_integration_preflight_is_automatic_and_read_only(self):
        workflow = (ROOT / ".github/workflows/integration-preflight.yml").read_text()
        self.assertIn("OVERSEAS_STORIES_INTEGRATION_ENABLED == 'true'", workflow)
        self.assertIn("default_transaction_read_only=on", workflow)
        self.assertIn("--dry-run --source indie-md --ai-limit 0", workflow)
        self.assertNotIn("workflow_dispatch:", workflow)

    def test_automatic_integration_preflight_checks_real_gateway_independently(self):
        workflow = (ROOT / ".github/workflows/integration-preflight.yml").read_text()
        self.assertNotIn("workflow_dispatch:", workflow)
        self.assertIn("push:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("default_transaction_read_only=on", workflow)
        self.assertIn("synthetic founder story", workflow)
        self.assertIn("!cancelled()", workflow)
        self.assertNotIn("github.event_name == 'schedule'", workflow)

    def test_substack_requires_explicit_authorization(self):
        workflow = (ROOT / ".github/workflows/weekly-pipeline.yml").read_text()
        self.assertIn("vars.SUBSTACK_CONTENT_AUTHORIZED", workflow)


if __name__ == "__main__":
    unittest.main()
