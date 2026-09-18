"""Durable, process-safe AI reservations. Unknown usage stays reserved."""
from __future__ import annotations
from datetime import date
from pathlib import Path
import json
import math
import uuid
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock


class AIBudget:
    def __init__(self, datei=None, cfg=None):
        import config
        from ai_router import _zustandswurzel
        self.cfg = cfg or config
        self.datei = Path(datei or _zustandswurzel() / str(
            getattr(self.cfg, 'AI_ROUTER_USAGE_FILE', 'ai_router_usage.json')))
        self.marker = self.datei.with_suffix('.initialized')

    def _leer(self):
        return {'datum': date.today().isoformat(), **{s: {'anfragen': 0,
                'input_tokens': 0, 'output_tokens': 0, 'kosten': 0.0}
                for s in ('luna', 'terra')}, 'reservierungen': {},
                'cache_treffer': 0, 'abgelehnt': 0, 'fehler': 0}

    def lies(self):
        try:
            if not self.datei.exists():
                if self.marker.exists():
                    raise ValueError('Bereits verwendete Budgetdatei fehlt')
                return self._leer()
            d = json.loads(self.datei.read_text(encoding='utf-8'))
            date.fromisoformat(d['datum'])
            for s in ('luna', 'terra'):
                for k in ('anfragen', 'input_tokens', 'output_tokens', 'kosten'):
                    n = float(d[s][k])
                    if not math.isfinite(n) or n < 0:
                        raise ValueError('Ungueltiger Budgetzaehler')
            if d['datum'] > date.today().isoformat():
                raise ValueError('Budgetdatum liegt in der Zukunft')
            if d['datum'] != date.today().isoformat():
                return self._leer()
            d.setdefault('reservierungen', {})
            if not isinstance(d['reservierungen'], dict):
                raise ValueError('Reservierungen unlesbar')
            return d
        except Exception:
            return {**self._leer(), 'gesperrt': True,
                    'grund': 'KI-Budgetstand fehlt oder ist beschaedigt; keine kostenpflichtige Anfrage'}

    def schreibe(self, daten):
        # Marker first: a crash/deletion can never silently reset spent budget.
        with critical_state_lock(self.datei, timeout_seconds=2):
            if not self.marker.exists():
                atomic_write_json(self.marker, {'initialized': True})
            atomic_write_json(self.datei, daten)

    def grenze(self, stufe):
        return max(0, int(getattr(self.cfg,
            'AI_LUNA_MAX_CALLS_PER_DAY' if stufe == 'luna' else 'AI_TERRA_MAX_CALLS_PER_DAY',
            40 if stufe == 'luna' else 8)))

    def kostengrenze(self):
        value = float(getattr(self.cfg, 'AI_MAX_COST_PER_DAY_USD', .50))
        return value if math.isfinite(value) and value > 0 else 0.0

    def _frei(self, d, stufe, kosten=0):
        if d.get('gesperrt'):
            return False, d['grund']
        if stufe not in ('luna', 'terra'):
            return False, 'Unbekannte KI-Stufe'
        if self.grenze(stufe) <= int(d[stufe]['anfragen']):
            return False, f'{stufe.upper()}-Tagesbudget ({self.grenze(stufe)} Anfragen) aufgebraucht'
        used = sum(float(d[s]['kosten']) for s in ('luna', 'terra'))
        if self.kostengrenze() <= 0 or used >= self.kostengrenze() or used + kosten > self.kostengrenze():
            return False, 'Tageskostenbudget erreicht oder fuer diese Anfrage zu klein'
        return True, ''

    def frei(self, stufe):
        return self._frei(self.lies(), stufe)

    def reserviere(self, stufe, kosten):
        amount = float(kosten)
        if not math.isfinite(amount) or amount <= 0:
            return '', 'Anfragekosten nicht belastbar abschaetzbar'
        with critical_state_lock(self.datei, timeout_seconds=2):
            d = self.lies()
            ok, grund = self._frei(d, stufe, amount)
            if not ok:
                return '', grund
            token = uuid.uuid4().hex
            d[stufe]['anfragen'] += 1
            d[stufe]['kosten'] = round(d[stufe]['kosten'] + amount, 8)
            d['reservierungen'][token] = {'stufe': stufe, 'kosten': amount}
            self.schreibe(d)
            return token, ''

    def abschliessen(self, token, *, input_tokens, output_tokens, kosten):
        with critical_state_lock(self.datei, timeout_seconds=2):
            d = self.lies()
            if d.get('gesperrt'):
                raise ValueError(d['grund'])
            r = d['reservierungen'].get(token)
            if not r:  # replay or completion after the daily boundary
                return
            amount = float(kosten)
            if not math.isfinite(amount) or amount < 0 or input_tokens < 0 or output_tokens < 0:
                raise ValueError('Ungueltiger KI-Nutzungsbeleg')
            s = d[r['stufe']]
            s['kosten'] = round(max(0, s['kosten'] - r['kosten']) + amount, 8)
            s['input_tokens'] += int(input_tokens)
            s['output_tokens'] += int(output_tokens)
            del d['reservierungen'][token]
            self.schreibe(d)

    def buche(self, stufe, *, input_tokens=0, output_tokens=0, kosten=0.0):
        """Compatibility for already performed usage; router reserves first."""
        if (stufe not in ('luna', 'terra') or not math.isfinite(float(kosten))
                or float(kosten) < 0 or int(input_tokens) < 0 or int(output_tokens) < 0):
            raise ValueError('Ungueltiger KI-Verbrauchsbeleg')
        with critical_state_lock(self.datei, timeout_seconds=2):
            d = self.lies()
            if d.get('gesperrt'):
                raise ValueError(d['grund'])
            s = d[stufe]
            s['anfragen'] += 1
            s['input_tokens'] += int(input_tokens)
            s['output_tokens'] += int(output_tokens)
            s['kosten'] = round(s['kosten'] + float(kosten), 8)
            self.schreibe(d)

    def zaehle(self, feld):
        if feld not in ('cache_treffer', 'abgelehnt', 'fehler'):
            raise ValueError('Unbekannter KI-Zaehler')
        try:
            with critical_state_lock(self.datei, timeout_seconds=2):
                d = self.lies()
                if not d.get('gesperrt'):
                    d[feld] = int(d.get(feld, 0)) + 1
                    self.schreibe(d)
        except OSError:
            pass  # telemetry only, never grants permission

    def status(self):
        d = self.lies()
        return {**d, **{s: {**d[s], 'grenze': self.grenze(s)} for s in ('luna', 'terra')},
                'kosten_gesamt': round(sum(d[s]['kosten'] for s in ('luna', 'terra')), 6),
                'kostengrenze': self.kostengrenze(),
                'reserviert_usd': round(sum(r['kosten'] for r in d.get('reservierungen', {}).values()), 6)}
