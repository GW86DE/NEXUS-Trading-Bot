"""Positionsgroesse und Risikogrenzen mit persistentem Tages-/Gesamtrisiko.

v5.2:
- Tagesverlustlimit, Trade-Zähler, Verlustserien-Cooldown und Kostenstatus
  werden atomar in risk_state.json gespeichert und überleben Neustarts.
- Bei beschädigter Risk-Datei fällt der Bot auf die sichere Seite und blockiert
  neue Käufe für den aktuellen Tag.
- Zusätzlich werden kumulierte realisierte Gewinne/Verluste für das Dashboard
  gespeichert.
"""
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from copy import deepcopy
import json
import logging
import math
import os
import hashlib
import config
from safe_persistence import atomic_write_json, atomic_write_text
from state_lock import critical_state_lock

logger = logging.getLogger(__name__)


def _equity_basis_identity(value: str) -> str:
    """v2-Symbollisten sind Positionen, keine neue Bewertungsmethode."""
    key = str(value or "").strip()[:500]
    parts = key.split(":")
    if len(parts) >= 4 and parts[:2] == ["handelbares_kapital", "v2"]:
        return ":".join(["handelbares_kapital", "v3", parts[2].lower(), parts[3].upper()])
    return key


def _risk_operation_key(kind: str, *identity) -> str:
    material = json.dumps(identity, sort_keys=True, separators=(",", ":"), default=str)
    return f"{kind}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def _backup_legacy_risk(target: Path, original: str) -> Path:
    """Vor der additiven v10-Migration exakt einen inhaltsadressierten Beleg sichern."""
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    backup = target.with_name(f"{target.name}.pre-v10-{digest}.bak")
    if backup.exists():
        if backup.read_text(encoding="utf-8") != original:
            raise RuntimeError("Risikomigrations-Backup widerspricht seinem Inhaltshash")
    # An existing backup might be the visible result of a previous failed
    # directory-fsync in this process. Reconfirm its durability before use.
    atomic_write_text(backup, original)
    return backup


def _handelstag_heute() -> date:
    """Kanonischer Handelstag in der vom Nutzer eingestellten Zeitzone.

    Tageslimits muessen zusammen mit Logbuch, Telegram und WebUI um lokale
    Mitternacht wechseln. UTC fuehrte in Deutschland zu einem Reset um 02:00
    bzw. 01:00 Uhr und damit zu widerspruechlichen Tagesergebnissen.
    """
    try:
        zone = ZoneInfo(str(getattr(config, "LOCAL_TIMEZONE", "Europe/Berlin")))
        return datetime.now(zone).date()
    except Exception:
        logger.warning("LOCAL_TIMEZONE ungueltig; lokaler Systemtag wird verwendet")
        return date.today()


def _utc_heute() -> date:
    """Rueckwaertskompatibler Name; liefert ab 9.0.14 den lokalen Handelstag."""
    return _handelstag_heute()


def _gueltige_heutige_daten() -> set[date]:
    """Nur der lokale Handelstag ist fuer Tageslimits gueltig."""
    return {_handelstag_heute()}


def _zustandswurzel() -> Path:
    """Wo die Zustandsdateien liegen.

    Offline- und Integrationstests duerfen niemals den echten Handelszustand
    veraendern; volltest.py und die Testskripte setzen dafuer
    ``TRADINGBOT_TEST_STATE_DIR``.
    """
    test_dir = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return Path(test_dir) if test_dir else Path(__file__).resolve().parent



@dataclass
class RiskState:
    current_date: date = field(default_factory=_handelstag_heute)
    realized_pnl_today: float = 0.0
    open_positions: int = 0
    trading_halted: bool = False
    crypto_exposure: float = 0.0
    consecutive_losses: int = 0
    cooldown_until: datetime | None = None
    trades_today: int = 0
    estimated_costs_today: float = 0.0
    gross_profit_today: float = 0.0
    gross_loss_today: float = 0.0
    net_profit_today: float = 0.0
    net_loss_today: float = 0.0
    unknown_pnl_trades_today: int = 0
    # Zweite Tagesbremse: Equity-Verlust inkl. unrealisierter P&L. Sie blockiert
    # ausschliesslich neue Kaeufe und laesst Schutz-/Verkaufslogik weiterlaufen.
    day_start_equity: float = 0.0
    last_equity: float = 0.0
    equity_guard_halted: bool = False
    equity_drawdown_pct: float = 0.0
    # 10.1.10: Bei genau einer finanzierten Fremdwaehrung (z. B. reines
    # USDC-Konto mit EUR-Risikobasis) misst der Basis-Drawdown vor allem den
    # Wechselkurs. Die native Reihe misst Handelsverluste in der finanzierten
    # Waehrung; liegt sie vor, entscheidet SIE ueber die Tagesbremse. Der
    # FX-Anteil bleibt getrennt sichtbar und loest keine Bremse aus.
    day_start_native_equity: float = 0.0
    last_native_equity: float = 0.0
    native_equity_ccy: str = ""
    native_drawdown_pct: float = 0.0
    fx_drawdown_pct: float = 0.0
    # 10.2.0: Bei MEHREREN finanzierten Waehrungs-Lanes (z. B. USDC+USD) wird
    # die native Reihe als Summe der Lanes mit am Tagesstart eingefrorenen
    # Umrechnungskursen gefuehrt. Ein Lane-Set-Wechsel ist ein Basis-Ereignis
    # und startet die Tagesbasis neu.
    native_fx_day_start: dict = field(default_factory=dict)
    # Kauf/Verkauf aendert die Zusammensetzung, niemals die Tagesbasis.
    # Echte Waehrungs-/Methoden-/Kontowechsel benoetigen einen belegten Abgleich.
    equity_basis_key: str = ""
    equity_basis_changed_at: str = ""
    equity_basis_review_required: bool = False
    equity_basis_review_reason: str = ""
    risk_scope_key: str = ""
    risk_scope_origin: str = ""
    # A narrowly verified metadata repair never replaces financial receipts.
    basis_review_receipt: dict = field(default_factory=dict)
    # Historical unknown results remain in the lifetime book. A verified
    # forward-period receipt determines which belong to the active buy gate.
    result_period_scope: dict = field(default_factory=dict)
    pending_persistence_operations: list[str] = field(default_factory=list)
    # Eine Broker-Order kann in mehrere Teil-Fills zerfallen. Der Tages-Tradezaehler
    # zaehlt die logische Verkaufsorder nur einmal; P&L bleibt fill-genau.
    counted_trade_ids_today: list[str] = field(default_factory=list)
    # Durable economic receipts must survive midnight and process restarts.
    # This is a projection receipt, not a second source of trade prices/fees.
    realized_receipts: dict = field(default_factory=dict)
    counted_cost_ids_today: list[str] = field(default_factory=list)
    unknown_pnl_ids_today: list[str] = field(default_factory=list)

    # Kumulierte Werte; werden bei Datumswechsel NICHT zurückgesetzt.
    lifetime_realized_pnl: float = 0.0
    lifetime_gross_profit: float = 0.0
    lifetime_gross_loss: float = 0.0
    lifetime_estimated_costs: float = 0.0
    lifetime_net_profit: float = 0.0
    lifetime_net_loss: float = 0.0
    lifetime_unknown_pnl_trades: int = 0
    risk_schema_version: int = 2

    @staticmethod
    def default_path(path=None) -> Path:
        """Der Pfad der Zustandsdatei -- immer absolut.

        v8.1.5: Ein RELATIVER Name wurde hier frueher unveraendert
        zurueckgegeben und loeste damit gegen das ARBEITSVERZEICHNIS auf.
        Beim Start als systemd-Dienst ist das ein anderer Ordner als das
        Botverzeichnis -- die Tagesbremse haette dort nach jedem Neustart bei
        null angefangen, ohne dass es jemand bemerkt. ``risk_pots`` hatte
        genau deshalb schon eine eigene Wurzelaufloesung; jetzt gilt sie
        ueberall.
        """
        wurzel = _zustandswurzel()
        if path is not None:
            kandidat = Path(path)
            return kandidat if kandidat.is_absolute() else wurzel / kandidat.name
        configured = Path(getattr(config, "RISK_STATE_FILE", "risk_state_etoro.json"))
        # Ein Test, der absichtlich einen absoluten Temp-Pfad in config setzt,
        # muss genau diesen Pfad testen koennen.
        if configured.is_absolute():
            return configured
        return wurzel / configured.name

    def _payload(self) -> dict:
        data = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, date) and not isinstance(value, datetime):
                data[f.name] = value.isoformat()
            elif isinstance(value, datetime):
                data[f.name] = value.isoformat()
            else:
                data[f.name] = value
        return data

    def _target(self, path=None) -> Path:
        """An die beim Laden gewaehlte Brokerdatei gebunden bleiben.

        Bis 9.4.1 sprang ein mit ``risk_state_okx.json`` geladenes Objekt bei
        seinem naechsten internen ``save()`` auf den globalen eToro-Pfad
        zurueck. Damit konnten OKX-Ergebnisse im falschen Risikotopf landen.
        """
        if path is not None:
            target = self.default_path(path)
            self._bound_path = str(target)
            return target
        bound = str(getattr(self, "_bound_path", "") or "")
        return Path(bound) if bound else self.default_path()

    @classmethod
    def _decode_payload(cls, raw: dict) -> dict:
        if not isinstance(raw, dict):
            raise ValueError("RiskState muss ein JSON-Objekt sein")
        allowed = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in raw.items() if k in allowed}
        version = raw.get("risk_schema_version", 1)
        if isinstance(version, bool) or version not in {1, 2}:
            raise ValueError("Unbekannte RiskState-Schemaversion")
        for name in ("day_start_equity", "last_equity", "equity_drawdown_pct",
                     "realized_pnl_today", "lifetime_realized_pnl"):
            if name in kwargs:
                value = kwargs[name]
                if (isinstance(value, bool) or not isinstance(value, (int, float))
                        or not math.isfinite(value)):
                    raise ValueError(f"Ungueltiger Risikowert: {name}")
                if name in {"day_start_equity", "last_equity"} and value < 0:
                    raise ValueError(f"Negativer Equitywert: {name}")
        for name in ("trading_halted", "equity_guard_halted", "equity_basis_review_required"):
            if name in kwargs and not isinstance(kwargs[name], bool):
                raise ValueError(f"Ungueltiger Risikostatus: {name}")
        receipts = kwargs.get("realized_receipts", {})
        if not isinstance(receipts, dict) or any(not isinstance(v, dict) for v in receipts.values()):
            raise ValueError("Ungueltige Risikobelege")
        if not isinstance(kwargs.get("basis_review_receipt", {}), dict):
            raise ValueError("Ungueltiger Risikobasis-Migrationsbeleg")
        if not isinstance(kwargs.get("result_period_scope", {}), dict):
            raise ValueError("Ungueltiger Ergebnisperiodenbeleg")
        fx_start = kwargs.get("native_fx_day_start", {})
        if (not isinstance(fx_start, dict)
                or any(isinstance(v, bool) or not isinstance(v, (int, float))
                       or not math.isfinite(v) or v <= 0 for v in fx_start.values())):
            raise ValueError("Ungueltige native Tagesstartkurse")
        pending = kwargs.get("pending_persistence_operations", [])
        if not isinstance(pending, list) or any(not isinstance(v, str) for v in pending):
            raise ValueError("Ungueltige Persistenzoperationen")
        if isinstance(kwargs.get("current_date"), str):
            kwargs["current_date"] = date.fromisoformat(kwargs["current_date"])
        if isinstance(kwargs.get("cooldown_until"), str) and kwargs["cooldown_until"]:
            kwargs["cooldown_until"] = datetime.fromisoformat(kwargs["cooldown_until"])
        elif kwargs.get("cooldown_until") in ("", None):
            kwargs["cooldown_until"] = None
        return kwargs

    def _replace_payload(self, raw: dict) -> None:
        fresh = type(self)(**self._decode_payload(raw))
        bound = str(getattr(self, "_bound_path", "") or "")
        pending = list(getattr(self, "pending_persistence_operations", []))
        for f in fields(self):
            setattr(self, f.name, deepcopy(getattr(fresh, f.name)))
        if bound:
            self._bound_path = bound
        # A disk read cannot erase an unconfirmed local operation. Another
        # successful projection may persist these markers for restart recovery.
        self.pending_persistence_operations = sorted(set(pending) | set(self.pending_persistence_operations))

    def persistence_failure_reason(self) -> str:
        """Ein blosses Lesen quittiert keinen zuvor fehlgeschlagenen Write."""
        if self.pending_persistence_operations:
            return "RISK_PERSISTENCE_UNCONFIRMED"
        return str(getattr(self, "_persistence_error", "") or "")

    def _mark_persistence_failure(self, operation: str, exc: Exception) -> None:
        pending = set(self.pending_persistence_operations)
        if len(pending) < 256:
            pending.add(operation or "UNCLASSIFIED_REVIEW_REQUIRED")
        else:
            pending.add("OVERFLOW_REVIEW_REQUIRED")
        self.pending_persistence_operations = sorted(pending)
        self._persistence_error = f"RISK_PERSISTENCE_UNCONFIRMED: {type(exc).__name__}"

    def refresh(self) -> None:
        """Aktuellen Brokerzustand laden, ohne einen alten Stand zu schreiben."""
        target = self._target()
        try:
            with critical_state_lock(target):
                if not target.exists():
                    raise RuntimeError(f"Risikozustand fehlt: {target}")
                self._replace_payload(json.loads(target.read_text(encoding="utf-8")))
                self.reset_if_new_day(persist=False)
        except Exception as exc:
            self._persistence_error = f"RISK_STATE_UNREADABLE: {type(exc).__name__}"
            raise

    def _transaction(self, callback, *, recovery_keys=()):
        """Lese-Aendern-Schreib atomar ueber Threads UND Prozesse.

        Ein atomisches Dateireplace verhindert nur halbe Dateien. Ohne dieses
        erneute Laden unter derselben Sperre konnte ein alter Scannerzustand
        eine parallel verbuchte Broker-Ausfuehrung wieder ueberschreiben.
        """
        target = self._target()
        before = deepcopy(self.__dict__)
        try:
            with critical_state_lock(target):
                if target.exists():
                    raw = json.loads(target.read_text(encoding="utf-8"))
                    if int(raw.get("risk_schema_version", 1)) < 2:
                        raise RuntimeError("RISK_MIGRATION_REQUIRED: Sicherung und Migration muessen zuerst erfolgreich geladen werden")
                    self._replace_payload(raw)
                self.reset_if_new_day(persist=False)
                result = callback()
                recovered = set(recovery_keys) & set(self.pending_persistence_operations)
                self.pending_persistence_operations = [key for key in self.pending_persistence_operations
                                                       if key not in recovered]
                atomic_write_json(target, self._payload(), retries=8)
                # Success of a different mutation is not a receipt for the
                # failed one. Only the qualified, idempotent replay clears it.
                if recovered and not self.pending_persistence_operations:
                    self._persistence_error = ""
                return result
        except Exception as exc:
            self._rollback(before)
            self._mark_persistence_failure(next(iter(recovery_keys), ""), exc)
            logger.error("Risikotransaktion fehlgeschlagen (%s): %s", target, exc)
            raise RuntimeError(
                f"Risikozustand konnte nicht dauerhaft aktualisiert werden: {target}"
            ) from exc

    def save(self, path=None) -> None:
        target = self._target(path)
        try:
            with critical_state_lock(target):
                if target.exists():
                    raw = json.loads(target.read_text(encoding="utf-8"))
                    if raw.get("risk_schema_version", 1) != 2:
                        raise RuntimeError("RISK_MIGRATION_REQUIRED: Fremdes Schema darf nicht ohne gepruefte Migration ersetzt werden")
                atomic_write_json(target, self._payload(), retries=8)
        except Exception as exc:
            self._mark_persistence_failure("UNCLASSIFIED_SAVE_REVIEW_REQUIRED", exc)
            logger.error("RiskState konnte nicht gespeichert werden: %s", exc)
            raise RuntimeError(
                f"Risikozustand konnte nicht dauerhaft gespeichert werden: {target}"
            ) from exc

    def _rollback(self, before: dict) -> None:
        self.__dict__.clear()
        self.__dict__.update(deepcopy(before))

    def _save_or_rollback(self, before: dict) -> None:
        try:
            self.save()
        except Exception:
            pending = list(self.pending_persistence_operations)
            failure = self._persistence_error
            self._rollback(before)
            self.pending_persistence_operations = sorted(set(self.pending_persistence_operations) | set(pending))
            self._persistence_error = failure
            raise

    @classmethod
    def load(cls, path=None):
        target = cls.default_path(path)
        if not target.exists():
            obj = cls()
            obj._bound_path = str(target)
            obj.save(target)
            return obj
        try:
            original = target.read_text(encoding="utf-8")
            raw = json.loads(original)
            kwargs = cls._decode_payload(raw)
            obj = cls(**kwargs)
            obj._bound_path = str(target)
            if int(raw.get("risk_schema_version", 1)) < 2:
                with critical_state_lock(target):
                    # Do not overwrite a concurrent receipt with the first read.
                    original = target.read_text(encoding="utf-8")
                    raw = json.loads(original)
                    obj._replace_payload(raw)
                    if int(raw.get("risk_schema_version", 1)) < 2:
                        _backup_legacy_risk(target, original)
                        obj.risk_schema_version = 2
                        obj.equity_basis_key = _equity_basis_identity(obj.equity_basis_key)
                        obj.risk_scope_origin = "LEGACY_UNASSIGNED"
                        obj.reset_if_new_day(persist=False)
                        atomic_write_json(target, obj._payload())
            obj.reset_if_new_day(persist=False)

            # v5.2/v5.2.1 konnten einen Verkauf ohne gefundenen Einstand als
            # exakt 0,00 P&L verbuchen. Ein alter State mit Trades + Kosten,
            # aber komplett leeren P&L-Buckets ist deshalb nicht vertrauenswuerdig.
            # Wir erfinden keinen Betrag, sondern markieren den historischen
            # Tages-P&L als unvollstaendig.
            legacy_unknown_missing = "unknown_pnl_trades_today" not in raw
            legacy_zero_pnl = (
                int(getattr(obj, "trades_today", 0) or 0) > 0
                and float(getattr(obj, "estimated_costs_today", 0) or 0) > 0
                and abs(float(getattr(obj, "realized_pnl_today", 0) or 0)) < 1e-12
                and abs(float(getattr(obj, "gross_profit_today", 0) or 0)) < 1e-12
                and abs(float(getattr(obj, "gross_loss_today", 0) or 0)) < 1e-12
                and abs(float(getattr(obj, "net_profit_today", 0) or 0)) < 1e-12
                and abs(float(getattr(obj, "net_loss_today", 0) or 0)) < 1e-12
            )
            if legacy_unknown_missing and legacy_zero_pnl:
                obj.unknown_pnl_trades_today = max(
                    int(getattr(obj, "unknown_pnl_trades_today", 0) or 0),
                    int(getattr(obj, "trades_today", 0) or 0),
                )
                obj.lifetime_unknown_pnl_trades = max(
                    int(getattr(obj, "lifetime_unknown_pnl_trades", 0) or 0),
                    obj.unknown_pnl_trades_today,
                )
            # Re-read under the transaction lock before any normal save.
            # A late legacy-unknown classification remains monotone.
            required_today = obj.unknown_pnl_trades_today
            required_lifetime = obj.lifetime_unknown_pnl_trades
            def preserve_unknown():
                obj.unknown_pnl_trades_today = max(obj.unknown_pnl_trades_today, required_today)
                obj.lifetime_unknown_pnl_trades = max(obj.lifetime_unknown_pnl_trades, required_lifetime)
            obj._transaction(preserve_unknown)
            return obj
        except Exception as exc:
            logger.exception("RiskState beschädigt/unlesbar: %s", exc)
            # Fail-safe: Keine neuen Käufe bis der Zustand bewusst geprüft wird.
            obj = cls(trading_halted=True)
            obj._bound_path = str(target)
            # Keep the original evidence. Never replace damaged/unconfirmed
            # state with zero counters while claiming a successful recovery.
            obj._persistence_error = f"RISK_STATE_UNREADABLE: {type(exc).__name__}"
            return obj

    def reset_if_new_day(self, persist=True):
        if persist:
            return self._transaction(lambda: self.reset_if_new_day(persist=False),
                                     recovery_keys=("DAILY_RESET",))
        before = deepcopy(self.__dict__) if persist else None
        today = _handelstag_heute()
        if self.current_date not in _gueltige_heutige_daten():
            self.current_date = today
            self.realized_pnl_today = 0.0
            self.trading_halted = False
            self.consecutive_losses = 0
            self.cooldown_until = None
            self.trades_today = 0
            self.estimated_costs_today = 0.0
            self.gross_profit_today = 0.0
            self.gross_loss_today = 0.0
            self.net_profit_today = 0.0
            self.net_loss_today = 0.0
            self.unknown_pnl_trades_today = 0
            self.day_start_equity = 0.0
            self.last_equity = 0.0
            self.equity_guard_halted = False
            self.equity_drawdown_pct = 0.0
            self.day_start_native_equity = 0.0
            self.last_native_equity = 0.0
            self.native_drawdown_pct = 0.0
            self.fx_drawdown_pct = 0.0
            # 10.2.0: neue Tagesstartkurse werden bei der ersten Beobachtung
            # des Tages frisch eingefroren.
            self.native_fx_day_start = {}
            self.counted_trade_ids_today = []
            self.counted_cost_ids_today = []
            self.unknown_pnl_ids_today = []
        return None

    def _count_logical_trade(self, trade_id=None) -> bool:
        """Zaehlt eine logische Verkaufsorder hoechstens einmal pro Tag."""
        if not trade_id:
            self.trades_today += 1
            return True
        key = str(trade_id)[:200]
        if key in self.counted_trade_ids_today:
            return False
        self.counted_trade_ids_today.append(key)
        # Begrenzen, damit eine kaputte Fremdquelle die State-Datei nicht aufblaeht.
        if len(self.counted_trade_ids_today) > 1000:
            self.counted_trade_ids_today = self.counted_trade_ids_today[-1000:]
        self.trades_today += 1
        return True

    def register_realized_pnl(self, pnl, account_equity, gross_pnl=None, trade_id=None,
                              max_daily_loss_pct=None):
        pnl = float(pnl)
        gross = float(pnl if gross_pnl is None else gross_pnl)

        def change():
            return self._register_realized_pnl_unlocked(
                pnl, account_equity, gross, trade_id, max_daily_loss_pct)
        keys = ((_risk_operation_key("REALIZED", str(trade_id), pnl, gross),
                 _risk_operation_key("UNKNOWN", str(trade_id))) if trade_id else ())
        return self._transaction(change, recovery_keys=keys)

    def resolve_unknown_result_at(self, trade_id, pnl, gross_pnl, account_equity, executed_at, *, evidence=None, settlement_evidence=None):
        """Complete an UNKNOWN receipt on its proven execution day, not read day.

        Only the newly verified closed-result path calls this method. Confirmed
        money is never reversed/rebooked. The correction and completion are one
        state-file transaction, including today's logical-trade/unknown counters.
        """
        stamp = datetime.fromisoformat(str(executed_at).replace("Z", "+00:00"))
        if stamp.tzinfo is None or stamp.timestamp() > datetime.now(timezone.utc).timestamp() + 60:
            raise ValueError("Ungesicherter Ausfuehrungszeitpunkt fuer Risikoergebnis")
        actual_day = str(stamp.astimezone(ZoneInfo(str(getattr(config,"LOCAL_TIMEZONE","Europe/Berlin")))).date())
        key = str(trade_id)
        pnl, gross = float(pnl), float(gross_pnl)
        if not math.isfinite(pnl) or not math.isfinite(gross):
            raise ValueError("Nicht endliches Risikoergebnis")
        proof = dict(evidence or {})
        # 10.3.0: Der zusammengesetzte Verkaufsbeweis (10.2.1) traegt eine
        # eigene Methodenkennung; beide Kennungen stammen aus derselben
        # nachgerechneten okx_reference_valuation-Kette.
        if proof and (set(proof) != {'source','receipt_hash','method','currency','quality'}
                or proof.get('source') != 'okx_reference_valuation'
                or proof.get('currency') != 'EUR' or proof.get('quality') != 'REFERENCE_VALUATION'
                or proof.get('method') not in {'EUR_REFERENCE_CASHFLOWS_V1',
                                               'EUR_REFERENCE_CASHFLOWS_COMPOSITE_V1'}
                or len(str(proof.get('receipt_hash') or '')) != 64):
            raise ValueError("Ungueltiger Referenzbewertungsnachweis")
        settlement = dict(settlement_evidence or {})
        # 10.3.1: Neben der Nutzerbestaetigung auch der automatische
        # Cash-Delta-Beleg (zwei authentifizierte Barbestandsbelege).
        settlement_pairs = {('ETORO_USER_VERIFIED_CASH_DELTA', 'USER_CONFIRMED'),
                            ('ETORO_AUTOMATIC_CASH_DELTA', 'CASH_DELTA_CONFIRMED'),
                            # 10.7.0: Intervallrechnung ueber alle bekannten
                            # Ereignisse (gleiche Belegqualitaet) und der
                            # gekennzeichnete Erwartungswert (Freigabe Georg
                            # 18.09.2026).
                            ('ETORO_INTERVAL_CASH_DELTA', 'CASH_DELTA_CONFIRMED'),
                            ('ETORO_EXPECTED_FEE_MODEL', 'EXPECTED_UNVERIFIED')}
        if settlement and (proof or set(settlement) != {'source','receipt_hash','currency','quality'}
                or (settlement.get('source'), settlement.get('quality')) not in settlement_pairs
                or settlement.get('currency') != 'USD'
                or len(str(settlement.get('receipt_hash') or '')) != 64
                or any(c not in '0123456789abcdef' for c in str(settlement.get('receipt_hash') or ''))):
            raise ValueError('Ungueltiger Abrechnungsnachweis')
        def change():
            prior = self.realized_receipts.get(key, {})
            if prior.get("status") != "UNKNOWN":
                if prior.get("status") == "CONFIRMED" and prior.get("pnl") == pnl and prior.get("gross") == gross:
                    return False
                raise ValueError("Nur unbezahlte UNKNOWN-Ergebnisse duerfen nachgetragen werden")
            before_day = prior.get("booked_day", str(self.current_date))
            was_today, is_today = before_day == str(self.current_date), actual_day == str(self.current_date)
            aliases = {key} | {k for k,r in self.realized_receipts.items()
                if r.get("status") == "ALIAS" and r.get("target") == key}
            if was_today and not is_today:
                self.unknown_pnl_trades_today = max(0,self.unknown_pnl_trades_today-1)
                self.unknown_pnl_ids_today = [x for x in self.unknown_pnl_ids_today if x not in aliases]
                if any(x in aliases for x in self.counted_trade_ids_today):
                    self.trades_today = max(0,self.trades_today-1)
                    self.counted_trade_ids_today = [x for x in self.counted_trade_ids_today if x not in aliases]
            elif is_today and not was_today:
                self.unknown_pnl_trades_today += 1
                self.unknown_pnl_ids_today.append(key)
                self._count_logical_trade(key)
            prior["booked_day"] = actual_day
            done = self._register_realized_pnl_unlocked(pnl,account_equity,gross,key,None)
            if proof:
                self.realized_receipts[key]['valuation_evidence'] = proof
            if settlement:
                self.realized_receipts[key]['settlement_evidence'] = settlement
            if before_day != actual_day:
                self.realized_receipts[key]["execution_day_correction"] = {
                    "from":before_day,"to":actual_day,"source":proof.get('source','closed_ledger_receipt')}
            return done
        return self._transaction(change, recovery_keys=(
            _risk_operation_key("REALIZED", key, pnl, gross), _risk_operation_key("UNKNOWN", key)))

    def adopt_receipt_alias(self, canonical_id, aliases):
        """Bind a new ledger key only to an already recorded exact receipt."""
        aliases = list(aliases)
        def change():
            if canonical_id in self.realized_receipts:
                return
            for alias in aliases:
                if alias and (alias in self.realized_receipts or alias in self.counted_trade_ids_today):
                    self.realized_receipts[canonical_id] = dict(self.realized_receipts.get(alias) or
                        {"legacy":True,"pnl":None})
                    if self.realized_receipts.get(alias, {}).get('status') == 'UNKNOWN':
                        self.realized_receipts[alias] = {'status': 'ALIAS', 'target': canonical_id}
                    return
        self._transaction(change, recovery_keys=(_risk_operation_key("ALIAS", canonical_id, sorted(aliases)),))

    def _register_realized_pnl_unlocked(self, pnl, account_equity, gross,
                                        trade_id, max_daily_loss_pct):
        if not math.isfinite(pnl) or not math.isfinite(gross):
            raise ValueError("Nicht endliches Handelsergebnis")
        key = str(trade_id or "")
        prior = self.realized_receipts.get(key, {}) if key else {}
        enriching = prior.get("status") == "UNKNOWN"
        booked_day = prior.get("booked_day", str(self.current_date))
        same_day = booked_day == str(self.current_date)
        if prior and not enriching:
            if prior.get("pnl") is not None and (
                    not math.isclose(float(prior['pnl']),pnl,rel_tol=1e-9,abs_tol=1e-8)
                    or not math.isclose(float(prior['gross']),gross,rel_tol=1e-9,abs_tol=1e-8)):
                raise ValueError("Widerspruechliches Ergebnis fuer denselben Buchungsbeleg")
            return False
        if not enriching:
            if key and key in self.counted_trade_ids_today:
                self.realized_receipts[key] = {"legacy":True,"pnl":None}
                return False
            if not self._count_logical_trade(trade_id):
                return False
        else:
            # A late fee completes one receipt, never another trade count.
            self.lifetime_unknown_pnl_trades = max(0,self.lifetime_unknown_pnl_trades-1)
            if same_day:
                self.unknown_pnl_trades_today = max(0,self.unknown_pnl_trades_today-1)
                self.unknown_pnl_ids_today = [x for x in self.unknown_pnl_ids_today if x!=key]
        if key:
            self.realized_receipts[key] = {"status":"CONFIRMED","pnl":pnl,"gross":gross,
                "booked_day":booked_day}
        if same_day:
            self.realized_pnl_today += pnl
        self.lifetime_realized_pnl += pnl

        # Dashboard-Gewinne/-Verluste sind NETTO nach den explizit
        # modellierten Broker-/Regulatorikgebuehren. Grosswerte bleiben
        # separat fuer die Kostenquoten-Bremse erhalten.
        if pnl > 0:
            if same_day:
                self.net_profit_today += pnl
            self.lifetime_net_profit += pnl
        elif pnl < 0:
            if same_day:
                self.net_loss_today += abs(pnl)
            self.lifetime_net_loss += abs(pnl)

        if gross > 0:
            if same_day:
                self.gross_profit_today += gross
            self.lifetime_gross_profit += gross
        elif gross < 0:
            if same_day:
                self.gross_loss_today += abs(gross)
            self.lifetime_gross_loss += abs(gross)

        if same_day and pnl < 0:
            self.consecutive_losses += 1
            limit = int(getattr(config, "LOSS_STREAK_LIMIT", 4))
            if self.consecutive_losses >= limit:
                self.cooldown_until = datetime.now() + timedelta(
                    minutes=int(getattr(config, "LOSS_STREAK_COOLDOWN_MINUTES", 60))
                )
        elif same_day and pnl > 0:
            self.consecutive_losses = 0

        daily_limit = (float(max_daily_loss_pct)
                       if max_daily_loss_pct is not None
                       else float(getattr(config, "MAX_DAILY_LOSS_PCT", 0.02)))
        if account_equity > 0 and self.realized_pnl_today <= -abs(
            account_equity * daily_limit
        ):
            self.trading_halted = True

        return True


    def update_equity_guard(self, account_equity: float, *, basis_key: str = "",
                            account_scope: str = "", native_equity=None,
                            native_ccy: str = "", native_beitraege=None) -> bool:
        """Aktualisiert die unrealisierte Tagesverlustbremse.

        Rueckgabe True bedeutet: neue Kaeufe sind gesperrt. Bestehende
        Positionen, Stops und Verkaeufe bleiben bewusst unberuehrt.
        """
        try:
            equity = float(account_equity)
        except Exception:
            return bool(self.equity_guard_halted)
        if not math.isfinite(equity) or equity <= 0:
            return bool(self.equity_guard_halted)
        try:
            native = float(native_equity)
            if not math.isfinite(native) or native <= 0 or not str(native_ccy or "").strip():
                native = None
        except (TypeError, ValueError):
            native = None
        # 10.2.0: Mehrlagen-Beitraege nur uebernehmen, wenn JEDE Lane einen
        # endlichen beobachteten Kurs und eine endliche Menge traegt. Jeder
        # Zweifel laesst die native Reihe fuer diesen Takt weg (UNKNOWN bleibt
        # UNKNOWN; der Basis-Drawdown gilt dann unveraendert).
        beitraege = None
        if isinstance(native_beitraege, dict) and len(native_beitraege) >= 2:
            geprueft = {}
            summe = 0.0
            for ccy, row in native_beitraege.items():
                try:
                    menge = float((row or {}).get("native"))
                    kurs = float((row or {}).get("rate_to_basis"))
                except (TypeError, ValueError):
                    geprueft = None
                    break
                if (not str(ccy or "").strip() or not math.isfinite(menge)
                        or menge < 0 or not math.isfinite(kurs) or kurs <= 0):
                    geprueft = None
                    break
                geprueft[str(ccy).strip().upper()] = {"native": menge, "rate_to_basis": kurs}
                summe += menge * kurs
            if geprueft and summe > 0:
                beitraege = geprueft
        self._basis_observation = dict(observed_basis=_equity_basis_identity(basis_key),
            observed_scope=account_scope, observed_equity=equity)
        blocked = self._transaction(
            lambda: self._update_equity_guard_unlocked(
                equity, basis_key, account_scope, native,
                str(native_ccy or "").strip().upper(), beitraege),
            recovery_keys=("EQUITY_OBSERVATION",))
        return bool(blocked or self.persistence_failure_reason())

    def basis_review(self) -> dict:
        """Current evidence, including blockers hidden by a closed market."""
        from risk_basis_status import status
        return status(self._payload(), **getattr(self, "_basis_observation", {}))

    def _update_equity_guard_unlocked(self, equity: float, basis_key: str,
                                    account_scope: str = "", native_equity=None,
                                    native_ccy: str = "",
                                    native_beitraege=None) -> bool:
        basis = _equity_basis_identity(basis_key)
        previous = _equity_basis_identity(self.equity_basis_key)
        scope = str(account_scope or "").strip()[:500]
        mismatch = ((basis and previous and basis != previous)
                    or (scope and self.risk_scope_key and scope != self.risk_scope_key))
        if mismatch:
            self.equity_basis_review_required = True
            self.equity_basis_review_reason = "RISK_EQUITY_BASIS_REVIEW: Konto, Umgebung oder Bewertungsmethode geaendert"
            logger.error("%s; keine automatische Tagesbasis-/Zaehlerzuruecksetzung",
                         self.equity_basis_review_reason)
            return True
        if scope and not self.risk_scope_key:
            self.risk_scope_key = scope
            if not self.risk_scope_origin:
                self.risk_scope_origin = "OBSERVED_V10"
        if basis and basis != self.equity_basis_key:
            self.equity_basis_key = basis
            self.equity_basis_changed_at = datetime.now(timezone.utc).isoformat()
        if self.day_start_equity <= 0:
            self.day_start_equity = equity
        self.last_equity = equity
        dd = (equity - self.day_start_equity) / self.day_start_equity if self.day_start_equity > 0 else 0.0
        self.equity_drawdown_pct = float(dd)
        # 10.1.10: Native Reihe (genau eine finanzierte Waehrung). Wechselt
        # die finanzierte Waehrung untertaegig, startet die native Basis neu;
        # bis dahin gilt fuer diesen Tag wieder der Basis-Drawdown.
        # 10.2.0: Bei mehreren finanzierten Lanes wird die native Reihe als
        # Summe der Lanes mit am Tagesstart eingefrorenen Kursen gefuehrt;
        # ein Lane-Set-Wechsel (auch die untertaegige Freigabe einer neuen
        # Waehrung) ist ein Basis-Ereignis und startet die Tagesbasis neu.
        if native_beitraege:
            label = "+".join(sorted(native_beitraege))
            neu_verankern = (label != self.native_equity_ccy
                             or self.day_start_native_equity <= 0
                             or not self.native_fx_day_start)
            if neu_verankern:
                if self.last_native_equity and label != self.native_equity_ccy:
                    logger.info("Finanzierte Lanes gewechselt (%s -> %s); native "
                                "Equity-Tagesbasis startet neu",
                                self.native_equity_ccy or "-", label)
                self.native_fx_day_start = {
                    c: float(v["rate_to_basis"]) for c, v in native_beitraege.items()}
            kurse = self.native_fx_day_start
            synth = sum(float(v["native"]) * float(kurse.get(c) or v["rate_to_basis"])
                        for c, v in native_beitraege.items())
            native_equity = float(synth)
            native_ccy = label
            if neu_verankern:
                self.native_equity_ccy = label
                self.day_start_native_equity = float(synth)
        effective_dd = dd
        if native_equity is not None:
            if native_ccy != self.native_equity_ccy:
                self.native_equity_ccy = native_ccy
                self.day_start_native_equity = float(native_equity)
                if self.last_native_equity:
                    logger.info("Finanzierte Waehrung gewechselt; native Equity-Tagesbasis "
                                "startet neu in %s", native_ccy)
            if self.day_start_native_equity <= 0:
                self.day_start_native_equity = float(native_equity)
            self.last_native_equity = float(native_equity)
            native_dd = ((native_equity - self.day_start_native_equity)
                         / self.day_start_native_equity
                         if self.day_start_native_equity > 0 else 0.0)
            self.native_drawdown_pct = float(native_dd)
            self.fx_drawdown_pct = float(dd - native_dd)
            effective_dd = native_dd
        limit = abs(float(getattr(config, "MAX_UNREALIZED_DAILY_LOSS_PCT", 0.03)))
        if limit > 0 and effective_dd <= -limit and not self.equity_guard_halted:
            self.equity_guard_halted = True
            logger.error(
                "Equity-Tagesbremse aktiviert: %.2f%% <= -%.2f%% (%s). Nur neue Kaeufe gesperrt.",
                effective_dd * 100.0, limit * 100.0,
                ("nativ " + self.native_equity_ccy) if native_equity is not None else "Basiswaehrung",
            )
        return bool(self.equity_guard_halted or self.equity_basis_review_required)

    def register_unknown_pnl_at(self, trade_id, observed_at):
        """Recover a missing ledger receipt without assigning it to today's P&L.

        The date is an observation date until a complete fill receipt corrects it.
        No money is invented. One state transaction preserves restart idempotency.
        """
        stamp = datetime.fromisoformat(str(observed_at).replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.timestamp() > datetime.now(timezone.utc).timestamp() + 60:
            raise ValueError('Ergebnisdatum nicht belegt')
        day = str(stamp.astimezone(ZoneInfo(str(getattr(config, 'LOCAL_TIMEZONE', 'Europe/Berlin')))).date())
        key = str(trade_id)
        def change():
            if key in self.realized_receipts:
                return False
            is_today = day == str(self.current_date)
            self.realized_receipts[key] = {'status':'UNKNOWN','pnl':None,'booked_day':day,
                                           'source':'ledger_recovery'}
            self.lifetime_unknown_pnl_trades += 1
            if is_today:
                self.unknown_pnl_trades_today += 1
                self.unknown_pnl_ids_today.append(key)
                self._count_logical_trade(key)
            return True
        return self._transaction(change, recovery_keys=(_risk_operation_key("UNKNOWN", key),))

    def resolve_proven_residual(self, trade_id, proof_hash):
        """A proved unsold inventory remainder is not an unknown realized trade.

        Never delete/reverse a confirmed monetary receipt. Keep the reclassification
        auditable. Callers validate the ledger proof in their exact account domain.
        """
        key = str(trade_id)
        if not proof_hash:
            raise ValueError('Restnachweis fehlt')
        def change():
            prior = self.realized_receipts.get(key, {})
            if not prior or prior.get('status') == 'RESIDUAL':
                return False
            if prior.get('status') != 'UNKNOWN':
                raise ValueError('Beziffertes Risikoergebnis darf nicht als Rest umgedeutet werden')
            self.lifetime_unknown_pnl_trades = max(0, self.lifetime_unknown_pnl_trades-1)
            if prior.get('booked_day') == str(self.current_date):
                self.unknown_pnl_trades_today = max(0,self.unknown_pnl_trades_today-1)
                self.unknown_pnl_ids_today = [x for x in self.unknown_pnl_ids_today if x != key]
                if key in self.counted_trade_ids_today:
                    self.trades_today = max(0,self.trades_today-1)
                    self.counted_trade_ids_today = [x for x in self.counted_trade_ids_today if x != key]
            self.realized_receipts[key] = {**prior,'status':'RESIDUAL','proof_hash':str(proof_hash),
                                           'previous_status':'UNKNOWN'}
            return True
        return self._transaction(change, recovery_keys=(_risk_operation_key("RESIDUAL", key, proof_hash),))

    def _notify_settlement_needed(self, trade_id):
        """10.1.10: Neues unbekanntes Verkaufsergebnis sofort melden.

        Die Kaufsperre der Kontodomaene war bisher nur im Log/WebUI-Status
        sichtbar und wurde oft erst Stunden spaeter entdeckt. Best effort;
        ein Zustellfehler aendert keinen Risikozustand.
        """
        try:
            bound = str(getattr(self, "_bound_path", "") or "").lower()
            domain = "OKX" if "okx" in bound else "eToro"
            key = str(trade_id or "").replace("ledger:", "") or "?"
            from notifier import notify
            notify("ERGEBNISABGLEICH ERFORDERLICH",
                   f"{domain}: Das Verkaufsergebnis von Trade {key} hat noch keinen "
                   "vollstaendigen Gebuehren-/Einstands-/Waehrungsbeleg. Neue Kaeufe "
                   "dieser Kontodomaene pausieren; Schutz und Verkaeufe laufen weiter.\n"
                   "Klaerung: WebUI -> Handel -> Trade oeffnen -> Abschlussabrechnung "
                   "pruefen und bestaetigen.")
        except Exception:
            logger.debug("Ergebnisabgleich-Hinweis nicht zustellbar", exc_info=True)

    def register_unknown_pnl_trade(self, trade_id=None):
        """Merkt einen Verkauf, dessen Einstand nicht sicher bekannt ist.

        Wichtig: Es wird absichtlich KEINE 0 als Gewinn/Verlust gebucht.
        Auch hier zaehlen mehrere Teil-Fills derselben Brokerorder nur als
        ein logischer Trade fuer MAX_TRADES_PER_DAY.
        """
        registered = self._transaction(lambda: self._register_unknown_unlocked(trade_id),
                                 recovery_keys=(_risk_operation_key("UNKNOWN", str(trade_id)),) if trade_id else ())
        if registered:
            self._notify_settlement_needed(trade_id)
        return registered

    def _register_unknown_unlocked(self, trade_id=None):
        key = str(trade_id or "")
        if key and (key in self.realized_receipts or key in self.unknown_pnl_ids_today):
            return False
        if key:
            self.realized_receipts[key] = {"status":"UNKNOWN", "pnl":None,
                "booked_day":str(self.current_date)}
            self.unknown_pnl_ids_today.append(key)
            self.unknown_pnl_ids_today = self.unknown_pnl_ids_today[-1000:]
        self.unknown_pnl_trades_today += 1
        self.lifetime_unknown_pnl_trades += 1
        self._count_logical_trade(trade_id)
        return True

    def register_estimated_cost(self, amount, event_id=None):
        try:
            identity_amount = float(amount)
        except (ValueError, TypeError):
            identity_amount = str(amount)
        return self._transaction(lambda: self._register_cost_unlocked(amount, event_id),
                                 recovery_keys=(_risk_operation_key("COST", str(event_id), identity_amount),) if event_id else ())

    def _register_cost_unlocked(self, amount, event_id=None):
        key = str(event_id or "")[:200]
        if key and key in self.counted_cost_ids_today:
            return False
        try:
            value = max(0.0, float(amount))
        except Exception:
            return False
        if key:
            self.counted_cost_ids_today.append(key)
            self.counted_cost_ids_today = self.counted_cost_ids_today[-2000:]
        self.estimated_costs_today += value
        self.lifetime_estimated_costs += value
        return True

    def set_trading_halted(self, halted: bool = True) -> None:
        self._transaction(lambda: setattr(self, "trading_halted", bool(halted)),
                          recovery_keys=(_risk_operation_key("HALT", bool(halted)),))

    def set_open_positions(self, count: int) -> None:
        self._transaction(lambda: setattr(self, "open_positions", max(0, int(count))),
                          recovery_keys=("OPEN_POSITION_COUNT",))

    def cooldown_active(self):
        return self.cooldown_until is not None and datetime.now() < self.cooldown_until

    def cost_ratio(self):
        if self.gross_profit_today <= 0:
            return 0.0
        return max(0.0, self.estimated_costs_today) / self.gross_profit_today

    def cost_pressure_active(self):
        if not bool(getattr(config, "COST_RATIO_GUARD_ENABLED", True)):
            return False
        if self.trades_today < int(getattr(config, "COST_RATIO_MIN_TRADES", 5)):
            return False
        if self.gross_profit_today <= 0:
            return False
        return self.cost_ratio() > float(
            getattr(config, "COST_RATIO_MAX_OF_GROSS_PROFIT", 0.30)
        )


def calculate_stop_take(entry_price, side, atr_value=None, asset_type="stock"):
    use_atr = (
        config.USE_ATR_STOPS
        and atr_value is not None
        and math.isfinite(float(atr_value))
        and atr_value > 0
    )
    if use_atr:
        stop_distance = atr_value * config.ATR_STOP_MULTIPLIER
        take_distance = atr_value * config.ATR_TAKE_MULTIPLIER
    else:
        stop_distance = entry_price * config.STOP_LOSS_PCT
        take_distance = entry_price * config.TAKE_PROFIT_PCT
    if side == "BUY":
        stop = entry_price - stop_distance
        take = entry_price + take_distance
    else:
        stop = entry_price + stop_distance
        take = entry_price - take_distance
    floor = 0.00000001 if asset_type == "crypto" else 0.01
    return (
        round(max(stop, floor), 8 if asset_type == "crypto" else 4),
        round(max(take, floor), 8 if asset_type == "crypto" else 4),
    )


def _step_round_down(qty, step):
    if qty <= 0:
        return 0.0
    return math.floor(qty / step) * step


def size_new_position(
    account_equity,
    entry_price,
    stop_price,
    max_capital_pct=None,
    asset_type="stock",
    risk_pct=None,
):
    """Menge aus Risikoabstand und Kapitaldeckel; die kleinere Grenze gilt.

    10.6.0: ``risk_pct`` erlaubt es dem Aufrufer, den Einsatz je Trade
    ausdruecklich vorzugeben -- so wirkt die in der WebUI gewaehlte
    Einsatzstufe (risk_levels.py) ohne Neustart. Ohne Angabe bleibt es beim
    bisherigen Wert aus Konfiguration und Profil.
    """
    if account_equity <= 0 or entry_price <= 0 or stop_price <= 0:
        return 0.0
    if asset_type == "crypto":
        risk_pct = (config.CRYPTO_RISK_PER_TRADE_PCT if risk_pct is None
                    else float(risk_pct))
        cap_pct = (
            max_capital_pct
            if max_capital_pct is not None
            else config.CRYPTO_MAX_POSITION_PCT
        )
        step = config.CRYPTO_QUANTITY_STEP
    else:
        risk_pct = (config.RISK_PER_TRADE_PCT if risk_pct is None
                    else float(risk_pct))
        cap_pct = (
            max_capital_pct if max_capital_pct is not None else config.MAX_POSITION_PCT
        )
        step = 1.0
    if risk_pct <= 0:
        return 0.0
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0:
        return 0.0
    qty_risk = (account_equity * risk_pct) / stop_distance
    qty_cap = (account_equity * cap_pct) / entry_price
    qty = _step_round_down(min(qty_risk, qty_cap), step)
    return int(qty) if asset_type != "crypto" else round(qty, 8)


def offene_ergebnisse_heute(risk_state) -> int:
    """Unbekannte Verkaufsergebnisse, die HEUTE noch sperren (10.7.0).

    Entscheidung Georg, 18.09.2026: Ein Verkauf ohne bezifferten Beleg sperrt
    neue Kaeufe nur am Handelstag des Verkaufs. Ab dem Folgetag kann er die
    Tagesverlustgrenze nicht mehr beruehren; er bleibt in der Buchhaltung als
    offener Beleg sichtbar (``open_unknown`` in result_scope_summary), aber
    er haelt den Handel nicht mehr an. Beide Broker rufen genau diese
    Funktion -- eToro ueber kaufsperre_grund, OKX ueber RiskPot.darf_kaufen.
    """
    today_key = str(getattr(risk_state, "current_date", "") or "")
    zaehler = int(getattr(risk_state, "unknown_pnl_trades_today", 0) or 0)
    receipts = getattr(risk_state, "realized_receipts", {}) or {}
    heute = sum(1 for r in receipts.values()
                if r.get("status") == "UNKNOWN" and str(r.get("booked_day") or "") == today_key)
    return max(zaehler, heute)


def can_open_new_position(risk_state):
    """Darf ein neuer Einstieg stattfinden?

    Wer hier einen Grund ergaenzt, muss ihn auch in ``kaufsperre_grund()``
    ergaenzen -- sonst erscheint er als falsche Meldung. Ein Test wacht
    darueber.
    """
    return not kaufsperre_grund(risk_state)


def kaufsperre_grund(risk_state) -> str:
    """Der Grund, der neue Kaeufe gerade sperrt -- leer heisst: erlaubt.

    Bis v8.1.4 stand die Begruendung in live_trader und kannte vier der sechs
    Gruende. Die Equity-Tagesbremse fehlte und fiel in einen Sammelzweig
    "Positionslimit erreicht (offen=…)". Am 25.08.2026 war das mit 243
    Meldungen der haeufigste Ablehnungsgrund des Tages -- und er war falsch.

    Jetzt gibt es genau eine Quelle fuer Entscheidung UND Begruendung.
    """
    failure = getattr(risk_state, "persistence_failure_reason", lambda: "")()
    if failure:
        return f"{failure}: Risikozustand nicht bestaetigt; neue Kaeufe pausieren, Stops und Verkaeufe bleiben aktiv"
    risk_state.reset_if_new_day()
    if bool(getattr(risk_state, "equity_basis_review_required", False)):
        return str(risk_state.equity_basis_review_reason or "RISK_EQUITY_BASIS_REVIEW")

    grenze_positionen = int(getattr(config, "MAX_OPEN_POSITIONS", 0) or 0)
    if grenze_positionen <= 0:
        return ("Konfigurationsfehler: MAX_OPEN_POSITIONS muss mindestens 1 sein "
                f"(steht auf {grenze_positionen})")

    if risk_state.trading_halted:
        verlust = float(getattr(risk_state, "realized_pnl_today", 0.0) or 0.0)
        return (f"Tagesverlustlimit erreicht (heute realisiert {verlust:+.2f})")

    from etoro_risk_period import result_scope_summary
    result_scope = result_scope_summary(risk_state)
    if result_scope.get("review_required"):
        return "RISK_RESULT_PERIOD_REVIEW: Ergebnisperiodenbeleg widerspruechlich; neue Kaeufe pausieren"
    pending_results = result_scope["active_unknown"]
    if pending_results:
        return (f"Ergebnisabgleich ausstehend: {pending_results} Verkaufsergebnis(se) "
                "ohne vollstaendige Gebuehren-/Einstands-/Waehrungsbelege. "
                "Neue Kaeufe dieser Kontodomaene pausieren; Schutz und Verkaeufe laufen weiter.")

    if bool(getattr(risk_state, "equity_guard_halted", False)):
        dd = float(getattr(risk_state, "equity_drawdown_pct", 0.0) or 0.0) * 100
        grenze = abs(float(getattr(config, "MAX_UNREALIZED_DAILY_LOSS_PCT", 0.03))) * 100
        return (f"Equity-Tagesbremse aktiv: {dd:+.2f} % seit Tagesbeginn "
                f"(Grenze -{grenze:.2f} %). Verkaeufe und Stops laufen weiter.")

    if risk_state.cooldown_active():
        rest = getattr(risk_state, "cooldown_until", None)
        return (f"Verlustserien-Cooldown aktiv bis {rest}" if rest
                else "Verlustserien-Cooldown aktiv")

    if risk_state.cost_pressure_active():
        quote = float(risk_state.cost_ratio()) * 100
        grenze = float(getattr(config, "COST_RATIO_MAX_OF_GROSS_PROFIT", 0.30)) * 100
        return f"Kostenquote zu hoch: {quote:.1f} % > {grenze:.0f} %"

    grenze_trades = int(getattr(config, "MAX_TRADES_PER_DAY", 20))
    if risk_state.trades_today >= grenze_trades:
        return (f"Maximale Trades pro Tag erreicht "
                f"({risk_state.trades_today}/{grenze_trades})")

    if risk_state.open_positions >= grenze_positionen:
        return (f"Positionslimit erreicht "
                f"(offen={risk_state.open_positions}, Limit={grenze_positionen})")

    return ""
