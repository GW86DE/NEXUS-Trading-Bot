from __future__ import annotations
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox
import config

ROOT=Path(__file__).resolve().parent
FILE=ROOT/'intelligence_settings.json'

NUMERIC_FIELDS=[
 ('ML_TRAIN_SYMBOL_LIMIT','ML-Training: Anzahl Aktien (0 = alle 100)',int),
 ('EDGE_COST_MULTIPLIER','Kosten-Multiplikator (z.B. 1.5)',float),
 ('EDGE_SAFETY_MARGIN_PCT','Sicherheitsmarge (0.0015 = 0,15 %)',float),
 ('EDGE_ATR_MULTIPLIER','Plausible Bewegung: ATR-Multiplikator',float),
 ('EDGE_TARGET_REALIZATION_FALLBACK','Plausible Bewegung ohne ATR: Anteil am Ziel',float),
 ('EDGE_MAX_PLAUSIBLE_MOVE_PCT','Max. plausible Bewegung pro Trade (0.12 = 12 %)',float),
 ('MAX_SPREAD_STOCK_PCT','Max. Spread Standardaktie',float),
 ('MAX_SPREAD_UNDERDOG_PCT','Max. Spread Underdog',float),
 ('EVENT_BUY_SCORE','Event-Buy Mindestscore (0–100)',int),
 ('EVENT_FAST_CONFIRM_SCORE','Score ab dem schnelle Event-Bestaetigung genutzt wird',int),
 ('UNDERDOG_MIN_EVENT_SCORE','Underdog Mindest-Eventscore',int),
 ('GLOBAL_CRISIS_HARD_BLOCK_SCORE','Krisen-Hardblock Score',int),
 ('EARNINGS_AVOID_HOURS_BEFORE','Keine normalen Käufe vor Earnings (h)',float),
 ('EARNINGS_POST_DRIFT_DAYS','Post-Earnings-Drift Beobachtungstage',int),
 ('MAX_SECTOR_POSITIONS','Max. Positionen je Branche',int),
 ('MAX_SECTOR_EXPOSURE_PCT','Max. Branchenanteil (0.25 = 25 %)',float),
 ('MAX_POSITION_CORRELATION','Max. Korrelation zu offenen Positionen (0.85)',float),
 ('MAX_HIGHLY_CORRELATED_POSITIONS','Max. bereits stark korrelierte Positionen',int),
 ('CORRELATION_LOOKBACK_BARS','Korrelations-Rueckblick in Kerzen',int),
 ('MARKET_RISK_OFF_SIZE_FACTOR','Positionsfaktor im Risk-Off-Markt',float),
 ('LOSS_STREAK_LIMIT','Verlustserie bis Cooldown',int),
 ('LOSS_STREAK_COOLDOWN_MINUTES','Cooldown Minuten',int),
 ('MAX_TRADES_PER_DAY','Max. Trades pro Tag',int),
 ('COST_RATIO_MAX_OF_GROSS_PROFIT','Max. Kostenanteil am positiven Bruttogewinn (0.30 = 30 %)',float),
 ('COST_RATIO_MIN_TRADES','Kostenquoten-Schutz ab Anzahl abgeschlossener Trades',int),
 ('TIME_STOP_HOURS','Time-Stop Stunden',int),
 ('TIME_STOP_MIN_RETURN_PCT','Mindestfortschritt Time-Stop',float),
 ('NEWS_CACHE_MINUTES','News-Cache Minuten',int),
 ('NEWS_RADAR_POLL_SECONDS','News-Radar Abruf Sekunden',int),
 ('NEWS_RADAR_LOOKBACK_MINUTES','News-Radar Rückblick Minuten',int),
 ('NEWS_RADAR_MIN_SCORE','News-Radar Mindestscore',int),
 ('NEWS_RADAR_MAX_PRIORITY','News-Radar max. Prioritätswerte/Zyklus',int),
 ('AUTO_MAINTENANCE_CHECK_MINUTES','Automatik-Statusprüfung alle Minuten',int),
 ('WALKFORWARD_MAX_AGE_DAYS','Walk-Forward spätestens nach Tagen erinnern',int),
 ('ML_MODEL_MAX_AGE_DAYS','ML-Modell spätestens nach Tagen erinnern',int),
]
BOOL_FIELDS=[
 ('EVENT_DRIVEN_BUY_ENABLED','Event-Momentum-Käufe erlauben'),
 ('EVENT_SHORT_CONFIRMATION_ENABLED','Frische Events mit abgeschlossenen 5-Minuten-Kerzen bestätigen'),
 ('REQUIRE_LIVE_QUOTE_FOR_ENTRY','Echten Bid/Ask-Quote für Aktienkauf verlangen'),
 ('REQUIRE_LIVE_CRYPTO_QUOTE_FOR_ENTRY','Echten Krypto-Orderbook Bid/Ask für Kauf verlangen'),
 ('UNDERDOG_REQUIRE_POSITIVE_EVENT','Underdog nur bei positiver Event-Lage'),
 ('MARKET_REGIME_ENABLED','Marktregime-Filter aktiv'),
 ('TIME_STOP_ENABLED','Time-Stop aktiv'),
 ('NEWS_RADAR_ENABLED','Schnellen News-Radar aktivieren'),
 ('CORRELATION_GUARD_ENABLED','Korrelationsschutz fuer offene Aktienpositionen aktivieren'),
 ('COST_RATIO_GUARD_ENABLED','Neue Trades blockieren, wenn Kostenquote zu hoch wird'),
 ('AUTO_MAINTENANCE_ENABLED','Automatische Wartungs-/Altersprüfung aktiv'),
 ('AUTO_MAINTENANCE_REMINDERS','Telegram-Hinweis bei altem ML/Walk-Forward'),
 ('AUTO_UNDERDOG_REFRESH','Underdog-Screening automatisch aktualisieren'),
 ('AUTO_WALKFORWARD_RUN','Walk-Forward bei Überfälligkeit automatisch ausführen (keine Strategieänderung)'),
 ('AUTO_ML_CANDIDATE_TRAIN','ML-Kandidatenmodell automatisch trainieren (wird NICHT automatisch aktiviert)'),
]

def _current_file():
    try:
        return json.loads(FILE.read_text(encoding='utf-8')) if FILE.exists() else {}
    except Exception:
        return {}

def main():
    root=tk.Tk();root.title('TradingBot 8.1.1 NEXUS – Intelligence Einstellungen');root.geometry('820x860')
    root.minsize(760,720)
    canvas=tk.Canvas(root,highlightthickness=0)
    scroll=ttk.Scrollbar(root,orient='vertical',command=canvas.yview)
    outer=ttk.Frame(canvas,padding=18)
    outer.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
    canvas.create_window((0,0),window=outer,anchor='nw')
    canvas.configure(yscrollcommand=scroll.set)
    canvas.pack(side='left',fill='both',expand=True);scroll.pack(side='right',fill='y')

    ttk.Label(outer,text='Intelligence & Kosten',font=('Segoe UI',16,'bold')).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,4))
    ttk.Label(outer,text='Änderungen gelten nach dem nächsten Bot-Neustart. Dezimalwerte sind bewusst sichtbar, damit die Regeln nachvollziehbar bleiben.',wraplength=720).grid(row=1,column=0,columnspan=2,sticky='w',pady=(0,16))
    saved=_current_file(); vars={}; row=2
    for key,label,typ in NUMERIC_FIELDS:
        ttk.Label(outer,text=label).grid(row=row,column=0,sticky='w',pady=5,padx=(0,16))
        v=tk.StringVar(value=str(saved.get(key,getattr(config,key,''))));vars[key]=(v,typ)
        ttk.Entry(outer,textvariable=v,width=30).grid(row=row,column=1,sticky='ew',pady=5)
        row+=1

    ttk.Label(outer,text='Earnings-Einstiegsmodus').grid(row=row,column=0,sticky='w',pady=5,padx=(0,16))
    earnings_mode=tk.StringVar(value=str(saved.get('EARNINGS_ENTRY_MODE',getattr(config,'EARNINGS_ENTRY_MODE','NORMAL'))).upper())
    ttk.Combobox(outer,textvariable=earnings_mode,values=('AGGRESSIVE','NORMAL','DEFENSIVE'),state='readonly',width=27).grid(row=row,column=1,sticky='ew',pady=5)
    row+=1
    ttk.Separator(outer,orient='horizontal').grid(row=row,column=0,columnspan=2,sticky='ew',pady=14);row+=1
    bool_vars={}
    for key,label in BOOL_FIELDS:
        v=tk.BooleanVar(value=bool(saved.get(key,getattr(config,key,False))));bool_vars[key]=v
        ttk.Checkbutton(outer,text=label,variable=v).grid(row=row,column=0,columnspan=2,sticky='w',pady=5);row+=1

    def save():
        data={}
        try:
            for k,(v,t) in vars.items(): data[k]=t(v.get().strip())
            for k,v in bool_vars.items(): data[k]=bool(v.get())
            data['EARNINGS_ENTRY_MODE']=earnings_mode.get().strip().upper()
            if data['EARNINGS_ENTRY_MODE'] not in {'AGGRESSIVE','NORMAL','DEFENSIVE'}: raise ValueError('Earnings-Modus ist ungueltig.')
            if not (0 <= data['MAX_SECTOR_EXPOSURE_PCT'] <= 1): raise ValueError('Max. Branchenanteil muss zwischen 0 und 1 liegen.')
            if not (0 <= data['MARKET_RISK_OFF_SIZE_FACTOR'] <= 1): raise ValueError('Risk-Off Positionsfaktor muss zwischen 0 und 1 liegen.')
            if not (0 <= data['EVENT_BUY_SCORE'] <= 100): raise ValueError('Event-Buy Score muss 0–100 sein.')
            if not (0 <= data['MAX_POSITION_CORRELATION'] <= 1): raise ValueError('Max. Korrelation muss zwischen 0 und 1 liegen.')
            if data['MAX_HIGHLY_CORRELATED_POSITIONS'] < 1: raise ValueError('Korrelations-Positionslimit muss mindestens 1 sein.')
            if not (0 < data['COST_RATIO_MAX_OF_GROSS_PROFIT'] <= 2): raise ValueError('Kostenanteil muss groesser 0 und hoechstens 2 sein.')
            if data['COST_RATIO_MIN_TRADES'] < 1: raise ValueError('Kostenquoten-Mindesttrades muss mindestens 1 sein.')
            if not (0 < data['EDGE_TARGET_REALIZATION_FALLBACK'] <= 1): raise ValueError('Ziel-Realisierungsanteil muss zwischen 0 und 1 liegen.')
            if not (0 < data['EDGE_MAX_PLAUSIBLE_MOVE_PCT'] <= 1): raise ValueError('Max. plausible Bewegung muss zwischen 0 und 1 liegen.')
            FILE.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
            messagebox.showinfo('Gespeichert','Intelligence-Einstellungen gespeichert. Bot/GUI bitte neu starten.')
        except Exception as exc: messagebox.showerror('Fehler',str(exc))
    def reset():
        if FILE.exists(): FILE.unlink()
        messagebox.showinfo('Zurückgesetzt','Overrides entfernt. Nach Neustart gelten wieder die Standardwerte aus config.py.')
        root.destroy()
    buttons=ttk.Frame(outer);buttons.grid(row=row,column=0,columnspan=2,sticky='ew',pady=20)
    ttk.Button(buttons,text='Speichern',command=save).pack(side='left')
    ttk.Button(buttons,text='Auf Standard zurücksetzen',command=reset).pack(side='left',padx=10)
    ttk.Button(buttons,text='Schließen',command=root.destroy).pack(side='right')
    outer.columnconfigure(1,weight=1)
    root.mainloop()

if __name__=='__main__': main()
