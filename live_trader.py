"""TradingBot 8.1.1 NEXUS: eToro-Aktiencore mit fail-closed Money Path.

KI hat keine Kauf-/Verkaufsrechte. Im Live-Pfad darf sie nur die Scan-Reihenfolge
des bereits von eToro qualifizierten Universums priorisieren. Separate Offline-/
Weekly-Rollen duerfen Research-Vorschlaege bzw. eigene Statistik auslegen, aber
keine Orders, Filter oder Universumsfreigaben selbst schreiben.
"""
from __future__ import annotations

import logging
import math
import time
import threading
import socket
from datetime import datetime
from pathlib import Path

import config
from broker import (
    get_broker, BrokerFehler, NichtVerbunden, VerbindungVerloren,
    OrderStatusUnklar, AuthentifizierungsFehler, NichtUnterstuetzt,
)
from broker.connectivity import is_connectivity_error
from contracts import build_universe
from favorites import as_universe_rows, load_favorites, merge_stock_favorite_rows
from sec_fundamentals import SecFundamentals
from ml_model import load_model
from strategy import generate_signal
from pulsar import core as pulsar_core
from pulsar.control import Blocked as PulsarBlocked
from risk_manager import (RiskState, size_new_position, calculate_stop_take,
                          can_open_new_position, kaufsperre_grund)
from fill_tracker import FillProgressTracker
from order_ownership import OrderOwnershipRegistry, classify_fill_owner
from notifier import notify, notify_trade, flush_trades, pending_trade_count, send_document, send_telegram_buttons, telegram_status
from position_manager import PositionManager, protection_result_confirmed
from trade_messages import buy_message, sell_message
from bot_zustand import BotZustand, AKTIV, PAUSIERT, GESTOPPT
from berichte import Befehlsverarbeitung
from news_filter import NachrichtenFilter
from underdog_screening import freigegebene_underdogs, screening_ausfuehren
from cost_engine import estimate_roundtrip, estimate_roundtrip_with_broker, economically_viable, estimate_one_way_cost, CostQuoteUnavailable
from market_quality import MarketQualityClient
from etoro_exit_costs import (assess as etoro_exit_assess, publish as etoro_exit_publish,
                              fresh_quote as etoro_exit_quote, own_position as etoro_owned_position,
                              quote_exit_decision)
from market_session import (market_session_status, entry_allowed,
                            melde_herabstufung)


# 9.5.5: Gedaechtnis der Ablehnungsdrosselung.
# Schluessel (Symbol, Grundcode, Grundtext) -> (letzte Meldung, unterdrueckte Anzahl)
_ABLEHNUNG_GESEHEN: dict[tuple, tuple] = {}


def _kursalter_grenze() -> float:
    """Erlaubtes Kursalter fuer Aktien-Neueinstiege.

    Eine einzige Quelle statt drei Aufrufstellen mit eigenem Standardwert --
    sonst laeuft eine Grenze auseinander, ohne dass es jemand merkt. Der Wert
    ist in der WebUI einstellbar und wirkt ohne Neustart.
    """
    try:
        import live_settings
        return live_settings.kursalter_grenze()
    except Exception:
        logger.debug("Kursaltergrenze nicht lesbar; Standard aus config", exc_info=True)
        return float(getattr(config, "MARKET_SESSION_QUOTE_MAX_AGE_SECONDS", 180.0))
from earnings_engine import EarningsClient
from event_intelligence import assess as assess_event, EARNINGS_WORDS, global_crisis_score
from market_regime import MarketRegimeClient
from portfolio_guard import candidate_sector_guard, candidate_correlation_guard
from news_radar import NewsRadar
from edge_estimator import estimate_plausible_move
from decision_journal import record_decision
from decision_analytics import (mark_execution, record_order_state, record_system_error,
                                summary as decision_summary)
from decision_snapshot import new_decision_id
from decision_source import speichere as speichere_decision_sources
from telegram_steuerung import TelegramSteuerung
from auto_maintenance import AutoMaintenance
from runtime_status import RuntimeStatus
from pi_system import trading_safety_reasons
from broker_health import BrokerHealthTracker
from scan_scheduler import RotatingScanScheduler
from instrument_risk import classify_instrument, broad_liquidity_check
from daily_report import maybe_send as maybe_send_daily_report, build_report as build_daily_report
from outcome_tracker import update_due as update_decision_outcomes
from news_sources import source_health as news_source_health
from automation_status import mark as automation_mark
from ai_attention import AIAttentionPrioritizer
from candidate_gate import bewerte_kandidat
from instrument_identity import canonical_key, same_instrument
from order_execution import submit_protected_buy
from ai_control import read_mode as ai_control_mode, set_mode as set_ai_control_mode
from telegram_exports import snapshot_database
from log_hygiene import configure_library_logging, configure_root_logging
from universe_proposals import pending_reviews as pending_universe_reviews, set_technical_result, get as get_universe_proposal
from universe_review import review_proposal
from universe_telegram import handle_callback as handle_universe_callback, technical_result_message
from market_calendar import darf_arbeiten as _kalender_darf_arbeiten, sitzungsstatus as _sitzungsstatus
from market_notifier import pruefe_und_melde as _melde_marktsitzung


configure_root_logging(
    config.LOG_FILE,
    getattr(config, "LOG_LEVEL", "INFO"),
    max_bytes=int(getattr(config, "LOG_ROTATE_MAX_BYTES", 5 * 1024 * 1024)),
    backups=int(getattr(config, "LOG_ROTATE_BACKUPS", 5)),
    stdout=bool(getattr(config, "LOG_TO_STDOUT", False)),
)
logger = logging.getLogger(__name__)
configure_library_logging()


def safety_check():
    if not config.PAPER_TRADING and not bool(getattr(config, "LIVE_ARMED", False)):
        raise RuntimeError(
            "LIVE ist gesperrt: Modus=LIVE, aber der unabhaengige Arming-Faktor fehlt/ist abgelaufen. "
            f"Status: {getattr(config, 'LIVE_ARM_STATUS', 'unbekannt')}. "
            "Bitte handelsmodus.py erneut ausfuehren."
        )

    mode = "PAPER" if config.PAPER_TRADING else "LIVE"

    if not config.PAPER_TRADING:
        # Im Live-Betrieb unmissverstaendlich anzeigen, womit gehandelt wird.
        # Ein Bot, der still mit echtem Geld laeuft, weil man den Modus
        # vergessen hat, ist ein vermeidbares Risiko.
        print()
        print("!" * 66)
        print("!!  LIVE-MODUS: ES WIRD MIT ECHTEM GELD GEHANDELT")
        print("!" * 66)
        print("!!  Broker      : eToro Public API (LIVE)")
        print(f"!!  Profil     : {getattr(config, 'ACTIVE_PROFILE', '?').upper()}")
        print(f"!!  Risiko/Trade: {config.RISK_PER_TRADE_PCT * 100:.2f} % des Kontos")
        print("!!  Krypto     : separater OKX-Core mit eigener Live-Freigabe")
        print("!" * 66)
        print("!!  Abbrechen mit Strg+C -- Start in 10 Sekunden ...")
        print("!" * 66)
        try:
            time.sleep(10)
        except KeyboardInterrupt:
            print("\nAbgebrochen. Es wurde nichts gehandelt.")
            raise

    print(f"\n=== TRADINGBOT {config.VERSION_NEXUS} | ETORO AKTIEN | {mode} ===")
    # Bewusst die WIRKSAMEN Zahlen statt nur des Profilnamens: der Fehler
    # "Profil angezeigt, aber nicht angewandt" waere sofort aufgefallen,
    # wenn hier von Anfang an die echten Werte gestanden haetten.
    print(config.profil_klartext())
    if not bool(getattr(config, "PROFILE_BOOTSTRAP_OK", False)):
        print("!! PROFIL-WARNUNG: " + str(getattr(config, "PROFILE_BOOTSTRAP_ERROR", "")))
    print(
        f"Scanner: {config.INSTRUMENTS_PER_CYCLE} Instrumente alle "
        f"{config.CYCLE_MINUTES} min | Kerze: {config.BAR_SIZE}"
    )


def _connection_failure(broker, exc: Exception) -> bool:
    """Globalen Transportfehler von einem instrumentbezogenen Fehler trennen."""
    if isinstance(exc, (VerbindungVerloren, NichtVerbunden)):
        return True
    try:
        if not broker.is_connected():
            return True
    except Exception:
        return True
    return is_connectivity_error(exc)


def aktien_stop_plan(asset_type: str, signal_price: float, stop: float, take: float,
                     *, ask: float | None = None, pulsar: bool = False) -> dict:
    """Stop und Ziel fuer Aktien: Mindestabstand und Bezug auf den Kaufkurs.

    WARUM (10.7.0)
    --------------
    CSCO am 18.09.2026: Signal 109,91, ATR-Stop 109,12 (0,72 %), Fill 109,15
    -- der Stop lag 3 Cent unter dem Einstieg und loeste nach 1,5 Sekunden
    aus. Zwei Fehler steckten darin: Der Stop hatte keinen Mindestabstand,
    und er war auf den Signalkurs des Scans bezogen statt auf den Kurs, zu
    dem tatsaechlich gekauft wird.

    Diese Funktion wird zweimal gerufen: einmal nach der Strategie (nur
    Mindestabstand, damit die Menge aus dem echten Risiko entsteht) und
    einmal unmittelbar vor der Order mit dem frischen Briefkurs (Bezug).
    Die prozentualen Abstaende bleiben, nur der Bezugspunkt wandert.

    Rueckgabe: referenz (Kurs, auf den sich Stop und Ziel beziehen), stop,
    take, hinweis (leer, wenn nichts veraendert wurde).
    Ein PULSAR-Plan wird nie veraendert: Der Adapter vergleicht ihn exakt.
    """
    signal_price = float(signal_price or 0.0)
    stop = float(stop or 0.0)
    take = float(take or 0.0)
    ergebnis = {"referenz": signal_price, "stop": stop, "take": take, "hinweis": ""}
    if (pulsar or str(asset_type or "") != "stock" or signal_price <= 0
            or stop <= 0 or stop >= signal_price):
        return ergebnis
    min_pct = max(0.0, float(getattr(config, "ETORO_MIN_STOP_DISTANCE_PCT", 0.010) or 0.0))
    stop_pct = (signal_price - stop) / signal_price
    take_pct = (take - signal_price) / signal_price if take > signal_price else 0.0
    hinweise = []
    if stop_pct < min_pct:
        hinweise.append(f"Stopabstand von {stop_pct * 100:.2f} % auf Mindestabstand "
                        f"{min_pct * 100:.2f} % erweitert")
        stop_pct = min_pct
    referenz = signal_price
    try:
        ask_wert = float(ask) if ask is not None else 0.0
    except (TypeError, ValueError):
        ask_wert = 0.0
    if math.isfinite(ask_wert) and ask_wert > 0 and abs(ask_wert - signal_price) > 1e-12:
        referenz = ask_wert
        hinweise.append(f"Stop und Ziel auf den frischen Kaufkurs {ask_wert:.4f} bezogen "
                        f"(Signalkurs {signal_price:.4f}, {((ask_wert - signal_price) / signal_price) * 100:+.2f} %)")
    ergebnis["referenz"] = referenz
    ergebnis["stop"] = round(referenz * (1.0 - stop_pct), 4)
    ergebnis["take"] = round(referenz * (1.0 + take_pct), 4) if take_pct > 0 else take
    ergebnis["stop_pct"] = stop_pct
    ergebnis["hinweis"] = "; ".join(hinweise)
    return ergebnis


def _einsatz_etoro(asset_type: str) -> dict:
    """Wirksamer Einsatz je Trade fuer eToro -- bei jedem Kandidaten frisch.

    10.6.0: Die Einsatzstufe wird in der WebUI gesetzt und liegt in einer
    eigenen Datei. Sie wird hier bei jeder Dimensionierung neu gelesen, damit
    eine Aenderung ohne Neustart des Trading-Core wirkt. Ohne gewaehlte Stufe
    kommen unveraendert die bisherigen Werte aus Profil und Konfiguration
    zurueck -- dieser Pfad aendert dann gar nichts.
    """
    krypto = str(asset_type or "") == "crypto"
    basis_risiko = float(config.CRYPTO_RISK_PER_TRADE_PCT if krypto
                         else config.RISK_PER_TRADE_PCT)
    basis_max = float(config.CRYPTO_MAX_POSITION_PCT if krypto
                      else config.MAX_POSITION_PCT)
    try:
        import risk_levels
        return risk_levels.effective("etoro", basis_risiko, basis_max)
    except Exception:
        logger.debug("eToro-Einsatzstufe nicht lesbar", exc_info=True)
        return {"risiko_pro_trade_pct": basis_risiko,
                "max_position_pct": basis_max, "level": "",
                "label": "Vorgabe (Stufe nicht lesbar)", "quelle": "vorgabe",
                "chosen": False, "error": ""}


def get_account_equity(broker):
    """Kontowert ueber die Broker-Schicht -- Verbindungsfehler nicht verschlucken."""
    try:
        return float(broker.kontowert())
    except Exception as exc:
        if _connection_failure(broker, exc):
            raise VerbindungVerloren(f"Kontowert nicht abrufbar: {exc}") from exc
        logger.warning("Kontowert konnte nicht ermittelt werden: %s", exc)
        return 0.0


def update_account_equity_guard(risk, broker, equity):
    """Bind this worker's valuation to its proven account/environment/currency.

    The stock worker owns its eToro adapter independently of the crypto hub.
    Passing no scope here would therefore bypass the RiskPot scope guard.
    This observes the current account; it does not assign legacy receipts.
    """
    import json
    name = str(getattr(broker, "name", "") or "").lower()
    getter = getattr(broker, "account_fingerprint", None)
    account = str(getter() or "") if callable(getter) else ""
    currency = str(broker.kontowaehrung() or "").upper()
    paper_getter = getattr(broker, "ist_paper", None)
    paper = bool(paper_getter()) if callable(paper_getter) else bool(getattr(broker, "paper", True))
    if (not name or not account or not currency
            or (name == "etoro" and not bool(getattr(broker, "_identity_loaded", False)))):
        raise RuntimeError("RISK_ACCOUNT_SCOPE_UNKNOWN: Kontoidentitaet fuer Tagesrisiko nicht belegt")
    return risk.update_equity_guard(
        equity, basis_key=f"broker_kontowert:v2:{name}:{currency}",
        account_scope=json.dumps([name, "DEMO" if paper else "LIVE", account], separators=(",", ":")))


def report_risk_buy_gate(runtime, risk):
    """Publish the very same risk decision used by BUY, including UNKNOWNs."""
    from datetime import timezone
    from etoro_risk_period import result_scope_summary
    try:
        reason = kaufsperre_grund(risk)
        scope = result_scope_summary(risk)
        gate = {"blocked": bool(reason), "reason": reason,
                "pending_results": scope["active_unknown"],
                # 10.7.0: offene Belege, die NICHT mehr sperren (aeltere Tage)
                "open_results": scope.get("open_unknown"),
                "historical_pending_results": scope["historical_unknown"],
                "lifetime_pending_results": scope["lifetime_unknown"],
                "period_method": scope["method"]}
    except Exception as exc:
        gate = {"blocked": True, "reason": "Risikopruefung nicht lesbar: " + type(exc).__name__,
                "pending_results": None}
    gate["assessed_at"] = datetime.now(timezone.utc).isoformat()
    # 10.7.0: jede Sperre mit Grund, Reichweite, Ablauf und Aufloesung.
    try:
        from handelsfreigabe import etoro_sperren, zusammenfassung
        sperren = zusammenfassung(etoro_sperren(risk))
    except Exception as exc:
        logger.debug("eToro-Sperrliste nicht erstellbar: %s", exc, exc_info=True)
        sperren = {"domaene_gesperrt": None, "gesperrte_symbole": [], "anzahl": 0, "sperren": [],
                   "fehler": type(exc).__name__}
    runtime.update(risk_manager_buy_gate=gate, sperren=sperren)
    return gate


def current_position(broker, instrument):
    """Aktueller Bestand asset-sicher; gleiche Basisticker duerfen nie kollidieren."""
    for p in broker.positionen():
        if same_instrument(p.symbol, p.asset_type, instrument.name, instrument.asset_type):
            return float(p.quantity), float(p.avg_cost)
    return 0.0, 0.0


def close_position(broker, instrument, quantity, asset_type="stock", reference_price=0.0,
                   *, position_ids=None, snapshot_id="", instrument_id=""):
    """Position schliessen -- die Broker-Schicht kennt die Besonderheiten."""
    if str(getattr(broker, "name", "")).lower() == "etoro":
        return broker.schliesse_position(
            instrument, quantity, referenzpreis=reference_price,
            position_ids=position_ids, snapshot_id=snapshot_id,
            instrument_id=instrument_id)
    return broker.schliesse_position(instrument, quantity, referenzpreis=reference_price)


def _price_from_id_map(mapping, key, fallback=0.0):
    """Robuster Lookup fuer numerische und textuelle eToro-IDs."""
    candidates=[key,str(key)]
    if str(key).isdigit():
        try:candidates.append(int(str(key)))
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    for candidate in candidates:
        try:
            value=float(mapping.get(candidate,0) or 0)
            if value>0:
                return value
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    try:
        return float(fallback or 0)
    except Exception:
        return 0.0


def check_client_side_stops(broker, manager, current_positions, order_meta, last_price_by_conid):
    """Legacy-Sicherheitsnetz fuer Adapter ohne Broker-Stop.

    eToro meldet ``unterstuetzt_krypto_stop() == True``; daher kehrt diese
    Funktion im Produktpfad sofort zurueck. Sie bleibt nur als fail-safe
    Schnittstellenreserve fuer Tests bestehen.
    """
    if not getattr(config, "CRYPTO_CLIENT_STOP", True):
        return []

    # eToro hinterlegt den Schutz brokerseitig; ein zweiter clientseitiger
    # Stop wuerde zu einer Doppelverkaufsgefahr fuehren.
    if broker.unterstuetzt_krypto_stop():
        return []

    ausgeloest = []
    for con_id, (contract, qty, avg_cost, inst) in list(current_positions.items()):
        if qty <= 0 or inst is None or inst.asset_type != "crypto":
            continue

        rec = manager.get(contract)
        if not rec or str(getattr(rec,"management_mode","OBSERVE")).upper() != "AUTO":
            continue
        stop = float(getattr(rec, "planned_stop", 0) or 0) if rec else 0.0
        if stop <= 0:
            continue

        price = _price_from_id_map(last_price_by_conid, con_id)
        if price <= 0:
            continue

        if price <= stop:
            logger.warning(
                "CLIENT-STOP ausgeloest: %s Kurs %.8f <= Stop %.8f",
                inst.name, price, stop,
            )
            print(f"  !! CLIENT-STOP {inst.name}: Kurs {price:.8f} <= Stop {stop:.8f} -> Verkauf")
            try:
                ergebnis = close_position(
                    broker, inst, qty,
                    asset_type=inst.asset_type, reference_price=price,
                )
                for oid in ergebnis.order_ids:
                    order_meta[str(oid)] = {
                        "reason": f"Client-Stop bei {price:.8f} (Stop lag bei {stop:.8f})",
                        "label": "CLIENT-STOP-LOSS",
                    }
                ausgeloest.append(inst.name)
            except Exception as exc:
                if _connection_failure(broker, exc):
                    raise VerbindungVerloren(f"Client-Stop konnte wegen Verbindungsverlust nicht gesendet werden: {exc}") from exc
                logger.exception("Client-Stop %s fehlgeschlagen: %s", inst.name, exc)
                print(f"  !! Client-Stop {inst.name} FEHLGESCHLAGEN: {exc}")

    return ausgeloest


def crypto_exposure_pct(current_positions, last_price_by_conid, equity):
    """
    Anteil des Kontos, der aktuell in Krypto steckt.
    Wird gegen MAX_CRYPTO_PORTFOLIO_PCT geprueft -- ohne diese Grenze
    koennten sich viele kleine Krypto-Positionen zu einem grossen
    Gesamtrisiko summieren (in v3.5 war der Wert definiert, aber nirgends
    ausgewertet).
    """
    if equity <= 0:
        return 0.0
    wert = 0.0
    for con_id, (contract, qty, avg_cost, inst) in current_positions.items():
        if inst is None or inst.asset_type != "crypto" or qty <= 0:
            continue
        price = _price_from_id_map(last_price_by_conid, con_id) or float(avg_cost or 0)
        wert += qty * price
    return wert / equity


class _ErsatzKontrakt:
    """Platzhalter fuer Ausfuehrungen zu Instrumenten ausserhalb des Universums."""

    def __init__(self, symbol, currency="USD", kennung=""):
        self.symbol = symbol
        self.localSymbol = symbol
        self.currency = currency
        self.conId = kennung


def _etoro_fill_instrument(fill, meta):
    """Typed broker target; a positionId is NEVER an instrumentId (TXN)."""
    from types import SimpleNamespace
    kind = str(fill.asset_type or "").lower()
    if kind not in {"stock", "crypto"}:
        raise BrokerFehler("eToro-Fill ohne eindeutigen Asset-Typ")
    detail = dict(getattr(fill, "broker_detail", {}) or {})
    identifiers = {str(x) for x in (
        detail.get("instrumentId"), detail.get("instrumentID"),
        meta.get("instrument_id"), meta.get("instrumentId")) if x not in (None, "", 0)}
    if len(identifiers) > 1 or any(not x.isdigit() or int(x) <= 0 for x in identifiers):
        raise BrokerFehler("eToro-Fill mit widerspruechlicher instrumentId; Buchung nicht quittiert")
    iid = next(iter(identifiers), "")
    contract = _ErsatzKontrakt(fill.symbol, fill.currency, iid)
    return SimpleNamespace(name=fill.symbol, asset_type=kind,
                           currency=fill.currency, contract=contract), iid


def _fill_id(fill):
    execution = getattr(fill, "execution", None)
    return str(getattr(execution, "execId", "")) if execution else ""


def _fill_order_id(fill):
    execution = getattr(fill, "execution", None)
    return int(getattr(execution, "orderId", 0) or 0) if execution else 0


def _fill_side(fill):
    execution = getattr(fill, "execution", None)
    return str(getattr(execution, "side", "")).upper()


def _fill_qty(fill):
    execution = getattr(fill, "execution", None)
    return abs(float(getattr(execution, "shares", 0) or 0)) if execution else 0.0


def _fill_price(fill):
    execution = getattr(fill, "execution", None)
    return float(getattr(execution, "price", 0) or 0) if execution else 0.0


def _holding_text(entry_time):
    try:
        dt = datetime.fromisoformat(entry_time)
        seconds = max(0, (datetime.now(dt.tzinfo) - dt).total_seconds())
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours} h {minutes} min"
    except Exception:
        return "unbekannt"


def _order_meta(order_meta, order_id):
    if order_id in order_meta:
        return order_meta[order_id]
    # Bei Bracket-Kindern den Parent suchen.
    return order_meta.get(order_id, {})


_POSITION_SNAPSHOT_UNSET = object()


class _CriticalFillAccountingError(RuntimeError):
    """Ein bestaetigter Bot-Fill ist noch nicht dauerhaft verbucht."""


def _ledger_zuordnung_unklar(exc) -> bool:
    """Ist der Ledgerfehler fachlich unaufloesbar statt technisch?

    9.5.8. Bis 9.5.7 wurde jede gescheiterte Ledgerbuchung eines eigenen
    Verkaufs gleich behandelt: der Prozess stoppte fail-closed. Fuer einen
    technischen Fehler (Datenbank gesperrt, Platte voll, paralleler
    Schreibzugriff) ist das richtig -- ein Neuversuch kann gelingen. Fuer einen
    fachlichen Befund ist es falsch: die Daten reichen nicht zur Zuordnung, und
    beim naechsten Poll reichen sie genauso wenig. Der Fill blieb dabei
    unquittiert, kam also wieder, und der Kern starb erneut -- eine
    Absturzschleife ohne jeden Fortschritt.

    Der Import liegt bewusst in der Funktion: ``live_trader`` importiert
    ``trade_ledger`` sonst nirgends auf Modulebene, und das soll so bleiben.
    """
    try:
        from trade_ledger import LedgerZuordnungUnklar
    except Exception:
        return False
    return isinstance(exc, LedgerZuordnungUnklar)


def _cleanup_candidate_failure_state(
        exc, *, instrument, order_meta, ownership_registry,
        last_data_error, failed_at: float | None = None) -> None:
    """Bereinigt nur Fehler, die wirklich zum aktuellen Kaufkandidaten gehoeren.

    Der unmittelbar nach einem akzeptierten BUY ausgefuehrte Fill-Poll kann
    ebenso einen inzwischen eingetroffenen SELL eines *anderen* Trades
    verarbeiten. Scheitert dessen kritische Ledgerbuchung, darf die generische
    Kandidatenbehandlung weder die Ownership des gerade akzeptierten Kaufs
    loeschen noch diesen Titel mit einem Kauf-Cooldown belegen. Der Fehler muss
    bis zur aeusseren Fail-Closed-Grenze weiterlaufen; der persistierte BUY-
    Intent bleibt fuer die Broker-Reconciliation erhalten.
    """
    if isinstance(exc, _CriticalFillAccountingError):
        logger.critical(
            "Kritischer Fill-Abrechnungsfehler nach BUY-Submit fuer %s; "
            "BUY-Pending/Ownership bleiben unveraendert und der Prozess "
            "stoppt fail-closed.",
            getattr(instrument, "name", "?"),
        )
        raise exc

    pending_key = (
        f"PENDING_BUY:{canonical_key(instrument.name, instrument.asset_type)}")
    order_meta.pop(pending_key, None)
    ownership_registry.clear_pending(instrument.name, instrument.asset_type)
    last_data_error[instrument.name] = (
        time.time() if failed_at is None else float(failed_at))


def _position_absent_in_complete_snapshot(snapshot, *, position_id: str,
                                          account_fingerprint: str,
                                          paper: bool) -> tuple[bool, str]:
    """Terminalen Close nur aus einem vollstaendigen, passenden Snapshot ableiten."""
    if not isinstance(snapshot, dict):
        return False, "kein normalisierter Positionssnapshot"
    if snapshot.get("complete") is not True:
        return False, "Positionssnapshot nicht als vollstaendig bestaetigt"
    expected_account = str(account_fingerprint or "")
    snapshot_account = str(snapshot.get("account_fingerprint") or "")
    if expected_account and snapshot_account and snapshot_account != expected_account:
        return False, "Positionssnapshot gehoert zu einem anderen eToro-Konto"
    expected_environment = "DEMO" if bool(paper) else "LIVE"
    snapshot_environment = str(snapshot.get("environment") or "").upper()
    if snapshot_environment and snapshot_environment != expected_environment:
        return False, "Positionssnapshot gehoert zur anderen eToro-Umgebung"

    ids = None
    for key in ("open_ids", "position_ids"):
        if key in snapshot:
            ids = {str(x) for x in (snapshot.get(key) or []) if str(x)}
            break
    if ids is None and "rows_by_position_id" in snapshot:
        ids = {str(x) for x in (snapshot.get("rows_by_position_id") or {}) if str(x)}
    if ids is None and "rows" in snapshot:
        ids = {
            str(row.get("positionId") or row.get("positionID") or "")
            for row in (snapshot.get("rows") or []) if isinstance(row, dict)
        }
        ids.discard("")
    if ids is None:
        return False, "Positionssnapshot enthaelt keine positionId-Sicht"
    pid = str(position_id or "")
    if not pid:
        return False, "SELL-Fill enthaelt keine positionId"
    return pid not in ids, ("positionId nicht mehr offen" if pid not in ids
                            else "positionId weiterhin offen")


def _tagesbuch(broker, risk):
    """Das belegte Tagesergebnis -- oder None, wenn es nicht ermittelbar ist.

    Best-effort und bewusst ohne eigenes Wiederholen: eine Nachricht darf den
    Geldpfad nie aufhalten. Ist das Handelsbuch nicht lesbar, faellt die
    Meldung auf den alten Summenzaehler zurueck.
    """
    try:
        import tagesbuch as _tagesbuch_modul
        try:
            offene = list(broker.positionen())
        except Exception:
            offene = None          # unbekannt, nicht "keine"
        waehrung = "USD"
        try:
            waehrung = str(broker.kontowaehrung() or "USD")
        except Exception:
            logger.debug("Kontowaehrung nicht lesbar", exc_info=True)
        return _tagesbuch_modul.tagesergebnis(
            broker=str(getattr(broker, "name", "") or "").lower(),
            waehrung=waehrung,
            offene_positionen=offene,
            zaehlerwert=getattr(risk, "realized_pnl_today", None),
        )
    except Exception:
        logger.debug("Tagesergebnis nicht berechenbar", exc_info=True)
        return None


def _etoro_exit_attribution(*, broker, fill, rec_before, meta: dict,
                            sell_bot_owned: bool, price: float) -> tuple[bool, dict]:
    """Keep position ownership, order identity and execution cause separate.

    Position history may omit a close-order ID. An exact owned position can
    still be accounted for, but price proximity never proves an SL/TP trigger.
    """
    result = dict(meta or {})
    if str(getattr(broker, "name", "") or "").lower() != "etoro":
        return sell_bot_owned, result

    # Die eToro-Historie kann beim Schliessen die Order-ID des Einstiegs
    # mitliefern. Dann findet die Ownership-Registry Metadaten wie
    # ``BOT-KAUF`` bzw. den Entry-Signalgrund. Beides beweist die Herkunft der
    # Position, ist aber keine Exit-Ursache. 9.5.0 meldete bei ADBE deshalb
    # "eToro-Ausfuehrung exakt wiederhergestellt" als Verkaufsgrund. Nur ein
    # ausdrueckliches Exit-Label darf die nachfolgende Broker-/Schutzattribution
    # ueberspringen.
    raw_label = str(result.get("label") or "").strip()
    entry_labels = {"BOT-KAUF", "KAUF", "BUY", "ENTRY"}
    explicit_exit_meta = bool(
        raw_label and raw_label.upper() not in entry_labels)
    protective_labels = {"STOP-LOSS", "STOP LOSS", "SL", "TAKE-PROFIT", "TAKE PROFIT", "TP"}
    if explicit_exit_meta and raw_label.upper() not in protective_labels:
        return sell_bot_owned, result
    result.pop("label", None)
    result.pop("reason", None)

    raw_reason = str(getattr(fill, "execution_reason", "") or "").strip()
    if rec_before is None:
        result.update({
            "label": "BROKER-VERKAUF",
            "reason": (f"Position beim Broker geschlossen ({raw_reason})"
                       if raw_reason else
                       "Position beim Broker geschlossen; kein Strategieauftrag zugeordnet"),
            "broker_execution_reason": raw_reason,
            "exit_cause_evidence": "BROKER_TEXT" if raw_reason else "UNPROVEN",
            "protective_exit_attributed": False,
        })
        return sell_bot_owned, result

    mode = str(getattr(rec_before, "management_mode", "") or "").upper()
    source = str(getattr(rec_before, "source", "") or "").upper()
    fill_pid = str(getattr(fill, "broker_id", "") or "")
    record_pids = {
        str(x) for x in list(getattr(rec_before, "owned_position_ids", None) or [])
        if str(x)
    }
    broker_account = (str(broker.account_fingerprint() or "")
                      if callable(getattr(broker, "account_fingerprint", None))
                      else "")
    record_account = str(
        getattr(rec_before, "broker_account_fingerprint", "") or "")
    same_domain = bool(broker_account and record_account == broker_account
        and str(getattr(rec_before, "broker_environment", "") or "").upper()
        == ("DEMO" if bool(getattr(broker, "paper", True)) else "LIVE"))
    exact_pending_bot_chain = bool(
        source == "BOT"
        and fill_pid
        and fill_pid in record_pids
        and same_domain
        and str(getattr(rec_before, "ownership_status", "")).upper() == "VERIFIED"
        and int(getattr(rec_before, "decision_id", 0) or 0) > 0
        and list(getattr(rec_before, "entry_order_ids", None) or []))
    # Ein brokerseitiger Schutz kann ausfuehren, waehrend die lokale Position
    # wegen des frueheren Zuordnungsfehlers noch PENDING_CONFIRMATION ist. Die
    # exakte, konto- und positionId-gebundene Entry-Kette bleibt auch dann ein
    # gueltiger Eigentumsbeweis fuer die Exit-Buchung (ADBE-Fall).
    eigene_auto_position = (
        mode == "AUTO" and source in {"BOT", "USER_MANAGED"}
        and same_domain and fill_pid in record_pids
    ) or exact_pending_bot_chain
    norm = raw_reason.upper().replace("_", " ").replace("-", " ")
    label = ""
    reason = ""
    tolerance = max(0.0, float(getattr(
        config, "ETORO_PROTECTIVE_EXIT_MATCH_TOLERANCE_PCT", 0.002)))
    stop = float(getattr(rec_before, "planned_stop", 0.0) or 0.0)
    take = float(getattr(rec_before, "planned_take", 0.0) or 0.0)
    # A threshold match is useful context, but it must not be promoted into
    # an execution reason (PEP: cancelled Sunday order, later close above TP).
    price_match = ""
    if eigene_auto_position and price > 0:
        if stop > 0 and price <= stop * (1.0 + tolerance):
            price_match = "STOP-LOSS"
        elif take > 0 and price >= take * (1.0 - tolerance):
            price_match = "TAKE-PROFIT"
    if eigene_auto_position and norm in {"STOP LOSS", "STOPLOSS", "SL", "S L", "TRAILING STOP", "TRAILING STOP LOSS"}:
        label, reason = "STOP-LOSS", "Brokerseitiger Stop-Loss ausgefuehrt"
    elif eigene_auto_position and norm in {"TAKE PROFIT", "TAKEPROFIT", "TP", "T P"}:
        label, reason = "TAKE-PROFIT", "Brokerseitiger Take-Profit ausgefuehrt"

    if label:
        result.update({"label": label, "reason": reason,
                       "broker_execution_reason": raw_reason,
                       "protective_exit_attributed": True,
                       "exit_cause_evidence": "BROKER_EXPLICIT_REASON",
                       "protective_price_match": price_match})
        return True, result

    result.update({
        "label": "BROKER-VERKAUF",
        "reason": (f"Position beim Broker geschlossen ({raw_reason})"
                   if raw_reason else
                   "Position beim Broker geschlossen; konkreter Ausloeser nicht belegt"),
        "broker_execution_reason": raw_reason,
        "protective_exit_attributed": False,
        "exit_cause_evidence": "BROKER_TEXT" if raw_reason else "UNPROVEN",
        "protective_price_match": price_match,
    })
    return bool(sell_bot_owned or eigene_auto_position), result


def capture_new_fills(
    broker,
    manager,
    order_meta,
    fill_tracker,
    ownership_registry,
    risk,
    equity,
    instrument_by_symbol,
    notify_enabled=True,
    tagesliste=None,
    position_snapshot=_POSITION_SNAPSHOT_UNSET,
):
    """
    Verarbeitet neue Ausfuehrungen und erzeugt Detailmeldungen.

    Der FillProgressTracker dedupliziert Ausfuehrungen und wird erst NACH
    erfolgreicher Verarbeitung committed. Instrumente werden immer mit
    asset-sicherer kanonischer Identitaet zugeordnet.
    """
    try:
        fills = broker.fills()
    except Exception as exc:
        if _connection_failure(broker, exc):
            raise VerbindungVerloren(f"Ausfuehrungen nicht abrufbar: {exc}") from exc
        logger.warning("Ausfuehrungen konnten nicht abgefragt werden: %s", exc)
        return 0

    processed = 0

    def _risk_write(operation, *args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except Exception as exc:
            # A durable trade ledger is not a durable daily-risk receipt.
            # The generic per-fill catch must never permit the next BUY after
            # this projection failed. Keep tracker/history/exit ACKs pending.
            raise _CriticalFillAccountingError(
                "ETORO_RISK_RECEIPT_UNCONFIRMED: Tagesrisikobuchung "
                "nicht bestaetigt; exakter Fill bleibt unquittiert") from exc

    try:
        from risk_result_recovery import reconcile
        reconcile(risk, broker, account_equity=equity)
    except Exception as exc:
        logger.warning('eToro-Ergebnisabgleich bleibt offen: %s', type(exc).__name__)

    # Fuer mehrere SELL-Fills desselben Polls hoechstens einen frischen
    # Depotbeweis laden. Der Hauptzyklus reicht seinen bereits aufgenommenen
    # Snapshot explizit herein; dort findet keine zweite PnL-Abfrage statt.
    close_snapshot = position_snapshot
    close_snapshot_loaded = position_snapshot is not _POSITION_SNAPSHOT_UNSET
    # Der eToro-Adapter darf seinen grossen Neustart-History-Cursor erst dann
    # quittieren, wenn genau die gelieferten Belege dauerhaft im FillTracker
    # angekommen sind. Bereits bekannte IDs zaehlen ebenfalls als committed.
    committed_fill_ids: set[str] = set()

    def _commit_fill(token, fill_obj) -> None:
        fill_tracker.commit(token)
        fill_id = str(getattr(fill_obj, "fill_id", "") or "")
        if fill_id:
            committed_fill_ids.add(fill_id)

    for raw_fill in fills:
        prepared, token = fill_tracker.prepare(raw_fill)
        if prepared is None:
            known_id = str(getattr(raw_fill, "fill_id", "") or "")
            if known_id:
                committed_fill_ids.add(known_id)
            continue

        fill = prepared
        try:
            broker_account = (str(broker.account_fingerprint() or "")
                              if callable(getattr(broker, "account_fingerprint", None))
                              else "")
            fill_account = str(getattr(fill, "account_fingerprint", "") or "")
            if broker_account and fill_account and fill_account != broker_account:
                raise BrokerFehler(
                    "Fill-Kontofingerabdruck widerspricht der aktiven "
                    "Brokerverbindung; Buchung bleibt unquittiert")
            symbol = fill.symbol
            fill_asset_type = str(getattr(fill, "asset_type", "") or "").lower()
            try:
                fill_key = canonical_key(symbol, fill_asset_type)
            except Exception:
                fill_key = ""
            inst = instrument_by_symbol.get(fill_key) if fill_key else None
            contract = (
                getattr(inst, "contract", None)
                or _ErsatzKontrakt(symbol, fill.currency)
            )
            asset_type = inst.asset_type if inst else fill.asset_type
            currency = fill.currency or (inst.currency if inst else "")
            side = str(fill.side or "").upper()
            qty = float(fill.quantity or 0)
            price = float(fill.price or 0)
            meta = order_meta.get(str(fill.order_id), {})
            if not meta and ownership_registry is not None:
                meta = ownership_registry.metadata(fill.order_id)
            # Wenn beim Order-Submit die Internetverbindung genau zwischen
            # Brokerannahme und lokaler Antwort abreisst, kennen wir die Order-ID
            # eventuell noch nicht. Fuer kurze Zeit darf deshalb die zuvor
            # gespeicherte symbolbezogene Kaufabsicht als Metadaten-Fallback dienen.
            if not meta and side.startswith("BUY"):
                pending_key = f"PENDING_BUY:{canonical_key(symbol, fill_asset_type)}"
                pending = order_meta.get(pending_key, {})
                if not pending and ownership_registry is not None:
                    pending = ownership_registry.pending_metadata(symbol, fill_asset_type)
                age = time.time() - float(pending.get("created_at_ts", 0) or 0)
                ttl = float(getattr(config, "PENDING_ORDER_OWNERSHIP_MATCH_SECONDS", 600))
                if pending and 0 <= age <= max(60.0, ttl):
                    # Eine verlorene Submit-Antwort beweist NICHT, dass ein
                    # spaeter sichtbarer unbekannter Fill wirklich vom Bot kam.
                    # Deshalb nur Metadaten uebernehmen, Ownership aber explizit
                    # AMBIGUOUS/OBSERVE halten. Sicherheit vor Automatisierung.
                    meta = dict(pending); meta["_ownership_ambiguous"] = True
                    if ownership_registry is not None and str(fill.order_id):
                        ownership_registry.register_ambiguous_order(fill.order_id, symbol, meta, asset_type=fill_asset_type)
                    order_meta[str(fill.order_id)] = dict(meta)
                    order_meta.pop(pending_key,None)
                    if ownership_registry is not None: ownership_registry.clear_pending(symbol, fill_asset_type)
                elif pending and age > max(60.0, ttl):
                    # Alte, nicht mehr belastbare Ambiguitaets-Metadaten nicht
                    # spaeter versehentlich einem manuellen Kauf zuordnen.
                    order_meta.pop(pending_key, None)

            if not side or qty <= 0 or price <= 0:
                # Ungueltige Broker-Zeile quittieren, damit sie nicht endlos
                # wieder auftaucht.
                _commit_fill(token, fill)
                continue

            if side.startswith("BUY"):
                stop = float(meta.get("stop", 0) or 0)
                take = float(meta.get("take", 0) or 0)
                reason = meta.get("reason", "")
                if getattr(fill, "explicit_fees", None) is not None:
                    one_way_cost = max(0.0, float(fill.explicit_fees or 0.0))
                else:
                    try:
                        one_way_cost = estimate_one_way_cost(
                            qty, price, asset_type, currency, side="buy",
                            broker=getattr(broker, "name", None),
                            include_execution_friction=False,
                        )["total"]
                    except Exception:
                        one_way_cost = 0.0

                fill_owner = classify_fill_owner(
                    meta, ownership_registry, fill.order_id,
                    account_fingerprint=broker_account,
                    paper=bool(getattr(
                        broker, "paper",
                        getattr(config, "PAPER_TRADING", True))),
                    broker_name=str(getattr(broker, "name", "") or ""),
                )
                ambiguous_owned = fill_owner == "AMBIGUOUS"
                bot_owned = fill_owner == "BOT"
                protection_confirmed = True
                protection_detail = ""
                if bot_owned and str(getattr(broker, "name", "")).lower() == "etoro":
                    protection_instrument, protection_iid = _etoro_fill_instrument(fill, meta)
                    # Positions/ledger keep the broker's instrument identity,
                    # not an unrelated contract or this execution's positionId.
                    contract = protection_instrument.contract
                    protection = broker.reconcile_position_protection(
                        protection_instrument, qty, stop, take,
                        position_ids=[str(fill.broker_id)] if fill.broker_id else [],
                        instrument_id=protection_iid)
                    resolved_iid = str(protection.get("instrument_id") or protection_iid)
                    if resolved_iid:
                        if not resolved_iid.isdigit() or (protection_iid and resolved_iid != protection_iid):
                            raise BrokerFehler("eToro-Schutzantwort mit widerspruechlicher instrumentId")
                        contract.conId = resolved_iid
                    protection_confirmed = protection_result_confirmed(protection)
                    protection_detail = str(protection.get("detail") or "")
                rec = manager.register_buy(
                    contract,
                    qty,
                    price,
                    currency,
                    asset_type,
                    equity,
                    stop,
                    take,
                    reason=reason,
                    profile=getattr(config, "ACTIVE_PROFILE", ""),
                    estimated_entry_cost=one_way_cost,
                    source="BOT" if bot_owned else ("AMBIGUOUS" if ambiguous_owned else "MANUAL"),
                    management_mode=("AUTO" if bot_owned and protection_confirmed
                                     else "PENDING_CONFIRMATION" if bot_owned
                                     else "OBSERVE"),
                    # v9.3: Die eToro-positionId aus dem Fill weiterreichen.
                    # Bis 9.2 endete sie hier -- register_buy musste danach
                    # ueber Symbol und Menge raten, und eine selbst gekaufte
                    # MSFT-Position blieb am 31.08.2026 "Externer Bestand".
                    position_ids=[str(fill.broker_id)] if getattr(fill, "broker_id", "") else [],
                    order_ids=[str(fill.order_id)] if fill.order_id else [],
                    reference_id=str(meta.get("reference_id") or ""),
                    decision_id=int(meta.get("decision_id") or 0),
                    instrument_id=str(getattr(contract, "conId", "") or ""),
                    account_fingerprint=(str(broker.account_fingerprint())
                        if callable(getattr(broker, "account_fingerprint", None)) else ""),
                    broker_environment=("DEMO" if bool(getattr(broker, "paper", True))
                                        else "LIVE"),
                    snapshot_id=(str(broker.portfolio_snapshot_id())
                                 if callable(getattr(broker, "portfolio_snapshot_id", None)) else ""),
                    fill_id=str(getattr(fill, "fill_id", "") or ""),
                )
                if bot_owned and not protection_confirmed:
                    rec.management_note = (
                        "Eigene positionId bestaetigt, Broker-Schutz aber noch "
                        "nicht exakt rueckgelesen; keine automatischen Exits. "
                        + protection_detail)[:300]
                    manager.save()
                position_value = qty * price
                risk_amount = rec.planned_risk_amount
                risk_pct = rec.planned_risk_pct
                rr = (
                    ((take - price) * qty / risk_amount)
                    if risk_amount > 0 and take > price
                    else 0.0
                )
                if bot_owned:
                    _risk_write(risk.register_estimated_cost, 
                        one_way_cost,
                        event_id=f"{getattr(broker, 'name', '?')}:{fill.fill_id}:entry-cost")
                    try:
                        requested_qty = float(meta.get("qty", meta.get("planned_qty", 0)) or 0)
                        fill_status = ("FILLED" if requested_qty > 0 and qty >= requested_qty
                                       else "PARTIALLY_FILLED")
                        record_order_state(
                            meta.get("decision_id"), broker=getattr(broker, "name", "etoro"),
                            broker_order_id=str(fill.order_id), role="ENTRY",
                            status=fill_status, symbol=symbol,
                            requested_qty=requested_qty or None, filled_qty=qty,
                            remaining_qty=max(0.0, requested_qty - qty) if requested_qty else None,
                            avg_fill_price=price, fees=getattr(fill, "explicit_fees", None),
                            currency=currency,
                            raw={"fill_id": str(getattr(fill, "fill_id", "") or "")},
                        )
                        mark_execution(
                            meta.get("decision_id"), "FILLED", [fill.order_id],
                            reference_id=str(meta.get("reference_id") or ""),
                            position_ids=[fill.broker_id] if getattr(fill, "broker_id", "") else [],
                            fill_price=price, fill_qty=qty, broker_paper=bool(broker.ist_paper()),
                        )
                    except Exception as exc:
                        logger.debug("Decision-Fillstatus nicht aktualisiert: %s", exc)
                    # Trade-Ledger (v8.1.3, Etappe A): reine Aufzeichnung.
                    # Ein Fehler hier darf den Kauf nicht beruehren.
                    try:
                        import trade_ledger
                        trade_ledger.trade_open(
                            broker=getattr(broker, "name", "etoro").lower(),
                            symbol=symbol, menge=qty, einstieg_preis=price,
                            referenzpreis=float(meta.get("signal_price", 0) or 0) or None,
                            asset_type=asset_type, waehrung=currency,
                            gebuehr=getattr(fill,"explicit_fees",None), paper=bool(broker.ist_paper()),
                            decision_id=meta.get("decision_id"),
                            marktphase=str(meta.get("regime", "") or ""),
                            entry_strategy_mode=str(meta.get("entry_strategy_mode") or ""),
                            strategy_parameter_hash=str(meta.get("strategy_parameter_hash") or ""),
                            strategy_parameters=meta.get("strategy_parameters") or {},
                            enter_tag=str(meta.get("reason", "") or "")[:160],
                            broker_position_id=str(getattr(fill, "broker_id", "") or ""),
                            entry_order_id=str(getattr(fill, "order_id", "") or ""),
                            entry_fill_id=str(getattr(fill, "fill_id", "") or ""),
                            broker_account_fingerprint=(str(broker.account_fingerprint())
                                if callable(getattr(broker, "account_fingerprint", None)) else ""),
                            reconciliation_status="CONFIRMED_OPEN",
                            critical=True)
                    except Exception as exc:
                        logger.error("Trade-Ledger Einstieg eToro nicht geschrieben: %s", exc)
                        raise
                else:
                    try:
                        record_decision(status="MANUAL_BUY", broker=getattr(broker,"name","?"),
                                        paper=bool(getattr(config,"PAPER_TRADING",True)), symbol=symbol,
                                        asset_type=asset_type, price=price, qty=qty, reason="manueller Broker-Kauf erkannt")
                    except Exception as exc:
                        logger.debug("Manueller Kauf nicht journalisiert: %s", exc)
                if tagesliste is not None:
                    tagesliste.append(("KAUF  " if bot_owned else "MANUELLER KAUF  ") +
                                     f"{symbol} {qty:g} @ {price:.4f} | Kosten~{one_way_cost:.2f}")
                if notify_enabled:
                    notify_trade(
                        "KAUF AUSGEFÜHRT" if bot_owned else "MANUELLER KAUF ERKANNT · NUR BEOBACHTEN",
                        buy_message(
                            symbol=symbol,
                            asset_type=asset_type,
                            qty=qty,
                            fill_price=price,
                            currency=currency,
                            position_value=position_value,
                            account_equity=equity,
                            stop=stop,
                            take=take,
                            risk_amount=risk_amount,
                            risk_pct=risk_pct,
                            rr=rr,
                            reason=(reason or "Order ausgeführt") if bot_owned else (
                                "Orderherkunft nach verlorenem Submit nicht eindeutig. Position bleibt sicherheitshalber nur beobachtet; Bot berücksichtigt Risiko/Portfolio, führt aber keine automatischen Verkäufe aus."
                                if ambiguous_owned else
                                "Manuell beim Broker gekauft. Bot berücksichtigt Risiko/Portfolio, führt aber keine automatischen Verkäufe aus."
                            ),
                            ml_probability=(
                                float(meta["ml_probability"])
                                if meta.get("ml_probability") is not None else None
                            ),
                            profile=getattr(config, "ACTIVE_PROFILE", ""),
                            entry_time=rec.entry_time,
                            estimated_cost=float(
                                meta.get("estimated_roundtrip_cost", 0) or 0
                            ),
                            net_edge_pct=meta.get("net_edge_pct"),
                            event_summary=str(meta.get("event_summary", "") or ""),
                            edge_model_reason=str(
                                meta.get("edge_model_reason", "") or ""
                            ),
                            plausible_move_pct=meta.get("plausible_move_pct"),
                        ),
                        event_id=f"trade-fill:{getattr(broker, 'name', '?')}:{fill.fill_id}:buy",
                    )
                order_meta.pop(f"PENDING_BUY:{canonical_key(symbol, fill_asset_type)}", None)
                if bot_owned and ownership_registry is not None:
                    ownership_registry.clear_pending(symbol, fill_asset_type)

            elif side.startswith("SELL"):
                fill_pid = str(getattr(fill, "broker_id", "") or "")
                account_fingerprint = (str(broker.account_fingerprint() or "")
                    if callable(getattr(broker, "account_fingerprint", None)) else "")
                rec_before = (manager.get_by_position_id(
                                  fill_pid, account_fingerprint=account_fingerprint)
                              if fill_pid and hasattr(manager, "get_by_position_id")
                              else None)
                if not fill_pid:
                    rec_before = manager.get(contract)
                sell_owner = classify_fill_owner(
                    meta, ownership_registry, fill.order_id,
                    account_fingerprint=broker_account,
                    paper=bool(getattr(
                        broker, "paper",
                        getattr(config, "PAPER_TRADING", True))),
                    broker_name=str(getattr(broker, "name", "") or ""),
                )
                sell_ambiguous = sell_owner == "AMBIGUOUS"
                sell_bot_owned = sell_owner == "BOT"
                attribution_deferred = rec_before is None
                if not attribution_deferred:
                    sell_bot_owned, meta = _etoro_exit_attribution(
                        broker=broker, fill=fill, rec_before=rec_before, meta=meta,
                        sell_bot_owned=sell_bot_owned, price=price)
                entry_price = rec_before.avg_cost if rec_before else 0.0
                stop = (
                    rec_before.planned_stop
                    if rec_before
                    else float(meta.get("stop", 0) or 0)
                )
                take = (
                    rec_before.planned_take
                    if rec_before
                    else float(meta.get("take", 0) or 0)
                )
                entry_time = rec_before.entry_time if rec_before else ""
                invested_value = qty * entry_price if entry_price > 0 else 0.0
                proceeds = qty * price
                if getattr(fill, "explicit_fees", None) is not None:
                    one_way_cost = max(0.0, float(fill.explicit_fees or 0.0))
                else:
                    try:
                        one_way_cost = estimate_one_way_cost(
                            qty, price, asset_type, currency, side="sell",
                            broker=getattr(broker, "name", None),
                            include_execution_friction=False,
                        )["total"]
                    except Exception:
                        one_way_cost = 0.0

                rec, pnl_calc, pnl_pct_calc, remaining = manager.register_sell(
                    contract,
                    qty,
                    price,
                    currency,
                    estimated_exit_cost=one_way_cost,
                    execution_source="BOT" if sell_bot_owned else "MANUAL",
                    position_ids=[fill_pid] if fill_pid else [],
                    account_fingerprint=account_fingerprint,
                    fill_id=str(getattr(fill, "fill_id", "") or ""),
                )
                # Ein Prozessabbruch direkt nach der Positionsmutation, aber
                # vor Ledger/Telegram, fuehrt beim Replay hierher. Der Manager
                # liefert dann seinen persistierten Vorher-Snapshot, damit die
                # noch fehlenden Schritte mit denselben P&L-Werten fortfahren.
                if rec_before is None and rec is not None:
                    rec_before = rec
                    entry_price = float(rec.avg_cost or 0.0)
                    stop = float(rec.planned_stop or 0.0)
                    take = float(rec.planned_take or 0.0)
                    entry_time = str(rec.entry_time or "")
                    invested_value = qty * entry_price if entry_price > 0 else 0.0
                if attribution_deferred:
                    # Nach einem Prozess-/Ledgerfehler kann register_sell beim
                    # Replay den persistierten Vorher-Snapshot zurueckgeben,
                    # obwohl get_by_position_id die bereits entfernte Position
                    # nicht mehr findet. Erst mit diesem Snapshot darf der
                    # Exitgrund erneut bestimmt werden; sonst wuerde ein echter
                    # Stop im Replay als manueller Brokerverkauf verbucht.
                    sell_bot_owned, meta = _etoro_exit_attribution(
                        broker=broker, fill=fill, rec_before=rec_before, meta=meta,
                        sell_bot_owned=sell_bot_owned, price=price)
                pnl_known = bool(rec_before is not None and entry_price > 0)
                if pnl_known:
                    pnl = pnl_calc
                    pnl_pct = pnl_pct_calc
                    gross_trade_pnl = (price - entry_price) * qty
                else:
                    # Niemals fehlende Einstandsdaten als 0,00 USD Gewinn
                    # verbuchen. Stattdessen explizit als unvollstaendig merken.
                    pnl = None
                    pnl_pct = None
                    if sell_bot_owned:
                        _risk_write(risk.register_unknown_pnl_trade, 
                            trade_id=f"{getattr(broker, 'name', '?')}:{fill.fill_id}")
                ledger_trade_id = None
                buchungsluecke = False
                try:
                    import trade_ledger
                    ledger_trade_id = trade_ledger.trade_close(
                        broker=getattr(broker, "name", "etoro").lower(), symbol=symbol,
                        ausstieg_preis=price, menge=qty,
                        exit_grund=str(meta.get("label") or meta.get("reason") or ""),
                        gebuehr=getattr(fill,"explicit_fees",None), netto_pnl=None,
                        einstieg_preis=entry_price or None, eingestiegen_am=entry_time,
                        asset_type=asset_type, waehrung=currency,
                        paper=bool(broker.ist_paper()),
                        zeit=getattr(fill, "timestamp", None),
                        exit_order_id=str(fill.order_id or ""),
                        exit_fill_ids=[str(fill.fill_id or "")],
                        event_id=str(fill.fill_id or ""),
                        broker_position_id=fill_pid,
                        broker_account_fingerprint=account_fingerprint,
                        entry_order_id=(str((rec_before.entry_order_ids or [""])[0])
                                        if rec_before else ""),
                        notiz=("brokerseitiger Schutzexit" if meta.get("protective_exit_attributed")
                               else "" if sell_bot_owned else "manueller Broker-Verkauf"),
                        critical=bool(sell_bot_owned))
                    if sell_bot_owned and ledger_trade_id is None:
                        # ``critical=True`` muss auch fachliche None-Rueckgaben
                        # fail-closed behandeln. Sonst wuerde der Fill unten
                        # quittiert, obwohl der Verkauf nie im Ledger ankam.
                        raise _CriticalFillAccountingError(
                            f"Kritischer Ledger-Ausstieg ohne Buchungsbeleg ({symbol})")
                    if not sell_bot_owned and ledger_trade_id is None:
                        # KORREKTUR 9.5.5: Hier wurde bisher gar nichts getan.
                        # ``critical`` haengt an der Zuordnungsheuristik: faellt
                        # sie auf "manueller Broker-Verkauf" zurueck, war der
                        # Abschluss nicht kritisch, ``trade_close`` gab in
                        # mehreren Zweigen still None zurueck, und der Fill galt
                        # trotzdem als erledigt. Ein selbst geschlossener
                        # Bot-Trade konnte damit im Geldbuch fehlen.
                        #
                        # Fail-closed waere hier falsch -- ein echter
                        # Fremdverkauf HAT keine Ledgerzeile und darf den
                        # Handel nicht anhalten. Sichtbar muss es trotzdem
                        # sein, deshalb eine Warnung und ein Vermerk.
                        logger.warning(
                            "eToro-Verkauf %s (positionId %s) wurde keiner "
                            "Ledgerzeile zugeordnet. Wenn das ein eigener "
                            "Trade war, fehlt sein Ergebnis im Geldbuch.",
                            symbol, fill_pid)
                        try:
                            from etoro_reconciliation import melde_buchungsluecke
                            import etoro_reconciliation as _rec
                            melde_buchungsluecke(
                                symbol=symbol, broker_position_id=str(fill_pid or ""),
                                grund="Verkaufsfill ohne zugeordnete Ledgerzeile",
                                domain=_rec.domain_key(paper=bool(broker.ist_paper()),
                                    profile="", account_fingerprint=account_fingerprint),
                                account_fingerprint=account_fingerprint,
                                environment="DEMO" if bool(broker.ist_paper()) else "LIVE",
                                close_evidence={"fill_id": str(fill.fill_id or ""),
                                    "order_id": str(fill.order_id or ""), "quantity": float(fill.quantity),
                                    "price": float(fill.price),
                                    "closed_at_utc": str(getattr(fill, "timestamp", "") or "")})
                        except Exception:
                            logger.warning(
                                "Buchungsluecke %s nicht vermerkt", symbol,
                                exc_info=True)
                except Exception as exc:
                    if sell_bot_owned and _ledger_zuordnung_unklar(exc):
                        # 9.5.8: Der Verkauf HAT stattgefunden -- die Position
                        # ist beim Broker weg und wurde oben bereits aus dem
                        # Positionsbuch entfernt. Nur die Zuordnung zu einer
                        # Ledgerzeile ist unmoeglich. Das kostet Genauigkeit im
                        # Geldbuch; den Handel anzuhalten kostet dagegen den
                        # Schutz aller anderen Positionen. Das kleinere Uebel
                        # ist der unvollstaendige Datensatz.
                        #
                        # Es wird ausdruecklich NICHTS geraten: keine fremde
                        # Ledgerzeile wird geschlossen, kein Ersatztrade
                        # angelegt. Der Verkauf wird als Buchungsluecke
                        # festgehalten und der Tages-PnL als unvollstaendig
                        # markiert, damit die Luecke sichtbar bleibt.
                        logger.error(
                            "Verkauf %s (positionId %s) ist keiner Ledgerzeile "
                            "zuzuordnen: %s -- als Buchungsluecke vermerkt, "
                            "der Handel laeuft weiter.",
                            symbol, fill_pid, exc)
                        try:
                            import etoro_reconciliation as _rec
                            _rec.melde_buchungsluecke(
                                symbol=symbol,
                                broker_position_id=str(fill_pid or ""),
                                grund=f"Eigener Verkauf ohne eindeutige Ledgerzeile: {exc}",
                                domain=_rec.domain_key(
                                    paper=bool(broker.ist_paper()), profile="",
                                    account_fingerprint=account_fingerprint),
                                account_fingerprint=account_fingerprint,
                                environment="DEMO" if bool(broker.ist_paper()) else "LIVE",
                                close_evidence={"fill_id": str(fill.fill_id or ""),
                                    "order_id": str(fill.order_id or ""), "quantity": float(fill.quantity),
                                    "price": float(fill.price),
                                    "closed_at_utc": str(getattr(fill, "timestamp", "") or "")})
                        except Exception as gap_exc:
                            # Ohne durable Ledgerbuchung UND ohne durable
                            # Reparaturmarke darf der Brokerfill nicht quittiert
                            # werden. Sonst verschwindet er dauerhaft aus dem
                            # Retry-Pfad.
                            logger.error("Buchungsluecke %s nicht dauerhaft vermerkt: %s",
                                         symbol, gap_exc, exc_info=True)
                            raise _CriticalFillAccountingError(
                                f"Buchungsluecke nicht speicherbar ({symbol})") from gap_exc
                        # Ohne Ledgerquittung bleibt das Ergebnis unbekannt.
                        pnl_known = False
                        pnl = None
                        pnl_pct = None
                        _risk_write(risk.register_unknown_pnl_trade, 
                            trade_id=f"{getattr(broker, 'name', '?')}:{fill.fill_id}")
                        try:
                            notify(
                                "BUCHUNGSLUECKE VERKAUF",
                                f"{symbol}: Der Verkauf wurde beim Broker "
                                f"ausgefuehrt, laesst sich aber keiner "
                                f"Ledgerzeile zuordnen ({exc}). "
                                + ("Das Ergebnis fehlt im Geldbuch, der "
                                   "Tagesgewinn ist unvollstaendig. "
                                   if not pnl_known else
                                   "Das Ergebnis steht im Tagesbuch, im "
                                   "Geldbuch fehlt die Zuordnung. ")
                                + "Der Handel laeuft weiter.")
                        except Exception:
                            logger.debug("Meldung zur Buchungsluecke fehlgeschlagen",
                                         exc_info=True)
                        ledger_trade_id = None
                        # 9.5.8: Der FILL ist bewiesen -- nur seine Ledgerzeile
                        # fehlt. Das Exit-Journal muss trotzdem quittiert
                        # werden, sonst bleibt der Intent dauerhaft ACTIVE: der
                        # eToro-Adapter fragt ihn dann alle 10 s erneut ab, fuer
                        # eine laengst geschlossene Position, unbegrenzt -- und
                        # ein spaeterer Verkauf derselben positionId liefe in
                        # OrderStatusUnklar.
                        buchungsluecke = True
                    else:
                        logger.error("Trade-Ledger Ausstieg eToro nicht geschrieben: %s", exc)
                        if sell_bot_owned:
                            if isinstance(exc, _CriticalFillAccountingError):
                                raise
                            raise _CriticalFillAccountingError(
                                f"Kritischer Ledger-Ausstieg fehlgeschlagen ({symbol})"
                            ) from exc
                if sell_bot_owned and ledger_trade_id is not None:
                    try:
                        from ledger_result import confirmed_net
                        receipt = trade_ledger.trade_detail(ledger_trade_id)
                        verified_net = confirmed_net(receipt or {})
                        if verified_net and (
                                str(receipt.get("broker") or "").lower() != str(getattr(broker, "name", "")).lower()
                                or str(receipt.get("broker_account_fingerprint") or "") != account_fingerprint
                                or receipt.get("paper") not in (0, 1, False, True)
                                or bool(receipt["paper"]) != bool(broker.ist_paper())
                                or str(receipt.get("broker_position_id") or "") != str(fill_pid or "")
                                or not receipt.get("ausgestiegen_am")):
                            raise _CriticalFillAccountingError(
                                "ETORO_RISK_RECEIPT_SCOPE_MISMATCH: Ledgerbeleg "
                                "passt nicht zur Fill-/Konto-/Umgebungsidentitaet")
                        risk_key = f"ledger:{ledger_trade_id}"
                        alias = getattr(risk, "adopt_receipt_alias", None)
                        if callable(alias):
                            _risk_write(alias, risk_key, [f"{getattr(broker, 'name', '?')}:{fill.fill_id}"])
                        # Establish a replayable exact receipt before the result
                        # write. On a crash/failure between these writes the normal
                        # recovery can discover ledger:<id>, even if the next broker
                        # poll has no fills. Alias adoption stays FIRST, so an old
                        # already-counted receipt is not counted a second time.
                        unknown_at = getattr(risk, "register_unknown_pnl_at", None)
                        if callable(unknown_at) and (receipt or {}).get("ausgestiegen_am"):
                            _risk_write(unknown_at, risk_key, receipt["ausgestiegen_am"])
                        else:
                            _risk_write(risk.register_unknown_pnl_trade, trade_id=risk_key)
                        if not verified_net:
                            pnl_known = False
                            pnl = None
                            pnl_pct = None
                        else:
                            pnl = float(receipt["netto_pnl"])
                            pnl_known = True
                            _risk_write(risk.register_realized_pnl, pnl, equity,
                                gross_pnl=float(receipt["brutto_pnl"]), trade_id=risk_key)
                    except _CriticalFillAccountingError:
                        raise
                    except Exception as exc:
                        raise _CriticalFillAccountingError(
                            "ETORO_RISK_RECEIPT_UNCONFIRMED: Exakter Ledgerbeleg "
                            "konnte nicht als Tagesrisiko bestaetigt werden") from exc
                # Ein adapterseitiger Exit-Intent wird erst NACH dem
                # erfolgreichen Ledger-Commit fortgeschrieben. Scheitert der
                # Prozess davor oder hier, bleibt der Fill unquittiert und
                # wird idempotent erneut verarbeitet. Broker-History-Fills
                # ohne lokalen Intent liefern einfach eine leere Zuordnung.
                if (str(getattr(broker, "name", "") or "").lower() == "etoro"
                        and fill_pid
                        and (ledger_trade_id is not None or buchungsluecke)):
                    try:
                        import broker_exit_journal
                        broker_exit_journal.confirm_from_fill(
                            broker="etoro",
                            account_fingerprint=account_fingerprint,
                            position_id=fill_pid,
                            broker_order_id=str(fill.order_id or ""),
                            environment="DEMO" if bool(broker.ist_paper()) else "LIVE",
                            instrument_id=_etoro_fill_instrument(fill, meta)[1],
                            filled_quantity=qty,
                            detail=dict(getattr(fill, "broker_detail", {}) or {}),
                            fill_identity=str(fill.fill_id or ""),
                        )
                    except Exception as exc:
                        logger.error(
                            "eToro-Exit-Journal nach Ledger-Commit nicht "
                            "fortgeschrieben (%s/%s): %s",
                            symbol, fill_pid, exc)
                        if sell_bot_owned:
                            raise _CriticalFillAccountingError(
                                f"Kritisches Exit-Journal fehlgeschlagen ({symbol})"
                            ) from exc
                        raise
                # Ein SELL-Fill allein beweist bei eToro noch keinen terminalen
                # Close: Teilverkaeufe behalten dieselbe positionId. Erst wenn
                # die lokale Restmenge null UND ein vollstaendiger, konto- und
                # umgebungsgebundener Depot-Snapshot genau diese positionId
                # nicht mehr offen fuehrt, darf der Entry-Lock geschlossen
                # werden. Die Symbolgleichheit spielt dabei keine Rolle.
                if (str(getattr(broker, "name", "") or "").lower() == "etoro"
                        and fill_pid):
                    if not close_snapshot_loaded:
                        close_snapshot_loaded = True
                        snapshot_getter = getattr(broker, "position_snapshot", None)
                        try:
                            close_snapshot = (snapshot_getter(force=True)
                                              if callable(snapshot_getter) else None)
                        except Exception as exc:
                            close_snapshot = None
                            logger.warning(
                                "eToro-Close-Snapshot fuer %s nicht lesbar; "
                                "Reconciliation bleibt offen: %s", fill_pid, exc)
                    paper = bool(getattr(
                        broker, "paper", getattr(config, "PAPER_TRADING", True)))
                    terminal_absence, absence_detail = (
                        _position_absent_in_complete_snapshot(
                            close_snapshot,
                            position_id=fill_pid,
                            account_fingerprint=account_fingerprint,
                            paper=paper))
                    if remaining <= 1e-12 and terminal_absence:
                        import etoro_reconciliation
                        etoro_reconciliation.confirm_position_closed(
                            paper=paper,
                            account_fingerprint=account_fingerprint,
                            position_id=fill_pid,
                            close_order_id=str(fill.order_id or ""),
                            fill_id=str(fill.fill_id or ""),
                            close_detail={
                                "symbol": symbol,
                                "quantity": qty,
                                "closePrice": price,
                                "closed_at_utc": str(
                                    getattr(fill, "timestamp", "") or ""),
                                "execution_reason": str(
                                    getattr(fill, "execution_reason", "") or ""),
                                "ledger_trade_id": ledger_trade_id,
                                "snapshot_id": str(
                                    (close_snapshot or {}).get("snapshot_id")
                                    or (close_snapshot or {}).get("_snapshot_id") or ""),
                            },
                        )
                    else:
                        logger.info(
                            "eToro-SELL %s positionId=%s nicht terminalisiert "
                            "(Rest=%s; %s)", symbol, fill_pid, remaining,
                            absence_detail)
                if not sell_bot_owned:
                    try:
                        record_decision(status="MANUAL_SELL", broker=getattr(broker,"name","?"),
                                        paper=bool(getattr(config,"PAPER_TRADING",True)), symbol=symbol,
                                        asset_type=asset_type, price=price, qty=qty, reason="manueller Broker-Verkauf erkannt")
                    except Exception as exc:
                        logger.debug("Manueller Verkauf nicht journalisiert: %s", exc)
                reason = (
                    meta.get("reason")
                    or meta.get("label")
                    or "Verkauf / Exit"
                )
                label = meta.get("label", "VERKAUF")
                if sell_bot_owned:
                    _risk_write(risk.register_estimated_cost, 
                        one_way_cost,
                        event_id=f"{getattr(broker, 'name', '?')}:{fill.fill_id}:exit-cost")
                try:
                    broker.on_fill_housekeeping(fill)
                except (VerbindungVerloren, AuthentifizierungsFehler):
                    raise
                except Exception as exc:
                    logger.warning("Broker-Housekeeping nach SELL-Fill %s fehlgeschlagen: %s", symbol, exc)
                if tagesliste is not None:
                    tagesliste.append(("VERK. " if sell_bot_owned else "MANUELLER VERK. ") +
                                     f"{symbol} {qty:g} @ {price:.4f} | Kosten~{one_way_cost:.2f}")
                if notify_enabled:
                    notify_trade(
                        "VERKAUF AUSGEFÜHRT" if sell_bot_owned else "MANUELLER VERKAUF ERKANNT",
                        sell_message(
                            symbol=symbol,
                            asset_type=asset_type,
                            qty=qty,
                            entry_price=entry_price,
                            exit_price=price,
                            currency=currency,
                            invested_value=invested_value,
                            proceeds=proceeds,
                            pnl=pnl,
                            pnl_pct=pnl_pct,
                            reason=reason,
                            holding_text=(
                                _holding_text(entry_time)
                                if entry_time and getattr(rec_before, "source", "") == "BOT"
                                else None
                            ),
                            account_equity=equity,
                            daily_realized=risk.realized_pnl_today,
                            tagesbuch=_tagesbuch(broker, risk),
                            stop=stop,
                            take=take,
                            order_label=label,
                            estimated_exit_cost=one_way_cost,
                            remaining=remaining,
                            pnl_complete=(getattr(risk, "unknown_pnl_trades_today", 0) == 0),
                        ),
                        event_id=f"trade-fill:{getattr(broker, 'name', '?')}:{fill.fill_id}:sell",
                    )
            else:
                _commit_fill(token, fill)
                continue

            _commit_fill(token, fill)
            processed += 1

        except _CriticalFillAccountingError as exc:
            # Kein Commit und kein Weiterlaufen in den Kaufpfad. Der Aufrufer
            # setzt die Readiness rot und versucht denselben Fill spaeter
            # idempotent erneut.
            logger.exception(
                "Kritische Fill-Buchung fehlgeschlagen (%s/%s): %s",
                getattr(raw_fill, "order_id", "?"),
                getattr(raw_fill, "fill_id", "?"), exc)
            raise
        except Exception as exc:
            # KEIN Commit: das Delta wird beim nächsten Poll erneut versucht.
            logger.exception(
                "Fill-Verarbeitung fehlgeschlagen (%s/%s): %s",
                getattr(raw_fill, "order_id", "?"),
                getattr(raw_fill, "fill_id", "?"),
                exc,
            )

    # A recovery worker can encounter a storage failure even when this poll
    # contains no fresh executions. First allow exact fill replays above to
    # repair their own receipt; then forbid returning into the BUY path while
    # any RiskState persistence operation is still unconfirmed.
    persistence_reason = getattr(risk, "persistence_failure_reason", None)
    if callable(persistence_reason) and persistence_reason():
        raise _CriticalFillAccountingError(
            "ETORO_RISK_RECOVERY_INCOMPLETE: Offene Risikobuchung; "
            "keine Kauf-Freigabe nach diesem Fillpoll")

    history_ack = getattr(broker, "ack_history_recovery_committed", None)
    if callable(history_ack):
        try:
            history_ack(committed_fill_ids)
        except Exception as exc:
            # Der grosse Cursor bleibt bei einem ACK-Fehler offen. Die bereits
            # committeden Fills werden durch den Tracker sicher dedupliziert.
            logger.warning(
                "eToro-History-Recovery konnte nicht quittiert werden; "
                "der grosse Cursor bleibt aktiv: %s", exc)

    return processed


def _confirm_ledger_from_current_positions(broker) -> int:
    """Offene Ledgerzeilen nur aus dem letzten echten Depot-Snapshot bestaetigen."""
    if str(getattr(broker, "name", "") or "").lower() != "etoro":
        return 0
    evidence = (broker.current_position_evidence()
                if hasattr(broker, "current_position_evidence") else {})
    if not evidence:
        return 0
    try:
        import trade_ledger
        count = 0
        account = (str(broker.account_fingerprint())
                   if callable(getattr(broker, "account_fingerprint", None)) else "")
        for symbol, ids in evidence.items():
            # Eine eToro-Depotzeile ist genau eine positionId. Mehrere Lines
            # desselben Symbols duerfen sich weder gegenseitig bestaetigen
            # noch zu einer synthetischen Komma-ID zusammenfallen.
            for position_id in sorted(str(x) for x in ids if str(x)):
                count += int(trade_ledger.confirm_open_trade(
                    broker="etoro", symbol=symbol,
                    broker_position_id=position_id,
                    broker_account_fingerprint=account) or 0)
        return count
    except Exception as exc:
        logger.debug("Ledger-Depotbestaetigung fehlgeschlagen: %s", exc)
        return 0


def run():
    safety_check()
    broker = get_broker()
    print(f"Broker: {broker.beschreibung()}")
    manager = PositionManager()
    from trading_ready import Handelsbereitschaft
    stock_bereitschaft = Handelsbereitschaft(
        "etoro", cfg=config,
        melder=lambda text: notify("AKTIEN-HANDELSBEREITSCHAFT", text))
    stock_bereitschaft.melde("systemzeit", True, "UTC-Systemzeit laeuft")
    stock_bereitschaft.melde("protokoll", True, "Decision- und Trade-Ledger initialisiert")
    shutdown_requested = False
    manual_scan_event = threading.Event()
    connection_was_lost = False
    ever_connected = False
    # 10.4.0: Scan-Uebersicht je Zyklus (eine Zeile im Logbuch statt Stille
    # bei HOLD/SELL ohne Position). Wird je Zyklus neu angelegt.
    scan_zyklus = None
    last_disconnect_alert = 0.0
    last_reconnect_attempt = 0.0
    order_meta = {}
    fill_tracker = FillProgressTracker(getattr(config, 'FILL_TRACKER_FILE', 'fill_progress.json'))
    if fill_tracker.storage_error:
        # 9.7: Der im Feld beobachtete Zustand "Fill-Tracker ist unlesbar"
        # darf den Aktienkern nicht in einer 45/90-s-Neustartschleife halten.
        # Rekonstruktion erfolgt ausschliesslich aus bereits DURABEL gebuchten
        # Ledger-Fills; aktuelle Brokerfills werden danach normal verarbeitet.
        try:
            fill_tracker.recover_from_ledger(
                getattr(config, 'DECISION_DB_FILE', 'decision_history.sqlite'))
        except Exception as exc:
            # Do not enter the 45/90-s restart loop on a persistent accounting
            # storage fault. Mark the equity domain visibly unavailable and
            # stop this worker cleanly; no broker order is submitted.
            logger.exception("Fill-Tracker-Recovery fehlgeschlagen; Aktienworker bleibt pausiert")
            try:
                _rt = RuntimeStatus("runtime_status.json")
                _rt.start(broker=getattr(broker, "name", "etoro"),
                          mode="PAPER" if config.PAPER_TRADING else "LIVE",
                          state="ACCOUNTING_RECOVERY_REQUIRED", broker_connected=False)
                _rt.update(running=False, connection_state="DEGRADED",
                           last_connection_error=f"Fill-Tracker-Recovery: {type(exc).__name__}: {str(exc)[:180]}",
                           _force_write=True)
            except Exception:
                logger.exception("Runtime-Status fuer Fill-Tracker-Fehler konnte nicht geschrieben werden")
            try:
                notify("🔴 Aktienhandel pausiert: Fill-Tracker/Ledger konnte nicht sicher wiederhergestellt werden. Keine neuen eToro-Orders bis zur Klärung.", wichtig=True)
            except Exception:
                logger.warning("Fill-Tracker-Recovery-Warnung nicht zustellbar", exc_info=True)
            return
    ownership_registry = OrderOwnershipRegistry(
        getattr(config, 'BOT_ORDER_REGISTRY_FILE', 'bot_order_registry.json'),
        getattr(config, 'PENDING_ORDER_META_TTL_SECONDS', 900),
    )
    # v5.2.0 konnte durch einen Offline-Integrationstest versehentlich eine
    # simulierte -2.001/-3.001/-6.001 P&L in risk_state.json schreiben.
    # Nur diese exakt erkennbare Testsignatur wird archiviert und verworfen.
    # v8.1.5: Zuerst den Umzug auf die gemeinsame Datei. Vorher fuehrte der
    # Aktienkern risk_state.json und der eToro-Risikotopf risk_state_etoro.json
    # -- die Topfdatei blieb dauerhaft leer, ihre Tagesverlustbremse konnte nie
    # ausloesen, und die brokeruebergreifende Klammer sah die Aktienseite mit
    # 0,00. Der Umzug wirft nichts weg.
    from risk_state_pfad import umzug_etoro, zustandsdatei
    try:
        _umzug = umzug_etoro(melder=notify)
        if _umzug:
            print(f"  {_umzug}")
    except Exception:
        logger.exception("Umzug des Risikozustands fehlgeschlagen")
    _risk_datei = zustandsdatei("etoro")
    try:
        from risk_state_sanity import quarantine_known_test_state
        _bad_state = quarantine_known_test_state(_risk_datei)
        if _bad_state:
            logger.warning("Alte v5.2.0-Test-P&L erkannt und archiviert: %s", _bad_state)
    except Exception as exc:
        logger.debug("RiskState-Sanitycheck nicht ausgefuehrt: %s", exc)
    risk = RiskState.load(_risk_datei)
    runtime = RuntimeStatus("runtime_status.json")
    runtime.start(broker=getattr(broker,"name","?"), mode="PAPER" if config.PAPER_TRADING else "LIVE", state="STARTET", broker_connected=False)
    health_tracker = BrokerHealthTracker(broker)
    telegram_steuerung = None
    auto_maintenance = None
    pulsar_worker = None

    # Betriebszustand aus Datei -- ueberlebt Neustarts, damit ein
    # Not-Stopp nicht durch einen Absturz aufgehoben wird.
    zustand = BotZustand(getattr(config, "BOT_STATE_FILE", "bot_zustand.json"))

    # Profil-Notbremse: eine beschaedigte aktives_profil.txt darf nicht dazu
    # fuehren, dass still mit irgendwelchen Zahlen gekauft wird. PAUSIERT
    # sperrt nur neue Kaeufe -- Stops, Schutzpfad und Verkaeufe laufen weiter.
    if not bool(getattr(config, "PROFILE_BOOTSTRAP_OK", False)):
        grund = str(getattr(config, "PROFILE_BOOTSTRAP_ERROR", "Profil unklar"))
        logger.error("PROFIL-BOOTSTRAP FEHLGESCHLAGEN: %s", grund)
        if zustand.zustand() == AKTIV:
            zustand.setze(PAUSIERT, grund=grund, quelle="profil_bootstrap")
        notify(
            "PROFIL NICHT ANWENDBAR -- KEINE NEUEN KAEUFE",
            f"{grund}\n\nAktuell wirksam:\n{config.profil_klartext()}\n\n"
            "Verkaeufe und Stop-Loss laufen weiter. Bitte Profil in der GUI "
            "neu setzen und den Dienst neu starten.",
        )
    else:
        logger.info("Profil-Bootstrap: %s", config.profil_klartext())

    trades_heute = []
    zyklen_gesamt = [0]           # Liste, damit die Callbacks sie sehen
    no_buy_cycles = 0
    last_no_buy_alert = 0.0
    last_pi_health_alert = 0.0
    pi_health_was_blocked = False
    last_pi_health_check = 0.0
    cached_pi_health_reasons = []

    def _pi_hardware_buy_ok(force: bool = False) -> bool:
        """Pi-Hardwaregate fuer BUYs; SELL-/Schutzpfad bleibt unberuehrt."""
        nonlocal last_pi_health_alert, pi_health_was_blocked
        nonlocal last_pi_health_check, cached_pi_health_reasons
        if not bool(getattr(config, "PI_MODE", False)):
            return True
        now_ts = time.time()
        # Hardwareabfrage hoechstens alle 30 s; dadurch reagiert der Trader
        # schnell, ohne pro Instrument vcgencmd/subprocess aufzurufen.
        if force or (now_ts - last_pi_health_check >= 30.0):
            try:
                cached_pi_health_reasons = trading_safety_reasons(Path(__file__).resolve().parent)
            except Exception as exc:
                logger.debug("Pi-Hardwarewache nicht verfuegbar: %s", exc)
                cached_pi_health_reasons = []
            last_pi_health_check = now_ts
        reasons = list(cached_pi_health_reasons or [])
        if reasons:
            if (not pi_health_was_blocked) or (now_ts - last_pi_health_alert >= 1800.0):
                notify(
                    "PI-HARDWARE: NEUE KAEUFE PAUSIERT",
                    "Der Raspberry Pi meldet einen unsicheren Hardwarezustand:\n- "
                    + "\n- ".join(reasons)
                    + "\n\nBestehende Positionen und Schutz-/Exitlogik bleiben aktiv. "
                      "Neue Kaeufe werden automatisch wieder freigegeben, sobald der Zustand normal ist.",
                )
                last_pi_health_alert = now_ts
            pi_health_was_blocked = True
            return False
        if pi_health_was_blocked:
            notify(
                "PI-HARDWARE WIEDER OK",
                "Unterspannung/Temperatur/RAM/Speicher sind wieder innerhalb der Sicherheitsgrenzen. "
                "Neue Kaeufe folgen wieder den normalen Trading- und Risikoregeln.",
            )
            pi_health_was_blocked = False
        return True

    # Krisenfilter: prueft aktuelle Meldungen vor Kauf und bei offenen
    # Positionen. Faellt auf "keine Pruefung" zurueck, wenn keine
    # Nachrichtendaten verfuegbar sind -- siehe news_filter.py.
    nachrichten = NachrichtenFilter()
    marktqualitaet = MarketQualityClient()
    earnings_client = EarningsClient()
    regime_client = MarketRegimeClient()
    news_radar = NewsRadar([x.get("symbol") for x in config.STOCK_SYMBOLS if x.get("currency","USD")=="USD"])
    attention = AIAttentionPrioritizer()
    attention_cache = {"at": 0.0, "keys": [], "reason": ""}
    if bool(getattr(config, "AI_ATTENTION_ENABLED", False)):
        if attention.enabled:
            print(f"OpenAI-Aufmerksamkeit aktiv: {attention.model} | nur Scan-Priorisierung | Websuche={bool(getattr(config, 'AI_ATTENTION_WEB_SEARCH', True))}")
        else:
            print("HINWEIS: KI-Aufmerksamkeit ist konfiguriert, aber kein OpenAI-Key verfuegbar. Deterministische Rotation bleibt aktiv.")
    _ud_file = Path(__file__).with_name("underdog_freigabe.json")
    _ud_mtime = _ud_file.stat().st_mtime if _ud_file.exists() else 0.0
    underdog_freigabe = {"symbole": set(freigegebene_underdogs()), "geprueft_am": _ud_mtime}

    resync_required = False
    initialization_complete = False
    reconnect_attempts = 0  # aktuelle Fehlerstrecke, NICHT lebenslang kumulativ
    reconnects_total = 0     # erfolgreich wiederhergestellte Verbindungen
    last_connection_error = ""
    offline_since = ""
    startup_wait_notified = False
    runtime_services_ok = str(getattr(broker, "name", "") or "").lower() != "etoro"
    runtime_services_detail = ""

    def _etoro_account_context() -> dict:
        """Aktuelle eToro-Domaene aus Konto und DEMO/LIVE ableiten.

        Ein globaler Reconciliation-Status ist hier unzulaessig: Ein alter
        LIVE-Intent darf kein DEMO-Konto sperren und umgekehrt. Auch ein
        Profilwechsel darf eine kontoweite Sperre weder erzeugen noch umgehen.
        """
        import etoro_reconciliation
        paper = bool(getattr(
            broker, "paper", getattr(config, "PAPER_TRADING", True)))
        profile = str(getattr(config, "ACTIVE_PROFILE", "") or "")
        account = (str(broker.account_fingerprint() or "")
                   if callable(getattr(broker, "account_fingerprint", None))
                   else "")
        domain = etoro_reconciliation.domain_key(
            paper=paper, profile=profile, account_fingerprint=account)
        return {
            "paper": paper,
            "profile": profile,
            "account_fingerprint": account,
            "domain": domain,
        }

    def _order_ownership_binding() -> dict:
        """Registry-Metadaten fest an Broker, Konto und Umgebung binden."""
        paper = bool(getattr(
            broker, "paper", getattr(config, "PAPER_TRADING", True)))
        account = (str(broker.account_fingerprint() or "")
                   if callable(getattr(broker, "account_fingerprint", None))
                   else "")
        return {
            "broker": str(getattr(broker, "name", "") or "").lower(),
            "paper": paper,
            "environment": "DEMO" if paper else "LIVE",
            "account_fingerprint": account,
        }

    def _set_etoro_reconciliation_readiness(*, reconciled: bool,
                                            reconciliation_detail: str = "",
                                            context: dict | None = None) -> dict:
        """Einen konsistenten, domaenengebundenen Readiness-Snapshot setzen."""
        if str(getattr(broker, "name", "") or "").lower() != "etoro":
            stock_bereitschaft.melde(
                "keine_unklare_order", True,
                "Broker verwendet keine eToro-Ausfuehrungsdomaene")
            stock_bereitschaft.melde("buchung_vollstaendig", True, "")
            stock_bereitschaft.melde(
                "reconciliation", bool(reconciled), reconciliation_detail)
            return {"domain": "", "blocked": False, "detail": ""}

        import etoro_reconciliation
        context = dict(context or _etoro_account_context())
        # Genau ein Lesevorgang: Bedingung, Detail und Runtime-Anzeige stammen
        # dadurch garantiert aus derselben Version der Reconciliation-Datei.
        block_detail = str(etoro_reconciliation.blocking_detail(
            context["domain"]) or "").strip()
        from etoro_open_operations import snapshot as open_operation_snapshot
        operations = open_operation_snapshot(context["account_fingerprint"],
            "DEMO" if context["paper"] else "LIVE")
        runtime.update(etoro_open_operations=operations)
        block_detail = "; ".join(x for x in (block_detail, operations["detail"]) if x)
        blocked = bool(block_detail)
        if reconciled:
            rec_detail = (str(reconciliation_detail or "").strip()
                          or "Aktueller eToro-Depot-Snapshot abgeglichen")
            stock_bereitschaft.melde("reconciliation", True, rec_detail)
            stock_bereitschaft.melde(
                "keine_unklare_order", not blocked,
                (f"Ungeklaerte eToro-Ausfuehrung in {context['domain']}: "
                 f"{block_detail}" if blocked else
                 f"Keine ungeklaerte eToro-Ausfuehrung in {context['domain']}"))
            unvollstaendig, pnl_detail = etoro_reconciliation.pnl_unvollstaendig(
                context["domain"])
            stock_bereitschaft.melde(
                "buchung_vollstaendig", not unvollstaendig,
                pnl_detail or "Alle abgeschlossenen Trades sind verbucht")
        else:
            rec_detail = (str(reconciliation_detail or "").strip()
                          or "Aktueller eToro-Depotabgleich fehlgeschlagen")
            stock_bereitschaft.melde("reconciliation", False, rec_detail)
            # Ein nicht belastbarer Snapshot ist selbst dann kein positiver
            # Nachweis, wenn die persistente Domaene gerade keinen Intent kennt.
            stock_bereitschaft.melde(
                "keine_unklare_order", False,
                (f"eToro-Ausfuehrungsstatus nicht belastbar: {rec_detail}"
                 + (f"; {block_detail}" if block_detail else "")))
            unvollstaendig, pnl_detail = etoro_reconciliation.pnl_unvollstaendig(
                context["domain"])
            stock_bereitschaft.melde(
                "buchung_vollstaendig", not unvollstaendig,
                pnl_detail or "Alle abgeschlossenen Trades sind verbucht")
        runtime.update(
            etoro_reconciliation_domain=context["domain"],
            account_fingerprint=str(context.get("account_fingerprint") or ""),
            etoro_reconciliation_blocked=(blocked or not reconciled),
            etoro_reconciliation_detail=(block_detail or rec_detail),
        )
        return {
            **context,
            "blocked": (blocked or not reconciled),
            "detail": (block_detail or rec_detail),
        }

    def _ensure_broker_runtime_services() -> bool:
        """Lifecycle-Dienste idempotent pruefen/starten (Runtime-Watchdog).

        Der Worker beschleunigt den Abgleich, ist aber kein Single Point of
        Failure: Der Hauptzyklus fuehrt zusaetzlich einen synchronen Abgleich
        aus. Ein Workerfehler trennt deshalb nicht die gesamte Brokerverbindung
        und blockiert insbesondere keine Schutz-/SELL-Pfade.
        """
        nonlocal runtime_services_ok, runtime_services_detail
        if str(getattr(broker, "name", "") or "").lower() != "etoro":
            runtime_services_ok = True
            runtime_services_detail = ""
            return True

        ensure = getattr(broker, "ensure_runtime_services", None)
        if not callable(ensure):
            runtime_services_ok = False
            runtime_services_detail = "Broker stellt keinen Runtime-Lifecycle bereit"
            runtime.update(
                etoro_runtime_services_ok=False,
                etoro_runtime_services_detail=runtime_services_detail)
            return False
        try:
            result = ensure()
            status_getter = getattr(broker, "reconciliation_worker_status", None)
            status = dict(status_getter() or {}) if callable(status_getter) else {}
            explicit_failure = result is False
            if isinstance(result, dict):
                explicit_failure = (
                    result.get("ok") is False
                    or result.get("running") is False
                    or result.get("alive") is False)
            if status:
                explicit_failure = explicit_failure or status.get("running") is False
            runtime_services_ok = not explicit_failure
            runtime_services_detail = str(
                status.get("last_error") or
                ("Reconciliation-Worker laeuft" if runtime_services_ok
                 else "Reconciliation-Worker nicht aktiv"))
            # 9.5.2: Der Privatstream wird jetzt getrennt ausgewiesen. Bis
            # 9.5.1 gab es in runtime_status.json ueberhaupt keinen
            # Streamabschnitt fuer eToro -- das Dashboard kannte nur REST und
            # zeigte durchgehend "ONLINE", waehrend sich der WebSocket im
            # Zweiminutentakt selbst trennte. Der Fehler war dadurch nirgends
            # sichtbar, weder in der Oberflaeche noch im Log.
            stream_getter = getattr(broker, "stream_status", None)
            try:
                stream = dict(stream_getter() or {}) if callable(stream_getter) else {}
            except Exception as exc:
                stream = {"running": False, "connected": False,
                          "last_error": f"{type(exc).__name__}: {exc}"[:200]}
            # Add actual request observations without turning an is_connected
            # flag into proof of REST freshness. Getter performs no network IO.
            component_getter = getattr(broker, "connection_components", None)
            try:
                observed_components = dict(component_getter() or {}) if callable(component_getter) else {}
            except Exception:
                observed_components = {"observations": {}, "rest_state": "UNKNOWN"}
            runtime.update(
                etoro_runtime_services_ok=runtime_services_ok,
                etoro_runtime_services_detail=runtime_services_detail,
                etoro_reconciliation_worker=status or None,
                etoro_private_stream=stream or None,
                connection_components={
                    "overall": ("ONLINE" if bool(getattr(broker, "is_connected", lambda: False)())
                                else "OFFLINE"),
                    "rest_state": ("ONLINE" if bool(getattr(broker, "is_connected", lambda: False)())
                                   else "OFFLINE"),
                    "reconciliation_running": bool((status or {}).get("running")),
                    "ws_zustand": str(stream.get("zustand") or "OFFLINE"),
                    "ws_connected": bool(stream.get("connected")),
                    "ws_authenticated": bool(stream.get("authenticated")),
                    "ws_subscribed": bool(stream.get("subscribed")),
                    "ws_last_message": stream.get("last_message"),
                    "ws_reconnects": int(stream.get("reconnects") or 0),
                    "ws_generation": int(stream.get("generation") or 0),
                    "ws_session_id": str(stream.get("session_id") or ""),
                    "ws_last_close_code": stream.get("last_close_code"),
                    "ws_last_close_reason": str(stream.get("last_close_reason") or ""),
                    "ws_next_reconnect_in": float(stream.get("next_reconnect_in") or 0.0),
                    "ws_last_error": str(stream.get("last_error") or ""),
                    **observed_components,
                },
            )
            if not runtime_services_ok:
                logger.warning("eToro-Runtime-Dienste nicht aktiv: %s",
                               runtime_services_detail)
            return runtime_services_ok
        except Exception as exc:
            runtime_services_ok = False
            runtime_services_detail = f"{type(exc).__name__}: {exc}"[:300]
            runtime.update(
                etoro_runtime_services_ok=False,
                etoro_runtime_services_detail=runtime_services_detail)
            logger.warning("eToro-Runtime-Watchdog fehlgeschlagen: %s", exc)
            return False

    def _runtime_connection(state: str, *, connected: bool | None = None,
                            error: str = ""):
        try:
            if connected is None:
                connected = bool(broker.is_connected())
            runtime.update(
                running=True,
                broker=getattr(broker, "name", "?"),
                mode="PAPER" if config.PAPER_TRADING else "LIVE",
                state=zustand.zustand(),
                broker_connected=bool(connected),
                connection_state=state,
                reconnect_attempts=reconnect_attempts,
                reconnect_attempts_current=reconnect_attempts,
                reconnects_total=reconnects_total,
                offline_since=offline_since or None,
                last_connection_error=error or last_connection_error or None,
                last_broker_contact=(broker.last_contact() if hasattr(broker, "last_contact") else None),
            )
        except Exception as exc:
            logger.debug("Runtime-Verbindungsstatus konnte nicht geschrieben werden: %s", exc)
        try:
            health_tracker.note_state(state, error or last_connection_error or "")
        except Exception as exc:
            logger.debug("Heartbeat-Journal konnte nicht aktualisiert werden: %s", exc)

    def on_disconnect(reason: str = ""):
        nonlocal connection_was_lost, last_disconnect_alert, ever_connected
        nonlocal resync_required, last_connection_error, offline_since
        if shutdown_requested:
            return
        if reason:
            last_connection_error = str(reason)
        if not offline_since:
            offline_since = datetime.now().astimezone().isoformat(timespec="seconds")
        if ever_connected:
            connection_was_lost = True
            if initialization_complete:
                resync_required = True
        _runtime_connection("OFFLINE", connected=False, error=last_connection_error)

        # Beim allerersten Start gibt es noch keinen "Abbruch"; dort meldet
        # wait_for_initial_connection() einmalig den Wartemodus. Im laufenden
        # Betrieb wird die Warnung rate-limitiert versendet/queued.
        if not ever_connected:
            return
        now = time.time()
        cooldown = getattr(config, "CONNECTION_ALERT_COOLDOWN_MINUTES", 15) * 60
        if now - last_disconnect_alert >= cooldown:
            last_disconnect_alert = now
            msg = (
                f"{broker.name}-Verbindung abgebrochen.\n"
                "Der Bot pausiert ALLE neuen Broker-Aktionen und versucht automatisch "
                f"alle {getattr(config, 'RECONNECT_INTERVAL_SECONDS', 30)} Sekunden die Wiederverbindung.\n"
                f"Offene Orders bei {broker.name} bleiben beim Broker bestehen.\n"
                "Instrumente erhalten wegen dieses globalen Ausfalls KEINEN 30-Minuten-Cooldown."
            )
            logger.warning("%s | Ursache: %s", msg, last_connection_error or "unbekannt")
            notify(f"{broker.name.upper()} VERBINDUNG ABGEBROCHEN", msg)

    def _drop_transport(reason: Exception | str):
        """Transportzustand explizit verwerfen, ohne Orders zu wiederholen."""
        text = str(reason)
        try:
            broker.disconnect()
        except Exception as exc:
            logger.debug("Broker disconnect nach Transportfehler: %s", exc)
        on_disconnect(text)

    def ensure_connection(force_probe: bool = False):
        nonlocal connection_was_lost, last_reconnect_attempt, ever_connected
        nonlocal reconnect_attempts, reconnects_total, last_connection_error, offline_since, resync_required

        # Beim Initialstart ist ``connect`` ein Lifecycle-Schritt, kein blosser
        # Erreichbarkeitstest. Der eToro-Healthcheck kann durch einen erfolgreichen
        # GET bereits ``connected`` melden, startet aber nicht zwingend Worker und
        # Privatstream. Deshalb muss connect vor dem ersten Healthcheck garantiert
        # einmal laufen.
        try:
            if not ever_connected:
                broker.connect()
            _probe_t0=time.perf_counter()
            healthy = bool(broker.health_check(force=force_probe))
            try:
                health_tracker.note_probe(healthy,(time.perf_counter()-_probe_t0)*1000.0,
                                          "Broker-Healthcheck erfolgreich" if healthy else "Healthcheck=False")
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        except AuthentifizierungsFehler:
            try: health_tracker.note_state("AUTH_ERROR","Authentifizierung fehlgeschlagen")
            except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            raise
        except Exception as exc:
            if _connection_failure(broker, exc):
                _drop_transport(exc)
                healthy = False
            else:
                logger.warning("Broker-Healthcheck fehlgeschlagen: %s", exc)
                healthy = False

        if healthy:
            # Idempotenter Watchdog: ein nach dem Start beendeter Worker wird
            # wiederhergestellt. Sein Ausfall macht den REST-Geldpfad nicht
            # offline; der synchrone Hauptzyklus-Abgleich bleibt autoritativ.
            _ensure_broker_runtime_services()
            ever_connected = True
            last_connection_error = ""
            if not connection_was_lost and not resync_required:
                offline_since = ""
                reconnect_attempts = 0
                _runtime_connection("ONLINE", connected=True)
            return True

        on_disconnect(last_connection_error or "Broker nicht erreichbar")
        now = time.time()
        interval = max(1.0, float(getattr(config, "RECONNECT_INTERVAL_SECONDS", 30)))
        if now - last_reconnect_attempt < interval:
            return False

        last_reconnect_attempt = now
        reconnect_attempts += 1
        _runtime_connection("RECONNECTING", connected=False)
        logger.warning("Broker nicht verbunden -- Wiederverbindungsversuch %d.", reconnect_attempts)
        try:
            broker.connect()
            if bool(broker.health_check(force=True)):
                _ensure_broker_runtime_services()
                was_reconnect = ever_connected or connection_was_lost
                ever_connected = True
                last_connection_error = ""
                if was_reconnect and initialization_complete:
                    reconnects_total += 1
                    reconnect_attempts = 0
                    # Erst nach Depot/Fills/Orders-Synchronisierung wieder handeln.
                    connection_was_lost = True
                    resync_required = True
                    _runtime_connection("RESYNC_PENDING", connected=True)
                else:
                    offline_since = ""
                    reconnect_attempts = 0
                    _runtime_connection("ONLINE", connected=True)
                return True
        except AuthentifizierungsFehler:
            # Falsche/abgelaufene Zugangsdaten werden NICHT endlos probiert.
            _runtime_connection("AUTH_ERROR", connected=False, error="Authentifizierung fehlgeschlagen")
            raise
        except BrokerFehler as exc:
            if isinstance(exc, (VerbindungVerloren, NichtVerbunden)) or is_connectivity_error(exc):
                last_connection_error = str(exc)
                on_disconnect(last_connection_error)
                logger.warning("Wiederverbindung fehlgeschlagen: %s", exc)
                return False
            # Konfigurations-/Berechtigungsfehler sind nicht durch Warten heilbar.
            _runtime_connection("FATAL", connected=False, error=str(exc))
            raise
        except Exception as exc:
            if _connection_failure(broker, exc):
                last_connection_error = str(exc)
                _drop_transport(exc)
                logger.warning("Wiederverbindung fehlgeschlagen: %s", exc)
                return False
            _runtime_connection("FATAL", connected=False, error=str(exc))
            raise
        return False

    def wait_for_initial_connection():
        nonlocal startup_wait_notified, last_connection_error
        if ensure_connection(force_probe=True):
            return
        if not bool(getattr(config, "STARTUP_WAIT_FOR_CONNECTION", True)):
            raise RuntimeError(f"Verbindung zu {broker.name} konnte beim Start nicht hergestellt werden.")

        max_minutes = max(0.0, float(getattr(config, "STARTUP_RECONNECT_MAX_MINUTES", 0)))
        deadline = (time.time() + max_minutes * 60.0) if max_minutes > 0 else None
        if not startup_wait_notified:
            startup_wait_notified = True
            msg = (
                f"Der Bot wurde gestartet, {broker.name} ist aber noch nicht erreichbar.\n"
                "Es werden keine Orders erzeugt. Der Prozess bleibt im sicheren OFFLINE-WARTEMODUS "
                f"und versucht alle {getattr(config, 'RECONNECT_INTERVAL_SECONDS', 30)} Sekunden erneut zu verbinden."
            )
            logger.warning(msg)
            # Ist Telegram offline, bleibt die Meldung in der persistenten Queue
            # und wird nach Rueckkehr des Internets zugestellt.
            notify("BOT WARTET AUF VERBINDUNG", msg)
        _runtime_connection("OFFLINE_WAIT", connected=False, error=last_connection_error)

        while not shutdown_requested:
            if deadline is not None and time.time() >= deadline:
                raise RuntimeError(
                    f"Start-Wartezeit fuer {broker.name} nach {max_minutes:.1f} Minuten abgelaufen."
                )
            broker.warte(min(5.0, max(1.0, float(getattr(config, "RECONNECT_INTERVAL_SECONDS", 30)))))
            if ensure_connection(force_probe=True):
                return

    _BUY_FILTER_ORDER = [
        "market_session", "earnings_window", "event", "broad_liquidity",
        "existing_position", "duplicate_open_order", "open_order_check_error",
        "crypto_disabled", "crypto_portfolio_cap",
        "risk_manager", "marktlage", "underdog_screening", "nachrichten",
        "sector_guard", "correlation_guard", "position_size_zero",
        "no_cash_margin_disabled", "cash_reserve", "market_quality", "cost_quote",
        "net_edge",
    ]

    def _decision_filter_trace(blocked_by: str | None = None) -> dict:
        """Describe which sequential stages were reached without running extra filters.

        A rejected candidate is NOT pushed through downstream network/GPT filters merely
        for analytics; that would add cost and could change side effects. Reaching a
        stage proves earlier stages were passed or not applicable. Later stages are
        explicitly marked NOT_RUN_AFTER_BLOCK.
        """
        if not blocked_by:
            return {
                "evaluated_filters": [{"name": x, "status": "PASS_OR_NOT_APPLICABLE"} for x in _BUY_FILTER_ORDER],
                "not_evaluated_filters": [],
            }
        try:
            idx=_BUY_FILTER_ORDER.index(str(blocked_by))
        except ValueError:
            return {"evaluated_filters": [{"name": str(blocked_by), "status": "BLOCKED"}], "not_evaluated_filters": []}
        evaluated=[{"name": x, "status": "PASS_OR_NOT_APPLICABLE"} for x in _BUY_FILTER_ORDER[:idx]]
        evaluated.append({"name": _BUY_FILTER_ORDER[idx], "status": "BLOCKED"})
        return {"evaluated_filters": evaluated, "not_evaluated_filters": _BUY_FILTER_ORDER[idx+1:]}

    current_decision_id = None

    def journal_ablehnung(inst, grund_code: str, grund_text: str,
                          signal=None, event=None, extra: dict = None) -> None:
        """
        Haelt eine abgelehnte Kaufgelegenheit im Entscheidungsjournal fest.

        WARUM DAS NOETIG IST
        Bis v5.3.1 wurden nur drei Ergebnisse journalisiert (freigegeben,
        von der KI abgelehnt, von der KI gehalten). Die uebrigen 18
        Ablehnungsgruende -- Kosten, Nachrichten, Portfolio, Risiko,
        Ereignisse und so weiter -- landeten nur im Textprotokoll.

        Damit liess sich die eigentlich wichtige Frage nicht beantworten:
        WELCHER Filter lehnt wie viel ab, und waren die abgelehnten
        Gelegenheiten wirklich schlechter? Genau dafuer wird hier
        einheitlich protokolliert (siehe auswertung.py).
        """
        # KORREKTUR 9.5.5: Unveraenderte Dauersperren nicht bei jedem Takt
        # erneut ins Logbuch schreiben. Am 02.09.2026 stand allein AVGO
        # sechsmal mit derselben Zeile darin ("Kurs 21 min alt"); zwischen
        # solchen Wiederholungen geht die eine wichtige Meldung unter.
        #
        # Unterdrueckt wird NICHT gezaehlt-weg: die Wiederholungen werden
        # mitgezaehlt und beim naechsten geschriebenen Eintrag ausgewiesen,
        # damit die Auswertung ("welcher Filter lehnt wie viel ab") ehrlich
        # bleibt.
        _wiederholungen = 0
        # 10.4.0: Zaehlung fuer die Scan-Uebersicht (eine Zeile je Zyklus).
        try:
            if scan_zyklus is not None:
                scan_zyklus.blockiert(getattr(inst, "name", "?"), grund_code, grund_text)
        except Exception:
            logger.debug("Scan-Uebersicht: Ablehnung nicht gezaehlt", exc_info=True)
        try:
            schluessel = (str(getattr(inst, "name", "?")), str(grund_code),
                          str(grund_text)[:300])
            cooldown = max(0.0, float(getattr(
                config, "DECISION_BLOCK_LOG_COOLDOWN_SECONDS", 1800.0)))
            jetzt_s = time.time()
            # "Noch nie gesehen" ausdruecklich als None, nicht als 0.0. Sonst
            # haengt das Verhalten an der Groesse der Uhr -- der erste Eintrag
            # wuerde bei kleinen Zeitwerten faelschlich als Wiederholung gelten.
            letzte, anzahl = _ABLEHNUNG_GESEHEN.get(schluessel, (None, 0))
            if cooldown > 0 and letzte is not None and jetzt_s - letzte < cooldown:
                _ABLEHNUNG_GESEHEN[schluessel] = (letzte, anzahl + 1)
                return
            _wiederholungen = anzahl
            _ABLEHNUNG_GESEHEN[schluessel] = (jetzt_s, 0)
            if len(_ABLEHNUNG_GESEHEN) > 2000:
                # Nicht unbegrenzt wachsen lassen.
                aeltester = min(_ABLEHNUNG_GESEHEN,
                                key=lambda k: _ABLEHNUNG_GESEHEN[k][0] or 0.0)
                _ABLEHNUNG_GESEHEN.pop(aeltester, None)
        except Exception:
            logger.warning("Ablehnungsdrosselung fehlgeschlagen; Eintrag wird "
                           "ungedrosselt geschrieben", exc_info=True)

        try:
            grund_voll = str(grund_text)[:300]
            if _wiederholungen:
                grund_voll = (f"{grund_voll} (zusaetzlich {_wiederholungen}x "
                              "unveraendert seit der letzten Meldung)")[:300]
            daten = {
                "decision_id": current_decision_id or new_decision_id(),
                "status": "BLOCKED",
                "blocked_by": grund_code,
                "reason": grund_voll,
                "wiederholungen": int(_wiederholungen),
                "broker": getattr(broker, "name", "?"),
                "paper": bool(getattr(config, "PAPER_TRADING", True)),
                "symbol": getattr(inst, "name", "?"),
                "asset_type": getattr(inst, "asset_type", ""),
                "sector": getattr(inst, "sector", ""),
                "profile": getattr(config, "ACTIVE_PROFILE", ""),
                **_decision_filter_trace(grund_code),
            }
            if signal is not None:
                daten["price"] = getattr(signal, "price", None)
                daten["signal_reason"] = str(getattr(signal, "reason", ""))[:200]
            if event is not None:
                try:
                    daten["event_score"] = getattr(event, "score", None)
                except Exception:
                    __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            if extra:
                daten.update(extra)
            # Die aktuelle Kontobindung stammt vom Broker, nie aus einer
            # Symbolsuche oder aus spaeter veraenderten Einstellungen.
            daten.update(_order_ownership_binding())
            did = record_decision(**daten)
            speichere_decision_sources({
                "decision_id": did, "symbol": daten["symbol"],
                "asset_type": daten["asset_type"], "broker": daten["broker"],
                "aktion": "KAUF", "ergebnis": "BLOCKED",
                "ergebnis_grund": daten["reason"],
                "quellen": [{"quelle": str(grund_code).upper(),
                             "richtung": "BLOCKIEREND", "detail": daten["reason"]}],
            }, decision_id=did)
        except Exception as exc:
            # 9.5.5: sichtbar statt still -- faellt das Entscheidungsprotokoll
            # aus, merkt es sonst niemand.
            logger.warning("Ablehnung konnte nicht journalisiert werden: %s",
                           exc, exc_info=True)

    def _ai_status_fuer_bericht() -> dict:
        """KI-Aufmerksamkeit fuer den Tagesbericht; niemals als Entscheidung."""
        d = {
            "aktiviert": bool(getattr(config, "AI_ATTENTION_ENABLED", False)),
            "rolle": "scan_priority_only",
            "letzter_fehler": str(getattr(attention, "last_error", "") or ""),
        }
        try:
            d.update(attention.budget_status())
        except Exception as exc:
            logger.debug("Attention-Budgetstatus nicht lesbar: %s", exc)
        return d

    def wait_until_connected(context: str = "Betrieb"):
        """Nach erkanntem Netzausfall ohne Prozessneustart weiter warten."""
        logger.warning("%s wartet auf Broker-Verbindung.", context)
        while not shutdown_requested:
            if ensure_connection(force_probe=True):
                return True
            broker.warte(min(5.0, max(1.0, float(getattr(config, "RECONNECT_INTERVAL_SECONDS", 30)))))
        return False

    try:
        wait_for_initial_connection()
        print(f"{broker.name} verbunden.")

        fav_stocks, fav_crypto = as_universe_rows()
        # Favoriten priorisieren ausschliesslich bereits freigegebene Werte.
        # Neue Aktien duerfen auch als Favorit nicht die Telegram-Freigabe umgehen.
        # Nur Aktien-Favoriten werden als zusaetzliche Kandidaten dem eToro-
        # Pfad hinzugefuegt. Krypto bleibt ausschliesslich bei OKX und kann
        # deshalb nie versehentlich hier qualifiziert oder gehandelt werden.
        stocks_cfg = merge_stock_favorite_rows(config.STOCK_SYMBOLS, fav_stocks)
        # 10.3.0 PULSAR-Universums-Zubringer: Entdeckte Aktien mit belegter
        # Identitaet (max. 10, 14 Tage Verfall) laufen als Gaeste durch die
        # UNVERAENDERTE Qualifikations- und NEXUS-Standard-Pruefung -- gleicher
        # Mechanismus wie Favoriten, kein PULSAR-Sonderweg und keine
        # PULSAR-Freigabe. Ein Fehler hier darf den Start nie verhindern.
        try:
            from pulsar.research import universe_guests
            guest_rows = [{"symbol": r["symbol"], "exchange": "ETORO", "currency": "USD",
                           "sector": "PULSAR-Entdeckung", "gruppe": "pulsar", "broad": True}
                          for r in universe_guests()]
            if guest_rows:
                stocks_cfg = merge_stock_favorite_rows(stocks_cfg, guest_rows)
                logger.info("PULSAR-Universumsgaeste im Kandidatenfeld: %s",
                            ", ".join(r["symbol"] for r in guest_rows))
        except Exception as exc:
            logger.warning("PULSAR-Universumsgaeste nicht ladbar: %s", exc)
        crypto_cfg = list(config.CRYPTO_SYMBOLS)
        # eToro-Krypto ist in NEXUS 8.1.1 deaktiviert; Krypto laeuft nur ueber OKX.
        crypto_cfg = []
        configured_universe = build_universe(stocks_cfg, config.FOREX_PAIRS, crypto_cfg)
        configured_universe_keys={canonical_key(x.name,x.asset_type) for x in configured_universe}
        favorite_keys={f.canonical_key for f in load_favorites()}
        for _i in configured_universe:
            setattr(_i,"favorite",f"{_i.asset_type}:{_i.name}".upper() in favorite_keys)
        print(f"Pruefe {len(configured_universe)} konfigurierte Instrumente ...")
        while True:
            try:
                universe, failed = broker.qualifiziere(configured_universe)
                break
            except Exception as exc:
                if not _connection_failure(broker, exc):
                    raise
                _drop_transport(exc)
                print("Verbindung waehrend Instrument-Pruefung verloren -- sichere Pause, danach automatischer Neustart der Pruefung.")
                if not wait_until_connected("Instrument-Pruefung"):
                    raise KeyboardInterrupt
        if failed:
            print(f"\nUebersprungen ({len(failed)}):")
            for name, grund in failed[:15]:
                print(f"  - {name}: {grund}")
            if len(failed) > 15:
                print(f"  ... und {len(failed) - 15} weitere")
        if not universe:
            raise RuntimeError(
                f"Kein Instrument ist bei {broker.name} handelbar. "
                "Bitte Universum/eToro-Berechtigungen pruefen."
            )

        instrument_by_symbol = {}
        for i in universe:
            key = canonical_key(i.name, i.asset_type)
            if key in instrument_by_symbol:
                raise RuntimeError(f"Doppelte Instrumentidentitaet im eToro-Universum: {key}")
            instrument_by_symbol[key] = i
        stock_bereitschaft.melde("broker_verbunden", bool(broker.is_connected()))
        stock_bereitschaft.melde("instrumente", bool(instrument_by_symbol),
                                  f"{len(instrument_by_symbol)} eindeutig qualifiziert")
        stock_bereitschaft.melde("handelsregeln", bool(instrument_by_symbol),
                                  "eToro Eligibility je Instrument geladen")
        stock_bereitschaft.melde("universum", bool(universe),
                                  f"{len(universe)} Aktien qualifiziert")
        stock_bereitschaft.melde(
            "gebuehren", True,
            "eToro What-if-Kosten werden unmittelbar vor jeder Order zwingend geprueft")
        print(f"\nAktives Universum: {len(universe)}")
        by_type = {}
        for i in universe:
            by_type.setdefault(i.asset_type, []).append(i.name)
        for k, v in sorted(by_type.items()):
            preview=', '.join(v[:40]) + (f" ... (+{len(v)-40})" if len(v)>40 else "")
            print(f"  {k:7s} {len(v):4d}: {preview}")
        try:
            runtime.update(
                configured_stocks=len(getattr(config,"STOCK_SYMBOLS",[]) or []),
                # Dieser Prozess ist die eToro-Aktiendomaene. Das konfigurierte
                # OKX-Kryptouniversum wird separat in runtime_status_okx.json
                # gemeldet und darf hier nicht wie ein eToro-Universum wirken.
                configured_crypto=0,
                active_stocks=len(by_type.get("stock",[])),
                active_crypto=len(by_type.get("crypto",[])),
                active_universe=len(universe),
            )
        except Exception as exc:
            logger.debug("Universumsstatus konnte nicht geschrieben werden: %s",exc)

        def _process_pending_universe_reviews():
            """Fuehrt angeforderte Aufnahmepruefungen nur im Main-Thread aus.

            Telegram selbst beruehrt den Broker nie. Damit gibt es keine parallelen
            API-/Orderadapter-Zugriffe aus dem Long-Polling-Thread.
            """
            limit=max(1,int(getattr(config,"UNIVERSE_REVIEW_MAX_PER_CYCLE",1)))
            processed=0
            for proposal in pending_universe_reviews(limit=limit):
                if not broker.is_connected():
                    break
                try:
                    result=review_proposal(broker,proposal,configured_universe_keys)
                    ok,_detail=set_technical_result(str(proposal.get("id") or ""),result.to_dict())
                    if not ok:
                        logger.warning("Universumspruefung %s konnte nicht persistiert werden",proposal.get("id"))
                        continue
                    fresh=get_universe_proposal(str(proposal.get("id") or "")) or proposal
                    text,keyboard=technical_result_message(fresh)
                    if keyboard:
                        send_telegram_buttons(text,keyboard,priority="normal")
                    else:
                        notify("UNIVERSUM-AUFNAHMEPRUEFUNG",text)
                    processed += 1
                except Exception as exc:
                    # REVIEW_REQUESTED bleibt bestehen und wird im naechsten
                    # Zyklus erneut versucht; ein voruebergehender Broker-/Netzfehler
                    # darf keine falsche technische Ablehnung erzeugen.
                    logger.warning("Universums-Aufnahmepruefung %s voruebergehend fehlgeschlagen: %s",proposal.get("id"),exc)
                    if _connection_failure(broker,exc):
                        break
            return processed

        while True:
            try:
                if fill_tracker.initialized:
                    # Offline-Fills zuerst gegen den VORHERIGEN lokalen
                    # Positionsstand verarbeiten. Ein Neustart darf sie nicht
                    # wie 9.4.1 ungesehen in einen neuen Baseline-Seed nehmen.
                    capture_new_fills(
                        broker, manager, order_meta, fill_tracker,
                        ownership_registry, risk, get_account_equity(broker),
                        instrument_by_symbol, notify_enabled=True,
                        tagesliste=trades_heute)
                current_positions = manager.sync_with_broker(broker, universe)
                _confirm_ledger_from_current_positions(broker)
                if fill_tracker.initialized:
                    # Nach dem Depot-Snapshot werden auch inzwischen
                    # bestaetigte BUY-positionIds aus der Reconciliation
                    # geliefert. Deduplizierung bleibt persistent.
                    capture_new_fills(
                        broker, manager, order_meta, fill_tracker,
                        ownership_registry, risk, get_account_equity(broker),
                        instrument_by_symbol, notify_enabled=True,
                        tagesliste=trades_heute)
                else:
                    # Nur eine echte Erstinstallation setzt eine Baseline.
                    fill_tracker.seed(broker.fills())
                # Ein erfolgreicher Orderabruf gehoert zum Start-Synchronisationscheck.
                if hasattr(broker, "offene_orders"):
                    broker.offene_orders()
                break
            except Exception as exc:
                if not _connection_failure(broker, exc):
                    logger.warning("Start-Synchronisierung fehlgeschlagen: %s", exc)
                    raise
                _drop_transport(exc)
                print("Verbindung waehrend Depot-Synchronisierung verloren -- warte und wiederhole vollstaendig.")
                if not wait_until_connected("Depot-Synchronisierung"):
                    raise KeyboardInterrupt

        print(manager.portfolio_text(current_positions, "DEPOT BEIM START"))
        # Ab hier sind Broker, Universum, Positionen, Fill-Basis und offene
        # Orders einmal vollstaendig synchronisiert. Der Pi-Watchdog darf nun
        # vom grosszuegigen Startfenster auf die normale Laufzeitgrenze wechseln.
        runtime.update(initialization_complete=True, _force_write=True)
        if bool(getattr(config, "NOTIFY_START_PORTFOLIO", True)):
            notify(
                ("Trading-Bot gestartet -- LIVE (ECHTES GELD)"
                 if not config.PAPER_TRADING else "Trading-Bot gestartet -- Paper"),
                manager.portfolio_text(
                    current_positions,
                    f"BOT START | {len(universe)} Instrumente | {'PAPER' if config.PAPER_TRADING else 'LIVE'}",
                ),
            )

        model = load_model(config.MODEL_PATH)
        if model is None and config.USE_ML_FILTER:
            print("WARNUNG: ML aktiviert, aber Modell fehlt. Signale werden ohne ML-Filter verarbeitet.")

        # --- Fernsteuerung per Telegram ---
        # Ein Transportfehler in einem Remote-STATUS darf niemals zu einem
        # erfundenen "0 Positionen / keine Orders" werden. Er aktualisiert
        # denselben globalen Offline-Zustand wie der Hauptloop.
        def _remote_broker_call(func):
            try:
                return func()
            except Exception as exc:
                if _connection_failure(broker, exc):
                    _drop_transport(exc)
                raise

        def _positionen_liste():
            return _remote_broker_call(lambda: broker.positionen())

        def _orders_liste():
            if not hasattr(broker, "offene_orders"):
                return []
            return _remote_broker_call(lambda: broker.offene_orders())

        def _kontowert_remote():
            return _remote_broker_call(lambda: get_account_equity(broker))

        try:
            remote_currency = broker.kontowaehrung() if hasattr(broker, "kontowaehrung") else "EUR"
        except Exception as exc:
            if _connection_failure(broker, exc):
                _drop_transport(exc)
            remote_currency = str(getattr(broker, "kontowaehrung", lambda: "USD")() or "USD")

        def _position_mode(symbol: str) -> str:
            try:
                rec = manager.get_by_symbol(symbol)
                return str(getattr(rec, "management_mode", "") or "") if rec else ""
            except Exception:
                return ""

        def _request_scan() -> str:
            if manual_scan_event.is_set():
                return "Ein zusätzlicher Scan ist bereits angefordert. Kaufregeln werden nicht umgangen."
            manual_scan_event.set()
            return "Zusätzlicher Scan angefordert. Er läuft mit exakt denselben Kauf-, Risiko- und Sicherheitsregeln wie ein normaler Scan."

        def _request_shutdown() -> str:
            nonlocal shutdown_requested
            try:
                zustand.setze(PAUSIERT, "vor kontrolliertem Telegram-Shutdown", "telegram")
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            shutdown_requested = True
            manual_scan_event.set()
            return "TradingBot wird kontrolliert beendet. Neue Käufe sind bereits gesperrt; der Raspberry Pi bleibt eingeschaltet."

        def _send_db() -> str:
            try:
                path = snapshot_database()
                ok = send_document(path, caption="Konsistenter Snapshot der TradingBot-Entscheidungsdatenbank")
                return "Decision-Datenbank wurde gesendet." if ok else f"Snapshot wurde erstellt, Telegram-Versand ist aber fehlgeschlagen: {path.name}"
            except Exception as exc:
                return f"Datenbankexport fehlgeschlagen: {exc}"

        def _ai_status_text() -> str:
            try:
                b = attention.budget_status()
            except Exception:
                b = {}
            mode = ai_control_mode()
            calls = b.get("calls", "?"); max_day = b.get("max_calls_per_day", "?")
            lines = [f"🤖 KI-ROLLEN · {mode}", f"Heute: {calls}/{max_day} Attention-Priorisierungen"]
            lines.append("Live-Rolle: nur Scan-Reihenfolge; niemals Kauf-/Verkaufsentscheidung.")
            lines.append(f"Weekly Research: {'aktiv' if getattr(config,'AI_RESEARCH_ENABLED',False) else 'aus'} · nur Vorschläge, zweistufige Mensch-Freigabe.")
            lines.append(f"Strategy Analyst: {'aktiv' if getattr(config,'STRATEGY_ANALYST_ENABLED',True) else 'aus'} · eigene Statistik, kein Web, keine Regeländerung.")
            if mode == "OFF":
                lines.append("Alle OpenAI-Aufrufe sind gesperrt. Deterministische Rotation und Statistik laufen weiter.")
            elif mode == "ON":
                lines.append("OpenAI-Rollen sind manuell freigegeben; Tradingfilter und Human-Gates bleiben unveraendert.")
            else:
                lines.append("Normaler AUTO-Modus fuer die getrennten KI-Rollen ist aktiv.")
            if getattr(attention, "last_error", ""):
                lines.append(f"Letzter Fehler: {str(attention.last_error)[:240]}")
            return "\n".join(lines)

        def _ai_set_mode(mode: str, source: str) -> str:
            mode = set_ai_control_mode(mode, source)
            labels = {
                "OFF": "Alle OpenAI-Aufrufe wurden deaktiviert. Deterministische Scan-Rotation und Strategy-Statistik laufen weiter.",
                "AUTO": "KI-Rollen laufen wieder im normalen Modus: Attention, optionales Weekly Research und Strategy-Auslegung. Keine Rolle hat Orderrechte.",
                "ON": "KI-Rollen sind manuell freigegeben. Human-Gates und deterministische Handelsfilter bleiben unverändert.",
            }
            return labels[mode]

        def _market_status_text() -> str:
            try:
                sample = next((x for x in universe if getattr(x,"asset_type","") == "stock" and
                               str(getattr(x,"currency","USD")).upper() == "USD"), None)
                if sample is None:
                    return "🕒 Markt: Aktienstatus nicht verfuegbar · Krypto 24/7"
                st = market_session_status(
                    broker, sample,
                    quote_max_age_seconds=_kursalter_grenze(),
                )
                src = "eToro/API" if str(getattr(broker,"name","")).lower() == "etoro" else st.source
                return f"🕒 Markt: US-Aktien {st.short()} · Quelle {src} · Krypto 24/7"
            except Exception as exc:
                return f"🕒 Markt: Status nicht lesbar ({type(exc).__name__}) · Krypto 24/7"

        def _health_text() -> str:
            lines = ["🩺 TRADINGBOT HEALTH"]
            try:
                b_ok = bool(broker.is_connected())
                lines.append(f"{'🟢' if b_ok else '🔴'} Broker: {'verbunden' if b_ok else 'nicht verbunden'}")
            except Exception as exc:
                lines.append(f"🔴 Broker: {type(exc).__name__}")
            try:
                with socket.create_connection(("1.1.1.1", 443), timeout=3):
                    lines.append("🟢 Internet: erreichbar")
            except Exception:
                lines.append("🔴 Internet: keine TCP-Verbindung")
            try:
                nh = news_source_health() or {}
                bad=[]; good=0; disabled=0
                for name,row in nh.items():
                    st=str((row or {}).get("state") or "unknown").lower()
                    if st in {"healthy","ok"}: good += 1
                    elif st in {"disabled","unknown"}: disabled += 1
                    else: bad.append(name)
                lines.append(("🟢" if good > 0 and not bad else "🟡") + f" Newsquellen: {good} OK, {disabled} aus/unbekannt" + (f", gestört: {', '.join(bad[:3])}" if bad else ""))
            except Exception as exc:
                lines.append(f"🟡 Newsquellen: Status nicht lesbar ({type(exc).__name__})")
            mode=ai_control_mode()
            ai_configured=bool(getattr(config,"AI_ATTENTION_ENABLED",False) and getattr(config,"OPENAI_API_KEY", ""))
            ai_icon="⚪" if not ai_configured or mode=="OFF" else ("🟡" if getattr(attention,"last_error","") else "🟢")
            lines.append(f"{ai_icon} KI-Aufmerksamkeit: {'nicht konfiguriert' if not ai_configured else mode}" + (f" · {str(attention.last_error)[:100]}" if getattr(attention,"last_error","") else ""))
            try:
                tg=telegram_status()
                tg_ok=bool(tg.get("configured") and tg.get("api_ok") and not tg.get("delivery_stalled"))
                lines.append(f"{'🟢' if tg_ok else '🟡'} Telegram: {tg.get('detail') or 'Status unbekannt'} · Queue {tg.get('queued',0)}")
            except Exception as exc:
                lines.append(f"🟡 Telegram: {type(exc).__name__}")
            try:
                lines.append(_market_status_text())
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            try:
                from berichte import pi_status_lines
                lines.extend(pi_status_lines())
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            return "\n".join(lines)

        def _instant_report() -> str:
            try:
                eq = _kontowert_remote()
                pos = _positionen_liste()
                return build_daily_report(
                    broker=broker, risk=risk, equity=eq, positions=pos,
                    cycles=zyklen_gesamt[0], ai=attention, runtime=dict(runtime.data),
                    news_health=news_source_health(), trades=list(trades_heute),
                )
            except Exception as exc:
                return f"Ausführlicher Bericht konnte nicht erzeugt werden: {exc}"

        befehle = Befehlsverarbeitung(
            zustand,
            hole_broker=lambda: broker,
            hole_positionen=_positionen_liste,
            hole_risiko=lambda: risk,
            hole_kontowert=_kontowert_remote,
            hole_trades_heute=lambda: list(trades_heute),
            hole_orders=_orders_liste,
            hole_zyklen=lambda: zyklen_gesamt[0],
            waehrung=remote_currency,
            hole_position_mode=_position_mode,
            hole_bericht=_instant_report,
            health_text=_health_text,
            request_scan=_request_scan,
            request_shutdown=_request_shutdown,
            send_database=_send_db,
            ai_status=_ai_status_text,
            ai_set_mode=_ai_set_mode,
            market_status=_market_status_text,
        )

        telegram_steuerung = TelegramSteuerung(
            befehle,
            melden=lambda text: notify("TELEGRAM-STEUERUNG", text),
            callback_handler=handle_universe_callback,
        )
        if telegram_steuerung.start():
            print("Telegram-Fernsteuerung aktiv: /status /pnl /positions /decisions /health /pause /resume /report /scan /database /ai /vorschlaege /universum /shutdownbot")

        auto_maintenance = AutoMaintenance(notify=lambda subject,text: notify(subject,text), logger=logger.warning)
        auto_maintenance.start()
        from pulsar.worker import Worker as PulsarWorker
        pulsar_worker = PulsarWorker()
        pulsar_worker.start()
        # One shared, optional research collector, independent of PULSAR's
        # enable switch. Failure cannot block either broker worker.
        try:
            import market_intelligence
            market_intelligence.start_worker()
        except Exception:
            logger.exception("Optionale X-Recherche konnte nicht starten")
        print(f"Betriebszustand: {zustand.zustand().upper()}")

        # Vor dem ersten neuen Scanner-Kauf werden alle persistenten eToro-
        # Submit-Faelle anhand ihrer exakten IDs abgeglichen. Ein Fehler laesst
        # die Domaenensperre stehen; Schutz- und Verkaufspfade bleiben aktiv.
        if str(getattr(broker, "name", "")).lower() == "etoro":
            try:
                import etoro_reconciliation
                _start_context = _etoro_account_context()
                etoro_reconciliation.recover_all(
                    broker, paper=_start_context["paper"],
                    profile=_start_context["profile"])
                _set_etoro_reconciliation_readiness(
                    reconciled=True,
                    reconciliation_detail="Depot, Orders und Historie abgeglichen",
                    context=_start_context)
            except Exception as exc:
                logger.warning("eToro-Startabgleich bleibt offen: %s", exc)
                try:
                    _set_etoro_reconciliation_readiness(
                        reconciled=False,
                        reconciliation_detail=f"Startabgleich fehlgeschlagen: {exc}"[:200])
                except Exception as status_exc:
                    detail = (f"eToro-Startabgleich und Statuslesung fehlgeschlagen: "
                              f"{status_exc}")[:200]
                    stock_bereitschaft.melde("reconciliation", False, detail)
                    stock_bereitschaft.melde("keine_unklare_order", False, detail)

        # Ab hier ist die normale Initialisierung komplett. Erst jetzt wird ein
        # spaeterer Verbindungsabbruch als echter Reconnect mit Pflicht-Resync
        # behandelt.
        initialization_complete = True
        connection_was_lost = False
        resync_required = False
        offline_since = ""
        _runtime_connection("ONLINE", connected=True)

        def resync_after_reconnect():
            """Depot, Fills, offene Orders und Schutz nach einem Ausfall abgleichen.

            Solange dieser Schritt nicht komplett erfolgreich war, darf kein neuer
            Scanner-/Orderpfad weiterlaufen. Eine Order, deren Submit-Antwort beim
            Netzausfall verloren ging, wird NICHT erneut gesendet; stattdessen
            werden Broker-Fills/Orders als Wahrheit eingelesen.
            """
            nonlocal current_positions, resync_required, connection_was_lost
            nonlocal offline_since, last_connection_error, last_disconnect_alert
            _runtime_connection("RESYNC", connected=True)
            attempts = max(1, int(getattr(config, "RECONNECT_RESYNC_RETRIES", 3)))
            delay = max(0.0, float(getattr(config, "RECONNECT_RESYNC_DELAY_SECONDS", 2)))
            last_exc = None
            for attempt in range(1, attempts + 1):
                try:
                    if str(getattr(broker, "name", "")).lower() == "etoro":
                        import etoro_reconciliation
                        etoro_reconciliation.recover_all(
                            broker, paper=bool(getattr(broker, "paper", True)),
                            profile=str(getattr(config, "ACTIVE_PROFILE", "") or ""))
                    equity_now = get_account_equity(broker)
                    update_account_equity_guard(risk, broker, equity_now)
                    _risk_review = risk.basis_review()
                    runtime.update(risk_review=_risk_review)
                    stock_bereitschaft.melde("Risikobasis und Kontozuordnung",
                        not _risk_review["blocks_entries"], _risk_review["detail"])
                    # Fills zuerst: Ausfuehrungen waehrend der Offline-Zeit werden
                    # gegen den VORHERIGEN Positions-State verbucht (wichtig fuer P&L).
                    processed = capture_new_fills(
                        broker, manager, order_meta, fill_tracker, ownership_registry, risk, equity_now,
                        instrument_by_symbol, notify_enabled=True, tagesliste=trades_heute,
                    )
                    current_positions = manager.sync_with_broker(broker, universe)
                    _confirm_ledger_from_current_positions(broker)
                    orders = broker.offene_orders() if hasattr(broker, "offene_orders") else []

                    # Ein Fill kann bereits erfolgt sein, bevor der lokale Prozess
                    # den brokerseitigen Schutzstatus abschliessend gelesen hat.
                    protection_fixed = 0
                    protection_warnings = []
                    for _key, (contract, qty, _avg, inst) in list(current_positions.items()):
                        if qty == 0 or inst is None:
                            continue
                        rec = manager.get(contract) or manager.get_by_symbol(getattr(inst, "name", ""))
                        if rec is None or getattr(rec, "source", "") != "BOT":
                            continue
                        if float(getattr(rec, "planned_stop", 0) or 0) <= 0:
                            continue
                        result = broker.reconcile_position_protection(
                            inst, qty, rec.planned_stop, rec.planned_take,
                            position_ids=sorted(rec.owned_position_id_set()),
                            instrument_id=rec.broker_instrument_id,
                            strict_contract=bool(getattr(rec, "protection_plan_history", [])),
                            force_snapshot=False)
                        manager.set_bot_protection(
                            str(_key),
                            protection_result_confirmed(result),
                            str(result.get("detail") or ""))
                        if result.get("changed"):
                            protection_fixed += 1
                        detail = str(result.get("detail", "") or "")
                        if result.get("checked") and result.get("protection_confirmed") is not True:
                            protection_warnings.append(f"{inst.name}: {detail}")

                    try:
                        orphaned = broker.verwaiste_orders_aufraeumen()
                    except NichtUnterstuetzt:
                        orphaned = 0

                    risk.set_open_positions(sum(
                        1 for _, (_, qty, _, _) in current_positions.items() if qty != 0
                    ))
                    resync_required = False
                    connection_was_lost = False
                    last_connection_error = ""
                    offline_since = ""
                    # Ein erfolgreich abgeschlossener Reconnect beendet den alten
                    # Ausfall. Ein spaeterer neuer Ausfall darf deshalb wieder
                    # sofort genau einmal gemeldet werden.
                    last_disconnect_alert = 0.0
                    _runtime_connection("ONLINE", connected=True)
                    msg = (
                        f"Die {broker.name}-Verbindung ist wieder stabil.\n"
                        f"Depot synchronisiert: {len(current_positions)} offene Position(en).\n"
                        f"Neue Fills seit dem letzten Stand verarbeitet: {processed}.\n"
                        f"Offene Orders geprueft: {len(orders)}.\n"
                        f"Schutzorders nachgebessert: {protection_fixed}.\n"
                        f"Verwaiste Orders entfernt: {orphaned}.\n"
                        "Erst nach diesem Abgleich wird der normale Handel fortgesetzt."
                    )
                    if protection_warnings:
                        msg += "\nACHTUNG Schutzpruefung: " + " | ".join(protection_warnings[:4])
                    logger.info(msg)
                    notify(f"{broker.name.upper()} VERBINDUNG WIEDERHERGESTELLT", msg)
                    return equity_now
                except Exception as exc:
                    last_exc = exc
                    if _connection_failure(broker, exc):
                        _drop_transport(exc)
                        return None
                    logger.warning("Reconnect-Synchronisierung fehlgeschlagen (%d/%d): %s", attempt, attempts, exc)
                    if attempt < attempts and delay:
                        broker.warte(delay * attempt)

            # Kein Handel mit nur teilweise synchronisiertem Zustand.
            resync_required = True
            last_connection_error = f"Synchronisierung fehlgeschlagen: {last_exc}"
            _runtime_connection("RESYNC_FAILED", connected=bool(broker.is_connected()), error=last_connection_error)
            logger.error("Reconnect-Synchronisierung dauerhaft fehlgeschlagen: %s", last_exc)
            return None

        # v5.8: pacing-sicherer, asset-klassenbewusster Rotationsscanner.
        # Favoriten/News bleiben priorisiert, reguläre Aktien und Kryptos
        # werden fair über die Zyklen verteilt.
        scan_scheduler = RotatingScanScheduler()
        last_data_error = {}
        cycle = 0
        while True:
            cycle += 1
            zyklen_gesamt[0] = cycle
            manual_scan_event.clear()

            # --- Boersenkalender ---------------------------------------
            # Meldet Oeffnung/Schluss genau einmal und beantwortet, ob sich
            # ein vollstaendiger Aktienzyklus ueberhaupt lohnt. An Feiertagen
            # und ausserhalb der Handelszeit spart das Kursabrufe,
            # Nachrichtenabfragen und kostenpflichtige KI-Aufrufe.
            aktien_zyklus_sinnvoll = True
            kalender_grund = ""
            if bool(getattr(config, "MARKET_CALENDAR_ENABLED", True)):
                try:
                    _melde_marktsitzung()
                except Exception as exc:
                    logger.debug("Marktsitzungsmeldung fehlgeschlagen: %s", exc)
                try:
                    aktien_zyklus_sinnvoll, kalender_grund = _kalender_darf_arbeiten(
                        puffer_minuten=int(getattr(config, "MARKET_PREOPEN_BUFFER_MINUTES", 20)))
                except Exception as exc:
                    logger.debug("Kalenderpruefung fehlgeschlagen: %s", exc)
                    aktien_zyklus_sinnvoll = True
            from stock_readiness import market as report_stock_market
            try:
                report_stock_market(stock_bereitschaft, _sitzungsstatus(),
                    regular_only=bool(getattr(config, "STOCK_NEW_BUYS_REGULAR_HOURS_ONLY", True)))
            except Exception as exc:
                stock_bereitschaft.melde("marktsitzung", False,
                    "Marktkalender nicht pruefbar: " + type(exc).__name__)
            runtime.update(last_scan_at=datetime.now().astimezone().isoformat(), cycle=cycle)
            report_risk_buy_gate(runtime, risk)
            try:
                import market_intelligence
                runtime.update(market_intelligence_hints=market_intelligence.event_hints())
            except Exception:
                logger.debug("Optionale X-Hinweise nicht lesbar", exc_info=True)
            try:
                approved_before_cycle = int(decision_summary().get("approved", 0) or 0)
            except Exception:
                approved_before_cycle = 0
            if not ensure_connection(force_probe=True):
                print(f"{broker.name} nicht verbunden -- sicherer OFFLINE-WARTEMODUS.")
                broker.warte(min(5.0, max(1.0, float(getattr(config, "RECONNECT_INTERVAL_SECONDS", 30)))))
                continue

            if resync_required:
                equity = resync_after_reconnect()
                if equity is None:
                    broker.warte(min(5.0, max(1.0, float(getattr(config, "RECONNECT_INTERVAL_SECONDS", 30)))))
                    continue
            else:
                try:
                    equity = get_account_equity(broker)
                except Exception as exc:
                    if _connection_failure(broker, exc):
                        _drop_transport(exc)
                        continue
                    raise

            if equity <= 0:
                print("Kontostand noch nicht verfuegbar. Warte 20 s ...")
                broker.warte(20)
                continue

            update_account_equity_guard(risk, broker, equity)
            _risk_review = risk.basis_review()
            runtime.update(risk_review=_risk_review)
            stock_bereitschaft.melde("Risikobasis und Kontozuordnung",
                not _risk_review["blocks_entries"], _risk_review["detail"])
            stock_bereitschaft.melde("broker_verbunden", bool(broker.is_connected()))
            stock_bereitschaft.melde("guthaben", equity > 0,
                                      f"Kontowert {equity:.2f} erfolgreich gelesen")
            stock_bereitschaft.melde(
                "signalkerze",
                stock_bereitschaft.kerzen_vollstaendig(15 * 60),
                "mindestens 15 Minuten seit Prozessstart")

            # Der Hintergrundworker ist nur der schnelle Pfad. Der Hauptthread
            # nimmt selbst genau einen autoritativen Depot-Snapshot auf. Zuerst
            # werden damit zusammenhaengende SELL-Fills verbucht; erst danach
            # darf derselbe Snapshot einen Entry-Intent als geschlossen markieren.
            # So kann die Reconciliation dem kritischen Ledgerpfad nicht mit
            # einem ergebnislosen Historien-Close zuvorkommen.
            _cycle_context = None
            _position_snapshot = None
            _reconciliation_error = ""
            if str(getattr(broker, "name", "") or "").lower() == "etoro":
                try:
                    _cycle_context = _etoro_account_context()
                    _snapshot_getter = getattr(broker, "position_snapshot", None)
                    if callable(_snapshot_getter):
                        _position_snapshot = _snapshot_getter(force=True)
                    else:
                        # Rueckwaertskompatibler, weiterhin einzelner
                        # Depotbeweis fuer Adapter-Doubles/Altadapter.
                        _open_ids = broker.current_position_ids(force=True)
                        _position_snapshot = {
                            "open_ids": sorted(str(x) for x in _open_ids),
                            "rows": [],
                            "snapshot_id": (
                                str(broker.portfolio_snapshot_id())
                                if callable(getattr(
                                    broker, "portfolio_snapshot_id", None)) else ""),
                            "complete": True,
                        }
                    if not isinstance(_position_snapshot, dict):
                        raise RuntimeError(
                            "eToro-position_snapshot lieferte kein Objekt")
                    if _position_snapshot.get("complete") is False:
                        raise RuntimeError(
                            "eToro-Depot-Snapshot ist als unvollstaendig markiert")
                except Exception as exc:
                    _reconciliation_error = (
                        f"Synchroner eToro-Depot-Snapshot fehlgeschlagen: {exc}")[:200]
                    logger.warning("%s", _reconciliation_error)

            # Betriebszustand pruefen -- kann sich per Telegram jederzeit
            # aendern, deshalb bei JEDEM Zyklus frisch lesen.
            aktueller_zustand = zustand.zustand()
            runtime.update(running=True, broker=getattr(broker,"name","?"), mode="PAPER" if config.PAPER_TRADING else "LIVE", state=aktueller_zustand, broker_connected=bool(broker.is_connected()), connection_state="ONLINE", last_broker_contact=(broker.last_contact() if hasattr(broker,"last_contact") else None), cycle=cycle)
            darf_kaufen = aktueller_zustand == AKTIV
            darf_verkaufen = aktueller_zustand in (AKTIV, PAUSIERT)

            # Raspberry-Pi-Hardwarewache: unsicherer Host darf keine neue
            # Risikoposition eroeffnen; SELL-/Schutzpfad bleibt aktiv.
            pi_hardware_buy_allowed = _pi_hardware_buy_ok(force=True)
            darf_kaufen = darf_kaufen and pi_hardware_buy_allowed

            if aktueller_zustand != AKTIV:
                print(f"[{aktueller_zustand.upper()}] "
                      + ("Keine neuen Kaeufe -- Positionen werden weiter ueberwacht."
                         if aktueller_zustand == PAUSIERT
                         else "Handel vollstaendig angehalten (auch keine Verkaeufe)."))

            # Zuerst neue Fills verarbeiten. Damit werden TP/SL und manuelle
            # Positionveraenderungen auch dann gemeldet, wenn kein Signal-Scan lief.
            try:
                capture_new_fills(
                    broker, manager, order_meta, fill_tracker, ownership_registry, risk, equity,
                    instrument_by_symbol, notify_enabled=True, tagesliste=trades_heute,
                    position_snapshot=_position_snapshot,
                )

                # Broker-Positionen sind die Wahrheit. Ein Netzfehler an dieser
                # Stelle pausiert den gesamten Zyklus statt ein einzelnes Symbol.
                current_positions = manager.sync_with_broker(broker, universe)
                _confirm_ledger_from_current_positions(broker)
                risk.set_open_positions(sum(
                    1 for _, (_, qty, _, _) in current_positions.items() if qty != 0))
                reviewed=_process_pending_universe_reviews()
                if reviewed:
                    logger.info("%d angeforderte Universums-Aufnahmepruefung(en) abgeschlossen",reviewed)
                # v8.1.5: Uebernahme-Auftraege aus der WebUI. Sie werden hier
                # ausgefuehrt, weil nur der Kern den Broker und den aktuellen
                # Kurs hat -- und weil zwei Prozesse niemals gleichzeitig in
                # position_state.json schreiben duerfen.
                try:
                    import positions_auftraege
                    kurse = {}
                    for _pos in broker.positionen():
                        _kurs = float(getattr(_pos, "market_price", 0.0) or 0.0)
                        if _kurs > 0:
                            kurse[str(_pos.symbol).upper()] = _kurs
                            for _pid in (getattr(_pos, "position_ids", ()) or []):
                                kurse[f"pid:{_pid}"] = _kurs
                    erledigt = positions_auftraege.verarbeite(
                        manager, kurse, melder=lambda betreff, text: notify(betreff, text))
                    if erledigt:
                        logger.info("%d Positionsauftrag/-auftraege ausgefuehrt", erledigt)
                except Exception:
                    logger.exception("Positionsauftraege nicht verarbeitbar")
            except Exception as exc:
                if _connection_failure(broker, exc):
                    _drop_transport(exc)
                    print("Brokerkontakt beim Fill-/Depotabgleich verloren -- Zyklus sicher pausiert.")
                    continue
                raise

            # Erst nach erfolgreicher Fill-/Ledger-Verarbeitung wird der zuvor
            # gelesene, unveraenderte Snapshot in die Entry-Reconciliation
            # uebernommen. Diese Aktualisierung liegt weiterhin vor jeder
            # ``darf_kaufen``-Abfrage im Scanner.
            if str(getattr(broker, "name", "") or "").lower() == "etoro":
                try:
                    if _reconciliation_error:
                        raise RuntimeError(_reconciliation_error)
                    if _cycle_context is None or _position_snapshot is None:
                        raise RuntimeError("eToro-Zyklussnapshot fehlt")
                    import etoro_reconciliation
                    etoro_reconciliation.verify_broker_truth(
                        broker,
                        paper=_cycle_context["paper"],
                        profile=_cycle_context["profile"],
                        account_fingerprint=_cycle_context["account_fingerprint"],
                        position_snapshot=_position_snapshot,
                    )
                    # Known legacy cross-broker metadata may start a NEW period
                    # only after this full cycle's scoped fill/ledger readback.
                    # No old daily basis or financial history is rewritten.
                    try:
                        from etoro_risk_period import attempt as start_forward_risk_period
                        if start_forward_risk_period(risk, broker):
                            equity = float(risk.last_equity)
                            _risk_review = risk.basis_review()
                            runtime.update(risk_review=_risk_review)
                            stock_bereitschaft.melde("Risikobasis und Kontozuordnung",
                                not _risk_review["blocks_entries"], _risk_review["detail"])
                            logger.info("eToro: neue kontogebundene Risikoperiode; Altbelege bleiben unzugeordnet archiviert")
                    except Exception as risk_exc:
                        logger.warning("eToro-Risikoperiode bleibt offen: %s", type(risk_exc).__name__)
                    _snapshot_id = str(
                        _position_snapshot.get("snapshot_id")
                        or _position_snapshot.get("_snapshot_id") or "")
                    _set_etoro_reconciliation_readiness(
                        reconciled=True,
                        reconciliation_detail=(
                            "Aktueller eToro-Depot-Snapshot abgeglichen"
                            + (f" ({_snapshot_id})" if _snapshot_id else "")),
                        context=_cycle_context)
                except Exception as exc:
                    detail = f"Synchroner eToro-Depotabgleich fehlgeschlagen: {exc}"[:200]
                    logger.warning("%s", detail)
                    try:
                        _set_etoro_reconciliation_readiness(
                            reconciled=False,
                            reconciliation_detail=detail,
                            context=_cycle_context)
                    except Exception as status_exc:
                        detail = (f"eToro-Depotabgleich und Statuslesung fehlgeschlagen: "
                                  f"{status_exc}")[:200]
                        stock_bereitschaft.melde("reconciliation", False, detail)
                        stock_bereitschaft.melde(
                            "keine_unklare_order", False, detail)
            else:
                _set_etoro_reconciliation_readiness(
                    reconciled=True,
                    reconciliation_detail="Brokerdepot im Hauptzyklus abgeglichen")
            runtime.update(stock_trading_ready=stock_bereitschaft.status())

            try:
                pulsar_core.reconcile()
            except Exception as exc:
                logger.warning("PULSAR-Ausfuehrungsabgleich bleibt offen: %s", exc)

            # Offene Positionen zuerst aktiv managen.
            last_price_by_conid = {}
            # Bereits geladene Historien offener Positionen werden fuer den
            # Korrelationsschutz wiederverwendet. Keine zusaetzlichen API-Requests.
            position_histories = {}
            cycle_connection_failure = False
            runtime.update(protection_evidence={}, etoro_exit_costs={"positions": [], "updated_at": datetime.now().astimezone().isoformat()})
            for con_id, (contract, qty, avg_cost, inst) in list(current_positions.items()):
                runtime.update()  # Main-Thread-Fortschritt fuer Pi-systemd-Watchdog
                # Fernzustand vor JEDEM Positionsentscheid frisch lesen.
                # PAUSIERT erlaubt Schutzverkäufe, GESTOPPT nicht.
                darf_verkaufen = zustand.darf_verkaufen()
                if qty == 0 or inst is None:
                    continue
                rec_owner = manager.get(contract) or manager.get_by_symbol(getattr(inst,"name",""))
                mode_owner = str(getattr(rec_owner,"management_mode","OBSERVE") or "OBSERVE").upper() if rec_owner else "OBSERVE"
                if (rec_owner is not None
                        and str(getattr(rec_owner, "source", "")).upper() == "BOT"
                        and rec_owner.ownership_chain_complete
                        and rec_owner.broker_account_fingerprint == broker.account_fingerprint()
                        and rec_owner.broker_environment == ("DEMO" if broker.paper else "LIVE")
                        and not rec_owner.user_observe_locked
                        and mode_owner in {"AUTO", "PENDING_CONFIRMATION"}):
                    try:
                        protection = broker.reconcile_position_protection(
                            inst, qty,
                            float(getattr(rec_owner, "planned_stop", 0) or 0),
                            float(getattr(rec_owner, "planned_take", 0) or 0),
                            position_ids=sorted(rec_owner.owned_position_id_set()),
                            instrument_id=rec_owner.broker_instrument_id,
                            strict_contract=bool(getattr(rec_owner, "protection_plan_history", [])),
                            force_snapshot=False)
                        _proofs = dict(runtime.data.get("protection_evidence", {}) or {})
                        _proofs[str(con_id)] = protection.get("protection_evidence", {})
                        runtime.update(protection_evidence=_proofs)
                        protected = protection_result_confirmed(protection)
                        manager.set_bot_protection(
                            str(con_id), protected,
                            str(protection.get("detail") or ""))
                        mode_owner = "AUTO" if protected else "PENDING_CONFIRMATION"
                    except Exception as exc:
                        if _connection_failure(broker, exc):
                            raise
                        manager.set_bot_protection(
                            str(con_id), False,
                            f"Schutzstatus nicht bestaetigt: {exc}")
                        mode_owner = "PENDING_CONFIRMATION"
                if mode_owner == "PENDING_TAKEOVER":
                    try:
                        result = broker.reconcile_position_protection(
                            inst, qty, float(getattr(rec_owner,"planned_stop",0) or 0),
                            float(getattr(rec_owner,"planned_take",0) or 0),
                            position_ids=sorted(rec_owner.position_id_set()),
                            update_requested=True,
                            instrument_id=rec_owner.broker_instrument_id)
                        detail=str((result or {}).get("detail","") or "Schutzpruefung ohne Detail")
                        stop_ok=(result or {}).get("stop_ok")
                        checked=bool((result or {}).get("checked",False))
                        # Sicherheitsentscheidung ausschliesslich ueber ein explizites
                        # Adapterfeld; menschlicher Detailtext ist nie Logik.
                        protected = protection_result_confirmed(result)
                        if protected:
                            manager.confirm_takeover(con_id, detail)
                            mode_owner="AUTO"
                            notify("BOT-VERWALTUNG UEBERNOMMEN", f"{inst.name}: {detail}")
                        elif checked:
                            manager.reject_takeover(con_id, detail)
                            mode_owner="OBSERVE"
                            notify("UEBERNAHME NICHT AKTIVIERT", f"{inst.name}: {detail}. Position bleibt nur beobachtet.")
                        else:
                            manager.reject_takeover(con_id, detail or "Broker kann Schutz fuer bestehende Position nicht sicher bestaetigen")
                            mode_owner="OBSERVE"
                            notify("UEBERNAHME NICHT AKTIVIERT", f"{inst.name}: Broker kann den benoetigten Schutz fuer diese bestehende Position nicht sicher bestaetigen. Position bleibt nur beobachtet.")
                    except Exception as exc:
                        if _connection_failure(broker, exc):
                            raise
                        manager.reject_takeover(con_id, f"Schutzpruefung fehlgeschlagen: {exc}")
                        mode_owner="OBSERVE"
                        logger.warning("Uebernahmepruefung %s fehlgeschlagen: %s",inst.name,exc)
                pulsar_position = __import__("pulsar.positions", fromlist=["owned_proposal"])
                held_pulsar = pulsar_position.owned_proposal(rec_owner)
                # Position monitoring runs before any regular-session candles or buy gate.
                # Native SL/TP stay broker-managed; a pending protection plan and
                # the existing time-stop can use a fresh extended-session quote.
                if etoro_owned_position(rec_owner, broker):
                    _observed = etoro_exit_assess(broker, inst, rec_owner, qty)
                    etoro_exit_publish(runtime, _observed)
                    if _observed["quote_price"] is not None and not held_pulsar and darf_verkaufen:
                        try:
                            _quote = {"bid": _observed["quote_price"], "timestamp": _observed["quote_at"],
                                      "broker_tradable": _observed["broker_tradable"]}
                            _action = quote_exit_decision(rec_owner, qty, _quote)
                            if _action:
                                _exit = etoro_exit_assess(broker, inst, rec_owner, qty,
                                    exit_kind=_action["kind"], quote=_quote)
                                if (_action["kind"] == "PROFIT" and _exit["allow_order"]
                                        and _exit["quote_price"] < float(rec_owner.planned_take)):
                                    _exit.update(allow_order=False, decision="BLOCK_PROFIT",
                                        reason="Frischer Brokerkurs liegt wieder unter dem bestehenden Kursziel")
                                etoro_exit_publish(runtime, _exit)
                                if _exit["allow_order"]:
                                    _sent = close_position(broker, inst, qty, asset_type=inst.asset_type,
                                        reference_price=_exit["quote_price"],
                                        position_ids=sorted(rec_owner.owned_position_id_set()),
                                        snapshot_id=rec_owner.broker_snapshot_id,
                                        instrument_id=rec_owner.broker_instrument_id)
                                    _meta = {"reason": _action["reason"], "label": _action["label"],
                                        "position_ids": sorted(rec_owner.owned_position_id_set()),
                                        **_order_ownership_binding()}
                                    for oid in _sent.order_ids:
                                        order_meta[str(oid)] = dict(_meta)
                                    ownership_registry.register_orders(_sent.order_ids, inst.name,
                                        _meta, owner="BOT", asset_type=inst.asset_type)
                                    continue
                        except Exception as exc:
                            logger.warning("Aktueller Positionsausstieg %s: %s", inst.name, exc)
                            if _connection_failure(broker, exc):
                                raise
                if held_pulsar:
                    try:
                        def pulsar_close(qty_to_close, price):
                            return close_position(broker, inst, qty_to_close, asset_type=inst.asset_type,
                                reference_price=price, position_ids=sorted(rec_owner.owned_position_id_set()),
                                snapshot_id=rec_owner.broker_snapshot_id,
                                instrument_id=rec_owner.broker_instrument_id)
                        def pulsar_register(result, reason):
                            meta = {"reason": reason, "label": "PULSAR-EXIT",
                                    "position_ids": sorted(rec_owner.owned_position_id_set()),
                                    **_order_ownership_binding()}
                            for oid in result.order_ids:
                                order_meta[str(oid)] = dict(meta)
                            ownership_registry.register_orders(result.order_ids, inst.name, meta,
                                                               owner="BOT", asset_type=inst.asset_type)
                        pulsar_position.manage(broker, inst, rec_owner, held_pulsar,
                            can_sell=darf_verkaufen, close=pulsar_close, register_exit=pulsar_register,
                            assess_exit=lambda kind, quantity: etoro_exit_assess(
                                broker, inst, rec_owner, quantity, exit_kind=kind),
                            publish_exit=lambda status: etoro_exit_publish(runtime, status))
                        manager.save()
                    except Exception as exc:
                        logger.warning("PULSAR-Positionsmanagement %s: %s", inst.name, exc)
                        if _connection_failure(broker, exc):
                            _drop_transport(exc)
                            cycle_connection_failure = True
                            break
                    continue
                if mode_owner != "AUTO":
                    print(f"POS {inst.name:8s} {mode_owner} | Herkunft={getattr(rec_owner,'source','?')} | Schutz-/Zuordnungsabgleich")
                    continue
                try:
                    lage_pos = None
                    # --- Nachrichtenlage zur offenen Position ---
                    # Auch ohne Verkaufssignal aus der Strategie: wenn zu
                    # einem gehaltenen Wert ernste Meldungen auflaufen, ist
                    # das ein eigener Grund auszusteigen. Genau dafuer hat
                    # man den Filter.
                    if (darf_verkaufen and qty > 0
                            and getattr(config, "NEWS_FILTER_ENABLED", True)
                            and getattr(config, "NEWS_SELL_ON_CRISIS", True)
                            and inst.asset_type != "forex"):
                        try:
                            lage_pos = nachrichten.pruefe(inst.name)
                            if lage_pos.verkauf_empfohlen:
                                print(f"  !! {inst.name}: Nachrichtenlage kritisch "
                                      f"({lage_pos.punkte} Punkte) -> Verkauf")
                                if lage_pos.schlagzeilen:
                                    print(f"       {lage_pos.schlagzeilen[0][:90]}")
                                logger.warning("NACHRICHTEN-EXIT %s: %s",
                                               inst.name, lage_pos.kurzfassung())
                                _news_exit = etoro_exit_assess(broker, inst, rec_owner, qty, exit_kind="NEWS_RISK")
                                etoro_exit_publish(runtime, _news_exit)
                                if not _news_exit["allow_order"]:
                                    logger.warning("Nachrichten-Exit %s wartet: %s", inst.name, _news_exit["reason"])
                                    continue
                                ergebnis_n = close_position(
                                    broker, inst, qty,
                                    asset_type=inst.asset_type,
                                    reference_price=_news_exit["quote_price"],
                                    position_ids=sorted(rec_owner.owned_position_id_set()),
                                    snapshot_id=rec_owner.broker_snapshot_id,
                                    instrument_id=rec_owner.broker_instrument_id,
                                )
                                if ergebnis_n:
                                    _exit_meta = {
                                        "reason": (f"Nachrichtenlage: "
                                                   f"{lage_pos.kurzfassung()}"),
                                        "label": "NACHRICHTEN-EXIT",
                                        "position_ids": sorted(
                                            rec_owner.owned_position_id_set()),
                                        **_order_ownership_binding(),
                                    }
                                    for oid in ergebnis_n.order_ids:
                                        order_meta[str(oid)] = dict(_exit_meta)
                                    ownership_registry.register_orders(
                                        ergebnis_n.order_ids, inst.name,
                                        _exit_meta,
                                        owner="BOT", asset_type=inst.asset_type)
                                continue     # Position ist raus, kein weiteres Signal noetig
                        except Exception as exc:
                            if isinstance(exc, (VerbindungVerloren, NichtVerbunden)) or not broker.is_connected():
                                raise
                            logger.warning("Nachrichtenpruefung Position %s: %s",
                                           inst.name, exc)

                    df = broker.historie(
                        inst, config.HISTORY_DURATION, config.BAR_SIZE,
                        nur_handelszeiten=inst.use_rth,
                    )
                    if df is not None and len(df) >= 30:
                        position_histories[str(inst.name).upper()] = df.copy()
                    signal = generate_signal(df, model)
                    # 10.2.0: Eine Position, die nachweislich mit einer
                    # Zusatzstrategie eroeffnet wurde (Ledger-Snapshot mit
                    # passendem Parameter-Hash), bekommt ihren regulaeren
                    # Ausstieg von GENAU dieser Strategie auf Tageskerzen.
                    # Broker-Stop/TP, Time-Stop und News-Exits weiter oben
                    # bleiben unveraendert. Jede Unklarheit (kein Ledger-Trade,
                    # anderer Hash, Datenfehler) faellt konservativ auf das
                    # NEXUS-Standardsignal zurueck.
                    if inst.asset_type == "stock":
                        try:
                            import trade_ledger
                            import zusatz_strategien
                            _offen = trade_ledger.offener_trade("etoro", inst.name)
                            _zmode = str((_offen or {}).get("entry_strategy_mode") or "")
                            if _offen and zusatz_strategien.ist_zusatzstrategie(_zmode):
                                _snap = zusatz_strategien.parameter_snapshot(_zmode)
                                if str(_offen.get("strategy_parameter_hash") or "") == _snap["parameter_hash"]:
                                    _spec = zusatz_strategien.STRATEGIEN[_zmode]
                                    _daily = broker.historie(
                                        inst, str(_spec["history_duration"]), "1 day",
                                        nur_handelszeiten=inst.use_rth,
                                    )
                                    _bew = zusatz_strategien.bewerte(_zmode, _daily)
                                    from strategy import Signal as _Signal
                                    signal = _Signal(
                                        "SELL" if _bew.exit else "HOLD",
                                        _bew.exit_reason or f"{_spec['label']}: kein Ausstiegssignal",
                                        0.5, float(signal.price), _bew.atr)
                        except Exception as exc:
                            logger.warning("Zusatzstrategie-Ausstieg %s nicht pruefbar; "
                                           "NEXUS-Standardsignal bleibt massgeblich: %s",
                                           inst.name, exc)
                    # Kurs merken -- wird gleich fuer den Client-Stop und die
                    # Krypto-Gesamtquote gebraucht.
                    if signal.price == signal.price and signal.price > 0:
                        last_price_by_conid[str(con_id)] = signal.price

                    print(
                        f"POS {inst.name:8s} {signal.action:4s} | {signal.reason} | "
                        f"Menge={qty:g} EK={avg_cost:.6g}"
                    )
                    if signal.action == "SELL" and qty > 0 and not darf_verkaufen:
                        print(f"  -> {inst.name}: Verkaufssignal, aber Bot ist GESTOPPT "
                              f"-- keine Order. ({signal.reason})")
                        logger.info("DECISION %s SELL blocked=bot_gestoppt", inst.name)
                    elif signal.action == "SELL" and qty > 0:
                        # Nur diskretionaere STRATEGIE-Exits duerfen von der KI
                        # gegengeprueft werden. Broker-Stop/TP, Time-Stop und harte
                        # News-Krisen-Exits passieren weiter oben ohne KI-Veto.
                        # Vorhandene TP/SL/Exit-Orders werden zuerst storniert.
                        _strategy_exit = etoro_exit_assess(broker, inst, rec_owner, qty, exit_kind="STRATEGY_EXIT")
                        etoro_exit_publish(runtime, _strategy_exit)
                        if not _strategy_exit["allow_order"]:
                            logger.warning("Strategie-Exit %s wartet: %s", inst.name, _strategy_exit["reason"])
                            continue
                        ergebnis = close_position(
                            broker, inst, qty,
                            asset_type=inst.asset_type, reference_price=_strategy_exit["quote_price"],
                            position_ids=sorted(rec_owner.owned_position_id_set()),
                            snapshot_id=rec_owner.broker_snapshot_id,
                            instrument_id=rec_owner.broker_instrument_id,
                        )
                        _exit_meta = {
                            "reason": signal.reason,
                            "label": "STRATEGIE-EXIT",
                            "position_ids": sorted(
                                rec_owner.owned_position_id_set()),
                            **_order_ownership_binding(),
                        }
                        for oid in ergebnis.order_ids:
                            order_meta[str(oid)] = dict(_exit_meta)
                        ownership_registry.register_orders(
                            ergebnis.order_ids, inst.name,
                            _exit_meta,
                            owner="BOT", asset_type=inst.asset_type)
                except Exception as exc:
                    if _connection_failure(broker, exc):
                        _drop_transport(exc)
                        cycle_connection_failure = True
                        print(f"  -> {inst.name}: Broker-/Internetverbindung verloren; Positionszyklus sicher pausiert.")
                        break
                    logger.warning("Positionsmanagement %s fehlgeschlagen: %s", inst.name, exc)

            if cycle_connection_failure:
                # Keine clientseitigen Stops/News-/Scannerentscheidungen auf
                # teilweise veralteten Daten. Nach Reconnect folgt Pflicht-Resync.
                continue

            # Client-seitiger Stop-Loss fuer Krypto (Aktien haben einen echten
            # Broker-Stop aus der Bracket-Order; Krypto nicht -- siehe Funktion).
            try:
                check_client_side_stops(broker, manager, current_positions, order_meta, last_price_by_conid)
            except Exception as exc:
                if _connection_failure(broker, exc):
                    _drop_transport(exc)
                    continue
                raise

            # Aktuelle Krypto-Gesamtquote fuer die Kaufentscheidungen weiter unten.
            krypto_quote = crypto_exposure_pct(current_positions, last_price_by_conid, equity)
            krypto_limit = getattr(config, "MAX_CRYPTO_PORTFOLIO_PCT", 0.15)

            # --- Marktlage: bei breiter Krise gar nicht erst kaufen ---
            markt_kritisch = False
            markt_lage = None
            markt_event_crisis = 0
            if darf_kaufen and getattr(config, "NEWS_FILTER_ENABLED", True):
                try:
                    markt_lage = nachrichten.marktlage()
                    markt_event_crisis = global_crisis_score(markt_lage)
                    _news_risk = getattr(markt_lage, "risiko_belege", {}) or {}
                    runtime.update(news_risk_evidence={key: _news_risk[key] for key in (
                        "status", "observed_at", "assessed_at", "raw_crisis_score",
                        "actionable_crisis_score", "buy_pause_supported", "verified_event",
                        "origin_count", "categories", "excluded_reasons", "detail") if key in _news_risk})
                    markt_kritisch = (nachrichten.markt_kritisch() or
                                      markt_event_crisis >= int(getattr(config,"GLOBAL_CRISIS_HARD_BLOCK_SCORE",22)))
                    if markt_kritisch:
                        print(f"MARKTLAGE KRITISCH (News={markt_lage.punkte}, Krise={markt_event_crisis}) "
                              "-- keine neuen Kaeufe in diesem Zyklus.")
                        logger.warning("Marktlage kritisch: %s", markt_lage.kurzfassung())
                    elif markt_event_crisis:
                        print(f"GLOBALE KRISE erkannt: Score {markt_event_crisis}; Branchenwirkung wird einzeln bewertet.")
                except Exception as exc:
                    logger.warning("Marktlage-Pruefung fehlgeschlagen: %s", exc)

            # --- Technisches Marktregime (SPY/QQQ/IWM + VIX) ---
            regime = regime_client.get() if getattr(config, "MARKET_REGIME_ENABLED", True) else None
            if regime and regime.checked:
                print(f"MARKTREGIME {regime.name} | Score={regime.score} | VIX={regime.vix:.1f}")

            # --- Underdog-Freigabe: Kennzahlen-Screening (selten, teuer) ---
            if darf_kaufen and getattr(config, "UNDERDOGS_AKTIV", True):
                alter = time.time() - underdog_freigabe["geprueft_am"]
                intervall = getattr(config, "UNDERDOG_SCREENING_HOURS", 24) * 3600
                if alter > intervall:
                    try:
                        print("Underdog-Screening laeuft (Kennzahlen) ...")
                        if getattr(config, "AUTO_UNDERDOG_REFRESH", True):
                            screening_ausfuehren(verbose=False)
                        freigegeben = set(freigegebene_underdogs())
                        automation_mark("underdogs", "ok", f"{len(freigegeben)} freigegeben")
                        underdog_freigabe["symbole"] = freigegeben
                        underdog_freigabe["geprueft_am"] = time.time()
                        gesamt = len([s for s in config.STOCK_SYMBOLS if s.get("underdog")])
                        print(f"  {len(freigegeben)} von {gesamt} Underdogs freigegeben.")
                        logger.info("Underdog-Freigabe: %s", sorted(freigegeben))
                    except Exception as exc:
                        logger.warning("Underdog-Screening fehlgeschlagen: %s", exc)
                        # Bei Fehler NICHTS freigeben -- im Zweifel nicht handeln.
                        underdog_freigabe["symbole"] = set()
                        underdog_freigabe["geprueft_am"] = time.time()

            # Chancen-Scanner fuer neue Positionen.
            # Bei PAUSE oder STOPP wird gar nicht erst gescannt: das spart
            # Datenabfragen und macht im Protokoll eindeutig sichtbar, dass
            # bewusst nicht gekauft wird.
            block = []
            priority_event_symbols=set()
            short_confirm_count = 0
            # Bei geschlossener Boerse nur noch Krypto scannen. Aktien-Kerzen
            # aktualisieren sich nicht, Nachrichten- und KI-Abfragen dafuer
            # waeren reine Kosten. Krypto handelt rund um die Uhr weiter.
            from stock_readiness import probe as probe_stock_quote
            probe_stock_quote(stock_bereitschaft, broker, universe)
            runtime.update(stock_trading_ready=stock_bereitschaft.status())
            report_risk_buy_gate(runtime, risk)
            scan_universe = universe
            if darf_kaufen and not aktien_zyklus_sinnvoll and bool(
                    getattr(config, "CRYPTO_IGNORES_MARKET_CALENDAR", True)):
                scan_universe = [i for i in universe if getattr(i, "asset_type", "") == "crypto"]
                if scan_universe:
                    print(f"Boerse zu ({kalender_grund}) -- nur Krypto im Scan "
                          f"({len(scan_universe)} Werte).")
                else:
                    print(f"Boerse zu ({kalender_grund}) -- Aktien-Scan ausgesetzt.")

            if not darf_kaufen:
                print(f"Scanner uebersprungen -- Zustand {aktueller_zustand.upper()}.")
            elif not scan_universe:
                pass
            elif scan_universe:
                # Frische relevante News ziehen betroffene US-Werte in diesen
                # Scanner-Block vor. Der Radar selbst löst NIE eine Order aus.
                priority=[]
                try:
                    hits=news_radar.poll()
                    automation_mark("news_radar","ok",f"{len(hits)} priorisierte Meldungen")
                    wanted={h.symbol:h for h in hits[:int(getattr(config,"NEWS_RADAR_MAX_PRIORITY",4))]}
                    priority_event_symbols=set(wanted)
                    for cand in scan_universe:
                        if cand.name.upper() in wanted:
                            priority.append(cand)
                    if priority:
                        print("NEWS-RADAR Priorität:",", ".join(f"{x.name}({wanted[x.name.upper()].score})" for x in priority))
                except Exception as exc:
                    logger.warning("News-Radar: %s",exc)
                # KI darf ausschliesslich Aufmerksamkeit steuern. Sie sieht nur
                # dieses bereits von eToro qualifizierte Universum; Fremdticker
                # werden in ai_attention.py technisch verworfen. Bei Ausfall oder
                # Budgetende bleibt die faire deterministische Rotation aktiv.
                ai_priority = []
                try:
                    refresh = max(60.0, float(getattr(config, "AI_ATTENTION_REFRESH_MINUTES", 30)) * 60.0)
                    if time.time() - float(attention_cache.get("at", 0.0) or 0.0) >= refresh:
                        # Nur das tatsaechlich zu scannende Universum an die KI
                        # geben. Bei geschlossener Boerse sind das nur Krypto-
                        # Werte -- eine Priorisierung von Aktien waere dann ein
                        # bezahlter Aufruf ohne jeden Nutzen.
                        ar = attention.prioritize(
                            scan_universe, max_items=int(getattr(config, "AI_ATTENTION_MAX_PRIORITY", 48))
                        )
                        attention_cache["at"] = time.time()
                        attention_cache["keys"] = list(ar.ordered_keys)
                        attention_cache["reason"] = str(ar.reason or "")
                    by_key = {canonical_key(x.name, x.asset_type): x for x in scan_universe}
                    ai_priority = [by_key[k] for k in attention_cache.get("keys", []) if k in by_key]
                    if ai_priority:
                        logger.info("AI_ATTENTION priority=%s reason=%s",
                                    [canonical_key(x.name, x.asset_type) for x in ai_priority],
                                    str(attention_cache.get("reason", ""))[:300])
                except Exception as exc:
                    logger.warning("KI-Aufmerksamkeit ausgefallen; Rotation unveraendert: %s", exc)
                    ai_priority = []

                # News-Radar und KI veraendern nur die Reihenfolge. Kein Eintrag
                # kann Sicherheits-, Risiko-, Kosten- oder Signalfilter umgehen.
                priority = ai_priority + [x for x in priority if x not in ai_priority]

                # Favoriten werden in JEDEM Zyklus zuerst betrachtet. Das ist nur
                # Prioritaet; Signal-, News-, Kosten- und Risikoregeln bleiben identisch.
                favorite_priority=[x for x in scan_universe if bool(getattr(x,"favorite",False))]
                broker_key=str(getattr(broker,"name","") or "").upper()
                budget=int(getattr(config, f"INSTRUMENTS_PER_CYCLE_{broker_key}", getattr(config,"INSTRUMENTS_PER_CYCLE",20)))
                budget=max(5,budget)

                block=scan_scheduler.select(
                    scan_universe, budget, favorites=favorite_priority, priority=priority
                )

            if darf_kaufen:
                try:
                    for candidate in pulsar_core.candidate_instruments(broker, instrument_by_symbol):
                        key = canonical_key(candidate.name, candidate.asset_type)
                        instrument_by_symbol[key] = candidate
                        if not any(canonical_key(i.name, i.asset_type) == key for i in block):
                            block.append(candidate)
                except Exception as exc:
                    logger.warning("PULSAR-Kandidaten bleiben in Beobachtung: %s", exc)
            symbols = {i.name for i in universe}
            print(
                f"\n--- Zyklus {cycle} | {datetime.now():%Y-%m-%d %H:%M:%S} | "
                f"Scanner {len(block)} | offene Positionen {risk.open_positions} ---"
            )

            scanner_connection_failure = False
            # eToro-Ausfuehrungen werden pro Scannerzyklus serialisiert. Ein
            # frischer Fill muss erst Depot, Cash, Sektor- und Korrelations-
            # Zustand aktualisieren, bevor die naechste Kaufentscheidung Geld
            # binden darf. Parallele Threads werden zusaetzlich atomar in
            # etoro_reconciliation.reserve_and_start_intent gesperrt.
            etoro_buy_submitted_this_cycle = False
            try:
                from scan_uebersicht import Zyklus as _ScanZyklus
                scan_zyklus = _ScanZyklus(getattr(broker, "name", "?"), cycle)
            except Exception:
                scan_zyklus = None
            for inst in block:
                pulsar_context = None
                # Die ID existiert vor dem ersten Kandidaten-Gate und wird bis
                # zu Quellen, Orders, Fill, Trade und Outcome durchgereicht.
                current_decision_id = new_decision_id()
                runtime.update()  # Main-Thread-Fortschritt fuer Pi-systemd-Watchdog
                # Fernzustand und Pi-Hardware vor JEDEM Kandidaten frisch genug
                # pruefen. Hardwaremetriken sind intern 30 s gecacht.
                darf_kaufen = zustand.darf_kaufen() and _pi_hardware_buy_ok(force=False)
                if not ensure_connection() or resync_required:
                    print(f"  -> {broker.name}-Verbindung unterbrochen/neu aufgebaut. Aktuellen Zyklus pausieren; vor Fortsetzung wird synchronisiert.")
                    scanner_connection_failure = True
                    break
                try:
                    last = last_data_error.get(inst.name, 0)
                    if time.time() - last < config.INSTRUMENT_COOLDOWN_MINUTES * 60:
                        continue
                    df = broker.historie(
                        inst, config.HISTORY_DURATION, config.BAR_SIZE,
                        nur_handelszeiten=inst.use_rth,
                    )
                    # 10.4.0: dieselbe Reihe fuer die WebUI-Kerzenansicht
                    # wegsichern -- kein zusaetzlicher eToro-Abruf.
                    try:
                        from etoro_chart_store import merke_scan
                        merke_scan(broker, inst, df, config.BAR_SIZE)
                    except Exception:
                        logger.debug("Chartreihe nicht gesichert", exc_info=True)
                    from stock_readiness import history_fresh
                    if inst.asset_type == "stock":
                        history_ok, history_detail = history_fresh(df, config.BAR_SIZE)
                        if not history_ok:
                            logger.warning("%s: Scanner wartet auf frische Kerzen: %s", inst.name, history_detail)
                            runtime.update(stock_history_status={"symbol": inst.name,
                                "ready": False, "detail": history_detail})
                            continue
                        runtime.update(stock_history_status={"symbol": inst.name,
                            "ready": True, "detail": history_detail})
                    # 10.2.0: Laufzeitschalter fuer Aktien-Einstiegsstrategien.
                    # Ein Zusatzmodus ersetzt NUR das Einstiegssignal; jede
                    # weitere Stufe der Kaufkaskade (Session, News, Earnings,
                    # KI, Kosten, Cash, Risiko, Broker-Schutz) bleibt identisch.
                    etoro_zusatz_mode = ""
                    etoro_zusatz_snapshot = None
                    if inst.asset_type == "stock":
                        try:
                            import etoro_strategy_mode
                            _mode = etoro_strategy_mode.current_mode()
                            if _mode != etoro_strategy_mode.NEXUS_STANDARD:
                                etoro_zusatz_mode = _mode
                        except Exception as exc:
                            logger.warning("eToro-Strategiemodus nicht lesbar (%s); "
                                           "NEXUS Standard bleibt aktiv.", exc)
                    if etoro_zusatz_mode:
                        import zusatz_strategien
                        from strategy import Signal
                        spec = zusatz_strategien.STRATEGIEN[etoro_zusatz_mode]
                        daily_df = broker.historie(
                            inst, str(spec["history_duration"]), "1 day",
                            nur_handelszeiten=inst.use_rth,
                        )
                        try:
                            bewertung = zusatz_strategien.bewerte(etoro_zusatz_mode, daily_df)
                        except ValueError as exc:
                            print(f"{inst.name:8s} HOLD | {exc}")
                            logger.info("DECISION %s HOLD zusatz_datenlage=%s", inst.name, exc)
                            continue
                        # Kurs und ATR fuer Stop/Sizing kommen weiter aus der
                        # frischen 1h-Reihe -- der Tagesschluss von gestern ist
                        # kein handelbarer Kurs.
                        frischer_kurs = float(df["close"].iloc[-1]) if df is not None and len(df) else float(bewertung.close)
                        signal = Signal(
                            "BUY" if bewertung.entry else "HOLD",
                            bewertung.entry_reason, 0.5, frischer_kurs,
                            bewertung.atr)
                        etoro_zusatz_snapshot = zusatz_strategien.parameter_snapshot(etoro_zusatz_mode)
                    else:
                        signal = generate_signal(df, model)
                    pulsar_context = pulsar_core.context(broker, inst)
                    if pulsar_context and signal.action != "SELL":
                        from dataclasses import replace
                        signal = replace(signal, action="BUY", reason="PULSAR: belegter Kandidat; persoenliche Freigabe erforderlich")
                    print(f"{inst.name:8s} {signal.action:4s} | {signal.reason} | px={signal.price:.6g}")

                    # --- Event Intelligence: News + Earnings + Preis/Volumen ---
                    # v5.8: Broad bleibt eine eigene, strengere Risikoklasse.
                    risk_class = classify_instrument(inst)
                    ist_underdog = risk_class.curated_underdog
                    ist_broad = risk_class.broad
                    konservativer_kleinwert = risk_class.conservative
                    entry_session = None

                    # v5.9 R2: Aktien-BUYs werden VOR News/AI auf die regulaere
                    # Boersenzeit geprueft. Damit wird das KI-Tagesbudget nicht
                    # schon im Pre-/After-Market verbraucht. Krypto bleibt 24/7.
                    # eToro liefert zusaetzliche Broker-/Quote-Evidenz; die
                    # Exchange-Zeit bleibt fuer "REGULAR" massgeblich.
                    if signal.action == "BUY" and inst.asset_type == "stock":
                        sess = market_session_status(
                            broker, inst,
                            quote_max_age_seconds=_kursalter_grenze(),
                        )
                        from stock_readiness import quote as report_stock_quote
                        report_stock_quote(stock_bereitschaft, sess)
                        entry_session = sess
                        session_ok, session_reason = entry_allowed(
                            sess,
                            regular_hours_only=bool(getattr(config,"STOCK_NEW_BUYS_REGULAR_HOURS_ONLY",True)),
                        )
                        if not session_ok:
                            # Offener Kalender + Sperre = Stoerung, nicht Feierabend.
                            # Einmal je Symbol und Tag geht das nach Telegram.
                            melde_herabstufung(sess, notify)
                            print(f"  -> {inst.name}: KEIN KAUF | Grund: {session_reason}")
                            logger.info("DECISION %s BUY blocked=market_session session=%s grund=%s alter=%s detail=%s",
                                        inst.name, sess.session, sess.downgrade_grund or "kalender",
                                        sess.quote_age_seconds, sess.detail)
                            journal_ablehnung(inst, "market_session", session_reason, signal=signal,
                                              extra={"market_session":sess.to_dict()})
                            continue
                    lage_event = None
                    if getattr(config, "NEWS_FILTER_ENABLED", True) and inst.asset_type == "stock":
                        try:
                            # API-schonend: gezielte Company-News nur dann, wenn
                            # der Wert bereits technisch interessant ist, vom
                            # breiten News-Radar priorisiert wurde oder als
                            # Underdog das Kennzahlen-Screening bestanden hat.
                            # Gehaltene Positionen werden weiter oben IMMER
                            # fokussiert geprueft.
                            focused_news = (
                                signal.action == "BUY"
                                or inst.name.upper() in priority_event_symbols
                                or (ist_underdog and inst.name in underdog_freigabe["symbole"])
                            )
                            lage_event = nachrichten.pruefe(inst.name, focused=focused_news)
                        except Exception as exc:
                            logger.warning("Event-News %s fehlgeschlagen: %s", inst.name, exc)
                    earnings_snapshot = None
                    days_to_earnings = None
                    if inst.asset_type == "stock" and earnings_client.enabled():
                        try:
                            days_to_earnings = earnings_client.days_to_earnings(inst.name)
                            text_event = " ".join(getattr(lage_event, "schlagzeilen", []) or []).lower()
                            if days_to_earnings == 0 or any(w in text_event for w in EARNINGS_WORDS):
                                earnings_snapshot = earnings_client.snapshot(inst.name)
                        except Exception as exc:
                            logger.warning("Earnings %s: %s", inst.name, exc)
                    market_crisis_score = markt_event_crisis

                    # Zuerst die vorhandene Stundenhistorie bewerten. Nur wenn das
                    # Event selbst stark genug ist, wird ein zusaetzlicher kurzer
                    # 5-Minuten-Request fuer die Einstiegskontrolle gemacht.
                    text_event = " ".join(getattr(lage_event, "schlagzeilen", []) or []).lower()
                    event = assess_event(inst.name, getattr(inst, "sector", ""), lage_event,
                                         earnings_snapshot, df, konservativer_kleinwert, market_crisis_score)
                    short_candidate=(
                        event.score >= int(getattr(config,"EVENT_FAST_CONFIRM_SCORE",70)) or
                        bool(earnings_snapshot and getattr(earnings_snapshot,"recent",False)) or
                        (inst.name.upper() in priority_event_symbols and event.event_type in ("NEWS","EARNINGS"))
                    )
                    short_limit=int(getattr(config,"EVENT_CONFIRM_MAX_EXTRA_PER_CYCLE",4))
                    if (short_candidate and getattr(config,"EVENT_SHORT_CONFIRMATION_ENABLED",True) and
                        inst.asset_type=="stock" and short_confirm_count < short_limit):
                        try:
                            short_df=broker.historie(inst,getattr(config,"EVENT_CONFIRM_DURATION","2 D"),
                                                    getattr(config,"EVENT_CONFIRM_BAR_SIZE","5 mins"),
                                                    nur_handelszeiten=inst.use_rth)
                            if short_df is not None and len(short_df)>=22:
                                # broker.historie() liefert bei USE_COMPLETED_BAR_ONLY
                                # bereits nur abgeschlossene Kerzen. Kein doppeltes Abschneiden.
                                event_df=short_df.copy()
                                event = assess_event(inst.name, getattr(inst,"sector",""), lage_event,
                                                     earnings_snapshot, event_df, konservativer_kleinwert, market_crisis_score)
                                short_confirm_count += 1
                                logger.info("EVENT_CONFIRM %s short_bars=%s score=%s",inst.name,len(event_df),event.score)
                        except Exception as exc:
                            if isinstance(exc, (VerbindungVerloren, NichtVerbunden)) or not broker.is_connected():
                                raise
                            logger.warning("5-Min-Eventbestaetigung %s fehlgeschlagen, nutze 1h: %s",inst.name,exc)
                    if getattr(config, "EVENT_INTELLIGENCE_ENABLED", True):
                        print(f"  INTEL: {event.summary()}")

                    # Erst jetzt entscheiden, ob ueberhaupt ein BUY-Kandidat
                    # existiert. Ein HOLD/kein Trigger wird NICHT als Event- oder
                    # Earnings-Ablehnung in decision_history gezaehlt.
                    effective_buy = signal.action == "BUY" or event.event_buy
                    if not effective_buy:
                        if scan_zyklus is not None:
                            scan_zyklus.hold(inst.name, signal.action)
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Signal={signal.action}, Event={event.event_type}/{event.score}")
                        (logger.debug if str(signal.action).upper() == "HOLD" else logger.info)(
                            "DECISION %s action=%s event=%s score=%s reason=%s",
                            inst.name, signal.action, event.event_type, event.score, signal.reason
                        )
                        continue

                    # Event-getriebene BUYs entstehen erst NACH der Eventanalyse;
                    # deshalb hier dieselbe Marktzeit-Sperre wie oben fuer
                    # technische BUYs anwenden.
                    if signal.action != "BUY" and event.event_buy and inst.asset_type == "stock":
                        sess = market_session_status(
                            broker, inst,
                            quote_max_age_seconds=_kursalter_grenze(),
                        )
                        from stock_readiness import quote as report_stock_quote
                        report_stock_quote(stock_bereitschaft, sess)
                        entry_session = sess
                        session_ok, session_reason = entry_allowed(
                            sess, regular_hours_only=bool(getattr(config,"STOCK_NEW_BUYS_REGULAR_HOURS_ONLY",True))
                        )
                        if not session_ok:
                            melde_herabstufung(sess, notify)
                            print(f"  -> {inst.name}: KEIN KAUF | Grund: {session_reason}")
                            logger.info("DECISION %s BUY blocked=market_session event_buy=1 session=%s grund=%s alter=%s detail=%s",
                                        inst.name, sess.session, sess.downgrade_grund or "kalender",
                                        sess.quote_age_seconds, sess.detail)
                            journal_ablehnung(inst, "market_session", session_reason, signal=signal, event=event,
                                              extra={"market_session":sess.to_dict(), "event_driven_buy":True})
                            continue

                    # Kurz vor Quartalszahlen keine normalen technischen Neueinstiege.
                    # Der Kalender liefert in der Regel ein Datum, keine exakte Uhrzeit.
                    # Bei >=24h Schutzfenster blockieren wir deshalb konservativ auch
                    # den Vortag. Ein bestaetigter Event-Buy NACH starken Zahlen bleibt
                    # davon ausgenommen.
                    avoid_hours=float(getattr(config,"EARNINGS_AVOID_HOURS_BEFORE",24) or 0)
                    avoid_days=max(0, int(math.ceil(avoid_hours/24.0)))
                    if days_to_earnings is not None and days_to_earnings <= avoid_days and not event.event_buy:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Quartalszahlen in {days_to_earnings} Tag(en); "
                              f"Schutzfenster {avoid_hours:.0f}h")
                        logger.info("DECISION %s BUY blocked=earnings_window days=%s hours=%s",
                                    inst.name, days_to_earnings, avoid_hours)
                        journal_ablehnung(inst, "earnings_window", "earnings_window", signal=signal, event=event)
                        continue
                    if event.buy_blocked:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Event Intelligence Score {event.score}/100")
                        logger.info("DECISION %s BUY blocked=event score=%s", inst.name, event.score)
                        journal_ablehnung(inst, "event", f"Event Intelligence Score {event.score}/100", signal=signal, event=event)
                        continue

                    # Broad-Werte: zusaetzliches Liquiditaets-Gate, fail-closed.
                    if ist_broad and inst.asset_type=="stock":
                        liquid_ok, avg_dollar, min_dollar = broad_liquidity_check(df)
                        if not liquid_ok:
                            print(f"  -> {inst.name}: KEIN KAUF | Grund: Broad-Liquiditaet {avg_dollar:,.0f} < {min_dollar:,.0f} USD/Bar")
                            logger.info("DECISION %s BUY blocked=broad_liquidity avg_dollar=%.2f min=%.2f",inst.name,avg_dollar,min_dollar)
                            journal_ablehnung(inst,"broad_liquidity","Zusatzuniversum nicht liquide genug",signal=signal,event=event,extra={"avg_dollar_volume":avg_dollar,"min_dollar_volume":min_dollar})
                            continue
                    if event.event_buy and signal.action != "BUY":
                        signal.reason = f"EVENT-MOMENTUM {event.event_type} {event.score}/100 | {event.summary()}"
                    # Den vollständigen Brokerbestand nicht für jedes gescannte
                    # HOLD-Instrument laden. Erst ein tatsächlich bis zum BUY-
                    # Pfad gelangter Kandidat braucht diese frische Prüfung;
                    # unmittelbar vor dem Submit folgt weiterhin das finale
                    # zweite Money-Path-Gate.
                    pos, avg = current_position(broker, inst)
                    if pos != 0:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Position bereits vorhanden (Menge={pos:g}, EK={avg:.6g})")
                        logger.info("DECISION %s BUY blocked=existing_position qty=%s avg=%s", inst.name, pos, avg)
                        journal_ablehnung(inst, "existing_position", "existing_position", signal=signal)
                        continue
                    try:
                        if broker.hat_offene_order(inst, "BUY"):
                            print(f"  -> {inst.name}: KEIN KAUF | Grund: offene BUY-Order bereits vorhanden")
                            logger.info("DECISION %s BUY blocked=duplicate_open_order", inst.name)
                            journal_ablehnung(inst, "duplicate_open_order", "duplicate_open_order", signal=signal)
                            continue
                    except Exception as exc:
                        if _connection_failure(broker, exc):
                            raise
                        # Eine fehlgeschlagene Schutzpruefung wird nicht still ignoriert:
                        # lieber diesen Einstieg auslassen als eine Doppelorder riskieren.
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: offene Orders konnten nicht sicher geprueft werden")
                        logger.warning("DECISION %s BUY blocked=open_order_check_error %s", inst.name, exc)
                        journal_ablehnung(inst, "open_order_check_error", str(exc), signal=signal, event=event)
                        continue
                    if inst.asset_type == "crypto" and not config.CRYPTO_LIVE_TRADING:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Crypto-Trading ist deaktiviert (CRYPTO_LIVE_TRADING=False)")
                        logger.info("DECISION %s BUY blocked=crypto_disabled", inst.name)
                        journal_ablehnung(inst, "crypto_disabled", "crypto_disabled", signal=signal)
                        continue
                    if inst.asset_type == "crypto" and krypto_quote >= krypto_limit:
                        print(
                            f"  -> {inst.name}: KEIN KAUF | Grund: Krypto-Gesamtquote "
                            f"{krypto_quote*100:.1f}% erreicht Limit {krypto_limit*100:.0f}%"
                        )
                        logger.info(
                            "DECISION %s BUY blocked=crypto_portfolio_cap quote=%.4f limit=%.4f",
                            inst.name, krypto_quote, krypto_limit,
                        )
                        journal_ablehnung(inst, "crypto_portfolio_cap", f"Kryptoquote {krypto_quote:.4f} >= Limit {krypto_limit:.4f}", signal=signal, event=event, extra={"crypto_portfolio_ratio": krypto_quote, "crypto_portfolio_limit": krypto_limit})
                        continue
                    if not can_open_new_position(risk):
                        # Jeder Grund aus can_open_new_position() bekommt seine
                        # eigene Meldung. Bis v8.1.4 fehlte hier die
                        # Equity-Tagesbremse -- sie fiel in den else-Zweig und
                        # erschien als "Positionslimit erreicht (offen=0)".
                        # Am 25.08.2026 war das mit 243 Meldungen der haeufigste
                        # Ablehnungsgrund des Tages, und er war falsch.
                        grund = kaufsperre_grund(risk)
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: {grund}")
                        logger.info("DECISION %s BUY blocked=risk_manager reason=%s", inst.name, grund)
                        journal_ablehnung(inst, "risk_manager", grund, signal=signal, event=event)
                        continue

                    # --- Breite Marktkrise ---
                    if markt_kritisch:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Marktlage kritisch")
                        logger.info("DECISION %s BUY blocked=marktlage", inst.name)
                        journal_ablehnung(inst, "marktlage", "marktlage", signal=signal)
                        continue

                    # --- Underdogs: nur mit bestandenem Kennzahlen-Screening ---
                    # Nur kuratierte Underdogs laufen durch das Fundamentaldaten-Screening; Broad nicht.
                    if ist_underdog and inst.name not in underdog_freigabe["symbole"]:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Underdog ohne "
                              "bestandenes Kennzahlen-Screening")
                        logger.info("DECISION %s BUY blocked=underdog_screening", inst.name)
                        journal_ablehnung(inst, "underdog_screening", "underdog_screening", signal=signal)
                        continue

                    # --- Nachrichtenlage des einzelnen Wertes ---
                    if getattr(config, "NEWS_FILTER_ENABLED", True) and inst.asset_type != "forex":
                        try:
                            lage = lage_event or nachrichten.pruefe(inst.name)
                            # Underdogs bekommen eine strengere Schwelle:
                            # kleinere Werte reagieren heftiger auf schlechte
                            # Nachrichten, und ein Fehlkauf tut dort mehr weh.
                            grenze = (__import__("live_settings").news_rule("NEWS_BLOCK_SCORE_UNDERDOG", 3)
                                      if konservativer_kleinwert
                                      else __import__("live_settings").news_rule("NEWS_BLOCK_SCORE", 5))
                            if lage.geprueft and lage.punkte >= grenze:
                                print(f"  -> {inst.name}: KEIN KAUF | Grund: Nachrichtenlage "
                                      f"({lage.punkte} Punkte, Grenze {grenze})")
                                if lage.schlagzeilen:
                                    print(f"       {lage.schlagzeilen[0][:90]}")
                                logger.warning("DECISION %s BUY blocked=nachrichten punkte=%d",
                                               inst.name, lage.punkte)
                                journal_ablehnung(inst, "nachrichten", "nachrichten", signal=signal,
                                                  extra={"news_punkte": lage.punkte})
                                continue
                        except Exception as exc:
                            # Ein Fehler im Filter darf den Handel nicht lahmlegen,
                            # aber er wird protokolliert.
                            logger.warning("Nachrichtenpruefung %s fehlgeschlagen: %s",
                                           inst.name, exc)

                    stop, take = (pulsar_core.stops(pulsar_context, signal.price) if pulsar_context
                                  else calculate_stop_take(signal.price, "BUY", signal.atr, inst.asset_type))
                    # 10.7.0: Mindestabstand VOR der Mengenberechnung, damit die
                    # Menge aus dem Risiko entsteht, das der Stop wirklich hat.
                    stop_plan = aktien_stop_plan(inst.asset_type, signal.price, stop, take,
                                                 pulsar=bool(pulsar_context))
                    stop, take = stop_plan["stop"], stop_plan["take"]
                    if stop_plan["hinweis"]:
                        logger.info("STOP %s: %s", inst.name, stop_plan["hinweis"])
                    entry_referenz = float(signal.price)

                    # Klumpenrisiko / Branchenlimit
                    sektor_ok, sektor_grund = candidate_sector_guard(inst, current_positions, last_price_by_conid, equity)
                    if not sektor_ok:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: {sektor_grund}")
                        logger.info("DECISION %s BUY blocked=sector_guard %s", inst.name, sektor_grund)
                        journal_ablehnung(inst, "sector_guard", sektor_grund, signal=signal, event=event)
                        continue

                    # Nicht nur Branchenzaehler: auch statistisch stark korrelierte
                    # offene Positionen begrenzen, damit z.B. mehrere Halbleiterwerte
                    # nicht unbemerkt denselben Risikofaktor vervielfachen.
                    corr_ok, corr_grund = candidate_correlation_guard(
                        inst, df, current_positions, position_histories
                    )
                    if not corr_ok:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: {corr_grund}")
                        logger.info("DECISION %s BUY blocked=correlation_guard %s", inst.name, corr_grund)
                        journal_ablehnung(inst, "correlation_guard", corr_grund, signal=signal, event=event)
                        continue
                    elif corr_grund != "ok" and "deaktiviert" not in corr_grund and "nur Aktien" not in corr_grund:
                        logger.info("CORRELATION %s: %s", inst.name, corr_grund)

                    # 10.6.0: Die in der WebUI gewaehlte Einsatzstufe fuer
                    # eToro gilt ab dem naechsten Zyklus, ohne Neustart. Ohne
                    # Wahl kommen unveraendert die Profilwerte zurueck.
                    einsatz = _einsatz_etoro(inst.asset_type)
                    max_pct = float(einsatz["max_position_pct"])
                    if ist_broad and inst.asset_type=="stock":
                        max_pct *= float(getattr(config,"BROAD_POSITION_SIZE_FACTOR",0.50))
                    if regime and regime.checked and regime.name == "RISK_OFF":
                        max_pct *= float(getattr(config, "MARKET_RISK_OFF_SIZE_FACTOR", 0.5))
                    elif regime and regime.checked and regime.name == "RISK_ON":
                        # Default 1.00 veraendert nichts. Ein bewusst kleinerer/groesserer
                        # Faktor wirkt jetzt tatsaechlich auf die Positionsobergrenze.
                        max_pct *= float(getattr(config, "MARKET_RISK_ON_SIZE_FACTOR", 1.0))
                    qty = size_new_position(
                        equity, signal.price, stop,
                        max_capital_pct=max_pct, asset_type=inst.asset_type,
                        risk_pct=float(einsatz["risiko_pro_trade_pct"]),
                    )
                    if pulsar_context:
                        qty = pulsar_core.size(pulsar_context, qty, signal.price, stop, equity)
                    if qty <= 0:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Positionsgroesse zu klein (Preis={signal.price:.6g}, Stop={stop:.6g})")
                        logger.info("DECISION %s BUY blocked=position_size_zero", inst.name)
                        journal_ablehnung(inst, "position_size_zero", "position_size_zero", signal=signal)
                        continue

                    # eToro wird ausschliesslich mit REAL-Settlement/Hebel 1 genutzt.
                    # Ein Neueinstieg darf die frei verfuegbare Cashreserve nicht ueberschreiten.
                    broker_name = "etoro"
                    cash_guard = True
                    if cash_guard:
                        try:
                            cash_raw = broker.verfuegbares_cash()
                            cash = float(cash_raw) if cash_raw is not None else 0.0
                        except Exception as exc:
                            if _connection_failure(broker, exc):
                                raise
                            logger.warning("%s Cash konnte nicht ermittelt werden: %s", broker_name, exc)
                            cash = 0.0
                        reserve_cfg = "ETORO_CASH_RESERVE_PCT"
                        reserve = max(0.0, min(0.50, float(getattr(config, reserve_cfg, 0.02))))
                        spendable = cash * (1.0 - reserve)
                        spendable -= pulsar_core.reserved_cash(
                            broker.account_fingerprint(), "DEMO" if broker.paper else "LIVE",
                            exclude=((pulsar_context or {}).get("proposal") or {}).get("id", ""))
                        if spendable <= 0:
                            print(f"  -> {inst.name}: KEIN KAUF | Grund: eToro-Cash nicht verfuegbar / Hebel-1-Cashschutz")
                            logger.info("DECISION %s BUY blocked=no_cash_margin_disabled broker=%s", inst.name, broker_name)
                            journal_ablehnung(inst, "no_cash_margin_disabled", "no_cash_margin_disabled", signal=signal)
                            continue
                        max_cash_qty = spendable / max(float(signal.price), 1e-12)
                        if inst.asset_type != "crypto":
                            max_cash_qty = math.floor(max_cash_qty)
                        geplante_qty = float(qty)
                        qty = min(float(qty), max_cash_qty)
                        if 0 < qty < geplante_qty:
                            # Die Verkleinerung passierte bisher still. Ohne
                            # Meldung wundert man sich spaeter, warum eine
                            # Position kleiner ausfiel als das Risikomodell
                            # vorgesehen hatte.
                            alt_wert = geplante_qty * float(signal.price)
                            neu_wert = qty * float(signal.price)
                            print(f"  -> {inst.name}: Order von {alt_wert:.2f} auf "
                                  f"{neu_wert:.2f} wegen Cash-Reserve reduziert")
                            logger.info(
                                "CASH_REDUKTION %s broker=%s geplant=%.4f neu=%.4f "
                                "cash=%.2f reserve=%.0f%%",
                                inst.name, broker_name, geplante_qty, qty, cash, reserve * 100)
                        if qty <= 0:
                            print(f"  -> {inst.name}: KEIN KAUF | Grund: Cash-Reserve/Hebel-Schutz")
                            logger.info("DECISION %s BUY blocked=cash_reserve broker=%s", inst.name, broker_name)
                            journal_ablehnung(inst, "cash_reserve", "cash_reserve", signal=signal)
                            continue

                    quote = marktqualitaet.latest_quote(inst.name, inst.asset_type, ist_underdog,
                                                            broad=ist_broad, broker=broker, instrument=inst)
                    quote_ok, quote_reason = marktqualitaet.acceptable(quote, ist_underdog, inst.asset_type, broad=ist_broad)
                    # Falls der Nutzer die RTH-Sperre bewusst deaktiviert, wird
                    # Extended-Hours-Handel trotzdem niemals mit einem
                    # Fallback-Spread kalkuliert. Echter Bid/Ask + frischer Quote
                    # sind Pflicht; bei eToro kommt danach zusaetzlich der
                    # offizielle What-if-Kosten-Check im Cost-Engine-Pfad.
                    if (entry_session is not None and getattr(entry_session,"session","") == "EXTENDED" and
                            not (getattr(quote,"checked",False) and getattr(quote,"bid",0) > 0 and getattr(quote,"ask",0) > getattr(quote,"bid",0))):
                        quote_ok=False
                        quote_reason="Extended Hours nur mit echtem Live-Bid/Ask; Fallback-Kosten unzulaessig"
                    if not quote_ok:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Marktqualitaet: {quote_reason}")
                        logger.info("DECISION %s BUY blocked=market_quality %s", inst.name, quote_reason)
                        journal_ablehnung(inst, "market_quality", quote_reason, signal=signal, event=event, extra={"spread_pct": getattr(quote,"spread_pct",None), "quote_source": getattr(quote,"source","")})
                        continue
                    # 10.7.0: Gekauft wird zum frischen Briefkurs, nicht zum
                    # Signalkurs des Scans. Stop und Ziel beziehen sich deshalb
                    # ab hier auf diesen Kurs -- mit denselben Abstaenden.
                    # Bei CSCO lag der Briefkurs 0,69 % unter dem Signal; der
                    # unveraenderte Stop sass damit 3 Cent unter dem Einstieg.
                    if (inst.asset_type == "stock" and not pulsar_context
                            and getattr(quote, "checked", False)
                            and float(getattr(quote, "ask", 0) or 0) > 0
                            and float(getattr(quote, "bid", 0) or 0) > 0):
                        stop_plan = aktien_stop_plan(inst.asset_type, signal.price, stop, take,
                                                     ask=float(quote.ask))
                        if stop_plan["hinweis"]:
                            logger.info("STOP %s: %s", inst.name, stop_plan["hinweis"])
                        entry_referenz = float(stop_plan["referenz"])
                        stop, take = stop_plan["stop"], stop_plan["take"]
                    # Bei bewusst erlaubten eToro-Extended-Hours reicht
                    # keine statische Gebührenannahme: eine echte What-if-
                    # Kostenantwort MUSS vorliegen. Der folgende Aufruf wird
                    # vom eToro-Adapter gecacht und in estimate_roundtrip...
                    # direkt wiederverwendet. Damit werden aktuelle
                    # Marktspread/Provision/Steuern statt einer erfundenen
                    # pauschalen Extended-Hours-Gebühr berücksichtigt.
                    if (entry_session is not None and getattr(entry_session,"session","") == "EXTENDED" and
                            broker_name == "etoro"):
                        try:
                            broker.dynamic_cost_quote(inst, qty, entry_referenz, action="open")
                        except Exception as exc:
                            print(f"  -> {inst.name}: KEIN KAUF | Grund: eToro Extended-Hours-Kosten nicht sicher abrufbar")
                            logger.warning("DECISION %s BUY blocked=cost_quote extended_hours %s", inst.name, exc)
                            journal_ablehnung(inst, "cost_quote", f"Extended Hours: eToro What-if-Kosten fehlen: {exc}",
                                              signal=signal, event=event,
                                              extra={"market_session":entry_session.to_dict()})
                            continue
                    try:
                        kosten = estimate_roundtrip_with_broker(
                            broker, qty, entry_referenz, inst.asset_type, inst.currency,
                            bid=quote.bid or None, ask=quote.ask or None,
                            underdog=ist_underdog, broad=ist_broad, instrument=inst,
                        )
                    except CostQuoteUnavailable as exc:
                        print(f"  -> {inst.name}: KEIN KAUF | Grund: Broker-Kosten nicht sicher abrufbar")
                        logger.warning("DECISION %s BUY blocked=cost_quote_unavailable %s", inst.name, exc)
                        journal_ablehnung(inst, "cost_quote", str(exc), signal=signal, event=event)
                        continue

                    # Take-Profit ist ein Ziel, keine Renditeprognose. Fuer die
                    # Kostenhuerde wird deshalb eine konservative plausible
                    # Bewegung aus ATR, Eventstaerke, ML und Marktregime verwendet.
                    move_est = estimate_plausible_move(
                        entry_referenz, take, atr_value=signal.atr, event=event,
                        ml_probability=getattr(signal, "ml_probability", 0.5),
                        regime=regime, underdog=konservativer_kleinwert,
                    )
                    expected_move = move_est.gross_move_pct
                    wirtschaft = economically_viable(expected_move, kosten)
                    if not wirtschaft["allowed"]:
                        print(f"  -> {inst.name}: KEIN KAUF | Netto-Edge zu klein: plausible Bewegung "
                              f"{expected_move*100:.2f}% < Mindest-Edge {kosten.required_edge_pct*100:.2f}% | "
                              f"Kosten {kosten.total_cost_pct*100:.2f}%")
                        print(f"       Edge-Modell: {move_est.reason}")
                        logger.info("DECISION %s BUY blocked=net_edge cost=%.5f required=%.5f plausible=%.5f details=%s",
                                    inst.name, kosten.total_cost_pct, kosten.required_edge_pct, expected_move, move_est.reason)
                        journal_ablehnung(inst, "net_edge", "Plausible Bewegung deckt Sicherheitsmarge/Kosten nicht", signal=signal, event=event, extra={"cost_pct": kosten.total_cost_pct, "required_edge_pct": kosten.required_edge_pct, "plausible_move_pct": expected_move, "net_edge_pct": wirtschaft.get("net_edge_pct"), "edge_model_reason": move_est.reason, "spread_pct": getattr(quote,"spread_pct",None), "quote_source": getattr(quote,"source","")})
                        continue

                    # Letztes, rein deterministisches Money-Path-Gate. Die KI
                    # kommt in dieser Funktion technisch nicht vor. Dynamische
                    # Kernbedingungen werden unmittelbar vor dem Submit erneut
                    # geprueft, damit ein Zustand zwischen Vorfilter und Order
                    # nicht unbemerkt veraltet.
                    final_market_open = True
                    if inst.asset_type == "stock":
                        final_session = market_session_status(
                            broker, inst,
                            quote_max_age_seconds=_kursalter_grenze(),
                        )
                        from stock_readiness import quote as report_stock_quote
                        report_stock_quote(stock_bereitschaft, final_session)
                        final_market_open, _final_session_reason = entry_allowed(
                            final_session,
                            regular_hours_only=bool(getattr(config, "STOCK_NEW_BUYS_REGULAR_HOURS_ONLY", True)),
                        )
                    final_qty_open, _ = current_position(broker, inst)
                    final_duplicate = bool(broker.hat_offene_order(inst, "BUY"))
                    final_sector_ok, _ = candidate_sector_guard(inst, current_positions, last_price_by_conid, equity)
                    final_corr_ok, _ = candidate_correlation_guard(inst, df, current_positions, position_histories)
                    final_gate = bewerte_kandidat(
                        state_allows_buy=bool(zustand.darf_kaufen() and _pi_hardware_buy_ok(force=False)),
                        broker_online=bool(broker.is_connected() and not resync_required),
                        instrument_identity_ok=(canonical_key(inst.name, inst.asset_type) in instrument_by_symbol),
                        market_open=bool(final_market_open),
                        position_already_open=bool(final_qty_open > 0),
                        duplicate_open_order=final_duplicate,
                        risk_allows_buy=bool(can_open_new_position(risk)),
                        portfolio_allows_buy=bool(final_sector_ok and final_corr_ok),
                        cash_allows_buy=bool(spendable >= float(qty) * float(signal.price)),
                        market_quality_ok=bool(quote_ok),
                        cost_quote_ok=bool(str(getattr(kosten, "source", "")).startswith("etoro_what_if") or not getattr(config, "ETORO_REQUIRE_COST_QUOTE", True)),
                        net_edge_ok=bool(wirtschaft["allowed"]),
                        quantity=float(qty), price=float(signal.price), stop=float(stop), take_profit=float(take),
                    )
                    if not final_gate.approved:
                        print(f"  -> {inst.name}: KEIN KAUF | finaler Money-Path-Block: {final_gate.blocked_by} ({final_gate.reason})")
                        logger.warning("DECISION %s BUY final_gate_block=%s reason=%s", inst.name, final_gate.blocked_by, final_gate.reason)
                        journal_ablehnung(inst, final_gate.blocked_by, final_gate.reason, signal=signal, event=event)
                        continue

                    stock_ready, stock_ready_reason = stock_bereitschaft.darf_kaufen()
                    if inst.asset_type == "stock" and not stock_ready:
                        print(f"  -> {inst.name}: KEIN KAUF | {stock_ready_reason}")
                        logger.info("DECISION %s BUY blocked=startup_readiness reason=%s",
                                    inst.name, stock_ready_reason)
                        journal_ablehnung(inst, "startup_readiness", stock_ready_reason,
                                          signal=signal, event=event,
                                          extra={"trading_ready": stock_bereitschaft.status()})
                        continue

                    if (str(getattr(broker, "name", "")).lower() == "etoro"
                            and etoro_buy_submitted_this_cycle):
                        grund = ("eToro-Kaufkoordination: In diesem Scannerzyklus "
                                 "wurde bereits ein Kauf uebermittelt; Depot, Cash "
                                 "und Risikolimits werden zuerst frisch abgeglichen")
                        print(f"  -> {inst.name}: KEIN KAUF | {grund}")
                        journal_ablehnung(
                            inst, "etoro_buy_coordination", grund,
                            signal=signal, event=event)
                        continue

                    # 10.7.0: Risiko und Wert beziehen sich auf den Kaufkurs,
                    # auf den auch der Stop bezogen ist -- nicht auf den Scan.
                    risk_amount = abs(entry_referenz - stop) * qty
                    position_value = entry_referenz * qty
                    print(
                        f"  -> {inst.name}: BUY FREIGEGEBEN | Menge={qty:g} | "
                        f"Wert={position_value:.2f} {inst.currency} | Stop={stop:.6g} | "
                        f"TP={take:.6g} | Risiko~{risk_amount:.2f} {inst.currency} | "
                        f"Kosten~{kosten.total_cost_pct*100:.2f}% | plausible Bewegung~{expected_move*100:.2f}% | "
                        f"Netto-Edge~{wirtschaft['net_edge_pct']*100:.2f}%"
                    )
                    logger.info(
                        "DECISION %s BUY approved qty=%s value=%s stop=%s take=%s risk=%s reason=%s",
                        inst.name, qty, position_value, stop, take, risk_amount, signal.reason,
                    )
                    if scan_zyklus is not None:
                        scan_zyklus.freigegeben(inst.name)
                    decision_id = record_decision(
                        decision_id=current_decision_id,
                        status="APPROVED", **_order_ownership_binding(), symbol=inst.name,
                        asset_type=inst.asset_type, sector=getattr(inst,"sector",""), underdog=ist_underdog,
                        profile=getattr(config,"ACTIVE_PROFILE",""),
                        signal_reason=signal.reason, ml_probability=getattr(signal,"ml_probability",0.5),
                        event_summary=event.summary(), event_score=event.score,
                        earnings_score=getattr(event,"earnings_score",0),
                        regime=getattr(regime,"name","NEUTRAL") if regime else "NEUTRAL",
                        price=signal.price, stop=stop, take=take, qty=qty, position_value=position_value,
                        entry_reference=entry_referenz,
                        stop_distance_pct=((entry_referenz - stop) / entry_referenz if entry_referenz > 0 else None),
                        risk_amount=risk_amount, cost_pct=kosten.total_cost_pct,
                        required_edge_pct=kosten.required_edge_pct, plausible_move_pct=expected_move,
                        net_edge_pct=wirtschaft["net_edge_pct"], edge_model_reason=move_est.reason,
                        currency=getattr(inst, "currency", ""),
                        quote_source=getattr(quote,"source","fallback"),
                        bid=getattr(quote, "bid", None), ask=getattr(quote, "ask", None),
                        spread_pct=getattr(quote,"spread_pct",0.0),
                        spread_limit_pct=getattr(config, "MAX_SPREAD_STOCK_PCT", None),
                        cash_before=spendable, equity_before=equity,
                        # 10.6.0: Womit gerechnet wurde, gehoert in den Beleg.
                        einsatz_stufe=str(einsatz.get("level") or ""),
                        einsatz_quelle=str(einsatz.get("quelle") or ""),
                        einsatz_risiko_pro_trade_pct=float(einsatz["risiko_pro_trade_pct"]),
                        einsatz_max_position_pct=float(einsatz["max_position_pct"]),
                        daily_pnl=getattr(risk, "realized_pnl_today", None),
                        open_positions=getattr(risk, "open_positions", None),
                        trades_today=getattr(risk, "trades_today", None),
                        completed_candles_only=bool(getattr(config, "USE_COMPLETED_BAR_ONLY", True)),
                        candle_timestamps={
                            str(getattr(config, "BAR_SIZE", "")): (
                                str(df.index[-1]) if df is not None and len(df) else None)
                        },
                        **_decision_filter_trace(None),
                    ) or current_decision_id
                    speichere_decision_sources({
                        "decision_id": decision_id, "symbol": inst.name,
                        "asset_type": inst.asset_type,
                        "broker": getattr(broker, "name", "etoro"),
                        "aktion": "KAUF", "ergebnis": "APPROVED",
                        "ergebnis_grund": signal.reason,
                        "quellen": [
                            {"quelle": "TECHNIK", "richtung": "DAFUER",
                             "detail": signal.reason},
                            {"quelle": "CANDIDATE_GATE", "richtung": "DAFUER",
                             "detail": "alle deterministischen Gates bestanden"},
                        ],
                    }, decision_id=decision_id)

                    if pulsar_context and not pulsar_core.before_submit(
                            pulsar_context, broker, inst, price=signal.price, quantity=qty,
                            stop=stop, take=take, equity=equity, cash=spendable,
                            decision_id=decision_id, estimated_fees=kosten.total_cost,
                            earnings_days=days_to_earnings):
                        mark_execution(decision_id, "PENDING_HUMAN_APPROVAL", [])
                        continue

                    pending_meta = {
                        "stop": stop,
                        "take": take,
                        "reason": signal.reason,
                        "label": "BOT-KAUF",
                        # v8.1.3: Referenzkurs und Marktphase wandern mit in
                        # das Trade-Ledger. Aus Referenzkurs minus echtem Fill
                        # entsteht die Slippage-Schaetzung.
                        "signal_price": float(signal.price),
                        # 10.7.0: Kurs, auf den Stop und Ziel bezogen sind.
                        "entry_reference": float(entry_referenz),
                        "stop_distance_pct": ((float(entry_referenz) - float(stop)) / float(entry_referenz)
                                              if float(entry_referenz) > 0 else None),
                        "regime": getattr(regime, "name", "") if regime else "",
                        "ml_probability": signal.ml_probability,
                        "estimated_roundtrip_cost": kosten.total_cost,
                        "estimated_cost_pct": kosten.total_cost_pct,
                        "net_edge_pct": wirtschaft["net_edge_pct"],
                        "plausible_move_pct": expected_move,
                        "edge_model_reason": move_est.reason,
                        "event_summary": event.summary(),
                        "decision_id": decision_id,
                        "planned_qty": qty,
                        "planned_price": entry_referenz,
                        "created_at_ts": time.time(),
                        "asset_type": inst.asset_type,
                        "canonical_key": canonical_key(inst.name, inst.asset_type),
                        **_order_ownership_binding(),
                    }
                    if pulsar_context:
                        proposal = pulsar_context["proposal"]
                        pending_meta.update(entry_strategy_mode="PULSAR",
                            strategy_parameter_hash=proposal.get("plan_hash") or __import__("pulsar.control", fromlist=["digest"]).digest(proposal["plan"]),
                            strategy_parameters=proposal["plan"], pulsar_proposal_id=proposal["id"])
                    elif etoro_zusatz_mode and etoro_zusatz_snapshot:
                        # 10.2.0: unveraenderlicher Strategie-Snapshot der
                        # Zusatzstrategie wandert mit in Ledger und Ownership.
                        pending_meta.update(
                            entry_strategy_mode=etoro_zusatz_mode,
                            strategy_parameter_hash=etoro_zusatz_snapshot["parameter_hash"],
                            strategy_parameters=dict(etoro_zusatz_snapshot))
                    pending_key = f"PENDING_BUY:{canonical_key(inst.name, inst.asset_type)}"
                    order_meta[pending_key] = pending_meta
                    ownership_registry.register_pending(inst.name, pending_meta, asset_type=inst.asset_type)

                    # Order-Submit wird bei Transportfehlern NIEMALS blind
                    # wiederholt. Die Pending-Metadaten erlauben nach Reconnect,
                    # einen eventuell bereits beim Broker angekommenen Fill sauber
                    # zuzuordnen und anschliessend den Schutz zu verifizieren.
                    if str(getattr(broker, "name", "")).lower() == "etoro":
                        etoro_buy_submitted_this_cycle = True
                    execution = submit_protected_buy(
                        broker, inst, qty, entry_referenz, stop, take, decision_id=decision_id
                    )
                    ergebnis = execution.result
                    pulsar_core.result(pulsar_context, ergebnis)
                    if ergebnis is None:
                        raise RuntimeError("eToro-Orderpfad lieferte kein Ergebnis")
                    pending_meta["reference_id"] = str(getattr(ergebnis, "reference_id", "") or "")
                    pending_meta["position_ids"] = list(getattr(ergebnis, "position_ids", []) or [])

                    if inst.asset_type == "crypto":
                        if ergebnis.filled_quantity <= 0:
                            print(f"  -> {inst.name}: Kauf NICHT ausgefuehrt -- keine Schutzorders gesetzt.")
                        else:
                            # Krypto-Quote sofort mitzaehlen, damit ein zweiter
                            # Coin im selben Zyklus das Limit respektiert.
                            wert = ergebnis.filled_quantity * (ergebnis.avg_fill_price or signal.price)
                            krypto_quote += wert / equity if equity > 0 else 0

                    if not ergebnis.stop_order_platziert:
                        print(f"  -> Hinweis {inst.name}: kein Broker-Stop moeglich "
                              f"({ergebnis.hinweis or 'Broker unterstuetzt das hier nicht'})")
                    # 10.7.0: Der Stop ging mit der Order zum Broker. Liegt der
                    # tatsaechliche Einstieg trotzdem zu nah daran (Kurs zwischen
                    # Kaufkurs und Fill eingebrochen), wird das sofort sichtbar
                    # gemacht -- eine stille Wiederholung von CSCO darf es nicht
                    # geben. Der Stop selbst bleibt der gesendete; er wird nicht
                    # still verschoben.
                    fill_kurs = float(getattr(ergebnis, "avg_fill_price", 0) or 0)
                    if inst.asset_type == "stock" and fill_kurs > 0 and stop > 0:
                        abstand_pct = (fill_kurs - stop) / fill_kurs
                        min_pct = float(getattr(config, "ETORO_MIN_STOP_DISTANCE_PCT", 0.010) or 0.0)
                        if abstand_pct < min_pct * 0.5:
                            logger.warning("STOP %s: Einstieg %.4f liegt nur %.3f %% ueber dem Stop %.4f "
                                           "(Mindestabstand %.2f %%, Kaufkurs %.4f)", inst.name, fill_kurs,
                                           abstand_pct * 100, stop, min_pct * 100, entry_referenz)
                            notify(f"STOP ZU NAH: {inst.name}",
                                   f"Einstieg {fill_kurs:.4f} liegt nur {abstand_pct * 100:.3f} % ueber dem "
                                   f"Stop {stop:.4f} (Kaufkurs {entry_referenz:.4f}, Mindestabstand "
                                   f"{min_pct * 100:.2f} %). Der Stop bleibt wie gesendet; ein Stop-Out "
                                   "ist wahrscheinlich.")

                    for oid in ergebnis.order_ids:
                        order_meta[str(oid)] = dict(pending_meta)
                    ownership_registry.register_orders(ergebnis.order_ids, inst.name, pending_meta, asset_type=inst.asset_type)

                    risk.set_open_positions(int(risk.open_positions) + 1)
                    # Kurz warten, damit Fill(s) nach einer Market-Order direkt als
                    # tatsaechlicher Ausfuehrungspreis gemeldet werden koennen.
                    broker.warte(1.0)
                    capture_new_fills(
                        broker,
                        manager,
                        order_meta,
                        fill_tracker,
                        ownership_registry,
                        risk,
                        equity,
                        instrument_by_symbol,
                        notify_enabled=True,
                        tagesliste=trades_heute,
                    )
                except OrderStatusUnklar as exc:
                    pulsar_core.failed(pulsar_context, exc)
                    # Angenommen/unklar ist kein globaler Netzausfall und kein
                    # instrumentbezogener Fehler. Pending-Metadaten und
                    # Domaenensperre bleiben erhalten; der persistente eToro-
                    # Worker klaert referenceId/orderId -> positionId weiter.
                    logger.warning("eToro-Auftrag %s wird weiter abgeglichen: %s", inst.name, exc)
                    print(
                        f"  -> {inst.name}: ORDER ANGENOMMEN, STATUS NOCH OFFEN | "
                        "kein zweiter Kauf; automatischer Brokerabgleich laeuft"
                    )
                    continue
                except PulsarBlocked as exc:
                    journal_ablehnung(inst, "pulsar", str(exc))
                    continue
                except Exception as exc:
                    pulsar_core.failed(pulsar_context, exc)
                    # Ein bereits festgestellter kritischer Accountingfehler
                    # bleibt auch dann ein Accountingfehler, wenn der Broker
                    # inzwischen zusaetzlich offline ist. Er darf niemals als
                    # kandidatenspezifischer Transportfehler in ``break`` und
                    # spaetere BUY-Bereinigung umgedeutet werden.
                    if isinstance(exc, _CriticalFillAccountingError):
                        _cleanup_candidate_failure_state(
                            exc,
                            instrument=inst,
                            order_meta=order_meta,
                            ownership_registry=ownership_registry,
                            last_data_error=last_data_error,
                        )
                    if _connection_failure(broker, exc):
                        # Globaler Ausfall != Symbolfehler. Kein Instrument-Cooldown,
                        # kein zweiter Orderversuch; Reconnect + Pflicht-Resync.
                        _drop_transport(exc)
                        scanner_connection_failure = True
                        logger.warning("Globaler Broker-/Netzausfall bei %s: %s", inst.name, exc)
                        print(f"  -> {inst.name}: VERBINDUNG VERLOREN | kein Instrument-Cooldown | Zyklus pausiert")
                        break
                    _cleanup_candidate_failure_state(
                        exc,
                        instrument=inst,
                        order_meta=order_meta,
                        ownership_registry=ownership_registry,
                        last_data_error=last_data_error,
                    )
                    record_system_error(
                        decision_id=current_decision_id,
                        symbol=getattr(inst, "name", "?"),
                        asset_type=getattr(inst, "asset_type", "stock"),
                        broker=getattr(broker, "name", "etoro"),
                        gate="candidate_processing",
                        exc=exc,
                        paper=bool(getattr(config, "PAPER_TRADING", True)),
                    )
                    logger.exception("Fehler %s: %s", inst.name, exc)
                    print(
                        f"  -> {inst.name}: FEHLER ({exc}) | "
                        f"Cooldown {config.INSTRUMENT_COOLDOWN_MINUTES} min"
                    )
                if broker.is_connected():
                    broker.warte(config.PAUSE_BETWEEN_INSTRUMENTS)
                else:
                    scanner_connection_failure = True
                    break

            if scan_zyklus is not None:
                try:
                    from scan_uebersicht import speichern as _scan_speichern
                    _scan_speichern(scan_zyklus)
                except Exception:
                    logger.debug("Scan-Uebersicht nicht gespeichert", exc_info=True)
                scan_zyklus = None
            # 10.4.0: 15m-/Tageskerzen fuer Instrumente mit Position/Trade
            # (WebUI-Kerzenansicht); hoechstens 4 Abrufe je Zyklus, 15 min
            # Ruhe je Reihe, nur bei bestehender Verbindung.
            if broker.is_connected() and not scanner_connection_failure:
                try:
                    from etoro_chart_store import ergaenze_fuer_trades
                    ergaenze_fuer_trades(broker, instrument_by_symbol)
                except Exception:
                    logger.debug("Chartreihen fuer Trades nicht ergaenzt", exc_info=True)
                # 10.8.0: 15-Minuten-Kerzen fuer aktive PULSAR-Karten (<= 5 Reihen je
                # Zyklus, 15 min Ruhe je Reihe); PULSAR liest sie nur aus dem Speicher.
                try:
                    from etoro_chart_store import ergaenze_fuer_karten
                    ergaenze_fuer_karten(broker, pulsar_core.aktive_karten_instrumente(broker, instrument_by_symbol))
                except Exception:
                    logger.debug("Chartreihen fuer PULSAR-Karten nicht ergaenzt", exc_info=True)
            if scanner_connection_failure or resync_required:
                continue

            # Abschliessender Fill-/Positions-Check dieses Zyklus.
            if broker.is_connected():
                try:
                    capture_new_fills(
                        broker, manager, order_meta, fill_tracker, ownership_registry, risk, equity,
                        instrument_by_symbol, notify_enabled=True,
                    )
                    current_positions = manager.sync_with_broker(broker, universe)
                    risk.set_open_positions(sum(
                        1 for _, (_, qty, _, _) in current_positions.items() if qty != 0
                    ))

                    # Schutzorders zu Symbolen ohne Position vorsorglich entfernen.
                    aufgeraeumt = broker.verwaiste_orders_aufraeumen()
                    if aufgeraeumt:
                        print(f"{aufgeraeumt} verwaiste Schutzorder(s) storniert.")
                except Exception as exc:
                    if _connection_failure(broker, exc):
                        _drop_transport(exc)
                        print("Verbindung beim Zyklusabschluss verloren -- vor weiterer Aktivitaet folgt Reconnect/Resync.")
                        continue
                    logger.warning("Abschliessender Fill-/Positions-/Orderabgleich: %s", exc)

            # Gesammelte Trade-Meldungen dieses Zyklus als EINE Nachricht
            # verschicken (Details bleiben vollstaendig erhalten).
            offen = pending_trade_count()
            if offen:
                print(f"Sende {offen} Trade-Meldung(en) gebuendelt per Telegram ...")
                flush_trades(f"TRADES ZYKLUS {cycle}")

            # --- Decision Outcomes + 18-Uhr-Telegram-Bericht ---
            try:
                updated = update_decision_outcomes(
                    broker, instrument_by_symbol,
                    limit=int(getattr(config,"DECISION_OUTCOME_MAX_PER_CYCLE",6)),
                )
                if updated:
                    logger.info("Decision-Analytics: %d spaetere Kursbeobachtung(en) ergaenzt", updated)
            except Exception as exc:
                logger.debug("Decision-Outcomes konnten nicht aktualisiert werden: %s", exc)

            # Mehrere Zyklen ohne freigegebenen BUY sind nicht automatisch ein
            # Fehler. Nach konfigurierbarer Dauer informieren wir einmalig ueber
            # den dominanten Filter, damit ein stiller Totalausfall erkennbar ist.
            try:
                ds_now = decision_summary()
                if int(ds_now.get("approved",0) or 0) > approved_before_cycle:
                    no_buy_cycles = 0
                else:
                    no_buy_cycles += 1
                threshold=max(1,int(getattr(config,"NO_BUY_ALERT_CYCLES",6)))
                cooldown=max(3600.0,float(getattr(config,"NO_BUY_ALERT_COOLDOWN_HOURS",12))*3600.0)
                if (bool(getattr(config,"NO_BUY_ALERT_ENABLED",True)) and no_buy_cycles >= threshold
                        and time.time()-last_no_buy_alert >= cooldown):
                    reasons=sorted((ds_now.get("reasons") or {}).items(),key=lambda x:-x[1])
                    top=(reasons[0] if reasons else ("keine ernsthaften Kandidaten",0))
                    notify(
                        "SEIT MEHREREN ZYKLEN KEIN KAUF FREIGEGEBEN",
                        f"Seit {no_buy_cycles} Scannerzyklen wurde kein neuer BUY freigegeben.\n"
                        f"Heute protokolliert: {ds_now.get('candidates',0)} Kandidaten, "
                        f"{ds_now.get('approved',0)} freigegeben.\n"
                        f"Haeufigster Ablehnungsgrund: {top[0]} ({top[1]}x).\n\n"
                        "Das ist eine Informationsmeldung, kein Sicherheitsfehler. "
                        "Die Decision-Analyse zeigt spaeter, ob die Ablehnungen sinnvoll waren.",
                    )
                    last_no_buy_alert=time.time()
                    no_buy_cycles=0
            except Exception as exc:
                logger.debug("No-Buy-Statistik fehlgeschlagen: %s", exc)

            try:
                maybe_send_daily_report(
                    broker=broker, risk=risk, equity=equity, positions=_positionen_liste(),
                    cycles=cycle, ai=attention, runtime=dict(runtime.data),
                    news_health=news_source_health(), trades=list(trades_heute),
                )
            except Exception as exc:
                logger.warning("18-Uhr-Tagesbericht fehlgeschlagen: %s", exc)

            print(f"Zyklus fertig. Naechster Block in {config.CYCLE_MINUTES} min.")
            # v5.2: Die lange Zykluspause wird in kurze Abschnitte geteilt.
            # Dadurch bleiben Heartbeat und Fernzustand aktuell, statt dass ein
            # GUI-/Telegram-Stopp minutenlang unsichtbar bleibt.
            wait_total = (
                float(config.CYCLE_MINUTES) * 60.0
                if broker.is_connected()
                else float(getattr(config, "RECONNECT_INTERVAL_SECONDS", 30))
            )
            wait_until = time.time() + max(0.0, wait_total)
            while time.time() < wait_until and not shutdown_requested:
                if manual_scan_event.is_set():
                    print("Zusätzlicher Telegram-Scan angefordert – Zykluspause wird beendet.")
                    break
                # Auch waehrend der mehrminuetigen Zykluspause aktiv pruefen.
                # eToro hat keinen dauerhaften Socket; ohne echten REST-Ping
                # wuerde ein Internetausfall sonst erst im naechsten Zyklus
                # sichtbar. health_check() ist intern auf BROKER_HEALTHCHECK_SECONDS
                # gedrosselt, daher erzeugt das keine Anfrageflut.
                try:
                    if not ensure_connection(force_probe=False) or resync_required:
                        break
                    z_now = zustand.zustand()
                    runtime.update(
                        running=True,
                        broker=getattr(broker, "name", "?"),
                        mode="PAPER" if config.PAPER_TRADING else "LIVE",
                        state=z_now,
                        broker_connected=True,
                        connection_state="ONLINE",
                        last_broker_contact=(broker.last_contact() if hasattr(broker, "last_contact") else None),
                        reconnect_attempts=reconnect_attempts,
                        reconnect_attempts_current=reconnect_attempts,
                        reconnects_total=reconnects_total,
                        cycle=cycle,
                    )
                except AuthentifizierungsFehler:
                    raise
                except Exception as exc:
                    if _connection_failure(broker, exc):
                        _drop_transport(exc)
                        break
                    logger.debug("Heartbeat-/Healthcheck in Zykluspause fehlgeschlagen: %s", exc)
                try:
                    maybe_send_daily_report(
                        broker=broker, risk=risk, equity=equity, positions=_positionen_liste(),
                        cycles=cycle, ai=attention, runtime=dict(runtime.data),
                        news_health=news_source_health(), trades=list(trades_heute),
                    )
                except Exception as exc:
                    logger.debug("Tagesbericht-Pruefung in Zykluspause: %s", exc)
                broker.warte(min(5.0, max(0.0, wait_until - time.time())))

    except KeyboardInterrupt:
        shutdown_requested = True
        try:
            flush_trades("TRADES VOR BEENDEN")   # nichts unterschlagen
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        print("Manuell gestoppt.")
        notify("Trading-Bot gestoppt", "Der Bot wurde manuell beendet. Offene eToro-Orders/Positionen bleiben beim Broker bestehen.")
    except Exception as exc:
        shutdown_requested = True
        try:
            flush_trades("TRADES VOR ABSTURZ")   # nichts unterschlagen
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        logger.exception("Bot abgestuerzt: %s", exc)
        notify("Trading-Bot ABGESTUERZT", str(exc))
        raise
    finally:
        shutdown_requested = True
        if pulsar_worker:
            pulsar_worker.stop()
        try:
            import market_intelligence
            market_intelligence.stop_worker()
        except Exception:
            logger.debug("X-Worker-Abschluss fehlgeschlagen", exc_info=True)
        try:
            if telegram_steuerung: telegram_steuerung.stop()
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        try:
            if auto_maintenance: auto_maintenance.stop()
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        try: runtime.stop("live_trader beendet")
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        # 9.5.8: BEDINGUNGSLOS trennen. Bis 9.5.7 stand hier
        # ``if broker.is_connected()`` -- und genau im Absturzumfeld steht
        # ``_connected`` schon auf False, weil der Transport verloren ging.
        # ``disconnect()`` lief dann nicht, und Reconciliation-Thread,
        # WebSocket-Stream und die requests-Session blieben offen. Solange der
        # Kern danach endgueltig starb, war das folgenlos. Seit dem Neustart in
        # ``nexus_start.starte_aktien`` entsteht bei jedem Durchlauf eine neue
        # Brokerinstanz -- ohne diese Zeile summierten sich Threads und Sockets
        # bis an TasksMax, und daran wuerde auch die gesunde Kryptoseite sterben.
        try:
            broker.disconnect()
        except Exception:
            __import__("logging").getLogger(__name__).debug(
                "Trennen beim Beenden fehlgeschlagen", exc_info=True)


if __name__ == "__main__":
    import signal
    from instance_lock import SingleInstanceLock, InstanceAlreadyRunning

    # systemd und die Linux-GUI beenden Prozesse per SIGTERM. In Python wird
    # daraus bewusst KeyboardInterrupt, damit der bestehende sichere Cleanup-
    # Pfad (Trade-Flush, Runtime-Stop, Disconnect) vollstaendig durchlaeuft.
    def _graceful_term(_signum, _frame):
        raise KeyboardInterrupt

    try:
        signal.signal(signal.SIGTERM, _graceful_term)
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    lock_path = Path(__file__).with_name("tradingbot.instance.lock")
    try:
        with SingleInstanceLock(lock_path):
            run()
    except InstanceAlreadyRunning as exc:
        print(f"SICHERHEITSSTOPP: {exc}")
        raise SystemExit(75)
