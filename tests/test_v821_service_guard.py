"""Ohne echten systemd-Daemon pruefen, dass alte Units fail-closed bleiben."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
GUARD = ROOT / "Pi_Service_Unit_Pruefen.sh"


def _fake_systemctl(folder: Path, *, core_exec: str, web_exec: str) -> None:
    script = folder / "systemctl"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "if [[ \"$1\" == \"cat\" ]]; then\n"
        "  echo '[Service]'\n"
        "  if [[ \"$2\" == \"tradingbot-pi5.service\" ]]; then\n"
        f"    echo 'ExecStart={core_exec}'\n"
        "  else\n"
        f"    echo 'ExecStart={web_exec}'\n"
        "  fi\n"
        "  exit 0\n"
        "fi\n"
        "if [[ \"$1\" == \"show\" ]]; then exit 0; fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


class ServiceUnitGuardTests(unittest.TestCase):
    def _run(self, *, current: bool) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            if current:
                core = f"{ROOT}/.venv/bin/python {ROOT}/pi_service.py"
                web = f"{ROOT}/.venv/bin/python {ROOT}/webui_start.py"
            else:
                core = "/home/georg/Georg/TradingBot_v8.1.5_NEXUS/.venv/bin/python /home/georg/Georg/TradingBot_v8.1.5_NEXUS/pi_service.py"
                web = "/home/georg/Georg/TradingBot_v8.1.5_NEXUS/.venv/bin/python /home/georg/Georg/TradingBot_v8.1.5_NEXUS/webui_start.py"
            _fake_systemctl(temp, core_exec=core, web_exec=web)
            env = os.environ.copy()
            env["PATH"] = f"{temp}{os.pathsep}{env['PATH']}"
            return subprocess.run(
                ["bash", str(GUARD), "all"], cwd=ROOT, env=env,
                text=True, capture_output=True, check=False,
            )

    def test_current_units_are_accepted(self):
        result = self._run(current=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK: tradingbot-webui.service", result.stdout)

    def test_old_unit_is_never_started_silently(self):
        result = self._run(current=False)
        self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
        self.assertIn("zeigt nicht auf diesen Release-Ordner", result.stdout)


if __name__ == "__main__":
    unittest.main()
