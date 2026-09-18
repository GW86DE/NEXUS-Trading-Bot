"""Offline-Integrationsteststarter fuer Geldpfad/Broker-Sicherheitslogik."""
from __future__ import annotations
import subprocess,sys
from pathlib import Path
from console_io import configure_utf8_console
configure_utf8_console()
ROOT=Path(__file__).resolve().parent
FILES=[
 "tests/test_money_path_integration.py","tests/test_etoro_order_adapter.py","tests/test_order_execution.py",
 "tests/test_protection_confirmed.py","tests/test_risk_equity_guard.py","tests/test_candidate_gate.py",
 "tests/test_etoro_flr_resolution.py","tests/test_v512_regressions.py",
]
def main():
    print("INTEGRATIONSTESTS OFFLINE - v6.0 Claude",flush=True)
    return subprocess.call([sys.executable,"-m","pytest","-q",*FILES],cwd=ROOT)
if __name__=="__main__": raise SystemExit(main())
