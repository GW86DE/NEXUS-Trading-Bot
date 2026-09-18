"""Gemeinsame Datentypen und Schnittstelle fuer eToro und OKX.

Die neutrale Schnittstelle bleibt bewusst erhalten, damit Handelslogik und
Fake-Broker-Tests nicht an HTTP-Details gekoppelt sind. Im Produktrelease gibt
es genau zwei reale Adapter: eToro fuer Aktien/ETFs und OKX fuer Krypto-Spot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# Fehlerarten
# ---------------------------------------------------------------------------
class BrokerFehler(Exception):
    """Oberklasse fuer alle Fehler der Broker-Schicht."""


class AuftragAbgelehnt(BrokerFehler):
    """Explizite Brokerablehnung ohne Annahme; kein Transportfehler."""


class NichtVerbunden(BrokerFehler):
    """Aktion wurde ohne bestehende Verbindung versucht."""


class VerbindungVerloren(NichtVerbunden):
    """Temporärer Transport-/Netzwerkfehler; automatische Wiederverbindung sinnvoll."""


class Zeitabweichung(VerbindungVerloren):
    """OKX-Zeitfenster verletzt; NTP/Serverzeit pruefen und spaeter erneut verbinden."""


class OrderStatusUnklar(BrokerFehler):
    """Order kann beim Broker angekommen sein; niemals blind erneut senden.

    Dieser Zustand ist absichtlich KEIN Verbindungsverlust. Der Transport
    kann bereits wieder gesund sein, waehrend der Broker die angenommene
    Order noch asynchron verarbeitet. Ein globaler Reconnect wuerde dann die
    eigentliche Beweiskette verdecken und falsche Meldungen erzeugen.
    """

    def __init__(self, message: str, *, reference_id: str = "", order_ids=None,
                 accepted: bool = False, profile: str = "", intent: dict | None = None):
        super().__init__(message)
        self.reference_id = str(reference_id or "")
        self.order_ids = [str(x) for x in (order_ids or []) if str(x)]
        self.accepted = bool(accepted)
        self.profile = str(profile or "")
        self.intent = dict(intent or {})


class AuthentifizierungsFehler(BrokerFehler):
    """Zugangsdaten/Berechtigung sind ungueltig; endloses Reconnect waere sinnlos."""


class NichtUnterstuetzt(BrokerFehler):
    """
    Dieser Broker kann die angeforderte Funktion nicht.
    Bewusst ein eigener Fehlertyp: der Bot soll darauf reagieren koennen
    (z.B. auf den Client-Stop ausweichen), statt blind zu scheitern.
    """


# ---------------------------------------------------------------------------
# Gemeinsame Datentypen -- brokerunabhaengig
# ---------------------------------------------------------------------------
@dataclass
class Position:
    """Eine offene Position, brokerneutral beschrieben."""
    symbol: str
    quantity: float
    avg_cost: float
    currency: str = "USD"
    asset_type: str = "stock"
    broker_id: str = ""          # eToro instrumentID / positionID
    market_price: float = 0.0
    market_value: float = 0.0
    unrealized_pnl: Optional[float] = 0.0
    broker_stop: Optional[float] = None
    broker_take_profit: Optional[float] = None
    # v9.3: Die EINZELNEN positionIds, nicht nur der Anzeige-String in
    # broker_id. Nur hierueber darf Eigentum entstehen -- eToro liefert fuer
    # dasselbe Instrument mehrere Position-Lines, rundet Mengen und
    # propagiert zeitversetzt.
    position_ids: tuple = ()
    # v9.4: Brokeridentitaet wird am Adapterrand normalisiert. eToro kann
    # dieselbe Aktie in mehreren Position-Lines fuehren; deshalb gehoeren
    # instrumentId, Snapshot und Konto zu jeder einzelnen Zeile.
    instrument_id: str = ""
    snapshot_id: str = ""
    account_fingerprint: str = ""
    broker_environment: str = ""
    # Woher der Kurs stammt und wie alt er ist. Ein fehlender Kurs ist
    # ``None`` und darf niemals als 0,00 gebucht werden.
    price_source: str = ""
    price_age_seconds: Optional[float] = None


@dataclass
class Fill:
    """Eine tatsaechliche Ausfuehrung."""
    fill_id: str
    order_id: str
    symbol: str
    side: str                    # "BUY" oder "SELL"
    quantity: float
    price: float
    currency: str = "USD"
    asset_type: str = "stock"
    broker_id: str = ""
    timestamp: Optional[str] = None
    # Falls ein Adapter kumulative Fill-Mengen liefert, kann der Tracker damit
    # nur die Mengendifferenz verbuchen. eToro liefert normalerweise Executions.
    quantity_is_cumulative: bool = False
    # Tatsaechliche explizite Broker-/Steuergebuehren dieses Fills, sofern der
    # Broker sie liefert (eToro liefert z.B. Fees in den Execution-Daten).
    explicit_fees: Optional[float] = None
    # Optionaler, vom Broker gelieferter Schliessungsgrund (z. B. Stop-Loss).
    # Freitext wird nur zur Attribution genutzt, niemals als Handelssignal.
    execution_reason: str = ""
    # Die normalisierte Fill-ID ist kontoweit eindeutig; die rohe Broker-ID
    # bleibt fuer Diagnose und Broker-Lookup getrennt erhalten.
    raw_fill_id: str = ""
    account_fingerprint: str = ""
    # Unveraenderte, positionsbezogene Brokerbelege fuer nachgelagerte,
    # idempotente Accounting-/Reconciliation-Schritte. Das Feld ist Teil des
    # Datentyps, damit ``dataclasses.replace`` bei kumulativen Fills die
    # Beweisdaten nicht verliert.
    broker_detail: dict = field(default_factory=dict)


@dataclass
class OrderErgebnis:
    """
    Ergebnis einer Orderplatzierung.

    'filled_quantity' ist bewusst Teil des Ergebnisses: bei Market-Orders
    mit sofortiger Ausfuehrung (v.a. Krypto) muss der Bot wissen, wie viel
    TATSAECHLICH gekauft wurde -- nicht, wie viel geplant war. Genau daran
    ist die frueheste Krypto-Umsetzung gescheitert.
    """
    order_ids: list = field(default_factory=list)
    status: str = "unbekannt"
    filled_quantity: float = 0.0
    avg_fill_price: float = 0.0
    stop_order_platziert: bool = False
    take_order_platziert: bool = False
    hinweis: str = ""
    reference_id: str = ""
    position_ids: list = field(default_factory=list)
    paper: bool | None = None
    requested_quantity: float = 0.0
    remaining_quantity: float = 0.0
    terminal: bool = False
    raw_status: str = ""
    trade_quote_ccy: str = ""
    client_order_id: str = ""
    order_tag: str = ""
    gross_filled_quantity: float = 0.0
    fills: list = field(default_factory=list)
    fill_ids: list = field(default_factory=list)
    fill_evidence_complete: bool = False
    fees: dict = field(default_factory=dict)
    fees_quote: Optional[float] = None
    protection_algo_id: str = ""
    protection_client_order_id: str = ""
    execution_quote: dict = field(default_factory=dict)
    # v9.3: Strukturierte Brokerbelege statt Freitext. Beim WLD-Beispiel war
    # die Entscheidung korrekt freigegeben, die FOK-Order wurde aber mit
    # 0/270,94 storniert -- angezeigt wurde trotzdem "APPROVED", was wie ein
    # erfolgreicher Kauf aussieht. Ohne diese Felder laesst sich der Grund
    # nachtraeglich nicht mehr belegen.
    execution_evidence: dict = field(default_factory=dict)
    # Konto und Umgebung gehoeren zur Brokeridentitaet des Ergebnisses. Ohne
    # sie kann dieselbe rohe Fill-ID nach einem Demo-/Live- oder Kontowechsel
    # nicht sicher dedupliziert werden.
    account_fingerprint: str = ""
    broker_environment: str = ""

    @property
    def execution_status(self) -> str:
        """Was der BROKER getan hat -- getrennt von der Entscheidung."""
        gefuellt = float(self.gross_filled_quantity or self.filled_quantity or 0.0)
        angefordert = float(self.requested_quantity or 0.0)
        zustand = str(self.raw_status or self.status or "").lower()
        if gefuellt > 0 and (angefordert <= 0 or gefuellt >= angefordert * (1 - 1e-9)):
            return "FILLED"
        if gefuellt > 0:
            return "PARTIAL"
        if not self.terminal:
            return "UNKNOWN"
        if zustand in {"canceled", "cancelled", "mmp_canceled"}:
            return "CANCELED_NO_FILL"
        if zustand == "rejected":
            return "REJECTED"
        if zustand == "expired":
            return "EXPIRED"
        return "UNKNOWN"

    @property
    def final_status(self) -> str:
        """Was am Ende herauskam. Nur FILLED/PARTIAL sind ein Kauf."""
        return ("EXECUTED" if self.execution_status in {"FILLED", "PARTIAL"}
                else ("NOT_EXECUTED" if self.terminal else "UNKLAR"))


# ---------------------------------------------------------------------------
# Schnittstelle
# ---------------------------------------------------------------------------
class BrokerBase:
    """
    Neutrale Broker-Schnittstelle.

    Jede Umsetzung muss die mit 'NotImplementedError' markierten Methoden
    bereitstellen. Die uebrigen haben sinnvolle Standardwerte.
    """

    name: str = "basis"

    # -- Verbindung ---------------------------------------------------------
    def connect(self) -> bool:
        raise NotImplementedError

    def disconnect(self) -> None:
        raise NotImplementedError

    def is_connected(self) -> bool:
        raise NotImplementedError

    def health_check(self, force: bool = False) -> bool:
        """Leichter Erreichbarkeitstest. Adapter duerfen intern cachen."""
        return self.is_connected()

    def last_contact(self) -> str | None:
        """UTC-Zeitstempel des letzten erfolgreichen Brokerkontakts, soweit bekannt."""
        return None

    # -- Konto --------------------------------------------------------------
    def kontowert(self) -> float:
        """Nettokontowert in der Kontowaehrung."""
        raise NotImplementedError

    def kontowaehrung(self) -> str:
        """Tatsaechliche Basiswaehrung des Kontos, z.B. 'EUR' oder 'USD'."""
        return "USD"

    def ist_paper(self) -> bool:
        """Laeuft dieses Konto mit Spielgeld?"""
        raise NotImplementedError

    # -- Instrumente --------------------------------------------------------
    def instrument_handelbar(self, instrument) -> tuple[bool, str]:
        """
        Kann dieser Broker das Instrument handeln?
        Gibt (True, "") oder (False, Begruendung) zurueck.
        """
        return True, ""

    def qualifiziere(self, instrumente: list) -> tuple[list, list]:
        """
        Prueft eine Liste von Instrumenten gegen das Angebot des Brokers.
        Gibt (nutzbar, [(name, grund), ...]) zurueck.
        """
        raise NotImplementedError

    # -- Marktdaten ---------------------------------------------------------
    def historie(self, instrument, dauer: str, kerzengroesse: str,
                 nur_handelszeiten: bool = True) -> pd.DataFrame:
        """
        Historische Kerzen als DataFrame mit den Spalten
        open, high, low, close, volume und Zeitindex.
        """
        raise NotImplementedError

    def latest_bid_ask(self, instrument):
        """Optional: aktueller Best Bid/Ask als dict oder None.

        Wird nur direkt vor einem moeglichen Einstieg abgefragt. Broker ohne
        Implementierung duerfen None liefern; die Cost Engine verwendet dann
        eine konservative Fallback-Spanne.
        """
        return None

    # -- Positionen und Ausfuehrungen --------------------------------------
    def positionen(self) -> list:
        raise NotImplementedError

    def fills(self) -> list:
        """Ausfuehrungen der laufenden Sitzung bzw. des Tages."""
        raise NotImplementedError

    # -- Orders -------------------------------------------------------------
    def kaufe_mit_absicherung(self, instrument, menge: float,
                              referenzpreis: float, stop: float,
                              take_profit: float) -> OrderErgebnis:
        """
        Kauf mit zugehoerigem Stop-Loss und Gewinnziel.

        Ob Stop und Ziel wirklich beim Broker liegen, meldet das Ergebnis
        ueber stop_order_platziert / take_order_platziert zurueck. Der Bot
        weiss dadurch, ob er zusaetzlich selbst absichern muss.
        """
        raise NotImplementedError

    def schliesse_position(self, instrument, menge: float,
                           referenzpreis: float = 0.0) -> OrderErgebnis:
        raise NotImplementedError

    def offene_orders(self) -> list:
        """Brokerneutral beschriebene offene Orders, soweit verfügbar."""
        return []

    def storniere_offene_orders(self, instrument) -> int:
        """Storniert offene Orders zu diesem Instrument, gibt Anzahl zurueck."""
        raise NotImplementedError

    def hat_offene_order(self, instrument, seite: str = "BUY") -> bool:
        """True, wenn fuer das Instrument bereits eine offene Order dieser Seite existiert.

        Dient als Duplicate-Order-Schutz nach Reconnects und zwischen Scanner-
        Zyklen. Broker ohne Implementierung liefern konservativ False; die
        eToro ueberschreibt diese Methode.
        """
        return False

    def verwaiste_orders_aufraeumen(self) -> int:
        """
        Entfernt Schutzorders zu Symbolen ohne Position.

        Sicherheits-Hook fuer verwaiste Schutzorders. Standard ist 0.
        """
        return 0

    def reconcile_position_protection(self, instrument, quantity: float,
                                      stop: float, take_profit: float) -> dict:
        """
        Nach Reconnect fehlende Broker-Schutzorders wiederherstellen.

        Standard: nichts zu tun. eToro liefert einen expliziten
        ``protection_confirmed``-Status fuer die sichere Uebernahme.
        """
        return {"checked": False, "changed": False, "protection_confirmed": False, "detail": "nicht erforderlich"}

    def verfuegbares_cash(self) -> float | None:
        """Optional: frei verfuegbares Cash. None = Broker liefert es hier nicht."""
        return None

    def dynamic_cost_quote(self, instrument, quantity: float, price: float,
                           *, action: str = "open") -> dict | None:
        """Optionaler konto-/instrumentbezogener Kosten-What-if vom Broker."""
        return None

    def on_fill_housekeeping(self, fill: Fill) -> dict | None:
        """Optionaler sofortiger Broker-Housekeeping-Schritt nach einem Fill."""
        return None


    # -- Faehigkeiten -------------------------------------------------------
    def unterstuetzt_krypto_stop(self) -> bool:
        """
        Kann der Broker einen echten Stop-Loss fuer Krypto beim Broker
        hinterlegen? Wenn nein, muss der Bot selbst pruefen (client-seitig,
        wirkt nur bei laufendem Programm).
        """
        return False

    def unterstuetzt_bruchstuecke(self, asset_type: str = "stock") -> bool:
        """Sind Bruchteile handelbar (z.B. 0,25 Aktien)?"""
        return asset_type == "crypto"

    def warte(self, sekunden: float) -> None:
        """
        Pause, die den internen Ereignisfluss des Brokers am Leben haelt.
        Der Adapter kann dafuer eine brokergeeignete Wartefunktion nutzen; standardmaessig reicht
        time.sleep().
        """
        import time
        time.sleep(sekunden)

    # -- Beschreibung -------------------------------------------------------
    def beschreibung(self) -> str:
        modus = "PAPER" if self.ist_paper() else "LIVE"
        return f"{self.name} ({modus})"
