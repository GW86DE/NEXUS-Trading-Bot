from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import queue
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime, timezone
from tool_runner import utf8_child_env, iter_stream_chunks

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable

# Optional visual assets / state
MODE_FILE = ROOT / "handelsmodus.txt"
PROFILE_FILE = ROOT / "aktives_profil.txt"
STATE_FILE = ROOT / "bot_zustand.json"
LOG_FILE = ROOT / "trading_bot.log"
VERSION = (ROOT / "VERSION.txt").read_text(encoding="utf-8").strip() if (ROOT / "VERSION.txt").exists() else "6.0 Claude"


def _repair_legacy_test_pnl():
    """Entfernt nur die eindeutig erkennbare v5.2.0-Test-P&L-Signatur.

    Die alte Datei wird als *_TESTDATA_BACKUP_*.json archiviert. Echte/andere
    P&L-Zustaende werden niemals automatisch veraendert.
    """
    try:
        from risk_state_sanity import quarantine_known_test_state
        from risk_state_pfad import zustandsdatei
        return quarantine_known_test_state(zustandsdatei("etoro"))
    except Exception:
        return None


_LEGACY_TEST_PNL_BACKUP = _repair_legacy_test_pnl()

BG = "#0b1220"
PANEL = "#111827"
PANEL2 = "#172033"
TEXT = "#e5e7eb"
MUTED = "#94a3b8"
ACCENT = "#3b82f6"
GREEN = "#22c55e"
RED = "#ef4444"
AMBER = "#f59e0b"
BORDER = "#263247"


def read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return default


def safe_import_config():
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import config
        return config
    except Exception:
        return None


def current_config():
    cfg = safe_import_config()
    broker = "ETORO + OKX"
    mode = read_text(MODE_FILE, "paper").upper()
    profile = read_text(PROFILE_FILE, "ausgewogen").upper()
    crypto = 0
    us = eu = 0
    try:
        crypto = len(getattr(cfg, "CRYPTO_SYMBOLS", []))
        symbols = getattr(cfg, "STOCK_SYMBOLS", [])
        for s in symbols:
            if str(s.get("currency", "USD")).upper() == "USD":
                us += 1
            else:
                eu += 1
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    return broker, mode, profile, us, eu, crypto


def universum_kartentext() -> str:
    """Text der Dashboard-Karte "UNIVERSUM" aus der echten Universumsquelle.

    Bis v8.1.2 zaehlte die Karte ``config.STOCK_SYMBOLS`` und
    ``config.CRYPTO_SYMBOLS``. Das war der statische Katalog, und weil
    ``CRYPTO_SYMBOLS`` in v8 leer ist (die Coins kommen von OKX), stand
    dort strukturell immer "0 Krypto". Ab v8.1.3 liest die Karte
    denselben Universums-Zustand wie die WebUI.
    """
    try:
        from universe_overview import dashboard_text
        return dashboard_text()
    except Exception:
        __import__("logging").getLogger(__name__).debug(
            "Universums-Kennzahlen nicht ermittelbar", exc_info=True)
        return "nicht verfügbar"


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}

def dashboard_state():
    from runtime_status import read_runtime
    runtime = read_runtime(ROOT / "runtime_status.json")
    state = _read_json(STATE_FILE)
    trading = str(state.get("zustand", "aktiv")).upper()
    auto = _read_json(ROOT / "automation_status.json")
    return runtime, trading, auto


def _aktueller_kurs(symbol: str) -> float:
    """Der zuletzt vom Handelskern gemeldete Kurs einer Aktienposition.

    Die Desktop-GUI hat wie die WebUI keine Brokerverbindung. Sie liest die
    Momentaufnahme, die der Kern bei jedem Positionsabgleich schreibt.
    Ist sie nicht da oder ohne Kurs, wird 0.0 zurueckgegeben -- und der
    Aufrufer lehnt ehrlich ab, statt auf den Einstand auszuweichen.
    """
    try:
        daten = _read_json(ROOT / "stock_positions.json")
        gesucht = str(symbol or "").strip().upper()
        for eintrag in (daten.get("positionen") or []):
            if str(eintrag.get("symbol") or "").upper() == gesucht:
                kurs = eintrag.get("kurs")
                return float(kurs) if kurs else 0.0
    except Exception:
        logging.getLogger(__name__).warning(
            "Kursmomentaufnahme fuer %s nicht lesbar -- die Uebernahme wird "
            "deshalb abgelehnt statt geraten.", symbol, exc_info=True)
    return 0.0


def dashboard_pnl():
    """Persistierte realisierte P&L-Werte ohne Broker-API-Aufruf."""
    # v8.1.5: die gemeinsame eToro-Datei, nicht mehr der alte Einzelname.
    try:
        from risk_state_pfad import zustandsdatei
        data = _read_json(zustandsdatei("etoro"))
    except Exception:
        data = {}
    return {
        "today_net": float(data.get("realized_pnl_today", 0) or 0),
        "today_profit": float(data.get("net_profit_today", data.get("gross_profit_today", 0)) or 0),
        "today_loss": float(data.get("net_loss_today", data.get("gross_loss_today", 0)) or 0),
        "total_net": float(data.get("lifetime_realized_pnl", 0) or 0),
        "total_profit": float(data.get("lifetime_net_profit", data.get("lifetime_gross_profit", 0)) or 0),
        "total_loss": float(data.get("lifetime_net_loss", data.get("lifetime_gross_loss", 0)) or 0),
    }


def _pnl_text(value, loss=False):
    value = float(value or 0)
    if loss:
        return f"-{abs(value):,.2f}"
    return f"{value:+,.2f}"

def _age_label(path: Path, stale_days: float):
    if not path.exists():
        return "FEHLT"
    age = (time.time() - path.stat().st_mtime) / 86400
    return f"{age:.1f} TAGE" + (" !" if age > stale_days else "")


class BotGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"TradingBot v{VERSION} • eToro • Telegram")
        self.root.geometry("1440x900")
        self.root.minsize(1120, 720)
        self.root.configure(bg=BG)
        self.process = None
        self.output_thread = None
        self.status_var = tk.StringVar(value="Bereit")
        self.cards = {}
        self.log_paused = tk.BooleanVar(value=False)
        self.log_filter = tk.StringVar(value="ALLE")
        self._tool_windows = []
        self._live_log_queue = queue.Queue()

        self._configure_style()
        self._build_layout()
        self.refresh_dashboard()
        self.refresh_log()
        if _LEGACY_TEST_PNL_BACKUP:
            self.status_var.set("v5.2.0-Test-P&L erkannt und sicher archiviert – Dashboard-P&L wurde bereinigt.")
        self.root.after(1500, self._poll_process)
        self.root.after(2500, self._refresh_loop)

    def _configure_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 22, "bold"))
        style.configure("SubTitle.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 10))
        style.configure("Card.TFrame", background=PANEL, relief="flat")
        style.configure("CardTitle.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("CardValue.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 16, "bold"))
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, rowheight=28, bordercolor=BORDER)
        style.configure("Treeview.Heading", background=PANEL2, foreground=TEXT, font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#1d4ed8")], foreground=[("selected", "white")])

    def _button(self, parent, text, command, color=ACCENT, width=18):
        b = tk.Button(
            parent, text=text, command=command, bg=color, fg="white",
            activebackground=color, activeforeground="white", relief="flat",
            bd=0, padx=12, pady=9, font=("Segoe UI", 10, "bold"), cursor="hand2", width=width
        )
        return b

    def _build_layout(self):
        shell = ttk.Frame(self.root)
        shell.pack(fill="both", expand=True)

        nav = tk.Frame(shell, bg="#0f172a", width=220)
        nav.pack(side="left", fill="y")
        nav.pack_propagate(False)

        tk.Label(nav, text="TRADING\nBOT", bg="#0f172a", fg="white", font=("Segoe UI", 22, "bold"), justify="left").pack(anchor="w", padx=22, pady=(26, 5))
        tk.Label(nav, text=f"v{VERSION} • Decision Intelligence & Telegram", bg="#0f172a", fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=22, pady=(0, 20))

        self.nav_buttons = {}
        for label, page in [
            ("⌂  Dashboard", "dashboard"),
            ("▶  Trading", "trading"),
            ("📊 Analyse", "analyse"),
            ("📋 Entscheidungen", "decisions"),
            ("📜 Logbuch", "logbuch"),
            ("👤 Positionen", "positions"),
            ("🧠 Intelligence", "intelligence"),
            ("🤖 KI-Aufmerksamkeit", "ai_analysis"),
            ("⭐ Favoriten", "favorites"),
            ("🧭 Universum", "universe"),
            ("💼 Broker", "broker"),
            ("🌐 WebUI", "webui"),
            ("⚙  Einstellungen", "settings"),
            ("📨 Kommunikation", "communication"),
            ("🧪 Diagnose", "diagnose"),
        ]:
            btn = tk.Button(nav, text=label, anchor="w", bg="#0f172a", fg=TEXT, activebackground="#1e293b", activeforeground="white", relief="flat", bd=0, font=("Segoe UI", 10), padx=18, pady=10, cursor="hand2", command=lambda p=page: self.show_page(p))
            btn.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[page] = btn

        bottom = tk.Frame(nav, bg="#0f172a")
        bottom.pack(side="bottom", fill="x", padx=14, pady=14)
        self._button(bottom, "🛑  Trading stoppen", self.stop_bot, RED, width=17).pack(fill="x", pady=4)
        self._button(bottom, "▶  Trading starten", self.start_bot, GREEN, width=17).pack(fill="x", pady=4)

        main = tk.Frame(shell, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        header = tk.Frame(main, bg=BG, height=82)
        header.pack(fill="x", padx=28, pady=(20, 0))
        left = tk.Frame(header, bg=BG)
        left.pack(side="left")
        tk.Label(left, text="TradingBot · NEXUS", bg=BG, fg=TEXT, font=("Segoe UI", 22, "bold")).pack(anchor="w")
        tk.Label(left, text=f"v{VERSION} • eToro Aktien + OKX Krypto · KI ohne Orderrechte", bg=BG, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
        right = tk.Frame(header, bg=BG)
        right.pack(side="right", fill="y")
        self.header_status = tk.Label(right, textvariable=self.status_var, bg=BG, fg=GREEN, font=("Segoe UI", 10, "bold"))
        self.header_status.pack(anchor="e", pady=(6, 0))

        # Boersensitzung dauerhaft sichtbar. Erklaert auf einen Blick, warum
        # der Bot gerade nichts tut -- Feiertag, Wochenende oder Feierabend.
        self.session_var = tk.StringVar(value="")
        self.header_session = tk.Label(right, textvariable=self.session_var, bg=BG,
                                       fg=MUTED, font=("Segoe UI", 9))
        self.header_session.pack(anchor="e")
        self._session_aktualisieren()

        self.page = tk.Frame(main, bg=BG)
        self.page.pack(fill="both", expand=True, padx=28, pady=10)
        self.show_page("dashboard")

    def _session_aktualisieren(self):
        """Haelt die Sitzungszeile im Kopf aktuell (jede Minute)."""
        try:
            from market_notifier import sitzungszeile
            from market_calendar import sitzungsstatus
            self.session_var.set(sitzungszeile())
            offen = bool(sitzungsstatus().get("offen"))
            self.header_session.configure(fg=GREEN if offen else MUTED)
        except Exception:
            self.session_var.set("")
        try:
            self.root.after(60000, self._session_aktualisieren)
        except Exception as exc:
            # Beim Schliessen des Fensters existiert die Schleife nicht mehr.
            __import__("logging").getLogger(__name__).debug(
                "Sitzungsaktualisierung nicht neu geplant: %s", exc)

    def clear_page(self):
        for widget in self.page.winfo_children():
            widget.destroy()

    def section_title(self, title, subtitle=None):
        tk.Label(self.page, text=title, bg=BG, fg=TEXT, font=("Segoe UI", 18, "bold")).pack(anchor="w")
        if subtitle:
            tk.Label(self.page, text=subtitle, bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w", pady=(2, 14))

    def card(self, parent, title, value, accent=ACCENT, key=None):
        frame = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        tk.Frame(frame, bg=accent, height=3).pack(fill="x")
        tk.Label(frame, text=title, bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(14, 2))
        var = tk.StringVar(value=value)
        tk.Label(frame, textvariable=var, bg=PANEL, fg=TEXT, font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=16, pady=(0, 16))
        if key:
            self.cards[key] = var
        return frame

    def show_page(self, name):
        for n, b in self.nav_buttons.items():
            b.configure(bg="#1e293b" if n == name else "#0f172a")
        self.clear_page()
        getattr(self, f"page_{name}")()

    def page_dashboard(self):
        self.section_title(
            "Dashboard",
            "Live-Übersicht über Bot, Broker, Risiko, Gewinne/Verluste und Betriebszustand",
        )

        row = tk.Frame(self.page, bg=BG)
        row.pack(fill="x")
        self.card(row, "BROKER", "–", ACCENT, "broker")
        self.card(row, "MODUS", "–", GREEN, "mode")
        self.card(row, "RISIKO-PROFIL", "–", AMBER, "profile")
        self.card(row, "UNIVERSUM", "–", "#8b5cf6", "universe")

        pnl_day = tk.Frame(self.page, bg=BG)
        pnl_day.pack(fill="x", pady=(8, 0))
        self.card(pnl_day, "HEUTE · NETTO REALISIERT", "0,00", "#0f766e", "pnl_today_net")
        self.card(pnl_day, "HEUTE · GEWINNE", "0,00", GREEN, "pnl_today_profit")
        self.card(pnl_day, "HEUTE · VERLUSTE", "0,00", RED, "pnl_today_loss")

        pnl_total = tk.Frame(self.page, bg=BG)
        pnl_total.pack(fill="x", pady=(8, 0))
        self.card(pnl_total, "GESAMT · NETTO REALISIERT", "0,00", "#0f766e", "pnl_total_net")
        self.card(pnl_total, "GESAMT · GEWINNE", "0,00", GREEN, "pnl_total_profit")
        self.card(pnl_total, "GESAMT · VERLUSTE", "0,00", RED, "pnl_total_loss")
        tk.Label(
            self.page,
            text="P&L = vom Bot verbuchte realisierte Trades NETTO nach geschätzten expliziten Broker-/Regulatorikgebühren. Tageswerte resetten täglich; Gesamtwerte bleiben ab v5.2 persistent erhalten.",
            bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=1180, justify="left",
        ).pack(anchor="w", pady=(5, 0))

        statusrow = tk.Frame(self.page, bg=BG)
        statusrow.pack(fill="x", pady=(8, 0))
        self.card(statusrow, "BOT-PROZESS", "–", GREEN, "bot_process")
        self.card(statusrow, "TRADING", "–", GREEN, "trading_state")
        self.card(statusrow, "NEWS / KRISEN", "–", ACCENT, "news_auto")
        self.card(statusrow, "UNDERDOGS", "–", "#8b5cf6", "underdog_auto")
        self.card(statusrow, "WALK-FORWARD", "–", AMBER, "walkforward_age")
        self.card(statusrow, "ML-MODELL", "–", "#db2777", "ml_age")

        connrow = tk.Frame(self.page, bg=BG)
        connrow.pack(fill="x", pady=(8, 0))
        self.card(connrow, "BROKER-VERBINDUNG", "–", ACCENT, "broker_connection")
        self.card(connrow, "LEBENSBIT", "–", GREEN, "broker_liveness")
        self.card(connrow, "LETZTER BROKERKONTAKT", "–", "#0f766e", "broker_contact")
        self.card(connrow, "RECONNECT", "0 aktuell · 0 erfolgreich", AMBER, "reconnect_attempts")

        pirow = tk.Frame(self.page, bg=BG)
        pirow.pack(fill="x", pady=(8, 0))
        self.card(pirow, "PI · CPU / TEMP", "–", "#0891b2", "pi_cpu")
        self.card(pirow, "PI · RAM", "–", "#0f766e", "pi_ram")
        self.card(pirow, "PI · SPEICHER FREI", "–", "#7c3aed", "pi_disk")
        self.card(pirow, "PI · POWER / UPTIME", "–", AMBER, "pi_power")

        lower = tk.Frame(self.page, bg=BG)
        lower.pack(fill="both", expand=True, pady=18)
        left = tk.Frame(
            lower, bg=PANEL, highlightbackground=BORDER, highlightthickness=1
        )
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        tk.Label(
            left,
            text="Schnellzugriff",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", padx=18, pady=(18, 12))
        for text, cmd, color in [
            ("🔌 Verbindung testen", lambda: self.run_script("test_connection.py"), ACCENT),
            ("🧪 Gesamten Selbsttest", lambda: self.run_script("self_test.py"), "#6366f1"),
            ("📦 Paper-Testorder", lambda: self.run_interactive("paper_test_order.py"), "#0891b2"),
            ("📈 Backtest", lambda: self.run_interactive("run_backtest.py"), "#7c3aed"),
            ("🧠 ML trainieren", lambda: self.run_interactive("train_model.py"), "#db2777"),
            ("📋 Entscheidungen auswerten", lambda: self.show_page("decisions"), "#0f766e"),
            ("📰 Kostenlose Research-Quellen", lambda: self.run_interactive("research_setup_gui.py"), "#0369a1"),
            ("🤖 OpenAI KI einrichten", lambda: self.run_interactive("openai_ai_setup.py"), "#7c3aed"),
        ]:
            self._button(left, text, cmd, color, width=26).pack(
                anchor="w", padx=18, pady=5
            )

        right = tk.Frame(
            lower, bg=PANEL, highlightbackground=BORDER, highlightthickness=1
        )
        right.pack(side="left", fill="both", expand=True, padx=(8, 0))
        head=tk.Frame(right,bg=PANEL); head.pack(fill="x",padx=18,pady=(14,8))
        tk.Label(head,text="System-Log",bg=PANEL,fg=TEXT,font=("Segoe UI",12,"bold")).pack(side="left")
        ttk.Combobox(head,textvariable=self.log_filter,values=["ALLE","FEHLER","WARNUNG","TRADE","BROKER","KI","NEWS","SYSTEM"],state="readonly",width=11).pack(side="right",padx=(6,0))
        tk.Checkbutton(head,text="Pause",variable=self.log_paused,bg=PANEL,fg=MUTED,selectcolor=PANEL,activebackground=PANEL,activeforeground=TEXT).pack(side="right",padx=6)
        self.log_text = tk.Text(
            right,
            bg="#0a0f1a",
            fg="#cbd5e1",
            insertbackground="white",
            relief="flat",
            font=("Consolas", 9),
            wrap="none",
        )
        self.log_text.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        for tag,color in [("ERROR",RED),("WARNING",AMBER),("TRADE",GREEN),("AI","#a78bfa"),("NEWS","#38bdf8"),("BROKER","#60a5fa"),("INFO","#cbd5e1")]: self.log_text.tag_configure(tag,foreground=color)
        self._populate_log_widget()

    def page_trading(self):
        self.section_title("Trading", "Trading starten, pausieren oder stoppen und den tatsächlichen Betriebszustand überwachen")
        row = tk.Frame(self.page, bg=BG); row.pack(fill="x")
        self._button(row, "▶ Bot starten", self.start_bot, GREEN, 20).pack(side="left", padx=5)
        self._button(row, "⏸ Trading pausieren", self.pause_bot, AMBER, 20).pack(side="left", padx=5)
        self._button(row, "🛑 Bot stoppen", self.stop_bot, RED, 20).pack(side="left", padx=5)
        self._button(row, "⏸ Zustand ändern", lambda: self.run_interactive("zustand_steuern.py"), AMBER, 20).pack(side="left", padx=5)
        self._button(row, "📋 Orders & Fills", lambda: self.run_interactive("orders_fills_status.py"), "#4f46e5", 20).pack(side="left", padx=5)
        self._button(row, "💼 Depotstatus", lambda: self.run_interactive("depot_status.py"), "#0f766e", 20).pack(side="left", padx=5)

        info = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        info.pack(fill="x", pady=20)
        tk.Label(info, text="Wichtig", bg=PANEL, fg=AMBER, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(16, 4))
        tk.Label(info, text="Der grüne Status bedeutet: Prozess läuft UND Handelszustand ist AKTIV. PAUSIERT blockiert neue Käufe, überwacht bestehende Positionen aber weiter. GESTOPPT deaktiviert auch automatische Verkäufe.", bg=PANEL, fg=TEXT, font=("Segoe UI", 10), wraplength=980, justify="left").pack(anchor="w", padx=18, pady=(0, 16))

    def page_analyse(self):
        self.section_title("Analyse & Training", "Analyse, Walk-Forward, ML und Multi-Source Intelligence")
        groups = [
            ("Strategie", [("Backtest", "run_backtest.py"), ("Walk-Forward", "run_walkforward.py"), ("Profile vergleichen", "profile_vergleich.py"), ("Parameter / Symbol Sweep", "sweep.py")]),
            ("ML", [("ML-Modell trainieren", "train_model.py")]),
            ("News", [("News-/Krisencheck", "news_check.py"), ("Underdog Screening", "underdog_screening.py")]),
        ]
        for title, items in groups:
            box = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            box.pack(fill="x", pady=7)
            tk.Label(box, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 8))
            row = tk.Frame(box, bg=PANEL); row.pack(anchor="w", padx=12, pady=(0, 14))
            for text, script in items:
                self._button(row, text, lambda s=script: self.run_interactive(s), "#334155", 20).pack(side="left", padx=6)

    def page_decisions(self):
        self.section_title("Entscheidungen", "Warum wurde ein BUY freigegeben oder abgelehnt – und wie entwickelte sich der Wert danach?")
        try:
            from decision_analytics import latest as decision_latest, summary as decision_summary, filter_performance
            rows=decision_latest(limit=300)
            ds=decision_summary()
        except Exception as exc:
            tk.Label(self.page,text=f"Decision-Datenbank nicht lesbar: {exc}",bg=BG,fg=RED,font=("Segoe UI",10)).pack(anchor="w",pady=12)
            return

        top=tk.Frame(self.page,bg=BG);top.pack(fill="x",pady=(0,8))
        self.card(top,"KANDIDATEN HEUTE",str(ds.get("candidates",0)),ACCENT)
        self.card(top,"FREIGEGEBEN",str(ds.get("approved",0)),GREEN)
        self.card(top,"MIT FILL BESTÄTIGT",str(ds.get("filled",0)),"#0f766e")
        self.card(top,"ABGELEHNT",str(ds.get("blocked",0)),AMBER)

        reasons=sorted((ds.get("reasons") or {}).items(),key=lambda x:-x[1])[:5]
        reasonbox=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1);reasonbox.pack(fill="x",pady=6)
        txt=" · ".join(f"{k}: {v}" for k,v in reasons) if reasons else "Heute noch keine Ablehnungen."
        tk.Label(reasonbox,text="Häufigste Ablehnungsgründe",bg=PANEL,fg=TEXT,font=("Segoe UI",10,"bold")).pack(anchor="w",padx=14,pady=(10,2))
        tk.Label(reasonbox,text=txt,bg=PANEL,fg=MUTED,font=("Segoe UI",9),wraplength=1120,justify="left").pack(anchor="w",padx=14,pady=(0,10))

        cols=("time","symbol","status","execution","blocked","h1","d1","d5")
        tree=ttk.Treeview(self.page,columns=cols,show="headings",height=14)
        labels={"time":"Zeit","symbol":"Wert","status":"Entscheidung","execution":"Ausführung","blocked":"Grund","h1":"+1h","d1":"+1d","d5":"+5d"}
        widths={"time":125,"symbol":80,"status":105,"execution":120,"blocked":200,"h1":70,"d1":70,"d5":70}
        for c in cols:
            tree.heading(c,text=labels[c]);tree.column(c,width=widths[c],anchor="w")
        def rp(row,h):
            try:
                v=(row.get("outcomes") or {}).get(h,{}).get("return_pct")
                return "–" if v is None else f"{float(v):+.2f}%"
            except Exception:return "–"
        rowmap={}
        for r in rows:
            try:
                t=datetime.fromisoformat(str(r.get("created_at_utc","")).replace("Z","+00:00")).astimezone().strftime("%d.%m %H:%M")
            except Exception:t=str(r.get("created_at_utc",''))[:16]
            iid=tree.insert("","end",values=(t,r.get("symbol","?"),r.get("status",""),r.get("execution_status") or "–",r.get("blocked_by") or "–",rp(r,"1h"),rp(r,"1d"),rp(r,"5d")))
            rowmap[iid]=r
        tree.pack(fill="both",expand=True,pady=6)

        def detail(_evt=None):
            sel=tree.selection()
            if not sel:return
            r=rowmap.get(sel[0],{})
            win=tk.Toplevel(self.root);win.title(f"Entscheidung {r.get('symbol','?')}");win.geometry("880x680");win.configure(bg=BG)
            text=tk.Text(win,bg="#0a0f1a",fg=TEXT,insertbackground="white",font=("Consolas",9),wrap="word");text.pack(fill="both",expand=True,padx=12,pady=12)
            payload=dict(r.get("payload") or {})
            payload["execution_status"]=r.get("execution_status"); payload["order_ids"]=r.get("order_ids"); payload["position_ids"]=r.get("position_ids"); payload["broker_reference_id"]=r.get("broker_reference_id"); payload["fill_price"]=r.get("fill_price"); payload["fill_qty"]=r.get("fill_qty"); payload["outcomes"]=r.get("outcomes")
            text.insert("1.0",json.dumps(payload,ensure_ascii=False,indent=2,default=str));text.configure(state="disabled")
        tree.bind("<Double-1>",detail)
        self._button(self.page,"Ausgewählte Entscheidung öffnen",detail,"#334155",28).pack(anchor="w",pady=(4,10))

        try:
            perf=[x for x in filter_performance(min_samples=3) if x.get("horizon")=="5d"]
        except Exception:
            perf=[]
        if perf:
            box=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1);box.pack(fill="x",pady=6)
            tk.Label(box,text="Filterwirkung nach 5 Tagen (nur Messung, keine automatische Parameteränderung)",bg=PANEL,fg=TEXT,font=("Segoe UI",10,"bold")).pack(anchor="w",padx=14,pady=(10,4))
            for r in perf[:10]:
                kind="SCHUTZ – nicht nach entgangenem Gewinn optimieren" if r.get("category")=="safety" else "Qualitätsfilter"
                tk.Label(box,text=f"{r.get('blocked_by')}: n={r.get('n')} · Ø danach {float(r.get('avg_return') or 0):+.2f}% · {kind}",bg=PANEL,fg=MUTED,font=("Segoe UI",9)).pack(anchor="w",padx=14,pady=2)
            tk.Label(box,text="Sicherheitsfilter werden bewusst separat gekennzeichnet und niemals automatisch gelockert.",bg=PANEL,fg=AMBER,font=("Segoe UI",9,"bold")).pack(anchor="w",padx=14,pady=(6,10))

    def page_intelligence(self):
        self.section_title("Intelligence", "Quartalszahlen, Multi-Source-Daten, Marktregime, Netto-Edge und OpenAI-Aufmerksamkeitspriorisierung")
        cfg = safe_import_config()
        row = tk.Frame(self.page, bg=BG); row.pack(fill="x")
        av = bool(getattr(cfg, "ALPHAVANTAGE_API_KEY", "")) if cfg else False
        sec = bool(getattr(cfg, "SEC_USER_AGENT_EMAIL", "")) if cfg else False
        self.card(row, "FUNDAMENTALS", "SEC AKTIV" if sec else ("ALPHA OPTIONAL" if av else "SEC E-MAIL FEHLT"), GREEN if sec else AMBER)
        self.card(row, "EVENT BUY", "AKTIV" if getattr(cfg,"EVENT_DRIVEN_BUY_ENABLED",False) else "AUS", ACCENT)
        self.card(row, "NETTO-EDGE", f"Kosten x {getattr(cfg,'EDGE_COST_MULTIPLIER',1.5):.1f} + Puffer", "#8b5cf6")
        self.card(row, "SEKTORLIMIT", f"{getattr(cfg,'MAX_SECTOR_POSITIONS',3)} Pos. / {getattr(cfg,'MAX_SECTOR_EXPOSURE_PCT',.25)*100:.0f}%", AMBER)
        row2 = tk.Frame(self.page, bg=BG); row2.pack(fill="x", pady=(8,0))
        self.card(row2, "EARNINGS-MODUS", str(getattr(cfg,'EARNINGS_ENTRY_MODE','NORMAL')), "#0f766e")
        self.card(row2, "KORRELATION", f"max {getattr(cfg,'MAX_POSITION_CORRELATION',.85):.2f} / {getattr(cfg,'MAX_HIGHLY_CORRELATED_POSITIONS',2)} Pos.", "#0891b2")
        self.card(row2, "NEWS-RADAR", "AKTIV" if getattr(cfg,'NEWS_RADAR_ENABLED',True) else "AUS", GREEN if getattr(cfg,'NEWS_RADAR_ENABLED',True) else AMBER)
        self.card(row2, "TAGESLIMIT", f"-{getattr(cfg,'MAX_DAILY_LOSS_PCT',.02)*100:.1f}%", RED)
        row3 = tk.Frame(self.page, bg=BG); row3.pack(fill="x", pady=(8,0))
        ai_on = bool(getattr(cfg, 'AI_ATTENTION_ENABLED', False))
        ai_key = bool(getattr(cfg, 'OPENAI_API_KEY', ''))
        self.card(row3, "OPENAI KI", "AKTIV" if ai_on and ai_key else "AUS / NICHT EINGERICHTET", GREEN if ai_on and ai_key else AMBER)
        self.card(row3, "KI-MODELL", str(getattr(cfg, 'AI_ATTENTION_MODEL', 'gpt-5.6-terra')), "#7c3aed")
        self.card(row3, "KI-LIMIT / TAG", str(getattr(cfg, 'AI_ATTENTION_MAX_CALLS_PER_DAY', 12)), "#0891b2")
        self.card(row3, "KI-ROLLE", "SCAN-PRIORITÄT", "#0f766e")

        groups = [
            ("Quartalszahlen / Research", [("Kostenlose Research-Quellen", "research_setup_gui.py"), ("Gratis-Research fuer Ticker testen", "research_snapshot.py"), ("Alpha Vantage optional", "alpha_vantage_setup_gui.py"), ("Earnings Status", "earnings_status.py")]),
            ("Event & Markt", [("Free Research Status", "free_research_status.py"), ("Optionale Legacy-Newsquellen", "news_sources_setup.py"), ("Newsquellen Status", "news_sources_status.py"), ("Intelligence Status", "intelligence_status.py"), ("News/Krisencheck", "news_check.py"), ("Entscheidungsjournal", "decision_journal_status.py")]),
            ("OpenAI Aufmerksamkeit", [("OpenAI einrichten", "openai_ai_setup.py"), ("KI-Aufmerksamkeitsstatus", "ai_status.py")]),
            ("Kosten & Training", [("Backtest", "run_backtest.py"), ("Walk-Forward", "run_walkforward.py"), ("ML neu trainieren", "train_model.py")]),
            ("Einstellungen", [("Intelligence konfigurieren", "intelligence_einstellungen.py")]),
            ("Pruefung", [("Intelligence Tests", "tests_intelligence.py"), ("Gesamter Selbsttest", "self_test.py")]),
        ]
        for title, items in groups:
            box=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1);box.pack(fill="x",pady=7)
            tk.Label(box,text=title,bg=PANEL,fg=TEXT,font=("Segoe UI",11,"bold")).pack(anchor="w",padx=18,pady=(14,8))
            rr=tk.Frame(box,bg=PANEL);rr.pack(anchor="w",padx=12,pady=(0,14))
            for text,script in items:
                self._button(rr,text,lambda sc=script:self.run_interactive(sc),"#334155",22).pack(side="left",padx=6)

        info=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1);info.pack(fill="x",pady=7)
        tk.Label(info,text=f"Kaufentscheidung v{VERSION}",bg=PANEL,fg=TEXT,font=("Segoe UI",11,"bold")).pack(anchor="w",padx=18,pady=(14,6))
        text=("Technisches Signal ODER starkes Event-Momentum → kostenlose Kernrecherche (SEC + Nasdaq-Halts + Yahoo/yfinance + Google News; GDELT nur fuer breite Marktlage) → "
              "Earnings/Event → Marktregime → Branchen- und Korrelationslimit → aktuelles Bid/Ask → Kommission + Gebühren + Spread + Slippage → "
              "plausible Netto-Edge → GPT-5.6 Terra nur fuer bereits stark gefilterte Aktienkandidaten. Eine zusaetzliche GPT-Websuche wird nur verwendet, "
              "wenn die kostenlosen/verifizierten Quellen fuer die aktuelle Lage nicht ausreichen. Risiko → Order. Die KI darf niemals Tageslimits, Stop-Loss, "
              "Krisen-Exits oder andere harte Sicherheitsregeln ueberstimmen.")
        tk.Label(info,text=text,bg=PANEL,fg=MUTED,font=("Segoe UI",9),wraplength=1050,justify="left").pack(anchor="w",padx=18,pady=(0,14))

    def page_favorites(self):
        self.section_title("Favoriten / Fokusliste", "Bis zu 5 frei waehlbare Aktien oder Kryptos. Favoriten werden in jedem Scanner-Zyklus priorisiert, umgehen aber KEINE Sicherheitsregel.")
        from favorites import load_favorites, save_favorites, resolve_favorite, MAX_FAVORITES
        box=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1); box.pack(fill="x",pady=8)
        row=tk.Frame(box,bg=PANEL); row.pack(fill="x",padx=16,pady=14)
        typ=tk.StringVar(value="auto"); sym=tk.StringVar(); cur=tk.StringVar(value="USD")
        ttk.Combobox(row,textvariable=typ,values=["auto","stock","crypto"],state="readonly",width=10).pack(side="left",padx=5)
        tk.Entry(row,textvariable=sym,width=16,font=("Consolas",10)).pack(side="left",padx=5)
        ttk.Combobox(row,textvariable=cur,values=["USD","EUR","USDC"],state="readonly",width=7).pack(side="left",padx=5)
        tree=ttk.Treeview(self.page,columns=("type","symbol","currency","exchange"),show="headings",height=8)
        for c,t,w in [("type","Typ",100),("symbol","Symbol",150),("currency","Waehrung",100),("exchange","Exchange",150)]: tree.heading(c,text=t); tree.column(c,width=w,anchor="center")
        tree.pack(fill="x",pady=10)
        def reload():
            tree.delete(*tree.get_children())
            for f in load_favorites(): tree.insert("","end",values=(f.asset_type,f.symbol,f.currency,f.exchange))
        def add():
            items=load_favorites()
            if len(items)>=MAX_FAVORITES:return messagebox.showwarning("Favoriten",f"Maximal {MAX_FAVORITES} Favoriten.")
            try:
                nf, grund = resolve_favorite(sym.get(), typ.get(), currency=cur.get())
                if any(x.canonical_key==nf.canonical_key for x in items): return messagebox.showinfo("Favoriten","Bereits enthalten.")
                items.append(nf); save_favorites(items); sym.set(""); reload()
                ziel = "OKX Spot" if nf.asset_type == "crypto" else "eToro Aktien"
                messagebox.showinfo("Favoriten", f"{nf.symbol} → {ziel} ({grund}).\n"
                                    "Der normale Universums- und Sicherheitsablauf entscheidet weiterhin ueber die Aufnahme.")
            except Exception as exc: messagebox.showerror("Favoriten",str(exc))
        def remove():
            sel=tree.selection()
            if not sel:return
            vals=tree.item(sel[0],"values"); key=f"{vals[0]}:{vals[1]}".upper()
            save_favorites([x for x in load_favorites() if x.canonical_key!=key]); reload()
        self._button(row,"+ Hinzufuegen",add,GREEN,15).pack(side="left",padx=8)
        self._button(row,"Entfernen",remove,RED,12).pack(side="left",padx=4)
        reload()
        tk.Label(self.page,text="Automatisch ordnet bekannte Aktien eToro und bekannte Coins OKX Spot zu. Bei einem mehrdeutigen oder unbekannten Symbol waehle bitte einmal Aktie oder Krypto. Ein Favorit wird nur bevorzugt geprueft; Broker-, Liquiditaets-, Score-, Bewaehrungs-, Kosten- und Risikoregeln entscheiden weiterhin, ob er ins Universum darf. Unbekannte Aktien-Sektoren bleiben konservativ als 'Favorit / unbekannt' gruppiert.",bg=BG,fg=MUTED,wraplength=1100,justify="left").pack(anchor="w",pady=8)

    def page_ai_analysis(self):
        self.section_title(
            "KI-Aufmerksamkeit",
            "OpenAI darf nur die Scan-Reihenfolge bereits von eToro qualifizierter Instrumente priorisieren.",
        )
        cfg = safe_import_config()
        row = tk.Frame(self.page, bg=BG); row.pack(fill="x", pady=6)
        enabled = bool(getattr(cfg, "AI_ATTENTION_ENABLED", False)) if cfg else False
        key = bool(getattr(cfg, "OPENAI_API_KEY", "")) if cfg else False
        self.card(row, "ROLLE", "NUR SCAN-PRIORITÄT", GREEN)
        self.card(row, "HANDELSRECHTE", "KEINE", GREEN)
        self.card(row, "STATUS", "AKTIV" if enabled and key else "AUS / NICHT EINGERICHTET", GREEN if enabled and key else AMBER)
        self.card(row, "TAGESBUDGET", str(getattr(cfg, "AI_ATTENTION_MAX_CALLS_PER_DAY", 12)) if cfg else "–", "#0891b2")
        box=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1); box.pack(fill="x",pady=12)
        tk.Label(box,text="Technische Grenze",bg=PANEL,fg=TEXT,font=("Segoe UI",11,"bold")).pack(anchor="w",padx=18,pady=(14,6))
        tk.Label(box,text=(
            "Die KI erhält ausschließlich kanonische Keys des bereits geprüften eToro-Universums. "
            "Ticker, die nicht in dieser Liste stehen, werden verworfen. Ihr Ergebnis wird nur an den "
            "Rotations-Scheduler übergeben. Signal-, News-, Event-, Earnings-, Marktzeit-, Liquiditäts-, "
            "Kosten-, Risiko-, Cash-, Sektor- und Korrelationsfilter bleiben unabhängig und unverändert aktiv. "
            "Bei OpenAI-Ausfall läuft die deterministische Rotation weiter."
        ),bg=PANEL,fg=MUTED,font=("Segoe UI",9),wraplength=1050,justify="left").pack(anchor="w",padx=18,pady=(0,14))
        row2=tk.Frame(self.page,bg=BG); row2.pack(fill="x",pady=8)
        self._button(row2,"OpenAI-Aufmerksamkeit einrichten",lambda:self.run_interactive("openai_ai_setup.py"),"#7c3aed",30).pack(side="left",padx=5)
        self._button(row2,"KI-Aufmerksamkeitsstatus",lambda:self.run_interactive("ai_status.py"),"#334155",26).pack(side="left",padx=5)

    def page_universe(self):
        """Zeigt das ECHTE dynamische Universum -- nicht die statische Liste.

        Bis 8.1.1 stand hier config.STOCK_SYMBOLS/CRYPTO_SYMBOLS, also der
        feste Katalog aus v6. Was der Bot tatsaechlich beobachtet, steht
        aber in universe_state.json: mit Zustand, Rang, Score, Aufnahmezeit
        und Pin. Die alte Anzeige war damit schlicht falsch.
        """
        self.section_title("Universum",
                           "Live-Zustand beider Anbieter · Krypto autonom über OKX · "
                           "Aktien nur mit Telegram-Freigabe")

        kopf = tk.Frame(self.page, bg=BG); kopf.pack(fill="x", pady=(0, 8))
        daten = self._universum_laden()
        if daten.get("fehler"):
            tk.Label(self.page, text=daten["fehler"], bg=BG, fg=RED,
                     font=("Segoe UI", 10)).pack(anchor="w", pady=10)
            return

        for broker, titel, farbe in (("okx", "OKX Krypto", "#2563eb"),
                                     ("etoro", "eToro Aktien", "#0f766e")):
            eintrag = daten["broker"].get(broker, {})
            u = eintrag.get("uebersicht", {})
            self.card(kopf, titel,
                      f"{u.get('handelbar', 0)} / {u.get('aktiv_limit', '?')}", farbe)
        self.card(kopf, "FESTER KERN", ", ".join(daten.get("kernwerte") or []) or "–", "#7c3aed")

        tree = ttk.Treeview(self.page, show="headings", columns=(
            "symbol", "anbieter", "zustand", "merkmale", "rang", "score",
            "aufgenommen", "grund"))
        for col, text, width in [
            ("symbol", "Symbol", 90), ("anbieter", "Anbieter", 110),
            ("zustand", "Zustand", 110), ("merkmale", "Merkmale", 160),
            ("rang", "Rang", 60), ("score", "Score", 70),
            ("aufgenommen", "Aufgenommen vor", 130), ("grund", "Grund", 320),
        ]:
            tree.heading(col, text=text); tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True, pady=10)

        zeilen = 0
        for broker in ("okx", "etoro"):
            eintrag = daten["broker"].get(broker, {})
            for w in eintrag.get("werte", []):
                merkmale = []
                if w.get("kern"): merkmale.append("KERN")
                if w.get("gepinnt"): merkmale.append("POSITION")
                if w.get("favorit"): merkmale.append("FAVORIT")
                if w.get("focus"): merkmale.append(f"FOCUS {w.get('focus_platz') or ''}".strip())
                alter = w.get("alter_stunden")
                alter_text = "–" if alter is None else (
                    f"{alter:.0f} h" if alter < 24 else f"{alter / 24:.1f} Tage")
                tree.insert("", "end", values=(
                    w.get("symbol", ""), eintrag.get("anzeige", broker),
                    w.get("zustand", ""), ", ".join(merkmale) or "–",
                    w.get("rang") or "–", f"{float(w.get('score') or 0):.3f}",
                    alter_text, (w.get("aufnahmegrund") or w.get("abganggrund") or "–")[:120]))
                zeilen += 1

        if not zeilen:
            tk.Label(self.page,
                     text="Noch keine Universumsdaten. Der erste Universumslauf füllt die Liste "
                          "(Krypto alle 15 Minuten, Aktien alle 45 Minuten).",
                     bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=900,
                     justify="left").pack(anchor="w", pady=6)

        tools = tk.Frame(self.page, bg=BG); tools.pack(fill="x")
        self._button(tools, "🔄 Neu laden", lambda: self.show_page("universe"), "#475569", 18).pack(side="left", padx=5)
        self._button(tools, "🌐 In der WebUI öffnen", self.open_webui, "#2563eb", 24).pack(side="left", padx=5)
        self._button(tools, "🐺 Underdog Screening", lambda: self.run_interactive("underdog_screening.py"), "#7c3aed", 22).pack(side="left", padx=5)

    def _universum_laden(self) -> dict:
        """Holt den Universumszustand ueber dieselbe Quelle wie die WebUI."""
        try:
            from webui.state import universe
            return universe()
        except Exception as exc:
            return {"fehler": f"Universum konnte nicht geladen werden: {exc}"}

    def page_broker(self):
        self.section_title("Broker", "Zwei getrennte Broker und Risikotoepfe laufen gleichzeitig")
        row=tk.Frame(self.page,bg=BG); row.pack(fill="x",pady=6)
        self._broker_card(row,"ETORO","Aktien · DEMO/PAPER + LIVE · What-if-Kosten · Hebel 1","etoro","#0f766e","AKTIEN")
        self._broker_card(row,"OKX EEA","Krypto Spot/Cash · 24/7 · getrennte EUR-/USD-/USDC-Kanaele · eigener Risikotopf","okx","#2563eb","KRYPTO")
        box=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1); box.pack(fill="x",pady=18)
        tk.Label(box,text="eToro konfigurieren",bg=PANEL,fg=TEXT,font=("Segoe UI",11,"bold")).pack(anchor="w",padx=18,pady=(14,10))
        self._button(box,"eToro komfortabel einrichten",lambda:self.run_interactive("etoro_setup.py"),"#0f766e",26).pack(anchor="w",padx=18,pady=(0,10))
        self._button(box,"NEXUS / OKX einrichten",lambda:self.run_interactive("nexus_setup.py"),"#2563eb",26).pack(anchor="w",padx=18,pady=(0,10))
        self._button(box,"WebUI-Zugang einrichten",lambda:self.run_interactive("webui_setup.py"),"#334155",26).pack(anchor="w",padx=18,pady=(0,10))
        self._button(box,"eToro DEMO-Testorder",lambda:self.run_interactive("paper_test_order.py"),"#334155",26).pack(anchor="w",padx=18,pady=(0,14))

    def _broker_card(self, parent, title, subtitle, value, color, role="AKTIV"):
        """Zeigt den TATSAECHLICHEN Zustand des Brokers.

        Bis 8.1.1 stand hier fest "AKTIV" im Code -- unabhaengig davon, ob
        eine Verbindung bestand, Zugangsdaten hinterlegt waren oder der
        Broker ueberhaupt eingeschaltet ist. Eine Anzeige, die immer dasselbe
        sagt, ist schlimmer als keine.
        """
        zustand = self._broker_zustand(value)
        f = tk.Frame(parent, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        f.pack(side="left", fill="both", expand=True, padx=6)
        tk.Frame(f, bg=color, height=4).pack(fill="x")
        tk.Label(f, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=16, pady=(14, 4))
        tk.Label(f, text=subtitle, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=380, justify="left").pack(anchor="w", padx=16, pady=(0, 8))
        tk.Label(f, text=f"{zustand['text']} · {role}", bg=PANEL, fg=zustand["farbe"],
                 font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(0, 2))
        tk.Label(f, text=zustand["detail"], bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
                 wraplength=380, justify="left").pack(anchor="w", padx=16, pady=(0, 14))

    def _broker_zustand(self, broker: str) -> dict:
        """Ermittelt Modus, Zugangsdaten und Freigabe eines Brokers."""
        cfg = safe_import_config()
        if cfg is None:
            return {"text": "UNBEKANNT", "farbe": MUTED, "detail": "Konfiguration nicht lesbar"}
        try:
            if broker == "okx":
                aktiv = bool(getattr(cfg, "OKX_ENABLED", False))
                live = bool(getattr(cfg, "OKX_LIVE_TRADING", False))
                hinweis = str(getattr(cfg, "OKX_SETUP_HINWEIS", "") or "")
                modus = "LIVE" if live else "DEMO"
                if not aktiv:
                    return {"text": "NICHT AKTIV", "farbe": MUTED,
                            "detail": hinweis or "Über 'NEXUS / OKX einrichten' aktivieren"}
                return {"text": f"AKTIV · {modus}", "farbe": GREEN,
                        "detail": f"Quote {getattr(cfg, 'OKX_QUOTE_CCY', 'EUR')} · "
                                  f"eigener Risikotopf"}
            aktiv = bool(getattr(cfg, "ETORO_ENABLED", True))
            modus = "LIVE" if not getattr(cfg, "PAPER_TRADING", True) else "PAPER"
            if not aktiv:
                return {"text": "NICHT AKTIV", "farbe": MUTED, "detail": "eToro ist abgeschaltet"}
            return {"text": f"AKTIV · {modus}", "farbe": GREEN,
                    "detail": "Handel nur bei geöffnetem Markt"}
        except Exception as exc:
            return {"text": "UNBEKANNT", "farbe": MUTED, "detail": str(exc)[:120]}

    def open_webui(self):
        """Oeffnet die Weboberflaeche im Standardbrowser."""
        import webbrowser
        adresse = "http://127.0.0.1:8780"
        try:
            import json as _json
            pfad = Path(__file__).resolve().parent / "web_ui_settings.json"
            if pfad.exists():
                web = _json.loads(pfad.read_text(encoding="utf-8"))
                adresse = f"http://{web.get('bind_host', '127.0.0.1')}:{web.get('port', 8780)}"
        except Exception:
            __import__("logging").getLogger(__name__).debug(
                "Web-UI-Einstellungen nicht lesbar; Standardadresse wird benutzt.", exc_info=True)
        try:
            webbrowser.open(adresse)
            self.status_var.set(f"WebUI geöffnet: {adresse}")
        except Exception as exc:
            messagebox.showerror("WebUI", f"Browser konnte nicht geöffnet werden: {exc}")

    def page_settings(self):
        self.section_title("Einstellungen", "Sichere GUI-Schalter für eToro-Modus und Risiko")
        box = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1); box.pack(fill="x", pady=7)
        tk.Label(box, text="Risiko-Profil", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 8))
        row = tk.Frame(box, bg=PANEL); row.pack(anchor="w", padx=12, pady=(0, 14))
        for p, c in [("konservativ", "#0f766e"), ("ausgewogen", "#2563eb"), ("offensiv", "#b91c1c")]:
            self._button(row, p.capitalize(), lambda x=p: self.set_profile(x), c, 18).pack(side="left", padx=6)
        modebox = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1); modebox.pack(fill="x", pady=7)
        tk.Label(modebox, text="Handelsmodus", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 8))
        tk.Label(modebox, text="PAPER kann direkt gesetzt werden. LIVE öffnet bewusst die vorhandene Sicherheits-Checkliste.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 8))
        row2 = tk.Frame(modebox, bg=PANEL); row2.pack(anchor="w", padx=12, pady=(0, 14))
        self._button(row2, "PAPER", lambda: self.set_mode("paper"), GREEN, 18).pack(side="left", padx=6)
        self._button(row2, "LIVE (Checkliste)", lambda: self.run_interactive("handelsmodus.py"), RED, 22).pack(side="left", padx=6)
        migrate = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1); migrate.pack(fill="x", pady=7)
        tk.Label(migrate, text="Update ohne Neueingabe", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 6))
        tk.Label(migrate, text="Übernimmt eToro-, Telegram-, Research-/News-, Favoriten-, Profil- und KI-Aufmerksamkeits-Einstellungen aus einer älteren Version. Nicht mehr unterstuetzte alte Benachrichtigungskanaele werden bewusst nicht migriert. Vorhandene Werte werden nicht überschrieben.", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=980, justify="left").pack(anchor="w", padx=18, pady=(0, 8))
        self._button(migrate, "Einstellungen übernehmen", lambda: self.run_interactive("settings_migration_gui.py"), "#0f766e", 24).pack(anchor="w", padx=18, pady=(0,14))

        quellen = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        quellen.pack(fill="x", pady=7)
        tk.Label(quellen, text="Nachrichtenquellen", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 4))
        tk.Label(quellen,
                 text="Schalter wirken sofort, ohne Neustart. Ausführlich schalten und einzeln "
                      "testen lässt sich alles in der WebUI unter Einstellungen bzw. auf dem Dashboard.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=980, justify="left").pack(anchor="w", padx=18, pady=(0, 8))
        self._quellen_zeilen(quellen)
        row3 = tk.Frame(quellen, bg=PANEL); row3.pack(anchor="w", padx=12, pady=(4, 14))
        self._button(row3, "Quellen einrichten", lambda: self.run_interactive("news_sources_setup.py"), "#0f766e", 20).pack(side="left", padx=6)
        self._button(row3, "Verbindungen prüfen", lambda: self.run_interactive("news_check.py"), "#334155", 20).pack(side="left", padx=6)
        self._button(row3, "🌐 In der WebUI schalten", self.open_webui, "#2563eb", 22).pack(side="left", padx=6)

        cost = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1); cost.pack(fill="x", pady=7)
        tk.Label(cost, text="Aktuelle Kostenannahmen im Core", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 8))
        cfg = safe_import_config()
        if cfg:
            text = (
                "eToro: Keine hart codierte Live-Gebührentabelle. Vor jedem kaufrelevanten Kandidaten werden "
                "die aktuell konto-/instrumentbezogenen What-if-Kosten direkt von der eToro Public API geladen. "
                "Ohne belastbare Kostenquote kein Neueinstieg.\n\n"
                f"OKX: Explizite Handelsgebühr je Seite, gerechnet mit dem Taker-Satz "
                f"({float(getattr(cfg, 'OKX_TAKER_FEE_PCT', 0.001)) * 100:.2f} %), weil der Bot mit "
                "Marktorders handelt. Spread und Slippage-Puffer fließen bei beiden Brokern "
                "in dieselbe Netto-Edge-Hürde ein."
            )
        else:
            text = "Konfiguration konnte nicht geladen werden."
        tk.Label(cost, text=text, bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=1000, justify="left").pack(anchor="w", padx=18, pady=(0, 16))

    def _quellen_zeilen(self, parent):
        """Zeigt je Quelle Schalter, Schluesselzustand und letzten Status.

        Bewusst nur ANZEIGE: geschaltet wird in der WebUI, damit es genau
        eine Stelle gibt, an der Einstellungen geaendert werden.
        """
        try:
            import live_settings
            from news_sources import MultiSourceNews
            schalter = live_settings.alle_schalter()
            vorhanden = live_settings.schluessel_vorhanden()
            gesundheit = MultiSourceNews().health_snapshot()
        except Exception as exc:
            tk.Label(parent, text=f"Quellenstatus nicht lesbar: {exc}", bg=PANEL, fg=RED,
                     font=("Segoe UI", 9)).pack(anchor="w", padx=18, pady=(0, 10))
            return

        anzeige = {
            "finnhub": ("Finnhub", "Finnhub"),
            "fmp": ("FMP", "FMP Symbol Search"),
            "massive": ("MASSIVE", "MASSIVE"),
            "alpha_vantage": ("Alpha Vantage", "Alpha Vantage"),
            "gdelt": ("GDELT", "GDELT"),
            "sec_edgar": ("SEC EDGAR", "SEC EDGAR"),
            "yahoo_finance": ("Yahoo Finance", "Yahoo Finance"),
            "google_news": ("Google News", "Google News"),
            "nasdaq_halts": ("Nasdaq Halts", "Nasdaq Halts"),
        }
        gitter = tk.Frame(parent, bg=PANEL); gitter.pack(fill="x", padx=18, pady=(0, 6))
        for schluessel, (titel, status_name) in anzeige.items():
            an = bool(schalter.get(schluessel))
            zeile = tk.Frame(gitter, bg=PANEL); zeile.pack(fill="x", pady=1)
            tk.Label(zeile, text="●", bg=PANEL, fg=GREEN if an else MUTED,
                     font=("Segoe UI", 11)).pack(side="left")
            tk.Label(zeile, text=f" {titel}", bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold"),
                     width=16, anchor="w").pack(side="left")
            tk.Label(zeile, text="eingeschaltet" if an else "aus", bg=PANEL,
                     fg=GREEN if an else MUTED, font=("Segoe UI", 9), width=14,
                     anchor="w").pack(side="left")
            if schluessel in vorhanden:
                key_text = "Schlüssel gesetzt" if vorhanden[schluessel] else "kein Schlüssel"
                key_farbe = GREEN if vorhanden[schluessel] else "#b45309"
            else:
                key_text, key_farbe = "kein Schlüssel nötig", MUTED
            tk.Label(zeile, text=key_text, bg=PANEL, fg=key_farbe, font=("Segoe UI", 9),
                     width=20, anchor="w").pack(side="left")
            eintrag = gesundheit.get(status_name, {}) if isinstance(gesundheit, dict) else {}
            detail = str(eintrag.get("detail") or "noch nicht geprüft")
            if schluessel == "fmp":
                detail = "Referenzdaten aktiv · Nachrichten nicht im Gratistarif (402)"
            tk.Label(zeile, text=detail[:70], bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
                     anchor="w").pack(side="left", fill="x", expand=True)

    def page_webui(self):
        self.section_title("Weboberfläche",
                           "Bedienung über Browser · von unterwegs nur über WireGuard-VPN")
        cfg = safe_import_config()
        adresse = "http://127.0.0.1:8780"
        try:
            import json as _json
            pfad = Path(__file__).resolve().parent / "web_ui_settings.json"
            if pfad.exists():
                web = _json.loads(pfad.read_text(encoding="utf-8"))
                adresse = f"http://{web.get('bind_host', '127.0.0.1')}:{web.get('port', 8780)}"
        except Exception:
            __import__("logging").getLogger(__name__).debug(
                "Web-UI-Einstellungen nicht lesbar", exc_info=True)

        box = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        box.pack(fill="x", pady=7)
        tk.Label(box, text="Zugang", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 6))
        tk.Label(box, text=f"Adresse: {adresse}", bg=PANEL, fg=TEXT, font=("Segoe UI", 10)).pack(anchor="w", padx=18)
        tk.Label(box,
                 text="Die Weboberfläche läuft als eigener Dienst. Fällt sie aus, handelt der Bot "
                      "unverändert weiter. Von unterwegs erst VPN verbinden, dann die Adresse öffnen "
                      "und zusätzlich am TradingBot anmelden.",
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 9), wraplength=980, justify="left").pack(anchor="w", padx=18, pady=(6, 10))
        row = tk.Frame(box, bg=PANEL); row.pack(anchor="w", padx=12, pady=(0, 14))
        self._button(row, "🌐 WebUI öffnen", self.open_webui, "#2563eb", 20).pack(side="left", padx=6)
        self._button(row, "Zugang einrichten", lambda: self.run_interactive("webui_setup.py"), "#334155", 20).pack(side="left", padx=6)

        seiten = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        seiten.pack(fill="x", pady=7)
        tk.Label(seiten, text="Was die WebUI kann", bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 6))
        for zeile in (
            "Dashboard – Botzustand, beide Broker, Marktphase, Pi-Zustand, News-Verbindungen",
            "Universum – beide Anbieter getrennt, mit Zustand, Rang, Score und Aufnahmezeit",
            "Einstellungen – Zugänge, News-Schalter, Risikoprofil, Demo/LIVE-Auswahl",
            "Logbuch – Entscheidungen und Ablehnungen mit Filtern, dazu das Systemprotokoll",
        ):
            tk.Label(seiten, text="• " + zeile, bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
                     wraplength=980, justify="left").pack(anchor="w", padx=26, pady=1)
        tk.Label(seiten, text="", bg=PANEL).pack(pady=4)

    def page_communication(self):
        self.section_title("Kommunikation", "Telegram ist der einzige Benachrichtigungs- und Fernsteuerungskanal")
        groups = [
            ("✈️ Telegram", [("Telegram komfortabel einrichten", "telegram_setup.py"), ("Telegram testen", "telegram_test.py"), ("Telegram Status", "telegram_status.py"), ("Telegram an/aus", "telegram_toggle.py")]),
            ("📊 18-Uhr-Bericht", [("Entscheidungsjournal Status", "decision_journal_status.py"), ("KI-Aufmerksamkeitsstatus", "ai_status.py"), ("Newsquellen Status", "news_sources_status.py")]),
        ]
        for title, items in groups:
            box = tk.Frame(self.page, bg=PANEL, highlightbackground=BORDER, highlightthickness=1); box.pack(fill="x", pady=7)
            tk.Label(box, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=18, pady=(14, 8))
            row = tk.Frame(box, bg=PANEL); row.pack(anchor="w", padx=12, pady=(0, 14))
            for text, script in items:
                self._button(row, text, lambda s=script: self.run_interactive(s), "#334155", 20).pack(side="left", padx=6)
        note=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1);note.pack(fill="x",pady=10)
        tk.Label(note,text="Tagesbericht",bg=PANEL,fg=TEXT,font=("Segoe UI",11,"bold")).pack(anchor="w",padx=18,pady=(14,5))
        tk.Label(note,text="Einmal täglich ab 18:00 Uhr (Europe/Berlin) sendet der Bot einen ausführlichen Telegram-Bericht. Ist Telegram/Internet kurz offline, bleibt die Nachricht in der persistenten Queue und wird nachgeliefert.",bg=PANEL,fg=MUTED,font=("Segoe UI",9),wraplength=1000,justify="left").pack(anchor="w",padx=18,pady=(0,14))

    def page_positions(self):
        self.section_title("Positionen & Herkunft", "Manuelle Broker-Trades werden erkannt, ins Risiko eingerechnet und standardmäßig nur beobachtet")
        try:
            from position_manager import PositionManager
            pm=PositionManager(ROOT / "position_state.json")
            records=sorted(pm.records.values(),key=lambda r:str(r.symbol))
        except Exception as exc:
            tk.Label(self.page,text=f"Positions-State nicht lesbar: {exc}",bg=BG,fg=RED).pack(anchor="w",pady=12); return

        note=tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1);note.pack(fill="x",pady=(0,10))
        tk.Label(note,text="Sicherheitsregel",bg=PANEL,fg=AMBER,font=("Segoe UI",10,"bold")).pack(anchor="w",padx=14,pady=(10,2))
        tk.Label(note,text="BOT-Positionen werden automatisch verwaltet. MANUAL/BROKER_EXISTING/MIXED bleiben OBSERVE und können nicht automatisch verkauft werden. Eine Übernahme wird erst aktiv, wenn der laufende Broker den Schutz der Position erfolgreich bestätigt.",bg=PANEL,fg=MUTED,font=("Segoe UI",9),wraplength=1120,justify="left").pack(anchor="w",padx=14,pady=(0,10))

        cols=("symbol","source","mode","qty","avg","stop","take","note")
        tree=ttk.Treeview(self.page,columns=cols,show="headings",height=18)
        widths={"symbol":90,"source":120,"mode":120,"qty":90,"avg":100,"stop":100,"take":100,"note":360}
        titles={"symbol":"Wert","source":"Herkunft","mode":"Verwaltung","qty":"Menge","avg":"Einstand","stop":"Stop","take":"Take-Profit","note":"Hinweis"}
        for c in cols: tree.heading(c,text=titles[c]); tree.column(c,width=widths[c],anchor="w")
        tree.pack(fill="both",expand=True,pady=6)
        rec_by_item={}
        for r in records:
            iid=tree.insert("","end",values=(r.symbol,r.source,r.management_mode,f"{r.quantity:g}",f"{r.avg_cost:.6g}",f"{r.planned_stop:.6g}" if r.planned_stop else "–",f"{r.planned_take:.6g}" if r.planned_take else "–",r.management_note or ""))
            rec_by_item[iid]=r

        buttons=tk.Frame(self.page,bg=BG);buttons.pack(fill="x",pady=8)
        def selected_record():
            sel=tree.selection()
            if not sel: messagebox.showinfo("Position","Bitte zuerst eine Position auswählen."); return None
            return rec_by_item.get(sel[0])
        def observe():
            r=selected_record()
            if not r:return
            pm.set_observe_only(r.symbol,"Vom Nutzer auf Nur beobachten gesetzt.")
            self.show_page("positions")
        def takeover():
            r=selected_record()
            if not r:return
            cfg=safe_import_config()
            # v8.1.5: Die alte Pruefung "nur Instrumente im konfigurierten
            # Universum" ist entfallen. Eine selbst gekaufte Position gehoert
            # naturgemaess nicht ins konfigurierte Universum -- deshalb hat sie
            # genau die Uebergabe blockiert, um die es geht. Der Bot verwaltet
            # sie ab jetzt als USER_MANAGED; ins Kaufuniversum kommt sie
            # dadurch nicht.
            kurs=_aktueller_kurs(r.symbol)
            if kurs<=0:
                return messagebox.showwarning(
                    "Übernahme nicht möglich",
                    "Für diese Position liegt gerade kein aktueller Kurs vor.\n\n"
                    "Stop und Ziel werden am aktuellen Kurs geprüft, nicht am "
                    "Einstand — ohne Kurs wäre die Prüfung geraten. Bitte erneut "
                    "versuchen, sobald der Handelskern Kursdaten gemeldet hat.")
            default_stop=(r.planned_stop if r.planned_stop>0 else kurs*(1-float(getattr(cfg,"STOP_LOSS_PCT",0.025))))
            default_take=(r.planned_take if r.planned_take>0 else kurs*(1+float(getattr(cfg,"TAKE_PROFIT_PCT",0.05))))
            stop=simpledialog.askfloat("Bot-Verwaltung übernehmen",f"Stop-Loss für {r.symbol}:\nMuss unter dem aktuellen Kurs {kurs:.6g} liegen.\n(Einstand war {r.avg_cost:.6g}.)",initialvalue=round(default_stop,8),parent=self.root)
            if stop is None:return
            take=simpledialog.askfloat("Bot-Verwaltung übernehmen",f"Take-Profit für {r.symbol}:\nMuss über dem aktuellen Kurs {kurs:.6g} liegen.",initialvalue=round(default_take,8),parent=self.root)
            if take is None:return
            ok,msg=pm.request_takeover(r.symbol,stop,take,aktueller_kurs=kurs)
            if not ok:return messagebox.showerror("Übernahme",msg)
            messagebox.showinfo("Übernahme angefordert",msg+"\n\nDer laufende Trading-Core prüft jetzt brokerseitigen Schutz. Bis zur Bestätigung bleibt die Position OBSERVE.")
            self.show_page("positions")
        self._button(buttons,"🔒 Nur beobachten",observe,"#475569",20).pack(side="left",padx=5)
        self._button(buttons,"🤖 Bot-Verwaltung übernehmen",takeover,"#0f766e",26).pack(side="left",padx=5)
        self._button(buttons,"↻ Aktualisieren",lambda:self.show_page("positions"),ACCENT,18).pack(side="left",padx=5)

    # -----------------------------------------------------------------
    # LOGBUCH -- eigene Seite statt zwei Zeilen im Dashboard
    # -----------------------------------------------------------------
    def page_logbuch(self):
        """
        Vollflaechiges Logbuch mit Filter, Volltextsuche und Export.

        Bis 5.12 war das Protokoll nur ein schmaler Kasten auf dem Dashboard.
        Bei einem Fehler musste man in die Datei wechseln, um etwas zu sehen.
        Diese Seite zeigt es in voller Hoehe und laesst gezielt suchen.
        """
        self.section_title(
            "Logbuch",
            "Vollstaendiges Protokoll mit Filter, Volltextsuche und Export",
        )
        wrap = tk.Frame(self.page, bg=BG)
        wrap.pack(fill="both", expand=True)

        # --- Kopfzeile mit Werkzeugen ---
        kopf = tk.Frame(wrap, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        kopf.pack(fill="x", pady=(0, 10))

        zeile1 = tk.Frame(kopf, bg=PANEL)
        zeile1.pack(fill="x", padx=16, pady=(12, 6))
        tk.Label(zeile1, text="Logbuch", bg=PANEL, fg=TEXT,
                 font=("Segoe UI", 15, "bold")).pack(side="left")
        self.logbuch_info = tk.Label(zeile1, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        self.logbuch_info.pack(side="left", padx=(14, 0))

        tk.Button(zeile1, text="Exportieren", command=self._logbuch_export,
                  bg=PANEL2, fg=TEXT, activebackground=ACCENT, activeforeground="white",
                  relief="flat", bd=0, padx=14, pady=5, cursor="hand2",
                  font=("Segoe UI", 9)).pack(side="right", padx=(6, 0))
        tk.Button(zeile1, text="Aktualisieren", command=self._logbuch_laden,
                  bg=PANEL2, fg=TEXT, activebackground=ACCENT, activeforeground="white",
                  relief="flat", bd=0, padx=14, pady=5, cursor="hand2",
                  font=("Segoe UI", 9)).pack(side="right", padx=(6, 0))

        zeile2 = tk.Frame(kopf, bg=PANEL)
        zeile2.pack(fill="x", padx=16, pady=(0, 12))

        tk.Label(zeile2, text="Suche:", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.logbuch_suche = tk.StringVar()
        such_feld = tk.Entry(zeile2, textvariable=self.logbuch_suche, bg="#0a0f1a", fg=TEXT,
                             insertbackground="white", relief="flat", width=34,
                             font=("Segoe UI", 10))
        such_feld.pack(side="left", padx=(8, 16), ipady=4)
        such_feld.bind("<Return>", lambda _e: self._logbuch_laden())

        tk.Label(zeile2, text="Art:", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.logbuch_art = tk.StringVar(value="ALLE")
        ttk.Combobox(zeile2, textvariable=self.logbuch_art, state="readonly", width=12,
                     values=["ALLE", "FEHLER", "WARNUNG", "TRADE", "BROKER",
                             "KI", "NEWS", "SYSTEM"]).pack(side="left", padx=(8, 16))

        tk.Label(zeile2, text="Zeilen:", bg=PANEL, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left")
        self.logbuch_anzahl = tk.StringVar(value="500")
        ttk.Combobox(zeile2, textvariable=self.logbuch_anzahl, state="readonly", width=7,
                     values=["200", "500", "1000", "5000"]).pack(side="left", padx=(8, 0))

        for var in (self.logbuch_art, self.logbuch_anzahl):
            var.trace_add("write", lambda *_a: self._logbuch_laden())

        # --- Textbereich mit Bildlauf ---
        rahmen = tk.Frame(wrap, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
        rahmen.pack(fill="both", expand=True, pady=(0, 10))

        leiste = tk.Scrollbar(rahmen, orient="vertical")
        leiste.pack(side="right", fill="y")

        self.logbuch_text = tk.Text(
            rahmen, bg="#080d16", fg="#cbd5e1", insertbackground="white",
            relief="flat", font=("Consolas", 10), wrap="none",
            yscrollcommand=leiste.set, padx=12, pady=10,
        )
        self.logbuch_text.pack(fill="both", expand=True)
        leiste.config(command=self.logbuch_text.yview)

        quer = tk.Scrollbar(wrap, orient="horizontal", command=self.logbuch_text.xview)
        self.logbuch_text.config(xscrollcommand=quer.set)

        for tag, farbe in [("ERROR", RED), ("WARNING", AMBER), ("TRADE", GREEN),
                           ("AI", "#a78bfa"), ("NEWS", "#38bdf8"),
                           ("BROKER", "#60a5fa"), ("INFO", "#94a3b8"),
                           ("TREFFER", "#fde047")]:
            self.logbuch_text.tag_configure(tag, foreground=farbe)
        self.logbuch_text.tag_configure("TREFFER", background="#3f3f13")

        self._logbuch_laden()

    def _logbuch_zeilen(self):
        """Liest die Protokolldatei und beruecksichtigt auch rotierte Teile."""
        from pathlib import Path as _P
        basis = _P(getattr(cfg, "LOG_FILE", "trading_bot.log"))
        if not basis.is_absolute():
            basis = _P(__file__).resolve().parent / basis
        dateien = [basis]
        for i in range(1, 4):
            rot = _P(f"{basis}.{i}")
            if rot.exists():
                dateien.append(rot)
        zeilen = []
        for datei in reversed(dateien):
            try:
                if datei.exists():
                    zeilen.extend(datei.read_text(encoding="utf-8", errors="replace").splitlines())
            except Exception:
                continue
        return zeilen

    @staticmethod
    def _logbuch_art_passt(zeile: str, art: str) -> bool:
        if art == "ALLE":
            return True
        oben = zeile.upper()
        schluessel = {
            "FEHLER": ("ERROR", "CRITICAL", "EXCEPTION", "TRACEBACK"),
            "WARNUNG": ("WARNING", "WARN"),
            "TRADE": ("TRADE", "KAUF", "VERKAUF", "BUY", "SELL", "FILL", "ORDER"),
            "BROKER": ("BROKER", "ETORO", "VERBINDUNG", "RECONNECT"),
            "KI": ("AI_", "OPENAI", "KI ", "ATTENTION", "RESEARCH"),
            "NEWS": ("NEWS", "GDELT", "SEC", "YAHOO", "NACHRICHT"),
            "SYSTEM": ("SYSTEM", "START", "STOP", "ZYKLUS", "CYCLE", "PI "),
        }.get(art, ())
        return any(s in oben for s in schluessel)

    @staticmethod
    def _logbuch_tag(zeile: str) -> str:
        oben = zeile.upper()
        if any(x in oben for x in ("ERROR", "CRITICAL", "TRACEBACK")):
            return "ERROR"
        if "WARNING" in oben or "WARN" in oben:
            return "WARNING"
        if any(x in oben for x in ("KAUF", "VERKAUF", "TRADE", "FILL")):
            return "TRADE"
        if any(x in oben for x in ("AI_", "OPENAI", "ATTENTION")):
            return "AI"
        if any(x in oben for x in ("NEWS", "GDELT", "SEC ")):
            return "NEWS"
        if any(x in oben for x in ("ETORO", "BROKER")):
            return "BROKER"
        return "INFO"

    def _logbuch_laden(self):
        if not hasattr(self, "logbuch_text"):
            return
        try:
            grenze = int(self.logbuch_anzahl.get())
        except Exception:
            grenze = 500
        art = self.logbuch_art.get()
        suche = (self.logbuch_suche.get() or "").strip().lower()

        alle = self._logbuch_zeilen()
        gefiltert = [z for z in alle if self._logbuch_art_passt(z, art)]
        if suche:
            gefiltert = [z for z in gefiltert if suche in z.lower()]
        anzeige = gefiltert[-grenze:]

        self.logbuch_text.config(state="normal")
        self.logbuch_text.delete("1.0", "end")
        if not anzeige:
            hinweis = ("Keine Eintraege gefunden."
                       if (suche or art != "ALLE")
                       else "Noch keine Protokolleintraege vorhanden.")
            self.logbuch_text.insert("end", f"  {hinweis}\n", "INFO")
        else:
            for zeile in anzeige:
                self.logbuch_text.insert("end", zeile + "\n", self._logbuch_tag(zeile))
            if suche:
                self._logbuch_treffer_markieren(suche)
        self.logbuch_text.see("end")
        self.logbuch_text.config(state="disabled")

        self.logbuch_info.config(
            text=f"{len(anzeige)} von {len(alle)} Zeilen"
                 + (f" · gefiltert nach {art}" if art != "ALLE" else "")
                 + (f" · Suche „{suche}“" if suche else "")
        )

    def _logbuch_treffer_markieren(self, suche: str):
        start = "1.0"
        while True:
            pos = self.logbuch_text.search(suche, start, stopindex="end", nocase=True)
            if not pos:
                break
            ende = f"{pos}+{len(suche)}c"
            self.logbuch_text.tag_add("TREFFER", pos, ende)
            start = ende

    def _logbuch_export(self):
        from tkinter import filedialog, messagebox
        from datetime import datetime as _dt
        ziel = filedialog.asksaveasfilename(
            title="Logbuch exportieren", defaultextension=".txt",
            initialfile=f"logbuch_{_dt.now():%Y%m%d_%H%M}.txt",
            filetypes=[("Textdatei", "*.txt"), ("Alle Dateien", "*.*")],
        )
        if not ziel:
            return
        try:
            inhalt = self.logbuch_text.get("1.0", "end")
            with open(ziel, "w", encoding="utf-8") as fh:
                fh.write(inhalt)
            messagebox.showinfo("Logbuch", f"Exportiert nach:\n{ziel}")
        except Exception as exc:
            messagebox.showerror("Logbuch", f"Export fehlgeschlagen:\n{exc}")

    def page_diagnose(self):
        self.section_title("Diagnose", f"Diagnosewerkzeuge und v{VERSION} Sicherheits-/Broker-/KI-Tests")
        items = [
            ("Verbindung testen", "test_connection.py"),
            ("Gesamter Selbsttest", "self_test.py"),
            ("eToro Money-Path", "volltest.py"),
            ("Integrationstests (offline)", "tests_integration_v560.py"),
            ("Crypto Diagnose", "crypto_diagnose.py"),
            ("Orders & Fills", "orders_fills_status.py"),
            ("Depotstatus", "depot_status.py"),
            ("Volltest aktuell (offline)", "volltest.py"),
        ]
        grid = tk.Frame(self.page, bg=BG); grid.pack(fill="x", pady=10)
        for i,(text,script) in enumerate(items):
            r,c=divmod(i,3)
            cell=tk.Frame(grid,bg=BG); cell.grid(row=r,column=c,padx=6,pady=6,sticky="ew")
            grid.grid_columnconfigure(c,weight=1)
            self._button(cell,text,lambda s=script:self.run_interactive(s),"#334155",22).pack(fill="x")
        note = tk.Frame(self.page,bg=PANEL,highlightbackground=BORDER,highlightthickness=1); note.pack(fill="x",pady=18)
        tk.Label(note,text="Sicherheits-Hinweis",bg=PANEL,fg=AMBER,font=("Segoe UI",11,"bold")).pack(anchor="w",padx=18,pady=(14,4))
        tk.Label(note,text="Die GUI steuert denselben Trading-Core wie die CLI. Persistente Risiko-, Fill- und Handelszustände bleiben auch nach Neustarts erhalten; die CLI-Werkzeuge bleiben zusätzlich verfügbar.",bg=PANEL,fg=MUTED,font=("Segoe UI",9),wraplength=1000,justify="left").pack(anchor="w",padx=18,pady=(0,14))

    def set_mode(self, mode):
        MODE_FILE.write_text(mode, encoding="utf-8")
        self.status_var.set(f"Handelsmodus: {mode.upper()}")
        self.refresh_dashboard()
        if mode == "paper":
            messagebox.showinfo("Handelsmodus", "PAPER-Modus gesetzt. Gilt ab dem nächsten Start des Bots.")

    def set_profile(self, profile):
        if profile == "offensiv":
            if not messagebox.askyesno("Offensives Risiko", "Offensiv erlaubt deutlich größere Positionen. Wirklich setzen?"):
                return
        PROFILE_FILE.write_text(profile, encoding="utf-8")
        self.status_var.set(f"Risiko-Profil: {profile.upper()}")
        self.refresh_dashboard()

    def _tool_window(self, script, interactive=False, args=None):
        path=ROOT/script
        if not path.exists(): return messagebox.showerror("Datei fehlt",f"{script} wurde nicht gefunden.")
        win=tk.Toplevel(self.root); win.title(f"Werkzeug · {script}"); win.geometry("980x680"); win.configure(bg=BG)
        hdr=tk.Frame(win,bg=BG); hdr.pack(fill="x",padx=12,pady=10)
        state=tk.StringVar(value="Laeuft …"); tk.Label(hdr,textvariable=state,bg=BG,fg=TEXT,font=("Segoe UI",10,"bold")).pack(side="left")
        txt=tk.Text(win,bg="#070b12",fg="#d1d5db",insertbackground="white",font=("Consolas",9),wrap="word"); txt.pack(fill="both",expand=True,padx=12,pady=(0,8))
        foot=tk.Frame(win,bg=BG); foot.pack(fill="x",padx=12,pady=(0,12)); inp=tk.Entry(foot,font=("Consolas",10)); inp.pack(side="left",fill="x",expand=True)
        send=tk.Button(foot,text="Eingabe senden",bg=ACCENT,fg="white",relief="flat"); send.pack(side="left",padx=(8,0))
        cmd=[PYTHON,"-u",str(path)]+list(args or [])
        try:
            proc=subprocess.Popen(
                cmd,cwd=str(ROOT),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                text=True,bufsize=1,encoding="utf-8",errors="replace",env=utf8_child_env()
            )
        except Exception as exc: win.destroy(); return messagebox.showerror("Startfehler",str(exc))
        def send_line(_=None):
            value=inp.get(); inp.delete(0,"end")
            if proc.poll() is None and proc.stdin:
                try: proc.stdin.write(value+"\n"); proc.stdin.flush(); txt.insert("end","> "+value+"\n"); txt.see("end")
                except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        inp.bind("<Return>",send_line); send.configure(command=send_line)
        if not interactive: inp.configure(state="disabled"); send.configure(state="disabled")
        output_q=queue.Queue()
        def reader():
            try:
                if proc.stdout is not None:
                    # Absichtlich NICHT zeilenweise: input("Prompt: ") schreibt
                    # keinen Zeilenumbruch. v5.11.3 zeigte deshalb bei Research
                    # ein scheinbar leeres/schwarzes Werkzeugfenster.
                    for chunk in iter_stream_chunks(proc.stdout, 1):
                        output_q.put(("text",chunk))
                output_q.put(("done",proc.wait()))
            except Exception as exc:
                output_q.put(("text",f"\n[GUI-Lesefehler] {exc}\n"))
                output_q.put(("done",proc.poll() if proc.poll() is not None else -1))
        def poll_output():
            if not win.winfo_exists():
                return
            finished=None
            try:
                while True:
                    kind,payload=output_q.get_nowait()
                    if kind=="text":
                        txt.insert("end",payload); txt.see("end")
                    elif kind=="done":
                        finished=int(payload)
            except queue.Empty:
                pass
            if finished is not None:
                state.set(f"Beendet · Code {finished} · Ergebnis bleibt geoeffnet")
                self.status_var.set("Bereit")
                inp.configure(state="disabled"); send.configure(state="disabled")
            elif proc.poll() is None:
                win.after(60,poll_output)
            else:
                # Prozess kann beendet sein, bevor das done-Event im Queue landet.
                win.after(30,poll_output)
        threading.Thread(target=reader,daemon=True).start(); win.after(30,poll_output)
        self.status_var.set(f"Starte {script} …")
        self._tool_windows.append(win)

    def run_script(self, script, args=None):
        self._tool_window(script,interactive=False,args=args)

    def run_interactive(self, script):
        self._tool_window(script,interactive=True)

    def start_bot(self):
        if self.process and self.process.poll() is None:
            messagebox.showinfo("Trading", "Der Bot läuft bereits.")
            return
        # Auf dem Raspberry Pi wird der Trader normalerweise von systemd
        # gestartet. Die GUI darf dann keinen zweiten Prozess erzeugen.
        try:
            from runtime_status import read_runtime
            external = read_runtime(ROOT / "runtime_status.json")
            external_okx = read_runtime(ROOT / "runtime_status_okx.json")
            if bool(external.get("online") or external_okx.get("online")):
                messagebox.showinfo(
                    "Trading",
                    "Der TradingBot läuft bereits als externer Dienst/Prozess. "
                    "Ein zweiter Start wurde aus Sicherheitsgründen blockiert.",
                )
                return
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
        mode = read_text(MODE_FILE, "paper").upper()
        msg = "Bot im PAPER-Modus starten?" if mode != "LIVE" else "LIVE-Modus erkannt. Die bestehende Sicherheitslogik wird ausgeführt. Fortfahren?"
        if not messagebox.askyesno("Trading starten", msg):
            return
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            try:
                from bot_zustand import BotZustand, AKTIV
                bz=BotZustand(str(STATE_FILE))
                if bz.zustand()!=AKTIV:
                    if not messagebox.askyesno("Handel freigeben", f"Aktueller Zustand: {bz.zustand().upper()}. Beim Start auf AKTIV setzen?"):
                        return
                    bz.setze(AKTIV,"über GUI gestartet","gui")
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            if os.name != "nt" and Path("/etc/systemd/system/tradingbot-pi5.service").exists():
                cp=subprocess.run(["sudo","systemctl","start","tradingbot-pi5.service"],capture_output=True,text=True,timeout=20)
                if cp.returncode != 0:
                    raise RuntimeError((cp.stderr or cp.stdout or "systemd-Start fehlgeschlagen").strip())
                self.status_var.set("Trading-Bot als Pi-Dienst gestartet")
                messagebox.showinfo("Trading", "Der TradingBot wurde über den sicheren systemd-Dienst gestartet. Autostart und Absturzschutz bleiben aktiv.")
                return
            self.process = subprocess.Popen([PYTHON, str(ROOT / "nexus_start.py")], cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1, creationflags=creationflags)
            self.status_var.set("Trading-Bot läuft")
            def reader(proc):
                for line in proc.stdout or []:
                    self._append_log(line.rstrip())
                self._append_log(f"[nexus_start] beendet mit Code {proc.poll()}")
                self.status_var.set("Bot beendet")
            threading.Thread(target=reader, args=(self.process,), daemon=True).start()
        except Exception as exc:
            messagebox.showerror("Bot konnte nicht gestartet werden", str(exc))

    def pause_bot(self):
        try:
            from bot_zustand import BotZustand, PAUSIERT
            BotZustand(str(STATE_FILE)).setze(
                PAUSIERT,
                "über GUI pausiert – keine neuen Käufe, Schutzverkäufe bleiben aktiv",
                "gui",
            )
            self.status_var.set("🟠 Trading pausiert")
            messagebox.showinfo(
                "Trading pausiert",
                "Neue Käufe sind ab sofort blockiert.\n\n"
                "Bestehende Positionen werden weiter überwacht und dürfen "
                "bei Stop-Loss, Take-Profit oder Verkaufssignal geschlossen werden.",
            )
        except Exception as exc:
            messagebox.showerror("Pause-Fehler", str(exc))

    def stop_bot(self):
        runtime = {}
        try:
            from runtime_status import read_runtime
            runtime = read_runtime(ROOT / "runtime_status.json")
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

        online = bool(runtime.get("online"))
        own_process = bool(self.process and self.process.poll() is None)

        if not messagebox.askyesno(
            "Trading vollständig stoppen",
            "Vollständigen STOPP setzen?\n\n"
            "ACHTUNG: GESTOPPT unterbindet auch automatische Verkäufe. "
            "Für den normalen Fern-/Sicherheitsstopp ist PAUSIEREN meist "
            "die bessere Wahl.",
        ):
            return

        try:
            from bot_zustand import BotZustand, GESTOPPT
            BotZustand(str(STATE_FILE)).setze(
                GESTOPPT, "über GUI vollständig gestoppt", "gui"
            )
        except Exception as exc:
            return messagebox.showerror("Stop-Fehler", str(exc))

        if own_process:
            try:
                self.process.terminate()
                self.status_var.set("🔴 Trading gestoppt · Prozess wird beendet")
                return
            except Exception as exc:
                return messagebox.showerror("Stop-Fehler", str(exc))

        if online:
            # Extern gestarteten Prozess nicht blind per PID abschießen. Der v5.3.1
            # Core liest den Zustand vor jedem Kauf/Verkauf und in der Wartezeit.
            self.status_var.set("🔴 Trading gestoppt · externer Bot bleibt online")
            messagebox.showwarning(
                "Trading gestoppt",
                "Der Handelszustand wurde auf GESTOPPT gesetzt.\n\n"
                "Der Bot-Prozess wurde außerhalb dieser GUI gestartet und läuft "
                "weiter. Der Core liest den Zustand vor jeder Handelsentscheidung "
                "frisch; neue Orders werden dadurch blockiert.\n\n"
                "Der Prozess bleibt für Status/Heartbeat online.",
            )
        else:
            self.status_var.set("🔴 Trading gestoppt")
            messagebox.showinfo(
                "Trading gestoppt",
                "Der persistente Handelszustand wurde auf GESTOPPT gesetzt. "
                "Er bleibt auch nach einem Neustart erhalten.",
            )

    def _poll_process(self):
        if self.process is not None and self.process.poll() is not None:
            self.status_var.set("Bot beendet")
        self.root.after(1500, self._poll_process)

    def _append_log(self, line):
        # Subprozess-Reader laeuft in einem Hintergrundthread. Tk-Aufrufe aus
        # diesem Thread sind plattformabhaengig instabil; deshalb nur Queue.
        try:
            self._live_log_queue.put_nowait(str(line))
        except Exception:
            __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    def _append_log_ui(self, line):
        if not (hasattr(self, "log_text") and self.log_text.winfo_exists()):
            return
        if " updatePortfolio:" in line:
            return
        kind=self._classify_log(line)
        filt=self.log_filter.get() if hasattr(self,"log_filter") else "ALLE"
        wanted={"FEHLER":"ERROR","WARNUNG":"WARNING","TRADE":"TRADE","BROKER":"BROKER","KI":"AI","NEWS":"NEWS","SYSTEM":"INFO"}.get(filt,filt)
        if filt!="ALLE" and kind!=wanted:
            return
        self.log_text.insert("end", line + "\n", kind)
        self.log_text.see("end")

    def _classify_log(self,line):
        u=line.upper()
        if " ERROR " in u or "TRACEBACK" in u:return "ERROR"
        if " WARNING " in u or "WARNUNG" in u:return "WARNING"
        if "DECISION" in u or "FILL" in u or "KAUF" in u or "VERKAUF" in u:return "TRADE"
        if " AI " in u or "OPENAI" in u:return "AI"
        if "NEWS" in u or "GDELT" in u or "SEC " in u:return "NEWS"
        if any(x in u for x in ("ETORO","BROKER","UPDATEPORTFOLIO")):return "BROKER"
        return "INFO"

    def _populate_log_widget(self):
        if self.log_paused.get() or not LOG_FILE.exists(): return
        try:
            raw=LOG_FILE.read_text(encoding="utf-8",errors="ignore").splitlines()[-400:]
            # Rauschende Bibliothekscallbacks nur im Dateilog behalten; im Dashboard ausblenden.
            raw=[x for x in raw if " updatePortfolio:" not in x]
            filt=self.log_filter.get(); out=[]; last=None; reps=0
            for line in raw:
                kind=self._classify_log(line)
                if filt!="ALLE":
                    wanted={"FEHLER":"ERROR","WARNUNG":"WARNING","TRADE":"TRADE","BROKER":"BROKER","KI":"AI","NEWS":"NEWS","SYSTEM":"INFO"}.get(filt,filt)
                    if kind!=wanted: continue
                # gleiche direkte Wiederholungen kompakt zusammenfassen
                core=line[24:] if len(line)>24 else line
                if core==last: reps+=1; continue
                if reps and out: out[-1]=(out[-1][0]+f"  [x{reps+1}]",out[-1][1])
                out.append((line,kind)); last=core; reps=0
            if reps and out: out[-1]=(out[-1][0]+f"  [x{reps+1}]",out[-1][1])
            self.log_text.delete("1.0","end")
            for line,kind in out[-180:]: self.log_text.insert("end",line+"\n",kind)
            self.log_text.see("end")
        except Exception: __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    def refresh_log(self):
        # Live-stdout ausschliesslich im GUI-Thread verarbeiten.
        try:
            while True:
                self._append_log_ui(self._live_log_queue.get_nowait())
        except queue.Empty:
            pass
        if hasattr(self, "log_text") and self.log_text.winfo_exists():
            self._populate_log_widget()
        self.root.after(3000, self.refresh_log)

    def refresh_dashboard(self):
        broker, mode, profile, us, eu, crypto = current_config()
        if "broker" in self.cards:
            self.cards["broker"].set(broker); self.cards["mode"].set(mode); self.cards["profile"].set(profile)
            # Ab v8.1.3 zeigt die Karte das ECHTE Universum (Kern + Dynamik),
            # nicht mehr den statischen Katalog aus der config.
            self.cards["universe"].set(universum_kartentext())
        runtime,trading,auto=dashboard_state(); online=bool(runtime.get("online"))
        if "pi_cpu" in self.cards:
            temp=runtime.get("pi_cpu_temperature_c"); cpu=runtime.get("pi_cpu_usage_pct")
            self.cards["pi_cpu"].set((f"{float(cpu):.1f}% · {float(temp):.1f} °C" if cpu is not None and temp is not None else (f"{float(temp):.1f} °C" if temp is not None else "nicht verfügbar")))
        if "pi_ram" in self.cards:
            used=runtime.get("pi_memory_used_mb"); total=runtime.get("pi_memory_total_mb")
            self.cards["pi_ram"].set(f"{float(used)/1024:.2f} / {float(total)/1024:.2f} GB" if used is not None and total else "nicht verfügbar")
        if "pi_disk" in self.cards:
            free=runtime.get("pi_disk_free_mb"); totald=runtime.get("pi_disk_total_mb")
            self.cards["pi_disk"].set(f"{float(free)/1024:.1f} / {float(totald)/1024:.1f} GB" if free is not None and totald else "nicht verfügbar")
        if "pi_power" in self.cards:
            up=float(runtime.get("pi_system_uptime_seconds",0) or 0); h=int(up//3600); d=h//24; h=h%24
            warn=bool(runtime.get("pi_undervoltage_now") or runtime.get("pi_throttled_now"))
            self.cards["pi_power"].set(("🔴 WARNUNG" if warn else "🟢 OK") + (f" · {d}d {h}h" if up else ""))
        # Die Karte wird oben bereits aus dem Universums-Zustand gefuellt.
        # Die frueher hier verwendeten runtime-Felder stammten allein aus dem
        # eToro-Prozess (configured_crypto ist dort fest 0) -- genau deshalb
        # stand auf der Karte strukturell immer "0 Krypto".
        if "bot_process" in self.cards:self.cards["bot_process"].set("🟢 ONLINE" if online else "⚫ OFFLINE")
        if "trading_state" in self.cards:self.cards["trading_state"].set(("🟢 " if trading=="AKTIV" and online else "🟠 " if trading=="PAUSIERT" else "🔴 ")+trading)
        conn_state = str(runtime.get("connection_state", "") or "").upper()
        broker_connected = bool(runtime.get("broker_connected")) if online else False
        if "broker_connection" in self.cards:
            if not online:
                conn_text = "⚫ BOT OFFLINE"
            elif conn_state in ("RESYNC", "RESYNC_PENDING"):
                conn_text = "🟡 SYNCHRONISIERT"
            elif conn_state == "RESYNC_FAILED":
                conn_text = "🔴 RESYNC FEHLER"
            elif conn_state == "AUTH_ERROR":
                conn_text = "🔴 AUTH-FEHLER"
            elif broker_connected:
                conn_text = "🟢 ONLINE"
            else:
                conn_text = "🔴 OFFLINE · AUTO-RECONNECT"
            self.cards["broker_connection"].set(conn_text)
        if "reconnect_attempts" in self.cards:
            current = int(runtime.get("reconnect_attempts_current", runtime.get("reconnect_attempts", 0)) or 0)
            total = int(runtime.get("reconnects_total", 0) or 0)
            self.cards["reconnect_attempts"].set(f"{current} aktuell · {total} erfolgreich")
        raw_contact = runtime.get("last_broker_contact")
        contact = "noch keiner"
        contact_age = None
        if raw_contact:
            try:
                dt = datetime.fromisoformat(str(raw_contact).replace("Z", "+00:00"))
                if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
                contact_age=max(0.0,(datetime.now(timezone.utc)-dt.astimezone(timezone.utc)).total_seconds())
                contact = dt.astimezone().strftime("%d.%m. %H:%M:%S")
            except Exception:
                contact = str(raw_contact)[:19]
        if "broker_contact" in self.cards:
            self.cards["broker_contact"].set(contact)
        if "broker_liveness" in self.cards:
            if not online:
                life="⚫ BOT OFFLINE"
            elif conn_state=="AUTH_ERROR":
                life="🔴 AUTH-FEHLER"
            elif contact_age is None:
                life="⚪ noch kein echter Kontakt"
            elif contact_age <= 60:
                life=f"🟢 {contact_age:.0f}s"
            elif contact_age <= 180:
                life=f"🟡 {contact_age:.0f}s alt"
            else:
                life=f"🔴 {contact_age/60:.1f} min alt"
            self.cards["broker_liveness"].set(life)
        if "news_auto" in self.cards:
            try:
                cfg_now = safe_import_config()
                if not getattr(cfg_now, "NEWS_RADAR_ENABLED", True):
                    self.cards["news_auto"].set("AUS")
                else:
                    from news_sources import source_health
                    health = source_health()
                    configured = [v for v in health.values() if v.get("configured")]
                    healthy = sum(1 for v in configured if v.get("healthy"))
                    unknown = sum(1 for v in configured if v.get("state") == "unknown")
                    total = len(configured)
                    if total == 0:
                        text = "🔴 AUTO · 0 Quellen eingerichtet"
                    elif unknown == total:
                        text = f"⚪ AUTO · 0/{total} getestet"
                    elif healthy == total:
                        text = f"🟢 AUTO · {healthy}/{total} erreichbar"
                    elif healthy > 0:
                        text = f"🟡 AUTO · {healthy}/{total} erreichbar"
                    else:
                        text = f"🔴 AUTO · 0/{total} erreichbar"
                    self.cards["news_auto"].set(text)
            except Exception:
                self.cards["news_auto"].set("⚪ AUTO · Status unbekannt")
        if "underdog_auto" in self.cards:self.cards["underdog_auto"].set("🟢 AUTO 24H" if getattr(safe_import_config(),"AUTO_UNDERDOG_REFRESH",True) else "MANUELL")
        if "walkforward_age" in self.cards:self.cards["walkforward_age"].set(_age_label(ROOT/"walkforward_status.json",float(getattr(safe_import_config(),"WALKFORWARD_MAX_AGE_DAYS",7))))
        if "ml_age" in self.cards:self.cards["ml_age"].set(_age_label(ROOT/getattr(safe_import_config(),"MODEL_PATH","ml_model.joblib"),float(getattr(safe_import_config(),"ML_MODEL_MAX_AGE_DAYS",30))))
        pnl = dashboard_pnl()
        if "pnl_today_net" in self.cards:self.cards["pnl_today_net"].set(_pnl_text(pnl["today_net"]))
        if "pnl_today_profit" in self.cards:self.cards["pnl_today_profit"].set(_pnl_text(pnl["today_profit"]))
        if "pnl_today_loss" in self.cards:self.cards["pnl_today_loss"].set(_pnl_text(pnl["today_loss"], loss=True))
        if "pnl_total_net" in self.cards:self.cards["pnl_total_net"].set(_pnl_text(pnl["total_net"]))
        if "pnl_total_profit" in self.cards:self.cards["pnl_total_profit"].set(_pnl_text(pnl["total_profit"]))
        if "pnl_total_loss" in self.cards:self.cards["pnl_total_loss"].set(_pnl_text(pnl["total_loss"], loss=True))
        if online and not broker_connected:
            if conn_state == "AUTH_ERROR":
                self.status_var.set("🔴 Bot läuft · Broker-Authentifizierung fehlgeschlagen")
                self.header_status.configure(fg=RED)
            else:
                self.status_var.set("🟠 Bot läuft · Broker offline · sichere Pause")
                self.header_status.configure(fg=AMBER)
        elif online and conn_state in ("RESYNC", "RESYNC_PENDING"):
            self.status_var.set("🟠 Broker wieder da · Zustand wird synchronisiert")
            self.header_status.configure(fg=AMBER)
        elif online and trading=="AKTIV": self.status_var.set("🟢 Trading läuft") ; self.header_status.configure(fg=GREEN)
        elif online and trading=="PAUSIERT": self.status_var.set("🟠 Bot läuft · Trading pausiert"); self.header_status.configure(fg=AMBER)
        elif online: self.status_var.set("🔴 Bot läuft · Trading gestoppt"); self.header_status.configure(fg=RED)
        else: self.status_var.set("⚫ Bot offline"); self.header_status.configure(fg=MUTED)

    def _refresh_loop(self):
        self.refresh_dashboard()
        self.root.after(2500, self._refresh_loop)


def main():
    try:
        from settings_migration import auto_migrate
        auto_migrate(ROOT)
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    root = tk.Tk()
    app = BotGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
