"""Offline-Teststarter fuer Intelligence/Human-Gate aus der GUI."""
from __future__ import annotations
import subprocess,sys
from pathlib import Path
from console_io import configure_utf8_console
configure_utf8_console()
ROOT=Path(__file__).resolve().parent
FILES=[
 "tests/test_ai_attention.py","tests/test_strategy_analyst.py","tests/test_weekly_intelligence.py",
 "tests/test_universe_research.py","tests/test_universe_review.py","tests/test_universe_human_gate.py",
 "tests/test_telegram_universe_callbacks.py","tests/test_v512_regressions.py",
]
def main():
    print("INTELLIGENCE / HUMAN-GATE OFFLINE-TESTS - v6.0 Claude",flush=True)
    return subprocess.call([sys.executable,"-m","pytest","-q",*FILES],cwd=ROOT)
if __name__=="__main__": raise SystemExit(main())
