"""Private bootstrap for volltest; never starts the trading worker."""
import os
from pathlib import Path
import sys

root = Path(__file__).resolve().parents[1]
if os.environ.get("NEXUS_OFFLINE_TEST_ROOT") != str(root):
    raise SystemExit("Nur ueber volltest.py starten")
sys.path.insert(0, str(root))
from offline_test_bootstrap.network_guard import install
install()
from offline_test_bootstrap.encoding_contract import require_utf8
try:
    require_utf8()
except RuntimeError as exc:
    raise SystemExit("TEST-ZEICHENCODIERUNG FEHLER: " + str(exc)) from None
import volltest
raise SystemExit(volltest.main())
