"""OKX-EEA-Anbindung (Spot) fuer TradingBot v8.1.1 NEXUS.

WARUM DIESES MODUL EXISTIERT
============================
Bis v6.0 lief Krypto ueber eToro. Das hatte drei Nachteile:

    1. eToro liefert fuer Krypto keine belastbaren Orderbuch-/Tickdaten,
       also war eine echte Marktqualitaets-Bewertung unmoeglich.
    2. eToro kennt keinen echten Broker-Stop fuer Krypto -- der Schutz lag
       ausschliesslich clientseitig und wirkte nur bei laufendem Programm.
    3. Aktien und Krypto teilten sich einen einzigen Kontostand, wodurch ein
       Krypto-Verlust die Aktienseite mitbremste.

OKX loest alle drei Punkte: vollstaendige Instrumentmetadaten (tickSz/lotSz/
minSz/state/listTime), echte Algo-Orders fuer Stop-Loss und Take-Profit und
ein eigenes, getrenntes Konto.

AUFBAU
======
    OKXClient   -- reine REST-Schicht, kennt keine Handelslogik.
                   Wird auch vom CryptoUniverseSelector benutzt, damit die
                   Universumsauswahl keinen Trading-Adapter braucht.
    OKXBroker   -- BrokerBase-Umsetzung fuer den Geldpfad.

SICHERHEITSGRUNDSAETZE (uebernommen aus dem eToro-Pfad)
=======================================================
- Ein unklarer Transportzustand nach einem POST wird NIEMALS als Misserfolg
  gewertet. Es gilt OrderStatusUnklar; der Bot sendet keinen blinden zweiten
  Kauf.
- Jede Order bekommt eine eigene clOrdId. Damit laesst sich nach einem
  Abbruch eindeutig feststellen, ob die Order angekommen ist.
- Geheimnisse (Key/Secret/Passphrase) tauchen in keiner Ausnahme, keinem
  Log und keiner Fehlermeldung auf.
- Demo- und Live-Zugang sind vollstaendig getrennte Schluesselsaetze. Ein
  Demo-Key kann technisch nicht live handeln und umgekehrt.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Any, Iterable, Optional
from urllib.parse import urlencode

import pandas as pd
import requests
from broker_observation import BrokerObservation, observe_rest, transport_started, transport_received

from .base import (
    AuthentifizierungsFehler,
    BrokerBase,
    BrokerFehler,
    Fill,
    NichtUnterstuetzt,
    NichtVerbunden,
    OrderErgebnis,
    OrderStatusUnklar,
    Position,
    VerbindungVerloren,
    Zeitabweichung,
)
from .history_safety import completed_bars_only, retry_call
from .okx_price_limits import OKXPriceBandError, PriceBand, bounded_limit, fresh

logger = logging.getLogger(__name__)


class _FillEvidenceConflict(BrokerFehler):
    """A conflicting execution receipt is not an unavailable history service."""


def _merge_fill_receipt(previous, row):
    for key in ("instId", "ordId", "side", "fillSz", "fillPx", "fee", "feeCcy", "tradeQuoteCcy", "fillTime"):
        a, b = previous.get(key), row.get(key)
        if a in (None, "") or b in (None, ""):
            continue
        same = str(a) == str(b)
        if key in {"fillSz", "fillPx", "fee"}:
            try:
                same = Decimal(str(a)) == Decimal(str(b))
            except InvalidOperation:
                same = False
        if not same:
            raise _FillEvidenceConflict("Widerspruechlicher doppelter Fillbeleg: " + key)
    return dict(previous, **{k: v for k, v in row.items() if v not in (None, "")})

# ---------------------------------------------------------------------------
# Konstanten
# ---------------------------------------------------------------------------
OKX_BASE_URL = "https://eea.okx.com"

# OKX-Kerzengroessen. Der Bot spricht die IB-artige Schreibweise ("15 mins"),
# OKX erwartet "15m"/"1H"/"1D".
BAR_MAP = {
    "1 min": "1m", "1 mins": "1m",
    "3 mins": "3m", "3 min": "3m",
    "5 mins": "5m", "5 min": "5m",
    "15 mins": "15m", "15 min": "15m",
    "30 mins": "30m", "30 min": "30m",
    "1 hour": "1H", "1 hours": "1H",
    "2 hours": "2H", "4 hours": "4H", "6 hours": "6H", "12 hours": "12H",
    "1 day": "1D", "1 days": "1D",
    "1 week": "1W",
}

BAR_SECONDS = {
    "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
    "1H": 3600, "2H": 7200, "4H": 14400, "6H": 21600, "12H": 43200,
    "1D": 86400, "1W": 604800,
}

# Fehlercodes, bei denen ein erneuter Versuch sinnlos ist.
_AUTH_CODES = {"50100", "50101", "50103", "50104", "50105",
               "50106", "50107", "50111", "50112", "50113", "50114"}

# 50102 bedeutet laut OKX nicht "falscher API-Key", sondern ein abgelaufenes
# 30-Sekunden-Zeitfenster. Der Fehler muss deshalb wiederholbar bleiben.
_TIME_CODES = {"50102"}

# Fehlercodes, die eine Ueberlast/Sperre bedeuten -- kurz warten hilft.
_RATE_CODES = {"50011", "50061", "58102"}

# Codes, bei denen OKX AUSDRUECKLICH offenlaesst, ob die Order angekommen ist.
# 50004 lautet woertlich: "Endpoint request timeout (does not indicate success
# or failure of order, please check order status)". Bis 9.0.15 landeten diese
# Codes im Zweig "fachliche Ablehnung ist eindeutig" -- der Bot hat die Order
# danach VERGESSEN (REJECTED + clear_pending). Eine tatsaechlich angenommene
# Order wurde damit zu einer unbekannten, ungeschuetzten Position.
_UNKLARE_ORDER_CODES = {"50001", "50004", "50005", "50013", "50026"}

# "Duplicated client order ID". Das ist der BEWEIS, dass die Order bei OKX
# liegt -- nicht das Gegenteil. Da die clOrdId deterministisch aus der
# decision_id entsteht, tritt der Code nach jedem Wiederanlauf derselben
# Entscheidung auf. Er gehoert deshalb in den Nachweispfad, nicht in die
# Ablehnung.
_DOPPELTE_CLORDID = "51016"

# Storno-Codes, die aus Sicht des Aufrufers ein ERFOLG sind: das Ziel "keine
# offene Order mehr" ist erreicht. Bis 9.0.15 blockierten sie den Ausstieg
# genau dann, wenn die Schutzorder gerade ausgeloest war -- also im
# aktivsten Marktmoment.
# Kennungen, die eine von NEXUS gesetzte Schutzorder tragen kann.
# protection_client_id = "P" + cl_ord_id, und cl_ord_id ist entweder
# "N9<decision_id>" oder ein make_client_order_id("TB..")-Wert. Damit
# sind "PN9...", "PTB..." und -- als Rueckfall in _setze_schutz --
# "TBP..." moeglich; "N9..." stammt aus aelteren Versionen.
_NEXUS_ALGO_PRAEFIXE = ("PN9", "PTB", "TBP", "N9")


def _mit_klartext(meldung: str, code: str) -> str:
    """Haengt die belegte Klartexterklaerung an eine OKX-Fehlermeldung.

    9.5.5, auf Georgs Wunsch nach dem 503-Vorfall: aus "OKX-Fehler 50001"
    liess sich nicht erkennen, ob es an den Zugangsdaten, der Uhr, dem Konto
    oder an OKX selbst lag. Jetzt steht die Antwort in der Meldung.

    Unbekannte Codes bleiben unveraendert -- lieber keine Erklaerung als eine
    erfundene.
    """
    try:
        from okx_fehlercodes import ergaenze
        return ergaenze(meldung, code)
    except Exception:
        return meldung
_STORNO_ERLEDIGT_CODES = {"51400", "51401", "51402", "51405", "51410"}

# 9.5.8: GENAU EIN OKX-Code bedeutet "diese Order kenne ich nicht" --
# 51603, die Auskunft von /trade/order. Das ist eine ANTWORT des Brokers, kein
# Ausfall.
#
# Bewusst NICHT in dieser Menge, obwohl es naheliegt:
#   51400 -- laut der eigenen Codetabelle (okx_fehlercodes.py) heisst das
#            "Stornieren fehlgeschlagen, weil die Order bereits AUSGEFUEHRT,
#            schon storniert oder nicht mehr vorhanden ist". "Bereits
#            ausgefuehrt" ist das Gegenteil von "nie ausgefuehrt".
#   51001 -- betrifft das INSTRUMENT, nicht die Order. Ist ein Paar umbenannt
#            oder delistet, faende auch die Archivsuche nichts (sie filtert auf
#            dieselbe instId) -- der Kauf wuerde faelschlich als nie ausgefuehrt
#            verbucht.
#
# 51603 allein beweist ebenfalls nichts: auch eine laengst archivierte Order
# liefert ihn. Erst zusammen mit einer LEEREN (nicht: unlesbaren) Auskunft des
# Fill-Archivs und einem Mindestalter ergibt sich der Beleg.
_ORDER_UNBEKANNT_CODES = {"51603"}

_OKX_CODE_RE = re.compile(
    r"OKX-(?:Fehler|Zugangsfehler|Ueberlast) (\d+):"
    r"|OKX lehnt die Order ab \((\d+)\)"
    r"|OKX-Order: Ausgang unbestimmt \((\d+)\)")


def _okx_fehlercode(exc) -> str:
    """Den OKX-Code aus der festen Stelle der Meldung lesen.

    9.5.8. Ein blosses ``"51603" in str(exc)`` traefe auch eine ordId, einen
    Preis oder eine Zeitangabe, in der dieselbe Ziffernfolge vorkommt -- und
    machte damit aus einem Transportfehler eine verbindliche Brokerauskunft.
    """
    if getattr(exc, "okx_code", ""):
        return str(exc.okx_code)
    treffer = _OKX_CODE_RE.search(str(exc))
    if not treffer:
        return ""
    return next((g for g in treffer.groups() if g), "")

_CLORDID_RE = re.compile(r"[^A-Za-z0-9]")


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------
def _f(value: Any, default: float = 0.0) -> float:
    """Robuste Zahlwandlung; OKX liefert alle Zahlen als String."""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _dec(value: Any) -> Optional[Decimal]:
    try:
        if value is None or value == "":
            return None
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def quantize_down(value: float, step: Any) -> float:
    """Rundet auf ein Vielfaches von 'step' ABWAERTS.

    Abwaerts ist hier bewusst richtig: eine Menge darf nie groesser werden,
    als der Bot berechnet hat, und ein Verkauf darf nie mehr anfordern, als
    tatsaechlich im Konto liegt.
    """
    d_step = _dec(step)
    d_val = _dec(value)
    if d_val is None:
        return 0.0
    if not d_step or d_step <= 0:
        return float(d_val)
    n = (d_val / d_step).to_integral_value(rounding=ROUND_DOWN)
    return float(n * d_step)


def quantize_up(value: float, step: Any) -> float:
    """Rundet fuer eine sichere Kauf-Preisgrenze auf die Tickgroesse auf."""
    d_step = _dec(step)
    d_val = _dec(value)
    if d_val is None:
        return 0.0
    if not d_step or d_step <= 0:
        return float(d_val)
    n = (d_val / d_step).to_integral_value(rounding=ROUND_UP)
    return float(n * d_step)


def quantize_price(value: float, tick: Any) -> float:
    """Rundet einen Preis auf die Tick-Groesse (kaufmaennisch)."""
    d_step = _dec(tick)
    d_val = _dec(value)
    if d_val is None:
        return 0.0
    if not d_step or d_step <= 0:
        return float(d_val)
    n = (d_val / d_step).to_integral_value(rounding=ROUND_HALF_UP)
    return float(n * d_step)


def format_decimal(value: float, step: Any) -> str:
    """Formatiert eine Zahl exakt so genau wie die Schrittweite es verlangt.

    OKX lehnt sowohl 1e-08-Notation als auch ueberzaehlige Nachkommastellen
    ab. Deshalb wird ueber Decimal formatiert statt ueber f-Strings.
    """
    d_step = _dec(step)
    d_val = _dec(value) or Decimal(0)
    if d_step and d_step > 0:
        exponent = -d_step.as_tuple().exponent
        quant = Decimal(1).scaleb(-max(0, int(exponent)))
        d_val = d_val.quantize(quant, rounding=ROUND_DOWN)
    # normalize() entfernt ueberfluessige Nullen, 'f' verhindert die
    # Exponentialschreibweise -- OKX lehnt '5E-8' ab.
    return format(d_val.normalize(), "f")


def make_client_order_id(prefix: str = "TBN8") -> str:
    """Eindeutige, OKX-konforme Client-Order-ID (nur A-Z a-z 0-9, max. 32)."""
    raw = f"{prefix}{uuid.uuid4().hex}"
    return _CLORDID_RE.sub("", raw)[:32]


def okx_fill_identity(row: dict, account_fingerprint: str = "") -> str:
    """Kontoweit eindeutige Identitaet eines OKX-Fills.

    Eine rohe ``tradeId`` ist nicht instrumentuebergreifend eindeutig. In
    9.4.1 kollidierte dadurch ein ONDO-Fill mit einem LINK-Fill.
    """
    inst = normalize_inst_id(str((row or {}).get("instId") or ""))
    order = str((row or {}).get("ordId") or "")
    trade = str((row or {}).get("tradeId") or "")
    if not trade:
        trade = ":".join((str((row or {}).get("billId") or ""),
                          str((row or {}).get("ts") or ""),
                          str((row or {}).get("fillSz") or "")))
    account = str(account_fingerprint or "unknown")[:64]
    return f"okx:{account}:{inst}:{order}:{trade}"


def client_order_id_for_decision(decision_id: int) -> str:
    """Deterministische clOrdId, die vor dem ersten POST speicherbar ist."""
    raw = f"N9{int(decision_id)}"
    return _CLORDID_RE.sub("", raw)[:32]


def okx_timestamp(epoch_seconds: Optional[float] = None) -> str:
    """ISO-8601-Zeitstempel in Millisekunden, wie OKX ihn signiert erwartet."""
    now = (datetime.now(timezone.utc) if epoch_seconds is None else
           datetime.fromtimestamp(float(epoch_seconds), timezone.utc))
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def normalize_inst_id(symbol: Any, quote: str = "EUR") -> str:
    """Wandelt 'BTC', 'BTC/EUR', 'btc-eur' einheitlich nach 'BTC-EUR'."""
    text = str(symbol or "").strip().upper().replace("/", "-").replace("_", "-")
    if not text:
        return ""
    if "-" in text:
        return text
    return f"{text}-{str(quote or 'EUR').upper()}"


def base_currency(inst_id: str) -> str:
    return str(inst_id or "").split("-")[0].upper()


# ---------------------------------------------------------------------------
# Ratenbegrenzung
# ---------------------------------------------------------------------------
class RateLimiter:
    """Einfacher Token-Bucket je Endpunktgruppe.

    OKX begrenzt pro Endpunkt und Konto. Der Bot bleibt bewusst deutlich
    unter den offiziellen Grenzen: ein gesperrter Schluessel kostet mehr als
    ein paar Millisekunden Wartezeit.
    """

    def __init__(self, rate: float, per_seconds: float = 1.0):
        self.capacity = max(1.0, float(rate))
        self.tokens = self.capacity
        self.rate = self.capacity / max(0.001, float(per_seconds))
        self.updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
                fehlend = (1.0 - self.tokens) / self.rate
            if time.monotonic() + fehlend > deadline:
                return False
            time.sleep(min(0.25, max(0.005, fehlend)))


# ---------------------------------------------------------------------------
# Instrumentmetadaten
# ---------------------------------------------------------------------------
@dataclass
class OKXInstrument:
    """Alles, was der Bot ueber ein OKX-Spotinstrument wissen muss."""
    inst_id: str
    base_ccy: str
    quote_ccy: str
    state: str = ""
    tick_size: str = "0.00000001"
    lot_size: str = "0.00000001"
    min_size: str = "0"
    max_market_size: str = ""
    list_time_ms: int = 0
    # Bei OKX Unified USD kann ein Markt wie UNI-USD mit mehreren
    # Abrechnungswaehrungen gehandelt werden. quoteCcy beschreibt den Markt,
    # tradeQuoteCcyList die fuer Orders tatsaechlich erlaubten Guthaben.
    trade_quote_ccy_list: tuple[str, ...] = ()

    @property
    def ist_live(self) -> bool:
        return str(self.state or "").lower() == "live"

    @property
    def alter_tage(self) -> float:
        if self.list_time_ms <= 0:
            return 0.0
        return max(0.0, (time.time() * 1000.0 - float(self.list_time_ms)) / 86_400_000.0)

    @classmethod
    def from_api(cls, row: dict) -> "OKXInstrument":
        trade_quotes = row.get("tradeQuoteCcyList") or []
        if isinstance(trade_quotes, str):
            trade_quotes = [trade_quotes]
        return cls(
            inst_id=str(row.get("instId", "")).upper(),
            base_ccy=str(row.get("baseCcy", "")).upper(),
            quote_ccy=str(row.get("quoteCcy", "")).upper(),
            state=str(row.get("state", "")),
            tick_size=str(row.get("tickSz") or "0.00000001"),
            lot_size=str(row.get("lotSz") or "0.00000001"),
            min_size=str(row.get("minSz") or "0"),
            max_market_size=str(row.get("maxMktSz") or ""),
            list_time_ms=int(_f(row.get("listTime"), 0.0)),
            trade_quote_ccy_list=tuple(dict.fromkeys(
                str(x).upper() for x in trade_quotes if str(x).strip()
            )),
        )


@dataclass
class OKXTicker:
    """Ein Ticker-Datensatz, reduziert auf die fuer uns relevanten Felder."""
    inst_id: str
    last: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    vol_24h_base: float = 0.0
    vol_24h_quote: float = 0.0
    open_24h: float = 0.0
    high_24h: float = 0.0
    low_24h: float = 0.0
    timestamp_ms: int = 0

    @property
    def spread_pct(self) -> float:
        """Relative Spanne bezogen auf die Mitte. 0 bei unbrauchbaren Daten."""
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            return 0.0
        mitte = (self.bid + self.ask) / 2.0
        if mitte <= 0:
            return 0.0
        return (self.ask - self.bid) / mitte

    @property
    def change_24h_pct(self) -> float:
        if self.open_24h <= 0 or self.last <= 0:
            return 0.0
        return (self.last - self.open_24h) / self.open_24h

    @classmethod
    def from_api(cls, row: dict) -> "OKXTicker":
        return cls(
            inst_id=str(row.get("instId", "")).upper(),
            last=_f(row.get("last")),
            bid=_f(row.get("bidPx")),
            ask=_f(row.get("askPx")),
            vol_24h_base=_f(row.get("vol24h")),
            vol_24h_quote=_f(row.get("volCcy24h")),
            open_24h=_f(row.get("open24h")),
            high_24h=_f(row.get("high24h")),
            low_24h=_f(row.get("low24h")),
            timestamp_ms=int(_f(row.get("ts"), 0.0)),
        )


# ---------------------------------------------------------------------------
# REST-Schicht
# ---------------------------------------------------------------------------
class OKXClient:
    """Schlanke, threadsichere REST-Schicht fuer die OKX-V5-API.

    Bewusst OHNE Handelslogik: dieses Objekt wird auch von der
    Universumsauswahl benutzt, die nur oeffentliche Marktdaten braucht und
    keinerlei Orderrechte besitzen soll.
    """

    def __init__(self, api_key: str = "", api_secret: str = "",
                 passphrase: str = "", *, demo: bool = True,
                 base_url: str = OKX_BASE_URL, timeout: float = 15.0):
        self._key = str(api_key or "").strip()
        self._secret = str(api_secret or "").strip()
        self._passphrase = str(passphrase or "").strip()
        self.demo = bool(demo)
        self.base_url = str(base_url or OKX_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self._observations = BrokerObservation("okx", "DEMO" if self.demo else "LIVE")
        self._candle_receipts = threading.local()

        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "TradingBot-v8-NEXUS",
        })

        self._public_limit = RateLimiter(10, 1.0)
        self._private_limit = RateLimiter(5, 1.0)
        self._order_limit = RateLimiter(8, 2.0)

        self._instruments: dict[str, OKXInstrument] = {}
        self._instruments_at = 0.0
        self._instruments_generation = 0
        self._instruments_quality = {"state": "UNKNOWN", "complete": None,
                                     "source": None, "last_success_at": None}
        self._public_instruments: dict[str, OKXInstrument] = {}
        self._public_instruments_at = 0.0
        self._lock = threading.RLock()
        self._last_contact: Optional[str] = None
        # Differenz OKX-Serverzeit minus lokale Systemzeit. Sie wird mit dem
        # oeffentlichen /public/time-Endpunkt bestimmt und fuer signierte
        # REST- sowie WebSocket-Anfragen verwendet.
        self._clock_offset_seconds = 0.0

    # -- Grundlagen ---------------------------------------------------------
    @property
    def hat_zugangsdaten(self) -> bool:
        return bool(self._key and self._secret and self._passphrase)

    def last_contact(self) -> Optional[str]:
        return self._last_contact

    def _signature(self, timestamp: str, method: str, request_path: str, body: str) -> str:
        message = f"{timestamp}{method.upper()}{request_path}{body}"
        digest = hmac.new(self._secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
        return base64.b64encode(digest).decode("ascii")

    @staticmethod
    def _query_string(params: Optional[dict]) -> str:
        if not params:
            return ""
        teile = [(str(k), str(v)) for k, v in params.items() if v not in (None, "")]
        return ("?" + urlencode(teile)) if teile else ""

    def _headers(self, method: str, request_path: str, body: str, private: bool) -> dict:
        headers: dict[str, str] = {}
        if self.demo and (private or request_path.startswith("/api/v5/market/")
                          or request_path.split("?", 1)[0] == "/api/v5/public/price-limit"):
            # Demo-Orders brauchen DEMO-Preise und DEMO-Orderbuchtiefe.
            # Oeffentlich bedeutet ohne Signatur, nicht automatisch LIVE.
            # Nur der Referenzkatalog /public/instruments und die Uhr bleiben
            # wie bisher live; handelbare Regeln stammen aus /account/instruments.
            # Freqtrade dry_run ist eine lokale Simulation und kein Vorbild
            # fuer die Vermischung eines Live-Buches mit OKX-Demo-Orders.
            headers["x-simulated-trading"] = "1"
        if not private:
            return headers
        if not self.hat_zugangsdaten:
            raise AuthentifizierungsFehler(
                "OKX-Zugangsdaten unvollstaendig (Key, Secret und Passphrase noetig)."
            )
        timestamp = okx_timestamp(time.time() + self._clock_offset_seconds)
        headers.update({
            "OK-ACCESS-KEY": self._key,
            "OK-ACCESS-SIGN": self._signature(timestamp, method, request_path, body),
            "OK-ACCESS-TIMESTAMP": timestamp,
            "OK-ACCESS-PASSPHRASE": self._passphrase,
        })
        return headers

    def _limiter(self, private: bool, is_order: bool) -> RateLimiter:
        if is_order:
            return self._order_limit
        return self._private_limit if private else self._public_limit

    def _fehlerort(self, method: str, request_path: str, private: bool) -> str:
        """Welcher OKX-Dienst hat geantwortet -- fuer verstaendliche Fehler.

        9.5.3: Am 02.09.2026 stand im Log 25 Minuten lang nur
        "OKX-Serverfehler HTTP 503". Tatsaechlich lief /public/time
        einwandfrei; ausgefallen war ausschliesslich der Demo-Handelsdienst.
        Das ist ein anderer Zustand als "Broker weg" und muss auch so
        dastehen.
        """
        bereich = ("Demo-Marktdaten" if (self.demo and not private
                                        and request_path.startswith("/api/v5/market/"))
                   else "Demo-Handelsdienst" if (self.demo and private)
                   else ("Handelsdienst" if private else "Marktdaten"))
        return f"{bereich} {method} {request_path}"

    @observe_rest()
    def request(self, method: str, path: str, *, params: Optional[dict] = None,
                body: Optional[dict] = None, private: bool = False,
                is_order: bool = False) -> list:
        """Fuehrt einen Aufruf aus und liefert die 'data'-Liste zurueck.

        Fehlerbehandlung nach Schwere:
            Netzwerkfehler bei GET     -> VerbindungVerloren (harmlos)
            Netzwerkfehler bei Order   -> OrderStatusUnklar  (niemals blind wiederholen)
            401/Auth-Fehlercode        -> AuthentifizierungsFehler (Warten hilft nicht)
            sonstiger Fachfehler       -> BrokerFehler
        """
        method = method.upper()
        query = self._query_string(params)
        request_path = f"/api/v5{path}{query}"
        body_text = json.dumps(body, separators=(",", ":")) if body is not None else ""
        url = f"{self.base_url}{request_path}"

        limiter = self._limiter(private, is_order)
        if not limiter.acquire(timeout=12.0):
            raise VerbindungVerloren("OKX-Ratenbegrenzung: kein freies Zeitfenster erhalten.")

        try:
            headers = self._headers(method, request_path, body_text, private)
        except AuthentifizierungsFehler:
            raise
        if is_order and path == "/trade/order":
            # OKX dokumentiert die Anfrage-Verfallszeit ausdruecklich nur fuer
            # "Place order / Place multiple orders / Amend order". v9.1: Ein
            # STORNO darf niemals verfallen -- sonst glaubt der Bot, storniert
            # zu haben, waehrend die Schutzorder noch liegt.
            # OKX kann Order-Anfragen nach einer vom Client gesetzten Frist
            # verwerfen. Das verhindert, dass eine durch Netzstau alte Order
            # erst deutlich spaeter im Matching ankommt. Bei einer unklaren
            # Antwort bleibt die bestehende fail-closed-Logik aktiv: niemals
            # blind erneut senden, sondern clOrdId pruefen.
            try:
                import config as _cfg
                sekunden = float(getattr(_cfg, "OKX_ORDER_REQUEST_EXPIRY_SECONDS", 12.0))
            except Exception:
                sekunden = 12.0
            sekunden = max(2.0, min(60.0, sekunden))
            headers["expTime"] = str(int((time.time() + self._clock_offset_seconds
                                           + sekunden) * 1000))

        request_started_at = time.time()
        try:
            transport_started()
            response = self.session.request(
                method, url, headers=headers,
                data=body_text if body_text else None,
                timeout=self.timeout,
            )
            transport_received(response.status_code)
            response_received_at = time.time()
        except requests.RequestException as exc:
            # Bewusst nur den Ausnahmetyp melden: manche Bibliotheken haengen
            # die vollstaendige URL inklusive Parametern an die Meldung.
            if is_order:
                raise OrderStatusUnklar(
                    f"OKX-Order: Transportzustand unklar ({type(exc).__name__}).",
                ) from None
            raise VerbindungVerloren(f"OKX nicht erreichbar ({type(exc).__name__}).") from None

        try:
            payload = response.json()
        except ValueError:
            if response.status_code == 401:
                raise AuthentifizierungsFehler(
                    "OKX lehnt die Zugangsdaten ab (HTTP 401).")
            if response.status_code == 429:
                raise VerbindungVerloren("OKX-Ratenbegrenzung erreicht (HTTP 429).")
            if response.status_code >= 500:
                # KORREKTUR 9.5.5 am Kommentar: Hier stand, ein 503 komme als
                # HTML-Seite und feuere deshalb genau diesen Zweig. Das war
                # falsch -- der echte 503 vom 02.09.2026 war gueltiges JSON mit
                # code 50001 und lief ueber den Zweig weiter unten. Dieser hier
                # greift nur, wenn die Antwort UEBERHAUPT kein JSON ist.
                # Beide Zweige sind seit 9.5.3 gleich behandelt; nur die
                # Begruendung stimmte nicht.
                wo = self._fehlerort(method, request_path, private)
                if is_order:
                    raise OrderStatusUnklar(
                        f"OKX-Order: Serverfehler HTTP {response.status_code} ({wo}).")
                raise VerbindungVerloren(
                    f"OKX-Serverfehler HTTP {response.status_code} ({wo}).")
            if is_order:
                raise OrderStatusUnklar("OKX-Order: Antwort nicht lesbar.")
            raise VerbindungVerloren(f"OKX-Antwort nicht lesbar (HTTP {response.status_code}).")

        if not isinstance(payload, dict):
            error = OrderStatusUnklar if is_order else BrokerFehler
            raise error("OKX_RESPONSE_SCHEMA_INVALID: Antwort ist kein Objekt")
        code = str(payload.get("code", ""))
        if code in {"0", "2"} and not isinstance(payload.get("data"), list):
            error = OrderStatusUnklar if is_order else BrokerFehler
            raise error("OKX_RESPONSE_SCHEMA_INVALID: data-Liste fehlt; keine leere Vollantwort")
        data = payload.get("data") or []
        if method == "GET" and not private and path in {"/market/candles", "/market/history-candles"}:
            try:
                from candle_observation import record_response
                receipt = record_response(base_url=self.base_url,
                    environment="DEMO" if self.demo else "LIVE", endpoint=path,
                    instrument=(params or {}).get("instId", "UNKNOWN"),
                    timeframe=(params or {}).get("bar", "UNKNOWN"),
                    started=request_started_at, received=response_received_at,
                    http_status=response.status_code, code=code, rows=data)
                self._candle_receipts.last = receipt
            except Exception:
                logger.debug("OKX-Kerzenbeleg nicht speicherbar", exc_info=True)
        # Bei Order-/Sammelantworten kann OKX oben code=0 melden und eine
        # konkrete Ablehnung nur als sCode im Datensatz liefern.
        #
        # v9.1: Beim Stornieren sind "Order existiert nicht", "bereits
        # storniert" und "bereits ausgefuehrt" fachlich ERFOLGE -- das Ziel
        # "keine offene Order mehr" ist erreicht. Vorher kippte eine solche
        # Zeile den ganzen Aufruf und blockierte damit den Ausstieg.
        storniert = path.startswith("/trade/cancel")
        erfolgscodes = _STORNO_ERLEDIGT_CODES if storniert else frozenset()
        detail_code, detail_msg = self._first_detail(data, erfolgscodes=erfolgscodes)
        msg = self._safe_error_text(payload.get("msg")) or "ohne Meldung"
        code_out = detail_code or code
        msg_out = self._safe_error_text(detail_msg) or msg
        # OKX nutzt bei Sammelanfragen code="2" fuer "teilweise erfolgreich".
        # Ohne diese Zeile galt ein Teilerfolg als Totalausfall.
        if code in ("0", "2") and not detail_code:
            code = "0"
        # Beim Stornieren kann der AEUSSERE Code "1" lauten (alle Zeilen
        # fehlgeschlagen), obwohl jede einzelne Zeile nur sagt: "die Order gibt
        # es nicht mehr". Fachlich ist das der gewuenschte Zustand. Ohne diese
        # Auswertung blieb code_out="1" stehen und der Ausstieg blieb blockiert.
        if storniert and not detail_code and data:
            alle = self._alle_details(data)
            if alle and all(c in ("0", *_STORNO_ERLEDIGT_CODES) for c, _ in alle):
                code = "0"

        # Wichtig: OKX kann auch bei einem Zeitfehler HTTP 401 liefern. Erst
        # den strukturierten Fehlercode auswerten, dann den HTTP-Status.
        meldung_klein = str(msg_out).lower()
        zeitfehler = (code_out in _TIME_CODES or
                      ("timestamp" in meldung_klein and
                       any(wort in meldung_klein for wort in
                           ("expired", "expire", "invalid", "difference"))))
        if zeitfehler:
            raise Zeitabweichung(_mit_klartext(
                f"OKX-Zeitfenster abgelaufen ({code_out or response.status_code}): {msg_out}",
                code_out))
        if response.status_code == 401:
            raise AuthentifizierungsFehler("OKX lehnt die Zugangsdaten ab (HTTP 401).")
        if response.status_code == 429:
            raise VerbindungVerloren("OKX-Ratenbegrenzung erreicht (HTTP 429).")
        if response.status_code >= 500:
            # 9.5.3: Pfad und Betriebsart mitnennen. Am 02.09.2026 stand im
            # Log 25 Minuten lang nur "OKX-Serverfehler HTTP 503" -- welcher
            # Aufruf scheiterte, war nirgends zu sehen. Tatsaechlich lief der
            # oeffentliche Pfad einwandfrei, und nur der Demo-Handelsdienst
            # antwortete nicht. Das ist ein anderer Zustand als "Broker weg".
            wo = self._fehlerort(method, request_path, private)
            # 9.5.5: Code und Originalmeldung von OKX mitnehmen. Am 02.09.2026
            # stand 25 Minuten lang nur "OKX-Serverfehler HTTP 503" im Log --
            # dass OKX selbst code 50001 ("Service temporarily unavailable")
            # mitgeliefert hatte, war nirgends zu sehen.
            zusatz = f", OKX-Code {code_out}: {msg_out}" if code_out else ""
            if is_order:
                raise OrderStatusUnklar(_mit_klartext(
                    f"OKX-Order: Serverfehler HTTP {response.status_code} "
                    f"({wo}{zusatz}).", code_out))
            raise VerbindungVerloren(_mit_klartext(
                f"OKX-Serverfehler HTTP {response.status_code} ({wo}{zusatz}).",
                code_out))

        if code == "0" and not detail_code:
            self._last_contact = datetime.now(timezone.utc).isoformat()
            return list(data)

        if code_out in _AUTH_CODES:
            raise AuthentifizierungsFehler(_mit_klartext(
                f"OKX-Zugangsfehler {code_out}: {msg_out}", code_out))
        if code_out in _RATE_CODES:
            raise VerbindungVerloren(_mit_klartext(
                f"OKX-Ueberlast {code_out}: {msg_out}", code_out))
        if is_order and code_out in _UNKLARE_ORDER_CODES:
            # v9.1: OKX sagt zu diesen Codes ausdruecklich, dass sie WEDER
            # Erfolg NOCH Misserfolg bedeuten. Sie duerfen deshalb nie als
            # Ablehnung durchgehen -- sonst vergisst der Bot eine Order, die
            # beim Broker liegt, und die Position bleibt ungeschuetzt.
            raise OrderStatusUnklar(_mit_klartext(
                f"OKX-Order: Ausgang unbestimmt ({code_out}): {msg_out}", code_out))
        if is_order:
            # Fachliche Ablehnung ist eindeutig: die Order wurde NICHT
            # angenommen. Das ist kein unklarer Zustand.
            if (method.upper() == "POST" and path == "/trade/order"
                    and code_out in {"51137", "51138"}):
                raise OKXPriceBandError(
                    f"OKX lehnt die Order ab ({code_out}): {msg_out}", code=code_out)
            if path == "/trade/order-algo":
                # Whitelisted request/response fields make otherwise blank
                # broker rejections diagnosable. Never log authentication
                # headers, signatures, credentials or arbitrary response data.
                request_fields = ("instId", "tdMode", "side", "ordType", "sz",
                                  "algoClOrdId", "tradeQuoteCcy", "slTriggerPx",
                                  "slOrdPx", "slTriggerPxType", "tpTriggerPx",
                                  "tpOrdPx", "tpTriggerPxType")
                response_fields = ("algoId", "algoClOrdId", "sCode", "sMsg", "subCode")
                context = {
                    "method": method, "path": "/api/v5" + path,
                    "environment": "DEMO" if self.demo else "LIVE",
                    "http_status": response.status_code, "code": code,
                    "msg": msg,
                    "request": {k: self._safe_error_text(body[k]) for k in request_fields
                                if isinstance(body, dict) and k in body},
                    "data": [{k: self._safe_error_text(row[k]) for k in response_fields if k in row}
                             for row in data[:5] if isinstance(row, dict)],
                }
                self.last_algo_error = context
                logger.warning("OKX-Schutzantwort: %s", json.dumps(context, ensure_ascii=True))
            raise BrokerFehler(_mit_klartext(
                f"OKX lehnt die Order ab ({code_out}): {msg_out}", code_out))
        raise BrokerFehler(_mit_klartext(
            f"OKX-Fehler {code_out}: {msg_out}", code_out))

    def _safe_error_text(self, value) -> str:
        text = str(value if value is not None else "").strip()
        for secret in sorted((self._key, self._secret, self._passphrase), key=len, reverse=True):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return " ".join(text.split())[:1000]

    @staticmethod
    def _first_detail(data: list, *, erfolgscodes: frozenset = frozenset()) -> tuple[str, str]:
        """Die erste wirklich fehlgeschlagene Zeile einer Sammelantwort.

        v9.1: ``erfolgscodes`` erlaubt es dem Aufrufer, Codes als Erfolg zu
        werten, die es fachlich sind -- beim Stornieren etwa "Order existiert
        nicht mehr". Ohne das kippte eine einzige solche Zeile den ganzen
        Aufruf, obwohl das Ziel erreicht war.
        """
        for row in data or []:
            if not isinstance(row, dict):
                continue
            code = str(row.get("sCode", ""))
            if code in ("", "0") or code in erfolgscodes:
                continue
            return code, str(row.get("sMsg") or "").strip()
        return "", ""

    @staticmethod
    def _alle_details(data: list) -> list[tuple[str, str]]:
        """Ergebnis JE ZEILE einer Sammelantwort (fuer Storno-Batches)."""
        return [(str(r.get("sCode", "0")), str(r.get("sMsg", "")))
                for r in data or [] if isinstance(r, dict) and "sCode" in r]

    # -- Oeffentliche Marktdaten -------------------------------------------
    def instruments(self, *, force: bool = False, max_age: float | None = None) -> dict[str, OKXInstrument]:
        """SPOT-Regeln; mit Zugangsdaten kontospezifisch samt tradeQuoteCcyList."""
        if max_age is None:
            import config
            max_age = max(1.0, float(getattr(config, "OKX_INSTRUMENT_MAX_AGE_SECONDS", 60.0)))
        with self._lock:
            frisch = (time.time() - self._instruments_at) < max_age
            if self._instruments and frisch and not force:
                return dict(self._instruments)
            self._instruments_generation += 1
            generation = self._instruments_generation
            self._instruments_quality.update(state="PENDING", generation=generation,
                                             last_attempt_at=datetime.now(timezone.utc).isoformat())
        try:
            if self.hat_zugangsdaten:
                source = "/account/instruments"
                rows = self.request("GET", source, params={"instType": "SPOT"}, private=True)
            else:
                source = "/public/instruments"
                rows = self.request("GET", source, params={"instType": "SPOT"})
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                raise BrokerFehler("OKX_INSTRUMENT_SCHEMA_INVALID")
            gefunden = {}
            for row in rows:
                inst = OKXInstrument.from_api(row)
                if not inst.inst_id:
                    raise BrokerFehler("OKX_INSTRUMENT_ID_MISSING")
                gefunden[inst.inst_id] = inst
        except Exception as exc:
            with self._lock:
                if generation == self._instruments_generation:
                    self._instruments_at = 0.0
                    self._instruments_quality.update(state="ERROR", complete=False,
                                                     error_code=type(exc).__name__)
            raise
        with self._lock:
            if generation != self._instruments_generation:
                raise BrokerFehler("OKX_INSTRUMENT_REQUEST_SUPERSEDED: neuere Anfrage vorhanden")
            self._instruments = gefunden
            self._instruments_at = time.time()
            self._instruments_quality.update(
                state="OK", complete=True, source=source, error_code="",
                last_success_at=datetime.now(timezone.utc).isoformat(),
                generation=generation, instrument_count=len(gefunden))
        return dict(gefunden)

    def public_instruments(self, *, force: bool = False,
                           max_age: float = 3600.0) -> dict[str, OKXInstrument]:
        """Breiter oeffentlicher SPOT-Katalog fuer die Entdeckungsstufe.

        Dieser Katalog ist niemals ein Ausfuehrungsbeweis. Fuer Orders und
        den handelbaren Pool bleibt ``instruments()`` mit Zugangsdaten die
        autoritative kontospezifische Schnittmenge samt tradeQuoteCcyList.
        """
        with self._lock:
            frisch = (time.time() - self._public_instruments_at) < max_age
            if self._public_instruments and frisch and not force:
                return dict(self._public_instruments)
        rows = self.request(
            "GET", "/public/instruments", params={"instType": "SPOT"})
        gefunden = {}
        for row in rows:
            inst = OKXInstrument.from_api(row)
            if inst.inst_id:
                gefunden[inst.inst_id] = inst
        with self._lock:
            self._public_instruments = gefunden
            self._public_instruments_at = time.time()
        return dict(gefunden)

    def instrument(self, inst_id: str) -> Optional[OKXInstrument]:
        inst_id = normalize_inst_id(inst_id)
        alle = self.instruments()
        if inst_id in alle:
            return alle[inst_id]
        alle = self.instruments(force=True)
        return alle.get(inst_id)

    def invalidate_instruments(self) -> None:
        with self._lock:
            self._instruments_at = 0.0

    def tickers(self) -> dict[str, OKXTicker]:
        """Alle SPOT-Ticker in EINEM Aufruf.

        Genau das ist der guenstige Vorfilter aus dem CCXT-Lernpunkt: ein
        Request liefert Preis, Bid/Ask und 24h-Volumen fuer alle Maerkte.
        """
        rows = self.request("GET", "/market/tickers", params={"instType": "SPOT"})
        return {t.inst_id: t for t in (OKXTicker.from_api(r) for r in rows) if t.inst_id}

    def ticker(self, inst_id: str) -> Optional[OKXTicker]:
        rows = self.request("GET", "/market/ticker", params={"instId": normalize_inst_id(inst_id)})
        return OKXTicker.from_api(rows[0]) if rows else None

    def orderbook(self, inst_id: str, depth: int = 20) -> dict:
        rows = self.request("GET", "/market/books",
                            params={"instId": normalize_inst_id(inst_id), "sz": int(depth)})
        if not rows:
            return {"bids": [], "asks": []}
        row = rows[0]
        return {
            "bids": [(_f(p), _f(q)) for p, q, *_ in (row.get("bids") or [])],
            "asks": [(_f(p), _f(q)) for p, q, *_ in (row.get("asks") or [])],
            "timestamp_ms": int(_f(row.get("ts"), 0.0)),
            "market_data_environment": "DEMO" if self.demo else "LIVE",
            "source": "/api/v5/market/books",
        }

    def price_limit(self, inst_id: str) -> list:
        """Uncached dynamic band, using the client's own trading environment."""
        return self.request("GET", "/public/price-limit",
                            params={"instId": normalize_inst_id(inst_id)})

    def _candle_frame(self, rows, *, inst_id, bar, nur_abgeschlossen):
        """Strict OHLCV parsing: unknown/invalid fields never become zero."""
        columns = ["open", "high", "low", "close", "volume", "quote_volume"]
        values = []
        for row in rows or []:
            if not isinstance(row, (list, tuple)) or len(row) < 9:
                raise BrokerFehler("OKX_CANDLE_SCHEMA_INVALID: OHLCV/Confirm-Felder fehlen")
            confirm = str(row[8])
            if confirm not in {"0", "1"}:
                raise BrokerFehler("OKX_CANDLE_CONFIRMATION_UNKNOWN: Kerzenabschluss unbekannt")
            if nur_abgeschlossen and confirm != "1":
                continue
            try:
                if any(isinstance(row[i], bool) for i in (0, 1, 2, 3, 4, 5, 7)):
                    raise ValueError("boolean OHLCV")
                stamp = float(row[0])
                numbers = [float(row[i]) for i in (1, 2, 3, 4, 5, 7)]
                o, h, lo, c, vol, qvol = numbers
                if (not math.isfinite(stamp) or stamp <= 0 or stamp != int(stamp)
                        or not all(math.isfinite(v) for v in numbers)
                        or min(o, h, lo, c) <= 0 or min(vol, qvol) < 0
                        or h < max(o, lo, c) or lo > min(o, h, c)):
                    raise ValueError("invalid OHLCV")
                values.append({"date": pd.to_datetime(int(stamp), unit="ms", utc=True),
                               **dict(zip(columns, numbers))})
            except (TypeError, ValueError, OverflowError):
                raise BrokerFehler("OKX_CANDLE_VALUES_INVALID: ungueltige Kerzenwerte") from None
        frame = (pd.DataFrame(values).sort_values("date").set_index("date")
                 if values else pd.DataFrame(columns=columns, index=pd.DatetimeIndex([], tz="UTC")))
        frame.attrs.update(broker_rows_only=True,
            confirmation="OKX_CONFIRM_1_FILTERED" if nur_abgeschlossen else "INCLUDES_OPEN_CANDLES",
            source_receipt=getattr(getattr(self, "_candle_receipts", None), "last", None))
        try:
            from candle_observation import quality, publish
            observation = quality(frame, base_url=self.base_url,
                environment="DEMO" if self.demo else "LIVE", instrument=inst_id,
                timeframe=bar, seconds=BAR_SECONDS.get(bar, 900), consumer="broker_response")
            frame.attrs["candle_quality"] = observation
            publish(observation)
        except Exception:
            logger.debug("OKX-Kerzenqualitaet nicht beobachtbar", exc_info=True)
        return frame[columns]

    def candles(self, inst_id: str, bar: str = "15m", limit: int = 300,
                *, nur_abgeschlossen: bool = True) -> pd.DataFrame:
        """UTC candles, oldest first; only confirmed candles by default."""
        inst_id = normalize_inst_id(inst_id)
        limit = max(1, min(300, int(limit)))
        self._candle_receipts.last = None
        rows = self.request("GET", "/market/candles",
                            params={"instId": inst_id, "bar": bar, "limit": limit})
        return self._candle_frame(rows, inst_id=inst_id, bar=bar, nur_abgeschlossen=nur_abgeschlossen)

    def historical_candles(self, inst_id: str, bar: str = "15m", limit: int = 100,
                           *, end_ms: int | None = None,
                           nur_abgeschlossen: bool = True) -> pd.DataFrame:
        """Confirmed history, strictly before the optional OKX `after` cursor."""
        inst_id = normalize_inst_id(inst_id)
        limit = max(1, min(100, int(limit)))
        params = {"instId": inst_id, "bar": bar, "limit": limit}
        if end_ms is not None:
            params["after"] = int(end_ms)
        self._candle_receipts.last = None
        rows = self.request("GET", "/market/history-candles", params=params)
        return self._candle_frame(rows, inst_id=inst_id, bar=bar, nur_abgeschlossen=nur_abgeschlossen)

    def server_time_ms(self) -> int:
        lokal_vorher = time.time()
        rows = self.request("GET", "/public/time")
        lokal_nachher = time.time()
        server_ms = int(_f(rows[0].get("ts"), 0.0)) if rows else 0
        if server_ms:
            lokal_mitte = (lokal_vorher + lokal_nachher) / 2.0
            self._clock_offset_seconds = (server_ms / 1000.0) - lokal_mitte
        return server_ms

    @property
    def clock_offset_seconds(self) -> float:
        return float(self._clock_offset_seconds)

    # -- Konto --------------------------------------------------------------
    def balances(self) -> dict[str, dict]:
        """Guthaben je Waehrung: {'EUR': {'cash':..,'frozen':..,'eq_usd':..}}"""
        rows = self.request("GET", "/account/balance", private=True)
        out: dict[str, dict] = {}
        for konto in rows:
            for detail in konto.get("details") or []:
                ccy = str(detail.get("ccy", "")).upper()
                if not ccy:
                    continue
                out[ccy] = {
                    "cash": _f(detail.get("availBal")),
                    "gesamt": _f(detail.get("cashBal")) or _f(detail.get("eq")),
                    "frozen": _f(detail.get("frozenBal")),
                    "eq_usd": _f(detail.get("eqUsd")),
                }
        return out

    def total_equity_usd(self) -> float:
        rows = self.request("GET", "/account/balance", private=True)
        if not rows:
            return 0.0
        return _f(rows[0].get("totalEq"))

    def trade_fee(self, inst_type: str = "SPOT", inst_id: str = "") -> dict:
        """Der tatsaechliche Gebuehrensatz dieses Kontos.

        OKX liefert Maker/Taker als NEGATIVE Bruchzahlen ("-0.0035" = 0,35 %).
        Bis v8.1.3 nahm der Bot pauschal 0,10 % an -- das ist der Tarif MIT
        eroeffnetem Derivate-Konto (X-Perps). Ohne eines sind es 0,35 %, und
        die Kostenhuerde war damit nur halb so hoch wie noetig.
        """
        params = {"instType": str(inst_type or "SPOT").upper()}
        if inst_id:
            params["instId"] = normalize_inst_id(inst_id)
        rows = self.request("GET", "/account/trade-fee", params=params, private=True)
        if not rows:
            return {}
        zeile = rows[0]
        return {
            "maker": abs(_f(zeile.get("maker"))),
            "taker": abs(_f(zeile.get("taker"))),
            "taker_known": (zeile.get("taker") not in (None, "")
                            and math.isfinite(_f(zeile.get("taker"), float("nan")))),
            "stufe": str(zeile.get("level", "") or ""),
        }

    def account_config(self) -> dict:
        rows = self.request("GET", "/account/config", private=True)
        return dict(rows[0]) if rows else {}

    def account_snapshot(self, *, fills_limit: int = 20) -> dict:
        """Vollstaendige, aber ausschliesslich lesende OKX-Kontoaufnahme.

        Der normale Handelszyklus verwendet weiter gezielte, sparsame
        Einzelaufrufe. Diese Aufnahme ist nur fuer ``crypto_diagnose --voll``
        gedacht: Sie macht sichtbar, ob EEA-Demo, Guthaben, Gebuehren,
        Standardorders, beide Schutzorderarten und Fills wirklich vom selben
        Konto gelesen werden. Ein einzelner optionaler Endpunkt macht die
        Diagnose nicht unbrauchbar; er steht stattdessen unter ``fehler``.
        """
        fehler: dict[str, str] = {}

        def lese(name: str, fn, standard):
            try:
                return fn()
            except Exception as exc:
                fehler[name] = type(exc).__name__
                return standard

        konto = lese("konto_config", self.account_config, {})
        snapshot = {
            "zeit": datetime.now(timezone.utc).isoformat(),
            "eea_endpoint": self.base_url,
            "demo": self.demo,
            "konto_config": {
                # Keine Geheimnisse und keine UID: nur die Betriebsmerkmale,
                # die fuer Spot-/Key-Pruefung relevant sind.
                "acctLv": str(konto.get("acctLv", "") or ""),
                "posMode": str(konto.get("posMode", "") or ""),
                "perm": konto.get("perm") or "",
            },
            "guthaben": lese("guthaben", self.balances, {}),
            "equity_usd": lese("equity_usd", self.total_equity_usd, 0.0),
            "gebuehren": lese("gebuehren", lambda: self.trade_fee("SPOT"), {}),
            "offene_standard_orders": lese("offene_standard_orders", self.pending_orders, []),
            "schutzorders_oco": lese(
                "schutzorders_oco", lambda: self.pending_algo_orders(ord_type="oco"), []),
            "schutzorders_conditional": lese(
                "schutzorders_conditional",
                lambda: self.pending_algo_orders(ord_type="conditional"), []),
            "letzte_fills": lese(
                "letzte_fills", lambda: self.fills(limit=max(1, min(100, int(fills_limit)))), []),
        }
        snapshot["fehler"] = fehler
        snapshot["vollstaendig"] = not bool(fehler)
        return snapshot

    # -- Orders -------------------------------------------------------------
    def place_order(self, body: dict) -> dict:
        rows = self.request("POST", "/trade/order", body=body, private=True, is_order=True)
        return rows[0] if rows else {}

    def order_status(self, inst_id: str, *, ord_id: str = "", cl_ord_id: str = "") -> dict:
        params = {"instId": normalize_inst_id(inst_id)}
        if ord_id:
            params["ordId"] = ord_id
        elif cl_ord_id:
            params["clOrdId"] = cl_ord_id
        else:
            raise BrokerFehler("Orderabfrage ohne ordId/clOrdId ist nicht moeglich.")
        rows = self.request("GET", "/trade/order", params=params, private=True)
        return rows[0] if rows else {}

    def pending_orders(self, inst_id: str = "") -> list[dict]:
        params = {"instType": "SPOT"}
        if inst_id:
            params["instId"] = normalize_inst_id(inst_id)
        return self.request("GET", "/trade/orders-pending", params=params, private=True)

    def order_history_match(self, inst_id: str, *, ord_id="", cl_ord_id="", side="") -> dict:
        """Find an exact terminal order; an empty/limited history is no rejection proof."""
        if not ord_id and not cl_ord_id:
            return {}
        for endpoint in ("/trade/orders-history", "/trade/orders-history-archive"):
            after = ""
            for _ in range(5):
                params = {"instType": "SPOT", "instId": normalize_inst_id(inst_id), "limit": "100"}
                if after:
                    params["after"] = after
                rows = self.request("GET", endpoint, params=params, private=True)
                matches = [r for r in rows if r.get('instId') == inst_id
                           and r.get('side') == side
                           and ((ord_id and r.get('ordId') == ord_id)
                                or (not ord_id and r.get('clOrdId') == cl_ord_id))]
                if matches:
                    if len(matches) != 1 or (cl_ord_id and matches[0].get('clOrdId') not in ('', None, cl_ord_id)):
                        raise BrokerFehler('Widerspruechliche Orderidentitaet im OKX-Archiv')
                    return matches[0]
                next_after = str(rows[-1].get('ordId') or '') if rows else ''
                if len(rows) < 100 or not next_after or next_after == after:
                    break
                after = next_after
        return {}

    def cancel_order(self, inst_id: str, ord_id: str) -> dict:
        rows = self.request("POST", "/trade/cancel-order",
                            body={"instId": normalize_inst_id(inst_id), "ordId": str(ord_id)},
                            private=True, is_order=True)
        return rows[0] if rows else {}

    def place_algo_order(self, body: dict) -> dict:
        rows = self.request("POST", "/trade/order-algo", body=body, private=True, is_order=True)
        return rows[0] if rows else {}

    def pending_algo_orders(self, inst_id: str = "", ord_type: str = "oco") -> list[dict]:
        params = {"instType": "SPOT", "ordType": ord_type}
        if inst_id:
            params["instId"] = normalize_inst_id(inst_id)
        return self.request("GET", "/trade/orders-algo-pending", params=params, private=True)

    def algo_order_details(self, algo_id: str) -> dict:
        """Eine Algo-Order samt eventuell erzeugter normaler ordId."""
        if not str(algo_id or "").strip():
            return {}
        rows = self.request("GET", "/trade/order-algo",
                            params={"algoId": str(algo_id)}, private=True)
        return rows[0] if rows else {}

    def cancel_algo_orders(self, eintraege: list[dict]) -> list[dict]:
        if not eintraege:
            return []
        return self.request("POST", "/trade/cancel-algos", body=eintraege,
                            private=True, is_order=True)

    def amend_algo_order(self, body: dict) -> dict:
        rows = self.request("POST", "/trade/amend-algos", body=body,
                            private=True, is_order=True)
        return rows[0] if rows else {}

    def fills(self, inst_id: str = "", limit: int = 100, *, ord_id: str = "") -> list[dict]:
        params = {"instType": "SPOT", "limit": str(max(1, min(100, int(limit))))}
        if inst_id:
            params["instId"] = normalize_inst_id(inst_id)
        if ord_id:
            params["ordId"] = str(ord_id)
        return self.request("GET", "/trade/fills", params=params, private=True)

    def fills_history(self, inst_id: str = "", *, limit: int = 100,
                      after: str = "", before: str = "", ord_id: str = "") -> list[dict]:
        """Archivierte Spot-Fills (OKX: bis zu drei Monate) seitenweise."""
        params = {"instType": "SPOT", "limit": str(max(1, min(100, int(limit))))}
        if inst_id:
            params["instId"] = normalize_inst_id(inst_id)
        if after:
            params["after"] = str(after)
        if before:
            params["before"] = str(before)
        if ord_id:
            params["ordId"] = str(ord_id)
        return self.request("GET", "/trade/fills-history", params=params, private=True)

    def fills_history_paginated(self, inst_id: str = "", *,
                                max_pages: int = 20, ord_id: str = "") -> list[dict]:
        """Historie dedupliziert laden und Vollstaendigkeit explizit merken.

        9.7: Das Erreichen von max_pages ist KEIN Beweis fuer eine vollstaendige
        Historie. Negative Orderbeweise duerfen diesen Zustand nicht als
        ``kein Fill`` interpretieren.
        """
        rows: list[dict] = []
        seen_rows: dict[str, dict] = {}
        after = ""
        complete = False
        for _page in range(max(1, int(max_pages))):
            batch = self.fills_history(inst_id, limit=100, after=after, ord_id=ord_id)
            fresh = []
            for row in batch:
                key = okx_fill_identity(row)
                if key in seen_rows:
                    merged = _merge_fill_receipt(seen_rows[key], row)
                    seen_rows[key].clear()
                    seen_rows[key].update(merged)
                    continue
                item = dict(row)
                seen_rows[key] = item
                fresh.append(item)
            rows.extend(fresh)
            if len(batch) < 100:
                complete = True
                break
            if not fresh:
                # A repeated full page is a stuck cursor, not end-of-history.
                break
            next_after = str(batch[-1].get("billId") or "")
            if not next_after or next_after == after:
                break
            after = next_after
        self._last_fills_history_complete = bool(complete)
        return rows


# ---------------------------------------------------------------------------
# Broker-Adapter
# ---------------------------------------------------------------------------
class OKXBroker(BrokerBase):
    """OKX-Spot-Adapter fuer den Geldpfad.

    Der Adapter handelt ausschliesslich SPOT im Cash-Modus (tdMode='cash').
    Margin, Futures, Swaps und Optionen sind bewusst NICHT umgesetzt: sie
    koennen ein Konto ueber den Einsatz hinaus belasten, was der Bot
    strukturell nicht absichern kann.
    """

    name = "OKX"

    def __init__(self, *, api_key: str = "", api_secret: str = "",
                 passphrase: str = "", demo: Optional[bool] = None,
                 quote_ccy: str = "", allowed_quotes: Optional[Iterable[str]] = None,
                 client: Optional[OKXClient] = None):
        import config

        if demo is None:
            demo = not bool(getattr(config, "OKX_LIVE_TRADING", False))
        self.demo = bool(demo)
        self.quote_ccy = str(quote_ccy or getattr(config, "OKX_QUOTE_CCY", "EUR")).upper()
        configured = allowed_quotes or getattr(
            config, "OKX_ALLOWED_QUOTE_CCY", ("EUR", "USD", "USDC"))
        self.allowed_quotes = tuple(dict.fromkeys(
            str(x).upper() for x in configured
            if str(x).upper() in {"EUR", "USD", "USDC"}
        )) or ("EUR", "USD", "USDC")
        if self.quote_ccy not in self.allowed_quotes:
            self.quote_ccy = self.allowed_quotes[0]
        self.max_clock_drift = float(getattr(config, "OKX_MAX_CLOCK_DRIFT_SECONDS", 25.0))

        if client is not None:
            self.client = client
        else:
            key = api_key or self._cfg(config, "OKX_DEMO_API_KEY", "OKX_API_KEY")
            secret = api_secret or self._cfg(config, "OKX_DEMO_API_SECRET", "OKX_API_SECRET")
            phrase = passphrase or self._cfg(config, "OKX_DEMO_API_PASSPHRASE", "OKX_API_PASSPHRASE")
            self.client = OKXClient(
                key, secret, phrase, demo=self.demo,
                base_url=str(getattr(config, "OKX_BASE_URL", OKX_BASE_URL)),
                timeout=float(getattr(config, "OKX_TIMEOUT_SECONDS", 15.0)),
            )

        self._connected = False
        self._equity_cache: tuple[float, float] = (0.0, 0.0)
        # Gemessener Taker-Satz (Wert, Zeitpunkt). Wird stuendlich erneuert.
        self._gebuehr_cache: tuple[float, float] = (0.0, 0.0)
        # Ergebnis der letzten Stornierung -- damit ein Fehlschlag nicht nur
        # im Protokoll steht, sondern gemeldet werden kann.
        self.letzte_stornierung: dict = {}
        # 9.5.8: Zeitpunkt je Instrument, zu dem zuletzt Guthaben gebunden ODER
        # freigegeben wurde (Storno, beendeter FOK). OKX quittiert das eine vor
        # dem anderen; nur in diesem Zeitfenster lohnt es sich, auf die
        # Freigabe zu WARTEN statt einmal zu lesen. Ausserhalb waere Warten
        # reine Verzoegerung in jedem Zyklus.
        self._freigabe_erwartet: dict[str, float] = {}
        self._last_health = 0.0
        self._lock = threading.RLock()
        self._stream = None
        self._account_fingerprint = ""
        self._last_rest_balance_at = 0.0
        self._rest_balance_cache: dict[str, dict] = {}
        self._quote_rate_cache: tuple[float, dict[tuple[str, str], float]] = (0.0, {})
        # Letzter read-only Bewertungsbeleg fuer die Risikobasis. Er enthaelt
        # nur Waehrungen/Betragswerte und beobachtete Umrechnungskurse, keine
        # API-Geheimnisse oder Brokerantworten.
        self._last_capital_evidence: dict = {}
        self._health_failures = 0
        self._health_state = "OFFLINE"
        self._health_detail = "noch nicht verbunden"
        self._last_health_success_at = 0.0

    def _trade_quote_ccy(self, meta: OKXInstrument, *, require_cash: bool = False) -> str:
        """Zulaessige Abrechnungswaehrung fuer genau dieses Instrument.

        Ein USD-Markt ist bei OKX Unified USD nicht auf USD-Guthaben
        beschraenkt. Entscheidend ist tradeQuoteCcyList. Ohne explizite Liste
        gilt aus Kompatibilitaetsgruenden nur die normale quoteCcy.
        """
        accepted = tuple(dict.fromkeys(
            str(x).upper() for x in (meta.trade_quote_ccy_list or (meta.quote_ccy,))
            if str(x).strip()
        ))
        permitted = tuple(self.allowed_quotes)
        if not set(accepted).intersection(permitted):
            raise BrokerFehler(
                f"{meta.inst_id}: keine erlaubte tradeQuoteCcy; OKX meldet "
                f"{', '.join(accepted) or 'keine'}, Bot erlaubt "
                f"{', '.join(permitted) or 'keine'}."
            )
        candidates = tuple(dict.fromkeys(
            [self.quote_ccy, *self.allowed_quotes]
        ))
        balances = self.client.balances() if require_cash else {}
        for ccy in candidates:
            if ccy not in accepted or ccy not in permitted:
                continue
            if require_cash and float((balances.get(ccy) or {}).get("cash", 0.0) or 0.0) <= 0:
                continue
            return ccy
        if require_cash:
            raise BrokerFehler(
                f"{meta.inst_id}: keine der von OKX erlaubten Abrechnungswaehrungen "
                f"({', '.join(accepted) or 'keine'}) besitzt frei verfuegbares Guthaben."
            )
        raise BrokerFehler(
            f"{meta.inst_id}: keine erlaubte tradeQuoteCcy; OKX meldet "
            f"{', '.join(accepted) or 'keine'}."
        )

    def _cfg(self, config, demo_name: str, live_name: str) -> str:
        """Demo- und Live-Schluessel sind strikt getrennt.

        Ein versehentlich im Live-Feld hinterlegter Demo-Key darf niemals
        still fuer den anderen Modus verwendet werden.
        """
        name = demo_name if self.demo else live_name
        return str(getattr(config, name, "") or "").strip()

    def _entry_trade_quote(self, meta, submit_context) -> str:
        """Die bereits dimensionierte Cash-Lane bleibt bis zum Fill fest."""
        quote = str(submit_context.get("trade_quote_ccy") or "").upper()
        if not quote:
            return self._trade_quote_ccy(meta, require_cash=True)
        self._protection_quote(meta, quote)
        if quote not in self.allowed_quotes:
            raise BrokerFehler(f"{meta.inst_id}: geplante Abrechnung {quote} ist fuer neue Kaeufe gesperrt")
        balance = self.client.balances().get(quote) or {}
        if _f(balance.get("cash")) <= 0:
            raise BrokerFehler(f"{meta.inst_id}: geplantes {quote}-Guthaben fehlt; kein Waehrungswechsel")
        return quote

    def initial_entry_route(self, symbol, current):
        from okx_entry_routing import initial_route
        return initial_route(self, symbol, current)

    def compare_entry_routes(self, symbol, quantity, cfg, *, reserved=None):
        from okx_entry_routing import compare_routes
        return compare_routes(self, symbol, quantity, cfg, reserved=reserved)

    # -- Verbindung ---------------------------------------------------------
    def connect(self) -> bool:
        self._connected = False
        if not self.client.hat_zugangsdaten:
            raise AuthentifizierungsFehler(
                "OKX-Zugangsdaten fehlen. Bitte API-Key, Secret und Passphrase "
                f"fuer den {'Demo' if self.demo else 'Live'}-Modus hinterlegen."
            )
        server_ms = self.client.server_time_ms()
        drift = abs(float(getattr(self.client, "clock_offset_seconds", 0.0))) \
            if server_ms else float("inf")
        if drift > self.max_clock_drift:
            raise Zeitabweichung(
                f"OKX {'DEMO' if self.demo else 'LIVE'} voruebergehend blockiert: "
                f"Systemzeit weicht {drift:.1f} s ab (Maximum "
                f"{self.max_clock_drift:.1f} s). NTP synchronisieren; der Bot versucht "
                "die Verbindung automatisch erneut."
            )

        # Private Aufrufe beweisen, dass Schluessel UND Passphrase stimmen.
        self.client.instruments(force=True)
        account_cfg = self.client.account_config()
        # Nur ein irreversibler Hash wird persistiert. Dadurch kann ein
        # importierter Trade nicht still mit Demo/Live oder einem anderen
        # Unterkonto vermischt werden, ohne UID/Schluessel preiszugeben.
        if not str(account_cfg.get("uid") or "").strip():
            raise AuthentifizierungsFehler("OKX_ACCOUNT_IDENTITY_UNKNOWN: UID fehlt")
        account_marker = "|".join((
            "demo" if self.demo else "live", str(self.client.base_url),
            str(account_cfg.get("uid") or account_cfg.get("mainUid") or ""),
            str(account_cfg.get("subAcct") or account_cfg.get("label") or ""),
        ))
        self._account_fingerprint = hashlib.sha256(
            account_marker.encode("utf-8")).hexdigest()[:24]
        from okx_account_context import bind_verified
        bind_verified(self._account_fingerprint, "DEMO" if self.demo else "LIVE")
        raw_permissions = account_cfg.get("perm") or ""
        if isinstance(raw_permissions, (list, tuple, set)):
            permission_items = raw_permissions
        else:
            permission_items = str(raw_permissions).replace(";", ",").split(",")
        permissions = {str(item).strip().lower() for item in permission_items if str(item).strip()}
        if not self.demo and "withdraw" in permissions:
            raise AuthentifizierungsFehler(
                "OKX LIVE blockiert: Der API-Key besitzt Withdraw-Rechte. "
                "Bitte einen Key nur mit Read + Trade anlegen."
            )
        if not self.demo and permissions and "trade" not in permissions:
            raise AuthentifizierungsFehler("OKX LIVE blockiert: Dem API-Key fehlt Trade-Recht.")
        equity = self.client.total_equity_usd()
        with self._lock:
            self._equity_cache = (float(equity), time.time())
            self._connected = True
            self._health_failures = 0
            self._health_state = "ONLINE"
            self._health_detail = "REST authentifiziert"
            self._last_health_success_at = time.time()
        self._start_stream_best_effort()
        logger.info("OKX verbunden (%s), Kontowert %.2f USD",
                    "DEMO" if self.demo else "LIVE", equity)
        return True

    def account_fingerprint(self) -> str:
        return str(self._account_fingerprint or "")

    def protection_exit_order_ids(self, algo_id: str) -> set[str]:
        """Vom Broker belegte normale Order-IDs einer Schutzorder."""
        try:
            row = self.client.algo_order_details(str(algo_id or ""))
        except BrokerFehler:
            return set()
        if row.get("algoId") and str(row["algoId"]) != str(algo_id):
            raise BrokerFehler("Widerspruechliche Algo-ID im Schutzbeleg")
        ids: set[str] = set()
        direct = str(row.get("ordId") or "").strip()
        if direct:
            ids.add(direct)
        raw_list = row.get("ordIdList") or row.get("ordIds") or []
        if isinstance(raw_list, str):
            raw_list = [x for x in re.split(r"[,;\s]+", raw_list) if x]
        for value in raw_list if isinstance(raw_list, (list, tuple, set)) else []:
            if isinstance(value, dict):
                value = value.get("ordId")
            if str(value or "").strip():
                ids.add(str(value).strip())
        return ids

    def historical_fills(self, symbol: str, *, since: str = "") -> list[dict]:
        """Alle archivierten Fills eines Basiswerts seit einem UTC-Zeitpunkt."""
        rows = self.client.fills_history_paginated(
            max_pages=int(getattr(__import__("config"), "OKX_FILL_HISTORY_MAX_PAGES", 20)))
        wanted = str(symbol or "").upper()
        since_ms = 0
        if since:
            try:
                dt = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                since_ms = int(dt.timestamp() * 1000)
            except (TypeError, ValueError):
                since_ms = 0
        result = [
            dict(row) for row in rows
            if base_currency(str(row.get("instId") or "")) == wanted
            and int(_f(row.get("ts"), 0.0)) >= since_ms
        ]
        result.sort(key=lambda x: (int(_f(x.get("ts"), 0.0)),
                                   str(x.get("tradeId") or "")))
        return result

    def external_exit_evidence(self, *, inst_id: str, since: str,
                               expected_qty: float,
                               expected_order_ids=None,
                               fremdverkauf_erlauben: bool = False,
                               entry_order_id: str = "") -> dict:
        """Belegt einen broker-/manuell ausgefuehrten Verkauf mit echten Fills.

        Symbolnaehe allein reicht nicht: Instrument, Richtung, Zeitpunkt,
        tradeId und die fast vollstaendige Sollmenge muessen zusammenpassen.

        KORREKTUR 9.5.5 -- der Grund, warum ein manueller Verkauf nie
        abgerechnet wurde.

        Bis 9.5.4 stand hier:

            allowed_orders = {...}
            if not allowed_orders:
                return {}

        Belegt werden konnte damit ausschliesslich ein Verkauf, dessen
        ordId NEXUS vorher schon kannte -- also ein eigener Exit oder eine
        ausgeloeste eigene Schutzorder. Ein Verkauf, den der Nutzer selbst in
        der OKX-App ausloest, hat eine ordId, die in keiner dieser Quellen
        steht. Er war strukturell nie beweisbar, und die Position wurde
        anschliessend mit "Ergebnis bleibt unbekannt" geschlossen.

        Mit ``fremdverkauf_erlauben`` werden auch SELL-Fills FREMDER Orders
        akzeptiert. Das ist kein Aufweichen des Eigentumsbeweises: die Fills
        stammen aus dem Konto des Bots, tragen echte tradeIds und werden bei
        OKX abgefragt, nicht rekonstruiert. Ein OKX-Spot-Guthaben ist
        fungibel -- wird verkauft, betrifft es denselben Bestand, egal wer die
        Order gesendet hat. Das Ergebnis wird als ``beweisart`` gekennzeichnet,
        damit der Aufrufer einen Fremdverkauf nie faelschlich als eigenen
        Ausstieg verbucht.
        """
        inst_id = normalize_inst_id(inst_id)
        meta = self.client.instrument(inst_id)
        if meta is None:
            return {}
        allowed_orders = {str(x) for x in (expected_order_ids or []) if str(x)}
        if not allowed_orders and not fremdverkauf_erlauben:
            return {}
        archived = self.historical_fills(meta.base_ccy, since=since)
        try:
            recent = self.client.fills(inst_id, limit=100)
        except BrokerFehler:
            recent = []
        since_ms = 0
        try:
            opened = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            since_ms = int(opened.timestamp() * 1000)
        except (TypeError, ValueError):
            pass
        combined: dict[str, dict] = {}
        for row in [*archived, *recent]:
            key = okx_fill_identity(row, self.account_fingerprint())
            if key and int(_f(row.get("ts"), 0.0)) >= since_ms:
                core = ('instId', 'ordId', 'tradeId', 'side', 'fillSz', 'fillPx', 'fee', 'feeCcy', 'ts')
                if key in combined and any(str(combined[key].get(k, '')) != str(row.get(k, '')) for k in core):
                    return {}  # A repeated identity is not permission to replace its execution.
                combined[key] = dict(row)
        known_sell = any(r.get("side") == "sell" and str(r.get("ordId") or "") in allowed_orders
                         for r in combined.values())
        if fremdverkauf_erlauben and entry_order_id and not known_sell:
            from okx_external_settlement import native_receipt
            groups = {}
            extra_buys = [r for r in combined.values() if r.get("side") == "buy"
                          and str(r.get("ordId")) != entry_order_id]
            for row in combined.values():
                other = str(row.get("instId") or "")
                if (row.get("side") == "sell" and str(row.get("ordId") or "") not in allowed_orders
                        and base_currency(other) == meta.base_ccy):
                    groups.setdefault((other, str(row.get("ordId") or "")), []).append(row)
            candidates = []
            for (other, oid), fills in groups.items():
                if extra_buys or not oid:
                    continue
                try:
                    status = self.client.order_status(other, ord_id=oid)
                    if (status.get("instId") != other or str(status.get("ordId")) != oid
                            or status.get("side") != "sell" or status.get("state") != "filled"):
                        continue
                    raw_qty = sum(Decimal(str(r["fillSz"])) for r in fills)
                    if raw_qty != Decimal(str(status.get("accFillSz"))):
                        continue
                    quote_ccy = str(status.get("tradeQuoteCcy") or other.split('-')[-1])
                    candidates.append(native_receipt([dict(r,tradeQuoteCcy=quote_ccy) for r in fills],
                        account=self.account_fingerprint(), environment="DEMO" if self.demo else "LIVE",
                        entry_instrument=inst_id, expected_quantity=expected_qty,
                        lot_size=meta.lot_size, expected_order_id=oid))
                except (BrokerFehler, ValueError, TypeError, InvalidOperation):
                    continue
            if len(candidates) == 1:
                return candidates[0]
            if candidates:
                return {}
        if not allowed_orders:
            return {}  # Foreign exits require one whole order and an anchored entry above.
        def _passt(row) -> bool:
            if normalize_inst_id(str(row.get("instId") or "")) != inst_id:
                return False
            if str(row.get("side") or "").lower() != "sell":
                return False
            if not str(row.get("tradeId") or "").strip():
                return False
            if allowed_orders:
                return str(row.get("ordId") or "") in allowed_orders
            return True

        rows = [row for row in combined.values() if _passt(row)]
        # Stammt kein einziger Fill aus einer bekannten Order, war es ein
        # Fremdverkauf. Der Aufrufer muss das unterscheiden koennen.
        fremd = bool(rows) and not allowed_orders
        rows.sort(key=lambda row: (int(_f(row.get("ts"), 0.0)),
                                   str(row.get("tradeId") or "")))
        if not rows:
            return {}
        # Nur die juengste, zur aktuell verschwundenen Buchmenge passende
        # Verkaufsgruppe verwenden. Aeltere Teilverkaeufe duerfen weder
        # Menge noch Gebuehren ein zweites Mal in diesen Abschluss tragen.
        selected: list[dict] = []
        selected_qty = 0.0
        target = max(0.0, float(expected_qty or 0.0))
        for row in reversed(rows):
            rest = target - selected_qty if target > 0 else _f(row.get("fillSz"))
            menge = _f(row.get("fillSz"))
            if target > 0 and menge > rest > 0:
                # A physical fill ID must not be rewritten to a smaller fill.
                # Keep ambiguous inventory allocation in reconciliation.
                return {}
            selected.append(row)
            selected_qty += menge
            if target > 0 and selected_qty + _f(meta.lot_size) > target:
                break
        rows = list(reversed(selected))
        quantity = sum(_f(row.get("fillSz")) for row in rows)
        lot = max(_f(meta.lot_size), 1e-12)
        expected = target
        if expected <= 0 or quantity + lot <= expected:
            return {}
        weighted = sum(_f(row.get("fillSz")) * _f(row.get("fillPx")) for row in rows)
        average = weighted / quantity if quantity > 0 else 0.0
        fees: dict[str, float] = {}
        fee_quote = 0.0
        fee_quote_known = True
        base_consumed = 0.0
        for row in rows:
            from execution_lifecycle import number
            if row.get("fee") in (None, ""):
                # Without fee currency/amount the consumed base inventory is
                # not established. Leave the existing position in reconciliation.
                return {}
            cost = -float(number(row["fee"]))
            ccy = str(row.get("feeCcy") or "").upper()
            if cost and not ccy:
                return {}
            if cost == 0:
                continue
            fees[ccy] = fees.get(ccy, 0.0) + cost
            if ccy == meta.base_ccy:
                base_consumed += cost
                fee_quote += cost * _f(row.get("fillPx"))
            elif ccy == meta.quote_ccy:
                fee_quote += cost
            else:
                fee_quote_known = False
        quantity += base_consumed
        if quantity > expected + 1e-12 or expected - quantity >= lot:
            return {}
        return {
            "confirmed": True, "inst_id": inst_id, "quantity": quantity,
            # 9.5.5: "EIGENER_EXIT" = der Verkauf gehoert zu einer Order, die
            # NEXUS kannte. "FREMDVERKAUF" = die Fills stammen aus dem Konto
            # des Bots, die Order hat aber jemand anderes gesendet (Nutzer in
            # der OKX-App). Beides ist mit echten tradeIds belegt; der
            # Unterschied entscheidet, wie gebucht und gemeldet wird.
            "beweisart": "FREMDVERKAUF" if fremd else "EIGENER_EXIT",
            "avg_price": average, "fees": fees, "fees_quote": fee_quote if fee_quote_known else None,
            "fill_ids": [okx_fill_identity(row, self.account_fingerprint())
                         for row in rows],
            "raw_fill_ids": [str(row.get("tradeId")) for row in rows],
            "order_ids": sorted({str(row.get("ordId")) for row in rows if row.get("ordId")}),
            "closed_at": datetime.fromtimestamp(
                max(int(_f(row.get("fillTime") or row.get("ts"), 0.0)) for row in rows) / 1000.0,
                tz=timezone.utc).isoformat(),
        }

    def composite_exit_evidence(self, *, inst_id: str, since: str,
                                expected_qty: float,
                                protection_algo_id: str = "",
                                entry_order_id: str = "") -> dict:
        """10.2.1: Mehrere terminale Verkaufsorders belegen ZUSAMMEN den Abgang.

        Ergaenzt ``external_exit_evidence`` fuer den realen Mischfall (DOGE,
        17.09.2026): eigener teilerfuellter Schutz-Exit plus manuelle Verkaeufe
        ueber ein ANDERES Handelspaar desselben Basiswerts, eine Order davon
        teilgefuellt-storniert. Jede Order wird einzeln terminal belegt
        (Status + tradeId-Fills, accFillSz == Fillsumme); erst die SUMME muss
        die Sollmenge bis auf Lot-Staub exakt decken. Erloese je Waehrung
        bleiben nativ (keine erfundenen Kurse). Jeder Zweifel liefert {}.
        """
        inst_id = normalize_inst_id(inst_id)
        meta = self.client.instrument(inst_id)
        if meta is None:
            return {}
        try:
            own_ids = (self.protection_exit_order_ids(protection_algo_id)
                       if protection_algo_id else set())
        except BrokerFehler:
            own_ids = set()
        archived = self.historical_fills(meta.base_ccy, since=since)
        since_ms = 0
        try:
            opened = datetime.fromisoformat(str(since).replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            since_ms = int(opened.timestamp() * 1000)
        except (TypeError, ValueError):
            pass
        insts = {inst_id} | {str(r.get("instId") or "") for r in archived if r.get("instId")}
        combined: dict[str, dict] = {}
        rows_all = list(archived)
        for other in sorted(x for x in insts if x):
            try:
                rows_all.extend(self.client.fills(other, limit=100))
            except BrokerFehler:
                continue
        for raw in rows_all:
            row = dict(raw)
            key = okx_fill_identity(row, self.account_fingerprint())
            if not key or int(_f(row.get("ts"), 0.0)) < since_ms:
                continue
            core = ('instId', 'ordId', 'tradeId', 'side', 'fillSz', 'fillPx', 'fee', 'feeCcy', 'ts')
            if key in combined and any(str(combined[key].get(k, '')) != str(row.get(k, '')) for k in core):
                return {}
            combined[key] = row
        # Zusaetzliche KAUF-Fills nach dem Einstieg machen die Bestands-
        # zuordnung mehrdeutig -- dann keine zusammengesetzte Behauptung.
        if any(r.get("side") == "buy" and str(r.get("ordId")) != str(entry_order_id or "")
               for r in combined.values()):
            return {}
        groups: dict[tuple, list] = {}
        for row in combined.values():
            other = str(row.get("instId") or "")
            if (row.get("side") == "sell" and str(row.get("tradeId") or "").strip()
                    and base_currency(other) == meta.base_ccy):
                groups.setdefault((other, str(row.get("ordId") or "")), []).append(row)
        order_groups = []
        for (other, oid), fills in sorted(groups.items()):
            if not oid:
                return {}
            try:
                status = self.client.order_status(other, ord_id=oid)
            except BrokerFehler:
                status = {}
            if not status:
                try:
                    status = self.client.order_history_match(other, ord_id=oid, side="sell")
                except BrokerFehler:
                    status = {}
            if not status:
                return {}
            raw_qty = sum(Decimal(str(r["fillSz"])) for r in fills)
            if (status.get("instId") != other or str(status.get("ordId")) != oid
                    or status.get("side") != "sell"
                    or str(status.get("state") or "") not in {"filled", "canceled", "mmp_canceled"}
                    or raw_qty != Decimal(str(status.get("accFillSz") or "0"))):
                return {}
            quote = str(status.get("tradeQuoteCcy") or other.split('-')[-1]).upper()
            order_groups.append(dict(
                status=dict(status),
                fills=[dict(r, tradeQuoteCcy=quote) for r in fills]))
        if not order_groups:
            return {}
        try:
            from okx_external_settlement import composite_receipt
            return composite_receipt(order_groups,
                account=self.account_fingerprint(),
                environment="DEMO" if self.demo else "LIVE",
                entry_instrument=inst_id, expected_quantity=expected_qty,
                lot_size=meta.lot_size, own_order_ids=own_ids)
        except (ValueError, InvalidOperation):
            return {}

    ECB_URL = ("https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml")

    def fx_followup_document(self, fill_times_ms, currencies) -> dict:
        """10.2.1: Rohbelege fuer die EUR-Referenzbewertung DIREKT abrufen.

        Bislang entstanden 'nexus-public-fx-followup-v1'-Dokumente nur aus
        Georgs manuell gelieferten Exporten (repair_okx_verified_history).
        Dieselben oeffentlichen, kontofreien Abrufe kann NEXUS selbst machen:
        USDC/EUR-Minutenkerzen von eea.okx.com und (nur falls ein USD-Leg
        existiert) die EZB-Tagesreferenz. Gespeichert wird die ROHE Antwort
        mit Pruefsumme; die strenge ``Rates``-Validierung bleibt der einzige
        Konsument -- es wird also exakt so geprueft wie bei der Handarbeit.
        """
        requests_out = []
        wanted = {str(c).upper() for c in (currencies or [])}
        session = getattr(self.client, "session", None)
        import requests as _rq
        http = session if session is not None else _rq
        if "USDC" in wanted:
            minutes = sorted({int(t) for t in fill_times_ms if str(t).isdigit()})
            for millis in minutes:
                url = (f"{self.client.base_url}/api/v5/market/history-candles"
                       f"?instId=USDC-EUR&bar=1m&after={millis}&limit=5")
                antwort = http.get(url, timeout=15)
                body = antwort.text
                requests_out.append(dict(
                    environment="LIVE_REFERENCE", url=url,
                    http_status=int(antwort.status_code), body_utf8=body,
                    body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                    body_bytes=len(body.encode())))
        if "USD" in wanted:
            antwort = http.get(self.ECB_URL, timeout=20)
            body = antwort.text
            requests_out.append(dict(
                environment="ECB_DAILY_REFERENCE", url=self.ECB_URL,
                http_status=int(antwort.status_code), body_utf8=body,
                body_sha256=hashlib.sha256(body.encode()).hexdigest(),
                body_bytes=len(body.encode())))
        return dict(schema="nexus-public-fx-followup-v1",
                    source_account=self.account_fingerprint(),
                    source_environment="DEMO" if self.demo else "LIVE",
                    collected_utc=datetime.now(timezone.utc).isoformat(),
                    collector="okx_reference_autovaluation/10.2.1",
                    requests=requests_out)

    def order_fills(self, inst_id: str, ord_id: str, *, expected_qty: float = 0.0,
                    attempts: int = 4) -> list[dict]:
        """Fetch one order at the server; still validate every returned identity.

        Positive completeness is established by terminal cumulative quantity and
        identified individual fills. Exhausting a bounded archive is relevant to
        a negative/no-fill claim, not a requirement to read unrelated trades.
        """
        inst_id = normalize_inst_id(inst_id)
        ord_id = str(ord_id or "")
        if not ord_id:
            raise BrokerFehler("Orderbezogene Fillabfrage ohne Order-ID")
        found: dict[str, dict] = {}

        def merge(rows):
            for raw in rows:
                row = dict(raw)
                if str(row.get("ordId") or "") != ord_id:
                    continue
                if row.get("instId") and normalize_inst_id(row["instId"]) != inst_id:
                    raise BrokerFehler("Widerspruechliches Instrument derselben Fill-Order")
                fill_id = str(row.get("tradeId") or "")
                if not fill_id:
                    continue
                previous = found.get(fill_id)
                if previous:
                    row = _merge_fill_receipt(previous, row)
                found[fill_id] = row

        for attempt in range(max(1, int(attempts))):
            try:
                rows = self.client.fills(inst_id, limit=100, ord_id=ord_id)
            except _FillEvidenceConflict:
                raise
            except BrokerFehler as exc:
                rows = []
                if attempt + 1 >= max(1, int(attempts)):
                    logger.warning("OKX-Fillabfrage %s ordId=%s fehlgeschlagen: %s",
                                   inst_id, ord_id, exc)
            merge(rows)
            total = sum(_f(row.get("fillSz")) for row in found.values())
            if found and (expected_qty <= 0 or total >= expected_qty - max(1e-12, expected_qty * 1e-9)):
                break
            if attempt + 1 < max(1, int(attempts)):
                time.sleep(0.45 * (attempt + 1))
        total = sum(_f(row.get("fillSz")) for row in found.values())
        need_history = (not found or expected_qty <= 0 or
                        total < expected_qty - max(1e-12, expected_qty * 1e-9) or
                        any(r.get("fee") in (None, "") for r in found.values()))
        history_ok = True
        if need_history:
            try:
                archived = self.client.fills_history_paginated(inst_id, max_pages=20, ord_id=ord_id)
                history_ok = bool(getattr(self.client, "_last_fills_history_complete", True))
            except _FillEvidenceConflict:
                raise
            except BrokerFehler as exc:
                archived = []
                history_ok = False
                logger.warning("OKX-Fillarchiv %s ordId=%s fehlgeschlagen: %s",
                               inst_id, ord_id, exc)
            merge(archived)
        self._last_order_fills_history_ok = history_ok
        return sorted(found.values(), key=lambda row: (
            int(_f(row.get("fillTime") or row.get("ts"), 0.0)),
            str(row.get("tradeId") or "")))

    def _order_result_from_evidence(self, *, meta: OKXInstrument, status: dict,
                                    cl_ord_id: str, requested_qty: float,
                                    reference_price: float) -> OrderErgebnis:
        """Orderstatus und echte tradeId-Fills zu einem Beweis zusammenfuehren."""
        ord_id = str(status.get("ordId") or "")
        has_gross_status = status.get("accFillSz") not in (None, "")
        gross_status = _f(status.get("accFillSz"))
        raw_fills = (self.order_fills(meta.inst_id, ord_id,
                                      expected_qty=gross_status)
                     if ord_id else [])
        # Manche History-Antworten (und insbesondere aeltere OKX-Antworten)
        # wiederholen instId/ordId nicht in jeder Fill-Zeile. Die Identitaet
        # darf dann nicht zu ``okx:<konto>::<order>:<trade>`` verkuerzen:
        # Instrument und Order sind aus dem exakt abgefragten Kontext bekannt.
        fills = []
        for raw in raw_fills:
            row = dict(raw or {})
            row["instId"] = str(row.get("instId") or meta.inst_id)
            row["ordId"] = str(row.get("ordId") or ord_id)
            if row["instId"] != meta.inst_id or row["ordId"] != ord_id:
                raise BrokerFehler("Fillbeleg mit fremdem Instrument oder Auftrag")
            if status.get("side") and row.get("side") not in (None, "", status["side"]):
                raise BrokerFehler("Fillbeleg mit widerspruechlicher Richtung")
            from execution_lifecycle import number
            if number(row.get("fillSz")) <= 0 or number(row.get("fillPx")) <= 0:
                raise BrokerFehler("Fillbeleg ohne positive endliche Menge und Preis")
            fills.append(row)
        gross_fills = sum(_f(row.get("fillSz")) for row in fills)
        gross = gross_fills or gross_status
        weighted = sum(_f(row.get("fillSz")) * _f(row.get("fillPx")) for row in fills)
        avg = (weighted / gross_fills if gross_fills > 0 else
               _f(status.get("avgPx")) or float(reference_price or 0.0))
        fees: dict[str, float] = {}
        base_fee_delta = 0.0
        quote_fee_value = 0.0
        fee_evidence_complete = True
        fee_quote_known = True
        trade_quote = str(status.get("tradeQuoteCcy") or
                          self._trade_quote_ccy(meta, require_cash=False))
        for row in fills:
            if row.get("tradeQuoteCcy") and str(row["tradeQuoteCcy"]).upper() != trade_quote.upper():
                raise BrokerFehler("Fillbeleg mit widerspruechlicher Abrechnungswaehrung")
            ccy = str(row.get("feeCcy") or "").upper()
            if row.get("fee") in (None, "") or (not ccy and row.get("fee") not in (0, 0.0, "0")):
                fee_evidence_complete = False
                fee_quote_known = False
                continue
            number(row.get("fee"))  # NaN/Infinity sind kein Gebuehrenbeleg.
            raw_fee = _f(row.get("fee"))
            if not ccy or raw_fee == 0:
                continue
            cost = -raw_fee  # OKX: negative=Gebuehr, positiv=Rabatt
            fees[ccy] = fees.get(ccy, 0.0) + cost
            if ccy == meta.base_ccy:
                base_fee_delta += raw_fee
                quote_fee_value += cost * _f(row.get("fillPx"))
            elif ccy == trade_quote:
                quote_fee_value += cost
            else:
                # v9.1: keine ungerechnete Fremdwaehrung in der Quotesumme.
                # Sie bleibt in ``fees`` einzeln sichtbar.
                logger.info("Gebuehr in %s bleibt ausserhalb der Quotesumme "
                            "des Marktes %s.", ccy, meta.inst_id)
                fee_quote_known = False
        side = str(status.get("side") or next((r.get("side") for r in fills if r.get("side")), "buy")).lower()
        net = max(0.0, gross + (base_fee_delta if side == "buy" else -base_fee_delta))
        state = str(status.get("state") or "unknown").lower()
        terminal = state in {"filled", "canceled", "cancelled", "rejected", "expired", "mmp_canceled"}
        if state == "filled" and gross <= 0:
            terminal = False
        tolerance = max(1e-12, gross_status * 1e-9)
        history_ok = bool(getattr(self, "_last_order_fills_history_ok", True))
        evidence_complete = bool(terminal and fee_evidence_complete and has_gross_status and (
            (gross_status == 0 and gross_fills == 0 and history_ok) or
            (gross_status > 0 and fills and abs(gross_fills - gross_status) <= tolerance)))
        trade_quote = str(status.get("tradeQuoteCcy") or
                          self._trade_quote_ccy(meta, require_cash=False))
        result = OrderErgebnis(
            order_ids=[ord_id] if ord_id else [], status=state,
            filled_quantity=net, gross_filled_quantity=gross,
            avg_fill_price=avg, reference_id=cl_ord_id,
            client_order_id=cl_ord_id,
            order_tag=str(status.get("tag") or getattr(__import__("config"), "OKX_ORDER_TAG", "NEXUS"))[:16],
            position_ids=[meta.inst_id], paper=self.demo,
            requested_quantity=float(requested_qty or 0.0),
            remaining_quantity=max(0.0, float(requested_qty or 0.0) - gross),
            terminal=terminal, raw_status=state, trade_quote_ccy=trade_quote,
            fills=[dict(row) for row in fills],
            fill_ids=[okx_fill_identity(row, self.account_fingerprint())
                      for row in fills if str(row.get("tradeId") or "")],
            fill_evidence_complete=evidence_complete,
            fees={key: round(value, 16) for key, value in fees.items()},
            fees_quote=float(quote_fee_value) if fee_quote_known else None,
            account_fingerprint=self.account_fingerprint(),
            broker_environment="DEMO" if self.demo else "LIVE",
            # v9.3: Alles, was den Ausgang nachtraeglich erklaert. Bis 9.2
            # landete davon nur ein Satz Freitext im Hinweis -- cancelSource
            # und sCode wurden verworfen.
            execution_evidence={
                "ord_id": ord_id,
                "cl_ord_id": cl_ord_id,
                "inst_id": meta.inst_id,
                "ord_type": str(status.get("ordType") or ""),
                "requested_qty": float(requested_qty or 0.0),
                "filled_qty": float(gross or 0.0),
                "limit_price": _f(status.get("px")) or float(reference_price or 0.0),
                "avg_price": float(avg or 0.0),
                "state": state,
                "cancel_source": str(status.get("cancelSource") or ""),
                "cancel_source_reason": str(status.get("cancelSourceReason") or ""),
                "s_code": str(status.get("sCode") or ""),
                "s_msg": str(status.get("sMsg") or status.get("msg") or ""),
                "broker_time_ms": str(status.get("uTime") or status.get("cTime") or ""),
            },
        )
        from execution_lifecycle import observe
        observe(result, broker="okx", instrument=meta.inst_id)
        if not evidence_complete:
            logger.warning(
                "OKX-Ausfuehrungsbelege unvollstaendig: %s ordId=%s state=%s "
                "Ordermenge=%s Fillmenge=%.12g tradeIds=%d Gebuehren=%s Archiv=%s",
                meta.inst_id, ord_id, state, status.get("accFillSz"), gross_fills,
                len(fills), fee_evidence_complete, history_ok)
        return result

    def _kauf_aus_archiv(self, inst_id: str, meta, cl_ord_id: str) -> tuple[dict, str]:
        """Einen KAUF ueber die archivierten Fills rekonstruieren.

        9.5.8. Wortgleich zum bereits vorhandenen Rueckfall im Verkaufspfad
        (``reconcile_exit_evidence``) -- der Kaufpfad hatte ihn nie, obwohl
        derselbe Kommentar dort seit Langem erklaert, warum er noetig ist.

        Rueckgabe ``(status, beweis)``. Der zweite Wert ist der WICHTIGE:

          * ``KEIN_FILL``    -- das Archiv hat geantwortet und kennt zu dieser
                                Kennung nichts. Nur das ist ein Beleg.
          * ``NICHT_LESBAR`` -- das Archiv war nicht erreichbar (Netz,
                                Ratenbegrenzung). Keine Aussage.
          * ``UNEINDEUTIG``  -- es gibt Fills, aber ohne oder mit mehreren
                                ordIds. Auch das ist keine Aussage -- und schon
                                gar kein Beleg, dass nichts ausgefuehrt wurde.
          * ``GEFUNDEN``     -- rekonstruierter Kauf im ersten Rueckgabewert.

        Diese Unterscheidung ist nicht formal: ohne sie wuerde eine
        Ratenbegrenzung auf die Archivabfrage einen echten, gefuellten Kauf als
        "nie ausgefuehrt" verbuchen. Die Coins laegen dann ohne Positionsbuch,
        ohne Ledgerzeile und ohne Stop im Konto. ``VerbindungVerloren`` ist ein
        ``BrokerFehler`` -- ein Netzausfall sah bis eben genauso aus wie eine
        leere Auskunft.
        """
        if not cl_ord_id:
            return {}, "NICHT_LESBAR"
        try:
            archived = self.historical_fills(meta.base_ccy, since="")
        except BrokerFehler as exc:
            logger.info("%s: Fill-Archiv nicht lesbar (%s) -- keine Aussage "
                        "zur Kennung %s.", inst_id, exc, cl_ord_id)
            return {}, "NICHT_LESBAR"
        exact = [row for row in archived
                 if normalize_inst_id(str(row.get("instId") or "")) == inst_id
                 and str(row.get("side") or "").lower() == "buy"
                 and str(row.get("clOrdId") or "") == cl_ord_id]
        if not exact:
            if not bool(getattr(self.client, "_last_fills_history_complete", True)):
                return {}, "UNEINDEUTIG"
            return {}, "KEIN_FILL"
        order_ids = sorted({str(row.get("ordId") or "")
                            for row in exact if row.get("ordId")})
        if any(not str(row.get("ordId") or "").strip() for row in exact) or len(order_ids) != 1:
            logger.warning("%s: Fill-Archiv liefert zu %s %d Fills mit %d "
                           "ordIds -- nicht eindeutig, keine Aussage.",
                           inst_id, cl_ord_id, len(exact), len(order_ids))
            return {}, "UNEINDEUTIG"
        gross = sum(_f(row.get("fillSz")) for row in exact)
        value = sum(_f(row.get("fillSz")) * _f(row.get("fillPx")) for row in exact)
        return {"instId": inst_id, "side": "buy", "state": "filled",
                "ordId": order_ids[0], "clOrdId": cl_ord_id,
                "accFillSz": gross,
                "avgPx": value / gross if gross > 0 else 0.0}, "GEFUNDEN"

    def _darf_als_nie_ausgefuehrt_gelten(self, intent: dict) -> bool:
        """Ist der Eintrag alt genug, um "unbekannt" als Beweis zu werten?

        Eine gerade abgesendete Order kann bei OKX sekundenlang noch nicht
        abfragbar sein. Erst nach einer Mindestfrist ist "kenne ich nicht"
        wirklich eine Aussage. Ohne Zeitstempel wird NICHT geraten.
        """
        import config
        frist = max(60.0, float(getattr(
            config, "OKX_ORDER_UNBEKANNT_MINDESTALTER_SEKUNDEN", 300.0)))
        for feld in ("created_at_ts", "updated_at_ts", "ts"):
            wert = intent.get(feld)
            try:
                if wert not in (None, "") and (time.time() - float(wert)) >= frist:
                    return True
            except (TypeError, ValueError):
                pass
        for feld in ("created_at", "updated_at", "zeit"):
            roh = str(intent.get(feld) or "").strip()
            if not roh:
                continue
            try:
                stamp = datetime.fromisoformat(roh.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                alter = (datetime.now(timezone.utc) - stamp).total_seconds()
                if alter >= frist:
                    return True
            except ValueError:
                continue
        return False

    def read_primary_order_status(self, inst_id, *, order_id='', client_id='', side='sell'):
        """Read current and archived status without placing or changing any order."""
        try:
            status = self.client.order_status(inst_id, ord_id=order_id,
                cl_ord_id='' if order_id else client_id)
        except BrokerFehler as exc:
            if _okx_fehlercode(exc) not in _ORDER_UNBEKANNT_CODES:
                raise
            status = {}
        if not status and hasattr(self.client, 'order_history_match'):
            key = (inst_id, order_id, client_id, side)
            checks = getattr(self, '_history_checks', {})
            if time.monotonic() - checks.get(key, -1e9) >= 300:
                checks[key] = time.monotonic()
                self._history_checks = checks
                status = self.client.order_history_match(inst_id, ord_id=order_id,
                    cl_ord_id=client_id, side=side)
        return status or {}

    def reconcile_order_evidence(self, intent: dict) -> OrderErgebnis | None:
        """Persistierte clOrdId/ordId ohne zweiten POST gegen OKX klaeren."""
        inst_id = normalize_inst_id(intent.get("inst_id") or "")
        meta = self.client.instrument(inst_id)
        if meta is None:
            return None
        ord_id = str(intent.get("ord_id") or intent.get("order_id") or "")
        cl_ord_id = str(intent.get("cl_ord_id") or intent.get("client_order_id") or
                        intent.get("reference_id") or "")
        # 9.5.8: "OKX kennt diese Order nicht" ist eine ANTWORT, kein Ausfall.
        #
        # Bis 9.5.7 wurde beides gleich behandelt: ``except BrokerFehler:
        # return None``. Der Aufrufer machte daraus ein stilles ``continue``,
        # der Registereintrag blieb auf UNKNOWN_AFTER_SUBMIT, verfaellt per TTL
        # nie -- und weil ein ungeklaerter Eintrag alle Kryptoeinstiege sperrt,
        # stand der Handel dauerhaft. Genau das zeigte APT ab dem 04.09.2026.
        unbekannt = False
        try:
            status = self.client.order_status(
                inst_id, ord_id=ord_id, cl_ord_id="" if ord_id else cl_ord_id)
        except BrokerFehler as exc:
            # Der Code wird an seiner festen Stelle gelesen ("OKX-Fehler
            # <code>:", siehe request()), nicht irgendwo in der Meldung. Sonst
            # koennte dieselbe Ziffernfolge in einer ordId oder einem Preis
            # stehen und einen Transportfehler zu einer Auskunft machen.
            if _okx_fehlercode(exc) not in _ORDER_UNBEKANNT_CODES:
                logger.warning("OKX-Kaufstatus %s ordId=%s clOrdId=%s nicht lesbar: %s",
                               inst_id, ord_id, cl_ord_id, exc)
                return None            # Transportfehler: weiterhin keine Aussage
            unbekannt = True
            status = {}
        archivbeweis = ""
        if not status:
            status = self.read_primary_order_status(inst_id, order_id=ord_id,
                                                   client_id=cl_ord_id, side='buy')
        if not status:
            # Der Kaufpfad hatte diesen Rueckfall nie -- der Verkaufspfad
            # (reconcile_exit_evidence) schon. Wieder eine Asymmetrie: fuer
            # eine aeltere Order liefert /trade/order nichts mehr, die
            # unveraenderliche clOrdId steht aber in den archivierten Fills.
            status, archivbeweis = self._kauf_aus_archiv(inst_id, meta, cl_ord_id)
        if not status:
            # NUR eine echte Leerauskunft des Archivs zaehlt. Ein nicht
            # erreichbares oder mehrdeutiges Archiv ist keine Aussage.
            # A paginated historical response only covers its retention window.
            # Even two missing lookups cannot prove rejection of a submitted order.
            logger.warning("%s: keine abschliessende Orderauskunft zu %s; "
                           "Abgleich bleibt offen", inst_id, cl_ord_id or ord_id)
            return None
        if normalize_inst_id(str(status.get("instId") or "")) != inst_id:
            return None
        if str(status.get("side") or "").lower() != "buy":
            return None
        broker_clid = str(status.get("clOrdId") or "")
        if cl_ord_id and broker_clid and broker_clid != cl_ord_id:
            return None
        result = self._order_result_from_evidence(
            meta=meta, status=status, cl_ord_id=cl_ord_id,
            requested_qty=float(intent.get("qty") or 0.0),
            reference_price=float(intent.get("signal_price") or 0.0))
        # Reconciliation only establishes the fill evidence. The engine
        # persists ownership, position and fills before requesting protection,
        # exactly as it does for a directly confirmed buy.
        return result

    def reconcile_exit_evidence(self, *, inst_id: str,
                                client_order_id: str = "",
                                order_id: str = "",
                                expected_qty: float = 0.0,
                                reference_price: float = 0.0) -> OrderErgebnis | None:
        """Einen persistierten SELL ausschliesslich ueber ordId/clOrdId klaeren.

        Anders als ``reconcile_order_evidence`` erzeugt dieser Pfad niemals
        einen Schutzauftrag. Er dient nur der Wiederaufnahme eines bereits
        gesendeten Exits nach Prozess-/Transportabbruch.
        """
        inst_id = normalize_inst_id(inst_id)
        meta = self.client.instrument(inst_id)
        if meta is None:
            return None
        oid = str(order_id or "")
        cid = str(client_order_id or "")
        if not oid and not cid:
            return None
        if cid:
            from execution_lifecycle import lookup
            local = lookup(broker="okx", account=self.account_fingerprint(),
                           environment="DEMO" if self.demo else "LIVE", client_id=cid)
            if (local and local['instrument'] == inst_id and local['side'] == 'SELL'
                    and local['state'] == 'REJECTED' and local['terminal']
                    and local['evidence_complete'] and _f(local['filled']) == 0):
                return OrderErgebnis(status='REJECTED', raw_status='REJECTED',
                    terminal=True, fill_evidence_complete=True, filled_quantity=0,
                    requested_quantity=float(local['requested']), client_order_id=cid,
                    account_fingerprint=self.account_fingerprint(),
                    broker_environment='DEMO' if self.demo else 'LIVE', paper=self.demo,
                    hinweis='Dauerhafter Nachweis: nicht uebermittelt oder eindeutig abgelehnt')
        try:
            status = self.read_primary_order_status(inst_id, order_id=oid,
                                                   client_id=cid, side='sell')
        except BrokerFehler:
            status = {}
        if not status and cid:
            # Nach laengerer Offlinezeit kann /trade/order die Order nicht
            # mehr liefern. Die unveraenderliche clOrdId steht aber auch in
            # den archivierten Fills und rekonstruiert die ordId eindeutig.
            try:
                archived = self.historical_fills(meta.base_ccy, since="")
            except BrokerFehler:
                archived = []
            exact = [row for row in archived
                     if normalize_inst_id(str(row.get("instId") or "")) == inst_id
                     and str(row.get("side") or "").lower() == "sell"
                     and str(row.get("clOrdId") or "") == cid]
            order_ids = sorted({str(row.get("ordId") or "")
                                for row in exact if row.get("ordId")})
            if len(order_ids) == 1:
                # Fills establish executions, not a terminal order status.
                # Resolve the recovered exchange ID instead of fabricating
                # state=filled from a potentially incomplete historical page.
                try:
                    status = self.client.order_status(inst_id, ord_id=order_ids[0])
                except BrokerFehler:
                    status = {}
        if not status or str(status.get("side") or "").lower() != "sell":
            return None
        if (not status.get("ordId")
                or status.get("instId") not in (None, "", inst_id)
                or (oid and str(status["ordId"]) != oid)
                or (cid and status.get("clOrdId") not in (None, "", cid))):
            raise BrokerFehler("Wiedergefundener Verkauf widerspricht der exakten Auftragsidentitaet")
        return self._order_result_from_evidence(
            meta=meta, status=status, cl_ord_id=cid,
            requested_qty=float(expected_qty or 0.0),
            reference_price=float(reference_price or 0.0))

    def _start_stream_best_effort(self) -> None:
        """Account-/Orderstream starten; REST bleibt bei jedem Fehler nutzbar."""
        try:
            from .okx_stream import OKXPrivateStream
            self._stream = OKXPrivateStream(
                getattr(self.client, "_key", ""), getattr(self.client, "_secret", ""),
                getattr(self.client, "_passphrase", ""),
                demo=self.demo,
                time_offset_seconds=float(getattr(
                    self.client, "clock_offset_seconds", 0.0)),
            )
            if not self._stream.start():
                logger.warning("OKX-Privatstream nicht gestartet; REST-Fallback aktiv.")
        except Exception as exc:
            self._stream = None
            logger.warning("OKX-Privatstream nicht verfuegbar (%s); REST-Fallback aktiv.",
                           type(exc).__name__)

    def disconnect(self) -> None:
        with self._lock:
            self._connected = False
        if self._stream is not None:
            try:
                self._stream.stop()
            except Exception:
                logger.debug("OKX-Stream-Stopp fehlgeschlagen", exc_info=True)
        try:
            self.client.session.close()
        except Exception:
            logger.debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    def is_connected(self) -> bool:
        return bool(self._connected)

    def health_check(self, force: bool = False) -> bool:
        """Authentifizierter Kontotest mit echtem DEGRADED-Zwischenzustand.

        Ein einzelner Timeout ist kein Verbindungsabbruch. Nur harte
        Authentifizierungs-/Zeitfehler oder mehrere REST-Fehler in Folge
        setzen den Broker offline.
        """
        now = time.monotonic()
        import config
        ttl = float(getattr(config, "BROKER_HEALTHCHECK_SECONDS", 30))
        if not force and self._connected and now - self._last_health < ttl:
            return True
        try:
            server_ms = self.client.server_time_ms()
            drift = abs(float(getattr(self.client, "clock_offset_seconds", 0.0))) \
                if server_ms else float("inf")
            if drift > self.max_clock_drift:
                raise Zeitabweichung(
                    f"OKX-Systemzeit weicht {drift:.1f} s ab; NTP wird benoetigt.")
            # Ein oeffentlicher Zeitabruf allein beweist weder Key noch Konto.
            self.client.account_config()
            self._last_health = now
            self._connected = True
            self._health_failures = 0
            self._health_state = "ONLINE"
            self._health_detail = "REST authentifiziert"
            self._last_health_success_at = time.time()
            try:
                from decision_analytics import heartbeat_record
                heartbeat_record("okx", "ONLINE", self._health_detail,
                                 last_contact=self.last_contact())
            except Exception:
                logger.debug("OKX-Heartbeat nicht persistierbar", exc_info=True)
            return True
        except Exception as exc:
            self._health_failures += 1
            hard = isinstance(exc, (AuthentifizierungsFehler, Zeitabweichung))
            threshold = max(2, int(getattr(config, "OKX_HEALTH_FAILURE_THRESHOLD", 3)))
            offline = hard or self._health_failures >= threshold
            self._connected = not offline
            self._health_state = "OFFLINE" if offline else "DEGRADED"
            self._health_detail = (
                f"REST-Pruefung {self._health_failures}/{threshold} fehlgeschlagen: "
                f"{type(exc).__name__}: {exc}")
            logger.warning("OKX %s: %s", self._health_state, self._health_detail)
            try:
                from decision_analytics import heartbeat_record
                heartbeat_record("okx", self._health_state, self._health_detail,
                                 last_contact=self.last_contact())
            except Exception:
                logger.debug("OKX-Heartbeat nicht persistierbar", exc_info=True)
            return not offline

    def last_contact(self) -> Optional[str]:
        return self.client.last_contact()

    def stream_status(self) -> dict:
        if self._stream is None:
            return {
                "running": False, "connected": False, "authenticated": False,
                "last_message": None, "last_error": "nicht gestartet",
                "reconnects": 0, "mode": "demo" if self.demo else "live",
            }
        return self._stream.status().as_dict()

    def health_state(self) -> str:
        """ONLINE / DEGRADED / OFFLINE -- fuer Aufrufer, die den Unterschied
        brauchen (z.B. der Universumslauf, der bei OFFLINE nicht rechnen darf)."""
        return str(self._health_state or "OFFLINE")

    def connection_components(self) -> dict:
        stream = self.stream_status()
        observer = getattr(self.client, "_observations", None)
        observations = observer.snapshot() if observer else {
            "schema_version": 1, "broker": "okx", "rest": {},
            "environment": "DEMO" if self.demo else "LIVE"}
        private = observations["rest"].get("private", {})
        return {
            "overall": self._health_state,
            "overall_meaning": "Historischer Broker-Healthcheck; keine Handelsfreigabe",
            "observations": observations,
            "account_fingerprint": self._account_fingerprint,
            "environment": "DEMO" if self.demo else "LIVE",
            "rest_state": private.get("state", "UNKNOWN"),
            "last_rest_attempt": private.get("last_attempt_at"),
            "rest_failures": int(self._health_failures),
            "rest_detail": self._health_detail,
            "last_rest_success": private.get("last_success_at"),
            "ws_connected": bool(stream.get("connected")),
            "ws_authenticated": bool(stream.get("authenticated")),
            "ws_last_message": stream.get("last_message"),
            # 9.5.2: Beide Schreibweisen lesen -- der Stream lieferte bis
            # 9.5.1 nur last_account / last_order, wodurch diese drei Felder
            # im Dashboard dauerhaft null blieben.
            "account_last_update": (stream.get("last_account_update")
                                    or stream.get("last_account")),
            "orders_last_update": (stream.get("last_order_update")
                                   or stream.get("last_order")),
            "pong_last_update": stream.get("last_pong"),
            "instrument_contract": self.instrument_contract_status(),
        }

    def instrument_contract_status(self, held_instruments=()) -> dict:
        """Read only cached private rules; never activate or rename instruments.

        Source verified 2026-09-13: https://www.okx.com/docs-v5/log_en/
        A local USD symbol is an investigation hint, not proof that this
        account is part of the announced September migration.
        """
        with getattr(self.client, "_lock", threading.RLock()):
            quality = dict(getattr(self.client, "_instruments_quality", {}))
            instruments = dict(getattr(self.client, "_instruments", {}))
            at = float(getattr(self.client, "_instruments_at", 0) or 0)
        age = max(0.0, time.time() - at) if at else None
        import config
        max_age = max(1.0, float(getattr(config, "OKX_INSTRUMENT_MAX_AGE_SECONDS", 60.0)))
        authoritative = (quality.get("source") == "/account/instruments"
                         and quality.get("state") == "OK"
                         and quality.get("complete") is True
                         and age is not None and age <= max_age)
        usd = sorted(k for k in instruments if k.endswith("-USD"))
        held = {str(x).upper() for x in held_instruments if str(x)}
        return {
            "state": "REVIEW_REQUIRED" if authoritative and usd else
                     "OBSERVED" if authoritative else "UNKNOWN",
            "quality": quality, "age_seconds": round(age, 2) if age is not None else None,
            "private_catalog_current": authoritative,
            "account_migration_affected": None,
            "usd_instrument_count": len(usd) if authoritative else None,
            "usd_instruments": usd[:30] if authoritative else [],
            "held_missing_from_catalog": sorted(held - set(instruments)) if authoritative else [],
            "routes": [{"inst_id": k, "trade_quote_ccy_list": list(instruments[k].trade_quote_ccy_list),
                        "quote_ccy": instruments[k].quote_ccy} for k in usd[:30]] if authoritative else [],
            "explicit_trade_quote_ccy": True,
            "automatic_instrument_rename": False,
            "parallel_period_start_utc": "2026-09-23T08:00:00+00:00",
            "parallel_period_end_utc": "2026-09-30T08:00:00+00:00",
            "source": "https://www.okx.com/docs-v5/log_en/",
            "detail": ("USD-Routen im privaten Katalog: Kontobetroffenheit und neue Instrumentregeln pruefen. "
                       "Gespeicherte Instrumente/Abrechnungswaehrungen bleiben unveraendert."
                       if authoritative and usd else "Kontobetroffenheit nicht belegt; Katalogsicht ist keine Migrationsfreigabe."),
        }

    def konto_snapshot(self, *, fills_limit: int = 20) -> dict:
        """Read-only Kontoaufnahme fuer Diagnose und Inbetriebnahme.

        Diese Methode setzt keine Orders, cancelt nichts und startet keinen
        Kauf. Sie ist absichtlich am Broker angebunden, damit Diagnose und
        Handelskern dieselben EEA-Endpunkte sowie denselben Demo-Header nutzen.
        """
        daten = self.client.account_snapshot(fills_limit=fills_limit)
        daten.update({
            "modus": "DEMO" if self.demo else "LIVE",
            "quote_ccy": self.quote_ccy,
            "allowed_quotes": list(self.allowed_quotes),
            "stream": self.stream_status(),
        })
        return daten

    def guthaben_schnappschuss(self) -> dict[str, dict]:
        """Dieselbe Guthabensicht, aus der auch ``positionen()`` gebaut wird.

        9.5.8. Bis 9.5.7 benutzten zwei Entscheidungen desselben Durchlaufs
        VERSCHIEDENE Quellen: der Mengenabgleich (der das Positionsbuch an die
        Realitaet anpasst und damit eine Sperre aufloesen wuerde) las ueber
        ``positionen()`` die Stream-/Cachesicht, die Exposure-Klassifizierung
        (die sperrt) dagegen einen frischen REST-Aufruf. Beide Schwellen liegen
        bei 2 %. Wenn nur eine ausloest, koennen die Zahlen nicht dieselben
        gewesen sein -- und der einzige Pfad, der den Zustand haette beenden
        koennen, sah die Abweichung nie. Genau so blieb XLM am 03.09.2026
        dauerhaft auf RESIDUAL_EXPOSURE stehen.

        Wer sperrt, muss dieselbe Zahl lesen wie der, der heilt.
        """
        snap = self._balances_stream_or_rest()
        self._last_balance_snapshot_for_positions = {k: dict(v) for k, v in snap.items()}
        return {k: dict(v) for k, v in snap.items()}

    def _balances_stream_or_rest(self) -> dict[str, dict]:
        import config as _cfg
        now = time.monotonic()
        interval = max(10.0, float(getattr(
            _cfg, "OKX_REST_BALANCE_REFRESH_SECONDS", 60.0)))
        stream_balances = None
        if self._stream is not None:
            stream_balances = self._stream.balances()
        # Regelmaessiger REST-Checkpoint ist die autoritative Vollsicht. Der
        # WebSocket beschleunigt Deltas, darf aber keinen alten Kontostand
        # unbegrenzt konservieren.
        if now - self._last_rest_balance_at >= interval:
            balances = self.client.balances()
            self._rest_balance_cache = {k: dict(v) for k, v in balances.items()}
            self._last_rest_balance_at = now
            if self._stream is not None:
                self._stream.replace_balances(balances)
            return balances
        if stream_balances is not None:
            return stream_balances
        if self._rest_balance_cache:
            return {k: dict(v) for k, v in self._rest_balance_cache.items()}
        balances = self.client.balances()
        self._rest_balance_cache = {k: dict(v) for k, v in balances.items()}
        self._last_rest_balance_at = now
        return balances

    # -- Konto --------------------------------------------------------------
    def kontowert(self) -> float:
        with self._lock:
            wert, gesetzt = self._equity_cache
        if wert and (time.time() - gesetzt) < 20.0:
            return wert
        wert = self.client.total_equity_usd()
        with self._lock:
            self._equity_cache = (float(wert), time.time())
        return float(wert)

    def gebuehrensatz(self) -> float:
        """Taker-Satz dieses Kontos -- gemessen, nicht angenommen.

        Faellt die Abfrage aus, gilt der KONSERVATIVERE der beiden Werte:
        der zuletzt gemessene oder der eingestellte. Eine zu niedrig
        angenommene Gebuehr macht schlechte Trades rechnerisch gut.
        """
        import config as _cfg
        angenommen = float(getattr(_cfg, "OKX_TAKER_FEE_PCT", 0.0035))
        with self._lock:
            wert, gesetzt = getattr(self, "_gebuehr_cache", (0.0, 0.0))
        if wert and (time.time() - gesetzt) < 3600.0:
            return max(wert, angenommen)
        try:
            daten = self.client.trade_fee("SPOT")
            taker = float(daten.get("taker") or 0.0)
        except Exception as exc:
            # Der konservativere Wert gilt: ein zu niedrig angenommener Satz
            # macht schlechte Trades rechnerisch gut.
            logger.info("OKX-Gebuehrensatz nicht abrufbar (%s) -- es gilt der "
                        "hoehere von gemessen %.4f %% und eingestellt %.4f %%.",
                        exc, wert * 100, angenommen * 100)
            return max(wert, angenommen)
        if taker <= 0:
            return max(wert, angenommen)
        with self._lock:
            self._gebuehr_cache = (taker, time.time())
        if abs(taker - angenommen) > 1e-6:
            logger.warning("OKX-Gebuehrensatz gemessen: %.4f %% (eingestellt "
                           "%.4f %%). Es gilt der gemessene Wert.",
                           taker * 100, angenommen * 100)
        return taker

    def handelbares_kapital(self, eigene_positionen=()) -> float:
        """Bot-Kapital in der primaeren Risikowaehrung.

        Nur frei verfuegbares Guthaben in *freigegebenen* Entry-Waehrungen
        sowie der Marktwert eindeutig eigener Botpositionen zaehlen. Mehrere
        Waehrungen werden niemals roh addiert: jede Nebenwaehrung braucht
        einen frischen, beobachteten Spot-Umrechnungskurs. Fehlt der, bleibt
        die Risikobasis unbekannt und neue Kaeufe bleiben gesperrt.

        Das schliesst die bisherige Luecke zwischen Routing und Risiko: NEXUS
        konnte USDC als explizit freigegebenen Fallback waehlen, der
        Risikotopf sah aber ausschliesslich EUR und meldete deshalb bei einem
        reinen USDC-Konto ``handelbar 0.00``. USD wird dadurch NICHT
        freigeschaltet; freie Cash-Waehrungen zaehlen nur, wenn sie bereits in
        ``allowed_quotes`` stehen.
        """
        basis = str(self.quote_ccy or "EUR").upper()
        try:
            guthaben = self._balances_stream_or_rest()
        except BrokerFehler:
            raise
        except Exception as exc:
            raise BrokerFehler("BALANCE_SNAPSHOT_INVALID: Cash nicht abrufbar") from exc
        if not isinstance(guthaben, dict):
            raise BrokerFehler("BALANCE_SNAPSHOT_INVALID: Guthabenformat ungueltig")

        positive = []
        for currency, row in guthaben.items():
            try:
                available = float((row or {}).get("cash", 0.0) or 0.0)
                gross = float((row or {}).get("gesamt", 0.0) or 0.0)
            except (TypeError, ValueError, AttributeError):
                continue
            if (math.isfinite(available) and available > 0) or (math.isfinite(gross) and gross > 0):
                positive.append(str(currency).upper())
        evidence = {
            "schema_version": 1,
            "basis_currency": basis,
            "allowed_cash_lanes": list(self.allowed_quotes),
            "positive_balance_currencies": sorted(set(positive)),
            "unsupported_positive_balance_currencies": sorted(
                set(positive) - set(self.allowed_quotes)),
            "cash_lanes": [],
            "position_values": [],
            "unconverted": [],
        }

        def rate_to_basis(currency: str, *, context: str) -> float:
            currency = str(currency or "").upper()
            if not currency:
                evidence["unconverted"].append({"context": context, "currency": "UNKNOWN"})
                self._last_capital_evidence = evidence
                raise BrokerFehler(
                    "RISK_CAPITAL_CURRENCY_UNKNOWN: Bewertungswaehrung fehlt")
            if currency == basis:
                return 1.0
            try:
                rate = self.quote_conversion_rate(currency, basis)
            except Exception as exc:
                evidence["unconverted"].append({"context": context, "currency": currency})
                self._last_capital_evidence = evidence
                raise BrokerFehler(
                    f"RISK_CAPITAL_FX_UNKNOWN: keine beobachtete Umrechnung {currency}->{basis}") from exc
            try:
                rate = float(rate)
            except (TypeError, ValueError):
                rate = 0.0
            if not math.isfinite(rate) or rate <= 0:
                evidence["unconverted"].append({"context": context, "currency": currency})
                self._last_capital_evidence = evidence
                raise BrokerFehler(
                    f"RISK_CAPITAL_FX_UNKNOWN: keine beobachtete Umrechnung {currency}->{basis}")
            return rate

        total = 0.0
        for currency in self.allowed_quotes:
            row = guthaben.get(str(currency).upper()) or {}
            try:
                cash = float(row.get("cash", 0.0) or 0.0)
            except (TypeError, ValueError):
                cash = float("nan")
            if not math.isfinite(cash) or cash < 0:
                self._last_capital_evidence = evidence
                raise BrokerFehler(
                    f"BALANCE_SNAPSHOT_INVALID: freies {str(currency).upper()}-Guthaben ungueltig")
            rate = 1.0
            value = 0.0
            if cash > 0:
                rate = rate_to_basis(currency, context="cash")
                value = cash * rate
                total += value
            evidence["cash_lanes"].append({
                "currency": str(currency).upper(),
                "available": round(cash, 12),
                "rate_to_basis": round(rate, 12) if cash > 0 else None,
                "value_in_basis": round(value, 12),
                "funded": cash > 0,
            })

        for eintrag in eigene_positionen or ():
            symbol = ""
            currency = ""
            try:
                if isinstance(eintrag, dict):
                    symbol = str(eintrag.get("symbol") or "").upper()
                    menge, preis = eintrag.get("menge"), eintrag.get("preis")
                    currency = str(eintrag.get("market_quote_ccy") or
                                   eintrag.get("quote_ccy") or
                                   eintrag.get("waehrung") or "").upper()
                elif len(eintrag) >= 4 and isinstance(eintrag[0], str):
                    symbol, menge, preis, currency = eintrag[:4]
                    symbol, currency = str(symbol).upper(), str(currency).upper()
                elif len(eintrag) >= 3 and isinstance(eintrag[0], str):
                    symbol, menge, preis = eintrag[:3]
                    symbol = str(symbol).upper()
                    # Legacy-Tripel hatten keine Waehrung. Nur ein explizites
                    # Paar wie BTC-EUR traegt selbst einen Beleg; sonst nicht
                    # still die primaere Waehrung erfinden.
                    parts = symbol.replace('/', '-').split('-')
                    currency = parts[-1] if len(parts) >= 2 else ""
                else:
                    menge, preis = eintrag[:2]
                    currency = basis  # historischer Paarvertrag war primaer
                wert = abs(float(menge)) * abs(float(preis))
            except (TypeError, ValueError, IndexError):
                continue
            if not math.isfinite(wert) or wert <= 0:
                continue
            rate = rate_to_basis(currency, context="position:" + (symbol or "UNKNOWN"))
            value = wert * rate
            total += value
            evidence["position_values"].append({
                "symbol": symbol or None,
                "market_quote_ccy": currency,
                "native_value": round(wert, 12),
                "rate_to_basis": round(rate, 12),
                "value_in_basis": round(value, 12),
            })

        evidence["total_in_basis"] = round(total, 12)
        evidence["funded_allowed_lanes"] = [r["currency"] for r in evidence["cash_lanes"] if r["funded"]]
        evidence["status"] = "VALUED" if total > 0 else "ZERO_SUPPORTED_CAPITAL"
        self._last_capital_evidence = evidence
        return round(total, 8)

    def capital_evidence(self) -> dict:
        """Letzten bereits erzeugten Risikobasisbeleg ohne Netzabfrage lesen."""
        return json.loads(json.dumps(self._last_capital_evidence)) if self._last_capital_evidence else {}

    def kontowaehrung(self) -> str:
        # Die Positionsgroesse rechnet in der Handelswaehrung, nicht in USD.
        return str(self.quote_ccy or "EUR").upper()

    def ist_paper(self) -> bool:
        return bool(self.demo)

    def verfuegbares_cash(self, quote_ccy: str = "") -> float | None:
        try:
            guthaben = self._balances_stream_or_rest()
        except BrokerFehler:
            return None
        eintrag = guthaben.get(str(quote_ccy or self.quote_ccy).upper())
        return float(eintrag.get("cash", 0.0)) if eintrag else 0.0

    # -- Instrumente --------------------------------------------------------
    def _inst_id(self, instrument) -> str:
        contract = getattr(instrument, "contract", None)
        symbol = (getattr(contract, "localSymbol", None) or
                  getattr(instrument, "name", None) or
                  getattr(contract, "symbol", None) or instrument)
        text = str(symbol or "").strip().upper().replace("/", "-").replace("_", "-")
        if "-" in text:
            return text
        alle = self.client.instruments()
        candidates = []
        for meta in alle.values():
            if meta.base_ccy != text or not meta.ist_live:
                continue
            try:
                self._trade_quote_ccy(meta)
            except BrokerFehler:
                continue
            quote_rank = (0 if meta.quote_ccy == self.quote_ccy else
                          1 + self.allowed_quotes.index(meta.quote_ccy)
                          if meta.quote_ccy in self.allowed_quotes else
                          100 if meta.quote_ccy == "USD" else 999)
            candidates.append((quote_rank, meta.inst_id))
        if not candidates:
            return normalize_inst_id(text, self.quote_ccy)
        return sorted(candidates)[0][1]

    def trade_quote_for_instrument(self, instrument, *, require_cash: bool = False) -> str:
        inst_id = self._inst_id(instrument)
        meta = self.client.instrument(inst_id)
        if meta is None:
            raise BrokerFehler(f"{inst_id}: Instrumentmetadaten fehlen")
        return self._trade_quote_ccy(meta, require_cash=require_cash)

    def quote_conversion_rate(self, source: str, target: str) -> float | None:
        """Beobachteter Spot-Kreuzkurs ohne angenommene Stablecoin-Paritaet."""
        source, target = str(source).upper(), str(target).upper()
        if source == target:
            return 1.0
        now = time.monotonic()
        cached_at, cached = self._quote_rate_cache
        key = (source, target)
        if now - cached_at <= 10.0 and key in cached:
            return cached[key]
        tickers = self.client.tickers()
        graph: dict[str, dict[str, float]] = {}
        for inst_id, ticker in tickers.items():
            last = float(getattr(ticker, "last", 0.0) or 0.0)
            stamp = float(getattr(ticker, "timestamp_ms", 0) or 0)
            parts = str(inst_id).upper().split("-")
            if (len(parts) != 2 or not math.isfinite(last) or last <= 0
                    or not math.isfinite(stamp) or not -5000 <= time.time()*1000-stamp <= 120000):
                continue
            base, quote = parts
            graph.setdefault(base, {})[quote] = last
            graph.setdefault(quote, {})[base] = 1.0 / last
        queue = [(source, 1.0, 0)]
        visited = {source}
        result = None
        while queue:
            ccy, rate, hops = queue.pop(0)
            if hops >= 3:
                continue
            for nxt, edge in sorted(graph.get(ccy, {}).items()):
                if nxt in visited or edge <= 0:
                    continue
                value = rate * edge
                if nxt == target:
                    result = value
                    queue = []
                    break
                visited.add(nxt)
                queue.append((nxt, value, hops + 1))
        fresh = dict(cached) if now - cached_at <= 10.0 else {}
        if result and result > 0:
            fresh[key] = result
        self._quote_rate_cache = (now, fresh)
        return result

    def instrument_handelbar(self, instrument) -> tuple[bool, str]:
        asset = str(getattr(instrument, "asset_type", "crypto")).lower()
        if asset != "crypto":
            return False, "OKX handelt in dieser Version ausschliesslich Krypto-Spot."
        inst_id = self._inst_id(instrument)
        try:
            meta = self.client.instrument(inst_id)
        except BrokerFehler as exc:
            return False, f"OKX-Instrumentpruefung nicht moeglich: {exc}"
        if meta is None:
            return False, f"{inst_id} ist bei OKX nicht als Spot-Instrument bekannt."
        if not meta.ist_live:
            return False, f"{inst_id} ist bei OKX nicht handelbar (Status {meta.state})."
        try:
            self._trade_quote_ccy(meta)
        except BrokerFehler as exc:
            return False, str(exc)
        return True, ""

    def qualifiziere(self, instrumente: list) -> tuple[list, list]:
        nutzbar, abgelehnt = [], []
        for inst in instrumente or []:
            ok, grund = self.instrument_handelbar(inst)
            if ok:
                nutzbar.append(inst)
            else:
                abgelehnt.append((getattr(inst, "name", str(inst)), grund))
        return nutzbar, abgelehnt

    # -- Marktdaten ---------------------------------------------------------
    def freqtrade_historie(self, instrument, *, cutoff=None, cached_only=False) -> pd.DataFrame:
        from freqtrade_candles import History
        import sqlite3
        reader = self.client
        if isinstance(reader, OKXClient):
            cached_reader = getattr(self, "_freqtrade_reader", None)
            if cached_reader is None or (cached_reader.base_url, cached_reader.demo) != (reader.base_url, reader.demo):
                cached_reader = OKXClient(demo=reader.demo, base_url=reader.base_url, timeout=3.)
                cached_reader._public_limit = reader._public_limit
                self._freqtrade_reader = cached_reader
            reader = cached_reader  # Public candles need no private account credentials.
        try:
            return History(reader).load(self._inst_id(instrument), cutoff=cutoff, cached_only=cached_only)
        except (ValueError, OSError, sqlite3.Error) as exc:
            raise BrokerFehler(f"Freqtrade-Kerzen nicht belegbar: {exc}") from exc

    def historie(self, instrument, dauer: str, kerzengroesse: str,
                 nur_handelszeiten: bool = True) -> pd.DataFrame:
        inst_id = self._inst_id(instrument)
        bar = BAR_MAP.get(str(kerzengroesse).strip().lower(), "15m")
        limit = self._kerzenanzahl(dauer, bar)

        def hole():
            df = self.client.candles(inst_id, bar=bar, limit=limit, nur_abgeschlossen=True)
            if df.empty:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            schmal = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
            return completed_bars_only(schmal, kerzengroesse)

        return retry_call(hole, label=f"OKX Historie {inst_id}")

    @staticmethod
    def _kerzenanzahl(dauer: str, bar: str) -> int:
        """Uebersetzt '2 D'/'260 D' in eine sinnvolle Kerzenanzahl."""
        text = str(dauer or "").strip().lower()
        ziffern = "".join(ch for ch in text if ch.isdigit())
        n = int(ziffern or 30)
        sekunden = BAR_SECONDS.get(bar, 900)
        if "d" in text:
            gesamt = n * 86400
        elif "w" in text:
            gesamt = n * 604800
        else:
            gesamt = n * 3600
        return max(80, min(300, int(gesamt / max(1, sekunden)) + 2))

    def latest_bid_ask(self, instrument):
        inst_id = self._inst_id(instrument)
        try:
            t = self.client.ticker(inst_id)
        except BrokerFehler:
            return None
        if t is None:
            return None
        return {
            "bid": t.bid, "ask": t.ask, "last": t.last,
            "timestamp": datetime.fromtimestamp(t.timestamp_ms / 1000.0, timezone.utc).isoformat()
            if t.timestamp_ms else None,
            "source": "OKX v5 market/ticker",
        }

    # -- Guthabenfreigabe ---------------------------------------------------
    def _freigabe_vormerken(self, inst_id: str) -> None:
        """Merken, dass OKX gerade Guthaben bindet oder freigibt.

        Monotone Uhr, nicht die Wanduhr: ein NTP-Ruecksprung wuerde sonst eine
        negative Zeitdifferenz erzeugen und jede Schutzpruefung unnoetig
        sekundenlang pollen lassen.
        """
        if not hasattr(self, "_freigabe_erwartet") or not isinstance(self._freigabe_erwartet, dict):
            self._freigabe_erwartet = {}
        self._freigabe_erwartet[str(inst_id)] = time.monotonic()

    def warte_auf_guthabenfreigabe(self, base_ccy: str, benoetigt: float, *,
                                   inst_id: str = "", lot_size: float = 0.0,
                                   timeout: float = 0.0,
                                   blockiert=None,
                                   immer_warten: bool = False):
        """Auf die tatsaechliche Guthabenfreigabe warten statt einmal zu lesen.

        9.5.8. OKX bestaetigt eine Stornierung VOR der Freigabe des
        eingefrorenen Guthabens -- derselbe Effekt tritt auf, wenn ein FOK
        storniert wird, denn auch dessen Reserve wird verzoegert aufgeloest.
        Der Verkaufspfad hatte das seit dem LINK/ONDO-Vorfall gelernt und
        pollte; der Schutz-Erneuerungspfad las weiterhin genau EINMAL und
        sendete sofort. Am 03.09.2026 antwortete OKX deshalb bei XLM mit 51008
        ("available balance is insufficient") auf eine Schutzorder, die nur
        Sekunden nach dem stornierten FOK gestellt wurde -- die Position blieb
        ungeschuetzt.

        Diese Funktion ist die eine Regel fuer beide Stellen.

        Rueckgabe ``(frei, erfuellt)``:
          * ``frei`` ist ``None``, wenn die Waehrung in der Guthabenantwort
            gar nicht vorkommt. Das heisst "keine Aussage", NICHT "null frei" --
            ein fehlender Eintrag darf den Schutz nach einem Kauf nicht sperren.
          * ``erfuellt`` sagt, ob ``benoetigt`` (abzueglich einer Lotgroesse)
            gedeckt ist und nichts mehr blockiert.

        Gewartet wird nur dann, wenn fuer dieses Instrument gerade eine
        Freigabe zu erwarten ist -- sonst kostet der Aufruf genau einen Read.
        """
        import config
        grenze = max(2.0, float(timeout or getattr(
            config, "OKX_CANCEL_RELEASE_TIMEOUT", 10.0)))
        # Das Vormerkfenster ist bewusst GROESSER als der Polltimeout. Waeren
        # beide gleich, traefe der wichtigste Fall daneben: der Verkaufspfad
        # pollt nach dem Storno die vollen 10 s vergeblich, bricht ab, und der
        # unmittelbar folgende Schutzabgleich saehe die Vormerkung dann bereits
        # als abgelaufen -- also genau dort ein einziger Read, wo die Position
        # gerade ungeschuetzt im Konto liegt.
        fenster = max(grenze * 3.0, float(getattr(
            config, "OKX_FREIGABE_FENSTER_SEKUNDEN", 45.0)))
        vorgemerkt = float(self._freigabe_erwartet.get(str(inst_id), 0.0))
        alter = time.monotonic() - vorgemerkt
        darf_warten = bool(immer_warten or (0.0 <= alter <= fenster))
        deadline = time.time() + (grenze if darf_warten else 0.0)

        frei = None
        while True:
            try:
                guthaben = self.client.balances() or {}
                frei = (_f((guthaben.get(base_ccy) or {}).get("cash"))
                        if base_ccy in guthaben else None)
            except BrokerFehler as exc:
                logger.debug("%s: Guthaben nicht lesbar: %s", base_ccy, exc)
                frei = None
            noch_blockiert = False
            if blockiert is not None:
                try:
                    noch_blockiert = bool(blockiert())
                except BrokerFehler as exc:
                    # Unlesbar heisst nicht frei. Weiter warten, nicht senden.
                    logger.debug("%s: Blockadepruefung nicht lesbar: %s",
                                 inst_id or base_ccy, exc)
                    noch_blockiert = True
            gedeckt = (frei is not None
                       and frei + float(lot_size or 0.0) >= float(benoetigt))
            if gedeckt and not noch_blockiert:
                return frei, True
            if time.time() >= deadline:
                return frei, False
            time.sleep(0.5)

    def execution_quote(self, instrument, quantity: float, *, side: str) -> dict:
        """Preis fuer die *gesamte* Menge aus einem frischen OKX-Orderbuch.

        Ein Ticker-``last`` beweist nur einen vergangenen Abschluss. Er sagt
        nicht, zu welchem Preis die eigene Menge jetzt gekauft oder verkauft
        werden kann. Deshalb summiert dieser Pfad die Gegenseite des Buches
        vollstaendig auf und bricht bei fehlender Tiefe oder alten Daten ab.
        """
        import config
        inst_id = self._inst_id(instrument)
        if bool(getattr(self.client, "demo", self.demo)) != self.demo:
            raise BrokerFehler(f"{inst_id}: Broker und Marktdatenclient haben "
                               "unterschiedliche Umgebung. Keine Order gesendet.")
        qty = float(quantity or 0.0)
        if not math.isfinite(qty) or qty <= 0:
            raise BrokerFehler(f"{inst_id}: Ausfuehrungsmenge muss positiv sein.")
        if str(side or "").lower() not in {"sell", "buy"}:
            raise BrokerFehler(f"{inst_id}: ungueltige Orderseite.")
        depth = max(20, min(400, int(getattr(config, "OKX_EXECUTION_BOOK_DEPTH", 100))))
        book = self.client.orderbook(inst_id, depth=depth)
        environment = "DEMO" if self.demo else "LIVE"
        if book.get("market_data_environment", environment) != environment:
            raise BrokerFehler(f"{inst_id}: Orderbuch aus anderer Umgebung; Order blockiert.")
        ts_ms = int(book.get("timestamp_ms") or 0)
        max_age = max(0.5, float(getattr(config, "OKX_ORDERBOOK_MAX_AGE_SECONDS", 3.0)))
        age = (time.time() * 1000.0 - ts_ms) / 1000.0 if ts_ms else float("inf")
        if age < -5.0 or age > max_age:
            raise BrokerFehler(
                f"{inst_id}: Orderbuch ist nicht frisch genug ({age:.1f} s; "
                f"Maximum {max_age:.1f} s). Order blockiert.")
        sell = str(side or "").lower() == "sell"
        levels = []
        for raw_price, raw_qty in book.get("bids" if sell else "asks") or []:
            price, available = float(raw_price), float(raw_qty)
            if not math.isfinite(price) or not math.isfinite(available) or price <= 0 or available < 0:
                raise BrokerFehler(f"{inst_id}: ungueltiges Orderbuchlevel; Order blockiert.")
            if available > 0:
                levels.append((price, available))
        levels.sort(key=lambda row: float(row[0]), reverse=sell)
        remaining = qty
        value = 0.0
        worst = 0.0
        used_levels = 0
        for raw_price, raw_qty in levels:
            price, available = float(raw_price or 0.0), float(raw_qty or 0.0)
            if price <= 0 or available <= 0:
                continue
            take = min(remaining, available)
            value += take * price
            remaining -= take
            worst = price
            used_levels += 1
            if remaining <= max(1e-12, qty * 1e-10):
                break
        if remaining > max(1e-12, qty * 1e-10) or not used_levels:
            raise BrokerFehler(
                f"{inst_id}: Orderbuchtiefe reicht fuer {qty:g} {base_currency(inst_id)} "
                "nicht aus. Order blockiert.")
        best = float(levels[0][0])
        vwap = value / qty
        slippage = ((vwap - best) / best if not sell else (best - vwap) / best)
        return {
            "inst_id": inst_id, "side": "sell" if sell else "buy",
            "quantity": qty, "best": best, "vwap": vwap, "worst": worst,
            "slippage_pct": max(0.0, slippage), "book_age_seconds": max(0.0, age),
            "levels": used_levels, "timestamp_ms": ts_ms,
            "market_data_environment": environment,
            "source": "/api/v5/market/books",
            "book_levels": [[float(p), float(q)] for p, q in levels],
        }

    def _bounded_order_price(self, meta, quote, *, side, requested, own_limit):
        if bool(self.client.demo) != self.demo:
            raise BrokerFehler("OKX-Preisgrenze aus anderer Umgebung; keine Order gesendet.")
        import config
        max_age = max(0.5, float(getattr(config, "OKX_ORDERBOOK_MAX_AGE_SECONDS", 3.0)))
        rows = self.client.price_limit(meta.inst_id)
        now_ms = time.time() * 1000.0
        band = PriceBand.parse(rows, meta.inst_id, now_ms=now_ms, max_age_seconds=max_age)
        # The additional GET must not make the previously checked book stale.
        fresh(quote.get("timestamp_ms"), now_ms, max_age, "Orderbuch nach Preisgrenzenabfrage")
        result = bounded_limit(side=side, requested=requested, own_limit=own_limit,
                               best=quote["best"], worst=quote["worst"],
                               tick=meta.tick_size, band=band)
        quote["price_band"] = band.evidence("DEMO" if self.demo else "LIVE")
        quote["limit_before_price_band"] = str(requested)
        quote["price_band_adjusted"] = result != Decimal(str(requested))
        return float(result)

    # -- Positionen ---------------------------------------------------------
    def positionen(self, *, guthaben_snapshot: dict | None = None) -> list:
        """Spot-Bestaende als Positionen.

        ``guthaben_snapshot`` bindet Mengenabgleich und Exposure-Pruefung an
        exakt dieselbe Kontosicht. Ohne explizites Argument wird ein unmittelbar
        zuvor mit ``guthaben_schnappschuss`` erzeugter Snapshot genau einmal
        weiterverwendet.
        """
        if guthaben_snapshot is not None:
            guthaben = {k: dict(v) for k, v in guthaben_snapshot.items()}
        elif hasattr(self, "_last_balance_snapshot_for_positions"):
            guthaben = {k: dict(v) for k, v in self._last_balance_snapshot_for_positions.items()}
            del self._last_balance_snapshot_for_positions
        else:
            guthaben = self._balances_stream_or_rest()
        tickers: dict[str, OKXTicker] = {}
        # Auch nicht fuer neue Botorders freigegebene Settlementwaehrungen
        # bleiben Cash und duerfen nie als Krypto-Position erscheinen.
        cash_ccys = set(self.allowed_quotes) | {"EUR", "USDC", "USD", "USDG", "USDT"}
        interessant = [c for c in guthaben if c not in cash_ccys
                        and guthaben[c].get("gesamt", 0.0) > 0]
        if interessant:
            try:
                tickers = self.client.tickers()
            except BrokerFehler:
                tickers = {}

        out: list[Position] = []
        for ccy in interessant:
            eintrag = guthaben[ccy]
            menge = float(eintrag.get("gesamt", 0.0))
            inst_id = next((f"{ccy}-{quote}" for quote in self.allowed_quotes
                            if f"{ccy}-{quote}" in tickers), f"{ccy}-{self.quote_ccy}")
            quote = inst_id.rsplit("-", 1)[-1]
            preis = ((tickers[inst_id].bid or tickers[inst_id].last)
                     if inst_id in tickers else 0.0)
            if menge <= 0:
                continue
            out.append(Position(
                symbol=ccy, quantity=menge, avg_cost=0.0, currency=quote,
                asset_type="crypto", broker_id=inst_id,
                market_price=preis, market_value=menge * preis, unrealized_pnl=0.0,
            ))
        return out

    def fills(self) -> list:
        rows = self.client.fills(limit=100)
        out: list[Fill] = []
        for row in rows:
            inst_id = str(row.get("instId", "")).upper()
            menge = _f(row.get("fillSz"))
            if menge <= 0:
                continue
            out.append(Fill(
                fill_id=okx_fill_identity(row, self.account_fingerprint()),
                order_id=str(row.get("ordId", "")),
                symbol=base_currency(inst_id),
                side=str(row.get("side", "")).upper(),
                quantity=menge,
                price=_f(row.get("fillPx")),
                currency=inst_id.rsplit("-", 1)[-1] if "-" in inst_id else self.quote_ccy,
                asset_type="crypto",
                broker_id=inst_id,
                timestamp=self._iso(row.get("ts")),
                quantity_is_cumulative=False,
                # OKX liefert Gebuehren negativ (Abgang aus dem Konto).
                # v9.1: Bei einem SPOT-KAUF belastet OKX die Gebuehr in der
                # BASISwaehrung (feeCcy="BTC"), beim Verkauf in der Quote.
                # Ohne Umrechnung erschienen Kaufgebuehren um Groessenordnungen
                # zu niedrig -- und die Kostenhuerde eines Trades damit auch.
                explicit_fees=self._gebuehr_in_quote(row, inst_id) or None,
                raw_fill_id=str(row.get("tradeId") or ""),
                account_fingerprint=self.account_fingerprint(),
            ))
        return out

    @staticmethod
    def _gebuehr_in_quote(row: dict, inst_id: str) -> float:
        """Fill-Gebuehr in der Quotewaehrung des Marktes.

        OKX nennt die Waehrung der Gebuehr in ``feeCcy``. Stimmt sie mit der
        Basiswaehrung ueberein, muss mit dem Fillpreis umgerechnet werden.
        """
        betrag = abs(_f(row.get("fee")))
        if betrag <= 0:
            return 0.0
        fee_ccy = str(row.get("feeCcy") or "").upper()
        if fee_ccy and fee_ccy == base_currency(inst_id).upper():
            preis = _f(row.get("fillPx"))
            return betrag * preis if preis > 0 else 0.0
        return betrag

    @staticmethod
    def _iso(ms: Any) -> Optional[str]:
        wert = int(_f(ms, 0.0))
        if wert <= 0:
            return None
        return datetime.fromtimestamp(wert / 1000.0, timezone.utc).isoformat()

    # -- Orders -------------------------------------------------------------
    def _submit_primary_order(self, body: dict, *, reservation_key=None) -> dict:
        from execution_lifecycle import reserve, accepted, rejected, begin_submission
        from freqtrade_candles import require_signal_current
        context = dict(getattr(self, "_nexus_submit_context", {}) or {}) if body.get("side") == "buy" else {}
        try:
            require_signal_current(context, body["instId"])
        except ValueError as exc:
            raise BrokerFehler(str(exc)) from exc
        if reservation_key is not None:
            key = reservation_key
            begin_submission(key, body)
        else:
            key = reserve(broker="okx", account=self.account_fingerprint(),
                environment="DEMO" if self.demo else "LIVE", instrument=body["instId"],
                side=body["side"].upper(), client_id=body["clOrdId"],
                quantity=body["sz"], request=body)
        try:
            require_signal_current(context, body["instId"])
        except ValueError as exc:
            rejected(key, proven=True)
            raise BrokerFehler(str(exc)) from exc
        action_revision = None
        action_account = self.account_fingerprint()
        action_environment = "DEMO" if self.demo else "LIVE"
        if body.get("side") == "buy" and action_account:
            from okx_account_action import before_buy
            try:
                action_revision = before_buy(action_account, action_environment)
            except (ValueError, OSError) as exc:
                rejected(key, proven=True)
                raise BrokerFehler(str(exc)) from exc
        try:
            reply = self.client.place_order(body)
        except Exception as exc:
            code = _okx_fehlercode(exc)
            proven = bool(code and code not in (_UNKLARE_ORDER_CODES | {_DOPPELTE_CLORDID})) and not isinstance(exc, OrderStatusUnklar)
            try:
                rejected(key, proven=proven, evidence={'broker_code': str(code or ''),
                    'action_required': code == '54092', 'reason_code': 'ACCOUNT_CONFIRMATION' if code == '54092' else 'BROKER_REPLY'})
            except Exception as persist_error:
                raise OrderStatusUnklar("OKX-Antwort nicht dauerhaft gespeichert; Abgleich erforderlich.",
                    reference_id=body["clOrdId"], accepted=True) from persist_error
            if action_account:
                from okx_account_action import record, finish_retry
                if code == "54092":
                    record(action_account, action_environment, instrument=body["instId"])
                finish_retry(action_account, action_environment, action_revision)
            if proven or isinstance(exc, OrderStatusUnklar) or code == _DOPPELTE_CLORDID:
                raise
            raise OrderStatusUnklar("OKX-Uebermittlung unklar; kein zweiter POST.",
                reference_id=body["clOrdId"], accepted=True) from exc
        try:
            accepted(key, str(reply.get("ordId") or "") if isinstance(reply, dict) else "")
        except Exception as exc:
            raise OrderStatusUnklar("OKX-Antwort nach Uebermittlung nicht dauerhaft zugeordnet.",
                reference_id=body["clOrdId"], accepted=True,
                order_ids=[str(reply['ordId'])] if isinstance(reply,dict) and reply.get('ordId') else []) from exc
        if action_revision:
            from okx_account_action import finish_retry
            try:
                finish_retry(action_account, action_environment, action_revision, accepted=True)
            except (OSError, ValueError) as exc:
                raise OrderStatusUnklar('OKX-Auftrag angenommen; Kontohinweis nicht gespeichert. Abgleich erforderlich.',
                    reference_id=body['clOrdId'], accepted=True,
                    order_ids=[str(reply['ordId'])] if isinstance(reply, dict) and reply.get('ordId') else []) from exc
        return reply

    def kaufe_mit_absicherung(self, instrument, menge: float, referenzpreis: float,
                              stop: float, take_profit: float) -> OrderErgebnis:
        """Preisbegrenzter FOK-Kauf in Basismenge, danach Broker-OCO.

        Der Auftrag wird vor dem POST dauerhaft reserviert. Nur echte,
        terminale Fillbelege geben die Nettomenge fuer Buchung und Schutz frei.
        """
        if float(referenzpreis or 0.0) <= 0 or float(stop or 0.0) <= 0 or float(take_profit or 0.0) <= 0:
            raise BrokerFehler(
                "OKX-Kauf blockiert: Einstieg, Stop-Loss und Take-Profit muessen "
                "vor der Order als positive Preise feststehen."
            )
        if not (float(stop) < float(referenzpreis) < float(take_profit)):
            raise BrokerFehler(
                "OKX-Kauf blockiert: fuer einen Spot-Long muss Stop < Einstieg < Ziel gelten."
            )
        if not self.demo:
            from broker_live_arming import status as live_arm_status
            armed, detail, _ = live_arm_status("okx")
            if not armed:
                raise BrokerFehler(
                    "OKX LIVE-Einstieg blockiert: " + detail +
                    ". Ausstiege bleiben weiterhin erlaubt."
                )
        inst_id = self._inst_id(instrument)
        meta = self.client.instrument(inst_id)
        if meta is None or not meta.ist_live:
            raise BrokerFehler(f"{inst_id} ist bei OKX nicht handelbar.")

        sz = quantize_down(menge, meta.lot_size)
        if sz <= 0 or sz < _f(meta.min_size):
            raise BrokerFehler(
                f"{inst_id}: Menge {menge:g} liegt unter der OKX-Mindestgroesse "
                f"({meta.min_size} {meta.base_ccy})."
            )

        submit_context = dict(getattr(self, "_nexus_submit_context", {}) or {})
        cl_ord_id = str(submit_context.get("client_order_id") or make_client_order_id())[:32]
        # Eine noch offene Verkaufs-/Schutzorder kann Bestand einfrieren oder
        # nach einem neuen Einstieg feuern. Solange sie nicht eindeutig zum
        # bestehenden Positionsbuch gehoert, ist ein weiterer Kauf verboten.
        if self.hat_offene_order(instrument, "SELL", include_algos=True):
            raise BrokerFehler(
                f"{inst_id}: offene Verkaufs- oder Schutzorder vorhanden; "
                "neuer Einstieg bis zum Abgleich blockiert.")

        import config
        # Das Orderbuch wird hier ohnehin frisch geholt (client.orderbook()
        # hat keinen Cache). Die verbleibende Unschaerfe ist die
        # Boersen-Zeitmarke, die bis OKX_ORDERBOOK_MAX_AGE_SECONDS alt sein
        # darf, plus die Netzlaufzeit bis zum Matching. Genau diese Unschaerfe
        # muss der Preispuffer unten abdecken.
        quote = self.execution_quote(instrument, sz, side="buy")
        max_slippage = max(0.0, float(getattr(
            config, "OKX_MAX_ENTRY_SLIPPAGE_PCT", 0.006)))
        if float(quote["slippage_pct"]) > max_slippage:
            raise BrokerFehler(
                f"{inst_id}: erwartete Kauf-Slippage {quote['slippage_pct'] * 100:.3f}% "
                f"ueber Limit {max_slippage * 100:.3f}%. Order blockiert.")

        # KORREKTUR 9.5.4 -- das eigentliche Problem.
        #
        # Bis 9.5.3 lautete die Zeile:
        #     limit_px = quantize_up(float(quote["worst"]), meta.tick_size)
        #
        # ``worst`` ist der letzte Orderbuch-Preis, der noetig war, um GENAU
        # diese Menge zu fuellen -- gemessen im Moment des Snapshots. Bei
        # kleinen Ordern ist das schlicht der aktuelle beste Brief. Das Limit
        # sagte damit: "kaufe zum Briefkurs, den ich gerade gesehen habe",
        # aufgerundet um EINEN Tick (bei ETH 0,00042 %).
        #
        # Zwischen Messung und Matching liegen aber Zyklusrest und Netzlaufzeit.
        # Bewegt sich der Brief um einen einzigen Tick nach oben, ist die Menge
        # zum alten Preis weg -- und FOK heisst: ganz oder gar nicht. Am
        # 02.09.2026 wurden ETH, SOL und DOGE genau so mit 0 gefuellt
        # storniert, obwohl alle Gates bestanden hatten.
        #
        # Das Slippage-Budget von 0,6 % wurde dabei ausschliesslich als
        # ABLEHNUNGSschwelle benutzt, nie als Spielraum. Gemessen waren
        # 0,0147 % -- vierzigfach darunter.
        #
        # Der Verkaufspfad (schliesse_position) macht das seit laengerem
        # richtig: dort ist der Limitpreis best * (1 - max_slippage), also das
        # volle Budget als Spielraum. Nur der Kaufpfad war noch auf den exakt
        # gemessenen Preis festgenagelt -- er war der Ausreisser.
        #
        # Der Puffer ist bewusst KEINE Aufweichung der Preisgrenze: Er ist auf
        # das ohnehin erlaubte Restbudget gedeckelt. Es kann also nie teurer
        # werden, als die Slippage-Pruefung oben zulaesst. Einstiege bleiben
        # damit strenger als Ausstiege -- gewollt: aussteigen muss man koennen,
        # einsteigen nicht.
        # Die Slippage-Pruefung oben misst den VWAP (Durchschnittskosten), der
        # Limitpreis ist aber eine Obergrenze pro Stueck. Ein Restbudget vom
        # VWAP auf den schlechtesten Preis zu addieren kann die Grenze deshalb
        # ueberschreiten. Die harte Zusage lautet stattdessen: nie mehr als
        # max_slippage ueber dem besten Brief zum Messzeitpunkt.
        puffer = max(0.0, float(getattr(
            config, "OKX_ENTRY_PREIS_PUFFER_PCT", 0.0015)))
        worst = float(quote["worst"])
        obergrenze = float(quote["best"]) * (1.0 + max_slippage)
        # max(worst, ...) ist der Sonderfall eines teuren obersten Levels, bei
        # dem schon das Buch selbst ueber der Grenze liegt und die
        # VWAP-Pruefung nur wegen guenstiger unterer Level bestanden hat. Dort
        # bleibt es exakt beim Verhalten bis 9.5.3 -- nie schlechter.
        limit_roh = max(worst, min(worst * (1.0 + puffer), obergrenze))
        limit_px = quantize_up(limit_roh, meta.tick_size)
        if limit_px > obergrenze:
            raise BrokerFehler(f"{inst_id}: kein Kaufpreis-Tick innerhalb der harten Preisgrenze.")
        limit_px = self._bounded_order_price(
            meta, quote, side="buy", requested=limit_px, own_limit=obergrenze)
        quote["preis_puffer_pct"] = max(0.0, limit_roh / worst - 1.0)
        quote["limit_px"] = limit_px
        trade_quote = self._entry_trade_quote(meta, submit_context)
        body = {
            "instId": inst_id,
            "tdMode": "cash",
            "side": "buy",
            # FOK: entweder die gesamte geplante Botmenge innerhalb der
            # Preisgrenze oder gar nichts. Keine unerkannte Teilposition.
            "ordType": "fok",
            "px": format_decimal(limit_px, meta.tick_size),
            "sz": format_decimal(sz, meta.lot_size),
            "tradeQuoteCcy": trade_quote,
            "clOrdId": cl_ord_id,
            "tag": str(submit_context.get("order_tag") or
                       getattr(__import__("config"), "OKX_ORDER_TAG", "NEXUS"))[:16],
        }

        try:
            antwort = self._submit_primary_order(body)
        except OrderStatusUnklar as exc:
            # Nachsehen, ob die Order trotzdem angekommen ist. Der clOrdId
            # macht das eindeutig -- ohne ihn bliebe nur Raten.
            gefunden = self._suche_order(inst_id, cl_ord_id)
            if gefunden:
                antwort = gefunden
                logger.warning("OKX-Kauf %s: Transport unklar, Order per clOrdId wiedergefunden.", inst_id)
            else:
                raise OrderStatusUnklar(str(exc), reference_id=cl_ord_id) from None
        except BrokerFehler as exc:
            # v9.1: "Duplicated client order ID" heisst, dass die Order bei OKX
            # BEREITS LIEGT -- es ist ein Nachweis, keine Ablehnung. Die
            # clOrdId entsteht deterministisch aus der decision_id, also tritt
            # der Code nach jedem Wiederanlauf derselben Entscheidung auf.
            # Bis 9.0.15 wurde die Order daraufhin verworfen und die real
            # gefuellte Position verschwand aus dem Positionsbuch.
            if _DOPPELTE_CLORDID not in str(exc):
                raise
            gefunden = self._suche_order(inst_id, cl_ord_id)
            if gefunden:
                antwort = gefunden
                logger.warning(
                    "OKX-Kauf %s: clOrdId bereits vergeben -- bestehende Order "
                    "uebernommen statt neu zu senden.", inst_id)
            else:
                raise OrderStatusUnklar(
                    f"OKX meldet die clOrdId als vergeben, liefert die Order aber "
                    f"nicht zurueck ({exc}). Kein zweiter Versuch.",
                    reference_id=cl_ord_id) from None

        ord_id = str(antwort.get("ordId") or "") if isinstance(antwort, dict) else ""
        try:
            if not ord_id:
                raise BrokerFehler("Orderannahme ohne ordId")
            status = dict(self._warte_auf_fill(inst_id, ord_id, cl_ord_id))
            for row in (antwort, status):
                for field, expected in (("ordId",ord_id),("clOrdId",cl_ord_id),
                                        ("instId",inst_id),("side","buy")):
                    if row.get(field) not in (None,"",expected):
                        raise BrokerFehler("Kaufantwort mit widerspruechlicher " + field)
            status.setdefault("ordId",ord_id)
            status.setdefault("side", "buy")
            ergebnis = self._order_result_from_evidence(
                meta=meta,status=status,cl_ord_id=cl_ord_id,
                requested_qty=sz,reference_price=referenzpreis)
        except Exception as exc:
            raise OrderStatusUnklar("OKX-Kauf nach Uebermittlung noch nicht eindeutig belegt.",
                reference_id=cl_ord_id,order_ids=[ord_id] if ord_id else [],accepted=True,
                intent={"inst_id":inst_id,"qty":sz,"cl_ord_id":cl_ord_id,"ord_id":ord_id,
                        "signal_price":referenzpreis,"stop":stop,"take":take_profit}) from exc
        ergebnis.trade_quote_ccy = trade_quote
        ergebnis.execution_quote = quote
        if ergebnis.gross_filled_quantity > 0 and not ergebnis.fill_evidence_complete:
            raise OrderStatusUnklar(
                f"OKX-Order {ord_id or cl_ord_id} ausgefuehrt, echte tradeId-Fills noch nicht vollstaendig sichtbar.",
                reference_id=cl_ord_id, order_ids=[ord_id] if ord_id else [],
                accepted=True,
                intent={"inst_id": inst_id, "qty": sz, "signal_price": referenzpreis,
                        "stop": stop, "take": take_profit,
                        "cl_ord_id": cl_ord_id, "ord_id": ord_id})

        if ergebnis.filled_quantity <= 0:
            ergebnis.hinweis = (
                "FOK-Kauf wurde nicht vollstaendig innerhalb der Preisgrenze "
                "ausgefuehrt; es wurde keine Position angelegt.")
            return ergebnis
        # Der Schutz wird absichtlich erst vom Engine-Pfad gesetzt, nachdem
        # Position, Ledger und Fill-Kette dauerhaft geschrieben wurden.
        ergebnis.stop_order_platziert = False
        ergebnis.take_order_platziert = False
        ergebnis.hinweis = "Kauf bestaetigt; Schutz folgt nach dauerhafter Verbuchung."
        return ergebnis

    def _suche_order(self, inst_id: str, cl_ord_id: str) -> dict:
        """Nach unklarem Transport pruefen, ob die Order doch existiert."""
        for versuch in range(3):
            time.sleep(0.8 * (versuch + 1))
            try:
                gefunden = self.client.order_status(inst_id, cl_ord_id=cl_ord_id)
            except BrokerFehler:
                continue
            if gefunden and str(gefunden.get("ordId", "")):
                return gefunden
        return {}

    def _warte_auf_fill(self, inst_id: str, ord_id: str, cl_ord_id: str,
                        sekunden: float = 12.0) -> dict:
        """Wartet kurz auf die endgueltige Ausfuehrung der Marktorder."""
        ende = time.time() + max(1.0, sekunden)
        letzter: dict = {}
        leere_antworten = 0
        while time.time() < ende:
            if self._stream is not None:
                stream_row = self._stream.order(ord_id=ord_id, cl_ord_id=cl_ord_id)
                if stream_row:
                    letzter = stream_row
                    zustand = str(letzter.get("state", "")).lower()
                    if zustand in ("filled", "canceled"):
                        return letzter
            try:
                letzter = self.client.order_status(inst_id, ord_id=ord_id, cl_ord_id=cl_ord_id)
            except BrokerFehler as exc:
                logger.debug("OKX-Orderabfrage %s: %s", inst_id, exc)
                time.sleep(0.7)
                continue
            if not letzter:
                # Die Order ist dem Broker unbekannt. Weiterfragen bringt
                # nichts und haelt nur den Handelszyklus auf.
                leere_antworten += 1
                if leere_antworten >= 3:
                    logger.warning("OKX kennt Order %s/%s nicht -- Abfrage beendet.",
                                   ord_id or "-", cl_ord_id or "-")
                    return letzter
                time.sleep(0.7)
                continue
            leere_antworten = 0
            zustand = str(letzter.get("state", "")).lower()
            if zustand in ("filled", "canceled"):
                return letzter
            time.sleep(0.7)
        return letzter

    @staticmethod
    def _protection_quote(meta: OKXInstrument, trade_quote_ccy: str) -> str:
        quote = str(trade_quote_ccy or meta.quote_ccy).strip().upper()
        accepted = tuple(meta.trade_quote_ccy_list or (meta.quote_ccy,))
        if not quote or quote not in accepted:
            raise BrokerFehler(
                f"{meta.inst_id}: Abrechnungswaehrung {quote or '-'} der Position "
                "ist fuer die Schutzorder nicht durch die Instrumentregeln belegt.")
        return quote

    def _setze_schutz(self, meta: OKXInstrument, menge: float,
                      stop: float, take_profit: float, *,
                      trade_quote_ccy: str = "",
                      protection_client_id: str = "") -> dict:
        """Legt eine OCO-Algo-Order fuer Stop-Loss und Gewinnziel an.

        OCO heisst: greift eine Seite, wird die andere automatisch storniert.
        Das verhindert die klassische Doppelausfuehrung, bei der Stop UND
        Ziel feuern und der Bot am Ende short steht.
        """
        sz = quantize_down(menge, meta.lot_size)
        if sz <= 0 or sz < _f(meta.min_size):
            return {"stop": False, "take": False,
                    "hinweis": "Restmenge zu klein fuer eine Schutzorder; Client-Stop bleibt aktiv."}

        stop_px = quantize_price(stop, meta.tick_size) if stop and stop > 0 else 0.0
        take_px = quantize_price(take_profit, meta.tick_size) if take_profit and take_profit > 0 else 0.0
        if stop_px <= 0 and take_px <= 0:
            return {"stop": False, "take": False, "hinweis": "Keine Schutzpreise uebergeben."}
        quote = self._protection_quote(meta, trade_quote_ccy)

        import config
        algo_cl_id = str(protection_client_id or make_client_order_id("TBP9"))[:32]
        import okx_protection_journal as protection_journal
        protection_scope = dict(account=self.account_fingerprint(),
                                environment="DEMO" if self.demo else "LIVE",
                                instrument=meta.inst_id, client_id=algo_cl_id)
        # Idempotenz: nach einem unklaren POST oder Neustart niemals blind
        # dieselbe Schutzorder ein zweites Mal senden.
        for existing in self.offene_schutzorders(meta.inst_id, strict=True):
            if str(existing.get("algoClOrdId") or "") == algo_cl_id:
                qty_ok = abs(_f(existing.get("sz")) - sz) <= max(
                    _f(meta.lot_size), sz * 0.001)
                stop_ok = (stop_px <= 0 or abs(
                    _f(existing.get("slTriggerPx")) - stop_px) <= _f(meta.tick_size))
                take_ok = (take_px <= 0 or abs(
                    _f(existing.get("tpTriggerPx")) - take_px) <= _f(meta.tick_size))
                quote_ok = str(existing.get("tradeQuoteCcy") or meta.quote_ccy).upper() == quote
                if not (qty_ok and stop_ok and take_ok and quote_ok):
                    return {
                        "stop": False, "take": False, "checked": True,
                        "protection_confirmed": False,
                        "algo_id": str(existing.get("algoId") or ""),
                        "algo_client_id": algo_cl_id,
                        "hinweis": "Eigene Schutz-ID gefunden, aber Menge/SL/TP/Abrechnungswaehrung weichen ab.",
                    }
                protection_journal.update(**protection_scope, state="CONFIRMED",
                                          algo_id=str(existing.get("algoId") or ""))
                return {
                    "stop": stop_px > 0, "take": take_px > 0,
                    "checked": True, "protection_confirmed": True,
                    "algo_id": str(existing.get("algoId") or ""),
                    "algo_client_id": algo_cl_id,
                    "hinweis": "Vorhandener Broker-Schutz eindeutig wiedergefunden.",
                }

        stop_limit_pct = max(0.0, float(getattr(
            config, "OKX_STOP_LIMIT_SLIPPAGE_PCT", 0.01)))
        stop_order_px = quantize_down(stop_px * (1.0 - stop_limit_pct), meta.tick_size)
        # v9.1: Bei Cent- und Sub-Cent-Coins faellt der abgeschlagene
        # Limitpreis unter einen Tick und wird auf 0 abgerundet. OKX lehnt die
        # GESAMTE OCO ab -- und die Position lief bis 9.0.15 ohne jeden
        # Broker-Schutz weiter, waehrend hier "stop: True" gemeldet wurde.
        if stop_px > 0 and stop_order_px <= 0:
            stop_order_px = quantize_down(stop_px, meta.tick_size)
        if stop_px > 0 and stop_order_px <= 0:
            return {"stop": False, "take": False, "checked": True,
                    "hinweis": ("Stop-Limitpreis liegt unter einem Tick; "
                                "Broker-Schutz nicht setzbar. Client-Stop bleibt aktiv.")}
        body = {
            "instId": meta.inst_id,
            "tdMode": "cash",
            "side": "sell",
            "ordType": "oco" if (stop_px > 0 and take_px > 0) else "conditional",
            "sz": format_decimal(sz, meta.lot_size),
            # Documented for SPOT algo orders. Preserve the position's actual
            # settlement currency, including USD; never substitute USDC.
            "tradeQuoteCcy": quote,
            "algoClOrdId": algo_cl_id,
        }
        if stop_px > 0:
            body["slTriggerPx"] = format_decimal(stop_px, meta.tick_size)
            # Kein unbeschraenkter Marktverkauf. Unterhalb dieser Grenze
            # bleibt die Position bestehen und wird sichtbar als ungeschuetzt
            # gemeldet, statt wie DOGE mit beliebigem Verlust verkauft.
            body["slOrdPx"] = format_decimal(stop_order_px, meta.tick_size)
            body["slTriggerPxType"] = "last"
        if take_px > 0:
            body["tpTriggerPx"] = format_decimal(take_px, meta.tick_size)
            body["tpOrdPx"] = format_decimal(take_px, meta.tick_size)
            body["tpTriggerPxType"] = "last"

        retry_key = (meta.inst_id, algo_cl_id)
        request_key = json.dumps(body, sort_keys=True)
        retries = getattr(self, "_protection_rejections", {})
        self._protection_rejections = retries
        previous = retries.get(retry_key)
        if (previous and previous["request"] == request_key
                and time.monotonic() < previous["until"]):
            return dict(previous["result"])
        unresolved = protection_journal.begin(**protection_scope, request=body)
        if unresolved:
            return {"stop": False, "take": False, "checked": False,
                    "algo_id": str(unresolved.get("algo_id") or ""),
                    "algo_client_id": algo_cl_id,
                    "hinweis": "Bereits gesendeter Schutzauftrag noch unbestaetigt; kein weiterer POST, auch nach Neustart."}
        accepted_post = False
        placed_id = ""
        try:
            placed = self.client.place_algo_order(body)
            accepted_post = True
            retries.pop(retry_key, None)
            placed_id = str(placed.get("algoId") or "")
            protection_journal.update(**protection_scope, state="ACCEPTED", algo_id=placed_id)
            # Eine erfolgreiche POST-Antwort ist noch kein aktiver Schutz.
            # Mindestens eine frische Brokerabfrage muss exakt dieselbe
            # algoId/algoClOrdId, Menge und Triggerpreise zurueckgeben.
            for attempt in range(5):
                if attempt:
                    time.sleep(0.25)
                rows = self.offene_schutzorders(meta.inst_id, strict=True)
                exact = [row for row in rows
                         if ((placed_id and str(row.get("algoId") or "") == placed_id)
                             or str(row.get("algoClOrdId") or "") == algo_cl_id)]
                if len(exact) != 1:
                    continue
                row = exact[0]
                qty_ok = abs(_f(row.get("sz")) - sz) <= max(
                    _f(meta.lot_size), sz * 0.001)
                stop_ok = (stop_px <= 0 or abs(
                    _f(row.get("slTriggerPx")) - stop_px) <= _f(meta.tick_size))
                take_ok = (take_px <= 0 or abs(
                    _f(row.get("tpTriggerPx")) - take_px) <= _f(meta.tick_size))
                quote_ok = str(row.get("tradeQuoteCcy") or meta.quote_ccy).upper() == quote
                if qty_ok and stop_ok and take_ok and quote_ok:
                    protection_journal.update(**protection_scope, state="CONFIRMED",
                                              algo_id=str(row.get("algoId") or placed_id))
                    return {"stop": stop_px > 0, "take": take_px > 0,
                            "checked": True, "protection_confirmed": True,
                            "algo_id": str(row.get("algoId") or placed_id),
                            "algo_client_id": algo_cl_id,
                            "hinweis": "Broker-Schutz aktiv und exakt rueckgelesen."}
            return {"stop": False, "take": False, "checked": False,
                    "protection_confirmed": False,
                    "algo_id": placed_id, "algo_client_id": algo_cl_id,
                    "hinweis": "Schutz-POST angenommen, aber Menge/SL/TP noch nicht exakt rueckgelesen."}
        except OrderStatusUnklar:
            # Unklar heisst hier: eventuell liegt der Schutz doch. Nicht
            # blind erneut senden -- der Reconcile-Lauf raeumt das auf.
            return {"stop": False, "take": False, "checked": False,
                    "algo_client_id": algo_cl_id,
                    "hinweis": "Schutzorder-Zustand unklar; wird beim naechsten Abgleich geprueft."}
        except BrokerFehler as exc:
            if accepted_post:
                return {"stop": False, "take": False, "checked": False,
                        "algo_id": placed_id, "algo_client_id": algo_cl_id,
                        "hinweis": f"Schutz-POST angenommen; Ruecklesen fehlgeschlagen ({exc})."}
            protection_journal.update(**protection_scope, state="REJECTED")
            if "51634" in str(exc):
                # Support-Ticket 7914190: Vertrag unbekannt. Der naechste
                # Abgleich liest den Kontokatalog frisch, ohne Instrumente
                # oder die Abrechnungswaehrung der Position umzubenennen.
                invalidate = getattr(self.client, "invalidate_instruments", None)
                if callable(invalidate):
                    invalidate()
            logger.warning("OKX-Schutzorder %s abgelehnt: %s", meta.inst_id, exc)
            retry_at = datetime.fromtimestamp(time.time() + 300, timezone.utc).isoformat()
            result = {"stop": False, "take": False, "checked": True,
                      "algo_client_id": algo_cl_id,
                      "hinweis": (f"Broker-Schutz abgelehnt ({exc}); Client-Stop bleibt aktiv. "
                                  f"Erneutes Senden unveraenderter Schutzdaten ab {retry_at} (UTC).")}
            # Only a definite rejection delays another POST. Broker reads and
            # the client stop still run every cycle. Changed parameters can be
            # attempted immediately; a transport-unknown POST is not rejected.
            retries[retry_key] = {"request": request_key, "until": time.monotonic() + 300,
                                  "result": result}
            return result

    def schliesse_position(self, instrument, menge: float,
                           referenzpreis: float = 0.0, *,
                           client_order_id: str = "",
                           protection_algo_id: str = "",
                           protection_client_id: str = "",
                           account_fingerprint: str = "") -> OrderErgebnis:
        self.letzte_stornierung = {}
        inst_id = self._inst_id(instrument)
        current_account = self.account_fingerprint()
        if (str(account_fingerprint or "") and current_account
                and str(account_fingerprint) != current_account):
            raise BrokerFehler(
                f"{inst_id}: Verkauf fuer anderes OKX-Konto blockiert")
        meta = self.client.instrument(inst_id)
        if meta is None:
            raise BrokerFehler(f"{inst_id} ist bei OKX unbekannt.")

        import config
        planned = quantize_down(float(menge or 0.0), meta.lot_size)
        if planned <= 0:
            return OrderErgebnis(status="keine_menge", filled_quantity=0.0, paper=self.demo)
        # Erst pruefen, ob die volle Menge aktuell innerhalb der erlaubten
        # Preisgrenze verkauft werden kann. Bis dahin bleibt der Schutz aktiv.
        quote = self.execution_quote(instrument, planned, side="sell")
        max_slippage = max(0.0, float(getattr(
            config, "OKX_MAX_EXIT_SLIPPAGE_PCT", 0.01)))
        if float(quote["slippage_pct"]) > max_slippage:
            raise BrokerFehler(
                f"{inst_id}: erwartete Verkaufs-Slippage {quote['slippage_pct'] * 100:.3f}% "
                f"ueber Limit {max_slippage * 100:.3f}%; Schutz bleibt aktiv.")
        pre_best = float(quote.get("best") or quote.get("vwap") or quote["worst"])
        pre_worst = float(quote.get("worst") or 0.0)
        hard_slippage = max(max_slippage, float(getattr(
            config, "OKX_MAX_EXIT_SLIPPAGE_HARD_PCT", 0.03)))
        pre_floor = pre_best * (1.0 - hard_slippage)
        if quantize_up(pre_floor, meta.tick_size) > quantize_down(pre_best, meta.tick_size):
            raise BrokerFehler(f"{inst_id}: kein gueltiger Preis-Tick innerhalb der "
                               "harten Verkaufsgrenze; Schutz bleibt aktiv.")
        if pre_worst > 0 and pre_worst < pre_floor:
            raise BrokerFehler(
                f"{inst_id}: Orderbuch traegt die Menge nur bis {pre_worst:.10g}; "
                f"harte Verkaufsgrenze {hard_slippage * 100:.2f}%. "
                "Keine Order gesendet, Schutz bleibt aktiv.")

        # Check the exchange band BEFORE canceling protection. A lower sell
        # limit is not automatically valid merely because it crosses the bid.
        self._bounded_order_price(meta, quote, side="sell",
            requested=min(pre_worst, pre_best * (1.0 - max_slippage)),
            own_limit=pre_floor)

        from execution_lifecycle import assert_clear
        assert_clear(broker="okx", account=current_account,
            environment="DEMO" if self.demo else "LIVE", instrument=inst_id, side="SELL")
        from execution_lifecycle import reserve, abandon_prepared
        cl_ord_id = str(client_order_id or make_client_order_id("TBS9"))[:32]
        try:
            key = reserve(broker="okx", account=current_account,
                environment="DEMO" if self.demo else "LIVE", instrument=inst_id,
                side="SELL", client_id=cl_ord_id, quantity=planned, prepared=True,
                request={"instId": inst_id, "clOrdId": cl_ord_id, "side": "sell",
                         "sz": str(planned), "protection_algo_id": protection_algo_id,
                         "protection_client_id": protection_client_id})
        except (OrderStatusUnklar, BrokerFehler):
            raise
        except Exception as exc:
            raise BrokerFehler("Verkaufsabsicht nicht speicherbar; Schutz nicht storniert und keine Order gesendet.") from exc
        try:
            result = self._schliesse_position_reserved(instrument, menge, referenzpreis,
                meta=meta, planned=planned, max_slippage=max_slippage,
                client_order_id=cl_ord_id, protection_algo_id=protection_algo_id,
                protection_client_id=protection_client_id, reservation_key=key)
            # E.g. no tradable quantity after unlock: no POST happened.
            abandon_prepared(key)
            return result
        except OrderStatusUnklar:
            raise
        except Exception as exc:
            try:
                not_sent = abandon_prepared(key)
            except Exception as journal_error:
                raise OrderStatusUnklar("Verkaufszustand nicht dauerhaft abschliessbar; zuerst abgleichen.",
                    reference_id=cl_ord_id) from journal_error
            if isinstance(exc, BrokerFehler):
                raise
            if not_sent:
                raise BrokerFehler("Verkauf vor Uebermittlung abgebrochen; Broker-Schutz muss erneut bestaetigt werden.") from exc
            raise OrderStatusUnklar("Verarbeitung nach Uebermittlungsbeginn fehlgeschlagen; kein zweiter Auftrag.",
                                   reference_id=cl_ord_id) from exc

    def _schliesse_position_reserved(self, instrument, menge, referenzpreis, *,
            meta, planned, max_slippage, client_order_id, protection_algo_id,
            protection_client_id, reservation_key):
        import config
        inst_id = meta.inst_id
        # Erst jetzt den Schutz entfernen: eine Algo-Order kann Guthaben
        # einfrieren und der neue IOC-Verkauf wuerde sonst abgelehnt.
        self.storniere_offene_orders(
            instrument, protection_algo_id=protection_algo_id,
            protection_client_id=protection_client_id)
        if self.letzte_stornierung.get("fehler"):
            raise BrokerFehler("Schutzorder konnte nicht sicher storniert werden; Verkauf nicht gesendet.")

        # OKX bestaetigt die Stornierung vor der Freigabe des eingefrorenen
        # Guthabens. Ein einmaliger Balance-Read erzeugte deshalb bei LINK/
        # ONDO einen FOK mit Menge null. Polling ist begrenzt und sendet keine
        # Order, solange Schutz oder Guthaben noch nicht eindeutig frei sind.
        release_timeout = max(2.0, float(getattr(config, "OKX_CANCEL_RELEASE_TIMEOUT", 10.0)))
        # 9.5.8: Dieselbe Regel wie im Schutzpfad, jetzt an einer Stelle. Das
        # Verhalten bleibt exakt wie bisher: warten, bis die eigene Schutzorder
        # weg UND das Guthaben frei ist -- sonst keine Verkaufsorder.
        self._freigabe_vormerken(inst_id)

        def _schutz_liegt_noch() -> bool:
            return bool([row for row in self.offene_schutzorders(inst_id, strict=True)
                         if ((protection_algo_id and str(row.get("algoId") or "") ==
                              str(protection_algo_id))
                             or (protection_client_id and
                                 str(row.get("algoClOrdId") or "") ==
                                 str(protection_client_id)))])

        frei, freigegeben = self.warte_auf_guthabenfreigabe(
            meta.base_ccy, planned, inst_id=inst_id,
            lot_size=float(meta.lot_size), timeout=release_timeout,
            blockiert=_schutz_liegt_noch, immer_warten=True)
        if not freigegeben:
            raise BrokerFehler(
                f"{inst_id}: Schutzstorno/Guthabenfreigabe nach {release_timeout:.0f}s "
                "nicht bestaetigt; keine Verkaufsorder gesendet.")
        verfuegbar = float(frei or 0.0)

        sz = quantize_down(min(float(menge or 0.0), verfuegbar), meta.lot_size)
        if sz <= 0:
            return OrderErgebnis(status="keine_menge", filled_quantity=0.0, paper=self.demo,
                                 hinweis=f"Kein verfuegbares {meta.base_ccy}-Guthaben zum Verkauf.")
        if sz < _f(meta.min_size):
            return OrderErgebnis(status="unter_mindestgroesse", filled_quantity=0.0, paper=self.demo,
                                 hinweis=f"Restmenge {sz:g} liegt unter OKX-Minimum {meta.min_size}.")

        # Nach der Freigabe ist das alte Orderbuch veraltet. Neu messen und
        # den IOC-Limitpreis bis zur konfigurierten Verlustgrenze erlauben.
        # Das bleibt preisbegrenzt, ist aber nicht mehr auf den exakten,
        # millisekundenalten schlechtesten Bid festgenagelt.
        quote = self.execution_quote(instrument, sz, side="sell")
        if float(quote["slippage_pct"]) > max_slippage:
            raise BrokerFehler(
                f"{inst_id}: Verkaufs-Slippage nach Schutzstorno {quote['slippage_pct'] * 100:.3f}% "
                f"ueber Limit {max_slippage * 100:.3f}%; Order nicht gesendet.")
        # 9.5.8: Das Limit muss die Tiefe abdecken, die gerade gemessen wurde.
        #
        # Bis 9.5.7 stand hier nur ``best * (1 - max_slippage)``. Die
        # Zulassungspruefung darueber misst aber den VWAP -- einen Durchschnitt
        # ueber die gesamte Menge. Liegt ``worst`` (der tiefste Bid, der zum
        # Fuellen gebraucht wird) weiter als max_slippage unter ``best``, dann
        # besteht die Order die VWAP-Pruefung muehelos und kann trotzdem
        # strukturell nicht fuellen: FOK ist alles-oder-nichts, der Rest liegt
        # unter dem Limit. Ergebnis: 0 gefuellt, OKX cancelSource=13. Genau das
        # zeigte XLM am 03.09.2026 -- Slippage 0,205 % bei einer Grenze von
        # 1,0 %, und trotzdem kein einziges Stueck.
        #
        # Es ist derselbe Fehler, den 9.5.4 auf der KAUFseite behoben hat, nur
        # spiegelverkehrt: dort war das Limit zu niedrig, hier ist es zu hoch.
        # Der Kommentar im Kaufpfad behauptete sogar, der Verkaufspfad mache es
        # "seit laengerem richtig". Das stimmte nur, solange
        # ``worst >= best * (1 - max_slippage)`` war.
        #
        # ``min(worst, ...)`` senkt das Limit hoechstens bis auf den bereits
        # gemessenen schlechtesten Bid. Die VWAP-Grenze bleibt die
        # Zulassungsschwelle: der DURCHSCHNITT bleibt im Budget, nur das
        # schlechteste einzelne Level darf tiefer liegen. Aussteigen koennen ist
        # wichtiger als der letzte Zehntelprozent.
        #
        # Wichtig und bewusst: diese Zusage gilt fuer das Buch ZUM MESSZEITPUNKT.
        # Bricht die Tiefe zwischen Messung und Ordereingang weg (das Buch darf
        # bis OKX_ORDERBOOK_MAX_AGE_SECONDS alt sein), kann die tatsaechlich
        # erzielte Slippage groesser ausfallen als max_slippage. Deshalb die
        # harte Untergrenze darunter: sie begrenzt den Schaden auch dann noch.
        best = float(quote.get("best") or quote.get("vwap") or quote["worst"])
        worst = float(quote["worst"])
        hart = max(max_slippage, float(getattr(
            config, "OKX_MAX_EXIT_SLIPPAGE_HARD_PCT", 0.03)))
        untergrenze = best * (1.0 - hart)
        limit_roh = min(worst, best * (1.0 - max_slippage)) if worst > 0 else \
            best * (1.0 - max_slippage)
        if limit_roh < untergrenze:
            # Das Buch ist so duenn, dass selbst die Notgrenze nicht reicht.
            # Dann NICHT senden -- der Schutz bleibt liegen und die naechste
            # Runde misst neu. Ein Verkauf zu jedem Preis ist kein Ausstieg.
            raise BrokerFehler(
                f"{inst_id}: Orderbuch traegt die Menge nur bis "
                f"{worst:.10g} ({(1.0 - worst / best) * 100.0:.2f}% unter dem "
                f"besten Brief); die harte Verkaufsgrenze liegt bei "
                f"{hart * 100.0:.2f}%. Keine Order gesendet; Broker-Schutz muss erneut bestaetigt werden.")
        # Abrunden darf die harte Untergrenze nicht nachtraeglich unterschreiten.
        hard_floor_px = quantize_up(untergrenze, meta.tick_size)
        limit_px = max(quantize_down(limit_roh, meta.tick_size), hard_floor_px)
        if limit_px > best:
            raise BrokerFehler(f"{inst_id}: kein gueltiger Preis-Tick innerhalb der "
                               "harten Verkaufsgrenze nach Schutzstorno; Order nicht gesendet.")
        # Cancellation/unlock can take seconds: re-read BOTH book and band.
        # Rounding UP the sell floor improves the minimum proceeds; no
        # market fallback or pxAmendType can silently change this request.
        limit_px = self._bounded_order_price(
            meta, quote, side="sell", requested=limit_px, own_limit=untergrenze)
        limit_alt = quantize_down(best * (1.0 - max_slippage), meta.tick_size)
        quote["limit_px"] = limit_px
        # Wahr, wenn die Fassung bis 9.5.7 hier einen 0-Fill erzeugt haette.
        # Damit ist im Nachhinein zaehlbar, wie oft der Defekt zugeschlagen hat.
        quote["buchtiefe_gerettet"] = bool(worst > 0 and limit_alt > worst)
        quote["preis_abschlag_pct"] = (
            max(0.0, 1.0 - (limit_px / best)) if best > 0 else 0.0)
        if quote["buchtiefe_gerettet"]:
            # Sichtbar machen, dass das Limit unter die normale Slippage-Grenze
            # gesenkt wurde -- sonst bleibt genau der Fall unbemerkt, in dem der
            # Verkauf teurer war als das eingestellte Budget.
            logger.warning(
                "OKX-Verkauf %s: Limit auf %.10g gesenkt (%.3f%% unter dem "
                "besten Brief statt %.3f%%), weil das Orderbuch die Menge nur "
                "bis dorthin traegt. Bis 9.5.7 waere die Order storniert worden.",
                inst_id, limit_px, quote["preis_abschlag_pct"] * 100.0,
                max_slippage * 100.0)
        cl_ord_id = str(client_order_id or make_client_order_id("TBS9"))[:32]
        trade_quote = self._trade_quote_ccy(meta)
        # Ohne diese Zeile war nach einem 0-Fill nicht zu entscheiden, ob das
        # Buch zu duenn war, das Limit falsch lag oder die Menge nicht stimmte.
        logger.info(
            "OKX-Verkauf %s: sz=%s best=%.10g vwap=%.10g worst=%.10g "
            "slippage=%.4f%% limit=%s quote=%s umgebung=%s buchzeit=%s "
            "okx_sell_min=%s preisgrenzenzeit=%s",
            inst_id, format_decimal(sz, meta.lot_size), best,
            float(quote.get("vwap") or 0.0), worst,
            float(quote["slippage_pct"]) * 100.0,
            format_decimal(limit_px, meta.tick_size), trade_quote,
            quote.get("market_data_environment", "UNKNOWN"), quote.get("timestamp_ms"),
            quote["price_band"]["sellLmt"], quote["price_band"]["ts"])
        body = {
            "instId": inst_id, "tdMode": "cash", "side": "sell", "ordType": "ioc",
            "px": format_decimal(limit_px, meta.tick_size),
            "sz": format_decimal(sz, meta.lot_size),
            "tradeQuoteCcy": trade_quote,
            "clOrdId": cl_ord_id,
            "tag": str(getattr(__import__("config"), "OKX_ORDER_TAG", "NEXUS"))[:16],
        }
        try:
            antwort = self._submit_primary_order(body, reservation_key=reservation_key)
        except OrderStatusUnklar as exc:
            gefunden = self._suche_order(inst_id, cl_ord_id)
            if not gefunden:
                raise OrderStatusUnklar(str(exc), reference_id=cl_ord_id) from None
            antwort = gefunden
        except BrokerFehler as exc:
            if _DOPPELTE_CLORDID not in str(exc):
                raise
            # A duplicate client ID can refer to a previously accepted SELL.
            # Like the entry path, reconcile that order instead of allowing
            # a fresh attempt and a new protective SELL for the same quantity.
            gefunden = self._suche_order(inst_id, cl_ord_id)
            if not gefunden:
                raise OrderStatusUnklar(
                    "OKX meldet die Verkaufs-clOrdId als vergeben; bestehende "
                    "Order noch nicht lesbar. Kein zweiter Verkauf.",
                    reference_id=cl_ord_id) from None
            antwort = gefunden

        ord_id = str(antwort.get("ordId") or "") if isinstance(antwort, dict) else ""
        try:
            if not ord_id:
                raise BrokerFehler("Orderannahme ohne ordId")
            status = dict(self._warte_auf_fill(inst_id, ord_id, cl_ord_id))
            for row in (antwort, status):
                for key, expected in (("ordId", ord_id), ("clOrdId", cl_ord_id),
                                      ("instId", inst_id), ("side", "sell")):
                    if row.get(key) not in (None, "", expected):
                        raise BrokerFehler("Verkaufsantwort mit widerspruechlicher " + key)
            # The acknowledgement is an order identity, never a fill proof.
            status.setdefault("ordId", ord_id)
            status.setdefault("side", "sell")
            result = self._order_result_from_evidence(
                meta=meta, status=status, cl_ord_id=cl_ord_id,
                requested_qty=sz, reference_price=referenzpreis)
            for fill in result.fills:
                if (fill.get("instId") != inst_id or fill.get("ordId") != ord_id
                        or fill.get("side") not in (None, "", "sell")):
                    raise BrokerFehler("Verkaufsfill mit widerspruechlicher Identitaet")
        except Exception as exc:
            # Everything after accepted/maybe-accepted submission is a lookup
            # or accounting problem, not a proven rejected order. In particular
            # BrokerFehler must not fall into the engine's ordinary retry path.
            logger.warning("OKX-Verkauf %s: Ausfuehrungsbeleg unklar (%s)",
                           ord_id or cl_ord_id, type(exc).__name__)
            raise OrderStatusUnklar(
                f"OKX-Verkauf {ord_id or cl_ord_id}: Antwort/Fillbeleg nach "
                "Uebermittlung nicht eindeutig. Kein zweiter Verkauf.",
                reference_id=cl_ord_id, order_ids=[ord_id] if ord_id else [],
                accepted=True, intent={"inst_id": inst_id, "qty": sz,
                    "cl_ord_id": cl_ord_id, "ord_id": ord_id, "side": "sell"}) from None
        result.trade_quote_ccy = trade_quote
        result.execution_quote = quote
        # Keep the exact submitted order separately from the exchange response.
        # No credentials or authentication headers are persisted here.
        result.execution_evidence.update({
            "submitted_order": dict(body),
            "market_data_environment": quote.get("market_data_environment", "UNKNOWN"),
            "broker_environment": "DEMO" if self.demo else "LIVE",
        })
        if result.gross_filled_quantity > 0 and not result.fill_evidence_complete:
            raise OrderStatusUnklar(
                f"OKX-Verkauf {ord_id or cl_ord_id} meldet Ausfuehrung, "
                "aber die echten tradeId-Fills sind noch nicht vollstaendig sichtbar. "
                "Kein zweiter SELL.",
                reference_id=cl_ord_id,
                order_ids=[ord_id] if ord_id else [], accepted=True,
                intent={"inst_id": inst_id, "qty": sz,
                        "cl_ord_id": cl_ord_id, "ord_id": ord_id,
                        "side": "sell"})
        if result.filled_quantity <= 0:
            cancel_source = str(status.get("cancelSource") or "")
            broker_detail = str(status.get("sMsg") or status.get("msg") or "")
            # 9.5.8: Auch ein stornierter FOK hat Guthaben reserviert; OKX gibt
            # es verzoegert wieder frei. Der Schutz wird direkt danach erneuert
            # -- ohne diesen Vermerk laese er wieder zu wenig und bekaeme 51008.
            self._freigabe_vormerken(inst_id)
            result.hinweis = (
                "IOC-Verkauf wurde nicht innerhalb der Preisgrenze "
                "ausgefuehrt; Broker-Schutz muss sofort erneuert werden."
                + (f" OKX cancelSource={cancel_source}." if cancel_source else "")
                + (f" {broker_detail}" if broker_detail else "")
                + f" Limit war {format_decimal(limit_px, meta.tick_size)}"
                  f" bei best {best:.10g} / worst {worst:.10g}"
                  f" fuer {format_decimal(sz, meta.lot_size)} {meta.base_ccy}.")
        return result

    def offene_orders(self) -> list:
        out = []
        for row in self.client.pending_orders():
            out.append({
                "order_id": str(row.get("ordId", "")),
                "symbol": base_currency(str(row.get("instId", ""))),
                "instrument": str(row.get("instId", "")),
                "side": str(row.get("side", "")).upper(),
                "quantity": _f(row.get("sz")),
                "price": _f(row.get("px")),
                "status": str(row.get("state", "")),
                "typ": "order",
            })
        try:
            for row in self.client.pending_algo_orders():
                out.append({
                    "order_id": str(row.get("algoId", "")),
                    "symbol": base_currency(str(row.get("instId", ""))),
                    "instrument": str(row.get("instId", "")),
                    "side": str(row.get("side", "")).upper(),
                    "quantity": _f(row.get("sz")),
                    "price": _f(row.get("slTriggerPx")) or _f(row.get("tpTriggerPx")),
                    "status": str(row.get("state", "")),
                    "typ": "schutz",
                })
        except BrokerFehler as exc:
            logger.debug("OKX-Algo-Orders nicht abrufbar: %s", exc)
        return out

    def hat_offene_order(self, instrument, seite: str = "BUY", *,
                         include_algos: bool = False) -> bool:
        inst_id = self._inst_id(instrument)
        try:
            offen = self.client.pending_orders(inst_id)
        except BrokerFehler as exc:
            logger.warning("%s: offene Standardorders nicht sicher pruefbar: %s; "
                           "Order vorsorglich als offen behandelt.", inst_id, exc)
            return True
        gesucht = str(seite or "BUY").lower()
        if any(str(r.get("side", "")).lower() == gesucht for r in offen):
            return True
        if include_algos and gesucht == "sell":
            try:
                return any(str(r.get("side", "")).lower() == "sell"
                           for r in self.offene_schutzorders(inst_id, strict=True))
            except BrokerFehler as exc:
                logger.warning("%s: Schutzorders nicht sicher pruefbar: %s; "
                               "neuer Einstieg blockiert.", inst_id, exc)
                return True
        return False

    # Schutzorders koennen als OCO (Stop UND Ziel) oder als "conditional"
    # (nur eines von beiden) angelegt sein. Bis v8.1.3 wurde nur nach OCO
    # gefragt -- eine conditional-Order wurde weder storniert noch als
    # vorhanden erkannt: doppelter Schutz und blockiertes Guthaben.
    # v9.1: "trigger" und "move_order_stop" ergaenzt. Eine im OKX-Web von Hand
    # angelegte Trigger-Verkaufsorder war fuer hat_offene_order() unsichtbar --
    # der Bot haette trotz blockiertem Bestand neu eingestiegen.
    ALGO_ARTEN = ("oco", "conditional", "trigger", "move_order_stop")
    # Kennungen, die eine von NEXUS gesetzte Schutzorder tragen kann.
    # "PN9..." entsteht aus protection_client_id, "TBP..." aus dem Rueckfall
    # make_client_order_id("TBP9"), "N9..." aus aelteren Versionen.

    def offene_schutzorders(self, inst_id: str, *, strict: bool = False) -> list[dict]:
        """Alle Schutzorders eines Instruments, ueber alle Algo-Arten."""
        gefunden: list[dict] = []
        gesehen: set[str] = set()
        fehler: list[str] = []
        for art in self.ALGO_ARTEN:
            try:
                for zeile in self.client.pending_algo_orders(inst_id, ord_type=art):
                    kennung = str(zeile.get("algoId", ""))
                    if kennung and kennung not in gesehen:
                        gesehen.add(kennung)
                        gefunden.append(zeile)
            except BrokerFehler as exc:
                logger.warning("OKX-Algo-Abfrage %s (%s): %s", inst_id, art, exc)
                fehler.append(f"{art}: {exc}")
        if strict and fehler:
            raise BrokerFehler("Schutzorders nicht vollstaendig pruefbar: " + "; ".join(fehler))
        return gefunden

    def storniere_offene_orders(self, instrument, *,
                                protection_algo_id: str = "",
                                protection_client_id: str = "") -> int:
        """Nur den nachgewiesenen NEXUS-Schutz dieser Position entfernen.

        Rueckgabe ist die Anzahl. Ein Fehlschlag wird NICHT verschluckt: er
        landet in ``self.letzte_stornierung`` und wird vom Aufrufer gemeldet.
        Bleibt Guthaben eingefroren, wird der folgende Verkauf sonst
        stillschweigend zu einer Teilausfuehrung -- genau das SOL-Muster vom
        25.08.2026.
        """
        inst_id = self._inst_id(instrument)
        anzahl = 0
        fehler = []
        wanted_algo = str(protection_algo_id or "")
        wanted_client = str(protection_client_id or "")
        if not wanted_algo and not wanted_client:
            self.letzte_stornierung = {
                "inst_id": inst_id, "anzahl": 0, "fehler": [],
                "detail": "keine eigene Schutz-ID; Fremdorders unangetastet"}
            return 0
        generated_order_ids = (self.protection_exit_order_ids(wanted_algo)
                               if wanted_algo else set())

        def _erledigt(exc: BrokerFehler) -> bool:
            """Ist "Order weg" schon der gewuenschte Zustand?

            v9.1: 51400 "does not exist", 51401 "already canceled", 51402
            "already completed" und 51410 "already under cancelling" bedeuten
            fachlich: das Ziel ist erreicht. Bis 9.0.15 galten sie als Fehler
            und blockierten den Ausstieg genau dann, wenn die Schutzorder
            gerade ausgeloest war -- also im aktivsten Marktmoment.
            """
            return any(code in str(exc) for code in _STORNO_ERLEDIGT_CODES)

        try:
            for row in self.client.pending_orders(inst_id):
                if str(row.get("ordId") or "") not in generated_order_ids:
                    continue
                try:
                    self.client.cancel_order(inst_id, str(row.get("ordId", "")))
                    anzahl += 1
                except BrokerFehler as exc:
                    if _erledigt(exc):
                        anzahl += 1
                    else:
                        raise
        except BrokerFehler as exc:
            logger.warning("OKX-Stornierung %s: %s", inst_id, exc)
            fehler.append(str(exc))
        try:
            algos = []
            for row in self.offene_schutzorders(inst_id, strict=True):
                # An explicit algoId is authoritative; clientId cannot override
                # a different algo. Missing returned identity is not guessed.
                if wanted_algo:
                    if str(row.get("algoId") or "") != wanted_algo:
                        continue
                    if wanted_client and row.get("algoClOrdId") and str(row["algoClOrdId"]) != wanted_client:
                        raise BrokerFehler("Schutz-ID und Client-ID widersprechen sich")
                elif str(row.get("algoClOrdId") or "") != wanted_client:
                    continue
                actual_inst = str(row.get("instId") or "")
                if not actual_inst or base_currency(actual_inst) != base_currency(inst_id):
                    raise BrokerFehler("Schutzstorno ohne passenden Broker-Instrumentbeleg")
                algos.append(row)
            eintraege = [{"instId": str(r["instId"]), "algoId": str(r["algoId"])}
                         for r in algos if r.get("algoId")]
            if eintraege:
                try:
                    self.client.cancel_algo_orders(eintraege)
                except BrokerFehler as exc:
                    if not _erledigt(exc):
                        raise
                anzahl += len(eintraege)
        except BrokerFehler as exc:
            logger.warning("OKX-Algo-Stornierung %s: %s", inst_id, exc)
            fehler.append(str(exc))
        self.letzte_stornierung = {"inst_id": inst_id, "anzahl": anzahl,
                                   "fehler": fehler}
        if anzahl:
            # 9.5.8: Ab hier gibt OKX das gebundene Guthaben frei -- aber
            # verzoegert. Wer als naechstes lesen will, muss warten duerfen.
            self._freigabe_vormerken(inst_id)
        return anzahl

    def alle_schutzorders(self) -> list[dict]:
        """Alle beim Broker liegenden Schutzorders ueber ALLE Instrumente.

        Nicht zu verwechseln mit ``offene_schutzorders(inst_id)``: die fragt
        ein einzelnes Instrument ab. Hier geht es genau um die Orders, zu
        denen NEXUS kein Instrument im Buch hat -- die kann man nicht finden,
        indem man nach bekannten Instrumenten fragt.

        9.5.4. Am 02.09.2026 lag im Konto eine lebende OCO-Schutzorder
        (algoId 3873284120214450177, ETH-EUR, sz 1,043377, SL 1894,9,
        TP 2205,1 -- rund 2530 EUR), zu der NEXUS keine Position im Buch
        hatte. Es gab keinen einzigen Leser, der so etwas haette bemerken
        koennen: ``verwaiste_orders_aufraeumen`` sieht nur Schutzorders OHNE
        Guthaben an, und hier ist Guthaben da.

        Diese Methode liest nur. Sie storniert nichts und uebernimmt nichts.
        """
        rows: list[dict] = []
        for art in self.ALGO_ARTEN:
            try:
                gefunden = self.client.pending_algo_orders(ord_type=art)
            except BrokerFehler as exc:
                # Fail closed nach oben: eine nicht abrufbare Art darf nicht
                # als "keine Schutzorder" durchgehen.
                raise BrokerFehler(
                    f"Schutzorders der Art {art} nicht abrufbar: {exc}") from None
            for row in gefunden:
                inst_id = str(row.get("instId", ""))
                rows.append({
                    "algo_id": str(row.get("algoId", "")),
                    "algo_client_id": str(row.get("algoClOrdId", "")),
                    "instrument": inst_id,
                    "symbol": base_currency(inst_id),
                    "art": art,
                    "side": str(row.get("side", "")).upper(),
                    "menge": _f(row.get("sz")),
                    "stop": _f(row.get("slTriggerPx")),
                    "take_profit": _f(row.get("tpTriggerPx")),
                    "status": str(row.get("state", "")),
                })
        return rows

    def verwaiste_orders_aufraeumen(self) -> int:
        """An empty balance never authorizes cancellation of protective orders.

        Prefix/tag ownership and missing assets are insufficient evidence.
        Explicit position exits retain their exact-order cancellation path.
        """
        return 0

    def reconcile_position_protection(self, instrument, quantity: float,
                                      stop: float, take_profit: float, *,
                                      protection_client_id: str = "",
                                      protection_algo_id: str = "",
                                      trade_quote_ccy: str = "") -> dict:
        """Prueft nach Neustart/Reconnect, ob der Broker-Schutz noch liegt."""
        inst_id = self._inst_id(instrument)
        meta = self.client.instrument(inst_id)
        if meta is None:
            return {"checked": True, "changed": False, "protection_confirmed": False,
                    "detail": f"{inst_id} bei OKX unbekannt"}
        quote = self._protection_quote(meta, trade_quote_ccy)
        try:
            all_algos = self.offene_schutzorders(inst_id, strict=True)
            wrong_quote = [row for row in all_algos
                           if str(row.get("tradeQuoteCcy") or meta.quote_ccy).upper() != quote
                           and ((protection_algo_id and str(row.get("algoId") or "") == protection_algo_id)
                                or (protection_client_id and str(row.get("algoClOrdId") or "") == protection_client_id)
                                or str(row.get("algoClOrdId") or "").startswith(_NEXUS_ALGO_PRAEFIXE))]
            if wrong_quote:
                return {"checked": True, "changed": False, "protection_confirmed": False,
                        "algo_id": str(wrong_quote[0].get("algoId") or ""),
                        "algo_client_id": str(wrong_quote[0].get("algoClOrdId") or protection_client_id),
                        "detail": "Eigene Schutzorder mit abweichender Abrechnungswaehrung; keine zweite Order gesendet."}
            algos = [row for row in all_algos
                     if str(row.get("tradeQuoteCcy") or meta.quote_ccy).upper() == quote
                     and ((protection_algo_id and
                          str(row.get("algoId") or "") == str(protection_algo_id))
                         or (protection_client_id and
                             str(row.get("algoClOrdId") or "") ==
                             str(protection_client_id)))]
            generated_ids = (self.protection_exit_order_ids(protection_algo_id)
                             if protection_algo_id else set())
            standard_sells = [r for r in self.client.pending_orders(inst_id)
                              if str(r.get("side") or "").lower() == "sell"
                              and str(r.get("ordId") or "") in generated_ids]
        except BrokerFehler as exc:
            # Nicht abrufbar ist NICHT dasselbe wie bestaetigt fehlend. Die
            # WebUI darf bei einem Transportfehler keinen falschen
            # Schutzverlust behaupten und der Bot darf keine zweite OCO
            # blind platzieren.
            return {"checked": False, "changed": False, "protection_confirmed": False,
                    "detail": f"Schutzstatus nicht abfragbar: {exc}"}

        # Nach einem Algo-Trigger kann der Schutz als normale SELL-Order
        # weiterlaufen. Beides zaehlt zur Deckung und darf nicht dupliziert
        # werden.
        abgedeckt = (sum(_f(r.get("sz")) for r in algos)
                     + sum(_f(r.get("sz")) - _f(r.get("accFillSz"))
                           for r in standard_sells))
        soll = quantize_down(quantity, meta.lot_size)
        stop_px = quantize_price(stop, meta.tick_size) if stop and stop > 0 else 0.0
        take_px = quantize_price(take_profit, meta.tick_size) if take_profit and take_profit > 0 else 0.0
        uebernommen = ""

        # ===================================================================
        # SCHUTZABGLEICH 9.5.7 -- vollstaendig neu geordnet
        # ===================================================================
        #
        # Vorgeschichte in zwei Vorfaellen:
        #
        #   02.09.2026 (LINK)  Die algoId war verloren. Der strenge Filter fand
        #                      nichts, NEXUS sendete eine zweite Schutzorder,
        #                      OKX lehnte sie mit 51008 ab, weil die erste das
        #                      Guthaben hielt. Im Minutentakt, ueber Stunden.
        #
        #   03.09.2026 (HYPE)  9.5.6 uebernahm eine vorhandene Order nur bei
        #                      Uebereinstimmung von Menge UND SL/TP. Bei HYPE
        #                      lag SL 63,18 statt 63,27786405 -- die Order war
        #                      mit einem vorlaeufigen Einstieg 70,20 gesetzt
        #                      worden, bevor der echte VWAP feststand. Dieselbe
        #                      Schleife.
        #
        # Die Kennung entscheidet ueber das Eigentum, nicht der Preis. Eine
        # Order mit NEXUS-Praefix auf diesem Instrument ist unsere -- ob ihre
        # SL/TP noch aktuell sind, ist eine ANDERE Frage und wird getrennt
        # beantwortet.
        def _eigene_kennung(row) -> bool:
            return str(row.get("algoClOrdId") or "").startswith(_NEXUS_ALGO_PRAEFIXE)

        def _ist_verkauf(row) -> bool:
            seite = str(row.get("side") or "").lower()
            return seite in ("", "sell")

        if not algos and soll > 0:
            eigene = [row for row in all_algos
                      if _eigene_kennung(row) and _ist_verkauf(row)
                      and str(row.get("algoId") or "").strip()]
            if eigene:
                # ALLE eigenen Orders zaehlen zur Deckung, nicht nur eine mit
                # exakt passender Menge. Deckt eine Order zu wenig ab, wird der
                # Rest unten regulaer nachgeschuetzt -- frueher blieb er
                # dauerhaft ungeschuetzt.
                algos = eigene
                uebernommen = ",".join(str(r.get("algoId") or "") for r in eigene)
                logger.warning(
                    "%s: %d eigene Schutzorder(s) ueber die Kennung erkannt "
                    "(%s). Sie zaehlen zur Deckung; es wird keine zweite "
                    "Order fuer dieselbe Menge gesendet.",
                    inst_id, len(eigene), uebernommen)
            elif stop_px > 0 or take_px > 0:
                # Rueckfall fuer Schutzorders aus Versionen vor 9.0 ohne
                # NEXUS-Kennung: dort entscheidet weiterhin die exakte
                # Uebereinstimmung von Menge UND SL/TP.
                passend = [
                    row for row in all_algos
                    if _ist_verkauf(row) and str(row.get("algoId") or "").strip()
                    and str(row.get("tradeQuoteCcy") or meta.quote_ccy).upper() == quote
                    and abs(_f(row.get("sz")) - soll) <= max(_f(meta.lot_size),
                                                             soll * 0.001)
                    and (stop_px <= 0 or abs(_f(row.get("slTriggerPx")) - stop_px)
                         <= _f(meta.tick_size))
                    and (take_px <= 0 or abs(_f(row.get("tpTriggerPx")) - take_px)
                         <= _f(meta.tick_size))
                ]
                if len(passend) == 1:
                    algos = passend
                    uebernommen = str(passend[0].get("algoId") or "")
                    logger.warning(
                        "%s: Schutzorder %s ohne NEXUS-Kennung uebernommen -- "
                        "Menge und SL/TP stimmen exakt.", inst_id, uebernommen)

            if algos:
                # Beides zaehlt zur Deckung. Eine Doppelzaehlung ist nicht
                # moeglich: ``all_algos`` listet nur NOCH OFFENE Algo-Orders --
                # eine bereits ausgeloeste, die als normale Verkaufsorder
                # weiterlaeuft, steht dort nicht mehr. Ein frueherer Entwurf
                # hat ``standard_sells`` hier weggelassen und damit einen
                # ausgeloesten Schutz als fehlend gemeldet.
                abgedeckt = (sum(_f(r.get("sz")) for r in algos)
                             + sum(max(0.0, _f(r.get("sz")) - _f(r.get("accFillSz")))
                                   for r in standard_sells))

        # Ohne gesetzten Stop UND ohne Take-Profit ist "die Preise passen" eine
        # leere Aussage -- all() ueber eine leere Bedingung ist wahr. Ohne
        # diesen Zusatz haette ein Aufruf mit stop=0 und take=0 jede beliebige
        # Order als exakt passend bestaetigt.
        exact_prices = (bool(algos) and (stop_px > 0 or take_px > 0) and all(
            (stop_px <= 0 or abs(_f(r.get("slTriggerPx")) - stop_px) <= _f(meta.tick_size))
            and (take_px <= 0 or abs(_f(r.get("tpTriggerPx")) - take_px) <= _f(meta.tick_size))
            for r in algos))
        # Eine getriggerte Standardorder kann nicht mehr beide OCO-Preise
        # repraesentieren, ist aber nur dann eigene Deckung, wenn ihre ordId
        # aus exakt unserer algoId stammt.
        price_proven = exact_prices or bool(standard_sells and generated_ids)

        # Deckung gilt erst, wenn hoechstens ein Lot fehlt. Die frueheren
        # 0,1 Prozent haben bei 100.000 DOGE 100 Stueck als "voll geschuetzt"
        # durchgehen lassen.
        gedeckt = soll > 0 and abgedeckt >= soll - max(_f(meta.lot_size), soll * 1e-9)

        def _bestaetigt(detail: str, geaendert: bool = False) -> dict:
            aktiv = next(iter(algos), {})
            client_id = str(aktiv.get("algoClOrdId") or protection_client_id or "")
            if client_id:
                import okx_protection_journal as protection_journal
                protection_journal.update(account=self.account_fingerprint(),
                    environment="DEMO" if self.demo else "LIVE", instrument=meta.inst_id,
                    client_id=client_id, state="CONFIRMED", algo_id=str(aktiv.get("algoId") or ""))
            return {"checked": True, "changed": geaendert,
                    "protection_confirmed": True,
                    "algo_id": str(aktiv.get("algoId") or ""),
                    "algo_client_id": str(aktiv.get("algoClOrdId")
                                          or protection_client_id),
                    "uebernommen": bool(uebernommen),
                    "detail": detail}

        if gedeckt and price_proven:
            return _bestaetigt(f"Eigener Broker-Schutz mit exakter ID, Menge und "
                               f"SL/TP vorhanden ({abgedeckt:g} {meta.base_ccy})")

        if gedeckt and algos and (stop_px > 0 and take_px > 0):
            # Menge gedeckt, nur SL/TP weichen ab: AENDERN statt eine zweite
            # Order zu senden. amend_algo_order aendert in place -- es gibt
            # keine Luecke, in der die Position ungeschuetzt waere.
            #
            # Beide Preise muessen gesetzt sein: der Amend-Body schreibt SL und
            # TP gemeinsam, ein fehlender Wert wuerde als "0" gesendet.
            if len(algos) != 1:
                # Mehrere eigene Orders decken die Menge. Sie SCHUETZEN -- das
                # ist die sicherheitsrelevante Tatsache -- aber welche zu
                # aendern waere, ist nicht entscheidbar.
                logger.warning(
                    "%s: %d eigene Schutzorders decken die Menge, ihre SL/TP "
                    "weichen aber ab. Es wird nichts geaendert; bitte im "
                    "OKX-Konto pruefen.", inst_id, len(algos))
                return _bestaetigt(
                    f"{len(algos)} eigene Schutzorders decken {abgedeckt:g} "
                    f"{meta.base_ccy}, ihre SL/TP weichen aber von "
                    f"{stop_px:g}/{take_px:g} ab. Bitte im OKX-Konto pruefen.")
            aktive_id = str(algos[0].get("algoId") or "")
            if aktive_id:
                try:
                    self.amend_position_protection(
                        instrument, quantity, stop, take_profit,
                        algo_id=aktive_id, trade_quote_ccy=trade_quote_ccy)
                    return _bestaetigt(
                        f"Eigene Schutzorder {aktive_id} auf SL {stop_px:g} / "
                        f"TP {take_px:g} geaendert, statt eine zweite zu senden.",
                        geaendert=True)
                except OrderStatusUnklar as exc:
                    # Angenommen, aber nicht rueckgelesen. "Nicht abrufbar" ist
                    # NICHT dasselbe wie "bestaetigt fehlend" -- checked bleibt
                    # False, damit daraus kein falscher Schutzverlust wird.
                    logger.warning("%s: Aenderung der Schutzorder %s "
                                   "unbestaetigt: %s", inst_id, aktive_id, exc)
                    return {"checked": False, "changed": False,
                            "protection_confirmed": False,
                            "algo_id": aktive_id,
                            "algo_client_id": str(algos[0].get("algoClOrdId")
                                                  or protection_client_id),
                            "detail": (f"Aenderung der Schutzorder {aktive_id} "
                                       f"von OKX angenommen, aber nicht "
                                       f"bestaetigt: {exc}")}
                except BrokerFehler as exc:
                    # Die Order LIEGT und schuetzt -- nur mit anderen Werten.
                    # Sie als fehlend zu melden waere die groessere Unwahrheit,
                    # und der naechste Takt wuerde erneut aendern wollen.
                    logger.warning("%s: Schutzorder %s liess sich nicht auf "
                                   "SL %s / TP %s aendern: %s",
                                   inst_id, aktive_id, stop_px, take_px, exc)
                    return _bestaetigt(
                        f"Schutzorder {aktive_id} deckt {abgedeckt:g} "
                        f"{meta.base_ccy}, liess sich aber nicht auf SL "
                        f"{stop_px:g} / TP {take_px:g} aendern ({exc}). Der "
                        "bestehende Schutz bleibt aktiv.")

        if gedeckt and algos:
            # Gedeckt, Preise nicht beweisbar und nicht aenderbar (etwa weil nur
            # ein Stop gesetzt ist). Der Schutz liegt trotzdem.
            return _bestaetigt(f"Eigene Schutzorder deckt {abgedeckt:g} "
                               f"{meta.base_ccy}; SL/TP nicht abschliessend "
                               "geprueft.")

        fehlend = max(0.0, soll - abgedeckt)
        if fehlend <= 0:
            return {"checked": True, "changed": False, "protection_confirmed": False,
                    "detail": "Keine Menge zu schuetzen"}

        if algos and fehlend < _f(meta.min_size):
            # Der Rest liegt unter der OKX-Mindestmenge und laesst sich gar
            # nicht schuetzen. Das als "ungeschuetzt" zu melden waere falsch.
            return _bestaetigt(
                f"Eigene Schutzorder deckt {abgedeckt:g} von {soll:g} "
                f"{meta.base_ccy}; die verbleibenden {fehlend:g} liegen unter "
                f"der Mindestmenge {_f(meta.min_size):g}.")

        # -------------------------------------------------------------------
        # Riegel: keine Order senden, die am gebundenen Guthaben scheitern MUSS
        # -------------------------------------------------------------------
        # Am 03.09.2026 lagen bei HYPE 31,6721581 im Konto, davon 31,6721 von
        # der eigenen Schutzorder eingefroren -- frei waren 0,0000581. Jeder
        # Versuch war von vornherein aussichtslos und erzeugte nur eine Warnung
        # im Minutentakt.
        #
        # Der Riegel greift nur, wenn die liegenden Verkaufsorders die fehlende
        # Menge auch WIRKLICH erklaeren. Eine fremde Order ueber 5 Stueck
        # rechtfertigt nicht die Annahme, sie halte 100 fest -- dort soll OKX
        # weiterhin klar antworten, statt dass NEXUS still auf Schutz verzichtet.
        #
        # Fehlt die Basiswaehrung in der Guthabenantwort, heisst das "keine
        # Aussage", nicht "null frei". Ein fehlender Eintrag als 0 zu lesen
        # wuerde den Schutz direkt nach einem Kauf blockieren.
        #
        # 9.5.8: Hier stand bis 9.5.7 ein EINZIGER Balance-Read. Genau davor
        # warnt der Kommentar im Verkaufspfad seit dem LINK/ONDO-Vorfall: OKX
        # quittiert die Stornierung, bevor es das Guthaben freigibt. Wird der
        # Schutz unmittelbar nach einem stornierten FOK erneuert -- also genau
        # im Fehlerfall, in dem er am dringendsten gebraucht wird --, liest der
        # eine Read zwangslaeufig zu wenig. Beide Pfade benutzen jetzt dieselbe
        # Funktion; gewartet wird nur, wenn fuer dieses Instrument gerade eine
        # Freigabe aussteht, sonst kostet es weiterhin einen Read.
        frei, _gedeckt = self.warte_auf_guthabenfreigabe(
            meta.base_ccy, fehlend, inst_id=inst_id,
            lot_size=_f(meta.lot_size))

        if frei is not None and fehlend > frei + _f(meta.lot_size):
            gebunden = sum(_f(r.get("sz")) for r in all_algos if _ist_verkauf(r))
            try:
                gebunden += sum(
                    max(0.0, _f(r.get("sz")) - _f(r.get("accFillSz")))
                    for r in self.client.pending_orders(inst_id)
                    if str(r.get("side") or "").lower() == "sell")
            except BrokerFehler:
                logger.debug("%s: offene Standardorders fuer den Riegel nicht "
                             "lesbar", inst_id)
            if gebunden >= fehlend:
                hinweis = (f"Fuer die Schutzorder fehlen {fehlend:g} "
                           f"{meta.base_ccy}, frei verfuegbar sind nur "
                           f"{frei:g}. Liegende Verkaufsorders binden "
                           f"{gebunden:g} -- eine weitere Order wuerde "
                           "zwangslaeufig abgelehnt und wird nicht gesendet.")
                logger.warning("%s: %s", inst_id, hinweis)
                return {"checked": True, "changed": False,
                        "protection_confirmed": False,
                        "algo_id": str(next(iter(algos), {}).get("algoId") or ""),
                        "algo_client_id": str(
                            next(iter(algos), {}).get("algoClOrdId")
                            or protection_client_id),
                        "detail": hinweis}

        schutz = self._setze_schutz(
            meta, fehlend, stop, take_profit,
            protection_client_id=protection_client_id,
            trade_quote_ccy=trade_quote_ccy)
        bestaetigt = bool(schutz.get("protection_confirmed", False))
        return {"checked": bool(schutz.get("checked", True)),
                "changed": bestaetigt, "protection_confirmed": bestaetigt,
                "algo_id": str(schutz.get("algo_id") or ""),
                "algo_client_id": str(schutz.get("algo_client_id") or protection_client_id),
                "detail": schutz.get("hinweis") or f"Schutz fuer {fehlend:g} {meta.base_ccy} nachgezogen"}

    def amend_position_protection(self, instrument, quantity: float,
                                  stop: float, take_profit: float, *,
                                  algo_id: str, trade_quote_ccy: str = "") -> dict:
        """Aendert genau eine belegte OKX-OCO und verifiziert das Ergebnis."""
        inst_id = self._inst_id(instrument)
        meta = self.client.instrument(inst_id)
        if meta is None:
            raise BrokerFehler(f"{inst_id} ist bei OKX unbekannt.")
        expected_qty = quantize_down(quantity, meta.lot_size)
        active = [row for row in self.offene_schutzorders(inst_id, strict=True)
                  if str(row.get("algoId") or "") == str(algo_id or "")]
        if len(active) != 1:
            raise BrokerFehler("Die zu aendernde Schutzorder ist nicht exakt einmal aktiv.")
        row = active[0]
        covered = quantize_down(_f(row.get("sz")), meta.lot_size)
        if expected_qty <= 0 or abs(covered - expected_qty) > max(_f(meta.lot_size), expected_qty * 0.001):
            raise BrokerFehler("Schutzordermenge stimmt nicht mit der Botposition ueberein.")
        stop_px = quantize_price(stop, meta.tick_size)
        take_px = quantize_price(take_profit, meta.tick_size)
        # KORREKTUR 9.5.7: Hier stand OKX_MAX_EXIT_SLIPPAGE_PCT, waehrend
        # _setze_schutz OKX_STOP_LIMIT_SLIPPAGE_PCT benutzt. Dieselbe Order
        # bekam also je nach Weg -- neu angelegt oder geaendert -- einen
        # anderen Limitabschlag. Solange amend nur aus der WebUI kam, fiel das
        # nicht auf; seit 9.5.7 aendert auch der automatische Abgleich.
        stop_limit_pct = max(0.0, float(getattr(
            __import__("config"), "OKX_STOP_LIMIT_SLIPPAGE_PCT", 0.01)))
        stop_order_px = quantize_down(stop_px * (1.0 - stop_limit_pct), meta.tick_size)
        # Und derselbe Riegel wie beim Anlegen: bei Cent- und Sub-Cent-Coins
        # faellt der abgeschlagene Limitpreis unter einen Tick und wird auf 0
        # abgerundet. OKX lehnt dann die GESAMTE OCO ab.
        if stop_px > 0 and stop_order_px <= 0:
            stop_order_px = quantize_down(stop_px, meta.tick_size)
        if stop_px > 0 and stop_order_px <= 0:
            raise BrokerFehler(
                "Stop-Limitpreis liegt unter einem Tick; die Schutzorder "
                "laesst sich nicht aendern. Der bestehende Schutz bleibt.")
        body = {
            "instId": inst_id, "algoId": str(algo_id),
            "newSlTriggerPx": format_decimal(stop_px, meta.tick_size),
            "newSlOrdPx": format_decimal(stop_order_px, meta.tick_size),
            "newSlTriggerPxType": "last",
            "newTpTriggerPx": format_decimal(take_px, meta.tick_size),
            "newTpOrdPx": format_decimal(take_px, meta.tick_size),
            "newTpTriggerPxType": "last",
        }
        self.client.amend_algo_order(body)
        # Erfolg des POST ist noch kein Beweis. Die aktive Brokerorder muss
        # die neuen Werte bei einer frischen Abfrage tatsaechlich zeigen.
        for _ in range(8):
            time.sleep(0.4)
            matches = [item for item in self.offene_schutzorders(inst_id, strict=True)
                       if str(item.get("algoId") or "") == str(algo_id)]
            if len(matches) == 1:
                current = matches[0]
                if (abs(_f(current.get("slTriggerPx")) - stop_px) <= _f(meta.tick_size)
                        and abs(_f(current.get("tpTriggerPx")) - take_px) <= _f(meta.tick_size)):
                    return {"checked": True, "protection_confirmed": True,
                            "algo_id": str(algo_id),
                            "algo_client_id": str(current.get("algoClOrdId") or ""),
                            "stop": stop_px, "take_profit": take_px,
                            "detail": "Manueller TP/SL bei OKX aktiv und rueckgelesen."}
        raise OrderStatusUnklar(
            "OKX nahm die TP/SL-Aenderung an, der neue Zustand konnte aber nicht bestaetigt werden.",
            reference_id=str(algo_id))

    # -- Faehigkeiten -------------------------------------------------------
    def unterstuetzt_krypto_stop(self) -> bool:
        """OKX kann echte Broker-Stops (Algo-Orders) -- eToro konnte das nicht."""
        return True

    def unterstuetzt_bruchstuecke(self, asset_type: str = "crypto") -> bool:
        return True

    def beschreibung(self) -> str:
        return (f"OKX EEA Spot ({'DEMO' if self.demo else 'LIVE'}, "
                f"Quotes {', '.join(self.allowed_quotes)})")


__all__ = [
    "OKXBroker", "OKXClient", "OKXInstrument", "OKXTicker", "RateLimiter",
    "normalize_inst_id", "base_currency", "quantize_down", "quantize_up", "quantize_price",
    "format_decimal", "make_client_order_id", "client_order_id_for_decision",
    "okx_timestamp", "okx_fill_identity",
    "BAR_MAP", "BAR_SECONDS", "OKX_BASE_URL",
]
