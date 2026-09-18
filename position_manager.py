"""Positionsverwaltung und detaillierte Trade-/Telegram-Informationen."""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional

import config
from broker.base import BrokerFehler
from instrument_identity import canonical_key, same_instrument
from safe_persistence import atomic_write_json, best_effort_json
from state_lock import critical_state_lock

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _safe_float(value, default=0.0) -> float:
    try:
        v = float(value)
        return v if math.isfinite(v) else default
    except Exception:
        return default


def fmt_money(value, digits=2) -> str:
    return f"{_safe_float(value):,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_price(value, digits=6) -> str:
    """Preis lesbar im deutschen Zahlenformat, ohne unnoetige Nachkommastellen."""
    v = _safe_float(value)
    raw = f"{v:,.{digits}f}".rstrip("0").rstrip(".")
    return raw.replace(",", "X").replace(".", ",").replace("X", ".") if raw else "0"


def _als_id(wert):
    """Broker-IDs koennen als Zahl oder Zeichenkette geliefert werden."""
    try:
        return int(wert or 0)
    except (TypeError, ValueError):
        return 0


class _EinfacherKontrakt:
    """
    Ersatz-Kontrakt fuer Positionen, die nicht im aktuellen Universum
    stehen (z.B. manuell gekauft oder Broker gewechselt). Damit bleiben
    solche Positionen sichtbar, statt stillschweigend zu verschwinden.
    """

    def __init__(self, symbol, currency="USD", kennung=""):
        self.symbol = symbol
        self.localSymbol = symbol
        self.currency = currency
        self.conId = kennung


def protection_result_confirmed(result: dict | None) -> bool:
    """Sicherheitsentscheidung nur aus dem expliziten Adapter-Boolfeld.

    Freitext wie "Schutz vorhanden" hat absichtlich keinerlei Wirkung.
    """
    return bool((result or {}).get("protection_confirmed", False))


@dataclass
class PositionRecord:
    con_id: object
    symbol: str
    asset_type: str
    currency: str
    quantity: float
    avg_cost: float
    entry_time: str
    planned_stop: float = 0.0
    planned_take: float = 0.0
    planned_risk_amount: float = 0.0
    planned_risk_pct: float = 0.0
    entry_reason: str = ""
    profile: str = ""
    source: str = "BOT"
    # AUTO = Bot darf Strategie-/News-/Time-Exits ausloesen.
    # OBSERVE = Position zaehlt voll ins Risiko/Portfolio, wird aber nicht
    # automatisch veraendert. PENDING_TAKEOVER wird erst nach erfolgreicher
    # Schutzpruefung im Trading-Core zu AUTO.
    management_mode: str = "AUTO"
    management_note: str = ""
    last_manual_change: str = ""
    estimated_entry_cost: float = 0.0
    # v9.3: Eigentum an einer eToro-Aktie entsteht AUSSCHLIESSLICH aus der
    # brokerseitig vergebenen positionId. Symbol und Menge sind Anzeige- und
    # Plausibilitaetsdaten -- niemals Eigentumsbeweis. Am 31.08.2026 wurde
    # eine vom Bot gekaufte MSFT-Position als Fremdbestand gefuehrt, weil die
    # Hochstufung an Symbol + nahezu gleicher Menge haengt.
    broker_position_ids: list = field(default_factory=list)
    # observed = im aktuellen Depot gesehen; owned = durch Brokerbeweis oder
    # ausdrueckliche Nutzeruebernahme dieser konkreten ID verwaltbar.
    observed_position_ids: list = field(default_factory=list)
    owned_position_ids: list = field(default_factory=list)
    broker_instrument_id: str = ""
    broker_account_fingerprint: str = ""
    broker_environment: str = ""
    broker_snapshot_id: str = ""
    user_observe_locked: bool = False
    takeover_previous_source: str = ""
    takeover_previous_mode: str = ""
    takeover_previous_stop: float = 0.0
    takeover_previous_take: float = 0.0
    takeover_previous_note: str = ""
    entry_order_ids: list = field(default_factory=list)
    entry_reference_id: str = ""
    decision_id: int = 0
    # PENDING = eigener Kauf, Depotbestaetigung steht noch aus
    # VERIFIED = positionId im autoritativen Depot-Snapshot bestaetigt
    # EXTERNAL = beim Broker vorhanden, nicht vom Bot
    # UNPROVABLE = Kette unvollstaendig, wird nur beobachtet
    ownership_status: str = ""
    reconciliation_status: str = ""
    protection_status: str = "UNKNOWN"
    protection_detail: str = ""
    # Explicit maintenance decisions, never an invented original order/price rule.
    # planned_stop/take contain the current effective plan; each earlier plan
    # and the exact broker readback are preserved here across restart/save.
    protection_plan_history: list = field(default_factory=list)
    attribution_history: list = field(default_factory=list)
    first_missing_at: str = ""
    consecutive_missing_snapshots: int = 0

    def position_id_set(self) -> set:
        values = self.observed_position_ids or self.broker_position_ids
        return {str(x) for x in values if str(x).strip()}

    def owned_position_id_set(self) -> set:
        return {str(x) for x in (self.owned_position_ids or []) if str(x).strip()}

    @property
    def ownership_chain_complete(self) -> bool:
        """Vollstaendige eToro-ID-Kette. Kein Wert hier darf sich durch ein
        Softwareupdate, eine Rundung oder einen Anzeigewechsel aendern."""
        owned = self.owned_position_id_set()
        # Nur fuer frisch erzeugte Legacy-Test-/Diagnoseobjekte ohne das neue
        # observed-Feld. Persistierte 9.3-Staende erhalten beim Laden observed
        # und koennen diesen Fallback daher nicht zur Selbstbestaetigung nutzen.
        if not owned and not self.observed_position_ids:
            owned = {str(x) for x in (self.broker_position_ids or []) if str(x)}
        return bool(owned
                    and (self.entry_order_ids or self.entry_reference_id)
                    and str(self.ownership_status or "").upper() == "VERIFIED")


class PositionManager:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or getattr(config, "POSITION_STATE_FILE", "position_state.json"))
        self.records: Dict[str, PositionRecord] = {}
        self.applied_fill_events: Dict[str, dict] = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            with critical_state_lock(self.path):
                data = json.loads(self.path.read_text(encoding="utf-8"))
            self.applied_fill_events = {
                str(k): dict(v) for k, v in
                (data.get("applied_fill_events", {}) or {}).items()
                if str(k) and isinstance(v, dict)
            }
            for key, payload in data.get("positions", {}).items():
                payload=dict(payload)
                if "management_mode" not in payload:
                    payload["management_mode"] = "AUTO" if str(payload.get("source","")).upper()=="BOT" else "OBSERVE"
                # v9.3: Ein Stand aus 9.2 kennt die neuen Eigentumsfelder
                # nicht. Ein unbekanntes Feld darf den Start nicht
                # verhindern -- und ein fehlendes Feld darf keine
                # Eigentumsbehauptung erzeugen.
                erlaubt = set(PositionRecord.__dataclass_fields__)
                payload = {k: v for k, v in payload.items() if k in erlaubt}
                if not payload.get("ownership_status"):
                    payload["ownership_status"] = (
                        "VERIFIED" if payload.get("broker_position_ids")
                        else ("EXTERNAL" if str(payload.get("source", "")).upper()
                              in {"BROKER_EXISTING", "MANUAL"} else "UNPROVABLE"))
                legacy_ids = [str(x) for x in (payload.get("broker_position_ids") or [])
                              if str(x).strip()]
                if not payload.get("observed_position_ids"):
                    payload["observed_position_ids"] = legacy_ids
                # Ein alter VERIFIED-Wert wird nicht als Eigentumsbeweis
                # migriert: 9.3.1 konnte ihn zirkulaer aus dem beobachteten
                # Depotbestand erzeugen. Der naechste Abgleich muss die ID
                # erneut gegen etoro_reconciliation beweisen.
                if "owned_position_ids" not in payload:
                    if (str(payload.get("source") or "").upper() == "USER_MANAGED"
                            and str(payload.get("management_mode") or "").upper() == "AUTO"):
                        payload["owned_position_ids"] = legacy_ids
                        payload["ownership_status"] = "USER_AUTHORIZED"
                    else:
                        payload["owned_position_ids"] = []
                        if str(payload.get("source") or "").upper() == "BOT":
                            payload["management_mode"] = "PENDING_CONFIRMATION"
                            payload["ownership_status"] = "PENDING"
                if "user_observe_locked" not in payload:
                    note = str(payload.get("management_note") or "").lower()
                    payload["user_observe_locked"] = bool(
                        payload.get("last_manual_change")
                        and ("nur beobachten" in note
                             or "vom nutzer auf beobachten" in note))
                self.records[str(key)] = PositionRecord(**payload)
        except Exception as exc:
            logger.error("Positions-State unlesbar; Geldpfad bleibt gesperrt: %s", exc)
            raise RuntimeError(
                "Positions-State ist unlesbar; keine Brokerorder darf ausgefuehrt werden"
            ) from exc

    def save(self):
        payload = {
            "updated_at": _now_iso(),
            "positions": {key: asdict(rec) for key, rec in self.records.items()},
            "applied_fill_events": dict(
                list(self.applied_fill_events.items())[-5000:]),
        }
        with critical_state_lock(self.path):
            atomic_write_json(self.path, payload)

    def key(self, contract_or_con_id) -> str:
        """
        Eindeutiger Schluessel einer Position.
        Broker-APIs koennen numerische oder textuelle IDs liefern --
        deshalb wird nicht mehr auf int gezwungen.
        """
        if isinstance(contract_or_con_id, str):
            return contract_or_con_id
        kennung = getattr(contract_or_con_id, "conId", None)
        if kennung:
            return str(kennung)
        symbol = getattr(contract_or_con_id, "localSymbol", None) or getattr(contract_or_con_id, "symbol", None)
        return str(symbol or contract_or_con_id)

    def _symbol_of(self, contract_or_con_id) -> str:
        if isinstance(contract_or_con_id, str):
            return contract_or_con_id.upper()
        symbol = (getattr(contract_or_con_id, "localSymbol", None)
                  or getattr(contract_or_con_id, "symbol", None) or "")
        return str(symbol).upper()

    def _find_key_by_symbol(self, symbol: str, asset_type: str | None = None) -> Optional[str]:
        wanted = str(symbol or "").upper()
        if not wanted:
            return None
        matches = []
        for key, rec in self.records.items():
            if asset_type:
                if same_instrument(rec.symbol, rec.asset_type, wanted, asset_type):
                    matches.append(key)
            elif str(rec.symbol or "").upper() == wanted:
                matches.append(key)
        return matches[0] if len(matches) == 1 else None

    def _find_key_by_position_ids(self, position_ids,
                                  account_fingerprint: str = "", broker_environment: str = "") -> Optional[str]:
        """Der EINZIGE belastbare Zuordnungsweg fuer eToro (v9.3).

        Gesucht wird die Schnittmenge exakter positionIds. Symbol und Menge
        kommen hier bewusst nicht vor: eToro liefert fuer dasselbe Instrument
        mehrere Position-Lines, rundet Mengen und propagiert zeitversetzt.
        Am 31.08.2026 wurde eine vom Bot gekaufte MSFT-Position deshalb als
        Fremdbestand gefuehrt.
        """
        gesucht = {str(x) for x in (position_ids or []) if str(x).strip()}
        if not gesucht:
            return None
        matches = []
        for key, rec in self.records.items():
            # Sobald der aktive Broker sein Konto belegt, darf weder ein
            # anderer noch ein alter kontoloser Datensatz nur aufgrund einer
            # zufaellig gleichen positionId passen. Legacy-Zeilen bleiben bis
            # zu einer expliziten Migration/Benutzerentscheidung pausiert.
            if (account_fingerprint and
                    str(rec.broker_account_fingerprint or "") !=
                    str(account_fingerprint)):
                continue
            if broker_environment and rec.broker_environment != broker_environment:
                continue
            if rec.position_id_set() & gesucht:
                matches.append(key)
        return matches[0] if len(matches) == 1 else None

    def get_by_record_id(self, record_id: str) -> Optional[PositionRecord]:
        return self.records.get(str(record_id or ""))

    def get_by_position_id(self, position_id: str,
                           account_fingerprint: str = "") -> Optional[PositionRecord]:
        key = self._find_key_by_position_ids(
            [position_id], account_fingerprint=account_fingerprint)
        return self.records.get(key) if key else None

    def _lookup_key(self, contract_or_con_id) -> str:
        direct = self.key(contract_or_con_id)
        if direct in self.records:
            return direct
        by_symbol = self._find_key_by_symbol(self._symbol_of(contract_or_con_id))
        return by_symbol or direct

    def get(self, contract_or_con_id) -> Optional[PositionRecord]:
        return self.records.get(self._lookup_key(contract_or_con_id))

    def get_by_symbol(self, symbol: str) -> Optional[PositionRecord]:
        matches = [rec for rec in self.records.values()
                   if str(rec.symbol or "").upper() == str(symbol or "").upper()]
        return matches[0] if len(matches) == 1 else None

    def _record_for_action(self, record_id_or_symbol: str) -> tuple[str, Optional[PositionRecord]]:
        token = str(record_id_or_symbol or "")
        if token in self.records:
            return token, self.records[token]
        matches = [(key, rec) for key, rec in self.records.items()
                   if str(rec.symbol or "").upper() == token.upper()]
        return matches[0] if len(matches) == 1 else ("", None)

    def remove(self, contract_or_con_id):
        self.records.pop(self.key(contract_or_con_id), None)
        self.save()

    @staticmethod
    def _symbolkern(wert) -> str:
        """Vergleichbare Form eines Aktiensymbols (SPGI, SPGI.US, spgi)."""
        roh = str(wert or "").strip().upper()
        for suffix in (".US", ".DE", ".L", ".PA", ".MI", ".AS", ".SW"):
            if roh.endswith(suffix):
                roh = roh[: -len(suffix)]
                break
        return roh

    @classmethod
    def _offene_kaufabsicht(cls, position_ids, symbol: str, *,
                            account_fingerprint: str = "",
                            broker_environment: str = "") -> Optional[dict]:
        """Gehoert dieser Depotbestand zu einem eigenen Kauf?

        KORREKTUR 31.08.2026 (SPGI). Die erste Fassung hatte zwei Loecher,
        durch die eine selbst gekaufte Aktie wieder als Fremdbestand landete:

        1. Der Symbolabgleich galt nur, wenn der Reconciliation-Satz noch GAR
           KEINE positionId hatte (``not eigene``). Sobald eToro eine
           Ausfuehrung gemeldet hatte -- was Sekunden nach dem Kauf passiert
           --, war er damit tot. Genau in diesem Fenster laeuft der
           Depot-Abgleich.
        2. Verglichen wurde das rohe Symbol. "SPGI" und "SPGI.US" galten als
           verschiedene Werte.

        Rangfolge jetzt:
          a) Schnittmenge exakter positionIds  -> Eigentum beweisbar
          b) gleicher Wert, eigener Kauf offen -> nur PENDING_CONFIRMATION

        (b) erzeugt NIEMALS Eigentum und niemals AUTO-Verwaltung. Die
        Position wird sichtbar als eigener Kauf gefuehrt, aber der Bot
        verkauft sie nicht, bevor die positionId sie belegt.
        """
        gesucht = {str(x) for x in (position_ids or []) if str(x).strip()}
        kern = cls._symbolkern(symbol)
        try:
            import etoro_reconciliation
            kaeufe = etoro_reconciliation.eigene_kaeufe()
        except Exception:
            logger.debug("Eigene eToro-Kaeufe nicht lesbar", exc_info=True)
            return None

        for satz in kaeufe:
            if (account_fingerprint and
                    str(satz.get("account_fingerprint") or "") !=
                    str(account_fingerprint)):
                continue
            if broker_environment:
                expected_paper = str(broker_environment).upper() == "DEMO"
                if bool(satz.get("paper")) != expected_paper:
                    continue
            verified = {str(x) for x in (satz.get("verified_position_ids") or [])
                        if str(x).strip()}
            if gesucht and bool(satz.get("bestaetigt")) and verified & gesucht:
                beweis = dict(satz)
                beweis["_id_beweis"] = True
                return beweis
        # Eine Kaufabsicht reserviert weiterhin Cash im Orderjournal. Sie
        # beansprucht aber keine andere, manuell gekaufte Depotposition.
        return None

    def _kaufabsicht_kompatibel(self, pids, symbol, *, account_fingerprint="",
                                broker_environment=""):
        try:
            return self._offene_kaufabsicht(
                pids, symbol, account_fingerprint=account_fingerprint,
                broker_environment=broker_environment)
        except TypeError:
            # Bestehende Erweiterungs-/Test-Doubles aus 9.3 akzeptieren nur
            # die beiden alten Argumente. Der Produktivpfad benutzt die neue
            # Kontobindung.
            return self._offene_kaufabsicht(pids, symbol)

    def upsert_existing_position(self, contract, quantity, avg_cost, currency=None,
                                 asset_type="stock", schluessel=None,
                                 position_ids=None, instrument_id="",
                                 account_fingerprint="", broker_environment="",
                                 snapshot_id=""):
        key = schluessel or self.key(contract)
        rec = self.records.get(key)
        pids = [str(x) for x in (position_ids or []) if str(x).strip()]
        symbol = getattr(contract, "localSymbol", None) or getattr(contract, "symbol", "?")
        eigen = self._kaufabsicht_kompatibel(
            pids, symbol, account_fingerprint=account_fingerprint,
            broker_environment=broker_environment)
        if rec is None:
            if eigen:
                exact = bool(eigen.get("_id_beweis"))
                owned = sorted(set(pids) & {
                    str(x) for x in (eigen.get("verified_position_ids") or [])})
                rec = PositionRecord(
                    con_id=_als_id(getattr(contract, "conId", 0)),
                    symbol=symbol, asset_type=asset_type,
                    currency=currency or getattr(contract, "currency", ""),
                    quantity=float(quantity), avg_cost=float(avg_cost),
                    entry_time=_now_iso(),
                    planned_stop=_safe_float(eigen.get("stop")),
                    planned_take=_safe_float(eigen.get("take_profit")),
                    profile=getattr(config, "ACTIVE_PROFILE", ""),
                    source="BOT",
                    management_mode="PENDING_CONFIRMATION",
                    management_note=("Eigener Kauf; Depotbestaetigung der "
                                     "positionId steht noch aus."),
                    broker_position_ids=list(pids),
                    observed_position_ids=list(pids),
                    owned_position_ids=owned,
                    broker_instrument_id=str(instrument_id or ""),
                    broker_account_fingerprint=str(account_fingerprint or ""),
                    broker_environment=str(broker_environment or ""),
                    broker_snapshot_id=str(snapshot_id or ""),
                    entry_order_ids=[str(x) for x in (eigen.get("order_ids") or [])],
                    entry_reference_id=str(eigen.get("reference_id") or ""),
                    decision_id=int(eigen.get("decision_id") or 0),
                    # Nur der positionId-Beweis erzeugt Eigentum. Ein reiner
                    # Symboltreffer im Propagationsfenster bleibt PENDING.
                    ownership_status=("VERIFIED" if exact else "PENDING"),
                    reconciliation_status=("CONFIRMED_OPEN" if exact
                                           else "PENDING_CONFIRMATION"),
                )
            else:
                rec = PositionRecord(
                    con_id=_als_id(getattr(contract, "conId", 0)),
                    symbol=symbol, asset_type=asset_type,
                    currency=currency or getattr(contract, "currency", ""),
                    quantity=float(quantity), avg_cost=float(avg_cost),
                    entry_time=_now_iso(),
                    profile=getattr(config, "ACTIVE_PROFILE", ""),
                    source="BROKER_EXISTING",
                    management_mode="OBSERVE",
                    management_note="Beim Broker bereits vorhanden; standardmaessig nur beobachten.",
                    broker_position_ids=list(pids),
                    observed_position_ids=list(pids),
                    owned_position_ids=[],
                    broker_instrument_id=str(instrument_id or ""),
                    broker_account_fingerprint=str(account_fingerprint or ""),
                    broker_environment=str(broker_environment or ""),
                    broker_snapshot_id=str(snapshot_id or ""),
                    ownership_status="EXTERNAL",
                )
            self.records[key] = rec
        else:
            # Pro Record genau eine Depotzeile. Beobachtung ist keine
            # Eigentumsbehauptung und darf owned_position_ids nie erweitern.
            rec.broker_position_ids = list(pids)
            rec.observed_position_ids = list(pids)
            rec.broker_instrument_id = str(instrument_id or rec.broker_instrument_id)
            rec.broker_account_fingerprint = str(
                account_fingerprint or rec.broker_account_fingerprint)
            rec.broker_environment = str(broker_environment or rec.broker_environment)
            rec.broker_snapshot_id = str(snapshot_id or rec.broker_snapshot_id)
            self._pruefe_eigenen_kauf_nach(
                rec, pids, eigen=eigen)
            new_qty=float(quantity)
            if abs(new_qty-float(rec.quantity or 0)) > max(1e-9, abs(new_qty)*1e-8):
                # Normalerweise hat capture_new_fills die Menge bereits gesetzt.
                # Eine hiervon abweichende Broker-Menge ist daher eine manuelle
                # oder sonstige externe Aenderung und darf nicht still automatisch
                # weiterverwaltet werden.
                # v9.3: Eine reine RUNDUNGSabweichung ist keine externe
                # Aenderung. eToro rundet Mengen; bis 9.2 hat das eine
                # bewiesene Botposition auf OBSERVE herabgestuft.
                relativ = abs(new_qty - float(rec.quantity or 0)) / max(abs(new_qty), 1e-12)
                rundung = relativ <= float(getattr(
                    config, "ETORO_MENGEN_RUNDUNGSTOLERANZ", 1e-6))
                if str(rec.management_mode).upper()=="AUTO" and not rundung:
                    rec.source = "MIXED" if str(rec.source).upper()=="BOT" else rec.source
                    rec.management_mode = "OBSERVE"
                    rec.management_note = "Externe Broker-Mengenaenderung erkannt; Automatik pausiert."
                    rec.last_manual_change = _now_iso()
            rec.quantity = new_qty
            if avg_cost:
                rec.avg_cost = float(avg_cost)
        return rec

    def _pruefe_eigenen_kauf_nach(self, rec, pids, *, eigen=None) -> bool:
        """Einen als fremd gefuehrten Bestand nachtraeglich richtigstellen.

        Greift nur bei Datensaetzen, die NICHT vom Bot gefuehrt werden und die
        der Nutzer nicht selbst angefasst hat. Eine bewusst auf "nur
        beobachten" gesetzte Position bleibt unberuehrt.

        Der positionId-Beweis fuehrt zu VERIFIED. Ein reiner Symboltreffer
        fuehrt nur zu PENDING_CONFIRMATION -- die Position wird sichtbar als
        eigener Kauf gefuehrt, aber der Bot verkauft sie nicht, bevor die
        positionId sie belegt.
        """
        if str(rec.management_mode or "").upper() == "AUTO":
            return False
        if bool(getattr(rec, "user_observe_locked", False)):
            return False

        eigen = eigen or self._kaufabsicht_kompatibel(
            pids or sorted(rec.position_id_set()), rec.symbol,
            account_fingerprint=rec.broker_account_fingerprint,
            broker_environment=rec.broker_environment)
        before = asdict(rec)
        if not eigen or not eigen.get("_id_beweis"):
            if (str(rec.ownership_status or "").upper() == "PENDING"
                    and not rec.owned_position_id_set()):
                rec.attribution_history.append({
                    "at": _now_iso(), "reason": "no_exact_position_evidence",
                    "decision_id": rec.decision_id,
                    "order_ids": list(rec.entry_order_ids),
                    "reference_id": rec.entry_reference_id,
                    "stop": rec.planned_stop, "take": rec.planned_take})
                rec.decision_id = 0
                rec.entry_order_ids = []
                rec.entry_reference_id = ""
                rec.planned_stop = rec.planned_take = 0.0
                rec.planned_risk_amount = rec.planned_risk_pct = 0.0
                rec.source = "BROKER_EXISTING"
                rec.management_mode = "OBSERVE"
                rec.ownership_status = "EXTERNAL"
                rec.reconciliation_status = ""
                rec.management_note = (
                    "Keine exakte eigene positionId belegt; nur beobachten.")
            return asdict(rec) != before

        id_beweis = bool(eigen.get("_id_beweis"))
        rec.source = "BOT"
        rec.decision_id = int(eigen.get("decision_id") or 0) or rec.decision_id
        rec.entry_order_ids = sorted(
            set(rec.entry_order_ids or []) | {str(x) for x in (eigen.get("order_ids") or [])})
        rec.entry_reference_id = rec.entry_reference_id or str(eigen.get("reference_id") or "")
        if not rec.planned_stop:
            rec.planned_stop = _safe_float(eigen.get("stop"))
        if not rec.planned_take:
            rec.planned_take = _safe_float(eigen.get("take_profit"))
        if id_beweis:
            verified = {str(x) for x in (eigen.get("verified_position_ids") or [])}
            rec.owned_position_ids = sorted(set(pids) & verified)
            rec.ownership_status = "VERIFIED"
            rec.reconciliation_status = "CONFIRMED_OPEN"
            rec.management_mode = "PENDING_CONFIRMATION"
            rec.management_note = (rec.protection_detail or
                ("Kauf und Einstand bestaetigt; Broker-Schutz wird geprueft."
                 if rec.avg_cost > 0 else
                 "Eigener Kauf: positionId belegt; Einstand wird abgeglichen."))
        else:
            rec.ownership_status = "PENDING"
            rec.reconciliation_status = "PENDING_CONFIRMATION"
            rec.management_mode = "PENDING_CONFIRMATION"
            rec.management_note = ("Eigener Kauf erkannt; Zuordnung der "
                                   "positionId laeuft.")
        changed = asdict(rec) != before
        if changed:
            logger.info("%s: Kaufzuordnung ueber positionId bestaetigt (decision_id=%s)",
                        rec.symbol, rec.decision_id)
        return changed

    def register_buy(self, contract, fill_qty, fill_price, currency, asset_type,
                     account_equity, stop_price, take_price, reason="", profile="",
                     estimated_entry_cost=0.0, source="BOT", management_mode=None,
                     position_ids=None, order_ids=None, reference_id="",
                     decision_id=0, instrument_id="", account_fingerprint="",
                     broker_environment="", snapshot_id="", fill_id=""):
        fill_qty = abs(_safe_float(fill_qty))
        fill_price = _safe_float(fill_price)
        pids = [str(x) for x in (position_ids or []) if str(x).strip()]
        oids = [str(x) for x in (order_ids or []) if str(x).strip()]
        # v9.3: ZUERST ueber die exakte positionId suchen. Erst danach der
        # alte Weg ueber Contract/Symbol, damit Altbestaende ohne IDs weiter
        # gefunden werden.
        key = self._find_key_by_position_ids(
            pids, account_fingerprint=account_fingerprint,
            broker_environment=broker_environment) or self._lookup_key(contract)
        old = self.records.get(key)
        if old is not None and ((account_fingerprint and old.broker_account_fingerprint != account_fingerprint)
                                or (broker_environment and old.broker_environment != broker_environment)):
            if not pids:
                raise RuntimeError("Kauf ohne positionId widerspricht der vorhandenen Kontobindung")
            key = f"etoro:{account_fingerprint}:{broker_environment.lower()}:{pids[0]}"
            old = self.records.get(key)
        event_key = str(fill_id or "")[:240]
        previous_event = self.applied_fill_events.get(event_key) if event_key else None
        if previous_event and str(previous_event.get("side") or "") == "BUY":
            record_id = str(previous_event.get("record_id") or "")
            existing = self.records.get(record_id)
            if existing is not None:
                return existing
            snapshot = dict(previous_event.get("record_after") or {})
            allowed = set(PositionRecord.__dataclass_fields__)
            snapshot = {k: v for k, v in snapshot.items() if k in allowed}
            if snapshot:
                return PositionRecord(**snapshot)
        # v9.3: Wenn dieser Kauf eine EIGENE positionId mitbringt und der ueber
        # das Symbol gefundene Datensatz nachweislich zu einer ANDEREN
        # Position-Line gehoert, ist das ein fremder Bestand. Er darf weder
        # uebernommen noch aufgestockt werden -- sonst stuenden 29 eigene
        # plus 29 fremde MSFT als 58 im Buch. Dieser Kauf bekommt einen
        # eigenen Datensatz unter seiner positionId.
        if (pids and old is not None and old.position_id_set()
                and not (old.position_id_set() & set(pids))):
            key = ",".join(sorted(pids))
            old = self.records.get(key)
        src=str(source or "BOT").upper()
        mode=(str(management_mode).upper() if management_mode else ("AUTO" if src=="BOT" else "OBSERVE"))
        # Eine eToro-Position ist im Depot oft sichtbar, BEVOR der Lookup-Fill
        # geliefert wird. sync_with_broker() legt sie dann zuerst an. Kommt
        # spaeter der bewiesene Bot-Fill, darf seine Menge nicht ein zweites
        # Mal addiert werden -- genau dieser Datensatz wird hochgestuft.
        #
        # Bis 9.2 haing die Hochstufung an Symbol UND nahezu gleicher Menge.
        # Der Kommentar behauptete "exakt per Broker-ID", der Code verglich
        # Mengen. Am 31.08.2026 blieb eine selbst gekaufte MSFT-Position
        # deshalb dauerhaft "Externer Bestand". Ab 9.3 entscheidet die
        # positionId; die Menge ist nur noch Plausibilitaet.
        id_beweis = bool(pids and old is not None
                         and (old.position_id_set() & set(pids)))
        mengen_naehe = (old is not None
                        and abs(float(old.quantity or 0.0) - fill_qty)
                            <= max(1e-9, fill_qty * 1e-8))
        uebernehmbar = (old is not None and src == "BOT"
                        and str(old.management_mode or "").upper()
                            in {"OBSERVE", "PENDING_CONFIRMATION"}
                        and str(old.source or "").upper()
                            in {"BOT", "BROKER_EXISTING", "AMBIGUOUS", "MANUAL"})
        if uebernehmbar and (id_beweis or (not pids and mengen_naehe)):
            risk_amount = max(0.0, (fill_price - _safe_float(stop_price)) * fill_qty)
            old.quantity = fill_qty
            old.avg_cost = fill_price
            old.planned_stop = _safe_float(stop_price)
            old.planned_take = _safe_float(take_price)
            old.planned_risk_amount = risk_amount
            old.planned_risk_pct = (risk_amount / account_equity) if account_equity > 0 else 0.0
            old.entry_reason = reason
            old.profile = profile or getattr(config, "ACTIVE_PROFILE", "")
            old.source = "BOT"
            # positionId beweist Eigentum, aber nicht den tatsaechlich beim
            # Broker hinterlegten SL/TP. 9.4.1 setzte einen bereits sichtbaren
            # Datensatz hier trotzdem immer auf AUTO und ueberging damit ein
            # angefordertes PENDING_CONFIRMATION.
            old.management_mode = mode
            if mode == "AUTO":
                old.management_note = (
                    "Botposition exakt ueber positionId und Schutz bestaetigt."
                    if id_beweis else
                    "Botposition ohne positionId uebernommen (Altbestand).")
            else:
                old.management_note = (
                    "Eigene positionId bestaetigt; Broker-Schutz noch nicht "
                    "vollstaendig rueckgelesen. Automatik bleibt pausiert.")
            old.last_manual_change = ""
            old.estimated_entry_cost = max(0.0, _safe_float(estimated_entry_cost))
            if pids:
                old.broker_position_ids = sorted(old.position_id_set() | set(pids))
                old.observed_position_ids = sorted(old.position_id_set() | set(pids))
                old.owned_position_ids = sorted(set(pids)) if id_beweis else []
            if oids:
                old.entry_order_ids = sorted(set(old.entry_order_ids or []) | set(oids))
            if reference_id:
                old.entry_reference_id = str(reference_id)
            if decision_id:
                old.decision_id = int(decision_id)
            old.ownership_status = "VERIFIED" if id_beweis else "UNPROVABLE"
            old.reconciliation_status = "CONFIRMED_OPEN"
            old.broker_instrument_id = str(instrument_id or old.broker_instrument_id)
            old.broker_account_fingerprint = str(
                account_fingerprint or old.broker_account_fingerprint)
            old.broker_environment = str(broker_environment or old.broker_environment)
            old.broker_snapshot_id = str(snapshot_id or old.broker_snapshot_id)
            if event_key:
                self.applied_fill_events[event_key] = {
                    "side": "BUY", "record_id": key,
                    "record_after": asdict(old), "applied_at": _now_iso()}
            self.save()
            return old
        if old and old.quantity > 0:
            total_qty = old.quantity + fill_qty
            old.avg_cost = ((old.avg_cost * old.quantity) + (fill_price * fill_qty)) / total_qty
            old.quantity = total_qty
            old.estimated_entry_cost += max(0.0, _safe_float(estimated_entry_cost))
            # Jede manuelle Aufstockung einer Bot-Position macht die Herkunft
            # gemischt. Automatik wird sicherheitshalber deaktiviert, bis der
            # Nutzer die Verwaltung bewusst wieder uebernimmt.
            if src != "BOT":
                old.source = "MIXED" if str(old.source).upper()=="BOT" else src
                old.management_mode = "OBSERVE"
                old.management_note = "Manuelle Aufstockung erkannt; Bot-Verwaltung pausiert."
                old.last_manual_change = _now_iso()
            rec = old
        else:
            risk_amount = max(0.0, (fill_price - _safe_float(stop_price)) * fill_qty)
            risk_pct = (risk_amount / account_equity) if account_equity > 0 else 0.0
            rec = PositionRecord(
                con_id=_als_id(getattr(contract, "conId", 0)),
                symbol=getattr(contract, "localSymbol", None) or getattr(contract, "symbol", "?"),
                asset_type=asset_type,
                currency=currency or getattr(contract, "currency", ""),
                quantity=fill_qty,
                avg_cost=fill_price,
                entry_time=_now_iso(),
                planned_stop=_safe_float(stop_price),
                planned_take=_safe_float(take_price),
                planned_risk_amount=risk_amount,
                planned_risk_pct=risk_pct,
                entry_reason=reason,
                profile=profile or getattr(config, "ACTIVE_PROFILE", ""),
                source=src,
                management_mode=mode,
                management_note=("" if mode=="AUTO" else "Manueller/uebernommener Broker-Kauf: nur beobachten."),
                last_manual_change=(_now_iso() if src != "BOT" else ""),
                estimated_entry_cost=max(0.0, _safe_float(estimated_entry_cost)),
                broker_position_ids=list(pids),
                observed_position_ids=list(pids),
                owned_position_ids=(list(pids) if src == "BOT" else []),
                broker_instrument_id=str(instrument_id or ""),
                broker_account_fingerprint=str(account_fingerprint or ""),
                broker_environment=str(broker_environment or ""),
                broker_snapshot_id=str(snapshot_id or ""),
                entry_order_ids=list(oids),
                entry_reference_id=str(reference_id or ""),
                decision_id=int(decision_id or 0),
                ownership_status=("VERIFIED" if (src == "BOT" and pids)
                                  else ("EXTERNAL" if src != "BOT" else "UNPROVABLE")),
                reconciliation_status=("CONFIRMED_OPEN" if (src == "BOT" and pids) else ""),
            )
            self.records[key] = rec
        if event_key:
            self.applied_fill_events[event_key] = {
                "side": "BUY", "record_id": key,
                "record_after": asdict(rec), "applied_at": _now_iso()}
        self.save()
        return rec

    def register_sell(self, contract, fill_qty, fill_price, currency=None,
                      estimated_exit_cost=0.0, execution_source="BOT",
                      position_ids=None, account_fingerprint="", fill_id=""):
        event_key = str(fill_id or "")[:240]
        previous_event = self.applied_fill_events.get(event_key) if event_key else None
        if previous_event and str(previous_event.get("side") or "") == "SELL":
            snapshot = dict(previous_event.get("record_before") or {})
            unresolved = (
                str(previous_event.get("status") or "").upper() == "UNMATCHED"
                or (not snapshot and str(previous_event.get("record_id") or "").startswith(
                    "unmatched-position:"))
            )
            if not unresolved:
                allowed = set(PositionRecord.__dataclass_fields__)
                snapshot = {k: v for k, v in snapshot.items() if k in allowed}
                previous = PositionRecord(**snapshot) if snapshot else None
                return (previous, _safe_float(previous_event.get("pnl")),
                        _safe_float(previous_event.get("pnl_pct")),
                        _safe_float(previous_event.get("remaining")))
            # 9.5.0 schrieb auch einen nicht zugeordneten SELL als dauerhaft
            # "angewendet". Dadurch konnte ein spaeter geladener exakter
            # Reconciliation-/Positionsdatensatz nie mehr mit demselben Fill
            # verbunden werden. UNMATCHED ist nur ein beobachteter Beleg und
            # wird bei jedem Replay erneut ueber account+positionId gesucht.
        exact_ids = [str(x) for x in (position_ids or []) if str(x)]
        key = self._find_key_by_position_ids(
            exact_ids, account_fingerprint=account_fingerprint)
        if key is None:
            key = (f"unmatched-position:{exact_ids[0]}" if exact_ids
                   else self._lookup_key(contract))
        rec = self.records.get(key)
        rec_before_snapshot = asdict(rec) if rec is not None else {}
        qty = abs(_safe_float(fill_qty))
        px = _safe_float(fill_price)
        entry = rec.avg_cost if rec else 0.0
        entry_cost_share = 0.0
        if rec and rec.quantity > 0:
            entry_cost_share = max(0.0, rec.estimated_entry_cost) * min(1.0, qty / rec.quantity)
        exit_cost = max(0.0, _safe_float(estimated_exit_cost))
        gross = (px - entry) * qty if entry > 0 else 0.0
        pnl = gross - entry_cost_share - exit_cost if entry > 0 else 0.0
        invested = entry * qty + entry_cost_share if entry > 0 else 0.0
        pnl_pct = pnl / invested if invested > 0 else 0.0
        remaining = max(0.0, (rec.quantity - qty)) if rec else 0.0
        if rec:
            if remaining <= 1e-9:
                self.records.pop(key, None)
            else:
                rec.quantity = remaining
                rec.estimated_entry_cost = max(0.0, rec.estimated_entry_cost - entry_cost_share)
                if str(execution_source or "BOT").upper() != "BOT":
                    rec.source = "MIXED" if str(rec.source).upper()=="BOT" else rec.source
                    rec.management_mode = "OBSERVE"
                    rec.management_note = "Manueller Teilverkauf erkannt; Bot-Verwaltung pausiert."
                    rec.last_manual_change = _now_iso()
        if event_key:
            self.applied_fill_events[event_key] = {
                "side": "SELL", "record_id": key,
                "record_before": rec_before_snapshot,
                "pnl": pnl, "pnl_pct": pnl_pct, "remaining": remaining,
                "status": "APPLIED" if rec is not None else "UNMATCHED",
                "applied_at": _now_iso()}
        self.save()
        return rec, pnl, pnl_pct, remaining

    def sync_with_broker(self, broker, instruments: Iterable, notify_missing=False):
        """
        Der Broker ist die Wahrheit: Positionen werden von eToro uebernommen.

        Die Zuordnung nutzt immer Asset-Typ + Symbol. Ein Kurzticker allein
        darf keine Aktie mit einer gleichnamigen Kryptowaehrung verbinden.
        """
        by_identity = {}
        for inst in instruments:
            try:
                by_identity[canonical_key(inst.name, inst.asset_type)] = inst
            except Exception as exc:
                logger.debug("Ungueltige Instrumentidentitaet im Universum: %s", exc)

        # Der erfolgreich gelesene Depotstand ist nicht nur Anzeigequelle,
        # sondern ein autoritativer Reconciliation-Beleg. In 9.5.0 sah dieser
        # Pfad ADBE bereits mit der exakten positionId, schrieb den Beleg aber
        # nie in ``etoro_reconciliation`` zurueck. Damit war der Daemon-Worker
        # ein Single Point of Failure. Der gleiche PnL-Snapshot heilt den
        # Kaufzustand nun, bevor irgendeine lokale Herkunft klassifiziert wird.
        broker_positions = list(broker.positionen())
        if str(getattr(broker, "name", "") or "").lower() == "etoro":
            try:
                import etoro_reconciliation
                snapshot = (broker.position_snapshot(force=False)
                            if callable(getattr(broker, "position_snapshot", None))
                            else {
                                "open_ids": {
                                    str(pid)
                                    for p in broker_positions
                                    for pid in (getattr(p, "position_ids", ()) or
                                                ([getattr(p, "broker_id", "")]
                                                 if getattr(p, "broker_id", "") else []))
                                    if str(pid)
                                },
                                "complete": True,
                                "snapshot_id": (str(broker.portfolio_snapshot_id())
                                                if callable(getattr(
                                                    broker, "portfolio_snapshot_id", None))
                                                else ""),
                            })
                etoro_reconciliation.verify_broker_truth(
                    broker,
                    paper=bool(getattr(broker, "paper", True)),
                    profile=str(getattr(config, "ACTIVE_PROFILE", "") or ""),
                    account_fingerprint=(str(broker.account_fingerprint())
                        if callable(getattr(broker, "account_fingerprint", None)) else ""),
                    position_snapshot=snapshot,
                )
            except Exception as exc:
                # Der aktive Reconciliation-Satz bleibt fail-closed. Der
                # Depotstand selbst wird trotzdem aktualisiert, damit Schutz-
                # und Verkaufspfade nicht wegen eines Diagnosefehlers stoppen.
                logger.warning(
                    "eToro-Depotbeweis konnte Reconciliation nicht aktualisieren: %s",
                    exc)

        current = {}
        for pos in broker_positions:
            qty = _safe_float(pos.quantity)
            if qty == 0:
                continue

            symbol = str(pos.symbol)
            try:
                pkey=canonical_key(symbol, pos.asset_type)
            except Exception:
                pkey=""
            info = by_identity.get(pkey)
            # Brokerpositionen mit unbekannter/mehrdeutiger Identitaet werden
            # sichtbar als OBSERVE uebernommen, aber nie einem anderen Asset
            # nur anhand des Kurztickers zugeordnet.
            pids = list(getattr(pos, "position_ids", ()) or [])
            if not pids and getattr(pos, "broker_id", None):
                pids = [str(pos.broker_id)]
            if len(pids) != 1:
                raise BrokerFehler(
                    f"eToro-Depotzeile {symbol} besitzt nicht genau eine positionId; "
                    "Aktienabgleich bleibt gesperrt")
            environment = str(getattr(pos, "broker_environment", "") or
                              ("DEMO" if bool(getattr(broker, "paper", True)) else "LIVE"))
            account = str(getattr(pos, "account_fingerprint", "") or
                          (broker.account_fingerprint()
                           if callable(getattr(broker, "account_fingerprint", None)) else ""))
            schluessel = f"etoro:{account or 'legacy'}:{environment.lower()}:{pids[0]}"
            # Der Laufzeit-Kontrakt traegt absichtlich den Record-Schluessel,
            # damit zwei Position-Lines desselben Symbols getrennt bleiben.
            kontrakt = _EinfacherKontrakt(symbol, pos.currency, schluessel)

            # Nur eine bereits beobachtete identische positionId migrieren.
            # Symbol-Fallback ist hier verboten.
            existing_key = self._find_key_by_position_ids(
                pids, account_fingerprint=account, broker_environment=environment)
            if existing_key and existing_key != schluessel and schluessel not in self.records:
                self.records[schluessel] = self.records.pop(existing_key)

            current[schluessel] = (kontrakt, qty, _safe_float(pos.avg_cost), info)
            seen_record = self.upsert_existing_position(
                kontrakt, qty, _safe_float(pos.avg_cost),
                currency=pos.currency,
                asset_type=(info.asset_type if info else pos.asset_type),
                schluessel=schluessel, position_ids=pids,
                instrument_id=str(getattr(pos, "instrument_id", "") or ""),
                account_fingerprint=account,
                broker_environment=environment,
                snapshot_id=str(getattr(pos, "snapshot_id", "") or ""),
            )
            if (info is None and seen_record.ownership_chain_complete
                    and account and seen_record.broker_instrument_id
                    and pos.asset_type == "stock"):
                # Offene, belegte Aktien werden auch nach einer Rotation des
                # Einstiegsuniversums weiter ueber den normalen Core verwaltet.
                # Der Adapter prueft erneut die gespeicherte instrumentId.
                from contracts import Instrument, build_stock
                info = Instrument(symbol, build_stock(symbol, currency=pos.currency),
                                  "stock", pos.currency, exchange="ETORO")
                current[schluessel] = (kontrakt, qty, _safe_float(pos.avg_cost), info)
            if seen_record.first_missing_at or seen_record.consecutive_missing_snapshots:
                seen_record.first_missing_at = ""
                seen_record.consecutive_missing_snapshots = 0
        # Positionen, die beim Broker nicht mehr existieren, werden nach einer
        # Karenzzeit entfernt. Ohne Karenz waere das riskant: ein gerade
        # ausgefuehrter Kauf taucht in der naechsten Positionsabfrage evtl.
        # noch nicht auf und wuerde faelschlich geloescht. Ohne Aufraeumen
        # wiederum sammeln sich Karteileichen an (z.B. manuell geschlossene
        # Positionen), die spaetere P&L-Berechnungen verfaelschen.
        karenz = float(getattr(config, "POSITION_STALE_MINUTES", 30)) * 60
        missing_minimum = max(5.0, float(getattr(
            config, "ETORO_POSITION_MISSING_CONFIRM_SECONDS", 20.0)))
        jetzt = datetime.now().astimezone()
        for key in list(self.records):
            if key in current:
                continue
            rec = self.records[key]
            rec.consecutive_missing_snapshots = int(
                rec.consecutive_missing_snapshots or 0) + 1
            if not rec.first_missing_at:
                rec.first_missing_at = _now_iso()
            try:
                missing_age = (jetzt - datetime.fromisoformat(
                    rec.first_missing_at)).total_seconds()
            except Exception:
                missing_age = 0.0
            try:
                position_age = (jetzt - datetime.fromisoformat(
                    rec.entry_time)).total_seconds()
            except Exception:
                position_age = karenz + 1
            if (position_age > karenz
                    and rec.consecutive_missing_snapshots >= 2
                    and missing_age >= missing_minimum):
                logger.info(
                    "Position %s fehlt in %d bestaetigten eToro-Snapshots seit %s -- Eintrag entfernt.",
                    rec.symbol, rec.consecutive_missing_snapshots,
                    rec.first_missing_at,
                )
                self.records.pop(key, None)
        self.save()
        self._schreibe_anzeigedatei(broker)
        return current

    # -- Anzeige ------------------------------------------------------------
    def _schreibe_anzeigedatei(self, broker) -> None:
        """Eine Momentaufnahme fuer die Oberflaeche (neu in v8.1.5).

        Das Dashboard hat keine Brokerverbindung -- es liest Dateien. Bis
        v8.1.4 gab es fuer die Aktienseite keine, und deshalb stand auf der
        Hauptseite ZWEIMAL die Zahl der OKX-Positionen und KEIN EINZIGES MAL
        die der eToro-Positionen. Genau das hat Georg am 25.08.2026 gemeldet.

        Der Kryptoseite ihr Gegenstueck ist ``crypto_positions.json``.
        Best-effort: eine fehlende Anzeigedatei darf nie den Handel stoeren.
        """
        try:
            aktuelle_kurse = {}
            for pos in broker.positionen():
                ids = [str(x) for x in (getattr(pos, "position_ids", ()) or [])]
                kursdaten = {
                    "kurs": _safe_float(getattr(pos, "market_price", 0.0)),
                    "wert": _safe_float(getattr(pos, "market_value", 0.0)),
                    # v9.1: Fehlt der Wert beim Broker, bleibt er UNBEKANNT.
                    # _safe_float(None) waere 0.0 -- und 0,00 sieht aus wie
                    # ausgeglichen, nicht wie "nicht ermittelbar".
                    "unrealisiert": (_safe_float(getattr(pos, "unrealized_pnl", None))
                                     if getattr(pos, "unrealized_pnl", None) is not None
                                     else None),
                    "broker_stop": getattr(pos, "broker_stop", None),
                    "broker_take": getattr(pos, "broker_take_profit", None),
                    # v9.3: Woher der Kurs stammt und wie alt er ist.
                    "kursquelle": str(getattr(pos, "price_source", "") or ""),
                    "kursalter": getattr(pos, "price_age_seconds", None),
                }
                for pid in ids:
                    aktuelle_kurse[f"pid:{pid}"] = kursdaten
                if not ids:
                    # Legacy-/Diagnosepositionen ohne eToro-ID koennen nur
                    # angezeigt, niemals automatisch verwaltet werden.
                    aktuelle_kurse[f"symbol:{str(pos.symbol).upper()}"] = kursdaten
        except Exception:
            logger.debug("Kurse fuer die Anzeigedatei nicht abrufbar", exc_info=True)
            aktuelle_kurse = {}

        positionen = []
        for record_id, rec in self.records.items():
            pids = sorted(rec.position_id_set())
            kurs = (aktuelle_kurse.get(f"pid:{pids[0]}", {}) if len(pids) == 1
                    else aktuelle_kurse.get(f"symbol:{str(rec.symbol).upper()}", {}))
            positionen.append({
                "record_id": str(record_id),
                "symbol": rec.symbol,
                "asset_type": rec.asset_type,
                "waehrung": rec.currency,
                "menge": _safe_float(rec.quantity),
                "einstand": _safe_float(rec.avg_cost),
                # None statt 0, wenn der Kurs fehlt: 0 saehe aus wie ein
                # Totalverlust und waere eine Falschaussage.
                "kurs": kurs.get("kurs") or None,
                "wert": kurs.get("wert") or None,
                # v9.1: ``or None`` wie bei Kurs und Wert. _safe_float(None)
                # liefert 0.0 -- ein UNBEKANNTES Ergebnis wurde damit als
                # 0,00 gebucht, und der Hinweis "X Positionen ohne Kurs"
                # erschien nie. Genau das verbietet der Kommentar oben.
                "unrealisiert": kurs.get("unrealisiert") if kurs else None,
                "stop": (kurs.get("broker_stop") if str(rec.management_mode or "").upper() == "OBSERVE"
                         else (_safe_float(rec.planned_stop) or None)),
                "ziel": (kurs.get("broker_take") if str(rec.management_mode or "").upper() == "OBSERVE"
                         else (_safe_float(rec.planned_take) or None)),
                "schutz_quelle": ("eToro read-only" if str(rec.management_mode or "").upper() == "OBSERVE"
                                    and (kurs.get("broker_stop") is not None or kurs.get("broker_take") is not None)
                                    else "lokaler Plan" if str(rec.management_mode or "").upper() == "AUTO" else "unbekannt"),
                "kursquelle": kurs.get("kursquelle") or "",
                "kursalter": kurs.get("kursalter") if kurs else None,
                "broker_stop": kurs.get("broker_stop"),
                "broker_take_profit": kurs.get("broker_take"),
                "planned_stop": _safe_float(rec.planned_stop) or None,
                "planned_take_profit": _safe_float(rec.planned_take) or None,
                "verwaltung": str(rec.management_mode or "").upper(),
                "herkunft": str(rec.source or "").upper(),
                "seit": rec.entry_time,
                "notiz": str(getattr(rec, "management_note", "") or ""),
                # v9.3: Eigentum, Abgleich und Verwaltung getrennt -- wie auf
                # der Kryptoseite seit 9.2. Ein Symbol- oder Mengentreffer
                # taucht hier bewusst nicht auf.
                "eigentum": str(getattr(rec, "ownership_status", "") or ""),
                "abgleich": str(getattr(rec, "reconciliation_status", "") or ""),
                "protection_status": rec.protection_status,
                "protection_detail": rec.protection_detail,
                "protection_plan_history": list(rec.protection_plan_history),
                "position_ids": pids,
                "owned_position_ids": sorted(rec.owned_position_id_set()),
                "instrument_id": str(rec.broker_instrument_id or ""),
                "account_fingerprint": str(rec.broker_account_fingerprint or ""),
                "broker_environment": str(rec.broker_environment or ""),
                "snapshot_id": str(rec.broker_snapshot_id or ""),
                "order_ids": [str(x) for x in (rec.entry_order_ids or [])],
            })
        positionen.sort(key=lambda x: str(x["symbol"]))
        best_effort_json(
            self.path.with_name("stock_positions.json"),
            {"updated_at": _now_iso(), "kurse_verfuegbar": bool(aktuelle_kurse),
             "positionen": positionen},
            label="Aktien-Anzeigedatei")

    def is_auto_managed(self, contract_or_symbol) -> bool:
        rec = self.get_by_symbol(contract_or_symbol) if isinstance(contract_or_symbol, str) else self.get(contract_or_symbol)
        return bool(rec and str(rec.management_mode).upper()=="AUTO")

    def set_observe_only(self, symbol: str, note: str = "Vom Nutzer auf Beobachten gesetzt") -> bool:
        _, rec = self._record_for_action(symbol)
        if not rec:return False
        rec.management_mode="OBSERVE"; rec.management_note=note
        rec.last_manual_change=_now_iso(); rec.user_observe_locked=True
        self.save(); return True

    def request_takeover(self, symbol: str, stop: float, take: float,
                         aktueller_kurs: float | None = None) -> tuple[bool, str]:
        """Eine selbst gekaufte Position dem Bot uebergeben.

        GEAENDERT IN v8.1.5 -- der alte Test war
        ``stop < rec.avg_cost < take``, also gemessen am EINSTAND.

        Georg am 25.08.2026: "Zusaetzlich wollte ich heute in der GUI eine
        aktie die ich selber gekauft habe dem Bot uebergeben das er sie
        handeln soll. die funktion gibt es dort ja aber es wurde blockiert
        weil sie zu sehr im minus war."

        Er hat recht, und der alte Test war schlicht falsch gedacht. Bei einer
        Position, die 30 % im Minus steht, liegt der Einstand WEIT ueber dem
        aktuellen Kurs. Ein Stop unter dem Einstand haette dann ueber dem
        Marktpreis gelegen und sofort ausgeloest; ein Ziel ueber dem Einstand
        waere in unerreichbarer Ferne. Der Einstand ist Vergangenheit -- er
        sagt nichts darueber, wo Stop und Ziel sinnvoll liegen.

        Massgeblich ist der AKTUELLE KURS:

            Stop  <  aktueller Kurs  <  Ziel

        Zusaetzlich muss der Stop weiter weg sein als die Handelskosten der
        Runde; sonst frisst ein Stop, der bei der ersten Zuckung greift,
        garantiert Geld. Das ist dieselbe Regel wie beim Bot-Einstieg.

        Was sich NICHT aendert: Die Uebernahme setzt nur eine Anforderung.
        Handelbar wird die Position erst, wenn der laufende Kern den
        Broker-Schutz tatsaechlich bestaetigt hat (``confirm_takeover``).
        """
        _, rec = self._record_for_action(symbol)
        if not rec:
            return False, "Position nicht eindeutig gefunden"
        if (rec.broker_environment and len(rec.position_id_set()) != 1):
            return False, "Uebernahme blockiert: keine eindeutige eToro-positionId"

        stop = _safe_float(stop)
        take = _safe_float(take)
        kurs = _safe_float(aktueller_kurs) if aktueller_kurs is not None else 0.0
        if kurs <= 0:
            # Ohne aktuellen Kurs wird NICHT auf den Einstand ausgewichen --
            # genau das war der Fehler. Lieber ehrlich ablehnen.
            return False, ("Aktueller Kurs unbekannt. Ohne ihn lassen sich Stop "
                           "und Ziel nicht sinnvoll pruefen; bitte erneut "
                           "versuchen, sobald Kursdaten anliegen.")
        if stop <= 0 or take <= 0:
            return False, "Stop und Ziel muessen groesser als null sein"
        if stop >= kurs:
            return False, (f"Der Stop ({stop:.6g}) liegt nicht unter dem aktuellen "
                           f"Kurs ({kurs:.6g}) -- er wuerde sofort ausloesen")
        if take <= kurs:
            return False, (f"Das Ziel ({take:.6g}) liegt nicht ueber dem aktuellen "
                           f"Kurs ({kurs:.6g}) -- es waere schon erreicht")

        mindestabstand = self._stop_mindestabstand(rec, kurs)
        if (kurs - stop) < mindestabstand:
            return False, (f"Der Stop ist zu eng: {kurs - stop:.6g} Abstand, "
                           f"noetig sind mindestens {mindestabstand:.6g} "
                           "(Kauf- und Verkaufsgebuehren der Runde). Ein engerer "
                           "Stop verliert im Auslosefall garantiert Geld.")

        rec.takeover_previous_source = str(rec.source or "")
        rec.takeover_previous_mode = str(rec.management_mode or "")
        rec.takeover_previous_stop = _safe_float(rec.planned_stop)
        rec.takeover_previous_take = _safe_float(rec.planned_take)
        rec.takeover_previous_note = str(rec.management_note or "")
        rec.planned_stop = stop
        rec.planned_take = take
        rec.management_mode = "PENDING_TAKEOVER"
        hinweis = ""
        if rec.avg_cost > 0 and kurs < rec.avg_cost:
            offen = (kurs - rec.avg_cost) * _safe_float(rec.quantity)
            beim_stop = (stop - rec.avg_cost) * _safe_float(rec.quantity)
            hinweis = (f" Hinweis: Die Position steht mit {offen:,.2f} "
                       f"{rec.currency} im Minus. Greift der Stop, wird ein "
                       f"Verlust von rund {beim_stop:,.2f} {rec.currency} "
                       "realisiert.")
        rec.management_note = ("Uebernahme angefordert; wartet auf "
                               "Broker-Schutzpruefung." + hinweis)
        self.save()
        return True, rec.management_note

    @staticmethod
    def _stop_mindestabstand(rec, kurs: float) -> float:
        """Wie weit muss ein Stop mindestens weg sein, um nicht zu schaden?

        Ein Stop innerhalb der Handelskosten ist kein Schutz: er loest im
        Rauschen aus und verliert dabei sicher die Gebuehren beider Seiten.
        """
        try:
            from cost_engine import estimate_roundtrip
            kosten = estimate_roundtrip(
                _safe_float(rec.quantity), kurs, rec.asset_type, rec.currency,
                broker=None)
            gesamt = _safe_float(getattr(kosten, "total_cost", 0.0))
            menge = _safe_float(rec.quantity)
            if gesamt > 0 and menge > 0:
                faktor = float(getattr(config, "CRYPTO_STOP_KOSTEN_FAKTOR", 1.5))
                return (gesamt / menge) * max(1.0, faktor)
        except Exception:
            logger.debug("Kostenabstand nicht berechenbar", exc_info=True)
        # Sicherer Ersatzwert, wenn die Kostenschaetzung nicht laeuft.
        return abs(kurs) * 0.002

    def confirm_takeover(self, symbol: str, detail: str = "Schutz geprueft") -> bool:
        _, rec = self._record_for_action(symbol)
        if not rec:return False
        rec.management_mode="AUTO"; rec.source="USER_MANAGED"
        rec.owned_position_ids=sorted(rec.position_id_set())
        rec.ownership_status="USER_AUTHORIZED"
        rec.management_note=str(detail or "Schutz geprueft")
        rec.last_manual_change=_now_iso(); rec.user_observe_locked=False
        rec.takeover_previous_source=""; rec.takeover_previous_mode=""
        rec.takeover_previous_stop=0.0; rec.takeover_previous_take=0.0
        rec.takeover_previous_note=""
        self.save(); return True

    def reject_takeover(self, symbol: str, detail: str) -> bool:
        _, rec = self._record_for_action(symbol)
        if not rec:return False
        rec.management_mode=(rec.takeover_previous_mode or "OBSERVE")
        rec.source=(rec.takeover_previous_source or rec.source or "BROKER_EXISTING")
        rec.planned_stop=_safe_float(rec.takeover_previous_stop)
        rec.planned_take=_safe_float(rec.takeover_previous_take)
        vorher=rec.takeover_previous_note
        rec.management_note=(str(detail or "Uebernahme nicht moeglich")
                             + (f" Vorher: {vorher}" if vorher else ""))
        rec.takeover_previous_source=""; rec.takeover_previous_mode=""
        rec.takeover_previous_stop=0.0; rec.takeover_previous_take=0.0
        rec.takeover_previous_note=""
        # Fehlgeschlagene Uebernahme ist kein Nutzerwunsch "nur beobachten"
        # und darf die automatische Nachzuordnung eines eigenen Kaufs nicht
        # dauerhaft blockieren.
        self.save(); return True

    def set_bot_protection(self, record_id: str, confirmed: bool,
                           detail: str = "") -> bool:
        """Eigene Position nur mit exakt bestaetigtem Schutz auf AUTO setzen."""
        rec = self.get_by_record_id(record_id) or self.get(record_id)
        if (rec is None or str(rec.source or "").upper() != "BOT"
                or rec.user_observe_locked):
            return False
        if not rec.ownership_chain_complete:
            confirmed = False
        before = asdict(rec)
        rec.protection_status = "ACTIVE" if confirmed else "UNCONFIRMED"
        rec.management_mode = "AUTO" if confirmed else "PENDING_CONFIRMATION"
        rec.management_note = str(detail or (
            "Broker-Schutz exakt bestaetigt" if confirmed else
            "Broker-Schutz nicht exakt bestaetigt; Automatik pausiert"))[:300]
        rec.protection_detail = rec.management_note
        if asdict(rec) != before:
            self.save()
        return True

    def portfolio_text(self, current_positions, title="DEPOT-STATUS"):
        lines = [str(title), f"Profil: {getattr(config, 'ACTIVE_PROFILE', 'unbekannt').upper()}"]
        if not current_positions:
            lines.append("Keine offenen Positionen.")
            return "\n".join(lines)
        lines.append(f"Offene Positionen: {len(current_positions)}")
        for _, (contract, qty, avg_cost, inst) in sorted(
                current_positions.items(), key=lambda x: getattr(x[1][0], 'symbol', '')):
            name = getattr(contract, "localSymbol", None) or getattr(contract, "symbol", "?")
            cur = getattr(contract, "currency", "")
            rec=self.get_by_symbol(name)
            owner=(getattr(rec,"source","") if rec else "")
            mode=(getattr(rec,"management_mode","") if rec else "")
            suffix=f" | {owner}/{mode}" if owner or mode else ""
            lines.append(f"• {name} | {qty:g} Stk. | EK {fmt_price(avg_cost)} {cur}{suffix}")
        return "\n".join(lines)
