"""Krypto-Handelsmaschine fuer OKX -- laeuft rund um die Uhr (v8.1.1 NEXUS).

ABGRENZUNG
==========
Diese Maschine ist bewusst ein EIGENER Prozessteil neben dem bestehenden
Aktienkern (live_trader.py). Gruende:

    1. Der Aktienkern ist erprobt. Ein Umbau seiner Hauptschleife waere das
       groesste Regressionsrisiko des ganzen Projekts.
    2. Krypto hat einen voellig anderen Takt (Minuten statt Stunden) und
       darf nicht auf einen langsamen Aktienzyklus warten.
    3. Faellt eine Seite aus, laeuft die andere weiter.

WAS GETEILT WIRD
================
    Candidate Gate      identische deterministische Kaufkaskade
    Strategie           dieselben Indikatoren und Signalregeln
    Kostenrechnung      dieselbe Netto-Edge-Pruefung, mit OKX-Gebuehren
    Entscheidungslog    dasselbe Journal, ergaenzt um Quellenangaben

WAS GETRENNT IST
================
    Kontostand, Risikogrenzen, Tagesbremse, Universum, Takt.

SICHERHEITSREIHENFOLGE JE KAUF
==============================
    Universum -> Signal -> Stop/Ziel -> Groesse -> Kosten -> Candidate Gate
    -> Order -> Broker-Schutz (OCO) -> Buchung -> Protokoll

Die KI kommt in dieser Kette NICHT vor. Sie beeinflusst ausschliesslich,
WELCHE Werte ueberhaupt beobachtet werden.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import config
from broker.base import (
    AuthentifizierungsFehler, BrokerFehler, OrderStatusUnklar, VerbindungVerloren,
)
from broker.okx import (base_currency, client_order_id_for_decision,
                        normalize_inst_id, okx_fill_identity)
from contracts import Instrument, SimpleContract
from decision_source import (
    BROKER, CANDIDATE_GATE, DAFUER, DAGEGEN, Entscheidungsprotokoll, LUNA,
    MARKT, NEUTRAL, NUTZER, RISK_GATE, TECHNIK, UNIVERSE, speichere as speichere_quellen,
)
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock
from decision_analytics import (mark_execution, record_order_result,
                                record_system_error)
from scheduler_v7 import POSITIONEN, SCAN, UNIVERSUM, Taktgeber

logger = logging.getLogger(__name__)


def _tickeralter_grenze(cfg) -> float:
    """Ab welchem Alter ein Krypto-Ticker nicht mehr als frisch gilt.

    Seit v8.1.5 in der WebUI einstellbar und ohne Neustart wirksam. Die
    Schwelle entscheidet nur, ob ein Kurs BENUTZT wird -- sie kann keine
    Order groesser oder ungeschuetzter machen.
    """
    try:
        import live_settings
        wert = live_settings.handelsschwellen().get("CRYPTO_TICKER_MAX_AGE_SECONDS")
        if wert is not None:
            return max(5.0, float(wert))
    except Exception:
        logger.debug("Tickeraltergrenze nicht lesbar; Standard aus config", exc_info=True)
    return max(5.0, float(getattr(cfg, "CRYPTO_TICKER_MAX_AGE_SECONDS", 120)))


def _fillzeitpunkt(ergebnis) -> str:
    """Der Zeitstempel des FRUEHESTEN Fills einer Order (ISO, UTC).

    OKX liefert je Fill ein ``ts``. Ohne diesen Wert wuerde die Haltedauer ab
    der lokalen Verbuchung laufen -- nach einem Absturz mit nachtraeglicher
    Aufklaerung waere das die falsche Uhr.
    """
    zeiten = []
    for fill in (getattr(ergebnis, "fills", None) or []):
        roh = None
        if isinstance(fill, dict):
            roh = fill.get("fillTime") or fill.get("ts") or fill.get("timestamp") or fill.get("zeit")
        else:
            roh = (getattr(fill, "ts", None) or getattr(fill, "timestamp", None))
        if roh in (None, ""):
            continue
        try:
            if isinstance(roh, (int, float)) or str(roh).isdigit():
                ms = float(roh)
                if ms <= 0:
                    continue
                zeiten.append(datetime.fromtimestamp(ms / 1000.0, timezone.utc))
            else:
                stempel = datetime.fromisoformat(str(roh).replace("Z", "+00:00"))
                zeiten.append(stempel if stempel.tzinfo else
                              stempel.replace(tzinfo=timezone.utc))
        except (TypeError, ValueError, OSError, OverflowError):
            continue
    return min(zeiten).isoformat() if zeiten else ""


def _kerzengroesse_sekunden(text: str) -> float:
    """"15 mins" -> 900. Unbekanntes ergibt 900 als sichere Vorgabe."""
    roh = str(text or "").strip().lower()
    zahl = "".join(ch for ch in roh if ch.isdigit())
    try:
        wert = float(zahl) if zahl else 15.0
    except ValueError:
        wert = 15.0
    if "hour" in roh or "std" in roh or roh.endswith("h"):
        return wert * 3600.0
    if "day" in roh or "tag" in roh or roh.endswith("d"):
        return wert * 86400.0
    if "sec" in roh:
        return wert
    return wert * 60.0


def _ist_positiv(wert) -> bool:
    """Echte positive Zahl? NaN und Unendlich fallen durch.

    ``float("nan") <= 0`` ist False -- ein NaN-Kontostand liefe damit durch
    jede Fallunterscheidung hindurch und gaelte als "stimmt mit dem Buch
    ueberein".
    """
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return False
    return math.isfinite(zahl) and zahl > 0


def meldungen_sicherheit(titel: str, text: str, *, handlung: str = "") -> str:
    """Sicherheitsmeldung -- lokal gekapselt, damit ein Importfehler nie
    einen Verkaufspfad abbricht."""
    try:
        import meldungen
        return meldungen.sicherheit(titel, text, handlung=handlung)
    except Exception:
        return f"SICHERHEIT · {titel}\n{text}" + (f"\nWas jetzt: {handlung}" if handlung else "")


def _state_root() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or
                Path(__file__).resolve().parent)


# ---------------------------------------------------------------------------
# Positionsbuch
# ---------------------------------------------------------------------------
# Herkunft und Verwaltungsmodus einer Position. Bewusst dieselbe Idee wie auf
# der Aktienseite (position_manager: source / management_mode): Was der Bot
# nicht selbst gekauft hat, verkauft er auch nicht.
HERKUNFT_BOT = "BOT"
HERKUNFT_BROKER = "BROKER_BESTAND"
VERWALTUNG_AUTO = "AUTO"
VERWALTUNG_MANUELL = "MANUELL"
VERWALTUNG_BEOBACHTEN = "BEOBACHTEN"

# Unterhalb dieser Abweichung gilt Kontostand == Buchmenge (Rundung, Staub).
MENGEN_TOLERANZ = 0.02


def fill_anchored_stop_take(*, strategy_mode: str, fill_price: float,
                            filled_quantity: float, entry_fee_quote: float,
                            exit_fee_pct: float, signal_price: float,
                            planned_stop: float, planned_take: float) -> tuple[float, float]:
    """Verankert Schutzwerte ausschliesslich am echten Broker-Fill.

    Die Funktion ist rein und damit gegen die reale DOGE-Fehlerklasse
    testbar: Weder Signalkurs noch ein Kurs aus einem anderen Quote-Markt
    duerfen im Freqtrade-Modus Stop oder ROI-Ziel bestimmen.
    """
    fill = float(fill_price or 0.0)
    quantity = float(filled_quantity or 0.0)
    if fill <= 0 or quantity <= 0:
        raise ValueError("Fillpreis und Fillmenge muessen positiv sein")
    if str(strategy_mode or "") == "FREQTRADE_SAMPLE":
        from freqtrade_sample_strategy import STOPLOSS, roi_exit_price
        entry_fee_pct = max(0.0, float(entry_fee_quote or 0.0) / (fill * quantity))
        return (
            fill * (1.0 + float(STOPLOSS)),
            roi_exit_price(fill, 0.04, entry_fee_pct=entry_fee_pct,
                           exit_fee_pct=float(exit_fee_pct or 0.0)),
        )
    signal = float(signal_price or 0.0)
    return (
        fill * (float(planned_stop or 0.0) / signal) if signal > 0 else float(planned_stop or 0.0),
        fill * (float(planned_take or 0.0) / signal) if signal > 0 else float(planned_take or 0.0),
    )



@dataclass
class KryptoPosition:
    """Eine offene Kryptoposition mit ihren Schutzwerten."""
    symbol: str
    inst_id: str
    menge: float
    einstieg: float
    stop: float
    take_profit: float
    eroeffnet_am: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    entry_order_created_at: str = ""
    order_id: str = ""
    referenz: str = ""
    broker_schutz: bool = False
    hoechstkurs: float = 0.0
    paper: bool = True
    # Ab v8.1.4: Woher stammt die Position, und darf der Bot sie automatisch
    # verwalten? Die Aktienseite fuehrt dieselbe Unterscheidung seit v6
    # (source / management_mode). Ohne sie hat der Bot am 25.08.2026 einen
    # Fremdbestand von 0,94 BTC mitverkauft.
    herkunft: str = HERKUNFT_BOT
    verwaltung: str = VERWALTUNG_AUTO
    verwaltungsnotiz: str = ""
    # v9.2: WARUM die Verwaltung eingeschraenkt ist -- getrennt von der Frage,
    # OB die Position dem Bot gehoert. "" heisst: kein Sonderfall.
    management_status: str = ""
    # v9.2: Zu WELCHEM OKX-Konto der Bestand gehoert. Ohne dieses Feld lief
    # die Kontopruefung in strategy_migration ins Leere -- ein Bestand aus
    # dem Demokonto haette nach einem Umschalten auf Live migriert werden
    # koennen.
    account_fingerprint: str = ""
    # v9.2: Nachweis einer ausdruecklichen Strategie-Migration. Ohne diese
    # Felder waere ein Hashwechsel im Nachhinein nicht nachvollziehbar.
    strategy_migrated_at: str = ""
    strategy_migrated_from_version: str = ""
    strategy_migrated_from_hash: str = ""
    strategy_migration_reason: str = ""
    strategy_migration_actor: str = ""
    decision_id: int | None = None
    # Immutable strategy identity from the entry fill. The global runtime
    # switch never rewrites these fields.
    entry_strategy_mode: str = "NEXUS_STANDARD"
    strategy_name: str = "NEXUS Standard Krypto"
    strategy_version: str = "NEXUS-STANDARD-LEGACY"
    strategy_parameter_hash: str = ""
    strategy_parameters: dict = field(default_factory=dict)
    trade_quote_ccy: str = ""
    # Ein OKX-Spot-Guthaben ist keine Position. Erst diese unveraenderliche
    # Brokerkette beweist, dass genau diese Teilmenge vom Bot gekauft wurde.
    client_order_id: str = ""
    order_tag: str = ""
    fill_ids: list[str] = field(default_factory=list)
    ownership_verified: bool = False
    entry_fee_by_currency: dict = field(default_factory=dict)
    entry_fee_quote: float = 0.0
    protection_algo_id: str = ""
    protection_client_order_id: str = ""
    protection_status: str = "PENDING"
    last_confirmed_protection: dict = field(default_factory=dict)
    broker_state: str = ""
    manual_changed_at: str = ""
    manual_changed_by: str = ""
    manual_exit_command_id: str = ""
    manual_exit_previous: dict = field(default_factory=dict)
    exit_state: str = "IDLE"
    exit_retry_after: str = ""
    exit_last_notice_at: str = ""
    exit_last_detail: str = ""
    exit_attempt_id: str = ""
    exit_started_at: str = ""
    exit_client_order_id: str = ""
    exit_order_ids: list[str] = field(default_factory=list)
    exit_fill_ids: list[str] = field(default_factory=list)
    exit_recovery_checks: int = 0
    # 9.5.8: Fehlversuche des Verkaufs IN FOLGE -- Grundlage der wachsenden
    # Wartezeit und der Eskalationsmeldung. Bewusst getrennt von
    # ``exit_recovery_checks``, das die Klaerungsversuche eines UNKLAREN
    # Ausstiegs zaehlt. Wird bei jedem erfolgreichen Verkauf zurueckgesetzt.
    exit_fehlversuche: int = 0
    first_missing_at: str = ""
    consecutive_missing_snapshots: int = 0
    # 10.6.0: Der Gegenzaehler. Ein wiedergesehener Bestand braucht dieselbe
    # Bestaetigung wie ein fehlender -- sonst wackelt der Zustand bei jedem
    # unvollstaendigen Schnappschuss hin und her.
    first_present_at: str = ""
    consecutive_present_snapshots: int = 0

    @property
    def darf_automatisch_verkaufen(self) -> bool:
        """Nur eigene Positionen unter Automatik werden vom Bot geschlossen."""
        from crypto_strategy_mode import strategy_is_resolved
        return (not self.broker_state and str(self.herkunft).upper() == HERKUNFT_BOT
                and str(self.verwaltung).upper() == VERWALTUNG_AUTO
                and bool(self.ownership_verified)
                and bool(str(self.order_id or "").strip())
                and bool(str(self.client_order_id or self.referenz or "").strip())
                and bool([x for x in self.fill_ids if str(x).strip()])
                and strategy_is_resolved(self.strategy_snapshot()))

    @property
    def ist_bewiesene_botposition(self) -> bool:
        return (str(self.herkunft).upper() == HERKUNFT_BOT
                and bool(self.ownership_verified)
                and bool(str(self.order_id or "").strip())
                and bool(str(self.client_order_id or self.referenz or "").strip())
                and bool([x for x in self.fill_ids if str(x).strip()]))

    @property
    def darf_schutz_ausfuehren(self) -> bool:
        """Darf der Bot fuer diese Position selbst VERKAUFEN?

        Das ist eine Frage der Verwaltung, nicht des Eigentums. Eine pausierte
        Position wird weiter ueberwacht und geschuetzt -- verkauft wird sie
        nicht (siehe ``safety_monitoring_enabled``).
        """
        return not self.broker_state and self.ist_bewiesene_botposition and str(self.verwaltung).upper() in {
            VERWALTUNG_AUTO, VERWALTUNG_MANUELL}

    # -----------------------------------------------------------------------
    # v9.2: Drei getrennte Fragen statt einer vermischten
    # -----------------------------------------------------------------------
    # In v9.1 hing der Eigentumsnachweis am Verwaltungsmodus. Eine Aenderung
    # von STARTUP_CANDLES hat deshalb den Strategie-Hash veraendert, die
    # Position auf BEOBACHTEN gesetzt -- und damit BNB, LINK und XLM zu
    # "externem Bestand ohne ID-Kette" gemacht, obwohl orderId, clOrdId und
    # echte tradeIds vollstaendig vorlagen.
    #
    # DIE REGEL, die das kuenftig verhindert:
    #   In den Eigentumsnachweis darf NICHTS eingehen, was ein
    #   Software-Update aendern kann.
    # Der Nachweis besteht ausschliesslich aus dem, was der Broker vergeben
    # hat. Strategie, Parameter und Verwaltungsmodus bestimmen hoechstens,
    # WELCHE Logik verwalten darf -- niemals, OB die Position dem Bot gehoert.

    @property
    def ownership_chain_complete(self) -> bool:
        """Gehoert diese Position nachweislich dem Bot?

        Nur brokerseitig vergebene Werte. Ueberlebt jedes Update.
        """
        return self.ist_bewiesene_botposition

    @property
    def blocks_reentry(self) -> bool:
        """Sperrt diese Position einen erneuten Einstieg in denselben Wert?

        Bewusst unabhaengig vom Verwaltungsmodus: auch eine pausierte oder
        manuell verwaltete Botposition darf nicht ein zweites Mal gekauft
        werden.
        """
        return self.ownership_chain_complete and float(self.menge or 0.0) > 0

    @property
    def safety_monitoring_enabled(self) -> bool:
        """Laeuft die Sicherheitsueberwachung fuer diese Position?

        Mengenabgleich, Erkennung externer Verkaeufe, Pruefung und
        Wiederherstellung des Broker-Schutzes und die Anzeige laufen fuer
        JEDE bewiesene Botposition -- auch fuer eine pausierte. Nur das
        automatische VERKAUFEN haengt am Verwaltungsmodus.
        """
        return self.ownership_chain_complete and str(self.verwaltung).upper() in {
            VERWALTUNG_AUTO, VERWALTUNG_MANUELL, VERWALTUNG_BEOBACHTEN}

    @property
    def strategy_execution_enabled(self) -> bool:
        """Darf die Einstiegsstrategie Ausgaenge ausloesen?"""
        from crypto_strategy_mode import strategy_is_resolved
        return (self.ownership_chain_complete
                and str(self.verwaltung).upper() == VERWALTUNG_AUTO
                and strategy_is_resolved(self.strategy_snapshot()))

    def strategy_snapshot(self) -> dict:
        return {
            "entry_strategy_mode": str(self.entry_strategy_mode or ""),
            "strategy_name": str(self.strategy_name or ""),
            "strategy_version": str(self.strategy_version or ""),
            "parameter_hash": str(self.strategy_parameter_hash or ""),
            "parameters": dict(self.strategy_parameters or {}),
        }

    def pausiere(self, grund: str, *, status: str = "") -> None:
        """Automatik aus -- Ueberwachung und Schutz laufen weiter.

        v9.2: ``status`` benennt den Grund maschinenlesbar. Der
        Eigentumsnachweis bleibt davon unberuehrt.
        """
        self.verwaltung = VERWALTUNG_BEOBACHTEN
        self.verwaltungsnotiz = str(grund)[:300]
        self.management_status = str(status or "")[:60]

    def manuell(self, actor: str, grund: str) -> None:
        self.verwaltung = VERWALTUNG_MANUELL
        self.verwaltungsnotiz = str(grund)[:300]
        self.manual_changed_at = datetime.now(timezone.utc).isoformat()
        self.manual_changed_by = str(actor or "")[:80]

    def als_dict(self) -> dict:
        return asdict(self)


class KryptoPositionsbuch:
    """Persistentes Positionsbuch der Kryptoseite.

    OKX kennt bei Spot keine 'Position' mit Einstandspreis -- nur Guthaben.
    Einstieg, Stop und Ziel muss der Bot deshalb selbst fuehren. Ohne
    Persistenz waeren diese Werte nach einem Neustart verloren und der Bot
    wuesste nicht mehr, wo sein Stop liegt.
    """

    def __init__(self, datei: Optional[Path] = None):
        self.datei = Path(datei or _state_root() / "crypto_positions.json")
        self.positionen: dict[str, KryptoPosition] = {}
        # 9.5.5: Eintraege, die NEXUS nicht lesen kann. Sie werden unveraendert
        # aufbewahrt und mitgeschrieben, damit ein Bestand nie stumm aus der
        # Datei faellt.
        self.unlesbar: list[dict] = []
        self._lock = threading.RLock()
        self.laden()

    def laden(self) -> None:
        if not self.datei.exists():
            return
        try:
            with critical_state_lock(self.datei):
                roh = json.loads(self.datei.read_text(encoding="utf-8"))
            if not isinstance(roh, dict) or not isinstance(roh.get("positionen", []), list):
                raise ValueError("ungueltiges Positionsbuch-Schema")
        except Exception as exc:
            logger.error("Krypto-Positionsbuch unlesbar; Geldpfad gesperrt: %s", exc)
            raise RuntimeError(
                "Krypto-Positionsbuch ist unlesbar; keine OKX-Order darf ausgefuehrt werden"
            ) from exc
        with self._lock:
            self.positionen = {}
            self.unlesbar = []
            for eintrag in roh.get("positionen", []):
                try:
                    p = KryptoPosition(**{k: v for k, v in eintrag.items()
                                          if k in KryptoPosition.__dataclass_fields__})
                    if (str(eintrag.get("herkunft") or HERKUNFT_BOT).upper() == HERKUNFT_BOT
                            and not str(eintrag.get("entry_strategy_mode") or "")):
                        # A pre-9.0 position has no persistent strategy proof.
                        # Never guess its exit engine during an automatic migration.
                        p.entry_strategy_mode = ""
                        p.strategy_name = ""
                        p.strategy_version = ""
                        p.strategy_parameter_hash = ""
                        p.strategy_parameters = {}
                        p.pausiere("Legacy-Position ohne belegte Einstiegsstrategie; explizite Migration erforderlich")
                    # Vor 9.0.9 wurde aus der orderId eine kuenstliche Fill-ID
                    # gebaut. Sie ist kein OKX-tradeId-Beweis und darf keine
                    # Verkaufsautomatik freischalten.
                    p.fill_ids = [str(x) for x in (p.fill_ids or [])
                                  if str(x).strip() and not str(x).startswith("okx-entry:")]
                    # v9.4.1 speicherte die OKX-tradeId global. OKX garantiert
                    # sie jedoch nicht brokerweit eindeutig (LINK und ONDO
                    # konnten dieselbe Roh-ID tragen). Mit vorhandener
                    # Konto-/Instrument-/Orderkette wird sie beim Laden
                    # deterministisch in die neue zusammengesetzte Identitaet
                    # migriert; ohne diese Kette wird nichts geraten.
                    if p.account_fingerprint and p.inst_id and p.order_id:
                        p.fill_ids = [
                            x if x.startswith("okx:") else
                            f"okx:{p.account_fingerprint}:{normalize_inst_id(p.inst_id)}:{p.order_id}:{x}"
                            for x in p.fill_ids]
                    p.ownership_verified = bool(
                        p.ownership_verified and p.order_id
                        and (p.client_order_id or p.referenz) and p.fill_ids)
                    self.positionen[p.symbol.upper()] = p
                except Exception:
                    # KORREKTUR 9.5.5: Hier wurde ein defekter Eintrag STILL
                    # uebersprungen -- und beim naechsten speichern() endgueltig
                    # geloescht, weil gespeichert wird, was im Speicher steht.
                    # Eine Position konnte damit spurlos verschwinden: kein
                    # Ledgerabschluss, keine Risikobuchung, keine Meldung, nur
                    # eine debug-Zeile. Die Schutzorder beim Broker blieb
                    # bestehen. Das ist der einzige Verlustpfad ohne jede Spur.
                    #
                    # Der Rohdatensatz wird jetzt aufbewahrt und beim Speichern
                    # unveraendert wieder mitgeschrieben. Lieber ein Eintrag,
                    # den NEXUS nicht versteht, als ein Bestand, von dem
                    # niemand mehr weiss.
                    self.unlesbar.append(dict(eintrag) if isinstance(eintrag, dict)
                                         else {"rohwert": repr(eintrag)})
                    logger.warning(
                        "Positionseintrag nicht lesbar und deshalb unveraendert "
                        "aufbewahrt (Symbol %r). Er wird NICHT geloescht.",
                        (eintrag or {}).get("symbol") if isinstance(eintrag, dict) else None,
                        exc_info=True)

    def speichern(self) -> None:
        with self._lock:
            eintraege = [p.als_dict() for p in self.positionen.values()]
            # Nicht lesbare Eintraege wandern unveraendert zurueck in die Datei.
            eintraege.extend(dict(x) for x in self.unlesbar)
            payload = {"version": 2, "gespeichert": datetime.now(timezone.utc).isoformat(),
                       "positionen": eintraege}
        with critical_state_lock(self.datei):
            atomic_write_json(self.datei, payload)

    def setze(self, position: KryptoPosition) -> None:
        with self._lock:
            self.positionen[position.symbol.upper()] = position
        self.speichern()

    def hole(self, symbol: str) -> Optional[KryptoPosition]:
        with self._lock:
            return self.positionen.get(str(symbol).upper())

    def entferne(self, symbol: str) -> None:
        with self._lock:
            self.positionen.pop(str(symbol).upper(), None)
        self.speichern()

    def symbole(self) -> list[str]:
        with self._lock:
            return sorted(self.positionen)

    def alle(self) -> list[KryptoPosition]:
        with self._lock:
            return list(self.positionen.values())

    def aktive(self) -> list[KryptoPosition]:
        """Nur exakt belegte, automatisch verwaltete Botpositionen.

        OKX-Spot-Salden und pausierte Legacy-Hinweise sind keine offenen
        Trades und duerfen weder Positionszaehler noch WebUI befuellen.
        """
        with self._lock:
            return [p for p in self.positionen.values()
                    if p.darf_schutz_ausfuehren]


# ---------------------------------------------------------------------------
# Handelsmaschine
# ---------------------------------------------------------------------------
class CryptoEngine:
    """Fuehrt Universumspflege, Signalsuche und Positionsueberwachung aus."""

    def __init__(self, *, hub, risiko, universum, taktgeber: Optional[Taktgeber] = None,
                 ai_router=None, melder=None, cfg=None):
        self.cfg = cfg or config
        self.hub = hub
        self.risiko = risiko
        self.universum = universum
        self.takt = taktgeber or Taktgeber(cfg=self.cfg)
        self.ai = ai_router
        # Symbole, deren Fremdbestand bereits gemeldet wurde -- damit die
        # Meldung nicht in jedem Minutentakt erneut kommt.
        self._gemeldeter_ueberhang: set = set()
        # 9.5.4: Schutzorders im Konto ohne Position im Buch.
        self._verwaiste_schutz: list = []
        self._gemeldete_verwaiste_schutz: set = set()
        # 9.5.5: gesetzt, wenn die Schutzorders beim Broker nicht abrufbar
        # waren. Nicht abrufbar heisst nicht "keine" -- Einstiege bleiben dann
        # gesperrt, bis die Lage geklaert ist.
        self._schutzorders_unlesbar: str = ""
        # Wie oft in Folge wurde fuer ein Symbol kein Guthaben gemeldet?
        # Ein einzelner Ausfall darf keine Position abrechnen.
        self._fehlender_bestand: dict = {}
        # Zweifache Bestaetigung fuer offene Ledger-Zeilen, zu denen weder
        # Positionsbuch noch Brokerbestand existieren. Ein einzelner leerer
        # Snapshot darf die Historie nicht schliessen.
        # 10.8.1: je trade_id (Anzahl Fehlmessungen, erste Fehlmessung ISO).
        # Bis 10.8.0 wurde der Zaehler nur geleert, nie gezaehlt -- die erste
        # Fehlmessung buchte sofort einen Bestandsbeleg (XRP-Rest 85 am
        # 19.09.2026, 05:20 UTC, aus einem nicht lesbaren Schnappschuss).
        self._fehlender_ledger_bestand: dict[int, tuple[int, str]] = {}
        # 10.8.1: Rueckweg fuer Ledgerzeilen ohne Position -- (Anzahl
        # Wiedersichten, erste Wiedersicht ISO) je trade_id.
        self._wiedergesehener_ledger_bestand: dict[int, tuple[int, str]] = {}
        # 10.8.1: (trade_id, ordId) einer Historienbuchung, die der Ledger
        # abgelehnt hat. Ein Neuversuch bringt keine neuen Daten.
        self._historie_abgelehnt: set[tuple[int, str]] = set()
        # 10.8.1: letzter Ablehnungsgrund je Restzeile -- Warnung nur bei
        # Aenderung, sonst DEBUG.
        self._rest_abgelehnt: dict[int, str] = {}
        # Handelsbereitschaft: neue Kaeufe erst nach Anlaufsperre und wenn
        # alle fachlichen Voraussetzungen erfuellt sind (v8.1.4).
        from trading_ready import Handelsbereitschaft
        self.bereitschaft = Handelsbereitschaft("okx", cfg=self.cfg, melder=self._melde)
        self.melder = melder
        self.buch = KryptoPositionsbuch()
        self.letzter_universumslauf: Optional[dict] = None
        self.letzter_fehler = ""
        self._exposure_sperre: list[str] = []
        # 10.2.0: Werte, deren eigener Schutz-Exit gerade abgerechnet wird.
        self._exit_in_progress_symbole: set[str] = set()
        # 9.5.8: Welche Waehrungen die Sperre ausgeloest haben. Nur sie werden
        # gesperrt; alle anderen Coins handeln weiter.
        self._exposure_gesperrte_waehrungen: list[str] = []
        # Guthabenstand des laufenden Abgleichs -- eine Zahl fuer Mengenpruefung
        # und Exposure-Klassifizierung.
        self._guthaben_dieser_runde: dict = {}
        # Die Klassifizierung ist nicht nur ein Logeintrag: Die WebUI muss
        # klar zeigen, dass OKX-Demo-Startguthaben keine offenen Bot-Orders
        # sind. Bis zur ersten Reconciliation bleibt die Anzeige leer statt
        # etwas zu erfinden.
        self._letzte_exposure: dict = {}
        self.zyklen = 0
        self._selector = None
        self._last_exposure_alert: tuple[str, float] = ("", 0.0)

    # -- Zugriffe -----------------------------------------------------------
    @property
    def broker(self):
        return self.hub.broker("okx")

    @property
    def topf(self):
        return self.risiko.topf("okx")

    def _selektor(self):
        if self._selector is None:
            from universe.crypto_selector import CryptoUniverseSelector
            broker = self.broker
            if broker is None:
                return None
            self._selector = CryptoUniverseSelector(broker.client, cfg=self.cfg)
        return self._selector

    def _instrument(self, symbol: str, inst_id: str = "") -> Instrument:
        symbol = str(symbol).upper()
        broker = self.broker
        inst_id = str(inst_id or "").upper()
        if not inst_id:
            member = self.universum.zustand.hole("okx", symbol)
            inst_id = str(getattr(member, "inst_id", "") or "").upper()
        quote = (inst_id.rsplit("-", 1)[-1] if "-" in inst_id else
                 str(getattr(broker, "quote_ccy", "EUR")))
        inst_id = inst_id or normalize_inst_id(symbol, quote)
        contract = SimpleContract(symbol, quote, localSymbol=inst_id)
        return Instrument(name=symbol, contract=contract,
                          asset_type="crypto", currency=quote, sector="crypto",
                          exchange="OKX")

    def _melde(self, text: str, *, wichtig: bool = False, klasse: str = "") -> None:
        """Eine Meldung zustellen.

        ``klasse`` erlaubt es, ein Ereignis ausdruecklich als KRITISCH zu
        kennzeichnen -- solche Meldungen werden nie gedrosselt.
        """
        logger.info(text)
        if self.melder is None:
            return
        try:
            self.melder(text, wichtig=wichtig, klasse=klasse)
        except TypeError:
            # Aeltere Melder kennen "klasse" nicht.
            try:
                self.melder(text, wichtig=wichtig)
            except Exception:
                logger.debug("Meldung konnte nicht zugestellt werden", exc_info=True)
        except Exception:
            logger.debug("Meldung konnte nicht zugestellt werden", exc_info=True)

    # -- Hauptzyklus --------------------------------------------------------
    def zyklus(self) -> dict:
        """Ein Durchlauf. Macht nur, was laut Taktgeber faellig ist."""
        self.zyklen += 1
        ergebnis = {"zeit": datetime.now(timezone.utc).isoformat(), "arbeiten": []}

        broker = self.broker
        if broker is None or not broker.is_connected():
            self.hub.verbinde("okx")
            broker = self.broker
            if broker is None or not broker.is_connected():
                ergebnis["hinweis"] = "OKX nicht verbunden"
                self.bereitschaft.melde(
                    "broker_verbunden", False,
                    "OKX nicht authentifiziert; automatische Wiederverbindung laeuft")
                self._write_runtime(ergebnis)
                return ergebnis

        self.topf.neuer_tag_pruefen()
        # Validate the same balance before any equity mutation, including ledger-only holdings.
        from okx_snapshot_guard import validate
        guard = validate(broker, self.buch.alle())
        self.topf._snapshot_error = "" if guard["valid"] else guard["detail"]
        broker._nexus_snapshot_valid = guard["valid"]
        # 10.7.0: Ein fehlender Bestand wird hier NICHT mehr sofort gebucht.
        # Bis 10.6.0 rief diese Stelle _mark_broker_unknown beim ersten
        # unvollstaendigen Schnappschuss -- ohne die Bestaetigungsfrist des
        # Positionsabgleichs, an der 10.6.0 gearbeitet hatte. Genau so
        # entstanden die drei Bestandsluecken vom 18.09.2026 um 02:34 UTC.
        # Die Buchung uebernimmt _pruefe_positionen nach zwei bestaetigten
        # Messungen; bis dahin ist der Coin nur fuer Neueinstiege gesperrt.
        # 10.2.0: Ein teilerfuellter EIGENER Schutz-Exit erklaert den Bestand
        # (EXIT_IN_PROGRESS). Die Domaene bleibt handelbar; nur die betroffenen
        # Werte sind fuer Neueinstiege gesperrt, bis der Exit abgerechnet ist.
        exit_laufend = {str(e.get("symbol") or "").upper()
                        for e in (guard.get("exit_in_progress") or [])}
        if exit_laufend and exit_laufend != getattr(self, "_exit_in_progress_symbole", set()):
            self._melde("Krypto: eigener Schutz-Exit teilerfuellt (EXIT_IN_PROGRESS): "
                        + ", ".join(sorted(exit_laufend))
                        + ". Bestand ist durch eigene Orderfuellungen erklaert; nur diese "
                        "Werte sind fuer Neueinstiege gesperrt.", wichtig=True)
        self._exit_in_progress_symbole = exit_laufend
        # 10.7.1: Nur der WIRKLICH fehlende Bestand heisst in der Sperrliste
        # "Bestand fehlt". Die Gesamtsperre (unten) enthaelt auch die Coins mit
        # offenem Buchungsbeleg -- die stehen dort schon unter ihrem Grund.
        self._fehlende_symbole = {str(s).upper() for s in (guard.get("missing") or [])}
        gesperrt = {str(s).upper() for s in (guard.get("blocked_symbols") or [])}
        vorher = getattr(self, "_gesperrte_symbole", set())
        if gesperrt != vorher:
            neu, frei = sorted(gesperrt - vorher), sorted(vorher - gesperrt)
            if neu:
                self._melde("Krypto: Neueinstiege nur fuer " + ", ".join(neu)
                            + " gesperrt (offener Bestands-/Buchungsbeleg dieses Coins). "
                            "Alle anderen Kaeufe laufen weiter.", wichtig=True)
            if frei:
                self._melde("Krypto: " + ", ".join(frei) + " wieder fuer Neueinstiege frei.",
                            wichtig=False)
        self._gesperrte_symbole = gesperrt
        # Snapshotgueltigkeit und finanziertes Handelskapital sind zwei
        # verschiedene Aussagen. Die Bereitschaft fasst sie absichtlich in
        # derselben Bedingung zusammen, nennt aber jetzt den konkreten Grund,
        # statt einen gueltigen Nullbestand als "nicht abrufbar" auszugeben.
        # Nur die eigenen Positionen zaehlen zum handelbaren Kapital.
        werte = self.risiko.aktualisiere_kontowerte(self.hub, self._eigene_positionen)
        self.bereitschaft.melde("broker_verbunden", self.broker is not None
                                and self.broker.is_connected())
        kapital = float(werte.get("okx", 0.0) or 0.0)
        evidence = (self.broker.capital_evidence()
                    if self.broker is not None and hasattr(self.broker, "capital_evidence") else {})
        if not guard["valid"]:
            kapital_ok, kapital_detail = False, guard["detail"]
        elif kapital > 0:
            lanes = ", ".join(evidence.get("funded_allowed_lanes") or [])
            basis = str(evidence.get("basis_currency") or getattr(self.broker, "quote_ccy", "EUR"))
            kapital_ok = True
            kapital_detail = f"handelbar {kapital:.2f} {basis}" + (f"; finanziert: {lanes}" if lanes else "")
        else:
            supported = ", ".join(evidence.get("allowed_cash_lanes") or []) or "keine"
            unsupported = ", ".join(evidence.get("unsupported_positive_balance_currencies") or [])
            kapital_ok = False
            kapital_detail = f"kein freies Kapital in freigegebenen Abrechnungswaehrungen ({supported})"
            if unsupported:
                kapital_detail += f"; Guthaben nur/auch in nicht freigegeben: {unsupported}"
        self.bereitschaft.melde("guthaben", kapital_ok, kapital_detail)
        self._melde_bereitschaft()

        # Positionen zuerst: Schutz geht immer vor neuen Chancen.
        ft_active = self._scan_raster_sekunden() == 300
        if self.takt.faellig("crypto", POSITIONEN)[0] or (ft_active and time.monotonic() >= getattr(self, "_ft_positions_after", 0)):
            try:
                ergebnis["positionen"] = self.pruefe_positionen()
                ergebnis["arbeiten"].append(POSITIONEN)
            finally:
                self.takt.markiere("crypto", POSITIONEN)
                self._ft_positions_after = time.monotonic() + 5

        # Late historical accounting never waits in front of position protection.
        # The dedicated reader has a GET-only HTTP session and bounded budget.
        from okx_closed_reconciliation import schedule as schedule_closed_results
        ergebnis["geschlossene_ergebnisse"] = schedule_closed_results(self.broker)
        # 10.2.1: Zusammengesetzte Abschluesse EUR-beziffern (begrenzt, GET-only;
        # der 10-Minuten-Backoff je Trade steckt im Modul selbst).
        if time.monotonic() >= getattr(self, "_autovaluation_after", 0.0):
            self._autovaluation_after = time.monotonic() + 120.0
            try:
                import okx_reference_autovaluation
                okx_reference_autovaluation.run_one(self.broker)
            except Exception:
                logger.debug("EUR-Referenz-Autobewertung uebersprungen", exc_info=True)

        if self.takt.faellig("crypto", UNIVERSUM)[0]:
            try:
                ergebnis["universum"] = self.universumslauf()
                ergebnis["arbeiten"].append(UNIVERSUM)
            finally:
                self.takt.markiere("crypto", UNIVERSUM)

        scan_due = (time.monotonic() >= getattr(self, "_ft_scan_after", 0)) if ft_active else self.takt.faellig("crypto", SCAN)[0]
        if scan_due:
            # v9.2: Der Rasterpunkt steht VOR dem Lauf fest. Ein Scan, der
            # eine Kerzengrenze ueberschreitet, wird sonst der neuen Kerze
            # zugeordnet, obwohl er auf der alten entschieden hat -- und die
            # neue Kerze faellt aus.
            scan_token = self.takt.rasterlauf_beginnen(
                "crypto", SCAN, raster_sekunden=self._scan_raster_sekunden())
            from freqtrade_candles import candle_cutoff
            self._scan_candle_cutoff = candle_cutoff() if ft_active else None
            try:
                ergebnis["scan"] = self.scan()
                ergebnis["arbeiten"].append(SCAN)
                if scan_token.get("kerzenschluss_utc"):
                    ergebnis["kerzenschluss_utc"] = (self._scan_candle_cutoff.isoformat() if ft_active
                        else scan_token["kerzenschluss_utc"])
            finally:
                self.takt.rasterlauf_abschliessen("crypto", SCAN, scan_token)
                self._scan_candle_cutoff = None
                self._ft_scan_after = time.monotonic() + 5

        self._write_runtime(ergebnis)
        return ergebnis

    def _write_runtime(self, cycle: dict) -> None:
        """Getrennter OKX-Heartbeat; die eToro-Datei wird nie ueberschrieben."""
        try:
            from safe_persistence import best_effort_json
            payload = self.status()
            payload.update({
                "running": True,
                "online": bool(self.broker is not None and self.broker.is_connected()),
                "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                "last_cycle": cycle,
            })
            broker_state = self.hub.zustaende().get("okx", {}) if hasattr(self.hub, "zustaende") else {}
            payload.update({
                "connection_state": broker_state.get("status", "UNBEKANNT"),
                "last_broker_contact": broker_state.get("letzter_kontakt", ""),
                "last_connection_error": broker_state.get("letzter_fehler", ""),
                "health_interval_seconds": float(getattr(self.cfg, "BROKER_HEALTHCHECK_SECONDS", 30)),
            })
            if self.broker is not None and hasattr(self.broker, "stream_status"):
                payload["position_stream"] = self.broker.stream_status()
            if self.broker is not None and hasattr(self.broker, "connection_components"):
                payload["connection_components"] = self.broker.connection_components()
            best_effort_json(_state_root() / "runtime_status_okx.json", payload,
                             label="OKX Runtime-Status", durable=False)
        except Exception:
            logger.debug("OKX Runtime-Status nicht schreibbar", exc_info=True)

    # -- Universum ----------------------------------------------------------
    def universumslauf(self) -> dict:
        """Waehlt das Krypto-Universum neu und meldet die Aenderungen.

        KORREKTUR 9.5.3: Am 02.09.2026 fiel OKX um 12:30 mit HTTP 503 aus.
        Der Katalog lag noch im Cache, die Marktdaten fehlten -- der Lauf um
        12:33:46 lief deshalb durch und rechnete das Universum auf

            0 geeignete Basen -> Kern 0/20 -> 0 im Pool -> 0 bewertet

        Ein Brokerausfall darf das Handelsuniversum EINFRIEREN, niemals
        leeren. Sonst steht der Bot nach der Erholung ohne Kern da.
        """
        selektor = self._selektor()
        if selektor is None:
            return {"ok": False, "grund": "OKX nicht verbunden"}
        # 1. Ohne authentifizierten Broker gar nicht erst rechnen.
        zustand = str(getattr(self.broker, "health_state", lambda: "")() or ""
                      ) if callable(getattr(self.broker, "health_state", None)) else ""
        if zustand and zustand.upper() not in {"ONLINE", "DEGRADED"}:
            grund = (f"OKX nicht authentifiziert ({zustand}); Universum bleibt "
                     "unveraendert")
            logger.warning("Krypto-Universumslauf uebersprungen: %s", grund)
            return {"ok": False, "grund": grund, "eingefroren": True}
        favoriten = self._favoriten()
        try:
            # Favoriten erhalten nur eine garantiert vollstaendige Pruefung
            # im OKX-Katalog. Sie bestehen trotzdem alle harten Filter und
            # werden erst bei ausreichend gutem Rang/Bewaehrung handelbar.
            auswahl = selektor.auswahl(bar=self._okx_bar(), favoriten=favoriten)
        except (VerbindungVerloren, BrokerFehler) as exc:
            self.letzter_fehler = str(exc)
            logger.warning("Krypto-Universumslauf fehlgeschlagen: %s", exc)
            return {"ok": False, "grund": str(exc)}

        # 2. Ein Lauf, der den bestehenden Kern ausloeschen wuerde, wird nicht
        #    angewendet. Ein leeres Ergebnis ist bei laufendem Handel kein
        #    Marktbefund, sondern ein Datenausfall.
        pool_jetzt = int(auswahl.get("pool") or 0)
        try:
            vorher = len(list(self.universum.zustand.fuer_broker("okx")))
        except Exception:
            vorher = 0
        if pool_jetzt <= 0 and vorher > 0:
            grund = (f"Universumslauf lieferte 0 Kandidaten, zuvor waren es "
                     f"{vorher}. Datenausfall angenommen; das Universum bleibt "
                     "unveraendert.")
            self._melde_einmal("universum_leer", "Krypto: " + grund,
                               wichtig=True, klasse="KRITISCH")
            logger.warning("Krypto-Universumslauf verworfen: %s", grund)
            return {"ok": False, "grund": grund, "eingefroren": True,
                    "vorher": vorher}
        self._entwarnung("universum_leer",
                         "Krypto: Universumslauf liefert wieder Kandidaten.")

        diff = self.universum.lauf(auswahl,
                                   offene_positionen=self.buch.symbole(),
                                   favoriten=favoriten)
        # v8.1.4: Zaehlen, welcher Filter wie viele Werte verwirft. Am
        # 25.08.2026 bestanden 2 von 581 Instrumenten die harten Filter --
        # welcher Filter dafuer verantwortlich war, stand nirgends.
        import universe_diagnose
        diagnose = universe_diagnose.protokolliere(auswahl)
        self.letzter_universumslauf = {
            "zeit": datetime.now(timezone.utc).isoformat(),
            "diff": diff.als_dict(),
            "pool": auswahl.get("pool"),
            "katalog": auswahl.get("katalog_gesamt"),
            "dauer": auswahl.get("dauer_sekunden"),
            "diagnose": diagnose,
        }
        if diff.hat_aenderungen:
            self._melde(f"Krypto-Universum: {diff.kurzfassung()}"
                        + (f" | neu: {', '.join(diff.aufgenommen)}" if diff.aufgenommen else "")
                        + (f" | Beobachtung: {', '.join(diff.beobachtung_gestartet)}"
                           if diff.beobachtung_gestartet else "")
                        + (f" | raus: {', '.join(diff.entfernt)}" if diff.entfernt else ""))
        self.bereitschaft.melde("universum", True,
                                f"{auswahl.get('pool', 0)} im Pool")
        return {"ok": True, "aenderungen": diff.kurzfassung(),
                "handelbar": len(self.universum.handelbare_symbole("okx"))}

    def _okx_bar(self) -> str:
        from broker.okx import BAR_MAP
        return BAR_MAP.get(str(getattr(self.cfg, "CRYPTO_BAR_SIZE", "15 mins")).lower(), "15m")

    def _favoriten(self) -> list[str]:
        try:
            from favorites import load_favorites
            eintraege = load_favorites()
        except Exception:
            logger.debug("Favoriten nicht lesbar", exc_info=True)
            return []
        return [str(f.symbol).upper() for f in eintraege
                if str(getattr(f, "asset_type", "")).lower() == "crypto"]

    # -- Positionsueberwachung ---------------------------------------------
    def pruefe_positionen(self) -> dict:
        from broker_observation import ProtectionObservation
        observation = getattr(self, "_protection_observation", None)
        if observation is None:
            observation = self._protection_observation = ProtectionObservation()
        observation.begin()
        try:
            report = self._pruefe_positionen()
        except BaseException as exc:
            observation.finish(error=exc)
            # Emit the failure even if the cycle will abort before its normal
            # runtime write. Telemetry must not replace the original failure.
            self._write_runtime({"position_check_error": type(exc).__name__})
            raise
        observation.finish(report)
        return report

    def functional_health(self) -> dict:
        from broker_observation import ProtectionObservation
        observation = getattr(self, "_protection_observation", None)
        return {"schema_version": 1, "broker": "okx",
                "protection": (observation.snapshot() if observation else ProtectionObservation().snapshot()),
                "persistence_entry_blocked": bool(getattr(self, "_accounting_write_fault", False) or getattr(self, "_critical_persistence_fault", False))}

    def _pruefe_positionen(self) -> dict:
        """Schutz nachziehen, Ausstiege pruefen, Buch mit dem Broker abgleichen."""
        # Auch Wartungs-/Migrationstests koennen den Abgleich ohne einen
        # initialisierten Broker-Hub aufrufen. Dann bleibt die sichere
        # Zwei-Snapshot-Regel aktiv, nur die Historiennachladung entfaellt.
        try:
            broker = self.broker
        except (AttributeError, KeyError):
            broker = None
        if broker is None:
            return {"ok": False, "grund": "OKX nicht verbunden"}

        bericht = {"geprueft": 0, "geschlossen": [], "schutz_ergaenzt": [], "diagnostic_errors": [],
                   "abgeglichen": [], "manuelle_klaerung": []}
        try:
            bericht["manuelle_auftraege"] = self._verarbeite_manuelle_auftraege(broker)
        except Exception as exc:
            logger.exception("Manuelle OKX-Auftraege nicht verarbeitbar: %s", exc)
            bericht["manuelle_auftraege"] = []
        try:
            import okx_reconciliation_actions
            bericht["manuelle_klaerung"] = okx_reconciliation_actions.verarbeite(
                self, broker)
        except Exception as exc:
            logger.warning("Manuelle OKX-Klaerungsaktionen nicht verarbeitbar: %s", exc,
                           exc_info=True)

        # Persistierte, unklare Orders werden zuerst und ausschliesslich ueber
        # clOrdId/ordId abgefragt. Es gibt hier bewusst keinen zweiten POST.
        try:
            if hasattr(broker, "reconcile_order_evidence"):
                from execution_lifecycle import recover_okx
                bericht["execution_lifecycle"] = recover_okx(broker)
                bericht["order_reconciliation"] = self._reconcile_pending_okx_orders(broker)
                bericht["legacy_entry_recovery"] = self._recover_terminal_okx_entries(broker)
                bericht["ueberfaellige_orders"] = self._melde_ueberfaellige_orders()
        except Exception as exc:
            logger.warning("OKX-Order-Reconciliation fehlgeschlagen: %s", exc,
                           exc_info=True)
            bericht["diagnostic_errors"].append("OKX_ORDER_RECONCILIATION_INCOMPLETE")

        # 10.7.0: Bestandsluecken, deren Trade laengst verkauft und verbucht
        # ist, schliessen sich hier von selbst (Lot-Rest als eigene Zeile
        # zaehlt mit). Am 18.09.2026 sperrten drei solche Luecken jeden
        # OKX-Kauf, obwohl XRP/BTC/ETH per Take-Profit sauber verkauft waren.
        try:
            from okx_accounting import repair_explained_gaps
            repariert = repair_explained_gaps(
                str(getattr(broker, "account_fingerprint", lambda: "")() or ""),
                "DEMO" if getattr(broker, "demo", True) else "LIVE")
            if repariert:
                bericht["bestandsluecken_geschlossen"] = repariert
                self._melde(
                    f"OKX: {len(repariert)} Bestandsbeleg(e) geschlossen -- die Trades "
                    f"{', '.join(str(t) for t in repariert)} sind vollstaendig verkauft und "
                    "verbucht; der Lot-Rest laeuft als eigene Zeile weiter.", wichtig=True)
        except Exception as exc:
            logger.warning("OKX-Bestandsluecken nicht reparierbar: %s", exc, exc_info=True)
            bericht["diagnostic_errors"].append("OKX_GAP_REPAIR_INCOMPLETE")

        try:
            from risk_result_recovery import reconcile
            bericht['ergebnisabgleich'] = reconcile(self.topf.state, broker,
                account_equity=float(getattr(self.topf, 'kontowert', 0) or 0))
        except Exception as exc:
            logger.warning('OKX-Ergebnisabgleich bleibt offen: %s', type(exc).__name__)
            bericht["diagnostic_errors"].append("OKX_RESULT_RECONCILIATION_INCOMPLETE")

        # 9.5.8: EIN Guthabenstand fuer diesen ganzen Durchlauf.
        #
        # Bis 9.5.7 las der Mengenabgleich (der das Buch an die Realitaet
        # anpasst und damit eine Sperre aufloesen wuerde) ueber ``positionen()``
        # die Stream-/Cachesicht, die Exposure-Klassifizierung (die sperrt)
        # dagegen einen frischen REST-Aufruf. Beide Schwellen liegen bei 2 % --
        # loeste nur eine aus, koennen die Zahlen nicht dieselben gewesen sein.
        # Genau so blieb XLM am 03.09.2026 dauerhaft auf RESIDUAL_EXPOSURE.
        #
        # Der Aufruf hier setzt den REST-Checkpoint; ``positionen()`` direkt
        # danach liest denselben Stand aus dem Cache, und die Klassifizierung
        # weiter unten benutzt genau diesen gemerkten Schnappschuss.
        self._guthaben_dieser_runde = {}
        try:
            if hasattr(broker, "guthaben_schnappschuss"):
                self._guthaben_dieser_runde = dict(broker.guthaben_schnappschuss())
        except BrokerFehler as exc:
            # 10.8.1: Ein nicht lesbarer Guthabenstand ist KEINE Messung.
            # Bis 10.8.0 lief der Durchlauf mit leerem Schnappschuss weiter:
            # ``positionen()`` lieferte dann eine leere Liste, jede Position
            # zaehlte eine Fehlmessung, und jede offene Ledgerzeile ohne
            # Position bekam SOFORT einen Bestandsbeleg (XRP-Rest 85 am
            # 19.09.2026, 05:20:10 UTC -- vier Minuten OKX-Ausfall, danach
            # zwei Stunden XRP-Sperre). Ohne Schnappschuss wird in diesem
            # Takt weder geheilt noch gebucht noch gesperrt; Stops und
            # Broker-Schutz brauchen ihn nicht und laufen im naechsten Takt
            # weiter. Bis zum naechsten vollstaendigen Abgleich bleiben
            # Neueinstiege zu -- ein abgebrochener Abgleich ist kein Abgleich.
            logger.warning("Guthabenstand fuer diesen Durchlauf nicht lesbar: %s", exc)
            bericht["diagnostic_errors"].append("OKX_BALANCE_SNAPSHOT_UNAVAILABLE")
            self._abgleich_abgebrochen(bericht, f"Guthabenstand nicht lesbar: {exc}")
            return bericht

        try:
            try:
                position_rows = broker.positionen(guthaben_snapshot=self._guthaben_dieser_runde)
            except TypeError:
                position_rows = broker.positionen()
            bestaende = {p.symbol.upper(): p for p in position_rows}
        except BrokerFehler as exc:
            bericht["diagnostic_errors"].append("OKX_POSITIONS_UNAVAILABLE")
            self._abgleich_abgebrochen(bericht, f"Bestaende nicht abrufbar: {exc}")
            return bericht
        try:
            bericht["verwaiste_schutzorders_entfernt"] = int(
                broker.verwaiste_orders_aufraeumen()
                if hasattr(broker, "verwaiste_orders_aufraeumen") else 0)
        except BrokerFehler as exc:
            logger.warning("Verwaiste OKX-Schutzorders nicht pruefbar: %s", exc)

        for position in self.buch.alle():
            bericht["geprueft"] += 1
            symbol = position.symbol.upper()
            bestand = bestaende.get(symbol)

            current_account = str(getattr(
                broker, "account_fingerprint", lambda: "")() or "")
            stored_account = str(position.account_fingerprint or "")
            if ((stored_account and current_account and stored_account != current_account)
                    or bool(position.paper) != bool(getattr(broker, 'demo', position.paper))):
                position.pausiere(
                    "Kontowechsel erkannt; Position wird auf diesem OKX-Konto "
                    "weder geschuetzt noch verkauft.", status="ACCOUNT_MISMATCH")
                self.buch.setze(position)
                bericht.setdefault("konto_falsch", []).append(symbol)
                self._melde_einmal(
                    f"okx-account:{symbol}:{stored_account}:{current_account}",
                    f"Krypto {symbol}: Positionsbuch und aktuelles OKX-Konto "
                    "stimmen nicht ueberein. Jede automatische Order fuer diese "
                    "Position ist gesperrt.", wichtig=True, klasse="KRITISCH")
                continue

            # Ein beobachteter Legacy-/Kontohinweis ist keine Botposition.
            # Bis 9.0.11 wurde er trotzdem gegen den Gesamtsaldo verglichen;
            # daraus entstand die falsche Meldung zu 0,996348 ETH.
            #
            # v9.2: Die Grenze verlaeuft jetzt am EIGENTUM, nicht am
            # Verwaltungsmodus. Eine bewiesene, aber pausierte Botposition
            # wurde vorher komplett uebersprungen -- ohne Mengenabgleich, ohne
            # Erkennung externer Verkaeufe und ohne Pruefung des
            # Broker-Schutzes. Das ist der gefaehrlichste Zustand ueberhaupt:
            # echtes Geld, das niemand mehr beobachtet. Verkauft wird eine
            # pausierte Position weiterhin nicht -- das haengt an
            # darf_schutz_ausfuehren, weiter unten.
            if not position.safety_monitoring_enabled:
                bericht.setdefault("nur_beobachtet", []).append(symbol)
                continue
            if not position.darf_schutz_ausfuehren:
                bericht.setdefault("ueberwacht_ohne_automatik", []).append(symbol)

            if position.broker_state and bestand is not None and float(bestand.quantity or 0) >= position.menge * (1 - MENGEN_TOLERANZ):
                evidence = self._externer_verkaufsbeweis(position, position.menge)
                if evidence:
                    self._position_extern_geschlossen(position, evidence, bericht, residual=float(bestand.quantity))
                    continue
                # 10.6.0: Bestand wieder vollstaendig da, kein Verkaufsbeleg --
                # bis 10.5.0 endete der Zustand hier fuer immer. Gelingt die
                # Ruecknahme, laeuft die Position in DIESEM Takt normal weiter
                # und laesst ihren Broker-Schutz erneut bestaetigen.
                if not self._bestand_zurueckgewonnen(position, bestand, bericht):
                    bericht.setdefault("broker_state_unknown", []).append(symbol)
                    continue

            if bestand is not None and not math.isfinite(float(getattr(bestand, "quantity", 0.0))):
                self._mark_broker_unknown(position, None)
                bericht.setdefault("broker_state_unknown", []).append(symbol)
                continue

            # 1. Abgleich zwischen Kontostand und Positionsbuch.
            #
            #    ACHTUNG, das ist die Stelle, an der am 25.08.2026 ein
            #    Fremdbestand von 0,94 BTC verkauft wurde. Bis 8.1.3 stand
            #    hier "position.menge = bestand.quantity" -- der Bot hat also
            #    den GESAMTEN Kontostand der Waehrung als seine Position
            #    uebernommen. broker.positionen() liefert bei OKX Spot aber
            #    Guthaben, keine Positionen; das steht so im Adapter.
            #
            #    Ab 8.1.4 wird nach RICHTUNG unterschieden:
            #      weniger im Konto  -> Buch nach unten korrigieren (sicher)
            #      mehr   im Konto   -> Ueberhang gehoert dem Bot NICHT
            # Ein Staubrest ist rechnerisch positiv, aber keine Position mehr.
            # Er laeuft bewusst durch DIESELBE Bestaetigung wie ein fehlendes
            # Guthaben (zwei Schnappschuesse in Folge plus Mindestalter),
            # damit ein unvollstaendiger Snapshot nie eine echte Position
            # schliesst.
            kontorest_staub = (bestand is not None
                               and self._kontorest_ist_staub(position, bestand))
            if (bestand is None
                    or not _ist_positiv(getattr(bestand, "quantity", 0.0))
                    or kontorest_staub):
                position.consecutive_missing_snapshots += 1
                if not position.first_missing_at:
                    position.first_missing_at = datetime.now(timezone.utc).isoformat()
                position.consecutive_present_snapshots = 0
                position.first_present_at = ""
                self.buch.setze(position)
                self._fehlender_bestand.pop(symbol, None)
                # 10.6.0: Erst BESTAETIGEN, dann buchen. Der Kommentar oben
                # versprach "zwei Schnappschuesse in Folge plus Mindestalter"
                # seit 9.5; geprueft wurde beides nie, und
                # OKX_POSITION_MISSING_CONFIRM_SECONDS stand unbenutzt in der
                # Konfiguration. Die Aktienseite (position_manager) macht es
                # seit jeher richtig; hier lief es auf den ersten Fehlschuss.
                # Ein belegter Verkauf wartet nicht -- er wird sofort gebucht.
                evidence = self._externer_verkaufsbeweis(position, position.menge)
                if not evidence and not self._fehlbestand_bestaetigt(position):
                    bericht.setdefault("bestand_fehlt_unbestaetigt", []).append(symbol)
                    logger.info(
                        "Krypto %s: Bestand fehlt im Schnappschuss (%s. Messung, "
                        "seit %s). Position bleibt unveraendert, bis die "
                        "Bestaetigung steht.", symbol,
                        position.consecutive_missing_snapshots,
                        position.first_missing_at or "?")
                    # Frueh informieren, spaet buchen. Der Verdacht gehoert
                    # sofort gemeldet -- nur eben ohne Bestandsluecke und ohne
                    # Sperre, solange er unbestaetigt ist.
                    lage = ("nur noch ein nicht handelbarer Rest oder Teilbestand"
                            if kontorest_staub else "kein Bestand im Kontoschnappschuss")
                    self._melde_einmal(
                        f"okx-fehlbestand:{symbol}:{position.first_missing_at}",
                        f"Krypto {symbol}: {lage}. Der Befund ist noch NICHT "
                        "bestaetigt. Die Position bleibt unveraendert im Buch und "
                        "wird erneut geprueft; es wird nichts gebucht und nichts "
                        "gesperrt. Stops und Broker-Schutz laufen weiter, bis eine "
                        "zweite Messung mit Mindestabstand den Befund bestaetigt.",
                        wichtig=True)
                    continue
                self._position_verschwunden(
                    position, bericht,
                    staubrest=(float(getattr(bestand, "quantity", 0.0) or 0.0)
                               if kontorest_staub else 0.0),
                    evidence=evidence)
                continue
            self._fehlender_bestand.pop(symbol, None)

            if position.consecutive_missing_snapshots or position.first_missing_at:
                position.consecutive_missing_snapshots = 0
                position.first_missing_at = ""
                self.buch.setze(position)

            konto = float(bestand.quantity)
            if not _ist_positiv(konto):
                # NaN oder Unendlich: laeuft durch jeden Vergleich hindurch und
                # wuerde stillschweigend als "stimmt mit dem Buch ueberein"
                # gelten. Lieber diesen Takt ueberspringen.
                logger.warning("Krypto %s: unbrauchbarer Kontostand %r -- Takt "
                               "uebersprungen.", symbol, bestand.quantity)
                continue
            exit_state = str(getattr(position, "exit_state", "") or "").upper()
            if exit_state in {"SUBMITTING", "UNCLEAR", "MANUAL_EXIT_PENDING",
                              "ACCOUNTING_PENDING"}:
                evidence = self._externer_verkaufsbeweis(position, position.menge)
                if evidence:
                    self._position_extern_geschlossen(
                        position, evidence, bericht, residual=konto)
                    continue
                exit_state = str(position.exit_state or "").upper()
                if exit_state in {"SUBMITTING", "UNCLEAR", "MANUAL_EXIT_PENDING",
                                  "ACCOUNTING_PENDING"}:
                    position.exit_recovery_checks = int(
                        position.exit_recovery_checks or 0) + 1
                    # Unchanged balances and empty archives never prove that a
                    # submitted order is terminal. Only the exact result above
                    # (or a durable NOT_SUBMITTED receipt) can release this state.
                    self.buch.setze(position)
                    # 10.2.1: Ratenbremse -- am 17.09. standen >2.500 identische
                    # WARNINGs im Log. Der Zustand aendert sich zwischen den
                    # Takten nicht; ein Hinweis alle 100 Checks genuegt.
                    checks = position.exit_recovery_checks
                    if checks <= 3 or checks % 100 == 0:
                        logger.warning("OKX-Exit %s wartet auf eindeutigen Orderbeleg (Check %d)",
                                       symbol, checks)
                    else:
                        logger.debug("OKX-Exit %s wartet auf eindeutigen Orderbeleg (Check %d)",
                                     symbol, checks)
                    continue
            # 9.0.14 hat nach einem externen Vollverkauf die Buchmenge auf
            # den nicht handelbaren Reststaub verkleinert (ONDO 0,002515).
            # Dadurch ist Konto==Buch und eine normale Abweichungspruefung
            # findet den Verkauf nie wieder. Die urspruengliche Ledger-Menge
            # plus echte OKX-Sell-Fills reparieren auch diesen Altzustand.
            dust_price = float(getattr(bestand, "market_price", 0.0) or
                               self._letzter_kurs(position) or position.einstieg)
            if not self._rest_lohnt_sich(position, konto, dust_price):
                try:
                    import trade_ledger
                    open_trade = trade_ledger.offener_trade("okx", symbol) or {}
                    original_qty = max(float(position.menge),
                                       float(open_trade.get("menge") or 0.0))
                except Exception:
                    original_qty = float(position.menge)
                evidence = self._externer_verkaufsbeweis(position, original_qty)
                if evidence and original_qty > position.menge:
                    self._position_extern_geschlossen(
                        position, evidence, bericht, residual=konto,
                        close_quantity=original_qty)
                    continue
            abweichung = abs(konto - position.menge) / max(position.menge, 1e-12)
            if abweichung > MENGEN_TOLERANZ:
                if konto < position.menge:
                    # Extern verkauft, Schutzorder gezogen oder Teilausfuehrung.
                    # Erst exakte Fills, niemals Kontostand als Botmenge uebernehmen.
                    alt_menge = position.menge
                    evidence = self._externer_verkaufsbeweis(position, alt_menge)
                    restkurs = float(getattr(bestand, "market_price", 0.0) or
                                     self._letzter_kurs(position) or position.einstieg)
                    if evidence and not self._rest_lohnt_sich(position, konto, restkurs):
                        self._position_extern_geschlossen(position, evidence, bericht,
                                                          residual=konto,
                                                          close_quantity=alt_menge)
                        continue
                    # 10.2.1: Ein EXAKT durch eigene Schutz-Fills erklaerter
                    # Teilabgang wird als Teilverkauf verbucht; Rest laeuft
                    # weiter. Nur der unerklaerte Fall friert wie bisher ein.
                    if self._verbuche_eigenen_schutz_teilfill(position, konto, bericht):
                        continue
                    # A balance limits available inventory; it is NOT an execution
                    # allocation. Keep original bot ownership and the active algo anchor.
                    import trade_ledger
                    from okx_accounting import mark_balance_gap, mark_unanchored
                    family = trade_ledger.entry_lineage('okx', position.inst_id,
                        position.order_id, position.account_fingerprint, paper=position.paper)
                    open_rows = [r for r in family if not r.get('ausgestiegen_am')]
                    try:
                        if len(open_rows) == 1:
                            mark_balance_gap(open_rows[0], konto, alt_menge)
                        else:
                            mark_unanchored(position, konto)
                    except Exception:
                        self._accounting_write_fault = True
                        self.bereitschaft.melde('buchung_vollstaendig',False,'OKX-Bestandsluecke konnte nicht gespeichert werden')
                        raise
                    self._mark_broker_unknown(position, konto)
                    position.exit_state = 'ACCOUNTING_PENDING'
                    self.buch.setze(position)
                    self.bereitschaft.melde('buchung_vollstaendig', False,
                        'Ungeklaerter OKX-Teilabgang; Originalmenge bleibt erhalten')
                    bericht.setdefault('buchung_ausstehend', []).append(symbol)
                    self._melde(f"Krypto {symbol}: {konto:g} statt {alt_menge:g} im Konto. "
                        "Mengenabgang wird belegt abgeglichen; kein weiterer Verkauf oder neuer Schutzauftrag.", wichtig=True)
                    continue
                else:
                    # MEHR im Konto als im Buch. Der Ueberhang ist NICHT die
                    # Position des Bots. Das Buch bleibt, wie es ist.
                    #
                    # v9.1: Vorher wurde hier KEIN Fill-Nachweis geholt --
                    # nur im Zweig "konto < menge". Genau daran hing ein
                    # Zweitverkauf: war der eigene Verkauf bereits ausgefuehrt,
                    # die Verbuchung aber verloren (Prozessabbruch, unklarer
                    # Zustand), und lag Fremdbestand desselben Coins im Konto,
                    # dann galt weiter die veraltete Buchmenge -- und der
                    # naechste Stop verkaufte sie ein zweites Mal, aus dem
                    # Bestand des Nutzers. Das ist das Schadensmuster vom
                    # 25.08.2026 ueber einen anderen Weg.
                    beweis = self._externer_verkaufsbeweis(position, position.menge)
                    if beweis:
                        self._melde(
                            f"Krypto {symbol}: eigener Verkauf ueber echte OKX-Fills "
                            f"belegt, waehrend {konto:g} im Konto liegen. Die Position "
                            "wird geschlossen; der Rest gehoert nicht zum Bot.",
                            wichtig=True, klasse="KRITISCH")
                        self._position_extern_geschlossen(
                            position, beweis, bericht, residual=konto)
                        continue
                    ueberhang = konto - position.menge
                    bericht.setdefault("fremdbestand", []).append(
                        {"symbol": symbol, "menge": round(ueberhang, 12)})
                    # Ein zusaetzliches Konto-Asset ist bei Spot normal und
                    # weder Fehler noch Einstiegssperre. Nur die bewiesene
                    # Botmenge bleibt verwaltet.
                    self._gemeldeter_ueberhang.add(symbol)
            else:
                self._gemeldeter_ueberhang.discard(symbol)

            # 2. Broker-Schutz nachziehen, falls er fehlt.
            #
            # v9.1: Auch dann, wenn ein Verkauf mittendrin abgebrochen ist.
            # ``exit_state`` blieb bis 9.0.15 auf SUBMITTING stehen und wurde
            # NIRGENDS ausgewertet -- die einzigen Leser waren der
            # Dataclass-Default und ein Anzeigefeld der WebUI. Ein Prozesstod
            # zwischen Storno und Verkauf hinterliess damit eine Position, die
            # sich fuer geschuetzt hielt und es nicht war.
            exit_unterbrochen = str(getattr(position, "exit_state", "")).upper() in {
                "SUBMITTING", "MANUAL_EXIT_PENDING"}
            # KORREKTUR 9.5.4: Ein gesetztes broker_schutz allein ist kein
            # Beweis. Ohne algoId kann NEXUS die eigene Schutzorder weder
            # wiederfinden noch pruefen noch vor einem Verkauf stornieren --
            # die Flagge behauptet dann nur Sicherheit.
            #
            # Bis 9.5.3 lief der Abgleich ausschliesslich bei
            # ``not position.broker_schutz``. Genau deshalb blieben BNB, LINK
            # und XLM am 02.09.2026 dauerhaft auf broker_schutz=True mit
            # leerer algoId und PENDING stehen: der einzige Heilungspfad sah
            # sie nie an. Es ist derselbe Fehlertyp wie der in v8.1.3
            # behobene, nur eine Ebene hoeher.
            schutz_unbelegt = bool(position.broker_schutz) and not str(
                getattr(position, "protection_algo_id", "") or "").strip()
            if not position.broker_schutz or exit_unterbrochen or schutz_unbelegt:
                try:
                    try:
                        zustand = broker.reconcile_position_protection(
                            self._instrument(symbol, position.inst_id), position.menge,
                            position.stop, position.take_profit,
                            protection_client_id=position.protection_client_order_id,
                            protection_algo_id=position.protection_algo_id,
                            trade_quote_ccy=position.trade_quote_ccy)
                    except TypeError:
                        zustand = broker.reconcile_position_protection(
                            self._instrument(symbol, position.inst_id), position.menge,
                            position.stop, position.take_profit)
                    position.broker_schutz = bool(zustand.get("protection_confirmed"))
                    position.protection_algo_id = str(
                        zustand.get("algo_id") or position.protection_algo_id or "")
                    position.protection_client_order_id = str(
                        zustand.get("algo_client_id") or
                        position.protection_client_order_id)
                    position.protection_status = (
                        "ACTIVE" if position.broker_schutz else
                        "MISSING" if bool(zustand.get("checked")) else "PENDING")
                    if schutz_unbelegt and not position.broker_schutz:
                        self._melde(
                            f"Krypto {symbol}: gespeicherter Broker-Schutz war "
                            "nicht belegbar (keine algoId) und liess sich beim "
                            "Broker nicht bestaetigen. Die Position gilt ab "
                            "jetzt als ungeschuetzt; es schuetzt nur der "
                            "Client-Stop.", wichtig=True, klasse="KRITISCH")
                    if exit_unterbrochen and position.broker_schutz:
                        # Der abgebrochene Verkauf ist abgeschlossen behandelt:
                        # die Position ist wieder geschuetzt und darf normal
                        # weiterlaufen.
                        position.exit_state = "IDLE"
                        self._melde(
                            f"Krypto {symbol}: unterbrochener Verkauf erkannt, "
                            "Broker-Schutz wurde neu gesetzt.",
                            wichtig=True, klasse="KRITISCH")
                    self.buch.setze(position)
                    self._ledger_schutz_synchronisieren(
                        position, str(zustand.get("detail") or ""))
                    if not position.broker_schutz:
                        self._warn_missing_protection(position, str(zustand.get("detail") or ""))
                    if position.broker_schutz:
                        bericht["schutz_ergaenzt"].append(symbol)
                except BrokerFehler as exc:
                    logger.warning("Schutzabgleich %s: %s", symbol, exc)
                    bericht["diagnostic_errors"].append("OKX_PROTECTION_UNCONFIRMED")

            # Der letzte gehandelte Tickerpreis ist nicht zwingend fuer
            # unsere volle Menge ausfuehrbar. Stop, Ziel und Strategie sehen
            # deshalb nur den aktuellen Full-size-Verkaufs-VWAP aus dem Buch.
            try:
                ausfuehrbar = broker.execution_quote(
                    self._instrument(symbol, position.inst_id),
                    position.menge, side="sell")
                preis = float(ausfuehrbar.get("vwap") or 0.0)
            except AttributeError:
                # BrokerBase-Kompatibilitaet fuer alte Paper-/Testadapter.
                # Der echte OKXBroker besitzt execution_quote immer.
                preis = float(bestand.market_price or 0.0)
            except BrokerFehler as exc:
                logger.warning("Krypto %s: ausfuehrbarer Verkaufspreis nicht sicher "
                               "messbar; kein Ausstieg in diesem Takt (%s)", symbol, exc)
                bericht["diagnostic_errors"].append("OKX_EXIT_PRICE_UNAVAILABLE")
                continue
            if preis <= 0:
                bericht["diagnostic_errors"].append("OKX_EXIT_PRICE_INVALID")
                continue
            position.hoechstkurs = max(position.hoechstkurs, preis)

            from crypto_strategy_mode import ZUSATZ_MODES as _ZUSATZ_MODES
            ft_auto = (str(position.verwaltung).upper() == VERWALTUNG_AUTO
                       and (position.entry_strategy_mode == "FREQTRADE_SAMPLE"
                            or position.entry_strategy_mode in _ZUSATZ_MODES))
            if ft_auto:
                strategy_exit, strategy_reason = self._strategy_exit(position, preis)
                if strategy_exit:
                    status = self._schliesse(position, preis, strategy_reason)
                    if status == "CLOSED":
                        bericht["geschlossen"].append(symbol)
                    elif status == "PARTIAL":
                        bericht.setdefault("teilverkauft", []).append(symbol)
                    continue

            # 3. Client-Stop als zweite Sicherung. Der Broker-Schutz ist die
            #    erste; diese Pruefung greift, wenn er fehlt oder nicht zog.
            #    Eine pausierte oder fremde Position wurde bereits oben als
            #    reiner Kontobestand ausgeschlossen.
            if preis <= position.stop:
                status = self._schliesse(position, preis, "Stop-Loss erreicht",
                                         allow_manual=True)
                if status == "CLOSED":
                    bericht["geschlossen"].append(symbol)
                elif status == "PARTIAL":
                    bericht.setdefault("teilverkauft", []).append(symbol)
                continue
            if not ft_auto and position.take_profit > 0 and preis >= position.take_profit:
                status = self._schliesse(position, preis, "Gewinnziel erreicht",
                                         allow_manual=True)
                if status == "CLOSED":
                    bericht["geschlossen"].append(symbol)
                elif status == "PARTIAL":
                    bericht.setdefault("teilverkauft", []).append(symbol)
                continue

            if not ft_auto and str(position.verwaltung).upper() == VERWALTUNG_AUTO:
                strategy_exit, strategy_reason = self._strategy_exit(position, preis)
                if strategy_exit:
                    status = self._schliesse(position, preis, strategy_reason)
                    if status == "CLOSED":
                        bericht["geschlossen"].append(symbol)
                    elif status == "PARTIAL":
                        bericht.setdefault("teilverkauft", []).append(symbol)
                    continue

            # MFE/MAE bewusst ERST HIER: die Aufzeichnung schreibt in SQLite
            # und darf die Stop- und Zielpruefung der naechsten Position
            # unter keinen Umstaenden verzoegern.
            try:
                import trade_ledger
                trade_ledger.hoechstkurs_melden(
                    "okx", symbol, preis,
                    broker_position_id=position.inst_id,
                    broker_account_fingerprint=position.account_fingerprint,
                    entry_order_id=position.order_id)
            except Exception:
                logger.debug("MFE/MAE OKX nicht fortgeschrieben", exc_info=True)

        # 4. Verwaiste offene Ledger-Zeilen abgleichen. Sie werden niemals
        #    automatisch verkauft oder einer Position zugeraten. Auch wiederholt
        #    fehlender Bestand ist kein Verkaufsbeleg; der Ledger bleibt offen.
        try:
            bericht["ledger_reconciliation"] = self._offene_ledger_abgleichen(bestaende)
        except Exception as exc:
            logger.warning("OKX-Ledgerabgleich fehlgeschlagen: %s", exc, exc_info=True)
            bericht["ledger_reconciliation"] = {"ok": False, "grund": str(exc)}
            bericht["diagnostic_errors"].append("OKX_LEDGER_RECONCILIATION_INCOMPLETE")

        # 5. Guthaben klassifizieren statt nur aufzuzaehlen.
        #    Vorher stand hier alle paar Minuten "OKX-Guthaben ohne
        #    Positionsbuch-Eintrag: BTC, XRP, USD, ETH" -- und warf damit
        #    Cash (USD), manuelle Altbestaende und echte ungeklaerte
        #    Exposure in einen Topf. Nur der letzte Fall ist gefaehrlich.
        # 9.5.4/9.5.5: Schutzorders ohne Position sind nirgends aufgefallen.
        # Muss VOR der Guthabenklassifizierung laufen -- sonst bewertet die
        # noch mit der Liste des vorigen Takts.
        verwaiste = self._verwaiste_schutzorders()
        self._verwaiste_schutz = list(verwaiste)
        bericht["verwaiste_schutzorders"] = list(verwaiste)
        self._verwaiste_schutzorders_melden(verwaiste)

        try:
            import exposure_klassifizierung as ek
            import trade_ledger
            # 9.5.8: Genau der Schnappschuss vom Anfang dieses Durchlaufs --
            # derselbe, aus dem oben die Buchmengen abgeglichen wurden. Wer
            # sperrt, muss dieselbe Zahl lesen wie der, der heilt.
            guthaben = dict(getattr(self, "_guthaben_dieser_runde", None) or {})
            if not guthaben:
                if hasattr(broker, "guthaben_schnappschuss"):
                    guthaben = broker.guthaben_schnappschuss()
                else:
                    guthaben = broker.client.balances() if hasattr(broker, "client") else {}
            preise = {p.symbol.upper(): float(p.market_price or 0.0)
                      for p in broker.positionen()}
            einstufung = ek.klassifiziere(
                guthaben,
                positionsbuch=self.buch.alle(),
                offene_orders=[{"symbol": s} for s in self._offene_order_symbole()],
                # 9.5.5: Schutzorders mitgeben -- ohne sie galt ein Bestand mit
                # scharfem SL/TP als frei verfuegbares Konto-Asset.
                schutzorders=list(getattr(self, "_verwaiste_schutz", [])),
                ledger_trades=trade_ledger.offene_trades("okx"),
                preise=preise, cfg=self.cfg)
            bericht["exposure"] = einstufung
            self._letzte_exposure = dict(einstufung)
            # 10.1.10: Diese Zeile lief jede ~6 s und stellte ~90 % des Logs.
            # INFO nur noch bei Aenderung; unveraendert bleibt sie DEBUG.
            _guthaben_kurz = ek.kurzfassung(einstufung)
            if _guthaben_kurz != getattr(self, "_guthaben_log_zuletzt", None):
                logger.info("OKX-Guthaben: %s", _guthaben_kurz)
                self._guthaben_log_zuletzt = _guthaben_kurz
            else:
                logger.debug("OKX-Guthaben: %s", _guthaben_kurz)
            if einstufung.get("einstiege_gesperrt"):
                self._exposure_sperre = einstufung.get("sperrgruende", [])
                self._exposure_gesperrte_waehrungen = list(
                    einstufung.get("gesperrte_waehrungen", []) or [])
                alert_text = "; ".join(self._exposure_sperre)
                old_text, old_at = self._last_exposure_alert
                cooldown = max(60.0, float(getattr(
                    self.cfg, "OKX_EXPOSURE_ALERT_COOLDOWN_SECONDS", 900.0)))
                if alert_text != old_text or time.time() - old_at >= cooldown:
                    self._melde("Krypto: neue Einstiege gesperrt -- ungeklaerter Bestand: "
                                + alert_text, wichtig=True)
                    self._last_exposure_alert = (alert_text, time.time())
            else:
                self._exposure_sperre = []
                self._exposure_gesperrte_waehrungen = []
                self._last_exposure_alert = ("", 0.0)
        except Exception as exc:
            logger.warning("Guthabenklassifizierung fehlgeschlagen: %s", exc, exc_info=True)
            bericht["diagnostic_errors"].append("OKX_EXPOSURE_CLASSIFICATION_INCOMPLETE")

        unknown = [p.symbol for p in self.buch.alle() if p.broker_state]
        if unknown or any(r.get("status") == "BROKER_STATE_UNKNOWN" for r in
                (bericht.get("ledger_reconciliation", {}).get("residual") or [])):
            bericht["diagnostic_errors"].append("BROKER_STATE_UNKNOWN")
        bericht["ok"] = True
        self.topf.setze_offene_positionen(len(self.buch.aktive()))
        # Der Abgleich ist einmal vollstaendig durchgelaufen -- das ist die
        # wichtigste Bedingung der Anlaufsperre. Ohne sie haette der Bot am
        # 25.08.2026 einen Fremdbestand als eigene Position uebernommen.
        self.bereitschaft.melde("reconciliation", not bericht["diagnostic_errors"],
                                ("BROKER_STATE_UNKNOWN: Bestandsklaerung offen" if unknown else
                                 f"{bericht['geprueft']} Positionen geprueft"))
        return bericht

    def _abgleich_abgebrochen(self, bericht: dict, grund: str) -> None:
        """10.8.1: Abbruch des Positionsabgleichs ohne gueltige Messung.

        Es wird nichts gebucht und nichts gesperrt -- aber auch nichts
        freigegeben. Die Bereitschaftsbedingung ``reconciliation`` bleibt
        offen, bis ein vollstaendiger Durchlauf sie wieder bestaetigt.
        """
        bericht["ok"] = False
        bericht["grund"] = grund
        bericht["abgebrochen"] = True
        try:
            self.bereitschaft.melde("reconciliation", False,
                                    f"Abgleich abgebrochen: {grund}"[:160])
        except Exception:
            logger.debug("Bereitschaft nach Abgleichsabbruch nicht gemeldet", exc_info=True)

    def _ledger_frist(self, name: str, standard: float) -> float:
        """Bestaetigungsfrist auch ohne vollstaendig gebaute Engine lesbar."""
        try:
            return max(0.0, float(getattr(getattr(self, "cfg", config), name, standard)))
        except (TypeError, ValueError):
            return float(standard)

    def _ledger_fehlmessung(self, trade_id: int) -> tuple[bool, int, str]:
        """10.8.1: Eine Fehlmessung fuer eine Ledgerzeile ohne Position zaehlen.

        Liefert (bestaetigt, anzahl, seit). Bestaetigt ist der Fehlbestand
        erst nach zwei Messungen in Folge UND dem Mindestalter der ersten
        (``OKX_POSITION_MISSING_CONFIRM_SECONDS``) -- dieselbe Regel, die
        seit 10.6.0 fuer Positionen im Buch gilt. Bis 10.8.0 buchte die
        erste Fehlmessung sofort einen Bestandsbeleg.
        """
        zaehler = getattr(self, "_fehlender_ledger_bestand", None)
        if zaehler is None:
            zaehler = self._fehlender_ledger_bestand = {}
        alt = zaehler.get(trade_id)
        try:
            anzahl = int(alt[0]) + 1
            seit = str(alt[1] or "")
        except (TypeError, ValueError, IndexError):
            anzahl, seit = 1, ""
        if not seit:
            seit = datetime.now(timezone.utc).isoformat()
        zaehler[trade_id] = (anzahl, seit)
        frist = self._ledger_frist("OKX_POSITION_MISSING_CONFIRM_SECONDS", 30.0)
        return self._messungen_bestaetigt(anzahl, seit, frist), anzahl, seit

    def _ledger_bestandsbeleg_geloest(self, trade: dict, konto: float,
                                      symbol: str) -> bool:
        """10.8.1: Rueckweg fuer Ledgerzeilen ohne Position.

        True heisst: Fuer diese Zeile ist kein Bestandsbeleg offen -- entweder
        gab es keinen, oder er wurde soeben aufgeloest, weil der Bestand die
        gebuchte Menge in zwei bestaetigten Messungen mit Mindestabstand
        (``OKX_POSITION_RESTORED_CONFIRM_SECONDS``) wieder deckt. False heisst:
        Der Beleg bleibt offen; die Zeile darf in diesem Takt nicht als
        Staubrest geschlossen werden, sonst waere der Beleg unaufloesbar.

        Der Rueckweg 10.6.0 (``_bestand_zurueckgewonnen``) kannte nur
        Positionen im Buch. Ein Lot-Rest hat kein Buch -- sein Beleg vom
        19.09.2026 (Trade 85, beobachtet "0" aus einem nicht lesbaren
        Schnappschuss) blieb deshalb bei 50.000 XRP im Konto fuer immer offen.
        """
        zaehler = getattr(self, "_wiedergesehener_ledger_bestand", None)
        if zaehler is None:
            zaehler = self._wiedergesehener_ledger_bestand = {}
        trade_id = int(trade.get("trade_id") or 0)
        try:
            from okx_accounting import pending_balance_gap
            beleg = pending_balance_gap(trade)
        except Exception as exc:
            logger.debug("Bestandsbeleg %s nicht lesbar: %s", trade_id, exc)
            zaehler.pop(trade_id, None)
            return False
        if beleg is None:
            zaehler.pop(trade_id, None)
            return True
        menge = float(trade.get("menge") or 0.0)
        if not _ist_positiv(konto) or not _ist_positiv(menge) or konto < menge:
            zaehler.pop(trade_id, None)
            return False
        alt = zaehler.get(trade_id)
        try:
            anzahl = int(alt[0]) + 1
            seit = str(alt[1] or "")
        except (TypeError, ValueError, IndexError):
            anzahl, seit = 1, ""
        if not seit:
            seit = datetime.now(timezone.utc).isoformat()
        zaehler[trade_id] = (anzahl, seit)
        frist = self._ledger_frist("OKX_POSITION_RESTORED_CONFIRM_SECONDS", 120.0)
        if not self._messungen_bestaetigt(anzahl, seit, frist):
            logger.info(
                "Krypto %s (Ledger %s): Konto weist %s aus und deckt die gebuchte "
                "Menge %s (%s. Messung seit %s). Der Bestandsbeleg bleibt bis zur "
                "Bestaetigung offen.", symbol, trade_id, f"{konto:g}", f"{menge:g}",
                anzahl, seit)
            return False
        try:
            from okx_accounting import resolve_balance_gap_restored
            geloest = resolve_balance_gap_restored(
                trade, konto, detail=f"ledgerzeile;snapshots={anzahl}")
        except Exception as exc:
            logger.warning("Krypto %s (Ledger %s): Bestandsbeleg nicht aufloesbar: %s",
                           symbol, trade_id, exc)
            zaehler.pop(trade_id, None)
            return False
        zaehler.pop(trade_id, None)
        if geloest:
            self._melde(
                f"Krypto {symbol}: Bestandsbeleg der Ledgerzeile {trade_id} aufgeloest -- "
                f"das Konto weist {konto:g} {symbol} aus und deckt die gebuchte Menge "
                f"{menge:g} in zwei bestaetigten Messungen; es gibt keinen Verkaufsbeleg. "
                f"{symbol} ist fuer Neueinstiege wieder frei.", wichtig=True)
        return True

    def _offene_ledger_abgleichen(self, bestaende: dict) -> dict:
        """Offene OKX-Ledgerzeilen gegen Buch und echten Bestand pruefen."""
        import trade_ledger

        # 10.8.1: Der Abgleich laeuft auch auf einer nur teilweise gebauten
        # Engine (Migration, Diagnose, Reparaturtests). Die Zaehler muessen
        # dann trotzdem existieren.
        for name, leer in (("_fehlender_ledger_bestand", {}),
                           ("_wiedergesehener_ledger_bestand", {}),
                           ("_historie_abgelehnt", set()), ("_rest_abgelehnt", {})):
            if getattr(self, name, None) is None:
                setattr(self, name, leer)
        buch_positionen = {str(p.symbol).upper(): p for p in self.buch.alle()}
        geschlossen: list[int] = []
        residual: list[dict] = []
        history_recovered: list[dict] = []
        account_mismatch: list[dict] = []
        gesehen: set[int] = set()
        # Der Datenbankabgleich wird auch von Migration/Diagnose ohne einen
        # vollstaendig gestarteten Broker-Hub verwendet.
        try:
            broker = self.broker
        except (AttributeError, KeyError):
            broker = None
        current_fingerprint = (broker.account_fingerprint()
                               if broker is not None and hasattr(broker, "account_fingerprint")
                               else "")

        # 9.0.12 konnte eine OKX-tradeId eines anderen Instruments fuer
        # identisch halten (z.B. LINK tradeId=1 und ONDO tradeId=1). Dadurch
        # blieb eine beweisbare Position ohne Ledgerzeile. Aus dem haltbaren
        # Positionsbuch wird dieser Eintrag jetzt idempotent rekonstruiert.
        existing_orders = {str(t.get("entry_order_id") or "")
                           for t in trade_ledger.offene_trades("okx")}
        repaired_ledgers: list[dict] = []
        for p in buch_positionen.values():
            if (not p.darf_schutz_ausfuehren or not p.order_id
                    or str(p.order_id) in existing_orders):
                continue
            try:
                trade_id = trade_ledger.trade_open(
                    broker="okx", symbol=p.symbol, menge=p.menge,
                    einstieg_preis=p.einstieg, asset_type="crypto",
                    waehrung=p.trade_quote_ccy, decision_id=p.decision_id,
                    paper=bool(p.paper), zeit=p.eroeffnet_am,
                    broker_position_id=p.inst_id, entry_order_id=p.order_id,
                    entry_fill_id=(p.fill_ids[0] if p.fill_ids else ""),
                    entry_fill_ids=list(p.fill_ids or []),
                    client_order_id=p.client_order_id or p.referenz,
                    order_tag=p.order_tag,
                    ownership_status="VERIFIED_BROKER_FILL_CHAIN",
                    entry_fee_by_currency=dict(p.entry_fee_by_currency or {}),
                    broker_account_fingerprint=current_fingerprint,
                    reconciliation_status="CONFIRMED_OPEN",
                    notiz="Ledger aus verifiziertem Positionsbuch 9.0.13 rekonstruiert",
                    critical=True)
            except Exception:
                logger.warning("Ledger-Rekonstruktion %s fehlgeschlagen", p.symbol,
                               exc_info=True)
                trade_id = None
            if trade_id:
                trade_ledger.set_protection(
                    trade_id, algo_id=p.protection_algo_id,
                    client_order_id=p.protection_client_order_id,
                    status=p.protection_status)
                repaired_ledgers.append({"trade_id": trade_id, "symbol": p.symbol})
                existing_orders.add(str(p.order_id))
        offen = trade_ledger.offene_trades("okx")

        def historical_exit(trade: dict) -> dict | None:
            """Noch nicht verbuchte SELL-Fills nach einem Einstieg nachladen."""
            if broker is None or not hasattr(broker, "historical_fills"):
                return None
            rows = broker.historical_fills(
                str(trade.get("symbol") or ""),
                since=str(trade.get("eingestiegen_am") or ""))
            sells = [r for r in rows if str(r.get("side") or "").lower() == "sell"
                     and float(r.get("fillSz") or 0) > 0
                     and float(r.get("fillPx") or 0) > 0]
            protection_algo_id = str(trade.get("protection_algo_id") or "")
            proven_orders: set[str] = set()
            if protection_algo_id:
                proven_orders.update(
                    broker.protection_exit_order_ids(protection_algo_id)
                    if hasattr(broker, "protection_exit_order_ids") else set())
            # Auch ein vom Client gestarteter Exit ist nur dann Eigentum des
            # Bots, wenn seine konkrete ordId zuvor persistent registriert
            # wurde. Ein beliebiger SELL desselben Symbols bleibt fremd.
            registry = self._order_registry()
            trade_entry_order = str(trade.get("entry_order_id") or "")
            trade_decision = str(trade.get("decision_id") or "")
            abgelehnt = getattr(self, "_historie_abgelehnt", None)
            if abgelehnt is None:
                abgelehnt = self._historie_abgelehnt = set()
            this_trade_id = int(trade.get("trade_id") or 0)
            for row in sells:
                oid = str(row.get("ordId") or "")
                meta = registry.metadata(oid) if oid else {}
                if (meta and str(meta.get("symbol") or "").upper() ==
                        str(trade.get("symbol") or "").upper()
                        and str(meta.get("role") or "").upper() == "EXIT"
                        and (not current_fingerprint
                             or not str(meta.get("account_fingerprint") or "")
                             or str(meta.get("account_fingerprint")) == current_fingerprint)):
                    # 10.8.1: Eigentum reicht nicht -- die Verkaufsorder muss
                    # zu DIESER Abstammungslinie gehoeren. Am 19.09.2026
                    # verkaufte der Bot ETH-Trade 90; die aeltere ETH-Restzeile
                    # 86 hielt denselben registrierten EXIT fuer ihren eigenen
                    # und lief in jedem Takt gegen "Broker-Exitanker gehoert zu
                    # einer anderen expliziten trade_id". Ohne Linienangabe in
                    # den Metadaten (Altregistrierung) bleibt der alte Weg.
                    meta_entry = str(meta.get("entry_order_id") or "")
                    meta_decision = str(meta.get("decision_id") or "")
                    if meta_entry and trade_entry_order:
                        if meta_entry != trade_entry_order:
                            continue
                    elif meta_decision and trade_decision:
                        if meta_decision != trade_decision:
                            continue
                    proven_orders.add(oid)
            if not proven_orders:
                return None
            sells = [r for r in sells if str(r.get("ordId") or "") in proven_orders
                     and (this_trade_id, str(r.get("ordId") or "")) not in abgelehnt]
            if not sells:
                return None
            group = trade_ledger.trade_group(
                broker="okx", symbol=str(trade.get("symbol") or ""),
                eingestiegen_am=str(trade.get("eingestiegen_am") or ""),
                account_fingerprint=str(trade.get("broker_account_fingerprint") or ""),
                paper=bool(trade.get("paper")),
                instrument_id=str(trade.get("broker_position_id") or ""),
                entry_order_id=str(trade.get("entry_order_id") or ""))
            booked_fill_ids: set[str] = set()
            legacy_already = 0.0
            for closed in group:
                if not closed.get("ausgestiegen_am"):
                    continue
                try:
                    exact_ids = {str(x) for x in json.loads(
                        str(closed.get("exit_fill_ids_json") or "[]")) if str(x)}
                except (TypeError, ValueError, json.JSONDecodeError):
                    exact_ids = set()
                if exact_ids:
                    booked_fill_ids.update(exact_ids)
                else:
                    # Nur echte Altdaten ohne Fill-ID muessen weiterhin
                    # mengenbasiert uebersprungen werden.
                    legacy_already += float(closed.get("menge") or 0)
            remaining_skip = max(0.0, legacy_already)
            # 10.8.1: Ein Fill, den der Ledger bereits einem ANDEREN Trade
            # zugeordnet hat, ist fuer diese Zeile kein Verkauf. Bis 10.8.0
            # fiel das erst in trade_close auf -- als Fehler mit Traceback,
            # in jedem Takt aufs Neue.
            fremd_gebucht: dict[str, int] = {}
            try:
                fremd_gebucht = trade_ledger.gebuchte_exit_fills(
                    broker="okx",
                    account=str(trade.get("broker_account_fingerprint") or ""),
                    instrument=str(trade.get("broker_position_id") or ""),
                    fill_ids=[okx_fill_identity(r, current_fingerprint) for r in sells],
                    paper=bool(trade.get("paper", 1)))
            except Exception:
                logger.debug("Exit-Fill-Zuordnung nicht lesbar", exc_info=True)
            unbooked: list[tuple[dict, float, float, float]] = []
            for row in sells:
                composite_id = okx_fill_identity(row, current_fingerprint)
                if composite_id in booked_fill_ids:
                    continue
                gebucht_fuer = fremd_gebucht.get(composite_id)
                if gebucht_fuer and gebucht_fuer != this_trade_id:
                    logger.debug("Ledger %s: Fill %s gehoert bereits Trade %s",
                                 this_trade_id, composite_id, gebucht_fuer)
                    continue
                qty = float(row.get("fillSz") or 0)
                if remaining_skip >= qty - 1e-12:
                    remaining_skip -= qty
                    continue
                if remaining_skip > 0:
                    qty -= remaining_skip; remaining_skip = 0.0
                if qty > 1e-12:
                    unbooked.append((row, qty, float(row.get("fillPx") or 0),
                                     abs(float(row.get("fee") or 0))))
            open_qty = float(trade.get("menge") or 0)
            available = sum(x[1] for x in unbooked)
            sold = min(open_qty, available)
            if sold <= 1e-12:
                return None
            left = sold; value = 0.0; fees = 0.0
            used_rows: list[dict] = []
            for row, qty, price, fee in unbooked:
                used = min(left, qty)
                if used <= 0:
                    break
                value += used * price
                fees += fee * (used / qty)
                used_rows.append(row)
                left -= used
            avg = value / sold if sold > 0 else 0.0
            used_fill_ids = [okx_fill_identity(row, current_fingerprint)
                             for row in used_rows]
            used_order_ids = sorted({str(row.get("ordId") or "")
                                     for row in used_rows if row.get("ordId")})
            try:
                closed_id = trade_ledger.trade_close(
                    broker="okx", symbol=str(trade.get("symbol") or ""),
                    ausstieg_preis=avg, menge=sold,
                    exit_grund="OKX-Fill-Historie nachgeladen",
                    gebuehr=fees, einstieg_preis=trade.get("einstieg_preis"),
                    eingestiegen_am=trade.get("eingestiegen_am"),
                    asset_type="crypto", waehrung=str(trade.get("waehrung") or ""),
                    paper=bool(trade.get("paper", 1)),
                    trade_id=int(trade.get("trade_id") or 0),
                    exit_order_id=(used_order_ids[0] if used_order_ids else ""),
                    exit_fill_ids=used_fill_ids,
                    event_id=(used_fill_ids[0] if used_fill_ids else ""),
                    broker_position_id=str(trade.get("broker_position_id") or ""),
                    broker_account_fingerprint=str(
                        trade.get("broker_account_fingerprint") or ""),
                    entry_order_id=str(trade.get("entry_order_id") or ""),
                    notiz="Paginiert aus /trade/fills-history rekonstruiert",
                    critical=True)
            except Exception as exc:
                # 10.8.1: Einmal warnen, dann merken. Ein fachlich unklarer
                # Verkauf wird durch Wiederholung nicht klarer, und ein
                # Traceback je Takt (833 MB Journal am 19.09.2026) hilft
                # niemandem. Technische Fehler behalten den Traceback.
                from trade_ledger import LedgerZuordnungUnklar
                for oid in used_order_ids:
                    abgelehnt.add((this_trade_id, oid))
                if isinstance(exc, LedgerZuordnungUnklar):
                    logger.warning(
                        "OKX-Historienfill %s (Ledger %s, Order %s) vom Ledger abgelehnt: %s "
                        "-- kein weiterer Versuch fuer diese Order.",
                        trade.get("symbol"), this_trade_id,
                        ",".join(used_order_ids) or "?", exc)
                else:
                    logger.warning("OKX-Historienfill %s konnte nicht ins Ledger geschrieben werden: %s",
                                   trade.get("symbol"), exc, exc_info=True)
                return None
            return ({"trade_id": int(closed_id or trade.get("trade_id") or 0),
                     "symbol": str(trade.get("symbol") or ""),
                     "nachgebucht": sold, "preis": avg}
                    if closed_id else None)

        for trade in offen:
            trade_id = int(trade.get("trade_id") or 0)
            symbol = str(trade.get("symbol") or "").upper()
            if not trade_id or not symbol:
                continue
            gesehen.add(trade_id)
            stored_fingerprint = str(trade.get("broker_account_fingerprint") or "")
            if stored_fingerprint and current_fingerprint and stored_fingerprint != current_fingerprint:
                trade_ledger.set_reconciliation_status(
                    trade_id, "ACCOUNT_MISMATCH",
                    notiz="Trade stammt aus einem anderen OKX Demo/Live-/Unterkonto")
                account_mismatch.append({"trade_id": trade_id, "symbol": symbol})
                continue

            position = buch_positionen.get(symbol)
            if position is not None:
                try:
                    stored_fills = set(json.loads(
                        str(trade.get("entry_fill_ids_json") or "[]")))
                except (TypeError, ValueError, json.JSONDecodeError):
                    stored_fills = set()
                if str(trade.get("entry_fill_id") or "").strip():
                    stored_fills.add(str(trade.get("entry_fill_id")))
                position_fills = {str(x) for x in (position.fill_ids or []) if str(x)}
                account_for_ids = (str(trade.get("broker_account_fingerprint") or "")
                                   or str(position.account_fingerprint or ""))
                instrument_for_ids = normalize_inst_id(
                    str(trade.get("broker_position_id") or position.inst_id or ""))
                order_for_ids = str(trade.get("entry_order_id") or position.order_id or "")
                if account_for_ids and instrument_for_ids and order_for_ids:
                    stored_fills = {
                        value if value.startswith("okx:") else
                        f"okx:{account_for_ids}:{instrument_for_ids}:{order_for_ids}:{value}"
                        for value in stored_fills}
                    position_fills = {
                        value if value.startswith("okx:") else
                        f"okx:{account_for_ids}:{instrument_for_ids}:{order_for_ids}:{value}"
                        for value in position_fills}
                # v9.2: NUR die Broker-ID-Kette entscheidet ueber Eigentum.
                # Vorher stand hier position.darf_schutz_ausfuehren -- damit war
                # der Verwaltungsmodus Teil des Eigentumsnachweises. Eine wegen
                # eines Strategieproblems pausierte Position galt dadurch als
                # "Lokaler Eintrag ohne identische ordId/clOrdId/tradeId-Kette",
                # obwohl die Kette vollstaendig vorlag.
                exact = bool(
                    position.ownership_chain_complete
                    and str(trade.get("entry_order_id") or "") == str(position.order_id or "")
                    and bool(stored_fills & position_fills)
                    and (not str(trade.get("client_order_id") or "")
                         or str(trade.get("client_order_id")) ==
                         str(position.client_order_id or position.referenz or "")))
                self._fehlender_ledger_bestand.pop(trade_id, None)
                if exact:
                    # v9.2: Ein Strategieproblem wird getrennt ausgewiesen. Die
                    # Position bleibt bestaetigt -- sie gehoert dem Bot.
                    zusatz = ""
                    if str(getattr(position, "management_status", "")):
                        zusatz = (f"; Verwaltung: {position.management_status} "
                                  "(Eigentumsnachweis unveraendert)")
                    trade_ledger.set_reconciliation_status(
                        trade_id, ("BROKER_STATE_UNKNOWN" if position.broker_state else "CONFIRMED_OPEN"),
                        broker_position_id=str(position.inst_id or ""),
                        notiz="ordId, clOrdId und echte OKX-tradeId-Fills stimmen ueberein"
                              + zusatz)
                else:
                    trade_ledger.set_reconciliation_status(
                        trade_id, "EXTERNAL_OBSERVE",
                        broker_position_id=str(position.inst_id or ""),
                        notiz=("Lokaler Eintrag ohne identische ordId/clOrdId/tradeId-Kette; "
                               "Kontoguthaben bleibt unangetastet"))
                    residual.append({"trade_id": trade_id, "symbol": symbol,
                                     "status": "EXTERNAL_OBSERVE"})
                continue

            # Fehlende/alte REST-Seiten duerfen einen Verkauf nicht unsichtbar
            # lassen. Vor der Guthabenbewertung wird die archivierte Fill-
            # Historie paginiert nachgeladen und bereits verbuchte Teilfills
            # werden mengenbasiert abgezogen.
            try:
                recovered = historical_exit(trade)
            except Exception as exc:
                logger.warning("OKX-Fill-Historie %s nicht lesbar: %s", symbol, exc)
                recovered = None
            if recovered:
                history_recovered.append(recovered)
                refreshed = trade_ledger.offener_trade("okx", symbol)
                if refreshed is None:
                    self._fehlender_ledger_bestand.pop(trade_id, None)
                    continue
                trade = refreshed
                trade_id = int(trade.get("trade_id") or trade_id)
                gesehen.add(trade_id)
            bestand = bestaende.get(symbol)
            konto = float(getattr(bestand, "quantity", 0.0) or 0.0) if bestand else 0.0
            konto_preis = float(getattr(bestand, "market_price", 0.0) or 0.0) if bestand else 0.0
            dust_limit = float(getattr(getattr(self, "cfg", config),
                                       "OKX_DUST_VALUE_LIMIT", 1.0))
            ledger_menge = float(trade.get("menge") or 0.0)
            # 10.8.1: Die Staubfrage stellt sich fuer die BOTMENGE dieser Zeile,
            # nicht fuer den Gesamtbestand des Kontos. Bis 10.8.0 wurde der
            # XRP-Rest 85 (0,0000532 XRP, ein Zehntel Cent) an 50.000 XRP
            # Fremdbestand gemessen und blieb deshalb fuer immer "Exposure".
            rest = min(konto, ledger_menge) if _ist_positiv(ledger_menge) else konto
            if _ist_positiv(konto) and konto_preis > 0 and rest * konto_preis <= dust_limit:
                self._fehlender_ledger_bestand.pop(trade_id, None)
                if not self._ledger_bestandsbeleg_geloest(trade, konto, symbol):
                    # Beleg noch offen: erst der bestaetigte Rueckweg loest ihn.
                    # Eine jetzt geschlossene Restzeile liesse ihn fuer immer
                    # offen -- und den Coin fuer immer gesperrt.
                    trade_ledger.set_reconciliation_status(
                        trade_id, "RESIDUAL_EXPOSURE",
                        notiz=("Restmenge im Konto vorhanden; Bestandsbeleg wartet auf "
                               "die zweite bestaetigte Messung"))
                    residual.append({"trade_id": trade_id, "symbol": symbol,
                                     "ledger_menge": ledger_menge, "broker_menge": konto,
                                     "status": "RESIDUAL_EXPOSURE",
                                     "bestandsbeleg": "PENDING"})
                    continue
                try:
                    from okx_residual_inventory import classify
                    meta = broker.client.instrument(trade['broker_position_id'])
                    rules = dict(inst_id=meta.inst_id, lot_size=str(meta.lot_size),
                        min_size=str(meta.min_size), observed_balance=str(konto),
                        checked_at=datetime.now(timezone.utc).isoformat())
                    if classify(trade_id, allow_open=True, market_rules=rules):
                        geschlossen.append(trade_id)
                    if getattr(self, "_rest_abgelehnt", None):
                        self._rest_abgelehnt.pop(trade_id, None)
                except (ValueError, AttributeError, BrokerFehler) as exc:
                    # A balance-only disappearance must keep a real result gap.
                    # 10.8.1: Derselbe Grund wird nicht in jedem Takt gewarnt.
                    grund = f"{type(exc).__name__}: {exc}"
                    merker = getattr(self, "_rest_abgelehnt", None)
                    if merker is None:
                        merker = self._rest_abgelehnt = {}
                    if merker.get(trade_id) != grund:
                        merker[trade_id] = grund
                        logger.warning('Rest %s (Ledger %s) bleibt im Abgleich: %s',
                                       symbol, trade_id, exc)
                    else:
                        logger.debug('Rest %s (Ledger %s) bleibt im Abgleich: %s',
                                     symbol, trade_id, exc)
                continue

            # Ein migrierter Alteintrag ohne irgendeinen Eigentumsbeweis darf
            # nicht allein deshalb fuer immer als Bot-Position gelten, weil
            # dasselbe Asset als frei verfuegbares Kontoguthaben existiert.
            # Genau das ist beim gemeldeten SOL-Fall passiert: Der Ledger kannte
            # weder decision/order/fill noch Positionsbuch, OKX meldete aber ein
            # reales SOL-Guthaben. Zwei vollstaendige, gleiche Snapshots schliessen
            # hier nur den unbeweisbaren Ledger-Eintrag. Das SOL-Guthaben bleibt
            # unangetastet und wird anschliessend als EXTERNAL_HOLDING angezeigt.
            legacy_unbeweisbar = (
                str(trade.get("link_status") or "") == "LEGACY_UNLINKED"
                and not trade.get("decision_id")
                and not str(trade.get("entry_order_id") or "").strip()
                and not str(trade.get("entry_fill_id") or "").strip()
                and not str(trade.get("broker_position_id") or "").strip()
                and not str(trade.get("broker_account_fingerprint") or "").strip()
            )
            if legacy_unbeweisbar:
                trade_ledger.set_reconciliation_status(trade_id, "LEGACY_AUDIT",
                    notiz="Alteintrag ohne Eigentumskette: nur historischer Hinweis, kein Verkaufsbeleg")
                residual.append({"trade_id": trade_id, "symbol": symbol, "status": "LEGACY_AUDIT"})
                continue

            if _ist_positiv(konto):
                self._fehlender_ledger_bestand.pop(trade_id, None)
                # 10.8.1: Ein offener Bestandsbeleg dieser Zeile loest sich,
                # sobald der Bestand die gebuchte Menge in zwei bestaetigten
                # Messungen wieder deckt. Die Zeile selbst bleibt offen und
                # wird weiterhin nicht automatisch verkauft.
                beleg_offen = not self._ledger_bestandsbeleg_geloest(trade, konto, symbol)
                trade_ledger.set_reconciliation_status(
                    trade_id, "RESIDUAL_EXPOSURE",
                    notiz=("Coin-Guthaben vorhanden, aber kein Eintrag im OKX-Positionsbuch; "
                           "wird nicht automatisch verkauft"))
                eintrag = {
                    "trade_id": trade_id, "symbol": symbol,
                    "ledger_menge": ledger_menge,
                    "broker_menge": konto,
                    "status": "RESIDUAL_EXPOSURE",
                }
                if beleg_offen:
                    eintrag["bestandsbeleg"] = "PENDING"
                residual.append(eintrag)
                continue

            # 10.8.1: Zwei Messungen in Folge plus Mindestalter -- wie fuer
            # Positionen im Buch seit 10.6.0. Vorher genuegte EINE leere
            # Messung fuer einen Bestandsbeleg, der den Coin sperrte.
            if getattr(self, "_wiedergesehener_ledger_bestand", None):
                self._wiedergesehener_ledger_bestand.pop(trade_id, None)
            bestaetigt, anzahl, seit = self._ledger_fehlmessung(trade_id)
            if not bestaetigt:
                logger.info(
                    "Krypto %s (Ledger %s): Bestand fehlt im Schnappschuss (%s. Messung, "
                    "seit %s). Es wird nichts gebucht und nichts gesperrt, bis eine "
                    "zweite Messung mit Mindestabstand den Befund bestaetigt.",
                    symbol, trade_id, anzahl, seit)
                residual.append({"trade_id": trade_id, "symbol": symbol,
                                 "status": "BESTAND_FEHLT_UNBESTAETIGT",
                                 "messungen": anzahl, "seit": seit})
                continue

            from okx_accounting import mark_balance_gap
            from okx_receipt_math import EvidenceError
            try:
                mark_balance_gap(trade, 0, float(trade.get("menge") or 0))
            except EvidenceError:
                pass  # The UNKNOWN ledger status itself keeps unanchored inventory blocked.
            trade_ledger.set_reconciliation_status(trade_id, "BROKER_STATE_UNKNOWN",
                notiz="Kein Bestand und kein SELL-Fill: Ledger bleibt offen; kein wirtschaftlicher Abschluss")
            residual.append({"trade_id": trade_id, "symbol": symbol,
                             "status": "BROKER_STATE_UNKNOWN"})

        for trade_id in list(self._fehlender_ledger_bestand):
            if trade_id not in gesehen:
                self._fehlender_ledger_bestand.pop(trade_id, None)
        for trade_id in list(getattr(self, "_wiedergesehener_ledger_bestand", {}) or {}):
            if trade_id not in gesehen:
                self._wiedergesehener_ledger_bestand.pop(trade_id, None)
        if geschlossen:
            self._melde(
                "Krypto: verwaiste offene Historieneintraege wurden nach zwei "
                "Brokerabgleichen geschlossen. Ergebnis bleibt als unbekannt markiert: "
                + ", ".join(str(x) for x in geschlossen),
                wichtig=True)
        return {"ok": True, "geschlossen": geschlossen, "residual": residual,
                "history_recovered": history_recovered,
                "repaired_ledgers": repaired_ledgers,
                "account_mismatch": account_mismatch,
                "erneut_zu_pruefen": sorted(self._fehlender_ledger_bestand)}

    def _verarbeite_manuelle_auftraege(self, broker) -> list[dict]:
        """Fuehrt WebUI-Absichten einmalig im Handelskern aus."""
        import manual_trade_control as controls
        import trade_ledger
        results = []
        # Resume local completion after a fill/ledger commit followed by a crash.
        # The exact closed entry lineage is sufficient; no broker POST is needed.
        for old in controls.unresolved():
            row = trade_ledger.trade_detail(int(old.get('trade_id') or 0)) or {}
            if (old.get('action') != 'SELL' or not row.get('ausgestiegen_am')
                    or not row.get('exit_order_id') or not json.loads(row.get('exit_fill_ids_json') or '[]')
                    or row.get('broker') != 'okx'
                    or row.get('symbol') != old.get('symbol')
                    or row.get('broker_account_fingerprint') != old.get('account_fingerprint')
                    or row.get('broker_account_fingerprint') != str(broker.account_fingerprint() or '')
                    or bool(row.get('paper')) != bool(broker.demo)):
                continue
            lineage = trade_ledger.entry_lineage('okx', row['broker_position_id'], row['entry_order_id'],
                row['broker_account_fingerprint'], paper=bool(row['paper']))
            if not lineage or any(not x.get('ausgestiegen_am') for x in lineage):
                continue
            lock = controls.add_lock(broker='okx', account_fingerprint=row['broker_account_fingerprint'],
                symbol=row['symbol'], duration=str(old.get('lock') or '6H'), actor=str(old.get('actor') or 'webui'),
                reason='Manueller Verkaufsauftrag nach eindeutigem Mengenbeleg abgeschlossen', source_id=old['id'])
            controls.update(old['id'], 'SUCCEEDED',
                'Vollstaendige Schliessung aus Ledger-/Fillbelegen bestaetigt; Ergebnisqualitaet separat ausgewiesen.',
                lock_id=lock['id'])
        # A persisted intention is proof of *not yet submitting*. A SUBMITTING
        # or UNCLEAR attempt is never released by this recovery path.
        for old in controls.unresolved():
            try:
                started = datetime.fromisoformat(str(old.get('updated_at') or old.get('created_at')).replace('Z', '+00:00'))
                if (datetime.now(timezone.utc) - started).total_seconds() < 120:
                    continue
            except (TypeError, ValueError):
                continue
            p = self.buch.hole(str(old.get("symbol") or "").upper())
            if (p and p.exit_state == "MANUAL_REQUESTED"
                    and p.manual_exit_command_id == old.get("id")
                    and p.account_fingerprint == str(broker.account_fingerprint() or "")
                    and p.paper == bool(broker.demo)):
                previous = dict(p.manual_exit_previous)
                if not previous:
                    continue
                for key, value in previous.items():
                    if key in {"verwaltung", "verwaltungsnotiz", "exit_state",
                               "manual_changed_at", "manual_changed_by"}:
                        setattr(p, key, value)
                p.manual_exit_command_id = ""
                p.manual_exit_previous = {}
                self.buch.setze(p)
                controls.update(str(old['id']), "FAILED",
                                "Vor der Orderuebermittlung unterbrochen; vorherige Verwaltung wiederhergestellt.")
        for old in controls.unresolved():
            p = self.buch.hole(str(old.get('symbol') or '').upper())
            if (not p or old.get('action') != 'SELL' or p.exit_state != 'MANUAL_EXIT_PENDING'
                    or p.exit_attempt_id or p.exit_client_order_id or p.exit_order_ids or p.exit_fill_ids
                    or p.exit_fehlversuche or p.manual_exit_command_id
                    or p.account_fingerprint != str(broker.account_fingerprint() or '')
                    or p.paper != bool(broker.demo)):
                continue
            trade = trade_ledger.trade_detail(int(old.get('trade_id') or 0)) or {}
            if (trade.get('broker_account_fingerprint') != p.account_fingerprint
                    or bool(trade.get('paper')) != p.paper or trade.get('entry_order_id') != p.order_id
                    or old.get('account_fingerprint') != p.account_fingerprint
                    or old.get('instrument') != p.inst_id):
                continue
            from execution_lifecycle import assert_clear
            try:
                assert_clear(broker='okx', account=p.account_fingerprint,
                    environment='DEMO' if p.paper else 'LIVE', instrument=p.inst_id, side='SELL')
            except BrokerFehler:
                continue
            # MANUAL_EXIT_PENDING was written only before the old self-blocking
            # guard; absent attempt fields prove that this path never submitted.
            p.exit_state = 'MANUAL'
            p.exit_last_detail = 'Alte manuelle Anforderung vor jedem Verkaufsversuch abgebrochen; neuer Bedienauftrag moeglich.'
            self.buch.setze(p)
            controls.update(old['id'], 'FAILED', p.exit_last_detail)
        for command in controls.pending():
            command_id = str(command.get("id") or "")
            if not controls.claim(command_id):
                continue
            symbol = str(command.get("symbol") or "").upper()
            position = self.buch.hole(symbol)
            try:
                if position is None or not position.ist_bewiesene_botposition:
                    raise ValueError("Keine eindeutig bewiesene Botposition im Positionsbuch.")
                current_account = str(getattr(broker, "account_fingerprint", lambda: "")() or "")
                current_env = "DEMO" if bool(broker.demo) else "LIVE"
                if command.get("environment") not in (None, "", current_env):
                    raise ValueError("Der Auftrag gehoert zu einer anderen DEMO/LIVE-Umgebung.")
                trade = trade_ledger.offener_trade("okx", symbol,
                    trade_id=command.get("trade_id"), paper=bool(broker.demo),
                    broker_account_fingerprint=current_account,
                    broker_position_id=position.inst_id, entry_order_id=position.order_id)
                if not trade or int(trade.get("trade_id") or 0) != int(command.get("trade_id") or 0):
                    raise ValueError("Ledger-Trade und Positionsbuch stimmen nicht exakt ueberein.")
                expected_account = str(command.get("account_fingerprint") or "")
                current_account = str(getattr(broker, "account_fingerprint", lambda: "")() or "")
                if expected_account and expected_account != current_account:
                    raise ValueError("Der Auftrag gehoert zu einem anderen OKX-Konto.")
                if str(command.get("instrument") or "") and str(command.get("instrument")) != str(position.inst_id):
                    raise ValueError("Das OKX-Instrument des Auftrags stimmt nicht mit der Position ueberein.")
                # Vor jeder manuellen Handelswirkung erneut Brokerwahrheit
                # pruefen. Ein bereits extern verkaufter Bot-Trade darf nie
                # aus einem zufaellig vorhandenen Konto-Asset bedient werden.
                prior_exit = self._externer_verkaufsbeweis(position, position.menge)
                if prior_exit:
                    raise ValueError(
                        "OKX meldet bereits passende Verkaufsfills; zuerst den Positionsabgleich ausfuehren.")
                balances = {str(row.symbol).upper(): row for row in broker.positionen()}
                held = float(getattr(balances.get(symbol), "quantity", 0.0) or 0.0)
                if held + max(1e-12, position.menge * 0.001) < position.menge:
                    raise ValueError(
                        "OKX-Gesamtbestand ist kleiner als die bewiesene Botposition; keine manuelle Order gesendet.")
                controls.update(command_id, "PROCESSING", "Vom Handelskern uebernommen.")
                actor = str(command.get("actor") or "webui")
                action = str(command.get("action") or "")

                if action == "SET_PROTECTION":
                    stop = float(command.get("stop") or 0.0)
                    take = float(command.get("take_profit") or 0.0)
                    quote = broker.execution_quote(
                        self._instrument(symbol, position.inst_id), position.menge, side="sell")
                    current = float(quote.get("vwap") or 0.0)
                    max_risk = max(0.01, float(getattr(
                        self.cfg, "MANUAL_CRYPTO_MAX_STOP_DISTANCE_PCT", 0.25)))
                    if current <= 0 or not (stop < current < take):
                        raise ValueError("Es muss gelten: Stop-Loss < aktueller Verkaufskurs < Take-Profit.")
                    if (current - stop) / current > max_risk:
                        raise ValueError(
                            f"Stop-Loss liegt mehr als {max_risk * 100:.1f}% unter dem aktuellen Kurs.")
                    # Unmittelbar vor dem Broker-POST dauerhaft auf manuell
                    # schalten. Auch ein unklarer Amend darf danach keinen
                    # Freqtrade-Ausgang mehr ausloesen.
                    position.manuell(actor, "Manuelle Verwaltung ueber NEXUS aktiviert")
                    position.exit_state = "MANUAL"
                    self.buch.setze(position)
                    try:
                        state = broker.amend_position_protection(
                            self._instrument(symbol, position.inst_id), position.menge,
                            stop, take, algo_id=position.protection_algo_id,
                            trade_quote_ccy=position.trade_quote_ccy)
                    except OrderStatusUnklar as exc:
                        # v9.1: Der Amend-POST ist bereits angenommen, nur das
                        # Ruecklesen scheiterte. OKX kann den NEUEN Stop
                        # fuehren, waehrend der lokale Client-Stop noch mit dem
                        # ALTEN rechnet -- bis 9.0.15 lief das stumm
                        # auseinander, ohne Wiederaufsetzpunkt.
                        #
                        # Sicher ist der ENGERE der beiden Werte: er loest
                        # hoechstens zu frueh aus, nie zu spaet.
                        position.stop = max(float(position.stop or 0.0), float(stop))
                        position.broker_schutz = False
                        position.protection_status = "UNKNOWN_AFTER_AMEND"
                        position.exit_state = "MANUAL"
                        self.buch.setze(position)
                        controls.update(
                            command_id, "UNCLEAR",
                            "OKX hat die Aenderung angenommen, konnte sie aber nicht "
                            "bestaetigen. Der lokale Stop wurde auf den engeren Wert "
                            f"({position.stop:g}) gesetzt und der Broker-Schutz wird "
                            "beim naechsten Abgleich neu geprueft.")
                        self._melde(
                            f"Krypto {symbol}: TP/SL-Aenderung unbestaetigt. Lokaler "
                            f"Stop steht sicherheitshalber auf {position.stop:g}; "
                            "bitte die Schutzorder im OKX-Konto pruefen.",
                            wichtig=True, klasse="KRITISCH")
                        results.append({"id": command_id, "ok": False, "unclear": True})
                        continue
                    position.stop = float(state.get("stop") or stop)
                    position.take_profit = float(state.get("take_profit") or take)
                    position.broker_schutz = True
                    position.protection_status = "ACTIVE"
                    position.protection_algo_id = str(state.get("algo_id") or position.protection_algo_id)
                    position.protection_client_order_id = str(
                        state.get("algo_client_id") or position.protection_client_order_id)
                    self.buch.setze(position)
                    self._ledger_schutz_synchronisieren(position, str(state.get("detail") or ""))
                    detail = (f"Manuelle Verwaltung aktiv: SL {position.stop:g}, "
                              f"TP {position.take_profit:g}. Freqtrade-ROI und "
                              "Strategie-Exit sind fuer diese Position deaktiviert.")
                    controls.update(command_id, "SUCCEEDED", detail)
                    self._melde(f"Krypto {symbol}: {detail}", wichtig=True)
                    results.append({"id": command_id, "ok": True, "action": action})
                    continue

                # v9.1: Die vorherige Verwaltung wird gemerkt. Ein
                # FEHLGESCHLAGENER Verkauf darf die Position nicht dauerhaft
                # auf MANUELL stehen lassen -- bis 9.0.15 gab es im ganzen Code
                # keinen Weg zurueck auf AUTO, und Freqtrade-ROI sowie
                # Strategie-Exit waren fuer diese Position danach tot, obwohl
                # der Nutzer sie nur verkaufen wollte.
                if str(position.exit_state or "").upper() in {
                        "SUBMITTING", "UNCLEAR", "MANUAL_EXIT_PENDING", "ACCOUNTING_PENDING", "MANUAL_REQUESTED"}:
                    raise OrderStatusUnklar("Bestehender Verkaufsversuch muss zuerst abgeglichen werden.")
                # Read-only work happens before changing management. In
                # particular an unavailable quote cannot disable auto exits.
                manual_price = float(broker.execution_quote(
                    self._instrument(symbol, position.inst_id), position.menge,
                    side="sell").get("vwap") or 0.0)
                if not math.isfinite(manual_price) or manual_price <= 0:
                    raise ValueError("Kein gueltiger aktueller Verkaufskurs.")
                previous = {key: getattr(position, key) for key in (
                    "verwaltung", "verwaltungsnotiz", "exit_state", "manual_changed_at", "manual_changed_by")}
                position.manual_exit_previous = previous
                position.manual_exit_command_id = command_id
                position.manuell(actor, "Manueller Verkauf ueber NEXUS angefordert")
                position.exit_state = "MANUAL_REQUESTED"
                self.buch.setze(position)
                outcome = self._schliesse(
                    position, manual_price,
                    "Manueller Verkauf über NEXUS", allow_manual=True,
                    explicit_manual=True, manual_command_id=command_id)
                if outcome == "CLOSED":
                    lock = controls.add_lock(
                        broker="okx", account_fingerprint=current_account, symbol=symbol,
                        duration=str(command.get("lock") or "6H"),
                        reason="Position manuell ueber NEXUS verkauft", actor=actor, source_id=command_id)
                    controls.update(command_id, "SUCCEEDED",
                                    "Verkauf vollstaendig bestaetigt; Wiedereinstieg gesperrt.",
                                    lock_id=lock["id"])
                    results.append({"id": command_id, "ok": True, "action": action})
                elif outcome in {"UNCLEAR", "ACCOUNTING_PENDING"}:
                    # Hier bleibt MANUELL richtig: der Verkauf kann beim Broker
                    # liegen. Eine automatische Verwaltung waere gefaehrlich.
                    controls.update(command_id, "UNCLEAR",
                                    "Brokerzustand unklar; kein zweiter Auftrag wird gesendet.")
                    results.append({"id": command_id, "ok": False, "unclear": True})
                elif outcome == "PARTIAL":
                    # v9.1: Ein Teilverkauf ist ausgefuehrt worden. Vorher fiel
                    # er in den else-Zweig und wurde als "nicht ausgefuehrt"
                    # gemeldet -- und die angeforderte Wiedereinstiegssperre
                    # blieb aus.
                    lock = controls.add_lock(
                        broker="okx", account_fingerprint=current_account, symbol=symbol,
                        duration=str(command.get("lock") or "6H"),
                        reason="Position manuell ueber NEXUS teilweise verkauft",
                        actor=actor, source_id=command_id)
                    controls.update(
                        command_id, "PARTIAL",
                        "Teilweise verkauft; der gefuehrte Rest bleibt mit Schutz "
                        "bestehen. Wiedereinstieg gesperrt.", lock_id=lock["id"])
                    self._melde(
                        f"Krypto {symbol}: manueller Verkauf teilweise ausgefuehrt. "
                        "Der Rest bleibt im Positionsbuch und behaelt seinen Schutz.",
                        wichtig=True)
                    results.append({"id": command_id, "ok": True, "partial": True,
                                    "action": action})
                else:
                    # Bewiesen nicht ausgefuehrt: die Verwaltung wird auf den
                    # Stand vor dem Auftrag zurueckgesetzt.
                    position.verwaltung = previous["verwaltung"]
                    position.verwaltungsnotiz = previous["verwaltungsnotiz"]
                    position.manual_changed_at = previous["manual_changed_at"]
                    position.manual_changed_by = previous["manual_changed_by"]
                    if position.exit_state == "MANUAL_REQUESTED":
                        position.exit_state = previous["exit_state"]
                    position.manual_exit_command_id = ""
                    position.manual_exit_previous = {}
                    self.buch.setze(position)
                    controls.update(command_id, "FAILED",
                                    "Verkauf nicht ausgefuehrt; Position, Verwaltung und "
                                    "bestaetigter Schutz bleiben unveraendert.")
                    results.append({"id": command_id, "ok": False, "action": action})
            except OrderStatusUnklar as exc:
                controls.update(command_id, "UNCLEAR", str(exc))
                results.append({"id": command_id, "ok": False, "unclear": True})
            except (BrokerFehler, ValueError) as exc:
                controls.update(command_id, "FAILED", str(exc))
                results.append({"id": command_id, "ok": False, "detail": str(exc)})
        return results

    def _externer_verkaufsbeweis(self, position: KryptoPosition,
                                 expected_qty: float) -> dict:
        broker = self.broker
        if broker is None or not hasattr(broker, "external_exit_evidence"):
            return {}
        try:
            expected_orders = {str(x) for x in position.exit_order_ids if str(x)}
            if (position.exit_client_order_id
                    and hasattr(broker, "reconcile_exit_evidence")):
                recovered = broker.reconcile_exit_evidence(
                    inst_id=position.inst_id,
                    client_order_id=position.exit_client_order_id,
                    order_id=(next(iter(expected_orders), "")),
                    expected_qty=expected_qty,
                    reference_price=position.einstieg)
                if recovered is not None:
                    expected_orders.update(
                        str(x) for x in (recovered.order_ids or []) if str(x))
                    position.exit_order_ids = sorted(expected_orders)
                    position.exit_fill_ids = sorted(set(position.exit_fill_ids) | {
                        str(x) for x in (recovered.fill_ids or []) if str(x)})
                    if (recovered.terminal and recovered.fill_evidence_complete
                            and float(recovered.filled_quantity or 0.0) > 0
                            and recovered.fill_ids):
                        position.exit_state = "FILLED_RECOVERED"
                        self.buch.setze(position)
                        return {
                            "confirmed": True, "inst_id": position.inst_id,
                            "quantity": float(recovered.filled_quantity or 0.0),
                            "avg_price": float(recovered.avg_fill_price or 0.0),
                            "fees_quote": recovered.fees_quote,
                            "fill_ids": list(recovered.fill_ids or []),
                            "order_ids": list(recovered.order_ids or []),
                            "closed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    if recovered.terminal and recovered.fill_evidence_complete and float(
                            recovered.gross_filled_quantity or 0.0) <= 0:
                        expected_orders.difference_update(str(x) for x in (recovered.order_ids or []))
                        position.exit_order_ids = sorted(expected_orders)
                        position.exit_state = "RETRY_WAIT"
                        position.exit_retry_after = (
                            datetime.now(timezone.utc) + timedelta(minutes=max(
                                1.0, float(getattr(
                                    self.cfg, "OKX_EXIT_RETRY_MINUTES", 5.0))))).isoformat()
                        position.exit_last_detail = (
                            "Persistierter OKX-Verkauf terminal ohne Fill bestaetigt")
                        position.exit_attempt_id = ""
                        position.exit_client_order_id = ""
                        if position.manual_exit_command_id and position.manual_exit_previous:
                            import manual_trade_control as controls
                            previous = position.manual_exit_previous
                            position.verwaltung = previous['verwaltung']
                            position.verwaltungsnotiz = previous['verwaltungsnotiz']
                            position.manual_changed_at = previous.get('manual_changed_at', '')
                            position.manual_changed_by = previous.get('manual_changed_by', '')
                            controls.update(position.manual_exit_command_id, 'FAILED',
                                            'Verkauf nach Abgleich nachweislich nicht ausgefuehrt; Verwaltung wiederhergestellt.')
                            position.manual_exit_command_id = ''
                            position.manual_exit_previous = {}
                    else:
                        position.exit_state = "UNCLEAR"
                    self.buch.setze(position)
                    if position.exit_state == "UNCLEAR":
                        return {}
                elif position.exit_state in {"UNCLEAR", "SUBMITTING", "MANUAL_EXIT_PENDING", "ACCOUNTING_PENDING"}:
                    return {}
            if (position.protection_algo_id
                    and hasattr(broker, "protection_exit_order_ids")):
                expected_orders.update(broker.protection_exit_order_ids(
                    position.protection_algo_id))
            # KORREKTUR 9.5.5: Sind keine eigenen Order-IDs bekannt, wird der
            # Verkauf jetzt trotzdem ueber echte tradeId-Fills des Kontos
            # belegt. Vorher gab external_exit_evidence in genau diesem Fall
            # ein leeres Ergebnis zurueck -- ein in der OKX-App ausgeloester
            # Verkauf war damit strukturell nie abrechenbar, und die Position
            # wurde mit "Ergebnis bleibt unbekannt" geschlossen.
            # 10.2.1: Massgeblich ist die LEDGER-Menge des offenen Trades.
            # Eine (kuenftig) auf den Rest reduzierte Buchmenge darf den
            # Beweis fuer den GESAMTEN Einstieg nicht verfehlen (9.0.14-Lehre).
            try:
                import trade_ledger
                rows = trade_ledger.entry_lineage(
                    "okx", position.inst_id, position.order_id,
                    position.account_fingerprint, paper=position.paper)
                offen = [r for r in rows if not r.get("ausgestiegen_am")]
                if len(offen) == 1 and float(offen[0].get("menge") or 0) > 0:
                    expected_qty = max(float(expected_qty), float(offen[0]["menge"]))
            except Exception:
                logger.debug("Ledger-Sollmenge nicht bestimmbar", exc_info=True)
            try:
                evidence = dict(broker.external_exit_evidence(
                    inst_id=position.inst_id, since=position.eroeffnet_am,
                    expected_qty=expected_qty,
                    expected_order_ids=sorted(expected_orders),
                    fremdverkauf_erlauben=True, entry_order_id=position.order_id) or {})
            except TypeError:
                # Aeltere Brokeradapter ohne den neuen Parameter.
                evidence = dict(broker.external_exit_evidence(
                    inst_id=position.inst_id, since=position.eroeffnet_am,
                    expected_qty=expected_qty,
                    expected_order_ids=sorted(expected_orders)) or {})
            if evidence:
                return evidence
            # 10.2.1: Zusammengesetzter Beweis -- mehrere terminale Verkaufs-
            # orders (eigener Schutz-Exit + Nutzerverkaeufe, auch ueber andere
            # Quote-Paare) decken zusammen die Sollmenge (DOGE-Fall 17.09.).
            if hasattr(broker, "composite_exit_evidence"):
                return dict(broker.composite_exit_evidence(
                    inst_id=position.inst_id, since=position.eroeffnet_am,
                    expected_qty=expected_qty,
                    protection_algo_id=position.protection_algo_id,
                    entry_order_id=position.order_id) or {})
            return {}
        except BrokerFehler as exc:
            logger.warning("Externer Verkaufsfill fuer %s nicht abrufbar: %s",
                           position.symbol, exc)
            return {}

    def _position_extern_fremdwaehrung(self, position, evidence, bericht, *, residual):
        """Account for inventory without inventing a historic FX rate."""
        try:
            from okx_external_settlement import record_native_exit
            tid = record_native_exit(entry_order_id=position.order_id,
                entry_instrument=position.inst_id, account=position.account_fingerprint,
                paper=position.paper, evidence=evidence, residual=residual)
            if not self._buche_okx_ergebnis(position,None,None,trade_id=f"ledger:{tid}"):
                raise RuntimeError("Risikobuchung nicht bestaetigt")
        except Exception:
            logger.exception("Fremdwaehrungs-Verkauf %s noch nicht vollstaendig verbucht",position.symbol)
            position.exit_state="ACCOUNTING_PENDING"
            position.exit_last_detail="Nativer Verkaufsbeleg wird abgeglichen; keine neue Verkaufsorder."
            self.buch.setze(position)
            bericht.setdefault("buchung_ausstehend",[]).append(position.symbol)
            return
        self.buch.entferne(position.symbol)
        self._gemeldeter_ueberhang.discard(position.symbol)
        self.topf.setze_offene_positionen(len(self.buch.aktive()))
        bericht.setdefault("abgeglichen",[]).append(position.symbol)
        bericht.setdefault("geschlossen",[]).append(position.symbol)
        self._melde(f"Krypto {position.symbol}: externer Verkauf in {evidence['native_currency']} "
            f"belegt. Netto-Verkaufserloes {evidence.get('net_proceeds') or 'unbekannt'} "
            f"{evidence['native_currency']} ist NICHT der Gewinn. Einstand in "
            f"{self._quote_waehrung(position)}; Ergebnis ohne historischen Wechselkurs unbekannt. "
            f"Rest {residual:g} {position.symbol} bleibt Kontostaub.",wichtig=True)

    def _verbuche_eigenen_schutz_teilfill(self, position, konto: float, bericht) -> bool:
        """10.2.1: Teil-Fill der EIGENEN Schutzorder als Teilverkauf verbuchen.

        Der DOGE-Hergang: die OCO-TP fuellte 1.575 von 7.334, die Position
        fror in ACCOUNTING_PENDING ein und der ungeklaerte Teilabgang sperrte
        die Domaene. Ist der Abgang EXAKT durch tradeId-belegte SELL-Fills der
        eigenen Schutzorder erklaert, wird er jetzt als Teilverkauf ins Ledger
        geschrieben (das Ledger splittet selbst und loest den Bestandsbeleg),
        die Position laeuft mit der Restmenge und aktivem Restschutz weiter.
        Die Bezifferung uebernimmt wie immer der Nachbeleg-Leser (9.8.8) mit
        Original-Belegen; hier wird kein Ergebnis erfunden. Jeder Zweifel
        liefert False und laesst den konservativen Altpfad greifen.
        """
        broker = self.broker
        algo_id = str(getattr(position, "protection_algo_id", "") or "").strip()
        if broker is None or not algo_id or not position.ist_bewiesene_botposition:
            return False
        fehlmenge = float(position.menge) - float(konto)
        if fehlmenge <= 0 or konto <= 0:
            return False
        try:
            ord_ids = sorted(broker.protection_exit_order_ids(algo_id))
            fills = []
            for oid in ord_ids:
                for row in broker.order_fills(position.inst_id, oid):
                    if (str(row.get("side") or "").lower() == "sell"
                            and str(row.get("tradeId") or "").strip()):
                        fills.append(dict(row))
            if not fills:
                return False
            summe = sum(float(row.get("fillSz") or 0.0) for row in fills)
            lot = 0.0
            meta = broker.client.instrument(position.inst_id)
            if meta is not None:
                lot = float(getattr(meta, "lot_size", 0.0) or 0.0)
            if not (0 < summe < float(position.menge)):
                return False
            if abs(summe - fehlmenge) > max(lot * 2, fehlmenge * 1e-6):
                return False
            wert = sum(float(r["fillSz"]) * float(r["fillPx"]) for r in fills)
            vwap = wert / summe
            zeit_ms = max(int(float(r.get("fillTime") or r.get("ts") or 0)) for r in fills)
            from broker.okx import okx_fill_identity
            fill_ids = [okx_fill_identity(r, position.account_fingerprint) for r in fills]
            import trade_ledger
            closed_id = trade_ledger.trade_close(
                broker="okx", symbol=position.symbol.upper(), ausstieg_preis=vwap,
                menge=summe,
                exit_grund="Eigene Broker-Schutzorder teilweise ausgefuehrt (OCO)",
                gebuehr=None, netto_pnl=None, einstieg_preis=position.einstieg,
                eingestiegen_am=position.eroeffnet_am, asset_type="crypto",
                waehrung=self._quote_waehrung(position), paper=bool(position.paper),
                zeit=datetime.fromtimestamp(zeit_ms / 1000, timezone.utc).isoformat(),
                exit_order_id=(ord_ids[0] if ord_ids else algo_id),
                exit_fill_ids=fill_ids, event_id=f"schutz-teilfill:{algo_id}:{zeit_ms}",
                broker_position_id=position.inst_id,
                broker_account_fingerprint=position.account_fingerprint,
                entry_order_id=position.order_id,
                notiz="10.2.1: Teil-Fill der eigenen Schutzorder; Rest laeuft als offene Position weiter.",
                critical=False)
            if not closed_id:
                return False
            if not self._buche_okx_ergebnis(position, None, None,
                                            trade_id=f"ledger:{closed_id}"):
                raise RuntimeError("Risikobuchung nicht bestaetigt")
        except Exception:
            logger.exception("Schutz-Teilfill %s nicht verbuchbar; konservative "
                             "Klaerung bleibt aktiv", position.symbol)
            return False
        alt = float(position.menge)
        position.menge = float(konto)
        position.exit_fill_ids = sorted(set(position.exit_fill_ids) | set(fill_ids))
        position.exit_state = ""
        position.broker_state = ""
        self.buch.setze(position)
        bericht.setdefault("teilverkauft", []).append(position.symbol)
        self._melde(
            f"Krypto {position.symbol}: eigener Schutz-Exit hat {summe:g} von {alt:g} "
            f"verkauft (belegt, VWAP {vwap:g} {self._quote_waehrung(position)}). "
            f"Teilverkauf ist verbucht; {konto:g} laufen mit aktivem Schutz weiter. "
            "Keine Kaufsperre aus diesem Vorgang.", wichtig=True)
        return True

    def _position_extern_zusammengesetzt(self, position, evidence, bericht, *, residual):
        """10.2.1: Mehr-Order-/Mehr-Waehrungs-Abschluss atomar verbuchen."""
        try:
            from okx_external_settlement import record_composite_exit
            tid = record_composite_exit(entry_order_id=position.order_id,
                entry_instrument=position.inst_id, account=position.account_fingerprint,
                paper=position.paper, evidence=evidence, residual=residual)
            if not self._buche_okx_ergebnis(position, None, None, trade_id=f"ledger:{tid}"):
                raise RuntimeError("Risikobuchung nicht bestaetigt")
        except Exception:
            logger.exception("Zusammengesetzter Verkauf %s noch nicht vollstaendig verbucht",
                             position.symbol)
            position.exit_state = "ACCOUNTING_PENDING"
            position.exit_last_detail = ("Zusammengesetzter Verkaufsbeleg wird abgeglichen; "
                                          "keine neue Verkaufsorder.")
            self.buch.setze(position)
            bericht.setdefault("buchung_ausstehend", []).append(position.symbol)
            return
        self.buch.entferne(position.symbol)
        self._gemeldeter_ueberhang.discard(position.symbol)
        self.topf.setze_offene_positionen(len(self.buch.aktive()))
        bericht.setdefault("abgeglichen", []).append(position.symbol)
        bericht.setdefault("geschlossen", []).append(position.symbol)
        legs = evidence.get("legs") or {}
        legs_text = "; ".join(
            f"{leg['net_proceeds']} {ccy}" for ccy, leg in sorted(legs.items()))
        eigen = len(evidence.get("own_order_ids") or [])
        fremd = len(evidence.get("order_ids") or []) - eigen
        self._melde(
            f"Krypto {position.symbol}: Verkauf vollstaendig belegt aus {eigen} eigener "
            f"Schutz- und {fremd} Nutzer-Order(s). Netto-Erloese nativ: {legs_text}. "
            "Das ist NICHT der Gewinn; die EUR-Bewertung folgt automatisch aus "
            "belegten Referenzkursen.", wichtig=True)
        # Bezifferung sofort anstossen (best effort; sonst naechster Takt).
        try:
            import okx_reference_autovaluation
            okx_reference_autovaluation.run_one(self.broker)
        except Exception:
            logger.debug("EUR-Referenzbewertung folgt im naechsten Takt", exc_info=True)

    def _position_extern_geschlossen(self, position: KryptoPosition,
                                     evidence: dict, bericht: dict, *,
                                     residual: float = 0.0,
                                     close_quantity: float = 0.0) -> None:
        """Schliesst nur mit echtem OKX-Fill; Rundungsstaub ist keine Position."""
        symbol = position.symbol.upper()
        if str(evidence.get("beweisart") or "") == "ZUSAMMENGESETZTER_VERKAUF":
            self._position_extern_zusammengesetzt(position, evidence, bericht,
                                                  residual=residual)
            return
        if (evidence.get("native_currency")
                and evidence["native_currency"] != self._quote_waehrung(position)):
            self._position_extern_fremdwaehrung(position, evidence, bericht, residual=residual)
            return
        intended = max(float(position.menge), float(close_quantity or 0.0))
        qty = min(intended, float(evidence.get("quantity") or 0.0))
        price = float(evidence.get("avg_price") or 0.0)
        if qty <= 0 or price <= 0 or not evidence.get("fill_ids"):
            # KORREKTUR 9.5.5: Hier wurde kommentarlos abgebrochen, und der
            # Aufrufer machte danach ein "continue" -- die Position wurde in
            # diesem Takt gar nicht mehr geprueft, ohne dass irgendwo etwas
            # stand. Ein unvollstaendiger Beweis ist ein Befund, kein
            # Schweigegrund.
            logger.warning(
                "Externe Schliessung %s nicht verwertbar: Menge %s, Preis %s, "
                "Fills %s -- Position bleibt unveraendert im Buch.",
                symbol, qty, price, len(evidence.get("fill_ids") or []))
            return
        fee = float(evidence["fees_quote"]) if evidence.get("fees_quote") is not None else None
        entry_fee = max(0.0, float(position.entry_fee_quote or 0.0))
        gross = (price - position.einstieg) * qty
        net = gross - entry_fee - fee if fee is not None else None
        exit_id = (str((evidence.get("order_ids") or [""])[0]) or
                   str((evidence.get("fill_ids") or [""])[0]))
        try:
            import trade_ledger
            closed_id = trade_ledger.trade_close(
                broker="okx", symbol=symbol, ausstieg_preis=price,
                menge=qty,
                exit_grund=("Vom Nutzer selbst bei OKX verkauft" if evidence.get("manual_confirmed")
                            else "Ausserhalb von NEXUS bei OKX verkauft"
                            if str(evidence.get("beweisart") or "") == "FREMDVERKAUF"
                            else "Manuell oder brokerseitig bei OKX verkauft"),
                gebuehr=fee, netto_pnl=None, einstieg_preis=position.einstieg,
                eingestiegen_am=position.eroeffnet_am, asset_type="crypto",
                waehrung=self._quote_waehrung(position), paper=bool(position.paper),
                zeit=evidence.get("closed_at"), exit_order_id=exit_id,
                exit_fill_ids=list(evidence.get("fill_ids") or []),
                event_id=(position.exit_attempt_id or exit_id),
                broker_position_id=position.inst_id,
                broker_account_fingerprint=position.account_fingerprint,
                entry_order_id=position.order_id,
                notiz=(f"OKX-Fill bestaetigt; Rest {residual:g} ist unter Mindestgroesse "
                       "und bleibt als Kontostaub unverwendet."),
                critical=True)
            if not closed_id:
                raise RuntimeError("Ledger hat die bestaetigte Schliessung nicht angenommen")
        except Exception:
            logger.exception("Externe OKX-Schliessung nicht ins Ledger geschrieben")
            return
        try:
            booked = self._buche_okx_ergebnis(
                position, net, gross,
                trade_id=f"ledger:{closed_id}")
            if not booked:
                raise RuntimeError("OKX-Risikobuchung nicht dauerhaft bestaetigt")
        except Exception as exc:
            position.exit_state = "ACCOUNTING_PENDING"
            position.exit_last_detail = (
                f"Externer Brokerfill im Ledger, Risikobuchung ausstehend: "
                f"{type(exc).__name__}")
            self.buch.setze(position)
            logger.exception("Externer OKX-Fill %s noch nicht im Risikotopf",
                             position.symbol)
            bericht.setdefault("buchung_ausstehend", []).append(symbol)
            return
        self.buch.entferne(symbol)
        self._gemeldeter_ueberhang.discard(symbol)
        bericht.setdefault("abgeglichen", []).append(symbol)
        bericht.setdefault("geschlossen", []).append(symbol)
        self.topf.setze_offene_positionen(len(self.buch.aktive()))
        from ledger_result import confirmed_net
        receipt = trade_ledger.trade_detail(closed_id) or {}
        net_text = f"{receipt['netto_pnl']:+.2f}" if confirmed_net(receipt) else "unbekannt (Gebuehren/Einstand offen)"
        self._melde(
            f"Krypto {symbol}: externer OKX-Verkauf anhand echter Fill-IDs bestaetigt. "
            f"{qty:g} zu {price:g} {self._quote_waehrung(position)}, "
            f"Netto {net_text}; Rest {residual:g} ist nicht handelbarer Staub.",
            wichtig=True)

    def _warn_missing_protection(self, position, detail: str) -> None:
        self._melde_einmal(
            f"okx_schutz:{position.symbol}:{position.client_order_id}",
            f"Krypto {position.symbol}: Kauf verbucht, Broker-Schutz nicht bestaetigt. "
            f"{detail} Der Client-Stop braucht einen laufenden Bot und verwertbare Kurse.",
            wichtig=True, klasse="KRITISCH")

    def _melde_einmal(self, schluessel: str, text: str, *, wichtig: bool = False,
                      klasse: str = "") -> bool:
        """Eine unveraenderte Stoerung nur einmal melden -- nicht im Zyklustakt.

        v9.2: Bis 9.1 kam dieselbe kritische Meldung fuer jede betroffene
        Position bei JEDEM Zyklus erneut. Bei drei Positionen und
        Fuenf-Minuten-Takt sind das ueber 800 Nachrichten am Tag; danach liest
        sie niemand mehr -- und genau dann geht die eine wichtige unter.

        Der Schluessel wird aus Symbol, Fehlerklasse und Broker-ID gebildet.
        Aendert sich der Zustand, ist es wieder eine Nachricht. Nach Ablauf des
        Cooldowns ebenfalls, damit eine dauerhafte Stoerung nicht in
        Vergessenheit geraet.
        """
        cooldown = max(60.0, float(getattr(
            self.cfg, "STOERUNGS_MELDUNG_COOLDOWN_SEKUNDEN", 900.0)))
        merker = getattr(self, "_gemeldete_stoerungen", None)
        if merker is None:
            merker = self._gemeldete_stoerungen = {}
        zuletzt = float(merker.get(str(schluessel), 0.0))
        if time.time() - zuletzt < cooldown:
            return False
        merker[str(schluessel)] = time.time()
        self._melde(text, wichtig=wichtig, klasse=klasse)
        return True

    def _entwarnung(self, schluessel: str, text: str) -> None:
        """Genau einmal Entwarnung geben, wenn eine gemeldete Stoerung weg ist."""
        merker = getattr(self, "_gemeldete_stoerungen", None) or {}
        if str(schluessel) in merker:
            merker.pop(str(schluessel), None)
            self._melde(text, wichtig=True)

    def _scan_raster_sekunden(self) -> float:
        """Kerzenraster des aktiven Einstiegsmodus -- 0 heisst: kein Raster.

        Nur der Freqtrade-Modus rastet ein. Der NEXUS-Standard behaelt seinen
        freien Takt: sein Scanintervall haengt nicht an der Kerzengroesse, und
        Georgs Vorgabe ist, dass die anderen Modi unveraendert arbeiten.
        """
        try:
            from crypto_strategy_mode import FREQTRADE_SAMPLE, current_mode
            if current_mode() != FREQTRADE_SAMPLE:
                return 0.0
            from freqtrade_sample_strategy import TIMEFRAME
            return float(_kerzengroesse_sekunden(str(TIMEFRAME)))
        except Exception:
            logger.debug("Kerzenraster nicht bestimmbar", exc_info=True)
            return 0.0

    def _freqtrade_cursor(self):
        from freqtrade_candles import ScanCursor
        from freqtrade_sample_strategy import PARAMETER_HASH
        account = str(getattr(self.broker, "account_fingerprint", lambda: "")() or "")
        env = "DEMO" if bool(getattr(self.broker, "demo", False)) else "LIVE"
        return ScanCursor(f"okx:{env}:{account}:{PARAMETER_HASH}")

    def _zusatz_cursor(self, strategy_mode: str):
        """Tageskerzen-Cursor je Zusatzstrategie (10.2.0).

        Gleiche Semantik wie der Freqtrade-Cursor: eine abgeschlossene
        Tageskerze loest je Symbol hoechstens einen Kaufversuch aus, auch
        ueber Neustarts hinweg.
        """
        from freqtrade_candles import ScanCursor
        import zusatz_strategien
        account = str(getattr(self.broker, "account_fingerprint", lambda: "")() or "")
        env = "DEMO" if bool(getattr(self.broker, "demo", False)) else "LIVE"
        digest = zusatz_strategien.parameter_hash(strategy_mode)
        return ScanCursor(f"okx:{env}:{account}:{digest}")

    def _freqtrade_history(self, instrument, *, cutoff=None, cached_only=False):
        from freqtrade_candles import candle_cutoff, latest_signal_valid, normalize
        cutoff = candle_cutoff() if cutoff is None else cutoff
        fn = getattr(self.broker, "freqtrade_historie", None)
        if callable(fn):
            frame = fn(instrument, cutoff=cutoff, cached_only=cached_only)
        elif cached_only:
            import pandas as pd
            return pd.DataFrame()
        else:
            frame = self.broker.historie(instrument, "3 D", "5 mins", False)
        if not callable(fn):
            frame = normalize(frame, cutoff=cutoff)
        from candle_observation import quality, publish
        client = getattr(self.broker, "client", None)
        instrument_id = str((frame.attrs.get("instrument") if frame is not None else "")
            or getattr(instrument, "inst_id", "")
            or getattr(getattr(instrument, "contract", None), "localSymbol", "")
            or (instrument if isinstance(instrument, str) and "-" in instrument else "UNKNOWN"))
        frame_quality = quality(frame, base_url=getattr(client, "base_url", ""),
            environment="DEMO" if bool(getattr(self.broker, "demo", False)) else "LIVE",
            instrument=instrument_id)
        if frame is not None:
            frame.attrs["candle_quality"] = frame_quality
        publish(frame_quality)
        if not latest_signal_valid(frame):
            raise BrokerFehler("Abgeschlossene 5m-Kerze fehlt oder ist veraltet")
        return frame

    def _strategy_exit(self, position: KryptoPosition, current_price: float) -> tuple[bool, str]:
        """Evaluate the immutable entry strategy of one open position."""
        from crypto_strategy_mode import FREQTRADE_SAMPLE, NEXUS_STANDARD, ZUSATZ_MODES
        mode = str(position.entry_strategy_mode or "")
        if mode == NEXUS_STANDARD:
            # Preserve the existing NEXUS 8.3.1 position behaviour exactly:
            # broker/client stop and take-profit, no new exit rule added here.
            return False, ""
        if mode in ZUSATZ_MODES:
            return self._zusatz_exit(position)
        if mode != FREQTRADE_SAMPLE:
            return False, ""
        from freqtrade_sample_strategy import (
            PARAMETER_HASH, STRATEGY_VERSION, exit_decision,
        )
        if (str(position.strategy_version) != STRATEGY_VERSION
                or str(position.strategy_parameter_hash) != PARAMETER_HASH):
            # v9.2: Erst eine ausdrueckliche Migration versuchen, statt sofort
            # zu pausieren. Ein Update, das nur die Ausstiegsreihenfolge oder
            # den Datenbedarf aendert, darf eine bewiesene Botposition nicht
            # aus der Verwaltung nehmen -- das war der Fehler vom 31.08.2026.
            # Der Eigentumsnachweis bleibt in JEDEM Fall unberuehrt.
            import strategy_migration
            fingerprint = ""
            try:
                fingerprint = str(getattr(self.broker, "account_fingerprint",
                                          lambda: "")() or "")
            except Exception:
                logger.debug("Kontofingerprint nicht lesbar", exc_info=True)
            ergebnis = strategy_migration.migriere(
                position, actor="auto-upgrade", account_fingerprint=fingerprint)
            self.buch.setze(position)
            if ergebnis.get("migriert"):
                self._melde(
                    f"Krypto {position.symbol}: Strategie migriert "
                    f"{ergebnis['von_version']} -> {ergebnis['nach_version']} "
                    f"({ergebnis['grund']}). Eigentumsnachweis und Broker-Schutz "
                    "unveraendert; automatische Verwaltung laeuft weiter.",
                    wichtig=True)
            else:
                # Nicht migrierbar: die Position wird pausiert, bleibt aber
                # eine bewiesene Botposition. Ueberwachung, Broker-Schutz und
                # die Wiedereinstiegssperre laufen weiter.
                position.pausiere(
                    "Strategie-Snapshot nicht reproduzierbar: "
                    + str(ergebnis.get("grund") or "unbekannte Identitaet"),
                    status=ergebnis.get("status") or
                    strategy_migration.STATUS_MIGRATION_NOETIG)
                self.buch.setze(position)
                self._melde_einmal(
                    f"strategie-migration:{position.symbol}:{ergebnis.get('status')}",
                    f"Krypto {position.symbol}: bestaetigte Botposition, aber die "
                    "Einstiegsstrategie ist nicht reproduzierbar. Automatische "
                    "Strategie-Ausgaenge pausiert; Broker-Schutz, Ueberwachung "
                    "und die Wiedereinstiegssperre bleiben aktiv.",
                    wichtig=True, klasse="KRITISCH")
                return False, ""
        broker = self.broker
        if broker is None:
            return False, ""
        try:
            opened = datetime.fromisoformat(str(getattr(position, "entry_order_created_at", "") or position.eroeffnet_am).replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - opened.astimezone(timezone.utc)).total_seconds() / 60.0
            # Prefer the current signal, but never make stop/ROI depend on a
            # successful history request. The 500-bar cache is shared with entries.
            import pandas as pd
            urgent, _, _ = exit_decision(pd.DataFrame(), entry_price=position.einstieg,
                current_price=current_price, elapsed_minutes=elapsed,
                entry_fee_pct=self._entry_fee_pct(position), exit_fee_pct=self._taker_satz())
            try:
                candles = self._freqtrade_history(self._instrument(position.symbol, position.inst_id), cached_only=urgent)
            except Exception:
                # Corrupt/unavailable history also cannot suppress price-based exits.
                candles = pd.DataFrame()
            close, reason, audit = exit_decision(
                candles, entry_price=position.einstieg,
                current_price=current_price, elapsed_minutes=elapsed,
                entry_fee_pct=self._entry_fee_pct(position),
                exit_fee_pct=self._taker_satz())
            if close:
                logger.info("Freqtrade-Ausgang %s: %s | %s", position.symbol, reason, audit)
            return bool(close), str(reason or "")
        except (BrokerFehler, ValueError) as exc:
            logger.warning("Freqtrade-Ausstieg fuer %s nicht pruefbar: %s", position.symbol, exc)
            return False, ""

    def _zusatz_exit(self, position: KryptoPosition) -> tuple[bool, str]:
        """Tageskerzen-Ausstieg einer Zusatzstrategie-Position (10.2.0).

        Broker-OCO-Schutz und alle Sicherheitsausgaenge laufen unabhaengig
        davon weiter; hier kommt nur der regulaere Strategie-Ausstieg dazu.
        Nicht pruefbare Daten unterdruecken niemals den Schutz -- sie fuehren
        schlicht zu "kein Strategie-Exit in diesem Zyklus".
        """
        import zusatz_strategien
        mode = str(position.entry_strategy_mode or "")
        try:
            snap = zusatz_strategien.parameter_snapshot(mode)
        except ValueError:
            return False, ""
        if (str(position.strategy_version) != snap["strategy_version"]
                or str(position.strategy_parameter_hash) != snap["parameter_hash"]):
            # V1 der Zusatzstrategien hat keine Vorgaengerversion, aus der
            # migriert werden koennte. Eine abweichende Identitaet pausiert
            # nur den Strategie-Ausstieg; Eigentum, Broker-Schutz und
            # Ueberwachung bleiben unangetastet (gleiche Politik wie beim
            # nicht migrierbaren Freqtrade-Snapshot).
            position.pausiere(
                "Strategie-Snapshot nicht reproduzierbar: Zusatzstrategie "
                f"{mode} erwartet {snap['strategy_version']}/{snap['parameter_hash'][:12]}")
            self.buch.setze(position)
            self._melde_einmal(
                f"strategie-migration:{position.symbol}:{mode}",
                f"Krypto {position.symbol}: bestaetigte Botposition, aber der "
                f"Zusatzstrategie-Snapshot ({mode}) ist nicht reproduzierbar. "
                "Automatische Strategie-Ausgaenge pausiert; Broker-Schutz, "
                "Ueberwachung und die Wiedereinstiegssperre bleiben aktiv.",
                wichtig=True, klasse="KRITISCH")
            return False, ""
        try:
            spec = zusatz_strategien.STRATEGIEN[mode]
            instrument = self._instrument(position.symbol, position.inst_id)
            df = self.broker.historie(instrument, str(spec["history_duration"]),
                                      "1 day", False)
            bewertung = zusatz_strategien.bewerte(mode, df)
        except (BrokerFehler, ValueError) as exc:
            logger.warning("Zusatzstrategie-Ausstieg fuer %s nicht pruefbar: %s",
                           position.symbol, exc)
            return False, ""
        if bewertung.exit:
            logger.info("Zusatzstrategie-Ausgang %s: %s | %s",
                        position.symbol, bewertung.exit_reason, bewertung.indicators)
            return True, bewertung.exit_reason
        return False, ""

    def _sperrliste(self) -> dict:
        """10.7.0: Jede aktive Kaufsperre mit Grund, Reichweite, Ablauf, Aufloesung."""
        try:
            from handelsfreigabe import okx_sperren, zusammenfassung
            # 10.7.1: "missing" sind nur die vom Waechter gemessenen Fehlbestaende;
            # Coins mit offenem Buchungsbeleg stehen ueber die Buchhaltung in der
            # Liste (bis 10.7.0 erschienen sie hier zusaetzlich als
            # BESTAND_FEHLT_UNBESTAETIGT, obwohl der Bestand nicht fehlte).
            guard = {"valid": not getattr(self.topf, "_snapshot_error", ""),
                     "detail": getattr(self.topf, "_snapshot_error", ""),
                     "missing": sorted(set(getattr(self, "_fehlende_symbole", set()) or set())
                                       - set(getattr(self, "_exit_in_progress_symbole", set()))),
                     "exit_in_progress": [{"symbol": s} for s in sorted(
                         getattr(self, "_exit_in_progress_symbole", set()))]}
            return zusammenfassung(okx_sperren(self.topf, self.broker, guard=guard))
        except Exception as exc:
            logger.debug("OKX-Sperrliste nicht erstellbar: %s", exc, exc_info=True)
            return {"domaene_gesperrt": None, "gesperrte_symbole": [], "anzahl": 0, "sperren": [],
                    "fehler": type(exc).__name__}

    def _bestaetigungsfrist(self, name: str, standard: float) -> float:
        try:
            return max(0.0, float(getattr(self.cfg, name, standard)))
        except (TypeError, ValueError):
            return float(standard)

    def _messungen_bestaetigt(self, anzahl: int, seit_iso: str,
                              frist_sekunden: float) -> bool:
        """Zwei Messungen in Folge UND Mindestalter der ersten Messung.

        Zwei Zyklen allein genuegen nicht: Am 18.09.2026 lagen sie neun
        Sekunden auseinander, weil der Zyklus nach einem Fehler sofort
        wiederholte. Zwei schnell aufeinanderfolgende Fehlmessungen sind
        dieselbe Stoerung, nicht zwei unabhaengige Beobachtungen.
        """
        if int(anzahl or 0) < 2:
            return False
        if frist_sekunden <= 0:
            return True
        try:
            seit = datetime.fromisoformat(str(seit_iso))
        except (TypeError, ValueError):
            # Ohne belastbaren Zeitstempel wird weiter beobachtet, nicht
            # gebucht. Eine fehlende Messung ist kein Beweis.
            return False
        if seit.tzinfo is None:
            seit = seit.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - seit).total_seconds() >= frist_sekunden

    def _fehlbestand_bestaetigt(self, position: KryptoPosition) -> bool:
        return self._messungen_bestaetigt(
            position.consecutive_missing_snapshots, position.first_missing_at,
            self._bestaetigungsfrist("OKX_POSITION_MISSING_CONFIRM_SECONDS", 30.0))

    def _bestand_zurueckgewonnen(self, position: KryptoPosition, bestand,
                                 bericht: dict) -> bool:
        """Nimmt BROKER_STATE_UNKNOWN zurueck, wenn der Bestand belegbar da ist.

        Voraussetzungen, alle gleichzeitig:
          * ein Guthaben-Schnappschuss dieser Runde liegt ueberhaupt vor,
          * der gemessene Bestand erreicht wieder die gebuchte Menge,
          * es gibt keinen Verkaufsbeleg (der Aufrufer hat das geprueft),
          * zwei Messungen in Folge mit Mindestabstand bestaetigen das.

        Erst danach werden Ledger und Position entsperrt -- in dieser
        Reihenfolge. Schlaegt die Ledgerbuchung fehl, bleibt die Position
        gesperrt; eine handelbare Position mit blockiertem Ledger waere der
        schlechteste aller Zustaende.
        """
        symbol = position.symbol.upper()
        if not self._guthaben_dieser_runde:
            # Ohne belastbaren Schnappschuss wird nichts zurueckgenommen.
            return False
        gemessen = float(getattr(bestand, "quantity", 0.0) or 0.0)
        if not _ist_positiv(gemessen) or gemessen < position.menge * (1 - MENGEN_TOLERANZ):
            return False
        position.consecutive_present_snapshots = int(
            position.consecutive_present_snapshots or 0) + 1
        if not position.first_present_at:
            position.first_present_at = datetime.now(timezone.utc).isoformat()
        self.buch.setze(position)
        if not self._messungen_bestaetigt(
                position.consecutive_present_snapshots, position.first_present_at,
                self._bestaetigungsfrist("OKX_POSITION_RESTORED_CONFIRM_SECONDS", 120.0)):
            bericht.setdefault("bestand_wieder_da_unbestaetigt", []).append(symbol)
            return False
        if not self._bestandsluecke_aufloesen(position, gemessen):
            bericht.setdefault("bestand_wieder_da_ledger_offen", []).append(symbol)
            return False
        position.broker_state = ""
        position.consecutive_present_snapshots = 0
        position.first_present_at = ""
        position.consecutive_missing_snapshots = 0
        position.first_missing_at = ""
        # Der Schutz wird NICHT aus dem alten Status uebernommen, sondern im
        # selben Takt beim Broker neu bestaetigt (siehe Schutzabgleich weiter
        # unten). Geglaubter Schutz ist kein Schutz.
        position.broker_schutz = False
        position.protection_status = "PENDING"
        self.buch.setze(position)
        bericht.setdefault("bestand_wieder_da", []).append(symbol)
        self._melde(
            f"Krypto {symbol}: Bestand ist in zwei bestaetigten Messungen wieder "
            f"vollstaendig da ({gemessen:g} statt gebucht {position.menge:g}) und es "
            "gibt keinen Verkaufsbeleg. Die Bestandsluecke ist aufgeloest, die "
            "Position wieder freigegeben; der Broker-Schutz wird sofort erneut "
            "bestaetigt.", wichtig=True)
        return True

    def _bestandsluecke_aufloesen(self, position: KryptoPosition,
                                  gemessen: float) -> bool:
        """Ledgerseite der Ruecknahme: Luecke schliessen, Trade wieder offen."""
        import trade_ledger
        from okx_accounting import resolve_balance_gap_restored
        try:
            rows = trade_ledger.entry_lineage(
                "okx", position.inst_id, position.order_id,
                position.account_fingerprint, paper=position.paper)
            opened = [r for r in rows if not r.get("ausgestiegen_am")]
            if not opened:
                # Keine offene Ledgerzeile heisst: Es gibt hier auch keine
                # Bestandsluecke aufzuloesen. Die Position selbst darf
                # trotzdem zurueck -- sie liegt belegbar im Konto und soll
                # wieder geschuetzt und verkauft werden koennen. Ein etwaiger
                # Buchungsvorfall ohne Ledgeranker bleibt davon unberuehrt
                # und sperrt neue Kaeufe weiter.
                logger.info("Krypto %s: Bestandsrueckkehr ohne offene "
                            "Ledgerzeile; nur die Position wird freigegeben.",
                            position.symbol)
                return True
            if len(opened) != 1:
                logger.warning(
                    "Krypto %s: Bestandsrueckkehr nicht eindeutig zuzuordnen "
                    "(%s offene Ledgerzeilen).", position.symbol, len(opened))
                return False
            row = opened[0]
            resolve_balance_gap_restored(
                row, gemessen,
                detail=f"snapshots={position.consecutive_present_snapshots}")
            trade_ledger.set_reconciliation_status(
                row["trade_id"], "CONFIRMED_OPEN",
                notiz="Bestand in zwei bestaetigten Messungen wieder vollstaendig; "
                      "kein Verkaufsbeleg vorhanden")
            trade_ledger.set_protection(
                row["trade_id"], algo_id=position.protection_algo_id,
                client_order_id=position.protection_client_order_id,
                status="PENDING",
                detail="Schutz nach Bestandsrueckkehr erneut zu bestaetigen")
            return True
        except Exception as exc:
            logger.warning("Krypto %s: Bestandsluecke nicht aufloesbar: %s",
                           position.symbol, exc, exc_info=True)
            return False

    def _position_verschwunden(self, position: KryptoPosition, bericht: dict,
                               *, staubrest: float = 0.0, evidence=None) -> None:
        """Kein Guthaben mehr: die Position wurde ausserhalb des Bots geschlossen.

        Bis 8.1.3 wurde sie hier nur still aus dem Buch genommen. Damit fehlte
        der Trade im Ledger und im Risikotopf, und der wahrscheinlichste Grund
        -- die brokerseitige Schutzorder hat ausgeloest -- stand nirgends.
        """
        symbol = position.symbol.upper()
        # 10.6.0: Der Aufrufer hat den Beleg oft schon geholt. Ihn erneut zu
        # erfragen kostet einen Broker-Aufruf und kann bei wechselnder
        # Antwort zwei verschiedene Wahrheiten in einem Takt erzeugen.
        if evidence is None:
            evidence = self._externer_verkaufsbeweis(position, position.menge)
        if not evidence and position.exit_state in {"UNCLEAR", "SUBMITTING", "MANUAL_EXIT_PENDING", "ACCOUNTING_PENDING"}:
            self._mark_broker_unknown(position, float(staubrest or 0.0))
            bericht.setdefault("buchung_ausstehend", []).append(symbol)
            return  # Missing balance is not permission to forget an unresolved SELL.
        if evidence and position.ist_bewiesene_botposition:
            # 9.5.4: Der Staubrest wird als Restmenge durchgereicht statt als
            # 0 -- sonst behauptet die Abrechnung, das Konto sei leer, obwohl
            # dort noch ein (unverkaeuflicher) Rest liegt.
            self._position_extern_geschlossen(position, evidence, bericht,
                                              residual=float(staubrest or 0.0))
            return
        if not position.ist_bewiesene_botposition:
            bericht.setdefault("buchung_ausstehend", []).append(symbol)
            return
        self._mark_broker_unknown(position, float(staubrest or 0.0))
        bericht.setdefault("broker_state_unknown", []).append(symbol)

    def _mark_broker_unknown(self, position, observed):
        """Preserve ownership, quantity, stops and execution intents until proven."""
        import trade_ledger
        from okx_accounting import mark_balance_gap, mark_unanchored
        from okx_receipt_math import EvidenceError
        first_observation = not position.broker_state
        if position.protection_status != "UNKNOWN_BROKER_STATE":
            position.last_confirmed_protection = {
                "status": position.protection_status,
                "broker_schutz": position.broker_schutz,
                "algo_id": position.protection_algo_id,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "historical_only": True,
            }
        position.broker_state = "BROKER_STATE_UNKNOWN"
        position.protection_status = "UNKNOWN_BROKER_STATE"
        self.buch.setze(position)
        if first_observation:
            rest = "nur noch ein nicht handelbarer Rest oder Teilbestand" if observed is not None and observed > 0 else "kein verlaesslicher Bestand"
            self._melde(f"Krypto {position.symbol}: {rest}. Position bleibt vorerst im Buch und wird erneut geprueft; ohne Verkaufsbeleg kein Abschluss.", wichtig=True)
        self.bereitschaft.melde("buchung_vollstaendig", False,
            "OKX-Bestand ungeklärt: kein Verkaufsbeleg. Position und Risikobasis bleiben erhalten.")
        try:
            rows = trade_ledger.entry_lineage("okx", position.inst_id,
                position.order_id, position.account_fingerprint, paper=position.paper)
            opened = [r for r in rows if not r.get("ausgestiegen_am")]
            if len(opened) == 1 and observed is not None:
                try:
                    mark_balance_gap(opened[0], observed, position.menge)
                except EvidenceError as exc:
                    # 10.6.0: Eine Ledgerzeile ohne vollstaendige Kontokette
                    # (z. B. ohne Kontofingerabdruck) liess diesen Aufruf
                    # werfen -- und die Exception riss den GESAMTEN
                    # Positionszyklus mit: Schutzabgleich, Ausstiege und
                    # Mengenpruefung aller anderen Coins fielen aus. Fuer
                    # genau diesen Fall gibt es die Quarantaene. Sie haelt
                    # den Vorgang dauerhaft fest und sperrt neue Kaeufe
                    # weiter; verschluckt wird nichts.
                    logger.warning(
                        "Krypto %s: Bestandsluecke ohne exakte Kontokette (%s) "
                        "-- wird als Buchungsvorfall quarantaeniert.",
                        position.symbol, exc)
                    mark_unanchored(position, observed)
                    return
                trade_ledger.set_reconciliation_status(opened[0]["trade_id"],
                    "BROKER_STATE_UNKNOWN", notiz="Bestand fehlt ohne Verkaufsbeleg; Ledger bleibt offen")
                trade_ledger.set_protection(opened[0]["trade_id"],
                    algo_id=position.protection_algo_id,
                    client_order_id=position.protection_client_order_id,
                    status="UNKNOWN_BROKER_STATE")
            else:
                mark_unanchored(position, observed)
        except Exception:
            self._accounting_write_fault = True
            raise

    def _menge_an_orderbuch_anpassen(self, symbol: str, mitglied, menge: float,
                                     quote_ccy: str) -> tuple[float, str]:
        """Verkleinert eine Order, die das Orderbuch sprengen wuerde.

        Geprueft wird, wieviel sich kaufen laesst, ohne den besten Briefkurs
        um mehr als ``CRYPTO_MAX_BOOK_IMPACT_PCT`` zu verlassen. Ist das
        Orderbuch nicht abrufbar, bleibt die Menge unveraendert -- eine
        fehlende Messung darf keinen Kauf erfinden und keinen verhindern.
        """
        broker = self.broker
        if broker is None or menge <= 0:
            return menge, ""
        grenze = float(getattr(self.cfg, "CRYPTO_MAX_BOOK_IMPACT_PCT", 0.005))
        anteil = float(getattr(self.cfg, "CRYPTO_MAX_BOOK_SHARE", 0.25))
        if grenze <= 0:
            return menge, ""

        inst_id = str(getattr(mitglied, "inst_id", "") or "").upper() or f"{symbol}-{quote_ccy}"
        try:
            buch = broker.client.orderbook(inst_id, depth=20)
        except Exception:
            logger.debug("Orderbuch %s nicht abrufbar", inst_id, exc_info=True)
            return menge, ""

        asks = [(float(p), float(q)) for p, q in (buch.get("asks") or []) if p > 0 and q > 0]
        if not asks:
            return menge, ""

        bester = asks[0][0]
        tragfaehig = 0.0
        for preis, groesse in asks:
            if (preis - bester) / bester > grenze:
                break
            tragfaehig += groesse
        # Nur einen Teil der sichtbaren Tiefe beanspruchen.
        tragfaehig *= max(0.0, min(1.0, anteil))
        if tragfaehig <= 0:
            return 0.0, (f"Orderbuch bietet innerhalb von {grenze * 100:.2f} % "
                         f"keine nutzbare Tiefe")
        if menge <= tragfaehig:
            return menge, ""

        neue_menge = self._auf_lot_runden(symbol, tragfaehig, inst_id=inst_id)
        if neue_menge <= 0:
            return 0.0, (f"Orderbuch traegt nur {tragfaehig:g} {symbol} -- "
                         f"unter der Mindestgroesse")
        mindestwert = float(getattr(self.cfg, "OKX_MIN_POSITION_VALUE", 15.0))
        if neue_menge * bester < mindestwert:
            return 0.0, (f"Orderbuch traegt nur {neue_menge * bester:.2f} {quote_ccy} "
                         f"innerhalb von {grenze * 100:.2f} %; Mindestordergroesse "
                         f"{mindestwert:.2f} {quote_ccy}")
        return neue_menge, (
            f"Order von {menge:g} auf {neue_menge:g} {symbol} wegen Orderbuchtiefe "
            f"reduziert (innerhalb von {grenze * 100:.2f} % ab {bester:g} "
            f"liegen {tragfaehig:g})")

    def _stop_mindestabstand(self, preis: float, stop: float, spanne: float,
                             quote_ccy: str) -> tuple[float, str]:
        """Hebt einen zu engen Stop auf den Kostenabstand an.

        Rueckgabe: (Stop, Hinweis). Der Hinweis ist leer, wenn der ATR-Stop
        ohnehin weit genug sitzt.
        """
        if preis <= 0 or stop <= 0 or stop >= preis:
            return stop, ""
        faktor = float(getattr(self.cfg, "CRYPTO_STOP_KOSTEN_FAKTOR", 1.5))
        if faktor <= 0:
            return stop, ""
        gebuehr = self._taker_satz()
        # Roundtrip: Gebuehr auf beiden Seiten plus einmal die Spanne.
        kosten_pct = 2.0 * gebuehr + max(0.0, float(spanne or 0.0))
        mindest_pct = kosten_pct * faktor
        ist_pct = (preis - stop) / preis
        if ist_pct >= mindest_pct:
            return stop, ""
        neuer_stop = preis * (1.0 - mindest_pct)
        return neuer_stop, (
            f"Stop von {ist_pct * 100:.3f} % auf {mindest_pct * 100:.3f} % "
            f"erweitert -- enger als die Handelskosten "
            f"({kosten_pct * 100:.3f} % Roundtrip) waere ein Stop-Out ein "
            f"rechnerisch sicherer Verlust")

    def _taker_satz(self) -> float:
        """Der geltende Taker-Satz -- gemessen, wenn der Broker ihn kennt."""
        broker = self.broker
        if broker is not None and hasattr(broker, "gebuehrensatz"):
            try:
                satz = float(broker.gebuehrensatz())
                if satz > 0:
                    return satz
            except Exception:
                logger.debug("Gebuehrensatz nicht abrufbar", exc_info=True)
        return float(getattr(self.cfg, "OKX_TAKER_FEE_PCT", 0.0035))

    def _second_opinion(self, symbol, protokoll, *, preis, menge, stop, ziel,
                        waehrung, signal, kosten, erwartete_bewegung,
                        mitglied) -> bool:
        """Zweite Meinung; bei kritisch kann ausschliesslich Georg freigeben."""
        import second_opinion as so

        live = bool(self.broker is not None and not self.broker.ist_paper())
        if not so.aktiv(live=live):
            return True

        fakten = {
            "symbol": symbol, "broker": "okx",
            "modus": "LIVE" if live else "DEMO",
            "preis": round(float(preis), 8),
            "menge": round(float(menge), 8),
            "wert": round(float(menge) * float(preis), 2),
            "waehrung": waehrung,
            "stop": round(float(stop), 8), "ziel": round(float(ziel), 8),
            "chance_risiko": round((ziel - preis) / (preis - stop), 2)
                             if preis > stop else None,
            "technik": [b.detail for b in protokoll.beitraege
                        if getattr(b, "quelle", "") == TECHNIK][:4],
            "kosten": {
                "huerde_pct": round(float(kosten.required_edge_pct) * 100, 3),
                "erwartete_bewegung_pct": round(float(erwartete_bewegung) * 100, 3),
                "gebuehr_pct": round(self._taker_satz() * 100, 4),
            },
            "universum": {"rang": getattr(mitglied, "letzter_rang", None),
                          "score": round(float(getattr(mitglied, "letzter_score", 0.0)), 3)},
            "portfolio": {"offene_positionen": len(self.buch.aktive())},
            "decision_id": protokoll.decision_id,
        }

        # Eine vorhandene Freigabe wird ohne weitere KI-Kosten geprueft. Sie
        # gilt nur fuer dieselbe, praktisch unveraenderte Ordergrundlage.
        pending = next((e for e in so.offene("okx")
                        if str(e.get("symbol", "")).upper() == str(symbol).upper()), None)
        if pending:
            if not pending.get("freigegeben"):
                protokoll.blockiert(NUTZER, "kritische KI-Warnung wartet auf persoenliche Freigabe")
                return False
            cash = self.broker.verfuegbares_cash(waehrung)
            gueltig, warum = so.grundlage_noch_gueltig(
                pending, preis=preis, cash=float(cash or 0.0),
                bestand_vorhanden=self.buch.hole(symbol) is not None,
                risiko_offen=self.risiko.darf_kaufen("crypto")[0],
                menge=menge, stop=stop)
            so.verwerfe("okx", symbol)
            if gueltig:
                protokoll.ergaenze(NUTZER, DAFUER, gewicht=0.0,
                                   detail="persoenlich per Telegram freigegeben; feste Regeln erneut bestanden")
                return True
            self._melde(f"🛡️ {symbol}: frühere Freigabe verworfen – {warum}. "
                        "Eine neue Entscheidung braucht eine neue Freigabe.", wichtig=True)

        urteil = so.hole(self.ai, fakten)
        if not urteil.gefragt:
            return True

        # Der KI-Beitrag steht IMMER mit Gewicht 0,0 und NEUTRAL im Protokoll.
        protokoll.ki("terra", NEUTRAL, urteil.kurztext or "keine Einschaetzung",
                     modell=urteil.modell, gewicht=0.0,
                     kosten_usd=urteil.kosten_usd)

        if urteil.einschaetzung == so.NICHT_ERREICHBAR:
            self._melde(f"KI nicht erreichbar vor dem Kauf von {symbol} "
                        f"({urteil.begruendung}). Der Kauf laeuft nach den festen "
                        f"Regeln weiter.", wichtig=False)
            return True

        if urteil.kritisch:
            if so.human_gate_aktiv():
                eintrag = so.stelle_zurueck(broker="okx", symbol=symbol,
                                            urteil=urteil, fakten=fakten)
                self._melde(so.rueckfragetext(eintrag), wichtig=True, klasse="KRITISCH")
                protokoll.blockiert(NUTZER, "kritische KI-Warnung: persoenliche Freigabe erforderlich")
                return False
            self._melde(so.warntext(urteil, fakten), wichtig=True, klasse="KRITISCH")
            protokoll.ergaenze(RISK_GATE, NEUTRAL, gewicht=0.0,
                               detail="KI-Warnung kritisch; Human-Gate deaktiviert",
                               datenquelle="Second Opinion")
        return True

    def _account_action(self):
        from okx_account_action import status, restore_observed_rejection
        broker = self.broker
        if broker is None:
            return {}
        identity = getattr(broker, 'account_fingerprint', None)
        if not callable(identity):
            return {}
        account = identity()
        if not isinstance(account, str) or not account:
            return {}
        environment = 'DEMO' if broker.demo else 'LIVE'
        try:
            restore_observed_rejection(account, environment)
        except (OSError, ValueError):
            return {'blocked': True, 'required': True, 'state': 'UNREADABLE',
                    'detail': 'OKX-Kontohinweis nicht lesbar; neue Käufe pausieren'}
        return status(account, environment)

    def _melde_account_action(self):
        state = self._account_action()
        self.bereitschaft.melde("kontobestaetigung", not state.get("blocked", False), state.get("detail", ""))

    def _melde_bereitschaft(self) -> None:
        """Die uebrigen Bedingungen der Anlaufsperre pruefen.

        Bewusst ohne zusaetzliche Netzabfragen: geprueft wird, was der Zyklus
        ohnehin schon weiss.
        """
        broker = self.broker
        try:
            self.bereitschaft.melde("systemzeit", True)
            self.bereitschaft.melde("protokoll", True)
            if broker is None:
                return
            self._melde_account_action()
            # Instrumentkatalog und Handelsregeln
            try:
                instrumente = broker.client.instruments()
                self.bereitschaft.melde("instrumente", bool(instrumente),
                                        f"{len(instrumente)} Instrumente")
                beispiel = next(iter(instrumente.values()), None) if instrumente else None
                self.bereitschaft.melde(
                    "handelsregeln",
                    bool(beispiel and beispiel.lot_size and beispiel.min_size))
            except Exception as exc:
                self.bereitschaft.melde("instrumente", False, str(exc)[:120])
                self.bereitschaft.melde("handelsregeln", False, str(exc)[:120])
            # Gebuehrensatz
            satz = self._taker_satz()
            self.bereitschaft.melde("gebuehren", satz > 0, f"{satz * 100:.4f} %")
            # Kursdaten: ein frischer Ticker, nicht bloss "ein Zyklus lief".
            frisch = False
            alter = None
            try:
                tickers = broker.client.tickers()
                stempel = max((t.timestamp_ms for t in tickers.values()
                               if getattr(t, "timestamp_ms", 0)), default=0)
                if stempel:
                    alter = max(0.0, time.time() - stempel / 1000.0)
                    frisch = alter <= _tickeralter_grenze(self.cfg)
            except Exception:
                logger.debug("Tickeralter nicht bestimmbar", exc_info=True)
            self.bereitschaft.melde("kursdaten", frisch,
                                    f"letzter Ticker vor {alter:.0f} s" if alter is not None
                                    else "Alter unbekannt")

            # Signalkerze: echte Messung gegen die Kerzengroesse, nicht der
            # Anlauftimer in anderen Worten.
            from crypto_strategy_mode import signal_timeframe
            timeframe = signal_timeframe(standard_timeframe=getattr(self.cfg, "CRYPTO_BAR_SIZE", "15 mins"))
            kerze = _kerzengroesse_sekunden(timeframe) if timeframe else 0
            self.bereitschaft.melde(
                "signalkerze", self.bereitschaft.kerzen_vollstaendig(kerze),
                f"Aktive Signalkerze {kerze / 60:.0f} min; Sicherheits-Anlaufsperre separat"
                if timeframe else "Keine aktive Einstiegsstrategie")
            # Offene, unklare Orders -- ausschliesslich eigene OKX-Orders
            # dieses Kontos und dieser Umgebung (9.5.2).
            # 9.5.8: Eine ungeklaerte Order sperrt nur noch IHREN Wert.
            #
            # Bis 9.5.7 faerbte jeder Eintrag diese Bedingung rot, und
            # ``darf_kaufen()`` blockierte damit ALLE Kryptokandidaten. Ein
            # einziger haengender APT-Eintrag legte ab dem 04.09.2026 den
            # gesamten Kryptoeinstieg still -- ohne dass es dafuer einen Grund
            # gab: der Schutz vor einem zweiten Kauf DESSELBEN Werts sitzt
            # bereits im Kandidatengate (``duplicate_open_order``, symbolgenau).
            #
            # Global gesperrt bleibt nur der Fall, in dem gar keine Aussage
            # moeglich ist: ein unlesbares Register. Dann greift das
            # symbolgenaue Gate naemlich auch nicht mehr.
            offen = self._offene_order_symbole()
            unlesbar = [s for s in offen if s.startswith("<")]
            benannt = [s for s in offen if not s.startswith("<")]
            if unlesbar:
                detail = "Orderregister nicht lesbar -- alle Einstiege gesperrt"
            elif benannt:
                detail = ("Ungeklaerte eigene OKX-Order (nur diese Werte "
                          "gesperrt): " + ", ".join(benannt[:5]))
            else:
                detail = "Keine ungeklaerte eigene OKX-Order"
            self.bereitschaft.melde("keine_unklare_order", not unlesbar, detail)
            # Accounting is independent of connection health and JSON counters.
            from okx_accounting import broker_status
            accounting = broker_status(broker)
            self.bereitschaft.melde("buchung_vollstaendig", accounting['complete'] and not (getattr(self,'_accounting_write_fault',False) or getattr(self,'_critical_persistence_fault',False)), accounting['detail'] if not (getattr(self,'_accounting_write_fault',False) or getattr(self,'_critical_persistence_fault',False)) else 'OKX-Buchungsschreibfehler; Abgleich erforderlich')
        except Exception:
            logger.debug("Bereitschaftspruefung unvollstaendig", exc_info=True)

    def _guthaben_uebersicht(self) -> dict:
        """Guthaben je Waehrung -- ohne zusaetzliche Netzabfrage zu erzwingen."""
        broker = self.broker
        if broker is None:
            return {}
        try:
            roh = broker.client.balances()
        except Exception:
            logger.debug("Guthaben nicht abrufbar", exc_info=True)
            return {}
        out = {}
        for waehrung, eintrag in (roh or {}).items():
            gesamt = float(eintrag.get("gesamt", 0.0) or 0.0)
            if gesamt > 0:
                out[str(waehrung).upper()] = {
                    "gesamt": round(gesamt, 8),
                    "frei": round(float(eintrag.get("cash", 0.0) or 0.0), 8),
                }
        return out

    def _eigene_positionen(self, broker_name: str):
        """Eigene Positionswerte mit expliziter Preiswaehrung.

        Fremdbestaende und pausierte Positionen zaehlen nicht zur Risikobasis.
        Der Preis einer OKX-Position steht in der Markt-Quotewaehrung des
        gespeicherten ``inst_id``. Diese Waehrung wird mitgegeben, damit der
        Risikotopf EUR, USDC oder USD niemals roh addiert.
        """
        if str(broker_name).lower() != "okx":
            return ()
        out = []
        for position in self.buch.alle():
            if not position.ist_bewiesene_botposition:
                continue
            if position.account_fingerprint != self._konto_fingerprint():
                continue
            # 10.1.10: Bewertung zum letzten beobachteten Marktkurs. Der
            # Hoechstkurs ist eine monoton steigende Trailing-Marke und
            # ueberzeichnete das Risikokapital systematisch (Equity-Bremse und
            # Positionsgroesse sahen fallende Kurse nicht). Ohne frischen Kurs
            # gilt der belegte Einstiegspreis, nie der Hoechststand.
            preis = self._letzter_kurs(position) or position.einstieg
            if position.menge <= 0 or preis <= 0:
                continue
            parts = str(position.inst_id or "").upper().replace('/', '-').split('-')
            market_quote = parts[-1] if len(parts) >= 2 and parts[-1] else ""
            # Ein gespeicherter settlement-Wert ist nur ein Fallback fuer
            # alte eindeutige Instrumente. Fehlt beides, blockiert die
            # Kapitalbewertung spaeter statt eine Waehrung zu erfinden.
            if not market_quote:
                market_quote = str(position.trade_quote_ccy or "").upper()
            out.append({
                "symbol": position.symbol.upper(),
                "menge": position.menge,
                "preis": preis,
                "market_quote_ccy": market_quote,
            })
        return tuple(out)

    def _rest_lohnt_sich(self, position: KryptoPosition, rest: float, kurs: float) -> bool:
        """Ist eine Restmenge gross genug, um weiter gefuehrt zu werden?

        Zu kleine Reste kann OKX gar nicht mehr verkaufen (minSz) -- die
        weiter zu fuehren wuerde bei jedem Zyklus eine fehlschlagende Order
        erzeugen.
        """
        if rest <= 0 or kurs <= 0:
            return False
        gerundet = self._auf_lot_runden(position.symbol, rest)
        if gerundet <= 0:
            return False
        # v9.1: Massstab ist, ob OKX den Rest UEBERHAUPT VERKAUFEN KANN --
        # nicht, ob er fuer eine NEUE Position gross genug waere.
        # OKX_MIN_POSITION_VALUE (15,00) ist die Eroeffnungsschwelle. Damit
        # galt ein Rest von 11,55 USDC als "nicht handelbarer Staub", obwohl er
        # das Hundertfache der OKX-Mindestgroesse war -- und verlor Client-Stop,
        # Broker-Schutz und Ledgerzeile.
        try:
            meta = self.broker.client.instrument(position.inst_id)
            min_size = float(getattr(meta, "min_size", 0.0) or 0.0)
        except Exception:
            logger.debug("Mindestgroesse fuer %s nicht lesbar", position.symbol,
                         exc_info=True)
            min_size = 0.0
        if min_size > 0 and gerundet < min_size:
            return False
        # Zusaetzlich eine echte Staubgrenze: unter diesem Gegenwert deckt der
        # Verkauf nicht einmal die Gebuehren.
        staubwert = float(getattr(self.cfg, "OKX_DUST_VALUE_LIMIT", 1.0))
        return gerundet * kurs >= staubwert

    def _kontorest_ist_staub(self, position: KryptoPosition, bestand) -> bool:
        """Ist der Kontorest zu klein, um noch eine Position zu sein?

        KORREKTUR 9.5.4. Der Abgleich zwischen Konto und Buch hat bis 9.5.3
        nur geprueft, ob ueberhaupt ein Guthaben > 0 gemeldet wird. Nach dem
        Komplettverkauf von BNB am 02.09.2026 (0,15443 von 0,15443757 zu
        684,25) blieben 0,00000757 BNB im Konto liegen -- ein positiver Wert.

        Die Position wurde deshalb nicht geschlossen, sondern nach unten auf
        den Staub korrigiert. Dort haengt sie fest: unter der OKX-Mindestmenge
        laesst sie sich nie wieder verkaufen, sie belegt einen Positionsplatz,
        blockiert den Wiedereinstieg und verfaelscht die Buchhaltung.

        Die Staubgrenze gab es bereits -- ``_rest_lohnt_sich`` -- sie wurde
        aber nur im Verkaufspfad benutzt. Genau dieser Verkauf lief jedoch
        ueber die brokerseitige Schutzorder und damit nie durch ``_schliesse``.
        """
        menge = float(getattr(bestand, "quantity", 0.0) or 0.0)
        if menge <= 0:
            return False
        kurs = float(getattr(bestand, "market_price", 0.0) or 0.0)
        if kurs <= 0:
            kurs = float(position.einstieg or 0.0)
        if kurs <= 0:
            # Ohne Preis wird nicht geraten. Lieber weiterfuehren als eine
            # echte Position faelschlich als Staub schliessen.
            return False
        return not self._rest_lohnt_sich(position, menge, kurs)

    def _schutz_als_verloren_merken(self, position: KryptoPosition) -> None:
        """Nach einem fehlgeschlagenen Verkauf gilt der Broker-Schutz als weg.

        ``schliesse_position`` storniert bei OKX ZUERST die Schutzorder und
        sendet danach den Verkauf. Schlaegt der Verkauf fehl, ist die
        Schutzorder trotzdem storniert -- die Position liegt ungeschuetzt im
        Konto. Bis v8.1.3 blieb ``broker_schutz`` dabei auf True, und die
        einzige Wiederherstellung (``if not position.broker_schutz``) griff
        deshalb nie.
        """
        if not position.broker_schutz:
            return
        position.broker_schutz = False
        self.buch.setze(position)
        logger.warning("Broker-Schutz fuer %s gilt nach fehlgeschlagenem Verkauf "
                       "als storniert -- wird im naechsten Takt neu gesetzt.",
                       position.symbol)

    def _schutz_nachziehen(self, position: KryptoPosition) -> None:
        """Broker-Schutz fuer die verbliebene Menge neu setzen.

        Nach einem Teilverkauf gilt die alte Schutzorder fuer eine Menge, die
        es nicht mehr gibt. Ohne dieses Nachziehen laege der Rest ungeschuetzt.
        """
        broker = self.broker
        if broker is None or position.menge <= 0:
            return
        try:
            try:
                zustand = broker.reconcile_position_protection(
                    self._instrument(position.symbol, position.inst_id),
                    position.menge, position.stop, position.take_profit,
                    protection_client_id=position.protection_client_order_id,
                    protection_algo_id=position.protection_algo_id,
                    trade_quote_ccy=position.trade_quote_ccy)
            except TypeError:
                zustand = broker.reconcile_position_protection(
                    self._instrument(position.symbol, position.inst_id),
                    position.menge, position.stop, position.take_profit)
            position.broker_schutz = bool(zustand.get("protection_confirmed"))
            position.protection_algo_id = str(zustand.get("algo_id") or "")
            position.protection_client_order_id = str(
                zustand.get("algo_client_id") or position.protection_client_order_id)
            position.protection_status = (
                "ACTIVE" if position.broker_schutz else
                "MISSING" if bool(zustand.get("checked")) else "PENDING")
            self.buch.setze(position)
            self._ledger_schutz_synchronisieren(
                position, str(zustand.get("detail") or ""))
            if not position.broker_schutz and bool(zustand.get("checked")):
                self._melde(f"Krypto {position.symbol}: Schutzorder fuer die Restmenge "
                            f"{position.menge:g} konnte NICHT bestaetigt werden -- "
                            f"nur der Client-Stop schuetzt.", wichtig=True)
        except BrokerFehler as exc:
            self._melde(f"Krypto {position.symbol}: Schutzorder fuer die Restmenge "
                        f"nicht setzbar ({exc}) -- nur der Client-Stop schuetzt.",
                        wichtig=True)

    @staticmethod
    def _verkaufsmeldung(position, menge, geplant, rest, rest_gefuehrt,
                         kurs, gebuehr, netto, quote, grund) -> str:
        """Verkaufsmeldung, die eine Teilausfuehrung ausdruecklich benennt."""
        import meldungen
        from datetime import datetime, timezone
        haltedauer = ""
        try:
            ein = datetime.fromisoformat(str(position.eroeffnet_am))
            if ein.tzinfo is None:
                ein = ein.replace(tzinfo=timezone.utc)
            minuten = (datetime.now(timezone.utc) - ein).total_seconds() / 60.0
            haltedauer = (f"{minuten:.0f} min" if minuten < 90
                          else f"{minuten / 60:.1f} h")
        except (TypeError, ValueError):
            haltedauer = ""
        return meldungen.verkauf(
            broker="okx", symbol=position.symbol, menge=menge, geplant=geplant,
            preis=kurs, waehrung=quote, gebuehr=gebuehr, ergebnis=netto,
            grund=grund, haltedauer=haltedauer, rest_gefuehrt=rest_gefuehrt)

    def _letzter_kurs(self, position: KryptoPosition) -> float:
        """Letzter bekannter Kurs -- ohne Ausnahme, ohne Blockieren."""
        broker = self.broker
        if broker is None:
            return 0.0
        try:
            quote = broker.latest_bid_ask(
                self._instrument(position.symbol, position.inst_id)) or {}
            return float(quote.get("last") or quote.get("bid") or 0.0)
        except Exception:
            logger.debug("Letzter Kurs fuer %s nicht abrufbar", position.symbol, exc_info=True)
            return 0.0

    @staticmethod
    def _quote_waehrung(position: KryptoPosition) -> str:
        """Quotewaehrung aus der Instrumentkennung -- nie blind EUR."""
        quote = str(getattr(position, "trade_quote_ccy", "") or "").strip().upper()
        if quote:
            return quote
        inst_id = str(getattr(position, "inst_id", "") or "")
        if "-" in inst_id:
            return inst_id.rsplit("-", 1)[-1].upper()
        return ""

    @staticmethod
    def _entry_fee_pct(position: KryptoPosition) -> float:
        """Tatsaechliche Einstiegsgebuehr relativ zum beweisbaren Fillwert."""
        fillwert = float(position.einstieg or 0.0) * float(position.menge or 0.0)
        return (max(0.0, float(position.entry_fee_quote or 0.0)) / fillwert
                if fillwert > 0 else 0.0)

    def _buche_okx_ergebnis(self, position: KryptoPosition, netto: float,
                            brutto: float, *, trade_id: str) -> bool:
        """Bucht exakt dasselbe Netto in Ledger- und OKX-Risikowaehrung."""
        broker = self.broker
        if broker is None:
            return False
        if str(trade_id or "").startswith("ledger:"):
            import trade_ledger
            from ledger_result import confirmed_net
            row=trade_ledger.trade_detail(int(str(trade_id).split(":",1)[1]))
            if (not row or row.get("broker")!="okx" or
                    row.get("broker_account_fingerprint")!=position.account_fingerprint or
                    bool(row.get("paper"))!=bool(position.paper)):
                raise RuntimeError("Risikobuchung ohne passenden Ledgerbeleg")
            proven=confirmed_net(row)
            if not proven:
                self.topf.state.register_unknown_pnl_trade(trade_id=str(trade_id))
                # Mengenbeweis und Ergebnisbeweis sind getrennt: unbekannte
                # Gebuehren verhindern weder Restschutz noch Mengenfortschritt.
                return True
            netto=float(row["netto_pnl"])
            brutto=float(row["brutto_pnl"])
        quote = self._quote_waehrung(position) or str(
            getattr(broker, "quote_ccy", "") or "EUR").upper()
        risk_ccy = str(getattr(broker, "kontowaehrung", lambda: quote)() or quote).upper()
        rate = (broker.quote_conversion_rate(quote, risk_ccy)
                if hasattr(broker, "quote_conversion_rate") else
                1.0 if quote == risk_ccy else None)
        if not rate or rate <= 0:
            self.topf.state.register_unknown_pnl_trade(trade_id=str(trade_id))
            self._melde(
                f"Krypto {position.symbol}: Ergebnis {netto:.4f} {quote} kann nicht "
                f"belastbar nach {risk_ccy} umgerechnet werden. Neue Kaeufe sind "
                "vorsorglich gestoppt.", wichtig=True, klasse="KRITISCH")
            return True
        alias = getattr(self.topf.state,"adopt_receipt_alias",None)
        if callable(alias) and str(trade_id or "").startswith("ledger:"):
            alias(str(trade_id),[position.exit_attempt_id,*position.exit_order_ids,*position.exit_fill_ids])
        self.topf.buche_ergebnis(
            float(netto) * float(rate), brutto=float(brutto) * float(rate),
            trade_id=str(trade_id or ""))
        return True

    @staticmethod
    def _ledger_schutz_synchronisieren(position: KryptoPosition,
                                        detail: str = "") -> None:
        """Spiegelt den periodischen Schutzabgleich in den offenen Trade."""
        try:
            import trade_ledger
            trade = trade_ledger.offener_trade(
                "okx", position.symbol,
                broker_position_id=position.inst_id,
                broker_account_fingerprint=position.account_fingerprint,
                entry_order_id=position.order_id)
            if not trade:
                # KORREKTUR 9.5.4: Hier wurde stumm zurueckgekehrt. Passte der
                # Identitaetsschluessel nicht (etwa nach einem Wechsel der
                # Kontokennung), lief der Schutzabgleich weiter, waehrend die
                # Ledger-Zeile fuer immer auf PENDING mit leerer algoId stand.
                # Buch und Ledger liefen so unbemerkt auseinander.
                logger.warning(
                    "Schutzstatus %s: keine offene Ledger-Zeile zu "
                    "inst_id=%s order_id=%s gefunden -- Buch sagt %s, das "
                    "Ledger bleibt unveraendert.",
                    position.symbol, position.inst_id, position.order_id,
                    position.protection_status)
                return
            trade_ledger.set_protection(
                int(trade["trade_id"]), algo_id=position.protection_algo_id,
                client_order_id=position.protection_client_order_id,
                status=position.protection_status, detail=detail)
        except Exception:
            # Sichtbar statt still: ein nicht gespiegelter Schutzstatus ist
            # der Unterschied zwischen "geschuetzt" und "haelt sich fuer
            # geschuetzt".
            logger.warning("Schutzstatus %s nicht ins Ledger gespiegelt",
                           position.symbol, exc_info=True)

    def _schliesse(self, position: KryptoPosition, preis: float, grund: str, *,
                   allow_manual: bool = False,
                   explicit_manual: bool = False, manual_command_id: str = "") -> str:
        broker = self.broker
        if broker is None:
            return "FAILED"
        identity = getattr(broker, "account_fingerprint", None)
        if callable(identity) and (not position.account_fingerprint
                or str(identity() or "") != str(position.account_fingerprint)
                or bool(getattr(broker, "demo", position.paper)) != bool(position.paper)):
            logger.error("OKX %s: Verkauf wegen Kontodomaenen-Konflikt gesperrt", position.symbol)
            return "BLOCKED"
        protokoll = Entscheidungsprotokoll(position.symbol, asset_type="crypto",
                                           broker="okx", aktion="VERKAUF")
        protokoll.technik(DAFUER, 1.0, grund)
        protokoll.messwerte(
            entry_decision_id=position.decision_id,
            entry_strategy_mode=position.entry_strategy_mode,
            strategy_name=position.strategy_name,
            strategy_version=position.strategy_version,
            strategy_parameter_hash=position.strategy_parameter_hash,
            exit_reason=grund,
        )

        # Harte Obergrenze: der Bot verkauft NIE mehr, als in seinem Buch
        # steht. Selbst wenn im Konto mehr liegt -- das gehoert ihm nicht.
        verkaufsmenge = float(position.menge)
        erlaubt = (position.darf_automatisch_verkaufen or
                   (allow_manual and position.darf_schutz_ausfuehren))
        if not erlaubt:
            logger.info("Verkauf %s uebersprungen: Position steht auf %s",
                        position.symbol, position.verwaltung)
            return "BLOCKED"
        if verkaufsmenge <= 0:
            return "FAILED"
        if position.exit_state == "MANUAL_REQUESTED":
            if (not explicit_manual or not manual_command_id
                    or position.manual_exit_command_id != manual_command_id):
                return "UNCLEAR"
            position.exit_state = str(position.manual_exit_previous.get("exit_state") or "IDLE")
        # Same rule for automatic and manual callers. A previous POST or an
        # uncommitted fill must be reconciled, never bypassed by a new attempt.
        if str(position.exit_state or "").upper() in {
                "SUBMITTING", "UNCLEAR", "MANUAL_EXIT_PENDING", "ACCOUNTING_PENDING", "MANUAL_REQUESTED"}:
            logger.warning("OKX %s: bestehender Ausstieg %s muss zuerst geklaert werden",
                           position.symbol, position.exit_state)
            return "UNCLEAR"
        # Eine UNGESCHUETZTE Botposition unterhalb ihres Stopniveaus darf nicht
        # von einem normalen Strategie-Backoff bis zu 60 Minuten blockiert
        # werden. Unklare bereits gesendete Orders bleiben davon unberuehrt
        # fail-closed (exit_state UNCLEAR wird weiter oben separat behandelt).
        emergency_unprotected = bool(
            not position.broker_schutz
            and str(position.protection_status or "").upper() in {"", "MISSING", "LOST", "FAILED", "PENDING"}
            and float(position.stop or 0.0) > 0
            and float(preis or 0.0) <= float(position.stop or 0.0)
            and str(position.exit_state or "").upper() not in {"UNCLEAR", "SUBMITTING"})
        if not explicit_manual and position.exit_retry_after and not emergency_unprotected:
            try:
                retry = datetime.fromisoformat(position.exit_retry_after.replace("Z", "+00:00"))
                if retry.tzinfo is None:
                    retry = retry.replace(tzinfo=timezone.utc)
                if (retry > datetime.now(timezone.utc)
                        and not self._preisgrenzen_retry_faellig(position)):
                    logger.info("Verkauf %s wartet bis %s (vorheriger Versuch: %s)",
                                position.symbol, retry.isoformat(), position.exit_last_detail)
                    return "COOLDOWN"
            except ValueError:
                logger.warning("Ungueltiger exit_retry_after-Wert fuer %s: %r",
                               position.symbol, position.exit_retry_after)
        # v9.1: Der Broker storniert als ERSTEN Schritt die OKX-Schutzorder.
        # Ab diesem Moment ist die Position ungeschuetzt -- auch wenn der
        # Prozess gleich darauf stirbt (SIGKILL, Stromausfall, OOM). Bis 9.0.15
        # blieb broker_schutz dabei auf True, und der einzige periodische
        # Schutzabgleich steht hinter "if not position.broker_schutz" -- er lief
        # deshalb nie wieder. Die Position stand dauerhaft nackt, waehrend die
        # Oberflaeche "Schutz aktiv" meldete.
        #
        # Der Vermerk wird deshalb VOR dem Brokeraufruf persistiert. Er ist
        # bewusst pessimistisch: scheitert der Verkauf schon vor dem Storno,
        # findet der naechste Abgleich die noch vorhandene Schutzorder ueber
        # ihre algoClOrdId wieder und bestaetigt sie. Das kostet einen Aufruf
        # und ist die sichere Richtung.
        schutz_vorher = bool(position.broker_schutz)
        if str(position.exit_state or "").upper() == "RETRY_WAIT":
            # Previous attempt is terminal; its IDs remain in the order journal.
            # Reusing clOrdId would make a later lookup select the wrong attempt.
            position.exit_attempt_id = ""
            position.exit_client_order_id = ""
            position.exit_order_ids = []
            position.exit_fill_ids = []
        position.exit_state = "SUBMITTING"
        position.exit_last_detail = str(grund)[:300]
        position.exit_started_at = datetime.now(timezone.utc).isoformat()
        position.exit_recovery_checks = 0
        if not position.exit_attempt_id:
            position.exit_attempt_id = uuid.uuid4().hex
        if not position.exit_client_order_id:
            from broker.okx import make_client_order_id
            position.exit_client_order_id = make_client_order_id("TBS95")
        if schutz_vorher:
            position.broker_schutz = False
            position.protection_status = "LOST"
        self.buch.setze(position)
        try:
            ergebnis = broker.schliesse_position(
                self._instrument(position.symbol, position.inst_id),
                verkaufsmenge, preis,
                client_order_id=position.exit_client_order_id,
                protection_algo_id=position.protection_algo_id,
                protection_client_id=position.protection_client_order_id,
                account_fingerprint=position.account_fingerprint)
        except OrderStatusUnklar as exc:
            protokoll.ergaenze(BROKER, NEUTRAL, detail=f"Transportzustand unklar: {exc}")
            protokoll.abschliessen("UNKLAR", "Verkauf konnte nicht bestaetigt werden")
            speichere_quellen(protokoll)
            # Bei unklarem SELL darf keine neue Schutz-SELL-Order entstehen:
            # die urspruengliche Order kann bereits gefuellt sein. Erst der
            # exakte ordId/clOrdId-/Fill-Abgleich entscheidet zwischen
            # Abschluss und nachweislichem No-Fill; bis dahin kein zweiter
            # Geldpfad fuer dieselbe Menge.
            position.exit_state = "UNCLEAR"
            if getattr(exc, "reference_id", ""):
                position.exit_client_order_id = str(exc.reference_id)
            position.exit_order_ids = sorted(set(position.exit_order_ids) | {
                str(x) for x in (getattr(exc, "order_ids", []) or []) if str(x)})
            position.exit_retry_after = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
            position.exit_last_detail = str(exc)[:300]
            self.buch.setze(position)
            self._melde(f"Krypto {position.symbol}: Verkaufszustand UNKLAR -- kein zweiter "
                        f"Versuch. Bitte im OKX-Konto pruefen.",
                        wichtig=True, klasse="KRITISCH")
            return "UNCLEAR"
        except BrokerFehler as exc:
            protokoll.ergaenze(BROKER, DAGEGEN, detail=str(exc))
            protokoll.abschliessen("FEHLGESCHLAGEN", str(exc))
            speichere_quellen(protokoll)
            logger.warning("Verkauf %s fehlgeschlagen: %s", position.symbol, exc)
            if schutz_vorher or dict(getattr(broker, "letzte_stornierung", {}) or {}).get("inst_id"):
                self._schutz_als_verloren_merken(position)
                self._schutz_nachziehen(position)
            from broker.okx_price_limits import OKXPriceBandError
            self._exit_fehlversuch(position, str(exc),
                                  proven_no_fill=isinstance(exc, OKXPriceBandError))
            return "FAILED"

        # Konnte die alte Schutzorder nicht storniert werden, ist das
        # Guthaben eingefroren -- der Verkauf wird dann stillschweigend zur
        # Teilausfuehrung. Das muss sichtbar sein.
        storno = dict(getattr(broker, "letzte_stornierung", {}) or {})
        if storno.get("fehler"):
            self._melde(meldungen_sicherheit(
                f"Schutzorder nicht stornierbar · {position.symbol}",
                "Die alte Schutzorder liess sich nicht entfernen: "
                + "; ".join(str(f)[:120] for f in storno["fehler"]),
                handlung="Das Guthaben kann eingefroren sein. Offene Orders im "
                         "OKX-Konto pruefen."), wichtig=True, klasse="KRITISCH")

        try:
            menge = float(ergebnis.filled_quantity or 0.0)
        except (TypeError, ValueError, AttributeError) as exc:
            logger.error("Verkauf %s: ungueltige Brokerantwort: %s", position.symbol, exc)
            position.exit_state = "UNCLEAR"
            position.exit_last_detail = "Ungueltige Brokerantwort; kein zweiter POST"
            self.buch.setze(position)
            return "UNCLEAR"
        position.exit_order_ids = list(getattr(ergebnis, "order_ids", []) or [])
        position.exit_fill_ids = list(getattr(ergebnis, "fill_ids", []) or [])
        self.buch.setze(position)
        if position.exit_order_ids:
            self._order_registry().register_orders(
                position.exit_order_ids, position.symbol,
                {"broker": "okx", "asset_type": "crypto", "role": "EXIT",
                 "reason": grund, "decision_id": position.decision_id,
                 "inst_id": position.inst_id,
                 "entry_order_id": position.order_id,
                 "exit_attempt_id": position.exit_attempt_id,
                 "client_order_id": position.exit_client_order_id,
                 "account_fingerprint": position.account_fingerprint,
                 "environment": "DEMO" if position.paper else "LIVE",
                 "cl_ord_id": position.exit_client_order_id,
                 "qty": verkaufsmenge, "trade_quote_ccy": self._quote_waehrung(position),
                 "zustand": str(ergebnis.status or "SUBMITTED").upper()},
                asset_type="crypto")
        record_order_result(
            position.decision_id, ergebnis, broker="okx", symbol=position.symbol,
            requested_qty=verkaufsmenge, requested_price=preis,
            currency=self._quote_waehrung(position), role="EXIT")
        kurs = float(ergebnis.avg_fill_price or 0.0)
        if not math.isfinite(menge) or (menge > 0 and (not math.isfinite(kurs) or kurs <= 0)):
            position.exit_state = "UNCLEAR"
            position.exit_last_detail = "Keine endlichen positiven Ausfuehrungswerte; kein zweiter POST"
            self.buch.setze(position)
            return "UNCLEAR"
        if menge <= 0:
            protokoll.abschliessen("NICHT_AUSGEFUEHRT", ergebnis.hinweis or "keine Ausfuehrung")
            speichere_quellen(protokoll)
            # v9.1: "0 gefuellt" ist nicht dasselbe wie "nachweislich nicht
            # ausgefuehrt". Der Broker berechnet dafuer ``terminal``: erst wenn
            # OKX einen Endzustand bestaetigt hat, ist der Fehlschlag bewiesen.
            # Bis 9.0.15 wurde das Feld NIE gelesen -- eine Order, deren
            # Statusabfrage dreimal leer blieb, landete im 5-Minuten-Retry und
            # wurde erneut gesendet, obwohl sie ausgefuehrt sein konnte.
            if (not bool(getattr(ergebnis, "terminal", False))
                    or not bool(getattr(ergebnis, "fill_evidence_complete", False))):
                position.exit_state = "UNCLEAR"
                position.exit_retry_after = (
                    datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
                position.exit_last_detail = (
                    f"{grund}: OKX bestaetigt keinen Endzustand "
                    f"({ergebnis.hinweis or 'ohne Detail'})")[:300]
                self.buch.setze(position)
                self._melde(
                    f"Krypto {position.symbol}: Verkaufszustand UNKLAR -- OKX hat "
                    "keinen Endzustand bestaetigt. Kein zweiter Versuch. Bitte im "
                    "OKX-Konto pruefen.", wichtig=True, klasse="KRITISCH")
                return "UNCLEAR"
            # Erst ein terminaler, fillloser Brokerzustand erlaubt wieder
            # eine Schutz-SELL-Order fuer dieselbe Menge.
            self._schutz_als_verloren_merken(position)
            self._schutz_nachziehen(position)
            self._exit_fehlversuch(
                position, f"{grund}: 0 von {verkaufsmenge:g} ausgefuehrt"
                + (f" ({ergebnis.hinweis})" if ergebnis.hinweis else ""),
                proven_no_fill=True)
            return "FAILED"

        if (not bool(getattr(ergebnis, "fill_evidence_complete", False))
                or not bool(getattr(ergebnis, "terminal", False))):
            position.exit_state = "UNCLEAR"
            position.exit_last_detail = "Ausfuehrung gemeldet, exakte Fillbelege noch unvollstaendig"
            self.buch.setze(position)
            self._melde(f"OKX {position.symbol}: {position.exit_last_detail}; kein zweiter Verkauf.",
                        wichtig=True, klasse="KRITISCH")
            return "UNCLEAR"

        # 9.5.8: Ab hier hat der Verkauf gefuellt. Die Fehlversuchskette ist
        # damit beendet -- der naechste Fehlversuch faengt wieder bei der
        # Grundwartezeit an, nicht bei der hochgezaehlten.
        position.exit_fehlversuche = 0

        # Nie mehr verbuchen, als verkauft werden durfte. Meldet der Broker
        # eine groessere Menge, ist das ein Datenfehler und keine Erlaubnis.
        if menge > verkaufsmenge * (1.0 + 1e-9):
            logger.error("Verkauf %s meldete %g statt hoechstens %g -- Abgleich erforderlich.",
                         position.symbol, menge, verkaufsmenge)
            position.exit_state = "ACCOUNTING_PENDING"
            position.exit_last_detail = "Verkaufsmenge groesser als bewiesener Botbestand; keine gekuerzte Buchung"
            self.buch.setze(position)
            return "UNCLEAR"

        ergebnis_pnl = (kurs - position.einstieg) * menge
        quote = self._quote_waehrung(position) or str(
            getattr(broker, "quote_ccy", "") or "EUR").upper()
        ausstiegsgebuehr = getattr(ergebnis, "fees_quote", None)
        einstiegsgebuehr_gesamt = float(position.entry_fee_quote or 0.0)
        einstiegsgebuehr = (einstiegsgebuehr_gesamt * menge / verkaufsmenge
                            if verkaufsmenge > 0 else einstiegsgebuehr_gesamt)
        netto = None  # Ausschliesslich der Ledger berechnet das bestaetigte Netto.
        eindeutige_exit_id = (str(ergebnis.reference_id or "") or
                              (str(ergebnis.order_ids[0]) if ergebnis.order_ids else "") or
                              f"{position.order_id}:{grund}")

        # --- Teilausfuehrung -------------------------------------------------
        # Bis 8.1.3 wurde die Position IMMER vollstaendig aus dem Buch
        # genommen -- auch wenn nur 10 % ausgefuehrt wurden. Am 25.08.2026
        # blieben dadurch 14,19 SOL ohne Stop, ohne Ziel und ohne Buchfuehrung
        # im Konto liegen. Ab 8.1.4 bleibt der Rest gefuehrt und geschuetzt.
        rest = round(verkaufsmenge - menge, 12)
        rest_gefuehrt = bool(
            rest > 0 and self._rest_lohnt_sich(position, rest, kurs))
        try:
            import trade_ledger
            closed_id = trade_ledger.trade_close(
                broker="okx", symbol=position.symbol.upper(), ausstieg_preis=kurs,
                menge=menge, exit_grund=grund, gebuehr=ausstiegsgebuehr,
                netto_pnl=netto, referenzpreis=preis,
                einstieg_preis=position.einstieg, eingestiegen_am=position.eroeffnet_am,
                asset_type="crypto", waehrung=quote, paper=bool(position.paper),
                exit_order_id=(str(ergebnis.order_ids[0]) if ergebnis.order_ids else ""),
                exit_fill_ids=list(getattr(ergebnis, "fill_ids", []) or []),
                event_id=position.exit_attempt_id,
                broker_position_id=position.inst_id,
                broker_account_fingerprint=position.account_fingerprint,
                entry_order_id=position.order_id,
                mfe_pct=(100.0 * (position.hoechstkurs - position.einstieg) / position.einstieg
                         if position.hoechstkurs and position.einstieg else None),
                critical=True)
            if not closed_id:
                raise RuntimeError("Ledger hat den bestaetigten OKX-Fill abgelehnt")
            # Erst nach dauerhafter Ledgerquittung den Risikotopf mutieren.
            # Scheitert eine lokale Persistenz, bleibt die Brokerposition im
            # Zustand ACCOUNTING_PENDING und es wird nur dieselbe Fill-ID
            # erneut gebucht -- niemals ein zweiter SELL gesendet.
            booked = self._buche_okx_ergebnis(
                position, netto, ergebnis_pnl,
                trade_id=f"ledger:{closed_id}")
            if not booked:
                raise RuntimeError("OKX-Risikobuchung nicht dauerhaft bestaetigt")
        except Exception as exc:
            position.exit_state = "ACCOUNTING_PENDING"
            position.exit_last_detail = (
                f"Brokerfill bestaetigt, lokale Buchung ausstehend: {type(exc).__name__}")
            self.buch.setze(position)
            logger.exception("OKX-Fill %s noch nicht vollstaendig verbucht",
                             position.symbol)
            self._melde(
                f"Krypto {position.symbol}: Verkauf beim Broker bestaetigt, "
                "lokale Buchung aber noch nicht dauerhaft abgeschlossen. "
                "Kein zweiter Verkauf; NEXUS wiederholt nur die Buchung.",
                wichtig=True, klasse="KRITISCH")
            return "ACCOUNTING_PENDING"

        if rest_gefuehrt:
            position.menge = rest
            position.entry_fee_quote = einstiegsgebuehr_gesamt - einstiegsgebuehr
            position.broker_schutz = False
            position.exit_state = "IDLE"
            position.exit_attempt_id = ""
            position.exit_client_order_id = ""
            position.exit_order_ids = []
            position.exit_fill_ids = []
            self.buch.setze(position)
            self._schutz_nachziehen(position)
        else:
            if rest > 0:
                # KORREKTUR 9.5.6: Hier stand die Quotewaehrung hinter einer
                # BASISMENGE. Im Logbuch las sich das am 03.09.2026 als
                # "Restmenge 0.01685 USDC" -- knapp zwei Cent. Tatsaechlich
                # waren es 0,01685 ZAMA, also 0,0009 USDC: weniger als ein
                # Zehntel Cent. Eine Zahl mit falscher Einheit ist schlimmer
                # als keine.
                gegenwert = float(rest) * float(kurs or 0.0)
                self._melde(f"Krypto {position.symbol}: Restmenge {rest:g} "
                            f"{position.symbol} (rund {gegenwert:.4f} {quote}) "
                            f"liegt unter der Mindestgroesse und wird nicht mehr "
                            f"gefuehrt.", wichtig=True)
            self.buch.entferne(position.symbol)
            self._gemeldeter_ueberhang.discard(position.symbol.upper())

        from ledger_result import confirmed_net
        receipt = trade_ledger.trade_detail(closed_id) or {}
        netto = receipt.get("netto_pnl") if confirmed_net(receipt) else None
        gebuehr_gesamt = receipt.get("gebuehren")
        gebuehr_text = f"{gebuehr_gesamt:.4f}" if gebuehr_gesamt is not None else "unbekannt"
        teil = (f"; Teilausfuehrung {menge:g} von {verkaufsmenge:g}"
                + (f", Rest {rest:g} bleibt gefuehrt und geschuetzt" if rest_gefuehrt
                   else ", Rest verfaellt") if rest > 0 else "")
        protokoll.ergaenze(BROKER, NEUTRAL,
                           detail=(f"{menge:g} zu {kurs:g}, Gebuehren gesamt "
                                   f"{gebuehr_text}{teil}"))
        protokoll.abschliessen("AUSGEFUEHRT", grund)
        speichere_quellen(protokoll)
        self._melde(self._verkaufsmeldung(position, menge, verkaufsmenge, rest,
                                          rest_gefuehrt, kurs,
                                          gebuehr_gesamt, netto,
                                          quote, grund), wichtig=True)
        self.topf.setze_offene_positionen(len(self.buch.aktive()))
        return "PARTIAL" if rest_gefuehrt else "CLOSED"

    def _preisgrenzen_retry_faellig(self, position: KryptoPosition) -> bool:
        """Shorten a legacy band-rejection cooldown only with exact journal proof.

        This does not change saved positions on install, invent fills or clear
        unresolved orders. A missing/foreign/nonterminal journal keeps the wait.
        """
        from broker.okx import _okx_fehlercode
        if (position.exit_state != "RETRY_WAIT" or not position.exit_client_order_id
                or _okx_fehlercode(position.exit_last_detail) not in {"51137", "51138"}):
            return False
        try:
            from execution_lifecycle import snapshot
            rows = [r for r in snapshot(broker="okx")
                    if r["account"] == position.account_fingerprint
                    and r["environment"] == ("DEMO" if position.paper else "LIVE")
                    and r["instrument"] == position.inst_id and r["side"] == "SELL"
                    and r["client_id"] == position.exit_client_order_id]
            if len(rows) != 1:
                return False
            row = rows[0]
            if (row["state"] != "REJECTED" or not row["terminal"]
                    or not row["evidence_complete"] or float(row["filled"]) != 0):
                return False
            at = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
            age = (datetime.now(timezone.utc) - at).total_seconds()
            delay = max(10.0, min(60.0, float(getattr(
                self.cfg, "OKX_EXIT_NO_FILL_RETRY_SECONDS", 30.0))))
            return age >= delay
        except Exception:
            logger.warning("Preisgrenzen-Retry %s: Journal nicht eindeutig; Wartezeit bleibt bestehen",
                           position.symbol)
            return False

    def _exit_fehlversuch(self, position: KryptoPosition, detail: str, *,
                         proven_no_fill: bool = False) -> None:
        """Persistenter Retry-Cooldown, Eskalation und Telegram-Deduplizierung.

        9.5.8: Bis 9.5.7 war das eine reine Endlosschleife. Die Wartezeit lag
        fest bei 5 Minuten, es gab keinen Zaehler und keinen Abbruch. Bei XLM
        drehte sich das ab dem 03.09.2026 unbegrenzt weiter -- Ausgangssignal,
        FOK 0 gefuellt, Schutz abgelehnt, 5 Minuten warten, von vorn --, und
        nur die Telegram-Meldung war gedrosselt. Die Sperre begrenzte damit die
        Frequenz, nicht die Dauer.

        Jetzt waechst die Wartezeit mit jedem Fehlversuch (5, 10, 20, 40, ...
        bis zur Obergrenze), und ab der eingestellten Schwelle sagt die Meldung
        ausdruecklich, dass Handarbeit noetig ist. Der Verkauf wird NICHT
        aufgegeben: eine Position ohne funktionierenden Ausstieg still
        liegenzulassen waere schlimmer als ein weiterer Versuch.
        """
        now = datetime.now(timezone.utc)
        position.exit_state = "RETRY_WAIT"
        # Eigenes Feld: ``exit_recovery_checks`` zaehlt bereits die
        # Klaerungsversuche eines UNKLAREN Ausstiegs (siehe _klaere_unklaren_exit).
        # Dieselbe Zahl fuer zwei Zwecke zu benutzen wuerde beide verfaelschen.
        try:
            position.exit_fehlversuche = int(position.exit_fehlversuche or 0) + 1
        except (TypeError, ValueError):
            position.exit_fehlversuche = 1
        versuche = int(position.exit_fehlversuche)
        basis = max(1.0, float(getattr(self.cfg, "OKX_EXIT_RETRY_MINUTES", 5.0)))
        obergrenze = max(basis, float(getattr(
            self.cfg, "OKX_EXIT_RETRY_MAX_MINUTES", 60.0)))
        # Der Exponent wird gedeckelt: die Schleife gibt bewusst nie auf, und
        # 2 ** 1024 waere ein OverflowError mitten im Ausstiegspfad.
        wartezeit = min(obergrenze, basis * (2 ** min(max(0, versuche - 1), 20)))
        if proven_no_fill:
            # Ein terminal belegter Nullfill braucht ein neues Orderbuch,
            # keine exponentielle 40-Minuten-Pause. Unklare Orders erreichen
            # diesen Pfad nie. Gesendet wird fruehestens im naechsten Kerntakt.
            wartezeit = max(10.0, min(60.0, float(getattr(
                self.cfg, "OKX_EXIT_NO_FILL_RETRY_SECONDS", 30.0)))) / 60.0
        position.exit_retry_after = (now + timedelta(minutes=wartezeit)).isoformat()
        schwelle = max(1, int(getattr(self.cfg, "OKX_EXIT_ESKALATION_VERSUCHE", 5)))
        eskaliert = versuche >= schwelle
        position.exit_last_detail = (
            f"{str(detail)[:260]} (Versuch {versuche})")[:300]
        notify = True
        if position.exit_last_notice_at:
            try:
                previous = datetime.fromisoformat(position.exit_last_notice_at.replace("Z", "+00:00"))
                if previous.tzinfo is None:
                    previous = previous.replace(tzinfo=timezone.utc)
                notify = (now - previous).total_seconds() >= max(
                    60.0, float(getattr(self.cfg, "OKX_EXIT_ALERT_COOLDOWN_SECONDS", 900.0)))
            except ValueError:
                pass
        if notify:
            position.exit_last_notice_at = now.isoformat()
        self.buch.setze(position)
        if eskaliert:
            # Nach der Schwelle immer melden -- eine Drossel darf einen
            # dauerhaft nicht ausfuehrbaren Ausstieg nicht unsichtbar machen.
            notify = True
            position.exit_last_notice_at = now.isoformat()
            self.buch.setze(position)
        if notify:
            schutz = ("Broker-Schutz im letzten Abgleich bestaetigt. "
                      if position.broker_schutz and position.protection_status == "ACTIVE" else
                      "ACHTUNG: Broker-Schutz NICHT bestaetigt "
                      f"({position.protection_status or 'UNKNOWN'}). ")
            handlung = (schutz + "Naechster regulaerer Verkaufsversuch fruehestens ab "
                        f"{position.exit_retry_after} (UTC). "
                        "Ein ungeklaerter Auftrag wird zuerst recherchiert.")
            if eskaliert:
                handlung += (
                    f" ACHTUNG: {versuche} Versuche in Folge fehlgeschlagen. "
                    "Der Ausstieg funktioniert nicht von selbst -- bitte die "
                    "Position im OKX-Konto pruefen (Orderbuchtiefe, gebundenes "
                    "Guthaben, liegende Schutzorder). NEXUS versucht es weiter, "
                    + (f"fruehestens nach {wartezeit * 60:.0f} Sekunden im naechsten Kerntakt."
                       if wartezeit < 1 else f"jetzt im Abstand von {wartezeit:.0f} Minuten."))
            self._melde(meldungen_sicherheit(
                f"Verkauf nicht ausgefuehrt · {position.symbol}",
                position.exit_last_detail, handlung=handlung),
                wichtig=True, klasse="KRITISCH")

    def _gebuehr(self, menge: float, preis: float, quote_ccy: str = "EUR") -> float:
        """Gebuehr mit dem GEMESSENEN Satz, nicht mit der Annahme."""
        from cost_engine import commission_for
        return commission_for(menge, preis, asset_type="crypto",
                              currency=str(quote_ccy or "EUR"), broker="okx",
                              fee_pct=self._taker_satz())

    def _verwaiste_schutzorders(self) -> list[dict]:
        """Schutzorders beim Broker, zu denen NEXUS keine Position fuehrt.

        9.5.4. Befund vom 02.09.2026: eine lebende OCO-Order auf ETH-EUR
        (algoId 3873284120214450177, sz 1,043377, SL 1894,9, TP 2205,1 --
        rund 2530 EUR) ohne jede Position im Buch. Sie war nirgends sichtbar.

        Bewusst wird hier NICHT uebernommen und NICHT storniert:

        * Uebernehmen hiesse, einen Bestand als eigenen zu buchen, fuer den
          es keinen Eigentumsbeweis gibt -- genau der Fehler vom 25.08.2026,
          als 0,94 fremde BTC verkauft wurden.
        * Stornieren hiesse, einen echten Bestand ungeschuetzt zu lassen.

        Sichtbar machen und den Wiedereinstieg sperren ist das Richtige. Die
        Entscheidung trifft Georg.
        """
        broker = self.broker
        self._schutzorders_unlesbar = ""
        if broker is None or not hasattr(broker, "alle_schutzorders"):
            return []
        try:
            schutzorders = broker.alle_schutzorders()
        except BrokerFehler as exc:
            # Nicht abrufbar heisst nicht "keine". Der Sperrgrund bleibt
            # bestehen, bis die Lage geklaert ist.
            # KORREKTUR 9.5.5: Bis 9.5.4 wurde hier ein Platzhalter mit dem
            # Symbol "<SCHUTZORDERS-UNLESBAR>" zurueckgegeben. Der Kaufpfad
            # filtert die Kandidaten aber nach Symbolnamen -- gesperrt wurde
            # also nur ein Symbol, das es gar nicht gibt. Der Docstring
            # versprach eine Sperre, der Code hielt sie nicht.
            #
            # Jetzt gibt es eine eigene Sperrflagge, die der Kaufpfad
            # auswertet: nicht abrufbar heisst nicht "keine".
            logger.warning("Schutzorders nicht abrufbar: %s", exc)
            self._schutzorders_unlesbar = str(exc)
            return []
        # KORREKTUR 9.5.5: Bis 9.5.4 wurde nur die Basiswaehrung verglichen.
        # Eine Position ETH-USDC im Buch hat damit eine verwaiste Schutzorder
        # auf ETH-EUR verdeckt -- genau die Konstellation aus dem Quotewechsel
        # EUR -> USDC, um die es geht. Verglichen wird jetzt instrumentgenau.
        gefuehrt = {normalize_inst_id(str(p.inst_id or "")) for p in self.buch.alle()
                    if str(p.inst_id or "").strip()}
        verwaist = []
        for row in schutzorders:
            instrument = normalize_inst_id(str(row.get("instrument") or ""))
            if not instrument or instrument in gefuehrt:
                continue
            verwaist.append(dict(row))
        return verwaist

    def _verwaiste_schutzorders_melden(self, verwaist: list[dict]) -> None:
        """Meldet die Lage -- mit Cooldown, nicht nur einmal je Prozesslauf.

        KORREKTUR 9.5.5: Bis 9.5.4 wurde genau einmal je Prozesslauf gemeldet
        (Vergleich gegen eine Menge im Speicher). Eine Dauerlage war nach der
        ersten Nachricht wieder unsichtbar -- und sichtbar war sie sonst
        nirgends. Jetzt laeuft die Meldung ueber denselben Cooldown wie alle
        anderen Dauerstoerungen und steht zusaetzlich im Status.
        """
        if self._schutzorders_unlesbar:
            self._melde_einmal(
                "schutzorders:unlesbar",
                "Krypto: Die Schutzorders im OKX-Konto sind derzeit nicht "
                f"abrufbar ({self._schutzorders_unlesbar}). Neue Einstiege "
                "bleiben gesperrt, bis sie wieder geprueft werden koennen.",
                wichtig=True, klasse="KRITISCH")
            return
        if not verwaist:
            return
        zeilen = []
        for r in verwaist:
            zeilen.append(
                f"{r.get('symbol')} ({r.get('instrument')}): Menge "
                f"{float(r.get('menge') or 0.0):g}, SL {float(r.get('stop') or 0.0):g}, "
                f"TP {float(r.get('take_profit') or 0.0):g}, algoId {r.get('algo_id')}")
        kennung = ",".join(sorted(str(r.get("algo_id") or "") for r in verwaist))
        self._melde_einmal(
            f"schutzorders:verwaist:{kennung}",
            "Krypto: Schutzorder(s) im OKX-Konto ohne zugehoerige Position im "
            "Buch:\n" + "\n".join(f"- {z}" for z in zeilen)
            + "\nNEXUS uebernimmt diesen Bestand NICHT und storniert nichts. "
              "Neue Einstiege in diese Werte bleiben gesperrt, bis die Lage "
              "geklaert ist.", wichtig=True, klasse="KRITISCH")

    def _offene_order_symbole(self) -> list[str]:
        """Symbole mit noch nicht abgeschlossener OKX-Bot-Order (9.5.2).

        Bis 9.5.1 stand hier ``registry.nicht_terminale()`` ohne jeden Filter.
        Das Register ist aber gemeinsam: ``etoro_reconciliation`` schreibt
        seine Aktienorders in dieselbe Datei. Eine eToro-Aktie konnte damit
        den Kryptohandel sperren -- unter der Meldung
        "eToro-Ausfuehrungsabgleich", die deshalb auch bei OKX erschien.

        Ein unlesbares Register wird NICHT mehr stillschweigend zu "keine
        offenen Orders". Es meldet sich als eigener Sperrgrund.
        """
        try:
            registry = self._order_registry()
        except Exception:
            logger.warning("Order-Register nicht ladbar; Krypto-Einstiege bleiben gesperrt",
                           exc_info=True)
            return ["<REGISTER-UNLESBAR>"]
        if getattr(registry, "storage_error", ""):
            logger.warning("Order-Register unlesbar (%s); Krypto-Einstiege bleiben gesperrt",
                           registry.storage_error)
            return ["<REGISTER-UNLESBAR>"]
        try:
            offen = registry.pending_for(
                broker="okx",
                environment=("DEMO" if bool(getattr(self.broker, "demo", False)) else "LIVE"),
                account_fingerprint=self._konto_fingerprint(),
                asset_type="crypto")
        except Exception:
            # 9.5.8: Hier stand ``return []`` -- "nichts offen". Zwei Zweige
            # weiter oben schliesst dieselbe Funktion fuer denselben Fehlerfall
            # bewusst zu (<REGISTER-UNLESBAR>). Ein Zeitueberlauf am
            # Zustandsschloss machte die Sperre damit sporadisch gruen, obwohl
            # der Registerzustand unbekannt war. Unlesbar heisst unlesbar --
            # in beiden Zweigen gleich.
            logger.warning("Offene Bot-Orders nicht ermittelbar; "
                           "Krypto-Einstiege bleiben gesperrt", exc_info=True)
            return ["<REGISTER-UNLESBAR>"]
        return [str(meta.get("symbol") or key.split(":")[-1]).upper()
                for key, meta in offen.items()]

    def _melde_ueberfaellige_orders(self) -> list[dict]:
        """Zu lange ungeklaerte eigene Einstiegsorders sichtbar machen (9.5.2).

        Die Klaerung selbst laeuft in _reconcile_pending_okx_orders(); hier
        wird nur festgestellt, dass sie ueber Gebuehr nicht gelingt. Eine
        Sperre, die stundenlang stumm bleibt, ist schlimmer als eine, die
        laut ist -- am 01.09.2026 stand der Kryptohandel deshalb 9,5 Stunden
        still, ohne dass irgendwo der Grund stand.
        """
        grenze = max(60.0, float(getattr(
            self.cfg, "ORDER_KLAERUNG_HOECHSTALTER_SEKUNDEN", 900.0)))
        try:
            registry = self._order_registry()
            offen = registry.ueberfaellige(
                hoechstalter_sekunden=grenze, broker="okx",
                environment=("DEMO" if bool(getattr(self.broker, "demo", False)) else "LIVE"),
                account_fingerprint=self._konto_fingerprint(),
                asset_type="crypto")
        except Exception:
            logger.debug("Ueberfaellige Orders nicht ermittelbar", exc_info=True)
            return []
        if not offen:
            self._entwarnung("order_ueberfaellig",
                             "Krypto: alle eigenen Orders wieder geklaert.")
            return []
        zeilen = "; ".join(
            f"{x['symbol']} {x['zustand']} seit {x['alter_sekunden'] / 3600:.1f} h "
            f"(ordId {x['ord_id'] or 'unbekannt'})" for x in offen[:5])
        self._melde_einmal(
            "order_ueberfaellig",
            f"Krypto: {len(offen)} eigene Order(s) seit ueber "
            f"{grenze / 60:.0f} min ungeklaert -- Einstiege bleiben gesperrt. "
            f"{zeilen}. Eine FOK-Order ist beim Broker sofort terminal; "
            "hier fehlt die Brokerantwort, nicht die Ausfuehrung.",
            wichtig=True, klasse="KRITISCH")
        return offen

    def _konto_fingerprint(self) -> str:
        """Kontokennung des aktuellen OKX-Adapters, leer wenn unbekannt."""
        try:
            fn = getattr(self.broker, "account_fingerprint", None)
            return str(fn() or "") if callable(fn) else ""
        except Exception:
            return ""

    @staticmethod
    def _nichtausfuehrungs_text(ergebnis, beleg: dict) -> str:
        """Klartext, warum ein freigegebener Kauf nicht stattgefunden hat."""
        art = str(beleg.get("ord_type") or "").upper()
        limit = beleg.get("limit_price")
        angefordert = float(beleg.get("requested_qty") or 0.0)
        status = getattr(ergebnis, "execution_status", "UNKNOWN")
        kopf = {
            "CANCELED_NO_FILL": (
                f"Kauf erlaubt -- bei OKX nicht ausgefuehrt: "
                f"0/{angefordert:g} zum {art or 'FOK'}-Limit"
                + (f" {float(limit):g}" if limit else "")),
            "REJECTED": "Kauf erlaubt -- von OKX abgelehnt",
            "EXPIRED": "Kauf erlaubt -- bei OKX verfallen",
            "PARTIAL": "Kauf erlaubt -- bei OKX nur teilweise ausgefuehrt",
        }.get(status, "Kauf erlaubt -- Ausgang bei OKX unklar")
        teile = [kopf]
        if beleg.get("cancel_source"):
            teile.append(f"cancelSource={beleg['cancel_source']}")
        if beleg.get("s_code"):
            teile.append(f"sCode={beleg['s_code']}")
        if beleg.get("s_msg"):
            teile.append(str(beleg["s_msg"])[:120])
        elif getattr(ergebnis, "hinweis", ""):
            teile.append(str(ergebnis.hinweis)[:120])
        return " · ".join(x for x in teile if x)

    def _order_registry(self):
        from order_ownership import OrderOwnershipRegistry
        return OrderOwnershipRegistry(
            _state_root() / str(getattr(
                self.cfg, "BOT_ORDER_REGISTRY_FILE", "bot_order_registry.json")))

    def _persistiere_okx_entry(self, *, symbol: str, decision_id: int,
                               ergebnis, intent: dict,
                               nachtraeglich: bool = False):
        """Echte OKX-Fills atomar als Bot-Teilmenge beweisen.

        Der Kontosaldo ist absichtlich kein Eingabewert. Eigentum entsteht
        allein aus der persistierten clOrdId, der ordId und echten tradeIds.
        """
        fill_ids = [str(x) for x in (getattr(ergebnis, "fill_ids", []) or [])
                    if str(x).strip()]
        order_ids = [str(x) for x in (getattr(ergebnis, "order_ids", []) or [])
                     if str(x).strip()]
        if (not bool(getattr(ergebnis, "fill_evidence_complete", False))
                or not bool(getattr(ergebnis, "terminal", False))
                or not fill_ids or not order_ids
                or float(getattr(ergebnis, "filled_quantity", 0.0) or 0.0) <= 0):
            return None, None
        strategy = dict(intent.get("strategy_identity") or {})
        strategy_mode = str(intent.get("strategy_mode") or
                            strategy.get("entry_strategy_mode") or "")
        menge = float(ergebnis.filled_quantity)
        kurs = float(ergebnis.avg_fill_price or intent.get("signal_price") or 0.0)
        signal_price = float(intent.get("signal_price") or 0.0)
        planned_stop = float(intent.get("stop") or 0.0)
        planned_take = float(intent.get("take") or 0.0)
        # Schutz und ROI werden IMMER am echten Durchschnittsfill verankert.
        # Im Freqtrade-Modus sind -10 % und anfangs +4 % netto Teil der
        # offiziellen SampleStrategy. Die spaeteren ROI-Stufen werden im
        # 60-Sekunden-Monitor clientseitig ausgewertet.
        stop, take = fill_anchored_stop_take(
            strategy_mode=strategy_mode, fill_price=kurs, filled_quantity=menge,
            entry_fee_quote=float(getattr(ergebnis, "fees_quote", 0.0) or 0.0),
            exit_fee_pct=self._taker_satz(), signal_price=signal_price,
            planned_stop=planned_stop, planned_take=planned_take)
        cl_ord_id = str(getattr(ergebnis, "client_order_id", "") or
                        getattr(ergebnis, "reference_id", "") or
                        intent.get("cl_ord_id") or "")
        order_tag = str(getattr(ergebnis, "order_tag", "") or
                        intent.get("order_tag") or "")
        protection_client_id = ("P" + "".join(
            ch for ch in cl_ord_id if ch.isalnum()))[:32]
        # v9.1: Die ROI-Uhr laeuft ab dem FILL, nicht ab der Verbuchung.
        # Vorher griff der Dataclass-Default datetime.now(). Im Normalfall sind
        # das nur Sekunden -- wird ein Fill aber erst nach einem Absturz
        # aufgeklaert (nachtraeglich=True), startete die Uhr komplett neu, und
        # die Position blieb auf der 4-%-Stufe stehen statt auf 2 % oder 1 %
        # zu fallen. Freqtrade misst die Haltedauer ab dem Einstiegsfill.
        eroeffnet = _fillzeitpunkt(ergebnis) or datetime.now(timezone.utc).isoformat()
        position = KryptoPosition(
            eroeffnet_am=eroeffnet,
            entry_order_created_at=str(intent.get("order_created_at") or ""),
            symbol=str(symbol).upper(), inst_id=str(intent.get("inst_id") or "").upper(),
            menge=menge, einstieg=kurs, stop=stop,
            take_profit=take,
            order_id=order_ids[0], referenz=cl_ord_id,
            broker_schutz=bool(ergebnis.stop_order_platziert), hoechstkurs=kurs,
            paper=bool(ergebnis.paper), decision_id=int(decision_id),
            entry_strategy_mode=strategy_mode,
            strategy_name=str(strategy.get("strategy_name") or ""),
            strategy_version=str(strategy.get("strategy_version") or ""),
            strategy_parameter_hash=str(strategy.get("parameter_hash") or ""),
            strategy_parameters=strategy,
            trade_quote_ccy=str(getattr(ergebnis, "trade_quote_ccy", "") or
                                intent.get("trade_quote_ccy") or ""),
            client_order_id=cl_ord_id, order_tag=order_tag,
            fill_ids=fill_ids, ownership_verified=True,
            # v9.2: Konto festhalten, damit ein Bestand aus dem Demokonto
            # nach einem Umschalten auf Live nicht uebernommen wird.
            account_fingerprint=(
                self.broker.account_fingerprint()
                if hasattr(self.broker, "account_fingerprint") else ""),
            entry_fee_by_currency=dict(getattr(ergebnis, "fees", {}) or {}),
            entry_fee_quote=float(getattr(ergebnis, "fees_quote", 0.0) or 0.0),
            protection_client_order_id=(
                str(getattr(ergebnis, "protection_client_order_id", "") or "")
                or protection_client_id),
            protection_algo_id=str(
                getattr(ergebnis, "protection_algo_id", "") or ""),
            # KORREKTUR 9.5.4: Der Status war hier fest "PENDING" verdrahtet,
            # waehrend broker_schutz aus dem Orderergebnis kam. Beides konnte
            # sich damit widersprechen -- am 02.09.2026 standen BNB, LINK und
            # XLM auf broker_schutz=True UND protection_status=PENDING mit
            # leerer algoId. Der Status folgt jetzt demselben Beweis wie die
            # Flagge.
            protection_status=("ACTIVE"
                               if bool(ergebnis.stop_order_platziert)
                               else "PENDING"),
        )
        existing = self.buch.hole(position.symbol)
        if existing is not None and not (
                str(existing.order_id) == order_ids[0]
                and str(existing.inst_id).upper() == position.inst_id
                and str(existing.account_fingerprint) == str(position.account_fingerprint)
                and bool(existing.paper) == bool(position.paper)):
            from trade_ledger import LedgerZuordnungUnklar
            raise LedgerZuordnungUnklar("OKX-Einstieg kollidiert mit anderer Positionslineage; Bestand bleibt erhalten")

        trade_id = None
        persistence_error = None
        lineage = []
        try:
            import trade_ledger
            lineage = trade_ledger.entry_lineage(
                "okx", position.inst_id, order_ids[0], position.account_fingerprint,
                paper=position.paper)
            # An in-flight EXIT is never mutated by an entry refresh.
            if existing is not None and str(existing.exit_state).upper() in {
                    "SUBMITTING", "UNCLEAR", "MANUAL_EXIT_PENDING", "ACCOUNTING_PENDING", "MANUAL_REQUESTED"}:
                logger.info("OKX %s: Entry-Replay wartet auf laufenden Exitabgleich", position.symbol)
                return None, None
            trade_id = trade_ledger.trade_open(
                broker="okx", symbol=position.symbol, menge=menge,
                zeit=position.eroeffnet_am,
                einstieg_preis=kurs, referenzpreis=intent.get("signal_price"),
                asset_type="crypto", waehrung=self._quote_waehrung(position),
                paper=bool(ergebnis.paper),
                gebuehr=getattr(ergebnis, "fees_quote", None),
                marktphase=str(intent.get("marktphase") or ""),
                decision_id=decision_id,
                strategie_version=str(strategy.get("strategy_version") or ""),
                entry_strategy_mode=strategy_mode,
                strategy_parameter_hash=str(strategy.get("parameter_hash") or ""),
                strategy_parameters=strategy,
                enter_tag=str(intent.get("enter_tag") or "")[:160],
                broker_position_id=position.inst_id,
                entry_order_id=order_ids[0], entry_fill_id=fill_ids[0],
                entry_fill_ids=fill_ids,
                entry_fills=list(getattr(ergebnis, "fills", []) or []),
                client_order_id=cl_ord_id, order_tag=order_tag,
                ownership_status="VERIFIED_BROKER_FILL_CHAIN",
                entry_fee_by_currency=dict(getattr(ergebnis, "fees", {}) or {}),
                broker_account_fingerprint=(
                    self.broker.account_fingerprint()
                    if hasattr(self.broker, "account_fingerprint") else ""),
                reconciliation_status="CONFIRMED_OPEN",
                notiz=("nachtraeglich aus persistierter OKX-Order bestaetigt"
                       if nachtraeglich else "echte OKX-tradeId-Fills bestaetigt"),
                critical=True)
        except Exception as exc:
            logger.warning("OKX-Fill konnte nicht vollstaendig ins Trade-Ledger geschrieben werden",
                           exc_info=True)
            if isinstance(exc, OSError):
                persistence_error = exc
                self._note_critical_persistence_fault(exc)

        try:
            registry = self._order_registry()
            domain = {"broker": "okx", "environment": "DEMO" if position.paper else "LIVE",
                      "account_fingerprint": position.account_fingerprint,
                      "decision_id": int(decision_id)}
            if trade_id:
                proof = {**intent, **domain, "role": "ENTRY", "zustand": "FILLED",
                         "ord_id": order_ids[0], "cl_ord_id": cl_ord_id,
                         "client_order_id": cl_ord_id, "fill_ids": fill_ids,
                         "trade_id": trade_id, "identifier_type": "ORDER"}
                registry.register_orders(order_ids, symbol, proof, asset_type="crypto")
                registry.register_orders(fill_ids, symbol,
                                         {**proof, "identifier_type": "FILL"}, asset_type="crypto")
                # Ledger may have a CLOSED slice plus a live remainder. Do not
                # reconstruct the original entry size from a historic buy result.
                after = trade_ledger.entry_lineage(
                    "okx", position.inst_id, order_ids[0], position.account_fingerprint,
                    paper=position.paper)
                open_rows = [r for r in after if not r.get("ausgestiegen_am")]
                if after and not open_rows:
                    registry.clear_pending(symbol, "crypto", **domain)
                    return None, trade_id
                if existing is not None:
                    before_ids = set(existing.fill_ids)
                    if set(fill_ids).issubset(before_ids):
                        registry.clear_pending(symbol, "crypto", **domain)
                        return existing, trade_id
                    if any(r.get("ausgestiegen_am") for r in after):
                        raise trade_ledger.LedgerZuordnungUnklar(
                            "OKX-Entry-Ergaenzung nach Teilverkauf; keine Positionsueberschreibung")
                    # New proven entry fills before any exit: use the full ledger
                    # projection, retaining manual controls, retry and high-water data.
                    row = open_rows[0] if len(open_rows) == 1 else None
                    if row is None:
                        raise trade_ledger.LedgerZuordnungUnklar("OKX-Entryprojektion nicht eindeutig")
                    existing.menge = float(row["menge"])
                    existing.einstieg = float(row["einstieg_preis"])
                    existing.entry_fee_quote = float(row.get("einstieg_gebuehr") or 0.0)
                    existing.entry_fee_by_currency = json.loads(row.get("entry_fee_currency_json") or "{}")
                    existing.fill_ids = sorted(before_ids | set(fill_ids))
                    position = existing
                elif any(r.get("ausgestiegen_am") for r in lineage):
                    raise trade_ledger.LedgerZuordnungUnklar(
                        "Offener Ledgerrest ohne Positionssteuerung; keine Rekonstruktion aus urspruenglicher Kaufmenge")
                self.buch.setze(position)
                self.topf.setze_offene_positionen(len(self.buch.aktive()))
            else:
                registry.setze_zustand(symbol, "AWAITING_POSITION_CONFIRMATION", "crypto",
                                       **domain, ord_id=order_ids[0], cl_ord_id=cl_ord_id,
                                       fill_ids=fill_ids)
                if existing is not None:
                    # Existing exit controls and amounts are not collateral damage
                    # of an unavailable ledger. Pending evidence remains replayable.
                    return None, None
                position.pausiere("Einstiegsbuchung ausstehend", status="ENTRY_ACCOUNTING_PENDING")
                self.buch.setze(position)
                self.topf.setze_offene_positionen(len(self.buch.aktive()))

        except OSError as exc:
            persistence_error = exc
            self._note_critical_persistence_fault(exc)
            # The economic fill is already established above. Keep its exact
            # IDs and quantity in RAM and still ask the normal idempotent
            # protection path to reconcile it. That path keeps its own durable
            # submit journal and may itself refuse a new order on storage error.

        try:
            # Normalerweise folgt Schutz auf die haltbare Positionsprojektion.
            # Nach bestaetigtem Fill darf ein lokaler Speicherfehler den Versuch
            # nicht ueberspringen; das separate Schutzjournal bleibt verbindlich.
            # KORREKTUR 9.5.4: Hier stand "if trade_id:". Schlug der
            # Ledger-Schreibvorgang oben fehl (er faengt Exception und loggt nur
            # eine Warnung), wurde der gesamte Schutzblock uebersprungen: keine
            # Schutzorder, kein Abgleich, keine Meldung -- die Position lief
            # ungeschuetzt weiter, waehrend broker_schutz aus dem Orderergebnis
            # True bleiben konnte. Das passt exakt zu den 560,88 XLM, die am
            # 02.09.2026 ohne jede Schutzorder im Konto lagen.
            #
            # Die Ledger-Zeile ist Buchhaltung, die Schutzorder ist Sicherheit.
            # Fehlende Buchhaltung darf die Sicherheit nie abschalten.
            try:
                try:
                    protection = self.broker.reconcile_position_protection(
                        self._instrument(position.symbol, position.inst_id),
                        position.menge, position.stop, position.take_profit,
                        protection_client_id=protection_client_id,
                        trade_quote_ccy=position.trade_quote_ccy)
                except TypeError:
                    protection = self.broker.reconcile_position_protection(
                        self._instrument(position.symbol, position.inst_id),
                        position.menge, position.stop, position.take_profit)
                position.broker_schutz = bool(protection.get("protection_confirmed"))
                position.protection_algo_id = str(protection.get("algo_id") or "")
                position.protection_client_order_id = str(
                    protection.get("algo_client_id") or protection_client_id)
                position.protection_status = (
                    "ACTIVE" if position.broker_schutz else
                    "MISSING" if bool(protection.get("checked", True)) else "PENDING")
                self.buch.setze(position)
                if trade_id:
                    trade_ledger.set_protection(
                        trade_id,
                        algo_id=position.protection_algo_id,
                        client_order_id=position.protection_client_order_id,
                        status=position.protection_status,
                        detail=str(protection.get("detail") or ""))
                ergebnis.stop_order_platziert = position.broker_schutz
                ergebnis.take_order_platziert = position.broker_schutz
                ergebnis.protection_algo_id = position.protection_algo_id
                ergebnis.protection_client_order_id = position.protection_client_order_id
                if not position.broker_schutz:
                    self._warn_missing_protection(position, str(protection.get("detail") or ""))
            except BrokerFehler as exc:
                position.protection_status = "MISSING"
                # Ohne bestaetigten Schutz darf die Flagge nicht stehen
                # bleiben. Sonst meldet die Oberflaeche Sicherheit, die es
                # nicht gibt.
                position.broker_schutz = False
                self.buch.setze(position)
                if trade_id:
                    trade_ledger.set_protection(
                        trade_id, client_order_id=protection_client_id,
                        status="MISSING", detail=str(exc))
                self._melde(meldungen_sicherheit(
                    f"Broker-Schutz fehlt · {position.symbol}",
                    f"Der Kauf ist eindeutig verbucht, aber die OKX-Schutzorder "
                    f"konnte nicht bestaetigt werden: {exc}",
                    handlung="Neue Einstiege in dieses Symbol bleiben gesperrt; "
                             "NEXUS versucht den Schutz im naechsten Takt erneut."),
                    wichtig=True, klasse="KRITISCH")
        except OSError as exc:
            persistence_error = persistence_error or exc
            self._note_critical_persistence_fault(exc)
        if persistence_error is not None:
            raise persistence_error
        if trade_id:
            try:
                registry.clear_pending(symbol, "crypto", **domain)
            except OSError as exc:
                self._note_critical_persistence_fault(exc)
                raise
        return (position, trade_id) if trade_id else (None, None)

    def _note_critical_persistence_fault(self, exc) -> None:
        self._critical_persistence_fault = True
        self._accounting_write_fault = True
        self.bereitschaft.melde(
            "buchung_vollstaendig", False,
            "OKX_PERSISTENCE_UNCONFIRMED: Dauerhaftigkeit unbestaetigt; "
            "neue OKX-Einstiege gesperrt, Schutzabgleich bleibt erforderlich")
        logger.error("OKX_PERSISTENCE_UNCONFIRMED: %s errno=%s; "
                     "kein erneuter Kauf, native Schutzabfrage bleibt erhalten",
                     type(exc).__name__, getattr(exc, "errno", None))

    def _reconcile_pending_okx_orders(self, broker) -> list[dict]:
        registry = self._order_registry()
        abgeschlossen: list[dict] = []
        current_account = str(getattr(broker, "account_fingerprint", lambda: "")() or "")
        current_env = "DEMO" if bool(getattr(broker, "demo", False)) else "LIVE"
        for key, intent in registry.nicht_terminale().items():
            if str(intent.get("asset_type") or "").lower() != "crypto":
                continue
            if str(intent.get("broker") or "okx").lower() != "okx":
                continue
            if str(intent.get("role") or "ENTRY").upper() != "ENTRY":
                continue
            stored_account = str(intent.get("account_fingerprint") or "")
            stored_env = str(intent.get("environment") or intent.get("broker_environment") or "").upper()
            if not current_account or stored_account != current_account:
                continue
            if stored_env != current_env:
                continue
            fallback_symbol = (key.split(":", 1)[1].split("|", 1)[0]
                               if ":" in key else key.split("|", 1)[0])
            symbol = str(intent.get("symbol") or fallback_symbol).upper()
            result = broker.reconcile_order_evidence(intent)
            if result is None:
                continue
            decision_id = int(intent.get("decision_id") or 0)
            scope = {"broker": "okx", "environment": current_env,
                     "account_fingerprint": current_account, "decision_id": decision_id}
            record_order_result(
                decision_id, result, broker="okx", symbol=symbol,
                requested_qty=intent.get("qty"),
                requested_price=intent.get("signal_price"),
                currency=str(intent.get("trade_quote_ccy") or ""))
            order_ids = list(getattr(result, "order_ids", []) or [])
            if result.fill_evidence_complete and result.filled_quantity > 0:
                position, trade_id = self._persistiere_okx_entry(
                    symbol=symbol, decision_id=decision_id, ergebnis=result,
                    intent=intent, nachtraeglich=True)
                if trade_id and position is None:
                    abgeschlossen.append({"symbol": symbol, "trade_id": trade_id,
                                           "status": "ALREADY_CLOSED"})
                if position:
                    mark_execution(
                        decision_id, "FILLED", order_ids,
                        reference_id=position.client_order_id,
                        fill_price=position.einstieg, fill_qty=position.menge,
                        broker_paper=bool(position.paper))
                    abgeschlossen.append({"symbol": symbol, "trade_id": trade_id,
                                           "status": "FILLED"})
                continue
            if (result.terminal and result.fill_evidence_complete
                    and float(result.gross_filled_quantity or 0.0) <= 0):
                registry.register_orders(
                    order_ids, symbol, {**intent, "zustand": str(result.status).upper()},
                    asset_type="crypto")
                registry.clear_pending(symbol, "crypto", **scope)
                mark_execution(decision_id, str(result.status or "NOT_FILLED").upper(),
                               order_ids, reference_id=result.reference_id,
                               broker_paper=bool(result.paper))
                abgeschlossen.append({"symbol": symbol,
                                       "status": str(result.status).upper()})
                continue
            registry.setze_zustand(
                symbol, "AWAITING_FILL_EVIDENCE", "crypto", **scope,
                ord_id=(order_ids[0] if order_ids else ""),
                cl_ord_id=result.client_order_id or result.reference_id)
        return abgeschlossen

    def _recover_terminal_okx_entries(self, broker) -> list[dict]:
        """9.4.1-Entry mit falschem Terminalstatus exakt nachpruefen.

        9.4.1 entfernte einen als CANCELED gelesenen Intent sofort aus
        ``pending``. Ein spaeter sichtbarer echter Fill wurde daher nie wieder
        derselben ordId zugeordnet. Dieser einmalige Migrationspfad prueft nur
        dauerhaft registrierte Entry-Order-IDs des Bots; Symbol oder Kontosaldo
        sind kein Eigentumsbeweis.
        """
        registry = self._order_registry()
        repaired: list[dict] = []
        current_account = str(getattr(
            broker, "account_fingerprint", lambda: "")() or "")
        try:
            import trade_ledger
            open_trades = trade_ledger.offene_trades("okx")
        except Exception:
            open_trades = []
        open_by_order = {str(row.get("entry_order_id") or ""): row
                         for row in open_trades if row.get("entry_order_id")}
        now = time.time()
        max_age = 90 * 86400.0
        terminal = {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED",
                    "MMP_CANCELED", "NOT_FILLED", "FAILED"}
        for order_id, intent in registry.registered_orders(
                asset_type="crypto", broker="okx").items():
            if str(intent.get("identifier_type") or "").upper() == "FILL":
                continue
            # Ab 9.5 wird ein terminaler No-Fill bereits mit der korrigierten
            # Beweiskette ermittelt. Der Sonderlauf gilt nur fuer alte Belege.
            if int(intent.get("identity_schema") or 0) >= 2:
                continue
            role = str(intent.get("role") or "ENTRY").upper()
            if role != "ENTRY":
                continue
            state = str(intent.get("zustand") or "").upper()
            if state not in terminal or int(intent.get("v950_recovery_checked") or 0) >= 1:
                continue
            registered = float(intent.get("registered_at") or
                               intent.get("created_at_ts") or 0.0)
            if registered and now - registered > max_age:
                registry.update_order(order_id, v950_recovery_checked=1,
                                      v950_recovery_result="TOO_OLD")
                continue
            stored_account = str(intent.get("account_fingerprint") or "")
            if stored_account and current_account and stored_account != current_account:
                registry.update_order(order_id, v950_recovery_checked=1,
                                      v950_recovery_result="ACCOUNT_MISMATCH")
                continue
            symbol = str(intent.get("symbol") or "").upper()
            existing = self.buch.hole(symbol) if symbol else None
            if existing is not None:
                result = ("ALREADY_BOOKED" if str(existing.order_id or "") == order_id
                          else "SYMBOL_ALREADY_HAS_OTHER_ORDER")
                registry.update_order(order_id, v950_recovery_checked=1,
                                      v950_recovery_result=result)
                continue
            other_open = [row for oid, row in open_by_order.items()
                          if str(row.get("symbol") or "").upper() == symbol
                          and oid != order_id]
            if other_open:
                registry.update_order(order_id, v950_recovery_checked=1,
                                      v950_recovery_result="AMBIGUOUS_OPEN_LEDGER")
                continue
            proof = dict(intent)
            proof["ord_id"] = order_id
            result = broker.reconcile_order_evidence(proof)
            if result is None:
                # Ein temporaerer API-Fehler darf die einmalige Migration nicht
                # verbrennen; beim naechsten Takt erneut pruefen.
                continue
            decision_id = int(intent.get("decision_id") or 0)
            if (bool(getattr(result, "fill_evidence_complete", False))
                    and float(getattr(result, "filled_quantity", 0.0) or 0.0) > 0):
                position, trade_id = self._persistiere_okx_entry(
                    symbol=symbol, decision_id=decision_id, ergebnis=result,
                    intent=proof, nachtraeglich=True)
                if position is not None:
                    mark_execution(
                        decision_id, "FILLED", list(result.order_ids or []),
                        reference_id=position.client_order_id,
                        fill_price=position.einstieg, fill_qty=position.menge,
                        broker_paper=bool(position.paper))
                    record_order_result(
                        decision_id, result, broker="okx", symbol=symbol,
                        requested_qty=intent.get("qty"),
                        requested_price=intent.get("signal_price"),
                        currency=str(intent.get("trade_quote_ccy") or ""))
                    registry.update_order(
                        order_id, v950_recovery_checked=1,
                        v950_recovery_result="FILLED_RECOVERED",
                        zustand="FILLED", trade_id=trade_id,
                        fill_ids=list(result.fill_ids or []))
                    repaired.append({"symbol": symbol, "order_id": order_id,
                                     "trade_id": trade_id, "status": "FILLED_RECOVERED"})
                    self._melde(
                        f"Krypto {symbol}: ein in 9.4.1 falsch als nicht ausgefuehrt "
                        f"gefuehrter Kauf wurde ueber ordId {order_id} und echte "
                        "OKX-tradeId-Fills sicher rekonstruiert.",
                        wichtig=True, klasse="KRITISCH")
                    continue
            # A failed ledger write or incomplete/non-terminal broker reply is
            # NOT a negative fill proof. Leave this exact order researchable.
            if (bool(getattr(result, "terminal", False))
                    and bool(getattr(result, "fill_evidence_complete", False))
                    and float(getattr(result, "gross_filled_quantity", 0.0) or 0.0) <= 0
                    and float(getattr(result, "filled_quantity", 0.0) or 0.0) <= 0):
                registry.update_order(
                    order_id, v950_recovery_checked=1,
                    v950_recovery_result="NO_FILL_CONFIRMED")
        return repaired

    def _standard_timeframe_confirmation(self, instrument: Instrument,
                                         protokoll: Entscheidungsprotokoll) -> tuple[bool, str]:
        """Existing NEXUS 5m/1h confirmation, isolated from SampleStrategy."""
        broker = self.broker
        try:
            trend = broker.historie(
                instrument, "14 D",
                str(getattr(self.cfg, "CRYPTO_TREND_BAR_SIZE", "1 hour")), False)
            confirm = broker.historie(
                instrument, "1 D",
                str(getattr(self.cfg, "CRYPTO_CONFIRM_BAR_SIZE", "5 mins")), False)
        except BrokerFehler as exc:
            protokoll.blockiert(BROKER, f"Mehrzeitebenen-Daten fehlen: {exc}")
            return False, "5m/1h-Daten nicht verfuegbar"
        if trend is None or len(trend) < 55:
            protokoll.blockiert(TECHNIK, "zu wenige abgeschlossene 1h-Kerzen")
            return False, "1h-Trend nicht pruefbar"
        trend_close = trend["close"].astype(float)
        trend_sma20 = float(trend_close.rolling(20).mean().iloc[-1])
        trend_sma50 = float(trend_close.rolling(50).mean().iloc[-1])
        trend_ok = (float(trend_close.iloc[-1]) > trend_sma50
                    and trend_sma20 > trend_sma50)
        candle_times = dict(protokoll.daten.get("candle_timestamps") or {})
        candle_times["1h"] = str(trend.index[-1]) if len(trend) else None
        protokoll.messwerte(sma20_1h=trend_sma20, sma50_1h=trend_sma50,
                           candle_timestamps=candle_times)
        protokoll.ergaenze(
            TECHNIK, DAFUER if trend_ok else DAGEGEN, gewicht=0.45,
            detail="1h-Aufwaertstrend bestaetigt" if trend_ok else "1h-Trend nicht positiv",
            datenquelle="OKX abgeschlossene 1h-Kerzen")
        if not trend_ok:
            return False, "1h-Trendfilter"
        if confirm is None or len(confirm) < 24:
            protokoll.blockiert(TECHNIK, "zu wenige abgeschlossene 5m-Kerzen")
            return False, "5m-Bestaetigung nicht pruefbar"
        confirm_close = confirm["close"].astype(float)
        confirm_ok = (float(confirm_close.iloc[-1]) >
                      float(confirm_close.rolling(20).mean().iloc[-1])
                      and float(confirm_close.iloc[-1]) >= float(confirm_close.iloc[-4]))
        protokoll.ergaenze(
            TECHNIK, DAFUER if confirm_ok else DAGEGEN, gewicht=0.35,
            detail="5m-Impuls bestaetigt" if confirm_ok else "5m-Impuls fehlt",
            datenquelle="OKX abgeschlossene 5m-Kerzen")
        if not confirm_ok:
            return False, "5m-Bestaetigungsfilter"
        candle_times = dict(protokoll.daten.get("candle_timestamps") or {})
        candle_times["5m"] = str(confirm.index[-1]) if len(confirm) else None
        protokoll.messwerte(candle_timestamps=candle_times)
        return True, ""

    # -- Chancensuche -------------------------------------------------------
    def _scan_blockiert(self, grund: str, *, gate: str,
                        strategy_mode: str) -> dict:
        """Eine globale Kaufsperre als sichtbare Scanentscheidung ablegen.

        Diese Sperren greifen vor der Einzelpruefung eines Coins. Ohne einen
        eigenen Eintrag sah /decisions deshalb stundenlang unveraendert aus,
        obwohl der Fuenf-Minuten-Takt korrekt weiterlief.
        """
        text = str(grund or "globale Krypto-Kaufsperre")[:500]
        # v9.1: Eine unveraenderte Sperre wird nur EINMAL protokolliert, nicht
        # bei jeder Scanrunde. Bei 300 s Takt waren das 288 Zeilen pro Tag mit
        # symbol="OKX_SCAN"; sie verdraengten die echten Ablehnungsgruende aus
        # der Top-5-Liste des Dashboards -- waehrend die Seite daneben
        # behauptet, ein Scan ohne Signal erzeuge keinen Datensatz.
        schluessel = (str(gate or "scan_gate"), str(text or ""))
        neu = getattr(self, "_letzte_scan_sperre", None) != schluessel
        self._letzte_scan_sperre = schluessel
        try:
            if not neu:
                raise StopIteration
            from decision_journal import record_decision
            record_decision(
                symbol="OKX_SCAN", asset_type="crypto", broker="okx",
                paper=bool(getattr(self.cfg, "OKX_DEMO", True)),
                status="BLOCKED", blocked_by=str(gate or "scan_gate"),
                reason=text, execution_status="",
                strategy_mode=str(strategy_mode or ""),
                entry_strategy_mode=str(strategy_mode or ""),
                evaluated_filters=[{
                    "name": str(gate or "scan_gate"), "status": "BLOCKED",
                    "reason": text,
                }],
                not_evaluated_filters=[
                    "universe", "technical_signal", "costs", "order_execution"
                ],
            )
        except StopIteration:
            pass                      # unveraenderte Sperre, schon protokolliert
        except Exception:
            logger.debug("Globale OKX-Scanblockade nicht protokollierbar",
                         exc_info=True)
        return {"ok": True, "gescannt": 0, "strategy_mode": strategy_mode,
                "hinweis": text, "scan_blocked": True, "blocked_by": gate,
                "erneut_protokolliert": neu}

    def scan(self) -> dict:
        broker = self.broker
        if broker is None:
            return {"ok": False, "grund": "OKX nicht verbunden"}


        from crypto_strategy_mode import CRYPTO_PAUSED, current_mode
        strategy_mode = current_mode()
        if strategy_mode == CRYPTO_PAUSED:
            return self._scan_blockiert(
                "Krypto-Neueinstiege sind pausiert. eToro sowie OKX-Schutz, "
                "Reconciliation und positionsgebundene Ausstiege laufen weiter.",
                gate="crypto_strategy_mode", strategy_mode=strategy_mode)

        # Ungeklaerte Exposure sperrt neue Einstiege fail-closed, bis die
        # Herkunft geklaert ist. Verkaeufe und Schutzorders bleiben erlaubt.
        #
        # 9.5.8: Der Riegel gilt jetzt nur noch fuer die BETROFFENEN Werte.
        # Bis 9.5.7 kehrte der Scan hier fuer alle Coins um -- ein einziger
        # RESIDUAL-Posten legte den kompletten Kryptoeinstieg still, obwohl
        # die Klassifizierung genau wusste, um welche Waehrung es geht. Ist die
        # Herkunft der Sperre unbekannt (keine Waehrung benennbar), bleibt es
        # bei der globalen Sperre -- dann ist keine engere Aussage moeglich.
        if getattr(self, "_exposure_sperre", None):
            betroffen = [str(x).upper() for x in
                         (getattr(self, "_exposure_gesperrte_waehrungen", None) or [])]
            if not betroffen:
                return self._scan_blockiert(
                    "Neue Einstiege gesperrt: " + "; ".join(self._exposure_sperre),
                    gate="exposure_reconciliation", strategy_mode=strategy_mode)

        darf, grund = self.risiko.darf_kaufen("crypto")
        if not darf:
            return self._scan_blockiert(
                grund, gate="risk_gate", strategy_mode=strategy_mode)

        # Die SampleStrategy darf nicht indirekt durch eine GPT-bewertete
        # Attention-Reihenfolge beeinflusst werden. Wie eine Freqtrade-
        # Pairlist verarbeitet sie deshalb deterministisch alle aktuell durch
        # die harten OKX-/Liquiditaetsfilter freigegebenen Paare. Im normalen
        # NEXUS-Modus bleibt das bewaehrte, begrenzte Focus-Set unveraendert.
        from crypto_strategy_mode import FREQTRADE_SAMPLE
        if strategy_mode == FREQTRADE_SAMPLE:
            symbole = sorted(self.universum.handelbare_symbole("okx"))
        else:
            symbole = self.universum.focus_set("okx")
        offene = {p.symbol.upper() for p in self.buch.aktive()}
        # 9.5.4: Liegt im Konto eine Schutzorder ohne Position im Buch, ist
        # die Lage fuer diesen Wert ungeklaert. Ein Wiedereinstieg wuerde
        # gegen eine fremde, noch scharfe SL/TP-Order laufen: sie koennte den
        # neuen Bestand sofort mitverkaufen. Gesperrt wird gezielt dieses
        # Symbol -- der Rest des Universums handelt normal weiter.
        # 9.5.5: Erkannt wird instrumentgenau, GESPERRT wird nach
        # Basiswaehrung. Das ist kein Widerspruch: ein OKX-Spot-Guthaben ist
        # fungibel. Eine lebende Verkaufsorder auf ETH-EUR greift auf dasselbe
        # ETH zu, das ein Kauf auf ETH-USDC gerade beschafft hat -- sie koennte
        # den neuen Bestand sofort mitverkaufen.
        blockiert = {str(r.get("symbol") or "").upper()
                     for r in getattr(self, "_verwaiste_schutz", [])}
        if getattr(self, "_schutzorders_unlesbar", ""):
            # Nicht abrufbar heisst nicht "keine". Fail closed.
            return self._scan_blockiert(
                "Neue Einstiege gesperrt: Schutzorders im OKX-Konto sind "
                f"nicht abrufbar ({self._schutzorders_unlesbar}). Ohne diese "
                "Pruefung laesst sich nicht ausschliessen, dass eine fremde "
                "Verkaufsorder den neuen Bestand sofort mitverkauft.",
                gate="schutzorders_unlesbar", strategy_mode=strategy_mode)
        kandidaten = [s for s in symbole
                      if s.upper() not in offene and s.upper() not in blockiert]

        # Der Scan hat alle globalen Sperren passiert. Damit ist eine spaeter
        # erneut auftretende Sperre wieder eine echte Nachricht und wird auch
        # wieder protokolliert.
        self._letzte_scan_sperre = None

        bericht = {"ok": True, "gescannt": 0, "gekauft": [], "abgelehnt": {},
                   "strategy_mode": strategy_mode}
        for symbol in kandidaten:
            cutoff = getattr(self, "_scan_candle_cutoff", None)
            if strategy_mode == FREQTRADE_SAMPLE and cutoff is not None:
                from datetime import timedelta
                if self._freqtrade_cursor().seen(symbol, cutoff-timedelta(minutes=5)):
                    continue
            darf, grund = self.risiko.darf_kaufen("crypto")
            if not darf:
                bericht["hinweis"] = grund
                break
            bericht["gescannt"] += 1
            try:
                ergebnis = self.pruefe_kandidat(symbol)
            except Exception as exc:
                logger.exception("Kandidatenpruefung %s fehlgeschlagen", symbol)
                record_system_error(
                    symbol=symbol, asset_type="crypto", broker="okx",
                    gate="candidate_processing", exc=exc,
                    paper=bool(getattr(self.cfg, "OKX_DEMO", True)),
                )
                bericht["abgelehnt"][symbol] = f"Fehler: {type(exc).__name__}"
                continue
            if ergebnis.get("gekauft"):
                bericht["gekauft"].append(symbol)
            else:
                bericht["abgelehnt"][symbol] = ergebnis.get("grund", "")
                # 10.8.1: Eine Sperre VOR der Signalpruefung (Bestandsbeleg,
                # Exposure, Wiedereinstiegssperre, Bereitschaft, Universum)
                # gilt fuer die ganze Kerze. Bis 10.8.0 wurde der Cursor nur
                # nach KEIN_SIGNAL gesetzt; XRP lief am 19.09.2026 deshalb
                # 1055-mal in 2,5 Stunden in dieselbe Sperre -- alle 8 s eine
                # Entscheidung, 833 MB Journal. Jetzt: eine je Kerze.
                if (strategy_mode == FREQTRADE_SAMPLE and cutoff is not None
                        and ergebnis.get("vor_signal") and ergebnis.get("decision_id")):
                    try:
                        from datetime import timedelta
                        self._freqtrade_cursor().mark(
                            symbol, cutoff - timedelta(minutes=5),
                            int(ergebnis["decision_id"]))
                    except Exception:
                        logger.debug("Kerzencursor nach Vor-Signal-Sperre nicht gesetzt",
                                     exc_info=True)
        return bericht

    def pruefe_kandidat(self, symbol: str, *, _route: dict | None = None) -> dict:
        """Der vollstaendige Kaufweg fuer genau ein Instrument."""
        broker = self.broker
        protokoll = Entscheidungsprotokoll(symbol, asset_type="crypto", broker="okx")
        from crypto_strategy_mode import (
            CRYPTO_PAUSED, FREQTRADE_SAMPLE, NEXUS_STANDARD,
            current_mode, entry_snapshot,
        )
        strategy_mode = current_mode()
        if strategy_mode == CRYPTO_PAUSED:
            protokoll.blockiert(RISK_GATE, "Krypto-Neueinstiege per Laufzeitschalter pausiert")
            return self._abschluss(protokoll, "ABGELEHNT", "Krypto-Neueinstiege pausiert")
        strategy_identity = entry_snapshot(strategy_mode)
        protokoll.messwerte(
            entry_strategy_mode=strategy_mode,
            strategy_name=strategy_identity.get("strategy_name"),
            strategy_version=strategy_identity.get("strategy_version"),
            strategy_parameter_hash=strategy_identity.get("parameter_hash"),
            strategy_parameters=strategy_identity,
        )
        # Read existing local identity for refused-entry logbook evidence. This
        # neither fetches metadata nor selects an execution route or grants an
        # entry. Missing identity stays missing in historical/display grouping.
        try:
            local_member = self.universum.zustand.hole("okx", symbol)
            local_account = str(getattr(self.broker, "account_fingerprint", lambda: "")() or "")
            local_paper = getattr(self.broker, "demo", None)
            fields = {"signal_timeframe": strategy_identity.get("timeframe")}
            if local_account:
                fields["account_fingerprint"] = local_account
            if type(local_paper) is bool:
                fields.update(paper=local_paper, broker_environment="DEMO" if local_paper else "LIVE")
            if getattr(local_member, "inst_id", None):
                fields["inst_id"] = str(local_member.inst_id)
            protokoll.messwerte(**fields)
        except Exception:
            logger.debug("Lokaler Entscheidungskontext noch nicht vollstaendig")

        # 9.5.8: Exposure-Sperre symbolgenau. Der Scan laeuft jetzt weiter,
        # deshalb muss der Riegel hier fuer genau die betroffenen Werte stehen.
        # Ein ungeklaerter XLM-Bestand darf keinen SOL-Kauf mehr verhindern --
        # er darf aber sehr wohl einen zweiten XLM-Kauf verhindern.
        # 10.2.0: EXIT_IN_PROGRESS sperrt genau den betroffenen Wert.
        if symbol.upper() in (getattr(self, "_exit_in_progress_symbole", None) or set()):
            grund = (f"Eigener Schutz-Exit fuer {symbol.upper()} laeuft "
                     "(EXIT_IN_PROGRESS); kein Neueinstieg bis zur Abrechnung")
            protokoll.blockiert(RISK_GATE, grund)
            return self._abschluss(protokoll, "ABGELEHNT", grund)
        # 10.7.0: Jeder offene Bestands- oder Buchungsbeleg sperrt genau
        # seinen Coin -- nicht mehr die Domaene.
        if symbol.upper() in (getattr(self, "_gesperrte_symbole", None) or set()):
            grund = (f"{symbol.upper()}: offener Bestands-/Buchungsbeleg; kein Neueinstieg "
                     "in diesen Coin, andere Kaeufe frei")
            protokoll.blockiert(RISK_GATE, grund)
            return self._abschluss(protokoll, "ABGELEHNT", grund)

        betroffen = {str(x).upper() for x in
                     (getattr(self, "_exposure_gesperrte_waehrungen", None) or [])}
        if symbol.upper() in betroffen:
            grund = ("Ungeklaerter Bestand in " + symbol.upper()
                     + ": " + "; ".join(
                         g for g in (getattr(self, "_exposure_sperre", None) or [])
                         if g.upper().startswith(symbol.upper() + ":")))
            protokoll.blockiert(RISK_GATE, grund)
            return self._abschluss(protokoll, "ABGELEHNT", grund)

        # Ein manueller Verkauf sperrt den Basiswert unabhaengig vom
        # Quote-Markt. So kauft der naechste Scan ONDO nicht sofort erneut
        # ueber USD/USDC/EUR.
        try:
            import manual_trade_control
            account = str(getattr(broker, "account_fingerprint", lambda: "")() or "")
            lock_reason = manual_trade_control.lock_reason("okx", account, symbol)
        except Exception as exc:
            # v9.1: fail-CLOSED. Ein Lesefehler der Sperrdatei (Rechte, NFS,
            # defektes JSON) bedeutete bis 9.0.15 "keine Sperre" -- der Bot
            # kaufte den gerade manuell verkauften Coin sofort nach. An einem
            # Sicherheitsgate muss der Zweifel sperren, nicht freigeben.
            logger.warning("Wiedereinstiegssperre nicht lesbar: %s", exc, exc_info=True)
            lock_reason = ("Wiedereinstiegssperre nicht lesbar -- Einstieg "
                           "vorsorglich blockiert, bis die Datei wieder lesbar ist")
        if lock_reason:
            protokoll.blockiert(RISK_GATE, lock_reason)
            return self._abschluss(protokoll, "ABGELEHNT", lock_reason)

        # --- Anlaufsperre (v8.1.4) ------------------------------------------
        # Neue Einstiege warten, bis die Domaene handelsbereit ist UND die
        # Mindestwartezeit abgelaufen ist. Verkaeufe, Stops und Schutzorders
        # betrifft das nicht -- die laufen in pruefe_positionen() sofort.
        from okx_accounting import broker_status
        accounting = broker_status(broker)
        self.bereitschaft.melde('buchung_vollstaendig', accounting['complete'] and not (getattr(self,'_accounting_write_fault',False) or getattr(self,'_critical_persistence_fault',False)), accounting['detail'] if not (getattr(self,'_accounting_write_fault',False) or getattr(self,'_critical_persistence_fault',False)) else 'OKX-Buchungsschreibfehler; Abgleich erforderlich')
        self._melde_account_action()
        darf_kaufen, bereit_grund = self.bereitschaft.darf_kaufen()
        if not darf_kaufen:
            protokoll.blockiert(RISK_GATE, bereit_grund)
            return self._abschluss(protokoll, "ABGELEHNT", bereit_grund)
        # 10.7.0: frisch gelesen, nicht nur aus dem letzten Waechterlauf --
        # ein Beleg, der seit dem letzten Zyklus entstand, sperrt seinen Coin.
        if symbol.upper() in {str(s).upper() for s in (accounting.get('blocked_symbols') or [])}:
            grund = (f"{symbol.upper()}: offener Bestands-/Buchungsbeleg; kein Neueinstieg "
                     "in diesen Coin, andere Kaeufe frei")
            protokoll.blockiert(RISK_GATE, grund)
            return self._abschluss(protokoll, "ABGELEHNT", grund)

        mitglied = self.universum.zustand.hole("okx", symbol)
        if mitglied is None or not mitglied.handelbar:
            protokoll.blockiert(UNIVERSE, "nicht im handelbaren Universum")
            return self._abschluss(protokoll, "ABGELEHNT", "nicht im handelbaren Universum")
        # Ab 9.0.1 ist die Monatsliste nur Mitgliedschaft, keine Handelsfreigabe.
        # Eine fehlgeschlagene Monatsrotation behaelt deshalb die vorige Liste.
        # Sicherheit entsteht im Geldpfad aus der Anlaufsperre oben sowie den
        # nachfolgenden Kerzen-, Instrument-, Spread-, Kosten- und Risikogates.
        # This route belongs only to a new entry. A changed route runs the
        # complete signal/price/risk cascade again; existing trades stay fixed.
        try:
            route = (_route or broker.initial_entry_route(symbol, mitglied.inst_id)
                     if hasattr(broker, "initial_entry_route") else {})
        except BrokerFehler as exc:
            protokoll.blockiert(BROKER, str(exc))
            return self._abschluss(protokoll, "ABGELEHNT", str(exc))
        if route:
            from copy import copy
            mitglied = copy(mitglied)
            mitglied.inst_id = route["inst_id"]
        instrument = self._instrument(symbol, mitglied.inst_id)
        quote_ccy = instrument.currency
        try:
            trade_quote_ccy = route.get("trade_quote_ccy") or (broker.trade_quote_for_instrument(
                instrument, require_cash=True)
                if hasattr(broker, "trade_quote_for_instrument") else quote_ccy)
        except BrokerFehler as exc:
            protokoll.blockiert(BROKER, str(exc))
            return self._abschluss(protokoll, "ABGELEHNT", str(exc))
        protokoll.ergaenze(UNIVERSE, DAFUER, gewicht=0.2,
                           detail=f"Rang {mitglied.letzter_rang}, Score {mitglied.letzter_score:.3f}",
                           datenquelle="UniverseManager")
        protokoll.messwerte(
            universe_rank=getattr(mitglied, "letzter_rang", None),
            universe_score=getattr(mitglied, "letzter_score", None),
            currency=quote_ccy, trade_quote_ccy=trade_quote_ccy,
        )
        if strategy_mode == NEXUS_STANDARD and mitglied.ai_bewertung:
            protokoll.ki("luna", DAFUER if mitglied.ai_bewertung == "HIGH" else NEUTRAL,
                         f"Einstufung {mitglied.ai_bewertung}", modell=mitglied.ai_modell,
                         cache=True, gewicht=0.1)

        # --- Signal --------------------------------------------------------
        is_freqtrade = strategy_mode == FREQTRADE_SAMPLE
        from crypto_strategy_mode import ZUSATZ_MODES
        is_zusatz = strategy_mode in ZUSATZ_MODES
        if is_zusatz:
            import zusatz_strategien
            zusatz_spec = zusatz_strategien.STRATEGIEN[strategy_mode]
        try:
            if is_zusatz:
                # 10.2.0: Tagesstrategien lesen abgeschlossene Tageskerzen.
                df = broker.historie(instrument, str(zusatz_spec["history_duration"]),
                                     "1 day", False)
            else:
                df = self._freqtrade_history(instrument, cutoff=getattr(self, "_scan_candle_cutoff", None)) if is_freqtrade else broker.historie(
                    instrument,
                    str(getattr(self.cfg, "FREQTRADE_HISTORY_DURATION", "3 D")
                        if is_freqtrade else getattr(self.cfg, "CRYPTO_HISTORY_DURATION", "3 D")),
                    "5 mins" if is_freqtrade else
                    str(getattr(self.cfg, "CRYPTO_BAR_SIZE", "15 mins")),
                    False,
                )
        except BrokerFehler as exc:
            protokoll.blockiert(BROKER, f"Kursdaten nicht verfuegbar: {exc}")
            return self._abschluss(protokoll, "ABGELEHNT", "keine Kursdaten")
        # Eine gemeinsame Startkerzenzahl aus der festgelegten Referenzstrategie.
        if is_freqtrade:
            from freqtrade_sample_strategy import STARTUP_CANDLES
            minimum_candles = int(STARTUP_CANDLES)
        elif is_zusatz:
            minimum_candles = int(zusatz_spec["min_candles"])
        else:
            minimum_candles = 60
        if df is None or len(df) < minimum_candles:
            protokoll.blockiert(TECHNIK, f"zu wenige Kerzen ({0 if df is None else len(df)})")
            return self._abschluss(protokoll, "ABGELEHNT", "zu wenige Kerzen")

        if is_freqtrade and self._freqtrade_cursor().seen(symbol, df.index[-1]):
            return {"gekauft": False, "grund": "5m-Kerze bereits verarbeitet", "protokoll": protokoll.als_dict()}
        if is_zusatz and self._zusatz_cursor(strategy_mode).seen(symbol, df.index[-1]):
            return {"gekauft": False, "grund": "Tageskerze bereits verarbeitet", "protokoll": protokoll.als_dict()}

        if is_zusatz:
            from strategy import Signal
            try:
                bewertung = zusatz_strategien.bewerte(strategy_mode, df)
            except ValueError as exc:
                protokoll.blockiert(TECHNIK, str(exc))
                return self._abschluss(protokoll, "ABGELEHNT", str(exc))
            signal = Signal(
                "BUY" if bewertung.entry else "HOLD", bewertung.entry_reason,
                0.5, bewertung.close, bewertung.atr)
            protokoll.messwerte(
                atr=bewertung.atr,
                completed_candles_only=True,
                candle_timestamps={"1d": bewertung.candle_time},
                signal_reason=bewertung.entry_reason,
                signal_checks=dict(bewertung.indicators),
                enter_tag=f"zusatz_{strategy_mode.lower()}_entry",
            )
        elif is_freqtrade:
            from freqtrade_sample_strategy import evaluate
            from strategy import Signal
            try:
                sample = evaluate(df)
            except ValueError as exc:
                protokoll.blockiert(TECHNIK, str(exc))
                return self._abschluss(protokoll, "ABGELEHNT", str(exc))
            signal = Signal(
                "BUY" if sample.entry else "HOLD", sample.entry_reason,
                0.5, sample.close, sample.atr)
            protokoll.messwerte(
                rsi_5m=sample.rsi, tema_5m=sample.tema,
                bb_middle_5m=sample.bb_middle, volume_5m=sample.volume,
                atr=sample.atr,
                completed_candles_only=True,
                candle_timestamps={"5m": sample.candle_time},
                signal_reason=sample.entry_reason,
                signal_checks=dict(getattr(sample, "entry_checks", {}) or {}),
                candle_quality=dict(df.attrs.get("candle_quality", {})),
                enter_tag="freqtrade_sample_entry",
            )
        else:
            from strategy import generate_signal
            signal = generate_signal(df, None)
            try:
                from strategy import prepare
                ind15 = prepare(df)
                last15 = ind15.iloc[-1] if ind15 is not None and len(ind15) else {}
                protokoll.messwerte(
                    rsi_15m=last15.get("rsi") if hasattr(last15, "get") else None,
                    atr=getattr(signal, "atr", None),
                    completed_candles_only=True,
                    candle_timestamps={"15m": str(df.index[-1]) if len(df) else None},
                    signal_reason=getattr(signal, "reason", ""),
                    enter_tag=getattr(signal, "reason", ""),
                )
            except Exception:
                logger.debug("15m-Auditindikatoren nicht extrahierbar", exc_info=True)
        if signal.action != "BUY":
            protokoll.technik(DAGEGEN, 0.6, f"{signal.action}: {signal.reason}")
            return self._abschluss(
                protokoll, "KEIN_SIGNAL", f"Kein Kaufsignal: {signal.reason}")
        protokoll.technik(DAFUER, 0.6, signal.reason)

        if not is_freqtrade and not is_zusatz:
            # Die 15m/1h-Bestaetigung gehoert zum NEXUS-Standard. Tages- und
            # Freqtrade-Strategien bringen ihren eigenen Zeitrahmen mit.
            confirmed, confirmation_reason = self._standard_timeframe_confirmation(
                instrument, protokoll)
            if not confirmed:
                return self._abschluss(protokoll, "ABGELEHNT", confirmation_reason)

        preis = float(signal.price or 0.0)
        if preis <= 0:
            protokoll.blockiert(TECHNIK, "kein gueltiger Signalpreis")
            return self._abschluss(protokoll, "ABGELEHNT", "kein gueltiger Preis")

        # --- Live-Quote und Spanne ----------------------------------------
        quote = broker.latest_bid_ask(instrument) or {}
        bid, ask = float(quote.get("bid") or 0.0), float(quote.get("ask") or 0.0)
        from cost_engine import fallback_spread_pct, spread_pct_from_bid_ask
        spanne = spread_pct_from_bid_ask(bid, ask, fallback_spread_pct("crypto"))
        max_spanne = float(getattr(self.cfg, "MAX_SPREAD_CRYPTO_PCT", 0.006))
        marktqualitaet_ok = spanne <= max_spanne
        if ask > 0:
            preis = ask          # gekauft wird zum Briefkurs, nicht zum Schlusskurs
        protokoll.messwerte(price=preis, bid=bid, ask=ask, spread_pct=spanne,
                           spread_limit_pct=max_spanne, quote_source="OKX Ticker")
        protokoll.ergaenze(MARKT, DAFUER if marktqualitaet_ok else DAGEGEN, gewicht=0.3,
                           detail=f"Spanne {spanne * 100:.3f} % (Grenze {max_spanne * 100:.2f} %)",
                           datenquelle="OKX Ticker")

        # --- Schutzwerte und Menge ----------------------------------------
        if is_freqtrade:
            from freqtrade_sample_strategy import MINIMAL_ROI, STOPLOSS, roi_exit_price
            stop = preis * (1.0 + float(STOPLOSS))
            # Broker-side disaster protection uses the initial ROI target.
            # The 2%/1% time steps remain client-managed and are checked on
            # every position cycle from the immutable entry snapshot.
            ziel = roi_exit_price(
                preis, float(MINIMAL_ROI["0"]),
                entry_fee_pct=self._taker_satz(),
                exit_fee_pct=self._taker_satz())
        else:
            from risk_manager import calculate_stop_take
            stop, ziel = calculate_stop_take(preis, "BUY", signal.atr, "crypto")
        # Der Stop darf nie enger sitzen als die Handelskosten. Sonst ist ein
        # Stop-Out rechnerisch ein garantierter Verlust -- genau das ist am
        # 25.08.2026 zweimal innerhalb von 63 Sekunden passiert.
        stop, stop_hinweis = self._stop_mindestabstand(preis, stop, spanne, quote_ccy)
        if stop_hinweis:
            protokoll.ergaenze(RISK_GATE, NEUTRAL, gewicht=0.0, detail=stop_hinweis,
                               datenquelle="Kostenmodell OKX")
        quote_rate = (broker.quote_conversion_rate(quote_ccy, trade_quote_ccy)
                      if hasattr(broker, "quote_conversion_rate") else
                      1.0 if quote_ccy == trade_quote_ccy else None)
        if not quote_rate or quote_rate <= 0:
            grund = (f"keine beobachtete Umrechnung {quote_ccy}->{trade_quote_ccy}; "
                     "USD wird nicht mit USDC gleichgesetzt")
            protokoll.blockiert(RISK_GATE, grund)
            return self._abschluss(protokoll, "ABGELEHNT", grund)
        # Jeder Handelskanal traegt sein eigenes Risiko. Ein USD-Kauf darf
        # daher weder aus dem EUR- noch aus dem USDC-Guthaben dimensioniert
        # werden. Der globale OKX-Topf begrenzt weiterhin Tagesverlust und
        # Anzahl der Positionen; die konkrete Ordergroesse basiert jedoch
        # ausschliesslich auf dem freien Cash ihres tradeQuoteCcy-Kanals.
        lane_cash = broker.verfuegbares_cash(trade_quote_ccy)
        if lane_cash is None or lane_cash <= 0:
            grund = f"freies {trade_quote_ccy}-Guthaben nicht abrufbar oder null"
            protokoll.blockiert(RISK_GATE, grund)
            return self._abschluss(protokoll, "ABGELEHNT", grund)
        # v9.3: Schwebende eigene Kaeufe derselben Quote-Lane abziehen. Sonst
        # sieht Kandidat B dasselbe Geld, das Kandidat A bereits gebunden hat
        # -- solange die Belastung bei OKX noch nicht verbucht ist. Die Lanes
        # bleiben getrennt: eine EUR-Reservierung blockiert kein USDC.
        gebunden = self._order_registry().lane_reservierung(
            trade_quote_ccy, asset_type="crypto", ausser_symbol=symbol)
        if gebunden > 0:
            puffer = 1.0 + max(0.0, float(getattr(
                self.cfg, "RESERVIERUNG_PUFFER_PCT", 0.01)))
            lane_cash = max(0.0, lane_cash - gebunden * puffer)
            protokoll.messwerte(
                lane_reserviert=round(gebunden * puffer, 8),
                lane_capital_nach_reservierung=round(lane_cash, 8))
            if lane_cash <= 0:
                grund = (f"freies {trade_quote_ccy}-Guthaben vollstaendig durch "
                         "ungeklaerte eigene Kaeufe gebunden")
                protokoll.blockiert(RISK_GATE, grund)
                return self._abschluss(protokoll, "ABGELEHNT", grund)
        menge, groessen_grund = self.topf.positionsgroesse(
            preis * quote_rate, stop * quote_rate, asset_type="crypto",
            kontowert_override=lane_cash)
        menge = self._auf_lot_runden(symbol, menge, inst_id=mitglied.inst_id)
        topf_status = self.topf.uebersicht()
        protokoll.messwerte(
            stop=stop, take=ziel, planned_qty=menge,
            equity_before=topf_status.get("kontowert"),
            lane_capital_before=lane_cash,
            lane_currency=trade_quote_ccy,
            daily_pnl=topf_status.get("tages_pnl"),
            open_positions=topf_status.get("offene_positionen"),
            trades_today=topf_status.get("trades_heute"),
            cooldown_active=topf_status.get("abkuehlung_aktiv"),
            # 10.6.0: Der Einsatz ist je Broker einstellbar. Welche Stufe
            # gerechnet hat, steht damit in jedem Beleg.
            einsatz_stufe=topf_status.get("einsatz_stufe"),
            einsatz_quelle=topf_status.get("einsatz_quelle"),
            einsatz_risiko_pro_trade_pct=topf_status.get("risiko_pro_trade_pct"),
            einsatz_max_position_pct=topf_status.get("max_position_pct"),
            risk_amount=(abs(preis - stop) * quote_rate * menge
                         if menge > 0 else None),
        )
        protokoll.ergaenze(RISK_GATE, DAFUER if menge > 0 else DAGEGEN, gewicht=0.4,
                           detail=groessen_grund, datenquelle="Risikotopf OKX")

        # --- Cash-Reserve: verkleinern statt blockieren --------------------
        # Bis 8.1.2 pruefte der Bot nur "passt die geplante Order ins freie
        # Cash?" und liess bei Nein die GESAMTE Order fallen. Eine kleinere,
        # regelkonforme Order war damit unmoeglich. Jetzt wird zuerst
        # verkleinert und erst danach abgelehnt, wenn selbst die kleinste
        # zulaessige Order nicht mehr passt.
        geplante_menge = float(menge)
        menge, cash_hinweis, cash_grund = self._menge_an_cash_anpassen(
            symbol, menge, preis * quote_rate, trade_quote_ccy, inst_id=mitglied.inst_id)
        try:
            protokoll.messwerte(
                cash_before=broker.verfuegbares_cash(trade_quote_ccy),
                market_quote_ccy=quote_ccy,
                trade_quote_ccy=trade_quote_ccy,
                quote_conversion_rate=quote_rate)
        except Exception:
            logger.debug("Cash-Auditwert nicht abrufbar", exc_info=True)
        cash_ok = menge > 0
        if cash_hinweis:
            protokoll.ergaenze(RISK_GATE, NEUTRAL, gewicht=0.0, detail=cash_hinweis,
                               datenquelle="Cash-Reserve OKX")
            # Bewusst KEINE Telegram-Meldung an dieser Stelle. Der Kandidat
            # muss erst die restliche Kaufkaskade bestehen. Sonst kaeme bei
            # knappem Guthaben je Scan eine Nachricht pro geprueftem Coin --
            # fuer Kaeufe, die danach ohnehin nicht stattfinden. Gemeldet
            # wird die Verkleinerung erst beim tatsaechlichen Kauf.
        if not cash_ok:
            protokoll.ergaenze(RISK_GATE, DAGEGEN, gewicht=0.4, detail=cash_grund,
                               datenquelle="Cash-Reserve OKX")

        # Equal-quantity, fee-inclusive VWAP comparison before any order. A
        # missing measurement cannot masquerade as a cost advantage.
        if menge > 0 and hasattr(broker, "compare_entry_routes"):
            try:
                comparison = broker.compare_entry_routes(symbol, menge, self.cfg,
                    reserved=lambda lane: self._order_registry().lane_reservierung(
                        lane, asset_type="crypto", ausser_symbol=symbol))
                protokoll.messwerte(entry_route_comparison=comparison)
                same = (comparison["inst_id"] == mitglied.inst_id and
                        comparison["trade_quote_ccy"] == trade_quote_ccy)
                if not same:
                    if _route is not None:
                        raise BrokerFehler("Marktwahl nach erneuter Dimensionierung instabil; neuer Scan erforderlich")
                    self._abschluss(protokoll, "ROUTE_RECHECK", comparison["reason"])
                    return self.pruefe_kandidat(symbol, _route=comparison)
            except BrokerFehler as exc:
                protokoll.blockiert(BROKER, str(exc))
                return self._abschluss(protokoll, "ABGELEHNT", str(exc))

        # --- Orderbuchtiefe -------------------------------------------------
        # Am 25.08.2026 bestellte der Bot 46,9 SOL und bekam 0,50 -- OKX
        # stornierte den Rest, weil der geschaetzte Ausfuehrungspreis den
        # besten Kurs um mehr als 5 % verfehlte. Was ausgefuehrt wurde, wurde
        # zu einem Preis ausgefuehrt, den der Markt sofort wieder verliess.
        if cash_ok:
            menge_vor_buchtiefe = menge
            menge, buch_hinweis = self._menge_an_orderbuch_anpassen(
                symbol, mitglied, menge, quote_ccy)
            cash_ok = menge > 0
            if buch_hinweis:
                protokoll.ergaenze(MARKT, NEUTRAL, gewicht=0.0, detail=buch_hinweis,
                                   datenquelle="OKX Orderbuch")
            if not cash_ok:
                cash_grund = buch_hinweis or "Orderbuch zu duenn"
                protokoll.ergaenze(MARKT, DAGEGEN, gewicht=0.4, detail=cash_grund,
                                   datenquelle="OKX Orderbuch")
            elif menge != menge_vor_buchtiefe and hasattr(broker, "compare_entry_routes"):
                try:
                    comparison = broker.compare_entry_routes(symbol, menge, self.cfg,
                        reserved=lambda lane: self._order_registry().lane_reservierung(
                            lane, asset_type="crypto", ausser_symbol=symbol))
                    protokoll.messwerte(entry_route_comparison=comparison)
                    if (comparison["inst_id"] != mitglied.inst_id
                            or comparison["trade_quote_ccy"] != trade_quote_ccy):
                        raise BrokerFehler("Verkleinerte Order veraendert die Marktwahl; naechsten frischen Kandidatenzyklus abwarten")
                except BrokerFehler as exc:
                    protokoll.blockiert(BROKER, str(exc))
                    return self._abschluss(protokoll, "ABGELEHNT", str(exc))

        # --- Kosten --------------------------------------------------------
        # Bewusst NACH der Cash-Anpassung: die Kostenrechnung haengt an der
        # Menge. Eine verkleinerte Order darf nicht mit der Kostenschaetzung
        # der urspruenglich geplanten Groesse freigegeben werden.
        from cost_engine import estimate_roundtrip
        # Mit Menge 0 liefert das Kostenmodell eine Fantasiehuerde (Division
        # durch ein Nullvolumen) und schriebe sie ins Entscheidungsjournal.
        # Ist die Menge ohnehin abgelehnt, wird die Referenzmenge gerechnet
        # und der Beitrag klar als nicht anwendbar gekennzeichnet.
        kosten = estimate_roundtrip(menge if cash_ok else geplante_menge, preis,
                                    asset_type="crypto", currency=quote_ccy,
                                    fee_pct=self._taker_satz(),
                                    bid=bid, ask=ask, broker="okx")
        erwartete_bewegung = abs(ziel - preis) / preis if preis > 0 else 0.0
        # required_edge_pct enthaelt bereits Kostenmultiplikator und
        # Sicherheitsmarge -- genau dieselbe Huerde wie auf der Aktienseite.
        netto_edge_ok = erwartete_bewegung > float(kosten.required_edge_pct)
        protokoll.messwerte(
            final_qty=menge, position_value=(menge * preis if menge > 0 else None),
            fees_estimated=(kosten.buy_commission + kosten.sell_commission),
            cost_pct=getattr(kosten, "total_cost_pct", None),
            required_edge_pct=kosten.required_edge_pct,
            expected_move_pct=erwartete_bewegung,
            net_edge_pct=(erwartete_bewegung - kosten.required_edge_pct),
        )
        protokoll.ergaenze(BROKER, DAFUER if netto_edge_ok else DAGEGEN, gewicht=0.5,
                           detail=(f"erwartete Bewegung {erwartete_bewegung * 100:.2f} % gegen "
                                   f"Kostenhuerde {kosten.required_edge_pct * 100:.2f} % "
                                   f"(davon Gebuehren "
                                   f"{(kosten.buy_commission + kosten.sell_commission):.4f} {quote_ccy})"),
                           datenquelle="Cost Engine (OKX-Gebuehren)")

        # --- Deterministische Endpruefung ----------------------------------
        # Einmal lesen, zweimal verwenden: die Liste geht in die Doppelorder-
        # Pruefung UND in die Positionszahl. Zwei getrennte Lesevorgaenge
        # koennten sich widersprechen.
        offene_order_symbole = self._offene_order_symbole()
        from candidate_gate import bewerte_kandidat
        entscheidung = bewerte_kandidat(
            state_allows_buy=self._zustand_erlaubt_kauf(),
            broker_online=broker.is_connected(),
            instrument_identity_ok=self._identitaet_ok(symbol, inst_id=mitglied.inst_id),
            market_open=True,                       # Krypto handelt immer
            # v9.2: Eine pausierte oder manuell verwaltete Botposition sperrt
            # den Wiedereinstieg genauso wie eine automatisch verwaltete.
            # Vorher meldete eine BEOBACHTEN-Position hier "keine Position
            # offen" -- der Bot haette denselben Coin ein zweites Mal gekauft.
            # Der Buchschluessel ist der Basiswert, damit sperrt LINK auch
            # LINK-EUR, LINK-USD und LINK-USDT.
            position_already_open=bool(
                self.buch.hole(symbol) and self.buch.hole(symbol).blocks_reentry),
            # 9.5.8: Seit die Bereitschaftsampel nicht mehr global sperrt, ist
            # DIESES Gate der einzige Riegel gegen einen zweiten Kauf desselben
            # Werts -- und es muss deshalb auch den Fall "Register unlesbar"
            # tragen. Der Platzhalter passt auf kein echtes Symbol; ohne die
            # ausdrueckliche Pruefung waere die Sperre in genau dem Fall offen,
            # fuer den sie eingefuehrt wurde.
            duplicate_open_order=(symbol.upper() in offene_order_symbole
                                  or "<REGISTER-UNLESBAR>" in offene_order_symbole
                                  or broker.hat_offene_order(instrument, "BUY")),
            risk_allows_buy=self.risiko.darf_kaufen("crypto")[0],
            portfolio_allows_buy=self._portfolio_erlaubt(
                zusaetzlich_belegt=len([s for s in offene_order_symbole
                                        if not s.startswith("<")])),
            cash_allows_buy=cash_ok,
            market_quality_ok=marktqualitaet_ok,
            cost_quote_ok=True,
            net_edge_ok=netto_edge_ok,
            quantity=menge, price=preis, stop=stop, take_profit=ziel)

        if not entscheidung.approved:
            protokoll.blockiert(CANDIDATE_GATE, f"{entscheidung.blocked_by}: {entscheidung.reason}")
            return self._abschluss(protokoll, "ABGELEHNT", entscheidung.reason)

        # --- GPT Second Opinion (v8.2) --------------------------------------
        # GANZ AM ENDE, wenn alles Deterministische bestanden ist. Die KI
        # bleibt eine neutrale Warn- und Dokumentationsquelle. Ist das Human
        # Gate aktiv, wartet allein Georgs zeitlich begrenzte Freigabe; danach
        # prueft der naechste Scan die gesamte feste Kaskade erneut.
        human_gate_ok = True
        if not is_freqtrade:
            human_gate_ok = self._second_opinion(
                symbol, protokoll, preis=preis, menge=menge, stop=stop, ziel=ziel,
                waehrung=quote_ccy, signal=signal, kosten=kosten,
                erwartete_bewegung=erwartete_bewegung, mitglied=mitglied)
        if not human_gate_ok:
            return self._abschluss(
                protokoll, "PENDING_HUMAN_APPROVAL",
                "kritische KI-Warnung wartet auf persoenliche Freigabe")

        # --- Order ---------------------------------------------------------
        # Die Entscheidung MUSS vor dem Netzwerk-Submit dauerhaft existieren.
        # So kann auch UNKNOWN_AFTER_SUBMIT niemals eine verwaiste Order ohne
        # Herkunft erzeugen.
        from decision_journal import record_decision
        ready_payload = self._decision_payload(
            protokoll, status="APPROVED", reason="alle deterministischen Gates bestanden",
            paper=bool(getattr(self.cfg, "OKX_DEMO", True)))
        ready_payload["execution_status"] = "READY_TO_SUBMIT"
        decision_id = record_decision(**ready_payload)
        if decision_id is None:
            protokoll.blockiert(CANDIDATE_GATE, "Entscheidung konnte nicht persistent verknuepft werden")
            return self._abschluss(protokoll, "SYSTEM_ERROR", "Audit-Persistenz fehlgeschlagen")
        inst_id = str(
            getattr(getattr(instrument, "contract", None), "localSymbol", "")
            or mitglied.inst_id or normalize_inst_id(symbol, quote_ccy)).upper()
        cl_ord_id = client_order_id_for_decision(decision_id)
        order_tag = str(getattr(self.cfg, "OKX_ORDER_TAG", "NEXUS"))[:16]
        intent = {
            "broker": "okx", "asset_type": "crypto", "symbol": symbol.upper(),
            "role": "ENTRY",
            "identity_schema": 2,
            "decision_id": int(decision_id), "zustand": "SUBMITTING",
            "inst_id": inst_id, "qty": float(menge),
            "signal_price": float(preis), "stop": float(stop), "take": float(ziel),
            "cl_ord_id": cl_ord_id, "client_order_id": cl_ord_id,
            "order_tag": order_tag, "trade_quote_ccy": trade_quote_ccy,
            "account_fingerprint": str(getattr(
                broker, "account_fingerprint", lambda: "")() or ""),
            # 9.5.2: Umgebung ausdruecklich mitschreiben. Ohne sie kann eine
            # DEMO-Order eine LIVE-Abfrage sperren und umgekehrt.
            "environment": ("DEMO" if bool(getattr(broker, "demo", False)) else "LIVE"),
            "strategy_mode": strategy_mode, "strategy_identity": strategy_identity,
            "order_created_at": datetime.now(timezone.utc).isoformat(),
            "enter_tag": ("freqtrade_sample_entry" if is_freqtrade else
                          f"zusatz_{strategy_mode.lower()}_entry" if is_zusatz else
                          str(getattr(signal, "reason", "") or "")[:160]),
            "marktphase": self._marktphase(),
        }
        registry = self._order_registry()
        # Vor dem ersten Netzwerkbyte dauerhaft schreiben. Derselbe
        # Entscheidungsschluessel erzeugt immer dieselbe clOrdId.
        registry.register_pending(symbol, intent, asset_type="crypto")
        if is_freqtrade:
            # Intent first: a crash on either side cannot cause a second order
            # from this candle. Existing reconciliation resolves that intent.
            self._freqtrade_cursor().mark(symbol, sample.candle_time, decision_id)
        elif is_zusatz:
            # Gleiche Absicherung fuer Tagesstrategien: eine Tageskerze darf
            # hoechstens einen Kaufversuch ausloesen.
            self._zusatz_cursor(strategy_mode).mark(symbol, bewertung.candle_time, decision_id)
        scope = {k: intent[k] for k in ("broker", "environment", "account_fingerprint", "decision_id")}
        mark_execution(decision_id, "SUBMITTING", [], reference_id=cl_ord_id)
        broker._nexus_submit_context = {
            "decision_id": int(decision_id), "client_order_id": cl_ord_id,
            "order_tag": order_tag, "trade_quote_ccy": trade_quote_ccy,
            "strategy_mode": strategy_mode, "signal_instrument": inst_id,
            "signal_candle": (sample.candle_time if is_freqtrade else
                              bewertung.candle_time if is_zusatz else ""),
        }
        try:
            ergebnis = broker.kaufe_mit_absicherung(instrument, menge, preis, stop, ziel)
        except OrderStatusUnklar as exc:
            exc_intent = dict(getattr(exc, "intent", {}) or {})
            ord_ids = list(getattr(exc, "order_ids", []) or [])
            registry.setze_zustand(
                symbol, "UNKNOWN_AFTER_SUBMIT", "crypto", **scope,
                ord_id=(ord_ids[0] if ord_ids else exc_intent.get("ord_id", "")),
                cl_ord_id=getattr(exc, "reference_id", "") or cl_ord_id)
            mark_execution(decision_id, "UNKNOWN_AFTER_SUBMIT",
                           ord_ids, reference_id=getattr(exc, "reference_id", "") or cl_ord_id)
            protokoll.ergaenze(BROKER, NEUTRAL, detail=f"Transportzustand unklar: {exc}")
            self._melde(f"Krypto {symbol}: Kaufzustand UNKLAR (clOrdId "
                        f"{getattr(exc, 'reference_id', '') or cl_ord_id}). Kein zweiter "
                        "Kauf; NEXUS klaert dieselbe Order im Hintergrund ueber OKX.",
                        wichtig=True, klasse="KRITISCH")
            return self._abschluss(protokoll, "UNKLAR", "Transportzustand nach Kauf unklar")
        except BrokerFehler as exc:
            registry.setze_zustand(symbol, "REJECTED", "crypto", **scope)
            registry.clear_pending(symbol, "crypto", **scope)
            mark_execution(decision_id, "FAILED", [])
            protokoll.blockiert(BROKER, str(exc))
            return self._abschluss(protokoll, "FEHLGESCHLAGEN", str(exc))
        finally:
            broker._nexus_submit_context = {}

        gefuellt = float(ergebnis.filled_quantity or 0.0)
        # v9.3: Entscheidung und Ausfuehrung sind zwei getrennte Zustaende.
        # "SUBMITTED_NOT_FILLED" sagte nicht, WARUM nichts passiert ist --
        # beim WLD-Beispiel (FOK, 0/270,94 storniert) stand in der
        # Entscheidungsansicht weiter APPROVED, was wie ein erfolgreicher
        # Kauf aussieht.
        execution_status = getattr(ergebnis, "execution_status", None) or (
            "FILLED" if gefuellt > 0 else "SUBMITTED_NOT_FILLED")
        mark_execution(
            decision_id, execution_status, getattr(ergebnis, "order_ids", []) or [],
            reference_id=str(getattr(ergebnis, "reference_id", "") or ""),
            fill_price=getattr(ergebnis, "avg_fill_price", None),
            fill_qty=getattr(ergebnis, "filled_quantity", None),
            broker_paper=bool(getattr(ergebnis, "paper", True)),
        )
        record_order_result(
            decision_id, ergebnis, broker="okx", symbol=symbol,
            requested_qty=menge, requested_price=preis, currency=quote_ccy,
        )
        if gefuellt <= 0:
            if (bool(getattr(ergebnis, "terminal", False))
                    and bool(getattr(ergebnis, "fill_evidence_complete", False))):
                registry.register_orders(
                    getattr(ergebnis, "order_ids", []) or [], symbol,
                    {**intent, "zustand": str(ergebnis.status).upper()},
                    asset_type="crypto")
                registry.clear_pending(symbol, "crypto", **scope)
            else:
                registry.setze_zustand(
                    symbol, "AWAITING_FILL_EVIDENCE", "crypto", **scope,
                    ord_id=str(next(iter(ergebnis.order_ids or []), "")))
            beleg = dict(getattr(ergebnis, "execution_evidence", {}) or {})
            grund = self._nichtausfuehrungs_text(ergebnis, beleg)
            protokoll.messwerte(
                execution_status=execution_status,
                final_status=getattr(ergebnis, "final_status", "UNKLAR"),
                broker_cancel_source=str(beleg.get("cancel_source") or ""),
                broker_s_code=str(beleg.get("s_code") or ""),
                broker_limit_price=beleg.get("limit_price"),
                broker_filled_qty=beleg.get("filled_qty"),
                broker_requested_qty=beleg.get("requested_qty"))
            protokoll.ergaenze(BROKER, DAGEGEN, detail=grund)
            return self._abschluss(protokoll, "NICHT_AUSGEFUEHRT", grund)

        position, trade_id = self._persistiere_okx_entry(
            symbol=symbol, decision_id=decision_id, ergebnis=ergebnis, intent=intent)
        if position is None:
            registry.setze_zustand(
                symbol, "AWAITING_FILL_EVIDENCE", "crypto", **scope,
                ord_id=str(next(iter(ergebnis.order_ids or []), "")),
                cl_ord_id=cl_ord_id)
            return self._abschluss(
                protokoll, "UNKLAR",
                "Order ausgefuehrt, echte OKX-tradeId-Beweiskette noch unvollstaendig")
        kurs = position.einstieg

        protokoll.ergaenze(BROKER, DAFUER, gewicht=1.0,
                           detail=(f"{gefuellt:g} zu {kurs:g}; Broker-Schutz "
                                   f"{'ja' if ergebnis.stop_order_platziert else 'nein'}"))
        # Einheitliche Kaufmeldung. Die Verkleinerungen (Cash-Reserve,
        # Orderbuchtiefe) stehen als Hinweis darin -- gemeldet wird beim
        # tatsaechlichen Kauf, einmal, mit den echten Zahlen.
        import meldungen
        hinweise = [h for h in (cash_hinweis, locals().get("buch_hinweis", ""),
                                locals().get("stop_hinweis", "")) if h]
        markt_quote = self._quote_waehrung(position) or quote_ccy
        abrechnung = str(position.trade_quote_ccy or "").upper()
        if abrechnung and abrechnung != str(markt_quote).upper():
            hinweise.append(f"Abgerechnet in {abrechnung}; Kurse stehen in {markt_quote}.")
        self._melde(meldungen.kauf(
            broker="okx", symbol=symbol, menge=gefuellt, preis=kurs,
            # v9.1: Preis, Stop und Ziel stehen in der MARKTQUOTE des instId
            # (BTC-USD -> USD). Etikettiert wurde bis 9.0.15 die
            # Abrechnungswaehrung -- die Meldung sagte dann "50 000.00 EUR",
            # waehrend das Ledger dieselben Zahlen als USD verbucht. Die
            # Abrechnungswaehrung steht jetzt als eigener Hinweis dabei.
            waehrung=self._quote_waehrung(position) or quote_ccy,
            gebuehr=getattr(ergebnis, "fees_quote", None),
            gebuehr_pct=self._taker_satz(), stop=stop, ziel=ziel,
            grund=getattr(signal, "reason", "") or "technisches Einstiegssignal",
            geplant=geplante_menge, schutz=bool(ergebnis.stop_order_platziert),
            hinweise=hinweise), wichtig=True)
        return self._abschluss(protokoll, "AUSGEFUEHRT", "gekauft", gekauft=True,
                               execution=ergebnis)

    def _marktphase(self) -> str:
        """Marktphase fuer die spaetere Auswertung -- oder leer.

        Bewusst nicht geraten: eine falsche Phase verfaelscht die Auswertung
        nach Marktphase still. Leer heisst "nicht bekannt".
        """
        try:
            from market_regime import letzter_bekannter
            # NUR lesen: der Kryptopfad darf keinen Marktdaten-Download
            # ausloesen und im Kaufpfad nicht auf das Netz warten.
            return letzter_bekannter()
        except Exception:
            logger.debug("Marktphase nicht ermittelbar", exc_info=True)
            return ""

    # -- Hilfsmittel --------------------------------------------------------
    def _abschluss(self, protokoll: Entscheidungsprotokoll, ergebnis: str, grund: str,
                   *, gekauft: bool = False, execution=None) -> dict:
        protokoll.abschliessen(ergebnis, grund)
        source_payload = protokoll.als_dict()
        decision_id = None
        try:
            from decision_journal import record_decision
            status = ("APPROVED" if gekauft else
                      "BLOCKED" if str(ergebnis).upper() == "ABGELEHNT" else
                      "NO_SIGNAL" if str(ergebnis).upper() == "KEIN_SIGNAL" else
                      "UNKNOWN_AFTER_SUBMIT" if str(ergebnis).upper() == "UNKLAR" else
                      str(ergebnis).upper())
            payload = self._decision_payload(
                protokoll, status=status, reason=grund,
                paper=bool(getattr(execution, "paper", True)))
            payload["execution_status"] = status
            if execution is not None:
                payload.update({
                    "execution_status": str(getattr(execution, "status", "")),
                    "order_ids": list(getattr(execution, "order_ids", []) or []),
                    "reference_id": str(getattr(execution, "reference_id", "") or ""),
                    "filled_quantity": float(getattr(execution, "filled_quantity", 0.0) or 0.0),
                    "avg_fill_price": float(getattr(execution, "avg_fill_price", 0.0) or 0.0),
                })
            decision_id = record_decision(**payload)
            speichere_quellen(source_payload, decision_id=decision_id or protokoll.decision_id)
            if str(ergebnis).upper() == "KEIN_SIGNAL" and protokoll.daten.get("rsi_5m") is not None:
                candle = (protokoll.daten.get("candle_timestamps") or {}).get("5m")
                if candle and decision_id:
                    self._freqtrade_cursor().mark(protokoll.symbol, candle, decision_id)
        except Exception:
            logger.debug("Entscheidungsjournal nicht schreibbar", exc_info=True)
        # 10.8.1: Der Scan braucht die Entscheidungs-ID und die Information,
        # ob die Sperre VOR der Signalpruefung fiel (dann gibt es noch keine
        # Kerzenzeit im Protokoll). Nur so kann er den Kerzencursor setzen.
        return {"gekauft": gekauft, "grund": grund, "protokoll": protokoll.als_dict(),
                "decision_id": decision_id,
                "vor_signal": "candle_timestamps" not in protokoll.daten}

    @staticmethod
    def _decision_payload(protokoll: Entscheidungsprotokoll, *, status: str,
                          reason: str, paper: bool) -> dict:
        source_payload = protokoll.als_dict()
        payload = {
            "decision_id": protokoll.decision_id,
            "symbol": protokoll.symbol, "asset_type": "crypto", "broker": "okx",
            "approved": str(status).upper() == "APPROVED", "status": str(status).upper(),
            "paper": bool(paper),
            "blocked_by": (protokoll.blockierer[0].quelle if protokoll.blockierer else ""),
            "reason": reason, "hauptquelle": protokoll.hauptquelle(),
            "sources": source_payload.get("quellen", []),
        }
        payload.update(dict(protokoll.daten))
        return payload

    def _auf_lot_runden(self, symbol: str, menge: float, *, inst_id: str = "") -> float:
        broker = self.broker
        if broker is None or menge <= 0:
            return 0.0
        try:
            member = self.universum.zustand.hole("okx", symbol)
            inst_id = str(inst_id or getattr(member, "inst_id", "") or normalize_inst_id(symbol, broker.quote_ccy))
            meta = broker.client.instrument(inst_id)
        except BrokerFehler:
            # Fail closed. Vorher kam die Menge UNGERUNDET zurueck -- also
            # weder auf dem Lotraster noch gegen minSz geprueft. OKX lehnt
            # so eine Order ab; schlimmer, sie wandert ungeprueft durch die
            # Cash-Rechnung. Ohne Instrumentdaten wird nicht gekauft.
            logger.warning("Lotgroesse fuer %s nicht abrufbar -- kein Kauf.", symbol)
            return 0.0
        if meta is None:
            return 0.0
        from broker.okx import quantize_down
        gerundet = quantize_down(menge, meta.lot_size)
        return gerundet if gerundet >= float(meta.min_size or 0) else 0.0

    def _zustand_erlaubt_kauf(self) -> bool:
        try:
            from bot_zustand import BotZustand
            return bool(BotZustand().darf_kaufen())
        except Exception:
            # Kein lesbarer Zustand heisst: nicht kaufen. Im Zweifel gilt
            # immer die sichere Seite.
            logger.warning("Botzustand nicht lesbar -- Kauf wird blockiert.", exc_info=True)
            return False

    def _identitaet_ok(self, symbol: str, *, inst_id: str = "") -> bool:
        """Ein OKX-Instrument ist eindeutig, wenn es im Katalog LIVE steht."""
        broker = self.broker
        if broker is None:
            return False
        try:
            member = self.universum.zustand.hole("okx", symbol)
            inst_id = str(inst_id or getattr(member, "inst_id", "") or normalize_inst_id(symbol, broker.quote_ccy))
            meta = broker.client.instrument(inst_id)
        except BrokerFehler:
            return False
        return bool(meta and meta.ist_live and meta.base_ccy.upper() == str(symbol).upper())

    def _portfolio_erlaubt(self, *, zusaetzlich_belegt: int = 0) -> bool:
        """Positionsgrenze -- ungeklaerte eigene Orders zaehlen mit.

        9.5.8: Eine ungeklaerte Einstiegsorder kann beim Broker gefuellt sein;
        im Buch steht sie noch nicht. Bis 9.5.7 sperrte die globale
        Bereitschaftsampel diesen Fall mit ab. Seit die Sperre symbolgenau ist,
        muss die Positionszahl den Platzhalter selbst mitzaehlen -- sonst
        koennte der Bot ``Grenze + Anzahl ungeklaerter Orders`` echte
        Positionen halten.
        """
        grenze = int(getattr(self.cfg, "OKX_MAX_OPEN_POSITIONS", 6))
        return (len(self.buch.aktive()) + max(0, int(zusaetzlich_belegt))) < grenze

    def _menge_an_cash_anpassen(self, symbol: str, menge: float, preis: float,
                                quote_ccy: str = "", *, inst_id: str = "") -> tuple[float, str, str]:
        """Verkleinert die Order auf das verfuegbare Cash statt sie zu blockieren.

        Rueckgabe: (menge, hinweis, ablehnungsgrund)
            menge  = 0 bedeutet: auch die kleinste zulaessige Order passt nicht
            hinweis        gefuellt, wenn tatsaechlich verkleinert wurde
            ablehnungsgrund gefuellt, wenn menge == 0

        Rechenweg:
            reserve      = freies Cash * OKX_CASH_RESERVE_PCT
            max_notional = (frei - reserve) / (1 + Taker-Gebuehr)

        Die Gebuehr wird ausdruecklich herausgerechnet. Vorher steckte sie
        stillschweigend in der Reserve -- wer bis an die Reservegrenze
        verkleinert, wuerde sie sonst genau mit der Gebuehr wieder aufbrauchen.

        Verkleinern ist sicherheitstechnisch unbedenklich: der Stopabstand
        bleibt unveraendert, die Position wird kleiner, das Risiko je Trade
        sinkt also. Die Kosten- und Netto-Edge-Pruefung laeuft anschliessend
        trotzdem mit der neuen Menge erneut.
        """
        broker = self.broker
        if broker is None:
            return 0.0, "", "OKX nicht verbunden"
        if menge <= 0 or preis <= 0:
            return 0.0, "", "Keine gueltige Menge oder kein Preis"

        waehrung = str(quote_ccy or getattr(broker, "quote_ccy", "EUR")).upper()
        frei = broker.verfuegbares_cash(waehrung)
        if frei is None:
            return 0.0, "", f"Freies {waehrung}-Guthaben nicht abrufbar"

        reserve_pct = max(0.0, float(getattr(self.cfg, "OKX_CASH_RESERVE_PCT", 0.05)))
        # Der GEMESSENE Satz, nicht die Annahme -- sonst plant die
        # Cash-Rechnung mit einer Gebuehr, die es nicht gibt.
        gebuehr_pct = max(0.0, self._taker_satz())
        mindestwert = float(getattr(self.cfg, "OKX_MIN_POSITION_VALUE", 15.0))

        reserve_betrag = frei * reserve_pct
        verfuegbar = max(0.0, frei - reserve_betrag)
        max_notional = verfuegbar / (1.0 + gebuehr_pct)

        geplanter_wert = menge * preis
        if geplanter_wert <= max_notional:
            return menge, "", ""

        # Verkleinern und auf das OKX-Mengenraster abrunden.
        neue_menge = self._auf_lot_runden(symbol, max_notional / preis, inst_id=inst_id)
        neuer_wert = neue_menge * preis

        if neue_menge <= 0:
            return 0.0, "", (
                f"Cash reicht nicht: frei {frei:.2f} {waehrung}, davon "
                f"{reserve_betrag:.2f} Reserve; nutzbar {max_notional:.2f} "
                f"{waehrung} liegt unter der OKX-Mindestmenge")
        if neuer_wert < mindestwert:
            return 0.0, "", (
                f"Cash reicht nicht: moeglich waeren nur {neuer_wert:.2f} "
                f"{waehrung}, Mindestordergroesse ist {mindestwert:.2f} "
                f"{waehrung} (frei {frei:.2f}, Reserve {reserve_betrag:.2f})")

        hinweis = (f"Order von {geplanter_wert:.2f} {waehrung} auf "
                   f"{neuer_wert:.2f} {waehrung} wegen Cash-Reserve reduziert "
                   f"(frei {frei:.2f}, Reserve {reserve_betrag:.2f}, "
                   f"Gebuehr {gebuehr_pct * 100:.2f} %)")
        return neue_menge, hinweis, ""

    # -- Statusanzeige ------------------------------------------------------
    def status(self) -> dict:
        try:
            from crypto_strategy_mode import status as strategy_mode_status
            strategy_status = strategy_mode_status()
        except Exception as exc:
            strategy_status = {"active_mode": "CRYPTO_PAUSED", "error": str(exc)}
        account, capital_currency = "", "UNKNOWN"
        try:
            if self.broker:
                account = str(getattr(self.broker, "account_fingerprint", lambda: "")())
                capital_currency = str(getattr(self.broker, "kontowaehrung", lambda: "UNKNOWN")())
        except Exception:
            logger.warning("OKX-Anzeigekontext nicht lesbar; Konto/Waehrung bleiben unbekannt", exc_info=True)
        from candle_observation import snapshot as candle_quality_snapshot
        from crypto_strategy_mode import signal_timeframe
        client = getattr(self.broker, "client", None)
        candle_quality = candle_quality_snapshot(base_url=getattr(client, "base_url", ""),
            environment="DEMO" if bool(getattr(self.broker, "demo", False)) else "LIVE")
        return {
            "zeit": datetime.now(timezone.utc).isoformat(),
            "signal_timeframe": signal_timeframe(standard_timeframe=getattr(self.cfg, "CRYPTO_BAR_SIZE", "15 mins")),
            "candle_quality": candle_quality,
            "zyklen": self.zyklen,
            "verbunden": self.broker is not None,
            "modus": ("DEMO" if (self.broker and self.broker.ist_paper()) else "LIVE")
                     if self.broker else "-",
            # Ab v8.1.4 gehoert alles in den Status, was der Nutzer im
            # Telegram-Status und auf dem Dashboard sehen muss. Bis 8.1.3
            # stand dort ueber OKX schlicht nichts.
            "account_fingerprint": account,
            "kapital_waehrung": capital_currency,
            "account_action": self._account_action(),
            "handelsbereitschaft": self.bereitschaft.status(),
            "guthaben": self._guthaben_uebersicht(),
            "exposure": dict(self._letzte_exposure),
            "broker_state": "BROKER_STATE_UNKNOWN" if getattr(self.topf, "_snapshot_error", "") else "",
            "balance_snapshot_valid": not bool(getattr(self.topf, "_snapshot_error", "")),
            "balance_snapshot_detail": getattr(self.topf, "_snapshot_error", ""),
            # 10.7.0: symbolbezogene Sperren sind keine Domaenensperre.
            "gesperrte_symbole": sorted(getattr(self, "_gesperrte_symbole", set()) or []),
            "sperren": self._sperrliste(),
            "handelbares_kapital": round(float(self.topf.kontowert or 0.0), 2),
            "kapitalbeleg": (self.broker.capital_evidence()
                              if self.broker is not None and hasattr(self.broker, "capital_evidence") else {}),
            "gebuehrensatz_pct": round(self._taker_satz() * 100, 4),
            "crypto_strategy": strategy_status,
            "positionen": [p.als_dict() for p in self.buch.aktive()],
            "konto_asset_hinweise": [p.als_dict() for p in self.buch.alle()
                                      if not p.darf_schutz_ausfuehren],
            # 9.5.4: Schutzorders im Konto ohne Position im Buch. Sie werden
            # weder uebernommen noch storniert -- nur sichtbar gemacht.
            "verwaiste_schutzorders": list(getattr(self, "_verwaiste_schutz", [])),
            "universum": self.universum.uebersicht("okx"),
            "risiko": self.topf.uebersicht() if self.topf else {},
            "takt": self.takt.plan().get("crypto", {}),
            "letzter_universumslauf": self.letzter_universumslauf,
            "letzter_fehler": self.letzter_fehler,
            "functional_health": self.functional_health(),
        }


__all__ = ["CryptoEngine", "KryptoPosition", "KryptoPositionsbuch",
           "fill_anchored_stop_take", "VERWALTUNG_AUTO",
           "VERWALTUNG_MANUELL"]
