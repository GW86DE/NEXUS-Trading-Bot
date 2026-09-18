"""Persistente, asset-sichere Zuordnung von Broker-Orders zu Bot-Orders."""
from __future__ import annotations
from pathlib import Path
import json
import logging
import time

from instrument_identity import canonical_key
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock

logger = logging.getLogger(__name__)


def _pending_key(symbol, asset_type="stock") -> str:
    return canonical_key(symbol, asset_type)


# Orderzustaende. Nur TERMINALE Zustaende beenden Ownership und Reservierung.
TERMINALE_ZUSTAENDE = frozenset({
    "FILLED", "REJECTED", "CANCELED", "CANCELLED", "EXPIRED",
})

# Alles andere ist NICHT terminal und darf niemals per Zeitablauf verschwinden:
# eine Order, die der Broker angenommen hat, bleibt unser Problem -- auch nach
# 24 Stunden Ausfall. Bis 8.1.1 loeschte die TTL solche Eintraege nach sechs
# Stunden; ein spaeter Fill war danach nicht mehr zuzuordnen.
NICHT_TERMINALE_ZUSTAENDE = frozenset({
    "PLANNED", "SUBMITTING", "SUBMITTED", "PARTIALLY_FILLED",
    "UNKNOWN_AFTER_SUBMIT", "LATE_FILL_WATCH", "AWAITING_FILL_EVIDENCE",
    "AWAITING_POSITION_CONFIRMATION",
})

# Nur dieser Zustand ist rein vorbereitend und wurde nie gesendet.
NUR_VORBEREITET = "PLANNED"


def ist_terminal(zustand) -> bool:
    return str(zustand or "").upper() in TERMINALE_ZUSTAENDE


def darf_per_ttl_verfallen(meta) -> bool:
    """Darf dieser Pending-Eintrag durch blossen Zeitablauf weg?

    Nur wenn er nie gesendet wurde. Sobald ein Submit begonnen hat, bleibt
    der Eintrag bis zu einer eindeutigen Brokerantwort erhalten.
    """
    zustand = str((meta or {}).get("zustand") or "").upper()
    if not zustand:
        # Altbestand ohne Zustandsfeld: konservativ als vorbereitend werten,
        # ABER nur wenn keine Brokerreferenz vorhanden ist.
        referenzen = [(meta or {}).get(k) for k in ("ord_id", "order_id", "cl_ord_id",
                                                    "clOrdId", "reference_id")]
        return not any(str(x or "").strip() for x in referenzen)
    return zustand == NUR_VORBEREITET


class RegistryIdentityConflict(RuntimeError):
    """Conflicting order identity. Preserve stored evidence and stop the mutation."""


def _environment(meta):
    value = str(meta.get("environment") or meta.get("broker_environment") or "").upper()
    if not value and isinstance(meta.get("paper"), bool):
        value = "DEMO" if meta["paper"] else "LIVE"
    return value


def _compatible_identity(old, new):
    # Missing historical fields may be enriched by an explicitly addressed
    # order. Known identities must never change, not even on a shared decision.
    for key in ("broker", "asset_type", "symbol", "inst_id", "role", "account_fingerprint"):
        a, b = str(old.get(key) or ""), str(new.get(key) or "")
        if key != "account_fingerprint":
            a, b = a.upper(), b.upper()
        if a and b and a != b:
            return False
    a, b = _environment(old), _environment(new)
    return not (a and b and a != b)


def _same_order(old, new):
    if not _compatible_identity(old, new):
        return False
    # A decision is a trade-level link, NEVER an order identity. Nor is
    # entry_order_id: an exit intentionally references that *different* order.
    a, b = str(old.get("ord_id") or ""), str(new.get("ord_id") or "")
    if a and b:
        return a == b
    for name in ("reference_id", "client_order_id", "cl_ord_id"):
        a, b = str(old.get(name) or ""), str(new.get(name) or "")
        if a and b and a == b:
            return True
    return False


class OrderOwnershipRegistry:
    def __init__(self, path="bot_order_registry.json", pending_ttl=900):
        self.path = Path(path)
        self.pending_ttl = float(pending_ttl or 900)
        self.orders = {}
        self.pending = {}
        self.storage_error = ""
        self._load()
        if not self.storage_error:
            self.cleanup()

    def _load(self, *, strict=False):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            if not isinstance(raw, dict):
                raise ValueError("Wurzel ist kein Objekt")
            self.orders = {str(k): dict(v) for k, v in (raw.get("orders") or {}).items() if isinstance(v, dict)}
            self.pending = {str(k): dict(v) for k, v in (raw.get("pending") or {}).items() if isinstance(v, dict)}
            self.storage_error = ""
        except Exception as exc:
            self.storage_error = str(exc)
            logger.error("Order-Ownership-Registry unlesbar; Geldpfad bleibt gesperrt: %s", exc)
            if strict:
                raise RuntimeError(
                    "Order-Ownership-Registry ist unlesbar; keine Order darf gesendet werden"
                ) from exc

    def _save_unlocked(self):
        atomic_write_json(self.path, {
            "schema_version": 3,
            "orders": self.orders,
            "pending": self.pending,
            "updated_at": time.time(),
        })

    def _mutate(self, callback):
        with critical_state_lock(self.path):
            # Der entscheidende Unterschied zu 9.4.1: unmittelbar vor JEDER
            # Mutation erneut laden. So kann kein alter Objektstand die
            # Aenderung eines anderen Threads/Prozesses ueberschreiben.
            self._load(strict=True)
            try:
                result = callback()
                self._save_unlocked()
                return result
            except Exception:
                # A failed mutation must not leave a half-updated object in RAM.
                self._load(strict=True)
                raise

    def _refresh(self):
        with critical_state_lock(self.path):
            self._load(strict=True)

    def save(self):
        # Kompatibilitaetsmethode fuer Altcode. Sie fuehrt absichtlich keinen
        # blinden Vollersatz mit einem moeglicherweise veralteten Objekt aus.
        self._mutate(lambda: None)

    def cleanup(self):
        def change():
            now = time.time()
            for key, meta in list(self.pending.items()):
                if darf_per_ttl_verfallen(meta) and now - float(
                        meta.get("created_at_ts", 0) or 0) > max(60.0, self.pending_ttl):
                    self.pending.pop(key, None)
            # Ein FILLED-Orderbeleg kann zu einer monatelang offenen Position
            # gehoeren. Die alte pauschale 45-Tage-Loeschung zerstoerte dann
            # die eindeutige Entry-/Exit-Zuordnung. Brokeranker werden deshalb
            # nicht mehr allein aufgrund ihres Alters entfernt.
        self._mutate(change)

    def register_pending(self, symbol, meta, asset_type="stock"):
        d = dict(meta or {})
        d.setdefault("created_at_ts", time.time())
        d.setdefault("zustand", NUR_VORBEREITET)
        d["owner"] = "BOT"
        d["asset_type"] = str(asset_type or d.get("asset_type") or "stock").lower()
        canonical = _pending_key(symbol, d["asset_type"])
        d["canonical_key"] = canonical
        # 9.7: Pending-Identitaet ist eine Kontodomaene, nicht nur ein Symbol.
        # Sonst ueberschreibt APT/DEMO einen gleichzeitigen APT/LIVE-Vorgang.
        broker = str(d.get("broker") or "").lower()
        env = str(d.get("environment") or d.get("broker_environment") or "").upper()
        account = str(d.get("account_fingerprint") or "")
        storage_key = "|".join((canonical, broker, env, account)) if (broker or env or account) else canonical
        self._mutate(lambda: self.pending.__setitem__(storage_key, d))

    def _pending_candidates(self, symbol, asset_type="stock", **selectors):
        canonical = _pending_key(symbol, asset_type)
        result = []
        for key, meta in self.pending.items():
            stored = str(meta.get("canonical_key") or key.split("|", 1)[0])
            if stored != canonical:
                continue
            matches = True
            for field in ("broker", "account_fingerprint", "environment", "role",
                          "decision_id", "cl_ord_id", "reference_id"):
                wanted = selectors.get(field)
                if wanted in (None, ""):
                    continue
                actual = (_environment(meta) if field == "environment" else
                          meta.get("cl_ord_id") or meta.get("client_order_id")
                          if field == "cl_ord_id" else meta.get(field))
                a, b = str(actual or ""), str(wanted)
                if field in {"broker", "environment", "role"}:
                    a, b = a.upper(), b.upper()
                # A supplied boundary must be positively matched, not simply
                # accepted when the stored account/environment is missing.
                if a != b:
                    matches = False
                    break
            if matches:
                result.append((key, meta))
        return result

    def setze_zustand(self, symbol, zustand, asset_type="stock", **felder):
        """Update exactly one pending intent; never guess across domains."""
        def change():
            # cl_ord_id may be an enrichment of a previously unknown identifier.
            # Domain / decision selectors are selection, other fields are payload.
            selection = {k: v for k, v in felder.items() if k in {
                "broker", "account_fingerprint", "environment", "role", "decision_id"}}
            candidates = self._pending_candidates(symbol, asset_type, **selection)
            if len(candidates) != 1:
                return False
            key, previous = candidates[0]
            meta = dict(previous)
            meta["zustand"] = str(zustand or "").upper()
            meta["zustand_seit"] = time.time()
            for name, value in felder.items():
                if value not in (None, ""):
                    meta[name] = value
            if not _compatible_identity(previous, meta):
                raise RegistryIdentityConflict("Pending identity conflict; no update")
            self.pending[key] = meta
            return True
        return bool(self._mutate(change))

    def nicht_terminale(self) -> dict:
        """Alle Eintraege, die noch auf eine eindeutige Brokerantwort warten.

        KORREKTUR 9.5.2: Bis 9.5.1 lautete die Bedingung
        ``not darf_per_ttl_verfallen(v)``. Diese Funktion beantwortet aber
        eine voellig andere Frage -- naemlich ob ein Eintrag durch blossen
        Zeitablauf verschwinden darf, was nur bei PLANNED der Fall ist. Ein
        ``FILLED``-Eintrag wurde damit als "nicht terminal" gemeldet und
        sperrte den Handel dauerhaft, obwohl der Kauf laengst abgeschlossen
        war. ``ist_terminal()`` steht direkt ueber dieser Methode und wurde
        einfach nicht benutzt.
        """
        self._refresh()
        return {k: dict(v) for k, v in self.pending.items()
                if not ist_terminal((v or {}).get("zustand"))
                and not darf_per_ttl_verfallen(v)}

    def pending_for(self, *, broker: str, environment: str = "",
                    account_fingerprint: str = "", asset_type: str = "") -> dict:
        """Offene Eintraege GENAU eines Brokers, Kontos und Marktes (9.5.2).

        Bis 9.5.1 las ``crypto_engine._offene_order_symbole()`` das gesamte
        gemeinsame Register. Da ``etoro_reconciliation`` seine Orders in
        dieselbe Datei schreibt, konnte eine eToro-Aktie den Kryptohandel
        sperren -- und die Meldung dazu sprach vom falschen Broker.

        Ein Eintrag ohne Broker- oder Umgebungsangabe stammt aus einer
        aelteren Version. Er wird dem anfragenden Broker NICHT stillschweigend
        zugerechnet: fehlt die Angabe, entscheidet ausschliesslich das
        ``asset_type``-Feld. Passt auch das nicht, bleibt der Eintrag aussen
        vor -- ein fremder Bestand darf keinen Handel sperren, den er nichts
        angeht.
        """
        ziel_broker = str(broker or "").strip().lower()
        ziel_env = str(environment or "").strip().upper()
        ziel_konto = str(account_fingerprint or "").strip()
        ziel_markt = str(asset_type or "").strip().lower()
        out: dict = {}
        for key, meta in self.nicht_terminale().items():
            eintrag_broker = str((meta or {}).get("broker") or "").strip().lower()
            eintrag_markt = str((meta or {}).get("asset_type") or "").strip().lower()
            if eintrag_broker:
                if ziel_broker and eintrag_broker != ziel_broker:
                    continue
            elif ziel_markt and eintrag_markt and eintrag_markt != ziel_markt:
                continue
            elif not eintrag_markt:
                continue  # Weder Broker noch Markt bekannt -- nicht zurechenbar.
            if ziel_markt and eintrag_markt and eintrag_markt != ziel_markt:
                continue
            eintrag_env = str((meta or {}).get("environment")
                              or (meta or {}).get("umgebung") or "").strip().upper()
            if ziel_env and eintrag_env and eintrag_env != ziel_env:
                continue
            eintrag_konto = str((meta or {}).get("account_fingerprint") or "").strip()
            if ziel_konto and eintrag_konto and eintrag_konto != ziel_konto:
                continue
            out[key] = dict(meta)
        return out

    def lane_reservierung(self, trade_quote_ccy, *, asset_type="crypto",
                          ausser_symbol="") -> float:
        """Kapital, das schwebende eigene Kaeufe in DIESER Quote-Lane binden.

        Neu in v9.3. Bis 9.2 wurde fuer jeden Kandidaten das freie Guthaben
        frisch beim Broker gelesen, ohne bereits angenommene, aber noch nicht
        verbuchte eigene Kaeufe abzuziehen. Bei zwei Kandidaten konnte
        derselbe Betrag damit zweimal als verfuegbar gelten.

        Die Lanes bleiben streng getrennt: eine EUR-Reservierung darf kein
        USDC-Guthaben blockieren und umgekehrt.
        """
        lane = str(trade_quote_ccy or "").upper()
        if not lane:
            return 0.0
        art = str(asset_type or "crypto").lower()
        aus = str(ausser_symbol or "").upper()
        summe = 0.0
        for meta in self.nicht_terminale().values():
            if str(meta.get("asset_type") or "").lower() != art:
                continue
            if str(meta.get("trade_quote_ccy") or "").upper() != lane:
                continue
            if aus and str(meta.get("symbol") or "").upper() == aus:
                continue
            offen = float(meta.get("qty") or 0.0) - float(meta.get("filled_qty") or 0.0)
            summe += max(0.0, offen) * float(meta.get("signal_price") or 0.0)
        return summe

    def ueberfaellige(self, *, hoechstalter_sekunden: float,
                      broker: str = "", environment: str = "",
                      account_fingerprint: str = "", asset_type: str = "",
                      jetzt: float | None = None) -> list[dict]:
        """Offene Eintraege, die zu lange ungeklaert sind (9.5.2).

        Anlass: Am 01.09.2026 blieb eine FOK-Kauforder (FET-EUR,
        ordId 3884888641315221505) **10 Stunden 34 Minuten** ungeklaert und
        sperrte in dieser Zeit jeden Kryptoeinstieg -- stumm, unter einem
        Meldungstext, der einen anderen Broker nannte.

        Eine Fill-or-Kill-Order ist beim Broker binnen Millisekunden terminal.
        Bleibt sie hier lange offen, ist NICHT die Order das Problem, sondern
        dass NEXUS ihren Ausgang nicht erfahren hat. Genau das muss sichtbar
        werden, statt als allgemeine Sperre weiterzulaufen.
        """
        grenze = max(0.0, float(hoechstalter_sekunden or 0.0))
        if grenze <= 0:
            return []
        now = float(jetzt if jetzt is not None else time.time())
        quelle = (self.pending_for(broker=broker, environment=environment,
                                   account_fingerprint=account_fingerprint,
                                   asset_type=asset_type)
                  if (broker or environment or account_fingerprint or asset_type)
                  else self.nicht_terminale())
        out: list[dict] = []
        for key, meta in quelle.items():
            erstellt = float((meta or {}).get("created_at_ts") or 0.0)
            if erstellt <= 0:
                continue
            alter = now - erstellt
            if alter < grenze:
                continue
            out.append({
                "key": key,
                "symbol": str((meta or {}).get("symbol") or "").upper(),
                "broker": str((meta or {}).get("broker") or ""),
                "asset_type": str((meta or {}).get("asset_type") or ""),
                "zustand": str((meta or {}).get("zustand") or ""),
                "role": str((meta or {}).get("role") or ""),
                "ord_id": str((meta or {}).get("ord_id")
                              or (meta or {}).get("order_id") or ""),
                "cl_ord_id": str((meta or {}).get("cl_ord_id")
                                 or (meta or {}).get("client_order_id") or ""),
                "decision_id": int((meta or {}).get("decision_id") or 0),
                "alter_sekunden": round(alter, 1),
            })
        out.sort(key=lambda x: -x["alter_sekunden"])
        return out

    def clear_pending(self, symbol, asset_type="stock", **selectors):
        """Remove exactly one completed intent, with the same key as registration.

        Legacy symbol-only callers are accepted only for one unambiguous candidate.
        Domain-aware callers must pass broker/environment/account and decision.
        """
        def change():
            candidates = self._pending_candidates(symbol, asset_type, **selectors)
            if len(candidates) != 1:
                return False
            self.pending.pop(candidates[0][0])
            return True
        return bool(self._mutate(change))

    def register_orders(self, order_ids, symbol, meta=None, owner="BOT", asset_type="stock"):
        at = str(asset_type or (meta or {}).get("asset_type") or "stock").lower()
        base = dict(meta or {})
        now = time.time()
        base.update({"owner": str(owner or "BOT").upper(), "symbol": str(symbol).upper(),
                     "asset_type": at, "canonical_key": _pending_key(symbol, at),
                     "updated_at": now})
        ids = [str(x) for x in (order_ids or []) if str(x)]
        def change():
            if not ids:
                return
            # OKX may return several broker orders on the same trade. Each is
            # independent; a separately supplied FILL is bound via its ord_id.
            groups = [[oid] for oid in ids] if base.get("broker") == "okx" and str(
                base.get("identifier_type") or "").upper() != "FILL" else [ids]
            for explicit in groups:
                update = dict(base)
                if update.get("broker") == "okx" and str(update.get("identifier_type") or "").upper() != "FILL":
                    update["ord_id"] = explicit[0]
                aliases = set(explicit)
                for oid, old in self.orders.items():
                    if _same_order(old, update):
                        aliases.add(oid)
                for oid in aliases:
                    old = self.orders.get(oid, {})
                    if not _compatible_identity(old, update):
                        raise RegistryIdentityConflict(
                            "Order identity conflict; no trade-wide overwrite")
                # Build each alias from its own record: do not leak identifier_type
                # from a fill to its broker order or old exit metadata into entry.
                for oid in sorted(aliases):
                    old = self.orders.get(oid, {})
                    merged = {**old, **update}
                    if oid not in explicit:
                        merged["identifier_type"] = old.get("identifier_type", "ORDER")
                    merged["registered_at"] = old.get("registered_at") or now
                    merged["_order_aliases"] = sorted(aliases)
                    # A late submitted/canceled observation cannot erase an
                    # already proven fill of the SAME order.
                    if str(old.get("zustand") or "").upper() == "FILLED" and str(
                            update.get("zustand") or "").upper() in {
                                "PLANNED", "SUBMITTING", "SUBMITTED", "UNKNOWN_AFTER_SUBMIT",
                                "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
                        merged["last_observed_status"] = update.get("zustand")
                        merged["zustand"] = "FILLED"
                    self.orders[oid] = merged
        self._mutate(change)

    def registered_orders(self, *, asset_type="", broker="") -> dict:
        """Haltbare Orderbelege als Kopien fuer einen Migrationsabgleich."""
        self._refresh()
        wanted_asset = str(asset_type or "").lower()
        wanted_broker = str(broker or "").lower()
        result = {}
        for order_id, raw in self.orders.items():
            meta = dict(raw or {})
            if wanted_asset and str(meta.get("asset_type") or "").lower() != wanted_asset:
                continue
            if wanted_broker and str(meta.get("broker") or "").lower() != wanted_broker:
                continue
            meta.setdefault("ord_id", str(order_id))
            result[str(order_id)] = meta
        return result

    def update_order(self, order_id, **fields) -> bool:
        oid = str(order_id or "")
        if not oid:
            return False
        def change():
            previous = self.orders.get(oid)
            if previous is None:
                return False
            proposed = {**previous, **{k: v for k, v in fields.items() if v is not None}}
            if not _compatible_identity(previous, proposed):
                raise RegistryIdentityConflict("Order identity conflict; no update")
            aliases = {oid}
            declared = set(previous.get("_order_aliases") or [])
            for alias, old in self.orders.items():
                if _same_order(old, previous) or (alias in declared and _compatible_identity(old, previous)):
                    aliases.add(alias)
            for alias in aliases:
                old = self.orders[alias]
                updated = {**old, **{k: v for k, v in fields.items() if v is not None}}
                updated["updated_at"] = time.time()
                if str(old.get("zustand") or "").upper() == "FILLED" and str(
                        fields.get("zustand") or "").upper() in {
                            "SUBMITTING", "SUBMITTED", "CANCELED", "CANCELLED", "REJECTED", "EXPIRED"}:
                    updated["last_observed_status"] = fields["zustand"]
                    updated["zustand"] = "FILLED"
                self.orders[alias] = updated
            return True
        return bool(self._mutate(change))

    def register_ambiguous_order(self, order_id, symbol, meta=None, asset_type="stock"):
        self.register_orders([order_id], symbol, meta, owner="AMBIGUOUS", asset_type=asset_type)

    def metadata(self, order_id):
        self._refresh()
        return dict(self.orders.get(str(order_id), {}))

    def is_bot_order(self, order_id):
        return self.owner(order_id) == "BOT"

    def owner(self, order_id):
        self._refresh()
        return str((self.orders.get(str(order_id)) or {}).get("owner", "") or "").upper()

    def pending_metadata(self, symbol, asset_type="stock", **selectors):
        self._refresh()
        candidates = self._pending_candidates(symbol, asset_type, **selectors)
        if len(candidates) != 1:
            return {}
        meta = candidates[0][1]
        if not darf_per_ttl_verfallen(meta):
            return dict(meta)
        age = time.time() - float(meta.get("created_at_ts", 0) or 0)
        return dict(meta) if 0 <= age <= max(60.0, self.pending_ttl) else {}


def classify_fill_owner(meta, registry, order_id, *, account_fingerprint="",
                        paper=None, broker_name="") -> str:
    """Ordnet einen Fill nur innerhalb derselben Broker-Kontodomaene zu.

    Die Order-ID bleibt der eigentliche Eigentumsbeweis.  Konto, Umgebung und
    Broker sind zusaetzliche Grenzen: Ein korrektes Order-Match aus einem
    anderen Konto oder aus LIVE statt DEMO darf niemals automatisch verwaltet
    werden.  Alte Registry-Eintraege ohne diese Bindung bleiben bei einem
    domaenengebundenen Aufruf deshalb sicherheitshalber ``AMBIGUOUS``.

    Die optionalen Argumente halten alte Aufrufer kompatibel.  Der produktive
    Fillpfad uebergibt sie immer.
    """
    meta = dict(meta or {})
    reg_owner = registry.owner(order_id) if registry is not None else ""
    if bool(meta.get("_ownership_ambiguous")) or reg_owner == "AMBIGUOUS":
        return "AMBIGUOUS"

    expected_account = str(account_fingerprint or "").strip()
    stored_account = str(
        meta.get("account_fingerprint")
        or meta.get("broker_account_fingerprint")
        or ""
    ).strip()
    expected_broker = str(broker_name or "").strip().lower()
    stored_broker = str(meta.get("broker") or "").strip().lower()

    stored_paper = None
    if "paper" in meta:
        stored_paper = bool(meta.get("paper"))
    elif str(meta.get("environment") or "").strip():
        stored_paper = str(meta.get("environment") or "").strip().upper() == "DEMO"

    identity_requested = bool(expected_account or expected_broker or paper is not None)
    identity_complete = True
    identity_matches = True
    if expected_account:
        identity_complete = identity_complete and bool(stored_account)
        identity_matches = identity_matches and stored_account == expected_account
    if expected_broker:
        identity_complete = identity_complete and bool(stored_broker)
        identity_matches = identity_matches and stored_broker == expected_broker
    if paper is not None:
        identity_complete = identity_complete and stored_paper is not None
        identity_matches = identity_matches and stored_paper == bool(paper)

    claimed_bot = reg_owner == "BOT" or str(meta.get("owner") or "").upper() == "BOT"
    # Historisch galt jedes nichtleere Meta-Objekt als BOT-Beweis.  Das war
    # kontouebergreifend zu weit. Ohne geforderte Domaene bleibt dieses
    # Verhalten nur fuer kompatible Alt-/Testaufrufe bestehen.
    if not identity_requested:
        return "BOT" if (bool(meta) or claimed_bot) else "MANUAL"
    if (bool(meta) or claimed_bot) and identity_complete and identity_matches:
        return "BOT"
    if bool(meta) or claimed_bot:
        return "AMBIGUOUS"
    return "MANUAL"
