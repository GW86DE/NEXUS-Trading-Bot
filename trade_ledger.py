"""Trade-Ledger: die Ausstiegsseite der Handelshistorie (v8.1.3, Etappe A).

WARUM DIESES MODUL EXISTIERT
============================
``decision_history.sqlite`` speicherte bis v8.1.2 nur die EINSTIEGSSEITE:
Fillpreis, Fillmenge, Ausfuehrungsstatus. Es gab kein realisiertes Ergebnis,
keine Gebuehren, keine Slippage, keine Haltedauer, keinen Ausstiegsgrund und
keine Strategieversion.

Damit waren genau die Kennzahlen, um die es in der Strategy Evolution Engine
geht, schlicht nicht berechenbar: Trefferquote, Erwartungswert, Profitfaktor,
Drawdown, Gebuehrenquote. Was es stattdessen gab, war Forward-Performance
(Kursbeobachtung 1 h / 4 h / 1 d / 5 d nach einer Entscheidung) -- ein
brauchbarer Stellvertreter fuer "haette sich der Einstieg gelohnt", aber kein
realisiertes Handelsergebnis.

KRITISCHE BUCHUNG
================
Bestaetigte Ausfuehrungen brauchen einen dauerhaften, eindeutig zugeordneten
Beleg. Geldpfad-Aufrufer verwenden critical=True: Fehler bleiben sichtbar und
sperren Wiederholungsorders bis zur Klaerung. Unbekannte Ergebnisse werden
nicht als Nullergebnis behandelt.

EHRLICHE LUECKEN
================
Nicht jeder Wert ist immer bekannt. Fehlt der Einstandspreis (etwa bei einer
Position, die vor v8.1.3 eroeffnet wurde), bleibt das Ergebnis ``NULL`` und
faellt aus der Statistik heraus. Es wird NIE als 0,00 verbucht -- eine Null
sieht aus wie ein Nullergebnis und verfaelscht jede Auswertung.
"""

from __future__ import annotations
from contextlib import closing

import json
import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from decision_analytics import _LOCK, _connect, _sample_quality, init_db
from ledger_result import confirmed_net, fees_confirmed, finite_number

logger = logging.getLogger(__name__)

# Ein Ausstieg ohne erkannten Grund bekommt diesen Platzhalter, damit die
# Auswertung "unbekannt" von "Stop-Loss" unterscheiden kann.
EXIT_UNBEKANNT = "UNBEKANNT"


def init_ledger() -> None:
    """Legt die Tabelle an. Mehrfach aufrufbar."""
    init_db()
    with _LOCK, closing(_connect()) as con, con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS trades (
                trade_id INTEGER PRIMARY KEY AUTOINCREMENT,
                decision_id INTEGER,
                link_status TEXT NOT NULL DEFAULT 'LEGACY_UNLINKED',
                enter_tag TEXT NOT NULL DEFAULT '',
                broker TEXT NOT NULL,
                asset_type TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL,
                waehrung TEXT NOT NULL DEFAULT '',
                strategie_version TEXT NOT NULL DEFAULT '',
                entry_strategy_mode TEXT NOT NULL DEFAULT '',
                strategy_parameter_hash TEXT NOT NULL DEFAULT '',
                strategy_parameters_json TEXT NOT NULL DEFAULT '{}',
                marktphase TEXT NOT NULL DEFAULT '',
                paper INTEGER NOT NULL DEFAULT 1,

                eingestiegen_am TEXT NOT NULL,
                einstieg_preis REAL,
                einstieg_referenz REAL,
                menge REAL,
                einstieg_gebuehr REAL,

                ausgestiegen_am TEXT,
                ausstieg_preis REAL,
                ausstieg_referenz REAL,
                exit_grund TEXT,

                brutto_pnl REAL,
                gebuehren REAL,
                fee_quality TEXT NOT NULL DEFAULT 'UNKNOWN',
                slippage_geschaetzt REAL,
                netto_pnl REAL,
                haltedauer_minuten REAL,
                mfe_pct REAL,
                mae_pct REAL,
                broker_position_id TEXT NOT NULL DEFAULT '',
                entry_order_id TEXT NOT NULL DEFAULT '',
                entry_fill_id TEXT NOT NULL DEFAULT '',
                entry_fill_ids_json TEXT NOT NULL DEFAULT '[]',
                client_order_id TEXT NOT NULL DEFAULT '',
                order_tag TEXT NOT NULL DEFAULT '',
                ownership_status TEXT NOT NULL DEFAULT '',
                entry_fee_currency_json TEXT NOT NULL DEFAULT '{}',
                broker_account_fingerprint TEXT NOT NULL DEFAULT '',
                reconciliation_status TEXT NOT NULL DEFAULT 'PENDING_CONFIRMATION',
                reconciliation_updated_at TEXT,
                protection_algo_id TEXT NOT NULL DEFAULT '',
                protection_client_order_id TEXT NOT NULL DEFAULT '',
                protection_status TEXT NOT NULL DEFAULT '',
                protection_detail TEXT NOT NULL DEFAULT '',
                exit_order_id TEXT NOT NULL DEFAULT '',
                exit_fill_ids_json TEXT NOT NULL DEFAULT '[]',
                notiz TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_trades_offen
                ON trades(broker, symbol, ausgestiegen_am);
            CREATE INDEX IF NOT EXISTS idx_trades_zeit
                ON trades(eingestiegen_am);
            CREATE INDEX IF NOT EXISTS idx_trades_version
                ON trades(strategie_version, broker);
            -- 9.5.3: Belegte Vorgaenger-Kontofingerprints.
            --
            -- Der Suchschluessel eines Trades enthaelt den Kontofingerprint.
            -- 9.5.1 hat das Fingerprint-Verfahren gewechselt, ohne das Ledger
            -- mitzunehmen. Seither existierten fuer dieselbe Brokerposition
            -- zwei Zeilen, die einander nicht sehen konnten -- ADBE wurde
            -- dadurch mit -344,76 USD zweimal verbucht.
            --
            -- Ein Alias entsteht ausschliesslich aus einem BROKERBELEG: der
            -- Order-Lookup gegen das aktuelle Konto hat die gespeicherte
            -- orderId bestaetigt. Niemals aus einer Namensregel.
            CREATE TABLE IF NOT EXISTS account_aliases (
                broker TEXT NOT NULL,
                alias_fingerprint TEXT NOT NULL,
                account_fingerprint TEXT NOT NULL,
                beleg TEXT NOT NULL DEFAULT '',
                erfasst_am_utc TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (broker, alias_fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_account_aliases_konto
                ON account_aliases(broker, account_fingerprint);
            CREATE TABLE IF NOT EXISTS trade_entry_fills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                broker TEXT NOT NULL,
                broker_account_fingerprint TEXT NOT NULL DEFAULT '',
                instrument TEXT NOT NULL DEFAULT '',
                order_id TEXT NOT NULL DEFAULT '',
                client_order_id TEXT NOT NULL DEFAULT '',
                fill_id TEXT NOT NULL,
                quantity REAL,
                price REAL,
                fee REAL,
                fee_currency TEXT NOT NULL DEFAULT '',
                filled_at TEXT,
                raw_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(trade_id) REFERENCES trades(trade_id)
            );
            CREATE INDEX IF NOT EXISTS idx_trade_entry_fills_trade
                ON trade_entry_fills(trade_id);
            CREATE TABLE IF NOT EXISTS trade_exit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                broker TEXT NOT NULL,
                broker_account_fingerprint TEXT NOT NULL DEFAULT '',
                instrument TEXT NOT NULL DEFAULT '',
                order_id TEXT NOT NULL DEFAULT '',
                fill_id TEXT NOT NULL DEFAULT '',
                event_id TEXT NOT NULL DEFAULT '',
                quantity REAL,
                price REAL,
                fee REAL,
                booked_at TEXT NOT NULL,
                FOREIGN KEY(trade_id) REFERENCES trades(trade_id)
            );
            """
        )
        con.execute("BEGIN IMMEDIATE")  # Serialize schema upgrades across core/UI/reader processes.
        columns = {str(r[1]) for r in con.execute("PRAGMA table_info(trades)").fetchall()}
        if "exit_native_json" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN exit_native_json TEXT NOT NULL DEFAULT '{}'")
        con.execute("""CREATE TABLE IF NOT EXISTS trade_native_exit_fills (
            broker TEXT NOT NULL, account TEXT NOT NULL, environment TEXT NOT NULL,
            fill_id TEXT NOT NULL, trade_id INTEGER NOT NULL,
            PRIMARY KEY(broker,account,environment,fill_id))""")
        # 9.8.8: A proven unsold remainder is inventory, never a sale.
        if "accounting_kind" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN accounting_kind TEXT NOT NULL DEFAULT 'TRADE'")
        if "entry_cost_basis" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN entry_cost_basis REAL")
        for quality in ("entry_fee_quality", "exit_fee_quality"):
            if quality not in columns:
                con.execute(f"ALTER TABLE trades ADD COLUMN {quality} TEXT NOT NULL DEFAULT 'UNKNOWN'")
        # Broker-reported history values have their own scope. They are not
        # substitutes for complete execution costs or our calculated net P&L.
        for name, definition in {
            "broker_reported_net_pnl": "REAL",
            "broker_reported_fees": "REAL",
            "broker_result_currency": "TEXT NOT NULL DEFAULT ''",
            "broker_result_quality": "TEXT NOT NULL DEFAULT 'UNOBSERVED'",
            "broker_result_receipt_hash": "TEXT NOT NULL DEFAULT ''",
            "broker_result_detail_json": "TEXT NOT NULL DEFAULT '{}'",
        }.items():
            if name not in columns:
                con.execute(f"ALTER TABLE trades ADD COLUMN {name} {definition}")
        con.execute("""CREATE TABLE IF NOT EXISTS okx_residual_inventory (
            trade_id INTEGER PRIMARY KEY, account TEXT NOT NULL, environment TEXT NOT NULL,
            instrument TEXT NOT NULL, entry_order_id TEXT NOT NULL,
            quantity TEXT NOT NULL, cost_basis TEXT NOT NULL, currency TEXT NOT NULL,
            separate_row INTEGER NOT NULL, proof_hash TEXT NOT NULL,
            original_json TEXT NOT NULL, proof_json TEXT NOT NULL, recorded_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS okx_entry_allocations (
            trade_id INTEGER PRIMARY KEY, cost_basis TEXT NOT NULL, entry_fee TEXT NOT NULL,
            original_json TEXT NOT NULL, proof_hash TEXT NOT NULL, proof_json TEXT NOT NULL,
            recorded_at TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS okx_accounting_incidents (
            incident_id TEXT PRIMARY KEY, account TEXT NOT NULL, environment TEXT NOT NULL,
            instrument TEXT NOT NULL, entry_order_id TEXT NOT NULL, status TEXT NOT NULL,
            original_json TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL)""")
        con.execute("""CREATE TABLE IF NOT EXISTS okx_balance_gaps (
            trade_id INTEGER PRIMARY KEY, account TEXT NOT NULL, environment TEXT NOT NULL,
            instrument TEXT NOT NULL, entry_order_id TEXT NOT NULL,
            tracked_quantity TEXT NOT NULL, observed_balance TEXT NOT NULL,
            status TEXT NOT NULL, first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
            resolution TEXT NOT NULL DEFAULT '')""")
        # 9.5.3: Eigene Spalte fuer die Ersetzung. link_status kommt dafuer
        # NICHT in Frage: init_ledger() schreibt ihn bei jedem Aufruf neu
        # (decision_id NULL -> LEGACY_UNLINKED, sonst LINKED). Ein dort
        # gesetzter Status waere beim naechsten Ledgerzugriff wieder weg.
        # superseded_by nennt ausserdem die Zeile, die uebernommen hat --
        # das ist die eigentlich nuetzliche Auditinformation.
        if "superseded_by" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN superseded_by INTEGER")
        if "link_status" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN link_status TEXT NOT NULL DEFAULT 'LEGACY_UNLINKED'")
        if "enter_tag" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN enter_tag TEXT NOT NULL DEFAULT ''")
        if "entry_strategy_mode" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN entry_strategy_mode TEXT NOT NULL DEFAULT ''")
        if "strategy_parameter_hash" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN strategy_parameter_hash TEXT NOT NULL DEFAULT ''")
        if "strategy_parameters_json" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN strategy_parameters_json TEXT NOT NULL DEFAULT '{}'")
        if "broker_position_id" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN broker_position_id TEXT NOT NULL DEFAULT ''")
        if "entry_order_id" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN entry_order_id TEXT NOT NULL DEFAULT ''")
        if "entry_fill_id" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN entry_fill_id TEXT NOT NULL DEFAULT ''")
        if "entry_fill_ids_json" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN entry_fill_ids_json TEXT NOT NULL DEFAULT '[]'")
        if "client_order_id" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN client_order_id TEXT NOT NULL DEFAULT ''")
        if "order_tag" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN order_tag TEXT NOT NULL DEFAULT ''")
        if "ownership_status" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN ownership_status TEXT NOT NULL DEFAULT ''")
        if "entry_fee_currency_json" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN entry_fee_currency_json TEXT NOT NULL DEFAULT '{}'")
        if "broker_account_fingerprint" not in columns:
            con.execute(
                "ALTER TABLE trades ADD COLUMN broker_account_fingerprint "
                "TEXT NOT NULL DEFAULT ''")
        if "reconciliation_status" not in columns:
            con.execute(
                "ALTER TABLE trades ADD COLUMN reconciliation_status "
                "TEXT NOT NULL DEFAULT 'PENDING_CONFIRMATION'")
        if "reconciliation_updated_at" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN reconciliation_updated_at TEXT")
        if "fee_quality" not in columns:
            con.execute("ALTER TABLE trades ADD COLUMN fee_quality TEXT NOT NULL DEFAULT 'UNKNOWN'")
        for name, definition in (
            ("protection_algo_id", "TEXT NOT NULL DEFAULT ''"),
            ("protection_client_order_id", "TEXT NOT NULL DEFAULT ''"),
            ("protection_status", "TEXT NOT NULL DEFAULT ''"),
            ("protection_detail", "TEXT NOT NULL DEFAULT ''"),
            ("exit_order_id", "TEXT NOT NULL DEFAULT ''"),
            ("exit_fill_ids_json", "TEXT NOT NULL DEFAULT '[]'"),
        ):
            if name not in columns:
                con.execute(f"ALTER TABLE trades ADD COLUMN {name} {definition}")
        fill_columns = {str(r[1]) for r in con.execute(
            "PRAGMA table_info(trade_entry_fills)").fetchall()}
        if "broker_account_fingerprint" not in fill_columns:
            con.execute("ALTER TABLE trade_entry_fills ADD COLUMN "
                        "broker_account_fingerprint TEXT NOT NULL DEFAULT ''")
        if "instrument" not in fill_columns:
            con.execute("ALTER TABLE trade_entry_fills ADD COLUMN "
                        "instrument TEXT NOT NULL DEFAULT ''")
        exit_columns = {str(r[1]) for r in con.execute(
            "PRAGMA table_info(trade_exit_events)").fetchall()}
        if "fee" not in exit_columns:
            # Nur die Ausstiegsgebuehr dieses Brokerereignisses. ``trades.gebuehren``
            # enthaelt zusaetzlich den anteiligen Einstieg und eignet sich deshalb
            # nicht fuer einen Replay-Konfliktvergleich.
            con.execute("ALTER TABLE trade_exit_events ADD COLUMN fee REAL")
        # Environment is inherited from the addressed trade, including legacy
        # writers. Trigger and unique index execute in the same transaction.
        for table in ("trade_entry_fills", "trade_exit_events"):
            names = {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})")}
            if "paper" not in names:
                con.execute(f"ALTER TABLE {table} ADD COLUMN paper INTEGER")
            con.execute(f"""UPDATE {table} SET paper=(SELECT paper FROM trades
                WHERE trades.trade_id={table}.trade_id) WHERE paper IS NULL""")
            con.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_environment_insert
                AFTER INSERT ON {table} BEGIN
                SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM trades WHERE trade_id=NEW.trade_id)
                    THEN RAISE(ABORT, 'Fill ohne Tradeidentitaet') END;
                SELECT CASE WHEN NEW.paper IS NOT NULL AND NEW.paper !=
                    (SELECT paper FROM trades WHERE trade_id=NEW.trade_id)
                    THEN RAISE(ABORT, 'Fill in falscher Umgebung') END;
                UPDATE {table} SET paper=(SELECT paper FROM trades WHERE trade_id=NEW.trade_id)
                    WHERE id=NEW.id;
                END""")
        for index in ("idx_trade_entry_fill_identity", "idx_trades_entry_order_identity",
                      "idx_trade_exit_event_identity"):
            old_index = con.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                                    (index,)).fetchone()
            if old_index and "paper" not in str(old_index[0]):
                con.execute(f"DROP INDEX {index}")
        # Legacy INSERT OR IGNORE writers omit paper. SQLite would otherwise
        # ignore the AFTER-trigger's unique UPDATE and leave a duplicate NULL
        # environment row. Resolve entry replays before the insertion itself.
        con.execute("""CREATE TRIGGER IF NOT EXISTS trade_entry_environment_replay
            BEFORE INSERT ON trade_entry_fills WHEN NEW.paper IS NULL BEGIN
            SELECT RAISE(IGNORE) WHERE EXISTS(
              SELECT 1 FROM trade_entry_fills e JOIN trades t ON t.trade_id=NEW.trade_id
              WHERE e.broker=NEW.broker AND e.broker_account_fingerprint=NEW.broker_account_fingerprint
                AND e.paper=t.paper AND e.instrument=NEW.instrument
                AND e.order_id=NEW.order_id AND e.fill_id=NEW.fill_id);
            END""")
        con.execute("""CREATE TRIGGER IF NOT EXISTS trade_exit_environment_replay
            BEFORE INSERT ON trade_exit_events WHEN NEW.paper IS NULL BEGIN
            SELECT RAISE(IGNORE) WHERE EXISTS(
              SELECT 1 FROM trade_exit_events e JOIN trades t ON t.trade_id=NEW.trade_id
              WHERE e.broker=NEW.broker AND e.broker_account_fingerprint=NEW.broker_account_fingerprint
                AND e.paper=t.paper AND e.instrument=NEW.instrument
                AND e.order_id=NEW.order_id AND e.fill_id=NEW.fill_id AND e.event_id=NEW.event_id);
            END""")
        con.execute(
            """UPDATE trade_entry_fills
               SET broker_account_fingerprint=COALESCE(
                       (SELECT t.broker_account_fingerprint FROM trades t
                        WHERE t.trade_id=trade_entry_fills.trade_id), ''),
                   instrument=COALESCE(
                       (SELECT t.broker_position_id FROM trades t
                        WHERE t.trade_id=trade_entry_fills.trade_id), '')
               WHERE broker_account_fingerprint='' OR instrument=''""")
        con.execute("DROP INDEX IF EXISTS idx_trade_entry_fill_exact")
        con.execute("DROP INDEX IF EXISTS idx_trades_entry_fill")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_entry_fill_identity "
            "ON trade_entry_fills(broker, broker_account_fingerprint, paper, instrument, "
            "order_id, fill_id)")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_entry_order_identity "
            "ON trades(broker, broker_account_fingerprint, paper, broker_position_id, "
            "entry_order_id) WHERE entry_order_id <> '' AND ausgestiegen_am IS NULL")
        con.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trade_exit_event_identity "
            "ON trade_exit_events(broker, broker_account_fingerprint, paper, instrument, "
            "order_id, fill_id, event_id)")
        # ``order_id`` ist Broker-Enrichment und kann beim ersten History-Poll
        # noch fehlen. Die kanonische Suche nutzt deshalb Fill/Event ohne Order;
        # diese nicht-eindeutigen Indizes halten die Migration bestehender
        # Datenbanken mit moeglichen 9.5-Dubletten bewusst blockierungsfrei.
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_exit_fill_anchor "
            "ON trade_exit_events(broker, broker_account_fingerprint, instrument, fill_id) "
            "WHERE fill_id <> ''")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_trade_exit_event_anchor "
            "ON trade_exit_events(broker, broker_account_fingerprint, instrument, event_id) "
            "WHERE event_id <> ''")
        # Altdaten werden absichtlich nicht geraten oder nachtraeglich zugeordnet.
        con.execute(
            "UPDATE trades SET link_status=CASE "
            "WHEN link_status='EXTERNAL' THEN 'EXTERNAL' "
            "ELSE 'LEGACY_UNLINKED' END WHERE decision_id IS NULL")
        con.execute("UPDATE trades SET link_status='LINKED' WHERE decision_id IS NOT NULL")
        con.execute(
            "UPDATE trades SET reconciliation_status='CLOSED', "
            "reconciliation_updated_at=COALESCE(reconciliation_updated_at, ausgestiegen_am) "
            "WHERE ausgestiegen_am IS NOT NULL "
            "AND reconciliation_status NOT IN "
            "('CLOSED', 'DISMISSED', 'ACCOUNT_ASSET_CONFIRMED')")


def _zahl(wert: Any) -> Optional[float]:
    """Zahl oder None -- niemals 0 als Ersatz fuer 'unbekannt'."""
    return finite_number(wert)


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


def _zeit(wert: Any) -> str:
    if isinstance(wert, datetime):
        d = wert if wert.tzinfo else wert.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat()
    text = str(wert or "").strip()
    if not text:
        return _jetzt()
    try:
        d = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).isoformat()
    except ValueError:
        return _jetzt()


def _version(vorgabe: str = "") -> str:
    if vorgabe:
        return str(vorgabe)
    try:
        import strategy_version
        return strategy_version.aktuell()
    except Exception:
        logger.debug("Strategieversion beim Schreiben nicht ermittelbar", exc_info=True)
        return ""


def _marktphase(vorgabe: str = "") -> str:
    """Die Marktphase zum Zeitpunkt des Einstiegs.

    Wird nichts uebergeben, wird sie NICHT geraten: eine falsch geratene
    Phase waere schlimmer als eine leere Spalte, weil sie eine Auswertung
    nach Marktphase still verfaelscht.
    """
    return str(vorgabe or "")


def _close_values_match(stored, incoming) -> bool:
    """Brokerwerte fuer einen bereits gebuchten Fill tolerant vergleichen.

    ``None`` in einem Altdatensatz bedeutet "damals nicht gespeichert" und ist
    kein Beweis fuer einen Konflikt. Sind beide Werte bekannt, darf ein Replay
    Preis, Menge oder Gebuehr jedoch nicht nachtraeglich umdeuten.
    """
    left, right = _zahl(stored), _zahl(incoming)
    if left is None or right is None:
        return True
    return abs(left - right) <= max(
        1e-12, max(abs(left), abs(right)) * 1e-9)


def _canonical_exit_event_rows(con, *, broker: str, account: str,
                               instrument: str, fill_ids: list[str],
                               event_id: str, paper: bool) -> list[dict]:
    """Exitereignisse ohne die nur optionale ``order_id`` finden.

    Die Kontodomane und positionId/Instrument-ID bleiben zwingender Teil der
    Identitaet. Fehlt einer dieser beiden Anker, wird bewusst nicht global nach
    einer vermeintlich eindeutigen Fill-ID geraten.
    """
    # eToro benoetigt zwingend die Kontodomaene. Bei OKX-Spot ist dagegen die
    # Kombination aus Broker, Instrument und echter Fill-/Event-ID bereits
    # der kanonische Brokeranker; Legacy-Positionen koennen noch keinen
    # Account-Fingerprint besitzen.
    if (not broker or not instrument
            or (str(broker).lower() == "etoro" and not account)):
        return []
    rows_by_id: dict[int, dict] = {}
    if fill_ids:
        marks = ",".join("?" for _ in fill_ids)
        rows = con.execute(
            """SELECT * FROM trade_exit_events
               WHERE broker=? AND broker_account_fingerprint=? AND instrument=?
                 AND paper=? AND fill_id IN (%s)
               ORDER BY id""" % marks,
            (broker, account, instrument, int(paper), *fill_ids),
        ).fetchall()
        rows_by_id.update((int(row["id"]), dict(row)) for row in rows)
    if event_id:
        rows = con.execute(
            """SELECT * FROM trade_exit_events
               WHERE broker=? AND broker_account_fingerprint=? AND instrument=?
                 AND paper=? AND event_id=?
               ORDER BY id""",
            (broker, account, instrument, int(paper), event_id),
        ).fetchall()
        rows_by_id.update((int(row["id"]), dict(row)) for row in rows)
    return [rows_by_id[key] for key in sorted(rows_by_id)]


def gebuchte_exit_fills(*, broker: str, account: str, instrument: str,
                        fill_ids: list[str], paper: bool = True) -> dict[str, int]:
    """10.8.1: ``{fill_id: trade_id}`` fuer bereits verbuchte Exit-Fills.

    Der Positionsabgleich fragt VOR einer Historienbuchung, ob ein
    Verkaufsfill schon einer anderen Ledgerzeile gehoert. Bis 10.8.0 fiel das
    erst in ``trade_close`` auf -- als ``LedgerZuordnungUnklar`` mit
    Traceback, in jedem Takt aufs Neue (ETH-Restzeile 86 gegen den Verkauf
    von Trade 90 am 19.09.2026). Reine Leseabfrage ueber denselben
    kanonischen Anker (Broker, Konto, Instrument, Umgebung, Fill-ID).
    """
    ids = [str(x) for x in (fill_ids or []) if str(x)]
    if not ids:
        return {}
    init_ledger()
    with _LOCK, closing(_connect()) as con:
        rows = _canonical_exit_event_rows(
            con, broker=str(broker or "").lower(), account=str(account or "")[:64],
            instrument=str(instrument or "")[:160], fill_ids=ids, event_id="",
            paper=bool(paper))
    return {str(r.get("fill_id") or ""): int(r.get("trade_id") or 0)
            for r in rows if str(r.get("fill_id") or "") and r.get("trade_id")}


class LedgerZuordnungUnklar(RuntimeError):
    """Der Verkauf ist echt, laesst sich aber keiner Ledgerzeile zuordnen.

    9.5.8. Das ist ein BUCHHALTUNGSbefund, kein Handelsfehler. Bis 9.5.7 warf
    ``trade_close`` in diesen Faellen ein nacktes ``RuntimeError``, der Aufrufer
    machte daraus einen ``_CriticalFillAccountingError``, und der beendete den
    gesamten Aktienkern -- am 04.09.2026 wegen einer einzigen KO-Meldung, deren
    Ledgerzeile bereits geschlossen war.

    Die Unterscheidung, die dabei fehlte:

      * FACHLICH UNKLAR (diese Klasse) -- die Daten reichen nicht, um den
        Verkauf eindeutig zuzuordnen. Ein Neuversuch aendert daran nichts, denn
        es kommen keine neuen Daten. Der Verkauf wird als unzugeordnet
        festgehalten und gemeldet; der Handel laeuft weiter.
      * TECHNISCH GESCHEITERT (weiterhin ``RuntimeError``) -- Datenbank gesperrt,
        Platte voll, paralleler Schreibzugriff. Ein Neuversuch kann gelingen,
        deshalb bleibt es fail-closed.

    Was diese Klasse ausdruecklich NICHT erlaubt: einen Trade zu raten. Es wird
    nach wie vor keine Zeile geschlossen, deren Zugehoerigkeit unbewiesen ist,
    und es entsteht kein Phantomtrade. Der Unterschied liegt allein darin, was
    der Aufrufer mit dem Befund macht.
    """


def _existing_exit_event_trade_id(
        con, *, broker: str, account: str, instrument: str,
        fill_ids: list[str], event_id: str, quantity, price, fee,
        requested_trade_id=None, paper: bool = True) -> Optional[int]:
    """Idempotenten Replay bestaetigen oder widerspruechliche Daten ablehnen.

    Eine Liste mehrerer Fill-IDs beschreibt im heutigen API-Vertrag nur eine
    aggregierte Menge/einen Durchschnittspreis. Ist davon lediglich ein Teil
    schon gebucht, kann die Menge nicht sicher auf alt und neu verteilt werden;
    dieser Fall muss deshalb fail-closed bleiben.
    """
    rows = _canonical_exit_event_rows(
        con, broker=broker, account=account, instrument=instrument,
        fill_ids=fill_ids, event_id=event_id, paper=paper)
    if not rows:
        return None

    if fill_ids:
        requested = set(fill_ids)
        matching = {
            str(row.get("fill_id") or "") for row in rows
            if str(row.get("fill_id") or "") in requested
        }
        # Ein Teil der angeforderten Fills war schon gebucht. Da der Aufrufer
        # nur aggregierte Menge/Preis/Gebuehr liefert, ist der neue Anteil nicht
        # verlustfrei abspaltbar.
        if matching and matching != requested:
            raise LedgerZuordnungUnklar(
                "Exit-Fill-Batch enthaelt bereits gebuchte und neue IDs; "
                "aggregierte Menge/Preis sind nicht sicher teilbar")
        # Derselbe Eventanker mit einer anderen Fill-Zusammensetzung ist kein
        # neues Ereignis. Auch hier darf der Ledger nicht ein zweites Mal auf
        # die Geldwerte wirken.
        if event_id:
            event_fills = {
                str(row.get("fill_id") or "") for row in rows
                if str(row.get("event_id") or "") == event_id
                and str(row.get("fill_id") or "")
            }
            if event_fills and event_fills != requested:
                raise LedgerZuordnungUnklar(
                    "Broker-Eventanker wurde mit abweichender Fill-Liste wiederholt")
        if not matching:
            raise LedgerZuordnungUnklar(
                "Broker-Eventanker wurde mit einer anderen Fill-Liste wiederholt")

    trade_ids = {int(row["trade_id"]) for row in rows}
    if len(trade_ids) != 1:
        raise LedgerZuordnungUnklar(
            "Derselbe Broker-Exitanker ist mehreren Ledgertrades zugeordnet")
    existing_trade_id = next(iter(trade_ids))
    if requested_trade_id not in (None, "") and int(requested_trade_id) != existing_trade_id:
        raise LedgerZuordnungUnklar(
            "Broker-Exitanker gehoert zu einer anderen expliziten trade_id")

    for row in rows:
        if not _close_values_match(row.get("quantity"), quantity):
            raise LedgerZuordnungUnklar(
                "Broker-Exitanker wurde mit abweichender Menge wiederholt")
        if not _close_values_match(row.get("price"), price):
            raise LedgerZuordnungUnklar(
                "Broker-Exitanker wurde mit abweichendem Preis wiederholt")
        if not _close_values_match(row.get("fee"), fee):
            raise LedgerZuordnungUnklar(
                "Broker-Exitanker wurde mit abweichender Gebuehr wiederholt")
    return existing_trade_id


def _corroborated_etoro_alias(con, *, broker: str, account: str,
                              instrument: str, entry_order: str,
                              exit_order: str, fill_ids: list[str], event_id: str,
                              quantity, price, fee, execution_time, paper: bool,
                              requested_trade_id=None) -> Optional[int]:
    """Resolve an alternative technical receipt, never by price/quantity alone.

    A canonical receipt and an alternative history/evidence receipt need the
    same account, position, entry order, environment and economics, plus an
    identical execution timestamp or an unambiguous closing order. Distinct
    genuine execution IDs are not aliases. All candidates are considered.
    """
    if broker != "etoro" or not (account and instrument and entry_order):
        return None
    incoming_ids = fill_ids or ([event_id] if event_id else [])
    if not incoming_ids or finite_number(quantity) is None or finite_number(price) is None:
        return None
    account_sql, account_values = _konto_platzhalter(broker, account, con=con)
    rows = con.execute(
        f"""SELECT * FROM trades WHERE broker=? AND {account_sql}
            AND broker_position_id=? AND entry_order_id=? AND paper=?
            AND ausgestiegen_am IS NOT NULL AND superseded_by IS NULL
            ORDER BY trade_id""",
        (broker, *account_values, instrument, entry_order, int(bool(paper))),
    ).fetchall()

    def timestamp(value):
        if not value:
            return None
        try:
            dt = value if isinstance(value, datetime) else datetime.fromisoformat(
                str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None

    def synthetic(identity):
        text = str(identity)
        return "evidence:" in text or text.startswith("etoro-close:history:")

    candidates = []
    new_time = timestamp(execution_time)
    for raw in rows:
        row = dict(raw)
        tid = int(row["trade_id"])
        if requested_trade_id not in (None, "") and tid != int(requested_trade_id):
            continue
        if any(finite_number(row.get(k)) is None for k in ("menge", "ausstieg_preis")):
            continue
        if not (_close_values_match(row["menge"], quantity)
                and _close_values_match(row["ausstieg_preis"], price)):
            continue
        receipts = [dict(x) for x in con.execute(
            "SELECT * FROM trade_exit_events WHERE trade_id=?", (tid,)).fetchall()]
        old_ids = [x.get("fill_id") or x.get("event_id") or "" for x in receipts]
        # We must have a booked receipt; a bare closed row is not enough.
        if not receipts or not (any(map(synthetic, incoming_ids)) or any(map(synthetic, old_ids))):
            continue
        old_order = str(row.get("exit_order_id") or "")
        if old_order == entry_order:  # legacy history used entry-order as close-order
            old_order = ""
        if old_order and exit_order and old_order != exit_order:
            continue
        old_time = timestamp(row.get("ausgestiegen_am"))
        same_time = (new_time is not None and old_time is not None
                     and abs(new_time - old_time) <= 0.000001)
        if execution_time not in (None, "") and not same_time:
            continue
        same_order = bool(old_order and exit_order and old_order == exit_order)
        if not (same_time or same_order):
            continue
        # Without a timestamp a multi-fill closing order cannot identify a fill.
        if not same_time and con.execute(
                "SELECT COUNT(*) FROM trades WHERE broker=? AND "
                "broker_account_fingerprint=? AND broker_position_id=? AND "
                "exit_order_id=? AND ausgestiegen_am IS NOT NULL AND superseded_by IS NULL",
                (broker, row["broker_account_fingerprint"], instrument, old_order),
        ).fetchone()[0] != 1:
            continue
        if any(not _close_values_match(x.get("fee"), fee) for x in receipts):
            raise LedgerZuordnungUnklar("Alternativer Exitbeleg widerspricht der gebuchten Gebuehr")
        candidates.append(tid)
    if len(candidates) > 1:
        raise LedgerZuordnungUnklar("Mehrere geschlossene Trades passen zum alternativen Exitbeleg")
    if not candidates:
        return None
    tid = candidates[0]
    for fid in fill_ids or [""]:
        con.execute(
            """INSERT OR IGNORE INTO trade_exit_events
               (trade_id, broker, broker_account_fingerprint, instrument, order_id,
                fill_id, event_id, quantity, price, fee, booked_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, broker, account, instrument, exit_order, fid, event_id,
             quantity, price, fee, _jetzt()))
    logger.info("Beleggestuetzter Exit-Alias %s/%s -> Trade %s", broker, instrument, tid)
    return tid


# ---------------------------------------------------------------------------
# Schreiben
# ---------------------------------------------------------------------------


def entry_lineage(broker: str, instrument: str, order_id: str, account: str, *, paper: bool) -> list[dict]:
    """All slices of one proven entry, not merely the first open symbol row."""
    init_ledger()
    with _LOCK, _connect() as con:
        return [dict(r) for r in con.execute(
            """SELECT * FROM trades WHERE broker=? AND broker_position_id=?
               AND entry_order_id=? AND broker_account_fingerprint=? AND paper=?
               AND superseded_by IS NULL ORDER BY trade_id""",
            (str(broker).lower(), str(instrument).upper(), str(order_id),
             str(account), 1 if paper else 0)).fetchall()]


def _merge_entry_fills(con, trade_id: int, *, broker_name: str, account_key: str,
                       instrument_key: str, entry_order_id: str,
                       client_order_id: str, entry_fill_id: str,
                       entry_fill_ids: list[str], entry_fills: list | None) -> None:
    """Project an unexited entry from immutable individual fills.

    Fees paid in base reduce inventory, not execution size. Raw OKX fees are
    signed (negative=charge). Missing/third-currency fee conversion stays unknown.
    A conflicting duplicate is an error rather than INSERT OR IGNORE data loss.
    """
    import math
    trade = dict(con.execute("SELECT * FROM trades WHERE trade_id=?", (trade_id,)).fetchone())
    previous = [dict(r) for r in con.execute(
        "SELECT * FROM trade_entry_fills WHERE trade_id=?", (trade_id,))]
    existing = {str(r["fill_id"]): r for r in previous}
    changed = False
    for raw in entry_fills or []:
        if not isinstance(raw, dict):
            raise LedgerZuordnungUnklar("Entry-Fill ist kein Objekt")
        fid = str(raw.get("tradeId") or raw.get("fill_id") or "").strip()
        qty = _zahl(raw.get("fillSz") if raw.get("fillSz") is not None else raw.get("quantity"))
        price = _zahl(raw.get("fillPx") if raw.get("fillPx") is not None else raw.get("price"))
        fee = _zahl(raw.get("fee"))
        ccy = str(raw.get("feeCcy") or raw.get("fee_currency") or "").upper()
        if not fid or qty is None or price is None or qty <= 0 or price <= 0:
            raise LedgerZuordnungUnklar("Entry-Fill ohne eindeutige ID oder positive Menge/Preis")
        for name, expected in (("ordId", entry_order_id), ("clOrdId", client_order_id),
                               ("instId", instrument_key)):
            if raw.get(name) and expected and str(raw[name]) != str(expected):
                raise LedgerZuordnungUnklar("Entry-Fill gehoert nicht zur angefragten Orderlineage")
        if fid in existing:
            old = existing[fid]
            if (not math.isclose(float(old["quantity"]), qty, rel_tol=1e-10, abs_tol=1e-12)
                    or not math.isclose(float(old["price"]), price, rel_tol=1e-10, abs_tol=1e-12)
                    or (fee is not None and old.get("fee") is not None
                        and not math.isclose(float(old["fee"]), fee, rel_tol=1e-10, abs_tol=1e-12))
                    or (ccy and old.get("fee_currency") and ccy != old["fee_currency"])):
                raise LedgerZuordnungUnklar("Widerspruechlicher doppelter Entry-Fill; keine Geldmutation")
            continue
        if not previous and float(trade.get("menge") or 0) > 0:
            raise LedgerZuordnungUnklar("Vorherige Entry-Fills fehlen; kein teilweiser Neuaufbau")
        if trade.get("ausgestiegen_am"):
            raise LedgerZuordnungUnklar("Geschlossener Trade darf nicht teilweise neu projiziert werden")
        con.execute(
            """INSERT INTO trade_entry_fills
               (trade_id,broker,broker_account_fingerprint,instrument,order_id,
                client_order_id,fill_id,quantity,price,fee,fee_currency,filled_at,raw_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (trade_id, broker_name, account_key, instrument_key, entry_order_id,
             client_order_id, fid, qty, price, fee, ccy,
             str(raw.get("ts") or raw.get("filled_at") or ""),
             json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)))
        existing[fid] = {"fill_id": fid, "quantity": qty, "price": price,
                         "fee": fee, "fee_currency": ccy}
        changed = True
    if not changed:
        return
    fills = list(existing.values())
    if any(_zahl(r.get("quantity")) is None or _zahl(r.get("price")) is None
           or float(r["quantity"]) <= 0 or float(r["price"]) <= 0 for r in fills):
        raise LedgerZuordnungUnklar("Unvollstaendige alte Entry-Fills; keine Teilprojektion")
    gross = math.fsum(float(r["quantity"]) for r in fills)
    value = math.fsum(float(r["quantity"]) * float(r["price"]) for r in fills)
    base = instrument_key.split("-", 1)[0]
    quote = str(trade.get("waehrung") or "").upper()
    fees = {}
    quote_fee = 0.0
    fees_known = True
    base_cost = 0.0
    for row in fills:
        fee = _zahl(row.get("fee"))
        ccy = str(row.get("fee_currency") or "").upper()
        if fee is None:
            fees_known = False
            continue
        cost = -fee if broker_name == "okx" else fee
        if broker_name != "okx" and cost < 0:
            raise LedgerZuordnungUnklar("Ungeklaertes Vorzeichen der Einstiegsgebuehr")
        if broker_name == "okx":
            if not ccy and cost:
                fees_known = False
            fees[ccy] = fees.get(ccy, 0.0) + cost
            if ccy == base:
                base_cost += cost
                quote_fee += cost * float(row["price"])
            elif ccy == quote or cost == 0:
                quote_fee += cost
            else:
                fees_known = False
        else:
            quote_fee += cost
    net = gross - base_cost
    if net <= 0:
        raise LedgerZuordnungUnklar("Entry-Gebuehr uebersteigt die ausgefuehrte Menge")
    ids = list(dict.fromkeys(json.loads(trade.get("entry_fill_ids_json") or "[]")
                            + list(entry_fill_ids or []) + list(existing)))
    con.execute(
        """UPDATE trades SET menge=?,einstieg_preis=?,einstieg_gebuehr=?,
             entry_fill_ids_json=?,entry_fee_currency_json=?,
             fee_quality=?,reconciliation_updated_at=? WHERE trade_id=?""",
        (net, value/gross, quote_fee if fees_known else None,
         json.dumps(ids), json.dumps(fees) if broker_name == "okx" else trade["entry_fee_currency_json"],
         "CONFIRMED" if fees_known else "UNKNOWN",
         _jetzt(), trade_id))


def _trade_open(*, broker: str, symbol: str, menge, einstieg_preis,
               asset_type: str = "", waehrung: str = "", decision_id=None,
               referenzpreis=None, gebuehr=None, paper: bool = True,
               strategie_version: str = "", marktphase: str = "",
               entry_strategy_mode: str = "", strategy_parameter_hash: str = "",
               strategy_parameters: dict | None = None,
               zeit=None, notiz: str = "", enter_tag: str = "",
               external: bool = False, broker_position_id: str = "",
               entry_order_id: str = "", entry_fill_id: str = "",
               entry_fill_ids: list | None = None, entry_fills: list | None = None,
               client_order_id: str = "", order_tag: str = "",
               ownership_status: str = "", entry_fee_by_currency: dict | None = None,
               broker_account_fingerprint: str = "",
               reconciliation_status: str = "CONFIRMED_OPEN",
               critical: bool = False) -> Optional[int]:
    """Einen Einstieg aufzeichnen. Gibt die trade_id zurueck oder None.

    ``referenzpreis`` ist der Kurs, mit dem die Entscheidung gerechnet hat.
    Aus der Differenz zum tatsaechlichen Fill entsteht spaeter die
    Slippage-Schaetzung -- ohne diesen Wert bleibt sie NULL.
    """
    try:
        init_ledger()
        if not decision_id and not external:
            logger.warning(
                "Neuer Trade ohne decision_id abgelehnt (%s %s). "
                "Manuelle/externe Bestaende muessen external=True tragen.",
                broker, symbol,
            )
            # KORREKTUR 9.5.5: Diese fachliche Ablehnung lag VOR der
            # critical-Behandlung. Ein Aufrufer, der ausdruecklich
            # critical=True verlangt hat, bekam trotzdem stumm None zurueck --
            # ein ausgefuehrter Kauf konnte damit ohne Ledgerzeile
            # weiterlaufen. Der Verkaufspfad behandelt genau das seit 9.1 als
            # kritisch; der Kaufpfad tat es nicht.
            if critical:
                raise RuntimeError(
                    f"Trade-Einstieg {broker} {symbol} ohne decision_id "
                    "abgelehnt, obwohl er als kritisch gefuehrt wird")
            return None
        preis = finite_number(einstieg_preis)
        anzahl = finite_number(menge)
        if preis is None or preis <= 0 or anzahl is None or anzahl <= 0:
            logger.warning(
                "Trade-Einstieg ohne belastbaren Preis/Menge nicht "
                "aufgezeichnet: %s (Preis %r, Menge %r)",
                symbol, einstieg_preis, menge)
            if critical:
                raise RuntimeError(
                    f"Trade-Einstieg {broker} {symbol} ohne Preis/Menge "
                    "abgelehnt, obwohl er als kritisch gefuehrt wird")
            return None
        with _LOCK, _connect() as con:
            con.execute("PRAGMA synchronous=FULL")
            con.execute("BEGIN IMMEDIATE")
            fill_ids = [str(x).strip() for x in (entry_fill_ids or []) if str(x).strip()]
            stable_fill = str(entry_fill_id or (fill_ids[0] if fill_ids else "")).strip()
            broker_name = str(broker or "").lower()
            account_key = str(broker_account_fingerprint or "")[:64]
            instrument_key = str(broker_position_id or symbol or "").upper()[:160]
            # 9.5.3: auch unter belegten Vorgaenger-Fingerprints suchen,
            # sonst legt ein Kontowechsel dieselbe Position ein zweites Mal an.
            konto_filter, konto_werte = _konto_platzhalter(broker_name, account_key, con=con)
            if str(entry_order_id or "").strip():
                # Freqtrade-orientierte Invariante: sobald eine Entry-Lineage
                # bereits einen Exit besitzt, darf ein spaeter eintreffender
                # neuer Entry-Fill nicht nur Menge/VWAP mutieren. Ohne komplette
                # Neuberechnung aller abgeleiteten Exit-/PnL-Werte bleibt der
                # Fall reconciliation-required. Bereits bekannte Replays sind
                # weiterhin idempotent.
                closed = con.execute(
                    f"""SELECT trade_id FROM trades
                        WHERE broker=? AND {konto_filter}
                          AND broker_position_id=? AND entry_order_id=? AND paper=?
                          AND ausgestiegen_am IS NOT NULL
                        ORDER BY trade_id DESC""",
                    (broker_name, *konto_werte, instrument_key,
                     str(entry_order_id or ""), 1 if paper else 0),
                ).fetchall()
                if closed:
                    closed_ids = [int(r[0]) for r in closed]
                    incoming = {str((r or {}).get("tradeId") or (r or {}).get("fill_id") or "").strip()
                                for r in (entry_fills or [])}
                    incoming.update(fill_ids)
                    if stable_fill:
                        incoming.add(stable_fill)
                    incoming.discard("")
                    if not incoming:
                        raise LedgerZuordnungUnklar(
                            "Geschlossene Entry-Lineage ohne Fill-Anker erneut gemeldet; "
                            "keine neue Position ohne Ausfuehrungsbeleg")
                    known = set()
                    for cid in closed_ids:
                        known.update(str(r[0]) for r in con.execute(
                            "SELECT fill_id FROM trade_entry_fills WHERE trade_id=? AND fill_id<>''",
                            (cid,)).fetchall())
                    for row in con.execute(
                            f"SELECT entry_fill_id,entry_fill_ids_json FROM trades WHERE trade_id IN ({','.join('?' for _ in closed_ids)})",
                            closed_ids):
                        if row[0]:
                            known.add(str(row[0]))
                        known.update(str(x) for x in json.loads(row[1] or "[]"))
                    if incoming and incoming.issubset(known):
                        # An ID alone is not permission to change an already
                        # booked execution's economic facts.
                        for incoming_row in (entry_fills or []):
                            fid = str(incoming_row.get("tradeId") or incoming_row.get("fill_id") or "")
                            matches = con.execute(
                                f"SELECT quantity,price,fee,fee_currency,order_id FROM trade_entry_fills WHERE trade_id IN ({','.join('?' for _ in closed_ids)}) AND fill_id=?",
                                (*closed_ids, fid)).fetchall()
                            if not matches:
                                raise LedgerZuordnungUnklar("Entry-Replay ohne passenden Einzelbeleg")
                            for old in matches:
                                q = _zahl(incoming_row.get("fillSz") or incoming_row.get("quantity"))
                                px = _zahl(incoming_row.get("fillPx") or incoming_row.get("price"))
                                fee = _zahl(incoming_row.get("fee"))
                                if (q is None or px is None
                                        or not math.isclose(q, float(old[0]), rel_tol=1e-9, abs_tol=1e-10)
                                        or not math.isclose(px, float(old[1]), rel_tol=1e-9, abs_tol=1e-10)
                                        or (fee is not None and old[2] is not None and not math.isclose(fee, float(old[2]), rel_tol=1e-9, abs_tol=1e-10))
                                        or (incoming_row.get("ordId") and str(incoming_row["ordId"]) != str(old[4]))
                                        or (incoming_row.get("feeCcy") and str(incoming_row["feeCcy"]) != str(old[3]))):
                                    raise LedgerZuordnungUnklar("Entry-Replay widerspricht dauerhaftem Fill")
                        _confirm_execution_accounting_on(con, closed_ids[0], "BUY",
                            order_id=entry_order_id, client_id=client_order_id)
                        return closed_ids[0]
                    if incoming:
                        raise LedgerZuordnungUnklar(
                            "Nachgemeldeter neuer Einstiegsfill fuer bereits verkaufte Entry-Lineage; "
                            "keine Teilprojektion ohne vollstaendige Neuberechnung")
            if str(entry_order_id or "").strip():
                existing = con.execute(
                    f"""SELECT trade_id FROM trades
                        WHERE broker=? AND {konto_filter}
                          AND broker_position_id=? AND entry_order_id=? AND paper=?
                          AND ausgestiegen_am IS NULL
                        ORDER BY trade_id DESC LIMIT 1""",
                    (broker_name, *konto_werte, instrument_key,
                     str(entry_order_id or ""), 1 if paper else 0),
                ).fetchone()
                if existing:
                    existing_id = int(existing[0])
                    _merge_entry_fills(con, existing_id, broker_name=broker_name,
                        account_key=account_key, instrument_key=instrument_key,
                        entry_order_id=str(entry_order_id or ""),
                        client_order_id=str(client_order_id or ""),
                        entry_fill_id=stable_fill, entry_fill_ids=fill_ids,
                        entry_fills=entry_fills)
                    _confirm_execution_accounting_on(con, existing_id, "BUY",
                        order_id=entry_order_id, client_id=client_order_id)
                    con.commit()
                    return existing_id
            if stable_fill or fill_ids:
                wanted = fill_ids or [stable_fill]
                existing = con.execute(
                    """SELECT t.trade_id FROM trades t
                       LEFT JOIN trade_entry_fills f ON f.trade_id=t.trade_id
                       WHERE t.broker=? AND t.{konto} AND t.paper=?
                         AND t.broker_position_id=? AND t.ausgestiegen_am IS NULL
                         AND (t.entry_fill_id=? OR
                              (f.instrument=? AND f.fill_id IN ({marken})))
                       ORDER BY t.trade_id DESC LIMIT 1""".format(
                        konto=konto_filter, marken=",".join("?" for _ in wanted)),
                    (broker_name, *konto_werte, int(paper), instrument_key, stable_fill,
                     instrument_key, *wanted),
                ).fetchone()
                if existing:
                    existing_id = int(existing[0])
                    _merge_entry_fills(con, existing_id, broker_name=broker_name,
                        account_key=account_key, instrument_key=instrument_key,
                        entry_order_id=str(entry_order_id or ""),
                        client_order_id=str(client_order_id or ""),
                        entry_fill_id=stable_fill, entry_fill_ids=fill_ids,
                        entry_fills=entry_fills)
                    _confirm_execution_accounting_on(con, existing_id, "BUY",
                        order_id=entry_order_id, client_id=client_order_id)
                    con.commit()
                    return existing_id
            status = ("EXTERNAL_OBSERVE" if external else
                      str(reconciliation_status or "CONFIRMED_OPEN").upper())
            cur = con.execute(
                """INSERT INTO trades
                   (decision_id, link_status, enter_tag, broker, asset_type, symbol, waehrung, strategie_version,
                    entry_strategy_mode, strategy_parameter_hash, strategy_parameters_json,
                    marktphase, paper, eingestiegen_am, einstieg_preis, einstieg_referenz,
                    menge, einstieg_gebuehr, broker_position_id, entry_order_id,
                    entry_fill_id, entry_fill_ids_json, client_order_id, order_tag,
                    ownership_status, entry_fee_currency_json,
                    broker_account_fingerprint, reconciliation_status,
                    reconciliation_updated_at, notiz)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (int(decision_id) if decision_id else None,
                 "LINKED" if decision_id else "EXTERNAL",
                 str(enter_tag or "")[:160],
                 broker_name, str(asset_type or ""), str(symbol or "").upper(),
                 str(waehrung or ""), _version(strategie_version),
                 str(entry_strategy_mode or ""), str(strategy_parameter_hash or ""),
                 __import__("json").dumps(strategy_parameters or {}, ensure_ascii=False, sort_keys=True),
                 _marktphase(marktphase),
                 1 if paper else 0, _zeit(zeit), preis, _zahl(referenzpreis),
                 anzahl, _zahl(gebuehr), str(broker_position_id or "")[:160],
                 str(entry_order_id or "")[:160], stable_fill[:240],
                 json.dumps(fill_ids, ensure_ascii=False),
                 str(client_order_id or "")[:80], str(order_tag or "")[:16],
                 str(ownership_status or "")[:60],
                 json.dumps(entry_fee_by_currency or {}, ensure_ascii=False, sort_keys=True),
                 account_key, status,
                 _jetzt(), str(notiz or "")[:300]),
            )
            trade_id = int(cur.lastrowid)
            con.execute("UPDATE trades SET fee_quality=? WHERE trade_id=?",
                        ("CONFIRMED" if gebuehr is not None else "UNKNOWN", trade_id))
            for row in entry_fills or []:
                fill_id = str((row or {}).get("tradeId") or (row or {}).get("fill_id") or "").strip()
                if not fill_id:
                    continue
                con.execute(
                    """INSERT OR IGNORE INTO trade_entry_fills
                       (trade_id, broker, broker_account_fingerprint, instrument,
                        order_id, client_order_id, fill_id,
                        quantity, price, fee, fee_currency, filled_at, raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (trade_id, broker_name, account_key, instrument_key,
                     str((row or {}).get("ordId") or entry_order_id or "")[:160],
                     str((row or {}).get("clOrdId") or client_order_id or "")[:80],
                     fill_id[:240], _zahl((row or {}).get("fillSz") or (row or {}).get("quantity")),
                     _zahl((row or {}).get("fillPx") or (row or {}).get("price")),
                     _zahl((row or {}).get("fee")),
                     # 10.7.1: OKX-Fills tragen 'feeCcy', eToro-Belege 'fee_currency'.
                     # Bis 10.7.0 wurde die eToro-Gebuehr MIT Betrag, aber OHNE
                     # Waehrung gespeichert; der spaetere Kostenabgleich las das
                     # als Widerspruch (CSCO, Trade 88).
                     str((row or {}).get("feeCcy") or (row or {}).get("fee_currency") or "").upper()[:16],
                     str((row or {}).get("ts") or (row or {}).get("filled_at") or "")[:60],
                     json.dumps(row or {}, ensure_ascii=False, sort_keys=True, default=str)),
                )
            _confirm_execution_accounting_on(con, trade_id, "BUY",
                order_id=entry_order_id, client_id=client_order_id)
            return trade_id
    except LedgerZuordnungUnklar:
        # Fachlich unaufloesbare Lineage bleibt als eigener Typ erkennbar. Der
        # Aufrufer kann reconciliation-required setzen, statt einen technischen
        # Datenbankfehler oder einen zweiten Kauf anzunehmen.
        raise
    except Exception as exc:
        logger.warning("Trade-Einstieg nicht aufgezeichnet (%s %s): %s", broker, symbol, exc)
        if critical:
            raise RuntimeError(
                f"Kritischer Ledger-Einstieg fehlgeschlagen ({broker} {symbol})") from exc
        return None


def gehandelte_symbole(grenze: int = 500) -> list[dict]:
    """Symbole, die im Handelsbuch vorkommen -- offen wie geschlossen.

    9.5.6. Das Logbuch zeigt Vergangenheit: ein Wert, der heute nicht mehr
    gefuehrt wird (ZAMA, BNB), soll dort genauso hervorgehoben werden wie ein
    aktueller. Nur lesend, mit Obergrenze.
    """
    try:
        init_ledger()
        with _LOCK, _connect() as con:
            zeilen = con.execute(
                "SELECT DISTINCT symbol FROM trades "
                "WHERE symbol IS NOT NULL AND symbol != '' "
                "ORDER BY trade_id DESC LIMIT ?", (int(grenze),)).fetchall()
        return [{"symbol": str(z[0])} for z in zeilen]
    except Exception:
        logger.debug("Gehandelte Symbole nicht lesbar", exc_info=True)
        return []


def offener_trade(broker: str, symbol: str, *, trade_id=None,
                  broker_position_id: str = "",
                  broker_account_fingerprint: str = "",
                  entry_order_id: str = "", paper: bool | None = None) -> Optional[dict]:
    """Einen eindeutig adressierten offenen Trade liefern.

    Der alte Symbol-Fallback bleibt fuer genau *einen* Treffer kompatibel.
    Bei mehreren gleichnamigen Positionen wird bewusst nichts geraten.
    """
    try:
        init_ledger()
        with _LOCK, _connect() as con:
            zeilen = _open_trade_rows(
                con, broker=broker, symbol=symbol, trade_id=trade_id,
                broker_position_id=broker_position_id,
                broker_account_fingerprint=broker_account_fingerprint,
                entry_order_id=entry_order_id, paper=paper)
        if len(zeilen) > 1:
            logger.error(
                "Ledger-Zuordnung mehrdeutig (%s %s): exakte trade_id/positionId fehlt",
                broker, symbol)
            return None
        return dict(zeilen[0]) if zeilen else None
    except Exception as exc:
        logger.debug("Offener Trade nicht ermittelbar (%s %s): %s", broker, symbol, exc)
        return None


def _open_trade_rows(con, *, broker: str, symbol: str, trade_id=None,
                     broker_position_id: str = "",
                     broker_account_fingerprint: str = "",
                     entry_order_id: str = "", paper: bool | None = None):
    """Offene Ledgerzeilen auf einer bereits bestehenden DB-Transaktion.

    ``trade_close`` darf fuer die Aufloesung nicht die oeffentliche Funktion
    aufrufen: deren eigene Verbindung wuerde Replaypruefung, Suche und einen
    eventuellen Fallback wieder in getrennte Transaktionen zerlegen.
    """
    clauses = ["broker=?", "symbol=?", "ausgestiegen_am IS NULL",
               "reconciliation_status NOT IN ('DISMISSED', 'ACCOUNT_ASSET_CONFIRMED')"]
    params: list[Any] = [str(broker or "").lower(), str(symbol or "").upper()]
    if broker == "okx" and not broker_account_fingerprint and trade_id in (None, ""):
        from okx_account_context import read_context
        context = read_context()
        if context:
            broker_account_fingerprint = context["account"]
            paper = context["environment"] == "DEMO"
    if paper is not None:
        clauses.append("paper=?")
        params.append(int(paper))
    if trade_id not in (None, ""):
        clauses.append("trade_id=?")
        params.append(int(trade_id))
    if str(broker_position_id or ""):
        clauses.append("broker_position_id=?")
        params.append(str(broker_position_id))
    if str(broker_account_fingerprint or ""):
        konto_filter, konto_werte = _konto_platzhalter(
            str(broker or "").lower(), str(broker_account_fingerprint)[:64], con=con)
        clauses.append(konto_filter)
        params.extend(konto_werte)
    if str(entry_order_id or ""):
        clauses.append("entry_order_id=?")
        params.append(str(entry_order_id))
    clauses.append("superseded_by IS NULL")
    return con.execute(
        "SELECT * FROM trades WHERE " + " AND ".join(clauses) +
        " ORDER BY eingestiegen_am ASC LIMIT 2", tuple(params)).fetchall()


def _trade_close(*, broker: str, symbol: str, ausstieg_preis, menge=None,
                exit_grund: str = "", gebuehr=None, referenzpreis=None,
                einstieg_preis=None, eingestiegen_am=None, netto_pnl=None,
                asset_type: str = "", waehrung: str = "", paper: bool = True,
                mfe_pct=None, mae_pct=None, zeit=None,
                exit_order_id: str = "", exit_fill_ids: list | None = None,
                event_id: str = "", trade_id=None,
                broker_position_id: str = "",
                broker_account_fingerprint: str = "",
                entry_order_id: str = "",
                notiz: str = "", critical: bool = False) -> Optional[int]:
    """Einen Ausstieg aufzeichnen. Gibt die trade_id zurueck oder None.

    Findet sich kein offener Trade -- etwa weil die Position vor v8.1.3
    eroeffnet wurde --, wird der Trade nachtraeglich angelegt, sofern
    Einstandspreis und Einstiegszeit uebergeben werden. Ohne Einstand bleibt
    das Ergebnis NULL statt 0.

    Bei einem Teilverkauf bleibt der Rest als offener Trade stehen.
    """
    try:
        init_ledger()
        exit_preis = finite_number(ausstieg_preis)
        if exit_preis is None or exit_preis <= 0:
            logger.debug("Trade-Ausstieg ohne Preis nicht aufgezeichnet: %s", symbol)
            if critical:
                raise LedgerZuordnungUnklar("Broker-Fill besitzt keinen belastbaren Ausstiegspreis")
            return None
        requested_quantity = finite_number(menge)
        if requested_quantity is None or requested_quantity <= 0:
            raise LedgerZuordnungUnklar("Verkaufsfill ohne positive bestaetigte Menge")
        broker_name = str(broker or "").lower()
        symbol_name = str(symbol or "").upper()
        account_key = str(broker_account_fingerprint or "")[:64]
        instrument_key = str(broker_position_id or "")[:160]
        exit_order_key = str(exit_order_id or "")[:160]
        # Reihenfolge behalten, Dubletten aber schon am API-Rand entfernen.
        exit_ids = list(dict.fromkeys(
            str(x).strip()[:240] for x in (exit_fill_ids or []) if str(x).strip()))
        event_key = str(event_id or "")[:240]
        exit_fee = _zahl(gebuehr)
        requested_trade_id = trade_id
        entry_order_key = str(entry_order_id or "")[:160]

        with _LOCK, _connect() as con:
            # Dieser BEGIN muss VOR dem definitiven Replay-Check liegen. Nur so
            # bilden Exitbeleg, offene Suche, eventueller Legacy-Fallback,
            # Geldwerte und Exitquittung auch gegen einen zweiten Prozess eine
            # einzige serialisierte Zustandsaenderung. Ein frueher SELECT auf
            # einer anderen Verbindung liess sonst genau zwischen Check und
            # Fallback einen bereits gebuchten Fill zum Phantomtrade werden.
            con.execute("PRAGMA synchronous=FULL")
            con.execute("BEGIN IMMEDIATE")

            if requested_trade_id not in (None, ""):
                target = con.execute("SELECT * FROM trades WHERE trade_id=?",
                                     (int(requested_trade_id),)).fetchone()
                if target is not None and (target['broker'] != broker_name
                        or target['symbol'] != symbol_name or bool(target['paper']) != bool(paper)
                        or (instrument_key and target['broker_position_id'] != instrument_key)
                        or (entry_order_key and target['entry_order_id'] != entry_order_key)
                        or (account_key and target['broker_account_fingerprint'] not in
                            konto_identitaeten(broker_name, account_key, con=con))):
                    raise LedgerZuordnungUnklar("Explizite Trade-ID gehoert zu anderer Kontodomaene oder Entry-Lineage")

            identity_account = account_key
            identity_instrument = instrument_key
            if ((exit_ids or event_key)
                    and requested_trade_id not in (None, "")
                    and (not identity_account or not identity_instrument)):
                identity_row = con.execute(
                    "SELECT broker_account_fingerprint, broker_position_id "
                    "FROM trades WHERE trade_id=? LIMIT 1",
                    (int(requested_trade_id),),
                ).fetchone()
                if identity_row:
                    identity_account = identity_account or str(identity_row[0] or "")[:64]
                    identity_instrument = identity_instrument or str(identity_row[1] or "")[:160]

            if exit_ids or event_key:
                replay_trade_id = _existing_exit_event_trade_id(
                    con, broker=broker_name, account=identity_account,
                    instrument=identity_instrument, fill_ids=exit_ids,
                    event_id=event_key, quantity=_zahl(menge),
                    price=exit_preis, fee=exit_fee,
                    requested_trade_id=requested_trade_id, paper=paper)
                if replay_trade_id is not None:
                    _confirm_execution_accounting_on(con, replay_trade_id, "SELL",
                        order_id=exit_order_key)
                    con.commit()
                    return replay_trade_id

            # An alternative receipt may arrive while a partial-sale remainder
            # is still open. Resolve proven replays before touching that remainder.
            alias_trade_id = _corroborated_etoro_alias(
                con, broker=broker_name, account=identity_account,
                instrument=identity_instrument, entry_order=entry_order_key,
                exit_order=exit_order_key, fill_ids=exit_ids, event_id=event_key,
                quantity=_zahl(menge), price=exit_preis, fee=exit_fee,
                execution_time=zeit, paper=paper, requested_trade_id=requested_trade_id)
            if alias_trade_id is not None:
                _confirm_execution_accounting_on(con, alias_trade_id, "SELL",
                    order_id=exit_order_key)
                con.commit()
                return alias_trade_id

            zeilen = _open_trade_rows(
                con, broker=broker_name, symbol=symbol_name,
                trade_id=requested_trade_id,
                broker_position_id=instrument_key,
                broker_account_fingerprint=account_key,
                entry_order_id=entry_order_key, paper=paper)
            if len(zeilen) > 1:
                con.rollback()
                logger.error(
                    "Ledger-Zuordnung mehrdeutig (%s %s): exakte trade_id/positionId fehlt",
                    broker_name, symbol_name)
                if critical:
                    raise LedgerZuordnungUnklar(
                        "Mehrere offene Ledgertrades passen zum Broker-Fill")
                return None
            offen = dict(zeilen[0]) if zeilen else None

            if offen is None:
                # Eine explizite trade_id darf niemals still durch einen neuen
                # Ersatztrade ersetzt werden. Ist sie nicht offen und gab es
                # oben keinen identischen Exitbeleg, ist der Zustand ungeklärt.
                if requested_trade_id not in (None, ""):
                    con.rollback()
                    logger.error(
                        "Verkauf %s nicht verbucht: trade_id %s ist nicht offen",
                        symbol_name, requested_trade_id)
                    if critical:
                        raise LedgerZuordnungUnklar(
                            "Explizite Ledger-trade_id ist nicht offen und besitzt "
                            "keinen identischen Exitbeleg")
                    return None

                # Besteht bereits irgendein offener Symboltreffer, ist die Lage
                # mehrdeutig. Dann niemals einen Phantomtrade anlegen oder den
                # aeltesten Eintrag schliessen.
                count = int(con.execute(
                    "SELECT COUNT(*) FROM trades WHERE broker=? AND symbol=? "
                    "AND ausgestiegen_am IS NULL",
                    (broker_name, symbol_name)).fetchone()[0])
                if count:
                    con.rollback()
                    logger.error(
                        "Verkauf %s nicht verbucht: exakte Ledger-ID fehlt",
                        symbol_name)
                    if critical:
                        raise LedgerZuordnungUnklar(
                            "Offener Symboltrade vorhanden, aber positionId/Konto/Entry-ID "
                            "des Broker-Fills passen nicht eindeutig")
                    return None

                # eToro benoetigt Konto + positionId, weil dieselbe lokale
                # Installation mehrere eToro-Konten/Domaenen abgleichen kann.
                # OKX-Spot besitzt dagegen keine brokerseitige positionId;
                # dort bilden instrumentId + echte Fill-/Event-ID den stabilen
                # Replayanker. Ein leerer Legacy-Kontofingerabdruck darf diesen
                # bereits broker-eindeutigen OKX-Fill nicht unverarbeitbar
                # machen.
                identity_incomplete = (
                    not identity_instrument
                    or (broker_name == "etoro" and not identity_account))
                if ((exit_ids or event_key) and identity_incomplete):
                    con.rollback()
                    logger.error(
                        "Verkauf %s nicht verbucht: kanonische Brokeridentitaet "
                        "fuer Exitanker fehlt", symbol_name)
                    if critical:
                        raise LedgerZuordnungUnklar(
                            "Stabiler Broker-Exitanker ohne kanonische Identitaet")
                    return None

                # Alte Schliessungen ohne Exit-Event duerfen bei einer exakten
                # Entry-Lineage ebenfalls keinen zweiten Finanztrade erzeugen.
                if ((exit_ids or event_key) and identity_account
                        and identity_instrument and entry_order_key):
                    schliess_filter, schliess_werte = _konto_platzhalter(
                        broker_name, identity_account, con=con)
                    existing_closed = con.execute(
                        f"""SELECT * FROM trades
                            WHERE broker=? AND {schliess_filter}
                              AND broker_position_id=? AND entry_order_id=? AND paper=?
                              AND ausgestiegen_am IS NOT NULL
                            ORDER BY trade_id LIMIT 1""",
                        (broker_name, *schliess_werte, identity_instrument,
                         entry_order_key, int(paper)),
                    ).fetchone()
                    if existing_closed:
                        con.rollback()
                        if critical:
                            raise LedgerZuordnungUnklar(
                                "Geschlossene exakte Entry-Lineage besitzt keinen "
                                "eindeutig belegten identischen Exit")
                        return None

                # Nachtraegliche Anlage bleibt fuer echte Altpositionen
                # moeglich, findet aber in genau derselben Schreibtransaktion
                # wie Replaypruefung und Schliessung statt.
                fallback_price = _zahl(einstieg_preis)
                fallback_quantity = _zahl(menge)
                if (not fallback_price or not fallback_quantity
                        or fallback_quantity <= 0):
                    con.rollback()
                    logger.info(
                        "Verkauf %s ohne bekannten Einstand -- kein Ledger-Eintrag.",
                        symbol_name)
                    if critical:
                        raise LedgerZuordnungUnklar(
                            "Bestaetigter Bot-Verkauf konnte nicht als Ledgertrade "
                            "angelegt werden")
                    return None
                cur = con.execute(
                    """INSERT INTO trades
                       (decision_id, link_status, enter_tag, broker, asset_type,
                        symbol, waehrung, strategie_version, entry_strategy_mode,
                        strategy_parameter_hash, strategy_parameters_json,
                        marktphase, paper, eingestiegen_am, einstieg_preis,
                        einstieg_referenz, menge, einstieg_gebuehr,
                        broker_position_id, entry_order_id, entry_fill_id,
                        entry_fill_ids_json, client_order_id, order_tag,
                        ownership_status, entry_fee_currency_json,
                        broker_account_fingerprint, reconciliation_status,
                        reconciliation_updated_at, notiz)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (None, "EXTERNAL", "", broker_name, str(asset_type or ""),
                     symbol_name, str(waehrung or ""), _version(""), "", "", "{}",
                     "", 1 if paper else 0, _zeit(eingestiegen_am),
                     fallback_price, None, fallback_quantity, None,
                     instrument_key, entry_order_key, "", "[]", "", "", "",
                     "{}", account_key, "EXTERNAL_OBSERVE", _jetzt(),
                     str(notiz or
                         "nachtraeglich aus dem Verkaufspfad angelegt")[:300]),
                )
                trade_id = int(cur.lastrowid)
                offen = dict(con.execute(
                    "SELECT * FROM trades WHERE trade_id=?", (trade_id,)
                ).fetchone())
            else:
                trade_id = int(offen["trade_id"])

            identity_account = account_key or str(
                offen.get("broker_account_fingerprint") or "")[:64]
            identity_instrument = instrument_key or str(
                offen.get("broker_position_id") or "")[:160]
            event_fill_ids = exit_ids or [""]
            offene_menge = _zahl(offen.get("menge")) or 0.0
            verkauft = _zahl(menge)
            if verkauft > offene_menge + max(1e-9, abs(offene_menge) * 1e-8):
                # 9.7: niemals eine Broker-Verkaufsmenge still auf den lokalen
                # Bestand kuerzen. Das verdeckte bisher verlorene Kauf-Teilfills.
                con.rollback()
                raise LedgerZuordnungUnklar(
                    f"Exitmenge {verkauft:g} uebersteigt Ledgerbestand {offene_menge:g}")
            rest = round(offene_menge - verkauft, 12)

            # Falls die Identitaet erst aus dem offenen Trade ableitbar war,
            # erfolgt der definitive Check jetzt noch einmal -- weiterhin unter
            # demselben BEGIN IMMEDIATE und zwingend vor jeder Geldmutation.
            if exit_ids or event_key:
                replay_trade_id = _existing_exit_event_trade_id(
                    con, broker=broker_name, account=identity_account,
                    instrument=identity_instrument, fill_ids=exit_ids,
                    event_id=event_key, quantity=verkauft,
                    price=exit_preis, fee=exit_fee,
                    requested_trade_id=trade_id, paper=paper)
                if replay_trade_id is not None:
                    con.rollback()
                    return replay_trade_id

            einstieg = _zahl(offen.get("einstieg_preis"))
            entry_fee_raw = _zahl(offen.get("einstieg_gebuehr"))
            einstieg_gebuehr = entry_fee_raw or 0.0
            # Anteilige Einstiegsgebuehr bei einem Teilverkauf.
            anteil = (verkauft / offene_menge) if offene_menge > 0 else 1.0
            fees_known = entry_fee_raw is not None and exit_fee is not None
            gebuehr_gesamt = ((einstieg_gebuehr * anteil) + exit_fee
                              if fees_known else None)
            fee_quality = "CONFIRMED" if fees_known else "UNKNOWN"

            brutto = (exit_preis - einstieg) * verkauft if einstieg else None
            netto = _zahl(netto_pnl)
            if netto is None and brutto is not None and fees_known:
                netto = brutto - float(gebuehr_gesamt or 0.0)

            # Slippage: Referenzkurs der Entscheidung gegen den echten Fill.
            # Positiv = schlechter als geplant, in Prozent des Referenzkurses.
            slippage = None
            referenz_ein = _zahl(offen.get("einstieg_referenz"))
            if referenz_ein and einstieg:
                slippage = 100.0 * (einstieg - referenz_ein) / referenz_ein
            referenz_aus = _zahl(referenzpreis)
            if referenz_aus:
                aus_slip = 100.0 * (referenz_aus - exit_preis) / referenz_aus
                slippage = aus_slip if slippage is None else (slippage + aus_slip)

            ein_zeit = str(offen.get("eingestiegen_am") or "")
            aus_zeit = _zeit(zeit)
            haltedauer = None
            try:
                haltedauer = round(
                    (datetime.fromisoformat(aus_zeit) - datetime.fromisoformat(ein_zeit))
                    .total_seconds() / 60.0, 2)
                if haltedauer < 0:
                    haltedauer = None
            except (TypeError, ValueError):
                haltedauer = None

            cur = con.execute(
                """UPDATE trades SET menge=?, einstieg_gebuehr=?, entry_cost_basis=?, ausgestiegen_am=?, ausstieg_preis=?,
                       ausstieg_referenz=?, exit_grund=?, brutto_pnl=?, gebuehren=?,
                       fee_quality=?, slippage_geschaetzt=?, netto_pnl=?, haltedauer_minuten=?,
                       mfe_pct=COALESCE(?, mfe_pct), mae_pct=COALESCE(?, mae_pct),
                       exit_order_id=?, exit_fill_ids_json=?,
                       reconciliation_status='CLOSED', reconciliation_updated_at=?
                   WHERE trade_id=? AND ausgestiegen_am IS NULL""",
                (verkauft, (entry_fee_raw * anteil if entry_fee_raw is not None else None),
                 ((einstieg * verkauft + entry_fee_raw * anteil)
                  if einstieg is not None and entry_fee_raw is not None else None),
                 aus_zeit, exit_preis, referenz_aus,
                 str(exit_grund or EXIT_UNBEKANNT)[:200], brutto,
                 gebuehr_gesamt, fee_quality,
                 slippage, netto, haltedauer, _zahl(mfe_pct), _zahl(mae_pct),
                 exit_order_key,
                 json.dumps(exit_ids, ensure_ascii=False),
                 _jetzt(), trade_id),
            )
            if not cur.rowcount:
                con.rollback()
                raise RuntimeError("Offener Trade wurde parallel bereits veraendert")
            if exit_ids or event_key:
                for fid in event_fill_ids:
                    con.execute(
                        """INSERT INTO trade_exit_events
                           (trade_id, broker, broker_account_fingerprint,
                            instrument, order_id, fill_id, event_id,
                            quantity, price, fee, booked_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                        (trade_id, broker_name, identity_account,
                         identity_instrument, exit_order_key, fid, event_key,
                         verkauft, exit_preis, exit_fee, _jetzt()))
            if rest > 1e-12:
                # Teilverkauf: der Rest laeuft als eigener offener Trade weiter.
                con.execute(
                    """INSERT INTO trades
                       (decision_id, link_status, enter_tag, broker, asset_type, symbol, waehrung,
                        strategie_version, entry_strategy_mode, strategy_parameter_hash,
                        strategy_parameters_json, marktphase, paper, eingestiegen_am,
                        einstieg_preis, einstieg_referenz, menge, einstieg_gebuehr,
                        broker_position_id, entry_order_id, entry_fill_id,
                        entry_fill_ids_json, client_order_id, order_tag,
                        ownership_status, entry_fee_currency_json,
                        broker_account_fingerprint,
                        reconciliation_status, reconciliation_updated_at, notiz)
                       SELECT decision_id, link_status, enter_tag, broker, asset_type, symbol, waehrung,
                              strategie_version, entry_strategy_mode, strategy_parameter_hash,
                              strategy_parameters_json, marktphase, paper, eingestiegen_am,
                              einstieg_preis, einstieg_referenz, ?, ?, broker_position_id,
                              entry_order_id, '', '[]', client_order_id, order_tag,
                              ownership_status, entry_fee_currency_json,
                              broker_account_fingerprint,
                              'CONFIRMED_OPEN', ?, 'Rest nach Teilverkauf'
                       FROM trades WHERE trade_id=?""",
                    (rest, (entry_fee_raw * (1.0 - anteil)
                            if entry_fee_raw is not None else None), _jetzt(), trade_id),
                )
            if broker_name == 'okx' and exit_ids and identity_account:
                # 10.7.0: EINE Aufloesung fuer alle drei Verkaufswege, und sie
                # kennt den Lot-Rest. Bis 10.6.0 blieb die Luecke offen, sobald
                # die Boerse einen Rest unter dem Lot stehen liess -- obwohl
                # dieser Rest zwei Zeilen weiter oben gerade als eigener
                # Trade angelegt wurde (18.09.2026: XRP/BTC/ETH).
                from okx_accounting import resolve_balance_gap_on
                resolve_balance_gap_on(
                    con, trade_id=trade_id, account=identity_account,
                    environment='DEMO' if paper else 'LIVE',
                    sold=verkauft, residual=(rest if rest > 1e-12 else 0),
                    exit_ids=exit_ids, reason='EXACT_EXIT_FILLS')
            _confirm_execution_accounting_on(con, trade_id, "SELL",
                order_id=exit_order_key)
            con.commit()
        return trade_id
    except LedgerZuordnungUnklar as exc:
        # Der Typ muss den Wrapper ueberleben: nur an ihm erkennt der Aufrufer,
        # dass ein Neuversuch nichts bringt und der Verkauf stattdessen als
        # unzugeordnet festzuhalten ist.
        logger.warning("Verkauf %s %s ist keiner Ledgerzeile zuzuordnen: %s",
                       broker, symbol, exc)
        if critical:
            raise LedgerZuordnungUnklar(
                f"{broker} {symbol}: {exc}") from exc
        return None
    except Exception as exc:
        logger.warning("Trade-Ausstieg nicht aufgezeichnet (%s %s): %s", broker, symbol, exc)
        if critical:
            raise RuntimeError(
                f"Kritischer Ledger-Ausstieg fehlgeschlagen ({broker} {symbol})") from exc
        return None


def register_account_alias(*, broker: str, alias_fingerprint: str,
                           account_fingerprint: str, beleg: str = "") -> bool:
    """Einen Vorgaenger-Fingerprint als DASSELBE Konto festhalten (9.5.3).

    Aufrufer ist ``etoro_reconciliation._uebernehme_altdomaene()``, und zwar
    erst nachdem der Broker die gespeicherte orderId im aktuellen Konto
    bestaetigt hat. Ohne diesen Beleg wird nichts eingetragen.
    """
    broker_name = str(broker or "").lower().strip()
    alias = str(alias_fingerprint or "").strip()[:64]
    konto = str(account_fingerprint or "").strip()[:64]
    if not (broker_name and alias and konto) or alias == konto:
        return False
    try:
        init_ledger()
        with _LOCK, _connect() as con:
            con.execute(
                """INSERT INTO account_aliases
                       (broker, alias_fingerprint, account_fingerprint,
                        beleg, erfasst_am_utc)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(broker, alias_fingerprint) DO UPDATE SET
                       account_fingerprint=excluded.account_fingerprint,
                       beleg=excluded.beleg""",
                (broker_name, alias, konto, str(beleg)[:300],
                 datetime.now(timezone.utc).isoformat()))
            con.commit()
        _alias_cache_leeren()
        logger.warning("Ledger-Kontoalias belegt: %s -> %s (%s)",
                       alias, konto, beleg)
        return True
    except Exception:
        logger.warning("Kontoalias konnte nicht gespeichert werden", exc_info=True)
        return False


# 9.5.3: Aliase aendern sich fast nie, werden aber in jedem Lookup gebraucht.
# Ohne Cache oeffnet jede Suche die Datenbank -- die Testsuite wurde dadurch
# zehnmal langsamer, und auf dem Pi laege das mitten im Geldpfad.
_ALIAS_CACHE: dict[tuple[str, str, str], list[str]] = {}
_ALIAS_CACHE_LOCK = threading.RLock()


def _alias_cache_leeren() -> None:
    with _ALIAS_CACHE_LOCK:
        _ALIAS_CACHE.clear()


def konto_identitaeten(broker: str, account_fingerprint: str, *, con=None) -> list[str]:
    """Alle Fingerprints, die nachweislich DASSELBE Konto bezeichnen.

    Der aktuelle Fingerprint steht immer an erster Stelle; Schreibvorgaenge
    benutzen ausschliesslich ihn. Erweitert wird nur die SUCHE, damit eine
    unter einem Vorgaenger angelegte Zeile gefunden statt neu erzeugt wird.
    """
    broker_name = str(broker or "").lower().strip()
    konto = str(account_fingerprint or "").strip()
    if not konto:
        return []
    out = [konto]
    if not broker_name:
        return out
    if con is not None:
        # An active write transaction must never open a second schema writer.
        # Read aliases on the same snapshot; no stale cache or swallowed error.
        rows = con.execute("""SELECT alias_fingerprint FROM account_aliases
            WHERE broker=? AND account_fingerprint=?""", (broker_name, konto)).fetchall()
        for row in rows:
            alias = str(row[0] or "").strip()
            if alias and alias not in out:
                out.append(alias)
        return out
    from decision_analytics import db_pfad
    schluessel = (str(db_pfad().resolve()), broker_name, konto)
    with _ALIAS_CACHE_LOCK:
        zwischenstand = _ALIAS_CACHE.get(schluessel)
    if zwischenstand is not None:
        return list(zwischenstand)
    try:
        init_ledger()
        with _LOCK, _connect() as con:
            rows = con.execute(
                """SELECT alias_fingerprint FROM account_aliases
                   WHERE broker=? AND account_fingerprint=?""",
                (broker_name, konto)).fetchall()
        for row in rows:
            alias = str(row[0] or "").strip()
            if alias and alias not in out:
                out.append(alias)
    except Exception:
        logger.debug("Kontoaliase nicht lesbar", exc_info=True)
        return out          # nicht zwischenspeichern, was fehlgeschlagen ist
    with _ALIAS_CACHE_LOCK:
        _ALIAS_CACHE[schluessel] = list(out)
    return out


def _konto_platzhalter(broker: str, account_fingerprint: str, *, con=None) -> tuple[str, list[str]]:
    """(SQL-Fragment, Parameter) fuer eine kontoweite Suche inkl. Aliase."""
    identitaeten = konto_identitaeten(broker, account_fingerprint, con=con)
    if not identitaeten:
        return "broker_account_fingerprint=?", [str(account_fingerprint or "")]
    marken = ",".join("?" for _ in identitaeten)
    return f"broker_account_fingerprint IN ({marken})", list(identitaeten)


# Felder, die eine zusammengefuehrte Zeile von ihrer Altzeile uebernehmen darf.
_ERGEBNISFELDER = ("ausgestiegen_am", "ausstieg_preis", "ausstieg_referenz",
                   "exit_grund", "brutto_pnl", "gebuehren",
                   "slippage_geschaetzt", "netto_pnl", "haltedauer_minuten")


def dubletten_gruppen(broker: str = "") -> list[dict]:
    """Zeilen, die dieselbe Brokerposition doppelt fuehren (9.5.3).

    Erkennungsmerkmal ist ausschliesslich ``broker + broker_position_id +
    entry_order_id``. Symbol, Menge oder Preisnaehe sind KEIN Merkmal -- eine
    Dublette wird bewiesen, nicht geraten.

    Der Fall entstand, weil 9.5.1 das Kontofingerprint-Verfahren wechselte,
    ohne das Ledger mitzunehmen: dieselbe Position lag danach unter zwei
    Fingerprints, und beide Zeilen konnten unabhaengig geschlossen werden.
    """
    try:
        init_ledger()
        sql = ("SELECT * FROM trades WHERE broker_position_id <> '' "
               "AND entry_order_id <> '' AND superseded_by IS NULL")
        werte: list[Any] = []
        if broker:
            sql += " AND broker=?"
            werte.append(str(broker).lower())
        sql += " ORDER BY trade_id"
        with _LOCK, _connect() as con:
            zeilen = [dict(r) for r in con.execute(sql, tuple(werte)).fetchall()]
    except Exception:
        logger.warning("Dublettensuche fehlgeschlagen", exc_info=True)
        return []

    nach_schluessel: dict[tuple, list[dict]] = {}
    for zeile in zeilen:
        schluessel = (str(zeile.get("broker") or ""),
                      str(zeile.get("broker_position_id") or ""),
                      str(zeile.get("entry_order_id") or ""))
        nach_schluessel.setdefault(schluessel, []).append(zeile)

    gruppen = []
    for schluessel, gruppe in nach_schluessel.items():
        if len(gruppe) < 2:
            continue
        konten = {str(x.get("broker_account_fingerprint") or "") for x in gruppe}
        gruppen.append({
            "broker": schluessel[0], "position_id": schluessel[1],
            "entry_order_id": schluessel[2],
            "symbol": str(gruppe[0].get("symbol") or ""),
            "konten": sorted(konten),
            "trade_ids": [int(x["trade_id"]) for x in gruppe],
            "zeilen": gruppe,
            "mit_ergebnis": [int(x["trade_id"]) for x in gruppe
                             if x.get("netto_pnl") is not None],
        })
    return gruppen


def _primaerzeile(gruppe: list[dict]) -> dict:
    """Welche Zeile bleibt bestehen?

    Vorrang hat die Zeile mit belegter Identitaet (decision_id und LINKED) --
    sie traegt die Verbindung zur Entscheidung und damit zur ganzen
    Beweiskette. Bei Gleichstand die aeltere trade_id.
    """
    def rang(zeile: dict) -> tuple:
        return (0 if int(zeile.get("decision_id") or 0) > 0 else 1,
                0 if str(zeile.get("link_status") or "") == "LINKED" else 1,
                int(zeile.get("trade_id") or 0))
    return sorted(gruppe, key=rang)[0]


def fuehre_dubletten_zusammen(broker: str = "", *, probelauf: bool = True,
                              actor: str = "auto") -> dict:
    """Doppelt gefuehrte Brokerpositionen zusammenfuehren (9.5.3).

    Es wird NICHTS geloescht. Die Primaerzeile behaelt ihre Identitaet und
    uebernimmt fehlende Ergebnisfelder aus der Altzeile; die Altzeile wird auf
    ``link_status = 'SUPERSEDED'`` gesetzt und faellt damit aus jeder
    Auswertung, bleibt aber vollstaendig als Auditspur erhalten.

    Idempotent: ein zweiter Lauf findet nichts mehr, weil SUPERSEDED-Zeilen
    aus der Dublettensuche ausgeschlossen sind.

    ``probelauf=True`` schreibt nicht und liefert nur den Plan.
    """
    gruppen = dubletten_gruppen(broker)
    bericht = {"gefunden": len(gruppen), "probelauf": bool(probelauf),
               "zusammengefuehrt": [], "uebersprungen": []}
    if not gruppen:
        return bericht

    for gruppe in gruppen:
        zeilen = gruppe["zeilen"]
        primaer = _primaerzeile(zeilen)
        andere = [z for z in zeilen if int(z["trade_id"]) != int(primaer["trade_id"])]

        # Sicherheitsgrenze: unterschiedliche Mengen oder Einstandspreise
        # bedeuten, dass es KEINE Dublette ist, sondern ein Teilverkauf oder
        # zwei echte Positionen. Dann wird nichts angefasst.
        mengen = {round(float(z.get("menge") or 0.0), 10) for z in zeilen}
        einstaende = {round(float(z.get("einstieg_preis") or 0.0), 10) for z in zeilen}
        if len(mengen) > 1 or len(einstaende) > 1:
            bericht["uebersprungen"].append({
                **{k: gruppe[k] for k in ("symbol", "position_id", "trade_ids")},
                "grund": "Menge oder Einstand unterschiedlich -- kein Dublettenbeweis"})
            continue

        uebernahme = {}
        # Hat die Primaerzeile GAR KEIN Ergebnis, die Altzeile aber eines,
        # dann ist deren gesamter Abschluss der belastbare -- einschliesslich
        # Ausstiegszeit und -grund. Sonst behielte CRM den lokalen
        # Buchungszeitpunkt (02.09. 08:19) statt des echten Brokerabschlusses
        # (01.09. 14:26) und den Grund BROKER_CLOSED_RESULT_MISSING.
        beleg = next((z for z in andere if z.get("netto_pnl") is not None), None)
        primaer_ohne_ergebnis = primaer.get("netto_pnl") is None
        for feld in _ERGEBNISFELDER:
            if primaer_ohne_ergebnis and beleg is not None:
                if beleg.get(feld) not in (None, ""):
                    uebernahme[feld] = beleg[feld]
                continue
            if primaer.get(feld) in (None, ""):
                for z in andere:
                    if z.get(feld) not in (None, ""):
                        uebernahme[feld] = z[feld]
                        break
        plan = {"symbol": gruppe["symbol"], "position_id": gruppe["position_id"],
                "primaer": int(primaer["trade_id"]),
                "ersetzt": [int(z["trade_id"]) for z in andere],
                "konten": gruppe["konten"], "uebernommen": dict(uebernahme)}

        if probelauf:
            bericht["zusammengefuehrt"].append(plan)
            continue

        try:
            with _LOCK, _connect() as con:
                con.execute("BEGIN IMMEDIATE")
                if uebernahme:
                    sets = ", ".join(f"{k}=?" for k in uebernahme)
                    con.execute(f"UPDATE trades SET {sets} WHERE trade_id=?",
                                (*uebernahme.values(), int(primaer["trade_id"])))
                for z in andere:
                    con.execute(
                        """UPDATE trades
                           SET superseded_by=?,
                               notiz = TRIM(COALESCE(notiz,'') || ?)
                           WHERE trade_id=?""",
                        (int(primaer["trade_id"]),
                         f" [9.5.3: zusammengefuehrt in Trade "
                         f"{int(primaer['trade_id'])} durch {actor}]",
                         int(z["trade_id"])))
                con.commit()
        except Exception as exc:
            bericht["uebersprungen"].append({
                **plan, "grund": f"Schreibfehler: {type(exc).__name__}: {exc}"})
            continue
        _alias_cache_leeren()
        logger.warning(
            "Ledger-Dublette zusammengefuehrt: %s positionId=%s -> Trade %s "
            "(ersetzt %s, Konten %s)", gruppe["symbol"], gruppe["position_id"],
            primaer["trade_id"], plan["ersetzt"], gruppe["konten"])
        bericht["zusammengefuehrt"].append(plan)
    return bericht


def reconcile_entry_fees_exact(*, broker, account, paper, position_id,
                               entry_order_id, entry_fills):
    """Project complete entry costs without inventing any missing exit costs.

    Account, environment, order, position, fill identities, quantity and price
    must all agree. A conflicting receipt rolls back the whole transaction.
    """
    if (broker != "etoro" or not account or not position_id or not entry_order_id
            or not isinstance(paper, bool)):
        raise LedgerZuordnungUnklar("Einstiegskosten ohne exakte Kontokette")

    def same(a, b):
        x, y = _zahl(a), _zahl(b)
        return x is not None and y is not None and abs(x-y) <= max(1e-8, abs(y)*1e-7)

    ids = [str(f.get("fill_id") or "") for f in entry_fills]
    if not ids or "" in ids or len(set(ids)) != len(ids):
        raise LedgerZuordnungUnklar("Einstiegskosten ohne eindeutige Fill-IDs")
    for f in entry_fills:
        qty, px, fee = (_zahl(f.get(k)) for k in ("quantity", "price", "fee"))
        if (any(isinstance(f.get(k), bool) for k in ("quantity", "price", "fee"))
                or qty is None or qty <= 0 or px is None or px <= 0
                or fee is None or fee < 0 or f.get("fee_currency") != "USD"):
            raise LedgerZuordnungUnklar("Einstiegskostenbeleg unvollstaendig")
    quantity = sum(float(f["quantity"]) for f in entry_fills)
    price = sum(float(f["quantity"])*float(f["price"]) for f in entry_fills)/quantity
    fee = sum(float(f["fee"]) for f in entry_fills)
    init_ledger()
    with _LOCK, _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = [dict(r) for r in con.execute("""SELECT * FROM trades WHERE broker=?
            AND broker_account_fingerprint=? AND paper=? AND broker_position_id=?
            AND entry_order_id=? AND superseded_by IS NULL""",
            (broker, account, int(paper), str(position_id), str(entry_order_id)))]
        if (not rows or any(r["waehrung"] != "USD" for r in rows)
                or not same(sum(r["menge"] for r in rows), quantity)
                or any(not same(r["einstieg_preis"], price) for r in rows)):
            raise LedgerZuordnungUnklar("Einstiegsbeleg widerspricht der Ledger-Lineage")
        known_ids = set()
        for row in rows:
            known_ids.update(json.loads(row.get("entry_fill_ids_json") or "[]"))
            if row.get("entry_fill_id"):
                known_ids.add(row["entry_fill_id"])
            allocation = fee * row["menge"] / quantity
            if ((row.get("entry_fee_quality") == "CONFIRMED"
                 or row.get("fee_quality") == "CONFIRMED")
                    and not same(row.get("einstieg_gebuehr"), allocation)):
                raise LedgerZuordnungUnklar("Neue Einstiegskosten widersprechen bestaetigten Kosten")
        allowed_ids = set(ids)
        # Older native-fill and reconciliation adapters used two deterministic
        # names for the same position execution (including its exact time).
        # The fill register must still contain just one economic execution.
        for f in entry_fills:
            stamp = str(f.get("filled_at") or "")
            if stamp and f["fill_id"] == f"etoro-entry:{entry_order_id}:{position_id}:{stamp}":
                allowed_ids.add(f"etoro:{account}:open:{entry_order_id}:{position_id}:{stamp}")
        if not set(ids) <= known_ids or not known_ids <= allowed_ids:
            raise LedgerZuordnungUnklar("Einstiegs-Fill-Identitaeten widersprechen dem Ledger")
        stored = [dict(r) for r in con.execute("""SELECT * FROM trade_entry_fills
            WHERE broker=? AND broker_account_fingerprint=? AND paper=?
            AND instrument=? AND order_id=?""",
            (broker, account, int(paper), str(position_id), str(entry_order_id)))]
        if (len(stored) != len(ids) or {r["fill_id"] for r in stored} != set(ids)
                or any(r["trade_id"] not in {t["trade_id"] for t in rows} for r in stored)):
            raise LedgerZuordnungUnklar("Einstiegs-Fillregister passt nicht zur Kontokette")
        by_id = {f["fill_id"]: f for f in entry_fills}
        for r in stored:
            incoming = by_id[r["fill_id"]]
            # 10.7.1: Eine LEERE gespeicherte Waehrung ist kein Widerspruch,
            # sondern ein fehlender Wert (CSCO 18.09.2026: Gebuehr 1,00 ohne
            # Waehrung gespeichert, Beleg sagt USD -- der Abgleich schlug 324x
            # in einer Stunde fehl und hielt die Domaene gesperrt). Nur eine
            # ANDERE Waehrung oder eine andere Gebuehr widerspricht.
            if (not same(r["quantity"], incoming["quantity"])
                    or not same(r["price"], incoming["price"])
                    or incoming.get("filled_at") and r.get("filled_at") != incoming["filled_at"]
                    or r["fee"] is not None and (not same(r["fee"], incoming["fee"])
                        or (r["fee_currency"] and r["fee_currency"] != "USD"))):
                raise LedgerZuordnungUnklar("Einstiegs-Fillregister enthaelt widersprechenden Beleg")
        con.execute("""CREATE TABLE IF NOT EXISTS etoro_entry_cost_audit (
            trade_id INTEGER PRIMARY KEY, projected_at TEXT NOT NULL,
            before_json TEXT NOT NULL, receipts_json TEXT NOT NULL)""")
        updates = 0
        for r in stored:
            if r["fee"] is None or not r["fee_currency"]:
                incoming = by_id[r["fill_id"]]
                raw = json.loads(r["raw_json"] or "{}")
                raw["entry_cost_receipt"] = incoming
                con.execute("""UPDATE trade_entry_fills SET fee=?,fee_currency='USD',raw_json=?
                    WHERE id=?""", (incoming["fee"], json.dumps(raw, ensure_ascii=False), r["id"]))
        for r in rows:
            allocation = fee * r["menge"] / quantity
            if r.get("entry_fee_quality") == "CONFIRMED" and same(r.get("einstieg_gebuehr"), allocation):
                continue
            # Keep the old provisional numbers in the audit, not in the net
            # display: a newly proven entry charge makes a gross-as-net value
            # demonstrably incomplete. Confirmed complete results stay intact.
            con.execute("""INSERT OR IGNORE INTO etoro_entry_cost_audit VALUES (?,?,?,?)""",
                (r["trade_id"], _jetzt(), json.dumps(r, ensure_ascii=False),
                 json.dumps(entry_fills, ensure_ascii=False)))
            con.execute("""UPDATE trades SET einstieg_gebuehr=?,entry_fee_quality='CONFIRMED',
                gebuehren=CASE WHEN fee_quality IN ('CONFIRMED','USER_CONFIRMED','CASH_DELTA_CONFIRMED') THEN gebuehren ELSE NULL END,
                netto_pnl=CASE WHEN fee_quality IN ('CONFIRMED','USER_CONFIRMED','CASH_DELTA_CONFIRMED') THEN netto_pnl ELSE NULL END,
                reconciliation_updated_at=? WHERE trade_id=?""", (allocation, _jetzt(), r["trade_id"]))
            updates += 1
        return {"updated": updates, "entry_fee": fee, "currency": "USD"}


def reconcile_fees_exact(*, broker, account, paper, position_id, entry_order_id,
                         entry_fills, exit_fills, cost_evidence=None):
    """Complete missing fees only from a full, exact closed execution lineage.

    No symbol join, no estimated fee schedule, no assumed USD/USDC parity.
    Conflicting receipts fail atomically; repeated identical receipts do nothing.
    """
    if (broker != "etoro" or not account or not position_id or not entry_order_id
            or not isinstance(paper, bool)):
        raise LedgerZuordnungUnklar("Gebuehrenabgleich ohne exakte Kontokette")
    def same(a, b):
        x, y = _zahl(a), _zahl(b)
        return x is not None and y is not None and abs(x-y) <= max(1e-8, abs(y)*1e-7)
    for group in (entry_fills, exit_fills):
        ids = [str(r.get("fill_id") or "") for r in group]
        if not ids or "" in ids or len(ids) != len(set(ids)):
            raise LedgerZuordnungUnklar("Gebuehrenbelege ohne eindeutige Fill-IDs")
        for r in group:
            fee, qty, px = (_zahl(r.get(k)) for k in ("fee", "quantity", "price"))
            if (any(isinstance(r.get(k), bool) for k in ("fee", "quantity", "price"))
                    or fee is None or fee < 0 or qty is None or qty <= 0 or px is None or px <= 0
                    or r.get("fee_currency") != "USD"):
                raise LedgerZuordnungUnklar("Gebuehren-/Mengen-/Waehrungsbeleg unvollstaendig")
    entry_qty = sum(float(r["quantity"]) for r in entry_fills)
    entry_price = sum(float(r["quantity"])*float(r["price"]) for r in entry_fills)/entry_qty
    entry_fee = sum(float(r["fee"]) for r in entry_fills)
    if not same(entry_qty, sum(float(r["quantity"]) for r in exit_fills)):
        raise LedgerZuordnungUnklar("Gebuehrenabgleich braucht den vollstaendigen Mengenzerfall")
    # A recovered broker TP/SL order becomes an exit anchor only together
    # with its exact native filled-close cost receipt. A stream hint or a
    # history row alone cannot bind an order, and an entry ID is never reused.
    native_close_id = ""
    if isinstance(cost_evidence, dict) and cost_evidence.get("source") == "ETORO_V2_FILLED_CLOSE_ORDER_TOTAL_COSTS":
        import hashlib
        raw = cost_evidence.get("order") or {}
        if not isinstance(raw, dict):
            raise LedgerZuordnungUnklar("Nativer Abschlussbeleg fehlt")
        cid = str(raw.get("accountId") or "")
        expected_account = hashlib.sha256(
            f"etoro|{'demo' if paper else 'live'}|cid:{cid}".encode()).hexdigest()[:24]
        asset, status = raw.get("asset") or {}, raw.get("status") or {}
        native_close_id = str(raw.get("orderId") or "")
        executions = raw.get("positionExecutions") or []
        total = _zahl(raw.get("totalCosts"))
        if (not isinstance(asset, dict) or not isinstance(status, dict)
                or not cid.isdigit() or expected_account != account
                or not native_close_id or native_close_id == str(entry_order_id)
                or raw.get("action") != "close" or raw.get("transaction") != "sell"
                or str(status.get("name") or "").lower() != "filled"
                or status.get("errorCode") not in (None, 0)
                or str(raw.get("orderCurrency") or "").upper() != "USD"
                or str(asset.get("currency") or "").upper() != "USD"
                or asset.get("side") != "long" or isinstance(asset.get("leverage"), bool)
                or asset.get("leverage") != 1
                or str(asset.get("settlementType") or "").upper() != "REAL"
                or {str(x) for x in raw.get("positionsToClose") or []} != {str(position_id)}
                or not executions or any(not isinstance(x, dict) or
                    str(x.get("positionId")) != str(position_id) or x.get("state") != "closed"
                    for x in executions)
                or isinstance(raw.get("requestedUnits"), bool)
                or not same(raw.get("requestedUnits"), entry_qty)
                or isinstance(raw.get("totalCosts"), bool) or total is None or total < 0
                or not same(total, sum(float(f["fee"]) for f in exit_fills))
                or any(str(f.get("order_id") or "") != native_close_id for f in exit_fills)):
            raise LedgerZuordnungUnklar("Nativer Abschlussauftrag nicht exakt belegt")
    init_ledger()
    with _LOCK, _connect() as con:
        con.execute("BEGIN IMMEDIATE")
        rows = [dict(r) for r in con.execute("""SELECT * FROM trades WHERE broker=?
          AND broker_account_fingerprint=? AND paper=? AND broker_position_id=?
          AND entry_order_id=? AND superseded_by IS NULL ORDER BY trade_id""",
          (broker, account, int(paper), position_id, entry_order_id))]
        if not rows or any(not r["ausgestiegen_am"] or r["waehrung"] != "USD" for r in rows):
            raise LedgerZuordnungUnklar("Lineage nicht vollstaendig in USD geschlossen")
        if not same(sum(r["menge"] for r in rows), entry_qty):
            raise LedgerZuordnungUnklar("Ledger-Mengenzerfall passt nicht zu Brokerbelegen")
        entry_ids = set()
        for row in rows:
            entry_ids.update(str(x) for x in json.loads(row.get("entry_fill_ids_json") or "[]") if x)
            if row.get("entry_fill_id"):
                entry_ids.add(str(row["entry_fill_id"]))
        incoming_ids = {str(f["fill_id"]) for f in entry_fills}
        allowed_ids = set(incoming_ids)
        for fill in entry_fills:
            stamp = str(fill.get("filled_at") or "")
            if stamp and fill["fill_id"] == f"etoro-entry:{entry_order_id}:{position_id}:{stamp}":
                allowed_ids.add(f"etoro:{account}:open:{entry_order_id}:{position_id}:{stamp}")
        if not incoming_ids <= entry_ids or not entry_ids <= allowed_ids:
            raise LedgerZuordnungUnklar("Entry-Fill-Identitaeten widersprechen dem Ledger")
        used = set()
        updates = []
        for row in rows:
            existing_close_id = str(row.get("exit_order_id") or "")
            if native_close_id and existing_close_id and existing_close_id != native_close_id:
                raise LedgerZuordnungUnklar("Nativer Abschlussauftrag widerspricht bestehender Exit-ID")
            allocated_entry = entry_fee * row["menge"] / entry_qty
            if (row.get("entry_fee_quality") == "CONFIRMED"
                    and not same(row.get("einstieg_gebuehr"), allocated_entry)):
                raise LedgerZuordnungUnklar("Abschlusskosten widersprechen bestaetigten Einstiegskosten")
            known = set(json.loads(row.get("exit_fill_ids_json") or "[]"))
            matching = [f for f in exit_fills if f["fill_id"] in known]
            if (not matching or any(f["fill_id"] in used for f in matching)
                    or not same(row["menge"], sum(f["quantity"] for f in matching))
                    or not same(row["einstieg_preis"], entry_price)):
                raise LedgerZuordnungUnklar("Exit-Fill kann keinem einzelnen Ledgerergebnis zugeordnet werden")
            price = sum(f["quantity"]*f["price"] for f in matching)/row["menge"]
            if not same(price, row["ausstieg_preis"]):
                raise LedgerZuordnungUnklar("Ausfuehrungspreis widerspricht dem Ledger")
            used.update(f["fill_id"] for f in matching)
            total_fee = entry_fee * row["menge"] / entry_qty + sum(f["fee"] for f in matching)
            gross = (price-entry_price)*row["menge"]
            if row.get("fee_quality") in {"CONFIRMED", "USER_CONFIRMED", "CASH_DELTA_CONFIRMED"}:
                if not same(row.get("gebuehren"), total_fee) or not same(row.get("netto_pnl"), gross-total_fee):
                    raise LedgerZuordnungUnklar("Neuer Beleg widerspricht bestaetigten Gebuehren")
                if not native_close_id or existing_close_id:
                    continue
            updates.append((entry_fee * row["menge"] / entry_qty, total_fee, gross, gross-total_fee,
                            _jetzt(), native_close_id or existing_close_id, row["trade_id"]))
        if used != {f["fill_id"] for f in exit_fills}:
            raise LedgerZuordnungUnklar("Nicht alle Exit-Fills exakt zugeordnet")
        con.execute("""CREATE TABLE IF NOT EXISTS etoro_closed_cost_audit (
            trade_id INTEGER PRIMARY KEY, projected_at TEXT NOT NULL,
            before_json TEXT NOT NULL, receipts_json TEXT NOT NULL)""")
        if native_close_id:
            con.execute("""CREATE TABLE IF NOT EXISTS etoro_close_order_binding_audit (
                trade_id INTEGER PRIMARY KEY, exit_order_id TEXT NOT NULL,
                bound_at TEXT NOT NULL, receipt_json TEXT NOT NULL)""")
        by_id = {row["trade_id"]: row for row in rows}
        for values in updates:
            con.execute("INSERT OR IGNORE INTO etoro_closed_cost_audit VALUES (?,?,?,?)",
                (values[-1], _jetzt(), json.dumps(by_id[values[-1]], ensure_ascii=False),
                 json.dumps({"entry_fills": entry_fills, "exit_fills": exit_fills,
                     "cost_evidence": cost_evidence}, ensure_ascii=False)))
            if native_close_id and not by_id[values[-1]].get("exit_order_id"):
                con.execute("INSERT INTO etoro_close_order_binding_audit VALUES (?,?,?,?)",
                    (values[-1], native_close_id, _jetzt(),
                     json.dumps({"exit_fills": exit_fills, "cost_evidence": cost_evidence}, ensure_ascii=False)))
            con.execute("""UPDATE trades SET einstieg_gebuehr=?,gebuehren=?,brutto_pnl=?,netto_pnl=?,
                fee_quality='CONFIRMED',entry_fee_quality='CONFIRMED',exit_fee_quality='CONFIRMED',
                reconciliation_updated_at=?,exit_order_id=? WHERE trade_id=?""", values)
        result = {"updated": len(updates)}
        if native_close_id:
            result["verified_close_order_id"] = native_close_id
        return result


def reconcile_closed_trade_exact(*, broker: str, symbol: str, paper: bool,
                                 decision_id: int,
                                 broker_position_id: str,
                                 broker_account_fingerprint: str,
                                 entry_order_id: str,
                                 entry_fill_id: str = "",
                                 entry_fill: dict | None = None,
                                 client_order_id: str = "",
                                 close_order_id: str = "",
                                 close_fill_id: str = "",
                                 exit_reason: str = "",
                                 expected_entry_price=None,
                                 expected_close_price=None,
                                 expected_quantity=None,
                                 notiz: str = "") -> dict:
    """Verknuepft einen bereits geschlossenen Trade mit seiner exakten Kette.

    Dieser Migrationspfad ist absichtlich enger als :func:`trade_open` und
    :func:`trade_close`: Ein alter, bereits geschlossener Ledgertrade darf
    weder erneut geschlossen noch anhand von Symbol/Mengenaehnlichkeit
    geraten werden. Er wird nur ueber

    ``broker + Konto + positionId + Entry-orderId``

    gefunden. Preise und Ergebnis werden dabei nicht neu berechnet. Die
    Funktion korrigiert ausserdem den 9.5.0-Fehler, bei dem die Entry-orderId
    als ``exit_order_id`` gespeichert wurde.
    """
    broker_name = str(broker or "").lower().strip()
    symbol_name = str(symbol or "").upper().strip()
    account = str(broker_account_fingerprint or "").strip()[:64]
    position_id = str(broker_position_id or "").strip()[:160]
    entry_order = str(entry_order_id or "").strip()[:160]
    real_close_order = str(close_order_id or "").strip()[:160]
    entry_fid = str(entry_fill_id or "").strip()[:240]
    close_fid = str(close_fill_id or "").strip()[:240]
    if not (broker_name and symbol_name and account and position_id
            and entry_order and int(decision_id or 0) > 0):
        return {"status": "INVALID_IDENTITY"}
    # Eine Entry-ID ist per Definition kein belastbarer Close-Anker.
    if real_close_order == entry_order:
        real_close_order = ""

    def _compatible(actual, expected) -> bool:
        left, right = _zahl(actual), _zahl(expected)
        if right in (None, 0) or left in (None, 0):
            return True
        return abs(left - right) <= max(1e-8, abs(right) * 1e-7)

    try:
        init_ledger()
        with _LOCK, _connect() as con:
            con.execute("BEGIN IMMEDIATE")
            # KORREKTUR 9.5.3: Die Suche beruecksichtigt jetzt belegte
            # Vorgaenger-Fingerprints desselben Kontos. Vorher wurde exakt auf
            # den AKTUELLEN Fingerprint gefiltert -- eine unter dem alten
            # Verfahren angelegte Zeile war damit unsichtbar, und dieselbe
            # Brokerposition konnte ein zweites Mal gebucht werden. Genau so
            # entstand die ADBE-Dublette (trade_id 35 unter ba32..., trade_id
            # 36 unter 664e..., beide mit -344,76 USD).
            konto_filter, konto_werte = _konto_platzhalter(broker_name, account, con=con)
            rows = con.execute(
                f"""SELECT * FROM trades
                    WHERE broker=? AND {konto_filter}
                      AND broker_position_id=? AND entry_order_id=?
                    ORDER BY trade_id""",
                (broker_name, *konto_werte, position_id, entry_order),
            ).fetchall()
            if not rows:
                con.rollback()
                return {"status": "NOT_FOUND"}
            lineage = [dict(item) for item in rows]
            first_trade_id = int(lineage[0]["trade_id"])

            # Symbol und Umgebung sind nur Konfliktpruefungen NACH der exakten
            # Konto+positionId+Entry-orderId-Suche, niemals Suchheuristiken.
            if any(str(item.get("symbol") or "").upper() != symbol_name
                   or bool(item.get("paper")) != bool(paper)
                   for item in lineage):
                con.rollback()
                return {"status": "IDENTITY_CONFLICT", "trade_id": first_trade_id}
            if any(int(item.get("decision_id") or 0) not in {0, int(decision_id)}
                   for item in lineage):
                con.rollback()
                return {"status": "DECISION_CONFLICT", "trade_id": first_trade_id}

            open_rows = [item for item in lineage if not item.get("ausgestiegen_am")]
            if open_rows:
                con.rollback()
                if len(open_rows) == 1:
                    return {"status": "NOT_CLOSED",
                            "trade_id": int(open_rows[0]["trade_id"])}
                return {"status": "AMBIGUOUS", "matches": len(lineage)}

            row = lineage[0]
            if len(lineage) > 1:
                # Ein regulaerer Teilverkauf erzeugt mehrere geschlossene
                # Ergebniszeilen, behaelt aber diese exakte Entry-Lineage. Nur
                # ein vollstaendig belegter Mengenzerfall darf zusammengefuehrt
                # werden; ohne Sollmenge bleiben echte Dubletten fail-closed.
                expected_total = _zahl(expected_quantity)
                entry_times = {str(item.get("eingestiegen_am") or "")
                               for item in lineage}
                base_entry = _zahl(lineage[0].get("einstieg_preis"))
                total_quantity = sum(_zahl(item.get("menge")) or 0.0
                                     for item in lineage)
                regular_lineage = bool(
                    expected_total and expected_total > 0
                    and len(entry_times) == 1
                    and all(_compatible(item.get("einstieg_preis"), base_entry)
                            for item in lineage)
                    and _compatible(total_quantity, expected_total))
                if not regular_lineage:
                    con.rollback()
                    return {"status": "AMBIGUOUS", "matches": len(lineage)}

                def _row_exit_ids(item: dict) -> set[str]:
                    try:
                        return {str(x) for x in json.loads(
                            item.get("exit_fill_ids_json") or "[]") if str(x)}
                    except (TypeError, ValueError):
                        return set()

                candidates = ([item for item in lineage
                               if close_fid and close_fid in _row_exit_ids(item)]
                              if close_fid else [])
                if not candidates and close_fid:
                    event_trade_ids = {
                        int(event[0]) for event in con.execute(
                            """SELECT trade_id FROM trade_exit_events
                               WHERE broker=? AND broker_account_fingerprint=?
                                 AND instrument=? AND fill_id=?""",
                            (broker_name, account, position_id, close_fid),
                        ).fetchall()
                    }
                    candidates = [
                        item for item in lineage
                        if int(item["trade_id"]) in event_trade_ids
                    ]
                if not candidates and real_close_order:
                    candidates = [item for item in lineage
                                  if str(item.get("exit_order_id") or "")
                                  == real_close_order]
                if len(candidates) > 1:
                    con.rollback()
                    return {"status": "AMBIGUOUS", "matches": len(lineage)}
                row = (candidates[0] if candidates else max(
                    lineage,
                    key=lambda item: (str(item.get("ausgestiegen_am") or ""),
                                      int(item["trade_id"]))))
                compared_quantity = total_quantity
            else:
                compared_quantity = row.get("menge")

            trade_id = int(row["trade_id"])
            if not (all(_compatible(item.get("einstieg_preis"), expected_entry_price)
                        for item in lineage)
                    and _compatible(row.get("ausstieg_preis"), expected_close_price)
                    and _compatible(compared_quantity, expected_quantity)):
                con.rollback()
                return {"status": "BROKER_VALUE_CONFLICT", "trade_id": trade_id}

            stored_exit_order = str(row.get("exit_order_id") or "")
            if (stored_exit_order and stored_exit_order != entry_order
                    and real_close_order and stored_exit_order != real_close_order):
                con.rollback()
                return {"status": "CLOSE_ORDER_CONFLICT", "trade_id": trade_id}
            corrected_exit_order = (
                real_close_order or
                ("" if stored_exit_order == entry_order else stored_exit_order))

            entry_ids: list[str] = []
            for item in lineage:
                try:
                    stored_ids = [str(x) for x in json.loads(
                        item.get("entry_fill_ids_json") or "[]") if str(x)]
                except (TypeError, ValueError):
                    stored_ids = []
                direct = str(item.get("entry_fill_id") or "")
                if direct:
                    stored_ids.append(direct)
                for stored_id in stored_ids:
                    if stored_id not in entry_ids:
                        entry_ids.append(stored_id)
            if entry_fid and entry_fid not in entry_ids:
                entry_ids.append(entry_fid)
            try:
                exit_ids = [str(x) for x in json.loads(
                    row.get("exit_fill_ids_json") or "[]") if str(x)]
            except (TypeError, ValueError):
                exit_ids = []
            # Den alten 9.5-Fillanker mit eingebetteter Entry-ID nicht weiter
            # als Brokerbeleg ausgeben. Der neue, positionsgebundene Fillanker
            # ersetzt ihn idempotent.
            if close_fid:
                exit_ids = [x for x in exit_ids
                            if not (entry_order in x and ":close:" in x)]
                if close_fid not in exit_ids:
                    exit_ids.append(close_fid)

            current_reason = str(row.get("exit_grund") or "")
            replaceable_reason = current_reason.upper() in {
                "", EXIT_UNBEKANNT,
                "ETORO-AUSFUEHRUNG EXAKT WIEDERHERGESTELLT",
                "BROKERHISTORIE BESTAETIGT SCHLIESSUNG",
                "BROKER_CLOSED_CONFIRMED",
            }
            corrected_reason = (str(exit_reason or "")[:200]
                                if exit_reason and replaceable_reason
                                else current_reason[:200])
            note = str(notiz or "")[:300]
            con.execute(
                """UPDATE trades SET decision_id=?, link_status='LINKED',
                       ownership_status='BOT_VERIFIED',
                       reconciliation_status='CLOSED',
                       reconciliation_updated_at=COALESCE(
                           reconciliation_updated_at, ?),
                       client_order_id=CASE WHEN ?<>'' THEN ? ELSE client_order_id END,
                       entry_fill_id=CASE WHEN entry_fill_id='' AND ?<>'' THEN ?
                                          ELSE entry_fill_id END,
                       entry_fill_ids_json=?, exit_order_id=?,
                       exit_fill_ids_json=?, exit_grund=?,
                       notiz=CASE WHEN ?<>'' THEN ? ELSE notiz END
                   WHERE trade_id=?""",
                (int(decision_id), _jetzt(),
                 str(client_order_id or ""), str(client_order_id or "")[:80],
                 entry_fid, entry_fid, json.dumps(entry_ids, ensure_ascii=False),
                 corrected_exit_order, json.dumps(exit_ids, ensure_ascii=False),
                 corrected_reason, note, note, trade_id),
            )

            # Die Finanzwerte der einzelnen Teilverkaufszeilen bleiben strikt
            # unangetastet. Gemeinsame Ownership-/Entry-Provenienz gehoert aber
            # zur gesamten exakten Lineage, nicht nur zum letzten Segment.
            other_trade_ids = [int(item["trade_id"]) for item in lineage
                               if int(item["trade_id"]) != trade_id]
            for lineage_trade_id in other_trade_ids:
                con.execute(
                    """UPDATE trades SET decision_id=?, link_status='LINKED',
                           ownership_status='BOT_VERIFIED',
                           reconciliation_status='CLOSED',
                           reconciliation_updated_at=COALESCE(
                               reconciliation_updated_at, ?),
                           client_order_id=CASE WHEN ?<>'' THEN ? ELSE client_order_id END,
                           entry_fill_id=CASE WHEN entry_fill_id='' AND ?<>'' THEN ?
                                              ELSE entry_fill_id END,
                           entry_fill_ids_json=?,
                           notiz=CASE WHEN ?<>'' AND notiz='' THEN ? ELSE notiz END
                       WHERE trade_id=?""",
                    (int(decision_id), _jetzt(),
                     str(client_order_id or ""), str(client_order_id or "")[:80],
                     entry_fid, entry_fid,
                     json.dumps(entry_ids, ensure_ascii=False),
                     note, note, lineage_trade_id),
                )

            fill_row = dict(entry_fill or {})
            if entry_fid:
                entry_trade_id = min(int(item["trade_id"]) for item in lineage)
                con.execute(
                    """INSERT OR IGNORE INTO trade_entry_fills
                       (trade_id, broker, broker_account_fingerprint,
                        instrument, order_id, client_order_id, fill_id,
                        quantity, price, fee, fee_currency, filled_at, raw_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (entry_trade_id, broker_name, account, position_id, entry_order,
                     str(client_order_id or "")[:80], entry_fid,
                     _zahl(fill_row.get("quantity")) or _zahl(expected_quantity),
                     _zahl(fill_row.get("price")) or _zahl(expected_entry_price),
                     _zahl(fill_row.get("fee")),
                     str(fill_row.get("fee_currency") or "")[:16],
                     str(fill_row.get("filled_at") or
                         fill_row.get("execution_time") or "")[:60],
                     json.dumps(fill_row, ensure_ascii=False,
                                sort_keys=True, default=str)),
                )

            # Korrupte 9.5-Exitereignisse tragen die Entry-orderId. Sie werden
            # im selben Commit auf den echten Close-Anker (oder leer) und den
            # stabilen neuen Fillanker umgestellt; es entsteht kein zweites
            # P&L-Ereignis.
            corrupt = con.execute(
                """SELECT * FROM trade_exit_events
                   WHERE trade_id=? AND order_id=? ORDER BY id""",
                (trade_id, entry_order),
            ).fetchall()
            for index, event in enumerate(corrupt):
                event = dict(event)
                new_fill = close_fid if close_fid and index == 0 else str(
                    event.get("fill_id") or "")
                new_event = close_fid if close_fid and index == 0 else str(
                    event.get("event_id") or "")
                try:
                    con.execute(
                        """UPDATE trade_exit_events SET order_id=?, fill_id=?,
                               event_id=? WHERE id=?""",
                        (corrected_exit_order, new_fill, new_event,
                         int(event["id"])),
                    )
                except Exception:
                    # Ein bereits vorhandener identischer Zielanker ist nur
                    # dann idempotent, wenn er zu demselben Trade gehoert.
                    duplicate = con.execute(
                        """SELECT trade_id FROM trade_exit_events
                           WHERE broker=? AND broker_account_fingerprint=?
                             AND instrument=? AND order_id=? AND fill_id=?
                             AND event_id=? LIMIT 1""",
                        (broker_name, account, position_id,
                         corrected_exit_order, new_fill, new_event),
                    ).fetchone()
                    if not duplicate or int(duplicate[0]) != trade_id:
                        raise
                    con.execute(
                        "UPDATE trade_exit_events SET order_id='' WHERE id=?",
                        (int(event["id"]),),
                    )
            if close_fid:
                existing_event = con.execute(
                    """SELECT trade_id FROM trade_exit_events
                       WHERE broker=? AND broker_account_fingerprint=?
                         AND instrument=? AND fill_id=? LIMIT 1""",
                    (broker_name, account, position_id, close_fid),
                ).fetchone()
                if existing_event is None:
                    con.execute(
                        """INSERT INTO trade_exit_events
                           (trade_id, broker, broker_account_fingerprint,
                            instrument, order_id, fill_id, event_id,
                            quantity, price, booked_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (trade_id, broker_name, account, position_id,
                         corrected_exit_order, close_fid, close_fid,
                         _zahl(row.get("menge")), _zahl(row.get("ausstieg_preis")),
                         str(row.get("ausgestiegen_am") or _jetzt())),
                    )
                elif int(existing_event[0]) not in {
                        int(item["trade_id"]) for item in lineage}:
                    raise RuntimeError(
                        "Close-Fillanker gehoert zu einem anderen Ledgertrade")
            con.commit()
            return {"status": "LINKED_CLOSED", "trade_id": trade_id,
                    "exit_order_id": corrected_exit_order}
    except Exception as exc:
        logger.warning("Geschlossener eToro-Trade nicht exakt verknuepft: %s", exc)
        return {"status": "ERROR", "error": str(exc)[:300]}


def mark_reconciled_closed(trade_id: int, *, grund: str, zeit=None,
                           notiz: str = "", critical: bool = False) -> bool:
    """Verwaisten Ledger-Eintrag ohne erfundenen Kurs sauber schliessen.

    Diese Funktion wird nur nach zwei vollstaendigen Brokerabgleichen ohne
    Bestand verwendet. Preis und P&L bleiben absichtlich NULL: der Trade ist
    nicht mehr offen, aber ein fehlender historischer Fill wird nicht geraten.
    """
    try:
        init_ledger()
        with _LOCK, _connect() as con:
            cur = con.execute(
                """UPDATE trades
                   SET ausgestiegen_am=?, exit_grund=?, notiz=?,
                       reconciliation_status='CLOSED', reconciliation_updated_at=?
                   WHERE trade_id=? AND ausgestiegen_am IS NULL""",
                (_zeit(zeit), str(grund or "Brokerabgleich")[:200],
                 str(notiz or "")[:300], _jetzt(), int(trade_id)),
            )
            return bool(cur.rowcount)
    except Exception as exc:
        logger.warning("Verwaister Trade %s nicht abgeglichen: %s", trade_id, exc)
        if critical:
            raise RuntimeError("Ungeklaerter Abgang nicht dauerhaft gespeichert") from exc
        return False


def set_reconciliation_status(trade_id: int, status: str, *,
                              broker_position_id: str = "", notiz: str = "") -> bool:
    """Den Brokerbeweis eines offenen Trades ohne Handelswirkung aktualisieren."""
    try:
        init_ledger()
        target = str(status or "PENDING_CONFIRMATION").upper()[:64]
        with _LOCK, _connect() as con:
            cur = con.execute(
                """UPDATE trades SET reconciliation_status=?,
                       reconciliation_updated_at=?,
                       broker_position_id=CASE WHEN ?<>'' THEN ? ELSE broker_position_id END,
                       notiz=CASE WHEN ?<>'' THEN ? ELSE notiz END
                   WHERE trade_id=? AND ausgestiegen_am IS NULL""",
                (target, _jetzt(), str(broker_position_id or ""),
                 str(broker_position_id or "")[:160], str(notiz or ""),
                 str(notiz or "")[:300], int(trade_id)),
            )
            return bool(cur.rowcount)
    except Exception as exc:
        logger.warning("Reconciliation-Status fuer Trade %s nicht geschrieben: %s", trade_id, exc)
        return False


def set_protection(trade_id: int, *, algo_id: str = "",
                   client_order_id: str = "", status: str = "",
                   detail: str = "") -> bool:
    """Persistiert die Broker-Schutzidentitaet ohne Handelswirkung."""
    try:
        init_ledger()
        with _LOCK, _connect() as con:
            cur = con.execute(
                """UPDATE trades SET protection_algo_id=?,
                       protection_client_order_id=?, protection_status=?,
                       protection_detail=?, reconciliation_updated_at=?
                   WHERE trade_id=?""",
                (str(algo_id or "")[:160], str(client_order_id or "")[:32],
                 str(status or "")[:40], str(detail or "")[:300],
                 _jetzt(), int(trade_id)),
            )
            return bool(cur.rowcount)
    except Exception as exc:
        logger.warning("Schutzstatus fuer Trade %s nicht speicherbar: %s", trade_id, exc)
        return False


def dismiss_unproven_trade(trade_id: int, *, status: str, actor: str,
                           reason: str) -> bool:
    """Unbeweisbaren lokalen Hinweis aus offenen Ansichten entfernen.

    Der Datensatz bleibt als Auditspur erhalten, besitzt aber weder Ergebnis,
    erfundenen Verkaufszeitpunkt noch Verkaufskurs. Diese Funktion beruehrt
    keinen Broker.
    """
    target = str(status or "DISMISSED").upper()
    if target not in {"DISMISSED", "ACCOUNT_ASSET_CONFIRMED"}:
        return False
    try:
        init_ledger()
        note = f"{reason}; Benutzer={actor}"[:300]
        with _LOCK, _connect() as con:
            cur = con.execute(
                """UPDATE trades SET notiz=?, reconciliation_status=?,
                       reconciliation_updated_at=?
                   WHERE trade_id=? AND ausgestiegen_am IS NULL
                     AND reconciliation_status<>'CONFIRMED_OPEN'""",
                (note, target, _jetzt(), int(trade_id)),
            )
            return bool(cur.rowcount)
    except Exception as exc:
        logger.warning("Ungeklaerten Trade %s nicht ausblendbar: %s", trade_id, exc)
        return False


def confirm_open_trade(*, broker: str, symbol: str,
                       broker_position_id: str = "",
                       broker_account_fingerprint: str = "",
                       trade_id=None, entry_order_id: str = "") -> bool:
    """Genau einen lokalen Trade durch genau einen Brokerbestand bestaetigen.

    Ein Symbol ist bei eToro kein Positionsschluessel.  Der kompatible
    Symbol-Fallback bleibt nur fuer den eindeutigen Einzelfall erhalten;
    produktive Reconciliation uebergibt positionId und Kontofingerabdruck.
    """
    offen = offener_trade(
        broker, symbol, trade_id=trade_id,
        broker_position_id=broker_position_id,
        broker_account_fingerprint=broker_account_fingerprint,
        entry_order_id=entry_order_id)
    return bool(offen and set_reconciliation_status(
        int(offen["trade_id"]), "CONFIRMED_OPEN",
        broker_position_id=broker_position_id,
        notiz=("Aktueller Brokerbestand exakt bestaetigt" if broker_position_id else
               "Aktueller Brokerbestand bestaetigt")))


def mark_decision_reconciliation(decision_id: int, status: str, *,
                                 notiz: str = "", close_without_result: bool = False) -> int:
    """Alle offenen Ledgerzeilen einer Entscheidung anhand Brokerbeweis markieren.

    ``close_without_result`` wird nur bei exakt nachgewiesener geschlossener
    Broker-positionId verwendet. Ohne diesen Beweis bleibt die Zeile sichtbar
    unter "Klärung nötig", zählt aber nicht mehr als bestätigte offene Position.
    """
    try:
        init_ledger()
        target = str(status or "UNPROVABLE").upper()[:64]
        with _LOCK, _connect() as con:
            if close_without_result:
                cur = con.execute(
                    """UPDATE trades SET ausgestiegen_am=COALESCE(ausgestiegen_am, ?),
                           exit_grund=COALESCE(exit_grund, 'Brokerhistorie bestaetigt Schliessung'),
                           reconciliation_status='CLOSED', reconciliation_updated_at=?,
                           notiz=CASE WHEN ?<>'' THEN ? ELSE notiz END
                       WHERE decision_id=? AND ausgestiegen_am IS NULL""",
                    (_jetzt(), _jetzt(), str(notiz or ""), str(notiz or "")[:300],
                     int(decision_id)),
                )
            else:
                cur = con.execute(
                    """UPDATE trades SET reconciliation_status=?,
                           reconciliation_updated_at=?,
                           notiz=CASE WHEN ?<>'' THEN ? ELSE notiz END
                       WHERE decision_id=? AND ausgestiegen_am IS NULL""",
                    (target, _jetzt(), str(notiz or ""), str(notiz or "")[:300],
                     int(decision_id)),
                )
            return int(cur.rowcount or 0)
    except Exception as exc:
        logger.warning("Decision-Reconciliation %s nicht ins Ledger geschrieben: %s",
                       decision_id, exc)
        return 0


def hoechstkurs_melden(broker: str, symbol: str, kurs, *, trade_id=None,
                       broker_position_id: str = "",
                       broker_account_fingerprint: str = "",
                       entry_order_id: str = "") -> None:
    """MFE/MAE fortschreiben, solange die Position laeuft.

    Ohne diese Werte laesst sich spaeter nicht sagen, ob ein Stop zu eng
    sass oder ein Ziel zu weit weg.
    """
    try:
        preis = _zahl(kurs)
        offen = offener_trade(
            broker, symbol, trade_id=trade_id,
            broker_position_id=broker_position_id,
            broker_account_fingerprint=broker_account_fingerprint,
            entry_order_id=entry_order_id)
        if not preis or not offen:
            return
        einstieg = _zahl(offen.get("einstieg_preis"))
        if not einstieg:
            return
        veraenderung = 100.0 * (preis - einstieg) / einstieg
        mfe = _zahl(offen.get("mfe_pct"))
        mae = _zahl(offen.get("mae_pct"))
        neu_mfe = veraenderung if mfe is None else max(mfe, veraenderung)
        neu_mae = veraenderung if mae is None else min(mae, veraenderung)
        if neu_mfe == mfe and neu_mae == mae:
            return
        with _LOCK, _connect() as con:
            con.execute("UPDATE trades SET mfe_pct=?, mae_pct=? WHERE trade_id=?",
                        (neu_mfe, neu_mae, int(offen["trade_id"])))
    except Exception as exc:
        logger.debug("MFE/MAE nicht fortgeschrieben (%s %s): %s", broker, symbol, exc)


# ---------------------------------------------------------------------------
# Auswerten
# ---------------------------------------------------------------------------
def _kennzahlen(trades: list[dict]) -> dict:
    """Confirmed net statistics, with independent execution metrics.

    Nonempty datasets always have the documented metric keys. Unknown amounts
    are None, not zero. Historical estimates remain counted separately. Currency
    amounts are never added across currencies; ratios involving cash also stay
    undefined for mixed currency samples.
    """
    confirmed = [t for t in trades if confirmed_net(t)]
    n = len(confirmed)
    basis = {
        "trades": len(trades), "bewertbar": n,
        "ohne_ergebnis": len(trades) - n,
        "vorlaeufig": sum(finite_number(t.get("netto_pnl")) is not None
                          and not confirmed_net(t) for t in trades),
        "gebuehren_offen": sum(not fees_confirmed(t) for t in trades),
        "sample_quality": _sample_quality(n),
    }
    if not trades:
        return basis
    # Hold duration and execution slippage remain meaningful without fee data.
    slippagen = [v for t in trades
                  if (v := finite_number(t.get("slippage_geschaetzt"))) is not None]
    haltedauern = [v for t in trades
                   if (v := finite_number(t.get("haltedauer_minuten"))) is not None]
    basis.update({
        "trefferquote_pct": None, "erwartungswert": None, "summe_netto": None,
        "mittlerer_gewinn": None, "mittlerer_verlust": None,
        "profitfaktor": None, "max_drawdown": None, "gebuehren_summe": None,
        "gebuehren_quote_pct": None,
        "summe_nach_waehrung": {}, "gebuehren_nach_waehrung": {},
        "slippage_mittel_pct": round(sum(slippagen) / len(slippagen), 4)
                                if slippagen else None,
        "haltedauer_minuten_mittel": round(sum(haltedauern) / len(haltedauern), 1)
                                      if haltedauern else None,
    })
    if not confirmed:
        return basis
    currencies: dict[str, list[dict]] = {}
    for t in confirmed:
        currencies.setdefault(str(t.get("waehrung") or "UNBEKANNT").upper(), []).append(t)
    basis["summe_nach_waehrung"] = {
        c: round(sum(float(t["netto_pnl"]) for t in rows), 4)
        for c, rows in currencies.items()}
    basis["gebuehren_nach_waehrung"] = {
        c: (round(sum(float(t["gebuehren"]) for t in rows), 4)
            if all(finite_number(t.get("gebuehren")) is not None for t in rows) else None)
        for c, rows in currencies.items()}
    ergebnisse = [float(t["netto_pnl"]) for t in confirmed]
    gewinne = [x for x in ergebnisse if x > 0]
    verluste = [x for x in ergebnisse if x < 0]
    basis["trefferquote_pct"] = round(100.0 * len(gewinne) / n, 2)
    if len(currencies) != 1:
        return basis
    summe_gewinn, summe_verlust = sum(gewinne), abs(sum(verluste))
    kumuliert = hoch = drawdown = 0.0
    for t in sorted(confirmed, key=lambda t: (str(t.get("ausgestiegen_am") or ""),
                                             int(t.get("trade_id") or 0))):
        kumuliert += float(t["netto_pnl"])
        hoch = max(hoch, kumuliert)
        drawdown = max(drawdown, hoch - kumuliert)
    fees = next(iter(basis["gebuehren_nach_waehrung"].values()))
    gross_values = [finite_number(t.get("brutto_pnl")) for t in confirmed]
    brutto = sum(gross_values) if all(v is not None for v in gross_values) else None
    basis.update({
        "erwartungswert": round(sum(ergebnisse) / n, 4),
        "summe_netto": round(sum(ergebnisse), 4),
        "mittlerer_gewinn": round(summe_gewinn / len(gewinne), 4) if gewinne else None,
        "mittlerer_verlust": round(-summe_verlust / len(verluste), 4) if verluste else None,
        "profitfaktor": round(summe_gewinn / summe_verlust, 3) if summe_verlust > 0 else None,
        "max_drawdown": round(drawdown, 4), "gebuehren_summe": fees,
        "gebuehren_quote_pct": (round(100.0 * fees / abs(brutto), 2)
                                if fees is not None and brutto is not None
                                and abs(brutto) > 1e-9 else None),
    })
    return basis


def _gruppiere(trades: list[dict], schluessel: str) -> list[dict]:
    gruppen: dict[str, list[dict]] = {}
    for t in trades:
        wert = str(t.get(schluessel) or "").strip() or "UNBEKANNT"
        gruppen.setdefault(wert, []).append(t)
    ergebnis = [{"wert": k, **_kennzahlen(v)} for k, v in gruppen.items()]
    ergebnis.sort(key=lambda x: (-int(x["trades"]), str(x["wert"])))
    return ergebnis


def trade_snapshot(broker: str = "", tage: int = 90) -> dict:
    """Objektive Handelskennzahlen -- die Eingabe fuer Etappe B.

    Bewusst bereits aggregiert: ein Sprachmodell bekommt spaeter diesen
    Rueckgabewert und niemals die Datenbank. Ohne ``broker`` werden beide
    Domaenen getrennt UND zusammen ausgewiesen, weil Gebuehrenstruktur,
    Takt und Marktzeiten bei eToro und OKX verschieden sind.
    """
    try:
        init_ledger()
        lookback = max(1, int(tage))
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback)).isoformat()
        sql = ("SELECT * FROM trades WHERE ausgestiegen_am IS NOT NULL AND accounting_kind<>'RESIDUAL' "
               "AND superseded_by IS NULL AND ausgestiegen_am >= ?")
        werte: list[Any] = [cutoff]
        if broker:
            sql += " AND broker=?"
            werte.append(str(broker).lower())
        with _LOCK, _connect() as con:
            zeilen = [dict(r) for r in con.execute(sql, tuple(werte)).fetchall()]
            offene = con.execute(
                "SELECT COUNT(*) FROM trades WHERE ausgestiegen_am IS NULL "
                "AND reconciliation_status NOT IN "
                "('DISMISSED', 'ACCOUNT_ASSET_CONFIRMED')"
                + (" AND broker=?" if broker else ""),
                (str(broker).lower(),) if broker else (),
            ).fetchone()[0]
    except Exception as exc:
        logger.warning("Trade-Snapshot nicht berechenbar: %s", exc)
        return {"fehler": str(exc), "trades": 0}

    ausgabe = {
        "generiert_am": _jetzt(),
        "zeitraum_tage": lookback,
        "broker": str(broker).lower() or "alle",
        "offene_positionen": int(offene or 0),
        "gesamt": _kennzahlen(zeilen),
        "je_broker": _gruppiere(zeilen, "broker"),
        "je_symbol": _gruppiere(zeilen, "symbol")[:40],
        "je_marktphase": _gruppiere(zeilen, "marktphase"),
        "je_exit_grund": _gruppiere(zeilen, "exit_grund"),
        "je_strategieversion": _gruppiere(zeilen, "strategie_version"),
        "aktuelle_strategieversion": _version(),
        "methodenhinweis": (
            "Nur geschlossene Trades mit bestätigtem Netto gehen in die Ergebniskennzahlen ein. "
            "Unbekannte Gebühren bleiben unbekannt; vorhandene alte Schätzwerte sind vorläufig. "
            "Geldbeträge verschiedener Währungen werden nicht addiert. "
            "Trades ohne bekannten Einstand erscheinen "
            "als 'ohne_ergebnis' und gehen NICHT in Trefferquote, Erwartungswert "
            "oder Profitfaktor ein. Der Profitfaktor ist ohne einen einzigen "
            "Verlusttrade nicht definiert und wird dann nicht ausgewiesen. "
            "Slippage ist eine Schaetzung aus der "
            "Differenz zwischen Referenzkurs der Entscheidung und tatsaechlichem "
            "Fill, keine Broker-Messung."
        ),
    }
    return ausgabe


def offene_trades(broker: str = "") -> list[dict]:
    """Laufende Positionen im Ledger -- fuer Abgleich und Diagnose."""
    try:
        init_ledger()
        sql = ("SELECT * FROM trades WHERE ausgestiegen_am IS NULL "
               "AND superseded_by IS NULL "
               "AND reconciliation_status NOT IN "
               "('DISMISSED', 'ACCOUNT_ASSET_CONFIRMED')")
        werte: tuple = ()
        if broker:
            sql += " AND broker=?"
            werte = (str(broker).lower(),)
        sql += " ORDER BY eingestiegen_am ASC"
        with _LOCK, _connect() as con:
            rows = [dict(r) for r in con.execute(sql, werte).fetchall()]
        from okx_account_context import active_rows
        return active_rows(rows)
    except Exception as exc:
        logger.debug("Offene Trades nicht lesbar: %s", exc)
        return []


def trade_liste(*, broker: str = "", tage: int = 90, limit: int = 250) -> list[dict]:
    """Chronologische Historie fuer die rein lesende WebUI-Auswertung."""
    try:
        init_ledger()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, int(tage)))).isoformat()
        # 9.5.3: Eine zusammengefuehrte Altzeile bleibt als Auditspur in der
        # Datenbank, darf aber weder angezeigt noch mitgerechnet werden --
        # sonst stuende das doppelte Ergebnis weiter im Tagesbericht.
        # Open inventory is independent of the history filter and cannot be
        # hidden by a long holding period or a large number of recent exits.
        scope = " AND broker=?" if broker else ""
        domain = [str(broker).lower()] if broker else []
        with _LOCK, _connect() as con:
            active = con.execute("SELECT * FROM trades WHERE ausgestiegen_am IS NULL AND superseded_by IS NULL" + scope, domain).fetchall()
            closed = con.execute("SELECT * FROM trades WHERE ausgestiegen_am>=? AND superseded_by IS NULL AND accounting_kind<>'RESIDUAL'" + scope +
                                 " ORDER BY ausgestiegen_am DESC,trade_id DESC LIMIT ?",
                                 [cutoff, *domain, max(1,min(1000,int(limit)))]).fetchall()
        return [dict(row) for row in [*active,*closed]]
    except Exception as exc:
        logger.debug("Trade-Liste nicht lesbar: %s", exc)
        return []


def trade_detail(trade_id: int) -> Optional[dict]:
    """Ein Ledger-Trade anhand seiner unveraenderlichen ID."""
    try:
        init_ledger()
        with _LOCK, _connect() as con:
            row = con.execute("SELECT * FROM trades WHERE trade_id=?",
                              (int(trade_id),)).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.debug("Trade %s nicht lesbar: %s", trade_id, exc)
        return None


def trade_group(*, broker: str, symbol: str, eingestiegen_am: str,
                account_fingerprint: str, paper: bool,
                instrument_id: str, entry_order_id: str) -> list[dict]:
    """Teilfills desselben Einstiegs fuer einen nachtraeglichen Brokerabgleich."""
    try:
        init_ledger()
        with _LOCK, _connect() as con:
            rows = con.execute(
                """SELECT * FROM trades WHERE broker=? AND symbol=?
                       AND eingestiegen_am=? AND broker_account_fingerprint=?
                       AND paper=? AND broker_position_id=? AND entry_order_id=?
                       AND superseded_by IS NULL ORDER BY trade_id ASC""",
                (str(broker or "").lower(), str(symbol or "").upper(),
                 str(eingestiegen_am or ""), str(account_fingerprint), int(paper),
                 str(instrument_id), str(entry_order_id)),
            ).fetchall()
        return [dict(x) for x in rows]
    except Exception as exc:
        logger.debug("Tradegruppe nicht lesbar: %s", exc)
        return []


__all__ = ["init_ledger", "trade_open", "trade_close", "mark_reconciled_closed",
           "set_reconciliation_status", "set_protection", "confirm_open_trade", "trade_snapshot",
           "mark_decision_reconciliation", "dismiss_unproven_trade",
           "offener_trade", "offene_trades", "trade_liste", "trade_detail",
           "trade_group",
           "hoechstkurs_melden", "EXIT_UNBEKANNT"]


def _confirm_execution_accounting_on(con, trade_id, side, *, order_id="", client_id=""):
    """Ledger result and lifecycle receipt share a single commit (NEXUS 10).

    Risk/position/journal consumers keep their existing idempotent recovery;
    this helper performs no external side effects and creates no new queue.
    """
    row = con.execute("SELECT * FROM trades WHERE trade_id=?", (int(trade_id),)).fetchone()
    if not row:
        raise LedgerZuordnungUnklar("Ledgerquittung nicht lesbar")
    from execution_lifecycle import confirm_accounted_on
    confirm_accounted_on(con, broker=row["broker"],
        account=row["broker_account_fingerprint"],
        environment="DEMO" if row["paper"] else "LIVE",
        order_id=order_id or row["entry_order_id" if side=="BUY" else "exit_order_id"],
        client_id=client_id if side=="BUY" else "")


def trade_open(**kwargs):
    return _trade_open(**kwargs)


def trade_close(**kwargs):
    return _trade_close(**kwargs)
