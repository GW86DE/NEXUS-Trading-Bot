"""GUI fuer KI-Aufmerksamkeit, Research-Vorschlaege und Strategy-Auslegung."""
from __future__ import annotations
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import requests
from credential_store import load_credentials, save_credentials

ROOT=Path(__file__).resolve().parent
PATH=ROOT/"openai_ai_settings.json"
DEFAULT_MODEL="gpt-5.6-terra"


def load():
    return load_credentials(PATH,{})


def test_key(key:str, model:str):
    r=requests.get("https://api.openai.com/v1/models",headers={"Authorization":f"Bearer {key.strip()}"},timeout=20)
    try:data=r.json()
    except ValueError:data={}
    if not r.ok:
        raise RuntimeError(str((data.get("error") or {}).get("message") or r.text[:500]))
    ids={str(x.get("id", "")) for x in data.get("data",[]) if isinstance(x,dict)}
    if model not in ids:
        return False,f"API-Key ist gueltig, aber {model} wird fuer diesen Account nicht in /models angezeigt."
    return True,f"API-Key gueltig. {model} ist verfuegbar."


def main():
    old=load(); root=tk.Tk(); root.title("TradingBot 8.1.1 NEXUS – KI-Rollen")
    root.geometry("780x700"); root.minsize(720,650)
    frm=ttk.Frame(root,padding=20); frm.pack(fill="both",expand=True)
    ttk.Label(frm,text="KI-Rollen ohne Orderrechte",font=("Segoe UI",17,"bold")).pack(anchor="w")
    ttk.Label(frm,text=("Drei strikt getrennte Rollen: Live darf KI nur bereits qualifizierte Instrumente priorisieren. "
                             "Weekly Research darf neue Aktien nur als Vorschlag mit Quellen erzeugen; Aufnahme braucht zwei menschliche Telegram-Schritte und eine deterministische eToro-Pruefung. "
                             "Der Strategy Analyst interpretiert nur vorab berechnete eigene Statistiken ohne Web. Keine Rolle darf Orders oder Tradingregeln schreiben."),wraplength=720).pack(anchor="w",pady=(6,16))

    enabled=tk.BooleanVar(value=bool(old.get("enabled",False)))
    research_enabled=tk.BooleanVar(value=bool(old.get("research_enabled",False)))
    strategy_ai=tk.BooleanVar(value=bool(old.get("strategy_analyst_ai",False)))
    web=tk.BooleanVar(value=bool(old.get("web_search",True)))
    key=tk.StringVar(value=str(old.get("api_key","")))
    model=tk.StringVar(value=str(old.get("model",DEFAULT_MODEL) or DEFAULT_MODEL))
    reasoning=tk.StringVar(value=str(old.get("reasoning_effort","low")))
    calls=tk.StringVar(value=str(old.get("max_calls_per_day",12)))
    refresh=tk.StringVar(value=str(old.get("refresh_minutes",30)))
    max_priority=tk.StringVar(value=str(old.get("max_priority",48)))

    ttk.Checkbutton(frm,text="KI-Aufmerksamkeitspriorisierung aktivieren",variable=enabled).pack(anchor="w",pady=3)
    ttk.Checkbutton(frm,text="Wöchentliches Research: 5–10 Vorschläge, Aufnahme nur zweistufig per Telegram",variable=research_enabled).pack(anchor="w",pady=3)
    ttk.Checkbutton(frm,text="Strategy Analyst: KI darf vorab berechnete eigene Statistiken interpretieren (kein Web)",variable=strategy_ai).pack(anchor="w",pady=3)
    ttk.Label(frm,text="OpenAI API-Key").pack(anchor="w",pady=(8,0)); ttk.Entry(frm,textvariable=key,show="*",width=80).pack(fill="x")
    row=ttk.Frame(frm); row.pack(fill="x",pady=10)
    ttk.Label(row,text="Modell").grid(row=0,column=0,sticky="w"); ttk.Entry(row,textvariable=model,width=25).grid(row=1,column=0,sticky="w",padx=(0,15))
    ttk.Label(row,text="Reasoning").grid(row=0,column=1,sticky="w"); ttk.Combobox(row,textvariable=reasoning,values=["low","medium"],state="readonly",width=12).grid(row=1,column=1,sticky="w")
    ttk.Checkbutton(row,text="Websuche fuer Attention (Weekly Research nutzt Web zwingend)",variable=web).grid(row=1,column=2,sticky="w",padx=18)

    grid=ttk.Frame(frm); grid.pack(fill="x",pady=8)
    for i,(label,var) in enumerate((("Max. Priorisierungen/Tag",calls),("Refresh-Minuten",refresh),("Max. priorisierte Instrumente",max_priority))):
        cell=ttk.Frame(grid); cell.grid(row=0,column=i,sticky="ew",padx=(0,16)); grid.grid_columnconfigure(i,weight=1)
        ttk.Label(cell,text=label).pack(anchor="w"); ttk.Entry(cell,textvariable=var,width=14).pack(anchor="w")

    status=tk.StringVar(value="Noch nicht getestet."); ttk.Label(frm,textvariable=status,wraplength=700).pack(anchor="w",pady=10)

    def do_test():
        if not key.get().strip(): messagebox.showwarning("OpenAI","Bitte API-Key eintragen."); return
        try:
            ok,text=test_key(key.get(),model.get().strip() or DEFAULT_MODEL); status.set(text)
            (messagebox.showinfo if ok else messagebox.showwarning)("OpenAI-Test",text)
        except Exception as exc:
            status.set(f"Test fehlgeschlagen: {exc}"); messagebox.showerror("OpenAI-Test",str(exc))

    def do_save():
        try:
            c=int(calls.get()); r=int(refresh.get()); m=int(max_priority.get())
            if not 1<=c<=500: raise ValueError("Priorisierungen/Tag muss 1-500 sein.")
            if not 5<=r<=1440: raise ValueError("Refresh muss 5-1440 Minuten sein.")
            if not 1<=m<=350: raise ValueError("Max. priorisierte Instrumente muss 1-350 sein.")
            if (enabled.get() or research_enabled.get() or strategy_ai.get()) and not key.get().strip(): raise ValueError("Eine aktivierte KI-Rolle erfordert einen API-Key.")
            save_credentials(PATH,{"api_key":key.get().strip(),"enabled":bool(enabled.get()),
                                   "research_enabled":bool(research_enabled.get()),"strategy_analyst_ai":bool(strategy_ai.get()),
                                   "model":model.get().strip() or DEFAULT_MODEL,"research_model":model.get().strip() or DEFAULT_MODEL,
                                   "strategy_model":model.get().strip() or DEFAULT_MODEL,
                                   "reasoning_effort":reasoning.get(),"web_search":bool(web.get()),"max_calls_per_day":c,
                                   "refresh_minutes":r,"max_priority":m})
            messagebox.showinfo("Gespeichert","KI-Rollen gespeichert. Wirksam ab dem naechsten Bot-Start.")
            root.destroy()
        except Exception as exc: messagebox.showerror("Eingabe pruefen",str(exc))

    buttons=ttk.Frame(frm); buttons.pack(fill="x",pady=14)
    ttk.Button(buttons,text="API-Key testen",command=do_test).pack(side="left")
    ttk.Button(buttons,text="Speichern",command=do_save).pack(side="right")
    ttk.Button(buttons,text="Abbrechen",command=root.destroy).pack(side="right",padx=8)
    ttk.Label(frm,text=("Sicherheitsgarantie: Attention-Ausgaben werden auf das gepruefte Universum gefiltert. Research schreibt nur in die Vorschlagsdatei. "
                             "Eine Universumsaufnahme braucht technische eToro-Pruefung plus zweiten autorisierten Telegram-Klick und wird erst beim Neustart aktiv."),wraplength=720).pack(anchor="w",pady=(10,0))
    root.mainloop()

if __name__=="__main__": main()
