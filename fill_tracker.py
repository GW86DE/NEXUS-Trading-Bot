"""Robuste Fill-Deduplizierung für echte Executions und kumulative Order-Fills."""
from __future__ import annotations
from dataclasses import replace
from pathlib import Path
import json
import logging
import math
import os
import sqlite3
import shutil
import time
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock

logger = logging.getLogger(__name__)


class FillProgressTracker:
    def __init__(self, path="fill_progress.json"):
        self.path = Path(path)
        self.seen_ids = set()
        self.cumulative = {}
        self.initialized = False
        self.storage_error = ""
        self._loaded_fingerprint = None
        self._load()

    def _load(self):
        if not self.path.exists():
            return
        try:
            with self.path.open(encoding="utf-8") as handle:
                raw = handle.read()
                # fstat belongs to the bytes actually read, even if another
                # writer atomically replaces the pathname during construction.
                self._loaded_fingerprint = self._fingerprint(os.fstat(handle.fileno()))
            if not raw.strip():
                # 9.5.8 contract: a zero-byte checkpoint is equivalent to no
                # checkpoint yet, not corrupt persisted evidence.
                return
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Wurzel ist kein Objekt")
            version = data.get("schema_version", 1)
            if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2}:
                raise ValueError("Unbekannte Fill-Tracker-Schemaversion")
            seen = data.get("seen_ids", [])
            if not isinstance(seen, list) or any(not isinstance(x, str) or not x for x in seen):
                raise ValueError("Fill-IDs muessen eine Liste nichtleerer Zeichenketten sein")
            initialized = data.get("initialized", True)
            if not isinstance(initialized, bool):
                raise ValueError("Initialisierungsstatus muss boolesch sein")
            cumulative = data.get("cumulative", {})
            if not isinstance(cumulative, dict) or any(
                    not isinstance(k, str) or not k or isinstance(v, bool)
                    or not isinstance(v, (int, float)) for k, v in cumulative.items()):
                raise ValueError("Kumulierter Fillfortschritt muss numerische Werte enthalten")
            self.seen_ids = set(seen)
            self.cumulative = {
                k: float(v) for k, v in cumulative.items()
            }
            if any(not math.isfinite(v) or (not k.endswith((":value", ":fees")) and v < 0)
                   for k, v in self.cumulative.items()):
                raise ValueError("Ungueltiger kumulierter Fillfortschritt")
            self.initialized = initialized
            self.storage_error = ""
        except Exception as exc:
            self.storage_error = str(exc)
            logger.error("Fill-Tracker unlesbar; Fill-Verarbeitung bleibt gesperrt: %s", exc)

    @staticmethod
    def _fingerprint(stat):
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

    def _current_fingerprint(self):
        try:
            return self._fingerprint(self.path.stat())
        except FileNotFoundError:
            return None

    def _refresh_locked(self):
        """Merge disk and RAM under the caller's process/thread lock.

        Quantity and its cumulative value/fee checkpoint are one unit. A late
        smaller checkpoint cannot overwrite totals belonging to a larger one.
        This is a checkpoint, not a claim for parallel economic consumers;
        those still require the ledger's unique execution receipt.
        """
        if self.storage_error:
            raise RuntimeError("Fill-Tracker ist unlesbar; Ledger-Recovery erforderlich")
        # A history poll may prepare thousands of already processed fills.
        # Re-read JSON only after a real file change, under the same lock.
        if self._current_fingerprint() == self._loaded_fingerprint:
            return
        disk = FillProgressTracker(self.path)
        if disk.storage_error:
            self.storage_error = disk.storage_error
            raise RuntimeError("Fill-Tracker ist unlesbar; Ledger-Recovery erforderlich")
        merged = dict(disk.cumulative)
        self._merge_cumulative(merged, self.cumulative)
        self.seen_ids |= disk.seen_ids
        self.cumulative = merged
        self.initialized = self.initialized or disk.initialized
        self._loaded_fingerprint = disk._loaded_fingerprint

    @staticmethod
    def _merge_cumulative(target, source):
        for key, quantity in source.items():
            if key.endswith((":value", ":fees")):
                continue
            current = float(target.get(key, 0.0))
            if quantity < current:
                continue
            if quantity > current:
                target[key] = quantity
                for suffix in (":value", ":fees"):
                    target.pop(key + suffix, None)
                    if key + suffix in source:
                        target[key + suffix] = source[key + suffix]
            else:
                target.setdefault(key, quantity)
                for suffix in (":value", ":fees"):
                    field = key + suffix
                    if field not in source:
                        continue
                    if field in target and not math.isclose(target[field], source[field],
                                                            rel_tol=1e-10, abs_tol=1e-12):
                        raise RuntimeError("Widerspruechlicher kumulierter Fillbeleg: " + key)
                    target[field] = source[field]

    def _write_locked(self):
        # Do not discard IDs by lexicographic order: that order says nothing
        # about age or replay eligibility. Existing identities remain intact.
        atomic_write_json(self.path, {
            "schema_version": 2,
            "initialized": bool(self.initialized),
            "seen_ids": sorted(self.seen_ids),
            "cumulative": self.cumulative,
        })
        self._loaded_fingerprint = self._current_fingerprint()


    def recover_from_ledger(self, db_path="decision_history.sqlite") -> dict:
        with critical_state_lock(self.path):
            # Another recovery writer may have repaired the checkpoint while
            # this instance still held the earlier read error in memory.
            latest = FillProgressTracker(self.path)
            if self.storage_error and not latest.storage_error and self.path.is_file():
                self.seen_ids, self.cumulative = latest.seen_ids, latest.cumulative
                self.initialized, self.storage_error = latest.initialized, ""
                self._loaded_fingerprint = latest._loaded_fingerprint
                return {"recovered": False, "reason": "tracker_readable"}
            return self._recover_from_ledger_locked(db_path)

    def _recover_from_ledger_locked(self, db_path) -> dict:
        """Unlesbaren Tracker ausschliesslich aus durablem Ledger rekonstruieren.

        Es wird NICHT blind gegen aktuelle Brokerfills geseedet: das koennte
        Offline-Fills verschlucken. Stattdessen gelten nur Fill-IDs als gesehen,
        die bereits in ``trade_entry_fills``/``trade_exit_events`` verbucht sind.
        Die kaputte Datei bleibt als ``.corrupt-<timestamp>`` erhalten.
        """
        if not self.storage_error:
            return {"recovered": False, "reason": "tracker_readable"}
        # Use exactly the same path resolver as the ledger itself. A relative
        # config filename must never resolve against an arbitrary service CWD.
        from decision_analytics import db_pfad
        db = Path(db_path) if Path(db_path).is_absolute() else Path(db_pfad())
        db = db.resolve()
        if not db.is_file():
            raise RuntimeError(f"Fill-Tracker unlesbar und Ledgerdatenbank fehlt: {db}")
        seen: set[str] = set()
        cumulative: dict[str, float] = {}
        try:
            con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=3)
        except sqlite3.Error as exc:
            raise RuntimeError(f"Fill-Tracker-Recovery: Ledger nicht lesbar: {type(exc).__name__}") from exc
        try:
            con.execute("PRAGMA query_only=ON")
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            if "trade_entry_fills" in tables:
                for fid, oid, qty, price, fee in con.execute(
                    "SELECT fill_id, order_id, quantity, price, fee FROM trade_entry_fills"):
                    if fid: seen.add(str(fid))
                    if oid and qty:
                        key=str(oid); cumulative[key]=max(cumulative.get(key,0.0), float(qty or 0.0))
            if "trade_exit_events" in tables:
                for fid, oid, qty in con.execute(
                    "SELECT fill_id, order_id, quantity FROM trade_exit_events"):
                    if fid: seen.add(str(fid))
                    if oid and qty:
                        key=str(oid); cumulative[key]=max(cumulative.get(key,0.0), float(qty or 0.0))
        finally:
            con.close()
        if self.path.exists():
            quarantine = self.path.with_name(
                self.path.name + f".corrupt-{int(time.time())}")
            shutil.copy2(self.path, quarantine)
        previous = (self.seen_ids, self.cumulative, self.initialized, self.storage_error)
        self.seen_ids, self.cumulative = seen, cumulative
        # Zero durable fills is not a recovery proof. Reset to an explicit
        # uninitialised baseline so the normal broker baseline path can run.
        self.initialized = bool(seen or cumulative)
        self.storage_error = ""
        try:
            self._write_locked()
        except Exception:
            self.seen_ids, self.cumulative, self.initialized, self.storage_error = previous
            raise
        if not self.initialized:
            logger.warning("Fill-Tracker-Recovery fand keine durable Fills; normaler Baseline-Pfad bleibt aktiv")
            return {"recovered": False, "reason": "ledger_empty", "seen_ids": 0, "orders": 0}
        logger.warning("Fill-Tracker aus Ledger rekonstruiert: %d Fill-IDs, %d Orders",
                       len(seen), len(cumulative))
        return {"recovered": True, "seen_ids": len(seen), "orders": len(cumulative)}

    def save(self):
        with critical_state_lock(self.path):
            self._refresh_locked()
            self._write_locked()

    def seed(self, fills):
        """Nur bei einer echten Erstinstallation einen Broker-Baseline setzen.

        Ein vorhandener Tracker darf beim Neustart niemals neu geseedet werden:
        sonst verschwinden genau die Fills, die waehrend der Offlinezeit kamen.
        """
        with critical_state_lock(self.path):
            self._refresh_locked()
            previous = (set(self.seen_ids), dict(self.cumulative), self.initialized)
            try:
                return self._seed_locked(fills)
            except Exception:
                self.seen_ids, self.cumulative, self.initialized = previous
                raise

    def _seed_locked(self, fills):
        if self.initialized:
            return False
        changed = False
        for fill in fills or []:
            if getattr(fill, "quantity_is_cumulative", False):
                key = str(getattr(fill, "order_id", "") or "")
                if not key:
                    continue
                current = max(0.0, float(getattr(fill, "quantity", 0) or 0))
                if current > self.cumulative.get(key, 0.0):
                    self.cumulative[key] = current
                    self.cumulative[key + ":value"] = current * float(getattr(fill, "price", 0.0) or 0.0)
                    fees = getattr(fill, "explicit_fees", None)
                    if fees is not None:
                        self.cumulative[key + ":fees"] = float(fees)
                    changed = True
            else:
                fid = str(getattr(fill, "fill_id", "") or "")
                if fid and fid not in self.seen_ids:
                    self.seen_ids.add(fid)
                    changed = True
        self.initialized = True
        self._write_locked()
        return changed

    def prepare(self, fill):
        """
        Gibt (verarbeitbarer_fill, token) oder (None, None) zurück.
        Aus einer kumulierten Fill-Menge wird eine Delta-Ausfuehrung.
        Commit erfolgt erst NACH erfolgreicher P&L-/Positionsverarbeitung.
        """
        with critical_state_lock(self.path):
            self._refresh_locked()
            return self._prepare_locked(fill)

    def _prepare_locked(self, fill):
        if getattr(fill, "quantity_is_cumulative", False):
            key = str(getattr(fill, "order_id", "") or "")
            if not key:
                return None, None
            current = max(0.0, float(getattr(fill, "quantity", 0) or 0))
            previous = max(0.0, float(self.cumulative.get(key, 0.0)))
            delta = current - previous
            if delta <= 1e-12:
                return None, None
            fid = f"{key}:{current:.12g}"
            # Kumulative Brokerwerte enthalten Durchschnittspreis/Gebuehren fuer
            # die GESAMTE Order. Fuer das neue Delta muessen deshalb Wert und
            # Gebuehren differenziert werden; sonst wird der neue Fill mit dem
            # kumulativen Durchschnitt und kumulativen Gebuehren verbucht.
            if previous > 0 and key + ":value" not in self.cumulative:
                # A quantity-only legacy checkpoint cannot reconstruct the
                # incremental execution price. Never treat its prior value as 0.
                raise RuntimeError(
                    f"Kumulativer Orderwert fehlt ({key}); Ledgerabgleich erforderlich")
            prev_value = float(self.cumulative.get(key + ":value", 0.0) or 0.0)
            current_price = float(getattr(fill, "price", 0.0) or 0.0)
            current_value = current * current_price
            delta_price = ((current_value - prev_value) / delta
                           if delta > 1e-12 else current_price)
            current_fees = getattr(fill, "explicit_fees", None)
            prev_fees = self.cumulative.get(key + ":fees")
            delta_fees = current_fees
            if current_fees is not None and prev_fees is not None:
                delta_fees = float(current_fees) - float(prev_fees)
            prepared = replace(fill, fill_id=fid, quantity=delta,
                               price=delta_price, explicit_fees=delta_fees)
            return prepared, ("cumulative", key, current, current_value, current_fees)

        fid = str(getattr(fill, "fill_id", "") or "")
        if not fid or fid in self.seen_ids:
            return None, None
        return fill, ("id", fid)

    def commit(self, token):
        """Token erst nach DURABLEM Schreiben als gesehen markieren.

        Schlaegt atomic_write_json fehl, wird auch der Arbeitsspeicher auf den
        vorherigen Stand zurueckgesetzt. Damit bleibt derselbe Broker-Fill im
        selben Prozess erneut verarbeitbar (9.7).
        """
        if not token:
            return
        with critical_state_lock(self.path):
            self._refresh_locked()
            self._commit_locked(token)

    def _commit_locked(self, token):
        old_seen = set(self.seen_ids)
        old_cumulative = dict(self.cumulative)
        old_initialized = self.initialized
        try:
            if token[0] == "cumulative":
                _, key, current, *extra = token
                proposed = {str(key): float(current)}
                if extra:
                    proposed[str(key) + ":value"] = float(extra[0] or 0.0)
                    if len(extra) > 1 and extra[1] is not None:
                        proposed[str(key) + ":fees"] = float(extra[1])
                if any(not math.isfinite(v) for v in proposed.values()) or float(current) < 0:
                    raise ValueError("Ungueltiger kumulierter Fillfortschritt")
                self._merge_cumulative(self.cumulative, proposed)
            elif token[0] == "id":
                _, fid = token
                self.seen_ids.add(str(fid))
            else:
                raise ValueError("Unbekannter Fill-Tracker-Token")
            self.initialized = True
            self._write_locked()
        except Exception:
            self.seen_ids = old_seen
            self.cumulative = old_cumulative
            self.initialized = old_initialized
            raise
