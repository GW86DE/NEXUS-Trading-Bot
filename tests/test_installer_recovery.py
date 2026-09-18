import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_pi_installer_tests_before_creating_webui_state():
    script = (ROOT / "Pi_Installieren.sh").read_text(encoding="utf-8")

    test_position = script.index('"$PY" volltest.py')
    settings_position = script.index('"$PY" webui_network_setup.py')

    assert test_position < settings_position
    assert "TRADINGBOT_ALLOW_LOCAL_STATE=1" in script
    assert 'USER_NAME="$(id -un)"' in script


def test_webui_activation_recovers_missing_systemd_unit():
    script = (ROOT / "Pi_WebUI_Aktivieren.sh").read_text(encoding="utf-8")

    assert "tradingbot-webui.service.template" in script
    assert 'sudo install -m 0644 "$SERVICE_TMP" "$SERVICE_PATH"' in script
    assert "sudo systemctl daemon-reload" in script
    assert 'sudo systemctl enable --now "$SERVICE_NAME"' in script


def test_webui_activation_only_recovers_a_missing_unit_before_guarding_path():
    script = (ROOT / "Pi_WebUI_Aktivieren.sh").read_text(encoding="utf-8")

    recovery_position = script.index('if ! systemctl cat "$SERVICE_NAME"')
    guard_position = script.index("./Pi_Service_Unit_Pruefen.sh webui")

    assert recovery_position < guard_position
    assert "Existiert dagegen bereits eine Unit, wird sie" in script


def test_webui_activation_renders_and_installs_missing_unit_before_starting():
    """Ohne echtes systemd pruefen, dass Recovery die aktuelle Unit erzeugt."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "TradingBot_v8.2.2_NEXUS"
        root.mkdir()
        state = root / "state"
        state.mkdir()
        bin_dir = root / "bin"
        bin_dir.mkdir()
        python_dir = root / ".venv" / "bin"
        python_dir.mkdir(parents=True)
        (python_dir / "python").symlink_to(sys.executable)

        (root / "Pi_WebUI_Aktivieren.sh").write_text(
            (ROOT / "Pi_WebUI_Aktivieren.sh").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (root / "Pi_WebUI_Aktivieren.sh").chmod(0o755)
        (root / "tradingbot-webui.service.template").write_text(
            (ROOT / "tradingbot-webui.service.template").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (root / "VERSION.txt").write_text("8.2.2-NEXUS\n", encoding="utf-8")
        (root / "webui_setup.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        (root / "webui_network_setup.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
        (root / "Pi_Service_Unit_Pruefen.sh").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "test -f \"$NEXUS_TEST_STATE/unit-installed\"\n"
            "printf 'guard\\n' >> \"$NEXUS_TEST_STATE/log\"\n",
            encoding="utf-8",
        )
        (root / "Pi_Service_Unit_Pruefen.sh").chmod(0o755)

        (bin_dir / "sudo").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [[ \"$1\" == \"install\" ]]; then\n"
            "  cp \"$4\" \"$NEXUS_TEST_STATE/unit\"\n"
            "  : > \"$NEXUS_TEST_STATE/unit-installed\"\n"
            "  printf 'install\\n' >> \"$NEXUS_TEST_STATE/log\"\n"
            "  exit 0\n"
            "fi\n"
            "exec \"$@\"\n",
            encoding="utf-8",
        )
        (bin_dir / "systemctl").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [[ \"$1\" == \"cat\" ]]; then\n"
            "  test -f \"$NEXUS_TEST_STATE/unit-installed\" || exit 1\n"
            "  cat \"$NEXUS_TEST_STATE/unit\"\n"
            "  exit 0\n"
            "fi\n"
            "printf 'systemctl %s\\n' \"$*\" >> \"$NEXUS_TEST_STATE/log\"\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (bin_dir / "sleep").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        for command in ("sudo", "systemctl", "sleep"):
            (bin_dir / command).chmod(0o755)

        env = os.environ.copy()
        env["NEXUS_TEST_STATE"] = str(state)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        result = subprocess.run(
            ["bash", str(root / "Pi_WebUI_Aktivieren.sh")],
            cwd=root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        unit_text = (state / "unit").read_text(encoding="utf-8")
        assert f"WorkingDirectory={root}" in unit_text
        assert f"ExecStart={root}/.venv/bin/python {root}/webui_start.py" in unit_text
        assert "Description=TradingBot 8.2.2 NEXUS WebUI" in unit_text
        assert (state / "log").read_text(encoding="utf-8").splitlines() == [
            "install",
            "systemctl daemon-reload",
            "guard",
            "systemctl enable --now tradingbot-webui.service",
            "systemctl --no-pager --full status tradingbot-webui.service",
        ]
