"""Sichere Uebernahme unterstuetzter Einstellungen aus einer aelteren TradingBot-Version.

Es werden eToro-, OKX-, Telegram-, Research-, GUI-/Profil-, Web-UI- und
persistente TradingBot-Zustaende uebernommen. Nicht mehr unterstuetzte
Brokerkonfigurationen werden bewusst ignoriert.

NEU IN v8.0 NEXUS
=================
1. Neue Dateien der v7-Bausteine (OKX, MASSIVE, dynamisches Universum,
   AI-Router, Web-UI) werden mit uebernommen.
2. Der EINE Risikozustand aus v6 (risk_state.json) wird auf den eToro-Topf
   abgebildet. In v6 gab es nur eToro -- ohne diese Abbildung waere die
   Tagesbremse nach dem Umstieg auf null zurueckgesetzt und der Bot duerfte
   an einem bereits schlechten Tag wieder von vorn verlieren.
3. Der Bericht sagt jetzt ausdruecklich, WAS fehlt und noch eingegeben
   werden muss -- statt nur zu zaehlen, was kopiert wurde.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path

from credential_store import load_credentials, save_credentials, secure_path

ROOT = Path(__file__).resolve().parent

CREDENTIAL_FILES = {
    "etoro_credentials.json", "telegram_credentials.json", "alpha_vantage_credentials.json",
    "news_sources_credentials.json", "openai_ai_settings.json",
    # --- NEXUS ---
    "okx_credentials.json", "massive_credentials.json", "web_ui_credentials.json",
    "market_intelligence_credentials.json",
}

PERSISTENT_FILES = [
    "market_intelligence_credentials.json", "market_intelligence_settings.json",
    "market_intelligence.sqlite",  # Paid reservations survive updates/restarts.
    "etoro_stream_inbox.sqlite",  # Unmatched private events retain their account scope.
    "etoro_http_budget.sqlite",  # Broker quotas/Retry-After survive restarts and updates.
    "okx_verified_history_repair_report.json",
    "okx_accounting_repair_report.json",
    "etoro_credentials.json", "telegram_credentials.json", "alpha_vantage_credentials.json",
    "news_sources_credentials.json",
    "news_research_settings.json", "intelligence_settings.json", "openai_ai_settings.json",
    "favorites.json",
    "handelsmodus.txt", "aktives_profil.txt", "bot_zustand.json", "bot_zustand.txt",
    "position_state.json", "risk_state.json",
    "fill_progress.json", "bot_order_registry.json", "underdog_freigabe.json",
    "ml_model.joblib", "decision_journal.jsonl",
    "decision_history.sqlite", "telegram_control_state.json", "ai_control_state.json",
    "walkforward_status.json", "ml_training_status.json",
    # --- 6.0 ---------------------------------------------------------------
    "market_session_state.json",
    "approved_universe.json",
    "universe_proposals.json",
    "news_source_status.json",
    "ai_attention_usage.json",
    "ai_usage.json",
    "telegram_queue.json",
    "strategy_report.json",
    "runtime_status.json",
    # --- NEXUS -------------------------------------------------------------
    # Ohne diese Eintraege wuerde NEXUS zwar starten, aber jede neue Faehigkeit
    # begaenne bei null: OKX-Zugang neu eintragen, Universum neu aufbauen,
    # KI-Budget des Tages doppelt verbrauchen.
    "okx_credentials.json",            # OKX Demo- und Live-Zugang
    "massive_credentials.json",        # MASSIVE API-Key
    "web_ui_credentials.json",         # Web-Login (Hash, niemals Klartext)
    "web_ui_settings.json",            # Anzeigeoptionen der Weboberflaeche
    "universe_settings.json",          # Grenzen des dynamischen Universums
    "universe_state.json",             # Mitgliedschaften, Aufenthaltsdauer
    "universe_audit.jsonl",            # Aenderungsprotokoll
    "ai_router_settings.json",         # Luna/Terra-Konfiguration
    "ai_router_usage.initialized", "news_rules_settings.json", "earnings_calendar_status.json",
    "ai_router_usage.json",            # verbrauchtes Tagesbudget
    "ai_router_cache.json",            # bereits bezahlte KI-Antworten
    "ai_usage_audit.jsonl",            # Aufruf-/Usagebelege, einschliesslich NEXUS-10-Ausfuehrungsphasen
    "decision_sources.jsonl",          # Quellenprotokoll der Entscheidungen
    "telegram_command_audit.jsonl",    # Telegram-Befehle ohne Geheimnisse
    "telegram_control_status.json",    # Zustand des Long-Polling-Kanals
    "risk_state_etoro.json",           # Risikotopf Aktien
    "risk_state_okx.json",             # Risikotopf Krypto
    # Ohne das Positionsbuch weiss die Kryptoseite nach einem Wechsel nicht
    # mehr, wo ihre Stops liegen -- OKX Spot kennt keinen Einstandspreis.
    "crypto_positions.json",           # offene Kryptopositionen samt Schutzwerten
    "second_opinion_settings.json",    # Schalter der GPT-Zweitmeinung
    "handel_settings.json",            # Datenfrische-Schwellen (v8.1.5)
    "trading_ready_notifications.json",  # keine doppelte Bereitmeldung nach Update
    # v8.3.1: Ein ungeklärter eToro-Submit und die belegte 30-Tage-
    # Volumenhistorie dürfen bei einem Update niemals vergessen werden.
    "etoro_reconciliation.json",
    "core_volume_20.json",
    "core_volume_20_history.json",
    "crypto_dynamic_30.json",       # 30 monatlich rotierende OKX-Basiswerte
    "crypto_dynamic_30_history.json",  # taegliche Messungen fuer den Monatslauf
    "crypto_strategy_mode.json",     # Laufzeitmodus und revisionssicherer Wechselverlauf
    # 10.6.0: Die gewaehlte Einsatzstufe je Broker. Ginge sie bei einem Update
    # verloren, faende der Bot stillschweigend zur alten Vorgabe zurueck --
    # eine Risikoentscheidung, die niemand getroffen hat.
    "risiko_stufen.json",
    ".risiko_stufen.seen",
    # v9.0.15: manuelle WebUI-Auftraege und Wiedereinstiegssperren duerfen
    # bei einem Update nicht verschwinden. Besonders ein unklarer Auftrag
    # darf niemals durch einen Versionswechsel erneut gesendet werden.
    "manual_trade_commands.json",
    "manual_coin_locks.json",
    # v9.5: Ein Update darf weder bereits verarbeitete Brokerfills noch
    # schwebende Exit-Intents oder das externe API-Tagesbudget vergessen.
    "api_daily_budgets.json",
    "broker_exit_journal.sqlite",
    "fmp_service.sqlite",  # Shared capabilities, caches and consumed quotas.
    "fmp_reference_cache.json",
    "massive_service.sqlite",  # Shared consumed quota, Retry-After and news cache.
    "etoro_protection_journal.json",  # Ambiguous protection writes must not be repeated.
    "candle_observations.json", "okx_account_action.json", "nasdaq_halt_diagnostics.json",
    "okx_account_context.json", "okx_account_switch_pending.json",
    "market_candles.sqlite",  # Restart-safe closed candles, not manufactured history.
    "freqtrade_scan.sqlite",  # Consumed signal candles survive upgrades.
    "pulsar_research.sqlite",  # Source history and spent budgets survive every update.
]

# Alte Datei -> neue Datei, wenn die neue noch nicht existiert.
UMBENENNUNGEN = {
    "risk_state.json": "risk_state_etoro.json",
}

# Was NEXUS zusaetzlich braucht und NICHT aus v6 kommen kann.
NEU_EINZUGEBEN = {
    "okx_credentials.json": "OKX API-Key, Secret und Passphrase (Demo und Live getrennt)",
    "massive_credentials.json": "MASSIVE API-Key fuer Referenzdaten und News",
    "web_ui_credentials.json": "Benutzername und Passwort fuer die Weboberflaeche",
}


def _candidates(root: Path):
    seen = set()
    items = []
    for base in (root.parent, root.parent.parent):
        if not base.exists():
            continue
        for p in base.iterdir():
            if not p.is_dir() or p.resolve() == root.resolve():
                continue
            if "TradingBot" not in p.name and "Trading_Bot" not in p.name:
                continue
            try:
                rp = p.resolve()
                if rp in seen:
                    continue
                seen.add(rp)
                score = sum((p / f).exists() or (f in CREDENTIAL_FILES and secure_path(p / f).exists())
                            for f in PERSISTENT_FILES)
                if score:
                    items.append((p.stat().st_mtime, score, p))
            except Exception:
                continue
    # Vollstaendigkeit ist wichtiger als Aenderungszeit. So gewinnt eine
    # komplette v6-Installation gegen einen spaeteren, aber nur teilweise
    # eingerichteten v7-Versuch.
    items.sort(reverse=True, key=lambda x: (x[1], x[0]))
    return [x[2] for x in items]


def _kopiere(name: str, src: Path, dst: Path) -> None:
    if name in CREDENTIAL_FILES:
        data = load_credentials(src, None)
        if not isinstance(data, dict):
            raise ValueError("kein lesbares Zugangsdatenformat")
        # Alte KI-Handelsfelder werden beim Speichern bewusst verworfen.
        if name == "openai_ai_settings.json":
            keep = {"api_key", "enabled", "model", "reasoning_effort", "web_search",
                    "max_calls_per_day", "refresh_minutes", "max_priority",
                    # v7: Luna/Terra-Felder duerfen mitwandern, falls vorhanden
                    "luna_model", "terra_model", "router_enabled"}
            data = {k: v for k, v in data.items() if k in keep}
        if name == "news_sources_credentials.json" and isinstance(data.get("enabled"), dict):
            data["enabled"] = {k: v for k, v in data["enabled"].items()
                               if k in {"alpha_vantage", "finnhub", "fmp",
                                        "sec_edgar", "massive", "gdelt",
                                        "yahoo_finance", "google_news", "nasdaq_halts", "federal_reserve"}}
        save_credentials(dst, data)
    elif name in {"decision_history.sqlite", "broker_exit_journal.sqlite", "pulsar_research.sqlite", "fmp_service.sqlite", "massive_service.sqlite", "market_candles.sqlite", "freqtrade_scan.sqlite", "market_intelligence.sqlite", "etoro_stream_inbox.sqlite", "etoro_http_budget.sqlite"}:
        src_con = sqlite3.connect(src.resolve().as_uri()+"?mode=ro", uri=True, timeout=5)
        try:
            dst_con = sqlite3.connect(dst, timeout=5)
            try:
                src_con.backup(dst_con)
            finally:
                dst_con.close()
        finally:
            src_con.close()
    elif name == _journal_name() and src.stat().st_size > _journal_obergrenze():
        # 10.8.1: Vom JSONL-Spiegel des Entscheidungsjournals wandern nur die
        # letzten ganzen Zeilen mit (833 MB am 19.09.2026 auf dem Pi). Die
        # SQLite-Datenbank ist die vollstaendige Quelle; die Quelle im alten
        # Ordner bleibt unangetastet.
        dst.write_bytes(_journal_schwanz(src, _journal_obergrenze()))
    else:
        shutil.copy2(src, dst)


def _journal_name() -> str:
    import config as _cfg
    return str(getattr(_cfg, "DECISION_JOURNAL_FILE", "decision_journal.jsonl"))


def _journal_obergrenze() -> int:
    """Obergrenze des JSONL-Spiegels in Bytes (``DECISION_JOURNAL_MAX_MB``, mind. 1 MB)."""
    import config as _cfg
    try:
        return int(max(1.0, float(getattr(_cfg, "DECISION_JOURNAL_MAX_MB", 20) or 20)) * 1024 * 1024)
    except (TypeError, ValueError):
        return 20 * 1024 * 1024


def _journal_schwanz(src: Path, limit: int) -> bytes:
    """Die letzten ganzen Zeilen einer JSONL-Datei, hoechstens ``limit`` Bytes."""
    size = src.stat().st_size
    with src.open("rb") as stream:
        stream.seek(max(0, size - limit))
        rest = stream.read()
    if size <= limit:
        return rest
    cut = rest.find(b"\n")
    return rest[cut + 1:] if cut >= 0 else b""


STRICT_STATE_FILES = (
    "market_intelligence.sqlite", "market_intelligence_settings.json",
    "etoro_stream_inbox.sqlite", "etoro_http_budget.sqlite",
    "market_candles.sqlite", "freqtrade_scan.sqlite",
    "fmp_service.sqlite", "massive_service.sqlite",
    "etoro_protection_journal.json", "okx_account_action.json",
    "pulsar_research.sqlite",
    "decision_history.sqlite", "broker_exit_journal.sqlite", "etoro_reconciliation.json",
    "bot_order_registry.json", "fill_progress.json", "position_state.json", "crypto_positions.json",
    "risk_state.json", "risk_state_etoro.json", "risk_state_okx.json",
)


def _risk_archive_names(root: Path) -> list[str]:
    """Only original risk-period receipts; no generic backup/glob import."""
    names = set()
    for pattern in ("risk_state_etoro.json.legacy-*.bak", "etoro_risk_period_*.json"):
        for path in root.glob(pattern):
            if path.is_symlink() or not path.is_file():
                raise ValueError("Risikoperiodenbeleg ist keine regulaere Datei: " + path.name)
            if not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                raise ValueError("Risikoperiodenbeleg hat kein Objektformat: " + path.name)
            names.add(path.name)
    for path in root.glob("okx_account_archive_*.zip"):
        if path.is_symlink() or not path.is_file():
            raise ValueError("OKX-Kontoarchiv ist keine regulaere Datei")
        names.add(path.name)
    return sorted(names)


def _strict_preflight(source: Path, target: Path) -> None:
    """A fresh target and readable source; caller must stop all source writers."""
    if source == target or not source.is_dir():
        raise ValueError("Expliziter, anderer Quellordner erforderlich")
    # A target with credentials/settings is already tied to a different
    # account or mode even if its trade database has not been initialized.
    # Checking only position files would combine source-A trades with target-B
    # credentials. A strict import is a fresh-target operation, never a merge.
    protected = set(PERSISTENT_FILES) | set(STRICT_STATE_FILES) | set(_risk_archive_names(target)) | {
        "live_trading_arm.json", "okx_live_arm.json", "etoro_live_arm.json"}
    occupied = []
    for name in sorted(protected):
        paths = [target / name]
        if name in CREDENTIAL_FILES:
            paths.append(secure_path(target / name))
        if name.endswith(".sqlite"):
            paths.extend(target / (name + suffix) for suffix in ("-wal", "-shm"))
        occupied.extend(path.name for path in paths if path.exists() or path.is_symlink())
    if occupied:
        raise ValueError("Ziel enthaelt bereits Handelszustand/Zugangsdaten/Einstellungen; nicht mischen: " + ", ".join(occupied))
    required = ("decision_history.sqlite", "etoro_reconciliation.json", "bot_order_registry.json", "fill_progress.json")
    missing = [name for name in required if not (source / name).is_file()]
    if missing:
        raise ValueError("Quellzustand unvollstaendig: " + ", ".join(missing))
    for name in STRICT_STATE_FILES:
        path = source / name
        if not path.is_file():
            continue
        if name.endswith(".sqlite"):
            con = sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=5)
            try:
                if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite-Quellpruefung fehlgeschlagen: " + name)
            finally:
                con.close()
        else:
            if not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                raise ValueError("Quellzustand hat kein Objektformat: " + name)


def _database_proven_empty(path: Path) -> bool:
    """Read-only evidence that every non-SQLite table is empty.

    An empty decisions table alone says nothing about fills/orders/trades. Any
    schema/read failure means UNKNOWN, never an empty database. As with the
    complete migration, callers must stop target writers before importing.
    """
    if path.is_symlink() or not path.is_file():
        return False
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA trusted_schema=OFF")
            con.execute("BEGIN")
            tables = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND lower(name) NOT GLOB 'sqlite_*'").fetchall()
            for (name,) in tables:
                quoted = '"' + str(name).replace('"', '""') + '"'
                if con.execute("SELECT 1 FROM " + quoted + " LIMIT 1").fetchone() is not None:
                    return False
            return True
        finally:
            con.close()
    except (sqlite3.Error, OSError, ValueError) as exc:
        logging.getLogger(__name__).warning(
            "Vorhandene Zielhistorie nicht nachweislich leer; bleibt erhalten (%s)", type(exc).__name__)
        return False


def migrate_from(source: Path, target: Path = ROOT, overwrite: bool = False, *, strict: bool = False):
    source = Path(source).resolve()
    target = Path(target).resolve()
    if strict:
        _strict_preflight(source, target)
    copied, skipped, failed = [], [], []

    for name in [*PERSISTENT_FILES, *_risk_archive_names(source)]:
        src = source / name
        dst = target / name
        src_exists = src.exists() or (name in CREDENTIAL_FILES and secure_path(src).exists())
        dst_exists = dst.exists() or (name in CREDENTIAL_FILES and secure_path(dst).exists())
        if not src_exists:
            continue
        # Das Dashboard initialisiert die Decision-DB schon vor der Migration.
        # Eine leere Zieldatei darf deshalb eine gefuellte Historie nicht
        # versehentlich blockieren.
        empty_decision_db = (name == "decision_history.sqlite" and dst_exists
                             and not overwrite and _database_proven_empty(dst))
        if dst_exists and not overwrite and not empty_decision_db:
            skipped.append(name)
            continue
        try:
            _kopiere(name, src, dst)
            copied.append(name)
        except Exception:
            skipped.append(name)
            failed.append(name)
            logging.getLogger(__name__).error("Zustandsuebernahme fehlgeschlagen: %s", name, exc_info=True)

    # Umbenennungen: nur ausfuehren, wenn das Ziel noch fehlt.
    for alt, neu in UMBENENNUNGEN.items():
        src = source / alt
        dst = target / neu
        if not src.exists() or dst.exists():
            continue
        try:
            shutil.copy2(src, dst)
            copied.append(f"{alt} -> {neu}")
        except Exception:
            skipped.append(f"{alt} -> {neu}")

    _migrate_v831_crypto_strategy_evidence(source, target, copied, skipped)
    _migrate_v1080_luna_tagesbudget(target, copied, skipped)
    _migrate_v1081_journal_kompakt(source, target, copied, skipped)
    _force_paper(target)
    _migration_report(target, source, copied, skipped)
    if strict and failed:
        raise RuntimeError("Migration unvollstaendig; Core nicht starten. Fehler: " + ", ".join(failed))
    if strict:
        okx = load_credentials(target / "okx_credentials.json", {})
        if ((target / "handelsmodus.txt").read_text().strip() != "paper"
                or not isinstance(okx, dict) or okx.get("live_trading") is not False):
            raise RuntimeError("Sicherer Demo/Paper-Modus nicht bestaetigt; Core nicht starten")
    return copied, skipped


def _source_version(source: Path) -> str:
    try:
        return (Path(source) / "VERSION.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _migrate_v831_crypto_strategy_evidence(source: Path, target: Path,
                                           copied: list[str], skipped: list[str]) -> None:
    """Bindet belegte 8.3.1-Botpositionen an die damalige Standardstrategie.

    Vor 9.0 gab es keinen Freqtrade-Modus. Eine ausdruecklich als BOT/AUTO
    gefuehrte Position mit positiver decision_id aus einer nachgewiesenen
    8.3.1-Installation kann deshalb eindeutig NEXUS_STANDARD zugeordnet
    werden. Bei jedem fehlenden Beleg wird bewusst nichts geraten; der
    9.0-Loader stellt diese Position spaeter auf BEOBACHTEN.
    """
    if _source_version(source) != "8.3.1-NEXUS":
        return
    path = Path(target) / "crypto_positions.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("positionen"), list):
            raise ValueError("ungueltiges Kryptopositionsbuch")
        changed = 0
        for row in data["positionen"]:
            if not isinstance(row, dict) or row.get("entry_strategy_mode"):
                continue
            decision_id = row.get("decision_id")
            try:
                decision_proven = int(decision_id) > 0
            except (TypeError, ValueError):
                decision_proven = False
            if not (str(row.get("herkunft") or "").upper() == "BOT"
                    and str(row.get("verwaltung") or "").upper() == "AUTO"
                    and decision_proven):
                continue
            row.update({
                "entry_strategy_mode": "NEXUS_STANDARD",
                "strategy_name": "NEXUS Standard Krypto",
                "strategy_version": "NEXUS-8.3.1-STANDARD-MIGRATED",
                "strategy_parameter_hash": "",
                "strategy_parameters": {
                    "source_version": "8.3.1-NEXUS",
                    "migration": "evidence_based_bot_auto_decision",
                },
            })
            changed += 1
        if not changed:
            return
        data["version"] = max(2, int(data.get("version") or 1))
        from safe_persistence import atomic_write_json
        atomic_write_json(path, data)
        copied.append(f"crypto_positions.json: {changed} Strategiezuordnung(en)")
    except Exception as exc:
        logging.getLogger(__name__).warning(
            "8.3.1-Kryptostrategien konnten nicht sicher migriert werden: %s", exc)
        skipped.append("crypto_positions.json: Strategiezuordnung")


LUNA_TAGESBUDGET_ALT_MAX = 50
LUNA_TAGESBUDGET_NEU = 200


def _migrate_v1080_luna_tagesbudget(target: Path, copied: list[str], skipped: list[str]) -> None:
    """Hebt ein altes Luna-Tagesbudget (Standard 40, eingerichtet 50) einmalig auf 200.

    10.8.0: Luna kostet je Anfrage Bruchteile eines Cents, und PULSAR sowie
    die Zweitmeinung liefen an Tagen mit vielen Karten ins 50er-Limit. Der
    USD-Deckel bleibt die harte Grenze. Angehoben wird nur ein Wert bis 50
    (alte Standards); ein spaeter bewusst kleiner gesetzter Wert bleibt
    stehen, weil die Markierung den zweiten Lauf verhindert.
    """
    path = Path(target) / "ai_router_settings.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("luna_max_calls_per_day_angehoben"):
            return
        alt = int(data.get("luna_max_calls_per_day", 40) or 0)
        if alt > LUNA_TAGESBUDGET_ALT_MAX:
            return
        data["luna_max_calls_per_day"] = LUNA_TAGESBUDGET_NEU
        data["luna_max_calls_per_day_angehoben"] = {"vorher": alt, "version": "10.8.0"}
        from safe_persistence import atomic_write_json
        atomic_write_json(path, data)
        copied.append(f"ai_router_settings.json: Luna-Tagesbudget {alt} -> {LUNA_TAGESBUDGET_NEU}")
    except Exception as exc:
        logging.getLogger(__name__).warning("Luna-Tagesbudget nicht angehoben: %s", exc)
        skipped.append("ai_router_settings.json: Luna-Tagesbudget")


def _migrate_v1081_journal_kompakt(source: Path, target: Path,
                                   copied: list[str], skipped: list[str]) -> None:
    """Haelt den uebernommenen JSONL-Spiegel des Entscheidungsjournals klein.

    10.8.1: Bis 10.8.0 wuchs ``decision_journal.jsonl`` unbegrenzt (833 MB
    auf dem Pi am 19.09.2026). ``_kopiere`` uebernimmt seit 10.8.1 nur noch
    die letzten ganzen Zeilen; hier wird eine trotzdem zu grosse ZIELDATEI
    (aelterer Kopierweg, ``--source``-Wiederholung) auf ganze Zeilen
    gekuerzt und das Ergebnis im Migrationsbericht genannt. Die Quelle im
    alten Ordner bleibt unangetastet.
    """
    name = _journal_name()
    path = Path(target) / name
    try:
        if not path.is_file():
            return
        limit = _journal_obergrenze()
        size = path.stat().st_size
        if size > limit:
            tail = _journal_schwanz(path, limit)
            tmp = path.with_suffix(path.suffix + ".kompakt")
            tmp.write_bytes(tail)
            os.replace(tmp, path)
            copied.append(f"{name}: Spiegel von {size // (1024 * 1024)} MB auf "
                          f"{len(tail) // (1024 * 1024)} MB gekuerzt")
            return
        quelle = Path(source) / name
        if quelle.is_file() and quelle.stat().st_size > limit:
            copied.append(f"{name}: nur die letzten {size // (1024 * 1024)} MB von "
                          f"{quelle.stat().st_size // (1024 * 1024)} MB uebernommen")
    except Exception as exc:
        logging.getLogger(__name__).warning("Entscheidungsjournal-Spiegel nicht gekuerzt: %s", exc)
        skipped.append(f"{name}: Spiegel nicht gekuerzt")


def _migration_report(target: Path, source: Path, copied: list[str], skipped: list[str]) -> None:
    """Nachvollziehbarer Bericht ohne Zugangsdaten oder geheime Werte."""
    from datetime import datetime, timezone
    from safe_persistence import atomic_write_json

    atomic_write_json(Path(target) / "migration_report.json", {
        "version": __import__("config").VERSION_NEXUS,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "copied": list(copied),
        "kept_or_skipped": list(skipped),
        "safety_reset": {
            "etoro_mode": "paper",
            "okx_mode": "demo",
            "live_arming_removed": True,
        },
    })


def _force_paper(target: Path) -> None:
    """Migrationen duerfen niemals einen alten LIVE-Zustand erben."""
    target = Path(target)
    (target / "handelsmodus.txt").write_text("paper\n", encoding="utf-8")
    okx_path = target / "okx_credentials.json"
    try:
        data = load_credentials(okx_path, {})
        if not isinstance(data, dict):
            data = {}
        data["live_trading"] = False
        save_credentials(okx_path, data)
    except Exception:
        logging.getLogger(__name__).warning(
            "OKX-Modus konnte nicht auf Paper zurueckgesetzt werden", exc_info=True)
    for name in ("live_trading_arm.json", "okx_live_arm.json", "etoro_live_arm.json"):
        try:
            (target / name).unlink(missing_ok=True)
        except OSError:
            pass


def fehlende_eingaben(target: Path = ROOT) -> list[str]:
    """Was muss der Nutzer nach der Uebernahme noch selbst eintragen?"""
    target = Path(target).resolve()
    offen = []
    for name, beschreibung in NEU_EINZUGEBEN.items():
        pfad = target / name
        vorhanden = pfad.exists() or secure_path(pfad).exists()
        if vorhanden:
            try:
                daten = load_credentials(pfad, {})
                if isinstance(daten, dict) and any(str(v).strip() for v in daten.values()):
                    continue
            except Exception:
                # Unlesbare Datei zaehlt bewusst als "noch offen": lieber
                # einmal zu viel nachfragen als eine kaputte Datei uebersehen.
                logging.getLogger(__name__).warning(
                    "Zugangsdatei %s ist nicht lesbar; sie gilt als noch nicht eingerichtet.",
                    name, exc_info=True)
        offen.append(f"{name}: {beschreibung}")
    return offen


def auto_migrate(target: Path = ROOT):
    for source in _candidates(target):
        copied, skipped = migrate_from(source, target, False)
        if copied:
            return source, copied, skipped
    return None, [], []


def bericht(target: Path = ROOT) -> dict:
    """Maschinenlesbarer Zustand fuer die Weboberflaeche."""
    quellen = _candidates(Path(target))
    return {
        "gefundene_vorgaengerversionen": [str(p) for p in quellen[:5]],
        "fehlende_eingaben": fehlende_eingaben(target),
        "uebernehmbare_dateien": len(PERSISTENT_FILES),
    }


def main():
    ap = argparse.ArgumentParser(description="Einstellungen aus einer aelteren Version uebernehmen")
    ap.add_argument("source", nargs="?")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--strict", action="store_true", help="Frisches Ziel und vollstaendigen, lesbaren Handelszustand verlangen")
    ap.add_argument("--json", action="store_true", help="Bericht als JSON ausgeben")
    args = ap.parse_args()

    if args.strict and (args.auto or not args.source):
        ap.error("--strict verlangt einen expliziten Quellordner, nicht --auto")

    if args.json:
        print(json.dumps(bericht(), ensure_ascii=False, indent=2))
        return

    if args.auto or not args.source:
        src, copied, skipped = auto_migrate()
        print("Einstellungen uebernommen aus:" if src else "Keine unterstuetzten alten Einstellungen gefunden.",
              src or "")
        if copied:
            print("Kopiert:", ", ".join(copied))
    else:
        copied, skipped = migrate_from(Path(args.source), strict=args.strict)
        print("Kopiert:", ", ".join(copied) if copied else "nichts")
        if skipped:
            print("Beibehalten/ignoriert:", ", ".join(skipped))

    offen = fehlende_eingaben()
    if offen:
        print()
        print("Noch selbst einzugeben (neu in v8.0):")
        for zeile in offen:
            print("  -", zeile)


if __name__ == "__main__":
    main()
