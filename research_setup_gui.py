from __future__ import annotations
import json
from pathlib import Path
import tkinter as tk
from safe_persistence import atomic_write_json
from tkinter import messagebox
ROOT=Path(__file__).resolve().parent
PATH=ROOT/'news_research_settings.json'

def load():
    try:return json.loads(PATH.read_text(encoding='utf-8')) if PATH.exists() else {}
    except Exception:return {}
def save(d):
    atomic_write_json(PATH,d)

def main():
    d=load(); r=tk.Tk(); r.title('Kostenlose Research-Quellen · TradingBot 8.1.1 NEXUS'); r.geometry('720x430'); r.configure(bg='#0b1220')
    fg='#e5e7eb'; muted='#94a3b8'; panel='#111827'
    tk.Label(r,text='Kostenlose Research-Quellen',bg='#0b1220',fg=fg,font=('Segoe UI',19,'bold')).pack(anchor='w',padx=24,pady=(22,6))
    tk.Label(r,text='Diese Version trennt die Quellen nach Aufgabe: SEC fuer harte Unternehmensdaten, Nasdaq fuer Handelsstopps, Yahoo/yfinance und Google News fuer Firmennews. GDELT wird nur noch fuer die breite Markt-/Krisenlage genutzt. Alle Kernwege benoetigen keinen bezahlten News-API-Key.',bg='#0b1220',fg=muted,wraplength=660,justify='left').pack(anchor='w',padx=24,pady=(0,18))
    box=tk.Frame(r,bg=panel); box.pack(fill='x',padx=24,pady=6)
    tk.Label(box,text='SEC Kontakt-E-Mail',bg=panel,fg=fg,font=('Segoe UI',10,'bold')).pack(anchor='w',padx=16,pady=(14,4))
    e=tk.Entry(box,font=('Segoe UI',10)); e.pack(fill='x',padx=16,pady=(0,8)); e.insert(0,d.get('sec_user_agent_email',''))
    gdelt=tk.BooleanVar(value=bool(d.get('gdelt_enabled',True)))
    legacy=tk.BooleanVar(value=bool(d.get('legacy_optional_sources_enabled',False)))
    tk.Checkbutton(box,text='GDELT fuer breite Markt-/Krisenlage verwenden (empfohlen)',variable=gdelt,bg=panel,fg=fg,selectcolor='#0b1220',activebackground=panel,activeforeground=fg).pack(anchor='w',padx=16,pady=(4,3))
    tk.Checkbutton(box,text='Optionale alte/API-Key-Newsanbieter aktivieren',variable=legacy,bg=panel,fg=fg,selectcolor='#0b1220',activebackground=panel,activeforeground=fg).pack(anchor='w',padx=16,pady=(0,8))
    tk.Label(box,text='Die Kontaktadresse wird ausschliesslich im SEC-User-Agent verwendet; sie ist kein Benachrichtigungskanal und kein API-Key.',bg=panel,fg=muted,wraplength=620,justify='left').pack(anchor='w',padx=16,pady=(0,14))
    def go():
        val=e.get().strip()
        if val and '@' not in val:return messagebox.showwarning('SEC','Bitte eine gültige Kontakt-E-Mail eingeben oder das Feld leer lassen.')
        save({'sec_user_agent_email':val,'gdelt_enabled':bool(gdelt.get()),'legacy_optional_sources_enabled':bool(legacy.get())}); messagebox.showinfo('Gespeichert','Einstellung gespeichert. Der Bot liest sie beim nächsten Start ein.')
    tk.Button(r,text='Speichern',command=go,bg='#2563eb',fg='white',relief='flat',padx=18,pady=9).pack(anchor='w',padx=24,pady=18)
    r.mainloop()
if __name__=='__main__':main()
