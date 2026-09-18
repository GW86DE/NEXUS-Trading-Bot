"""Pacing-sicherer Rotationsscanner fuer grosse Multi-Asset-Universen (v5.8).

Favoriten und ereignisbasierte Prioritaeten werden in jedem Zyklus zuerst
beruecksichtigt. Die restlichen Slots werden proportional auf die beim Broker
tatsaechlich aktiven Asset-Klassen verteilt. Dadurch werden z.B. Kryptos nicht
erst nach einem kompletten Durchlauf durch den gesamten Aktienbestand gescannt.
"""
from __future__ import annotations
from collections import defaultdict
from math import floor


def _key(inst):
    return (str(getattr(inst, "asset_type", "")).lower(), str(getattr(inst, "name", "")).upper())


class RotatingScanScheduler:
    def __init__(self):
        self.cursors: dict[str, int] = defaultdict(int)

    @staticmethod
    def _allocate(counts: dict[str, int], slots: int) -> dict[str, int]:
        active = {k: int(v) for k, v in counts.items() if int(v) > 0}
        out = {k: 0 for k in counts}
        if slots <= 0 or not active:
            return out
        keys = list(active)
        if slots < len(keys):
            for k, _ in sorted(active.items(), key=lambda kv: (-kv[1], kv[0]))[:slots]:
                out[k] = 1
            return out

        # Largest-Remainder direkt auf das Gesamtbudget. Bei 100 Aktien /
        # 10 Kryptos und 11 Slots ergibt das exakt 10/1 und damit fuer beide
        # Klassen dieselbe ungefaehre Vollabdeckungszeit.
        total = sum(active.values())
        raw = {k: slots * active[k] / total for k in keys}
        floors = {k: floor(raw[k]) for k in keys}
        for k,n in floors.items(): out[k]=n
        remainder = slots - sum(floors.values())
        ranked = sorted(keys, key=lambda k: (-(raw[k]-floors[k]), -active[k], k))
        for k in ranked[:remainder]: out[k]+=1

        # Kleine vorhandene Klassen niemals komplett verhungern lassen, wenn
        # genug Slots fuer mindestens einen je Klasse vorhanden sind. Slot
        # wird von der groessten Zuteilung genommen.
        zeros=[k for k in keys if out[k]==0]
        for k in zeros:
            donor=max(keys,key=lambda x: out[x])
            if out[donor] > 1:
                out[donor]-=1; out[k]=1
        return out

    def _rotate(self, kind: str, rows: list, n: int) -> list:
        if not rows or n <= 0:
            return []
        n = min(int(n), len(rows))
        cursor = int(self.cursors.get(kind, 0)) % len(rows)
        out = rows[cursor:cursor + n]
        if len(out) < n:
            out += rows[:n - len(out)]
        self.cursors[kind] = (cursor + n) % len(rows)
        return out

    def select(self, universe: list, budget: int, favorites=None, priority=None) -> list:
        budget = max(1, int(budget or 1))
        favorites = list(favorites or [])
        priority = list(priority or [])

        result = []
        seen = set()
        for inst in favorites + priority:
            k = _key(inst)
            if k in seen:
                continue
            result.append(inst); seen.add(k)
            if len(result) >= budget:
                return result

        regular_slots = budget - len(result)
        classes: dict[str, list] = defaultdict(list)
        for inst in universe:
            k = _key(inst)
            if k in seen:
                continue
            kind = str(getattr(inst, "asset_type", "other") or "other").lower()
            classes[kind].append(inst)

        allocation = self._allocate({k: len(v) for k, v in classes.items()}, regular_slots)
        buckets = []
        # Stabile Reihenfolge, aber Interleaving verhindert grosse Bloecke nur
        # einer Asset-Klasse im Scanner.
        preferred = ["stock", "crypto", "forex"]
        order = preferred + sorted(k for k in classes if k not in preferred)
        for kind in order:
            if kind in classes:
                buckets.append(self._rotate(kind, classes[kind], allocation.get(kind, 0)))

        while any(buckets) and len(result) < budget:
            for bucket in buckets:
                if bucket and len(result) < budget:
                    inst = bucket.pop(0)
                    k = _key(inst)
                    if k not in seen:
                        result.append(inst); seen.add(k)
        return result

    def cursor_snapshot(self) -> dict[str, int]:
        return dict(self.cursors)
