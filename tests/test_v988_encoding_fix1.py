"""Regression: UTF-8 survives isolation and chart protocols are explicit.

Child processes use real Python/Node and the normal offline network guard.
The C/POSIX countercases work without installing any additional OS locale.
No accounting fixture or expected financial/chart assertion is weakened.
"""
from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import offline_validation as isolation
from release_unpack import unpack_release, RELEASE_ROOT
from test_v975_release_unpack import payload

ROOT = Path(__file__).resolve().parents[1]


def run_child(tmp_path, code, *, native=False, loc="C"):
    inherited = {"PATH": os.environ.get("PATH", os.defpath), "LANG": loc,
                 "LC_ALL": loc, "TZ": "Europe/Berlin",
                 "PYTHONUTF8": "0", "PYTHONIOENCODING": "latin-1"}
    env = isolation.test_environment(ROOT, tmp_path, inherited)
    if native:
        # Deliberately bypass the normalization ONLY in this negative fixture.
        # Test helpers must still decode their explicit UTF-8 protocols safely.
        env.update(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0",
                   PYTHONIOENCODING="utf-8:strict")
    return subprocess.run(
        [sys.executable, str(ROOT / "offline_test_bootstrap/run_python.py"), "-c", code],
        env=env, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="strict", timeout=35,
    )


@pytest.mark.parametrize("loc", ["C", "POSIX", "C.UTF-8"])
@pytest.mark.parametrize("inherited_mode", ["0", "1"])
def test_encoding_is_reestablished_not_inherited_and_locale_tz_are_preserved(tmp_path,loc,inherited_mode):
    inherited = {"LANG":loc, "LC_ALL":loc, "TZ":"Europe/Berlin", "PYTHONUTF8":inherited_mode,
                 "PYTHONIOENCODING":"latin-1:replace", "OPENAI_API_KEY":"PRIVATE_SENTINEL",
                 "PYTEST_ADDOPTS":"--ignore=tests", "PYTHONPATH":"/untrusted"}
    before = dict(inherited)
    env = isolation.test_environment(ROOT,tmp_path,inherited)
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8:strict"
    assert all(env[k] == inherited[k] for k in ("LANG","LC_ALL","TZ"))
    assert "OPENAI_API_KEY" not in env and "PYTEST_ADDOPTS" not in env
    assert env["PYTHONPATH"] != inherited["PYTHONPATH"] and inherited == before


@pytest.mark.parametrize("loc", ["C", "POSIX", "C.UTF-8"])
def test_real_isolated_python_and_its_grandchild_keep_utf8(tmp_path,loc):
    code = r'''
from offline_test_bootstrap.encoding_contract import require_utf8
import json,subprocess,sys
first=require_utf8()
p=subprocess.run([sys.executable,"-c",
    "from offline_test_bootstrap.encoding_contract import require_utf8; require_utf8()"],
    capture_output=True,text=True,encoding="utf-8",errors="strict",check=True,timeout=10)
second=json.loads(p.stdout.split("TEST-ZEICHENCODIERUNG OK: ",1)[1])
assert first["utf8_mode"]==second["utf8_mode"]==1
assert first["default_file_encoding"]==second["default_file_encoding"]=="utf-8"
print("ROUNDTRIP_OK")
'''
    result = run_child(tmp_path,code,loc=loc)
    assert result.returncode == 0, result.stderr
    assert "ROUNDTRIP_OK" in result.stdout


@pytest.mark.parametrize("loc", ["C", "POSIX"])
def test_missing_startup_normalization_is_an_explicit_error(tmp_path,loc):
    result = run_child(tmp_path,
        'from offline_test_bootstrap.encoding_contract import require_utf8; require_utf8()',
        native=True,loc=loc)
    assert result.returncode != 0
    assert "nicht durchgaengig UTF-8" in result.stderr
    assert '"utf8_mode": 0' in result.stderr
    assert "TEST-ZEICHENCODIERUNG OK" not in result.stdout


@pytest.mark.parametrize("loc", ["C", "POSIX"])
def test_all_six_reported_chart_assertions_pass_even_without_utf8_mode(tmp_path,loc):
    # Run the EXACT existing assertions, including four parameter combinations.
    code = r'''
import sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/"tests"))
assert sys.flags.utf8_mode==0
from test_v981_performance import test_chart_keeps_null_gaps_and_has_accessible_table
from test_v988_import_migration_ui import (
 test_actual_javascript_renders_confirmed_partial_sum_with_unknown_day,
 test_unknown_only_day_is_not_drawn_as_zero_profit)
test_chart_keeps_null_gaps_and_has_accessible_table()
for mode in ["daily","cumulative"]:
 for unit in ["net","percent"]:
  test_actual_javascript_renders_confirmed_partial_sum_with_unknown_day(mode,unit)
test_unknown_only_day_is_not_drawn_as_zero_profit()
print("SIX_ORIGINAL_ASSERTIONS_OK")
'''
    result = run_child(tmp_path,code,native=True,loc=loc)
    assert result.returncode == 0, result.stderr
    assert "SIX_ORIGINAL_ASSERTIONS_OK" in result.stdout


def test_chart_renderer_declares_strict_utf8_without_changing_the_js():
    tree = ast.parse((ROOT/"tests/test_v988_import_migration_ui.py").read_text(encoding="utf-8"))
    render = next(node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=="render")
    call = next(node for node in ast.walk(render) if isinstance(node,ast.Call)
                and isinstance(node.func,ast.Attribute) and node.func.attr=="run")
    keywords = {kw.arg:ast.literal_eval(kw.value) for kw in call.keywords
                if kw.arg in {"encoding","errors","check"}}
    assert keywords == {"encoding":"utf-8","errors":"strict","check":True}


def test_broken_utf8_node_output_is_rejected_not_replaced(tmp_path,monkeypatch):
    import test_v988_import_migration_ui as ui
    from test_v981_performance import row,NOW
    from trade_performance import aggregate
    real_run = subprocess.run
    def broken_node(command,**kwargs):
        # Controlled protocol corruption, not a fake successful chart.
        return real_run([sys.executable,"-c","import os; os.write(1,b'\\xff')"],**kwargs)
    monkeypatch.setattr(ui.subprocess,"run",broken_node)
    with pytest.raises(UnicodeDecodeError):
        ui.render(aggregate([row()],days=1,now=NOW))


def test_fix1_extracts_beside_original_failed_target_without_touching_it(tmp_path):
    assert RELEASE_ROOT=="TradingBot_v10.1.10_NEXUS"
    old=tmp_path/"TradingBot_v9.8.8_NEXUS";old.mkdir()
    states={"nexus_update_state.json":'{"phase":"FEHLGESCHLAGEN","start_boundary":false}',
            "decision_history.sqlite":"preserve latest state",
            "crypto_positions.json":"preserve holdings", "VERSION.txt":"9.8.8-NEXUS"}
    for name,value in states.items():(old/name).write_text(value,encoding="utf-8")
    before={p.name:p.read_bytes() for p in old.iterdir()}
    data,digest=payload();target=unpack_release(data,tmp_path,digest)
    assert target!=old and target.name==RELEASE_ROOT
    assert before=={p.name:p.read_bytes() for p in old.iterdir()}
    (target/"operator_state.json").write_text('"pausiert"',encoding="utf-8")
    assert unpack_release(data,tmp_path,digest)==target
    assert (target/"operator_state.json").read_text(encoding="utf-8")=='"pausiert"'
    assert before=={p.name:p.read_bytes() for p in old.iterdir()}


def test_new_sibling_uses_services_not_old_aborted_directory(tmp_path):
    from nexus_update import determine_source,UNITS
    active=tmp_path/"TradingBot_v9.8.7_NEXUS";active.mkdir()
    abandoned=tmp_path/"TradingBot_v9.8.8_NEXUS";abandoned.mkdir()
    target=tmp_path/RELEASE_ROOT;target.mkdir()
    states={u:{"WorkingDirectory":str(active),"ExecStart":str(active/".venv/bin/python"),
               "ActiveState":"active","UnitFileState":"enabled"} for u in UNITS}
    assert determine_source(states,target)==active
    assert not list(abandoned.iterdir()) and not list(target.iterdir())


def test_bootstrap_checks_encoding_before_test_import():
    source=(ROOT/"offline_test_bootstrap/run_checks.py").read_text(encoding="utf-8")
    assert source.index("require_utf8()") < source.index("import volltest")
    assert "TEST-ZEICHENCODIERUNG FEHLER" in source
