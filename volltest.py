"""Versionsaktueller Offline-Volltest fuer TradingBot NEXUS.

Der Test fuehrt verhaltensbasierte Regressionen des Geldpfads mit Testdoubles,
den lokalen Selbsttest, Syntaxpruefung, Pi-Preflight und Shell-Syntaxpruefung aus.
Es werden keine echten Orders, Telegram-Nachrichten oder KI-Anfragen gesendet.
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _allow_local_state() -> bool:
    """Installed trees may legitimately contain private runtime state.

    The default remains strict so that a packaged release can never silently
    contain credentials or state databases.  The Pi installer opts in only for
    its repeatable local verification run, where those files can already exist
    after a partial installation or an update.
    """
    return os.getenv("TRADINGBOT_ALLOW_LOCAL_STATE", "").strip() == "1"

# Verzeichnisse, die nicht zum ausgelieferten TradingBot-Quellcode gehoeren.
# Besonders wichtig auf dem Raspberry Pi: Pi_Installieren.sh erzeugt .venv
# vor dem Volltest. Fremdpakete unter site-packages duerfen deshalb niemals
# als TradingBot-Releasecode bewertet werden.
HYGIENE_EXCLUDED_DIRS = {
    ".venv", "venv", "env", "site-packages",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".git", "build", "dist",
}

REQUIRED_RELEASE_FILES = (
    "CHANGELOG_v10.8.0_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.8.0_DE.md",
    "NEXUS_10.8.0_IMPLEMENTATION_REPORT.md", "NEXUS_10.8.0_Pruefbericht.md",
    "NEXUS_Architektur_Audit_2026-09-19.md", "validation/ARCHITEKTUR_BASELINE.json",
    "nexus/__init__.py", "nexus/weiche.py", "nexus/pfade.py",
    "nexus/architektur/__init__.py", "nexus/architektur/importgraph.py",
    "nexus/architektur/schichten.py", "nexus/architektur/schichten.json",
    "nexus/domain/__init__.py", "nexus/domain/ledger_result.py", "nexus/domain/okx_receipt_math.py",
    "nexus/application/__init__.py", "nexus/application/handelsfreigabe.py", "nexus/application/risk_levels.py",
    "nexus/ports/__init__.py", "nexus/adapters/__init__.py", "nexus/state/__init__.py", "nexus/interfaces/__init__.py",
    "ledger_result.py", "okx_receipt_math.py",
    "news_model.py", "installer_host.py", "risk_basis_status.py", "risk_grenzen.py",
    "etoro_nachlauf.py", "etoro_protection_readback.py", "fmp_kontext.py",
    "tests/test_v1080_architektur.py", "tests/test_v1080_pulsar_bestaetigung.py",
    "NEXUS_10_8_0_Diagnose.py", "NEXUS_10.8.0_Diagnose_Starten.sh",
    "CHANGELOG_v10.7.1_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.7.1_DE.md",
    "NEXUS_10.7.1_IMPLEMENTATION_REPORT.md", "NEXUS_10.7.1_Pruefbericht.md",
    "tests/test_v1071_sperren_aufloesung.py",
    "CHANGELOG_v10.7.0_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.7.0_DE.md",
    "NEXUS_10.7.0_IMPLEMENTATION_REPORT.md", "NEXUS_10.7.0_Pruefbericht.md",
    "handelsfreigabe.py", "tests/test_v1070_lot_rest.py", "tests/test_v1070_aktien_stop.py",
    "tests/test_v1070_gebuehrenbeleg.py", "tests/test_v1070_handelsfreigabe.py",
    "CHANGELOG_v10.6.0_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.6.0_DE.md",
    "NEXUS_10.6.0_IMPLEMENTATION_REPORT.md", "NEXUS_10.6.0_Pruefbericht.md",
    "risk_levels.py", "tests/test_v1060_einsatzstufen.py",
    "tests/test_v1060_bestandsrueckkehr.py",
    "CHANGELOG_v10.5.0_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.5.0_DE.md",
    "NEXUS_10.5.0_IMPLEMENTATION_REPORT.md", "NEXUS_10.5.0_Pruefbericht.md",
    "tests/test_v1050_pulsar_messung_quellen.py", "pulsar/stocktwits.py", "pulsar/short_interest.py",
    "pulsar/volume_watch.py", "pulsar/measurement.py",
    "webui/templates/diagnosis_report.html", "webui/static/diagnosis_report.js",
    "tests/fixtures/echt_stocktwits_stream_gme.json", "tests/fixtures/echt_stocktwits_trending.json",
    "tests/fixtures/echt_finra_short_interest.json",
    "CHANGELOG_v10.4.0_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.4.0_DE.md",
    "NEXUS_10.4.0_IMPLEMENTATION_REPORT.md", "NEXUS_10.4.0_Pruefbericht.md",
    "tests/test_v1040_scan_chart_existence.py", "scan_uebersicht.py", "etoro_chart_store.py",
    "trade_chart_data.py", "webui/static/echarts.min.js", "webui/static/echarts.LICENSE.txt",
    "CHANGELOG_v10.3.1_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.3.1_DE.md",
    "NEXUS_10.3.1_IMPLEMENTATION_REPORT.md", "NEXUS_10.3.1_Pruefbericht.md",
    "tests/test_v1031_etoro_cash_delta.py", "etoro_settlement_review.py",
    "CHANGELOG_v10.3.0_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.3.0_DE.md",
    "NEXUS_10.3.0_IMPLEMENTATION_REPORT.md", "NEXUS_10.3.0_Pruefbericht.md",
    "tests/test_v1030_okx_freigabe.py", "tests/test_v1030_pulsar_hype.py",
    "pulsar/evidence.py", "pulsar/telegram.py",
    "CHANGELOG_v10.2.2_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_10.2.2_DE.md",
    "okx_reference_autovaluation.py", "tests/test_v1021_composite_exit.py",
    "INSTALLATIONSANLEITUNG_NEXUS_10.2.1_DE.md", "CHANGELOG_v10.2.1_NEXUS.txt",
    "NEXUS_10.2.1_IMPLEMENTATION_REPORT.md", "NEXUS_10.2.1_Pruefbericht.md",
    "zusatz_strategien.py", "etoro_strategy_mode.py", "etoro_fee_snapshot.py",
    "INSTALLATIONSANLEITUNG_NEXUS_10.2.0_DE.md", "CHANGELOG_v10.2.0_NEXUS.txt",
    "NEXUS_10.2.0_IMPLEMENTATION_REPORT.md", "NEXUS_10.2.0_Pruefbericht.md",
    "tests/test_v1020_zusatzstrategien.py", "tests/test_v1020_exit_in_progress.py",
    "tests/test_v1020_multilane_equity.py", "tests/test_v1020_fee_snapshot.py",
    "NEXUS_Universum_Backtest_V6.sh", "webui/backtest_jobs.py",
    "webui/templates/backtest.html", "webui/static/backtest.js",
    "market_intelligence/account_registry.py",
    "INSTALLATIONSANLEITUNG_NEXUS_10.1.10_DE.md", "CHANGELOG_v10.1.10_NEXUS.txt",
    "NEXUS_10.1.10_IMPLEMENTATION_REPORT.md", "NEXUS_10.1.10_Pruefbericht.md",
    "tests/test_v10110_backtest_webui.py", "tests/test_v10110_news_source_fixes.py",
    "tests/test_v10110_x_account_registry.py", "tests/test_v10110_risk_and_currencies.py",
    "okx_precheck_diagnostics.py", "tests/test_v1018_precheck.py",
    "okx_account_context.py", "okx_account_switch.py", "okx_snapshot_guard.py",
    "tests/test_v1017_okx_boundary.py",
    "okx_account_action.py", "market_intelligence/candidate_research.py",
    "tests/test_v1016_runtime_repairs.py", "tests/test_v1016_candidate_research.py",
    "tests/test_v1016_source_ui.py", "INSTALLATIONSANLEITUNG_NEXUS_10.1.6_DE.md",
    "etoro_settlement_review.py", "etoro_cancellations.py", "etoro_order_status.py",
    "tests/test_v1015_etoro_settlement.py", "tests/test_v1015_etoro_cancellations.py",
    "tests/test_v1015_webui_diagnosis.py", "INSTALLATIONSANLEITUNG_NEXUS_10.1.5_DE.md",
    "etoro_transport_budget.py", "etoro_stream_inbox.py", "etoro_history_accounting.py",
    "market_intelligence/source_registry.py", "pulsar/source_coordination.py",
    "tests/test_v1014_etoro_history_accounting.py", "tests/test_v1014_x_discovery.py",
    "tests/test_v1014_pulsar_x_candidates.py", "tests/test_v1014_source_diagnosis.py",
    "tests/test_v1014_ui_and_migration.py", "tests/test_v1014_etoro_transport.py",
    "tests/test_v1014_news_risk_origins.py", "INSTALLATIONSANLEITUNG_NEXUS_10.1.4_DE.md",
    "etoro_exit_costs.py", "market_intelligence/service.py", "market_intelligence/store.py",
    "webui/market_sources.py", "webui/templates/sources.html", "webui/static/sources.js",
    "tests/test_v1013_etoro_exit_identity.py", "tests/test_v1013_etoro_result_scope.py",
    "tests/test_v1013_exit_costs.py",
    "tests/test_v1013_x_market_intelligence.py", "tests/test_v1013_sources_webui.py",
    "tests/test_v1013_release_integration.py", "INSTALLATIONSANLEITUNG_NEXUS_10.1.3_DE.md",
    "etoro_risk_period.py", "etoro_open_operations.py", "webui/diagnosis_jobs.py", "webui/source_receipts.py",
    "webui/templates/diagnosis.html", "webui/static/diagnosis.js",
    "tests/test_v1012_risk_and_protection.py", "tests/test_v1012_diagnosis_webui.py",
    "tests/test_v1012_source_receipts.py", "INSTALLATIONSANLEITUNG_NEXUS_10.1.2_DE.md",
    "NEXUS_10_Diagnose.py", "NEXUS_Diagnose_Starten.sh",
    "INSTALLATIONSANLEITUNG_NEXUS_10.1.9_DE.md", "CHANGELOG_v10.1.9_NEXUS.txt",
    "NEXUS_10.1.9_IMPLEMENTATION_REPORT.md", "NEXUS_10.1.9_Pruefbericht.md",
    "tests/test_v1019_report_repairs.py",
    "NEXUS_eToro_Risikopruefung.sh", "NEXUS_eToro_Schutzplan.sh",
    "etoro_risk_maintenance.py", "etoro_protection_repair.py",
    "pulsar/candidate_selection.py", "webui/display_evidence.py",
    "INSTALLATIONSANLEITUNG_NEXUS_10.1.1_DE.md",
    "broker_observation.py", "pulsar/diagnostics.py", "webui/diagnostics.py",
    "webui/static/execution_details.js", "webui/static/ai_diagnostics.js",
    "CHANGELOG.md", "IMPLEMENTATION_REPORT.md", "TEST_REPORT.md", "MIGRATION_NOTES.md", "KNOWN_ISSUES.md",
    "RELEASE_BUILD.txt", "release_unpack.py",
    "tests/test_v100_risk_persistence.py", "tests/test_v100_execution_recovery.py",
    "tests/test_v100_broker_health.py", "tests/test_v100_strategy_pulsar.py",
    "tests/test_v100_analysis_jobs.py", "tests/test_v100_webui_backend.py",
    "tests/test_v100_frontend.py", "tests/frontend_v100.test.js",
    "freqtrade_candles.py", "etoro_trade_display.py", "pulsar/ai_packet.py",
    "tests/test_v990_freqtrade.py", "tests/test_v990_research_display.py", "tests/test_v990_guards.py",
    "CHANGELOG_v9.9.0_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_9.9.0_DE.md",
    "fmp_service.py", "fmp_data.py", "fmp_setup.py", "pulsar/financials.py",
    "tests/test_v989_fmp.py", "CHANGELOG_v9.8.9_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.8.9_DE.md",
    "okx_reference_valuation.py", "repair_okx_verified_history.py",
    "tests/test_fix3_pulsar_timeouts.py", "tests/test_fix3_update.py",
    "tests/test_daily_universe_membership.py", "CHANGELOG_v9.8.8_FIX3_NEXUS.txt",
    "ledger_database_scope.py", "tests/test_v988_cold_migration_fix2.py",
    "CHANGELOG_v9.8.8_FIX2_NEXUS.txt",
    "offline_test_bootstrap/encoding_contract.py", "tests/test_v988_encoding_fix1.py",
    "CHANGELOG_v9.8.8_FIX1_NEXUS.txt",
    "okx_closed_reconciliation.py", "stock_readiness.py", "pulsar/research_history.py",
    "NEXUS_9_8_7_Statuspruefung.py", "START_HIER_9.8.7.txt", "PULSAR_9.8.7_DE.md",
    "CHANGELOG_v9.8.7_NEXUS.txt", "NEXUS_9.8.7_Pruefbericht.md",
    "CHANGELOG_v9.8.8_NEXUS.txt", "START_HIER_9.8.8.txt", "PRUEFKATALOG_9.8.8.md",
    "NEXUS_9_8_8_Statuspruefung.py", "repair_okx_accounting.py", "okx_accounting.py",
    "okx_receipt_math.py", "okx_receipt_import.py", "okx_residual_inventory.py",
    "tests/test_v987_broker_evidence.py", "tests/test_v987_closed_results.py",
    "tests/test_v987_readiness.py", "tests/test_v987_pulsar_quality.py",
    "tests/test_v987_status_inventory.py",
    "pulsar/sources.py", "tests/test_v986_pulsar_research.py",
    "START_HIER_9.8.6.txt", "CHANGELOG_v9.8.6_NEXUS.txt", "PULSAR_9.8.6_DE.md",
    "pulsar/requirements.py", "tests/test_v985_pulsar_flow_and_pages.py",
    "START_HIER_9.8.5.txt", "CHANGELOG_v9.8.5_NEXUS.txt", "PULSAR_9.8.5_DE.md",
    "okx_entry_routing.py", "stock_exit_control.py", "pulsar/__init__.py",
    "pulsar/control.py", "pulsar/research.py", "pulsar/worker.py", "pulsar/analysis.py",
    "pulsar/core.py", "pulsar/evidence.py", "pulsar/positions.py", "pulsar/telegram.py",
    "pulsar/presentation.py", "webui/templates/pulsar.html", "webui/static/pulsar.js",
    "tests/test_v984_attribution_and_display.py", "tests/test_v984_pulsar_and_routing.py",
    "tests/test_v984_receipts_and_pulsar_lifecycle.py", "tests/test_v984_recovery_and_access.py",
    "START_HIER_9.8.4.txt", "CHANGELOG_v9.8.4_NEXUS.txt", "PULSAR_9.8.4_DE.md",
    "offline_validation.py", "offline_test_bootstrap/sitecustomize.py",
    "offline_test_bootstrap/run_checks.py", "offline_test_bootstrap/network_guard.py",
    "offline_test_bootstrap/run_python.py", "tests/test_v974_isolation_fix1.py",
    "check_test_dependencies.py", "tests/test_v974_test_dependencies.py",
    "etoro_accounting_resolution.py", "broker_display_context.py", "Nexus_Buchungsabgleich.py",
    "tests/test_v972_accounting_projection.py", "tests/test_v972_webui_contexts.py", "tests/test_v972_optional_provider.py", "tests/test_v972_migration_and_fees.py",
    "ledger_result.py", "tests/test_v971_installation_regressions.py",
    "INSTALLATIONSANLEITUNG_NEXUS_9.7.4_DE.md", "CHANGELOG_v9.7.4_NEXUS.txt",
    "VERSION.txt", "live_trader.py", "gui_app.py", "news_check.py", "research_snapshot.py",
    "crypto_diagnose.py", "tests_intelligence.py", "tests_integration_v560.py",
    "Pi_Installieren.sh", "Pi_GUI_Starten.sh", "tradingbot-pi5.service.template",
    "TradingBot_GUI.desktop.template", "TradingBot_Starten.desktop.template",
    "TradingBot_Stoppen.desktop.template", "TradingBot_Status.desktop.template",
    "requirements-lock.txt", "requirements-test.txt",
    # --- NEXUS 8.1.1 ---------------------------------------------------------
    "nexus_start.py", "nexus_setup.py", "venv_guard.py",
    "crypto_engine.py", "ai_router.py", "risk_pots.py", "scheduler_v7.py",
    "decision_source.py", "massive_api.py",
    "decision_snapshot.py",
    "etoro_reconciliation.py", "core_volume_20.py", "crypto_dynamic_30.py",
    "trade_chart_data.py",
    # NEXUS 9.0: isolierter, positionsgebundener SampleStrategy-Modus.
    "crypto_strategy_mode.py", "freqtrade_sample_strategy.py",
    "freqtrade_sample_backtest.py", "tests/test_v900_freqtrade_sample.py",
    # v8.1.3: Universums-Kennzahlen, Trade-Ledger und Strategieversion.
    "universe_overview.py", "trade_ledger.py", "strategy_version.py",
    "stock_universe_runner.py",
    # v8.1.4: Handelsbereitschaft, Meldeschicht, OKX-Status, Second Opinion,
    # Universumsdiagnose.
    "trading_ready.py", "meldungen.py", "okx_status.py",
    "second_opinion.py", "universe_diagnose.py",
    # v8.1.5: Tagesbuch, gemeinsamer Risikopfad, Positionsuebergabe.
    "tagesbuch.py", "risk_state_pfad.py", "positions_auftraege.py",
    "webui/static/nexus.css", "webui/static/positions.js", "execution_lifecycle.py",
    "broker/okx.py", "broker/okx_stream.py", "broker/multi.py",
    "Nexus_Starten.sh", "Nexus_Einrichten.sh",
    "CHANGELOG_v8.1.2_NEXUS.txt", "README_V8_1_2_NEXUS_DE.md",
    "INSTALLATIONSANLEITUNG_NEXUS_8.1.5_DE.md", "INSTALLATIONSANLEITUNG_NEXUS_8.2_DE.md",
    "INSTALLATIONSANLEITUNG_NEXUS_8.2.1_DE.md",
    "INSTALLATIONSANLEITUNG_NEXUS_8.2.2_DE.md",
    "INSTALLATIONSANLEITUNG_NEXUS_8.3.0_DE.md",
    "INSTALLATIONSANLEITUNG_NEXUS_8.3.1_DE.md",
    "CHANGELOG_v8.2_NEXUS.txt", "MASTER_NOTIZ_NEXUS_8.2.txt",
    "CHANGELOG_v8.2.1_NEXUS.txt", "MASTER_NOTIZ_NEXUS_8.2.1.txt",
    "CHANGELOG_v8.2.2_NEXUS.txt", "MASTER_NOTIZ_NEXUS_8.2.2.txt",
    "CHANGELOG_v8.3.0_NEXUS.txt", "TEST_REPORT_v8.3.0_NEXUS.txt",
    "CHANGELOG_v8.3.1_NEXUS.txt", "TEST_REPORT_v8.3.1_NEXUS.txt",
    "CHANGELOG_v9.0_NEXUS.txt", "TEST_REPORT_v9.0_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0_DE.md",
    "FREQTRADE_MODUS_NEXUS_9.0_DE.md", "RISIKOPRUEFUNG_NEXUS_9.0_DE.md",
    "CHANGELOG_v9.0.1_NEXUS.txt", "TEST_REPORT_v9.0.1_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.1_DE.md",
    "KRYPTO_UNIVERSUM_UND_TRADEANALYSE_NEXUS_9.0.1_DE.md",
    "CHANGELOG_v9.0.2_NEXUS.txt", "TEST_REPORT_v9.0.2_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.2_DE.md",
    "KORREKTUREN_NEXUS_9.0.2_DE.md", "tests/test_v902_stability_fixes.py",
    "CHANGELOG_v9.0.3_NEXUS.txt", "TEST_REPORT_v9.0.3_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.3_DE.md",
    "KORREKTUREN_NEXUS_9.0.3_DE.md", "tests/test_v903_broker_truth_and_mobile.py",
    "CHANGELOG_v9.0.4_NEXUS.txt", "TEST_REPORT_v9.0.4_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.4_DE.md",
    "KORREKTUREN_NEXUS_9.0.4_DE.md", "tests/test_v904_critical_regressions.py",
    "CHANGELOG_v9.0.5_NEXUS.txt", "TEST_REPORT_v9.0.5_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.5_DE.md",
    "KORREKTUREN_NEXUS_9.0.5_DE.md", "tests/test_v905_okx_connection_resilience.py",
    "CHANGELOG_v9.0.6_NEXUS.txt", "TEST_REPORT_v9.0.6_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.6_DE.md",
    "KORREKTUREN_NEXUS_9.0.6_DE.md",
    "CHANGELOG_v9.0.7_NEXUS.txt", "TEST_REPORT_v9.0.7_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.7_DE.md",
    "KORREKTUREN_NEXUS_9.0.7_DE.md", "tests/test_v907_api_reconciliation_currency.py",
    "CHANGELOG_v9.0.8_NEXUS.txt", "TEST_REPORT_v9.0.8_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.8_DE.md",
    "KORREKTUREN_NEXUS_9.0.8_DE.md", "tests/test_v908_etoro_scanner_resilience.py",
    "CHANGELOG_v9.0.9_NEXUS.txt", "TEST_REPORT_v9.0.9_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.9_DE.md",
    "KORREKTUREN_NEXUS_9.0.9_DE.md", "tests/test_v909_broker_truth_universe_logbook.py",
    "CHANGELOG_v9.0.10_NEXUS.txt", "TEST_REPORT_v9.0.10_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.10_DE.md",
    "tests/test_v9010_equity_scan_visibility.py",
    "CHANGELOG_v9.0.11_NEXUS.txt", "TEST_REPORT_v9.0.11_NEXUS.txt",
    "INSTALLATIONSANLEITUNG_NEXUS_9.0.11_DE.md",
    "tests/test_v9011_okx_universe_currency_lanes.py",
    "ARCHITEKTUR_V8_NEXUS_DE.md",
    "TEST_REPORT_v8.1.1_NEXUS.txt", "broker_diagnostics.py",
    "broker_live_arming.py", "webui_start.py", "webui_setup.py",
    "webui/app.py", "webui/auth.py", "webui/settings_store.py", "webui/state.py",
    "webui/templates/dashboard.html", "webui/templates/settings.html",
    "webui/templates/trades.html", "webui/static/trades.js",
    "webui/static/common.js", "webui/static/dashboard.js", "webui/static/settings.js", "webui/static/app.css",
    "webui_network_setup.py", "webui_open.py", "Pi_WebUI_Aktivieren.sh", "Pi_Service_Unit_Pruefen.sh",
    "tests/test_v821_service_guard.py", "tests/test_v821_favorites_routing.py",
    "tradingbot-webui.service.template", "TradingBot_WebUI.desktop.template",
    "WIREGUARD_VPN_EINRICHTUNG_DE.md", "WireGuard_Status_Pruefen.sh",
    # --- v9.1 ---------------------------------------------------------------
    "CHANGELOG_v9.1_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_9.1_DE.md",
    "tests/test_v910_okx_api.py", "tests/test_v910_geldpfad.py",
    "tests/test_v910_freqtrade.py", "tests/test_v910_zustand_und_anzeige.py",
    # --- v9.2 ---------------------------------------------------------------
    "CHANGELOG_v9.2_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_9.2_DE.md",
    "strategy_migration.py", "tests/test_v920_versionswechsel.py",
    # --- v9.3 ---------------------------------------------------------------
    "CHANGELOG_v9.3_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_9.3_DE.md",
    "tests/test_v930_etoro_zuordnung.py", "tests/test_v930_kapitalreservierung.py",
    "tests/test_v930_underdogs.py", "tests/test_v930_ausfuehrungsstatus.py",
    "tests/test_v930_upgrade_und_takt.py",
    # --- v9.3.1 -------------------------------------------------------------
    "KORREKTUREN_NEXUS_9.3.1_DE.md",
    # --- v9.4 ---------------------------------------------------------------
    "CHANGELOG_v9.4_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_9.4_DE.md",
    "TEST_REPORT_v9.4_NEXUS.txt", "tests/test_v940_etoro_identity.py",
    "webui/static/nexus-logo.png",
    # --- v9.5 ---------------------------------------------------------------
    "CHANGELOG_v9.5_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_9.5_DE.md",
    "TEST_REPORT_v9.5_NEXUS.txt", "tests/test_v950_critical_release.py",
    "state_lock.py", "persistent_daily_budget.py", "broker_exit_journal.py",
    "crypto_analysis.py", "run_crypto_backtest.py", "run_crypto_walkforward.py",
    # --- v9.5.1 -------------------------------------------------------------
    "CHANGELOG_v9.5.1_NEXUS.txt", "INSTALLATIONSANLEITUNG_NEXUS_9.5.1_DE.md",
    "TEST_REPORT_v9.5.1_NEXUS.txt", "tests/test_v951_etoro_rootfix.py",
    "tests/test_v951_etoro_price_cap.py", "tests/test_v951_ownership_domain.py",
    "tests/test_v951_etoro_closed_migration.py",
    "tests/test_v951_trade_ledger_exact_link.py",
    "tests/test_v951_actual_upgrade_replay.py",
    "tests/test_v951_etoro_close_broker_hardening.py",
    "tests/test_v951_fill_commit_pipeline.py",
    "tests/test_v951_post_buy_accounting_race.py",
    "tests/test_v951_trade_ledger_sell_replay.py",
    # --- v9.5.2 -------------------------------------------------------------
    "KORREKTUREN_NEXUS_9.5.2_DE.md",
    "tests/test_v952_echte_zustandsdaten.py",
    "tests/fixtures/echt_bot_order_registry.json",
    "tests/fixtures/echt_etoro_reconciliation.json",
    "tests/fixtures/echt_runtime_status.json",
    "tests/fixtures/echt_runtime_status_okx.json",
    # --- v9.5.3 -------------------------------------------------------------
    "KORREKTUREN_NEXUS_9.5.3_DE.md",
    "tests/test_v953_ledger_dubletten.py",
    "tests/fixtures/echt_dubletten_trades.json",
)


def _versionstext() -> str:
    """Versionsangabe fuer die Zusammenfassung -- ohne feste Nummer im Code."""
    try:
        return (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() or "unbekannt"
    except OSError:
        return "unbekannt"


def _is_hygiene_excluded(path: Path) -> bool:
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return True
    return any(part in HYGIENE_EXCLUDED_DIRS for part in rel.parts)


def _run(label: str, cmd: list[str], *, env: dict[str, str]) -> tuple[str, int, float]:
    print("\n" + "=" * 78)
    print(label)
    print("=" * 78)
    t0 = time.time()
    if env.get("NEXUS_OFFLINE_TEST_ROOT") == str(ROOT) and cmd[0] == sys.executable:
        cmd = [sys.executable, str(ROOT / "offline_test_bootstrap" / "run_python.py"), *cmd[1:]]
    proc = subprocess.run(cmd, cwd=ROOT, env=env)
    return label, proc.returncode, time.time() - t0


def _static_hygiene() -> tuple[int, list[str]]:
    issues: list[str] = []
    # In echten Release-Baeumen Vollstaendigkeit pruefen. Unit-Tests duerfen
    # ROOT absichtlich auf einen Minimalbaum umbiegen, um einzelne Hygiene-
    # Regeln isoliert zu testen; ohne VERSION.txt ist es kein Release-Baum.
    if (ROOT / "VERSION.txt").exists():
        for name in REQUIRED_RELEASE_FILES:
            if not (ROOT / name).is_file():
                issues.append(f"Pflichtdatei fehlt im Release: {name}")
        try:
            version = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()
            # Die Pruefung haengt nicht mehr an einer festen Nummer, sondern
            # vergleicht VERSION.txt mit config.VERSION_NEXUS. Eine fest
            # verdrahtete "6.0" liess den Volltest bei jedem Versionssprung
            # zwangslaeufig fehlschlagen -- ohne dass etwas kaputt war.
            if not version:
                issues.append("VERSION.txt ist leer")
            else:
                erwartet = ""
                try:
                    import config as _cfg
                    erwartet = str(getattr(_cfg, "VERSION_NEXUS", "") or "").strip()
                except Exception as exc:
                    issues.append(f"config.py nicht ladbar: {exc}")
                if erwartet and version != erwartet:
                    issues.append(
                        f"VERSION.txt ({version!r}) und config.VERSION_NEXUS ({erwartet!r}) "
                        "stimmen nicht ueberein")
        except Exception as exc:
            issues.append(f"VERSION.txt nicht lesbar: {exc}")
    for path in ROOT.rglob("*.py"):
        if _is_hygiene_excluded(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception as exc:
            issues.append(f"{path.relative_to(ROOT)}: AST nicht lesbar: {exc}")
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ExceptHandler)
                and isinstance(node.type, ast.Name)
                and node.type.id == "Exception"
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            ):
                issues.append(f"{path.relative_to(ROOT)}:{node.lineno}: stummer Exception-Pfad")
    # Release soll keine lokalen Laufzeit-/Credential-Dateien ausliefern.
    forbidden_runtime = [
        "bot_zustand.txt", "bot_zustand.json",
        "decision_history.sqlite",
        "telegram_queue.json",
        "telegram_control_status.json",
        "telegram_control_state.json",
        "telegram_command_audit.jsonl",
        "etoro_cost_status.json",
        "news_source_status.json",
        "ai_attention_usage.json",
        "ai_control_state.json",
        "live_trading_arm.json",
        "risk_state.json",
        "approved_universe.json",
        "universe_proposals.json",
        "weekly_intelligence_state.json",
        # --- NEXUS-Laufzeitzustaende der Kryptoseite -----------------------
        "universe_state.json",
        "universe_audit.jsonl",
        "decision_sources.jsonl",
        "ai_router_usage.json",
        "ai_router_cache.json",
        "ai_usage_audit.jsonl",
        "crypto_positions.json",
        "stock_positions.json",
        "market_regime.json",
        "ai_pending_orders.json",
        "second_opinion_settings.json",
        "handel_settings.json",
        "risk_state_etoro.json",
        "risk_state_okx.json",
        "runtime_status_okx.json",
        "okx_live_arm.json",
        "etoro_live_arm.json",
        "web_ui_credentials.json",
        "web_ui_settings.json",
        "migration_report.json",
        "crypto_strategy_mode.json",
        ".crypto_strategy_mode.seen",
        # --- 10.2.0 ---------------------------------------------------------
        "etoro_strategy_mode.json",
        "etoro_fee_snapshots.json",
        ".manual_trade_control.lock",
        "crypto_dynamic_30.json",
        "crypto_dynamic_30_history.json",
        "manual_trade_commands.json",
        "manual_coin_locks.json",
        # --- 10.4.0 ---------------------------------------------------------
        "scan_uebersicht.json",
        "etoro_chart_candles.sqlite",
        # --- 10.6.0 ---------------------------------------------------------
        "risiko_stufen.json",
        ".risiko_stufen.seen",
    ]
    if not _allow_local_state():
        for name in forbidden_runtime:
            if (ROOT / name).exists():
                issues.append(f"Laufzeitdatei im Release-Baum: {name}")
        for path in ROOT.glob("*_credentials.json"):
            issues.append(f"Credential-Datei im Release-Baum: {path.name}")
        if (ROOT / "openai_ai_settings.json").exists():
            issues.append("Lokale KI-Zugangsdaten im Release-Baum")
        if (ROOT / "strategy_reports").exists():
            issues.append("Lokale Strategy-Berichte im Release-Baum")

    # NEXUS nutzt ausschliesslich eToro fuer Aktien und OKX fuer Krypto. Alte Brokerbezeichnungen duerfen weder in
    # Produktcode noch GUI, Tests oder Dokumentation des Release-Baums stehen.
    forbidden_broker_terms = ("al" + "paca", "ib" + "kr", "interactive" + " brokers")
    text_ext = {".py", ".md", ".txt", ".json", ".sh", ".bat", ".template"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_ext or _is_hygiene_excluded(path):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except Exception as exc:
            issues.append(f"{path.relative_to(ROOT)}: Textscan fehlgeschlagen: {exc}")
            continue
        for term in forbidden_broker_terms:
            if term in text:
                issues.append(f"{path.relative_to(ROOT)}: verbotene Alt-Brokerreferenz '{term}'")
                break
    issues.extend(_release_checklist())
    return (0 if not issues else 1), issues


def _release_checklist() -> list[str]:
    """10.2.0: automatisierte Release-Checkliste.

    Verankert die im 10.1.10-Zyklus von Hand gefundenen Paketierungsfehler als
    Pflichtpruefung: (a) keine UTF-8-BOMs (Windows-PowerShell-Falle), (b) keine
    alten versionsgebundenen Diagnose-Einstiege (Modul-Import-Test!), (c) jedes
    neue RiskState-Feld muss in risk_basis_review klassifiziert sein, (d) jede
    neue settings-Einstellung muss im Settings-Vertragstest stehen.
    """
    issues: list[str] = []
    if not (ROOT / "VERSION.txt").exists():
        return issues  # Minimalbaeume einzelner Unit-Tests sind kein Release.
    version = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip()

    # (a) BOM-Scan ueber alle Textdateien des Baums.
    text_ext = {".py", ".md", ".txt", ".json", ".sh", ".html", ".js", ".css", ".template"}
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_ext or _is_hygiene_excluded(path):
            continue
        try:
            if path.read_bytes()[:3] == b"\xef\xbb\xbf":
                issues.append(f"UTF-8-BOM in {path.relative_to(ROOT)}")
        except OSError as exc:
            issues.append(f"{path.relative_to(ROOT)}: BOM-Scan fehlgeschlagen: {exc}")

    # (b) Nur der Diagnose-Einstieg der AKTUELLEN Version darf im Paket liegen.
    import re as _re
    kurz = _re.match(r"(\d+)\.(\d+)\.(\d+)", version)
    erlaubt = (f"NEXUS_{kurz.group(1)}_{kurz.group(2)}_{kurz.group(3)}_Diagnose.py"
               if kurz else "")
    for path in ROOT.glob("NEXUS_*_Diagnose.py"):
        if _re.fullmatch(r"NEXUS_\d+_\d+_\d+_Diagnose\.py", path.name) and path.name != erlaubt:
            issues.append(
                f"Alter versionsgebundener Diagnose-Einstieg im Paket: {path.name} "
                f"(erlaubt ist nur {erlaubt}; der Modul-Import-Test wuerde ihn ausfuehren)")

    # (c) RiskState-Felder muessen vollstaendig klassifiziert sein.
    try:
        from dataclasses import fields as _fields
        from risk_manager import RiskState as _RiskState
        import risk_basis_review as _rbr
        baseline = {
            "consecutive_losses", "cooldown_until", "counted_cost_ids_today",
            "counted_trade_ids_today", "crypto_exposure", "current_date",
            "day_start_equity", "equity_drawdown_pct", "equity_guard_halted",
            "estimated_costs_today", "gross_loss_today", "gross_profit_today",
            "last_equity", "lifetime_estimated_costs", "lifetime_gross_loss",
            "lifetime_gross_profit", "lifetime_net_loss", "lifetime_net_profit",
            "lifetime_realized_pnl", "lifetime_unknown_pnl_trades",
            "net_loss_today", "net_profit_today", "open_positions",
            "pending_persistence_operations", "realized_pnl_today",
            "realized_receipts", "risk_schema_version", "trades_today",
            "trading_halted", "unknown_pnl_ids_today", "unknown_pnl_trades_today",
        }
        rest = ({f.name for f in _fields(_RiskState)}
                - _rbr.METADATA - _rbr.OPTIONAL_FIELDS)
        unklassifiziert = sorted(rest - baseline)
        if unklassifiziert:
            issues.append(
                "Neue RiskState-Felder ohne Klassifizierung in risk_basis_review "
                "(METADATA oder OPTIONAL_FIELDS_<version>): " + ", ".join(unklassifiziert)
                + ". Ohne Klassifizierung entwerten sie historische Checkpoints "
                "(RISK_CHECKPOINT_FINANCIAL_FIELDS_MISSING).")
    except Exception as exc:
        issues.append(f"RiskState-Klassifizierungs-Check nicht ausfuehrbar: {exc}")

    # (d) Jede settings-Einstellung steht im Settings-Vertragstest.
    try:
        import re as _re2
        html = (ROOT / "webui" / "templates" / "settings.html").read_text(encoding="utf-8")
        vertrag = (ROOT / "tests" / "test_v980_webui.py").read_text(encoding="utf-8")
        felder = sorted(set(_re2.findall(r'data-field="([^"]+)"', html)))
        fehlend = [f for f in felder if f not in vertrag]
        if fehlend:
            issues.append(
                "settings.html-Felder fehlen im Vertragstest tests/test_v980_webui.py: "
                + ", ".join(fehlend))
    except OSError as exc:
        issues.append(f"Settings-Vertragscheck nicht ausfuehrbar: {exc}")
    return issues


def _shell_syntax_command(files) -> list[str]:
    # bash -n a.sh b.sh parses only a.sh; the remaining names become argv.
    return ["bash", "-c", 'for script in "$@"; do bash -n "$script" || exit $?; done',
            "nexus-shell-check", *map(str, files)]


def main() -> int:
    started = time.time()
    results: list[tuple[str, int, float]] = []
    with tempfile.TemporaryDirectory(prefix="tradingbot_v80_nexus_test_") as td:
        env = os.environ.copy()
        env["TRADINGBOT_TEST_STATE_DIR"] = td
        env["PYTHONUNBUFFERED"] = "1"
        env["TRADING_MODE"] = "paper"

        # Release-Hygiene MUSS vor verhaltensbasierten Tests laufen: einige
        # Tests erzeugen bewusst lokale Statusdateien. Diese duerfen einen
        # sauberen, frisch entpackten Release nicht nachtraeglich als unsauber
        # erscheinen lassen.
        print("\n" + "=" * 78)
        print("STATIC RELEASE HYGIENE (vor Testlauf)")
        print("=" * 78)
        t0 = time.time()
        code, issues = _static_hygiene()
        for issue in issues:
            print("FEHLER:", issue)
        if not issues:
            print("OK")
        results.append(("STATIC RELEASE HYGIENE", code, time.time() - t0))

        dependency_result = _run(
            "TEST-ABHAENGIGKEITEN / WEBUI-CLIENT",
            [sys.executable, "check_test_dependencies.py"], env=env,
        )
        results.append(dependency_result)
        if dependency_result[1] != 0:
            print("FEHLER: Pflicht-Testumgebung nicht einsatzbereit. "
                  "pytest wurde NICHT ausgefuehrt; der Volltest ist fehlgeschlagen.")
            results.append(("PYTEST GELDPFAD / REGRESSIONEN", 78, 0.0))
        else:
            results.append(_run("PYTEST GELDPFAD / REGRESSIONEN", [sys.executable, "-m", "pytest", "-q", "-ra", "tests"], env=env))
        results.append(_run("SELF TEST", [sys.executable, "self_test.py"], env=env))
        results.append(_run(
            "COMPILEALL",
            [sys.executable, "-m", "compileall", "-q", "-x", r"(^|/)(\.venv|venv|env|site-packages|__pycache__|\.pytest_cache|build|dist)(/|$)", str(ROOT)],
            env=env,
        ))
        results.append(_run("PI PREFLIGHT (host-neutral)", [sys.executable, "pi_preflight.py", "--no-imports"], env=env))

        if shutil.which("bash"):
            shell_files = sorted(ROOT.glob("*.sh"))
            if shell_files:
                results.append(_run("SHELL SYNTAX", _shell_syntax_command(shell_files), env=env))

    print("\n" + "=" * 78)
    print(f"VOLLTEST {_versionstext()} - ZUSAMMENFASSUNG")
    print("=" * 78)
    failed = []
    for name, code, seconds in results:
        state = "OK" if code == 0 else f"FEHLER({code})"
        print(f"{name:34s} {state:12s} {seconds:7.2f}s")
        if code != 0:
            failed.append(name)
    print(f"Gesamtlaufzeit: {time.time() - started:.1f}s")
    if failed:
        print("FEHLGESCHLAGEN:", ", ".join(failed))
        return 1
    print("VOLLTEST OK")
    print("Grenze: echte eToro-/Telegram-/KI-Netzwerkaufrufe und ARM64-Hardware werden offline nicht simuliert.")
    return 0


if __name__ == "__main__":
    # Import no trading/config module in an installed, configured directory.
    from offline_validation import run_isolated
    raise SystemExit(run_isolated(ROOT))
