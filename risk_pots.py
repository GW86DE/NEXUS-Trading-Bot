"""Getrennte Risikotoepfe je Broker (v8.1.1 NEXUS).

WARUM
=====
eToro und OKX sind zwei getrennte Konten mit getrenntem Geld. Ein einziger
gemeinsamer Risikozustand waere in beide Richtungen falsch:

    - Ein schlechter Krypto-Tag wuerde den Aktienhandel bremsen, obwohl auf
      dem eToro-Konto gar nichts passiert ist.
    - Eine Positionsgroesse von "4 % des Depots" waere sinnlos, weil "das
      Depot" je nach Broker etwas voellig anderes bedeutet.

Deshalb bekommt jeder Broker einen eigenen Topf:

    eigener Kontowert          -> eigene Positionsgroessen
    eigener Tagesverlust       -> eigene Tagesbremse
    eigene Positionsobergrenze -> eigene Streuung
    eigene Zustandsdatei       -> ueberlebt Neustarts getrennt

Zusaetzlich gibt es eine optionale GLOBALE Klammer (globale Tagesverlust-
grenze ueber beide Konten). Sie ist standardmaessig aus und wird nur aktiv,
wenn sie in der Konfiguration eingeschaltet wird.

WAS DIESES MODUL NICHT TUT
==========================
Es entscheidet nicht, OB ein Kandidat gut ist. Das bleibt Aufgabe von
bewerte_kandidat() im Candidate Gate. Hier geht es ausschliesslich um
Groesse, Anzahl und Tagesbremsen.
"""

from __future__ import annotations

import logging
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import config
from risk_manager import RiskState, _step_round_down, offene_ergebnisse_heute

logger = logging.getLogger(__name__)


def _zustandswurzel() -> Path:
    """Wo liegen die Zustandsdateien?

    RiskState.default_path() gibt einen uebergebenen Namen unveraendert
    zurueck -- daraus wuerde ein RELATIVER Pfad im aktuellen Arbeits-
    verzeichnis. Beim Start ueber einen systemd-Dienst waere das ein
    voellig anderer Ordner als das Botverzeichnis, und die Tagesbremse
    wuerde nach jedem Neustart bei null anfangen.
    """
    test_dir = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return Path(test_dir) if test_dir else Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Grenzwerte eines Topfes
# ---------------------------------------------------------------------------
@dataclass
class TopfGrenzen:
    """Alle Grenzwerte eines einzelnen Risikotopfes."""
    risiko_pro_trade_pct: float = 0.005
    max_position_pct: float = 0.04
    max_offene_positionen: int = 8
    max_tagesverlust_pct: float = 0.02
    max_trades_pro_tag: int = 20
    mengen_schritt: float = 0.0
    min_positionswert: float = 0.0

    @classmethod
    def fuer_broker(cls, broker: str, cfg=None) -> "TopfGrenzen":
        """Liest die Grenzwerte des Topfes aus der Konfiguration.

        Namensschema: <BROKER>_MAX_POSITION_PCT usw. Fehlt ein Wert, gilt
        der globale Standard aus v6. So bleibt eine alte Konfiguration
        gueltig und der Nutzer muss nichts neu eintragen.
        """
        cfg = cfg or config
        prefix = str(broker or "").strip().upper()

        def hole(name: str, standard):
            spezifisch = getattr(cfg, f"{prefix}_{name}", None)
            if spezifisch is not None:
                return spezifisch
            return getattr(cfg, name, standard)

        return cls(
            risiko_pro_trade_pct=float(hole("RISK_PER_TRADE_PCT", 0.005)),
            max_position_pct=float(hole("MAX_POSITION_PCT", 0.04)),
            max_offene_positionen=int(hole("MAX_OPEN_POSITIONS", 8)),
            max_tagesverlust_pct=float(hole("MAX_DAILY_LOSS_PCT", 0.02)),
            max_trades_pro_tag=int(hole("MAX_TRADES_PER_DAY", 20)),
            mengen_schritt=float(getattr(cfg, f"{prefix}_QUANTITY_STEP", 0.0) or 0.0),
            min_positionswert=float(getattr(cfg, f"{prefix}_MIN_POSITION_VALUE", 0.0) or 0.0),
        )


# ---------------------------------------------------------------------------
# Ein Topf
# ---------------------------------------------------------------------------
class RiskPot:
    """Risikozustand und Grenzen genau eines Brokers."""

    def __init__(self, broker: str, *, grenzen: Optional[TopfGrenzen] = None,
                 state_datei: Optional[str] = None):
        self.broker = str(broker).strip().lower()
        self.grenzen = grenzen or TopfGrenzen.fuer_broker(self.broker)
        # 10.6.0: Ausdruecklich uebergebene Grenzen sind eine Ansage und
        # bleiben unangetastet. Nur ein Topf, der seine Werte selbst aus der
        # Konfiguration geholt hat, folgt der Einsatzstufe aus der WebUI.
        self._grenzen_explizit = grenzen is not None
        # Den Dateinamen bildet niemand mehr selbst -- genau das hat die
        # Aktienseite und den eToro-Topf auf zwei Dateien auseinanderlaufen
        # lassen (siehe risk_state_pfad.py).
        if state_datei:
            self.state_datei = Path(state_datei)
            if not self.state_datei.is_absolute():
                self.state_datei = _zustandswurzel() / self.state_datei
        else:
            from risk_state_pfad import zustandsdatei
            self.state_datei = zustandsdatei(self.broker)
        self.state: RiskState = RiskState.load(self.state_datei)
        self.kontowert: float = 0.0
        self._lock = threading.RLock()
        self._persistence_error = self.state.persistence_failure_reason()

    # -- Zustand ------------------------------------------------------------
    def neuer_tag_pruefen(self) -> None:
        with self._lock:
            self.state.reset_if_new_day(persist=True)

    def setze_kontowert(self, wert: float, *, basis_key: str = "",
                       account_scope: str = "", native_wert=None,
                       native_ccy: str = "", native_beitraege=None) -> None:
        """Kontowert dieses Brokers uebernehmen und Equity-Bremse pruefen."""
        with self._lock:
            self.kontowert = max(0.0, float(wert or 0.0))
            try:
                self.state.update_equity_guard(self.kontowert, basis_key=basis_key,
                                               account_scope=account_scope,
                                               native_equity=native_wert,
                                               native_ccy=native_ccy,
                                               native_beitraege=native_beitraege)
                if self.kontowert > 0:
                    self._persistence_error = self.state.persistence_failure_reason()
            except Exception:
                # Ohne persistierbaren Risikostand bleiben neue Kaeufe zu.
                # Broker-Exits werden ueber ihre Fill-Journale weiter
                # rekonstruiert und spaeter nur lokal nachgebucht.
                # Ein zweiter blind geschriebener Halt-Status kann einen
                # unbestaetigten Write nicht nachtraeglich bestaetigen.
                self._persistence_error = "RISK_PERSISTENCE_UNCONFIRMED"
                logger.error("Equity-Guard %s nicht persistierbar; Kaeufe gesperrt",
                             self.broker, exc_info=True)

    def speichern(self) -> None:
        with self._lock:
            # Geldmutationen speichern sich transaktional selbst. Ein blinder
            # Shutdown-Save eines alten Objekts duerfte sonst neuere Broker-
            # Fills eines anderen Prozesses ueberschreiben.
            self.state.refresh()

    # -- Bremsen ------------------------------------------------------------
    def tagesverlust_erreicht(self) -> bool:
        with self._lock:
            if self.kontowert <= 0:
                return False
            grenze = self.kontowert * float(self.grenzen.max_tagesverlust_pct)
            return float(self.state.realized_pnl_today) <= -abs(grenze)

    def darf_kaufen(self) -> tuple[bool, str]:
        if getattr(self, "_snapshot_error", ""):
            return False, self._snapshot_error
        """Darf in diesem Topf ueberhaupt neu gekauft werden?"""
        with self._lock:
            failure = self._persistence_error or self.state.persistence_failure_reason()
            if failure:
                return False, (f"{self.broker}: {failure}; Risikozustand nicht bestaetigt. "
                               "Neue Kaeufe pausieren; Stops und Verkaeufe bleiben aktiv")
            try:
                self.state.refresh()
            except Exception:
                self._persistence_error = "RISK_STATE_UNREADABLE"
                return False, f"{self.broker}: RISK_STATE_UNREADABLE; neue Kaeufe pausieren"
            if self.state.persistence_failure_reason():
                return False, f"{self.broker}: {self.state.persistence_failure_reason()}; neue Kaeufe pausieren"
            if self.state.equity_basis_review_required:
                return False, f"{self.broker}: {self.state.equity_basis_review_reason}"
            # 10.7.0 (Entscheidung Georg, 18.09.2026): Ein unbekanntes
            # Verkaufsergebnis sperrt nur am Handelstag des Verkaufs. Bis
            # 10.6.0 sperrte hier auch ``lifetime_unknown_pnl_trades`` -- also
            # jeder jemals offene Beleg, unbegrenzt. Die Buchhaltung fuehrt
            # ihn weiter; die Sperre ist an ihren Schutzzweck gebunden.
            offen_heute = offene_ergebnisse_heute(self.state)
            if offen_heute:
                return False, (f'{self.broker}: Ergebnisabgleich ausstehend ({offen_heute} Verkauf/Verkaeufe '
                               'von heute ohne bezifferten Beleg); neue Kaeufe pausieren bis zum '
                               'Beleg oder Tageswechsel, Schutz und Ausstiege laufen weiter')
            if self.state.trading_halted:
                return False, f"{self.broker}: Handel fuer heute gestoppt"
            if self.state.equity_guard_halted:
                return False, (f"{self.broker}: Equity-Bremse aktiv "
                               f"({self.state.equity_drawdown_pct * 100:.1f} % Rueckgang)")
            if self.state.cooldown_active():
                return False, f"{self.broker}: Abkuehlphase nach Verlustserie"
            if self.state.trades_today >= int(self.grenzen.max_trades_pro_tag):
                return False, (f"{self.broker}: Tageslimit von "
                               f"{self.grenzen.max_trades_pro_tag} Trades erreicht")
            if self.state.open_positions >= int(self.grenzen.max_offene_positionen):
                return False, (f"{self.broker}: maximal "
                               f"{self.grenzen.max_offene_positionen} offene Positionen")
        if self.tagesverlust_erreicht():
            return False, f"{self.broker}: Tagesverlustgrenze erreicht"
        return True, ""

    # -- Einsatzstufe --------------------------------------------------------
    def einsatz(self) -> dict:
        """Die JETZT wirksamen Einsatzwerte dieses Topfes.

        10.6.0: Die WebUI kann je Broker eine von drei Einsatzstufen setzen.
        Sie wird hier bei jedem Aufruf frisch gelesen, damit ein Wechsel ohne
        Neustart des Trading-Core wirkt. Ohne gewaehlte Stufe kommen
        unveraendert die Werte aus Konfiguration und Profil zurueck.

        Anzeige (``uebersicht``) und Kaufpfad (``positionsgroesse``) rufen
        beide diese Methode. Eine Oberflaeche, die etwas anderes anzeigt als
        gerechnet wird, waere schlimmer als gar keine Anzeige.
        """
        basis_risiko = float(self.grenzen.risiko_pro_trade_pct)
        basis_max = float(self.grenzen.max_position_pct)
        if self._grenzen_explizit:
            return {"risiko_pro_trade_pct": basis_risiko,
                    "max_position_pct": basis_max, "level": "",
                    "label": "feste Grenzen", "quelle": "explizit",
                    "chosen": False, "error": ""}
        try:
            import risk_levels
            return risk_levels.effective(self.broker, basis_risiko, basis_max)
        except Exception:
            # Eine kaputte Stufendatei darf den Handel nicht anhalten. Sie
            # faellt auf die bisherigen Werte zurueck; risk_levels selbst
            # verkleinert bereits, wenn es die Datei lesen, aber nicht
            # verstehen kann.
            logger.debug("Einsatzstufe fuer %s nicht lesbar", self.broker,
                         exc_info=True)
            return {"risiko_pro_trade_pct": basis_risiko,
                    "max_position_pct": basis_max, "level": "",
                    "label": "Vorgabe (Stufe nicht lesbar)",
                    "quelle": "vorgabe", "chosen": False, "error": ""}

    # -- Groesse ------------------------------------------------------------
    def positionsgroesse(self, einstieg: float, stop: float, *,
                         asset_type: str = "crypto",
                         kontowert_override: float | None = None) -> tuple[float, str]:
        """Menge aus Risikoabstand und Topfgroesse.

        Zwei Obergrenzen wirken gleichzeitig:
            1. Risiko je Trade  -- wie viel darf dieser eine Trade kosten?
            2. Positionsgroesse -- wie gross darf ein einzelner Wert werden?
        Es gilt immer die kleinere der beiden.
        """
        einstieg = float(einstieg or 0.0)
        stop = float(stop or 0.0)
        if einstieg <= 0:
            return 0.0, "Kein gueltiger Einstiegspreis"
        if stop <= 0 or stop >= einstieg:
            return 0.0, "Stop liegt nicht unter dem Einstieg"
        with self._lock:
            kontowert = (self.kontowert if kontowert_override is None
                         else max(0.0, float(kontowert_override or 0.0)))
        if kontowert <= 0:
            return 0.0, f"{self.broker}: Kontowert unbekannt"

        stufe = self.einsatz()
        abstand = einstieg - stop
        risikobetrag = kontowert * float(stufe["risiko_pro_trade_pct"])
        menge_risiko = risikobetrag / abstand

        max_wert = kontowert * float(stufe["max_position_pct"])
        menge_groesse = max_wert / einstieg

        menge = min(menge_risiko, menge_groesse)
        schritt = float(self.grenzen.mengen_schritt or 0.0)
        if schritt > 0:
            menge = _step_round_down(menge, schritt)
        if menge <= 0:
            return 0.0, f"{self.broker}: berechnete Menge ist null"

        wert = menge * einstieg
        if self.grenzen.min_positionswert > 0 and wert < self.grenzen.min_positionswert:
            return 0.0, (f"{self.broker}: Positionswert {wert:.2f} unter Minimum "
                         f"{self.grenzen.min_positionswert:.2f}")

        begrenzer = "Risiko/Trade" if menge_risiko <= menge_groesse else "Positionsgroesse"
        # Die wirksame Stufe gehoert in die Begruendung. Sonst laesst sich im
        # Entscheidungsjournal spaeter nicht mehr feststellen, mit welchem
        # Einsatz gerechnet wurde -- und genau das war die offene Frage.
        return menge, (f"{self.broker}: {begrenzer} begrenzt auf {menge:g} ({wert:.2f}) "
                       f"| Einsatz {stufe['label']} "
                       f"({float(stufe['risiko_pro_trade_pct']) * 100:.2f} % Risiko, "
                       f"max. {float(stufe['max_position_pct']) * 100:.0f} %)")

    # -- Buchhaltung --------------------------------------------------------
    def buche_ergebnis(self, pnl: float, *, brutto: float | None = None,
                       trade_id: str | None = None) -> None:
        with self._lock:
            self.state.register_realized_pnl(
                pnl, self.kontowert, gross_pnl=brutto, trade_id=trade_id,
                max_daily_loss_pct=self.grenzen.max_tagesverlust_pct)
        if self.tagesverlust_erreicht():
            with self._lock:
                self.state.set_trading_halted(True)
            logger.warning("%s: Tagesverlustgrenze erreicht -- keine neuen Kaeufe mehr heute.",
                           self.broker)
            self.speichern()

    def setze_offene_positionen(self, anzahl: int) -> None:
        with self._lock:
            self.state.set_open_positions(anzahl)

    def uebersicht(self) -> dict:
        # Dieselbe Quelle wie der Kaufpfad, ausserhalb des Locks gelesen.
        stufe = self.einsatz()
        with self._lock:
            # Eine Statusanzeige muss exakt dieselbe Entscheidung liefern wie
            # der echte Kaufpfad. Die alte Kurzpruefung uebersah insbesondere
            # Equity-Bremse, Cooldown, Trade- und Positionslimit.
            darf, grund = self.darf_kaufen()
            return {
                "broker": self.broker,
                "kontowert": round(self.kontowert, 2),
                "tages_pnl": round(float(self.state.realized_pnl_today), 2),
                "tagesverlust_grenze": round(self.kontowert * self.grenzen.max_tagesverlust_pct, 2),
                "offene_positionen": int(self.state.open_positions),
                "max_offene_positionen": int(self.grenzen.max_offene_positionen),
                "trades_heute": int(self.state.trades_today),
                "max_trades": int(self.grenzen.max_trades_pro_tag),
                "handel_gestoppt": bool(self.state.trading_halted),
                "equity_bremse": bool(self.state.equity_guard_halted),
                "equity_tagesbasis": self.state.day_start_equity,
                "equity_drawdown_pct": self.state.equity_drawdown_pct,
                "equity_basis_review_required": self.state.equity_basis_review_required,
                "equity_basis_review_reason": self.state.equity_basis_review_reason,
                "risk_scope_origin": self.state.risk_scope_origin,
                "persistenz_fehler": self._persistence_error or self.state.persistence_failure_reason(),
                "abkuehlung_aktiv": bool(self.state.cooldown_active()),
                "risiko_pro_trade_pct": float(stufe["risiko_pro_trade_pct"]),
                "max_position_pct": float(stufe["max_position_pct"]),
                "einsatz_stufe": str(stufe.get("level") or ""),
                "einsatz_label": str(stufe.get("label") or ""),
                "einsatz_quelle": str(stufe.get("quelle") or ""),
                "einsatz_fehler": str(stufe.get("error") or ""),
                "darf_kaufen": bool(darf),
                "grund": grund,
            }


# ---------------------------------------------------------------------------
# Verwaltung aller Toepfe
# ---------------------------------------------------------------------------
class RiskPotManager:
    """Haelt alle Risikotoepfe und die optionale globale Klammer."""

    def __init__(self, broker_namen: Optional[list[str]] = None, cfg=None):
        self.cfg = cfg or config
        namen = broker_namen or ["etoro", "okx"]
        self.toepfe: dict[str, RiskPot] = {n: RiskPot(n) for n in namen}
        self.global_halt = False
        self.global_grund = ""
        self._lock = threading.RLock()

    # -- Zugriff ------------------------------------------------------------
    def topf(self, broker: str) -> Optional[RiskPot]:
        return self.toepfe.get(str(broker or "").strip().lower())

    def topf_fuer_asset(self, asset_type: str) -> Optional[RiskPot]:
        from broker import broker_fuer_asset
        return self.topf(broker_fuer_asset(asset_type))

    # -- Tagesablauf --------------------------------------------------------
    def neuer_tag_pruefen(self) -> None:
        for topf in self.toepfe.values():
            topf.neuer_tag_pruefen()

    def aktualisiere_kontowerte(self, hub, eigene_positionen=None) -> dict[str, float]:
        """Holt bei jedem Broker den aktuellen Kontowert.

        Ein nicht erreichbarer Broker fuehrt NICHT dazu, dass sein Kontowert
        auf null faellt -- sonst wuerde die naechste Positionsgroesse null
        und der Bot wuerde still aufhoeren zu handeln, ohne dass jemand den
        Grund sieht.

        ``eigene_positionen`` liefert positionsbezogene Mengen/Preise samt
        belegter Markt-/Abrechnungswaehrung. Nur eindeutig eigene Positionen
        zaehlen zum handelbaren Kapital; verschiedene Waehrungen werden nicht
        roh addiert.
        """
        werte: dict[str, float] = {}
        for name, topf in self.toepfe.items():
            broker = hub.broker(name) if hub is not None else None
            if broker is None:
                werte[name] = topf.kontowert
                continue
            # Ab v8.1.4: Kann der Broker das HANDELBARE Kapital nennen, gilt
            # das -- nicht das Gesamtvermoegen des Kontos. Fremdbestaende sind
            # kein Bot-Kapital und duerfen keine Positionsgroesse begruenden.
            try:
                if name == "okx" and getattr(broker, "_nexus_snapshot_valid", True) is False:
                    if not topf.kontowert:
                        topf.kontowert = float(topf.state.last_equity or 0.0)
                    werte[name] = topf.kontowert
                    continue
                native_wert, native_ccy, native_beitraege = None, "", None
                if hasattr(broker, "handelbares_kapital"):
                    positionen = tuple(eigene_positionen(name) if eigene_positionen else ())
                    wert = float(broker.handelbares_kapital(positionen))
                    quote = str(broker.kontowaehrung() if hasattr(
                        broker, "kontowaehrung") else "").upper()
                    basis_key = f"handelbares_kapital:v3:{name}:{quote}"
                    # 10.1.10: Beim rein fremdwaehrungsfinanzierten Konto misst
                    # der Basis-Drawdown vor allem den Wechselkurs. Existiert
                    # genau EINE finanzierte Cash-/Positionswaehrung, wird der
                    # Kapitalwert zusaetzlich nativ in dieser Waehrung gefuehrt
                    # und die Equity-Bremse bewertet Handels- statt FX-Verlust.
                    if hasattr(broker, "capital_evidence"):
                        beleg = broker.capital_evidence() or {}
                        lanes = [l for l in beleg.get("cash_lanes", []) if l.get("funded")]
                        posw = list(beleg.get("position_values", []))
                        waehrungen = ({str(l.get("currency") or "").upper() for l in lanes}
                                      | {str(p.get("market_quote_ccy") or "").upper() for p in posw})
                        waehrungen.discard("")
                        if len(waehrungen) == 1:
                            native_ccy = next(iter(waehrungen))
                            native_wert = (
                                sum(float(l.get("available") or 0.0) for l in lanes)
                                + sum(float(p.get("native_value") or 0.0) for p in posw))
                        elif len(waehrungen) >= 2:
                            # 10.2.0: Mehrere finanzierte Lanes (z. B. USDC+USD)
                            # liefern je Waehrung ihren nativen Betrag samt
                            # beobachtetem Kurs; die Bremse friert die Kurse am
                            # Tagesstart ein. Fehlt irgendwo ein beobachteter
                            # Kurs, gibt es fuer diesen Takt keine native Reihe
                            # (keine erfundenen Paritaeten).
                            beitraege = {}
                            vollstaendig = True
                            for lane in lanes:
                                ccy = str(lane.get("currency") or "").upper()
                                rate = lane.get("rate_to_basis")
                                if not ccy or rate is None or not float(rate) > 0:
                                    vollstaendig = False
                                    break
                                zeile = beitraege.setdefault(
                                    ccy, {"native": 0.0, "rate_to_basis": float(rate)})
                                zeile["native"] += float(lane.get("available") or 0.0)
                            if vollstaendig:
                                for p in posw:
                                    ccy = str(p.get("market_quote_ccy") or "").upper()
                                    rate = p.get("rate_to_basis")
                                    if not ccy or rate is None or not float(rate) > 0:
                                        vollstaendig = False
                                        break
                                    zeile = beitraege.setdefault(
                                        ccy, {"native": 0.0, "rate_to_basis": float(rate)})
                                    zeile["native"] += float(p.get("native_value") or 0.0)
                            if vollstaendig and beitraege:
                                native_beitraege = beitraege
                        topf.state.crypto_exposure = float(sum(
                            float(p.get("value_in_basis") or 0.0) for p in posw))
                else:
                    wert = float(broker.kontowert())
                    quote = str(broker.kontowaehrung() if hasattr(broker, "kontowaehrung") else "").upper()
                    basis_key = f"broker_kontowert:v2:{name}:{quote}"
                fingerprint = (str(broker.account_fingerprint() or "")
                               if hasattr(broker, "account_fingerprint") else "")
                # eToro has a provisional environment hash before /me proves
                # its account. Do not turn that provisional value into scope.
                if name == "etoro" and not getattr(broker, "_identity_loaded", False):
                    fingerprint = ""
                environment = ("DEMO" if getattr(broker, "demo", getattr(broker, "paper", False))
                               else "LIVE")
                scope = (json.dumps([name, environment, fingerprint], separators=(",", ":"))
                         if fingerprint else "")
            except Exception as exc:
                logger.warning("Kontowert %s nicht abrufbar: %s", name, exc)
                werte[name] = topf.kontowert
                continue
            topf.setze_kontowert(wert, basis_key=basis_key, account_scope=scope,
                                 native_wert=native_wert, native_ccy=native_ccy,
                                 native_beitraege=native_beitraege)
            werte[name] = wert
        return werte

    # -- Globale Klammer ----------------------------------------------------
    def globale_pruefung(self) -> tuple[bool, str]:
        """Optionale Gesamtgrenze ueber beide Konten."""
        if not bool(getattr(self.cfg, "GLOBAL_RISK_GUARD_ENABLED", False)):
            return True, ""
        gesamt_wert = sum(t.kontowert for t in self.toepfe.values())
        if gesamt_wert <= 0:
            return True, ""
        gesamt_pnl = sum(float(t.state.realized_pnl_today) for t in self.toepfe.values())
        grenze_pct = float(getattr(self.cfg, "GLOBAL_MAX_DAILY_LOSS_PCT", 0.03))
        if gesamt_pnl <= -abs(gesamt_wert * grenze_pct):
            return False, (f"Globale Tagesverlustgrenze erreicht "
                           f"({gesamt_pnl:.2f} von {gesamt_wert:.2f})")
        return True, ""

    def darf_kaufen(self, asset_type: str) -> tuple[bool, str]:
        with self._lock:
            if self.global_halt:
                return False, self.global_grund or "Globaler Handelsstopp aktiv"
        ok, grund = self.globale_pruefung()
        if not ok:
            return False, grund
        topf = self.topf_fuer_asset(asset_type)
        if topf is None:
            return False, f"Kein Risikotopf fuer Anlageklasse {asset_type!r}"
        return topf.darf_kaufen()

    def globaler_stopp(self, grund: str) -> None:
        with self._lock:
            self.global_halt = True
            self.global_grund = str(grund or "Globaler Handelsstopp")
        logger.critical("Globaler Handelsstopp: %s", self.global_grund)

    def globalen_stopp_aufheben(self) -> None:
        with self._lock:
            self.global_halt = False
            self.global_grund = ""

    # -- Anzeige ------------------------------------------------------------
    def uebersicht(self) -> dict:
        with self._lock:
            halt, grund = self.global_halt, self.global_grund
        toepfe = {n: t.uebersicht() for n, t in self.toepfe.items()}
        return {
            "zeit": datetime.now(timezone.utc).isoformat(),
            "global_halt": halt,
            "global_grund": grund,
            "global_guard_aktiv": bool(getattr(self.cfg, "GLOBAL_RISK_GUARD_ENABLED", False)),
            "gesamt_kontowert": round(sum(t.kontowert for t in self.toepfe.values()), 2),
            "gesamt_tages_pnl": round(
                sum(float(t.state.realized_pnl_today) for t in self.toepfe.values()), 2),
            "toepfe": toepfe,
        }

    def speichern(self) -> None:
        for topf in self.toepfe.values():
            topf.speichern()


__all__ = ["RiskPot", "RiskPotManager", "TopfGrenzen"]
