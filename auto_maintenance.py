from __future__ import annotations
import json, subprocess, sys, threading, time
from pathlib import Path
from datetime import datetime, timezone
import config
from automation_status import mark,load

ROOT=Path(__file__).resolve().parent
class AutoMaintenance:
    def __init__(self,notify=lambda *_:None,logger=print):
        self.notify=notify;self.log=logger;self.stop_event=threading.Event();self.thread=None;self.notified={}
        try:
            from weekly_intelligence import WeeklyIntelligence
            self.weekly_intelligence=WeeklyIntelligence()
        except Exception as exc:
            self.weekly_intelligence=None
            try:self.log(f'WeeklyIntelligence konnte nicht initialisiert werden: {exc}')
            except Exception:
                __import__("logging").getLogger(__name__).debug("WeeklyIntelligence-Initfehler konnte nicht geloggt werden",exc_info=True)
    def start(self):
        if not getattr(config,'AUTO_MAINTENANCE_ENABLED',True): return None
        self.thread=threading.Thread(target=self._loop,daemon=True,name='AutoMaintenance');self.thread.start();return self.thread
    def stop(self): self.stop_event.set()
    def _age_days(self,path):
        p=ROOT/path
        if not p.exists(): return None
        return (time.time()-p.stat().st_mtime)/86400
    def _once_daily(self,key,text):
        day=datetime.now().astimezone().strftime('%Y-%m-%d')
        if self.notified.get(key)==day:return
        self.notified[key]=day
        try:self.notify('WARTUNGSHINWEIS',text)
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    def check_once(self):
        ml_age=self._age_days(getattr(config,'MODEL_PATH','ml_model.joblib'))
        ml_limit=float(getattr(config,'ML_MODEL_MAX_AGE_DAYS',30))
        if ml_age is None:
            mark('ml_model','missing','Kein Modell vorhanden')
            if getattr(config,'AUTO_ML_CANDIDATE_TRAIN',False): self._run_task('ml_candidate','train_model_candidate.py')
            elif getattr(config,'AUTO_MAINTENANCE_REMINDERS',True):self._once_daily('ml','ML-Modell fehlt. ML-Training empfohlen, falls der ML-Filter genutzt werden soll.')
        else:
            mark('ml_model','ok' if ml_age<=ml_limit else 'stale',f'{ml_age:.1f} Tage alt',age_days=ml_age)
            if ml_age>ml_limit and getattr(config,'AUTO_ML_CANDIDATE_TRAIN',False): self._run_task('ml_candidate','train_model_candidate.py')
            elif ml_age>ml_limit and getattr(config,'AUTO_MAINTENANCE_REMINDERS',True):self._once_daily('ml',f'ML-Modell ist {ml_age:.0f} Tage alt. Neues Training/Validierung empfohlen.')
        wf_age=self._age_days('walkforward_status.json')
        wf_limit=float(getattr(config,'WALKFORWARD_MAX_AGE_DAYS',7))
        stale = wf_age is None or wf_age>wf_limit
        mark('walkforward','missing' if wf_age is None else ('stale' if stale else 'ok'), 'noch nie ausgeführt' if wf_age is None else f'{wf_age:.1f} Tage alt', age_days=wf_age)
        if stale and getattr(config,'AUTO_WALKFORWARD_RUN',False): self._run_task('walkforward','run_walkforward.py')
        elif stale and getattr(config,'AUTO_MAINTENANCE_REMINDERS',True): self._once_daily('wf','Walk-Forward-Prüfung ist überfällig. Bitte unter Analyse → Walk-Forward prüfen.')
        # Underdog-Datei nur überwachen; der laufende Trader aktualisiert sie automatisch.
        ud_age=self._age_days('underdog_freigabe.json')
        limit=float(getattr(config,'UNDERDOG_SCREENING_HOURS',24))/24
        mark('underdogs','missing' if ud_age is None else ('stale' if ud_age>limit else 'ok'),'noch kein Screening' if ud_age is None else f'{ud_age*24:.1f} Stunden alt',age_days=ud_age)
        # 10.1.10: Taeglich einmal alte Kandidatenentscheidungen ohne Order-/
        # Eventbeleg und reine Heartbeat-Telemetrie entfernen. Orders, Fills,
        # Trades und Belege bleiben unangetastet (DB erzwingt das per FK).
        if getattr(config,'DECISION_HISTORY_PRUNE_ENABLED',True):
            day=datetime.now().astimezone().strftime('%Y-%m-%d')
            if self.notified.get('_decision_prune_day')!=day:
                self.notified['_decision_prune_day']=day
                try:
                    from decision_analytics import prune_history
                    result=prune_history()
                    mark('decision_retention','ok',
                         f"{result['removed_decisions']} alte Entscheidungen / "
                         f"{result['removed_heartbeats']} Heartbeats entfernt",result=result)
                except Exception as exc:
                    mark('decision_retention','error',str(exc))
                    self.log(f'Entscheidungs-Retention fehlgeschlagen: {exc}')
        mark('maintenance','ok','Wartungsprüfung aktiv')
        if self.weekly_intelligence is not None:
            try:
                result=self.weekly_intelligence.check_once()
                mark('weekly_intelligence','ok','Wöchentliche Intelligence-Prüfung aktiv',result=result)
            except Exception as exc:
                mark('weekly_intelligence','error',str(exc))
                self.log(f'WeeklyIntelligence Fehler: {exc}')
    def _run_task(self,name,script):
        state=load().get(name,{})
        if state.get('status')=='running':return
        mark(name,'running',f'{script} läuft automatisch')
        def run():
            try:
                p=subprocess.run([sys.executable,str(ROOT/script)],cwd=str(ROOT),capture_output=True,text=True,timeout=60*45)
                logdir=ROOT/'maintenance_logs';logdir.mkdir(exist_ok=True)
                (logdir/f'{name}_{datetime.now():%Y%m%d_%H%M%S}.log').write_text((p.stdout or '')+'\n'+(p.stderr or ''),encoding='utf-8')
                mark(name,'ok' if p.returncode==0 else 'error',f'Automatik beendet, Code {p.returncode}')
                if p.returncode==0:self.notify('AUTO-WARTUNG',f'{name} automatisch abgeschlossen. Ergebnis im Wartungslog.')
                else:self.notify('AUTO-WARTUNG FEHLER',f'{name} fehlgeschlagen (Code {p.returncode}).')
            except Exception as exc:
                mark(name,'error',str(exc));self.notify('AUTO-WARTUNG FEHLER',f'{name}: {exc}')
        threading.Thread(target=run,daemon=True,name=f'Auto-{name}').start()
    def _loop(self):
        while not self.stop_event.is_set():
            try:self.check_once()
            except Exception as exc:
                try:self.log(f'AutoMaintenance Fehler: {exc}')
                except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            self.stop_event.wait(max(60,int(getattr(config,'AUTO_MAINTENANCE_CHECK_MINUTES',15))*60))
