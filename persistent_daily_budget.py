"""Persistentes, prozesssicheres Tagesbudget fuer externe APIs."""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
import json
import os
import time

from safe_persistence import atomic_write_json
from state_lock import critical_state_lock


class PersistentDailyBudget:
    def __init__(self, name: str, *, limit: int, automatic_limit: int = 0,
                 path: str | Path | None = None,
                 protect_unknown_usage_on_first_day: bool = False):
        root = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                    or Path(__file__).resolve().parent)
        self.path = Path(path) if path else root / "api_daily_budgets.json"
        self.name = str(name).lower()
        self.limit = max(0, int(limit))
        self.automatic_limit = max(0, int(automatic_limit))
        self.protect_unknown_usage_on_first_day = bool(
            protect_unknown_usage_on_first_day)

    def _read(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "budgets": {}}
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("budgets", {}), dict):
            raise RuntimeError("API-Budgetdatei ist unlesbar")
        raw.setdefault("budgets", {})
        return raw

    def _entry(self, data: dict) -> dict:
        today = date.today().isoformat()
        existing = (data.get("budgets") or {}).get(self.name)
        entry = dict(existing or {})
        if str(entry.get("day") or "") != today:
            entry = {"day": today, "used": 0, "automatic_used": 0,
                     "blocked_until": 0.0, "block_reason": ""}
            # Beim ersten Start nach einem Update kennt NEXUS die bereits an
            # diesem Tag direkt bei FMP verbrauchten Calls nicht. Ein blindes
            # Nullsetzen koennte die 250 deshalb noch einmal freigeben. Nur
            # die Automatik wartet in diesem einmaligen Fall bis zum naechsten
            # UTC-Tag; manuelle Diagnose bleibt moeglich.
            if (existing is None and self.protect_unknown_usage_on_first_day
                    and datetime.now(timezone.utc).hour >= 3
                    and self.automatic_limit):
                entry["automatic_used"] = self.automatic_limit
                entry["block_reason"] = (
                    "Erster Tag ohne bekannte FMP-Verbrauchsbasis; "
                    "Automatik startet nach dem Tageswechsel")
        return entry

    def reserve(self, *, purpose: str = "automatic") -> tuple[bool, str]:
        """Pruefen und genau einen Call atomar reservieren."""
        with critical_state_lock(self.path):
            data = self._read()
            entry = self._entry(data)
            now = time.time()
            if float(entry.get("blocked_until") or 0.0) > now:
                return False, str(entry.get("block_reason") or "API voruebergehend gesperrt")
            used = int(entry.get("used") or 0)
            auto_used = int(entry.get("automatic_used") or 0)
            if self.limit and used >= self.limit:
                return False, f"Tagesbudget von {self.limit} Anfragen aufgebraucht"
            automatic = str(purpose).lower() == "automatic"
            if automatic and self.automatic_limit and auto_used >= self.automatic_limit:
                data["budgets"][self.name] = entry
                atomic_write_json(self.path, data)
                return False, (f"Automatikreserve von {self.automatic_limit} Anfragen "
                               "erreicht; Rest bleibt fuer Handel/Diagnose reserviert")
            entry["used"] = used + 1
            if automatic:
                entry["automatic_used"] = auto_used + 1
            data["budgets"][self.name] = entry
            atomic_write_json(self.path, data)
            return True, ""

    def sperren(self, sekunden: float, grund: str) -> None:
        with critical_state_lock(self.path):
            data = self._read(); entry = self._entry(data)
            entry["blocked_until"] = time.time() + max(60.0, float(sekunden))
            entry["block_reason"] = str(grund)[:200]
            data["budgets"][self.name] = entry
            atomic_write_json(self.path, data)

    @property
    def verbraucht(self) -> int:
        return int(self.als_dict()["verbraucht"])

    def frei(self, *, purpose: str = "automatic") -> tuple[bool, str]:
        with critical_state_lock(self.path):
            entry = self._entry(self._read())
        if float(entry.get("blocked_until") or 0.0) > time.time():
            return False, str(entry.get("block_reason") or "API gesperrt")
        if self.limit and int(entry.get("used") or 0) >= self.limit:
            return False, f"Tagesbudget von {self.limit} Anfragen aufgebraucht"
        if (str(purpose).lower() == "automatic" and self.automatic_limit
                and int(entry.get("automatic_used") or 0) >= self.automatic_limit):
            return False, f"Automatikreserve von {self.automatic_limit} erreicht"
        return True, ""

    def als_dict(self) -> dict:
        with critical_state_lock(self.path):
            entry = self._entry(self._read())
        used = int(entry.get("used") or 0)
        auto = int(entry.get("automatic_used") or 0)
        return {"limit": self.limit, "verbraucht": used,
                "rest": max(0, self.limit - used) if self.limit else None,
                "automatic_limit": self.automatic_limit,
                "automatic_used": auto,
                "manual_reserve": max(0, self.limit - used) if self.limit else None,
                "gesperrt": float(entry.get("blocked_until") or 0.0) > time.time(),
                "sperrgrund": str(entry.get("block_reason") or ""),
                "tag": str(entry.get("day") or "")}
