"""10.1.10: WebUI-Integration des read-only Universum-Backtests.

Geprueft werden Parametervertrag, Ergebnisvalidierung und die
Oberflaechenverdrahtung. Es wird kein Backtest ausgefuehrt und kein
Netzwerkzugriff erzeugt.
"""
import json
import os
from pathlib import Path
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]


class BacktestJobContract(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["TRADINGBOT_TEST_STATE_DIR"] = self._tmp.name
        from webui import backtest_jobs
        self.jobs = backtest_jobs

    def tearDown(self):
        os.environ.pop("TRADINGBOT_TEST_STATE_DIR", None)
        self._tmp.cleanup()

    def test_script_is_part_of_the_release(self):
        self.assertTrue(self.jobs.SCRIPT.is_file(), "Backtest-Werkzeug fehlt im Quellordner")

    def test_option_validation_accepts_defaults_and_bounds(self):
        checked = self.jobs._validated_options({})
        self.assertEqual(checked, {"umfang": "fokus", "aktien_limit": 15,
                                   "krypto_limit": 5, "nur_plan": False})
        checked = self.jobs._validated_options(
            {"umfang": "aktiv", "aktien_limit": 40, "krypto_limit": 1, "nur_plan": True})
        self.assertEqual(checked["umfang"], "aktiv")
        self.assertTrue(checked["nur_plan"])

    def test_option_validation_rejects_bad_input(self):
        for bad in ({"umfang": "alles"}, {"aktien_limit": 0}, {"aktien_limit": 41},
                    {"krypto_limit": 11}, {"nur_plan": "ja"}, {"extra": 1},
                    {"aktien_limit": "viele"}, "kein-dict"):
            with self.assertRaises(ValueError):
                self.jobs._validated_options(bad)

    def test_script_arguments_stay_read_only(self):
        args = self.jobs._script_arguments(
            {"umfang": "fokus", "aktien_limit": 12, "krypto_limit": 3, "nur_plan": True})
        self.assertIn("--nur-plan", args)
        self.assertIn("--nexus-root", args)
        self.assertNotIn("--live", " ".join(args))

    def test_result_paths_reject_foreign_files(self):
        identity = uuid.uuid4().hex
        directory = self.jobs.job_dir()
        directory.mkdir(parents=True, exist_ok=True)
        row = {"id": identity, "status": "COMPLETED",
               "archive": "/etc/passwd", "report": str(ROOT / "README.md")}
        (directory / (identity + ".json")).write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaises(ValueError):
            self.jobs.archive_path(identity)
        with self.assertRaises(ValueError):
            self.jobs.report_path(identity)

    def test_status_reports_jobs_without_starting_anything(self):
        state = self.jobs.status()
        self.assertIn("jobs", state)
        self.assertIn("busy", state)
        self.assertIn("output_directory", state)


class BacktestSurfaceWiring(unittest.TestCase):
    def test_routes_exist(self):
        source = (ROOT / "webui" / "app.py").read_text(encoding="utf-8")
        for route in ('"/backtest"', '"/api/backtest"',
                      '"/api/backtest/{identity}/download"',
                      '"/api/backtest/{identity}/report"',
                      '"/api/backtest/{identity}/log"'):
            self.assertIn(route, source, route)

    def test_template_matches_house_style(self):
        page = (ROOT / "webui" / "templates" / "backtest.html").read_text(encoding="utf-8")
        for needle in ('href="/backtest"', "backtest.js", "common.js",
                       "erstellt keine Orders", "ZIP", 'name="csrf"',
                       'name="viewport"'):
            self.assertIn(needle, page, needle)

    def test_navigation_injects_backtest_and_more_menu(self):
        script = (ROOT / "webui" / "static" / "common.js").read_text(encoding="utf-8")
        self.assertIn("'/backtest','Backtest'", script)
        self.assertIn("nav-more", script)
        style = (ROOT / "webui" / "static" / "nexus.css").read_text(encoding="utf-8")
        self.assertIn(".nav-more-panel", style)
        self.assertIn(".backtest-log", style)


if __name__ == "__main__":
    unittest.main()
