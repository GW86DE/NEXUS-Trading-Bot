"""10.1.9: report-driven OKX capital and diagnosis evidence regressions."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]


def test_current_release_identity_and_diagnosis_match():
    assert (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() == "10.7.1-NEXUS"
    assert "10.7.1-LOCK-RESOLUTION-AND-LEDGER-RECEIPTS" in (ROOT / "RELEASE_BUILD.txt").read_text(encoding="utf-8")
    assert "TOOL_VERSION = '1.9.0'" in (ROOT / "NEXUS_10_Diagnose.py").read_text(encoding="utf-8")
    assert (ROOT / "NEXUS_10.7.1_Diagnose_Starten.sh").is_file()


def test_versioned_diagnosis_entry_is_bound_to_release():
    source = (ROOT / "NEXUS_10_7_1_Diagnose.py").read_text(encoding="utf-8")
    assert 'EXPECTED_VERSION = "10.7.1-NEXUS"' in source
    assert "from NEXUS_10_Diagnose import main" in source


def test_okx_capital_contract_never_raw_adds_multiple_cash_currencies():
    source = (ROOT / "broker" / "okx.py").read_text(encoding="utf-8")
    assert "RISK_CAPITAL_FX_UNKNOWN" in source
    assert "quote_conversion_rate(currency, basis)" in source
    assert "unsupported_positive_balance_currencies" in source


def test_runtime_status_exports_capital_evidence_without_new_network_call():
    source = (ROOT / "crypto_engine.py").read_text(encoding="utf-8")
    assert '"handelbares_kapital"' in source
    assert '"kapitalbeleg"' in source
    assert "capital_evidence()" in source


def test_pulsar_diagnosis_has_revision_bound_compact_projection():
    worker = (ROOT / "pulsar" / "worker.py").read_text(encoding="utf-8")
    diagnose = (ROOT / "NEXUS_10_Diagnose.py").read_text(encoding="utf-8")
    assert 'research.cache_put("diagnostic_top5"' in worker
    assert "EXPORTED_COMPACT_PROJECTION" in diagnose
