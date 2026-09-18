"""Explicit network-guarded Python entry, independent of sitecustomize order."""
import os
from pathlib import Path
import runpy
import sys

root = Path(__file__).resolve().parents[1]
if os.environ.get("NEXUS_OFFLINE_TEST_ROOT") != str(root):
    raise SystemExit("Nur ueber volltest.py starten")
sys.path.insert(0, str(root))
from offline_test_bootstrap.network_guard import install
install()
args = sys.argv[1:]
if not args:
    raise SystemExit("Python-Testziel fehlt")
if args[0] == "-m" and len(args) > 1:
    sys.argv = args[1:]
    runpy.run_module(args[1], run_name="__main__", alter_sys=True)
elif args[0] == "-c" and len(args) > 1:
    sys.argv = ["-c", *args[2:]]
    exec(compile(args[1], "<isolated-test>", "exec"), {"__name__": "__main__"})
else:
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")
