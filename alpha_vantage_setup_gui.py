from __future__ import annotations
import json
from credential_store import load_credentials, save_credentials
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "alpha_vantage_credentials.json"

def load():
    return load_credentials(FILE, {})

def main():
    saved = load()
    root = tk.Tk()
    root.title("TradingBot 8.1.1 NEXUS – Alpha Vantage einrichten")
    root.geometry("680x360")
    root.minsize(620, 330)
    frm = ttk.Frame(root, padding=22)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Alpha Vantage", font=("Segoe UI", 17, "bold")).grid(row=0,column=0,columnspan=2,sticky="w")
    ttk.Label(frm, text="Dieser Dienst liefert zusätzliche Earnings-/Quartalsdaten. Der API-Key wird ausschließlich lokal gespeichert. Ohne Key handelt der Bot nicht blind: fehlende Earnings-Daten reduzieren die Event-Sicherheit bzw. führen je nach Schutzregel zum Auslassen.", wraplength=610).grid(row=1,column=0,columnspan=2,sticky="w",pady=(5,18))
    key=tk.StringVar(value=str(saved.get("api_key", "")))
    status=tk.StringVar(value="Noch nicht gespeichert.")
    ttk.Label(frm,text="API-Key").grid(row=2,column=0,sticky="w",pady=6)
    ttk.Entry(frm,textvariable=key,show="•",width=52).grid(row=2,column=1,sticky="ew",pady=6)
    def save():
        k=key.get().strip()
        if not k:
            return messagebox.showerror("Fehler","Bitte API-Key eingeben.")
        save_credentials(FILE, {"api_key": k})
        status.set("✅ API-Key lokal gespeichert. Gilt beim nächsten Bot-Start.")
        messagebox.showinfo("Alpha Vantage","API-Key gespeichert.")
    buttons=ttk.Frame(frm);buttons.grid(row=3,column=0,columnspan=2,sticky="ew",pady=(20,8))
    ttk.Button(buttons,text="Speichern",command=save).pack(side="left")
    ttk.Button(buttons,text="Schließen",command=root.destroy).pack(side="right")
    ttk.Label(frm,textvariable=status,wraplength=610).grid(row=4,column=0,columnspan=2,sticky="w",pady=(10,0))
    frm.columnconfigure(1,weight=1)
    root.mainloop()

if __name__ == "__main__":
    main()
