from __future__ import annotations
import json
from credential_store import load_credentials, save_credentials
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

ROOT = Path(__file__).resolve().parent
FILE = ROOT / "news_sources_credentials.json"
ALPHA_FILE = ROOT / "alpha_vantage_credentials.json"


def _load(path):
    return load_credentials(path, {})


def _write(path, data):
    save_credentials(path, data)


def main():
    saved = _load(FILE)
    alpha_saved = _load(ALPHA_FILE)
    root = tk.Tk()
    root.title("TradingBot 8.2 NEXUS – Newsquellen einrichten")
    root.geometry("850x720")
    root.minsize(790, 650)

    frm = ttk.Frame(root, padding=22)
    frm.pack(fill="both", expand=True)
    ttk.Label(frm, text="Optionale Zusatz-/API-Key-Newsquellen", font=("Segoe UI", 17, "bold")).grid(
        row=0, column=0, columnspan=3, sticky="w"
    )
    ttk.Label(
        frm,
        text=(
            "Diese Seite ist fuer optionale Zusatzanbieter gedacht. Der kostenlose Kern nutzt GDELT, "
            "SEC EDGAR/Company Facts, Nasdaq Halts sowie Yahoo/Google News. Finnhub "
            "bleibt eine optionale Zusatzquelle. FMP nutzt dagegen direkt die aktuelle Stable API, sobald FMP "
            "aktiviert und ein API-Key gespeichert ist. Zugangsdaten bleiben lokal auf diesem Pi."
        ),
        wraplength=780,
    ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(5, 16))

    enabled_saved = saved.get("enabled", {}) if isinstance(saved.get("enabled", {}), dict) else {}
    finnhub = tk.StringVar(value=str(saved.get("finnhub_api_key", "")))
    fmp = tk.StringVar(value=str(saved.get("fmp_api_key", "")))
    alpha = tk.StringVar(value=str(alpha_saved.get("api_key", "")))
    sec_email = tk.StringVar(value=str(saved.get("sec_contact_email", "")))
    status = tk.StringVar(value="Noch nicht getestet.")

    toggles = {
        "alpha_vantage": tk.BooleanVar(value=bool(enabled_saved.get("alpha_vantage", True))),
        "finnhub": tk.BooleanVar(value=bool(enabled_saved.get("finnhub", True))),
        "fmp": tk.BooleanVar(value=bool(enabled_saved.get("fmp", True))),
        "sec_edgar": tk.BooleanVar(value=bool(enabled_saved.get("sec_edgar", True))),
    }

    rows = [
        ("Alpha Vantage", "alpha_vantage", "NEWS_SENTIMENT + vorhandene Earnings-Daten."),
        ("Finnhub", "finnhub", "Company News / General Market News."),
        ("Financial Modeling Prep (FMP)", "fmp", "Aktuelle Stable API: Symbolsuche + Stock News; kein Legacy-Hauptschalter noetig."),
        ("SEC EDGAR (Legacy-Schalter)", "sec_edgar", "Der neue Kern nutzt SEC ueber die separate Research-Einrichtung; dieser Schalter bleibt nur fuer Alt-Konfigurationen."),
    ]
    ttk.Label(frm, text="Quelle", font=("Segoe UI", 10, "bold")).grid(row=2, column=0, sticky="w", pady=(0, 6))
    ttk.Label(frm, text="Aktiv", font=("Segoe UI", 10, "bold")).grid(row=2, column=1, sticky="w", pady=(0, 6))
    ttk.Label(frm, text="Hinweis", font=("Segoe UI", 10, "bold")).grid(row=2, column=2, sticky="w", pady=(0, 6))
    rr = 3
    for label, key, hint in rows:
        ttk.Label(frm, text=label).grid(row=rr, column=0, sticky="w", pady=4)
        ttk.Checkbutton(frm, variable=toggles[key]).grid(row=rr, column=1, sticky="w")
        ttk.Label(frm, text=hint, wraplength=480).grid(row=rr, column=2, sticky="w", pady=4)
        rr += 1

    ttk.Separator(frm, orient="horizontal").grid(row=rr, column=0, columnspan=3, sticky="ew", pady=14); rr += 1
    ttk.Label(frm, text="API-/Kontakt-Daten", font=("Segoe UI", 11, "bold")).grid(row=rr, column=0, columnspan=3, sticky="w"); rr += 1

    fields = [
        ("Alpha Vantage API-Key", alpha, True),
        ("Finnhub API-Key", finnhub, True),
        ("FMP API-Key", fmp, True),
        ("SEC Kontakt-E-Mail", sec_email, False),
    ]
    for label, var, secret in fields:
        ttk.Label(frm, text=label).grid(row=rr, column=0, sticky="w", pady=6)
        ttk.Entry(frm, textvariable=var, show="•" if secret else "", width=56).grid(row=rr, column=1, columnspan=2, sticky="ew", pady=6)
        rr += 1

    ttk.Label(
        frm,
        text=(
            "Empfehlung: diese Seite im Normalbetrieb nicht benoetigen. Die kostenlosen Kernquellen werden unter "
            "'Kostenlose Research-Quellen' konfiguriert und funktionieren ohne bezahlten News-API-Key. "
            "Gezielt teure/API-Key-Abfragen bleiben optional und werden gecacht."
        ),
        wraplength=780,
    ).grid(row=rr, column=0, columnspan=3, sticky="w", pady=(4, 10)); rr += 1

    def save_only(show=True):
        data = {
            **_load(FILE),  # Preserve FMP tariff and budget settings from WebUI.
            "finnhub_api_key": finnhub.get().strip(),
            "fmp_api_key": fmp.get().strip(),
            "sec_contact_email": sec_email.get().strip(),
            "enabled": {k: bool(v.get()) for k, v in toggles.items()},
        }
        _write(FILE, data)
        if alpha.get().strip():
            _write(ALPHA_FILE, {"api_key": alpha.get().strip()})
        status.set("✅ Newsquellen-Einstellungen gespeichert. Gilt beim naechsten Bot-Start.")
        if show:
            messagebox.showinfo("Newsquellen", "Einstellungen lokal gespeichert.")

    def test_all():
        try:
            save_only(show=False)
            import importlib
            import config
            importlib.reload(config)
            from news_sources import MultiSourceNews
            client = MultiSourceNews()
            # AAPL ist nur ein Verbindungstest; keine Handelsentscheidung.
            bundle = client.fetch_symbol("AAPL", hours=48)
            conf = client.provider_configuration()
            health = client.health_snapshot()
            ok = sorted(name for name, row in health.items() if row.get("configured") and row.get("healthy"))
            configured = sorted(name for name, active in conf.items() if active)
            failed = {name: row.get("detail", "") for name, row in health.items()
                      if row.get("configured") and row.get("state") in {"error", "backoff"}}
            skipped = [name for name, active in conf.items() if not active]
            msg = (
                f"Tatsaechlich erreichbar: {len(ok)}/{len(configured)}\n"
                f"OK: {', '.join(ok) if ok else 'keine'}\n"
                f"Meldungen nach Deduplizierung: {len(bundle.items)}\n"
                f"Nicht eingerichtet/deaktiviert: {', '.join(skipped) if skipped else 'keine'}"
            )
            if conf.get("FMP Symbol Search"):
                try:
                    fd=client.fmp_diagnostic("AAPL")
                    msg += (f"\nFMP Stable-Test: Symbolsuche {'OK' if fd.get('resolved') else 'KEIN EXAKTER TREFFER'}"
                            f" · Stock-News {fd.get('news',0)}")
                except Exception as fmp_exc:
                    msg += f"\nFMP Stable-Test: FEHLER - {str(fmp_exc)[:160]}"
            if failed:
                msg += "\n\nFehler/Backoff:\n" + "\n".join(f"- {k}: {v[:160]}" for k,v in failed.items())
            status.set(msg.replace("\n", " · "))
            messagebox.showinfo("Newsquellen-Test", msg)
        except Exception as exc:
            status.set(f"❌ {exc}")
            messagebox.showerror("Newsquellen-Test", str(exc))

    buttons = ttk.Frame(frm)
    buttons.grid(row=rr, column=0, columnspan=3, sticky="ew", pady=(16, 8)); rr += 1
    ttk.Button(buttons, text="Speichern", command=save_only).pack(side="left")
    ttk.Button(buttons, text="Alle Quellen testen", command=test_all).pack(side="left", padx=8)
    ttk.Button(buttons, text="Schliessen", command=root.destroy).pack(side="right")
    ttk.Label(frm, textvariable=status, wraplength=780).grid(row=rr, column=0, columnspan=3, sticky="w", pady=(8,0))
    frm.columnconfigure(2, weight=1)
    root.mainloop()


if __name__ == "__main__":
    main()
