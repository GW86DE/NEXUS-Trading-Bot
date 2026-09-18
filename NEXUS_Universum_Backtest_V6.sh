#!/usr/bin/env bash
# NEXUS Universum-Backtest V6 - eigenstaendiges, read-only Analysewerkzeug.
# V6: sechs zusaetzliche, oeffentlich dokumentierte Referenzstrategien
# (Aktien: RSI-2 Mean Reversion, 52-Wochen-Hoch-Momentum, Goldenes Kreuz;
# Krypto: Zeitreihen-Momentum, Keltner-Ausbruch, MACD-Trend) mit leicht
# verstaendlichen, aufklappbaren Strategie-Erklaerungen im Bericht.
# V5: korrigierter Portfolio-Replay, harte Datenqualitaets-/Identitaets-Gates,
# verstaerkte Lookahead-/Recursive-/Walk-Forward-/Stress-/Monte-Carlo-Pruefungen,
# exposure-matched Benchmarks und eine qualitaetsgebundene HTML-Rangliste.
# Es erstellt KEINE Orders, veraendert KEINE NEXUS-Daten und benoetigt KEINE neue NEXUS-Version.
set -euo pipefail
umask 077
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8:strict
export LANG="${LANG:-C.UTF-8}"
export TERM="${TERM:-xterm-256color}"

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"

# --nexus-root bereits in der Shell auswerten, damit die passende NEXUS-venv
# fuer pandas/yfinance benutzt werden kann. Die Python-Seite prueft den Pfad erneut.
NEXUS_ROOT=""
ARGS=("$@")
for ((i=0; i<${#ARGS[@]}; i++)); do
  case "${ARGS[$i]}" in
    --nexus-root)
      if (( i + 1 < ${#ARGS[@]} )); then NEXUS_ROOT="${ARGS[$((i+1))]}"; fi
      ;;
    --nexus-root=*) NEXUS_ROOT="${ARGS[$i]#*=}" ;;
  esac
done

find_nexus_root() {
  local candidate=""
  if [[ -n "${NEXUS_ROOT}" && -f "${NEXUS_ROOT}/config.py" ]]; then
    printf '%s\n' "${NEXUS_ROOT}"
    return 0
  fi

  # Aktiver systemd-Dienst ist die beste Quelle. Lesen benoetigt kein sudo.
  if command -v systemctl >/dev/null 2>&1; then
    for unit in tradingbot-pi5.service tradingbot-webui.service; do
      candidate="$(systemctl show "$unit" -p WorkingDirectory --value 2>/dev/null || true)"
      if [[ -n "$candidate" && -f "$candidate/config.py" ]]; then
        printf '%s\n' "$candidate"
        return 0
      fi
      candidate="$(systemctl show "$unit" -p ExecStart --value 2>/dev/null | sed -n 's#.*path=\([^ ;]*\).*#\1#p' | head -n1 || true)"
      if [[ -n "$candidate" ]]; then
        candidate="$(dirname "$candidate")"
        if [[ -f "$candidate/config.py" ]]; then
          printf '%s\n' "$candidate"
          return 0
        fi
      fi
    done
  fi

  # Fallback: neuester plausibler NEXUS-Quellordner. Der Python-Teil protokolliert,
  # dass dies nur ein Fallback war.
  local newest=""
  while IFS= read -r d; do
    [[ -f "$d/config.py" ]] || continue
    newest="$d"
    break
  done < <(find "$HOME/Georg" -maxdepth 1 -type d -name 'TradingBot_v*_NEXUS*' -printf '%T@ %p\n' 2>/dev/null | sort -nr | cut -d' ' -f2-)
  if [[ -n "$newest" ]]; then
    printf '%s\n' "$newest"
    return 0
  fi
  return 1
}

ROOT="$(find_nexus_root || true)"
if [[ -z "$ROOT" ]]; then
  echo "FEHLER: Keine NEXUS-Installation gefunden. Mit --nexus-root /pfad/zur/Version angeben." >&2
  exit 2
fi

PY="$ROOT/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3 || true)"
fi
if [[ -z "$PY" ]]; then
  echo "FEHLER: Kein Python 3 gefunden." >&2
  exit 2
fi

exec "$PY" - "$ROOT" "$SCRIPT_PATH" "$@" <<'PY'
from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import html
import io
import json
import logging
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import traceback
import zipfile
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

ROOT = Path(sys.argv[1]).expanduser().resolve()
SELF = Path(sys.argv[2]).resolve()
ARGV = sys.argv[3:]

TOOL_VERSION = "6.0"

parser = argparse.ArgumentParser(
    prog=SELF.name,
    description=(
        "Read-only NEXUS-Universum-Backtest fuer eToro-Aktien und OKX-Krypto. "
        "Testet 2025 sowie 2026 bis heute (spaeter: das komplette Jahr 2026), "
        "zeigt Ergebnisse im Terminal und erzeugt HTML/CSV/JSON/ZIP. "
        "V5 korrigiert Same-Bar-Entry/Exit, sperrt unvollstaendige oder mehrdeutige Daten aus der Rangliste, staerkt Lookahead/Recursive-Pruefungen und vergleicht zusaetzlich gegen exposure-matched Benchmarks."
    ),
)
parser.add_argument("--nexus-root", default=str(ROOT), help="NEXUS-Quellordner (normalerweise automatisch erkannt)")
parser.add_argument("--ausgabe", default=str(Path.home()/"NEXUS_Backtests"), help="Ausgabe-/Cache-Verzeichnis")
parser.add_argument("--umfang", choices=("fokus", "aktiv"), default="fokus",
                    help="fokus = hoechstbewertete Universumswerte; aktiv = gesamtes handelbares Universum")
parser.add_argument("--aktien-limit", type=int, default=15, help="Maximale eToro-Aktien im Fokusmodus")
parser.add_argument("--krypto-limit", type=int, default=5, help="Maximale OKX-Coins im Fokusmodus (5m-Historie ist gross)")
parser.add_argument("--capital", type=float, default=10000.0, help="Startkapital je Einzel-Backtest")
parser.add_argument("--okx-rps", type=float, default=4.0, help="Maximale oeffentliche OKX-History-GETs pro Sekunde")
parser.add_argument("--nur-plan", action="store_true", help="Nur Version, Universum und Datenaufwand anzeigen; kein Netz")
parser.add_argument("--selbsttest", action="store_true", help="Ohne Netz mit synthetischen Daten Modul-/Reporttest ausfuehren")
parser.add_argument("--html-oeffnen", action="store_true", help="HTML-Bericht bei vorhandener Desktop-Sitzung oeffnen")
parser.add_argument("--aktien", default="", help="Optional: kommaseparierte Aktien statt automatischer Auswahl")
parser.add_argument("--krypto", default="", help="Optional: kommaseparierte Coins statt automatischer Auswahl")
parser.add_argument("--symbol-map", default="",
                    help="Optionales JSON mit eToro->Yahoo-Symbolen; Standard ist der persistente Backtest-Cache")
parser.add_argument("--okx-checkpoint-pages", type=int, default=25,
                    help="OKX-Download alle N Seiten dauerhaft zwischenspeichern (Standard 25)")
parser.add_argument("--aktien-datenquelle", choices=("auto","fmp","yahoo"), default="auto",
                    help="Aktienhistorie: auto=Cache/FMP/Yahoo, fmp=Cache/FMP mit Yahoo-Fallback bei Nichtverfuegbarkeit, yahoo=Cache/Yahoo")
parser.add_argument("--fmp-rps", type=float, default=2.0, help="Maximale manuelle FMP-Backtestabrufe pro Sekunde")
parser.add_argument("--monte-carlo", type=int, default=300, help="Moving-Block-Bootstrap-Simulationen je Portfolio/Zeitraum (Standard 300)")
parser.add_argument("--mc-block-days", type=int, default=5, help="Blocklaenge fuer Monte-Carlo/Bootstrap in Tagen")
parser.add_argument("--mit-crossover-forschung", action="store_true", help="Zusaetzlich NEXUS Crossover als experimentelle Forschungsvariante testen; nicht Hauptmodus")
parser.add_argument("--keine-robustheit", action="store_true", help="Parameter-Nachbarschaftstests der externen Strategien ueberspringen")
parser.add_argument("--keine-validierung", action="store_true", help="Lookahead-/Recursive-Pruefungen ueberspringen (nicht empfohlen)")
parser.add_argument("--warmup-start", default="2024-01-01", help="Fruehester Datenbeginn fuer 200-Tage-/365-Tage-Historie (Momentum/Startup)")
parser.add_argument("--max-portfolio-positionen", type=int, default=0, help="Optionales gemeinsames Positionslimit; 0 = brokerbezogener NEXUS-Wert")
args = parser.parse_args(ARGV)

ROOT = Path(args.nexus_root).expanduser().resolve()
if not (ROOT/"config.py").is_file():
    raise SystemExit(f"FEHLER: {ROOT} ist kein plausibler NEXUS-Quellordner (config.py fehlt).")

OUT_BASE = Path(args.ausgabe).expanduser().resolve()
CACHE = OUT_BASE/"cache"
CACHE.mkdir(parents=True, exist_ok=True)
# Eine zweite V2-Instanz darf nicht gleichzeitig denselben Cache beschreiben.
try:
    import fcntl
    _RUN_LOCK=(OUT_BASE/".backtest_v5.lock").open("a+")
    fcntl.flock(_RUN_LOCK.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("FEHLER: Ein anderer NEXUS Backtest laeuft bereits. Nicht parallel starten.")
except Exception:
    _RUN_LOCK=None

now = datetime.now(timezone.utc)
stamp = now.strftime("%Y%m%d_%H%M%S_UTC")
RUN_PREFIX = "NEXUS_Backtest_SELBSTTEST" if args.selbsttest else "NEXUS_Backtest"
RUN_DIR = OUT_BASE/f"{RUN_PREFIX}_{stamp}"
RUN_DIR.mkdir(parents=True, exist_ok=False)
ISOLATED_STATE = RUN_DIR/"isolierter_state"
ISOLATED_STATE.mkdir(parents=True, exist_ok=True)

# Ganz wichtig: NEXUS-Imports duerfen keinerlei Laufzeitdatei im produktiven
# Quellbaum erzeugen. Der Backtest bekommt einen isolierten State-Pfad.
os.environ["TRADINGBOT_TEST_STATE_DIR"] = str(ISOLATED_STATE)
os.environ["NEXUS_BACKTEST_ONLY"] = "1"
sys.path.insert(0, str(ROOT))

LOG_PATH = RUN_DIR/"run.log"

def log(msg=""):
    text = str(msg)
    print(text, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


def safe_float(value, default=0.0):
    try:
        x = float(value)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def version_info():
    out = {"root": str(ROOT), "version": "unbekannt", "build": ""}
    for name in ("VERSION.txt", "RELEASE_BUILD.txt"):
        p = ROOT/name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace").strip()
            if name == "VERSION.txt": out["version"] = text
            else: out["build"] = text[:500]
    if out["version"] == "unbekannt" and out["build"]:
        out["version"] = out["build"].splitlines()[0][:120]
    return out

VERSION = version_info()

# ---------- Universum -----------------------------------------------------
def parse_list(text):
    return [x.strip().upper() for x in str(text or "").split(",") if x.strip()]


def load_universe_state():
    p = ROOT/"universe_state.json"
    if not p.exists():
        return {"path": str(p), "members": [], "warning": "universe_state.json fehlt"}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"path": str(p), "members": [], "warning": f"Universumszustand unlesbar: {type(exc).__name__}: {exc}"}
    members = data.get("mitglieder") if isinstance(data, dict) else None
    return {"path": str(p), "members": list(members or []), "warning": ""}


def member_tradable(m):
    state = str(m.get("zustand") or "").upper()
    if state not in {"AKTIV", "ABGANG"}:
        return False
    if str(m.get("kaufblock_grund") or "").strip():
        return False
    if str(m.get("abganggrund") or "").startswith("CORE_SAFETY_BLOCKED"):
        return False
    return True


def member_rank_key(m):
    ai = {"HIGH": 0, "": 1, "MEDIUM": 1, "NORMAL": 1, "LOW": 2}.get(str(m.get("ai_bewertung") or "").upper(), 1)
    return (
        0 if m.get("gepinnt") else 1,
        0 if m.get("favorit") else 1,
        ai,
        -safe_float(m.get("letzter_score"), 0.0),
        int(m.get("letzter_rang") or 999999),
        str(m.get("symbol") or ""),
    )


def fallback_symbols(broker):
    try:
        import config
        if broker == "etoro":
            rows = list(getattr(config, "STOCK_FIXED_CORE_SYMBOLS", []) or getattr(config, "STOCK_SYMBOLS", []) or [])
            vals = []
            for x in rows:
                vals.append(str(x.get("symbol") if isinstance(x, dict) else x).upper())
            return [x for x in vals if x]
        vals = list(getattr(config, "CRYPTO_ANALYSIS_SYMBOLS", ()) or ())
        if not vals:
            rows = list(getattr(config, "CRYPTO_SYMBOLS", []) or [])
            vals = [str(x.get("symbol") if isinstance(x, dict) else x).upper() for x in rows]
        return [x for x in vals if x]
    except Exception:
        return []


univ = load_universe_state()
active = {"etoro": [], "okx": []}
for m in univ["members"]:
    broker = str(m.get("broker") or "").lower()
    if broker in active and member_tradable(m):
        active[broker].append(m)
for broker in active:
    active[broker].sort(key=member_rank_key)

manual_stocks = parse_list(args.aktien)
manual_crypto = parse_list(args.krypto)

selection_meta = {"source": {}, "warning": univ.get("warning", ""), "state_path": univ.get("path", "")}

def select(broker, manual, limit):
    if manual:
        selection_meta["source"][broker] = "MANUELL"
        return [{"symbol": s, "inst_id": "", "letzter_score": None, "letzter_rang": None} for s in manual]
    rows = active[broker]
    if rows:
        selection_meta["source"][broker] = "ACTIVE_UNIVERSE" if args.umfang == "aktiv" else "UNIVERSE_FOCUS_RANKING"
        return rows if args.umfang == "aktiv" else rows[:max(1, limit)]
    fb = fallback_symbols(broker)
    selection_meta["source"][broker] = "FALLBACK_CONFIG"
    return [{"symbol": s, "inst_id": "", "letzter_score": None, "letzter_rang": None} for s in fb[:max(1, limit)]]

stocks = select("etoro", manual_stocks, args.aktien_limit)
cryptos = select("okx", manual_crypto, args.krypto_limit)

# Doppelte Symbole entfernen, Reihenfolge beibehalten.
def dedup(rows):
    out=[]; seen=set()
    for r in rows:
        s=str(r.get("symbol") or "").upper()
        if s and s not in seen:
            seen.add(s); out.append(r)
    return out
stocks, cryptos = dedup(stocks), dedup(cryptos)

# ---------- Zeitraeume ----------------------------------------------------
today = now.date()
periods = [
    ("2025", datetime(2025,1,1,tzinfo=timezone.utc), datetime(2025,12,31,23,59,59,tzinfo=timezone.utc)),
]
end_2026_date = min(today, datetime(2026,12,31,tzinfo=timezone.utc).date())
if end_2026_date >= datetime(2026,1,1,tzinfo=timezone.utc).date():
    label = "2026_BIS_HEUTE" if today.year == 2026 else "2026_GESAMT"
    periods.append((label, datetime(2026,1,1,tzinfo=timezone.utc), datetime.combine(end_2026_date, datetime.max.time(), tzinfo=timezone.utc)))

# ---------- Kompatibilitaet / imports ------------------------------------
compat = {"nexus_standard": False, "freqtrade_sample": False, "notes": []}
try:
    import pandas as pd
    import numpy as np
except Exception as exc:
    raise SystemExit(f"FEHLER: pandas/numpy fehlen in der verwendeten Python-Umgebung: {exc}")

try:
    import config
    import backtest as nexus_bt
    import strategy as nexus_strategy
    needed = all(hasattr(nexus_bt, x) for x in ("_prepare_full", "_simulate", "summarize_backtest"))
    compat["nexus_standard"] = bool(needed)
    if not needed:
        compat["notes"].append("Aktuelle Version hat nicht die erwarteten Standard-Backtest-Helfer; Standardmodell wird uebersprungen.")
except Exception as exc:
    compat["notes"].append(f"NEXUS-Standardmodule nicht importierbar: {type(exc).__name__}: {exc}")

try:
    from freqtrade_sample_backtest import run_backtest as ft_run_backtest
    from freqtrade_sample_strategy import STARTUP_CANDLES as FT_STARTUP, parameter_snapshot as ft_parameter_snapshot
    compat["freqtrade_sample"] = True
except Exception as exc:
    compat["notes"].append(f"Freqtrade-Sample-Backtest nicht importierbar: {type(exc).__name__}: {exc}")
    FT_STARTUP = 200

# ---------- Datenhelfer ---------------------------------------------------
def normalize_frame(df):
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["open","high","low","close","volume"])
    out=df.copy()
    if not isinstance(out.index, pd.DatetimeIndex):
        out.index=pd.to_datetime(out.index, utc=True, errors="coerce")
    elif out.index.tz is None:
        out.index=out.index.tz_localize("UTC")
    else:
        out.index=out.index.tz_convert("UTC")
    if isinstance(out.columns, pd.MultiIndex):
        out.columns=[str(c[0]).lower() for c in out.columns]
    else:
        out.columns=[str(c).lower() for c in out.columns]
    needed=["open","high","low","close","volume"]
    for c in needed:
        if c not in out.columns:
            if c == "volume": out[c]=0.0
            elif "close" in out.columns: out[c]=out["close"]
            else: return pd.DataFrame(columns=needed)
        out[c]=pd.to_numeric(out[c], errors="coerce")
    out=out[needed].dropna(subset=["open","high","low","close"])
    out=out[(out.open>0)&(out.high>0)&(out.low>0)&(out.close>0)&(out.volume>=0)]
    out=out[~out.index.duplicated(keep="last")].sort_index()
    return out


def cache_csv_path(kind, key, timeframe):
    safe="".join(ch for ch in str(key).upper() if ch.isalnum() or ch in "-_.")
    return CACHE/kind/f"{safe}_{timeframe}.csv"


def read_csv_cache(path):
    try:
        if not path.exists(): return pd.DataFrame()
        df=pd.read_csv(path, index_col="date", parse_dates=["date"])
        return normalize_frame(df)
    except Exception:
        return pd.DataFrame()


def write_csv_cache(path, df):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp=path.with_name("."+path.name+".tmp")
    out=df.copy(); out.index.name="date"; out.to_csv(tmp)
    os.replace(tmp, path)

def cache_meta_path(path):
    return path.with_suffix(path.suffix+".meta.json")

def read_cache_meta(path):
    mp=cache_meta_path(path)
    try:
        raw=json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else {}
        return raw if isinstance(raw,dict) else {}
    except Exception:return {}

def write_cache_meta(path,meta):
    mp=cache_meta_path(path);mp.parent.mkdir(parents=True,exist_ok=True)
    tmp=mp.with_name("."+mp.name+".tmp")
    tmp.write_text(json.dumps(meta,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
    os.replace(tmp,mp)


# Persistente Provider-Symbolauflösung. Sie liegt bewusst ausserhalb von NEXUS,
# damit ein Update/Versionswechsel den bereits belegten Backtest-Cache nicht verliert.
STOCK_MAP_PATH = Path(args.symbol_map).expanduser().resolve() if args.symbol_map else CACHE/"stock_symbol_map.json"

# Die 15 aktuell kuratierten EU-Kernwerte haben bei eToro kurze Symbole, Yahoo
# verwendet dagegen Boersen-Suffixe. Diese Abbildung verhindert insbesondere
# den im ersten Backtest sichtbaren BAS/BAS.DE-Fehler. Neue Werte werden
# zusaetzlich dynamisch ueber Yahoo Search aufgeloest und danach persistent
# gespeichert; die feste Liste ist also kein geschlossenes Universum.
KNOWN_YAHOO_ALIASES = {
    "SAP":"SAP.DE", "SIE":"SIE.DE", "ALV":"ALV.DE", "MUV2":"MUV2.DE",
    "BAS":"BAS.DE", "BAYN":"BAYN.DE", "DTE":"DTE.DE", "RWE":"RWE.DE",
    "ASML":"ASML.AS", "MC":"MC.PA", "OR":"OR.PA", "AIR":"AIR.PA",
    "TTE":"TTE.PA", "SU":"SU.PA", "ITX":"ITX.MC",
}


def symbol_identity_allowed(etoro_symbol, provider_symbol, currency=""):
    """Fail-closed provider identity check.

    A provider ticker with a different root symbol is never accepted merely
    because a search returned price data. Known EU eToro aliases are pinned
    to their exchange-qualified ticker. This blocks cases such as BAS->BMGL
    or ALV->the unrelated US ticker.
    """
    e=str(etoro_symbol or "").upper().strip()
    p=str(provider_symbol or "").upper().strip()
    cur=str(currency or "").upper().strip()
    if not e or not p:return False
    known=KNOWN_YAHOO_ALIASES.get(e)
    if known:
        return p==known
    root=p.split(".",1)[0]
    expected=e.replace(".US","").split(".",1)[0]
    if root!=expected:return False
    # Non-USD instruments without a known exchange mapping must not silently
    # fall back to a same-named US listing. They remain unresolved instead.
    if cur and cur!="USD" and "." not in p:
        return False
    return True

def identity_status(etoro_symbol, provider_symbol, currency=""):
    if not symbol_identity_allowed(etoro_symbol,provider_symbol,currency):return "ABGELEHNT"
    e=str(etoro_symbol).upper();p=str(provider_symbol).upper()
    if KNOWN_YAHOO_ALIASES.get(e)==p:return "BESTAETIGTER_BOERSENALIAS"
    return "GLEICHER_TICKERSTAMM"


def load_symbol_map():
    try:
        raw=json.loads(STOCK_MAP_PATH.read_text(encoding="utf-8")) if STOCK_MAP_PATH.exists() else {}
        return raw if isinstance(raw,dict) else {}
    except Exception:
        return {}


def save_symbol_map(mapping):
    try:
        STOCK_MAP_PATH.parent.mkdir(parents=True,exist_ok=True)
        tmp=STOCK_MAP_PATH.with_name("."+STOCK_MAP_PATH.name+".tmp")
        tmp.write_text(json.dumps(mapping,ensure_ascii=False,indent=2,sort_keys=True),encoding="utf-8")
        os.replace(tmp,STOCK_MAP_PATH)
    except Exception:
        pass


SYMBOL_MAP=load_symbol_map()
symbol_mapping_rows=[]


def catalog_metadata(symbol):
    """Liest nur die Konfiguration der aktuell installierten NEXUS-Version."""
    symbol=str(symbol).upper()
    try:
        rows=list(getattr(config,"STOCK_CATALOG_SYMBOLS",[]) or [])
    except Exception:
        rows=[]
    for row in rows:
        if isinstance(row,dict) and str(row.get("symbol") or "").upper()==symbol:
            return {"currency":str(row.get("currency") or "").upper(),
                    "exchange":str(row.get("exchange") or ""),
                    "name":str(row.get("display_name") or row.get("name") or "")}
    return {"currency":"","exchange":"","name":""}


def yahoo_suffix_candidates(symbol, currency=""):
    symbol=str(symbol).upper().strip()
    cur=str(currency or "").upper()
    out=[]
    if symbol in KNOWN_YAHOO_ALIASES:
        out.append(KNOWN_YAHOO_ALIASES[symbol])
    suffixes={
        "EUR":[".DE",".PA",".AS",".MI",".MC",".BR",".VI",".LS"],
        "GBP":[".L"], "GBX":[".L"], "CHF":[".SW"], "CAD":[".TO"],
        "AUD":[".AX"], "JPY":[".T"], "HKD":[".HK"], "SEK":[".ST"],
        "NOK":[".OL"], "DKK":[".CO"], "SGD":[".SI"],
    }.get(cur,[])
    # Bei Nicht-USD-Werten erst die zur Handelswaehrung passenden Boersen
    # pruefen; ein zufaellig gleichlautender US-Ticker darf nicht gewinnen.
    if cur in {"", "USD"}:
        out.append(symbol.replace(".US",""))
        out.extend(symbol+s for s in suffixes)
    else:
        out.extend(symbol+s for s in suffixes)
        out.append(symbol.replace(".US",""))
    seen=set();ded=[]
    for x in out:
        x=str(x).upper()
        if x and x not in seen:
            seen.add(x);ded.append(x)
    return ded

def yahoo_search_symbols(symbol, currency=""):
    """Best-effort dynamische Suche. Fehler sind kein Freibrief fuer Raten."""
    try:
        import yfinance as yf
    except Exception:
        return []
    quotes=[]
    try:
        # yfinance hat die Signatur ueber die Zeit veraendert; nur die kleinste
        # gemeinsame Parameterflaeche verwenden.
        search=yf.Search(str(symbol), max_results=20)
        quotes=list(getattr(search,"quotes",[]) or [])
    except Exception:
        return []
    scored=[]
    want=str(symbol).upper();cur=str(currency or "").upper()
    for q in quotes:
        if not isinstance(q,dict):continue
        qs=str(q.get("symbol") or "").upper().strip()
        if not qs:continue
        qt=str(q.get("quoteType") or q.get("typeDisp") or "").upper()
        if qt and "EQUITY" not in qt and "STOCK" not in qt:continue
        qcur=str(q.get("currency") or "").upper()
        score=0
        if qs==want:score+=100
        if qs.split(".",1)[0]==want:score+=70
        if cur and qcur==cur:score+=30
        if qs==KNOWN_YAHOO_ALIASES.get(want):score+=150
        exch=str(q.get("exchange") or "").upper()
        if cur=="EUR" and exch in {"GER","FRA","PAR","AMS","MCE","MIL"}:score+=10
        scored.append((score,qs))
    scored.sort(key=lambda x:(-x[0],x[1]))
    return [x[1] for x in scored]


def _quiet_yfinance_download(yf, *a, **kw):
    # yfinance schreibt bei ungueltigen Kandidaten sonst irrefuehrende
    # "possibly delisted"-Meldungen direkt ins Terminal. Der Backtest bewertet
    # den Treffer selbst und protokolliert nur den fachlichen Fehler.
    old_level=logging.getLogger("yfinance").level
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return yf.download(*a, **kw)
    finally:
        logging.getLogger("yfinance").setLevel(old_level)


def yahoo_download(ticker, wanted_start, wanted_end):
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("yfinance fehlt; 1h-Aktienhistorie kann nicht geladen werden") from exc
    errors=[]
    for interval in ("60m","1h"):
        try:
            raw=_quiet_yfinance_download(yf,ticker,start=wanted_start.date().isoformat(),end=wanted_end.date().isoformat(),
                            interval=interval,progress=False,auto_adjust=True,threads=False)
            frame=normalize_frame(raw)
            if len(frame):return frame,errors
        except Exception as exc:
            errors.append(f"{interval}:{type(exc).__name__}:{exc}")
    try:
        raw=_quiet_yfinance_download(yf,ticker,period="730d",interval="60m",progress=False,auto_adjust=True,threads=False)
        frame=normalize_frame(raw)
        if len(frame):return frame,errors
    except Exception as exc:
        errors.append(f"730d:{type(exc).__name__}:{exc}")
    return pd.DataFrame(),errors


def provider_timezone(ticker):
    t=str(ticker).upper()
    suffixes={".DE":"Europe/Berlin",".PA":"Europe/Paris",".AS":"Europe/Amsterdam",
              ".MC":"Europe/Madrid",".MI":"Europe/Rome",".BR":"Europe/Brussels",
              ".VI":"Europe/Vienna",".LS":"Europe/Lisbon",".L":"Europe/London",
              ".SW":"Europe/Zurich",".ST":"Europe/Stockholm",".OL":"Europe/Oslo",
              ".CO":"Europe/Copenhagen",".TO":"America/Toronto",".AX":"Australia/Sydney",
              ".T":"Asia/Tokyo",".HK":"Asia/Hong_Kong",".SI":"Asia/Singapore"}
    for suf,zone in suffixes.items():
        if t.endswith(suf):return zone
    return "America/New_York"


def fmp_download(ticker, wanted_start, wanted_end):
    """Read-only FMP 1h history with small date windows and completeness checks.

    FMP may cap intraday rows per response. V4 used year-sized windows and could
    silently receive only a recent slice. V5 requests 28-day chunks, checks both
    edges of every non-empty chunk and later runs a full internal-gap quality gate.
    """
    key=str(getattr(config,"FMP_API_KEY","") or "").strip()
    if not key:return pd.DataFrame(),["FMP_API_KEY fehlt"]
    if args.aktien_datenquelle=="yahoo":return pd.DataFrame(),["FMP durch --aktien-datenquelle yahoo deaktiviert"]
    try:import requests
    except Exception as exc:return pd.DataFrame(),[f"requests fehlt:{type(exc).__name__}"]
    base="https://financialmodelingprep.com/stable/historical-chart/1hour"
    errors=[];frames=[];zone=provider_timezone(ticker);cur=pd.Timestamp(wanted_start).to_pydatetime()
    wanted_end_dt=pd.Timestamp(wanted_end).to_pydatetime();min_pause=1.0/max(0.2,float(args.fmp_rps))
    while cur <= wanted_end_dt:
        stop=min(wanted_end_dt,cur+timedelta(days=27))
        t0=time.monotonic();resp=None
        try:
            params={"symbol":ticker,"from":cur.date().isoformat(),"to":stop.date().isoformat()}
            resp=requests.get(base,params=params,headers={"apikey":key},timeout=20)
            if resp.status_code==429:
                try:wait=min(30.0,max(1.0,float(resp.headers.get("Retry-After","1"))))
                except Exception:wait=1.0
                time.sleep(wait);resp.close()
                resp=requests.get(base,params=params,headers={"apikey":key},timeout=20)
            if resp.status_code>=300:
                errors.append(f"HTTP {resp.status_code} {cur.date()}..{stop.date()}")
                return pd.DataFrame(),errors
            payload=resp.json()
            if not isinstance(payload,list):
                errors.append(f"unerwartetes Antwortformat {type(payload).__name__}");return pd.DataFrame(),errors
            if payload:
                raw=pd.DataFrame(payload)
                if "date" not in raw.columns:
                    errors.append("FMP-Antwort ohne date");return pd.DataFrame(),errors
                idx=pd.to_datetime(raw.pop("date"),errors="coerce")
                try:idx=idx.dt.tz_localize(zone,ambiguous="NaT",nonexistent="shift_forward").dt.tz_convert("UTC")
                except Exception:idx=pd.to_datetime(idx,utc=True,errors="coerce")
                raw.index=pd.DatetimeIndex(idx);fr=normalize_frame(raw)
                if len(fr):
                    frames.append(fr)
                    # A chunk can legitimately start/end on a weekend or holiday.
                    # More than seven calendar days missing at either edge is not
                    # accepted as a complete response.
                    c0=pd.Timestamp(cur,tz="UTC") if pd.Timestamp(cur).tzinfo is None else pd.Timestamp(cur).tz_convert("UTC")
                    c1=pd.Timestamp(stop,tz="UTC") if pd.Timestamp(stop).tzinfo is None else pd.Timestamp(stop).tz_convert("UTC")
                    if fr.index.min()>c0+pd.Timedelta(days=7):errors.append(f"FMP_CHUNK_START_LUECKE {cur.date()}..{stop.date()} -> {fr.index.min()}")
                    if fr.index.max()<c1-pd.Timedelta(days=7):errors.append(f"FMP_CHUNK_END_LUECKE {cur.date()}..{stop.date()} -> {fr.index.max()}")
        except Exception as exc:
            errors.append(f"{type(exc).__name__}:{exc}");return pd.DataFrame(),errors
        finally:
            if resp is not None:
                try:resp.close()
                except Exception:pass
            elapsed=time.monotonic()-t0
            if elapsed<min_pause:time.sleep(min_pause-elapsed)
        cur=(pd.Timestamp(stop)+pd.Timedelta(days=1)).to_pydatetime()
    if not frames:return pd.DataFrame(),errors
    return normalize_frame(pd.concat(frames)),errors

def _valid_provider_candidates(symbol, meta):
    symbol=str(symbol).upper().strip();cur=str(meta.get("currency") or "").upper();out=[]
    raw_cached=SYMBOL_MAP.get(symbol)
    if isinstance(raw_cached,str):
        cp=raw_cached.upper().strip();cstatus="MANUELL_BESTAETIGT"
    elif isinstance(raw_cached,dict):
        cp=str(raw_cached.get("provider_symbol") or "").upper().strip();cstatus=str(raw_cached.get("identity_status") or "")
    else:cp="";cstatus=""
    known=KNOWN_YAHOO_ALIASES.get(symbol)
    if known:
        # Known EU aliases are pinned; a stale persistent map to another root
        # (or another listing of the same company) cannot override the venue.
        if cp==known:out.append((cp,"PERSISTENT_CONFIRMED" if cstatus else "PERSISTENT_MAP"))
        out.append((known,"KNOWN_ALIAS"))
    elif cur in {"","USD"}:
        if cp and symbol_identity_allowed(symbol,cp,cur):out.append((cp,"PERSISTENT_MAP"))
        direct=symbol.replace(".US","")
        if symbol_identity_allowed(symbol,direct,cur):out.append((direct,"SAME_TICKER"))
    else:
        # For an unknown non-USD eToro ticker we prefer 'unknown' over choosing
        # an arbitrary .DE/.PA/.AS listing. Only a persisted/manual confirmed
        # mapping is accepted until NEXUS provides a specific venue/ISIN.
        if cp and symbol_identity_allowed(symbol,cp,cur) and (cstatus or isinstance(raw_cached,str)):
            out.append((cp,"PERSISTENT_CONFIRMED"))
    seen=set();ded=[]
    for cand,method in out:
        if cand in seen:continue
        seen.add(cand);ded.append((cand,method))
    return ded

def resolve_fmp_symbol(symbol, wanted_start, wanted_end, meta):
    """Resolve only identity-safe provider tickers. No fuzzy cross-company fallback."""
    symbol=str(symbol).upper().strip();cur=str(meta.get("currency") or "").upper()
    all_errors=[]
    for cand,method in _valid_provider_candidates(symbol,meta):
        frame,errs=fmp_download(cand,wanted_start,wanted_end)
        all_errors.extend(f"{cand}:{e}" for e in errs)
        if len(frame):
            info={"provider_symbol":cand,"method":"FMP_"+method,"verified_at_utc":datetime.now(timezone.utc).isoformat(),
                  "currency":cur,"exchange":meta.get("exchange",""),"identity_status":identity_status(symbol,cand,cur)}
            SYMBOL_MAP[symbol]=info;save_symbol_map(SYMBOL_MAP)
            return cand,frame,"FMP_"+method,all_errors
    return None,None,None,all_errors

def resolve_yahoo_symbol(symbol, wanted_start, wanted_end, meta):
    symbol=str(symbol).upper().strip();cur=str(meta.get("currency") or "").upper()
    all_errors=[];candidates=_valid_provider_candidates(symbol,meta)
    # Dynamic search may only discover another exchange suffix for the same
    # ticker root. Completely different symbols are rejected fail-closed.
    if cur in {"","USD"}:
        for x in yahoo_search_symbols(symbol,cur):
            if symbol_identity_allowed(symbol,x,cur):candidates.append((x,"YAHOO_SEARCH_SAME_IDENTITY"))
    seen=set()
    for cand,method in candidates:
        if cand in seen:continue
        seen.add(cand)
        frame,errs=yahoo_download(cand,wanted_start,wanted_end)
        all_errors.extend(f"{cand}:{e}" for e in errs)
        if len(frame):
            info={"provider_symbol":cand,"method":method,"verified_at_utc":datetime.now(timezone.utc).isoformat(),
                  "currency":cur,"exchange":meta.get("exchange",""),"identity_status":identity_status(symbol,cand,cur)}
            SYMBOL_MAP[symbol]=info;save_symbol_map(SYMBOL_MAP)
            return cand,frame,method,all_errors
    raise RuntimeError("kein identitaetssicheres Yahoo-Aktiensymbol mit Historie gefunden"+
                       (" | "+"; ".join(all_errors[-8:]) if all_errors else ""))

def stock_history(symbol, start, end):
    """1h history with provider-pure cache and fail-closed identity handling."""
    path=cache_csv_path("stocks",symbol,"1h");cached=read_csv_cache(path);cmeta=read_cache_meta(path)
    wanted_start=start-timedelta(days=45);wanted_end=end+timedelta(days=1);meta=catalog_metadata(symbol);cur=str(meta.get("currency") or "").upper()
    mapped=(SYMBOL_MAP.get(str(symbol).upper()) or {}).get("provider_symbol") if isinstance(SYMBOL_MAP.get(str(symbol).upper()),dict) else ""
    cache_family=str(cmeta.get("source_family") or ("YAHOO" if len(cached) else "")).upper()
    cached_provider=str(cmeta.get("provider_symbol") or mapped or symbol).upper()
    # Never reuse a stale cache whose provider identity no longer passes V5.
    if len(cached) and not symbol_identity_allowed(symbol,cached_provider,cur):
        cached=pd.DataFrame();cache_family=""
    if len(cached) and cached.index.min()<=pd.Timestamp(wanted_start) and cached.index.max()>=pd.Timestamp(end)-pd.Timedelta(hours=8):
        q=period_quality(cached,start,end,"stock","1h")
        if q.get("ranking_eligible"):
            symbol_mapping_rows.append({"etoro_symbol":symbol,"provider_symbol":cached_provider,"method":"CACHE_"+(cache_family or "UNKNOWN"),
                                        "currency":cur,"exchange":meta.get("exchange",""),"identity_status":identity_status(symbol,cached_provider,cur)})
            return cached[(cached.index>=pd.Timestamp(wanted_start))&(cached.index<=pd.Timestamp(wanted_end))],f"CACHE:{cache_family or 'UNKNOWN'}:{cached_provider}",cached_provider
    if args.aktien_datenquelle=="yahoo":order=["YAHOO"]
    elif args.aktien_datenquelle=="fmp":order=["FMP","YAHOO"]
    elif cache_family in {"YAHOO","FMP"}:order=[cache_family,"FMP" if cache_family=="YAHOO" else "YAHOO"]
    else:order=["FMP","YAHOO"]
    all_errors=[]
    for family in order:
        provider=fresh=method=None
        if family=="FMP":
            provider,fresh,method,errs=resolve_fmp_symbol(symbol,wanted_start,wanted_end,meta);all_errors.extend(errs)
        else:
            try:provider,fresh,method,errs=resolve_yahoo_symbol(symbol,wanted_start,wanted_end,meta);all_errors.extend(errs)
            except Exception as exc:all_errors.append(f"YAHOO:{type(exc).__name__}:{exc}");continue
        if provider is None or fresh is None or fresh.empty:continue
        if not symbol_identity_allowed(symbol,provider,cur):
            all_errors.append(f"IDENTITAET_ABGELEHNT:{symbol}->{provider}");continue
        # Merge only if the cache belongs to exactly the same provider symbol.
        if len(cached) and cache_family==family and cached_provider==provider:
            merged=normalize_frame(pd.concat([cached,fresh]));source=f"{family}+CACHE:{provider}"
        else:
            merged=normalize_frame(fresh);source=f"{family}_REBUILD:{provider}" if len(cached) else f"{family}:{provider}"
        frame=merged[(merged.index>=pd.Timestamp(wanted_start))&(merged.index<=pd.Timestamp(wanted_end))]
        if frame.empty:continue
        q=period_quality(frame,start,end,"stock","1h")
        # Incomplete provider responses may be retained in the cache for future
        # completion, but are not returned as a valid full-period series if a
        # fallback provider can still be tried.
        write_csv_cache(path,merged);write_cache_meta(path,{"source_family":family,"provider_symbol":provider,"updated_at_utc":datetime.now(timezone.utc).isoformat(),"identity_status":identity_status(symbol,provider,cur)})
        symbol_mapping_rows.append({"etoro_symbol":symbol,"provider_symbol":provider,"method":method,"currency":cur,"exchange":meta.get("exchange",""),
                                    "identity_status":identity_status(symbol,provider,cur),"quality":q.get("quality"),"coverage_pct":q.get("calendar_coverage_pct")})
        if q.get("ranking_eligible") or family==order[-1]:return frame,source,provider
        all_errors.append(f"{family}:{provider}:Datenqualitaet {q.get('quality')} coverage={q.get('calendar_coverage_pct')}")
    raise RuntimeError("keine identitaetssichere 1h-Historie; "+(" | ".join(all_errors[-12:]) if all_errors else "kein Provider lieferte Daten"))

def okx_public_client():
    from broker.okx import OKXClient
    client=OKXClient(api_key="", api_secret="", passphrase="", demo=False,
                     base_url=str(getattr(config,"OKX_BASE_URL","https://eea.okx.com")))
    original=client.request
    def safe_request(method, path, *a, **kw):
        if str(method).upper() != "GET" or bool(kw.get("private", False)):
            raise RuntimeError(f"BACKTEST-SCHUTZ: nicht-lesender OKX-Aufruf blockiert: {method} {path}")
        return original(method, path, *a, **kw)
    client.request=safe_request
    return client


def resolve_okx_inst(member, client, catalog=None):
    inst=str(member.get("inst_id") or "").upper().strip()
    symbol=str(member.get("symbol") or "").upper().strip()
    if inst and "-" in inst: return inst
    catalog = catalog if catalog is not None else client.public_instruments()
    candidates=[x for x in catalog.values() if str(getattr(x,"base_ccy","")).upper()==symbol and bool(getattr(x,"ist_live",False))]
    preferred=[]
    for q in (str(getattr(config,"OKX_PRIMARY_QUOTE_CCY","EUR")).upper(),"EUR","USDC","USD","USDT"):
        preferred += [x for x in candidates if str(getattr(x,"quote_ccy","")).upper()==q]
    return str(preferred[0].inst_id) if preferred else ""


def okx_history_5m(inst_id, start, end, client):
    """Fortsetzbarer 5m-Download mit Checkpoints im gemeinsamen Backtest-Cache."""
    path=cache_csv_path("okx",inst_id,"5m")
    cached=read_csv_cache(path)
    had_cache=bool(len(cached))
    wanted_start=start-timedelta(minutes=5*(int(FT_STARTUP)+12))
    wanted_end=end
    ws=pd.Timestamp(wanted_start);we=pd.Timestamp(wanted_end)
    if len(cached) and cached.index.min()<=ws and cached.index.max()>=we-pd.Timedelta(minutes=15):
        return cached[(cached.index>=ws)&(cached.index<=we)],"CACHE",0

    requests_count=0;last_oldest=None
    min_pause=1.0/max(0.2,float(args.okx_rps))
    checkpoint=max(5,int(args.okx_checkpoint_pages or 25))
    pages=[]

    # Ist der obere Rand bereits vorhanden, wird direkt am aeltesten Cachepunkt
    # weiter nach hinten geladen. Ein abgebrochener mehrstuendiger Lauf beginnt
    # daher nicht wieder bei heute.
    if len(cached) and cached.index.max()>=we-pd.Timedelta(minutes=15):
        cursor=int(cached.index.min().timestamp()*1000)-1
        log(f"    {inst_id}: setze Cache fort ab {cached.index.min().date().isoformat()} (bereits {len(cached)} Bars)")
    else:
        cursor=int(we.timestamp()*1000)

    total_minutes=max(1.0,(we-ws).total_seconds()/60.0)

    def checkpoint_write(force=False):
        nonlocal cached,pages
        if not pages:return
        if not force and len(pages)<checkpoint:return
        cached=normalize_frame(pd.concat([cached,*pages])) if len(cached) else normalize_frame(pd.concat(pages))
        write_csv_cache(path,cached)
        pages=[]

    try:
        while True:
            t0=time.monotonic()
            page=client.historical_candles(inst_id,bar="5m",limit=100,end_ms=cursor,nur_abgeschlossen=True)
            requests_count+=1
            page=normalize_frame(page)
            if page.empty:break
            oldest=page.index.min();newest=page.index.max()
            if last_oldest is not None and oldest>=last_oldest:
                break
            last_oldest=oldest
            pages.append(page)

            # Wenn wir beim frischen oberen Download in bereits vorhandenen
            # Cache hineinlaufen, springen wir an dessen unteren Rand weiter.
            if len(cached) and oldest<=cached.index.max()+pd.Timedelta(minutes=5) and cached.index.min()<oldest:
                cursor=int(cached.index.min().timestamp()*1000)-1
            else:
                cursor=int(oldest.timestamp()*1000)-1

            checkpoint_write(False)
            if oldest<=ws:break
            elapsed=time.monotonic()-t0
            if elapsed<min_pause:time.sleep(min_pause-elapsed)

            if requests_count%50==0:
                progressed=max(0.0,min(1.0,(we-oldest).total_seconds()/60.0/total_minutes))
                log(f"    {inst_id}: {requests_count} neue Seiten | bis {oldest.date().isoformat()} | ca. {progressed*100:5.1f}% Zeitspanne")
            if requests_count>5000:
                raise RuntimeError("Sicherheitsgrenze 5000 History-Seiten pro Instrument erreicht")
    except BaseException:
        checkpoint_write(True)
        raise
    checkpoint_write(True)
    merged=read_csv_cache(path)
    frame=merged[(merged.index>=ws)&(merged.index<=we)] if len(merged) else merged
    if frame.empty:
        raise RuntimeError(f"OKX liefert keine 5m-Historie fuer {inst_id}")
    return frame,("OKX_PUBLIC+CACHE" if had_cache else "OKX_PUBLIC"),requests_count

def resample_15m(frame5):
    if frame5.empty: return frame5
    # Nur echte vollstaendige 3x5m-Gruppen. Keine erfundenen Kerzen.
    g=frame5.resample("15min", label="left", closed="left")
    out=g.agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
    counts=g["close"].count()
    out=out[counts==3]
    return normalize_frame(out)

def frame_digest(frame):
    if frame is None or frame.empty:return None
    cols=[c for c in ("open","high","low","close","volume") if c in frame.columns]
    payload=frame[cols].to_csv(index=True,float_format="%.10g").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

def _longest_missing_calendar_run(expected_dates, present_dates):
    present=set(pd.Timestamp(x).date() for x in present_dates);best=cur=0
    for x in expected_dates:
        if pd.Timestamp(x).date() in present:cur=0
        else:cur+=1;best=max(best,cur)
    return best

def period_quality(frame,start,end,asset_type,timeframe):
    """Measure actual temporal completeness, not merely whether a test ran."""
    sub=frame[(frame.index>=pd.Timestamp(start))&(frame.index<=pd.Timestamp(end))] if frame is not None and len(frame) else pd.DataFrame()
    if sub.empty:return {"bars_period":0,"zero_volume_pct":None,"data_from":None,"data_to":None,"quality":"FEHLT","ranking_eligible":False,"quality_reason":"keine Bars im Zeitraum"}
    sub=normalize_frame(sub);zero=100.0*float((sub["volume"]<=0).sum())/len(sub) if "volume" in sub else 0.0
    first=sub.index.min();last=sub.index.max();result={"bars_period":int(len(sub)),"zero_volume_pct":zero,"data_from":first.isoformat(),"data_to":last.isoformat(),"input_digest":frame_digest(sub),"quality":"OK"}
    end_tolerance=pd.Timedelta(days=4 if pd.Timestamp(end).date()>=datetime.now(timezone.utc).date()-timedelta(days=2) else 10)
    partial_start=first>pd.Timestamp(start)+pd.Timedelta(days=14);partial_end=last<pd.Timestamp(end)-end_tolerance
    result["period_partial_start"]=bool(partial_start);result["period_partial_end"]=bool(partial_end)
    result["liquidity_warning"]="HOHER_NULLVOLUMENANTEIL" if zero>20 else ("NULLVOLUMEN_AUFFAELLIG" if zero>10 else "")
    ranking=True;reasons=[]
    if partial_start:ranking=False;reasons.append("Zeitraumanfang fehlt")
    if partial_end:ranking=False;reasons.append("Zeitraumende fehlt")
    tf=str(timeframe).lower()
    if asset_type=="crypto" and tf in {"5m","15m"}:
        minutes=5 if tf=="5m" else 15
        expected_start=max(pd.Timestamp(start),first);expected_end=min(pd.Timestamp(end),last+pd.Timedelta(minutes=minutes))
        expected=max(1,int((expected_end-expected_start).total_seconds()//(minutes*60))+1)
        actual=len(sub[(sub.index>=expected_start)&(sub.index<=expected_end)])
        coverage=100.0*actual/expected;result["bar_coverage_pct"]=coverage;result["calendar_coverage_pct"]=coverage;result["missing_bars_estimate"]=max(0,expected-actual)
        if coverage<98.0:ranking=False;reasons.append(f"Barabdeckung {coverage:.1f}% < 98%")
    elif asset_type=="crypto":
        exp=pd.date_range(pd.Timestamp(start).normalize(),pd.Timestamp(end).normalize(),freq="1D",tz="UTC")
        pres=pd.DatetimeIndex(sub.index).tz_convert("UTC").normalize().unique();coverage=100.0*len(set(pres))/max(1,len(exp))
        result["calendar_coverage_pct"]=coverage;result["largest_missing_day_run"]=_longest_missing_calendar_run(exp,pres)
        if coverage<97.0:ranking=False;reasons.append(f"Tagesabdeckung {coverage:.1f}% < 97%")
    else:
        # Equities trade on business days. Holidays make a theoretical B-day
        # denominator slightly conservative, so 92% is deliberately below 100%.
        exp=pd.date_range(pd.Timestamp(start).normalize(),pd.Timestamp(end).normalize(),freq="B",tz="UTC")
        present_all=pd.DatetimeIndex(sub.index).tz_convert("UTC").normalize().unique();pres=[x for x in present_all if x.weekday()<5]
        coverage=100.0*len(set(pres))/max(1,len(exp));missing_run=_longest_missing_calendar_run(exp,pres)
        result["calendar_coverage_pct"]=coverage;result["largest_missing_business_day_run"]=missing_run
        by_day=sub.groupby(pd.DatetimeIndex(sub.index).tz_convert("UTC").date).size();result["median_bars_per_data_day"]=float(by_day.median()) if len(by_day) else None
        if coverage<92.0:ranking=False;reasons.append(f"Handelstagsabdeckung {coverage:.1f}% < 92%")
        if missing_run>7:ranking=False;reasons.append(f"interne Luecke {missing_run} Geschaeftstage")
    result["ranking_eligible"]=bool(ranking)
    result["quality_reason"]="; ".join(reasons) if reasons else (result["liquidity_warning"] or "vollstaendige Zeitabdeckung")
    if not ranking:
        result["quality"]="QUALITAETSSPERRE"
    elif zero>50:
        result["quality"]="EXTREM_VIELE_NULLVOLUMEN_BARS"
    elif zero>20:
        result["quality"]="VOLUMEN_AUFFAELLIG"
    elif zero>10:
        result["quality"]="VOLUMEN_LEICHT_AUFFAELLIG"
    else:result["quality"]="OK"
    return result

# ---------- Backtestmodelle ----------------------------------------------
def comparison_position_pct():
    # Gleiche Positionsobergrenze fuer den Strategievergleich. V2 griff auf
    # einen nicht vorhandenen Namen MAX_POSITION_SIZE_PCT zurueck und konnte
    # dadurch faelschlich 20 % statt des NEXUS-Backtestlimits verwenden.
    for name in ("BACKTEST_POSITION_PCT","MAX_POSITION_PCT"):
        try:
            value=float(getattr(config,name))
            if 0 < value <= 1:
                return value,name
        except Exception:
            pass
    return 0.04,"SAFE_FALLBACK_4PCT"

COMPARISON_POSITION_PCT, COMPARISON_POSITION_SOURCE = comparison_position_pct()
def nexus_standard_period(raw, start, end, entry_mode, asset_type, currency="USD"):
    if not compat["nexus_standard"]:
        raise RuntimeError("NEXUS-Standard-Backtest-API in dieser Version nicht kompatibel")
    old_mode=getattr(config,"ENTRY_MODE",None); old_ml=getattr(config,"USE_ML_FILTER",None)
    try:
        config.ENTRY_MODE=entry_mode
        # Aktueller NEXUS-Default ist deterministisch ohne ML. Ein spaeteres ML-Modell
        # darf nicht ohne sauberes, zeitlich getrenntes Training heimlich hineingeraten.
        config.USE_ML_FILTER=False
        prepared=nexus_bt._prepare_full(raw)
        if not isinstance(prepared.index,pd.DatetimeIndex):
            raise RuntimeError("vorbereitete NEXUS-Daten haben keinen DatetimeIndex")
        idx=prepared.index
        ts0=pd.Timestamp(start); ts1=pd.Timestamp(end)
        pos0=int(idx.searchsorted(ts0, side="left")); pos1=int(idx.searchsorted(ts1, side="right"))
        if pos1-pos0 < 50: raise RuntimeError("zu wenige Bars im Zielzeitraum")
        cap_pct=float(COMPARISON_POSITION_PCT)
        result=nexus_bt._simulate(prepared,pos0,pos1,None,float(args.capital),cap_pct,asset_type=asset_type,currency=str(currency or "USD").upper())
        if result is None: raise RuntimeError("Simulation lieferte kein Ergebnis")
        trades,equity,bh,n_bars,costs=result
        summary,_,_=nexus_bt.summarize_backtest(trades,equity,float(args.capital),bh,n_bars,verbose=False,costs=costs)
        return summary,trades
    finally:
        if old_mode is not None: config.ENTRY_MODE=old_mode
        if old_ml is not None: config.USE_ML_FILTER=old_ml


def freqtrade_period(frame5, start, end):
    if not compat["freqtrade_sample"]:
        raise RuntimeError("Freqtrade-Sample-Backtest in dieser Version nicht verfuegbar")
    start_ts=pd.Timestamp(start); end_ts=pd.Timestamp(end)
    pre=frame5[frame5.index<start_ts].tail(int(FT_STARTUP))
    body=frame5[(frame5.index>=start_ts)&(frame5.index<=end_ts)]
    if len(body) < 2: raise RuntimeError("keine 5m-Kerzen im Zielzeitraum")
    # Neue/juengere Instrumente duerfen nach ihrer Boersennotierung trotzdem
    # getestet werden. Fehlen Kerzen vor dem Periodenstart, dienen die ersten
    # Periodenkerzen ausschliesslich als Startup-Historie; sie erzeugen noch
    # kein Signal.
    need=max(0,int(FT_STARTUP)-len(pre))
    if len(body) <= need+1:
        raise RuntimeError(f"zu wenig Historie: {len(pre)} Vorlauf + {len(body)} Periodenkerzen fuer {FT_STARTUP} Startup-Kerzen")
    warm_inside=body.iloc[:need] if need else body.iloc[:0]
    test_body=body.iloc[need:]
    inp=pd.concat([pre,warm_inside,test_body])
    result=ft_run_backtest(
        inp,
        initial_capital=float(args.capital),
        stake_pct=float(COMPARISON_POSITION_PCT),
        fee_pct=max(0.0,float(getattr(config,"OKX_TAKER_FEE_PCT",0.0035))),
        slippage_pct=max(0.0,float(getattr(config,"CRYPTO_SLIPPAGE_PCT",0.0015))),
        execution_mode="observed_bars",
        include_equity_curve=False,
    )
    summary={
        "trades_total": result.get("trades",0),
        "win_rate_pct": result.get("win_rate_pct",0.0),
        "profit_factor": result.get("profit_factor"),
        "total_return_pct": result.get("return_pct",0.0),
        "max_drawdown_pct": -abs(safe_float(result.get("max_drawdown_pct"),0.0)),
        "final_equity": result.get("final_capital",args.capital),
        "bars_tested": max(0,int(result.get("candles",0))-int(FT_STARTUP)),
        "buy_hold_return_pct": None,
        "total_trading_cost": sum(safe_float(t.get("fees"),0.0) for t in result.get("trade_rows") or []),
        "synthetic_gap_candles": result.get("synthetic_gap_candles",0),
        "result_schema_version": result.get("result_schema_version"),
        "input_digest": result.get("input_digest"),
    }
    return summary, list(result.get("trade_rows") or []), result

# ---------- V5: Strategien, Portfolio und robuste Validierung -----------------

def synthetic_data(freq, periods_count, start="2024-01-01"):
    idx=pd.date_range(start=start, periods=int(periods_count), freq=freq, tz="UTC")
    x=np.arange(len(idx),dtype=float)
    # Multiple cycles + trend changes to exercise trend, mean-reversion and risk exits.
    base=100.0 + 0.004*x + 4.0*np.sin(x/37.0) + 1.8*np.sin(x/9.0)
    close=pd.Series(base,index=idx)
    open_=close.shift(1).fillna(close.iloc[0])
    high=np.maximum(open_,close)+0.55
    low=np.minimum(open_,close)-0.55
    volume=pd.Series(1200.0+250.0*(1+np.sin(x/17.0)),index=idx)
    return pd.DataFrame({"open":open_,"high":high,"low":low,"close":close,"volume":volume},index=idx)

# Ab hier ist V5 bewusst eigenstaendig: NEXUS selbst bleibt unveraendert.

try:
    from risk_manager import calculate_stop_take, size_new_position
    from cost_engine import regulatory_cost_for
except Exception as exc:
    calculate_stop_take = size_new_position = None
    regulatory_cost_for = None
    compat["notes"].append(f"V5-Risiko-/Kostenhelfer nicht vollstaendig importierbar: {type(exc).__name__}: {exc}")

try:
    from freqtrade_sample_backtest import _signals as ft_signal_frame
except Exception:
    ft_signal_frame = None

STRATEGY_SPECS = {
    "NEXUS_STANDARD": {
        "label": "NEXUS Standard",
        "brokers": ["eToro", "OKX"],
        "class": "NEXUS",
        "fidelity": "INSTALLIERTE NEXUS-REGELN",
        "source": "installierte NEXUS-Version",
        "params": {"entry_mode": str(getattr(config, "ENTRY_MODE", "trend"))},
    },
    "FREQTRADE_SAMPLE": {
        "label": "Freqtrade Sample",
        "brokers": ["OKX"],
        "class": "FREQTRADE",
        "fidelity": "INSTALLIERTE NEXUS-FREQMODE-REFERENZ",
        "source": "freqtrade_sample_strategy.py der installierten NEXUS-Version",
        "params": (ft_parameter_snapshot() if compat.get("freqtrade_sample") else {}),
    },
    "TURTLE_DONCHIAN": {
        "label": "Turtle / Donchian",
        "brokers": ["eToro", "OKX"],
        "class": "EXTERN",
        "fidelity": "HOCH - veroeffentlichte Regelparameter",
        "source": "https://github.com/vrajvyas14/donchian-channel-backtester",
        "params": {"timeframe": "1d", "entry_window": 20, "exit_window": 10, "atr_period": 14, "atr_stop_multiple": 2.0, "risk_per_trade": 0.01, "long_only": True},
    },
    "IBS_MEAN_REVERSION": {
        "label": "IBS Mean Reversion",
        "brokers": ["eToro"],
        "class": "EXTERN",
        "fidelity": "HOCH - Originalregeln, nicht die spaeter verbesserte Variante",
        "source": "https://www.reddit.com/r/algotrading/comments/1cwsco8/a_mean_reversion_strategy_with_211_sharpe/",
        "params": {"timeframe": "1d", "range_window": 25, "high_window": 10, "range_multiplier": 2.5, "ibs_threshold": 0.3, "exit": "close > previous high"},
    },
    "CRYPTO_MOMENTUM_REFERENCE": {
        "label": "Crypto Momentum Rotation",
        "brokers": ["OKX"],
        "class": "EXTERN_PORTFOLIO",
        "fidelity": "TEILWEISE - oeffentliches Regelgeruest, Ranking-Formel deterministisch dokumentiert",
        "source": "https://www.reddit.com/r/algotradingcrypto/comments/1ve9d6j/built_a_readonly_crypto_shadow_trader_187_cagr/",
        "params": {"timeframe": "1d", "momentum_days": [21,63,126], "volatility_adjustment": "mean(return_horizon / annualized_63d_vol)", "max_assets": 3, "regime_sma": 200, "history_min_days": 365, "liquidity_window": 90, "liquidity_top_n": 10, "rebalance": "weekly", "sizing": "inverse_20d_volatility", "note": "Exakte private Ranking-Gewichtung war in der zugaenglichen Quelle nicht angegeben."},
    },
}
if args.mit_crossover_forschung:
    STRATEGY_SPECS["NEXUS_CROSSOVER_RESEARCH"] = {
        "label": "NEXUS Crossover (Forschung)", "brokers": ["eToro","OKX"], "class": "RESEARCH",
        "fidelity": "INSTALLIERTE ALTERNATIVE ENTRY-LOGIK", "source": "strategy.py der installierten NEXUS-Version",
        "params": {"entry_mode": "crossover"},
    }

# V6: zusaetzliche Strategien plus leicht verstaendliche Erklaerungen fuer alle
# Karten. "beschreibung" ist der Zwei-Satz-Ueberblick, "details" die
# aufklappbare Erklaerung im Bericht. Quellen sind oeffentlich dokumentiert;
# vergangene Ergebnisse sind kein Versprechen fuer die Zukunft.
STRATEGY_SPECS.update({
    "RSI2_MEAN_REVERSION": {
        "label": "RSI-2 Ruecksetzer (Connors)", "brokers": ["eToro"], "class": "EXTERN",
        "fidelity": "HOCH - veroeffentlichte Originalregeln (Connors/Alvarez)",
        "source": "https://www.quantifiedstrategies.com/rsi-2-strategy/",
        "params": {"timeframe": "1d", "rsi_period": 2, "buy_below": 10, "trend_sma": 200, "exit_sma": 5},
        "beschreibung": "Kauft starke Aktien im Aufwaertstrend, wenn sie kurz heftig durchsacken - und verkauft schon nach wenigen Tagen wieder.",
        "details": [
            "Kauf: Die Aktie notiert ueber ihrem 200-Tage-Durchschnitt (gesunder Aufwaertstrend) UND der sehr kurze RSI(2)-Indikator faellt unter 10 - ein Zeichen fuer einen uebertriebenen Kurzschock.",
            "Verkauf: Sobald der Kurs wieder ueber seinen 5-Tage-Durchschnitt steigt (der Ruecksetzer ist aufgeholt) oder der Aufwaertstrend bricht.",
            "Idee dahinter: Kurze Panik in ansonsten starken Aktien wird an der Boerse oft schnell wieder ausgeglichen. Larry Connors hat diese Regeln ab den 1990ern ausfuehrlich getestet und veroeffentlicht.",
            "Typische Schwaeche: In echten Abwaertstrends kauft sie zu frueh; deshalb ist der 200-Tage-Filter Pflicht. Viele kleine Gewinne, gelegentlich groessere Einzelverluste.",
        ],
    },
    "HIGH_52W_MOMENTUM": {
        "label": "52-Wochen-Hoch Momentum", "brokers": ["eToro"], "class": "EXTERN",
        "fidelity": "HOCH - akademisch dokumentierter Effekt (George/Hwang 2004)",
        "source": "https://www.jstor.org/stable/3694871",
        "params": {"timeframe": "1d", "high_window": 252, "proximity": 0.97, "trend_sma": 100, "exit_sma": 50},
        "beschreibung": "Kauft Aktien, die dicht an ihrem 52-Wochen-Hoch notieren - Staerke zieht erfahrungsgemaess weitere Staerke nach sich.",
        "details": [
            "Kauf: Der Kurs steht bei mindestens 97 Prozent seines hoechsten Stands der letzten 52 Wochen UND ueber dem 100-Tage-Durchschnitt.",
            "Verkauf: Wenn der Kurs unter seinen 50-Tage-Durchschnitt faellt - die Staerke laesst nach.",
            "Idee dahinter: Anleger zoegern nahe alter Hochs ('zu teuer'), wodurch gute Nachrichten nur verzoegert im Kurs ankommen. Die Studie von George und Hwang (2004) zeigt, dass die Naehe zum 52-Wochen-Hoch kuenftige Renditen besser vorhersagt als klassisches Momentum.",
            "Typische Schwaeche: Bei ploetzlichen Markteinbruechen ist sie voll investiert; der 50-Tage-Ausstieg reagiert erst mit Verzoegerung.",
        ],
    },
    "GOLDEN_CROSS_TREND": {
        "label": "Goldenes Kreuz (50/200)", "brokers": ["eToro"], "class": "EXTERN",
        "fidelity": "HOCH - klassische, vielfach dokumentierte Trendfolgeregel",
        "source": "https://chartschool.stockcharts.com/table-of-contents/trading-strategies-and-models/trading-strategies",
        "params": {"timeframe": "1d", "fast_sma": 50, "slow_sma": 200},
        "beschreibung": "Die wohl bekannteste Trendfolgeregel: investiert sein, solange der 50-Tage-Durchschnitt ueber dem 200-Tage-Durchschnitt liegt.",
        "details": [
            "Kauf: Der 50-Tage-Durchschnitt liegt ueber dem 200-Tage-Durchschnitt (das 'Goldene Kreuz') - der mittelfristige Trend zeigt nach oben.",
            "Verkauf: Der 50-Tage-Durchschnitt faellt unter den 200-Tage-Durchschnitt (das 'Todeskreuz').",
            "Idee dahinter: Grosse Auf- und Abwaertsphasen dauern meist Monate. Die Regel verpasst zwar Anfang und Ende, faengt aber den Mittelteil grosser Trends ein und haelt einen aus langen Baerenmaerkten heraus.",
            "Typische Schwaeche: In Seitwaertsmaerkten erzeugen die Kreuzungen Fehlsignale ('Whipsaws'), die sich zu vielen kleinen Verlusten addieren.",
        ],
    },
    "TSMOM_LONG_FLAT": {
        "label": "Zeitreihen-Momentum (Krypto)", "brokers": ["OKX"], "class": "EXTERN",
        "fidelity": "HOCH - akademisch dokumentiert (Moskowitz/Ooi/Pedersen 2012), hier long/flat",
        "source": "https://pages.stern.nyu.edu/~lpederse/papers/TimeSeriesMomentum.pdf",
        "params": {"timeframe": "1d", "lookback_days": 90, "exit_lookback_days": 30, "trend_sma": 100},
        "beschreibung": "Haelt einen Coin nur, wenn er in den letzten Monaten gestiegen ist - sonst bleibt das Geld in Cash.",
        "details": [
            "Kauf: Die Rendite der letzten 90 Tage ist positiv UND der Kurs liegt ueber dem 100-Tage-Durchschnitt.",
            "Verkauf: Die 30-Tage-Rendite dreht negativ oder der Kurs faellt unter den 100-Tage-Durchschnitt - dann komplett in Cash.",
            "Idee dahinter: 'Was zuletzt stieg, steigt im Schnitt weiter' ist einer der am breitesten belegten Kapitalmarkteffekte (Studie ueber 58 Maerkte und 25 Jahre). Gerade Krypto hatte historisch lange, ausgepraegte Trends.",
            "Typische Schwaeche: An scharfen Wendepunkten gibt sie einen Teil der Gewinne wieder ab und steigt nach Boeden erst verspaetet wieder ein.",
        ],
    },
    "KELTNER_BREAKOUT": {
        "label": "Keltner-Ausbruch (Krypto)", "brokers": ["OKX"], "class": "EXTERN",
        "fidelity": "HOCH - veroeffentlichte Standardregeln (EMA20 + 2 x ATR10)",
        "source": "https://www.quantifiedstrategies.com/keltner-bands-trading-strategies/",
        "params": {"timeframe": "1d", "ema_period": 20, "atr_period": 10, "atr_mult": 2.0},
        "beschreibung": "Kauft erst, wenn der Kurs kraftvoll aus seinem normalen Schwankungsband nach oben ausbricht - und steigt beim Rueckfall zur Mitte wieder aus.",
        "details": [
            "Kauf: Der Schlusskurs schliesst ueber dem oberen Keltner-Band (20-Tage-EMA plus 2 x durchschnittliche Tagesschwankung ATR) - ein ungewoehnlich starker Ausbruch.",
            "Verkauf: Der Kurs faellt zurueck unter den 20-Tage-EMA, die Mitte des Bandes.",
            "Idee dahinter: Weil sich das Band an die aktuelle Schwankungsbreite anpasst, zaehlt nur echte, ungewoehnliche Staerke als Signal - das filtert viele Fehlausbrueche heraus, die feste Kanaele (wie Donchian) mitnehmen.",
            "Typische Schwaeche: In nervoesen Seitwaertsphasen folgen auf Ausbrueche oft schnelle Ruecksetzer; die Trefferquote ist maessig, einzelne grosse Trends muessen die vielen kleinen Verluste bezahlen.",
        ],
    },
    "MACD_TREND_CRYPTO": {
        "label": "MACD-Trend (Krypto)", "brokers": ["OKX"], "class": "EXTERN",
        "fidelity": "HOCH - klassischer Indikator (Gerald Appel), Standardparameter 12/26/9",
        "source": "https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/moving-average-convergence-divergence-macd",
        "params": {"timeframe": "1d", "fast_ema": 12, "slow_ema": 26, "signal_ema": 9},
        "beschreibung": "Misst mit zwei gleitenden Durchschnitten, ob der Aufwaertstrend an Kraft gewinnt - und ist nur dann investiert.",
        "details": [
            "Kauf: Die MACD-Linie (Abstand zwischen 12- und 26-Tage-EMA) liegt ueber ihrer 9-Tage-Signallinie UND ueber null - der Aufwaertstrend beschleunigt.",
            "Verkauf: Die MACD-Linie faellt unter die Signallinie - der Schwung laesst nach.",
            "Idee dahinter: Der MACD (seit den 1970ern von Gerald Appel dokumentiert) macht Trendwechsel frueh sichtbar, weil er Beschleunigung statt nur Richtung misst. Die Null-Linien-Bedingung haelt die Strategie aus Baerenmaerkten heraus.",
            "Typische Schwaeche: Wie jede Trendfolge produziert er in Seitwaertsmaerkten Fehlsignale und reagiert auf ploetzliche Einbrueche erst verzoegert.",
        ],
    },
})
STRATEGY_SPECS["NEXUS_STANDARD"].update({
    "beschreibung": "Die eingebauten NEXUS-Regeln der installierten Version - genau die Logik, die der Bot im Standardmodus live verwendet.",
    "details": [
        "Kauf und Verkauf: exakt die entry_conditions/exit_conditions der installierten NEXUS-Version im konfigurierten Modus.",
        "Zweck im Backtest: zeigt, wie sich die aktuelle Live-Logik historisch geschlagen haette - inklusive aller Kosten.",
    ],
})
STRATEGY_SPECS["FREQTRADE_SAMPLE"].update({
    "beschreibung": "Die offizielle Freqtrade-Beispielstrategie (RSI + Bollinger + TEMA) - von den Freqtrade-Autoren ausdruecklich als Lehrbeispiel, nicht als Gewinnstrategie gedacht.",
    "details": [
        "Kauf: RSI kreuzt ueber 30, der Kurs liegt unter dem unteren Bollinger-Band-Bereich und die TEMA-Kurve steigt.",
        "Verkauf: RSI kreuzt ueber 70 bzw. Stop-Loss/ROI-Ziele der Beispielkonfiguration.",
        "Zweck im Backtest: Referenz fuer den Freqtrade-Modus des Bots; sie ist bewusst unveraendert uebernommen.",
    ],
})
STRATEGY_SPECS["TURTLE_DONCHIAN"].update({
    "beschreibung": "Der beruehmte Turtle-Ansatz: kaufen, wenn der Kurs ueber sein 20-Tage-Hoch ausbricht, verkaufen unter dem 10-Tage-Tief - mit ATR-Stop.",
    "details": [
        "Kauf: Schlusskurs ueber dem hoechsten Hoch der letzten 20 Tage (Ausbruch).",
        "Verkauf: Schlusskurs unter dem tiefsten Tief der letzten 10 Tage oder am 2-ATR-Stop.",
        "Idee dahinter: Das Original-Regelwerk der Turtle-Trader aus den 1980ern; lebt von wenigen grossen Trends und verkraftet dafuer viele kleine Verluste.",
        "Typische Schwaeche: In Seitwaertsmaerkten viele Fehlausbrueche.",
    ],
})
STRATEGY_SPECS["IBS_MEAN_REVERSION"].update({
    "beschreibung": "Kauft Aktien, die nahe am Tagestief UND deutlich unter ihrem juengsten Hoch schliessen - und verkauft beim ersten Erholungstag.",
    "details": [
        "Kauf: Der Schlusskurs liegt weit unten in der Tagesspanne (IBS unter 0,3) und deutlich unter dem 10-Tage-Hoch (mehr als das 2,5-fache der ueblichen Tagesspanne).",
        "Verkauf: Der Kurs schliesst ueber dem Hoch des Vortages.",
        "Idee dahinter: Kurzfristige Uebertreibungen nach unten gleichen sich bei Aktien oft binnen weniger Tage aus; hohe Trefferquote, kleine Einzelgewinne.",
        "Typische Schwaeche: In echten Crashs kauft sie in fallende Kurse.",
    ],
})
STRATEGY_SPECS["CRYPTO_MOMENTUM_REFERENCE"].update({
    "beschreibung": "Ein woechentlich rotierendes Krypto-Portfolio: haelt die 2-3 staerksten Coins nach Mehrmonats-Momentum, gewichtet nach Schwankung.",
    "details": [
        "Kauf: Woechentliches Ranking der Coins nach 21/63/126-Tage-Momentum, schwankungsbereinigt; nur die Top 3 mit ausreichend Historie und Liquiditaet werden gehalten.",
        "Verkauf: Beim naechsten woechentlichen Rebalancing, wenn ein Coin aus den Top-Raengen faellt oder der Marktfilter (200-Tage-Linie) dreht.",
        "Idee dahinter: Rotation in die relative Staerke des Kryptomarkts, dokumentiertes oeffentliches Regelgeruest.",
        "Typische Schwaeche: An Wendepunkten haengt sie in den Vorwochen-Gewinnern fest.",
    ],
})


for _key, _spec in STRATEGY_SPECS.items():
    _spec["parameter_hash"] = hashlib.sha256(json.dumps(_spec.get("params",{}), sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def broker_position_pct(broker: str) -> float:
    names = ("ETORO_MAX_POSITION_PCT","MAX_POSITION_PCT") if broker == "eToro" else ("OKX_MAX_POSITION_PCT","CRYPTO_MAX_POSITION_PCT","MAX_POSITION_PCT")
    for name in names:
        try:
            value=float(getattr(config,name))
            if 0 < value <= 1: return value
        except Exception: pass
    return float(COMPARISON_POSITION_PCT)


def broker_max_positions(broker: str) -> int:
    if int(args.max_portfolio_positionen or 0) > 0:
        return int(args.max_portfolio_positionen)
    names=("ETORO_MAX_OPEN_POSITIONS","MAX_OPEN_POSITIONS") if broker=="eToro" else ("OKX_MAX_OPEN_POSITIONS","MAX_OPEN_POSITIONS")
    for name in names:
        try:
            value=int(getattr(config,name))
            if value > 0:return value
        except Exception:pass
    return 8


def broker_max_trades_day(broker: str) -> int:
    names=("ETORO_MAX_TRADES_PER_DAY","MAX_TRADES_PER_DAY") if broker=="eToro" else ("OKX_MAX_TRADES_PER_DAY","MAX_TRADES_PER_DAY")
    for name in names:
        try:
            value=int(getattr(config,name))
            if value>0:return value
        except Exception:pass
    return 999999


def local_daily(frame: pd.DataFrame, timezone_name: str = "UTC", crypto: bool = False) -> pd.DataFrame:
    """Daily OHLCV from observed intraday bars; never invents missing bars."""
    if frame is None or frame.empty:return pd.DataFrame(columns=["open","high","low","close","volume"])
    f=normalize_frame(frame)
    if crypto:
        g=f.resample("1D",label="left",closed="left")
        out=g.agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"})
        counts=g["close"].count()
        # 5m-derived crypto day: keep only reasonably observed days. For direct 1D data counts==1.
        if counts.max() > 5:
            out=out[counts>=274]  # >=95% of 288 expected 5m bars
        return normalize_frame(out)
    try:
        local=f.tz_convert(timezone_name)
    except Exception:
        local=f
    rows=[]
    for _,grp in local.groupby(local.index.date):
        if grp.empty:continue
        rows.append({"date":grp.index[-1].tz_convert("UTC") if getattr(grp.index[-1],"tzinfo",None) else pd.Timestamp(grp.index[-1],tz="UTC"),
                     "open":float(grp.iloc[0]["open"]),"high":float(grp["high"].max()),"low":float(grp["low"].min()),
                     "close":float(grp.iloc[-1]["close"]),"volume":float(grp["volume"].sum())})
    if not rows:return pd.DataFrame(columns=["open","high","low","close","volume"])
    out=pd.DataFrame(rows).set_index("date")
    return normalize_frame(out)


def true_range_atr(df: pd.DataFrame, period: int=14) -> pd.Series:
    prev=df["close"].shift(1)
    tr=pd.concat([(df["high"]-df["low"]).abs(),(df["high"]-prev).abs(),(df["low"]-prev).abs()],axis=1).max(axis=1)
    return tr.rolling(period,min_periods=period).mean()


def turtle_signals(daily: pd.DataFrame, entry_window=20, exit_window=10, atr_period=14) -> pd.DataFrame:
    d=normalize_frame(daily).copy()
    d["atr"]=true_range_atr(d,atr_period)
    d["donchian_entry_high"]=d["high"].shift(1).rolling(entry_window,min_periods=entry_window).max()
    d["donchian_exit_low"]=d["low"].shift(1).rolling(exit_window,min_periods=exit_window).min()
    d["enter_signal"]=(d["close"]>d["donchian_entry_high"]) & d["atr"].notna() & (d["volume"]>0)
    d["exit_signal"]=(d["close"]<d["donchian_exit_low"]) & (d["volume"]>0)
    return d


def ibs_signals(daily: pd.DataFrame, range_window=25, high_window=10, multiplier=2.5, ibs_threshold=0.3) -> pd.DataFrame:
    d=normalize_frame(daily).copy()
    day_range=(d["high"]-d["low"])
    d["avg_range"]=day_range.rolling(range_window,min_periods=range_window).mean()
    d["rolling_high"]=d["high"].rolling(high_window,min_periods=high_window).max()
    denom=(d["high"]-d["low"]).replace(0,np.nan)
    d["ibs"]=(d["close"]-d["low"])/denom
    d["lower_band"]=d["rolling_high"]-float(multiplier)*d["avg_range"]
    d["enter_signal"]=(d["close"]<d["lower_band"])&(d["ibs"]<float(ibs_threshold))&(d["volume"]>0)
    d["exit_signal"]=(d["close"]>d["high"].shift(1))&(d["volume"]>0)
    return d



# ---------- V6: sechs dokumentierte Referenzstrategien ------------------------
# Alle sind kausale Long-only-Tagesstrategien im selben Engine-Vertrag wie
# Turtle/IBS: Signal auf abgeschlossener Kerze, Ausfuehrung naechste Kerze.

def wilder_rsi(close, period):
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / float(period), min_periods=int(period), adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / float(period), min_periods=int(period), adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # avg_loss == 0 bei vorhandenem avg_gain bedeutet RSI 100.
    rsi = rsi.where(avg_loss.ne(0.0), 100.0)
    return rsi.where(avg_gain.notna() & avg_loss.notna())


def rsi2_signals(daily, rsi_period=2, buy_below=10.0, trend_sma=200, exit_sma=5):
    d = normalize_frame(daily).copy()
    d["rsi2"] = wilder_rsi(d["close"], int(rsi_period))
    d["sma_trend"] = d["close"].rolling(int(trend_sma), min_periods=int(trend_sma)).mean()
    d["sma_exit"] = d["close"].rolling(int(exit_sma), min_periods=int(exit_sma)).mean()
    d["enter_signal"] = (d["close"] > d["sma_trend"]) & (d["rsi2"] < float(buy_below)) & (d["volume"] > 0)
    d["exit_signal"] = ((d["close"] > d["sma_exit"]) | (d["close"] < d["sma_trend"])) & (d["volume"] > 0)
    return d


def high52w_signals(daily, high_window=252, proximity=0.97, trend_sma=100, exit_sma=50):
    d = normalize_frame(daily).copy()
    d["rolling_high52"] = d["high"].shift(1).rolling(int(high_window), min_periods=int(high_window)).max()
    d["sma_trend"] = d["close"].rolling(int(trend_sma), min_periods=int(trend_sma)).mean()
    d["sma_exit"] = d["close"].rolling(int(exit_sma), min_periods=int(exit_sma)).mean()
    d["enter_signal"] = (d["close"] >= float(proximity) * d["rolling_high52"]) & (d["close"] > d["sma_trend"]) & (d["volume"] > 0)
    d["exit_signal"] = (d["close"] < d["sma_exit"]) & (d["volume"] > 0)
    return d


def golden_cross_signals(daily, fast=50, slow=200):
    d = normalize_frame(daily).copy()
    d["sma_fast"] = d["close"].rolling(int(fast), min_periods=int(fast)).mean()
    d["sma_slow"] = d["close"].rolling(int(slow), min_periods=int(slow)).mean()
    above = (d["sma_fast"] > d["sma_slow"]) & d["sma_slow"].notna()
    d["enter_signal"] = above & (d["volume"] > 0)
    d["exit_signal"] = (~above) & d["sma_slow"].notna() & (d["volume"] > 0)
    return d


def tsmom_signals(daily, lookback=90, exit_lookback=30, trend_sma=100):
    d = normalize_frame(daily).copy()
    d["mom_entry"] = d["close"].pct_change(int(lookback))
    d["mom_exit"] = d["close"].pct_change(int(exit_lookback))
    d["sma_trend"] = d["close"].rolling(int(trend_sma), min_periods=int(trend_sma)).mean()
    d["enter_signal"] = (d["mom_entry"] > 0) & (d["close"] > d["sma_trend"]) & (d["volume"] > 0)
    d["exit_signal"] = ((d["mom_exit"] < 0) | (d["close"] < d["sma_trend"])) & (d["volume"] > 0)
    return d


def keltner_signals(daily, ema_period=20, atr_period=10, atr_mult=2.0):
    d = normalize_frame(daily).copy()
    d["ema_mid"] = d["close"].ewm(span=int(ema_period), min_periods=int(ema_period), adjust=False).mean()
    d["atr"] = true_range_atr(d, int(atr_period))
    d["kc_upper"] = d["ema_mid"] + float(atr_mult) * d["atr"]
    d["enter_signal"] = (d["close"] > d["kc_upper"].shift(1)) & (d["volume"] > 0)
    d["exit_signal"] = (d["close"] < d["ema_mid"]) & (d["volume"] > 0)
    return d


def macd_trend_signals(daily, fast=12, slow=26, signal=9):
    d = normalize_frame(daily).copy()
    ema_fast = d["close"].ewm(span=int(fast), min_periods=int(fast), adjust=False).mean()
    ema_slow = d["close"].ewm(span=int(slow), min_periods=int(slow), adjust=False).mean()
    d["macd"] = ema_fast - ema_slow
    d["macd_signal"] = d["macd"].ewm(span=int(signal), min_periods=int(signal), adjust=False).mean()
    d["enter_signal"] = (d["macd"] > d["macd_signal"]) & (d["macd"] > 0) & (d["volume"] > 0)
    d["exit_signal"] = (d["macd"] < d["macd_signal"]) & (d["volume"] > 0)
    return d


V6_STRATEGY_BUILDERS = {
    "RSI2_MEAN_REVERSION": (rsi2_signals, ("eToro",)),
    "HIGH_52W_MOMENTUM": (high52w_signals, ("eToro",)),
    "GOLDEN_CROSS_TREND": (golden_cross_signals, ("eToro",)),
    "TSMOM_LONG_FLAT": (tsmom_signals, ("OKX",)),
    "KELTNER_BREAKOUT": (keltner_signals, ("OKX",)),
    "MACD_TREND_CRYPTO": (macd_trend_signals, ("OKX",)),
}
V6_ETORO = ("RSI2_MEAN_REVERSION", "HIGH_52W_MOMENTUM", "GOLDEN_CROSS_TREND")
V6_OKX = ("TSMOM_LONG_FLAT", "KELTNER_BREAKOUT", "MACD_TREND_CRYPTO")


def nexus_signal_frame(raw: pd.DataFrame, entry_mode: str) -> pd.DataFrame:
    if not compat.get("nexus_standard"):
        raise RuntimeError("NEXUS-Standard-Signal-API nicht kompatibel")
    old_mode=getattr(config,"ENTRY_MODE",None);old_ml=getattr(config,"USE_ML_FILTER",None)
    try:
        config.ENTRY_MODE=str(entry_mode)
        config.USE_ML_FILTER=False
        d=nexus_strategy.prepare(raw).copy()
        if not isinstance(d.index,pd.DatetimeIndex):
            raise RuntimeError("NEXUS prepare() liefert keinen DatetimeIndex")
        entries=[];exits=[]
        for i in range(len(d)):
            if i < 1:
                entries.append(False);exits.append(False);continue
            try:ent=bool(nexus_strategy.entry_conditions(d,i,0.5)[0])
            except Exception:ent=False
            try:ext=bool(nexus_strategy.exit_conditions(d,i)[0])
            except Exception:ext=False
            entries.append(ent);exits.append(ext)
        d["enter_signal"]=entries;d["exit_signal"]=exits
        return d
    finally:
        if old_mode is not None:config.ENTRY_MODE=old_mode
        if old_ml is not None:config.USE_ML_FILTER=old_ml


def estimate_friction(asset_type: str, currency: str, raw_price: float, side: str, allocation: float, multiplier: float=1.0):
    raw=max(1e-12,float(raw_price)); allocation=max(1e-9,float(allocation))
    try:spread=max(0.0,float(nexus_bt.spread_for(asset_type)))
    except Exception:spread=0.0
    try:slip=max(0.0,float(nexus_bt.slippage_for(asset_type)))
    except Exception:slip=0.0
    spread*=float(multiplier);slip*=float(multiplier)
    signed=1.0 if str(side).upper()=="BUY" else -1.0
    fill=raw*(1.0+signed*(spread/2.0+slip))
    qty=max(1e-12,allocation/fill)
    try:commission=max(0.0,float(nexus_bt.commission_for(qty,fill,asset_type,currency)))
    except Exception:commission=0.0
    try:
        regulatory=max(0.0,float(regulatory_cost_for(qty,fill,asset_type,currency,side="buy" if signed>0 else "sell"))) if regulatory_cost_for else 0.0
    except Exception:regulatory=0.0
    fees=(commission+regulatory)*float(multiplier)
    total_pct=((abs(fill/raw-1.0))+fees/allocation)*100.0
    return fill,fees,total_pct,spread,slip


def simulate_signal_strategy(signal_df: pd.DataFrame, start, end, *, broker: str, symbol: str, strategy: str,
                             asset_type: str, currency: str="USD", stop_mode: str="none", turtle_atr_mult: float=2.0,
                             risk_per_trade: float|None=None) -> list[dict]:
    """Causal long-only engine: signal on completed bar -> fill next observed bar open."""
    d=signal_df.copy().sort_index()
    start_ts=pd.Timestamp(start);end_ts=pd.Timestamp(end)
    # Keep warmup rows before start but never enter before start.
    if d.empty:return []
    cap=broker_position_pct(broker)
    nominal=float(args.capital)*cap
    trades=[];position=None;pending_entry=None;pending_exit=None
    for i in range(1,len(d)):
        row=d.iloc[i];ts=d.index[i]
        prev=d.iloc[i-1];prev_ts=d.index[i-1]
        if ts < start_ts:continue
        if ts > end_ts:break

        # 1) known exit signal from prior completed candle executes first at this open.
        if position is not None and pending_exit is not None:
            raw=float(row["open"]);fill,fees,exit_cost_pct,_,_=estimate_friction(asset_type,currency,raw,"SELL",position["entry_cash"])
            gross=fill*position["qty"];net=gross-fees
            ret=(net/position["entry_cash"]-1.0)*100.0
            holding=d.iloc[position["entry_i"]:i+1]
            mfe=((holding["high"].max()/position["entry_fill"])-1.0)*100.0 if len(holding) else 0.0
            mae=((holding["low"].min()/position["entry_fill"])-1.0)*100.0 if len(holding) else 0.0
            next_open=float(d.iloc[i+1]["open"]) if i+1<len(d) else raw
            delay_penalty=max(0.0,(next_open/raw-1.0)*100.0)
            base_cost=position["entry_cost_pct"]+exit_cost_pct
            trades.append({"broker":broker,"strategy":strategy,"symbol":symbol,"signal_time":str(position["signal_time"]),
                           "entry_time":str(position["entry_time"]),"exit_signal_time":str(pending_exit),"exit_time":str(ts),
                           "entry_price":position["entry_fill"],"exit_price":fill,"return_pct":ret,
                           "requested_fraction":position["requested_fraction"],"entry_cost_pct":position["entry_cost_pct"],
                           "exit_cost_pct":exit_cost_pct,"base_cost_pct":base_cost,"delay_penalty_pct":delay_penalty,
                           "mfe_pct":mfe,"mae_pct":mae,"bars_held":i-position["entry_i"],"exit_reason":"signal"})
            position=None;pending_exit=None

        # 2) entry from prior signal executes at this open.
        if position is None and pending_entry is not None:
            raw=float(row["open"]);fill,fees,entry_cost_pct,_,_=estimate_friction(asset_type,currency,raw,"BUY",nominal)
            # Fee-aware quantity fitting inside nominal allocation.
            qty=max(0.0,(nominal-fees)/max(fill,1e-12));entry_cash=qty*fill+fees
            if qty>0 and entry_cash>0:
                stop=None;take=None;requested_fraction=cap
                sig_row=d.loc[pending_entry["signal_time"]]
                if stop_mode=="nexus" and calculate_stop_take and size_new_position:
                    try:
                        stop,take=calculate_stop_take(fill,"BUY",atr_value=sig_row.get("atr"),asset_type=asset_type)
                        q2=size_new_position(float(args.capital),fill,stop,max_capital_pct=cap,asset_type=asset_type)
                        requested_fraction=min(cap,max(0.0,float(q2)*fill/float(args.capital))) if q2 else cap
                    except Exception:pass
                elif stop_mode=="turtle":
                    atr=safe_float(sig_row.get("atr"),0.0)
                    if atr>0:
                        stop=fill-float(turtle_atr_mult)*atr
                        if risk_per_trade and stop<fill:
                            risk_frac=float(risk_per_trade)/(max(1e-12,(fill-stop)/fill))
                            requested_fraction=min(cap,max(0.0,risk_frac))
                position={"signal_time":pending_entry["signal_time"],"entry_time":ts,"entry_i":i,"entry_fill":fill,
                          "qty":qty,"entry_cash":entry_cash,"entry_cost_pct":entry_cost_pct,"stop":stop,"take":take,
                          "requested_fraction":requested_fraction}
            pending_entry=None

        # 3) intrabar risk after any next-open entry.
        if position is not None:
            stop=position.get("stop");take=position.get("take")
            reason=None;trigger=None
            if stop is not None and float(row["low"])<=float(stop):
                trigger=min(float(stop),float(row["open"])) if float(row["open"])<=float(stop) else float(stop);reason="stop_loss"
            elif take is not None and float(row["high"])>=float(take):
                trigger=float(take);reason="take_profit"
            if reason:
                fill,fees,exit_cost_pct,_,_=estimate_friction(asset_type,currency,float(trigger),"SELL",position["entry_cash"])
                net=fill*position["qty"]-fees;ret=(net/position["entry_cash"]-1.0)*100.0
                holding=d.iloc[position["entry_i"]:i+1]
                mfe=((holding["high"].max()/position["entry_fill"])-1.0)*100.0 if len(holding) else 0.0
                mae=((holding["low"].min()/position["entry_fill"])-1.0)*100.0 if len(holding) else 0.0
                next_open=float(d.iloc[i+1]["open"]) if i+1<len(d) else float(row["open"])
                delay_penalty=max(0.0,(next_open/float(row["open"])-1.0)*100.0)
                base_cost=position["entry_cost_pct"]+exit_cost_pct
                trades.append({"broker":broker,"strategy":strategy,"symbol":symbol,"signal_time":str(position["signal_time"]),
                               "entry_time":str(position["entry_time"]),"exit_signal_time":"INTRABAR","exit_time":str(ts),
                               "entry_price":position["entry_fill"],"exit_price":fill,"return_pct":ret,
                               "requested_fraction":position["requested_fraction"],"entry_cost_pct":position["entry_cost_pct"],
                               "exit_cost_pct":exit_cost_pct,"base_cost_pct":base_cost,"delay_penalty_pct":delay_penalty,
                               "mfe_pct":mfe,"mae_pct":mae,"bars_held":i-position["entry_i"],"exit_reason":reason})
                position=None;pending_exit=None

        # 4) Signals at this close become actionable only next observed open.
        if position is None:
            if bool(row.get("enter_signal",False)):
                pending_entry={"signal_time":ts}
        else:
            if bool(row.get("exit_signal",False)):
                pending_exit=ts

    # Honest period close: force close only if a test position remains.
    if position is not None:
        sub=d[(d.index>=start_ts)&(d.index<=end_ts)]
        if len(sub):
            ts=sub.index[-1];row=sub.iloc[-1]
            fill,fees,exit_cost_pct,_,_=estimate_friction(asset_type,currency,float(row["close"]),"SELL",position["entry_cash"])
            net=fill*position["qty"]-fees;ret=(net/position["entry_cash"]-1.0)*100.0
            holding=d.iloc[position["entry_i"]:d.index.get_loc(ts)+1]
            mfe=((holding["high"].max()/position["entry_fill"])-1.0)*100.0 if len(holding) else 0.0
            mae=((holding["low"].min()/position["entry_fill"])-1.0)*100.0 if len(holding) else 0.0
            base_cost=position["entry_cost_pct"]+exit_cost_pct
            trades.append({"broker":broker,"strategy":strategy,"symbol":symbol,"signal_time":str(position["signal_time"]),
                           "entry_time":str(position["entry_time"]),"exit_signal_time":"END_OF_TEST","exit_time":str(ts),
                           "entry_price":position["entry_fill"],"exit_price":fill,"return_pct":ret,
                           "requested_fraction":position["requested_fraction"],"entry_cost_pct":position["entry_cost_pct"],
                           "exit_cost_pct":exit_cost_pct,"base_cost_pct":base_cost,"delay_penalty_pct":0.0,
                           "mfe_pct":mfe,"mae_pct":mae,"bars_held":max(0,d.index.get_loc(ts)-position["entry_i"]),"exit_reason":"end_of_test"})
    return trades


def normalize_freqtrade_trades(frame5: pd.DataFrame, start, end, symbol: str, inst: str) -> list[dict]:
    if not compat.get("freqtrade_sample"):
        return []
    start_ts=pd.Timestamp(start);end_ts=pd.Timestamp(end)
    pre=frame5[frame5.index<start_ts].tail(int(FT_STARTUP))
    body=frame5[(frame5.index>=start_ts)&(frame5.index<=end_ts)]
    need=max(0,int(FT_STARTUP)-len(pre))
    if len(body)<=need+1:return []
    inp=pd.concat([pre,body.iloc[:need],body.iloc[need:]])
    # Common broker friction: Freqtrade engine has fee+slippage parameters but no separate spread.
    try:fee=max(0.0,float(getattr(config,"OKX_TAKER_FEE_PCT",0.001)))
    except Exception:fee=0.001
    try:slip=max(0.0,float(nexus_bt.slippage_for("crypto")))+max(0.0,float(nexus_bt.spread_for("crypto")))/2.0
    except Exception:slip=0.0015
    r=ft_run_backtest(inp,initial_capital=float(args.capital),stake_pct=broker_position_pct("OKX"),fee_pct=fee,slippage_pct=slip,
                      execution_mode="observed_bars",include_equity_curve=False)
    out=[]
    for t in list(r.get("trade_rows") or []):
        entry=pd.Timestamp(t.get("entry_time"));exit_t=pd.Timestamp(t.get("exit_time"))
        # Delay penalty: adverse move to next observed open after actual entry.
        pos=frame5.index.searchsorted(entry,side="right")
        next_open=float(frame5.iloc[pos]["open"]) if pos<len(frame5) else float(t.get("entry_price") or 0)
        entry_price=float(t.get("entry_price") or 0)
        delay=max(0.0,(next_open/max(entry_price,1e-12)-1.0)*100.0)
        base_cost=(2*fee+2*slip)*100.0
        out.append({"broker":"OKX","strategy":"FREQTRADE_SAMPLE","symbol":symbol,"instrument":inst,
                    "signal_time":"NATIVE_FREQTRADE_PREVIOUS_CLOSE","entry_time":str(entry),"exit_signal_time":"NATIVE_FREQTRADE",
                    "exit_time":str(exit_t),"entry_price":entry_price,"exit_price":float(t.get("exit_price") or 0),
                    "return_pct":safe_float(t.get("return_pct"),0.0),"requested_fraction":broker_position_pct("OKX"),
                    "entry_cost_pct":base_cost/2.0,"exit_cost_pct":base_cost/2.0,"base_cost_pct":base_cost,
                    "delay_penalty_pct":delay,"mfe_pct":None,"mae_pct":None,"bars_held":int(t.get("bars") or 0),
                    "exit_reason":str(t.get("exit_reason") or "")})
    return out


def scenario_return(trade: dict, scenario: str) -> float:
    base=safe_float(trade.get("return_pct"),0.0)
    cost=max(0.0,safe_float(trade.get("base_cost_pct"),0.0))
    delay=max(0.0,safe_float(trade.get("delay_penalty_pct"),0.0))
    broker=str(trade.get("broker"))
    if scenario=="normal":return base
    if scenario=="stress":return base-cost-delay
    gap=0.25 if broker=="eToro" else 0.50
    return base-2.0*cost-2.0*delay-gap


def price_asof(frame: pd.DataFrame, ts) -> float|None:
    if frame is None or frame.empty:return None
    t=pd.Timestamp(ts)
    try:
        idx=frame.index.searchsorted(t,side="right")-1
        if idx<0:return None
        return float(frame.iloc[idx]["close"])
    except Exception:return None


def portfolio_replay(trades: list[dict], start, end, broker: str, strategy: str, market_frames: dict,
                     scenario: str="normal", starting_capital: float|None=None) -> dict:
    """Portfolio event replay with deterministic same-timestamp semantics.

    At one timestamp: (1) exits of positions already open before the timestamp,
    (2) new entries, (3) exits belonging to those newly entered trades. This
    preserves intrabar same-candle stop/exit trades while still freeing slots
    from older positions before evaluating new entries.
    """
    start_ts=pd.Timestamp(start);end_ts=pd.Timestamp(end);capital=float(starting_capital or args.capital)
    cash=capital;open_pos={};accepted=[];rejected=[];points=[];exposure_points=[];day_trade_count=defaultdict(int)
    max_pos=broker_max_positions(broker);max_day=broker_max_trades_day(broker);events=defaultdict(lambda:{"entries":[],"exits":[]})
    for tr in trades:
        try:et=pd.Timestamp(tr["entry_time"]);xt=pd.Timestamp(tr["exit_time"])
        except Exception:continue
        if et<start_ts or et>end_ts:continue
        x=min(xt,end_ts);events[et]["entries"].append(tr);events[x]["exits"].append(tr)
    event_times=sorted(events);event_i=0

    def mark_state(ts):
        total=cash;gross=0.0
        for p in open_pos.values():
            fr=market_frames.get((broker,p["trade"]["symbol"]));px=price_asof(fr,ts)
            if px is None:px=float(p["trade"].get("entry_price") or 0)
            ep=max(1e-12,float(p["trade"].get("entry_price") or 1));entry_fee=max(0.0,safe_float(p["trade"].get("entry_cost_pct"),0.0))/100.0;exit_fee=max(0.0,safe_float(p["trade"].get("exit_cost_pct"),0.0))/100.0
            mv=p["allocation"]*max(0.0,(1-entry_fee)*(px/ep)*(1-exit_fee));total+=mv;gross+=mv
        exposure=(gross/total) if total>1e-12 else 0.0
        return total,exposure

    def record(ts):
        eq,ex=mark_state(ts);points.append((ts,eq));exposure_points.append((ts,ex))

    def close_trade(ts,tr):
        nonlocal cash
        key=(tr["symbol"],str(tr.get("entry_time")));p=open_pos.pop(key,None)
        if p is None:return False
        ret=scenario_return(tr,scenario);payout=p["allocation"]*(1+ret/100.0);cash+=payout
        accepted.append({**tr,"portfolio_allocation":p["allocation"],"portfolio_pnl":payout-p["allocation"],"scenario":scenario});return True

    def open_trade(ts,tr):
        nonlocal cash
        key=(tr["symbol"],str(tr.get("entry_time")))
        if key in open_pos:return False
        equity,_=mark_state(ts);reason=None
        if len(open_pos)>=max_pos:reason="MAX_OPEN_POSITIONS"
        dkey=ts.date().isoformat()
        if day_trade_count[dkey]>=max_day:reason="MAX_TRADES_PER_DAY"
        if any(p["trade"]["symbol"]==tr["symbol"] for p in open_pos.values()):reason="SYMBOL_ALREADY_OPEN"
        frac=min(broker_position_pct(broker),max(0.0,safe_float(tr.get("requested_fraction"),broker_position_pct(broker))))
        allocation=min(cash,equity*frac)
        if allocation<max(1.0,equity*0.001):reason=reason or "INSUFFICIENT_CASH"
        if reason:
            rejected.append({"time":str(ts),"symbol":tr["symbol"],"reason":reason,"strategy":strategy,"entry_time":str(tr.get("entry_time"))});return False
        cash-=allocation;open_pos[key]={"trade":tr,"allocation":allocation};day_trade_count[dkey]+=1;return True

    daily=pd.date_range(start_ts.normalize(),end_ts.normalize(),freq="1D",tz="UTC")
    for day in daily:
        day_end=min(end_ts,day+pd.Timedelta(days=1)-pd.Timedelta(microseconds=1))
        while event_i<len(event_times) and event_times[event_i]<=day_end:
            ts=event_times[event_i];event_i+=1;bucket=events[ts];preexisting=set(open_pos.keys());deferred=[]
            # Close positions that existed before this timestamp first.
            for tr in bucket["exits"]:
                key=(tr["symbol"],str(tr.get("entry_time")))
                if key in preexisting:close_trade(ts,tr)
                else:deferred.append(tr)
            # Deterministic entry order avoids dependency on dictionary/input order.
            for tr in sorted(bucket["entries"],key=lambda x:(str(x.get("symbol")),str(x.get("signal_time")),str(x.get("entry_time")))):
                open_trade(ts,tr)
            # Same-bar exits are now applied after their entry.
            for tr in deferred:close_trade(ts,tr)
            record(ts)
        record(day_end)
    # Close still-open positions exactly at the requested period boundary.
    for key,p in list(open_pos.items()):
        tr=p["trade"];fr=market_frames.get((broker,tr["symbol"]));px=price_asof(fr,end_ts);ep=max(1e-12,float(tr.get("entry_price") or 1.0))
        if px is None:ret=0.0
        else:
            raw_ret=(float(px)/ep-1.0)*100.0;base_cost=max(0.0,safe_float(tr.get("base_cost_pct"),0.0));ret=raw_ret-base_cost
            if scenario=="stress":ret-=base_cost+max(0.0,safe_float(tr.get("delay_penalty_pct"),0.0))
            elif scenario=="extreme":ret-=2*base_cost+2*max(0.0,safe_float(tr.get("delay_penalty_pct"),0.0))+(0.25 if broker=="eToro" else 0.50)
        payout=p["allocation"]*(1+ret/100.0);cash+=payout;accepted.append({**tr,"exit_time":str(end_ts),"portfolio_allocation":p["allocation"],"portfolio_pnl":payout-p["allocation"],"scenario":scenario,"forced_period_close":True});open_pos.pop(key,None)
    points.append((end_ts,cash));exposure_points.append((end_ts,0.0))
    eq=pd.Series({pd.Timestamp(t):float(v) for t,v in points}).sort_index();eq=eq[~eq.index.duplicated(keep="last")]
    ex=pd.Series({pd.Timestamp(t):float(v) for t,v in exposure_points}).sort_index();ex=ex[~ex.index.duplicated(keep="last")]
    if eq.empty:eq=pd.Series([capital,cash],index=[start_ts,end_ts])
    daily_eq=eq.resample("1D").last().ffill();daily_ex=ex.resample("1D").last().ffill().fillna(0.0) if len(ex) else pd.Series(0.0,index=daily_eq.index)
    if daily_eq.index[0]>start_ts.normalize():daily_eq.loc[start_ts.normalize()]=capital;daily_eq=daily_eq.sort_index().ffill()
    daily_ex=daily_ex.reindex(daily_eq.index).ffill().fillna(0.0)
    return {"equity":eq,"daily_equity":daily_eq,"daily_exposure":daily_ex,"accepted":accepted,"rejected":rejected,"final_equity":float(cash),
            "return_pct":(cash/capital-1.0)*100.0,"starting_capital":capital,"avg_gross_exposure_pct":float(daily_ex.mean()*100.0) if len(daily_ex) else 0.0}

def performance_metrics(replay: dict, broker: str) -> dict:
    eq=replay["daily_equity"].astype(float)
    start=float(replay["starting_capital"]);end=float(replay["final_equity"])
    if len(eq)<2:
        returns=pd.Series(dtype=float)
    else:returns=eq.pct_change().dropna()
    running_max=eq.cummax();dd=(eq/running_max-1.0) if len(eq) else pd.Series(dtype=float)
    maxdd=float(dd.min()*100.0) if len(dd) else 0.0
    # longest drawdown duration in calendar days
    longest=0;current=0
    for x in dd:
        if x<0:current+=1;longest=max(longest,current)
        else:current=0
    days=max(1,(eq.index[-1]-eq.index[0]).days) if len(eq)>1 else 1
    years=days/365.25
    cagr=((end/start)**(1/years)-1.0)*100.0 if start>0 and end>0 and years>0 else None
    ann=252.0 if broker=="eToro" else 365.0
    mean=float(returns.mean()) if len(returns) else 0.0;std=float(returns.std(ddof=1)) if len(returns)>1 else 0.0
    downside=returns[returns<0];downstd=float(downside.std(ddof=1)) if len(downside)>1 else 0.0
    sharpe=(mean/std*math.sqrt(ann)) if std>1e-12 else None
    sortino=(mean/downstd*math.sqrt(ann)) if downstd>1e-12 else None
    calmar=(cagr/abs(maxdd)) if cagr is not None and abs(maxdd)>1e-12 else None
    pos=returns[returns>0].sum();neg=abs(returns[returns<0].sum());omega=float(pos/neg) if neg>1e-12 else None
    accepted=replay.get("accepted") or []
    pnls=[safe_float(x.get("portfolio_pnl"),0.0) for x in accepted]
    gp=sum(x for x in pnls if x>0);gl=abs(sum(x for x in pnls if x<0))
    pf=(gp/gl) if gl>1e-12 else (None if gp<=0 else float("inf"))
    monthly=eq.resample("ME").last().pct_change().dropna() if len(eq)>1 else pd.Series(dtype=float)
    positive_months=100.0*float((monthly>0).sum())/len(monthly) if len(monthly) else None
    gross_positive=gp
    sorted_pos=sorted([x for x in pnls if x>0],reverse=True)
    top1=(100.0*sum(sorted_pos[:1])/gross_positive) if gross_positive>0 else None
    top5=(100.0*sum(sorted_pos[:5])/gross_positive) if gross_positive>0 else None
    top10n=max(1,math.ceil(len(sorted_pos)*0.10)) if sorted_pos else 0
    top10pct=(100.0*sum(sorted_pos[:top10n])/gross_positive) if gross_positive>0 and top10n else None
    return {"return_pct":(end/start-1.0)*100.0 if start else 0.0,"cagr_pct":cagr,"max_drawdown_pct":maxdd,
            "max_drawdown_days":longest,"sharpe":sharpe,"sortino":sortino,"calmar":calmar,"omega":omega,
            "profit_factor":pf,"trades":len(accepted),"rejected_entries":len(replay.get("rejected") or []),
            "win_rate_pct":100.0*sum(1 for x in pnls if x>0)/len(pnls) if pnls else None,
            "expectancy_currency":sum(pnls)/len(pnls) if pnls else None,"positive_month_pct":positive_months,
            "avg_gross_exposure_pct":safe_float(replay.get("avg_gross_exposure_pct"),float(replay.get("daily_exposure",pd.Series(dtype=float)).mean()*100.0) if len(replay.get("daily_exposure",[])) else 0.0),
            "top1_profit_concentration_pct":top1,"top5_profit_concentration_pct":top5,"top10pct_profit_concentration_pct":top10pct}


def moving_block_bootstrap(daily_equity: pd.Series, broker: str, iterations: int, block_days: int, seed: int=20260915) -> dict:
    r=daily_equity.astype(float).pct_change().dropna().values
    if len(r)<20 or iterations<=0:return {"iterations":0}
    rng=np.random.default_rng(seed);block=max(1,min(int(block_days),len(r)))
    totals=[];drawdowns=[]
    for _ in range(int(iterations)):
        sample=[]
        while len(sample)<len(r):
            s=int(rng.integers(0,max(1,len(r)-block+1)));sample.extend(r[s:s+block].tolist())
        sample=np.asarray(sample[:len(r)],dtype=float)
        eq=np.cumprod(1.0+sample)
        totals.append((eq[-1]-1.0)*100.0)
        peaks=np.maximum.accumulate(eq);drawdowns.append(float(np.min(eq/peaks-1.0)*100.0))
    return {"iterations":int(iterations),"block_days":block,"median_return_pct":float(np.percentile(totals,50)),
            "p25_return_pct":float(np.percentile(totals,25)),"p05_return_pct":float(np.percentile(totals,5)),
            "negative_probability_pct":100.0*sum(1 for x in totals if x<0)/len(totals),
            "median_max_drawdown_pct":float(np.percentile(drawdowns,50)),"p05_max_drawdown_pct":float(np.percentile(drawdowns,5))}


def signal_digest_frame(d: pd.DataFrame) -> str:
    cols=[c for c in ("enter_signal","exit_signal") if c in d]
    if not cols:return ""
    return hashlib.sha256(d[cols].astype(int).to_csv().encode("utf-8")).hexdigest()


def _derived_numeric_columns(df):
    raw={"open","high","low","close","volume","enter_signal","exit_signal","enter_long","exit_long"}
    return [c for c in df.columns if c not in raw and pd.api.types.is_numeric_dtype(df[c])]

def _relative_diff(a,b):
    try:
        a=float(a);b=float(b)
        if not (math.isfinite(a) and math.isfinite(b)):return 0.0 if (pd.isna(a) and pd.isna(b)) else float("inf")
        return abs(a-b)/max(1.0,abs(a),abs(b))
    except Exception:return float("inf")

def lookahead_check(builder, frame: pd.DataFrame, *, min_history: int=60, samples: int=24) -> dict:
    if args.keine_validierung:return {"status":"UEBERSPRUNGEN"}
    if frame is None or len(frame)<min_history+5:return {"status":"NICHT_PRUEFBAR","detail":"zu wenig Historie"}
    # Bound cost for 5m datasets while preserving a long causal prefix.
    work=frame.tail(min(len(frame),20000)).copy()
    try:full=builder(work)
    except Exception as exc:return {"status":"FEHLER","detail":f"full:{type(exc).__name__}:{exc}"}
    signal_col_entry="enter_signal" if "enter_signal" in full else ("enter_long" if "enter_long" in full else None)
    signal_col_exit="exit_signal" if "exit_signal" in full else ("exit_long" if "exit_long" in full else None)
    start_i=min(min_history,max(1,len(work)-2));candidate=set()
    if signal_col_entry or signal_col_exit:
        mask=pd.Series(False,index=full.index)
        if signal_col_entry:mask=mask|full[signal_col_entry].fillna(False).astype(bool)
        if signal_col_exit:mask=mask|full[signal_col_exit].fillna(False).astype(bool)
        sig_idx=[work.index.get_loc(x) for x in full.index[mask] if x in work.index and work.index.get_loc(x)>=start_i]
        if sig_idx:
            step=max(1,len(sig_idx)//max(1,(3 if args.selbsttest else samples//2)));candidate.update(sig_idx[::step][:max(3,samples//2)])
    even=np.linspace(start_i,len(work)-2,num=min((3 if args.selbsttest else samples),max(1,len(work)-start_i-1)),dtype=int)
    candidate.update(int(x) for x in even);points=sorted(candidate)[:(8 if args.selbsttest else 40)]
    mismatches=[];indicator_cols=_derived_numeric_columns(full)[:30]
    max_indicator_diff=0.0
    for i in points:
        ts=work.index[i]
        try:
            prefix=builder(work.iloc[:i+1]);frow=full.loc[ts];prow=prefix.iloc[-1]
            fe=bool(frow.get(signal_col_entry,False)) if signal_col_entry else False;pe=bool(prow.get(signal_col_entry,False)) if signal_col_entry else False
            fx=bool(frow.get(signal_col_exit,False)) if signal_col_exit else False;px=bool(prow.get(signal_col_exit,False)) if signal_col_exit else False
            if (fe,fx)!=(pe,px):mismatches.append({"time":str(ts),"kind":"SIGNAL","full":[fe,fx],"prefix":[pe,px]});continue
            for c in indicator_cols:
                if c not in prefix.columns:continue
                diff=_relative_diff(frow.get(c),prow.get(c));max_indicator_diff=max(max_indicator_diff,diff if math.isfinite(diff) else 1e9)
                if diff>1e-7:
                    mismatches.append({"time":str(ts),"kind":"INDIKATOR","column":c,"relative_diff":diff,"full":safe_float(frow.get(c),None),"prefix":safe_float(prow.get(c),None)});break
        except Exception as exc:mismatches.append({"time":str(ts),"kind":"FEHLER","error":f"{type(exc).__name__}:{exc}"})
    return {"status":"BESTANDEN" if not mismatches else "NICHT_BESTANDEN","samples":len(points),"indicator_columns":indicator_cols,"max_indicator_relative_diff":max_indicator_diff,"mismatches":mismatches[:20]}

def recursive_signal_check(builder, frame: pd.DataFrame, lengths: list[int]) -> dict:
    if args.keine_validierung:return {"status":"UEBERSPRUNGEN"}
    vals=[];snapshots=[]
    for n in lengths:
        if len(frame)<n:continue
        try:
            d=builder(frame.tail(n));last=d.iloc[-1];entry_col="enter_signal" if "enter_signal" in d else ("enter_long" if "enter_long" in d else None);exit_col="exit_signal" if "exit_signal" in d else ("exit_long" if "exit_long" in d else None)
            indicators={c:safe_float(last.get(c),None) for c in _derived_numeric_columns(d)[:30]}
            row={"bars":n,"entry":bool(last.get(entry_col,False)) if entry_col else False,"exit":bool(last.get(exit_col,False)) if exit_col else False,"indicators":indicators};vals.append(row);snapshots.append(row)
        except Exception as exc:vals.append({"bars":n,"error":f"{type(exc).__name__}:{exc}"})
    if len(snapshots)<2:return {"status":"NICHT_PRUEFBAR","runs":vals}
    signal_pairs={(x["entry"],x["exit"]) for x in snapshots};maxdiff=0.0;diffs=[];base=snapshots[-1]
    for x in snapshots[:-1]:
        for c,bv in base["indicators"].items():
            if c not in x["indicators"]:continue
            av=x["indicators"][c]
            if av is None or bv is None:continue
            d=_relative_diff(av,bv);maxdiff=max(maxdiff,d if math.isfinite(d) else 1e9)
            if d>0.005:diffs.append({"bars":x["bars"],"column":c,"relative_diff":d})
    if len(signal_pairs)>1:status="SIGNAL_ABHAENGIG"
    elif diffs:status="SIGNAL_STABIL_INDIKATOREN_ABWEICHEND"
    else:status="STABIL"
    return {"status":status,"runs":vals,"max_indicator_relative_diff":maxdiff,"material_indicator_diffs":diffs[:20]}

def strategy_validation(strategy: str, broker: str, frames: list[pd.DataFrame]) -> dict:
    min_rows=50 if strategy in ({"TURTLE_DONCHIAN","IBS_MEAN_REVERSION"}|set(V6_STRATEGY_BUILDERS)) else 300
    eligible=[f for f in frames if f is not None and len(f)>min_rows]
    if not eligible:return {"lookahead":{"status":"NICHT_PRUEFBAR","detail":f"weniger als {min_rows} geeignete Bars"},"recursive":{"status":"NICHT_PRUEFBAR"}}
    if strategy=="NEXUS_STANDARD":mode=str(getattr(config,"ENTRY_MODE","trend"));builder=lambda x:nexus_signal_frame(x,mode);lengths=[200,500,1000,2000,5000]
    elif strategy=="NEXUS_CROSSOVER_RESEARCH":builder=lambda x:nexus_signal_frame(x,"crossover");lengths=[200,500,1000,2000,5000]
    elif strategy=="FREQTRADE_SAMPLE" and ft_signal_frame:builder=lambda x:ft_signal_frame(x).rename(columns={"enter_long":"enter_signal","exit_long":"exit_signal"});lengths=[200,500,1000,2000,5000]
    elif strategy=="TURTLE_DONCHIAN":builder=lambda x:turtle_signals(x);lengths=[60,100,200,400]
    elif strategy=="IBS_MEAN_REVERSION":builder=lambda x:ibs_signals(x);lengths=[60,100,200,400]
    elif strategy in V6_STRATEGY_BUILDERS:builder=V6_STRATEGY_BUILDERS[strategy][0];lengths=[60,100,260,400]
    else:return {"lookahead":{"status":"NICHT_ANWENDBAR"},"recursive":{"status":"NICHT_ANWENDBAR"}}
    # V4 validated one representative instrument. V5 samples up to six across
    # the actual universe and aggregates both signal and indicator evidence.
    if len(eligible)>6:
        idx=np.linspace(0,len(eligible)-1,num=6,dtype=int);chosen=[eligible[int(i)] for i in sorted(set(idx))]
    else:chosen=eligible
    la=[];rec=[]
    for f in chosen:
        la.append(lookahead_check(builder,f,min_history=min(lengths[0],max(30,min(len(f)//4,1000)))))
        rec.append(recursive_signal_check(builder,f,lengths))
    la_status="NICHT_BESTANDEN" if any(x.get("status") in {"NICHT_BESTANDEN","FEHLER"} for x in la) else ("BESTANDEN" if all(x.get("status")=="BESTANDEN" for x in la) else "NICHT_PRUEFBAR")
    rec_states=[x.get("status") for x in rec];rec_status="SIGNAL_ABHAENGIG" if "SIGNAL_ABHAENGIG" in rec_states else ("SIGNAL_STABIL_INDIKATOREN_ABWEICHEND" if "SIGNAL_STABIL_INDIKATOREN_ABWEICHEND" in rec_states else ("STABIL" if rec_states and all(x=="STABIL" for x in rec_states) else "NICHT_PRUEFBAR"))
    return {"lookahead":{"status":la_status,"frames_tested":len(chosen),"samples":sum(int(x.get("samples") or 0) for x in la),"details":la},
            "recursive":{"status":rec_status,"frames_tested":len(chosen),"details":rec}}

def okx_history_daily(inst_id: str, start, end, client):
    """Small read-only daily history path, independent of the 5m cache."""
    path=cache_csv_path("okx_daily",inst_id,"1d")
    cached=read_csv_cache(path);ws=pd.Timestamp(start);we=pd.Timestamp(end)
    if len(cached) and cached.index.min()<=ws+pd.Timedelta(days=2) and cached.index.max()>=we-pd.Timedelta(days=2):
        return cached[(cached.index>=ws)&(cached.index<=we)],"CACHE",0
    min_pause=1.0/max(0.2,float(args.okx_rps));pages=[];reqs=0;last=None
    cursor=int(we.timestamp()*1000);bar_used=None
    for bar in ("1Dutc","1D"):
        pages=[];last=None;cursor=int(we.timestamp()*1000);reqs_local=0
        try:
            while True:
                t0=time.monotonic();page=client.historical_candles(inst_id,bar=bar,limit=100,end_ms=cursor,nur_abgeschlossen=True)
                reqs_local+=1;page=normalize_frame(page)
                if page.empty:break
                oldest=page.index.min()
                if last is not None and oldest>=last:break
                last=oldest;pages.append(page);cursor=int(oldest.timestamp()*1000)-1
                if oldest<=ws:break
                elapsed=time.monotonic()-t0
                if elapsed<min_pause:time.sleep(min_pause-elapsed)
                if reqs_local>100:raise RuntimeError("Daily-History Sicherheitsgrenze")
            if pages:
                reqs+=reqs_local;bar_used=bar;break
        except Exception:
            continue
    if not pages and cached.empty:raise RuntimeError(f"OKX liefert keine Tageshistorie fuer {inst_id}")
    merged=normalize_frame(pd.concat([cached,*pages])) if pages and len(cached) else (normalize_frame(pd.concat(pages)) if pages else cached)
    write_csv_cache(path,merged);write_cache_meta(path,{"source_family":"OKX_PUBLIC","bar":bar_used,"updated_at_utc":datetime.now(timezone.utc).isoformat()})
    return merged[(merged.index>=ws)&(merged.index<=we)],("OKX_PUBLIC+CACHE" if len(cached) else "OKX_PUBLIC"),reqs


def _series_on_dates(frame: pd.DataFrame, col="close") -> pd.Series:
    if frame is None or frame.empty:return pd.Series(dtype=float)
    s=frame[col].astype(float).copy();s.index=pd.DatetimeIndex(s.index).tz_convert("UTC").normalize()
    return s.groupby(level=0).last().sort_index()


def benchmark_equal_weight(daily_map: dict[str,pd.DataFrame], start, end, *, asset_type="stock") -> dict:
    start_ts=pd.Timestamp(start).normalize();end_ts=pd.Timestamp(end).normalize();series=[];members=[];excluded=[]
    for sym,frame in daily_map.items():
        q=period_quality(frame,start,end,asset_type,"1d")
        if not q.get("ranking_eligible"):
            excluded.append({"symbol":sym,"reason":q.get("quality_reason")});continue
        ss=_series_on_dates(frame,"close");ss=ss[(ss.index>=start_ts)&(ss.index<=end_ts)].dropna()
        if len(ss)<2:continue
        first=float(ss.iloc[0]);series.append((ss/first).rename(sym));members.append(sym)
    if not series:return {"return_pct":None,"equity":pd.Series(dtype=float),"members":[],"excluded":excluded}
    panel=pd.concat(series,axis=1).sort_index().ffill();eq=panel.mean(axis=1,skipna=False).dropna()*float(args.capital)
    if eq.empty:return {"return_pct":None,"equity":pd.Series(dtype=float),"members":members,"excluded":excluded}
    return {"return_pct":(float(eq.iloc[-1])/float(args.capital)-1.0)*100.0,"equity":eq,"members":members,"excluded":excluded}

def benchmark_btc(daily_map: dict[str,pd.DataFrame], start, end) -> dict:
    frame=daily_map.get("BTC")
    if frame is None:return {"return_pct":None,"equity":pd.Series(dtype=float)}
    q=period_quality(frame,start,end,"crypto","1d")
    if not q.get("ranking_eligible"):return {"return_pct":None,"equity":pd.Series(dtype=float),"quality":q}
    ss=_series_on_dates(frame,"close");ss=ss[(ss.index>=pd.Timestamp(start).normalize())&(ss.index<=pd.Timestamp(end).normalize())].dropna()
    if len(ss)<2:return {"return_pct":None,"equity":pd.Series(dtype=float)}
    eq=ss/float(ss.iloc[0])*float(args.capital)
    return {"return_pct":(float(eq.iloc[-1])/float(args.capital)-1.0)*100.0,"equity":eq,"quality":q}

def exposure_matched_benchmark(benchmark_eq: pd.Series, daily_exposure: pd.Series, capital: float) -> dict:
    """Apply the strategy's daily gross exposure to benchmark daily returns; cash earns 0%."""
    if benchmark_eq is None or len(benchmark_eq)<2 or daily_exposure is None or len(daily_exposure)<2:
        return {"return_pct":None,"equity":pd.Series(dtype=float)}
    b=benchmark_eq.astype(float).pct_change().fillna(0.0);e=daily_exposure.astype(float).clip(lower=0.0,upper=1.0)
    idx=b.index.union(e.index).sort_values();b=b.reindex(idx).fillna(0.0);e=e.reindex(idx).ffill().fillna(0.0)
    r=b*e.shift(1).fillna(e);eq=(1.0+r).cumprod()*float(capital)
    return {"return_pct":(float(eq.iloc[-1])/float(capital)-1.0)*100.0,"equity":eq,"avg_exposure_pct":float(e.mean()*100.0)}

def regimes_from_benchmark(eq: pd.Series) -> pd.Series:
    if eq is None or len(eq)<30:return pd.Series(dtype=object)
    s=eq.astype(float).sort_index();sma=s.rolling(100,min_periods=60).mean();mom=s.pct_change(20)
    vol=s.pct_change().rolling(20,min_periods=15).std();vol_threshold=vol.expanding(min_periods=60).quantile(0.75)
    labels=[]
    for i in range(len(s)):
        if pd.isna(sma.iloc[i]) or pd.isna(mom.iloc[i]):base="UNBEKANNT"
        elif s.iloc[i]>sma.iloc[i] and mom.iloc[i]>0:base="BULL"
        elif s.iloc[i]<sma.iloc[i] and mom.iloc[i]<0:base="BEAR"
        else:base="SEITWAERTS"
        if pd.notna(vol.iloc[i]) and pd.notna(vol_threshold.iloc[i]) and vol.iloc[i]>vol_threshold.iloc[i]:base += "_HOHE_VOL"
        labels.append(base)
    return pd.Series(labels,index=s.index)


def trade_regime_breakdown(accepted: list[dict], regimes: pd.Series) -> list[dict]:
    buckets=defaultdict(lambda:{"trades":0,"pnl":0.0,"wins":0})
    if regimes is None or regimes.empty:return []
    for t in accepted:
        try:d=pd.Timestamp(t["entry_time"]).tz_convert("UTC").normalize()
        except Exception:continue
        idx=regimes.index.searchsorted(d,side="right")-1
        reg=str(regimes.iloc[idx]) if idx>=0 else "UNBEKANNT"
        pnl=safe_float(t.get("portfolio_pnl"),0.0);b=buckets[reg];b["trades"]+=1;b["pnl"]+=pnl;b["wins"]+=int(pnl>0)
    out=[]
    for reg,b in sorted(buckets.items()):
        out.append({"regime":reg,"trades":b["trades"],"pnl":b["pnl"],"win_rate_pct":100*b["wins"]/b["trades"] if b["trades"] else None})
    return out


def momentum_score(close: pd.Series, date, horizons=(21,63,126)) -> float|None:
    s=close.loc[:date].dropna()
    if len(s)<max(horizons)+64:return None
    vol=s.pct_change().tail(63).std()*math.sqrt(365.0)
    if not math.isfinite(vol) or vol<=1e-12:return None
    vals=[]
    for h in horizons:
        if len(s)<=h:return None
        vals.append((float(s.iloc[-1]/s.iloc[-1-h]-1.0))/vol)
    return float(np.mean(vals))


def simulate_crypto_momentum(daily_map: dict[str,pd.DataFrame], start, end, *, scenario="normal",
                             horizons=(21,63,126), sma_days=200, max_assets=3, liquidity_window=90,
                             history_min_days=365, top_n=10) -> dict:
    """Reference implementation of the publicly described rule skeleton.

    The accessible source does not publish its exact weighting of the three
    volatility-adjusted horizons. V5 therefore freezes and hashes the explicit
    interpretation documented in STRATEGY_SPECS; it never presents this as an
    exact reproduction of the author's private backtest.
    """
    if "BTC" not in daily_map:return {"error":"BTC-Referenz fehlt"}
    closes={s:_series_on_dates(f,"close") for s,f in daily_map.items()}
    opens={s:_series_on_dates(f,"open") for s,f in daily_map.items()}
    vols={s:_series_on_dates(f,"volume") for s,f in daily_map.items()}
    union=sorted(set().union(*[set(x.index) for x in closes.values() if len(x)]))
    if not union:return {"error":"keine Tagesdaten"}
    dates=pd.DatetimeIndex(union)
    warm_start=min(dates);start_d=pd.Timestamp(start).normalize();end_d=pd.Timestamp(end).normalize()
    dates=dates[(dates>=warm_start)&(dates<=end_d)]
    cash=float(args.capital);holdings={};basis={};pending=None;points=[];exposure_points=[];closed=[];turnover_rows=[]
    cap=broker_position_pct("OKX")
    # One-side friction from the NEXUS broker assumptions. Stress/extreme multiplies it.
    _,_,one_side_pct,_,_=estimate_friction("crypto","EUR",100.0,"BUY",1000.0,1.0)
    mult={"normal":1.0,"stress":2.0,"extreme":3.0}.get(scenario,1.0)
    one_side=(one_side_pct/100.0)*mult + (0.0 if scenario!="extreme" else 0.0025)

    def px(sym,date,kind="close"):
        src=opens[sym] if kind=="open" else closes[sym]
        x=src.loc[:date]
        return float(x.iloc[-1]) if len(x) else None
    def equity(date,kind="close"):
        total=cash
        for sym,units in holdings.items():
            p=px(sym,date,kind)
            if p is not None:total+=units*p
        return total
    def exposure(date,kind="close"):
        gross=0.0
        for sym,units in holdings.items():
            p=px(sym,date,kind)
            if p is not None:gross+=units*p
        eq=equity(date,kind)
        return gross/eq if eq>1e-12 else 0.0

    for date in dates:
        if date < start_d:continue
        # Execute prior weekly target at today's open.
        if pending is not None:
            eq_open=equity(date,"open")
            targets=pending["targets"]
            current_values={s:holdings.get(s,0.0)*(px(s,date,"open") or 0.0) for s in set(holdings)|set(targets)}
            target_values={s:eq_open*float(w) for s,w in targets.items()}
            # sell first
            for sym in sorted(set(current_values)-set(targets) | {s for s in current_values if current_values.get(s,0)>target_values.get(s,0)}):
                p=px(sym,date,"open")
                if p is None or p<=0:continue
                cur=current_values.get(sym,0.0);tar=target_values.get(sym,0.0);sell=max(0.0,cur-tar)
                if sell<=1e-8:continue
                units=min(holdings.get(sym,0.0),sell/p);gross=units*p;cost=gross*one_side;cash+=gross-cost;holdings[sym]=holdings.get(sym,0.0)-units
                avg=basis.get(sym,p);pnl=(p-avg)*units-cost
                turnover_rows.append({"time":str(date),"symbol":sym,"side":"SELL","notional":gross,"cost":cost})
                if holdings.get(sym,0.0)<=1e-12:
                    closed.append({"broker":"OKX","strategy":"CRYPTO_MOMENTUM_REFERENCE","symbol":sym,"entry_time":str(pending.get("signal_time")),"exit_time":str(date),"portfolio_pnl":pnl,"return_pct":pnl/max(gross,1e-12)*100.0})
                    holdings.pop(sym,None);basis.pop(sym,None)
            # buy to target
            for sym,tar in target_values.items():
                p=px(sym,date,"open")
                if p is None or p<=0:continue
                cur=holdings.get(sym,0.0)*p;buy=max(0.0,tar-cur)
                if buy<=1e-8:continue
                cost=buy*one_side;spend=min(cash,buy+cost)
                if spend<=cost:continue
                actual=spend-cost;units=actual/p;old_units=holdings.get(sym,0.0);old_basis=basis.get(sym,p)
                holdings[sym]=old_units+units;basis[sym]=(old_basis*old_units+p*units)/max(holdings[sym],1e-12);cash-=spend
                turnover_rows.append({"time":str(date),"symbol":sym,"side":"BUY","notional":actual,"cost":cost})
            pending=None
        points.append((date,equity(date,"close")));exposure_points.append((date,exposure(date,"close")))
        # Sunday close creates next-open rebalance target.
        if date.weekday()!=6:continue
        btc=closes["BTC"].loc[:date].dropna()
        if len(btc)<sma_days or float(btc.iloc[-1])<=float(btc.tail(sma_days).mean()):
            pending={"signal_time":date,"targets":{}};continue
        liquid=[]
        for sym,s in closes.items():
            hist=s.loc[:date].dropna()
            if len(hist)<history_min_days:continue
            v=vols.get(sym,pd.Series(dtype=float)).loc[:date]
            # Approximate quote volume from base volume * close; causal trailing 90d.
            joined=pd.concat([hist.rename("c"),v.rename("v")],axis=1).dropna().tail(liquidity_window)
            if len(joined)<min(30,liquidity_window):continue
            qv=float((joined["c"]*joined["v"]).mean());liquid.append((qv,sym))
        eligible=[sym for _,sym in sorted(liquid,reverse=True)[:top_n]]
        scored=[]
        for sym in eligible:
            s=closes[sym].loc[:date].dropna()
            if len(s)<sma_days or float(s.iloc[-1])<=float(s.tail(sma_days).mean()):continue
            sc=momentum_score(s,date,horizons)
            if sc is None or sc<=0:continue
            vol20=s.pct_change().tail(20).std()*math.sqrt(365.0)
            if not math.isfinite(vol20) or vol20<=1e-12:continue
            scored.append((sc,sym,vol20))
        selected=sorted(scored,reverse=True)[:max_assets]
        if not selected:pending={"signal_time":date,"targets":{}};continue
        inv={sym:1.0/vol for _,sym,vol in selected};den=sum(inv.values());targets={sym:min(cap,inv[sym]/den) for sym in inv}
        pending={"signal_time":date,"targets":targets}
    if not points:return {"error":"keine Portfolio-Punkte im Zeitraum"}
    eq=pd.Series({pd.Timestamp(t):float(v) for t,v in points}).sort_index();eq=eq[~eq.index.duplicated(keep="last")]
    # Liquidate at period end with one-side friction for honest comparability.
    last=eq.index[-1]
    final_eq=equity(last,"close")
    liquidation_cost=0.0
    for sym,u in holdings.items():
        p=px(sym,last,"close") or 0;liquidation_cost+=u*p*one_side
    final_eq-=liquidation_cost;eq.iloc[-1]=final_eq
    daily=eq.resample("1D").last().ffill()
    ex=pd.Series({pd.Timestamp(t):float(v) for t,v in exposure_points}).sort_index() if exposure_points else pd.Series(dtype=float)
    daily_ex=ex.resample("1D").last().ffill().reindex(daily.index).ffill().fillna(0.0) if len(ex) else pd.Series(0.0,index=daily.index)
    replay={"equity":eq,"daily_equity":daily,"daily_exposure":daily_ex,"accepted":closed,"rejected":[],"final_equity":float(final_eq),"return_pct":(final_eq/float(args.capital)-1)*100,"starting_capital":float(args.capital),"avg_gross_exposure_pct":float(daily_ex.mean()*100.0) if len(daily_ex) else 0.0}
    return {"replay":replay,"turnover":turnover_rows,"cash_fraction_end":cash/max(final_eq,1e-12),"interpretation":"mean(vol-adjusted returns 21/63/126); exact source weighting not public"}


def quarter_windows(start, end):
    s=pd.Timestamp(start);e=pd.Timestamp(end);out=[];cur=pd.Timestamp(year=s.year,month=((s.month-1)//3)*3+1,day=1,tz="UTC")
    while cur<=e:
        nxt=cur+pd.offsets.QuarterBegin(startingMonth=1)
        qend=min(e,nxt-pd.Timedelta(seconds=1));qstart=max(s,cur)
        out.append((f"{qstart.year}Q{((qstart.month-1)//3)+1}",qstart.to_pydatetime(),qend.to_pydatetime()));cur=nxt
    return out


def combined_period():
    return ("2025_2026_GESAMT",periods[0][1],periods[-1][2])


def strategy_window_data_status(broker,strategy,start,end):
    members=stocks if broker=="eToro" else cryptos;valid=[];invalid=[]
    for m in members:
        sym=str(m.get("symbol") or "").upper()
        if strategy in ({"TURTLE_DONCHIAN","IBS_MEAN_REVERSION","CRYPTO_MOMENTUM_REFERENCE"}|set(V6_STRATEGY_BUILDERS)):
            fr=daily_frames.get(broker,{}).get(sym);tf="1d"
        else:
            fr=market_frames.get((broker,sym));tf="1h" if broker=="eToro" else "5m"
        if fr is None or len(fr)==0:
            invalid.append({"symbol":sym,"reason":"keine Daten"});continue
        q=apply_symbol_quality_overlay(broker,sym,period_quality(fr,start,end,"stock" if broker=="eToro" else "crypto",tf))
        if q.get("ranking_eligible"):valid.append(sym)
        else:invalid.append({"symbol":sym,"reason":q.get("quality_reason")})
    expected=len(members);coverage=100.0*len(valid)/expected if expected else 0.0;minimum=max(1,min(3,expected))
    usable=len(valid)>=minimum and coverage>=50.0
    return {"usable":usable,"valid_symbols":valid,"invalid":invalid,"valid_count":len(valid),"expected_count":expected,"coverage_pct":coverage,
            "status":"GETESTET" if usable else "NICHT_PRUEFBAR"}
# ---------- V5 main data/run -------------------------------------------------
results_by_symbol=[];errors=[];warnings=[];quality_blocks=[];test_status=[];data_manifest=[];corporate_checks=[];all_trade_rows=[]
trade_sets=defaultdict(list);market_frames={};daily_frames={"eToro":{},"OKX":{}};inst_by_symbol={}
validation_inputs=defaultdict(list)

ALL_PERIODS=list(periods)+[combined_period()]

def status_key(broker,strategy,period,symbol):return (str(broker),str(strategy),str(period),str(symbol))
def set_status(broker,strategy,period,symbol,status,detail=""):
    key=status_key(broker,strategy,period,symbol)
    for x in test_status:
        if status_key(x["broker"],x["strategy"],x["period"],x["symbol"])==key:
            x["status"]=status;x["detail"]=str(detail)[:1200];return
    test_status.append({"broker":broker,"strategy":strategy,"period":period,"symbol":symbol,"status":status,"detail":str(detail)[:1200]})

def expect(broker,strategy,period,symbol):set_status(broker,strategy,period,symbol,"AUSSTEHEND","")
def err(broker,strategy,period,symbol,exc):
    msg=f"{type(exc).__name__}: {exc}";errors.append({"broker":broker,"strategy":strategy,"period":period,"symbol":symbol,"error":msg});set_status(broker,strategy,period,symbol,"NICHT_GETESTET",msg);log(f"  FEHLER {broker}/{strategy}/{period}/{symbol}: {msg}")

def summarize_symbol_trades(broker,strategy,period,symbol,trades,source,quality=None):
    rr=[safe_float(t.get("return_pct"),0.0) for t in trades];wins=[x for x in rr if x>0];loss=[x for x in rr if x<0]
    gp=sum(wins);gl=abs(sum(loss));pf=(gp/gl if gl>1e-12 else (None if gp<=0 else float("inf")))
    row={"broker":broker,"strategy":strategy,"period":period,"symbol":symbol,"trades":len(trades),
         "avg_trade_return_pct":float(np.mean(rr)) if rr else None,"median_trade_return_pct":float(np.median(rr)) if rr else None,
         "win_rate_pct":100.0*len(wins)/len(rr) if rr else None,"profit_factor_trade_returns":pf,
         "best_trade_pct":max(rr) if rr else None,"worst_trade_pct":min(rr) if rr else None,"data_source":source}
    if quality:row.update(quality)
    results_by_symbol.append(row)
    eligible=bool((quality or {}).get("ranking_eligible",True))
    if eligible:
        set_status(broker,strategy,period,symbol,"GETESTET","OK" if trades else "0 Trades bei gueltiger Historie")
    else:
        reason=str((quality or {}).get("quality_reason") or (quality or {}).get("quality") or "Datenqualitaet unzureichend")
        set_status(broker,strategy,period,symbol,"QUALITAETSSPERRE",reason)
        quality_blocks.append({"broker":broker,"strategy":strategy,"period":period,"symbol":symbol,"reason":reason,
                               "quality":(quality or {}).get("quality"),"coverage_pct":(quality or {}).get("calendar_coverage_pct"),
                               "data_from":(quality or {}).get("data_from"),"data_to":(quality or {}).get("data_to")})
    return row

def quality_allows_portfolio(q):
    return bool(q and q.get("ranking_eligible"))

def apply_symbol_quality_overlay(broker,symbol,q):
    q=dict(q or {});reasons=[str(q.get("quality_reason") or "").strip()] if q.get("quality_reason") else []
    if broker=="eToro":
        cc=next((x for x in corporate_checks if str(x.get("symbol"))==str(symbol)),None)
        if cc and str(cc.get("status","")).startswith("PRUEFEN"):
            q["ranking_eligible"]=False;q["quality"]="QUALITAETSSPERRE";reasons.append("Corporate-Action/Preisbasis nicht eindeutig")
        sm=next((x for x in reversed(symbol_mapping_rows) if str(x.get("etoro_symbol"))==str(symbol)),None)
        if sm and str(sm.get("identity_status"))=="ABGELEHNT":
            q["ranking_eligible"]=False;q["quality"]="QUALITAETSSPERRE";reasons.append("Provider-Instrumentidentitaet abgelehnt")
    q["quality_reason"]="; ".join(x for x in reasons if x) or "vollstaendige Zeitabdeckung"
    return q

# Expected matrix is fixed before data access, so missing cases stay visible.
for m in stocks:
    sym=str(m.get("symbol") or "").upper()
    for label,_,_ in ALL_PERIODS:
        for strategy in ("NEXUS_STANDARD","TURTLE_DONCHIAN","IBS_MEAN_REVERSION")+V6_ETORO:
            expect("eToro",strategy,label,sym)
        if args.mit_crossover_forschung:expect("eToro","NEXUS_CROSSOVER_RESEARCH",label,sym)
for m in cryptos:
    sym=str(m.get("symbol") or "").upper()
    for label,_,_ in ALL_PERIODS:
        for strategy in ("NEXUS_STANDARD","FREQTRADE_SAMPLE","TURTLE_DONCHIAN")+V6_OKX:
            expect("OKX",strategy,label,sym)
        if args.mit_crossover_forschung:expect("OKX","NEXUS_CROSSOVER_RESEARCH",label,sym)
for label,_,_ in ALL_PERIODS:
    expect("OKX","CRYPTO_MOMENTUM_REFERENCE",label,"PORTFOLIO")

log("="*86)
log("NEXUS UNIVERSUM-BACKTEST V6 - READ ONLY / QUALITAETSGEBUNDENE PORTFOLIO-VALIDIERUNG")
log("="*86)
log(f"NEXUS: {VERSION['version']} | Build: {(VERSION.get('build') or 'unbekannt').splitlines()[0]}")
log(f"Pfad:  {ROOT}")
if args.selbsttest:
    log("Universum: synthetischer Selbsttest; die reale NEXUS-Auswahl wird nicht gehandelt oder ausgewertet.")
else:
    log(f"eToro Universum: {len(stocks)} | OKX Universum: {len(cryptos)} | Auswahl: {args.umfang}")
log("Hauptstrategien: NEXUS Standard | Freqtrade Sample (OKX) | Turtle/Donchian | IBS Mean Reversion (eToro) | Crypto Momentum Rotation (OKX)")
log("V6 zusaetzlich: RSI-2 | 52-Wochen-Hoch | Goldenes Kreuz (eToro) · Zeitreihen-Momentum | Keltner-Ausbruch | MACD-Trend (OKX)")
if args.mit_crossover_forschung:log("Forschung zusaetzlich: NEXUS Crossover (nicht Teil des produktiven Hauptmodus)")
log("Bewertung: Einzel-Signaltests + gemeinsames Broker-Portfolio + Benchmarks + Validierung + Stress + Walk-Forward + Bootstrap")
log("")

if args.nur_plan:
    first=periods[0][1];last=periods[-1][2];days=max(1,(last-first).days)
    approx_5m=math.ceil((days+5)*288/100)
    plan={"tool_version":TOOL_VERSION,"nexus":VERSION,"stocks":[x.get("symbol") for x in stocks],"cryptos":[x.get("symbol") for x in cryptos],
          "periods":[{"label":a,"start":str(b),"end":str(c)} for a,b,c in ALL_PERIODS],"strategies":STRATEGY_SPECS,
          "estimated_okx_5m_pages_per_selected_coin":approx_5m,"daily_momentum_history_from":args.warmup_start,
          "monte_carlo_iterations":args.monte_carlo}
    (RUN_DIR/"plan.json").write_text(json.dumps(plan,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    log(f"PLAN geschrieben: {RUN_DIR/'plan.json'}")
    raise SystemExit(0)

if args.selbsttest:
    log("SELBSTTEST: synthetische Daten, keine Netzabfragen.")
    # Keep the self-test deliberately compact. It validates every engine and report
    # path without spending minutes on production-sized 5m histories.
    args.keine_robustheit=True
    args.monte_carlo=min(int(args.monte_carlo),20)
    periods=[
        ("2025",datetime(2025,1,1,tzinfo=timezone.utc),datetime(2025,1,15,23,59,59,tzinfo=timezone.utc)),
        ("2026_BIS_HEUTE",datetime(2025,1,16,tzinfo=timezone.utc),datetime(2025,1,31,23,59,59,tzinfo=timezone.utc)),
    ]
    ALL_PERIODS=list(periods)+[combined_period()]
    for j,sym in enumerate(("AAA","BBB")):
        f=synthetic_data("1h",24*460,start="2023-11-01")
        f=f.copy();f[["open","high","low","close"]]*=(1+0.03*j)
        market_frames[("eToro",sym)]=f;daily_frames["eToro"][sym]=local_daily(f,"UTC",False)
    # Intraday path only needs BTC; ETH/SOL are auxiliary daily series for momentum rotation.
    f=synthetic_data("5min",12*24*50,start="2024-12-15")
    market_frames[("OKX","BTC")]=f;inst_by_symbol["BTC"]="BTC-EUR"
    for j,sym in enumerate(("BTC","ETH","SOL")):
        d=synthetic_data("1D",800,start="2024-01-01")
        d=d.copy();d[["open","high","low","close"]]*=(1+0.05*j)
        daily_frames["OKX"][sym]=d
    daily_frames["eToro"]["AAA"]=local_daily(market_frames[("eToro","AAA")],"UTC",False)
    daily_frames["eToro"]["BBB"]=local_daily(market_frames[("eToro","BBB")],"UTC",False)
    stocks=[{"symbol":x} for x in ("AAA","BBB")];cryptos=[{"symbol":"BTC"}]
    # Die vor dem Selbsttest aus dem realen Universum angelegte Erwartungsmatrix
    # darf die synthetische Testabdeckung nicht verfaelschen.
    test_status.clear()
    for m in stocks:
        sym=str(m.get("symbol") or "").upper()
        for label,_,_ in ALL_PERIODS:
            for strategy in ("NEXUS_STANDARD","TURTLE_DONCHIAN","IBS_MEAN_REVERSION")+V6_ETORO:
                expect("eToro",strategy,label,sym)
            if args.mit_crossover_forschung:expect("eToro","NEXUS_CROSSOVER_RESEARCH",label,sym)
    for m in cryptos:
        sym=str(m.get("symbol") or "").upper()
        for label,_,_ in ALL_PERIODS:
            for strategy in ("NEXUS_STANDARD","FREQTRADE_SAMPLE","TURTLE_DONCHIAN")+V6_OKX:
                expect("OKX",strategy,label,sym)
            if args.mit_crossover_forschung:expect("OKX","NEXUS_CROSSOVER_RESEARCH",label,sym)
    for label,_,_ in ALL_PERIODS:
        expect("OKX","CRYPTO_MOMENTUM_REFERENCE",label,"PORTFOLIO")
    log("SELBSTTEST-Auswahl: 2 synthetische Aktien, 1 Intraday-Coin und 2 zusaetzliche Daily-Coins fuer Momentum.")
else:
    # Stocks: one 1h provider series and provider-aware daily series.
    for m in stocks:
        sym=str(m.get("symbol") or "").upper()
        try:
            load_start=periods[0][1];load_end=periods[-1][2]
            log(f"AKTIE {sym}: 1h-Historie laden/Cache pruefen ...")
            frame,source,provider=stock_history(sym,load_start,load_end)
            market_frames[("eToro",sym)]=frame
            tz=provider_timezone(provider);daily=local_daily(frame,tz,False);daily_frames["eToro"][sym]=daily
            validation_inputs[("NEXUS_STANDARD","eToro")].append(frame);validation_inputs[("TURTLE_DONCHIAN","eToro")].append(daily);validation_inputs[("IBS_MEAN_REVERSION","eToro")].append(daily)
            for v6n in V6_ETORO:validation_inputs[(v6n,"eToro")].append(daily)
            if args.mit_crossover_forschung:validation_inputs[("NEXUS_CROSSOVER_RESEARCH","eToro")].append(frame)
            jumps=daily["close"].pct_change().abs();jump_rows=[{"date":str(i),"move_pct":float(v*100)} for i,v in jumps[jumps>0.45].items()]
            adjusted="JA (Yahoo auto_adjust)" if "YAHOO" in source else "UNBEKANNT/PROVIDERABHAENGIG"
            corporate_checks.append({"symbol":sym,"provider_symbol":provider,"source":source,"price_adjustment":adjusted,"split_like_moves":len(jump_rows),"largest_abs_daily_move_pct":float(jumps.max()*100) if len(jumps.dropna()) else None,"status":"PRUEFEN" if jump_rows and "YAHOO" not in source else "OK/KEIN_HARTER_BEWEIS"})
            data_manifest.append({"broker":"eToro","symbol":sym,"provider_symbol":provider,"source":source,"rows_1h":len(frame),"rows_daily":len(daily),"from":str(frame.index.min()),"to":str(frame.index.max()),"daily_digest":frame_digest(daily)})
        except Exception as exc:
            for label,_,_ in ALL_PERIODS:
                for st in ("NEXUS_STANDARD","TURTLE_DONCHIAN","IBS_MEAN_REVERSION")+V6_ETORO+(('NEXUS_CROSSOVER_RESEARCH',) if args.mit_crossover_forschung else ()):
                    err("eToro",st,label,sym,exc)

    # OKX selected universe: 5m for NEXUS/Freqtrade, compact daily history for daily reference strategies.
    client=None;catalog=None
    if cryptos:
        try:client=okx_public_client();catalog=client.public_instruments()
        except Exception as exc:
            for m in cryptos:
                sym=str(m.get("symbol") or "").upper()
                for label,_,_ in ALL_PERIODS:
                    for st in ("NEXUS_STANDARD","FREQTRADE_SAMPLE","TURTLE_DONCHIAN")+V6_OKX+(('NEXUS_CROSSOVER_RESEARCH',) if args.mit_crossover_forschung else ()):
                        err("OKX",st,label,sym,exc)
    if client is not None:
        for m in cryptos:
            sym=str(m.get("symbol") or "").upper()
            try:
                inst=resolve_okx_inst(m,client,catalog)
                if not inst:raise RuntimeError("kein aktuell live aufloesbarer OKX-Spotmarkt")
                inst_by_symbol[sym]=inst
                log(f"OKX {sym} ({inst}): 5m-Historie laden/fortsetzen ...")
                f5,source5,req5=okx_history_5m(inst,periods[0][1],periods[-1][2],client)
                f15=resample_15m(f5);market_frames[("OKX",sym)]=f5
                daily_start=pd.Timestamp(args.warmup_start,tz="UTC").to_pydatetime();daily,source_d,req_d=okx_history_daily(inst,daily_start,periods[-1][2],client)
                daily_frames["OKX"][sym]=daily
                validation_inputs[("NEXUS_STANDARD","OKX")].append(f15);validation_inputs[("FREQTRADE_SAMPLE","OKX")].append(f5);validation_inputs[("TURTLE_DONCHIAN","OKX")].append(daily)
                for v6n in V6_OKX:validation_inputs[(v6n,"OKX")].append(daily)
                if args.mit_crossover_forschung:validation_inputs[("NEXUS_CROSSOVER_RESEARCH","OKX")].append(f15)
                data_manifest.append({"broker":"OKX","symbol":sym,"instrument":inst,"source_5m":source5,"source_daily":source_d,"rows_5m":len(f5),"rows_15m":len(f15),"rows_daily":len(daily),"requests_5m":req5,"requests_daily":req_d,"from_5m":str(f5.index.min()),"to_5m":str(f5.index.max()),"from_daily":str(daily.index.min()),"to_daily":str(daily.index.max())})
            except Exception as exc:
                for label,_,_ in ALL_PERIODS:
                    for st in ("NEXUS_STANDARD","FREQTRADE_SAMPLE","TURTLE_DONCHIAN")+V6_OKX+(('NEXUS_CROSSOVER_RESEARCH',) if args.mit_crossover_forschung else ()):
                        err("OKX",st,label,sym,exc)
        # Momentum/BTC benchmark requires BTC daily even when BTC is not selected.
        if "BTC" not in daily_frames["OKX"]:
            try:
                dummy={"symbol":"BTC","inst_id":""};inst=resolve_okx_inst(dummy,client,catalog)
                if inst:
                    daily,source_d,req_d=okx_history_daily(inst,pd.Timestamp(args.warmup_start,tz="UTC").to_pydatetime(),periods[-1][2],client)
                    daily_frames["OKX"]["BTC"]=daily;inst_by_symbol["BTC"]=inst
                    data_manifest.append({"broker":"OKX","symbol":"BTC","instrument":inst,"auxiliary":True,"source_daily":source_d,"rows_daily":len(daily),"requests_daily":req_d})
            except Exception as exc:
                errors.append({"broker":"OKX","strategy":"CRYPTO_MOMENTUM_REFERENCE","period":"ALL","symbol":"BTC_AUX","error":f"{type(exc).__name__}: {exc}"})

# Synthetic validation lists must be filled after synthetic frame creation.
if args.selbsttest:
    validation_inputs[("NEXUS_STANDARD","eToro")]=[market_frames[("eToro","AAA")]]
    validation_inputs[("TURTLE_DONCHIAN","eToro")]=[daily_frames["eToro"]["AAA"]]
    validation_inputs[("IBS_MEAN_REVERSION","eToro")]=[daily_frames["eToro"]["AAA"]]
    for v6n in V6_ETORO:validation_inputs[(v6n,"eToro")]=[daily_frames["eToro"]["AAA"]]
    validation_inputs[("NEXUS_STANDARD","OKX")]=[resample_15m(market_frames[("OKX","BTC")])]
    validation_inputs[("FREQTRADE_SAMPLE","OKX")]=[market_frames[("OKX","BTC")]]
    validation_inputs[("TURTLE_DONCHIAN","OKX")]=[daily_frames["OKX"]["BTC"]]
    for v6n in V6_OKX:validation_inputs[(v6n,"OKX")]=[daily_frames["OKX"]["BTC"]]
    # Replace the real-universe expectation matrix with the synthetic one.
    test_status.clear()
    for m in stocks:
        sym=str(m.get("symbol") or "").upper()
        for label,_,_ in ALL_PERIODS:
            for strategy in ("NEXUS_STANDARD","TURTLE_DONCHIAN","IBS_MEAN_REVERSION")+V6_ETORO:
                expect("eToro",strategy,label,sym)
    for m in cryptos:
        sym=str(m.get("symbol") or "").upper()
        for label,_,_ in ALL_PERIODS:
            for strategy in ("NEXUS_STANDARD","FREQTRADE_SAMPLE","TURTLE_DONCHIAN")+V6_OKX:
                expect("OKX",strategy,label,sym)
    for label,_,_ in ALL_PERIODS:expect("OKX","CRYPTO_MOMENTUM_REFERENCE",label,"PORTFOLIO")

# Run causal single-instrument strategies. Trades from a period with a data
# quality block remain auditable, but are never admitted to the portfolio rank.
for broker, members in (("eToro",stocks),("OKX",cryptos)):
    for m in members:
        sym=str(m.get("symbol") or "").upper()
        if (broker,sym) not in market_frames:continue
        raw=market_frames[(broker,sym)];daily=daily_frames[broker].get(sym,pd.DataFrame())
        source="SYNTHETIC" if args.selbsttest else next((x.get("source") or x.get("source_5m") for x in data_manifest if x.get("broker")==broker and x.get("symbol")==sym),"")
        for label,start,end in ALL_PERIODS:
            base_q=apply_symbol_quality_overlay(broker,sym,period_quality(raw,start,end,"stock" if broker=="eToro" else "crypto","1h" if broker=="eToro" else "5m"))
            # NEXUS Standard uses the actually configured entry mode.
            try:
                mode=str(getattr(config,"ENTRY_MODE","trend"));sig=nexus_signal_frame(raw if broker=="eToro" else resample_15m(raw),mode)
                tr=simulate_signal_strategy(sig,start,end,broker=broker,symbol=sym,strategy="NEXUS_STANDARD",asset_type="stock" if broker=="eToro" else "crypto",currency=(catalog_metadata(sym).get("currency") or "USD") if broker=="eToro" else (inst_by_symbol.get(sym,"-").split("-")[-1] or "EUR"),stop_mode="nexus")
                for x in tr:x["data_quality"]=base_q.get("quality");x["excluded_from_portfolio"]=not quality_allows_portfolio(base_q)
                if quality_allows_portfolio(base_q):trade_sets[(broker,"NEXUS_STANDARD",label)].extend(tr)
                all_trade_rows.extend([{**x,"period":label} for x in tr]);summarize_symbol_trades(broker,"NEXUS_STANDARD",label,sym,tr,source,base_q)
            except Exception as exc:err(broker,"NEXUS_STANDARD",label,sym,exc)
            if args.mit_crossover_forschung:
                try:
                    sig=nexus_signal_frame(raw if broker=="eToro" else resample_15m(raw),"crossover")
                    tr=simulate_signal_strategy(sig,start,end,broker=broker,symbol=sym,strategy="NEXUS_CROSSOVER_RESEARCH",asset_type="stock" if broker=="eToro" else "crypto",currency=(catalog_metadata(sym).get("currency") or "USD") if broker=="eToro" else "EUR",stop_mode="nexus")
                    for x in tr:x["data_quality"]=base_q.get("quality");x["excluded_from_portfolio"]=not quality_allows_portfolio(base_q)
                    if quality_allows_portfolio(base_q):trade_sets[(broker,"NEXUS_CROSSOVER_RESEARCH",label)].extend(tr)
                    all_trade_rows.extend([{**x,"period":label} for x in tr]);summarize_symbol_trades(broker,"NEXUS_CROSSOVER_RESEARCH",label,sym,tr,source,base_q)
                except Exception as exc:err(broker,"NEXUS_CROSSOVER_RESEARCH",label,sym,exc)
            # Daily reference strategies require their own full daily-history gate.
            daily_q=apply_symbol_quality_overlay(broker,sym,period_quality(daily,start,end,"stock" if broker=="eToro" else "crypto","1d"))
            try:
                sig=turtle_signals(daily);tr=simulate_signal_strategy(sig,start,end,broker=broker,symbol=sym,strategy="TURTLE_DONCHIAN",asset_type="stock" if broker=="eToro" else "crypto",currency=(catalog_metadata(sym).get("currency") or "USD") if broker=="eToro" else "EUR",stop_mode="turtle",turtle_atr_mult=2.0,risk_per_trade=0.01)
                for x in tr:x["data_quality"]=daily_q.get("quality");x["excluded_from_portfolio"]=not quality_allows_portfolio(daily_q)
                if quality_allows_portfolio(daily_q):trade_sets[(broker,"TURTLE_DONCHIAN",label)].extend(tr)
                all_trade_rows.extend([{**x,"period":label} for x in tr]);summarize_symbol_trades(broker,"TURTLE_DONCHIAN",label,sym,tr,source,daily_q)
            except Exception as exc:err(broker,"TURTLE_DONCHIAN",label,sym,exc)
            if broker=="eToro":
                try:
                    sig=ibs_signals(daily);tr=simulate_signal_strategy(sig,start,end,broker=broker,symbol=sym,strategy="IBS_MEAN_REVERSION",asset_type="stock",currency=(catalog_metadata(sym).get("currency") or "USD"),stop_mode="none")
                    for x in tr:x["data_quality"]=daily_q.get("quality");x["excluded_from_portfolio"]=not quality_allows_portfolio(daily_q)
                    if quality_allows_portfolio(daily_q):trade_sets[(broker,"IBS_MEAN_REVERSION",label)].extend(tr)
                    all_trade_rows.extend([{**x,"period":label} for x in tr]);summarize_symbol_trades(broker,"IBS_MEAN_REVERSION",label,sym,tr,source,daily_q)
                except Exception as exc:err(broker,"IBS_MEAN_REVERSION",label,sym,exc)
            # V6: dokumentierte Referenzstrategien auf demselben Tages-Gate.
            for v6_name,(v6_builder,v6_brokers) in V6_STRATEGY_BUILDERS.items():
                if broker not in v6_brokers:continue
                try:
                    sig=v6_builder(daily);tr=simulate_signal_strategy(sig,start,end,broker=broker,symbol=sym,strategy=v6_name,asset_type="stock" if broker=="eToro" else "crypto",currency=(catalog_metadata(sym).get("currency") or "USD") if broker=="eToro" else "EUR",stop_mode="none")
                    for x in tr:x["data_quality"]=daily_q.get("quality");x["excluded_from_portfolio"]=not quality_allows_portfolio(daily_q)
                    if quality_allows_portfolio(daily_q):trade_sets[(broker,v6_name,label)].extend(tr)
                    all_trade_rows.extend([{**x,"period":label} for x in tr]);summarize_symbol_trades(broker,v6_name,label,sym,tr,source,daily_q)
                except Exception as exc:err(broker,v6_name,label,sym,exc)
            if broker=="OKX":
                try:
                    tr=normalize_freqtrade_trades(raw,start,end,sym,inst_by_symbol.get(sym,""))
                    for x in tr:x["data_quality"]=base_q.get("quality");x["excluded_from_portfolio"]=not quality_allows_portfolio(base_q)
                    if quality_allows_portfolio(base_q):trade_sets[(broker,"FREQTRADE_SAMPLE",label)].extend(tr)
                    all_trade_rows.extend([{**x,"period":label} for x in tr]);summarize_symbol_trades(broker,"FREQTRADE_SAMPLE",label,sym,tr,source,base_q)
                except Exception as exc:err(broker,"FREQTRADE_SAMPLE",label,sym,exc)

# Persist single-strategy trade rows before portfolio processing.
if all_trade_rows:
    pd.DataFrame(all_trade_rows).to_csv(RUN_DIR/"trade_events_all.csv",index=False)

# Crypto momentum is a direct portfolio model. It is never presented as an exact replica of the Reddit author's private implementation.
momentum_runs={}
for label,start,end in ALL_PERIODS:
    try:
        mr=simulate_crypto_momentum(daily_frames["OKX"],start,end,scenario="normal")
        if mr.get("error"):raise RuntimeError(mr["error"])
        momentum_runs[(label,"normal")]=mr;set_status("OKX","CRYPTO_MOMENTUM_REFERENCE",label,"PORTFOLIO","GETESTET","OK")
    except Exception as exc:err("OKX","CRYPTO_MOMENTUM_REFERENCE",label,"PORTFOLIO",exc)

# ---------- Portfolio replays, benchmarks and metrics -----------------------
portfolio_rows=[];portfolio_objects={};benchmark_rows=[];regime_rows=[];walkforward_rows=[];validation_rows=[];robustness_rows=[];mc_rows=[];scenario_rows=[]

# Benchmarks for every reporting period.
benchmarks={}
for label,start,end in ALL_PERIODS:
    et=benchmark_equal_weight(daily_frames["eToro"],start,end,asset_type="stock");okxeq=benchmark_equal_weight({k:v for k,v in daily_frames["OKX"].items() if k in {str(x.get('symbol') or '').upper() for x in cryptos}},start,end,asset_type="crypto");btc=benchmark_btc(daily_frames["OKX"],start,end)
    benchmarks[("eToro",label)]={"primary":"EQUAL_WEIGHT_UNIVERSE","return_pct":et.get("return_pct"),"equity":et.get("equity")}
    benchmarks[("OKX",label)]={"primary":"BTC_BUY_HOLD","return_pct":btc.get("return_pct"),"equity":btc.get("equity"),"equal_weight_return_pct":okxeq.get("return_pct"),"equal_weight_equity":okxeq.get("equity")}
    benchmark_rows.append({"broker":"eToro","period":label,"benchmark":"EQUAL_WEIGHT_UNIVERSE","return_pct":et.get("return_pct"),"members":len(et.get("members") or [])})
    benchmark_rows.append({"broker":"OKX","period":label,"benchmark":"BTC_BUY_HOLD","return_pct":btc.get("return_pct")})
    benchmark_rows.append({"broker":"OKX","period":label,"benchmark":"EQUAL_WEIGHT_UNIVERSE","return_pct":okxeq.get("return_pct"),"members":len(okxeq.get("members") or [])})

main_strategies={"eToro":["NEXUS_STANDARD","TURTLE_DONCHIAN","IBS_MEAN_REVERSION"]+list(V6_ETORO),"OKX":["NEXUS_STANDARD","FREQTRADE_SAMPLE","TURTLE_DONCHIAN","CRYPTO_MOMENTUM_REFERENCE"]+list(V6_OKX)}
if args.mit_crossover_forschung:
    main_strategies["eToro"].append("NEXUS_CROSSOVER_RESEARCH");main_strategies["OKX"].append("NEXUS_CROSSOVER_RESEARCH")

for broker,strategies in main_strategies.items():
    for strategy in strategies:
        # Validation once per broker/strategy from representative data.
        if strategy=="CRYPTO_MOMENTUM_REFERENCE":
            val={"lookahead":{"status":"BESTANDEN_DURCH_POINT_IN_TIME_KONSTRUKTION","detail":"Alle Ranking-, SMA-, Volumen- und Momentumfenster verwenden nur Daten <= Entscheidungsdatum."},"recursive":{"status":"NICHT_ANWENDBAR","detail":"Portfolio-Rotation mit festen 365/200/126/90-Tage-Vertraegen."}}
        else:
            val=strategy_validation(strategy,broker,validation_inputs.get((strategy,broker),[]))
        validation_rows.append({"broker":broker,"strategy":strategy,"lookahead_status":val.get("lookahead",{}).get("status"),"recursive_status":val.get("recursive",{}).get("status"),"detail":json.dumps(val,ensure_ascii=False,default=str)[:4000]})
        for label,start,end in ALL_PERIODS:
            try:
                if strategy=="CRYPTO_MOMENTUM_REFERENCE":
                    replay=momentum_runs[(label,"normal")]["replay"]
                else:
                    replay=portfolio_replay(trade_sets.get((broker,strategy,label),[]),start,end,broker,strategy,market_frames,"normal")
                metrics=performance_metrics(replay,broker);bench=benchmarks.get((broker,label),{})
                matched=exposure_matched_benchmark(bench.get("equity"),replay.get("daily_exposure"),float(replay.get("starting_capital") or args.capital))
                row={"broker":broker,"strategy":strategy,"period":label,**metrics,"benchmark":bench.get("primary"),"benchmark_return_pct":bench.get("return_pct"),
                     "alpha_vs_primary_benchmark_pct":(metrics["return_pct"]-bench.get("return_pct")) if bench.get("return_pct") is not None else None,
                     "exposure_matched_benchmark_return_pct":matched.get("return_pct"),
                     "alpha_vs_exposure_matched_pct":(metrics["return_pct"]-matched.get("return_pct")) if matched.get("return_pct") is not None else None,
                     "lookahead_status":val.get("lookahead",{}).get("status"),"recursive_status":val.get("recursive",{}).get("status")}
                portfolio_rows.append(row);portfolio_objects[(broker,strategy,label)]=replay
                # Regime attribution uses primary benchmark equity.
                regs=regimes_from_benchmark(bench.get("equity"));
                for rr in trade_regime_breakdown(replay.get("accepted") or [],regs):regime_rows.append({"broker":broker,"strategy":strategy,"period":label,**rr})
                log(f"PORTFOLIO {broker:5s} {label:17s} {strategy:28s}: {metrics['return_pct']:+7.2f}% | DD {metrics['max_drawdown_pct']:+7.2f}% | Trades {metrics['trades']:3d}")
            except Exception as exc:
                errors.append({"broker":broker,"strategy":strategy,"period":label,"symbol":"PORTFOLIO","error":f"{type(exc).__name__}: {exc}"})

# Stress/extreme and bootstrap on the combined window.
all_label,all_start,all_end=combined_period()
for broker,strategies in main_strategies.items():
    for strategy in strategies:
        normal=portfolio_objects.get((broker,strategy,all_label))
        if normal is None:continue
        for scenario in ("normal","stress","extreme"):
            try:
                if strategy=="CRYPTO_MOMENTUM_REFERENCE":
                    rr=simulate_crypto_momentum(daily_frames["OKX"],all_start,all_end,scenario=scenario)
                    if rr.get("error"):raise RuntimeError(rr["error"])
                    replay=rr["replay"]
                else:replay=portfolio_replay(trade_sets.get((broker,strategy,all_label),[]),all_start,all_end,broker,strategy,market_frames,scenario)
                mm=performance_metrics(replay,broker);scenario_rows.append({"broker":broker,"strategy":strategy,"scenario":scenario,"return_pct":mm["return_pct"],"max_drawdown_pct":mm["max_drawdown_pct"],"sharpe":mm.get("sharpe")})
            except Exception as exc:errors.append({"broker":broker,"strategy":strategy,"period":all_label,"symbol":"STRESS","error":f"{type(exc).__name__}: {exc}"})
        mc=moving_block_bootstrap(normal["daily_equity"],broker,int(args.monte_carlo),int(args.mc_block_days),seed=20260915+sum(map(ord,strategy+broker)))
        mc_rows.append({"broker":broker,"strategy":strategy,**mc})

def annual_trade_pool_for_window(broker,strategy,qs,qe):
    year=pd.Timestamp(qs).year
    label="2025" if year==2025 else ("2026_BIS_HEUTE" if any(x[0]=="2026_BIS_HEUTE" for x in periods) else "2026_GESAMT")
    pool=trade_sets.get((broker,strategy,label),[])
    return [t for t in pool if pd.Timestamp(t.get("entry_time"))>=pd.Timestamp(qs) and pd.Timestamp(t.get("entry_time"))<=pd.Timestamp(qe)]

# Quarterly fixed-rule walk-forward robustness (no parameter optimization).
# A missing quarter is not a zero-return quarter: it is explicitly untestable.
for broker,strategies in main_strategies.items():
    for strategy in strategies:
        vals=[];untestable=0
        for qlabel,qs,qe in quarter_windows(all_start,all_end):
            ds=strategy_window_data_status(broker,strategy,qs,qe)
            if not ds["usable"]:
                untestable+=1;walkforward_rows.append({"broker":broker,"strategy":strategy,"window":qlabel,"return_pct":None,"status":"NICHT_PRUEFBAR_DATEN","data_coverage_pct":ds["coverage_pct"],"valid_symbols":ds["valid_count"],"expected_symbols":ds["expected_count"]});continue
            try:
                if strategy=="CRYPTO_MOMENTUM_REFERENCE":
                    rr=simulate_crypto_momentum(daily_frames["OKX"],qs,qe,scenario="normal");replay=rr.get("replay") if not rr.get("error") else None
                else:replay=portfolio_replay(annual_trade_pool_for_window(broker,strategy,qs,qe),qs,qe,broker,strategy,market_frames,"normal")
                if replay is None:
                    untestable+=1;walkforward_rows.append({"broker":broker,"strategy":strategy,"window":qlabel,"return_pct":None,"status":"NICHT_PRUEFBAR_REPLAY"});continue
                ret=(float(replay["final_equity"])/float(replay["starting_capital"])-1)*100;vals.append(ret)
                walkforward_rows.append({"broker":broker,"strategy":strategy,"window":qlabel,"return_pct":ret,"status":"GETESTET_KEINE_TRADES" if len(replay.get("accepted") or [])==0 else "GETESTET","data_coverage_pct":ds["coverage_pct"],"valid_symbols":ds["valid_count"],"expected_symbols":ds["expected_count"]})
            except Exception as exc:
                untestable+=1;walkforward_rows.append({"broker":broker,"strategy":strategy,"window":qlabel,"return_pct":None,"status":"FEHLER","error":f"{type(exc).__name__}: {exc}"})
        if vals:
            walkforward_rows.append({"broker":broker,"strategy":strategy,"window":"SUMMARY","return_pct":None,"positive_windows_pct":100*sum(1 for x in vals if x>0)/len(vals),"median_window_return_pct":float(np.median(vals)),"windows":len(vals),"untestable_windows":untestable})
        else:walkforward_rows.append({"broker":broker,"strategy":strategy,"window":"SUMMARY","return_pct":None,"positive_windows_pct":None,"median_window_return_pct":None,"windows":0,"untestable_windows":untestable})
# ---------- Parameter robustness without optimization -------------------------
trial_registry=[]

def run_turtle_variant(broker, params):
    trades=[]
    members=stocks if broker=="eToro" else cryptos
    for m in members:
        sym=str(m.get("symbol") or "").upper();daily=daily_frames[broker].get(sym)
        if daily is None or daily.empty:continue
        sig=turtle_signals(daily,params[0],params[1],14)
        trades.extend(simulate_signal_strategy(sig,all_start,all_end,broker=broker,symbol=sym,strategy="TURTLE_DONCHIAN",asset_type="stock" if broker=="eToro" else "crypto",currency="USD" if broker=="eToro" else "EUR",stop_mode="turtle",turtle_atr_mult=params[2],risk_per_trade=0.01))
    return portfolio_replay(trades,all_start,all_end,broker,"TURTLE_DONCHIAN",market_frames,"normal")

def run_ibs_variant(params):
    trades=[]
    rw,hw,mult,thr=params
    for m in stocks:
        sym=str(m.get("symbol") or "").upper();daily=daily_frames["eToro"].get(sym)
        if daily is None or daily.empty:continue
        sig=ibs_signals(daily,rw,hw,mult,thr)
        trades.extend(simulate_signal_strategy(sig,all_start,all_end,broker="eToro",symbol=sym,strategy="IBS_MEAN_REVERSION",asset_type="stock",currency=(catalog_metadata(sym).get("currency") or "USD"),stop_mode="none"))
    return portfolio_replay(trades,all_start,all_end,"eToro","IBS_MEAN_REVERSION",market_frames,"normal")

if not args.keine_robustheit:
    for broker in ("eToro","OKX"):
        variants=[(18,9,1.8),(20,10,2.0),(22,11,2.2),(25,12,2.5)]
        vals=[];canonical=None
        for v in variants:
            try:
                rp=run_turtle_variant(broker,v);ret=safe_float(rp.get("return_pct"),0.0);vals.append(ret)
                if v==(20,10,2.0):canonical=ret
                trial_registry.append({"broker":broker,"strategy":"TURTLE_DONCHIAN","variant":f"entry={v[0]},exit={v[1]},atr={v[2]}","return_pct":ret,"purpose":"ROBUSTHEIT_NICHT_OPTIMIERUNG"})
            except Exception as exc:trial_registry.append({"broker":broker,"strategy":"TURTLE_DONCHIAN","variant":str(v),"error":f"{type(exc).__name__}:{exc}","purpose":"ROBUSTHEIT_NICHT_OPTIMIERUNG"})
        robustness_rows.append({"broker":broker,"strategy":"TURTLE_DONCHIAN","variants":len(vals),"positive_variants_pct":100*sum(1 for x in vals if x>0)/len(vals) if vals else None,"same_sign_as_canonical_pct":100*sum(1 for x in vals if canonical is not None and (x>=0)==(canonical>=0))/len(vals) if vals and canonical is not None else None,"median_return_pct":float(np.median(vals)) if vals else None,"min_return_pct":min(vals) if vals else None,"max_return_pct":max(vals) if vals else None})
    ibs_variants=[(21,9,2.25,0.25),(25,10,2.5,0.30),(29,11,2.75,0.35),(25,10,2.5,0.25),(25,10,2.5,0.35)]
    vals=[];canonical=None
    for v in ibs_variants:
        try:
            rp=run_ibs_variant(v);ret=safe_float(rp.get("return_pct"),0.0);vals.append(ret)
            if v==(25,10,2.5,0.30):canonical=ret
            trial_registry.append({"broker":"eToro","strategy":"IBS_MEAN_REVERSION","variant":f"range={v[0]},high={v[1]},mult={v[2]},ibs={v[3]}","return_pct":ret,"purpose":"ROBUSTHEIT_NICHT_OPTIMIERUNG"})
        except Exception as exc:trial_registry.append({"broker":"eToro","strategy":"IBS_MEAN_REVERSION","variant":str(v),"error":f"{type(exc).__name__}:{exc}","purpose":"ROBUSTHEIT_NICHT_OPTIMIERUNG"})
    robustness_rows.append({"broker":"eToro","strategy":"IBS_MEAN_REVERSION","variants":len(vals),"positive_variants_pct":100*sum(1 for x in vals if x>0)/len(vals) if vals else None,"same_sign_as_canonical_pct":100*sum(1 for x in vals if canonical is not None and (x>=0)==(canonical>=0))/len(vals) if vals and canonical is not None else None,"median_return_pct":float(np.median(vals)) if vals else None,"min_return_pct":min(vals) if vals else None,"max_return_pct":max(vals) if vals else None})
    momentum_variants=[((18,54,108),180),((21,63,126),200),((24,72,144),220)]
    vals=[];canonical=None
    for horizons,sma in momentum_variants:
        try:
            rr=simulate_crypto_momentum(daily_frames["OKX"],all_start,all_end,scenario="normal",horizons=horizons,sma_days=sma)
            if rr.get("error"):raise RuntimeError(rr["error"])
            ret=safe_float(rr["replay"].get("return_pct"),0.0);vals.append(ret)
            if tuple(horizons)==(21,63,126) and sma==200:canonical=ret
            trial_registry.append({"broker":"OKX","strategy":"CRYPTO_MOMENTUM_REFERENCE","variant":f"horizons={horizons},sma={sma}","return_pct":ret,"purpose":"ROBUSTHEIT_NICHT_OPTIMIERUNG"})
        except Exception as exc:trial_registry.append({"broker":"OKX","strategy":"CRYPTO_MOMENTUM_REFERENCE","variant":f"horizons={horizons},sma={sma}","error":f"{type(exc).__name__}:{exc}","purpose":"ROBUSTHEIT_NICHT_OPTIMIERUNG"})
    robustness_rows.append({"broker":"OKX","strategy":"CRYPTO_MOMENTUM_REFERENCE","variants":len(vals),"positive_variants_pct":100*sum(1 for x in vals if x>0)/len(vals) if vals else None,"same_sign_as_canonical_pct":100*sum(1 for x in vals if canonical is not None and (x>=0)==(canonical>=0))/len(vals) if vals and canonical is not None else None,"median_return_pct":float(np.median(vals)) if vals else None,"min_return_pct":min(vals) if vals else None,"max_return_pct":max(vals) if vals else None})
    v6_varianten={
        "RSI2_MEAN_REVERSION":[{"buy_below":5.0},{"buy_below":10.0},{"buy_below":15.0}],
        "HIGH_52W_MOMENTUM":[{"proximity":0.95},{"proximity":0.97},{"proximity":0.99}],
        "GOLDEN_CROSS_TREND":[{"fast":40,"slow":180},{"fast":50,"slow":200},{"fast":60,"slow":220}],
        "TSMOM_LONG_FLAT":[{"lookback":60},{"lookback":90},{"lookback":120}],
        "KELTNER_BREAKOUT":[{"atr_mult":1.5},{"atr_mult":2.0},{"atr_mult":2.5}],
        "MACD_TREND_CRYPTO":[{"fast":8,"slow":21},{"fast":12,"slow":26},{"fast":16,"slow":34}],
    }
    v6_kanonisch={"RSI2_MEAN_REVERSION":{"buy_below":10.0},"HIGH_52W_MOMENTUM":{"proximity":0.97},
        "GOLDEN_CROSS_TREND":{"fast":50,"slow":200},"TSMOM_LONG_FLAT":{"lookback":90},
        "KELTNER_BREAKOUT":{"atr_mult":2.0},"MACD_TREND_CRYPTO":{"fast":12,"slow":26}}
    for v6_name,(v6_builder,v6_brokers) in V6_STRATEGY_BUILDERS.items():
        for v6_broker in v6_brokers:
            v6_members=stocks if v6_broker=="eToro" else cryptos
            vals=[];canonical=None
            for variante in v6_varianten[v6_name]:
                try:
                    v6_trades=[]
                    for m in v6_members:
                        sym=str(m.get("symbol") or "").upper();daily=daily_frames[v6_broker].get(sym)
                        if daily is None or daily.empty:continue
                        sig=v6_builder(daily,**variante)
                        v6_trades.extend(simulate_signal_strategy(sig,all_start,all_end,broker=v6_broker,symbol=sym,strategy=v6_name,asset_type="stock" if v6_broker=="eToro" else "crypto",currency="USD" if v6_broker=="eToro" else "EUR",stop_mode="none"))
                    rp=portfolio_replay(v6_trades,all_start,all_end,v6_broker,v6_name,market_frames,"normal");ret=safe_float(rp.get("return_pct"),0.0);vals.append(ret)
                    if variante==v6_kanonisch[v6_name]:canonical=ret
                    trial_registry.append({"broker":v6_broker,"strategy":v6_name,"variant":json.dumps(variante,sort_keys=True),"return_pct":ret,"purpose":"ROBUSTHEIT_NICHT_OPTIMIERUNG"})
                except Exception as exc:trial_registry.append({"broker":v6_broker,"strategy":v6_name,"variant":json.dumps(variante,sort_keys=True),"error":f"{type(exc).__name__}:{exc}","purpose":"ROBUSTHEIT_NICHT_OPTIMIERUNG"})
            robustness_rows.append({"broker":v6_broker,"strategy":v6_name,"variants":len(vals),"positive_variants_pct":100*sum(1 for x in vals if x>0)/len(vals) if vals else None,"same_sign_as_canonical_pct":100*sum(1 for x in vals if canonical is not None and (x>=0)==(canonical>=0))/len(vals) if vals and canonical is not None else None,"median_return_pct":float(np.median(vals)) if vals else None,"min_return_pct":min(vals) if vals else None,"max_return_pct":max(vals) if vals else None})
else:
    for broker,strategy in (("eToro","TURTLE_DONCHIAN"),("OKX","TURTLE_DONCHIAN"),("eToro","IBS_MEAN_REVERSION"),("OKX","CRYPTO_MOMENTUM_REFERENCE"))+tuple((b,n) for n,(f,bs) in V6_STRATEGY_BUILDERS.items() for b in bs):
        robustness_rows.append({"broker":broker,"strategy":strategy,"variants":0,"status":"UEBERSPRUNGEN"})

for broker,strategy in (("eToro","NEXUS_STANDARD"),("OKX","NEXUS_STANDARD"),("OKX","FREQTRADE_SAMPLE")):
    robustness_rows.append({"broker":broker,"strategy":strategy,"variants":0,"status":"KANONISCHE_REGELN_NICHT_VARIIERT"})
if args.mit_crossover_forschung:
    robustness_rows.extend([{"broker":"eToro","strategy":"NEXUS_CROSSOVER_RESEARCH","variants":0,"status":"FORSCHUNGSVARIANTE"},{"broker":"OKX","strategy":"NEXUS_CROSSOVER_RESEARCH","variants":0,"status":"FORSCHUNGSVARIANTE"}])

# ---------- Correlation and consolidated cards -------------------------------
correlation_rows=[]
for broker,strategies in main_strategies.items():
    series={}
    for strategy in strategies:
        rp=portfolio_objects.get((broker,strategy,all_label))
        if rp is None:continue
        s=rp["daily_equity"].astype(float).pct_change().rename(strategy)
        series[strategy]=s
    if len(series)>=2:
        corr=pd.concat(series.values(),axis=1).corr()
        for a in corr.index:
            for b in corr.columns:
                correlation_rows.append({"broker":broker,"strategy_a":a,"strategy_b":b,"correlation":float(corr.loc[a,b])})

# Merge all combined-period evidence into one card row per broker/strategy.
def findrow(rows, **kw):
    for r in rows:
        if all(r.get(k)==v for k,v in kw.items()):return r
    return {}

cards=[]
for broker,strategies in main_strategies.items():
    for strategy in strategies:
        base=findrow(portfolio_rows,broker=broker,strategy=strategy,period=all_label)
        if not base:continue
        stress=findrow(scenario_rows,broker=broker,strategy=strategy,scenario="stress");extreme=findrow(scenario_rows,broker=broker,strategy=strategy,scenario="extreme")
        mc=findrow(mc_rows,broker=broker,strategy=strategy);wf=findrow(walkforward_rows,broker=broker,strategy=strategy,window="SUMMARY")
        rb=findrow(robustness_rows,broker=broker,strategy=strategy);val=findrow(validation_rows,broker=broker,strategy=strategy)
        p25=findrow(portfolio_rows,broker=broker,strategy=strategy,period="2025")
        p26=findrow(portfolio_rows,broker=broker,strategy=strategy,period=("2026_BIS_HEUTE" if any(x.get("period")=="2026_BIS_HEUTE" for x in portfolio_rows) else "2026_GESAMT"))
        ds=strategy_window_data_status(broker,strategy,all_start,all_end)
        quality_status="BELASTBAR" if ds["usable"] and ds["coverage_pct"]>=90 else ("EINGESCHRAENKT" if ds["usable"] else "NICHT_AUSWERTBAR")
        recursive_status=val.get("recursive_status",base.get("recursive_status"));lookahead_status=val.get("lookahead_status",base.get("lookahead_status"))
        rank_excluded=(str(lookahead_status).startswith("NICHT_BESTANDEN") or str(lookahead_status)=="FEHLER" or
                       str(recursive_status)=="SIGNAL_ABHAENGIG" or STRATEGY_SPECS.get(strategy,{}).get("class")=="RESEARCH" or not ds["usable"])
        row={**base,"label":STRATEGY_SPECS.get(strategy,{}).get("label",strategy),"class":STRATEGY_SPECS.get(strategy,{}).get("class",""),
             "return_2025_pct":p25.get("return_pct"),"return_2026_pct":p26.get("return_pct"),
             "coverage_pct":ds["coverage_pct"],"valid_symbols":ds["valid_count"],"expected_symbols":ds["expected_count"],"quality_status":quality_status,
             "stress_return_pct":stress.get("return_pct"),"extreme_return_pct":extreme.get("return_pct"),
             "mc_p05_return_pct":mc.get("p05_return_pct"),"mc_negative_probability_pct":mc.get("negative_probability_pct"),
             "oos_positive_windows_pct":wf.get("positive_windows_pct"),"oos_median_window_pct":wf.get("median_window_return_pct"),"oos_untestable_windows":wf.get("untestable_windows"),
             "parameter_positive_variants_pct":rb.get("positive_variants_pct"),"parameter_same_sign_pct":rb.get("same_sign_as_canonical_pct"),
             "lookahead_status":lookahead_status,"recursive_status":recursive_status,"rank_excluded":rank_excluded}
        cards.append(row)

# Rank only quality-eligible main strategies. Limited data remains visible but
# explicitly marked EINGESCHRAENKT; untestable data never receives a rank.
for broker in {x["broker"] for x in cards}:
    eligible=[x for x in cards if x["broker"]==broker and not x.get("rank_excluded")]
    eligible.sort(key=lambda x:(-(safe_float(x.get("calmar"),-1e9) if x.get("calmar") is not None else -1e9),-(safe_float(x.get("sharpe"),-1e9) if x.get("sharpe") is not None else -1e9),-safe_float(x.get("return_pct"),-1e9)))
    for i,x in enumerate(eligible,1):x["rank"]=i

# ---------- Fixed regression checks -------------------------------------------
regression_checks=[]
# 1) Drawdown must remain anchored to the actual peak; a basis change may not reset it.
try:
    test_eq=pd.Series([1000.0,980.0,960.4],index=pd.date_range("2026-01-01",periods=3,tz="UTC"))
    test_replay={"daily_equity":test_eq,"starting_capital":1000.0,"final_equity":960.4,"accepted":[],"rejected":[]}
    dd=performance_metrics(test_replay,"eToro")["max_drawdown_pct"]
    expected=-3.96
    ok=abs(dd-expected)<1e-9
    regression_checks.append({"check":"DRAWDOWN_PEAK_ANCHOR","status":"BESTANDEN" if ok else "NICHT_BESTANDEN","measured":dd,"expected":expected})
except Exception as exc:
    regression_checks.append({"check":"DRAWDOWN_PEAK_ANCHOR","status":"FEHLER","detail":f"{type(exc).__name__}:{exc}"})

# 2) Signal must execute strictly after the completed signal bar.
try:
    idx=pd.date_range("2026-01-01",periods=8,freq="1h",tz="UTC")
    d=pd.DataFrame({"open":[100,100,101,102,103,104,105,106],"high":[101,101,102,103,104,105,106,107],"low":[99,99,100,101,102,103,104,105],"close":[100,101,102,103,104,105,106,107],"volume":[1000]*8},index=idx)
    d["enter_signal"]=[False,False,True,False,False,False,False,False];d["exit_signal"]=[False,False,False,False,True,False,False,False]
    tr=simulate_signal_strategy(d,idx[0],idx[-1],broker="eToro",symbol="REG",strategy="REGRESSION",asset_type="stock",currency="USD")
    ok=bool(tr) and pd.Timestamp(tr[0]["entry_time"])>pd.Timestamp(tr[0]["signal_time"])
    regression_checks.append({"check":"SIGNAL_BEFORE_FILL","status":"BESTANDEN" if ok else "NICHT_BESTANDEN","signal_time":tr[0]["signal_time"] if tr else None,"entry_time":tr[0]["entry_time"] if tr else None})
except Exception as exc:
    regression_checks.append({"check":"SIGNAL_BEFORE_FILL","status":"FEHLER","detail":f"{type(exc).__name__}:{exc}"})

# 3) The installed Freqtrade backtest must explicitly use observed-bar gap handling.
try:
    if compat.get("freqtrade_sample"):
        base=synthetic_data("5min",int(FT_STARTUP)+260,start="2026-01-01")
        base=base.drop(base.index[int(FT_STARTUP)+10])
        rr=ft_run_backtest(base,initial_capital=10000.0,stake_pct=min(0.1,broker_position_pct("OKX")),fee_pct=0.001,slippage_pct=0.0,execution_mode="observed_bars",include_equity_curve=False)
        assumptions=rr.get("assumptions") or {}
        ok=(assumptions.get("execution_mode")=="observed_bars" and int(rr.get("synthetic_gap_candles") or 0)>=1)
        regression_checks.append({"check":"FREQTRADE_SYNTHETIC_GAP_NO_LEGACY_FILL","status":"BESTANDEN" if ok else "NICHT_BESTANDEN","synthetic_gap_candles":rr.get("synthetic_gap_candles"),"gap_execution":assumptions.get("gap_execution")})
    else:
        regression_checks.append({"check":"FREQTRADE_SYNTHETIC_GAP_NO_LEGACY_FILL","status":"NICHT_PRUEFBAR","detail":"Freqtrade-Samplemodul fehlt"})
except Exception as exc:
    regression_checks.append({"check":"FREQTRADE_SYNTHETIC_GAP_NO_LEGACY_FILL","status":"FEHLER","detail":f"{type(exc).__name__}:{exc}"})

# 4) Same-timestamp entry/exit must not be lost in portfolio replay.
try:
    ts=pd.Timestamp("2026-02-02 10:00",tz="UTC");idx=pd.date_range(ts-pd.Timedelta(hours=1),periods=4,freq="1h",tz="UTC")
    mf=pd.DataFrame({"open":[100,100,99,99],"high":[101,101,100,100],"low":[99,98,98,98],"close":[100,99,99,99],"volume":[1000]*4},index=idx)
    tr={"broker":"eToro","strategy":"REGRESSION","symbol":"SAMEBAR","entry_time":str(ts),"exit_time":str(ts),"entry_price":100.0,"exit_price":99.0,"return_pct":-1.0,"base_cost_pct":0.0,"entry_cost_pct":0.0,"exit_cost_pct":0.0}
    rp=portfolio_replay([tr],ts-pd.Timedelta(hours=1),ts+pd.Timedelta(hours=1),"eToro","REGRESSION",{("eToro","SAMEBAR"):mf},"normal",starting_capital=10000.0)
    ok=(len(rp.get("accepted") or [])==1 and len(rp.get("rejected") or [])==0 and float(rp.get("final_equity"))<10000.0)
    regression_checks.append({"check":"SAME_TIMESTAMP_ENTRY_EXIT","status":"BESTANDEN" if ok else "NICHT_BESTANDEN","accepted":len(rp.get("accepted") or []),"rejected":len(rp.get("rejected") or []),"final_equity":rp.get("final_equity")})
except Exception as exc:regression_checks.append({"check":"SAME_TIMESTAMP_ENTRY_EXIT","status":"FEHLER","detail":f"{type(exc).__name__}:{exc}"})

# 5) A long internal stock-history hole must block ranking, never count as 0%.
try:
    a=pd.date_range("2025-01-02",periods=40,freq="B",tz="UTC");b=pd.date_range("2025-10-01",periods=40,freq="B",tz="UTC");idx=a.append(b)
    gap=pd.DataFrame({"open":100.0,"high":101.0,"low":99.0,"close":100.0,"volume":1000.0},index=idx)
    q=period_quality(gap,datetime(2025,1,1,tzinfo=timezone.utc),datetime(2025,12,31,23,59,59,tzinfo=timezone.utc),"stock","1h")
    ok=not bool(q.get("ranking_eligible")) and str(q.get("quality"))=="QUALITAETSSPERRE"
    regression_checks.append({"check":"INTERNAL_HISTORY_GAP_BLOCKS_RANKING","status":"BESTANDEN" if ok else "NICHT_BESTANDEN","quality":q.get("quality"),"coverage":q.get("calendar_coverage_pct"),"largest_gap":q.get("largest_missing_business_day_run")})
except Exception as exc:regression_checks.append({"check":"INTERNAL_HISTORY_GAP_BLOCKS_RANKING","status":"FEHLER","detail":f"{type(exc).__name__}:{exc}"})

# 6) Provider identity must reject a different company/ticker root.
try:
    ok=(symbol_identity_allowed("BAS","BAS.DE","EUR") and not symbol_identity_allowed("BAS","BMGL","EUR") and symbol_identity_allowed("AAPL","AAPL","USD"))
    regression_checks.append({"check":"STOCK_PROVIDER_IDENTITY_FAIL_CLOSED","status":"BESTANDEN" if ok else "NICHT_BESTANDEN","bas_de":symbol_identity_allowed("BAS","BAS.DE","EUR"),"bas_bmgl":symbol_identity_allowed("BAS","BMGL","EUR")})
except Exception as exc:regression_checks.append({"check":"STOCK_PROVIDER_IDENTITY_FAIL_CLOSED","status":"FEHLER","detail":f"{type(exc).__name__}:{exc}"})

# 7) Exposure matched benchmark: 25% exposure must apply only 25% of benchmark returns.
try:
    idx=pd.date_range("2026-01-01",periods=4,freq="1D",tz="UTC");beq=pd.Series([10000,11000,12100,13310],index=idx);ex=pd.Series([0.25]*4,index=idx)
    mb=exposure_matched_benchmark(beq,ex,10000.0);expected=((1+0.10*0.25)**3-1)*100;ok=abs(float(mb.get("return_pct"))-expected)<1e-9
    regression_checks.append({"check":"EXPOSURE_MATCHED_BENCHMARK","status":"BESTANDEN" if ok else "NICHT_BESTANDEN","measured":mb.get("return_pct"),"expected":expected})
except Exception as exc:regression_checks.append({"check":"EXPOSURE_MATCHED_BENCHMARK","status":"FEHLER","detail":f"{type(exc).__name__}:{exc}"})

# 8) Store a daily snapshot of the current universe outside NEXUS for future point-in-time tests.
universe_snapshot={"status":"SELBSTTEST" if args.selbsttest else "NICHT_GESPEICHERT"}
if not args.selbsttest:
    try:
        src=Path(selection_meta.get("state_path") or "")
        if src.is_file():
            snapdir=OUT_BASE/"universe_snapshots";snapdir.mkdir(parents=True,exist_ok=True)
            dest=snapdir/f"universe_{now.date().isoformat()}.json"
            if not dest.exists():shutil.copy2(src,dest)
            universe_snapshot={"status":"GESPEICHERT","path":str(dest),"sha256":sha256_file(dest),"source":str(src)}
        else:universe_snapshot={"status":"NICHT_VERFUEGBAR","source":str(src)}
    except Exception as exc:universe_snapshot={"status":"FEHLER","detail":f"{type(exc).__name__}:{exc}"}

# ---------- Output files ------------------------------------------------------
def write_csv(name, rows, fallback_fields):
    path=RUN_DIR/name
    fields=[]
    for r in rows:
        for k in r:
            if k not in fields:fields.append(k)
    fields=fields or list(fallback_fields)
    with path.open("w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore");w.writeheader();w.writerows(rows)
    return path

write_csv("portfolio_summary.csv",portfolio_rows,["broker","strategy","period"])
write_csv("strategy_cards.csv",cards,["broker","strategy"])
write_csv("results_by_symbol.csv",results_by_symbol,["broker","strategy","period","symbol"])
write_csv("test_abdeckung.csv",test_status,["broker","strategy","period","symbol","status","detail"])
write_csv("validation.csv",validation_rows,["broker","strategy","lookahead_status","recursive_status"])
write_csv("stress_scenarios.csv",scenario_rows,["broker","strategy","scenario","return_pct"])
write_csv("walkforward_quarters.csv",walkforward_rows,["broker","strategy","window","return_pct"])
write_csv("parameter_robustness.csv",robustness_rows,["broker","strategy"])
write_csv("monte_carlo.csv",mc_rows,["broker","strategy","iterations"])
write_csv("benchmarks.csv",benchmark_rows,["broker","period","benchmark","return_pct"])
write_csv("regime_performance.csv",regime_rows,["broker","strategy","period","regime"])
write_csv("strategy_correlation.csv",correlation_rows,["broker","strategy_a","strategy_b","correlation"])
write_csv("corporate_action_checks.csv",corporate_checks,["symbol","source","status"])
write_csv("trial_registry.csv",trial_registry,["broker","strategy","variant","purpose"])
write_csv("regression_checks.csv",regression_checks,["check","status","detail"])
write_csv("quality_blocks.csv",quality_blocks,["broker","strategy","period","symbol","reason","quality","coverage_pct"])
(RUN_DIR/"warnings.json").write_text(json.dumps(warnings,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
if symbol_mapping_rows:write_csv("stock_symbol_mapping.csv",symbol_mapping_rows,["etoro_symbol","provider_symbol","method"])

# Per-strategy equity curves for audit/replay.
for (broker,strategy,label),rp in portfolio_objects.items():
    safe_name="".join(c if c.isalnum() else "_" for c in f"{broker}_{strategy}_{label}")
    pd.DataFrame({"time":rp["daily_equity"].index.astype(str),"equity":rp["daily_equity"].values}).to_csv(RUN_DIR/f"equity_{safe_name}.csv",index=False)

# Trial register is complete for this run, but not for historical experiments before V5.
multiple_testing={"current_run_trial_count":len(trial_registry),"deflated_sharpe_computed":False,
                  "reason":"Ein formaler Deflated Sharpe/PBO waere ohne vollstaendiges historisches Register aller frueher ausprobierten Parameter/Strategien nicht belastbar. V5 beginnt deshalb ein explizites Trial-Register, erfindet aber keine fehlende Historie."}

metadata={"generated_at_utc":now.isoformat(),"tool":SELF.name,"tool_version":TOOL_VERSION,"tool_sha256":sha256_file(SELF),"nexus":VERSION,
          "strategies":STRATEGY_SPECS,"selection":selection_meta,"periods":[{"label":a,"start":str(b),"end":str(c)} for a,b,c in ALL_PERIODS],
          "broker_limits":{"eToro":{"position_pct":broker_position_pct("eToro"),"max_positions":broker_max_positions("eToro"),"max_trades_day":broker_max_trades_day("eToro")},
                           "OKX":{"position_pct":broker_position_pct("OKX"),"max_positions":broker_max_positions("OKX"),"max_trades_day":broker_max_trades_day("OKX")}},
          "read_only":True,"multiple_testing":multiple_testing,"universe_snapshot":universe_snapshot,"regression_checks":regression_checks,"limitations":[
              "Aktuelles NEXUS-Universum wird rueckwirkend getestet; historische Universumssnapshots fuer 2025 liegen nicht automatisch vor.",
              "eToro historische Eligibility, What-if-Kosten, echte Bid/Ask-Historie, News/Earnings und Brokerfills sind nicht vollstaendig rekonstruierbar.",
              "Crypto Momentum Rotation ist eine klar gekennzeichnete Referenzimplementierung des oeffentlich beschriebenen Regelgeruests; die exakte private Momentum-Gewichtung ist nicht veroeffentlicht.",
              "Portfolio-Drawdown nutzt Eventpunkte plus taegliche Mark-to-Market-Werte; unbekannte Intraday-Pfade zwischen Beobachtungen werden nicht erfunden.",
              "Monte Carlo verwendet Moving-Block-Bootstrap der realisierten taeglichen Portfoliorenditen und ist kein Beweis kuenftiger Renditen."],
          "compatibility":compat}
(RUN_DIR/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
(RUN_DIR/"data_manifest.json").write_text(json.dumps(data_manifest,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
(RUN_DIR/"errors.json").write_text(json.dumps(errors,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
(RUN_DIR/"multiple_testing.json").write_text(json.dumps(multiple_testing,ensure_ascii=False,indent=2),encoding="utf-8")
# ---------- Human-readable methodology / report ------------------------------
readme=f"""# NEXUS Universum-Backtest V6

Stand: {now.isoformat()}
NEXUS: {VERSION['version']}
Tool: {SELF.name} / V{TOOL_VERSION}

## Zweck
Separates, read-only Analysewerkzeug. Keine Brokerorder, keine Aenderung produktiver NEXUS-Handelsdaten und keine neue NEXUS-Version.

## V5-Korrekturen gegen den V4-Befund
- Same-Timestamp/Same-Bar-Entry und -Exit werden deterministisch verarbeitet: alte Positionen schliessen, neue Entries, danach zugehoerige Same-Bar-Exits.
- Aktienhistorie wird nicht mehr allein anhand von Anfang/Ende als vollstaendig behandelt. Interne Geschaeftstagsluecken und echte Kalenderabdeckung werden gemessen.
- Fehlende Quartale sind NICHT_PRUEFBAR und zaehlen nicht als 0%-Quartale.
- eToro-Tickerauflosung ist fail-closed. Bekannte EU-Symbole sind an exchange-qualified Provider-Ticker gebunden; ein anderer Tickerstamm wird nie nur wegen vorhandener Kurse akzeptiert.
- FMP-1h wird in kleinen 28-Tage-Fenstern abgefragt, damit Provider-Zeilenlimits kein scheinbar komplettes Jahr erzeugen.
- Ranglisten benoetigen eine Mindestzahl vollstaendig testbarer Instrumente. Teilzeiträume bleiben sichtbar, werden aber nicht als Vollperioden gewertet.
- Lookahead prueft bis zu sechs reale Instrumentserien und vergleicht Signale plus abgeleitete Indikatorwerte. Recursive Analysis vergleicht Signale und Indikatorstabilitaet.
- Der primaere Benchmarkvergleich ist exposure-matched. Ein 100%-Buy&Hold bleibt nur als Marktvergleich sichtbar.
- Corporate-Action-/Preisbasiswarnungen und Provideridentitaet koennen ein Instrument aus der Rangliste sperren.

## Hauptvergleich
- NEXUS Standard: aktuell konfigurierte NEXUS-Entrylogik.
- Freqtrade Sample: nur OKX; installierte NEXUS-Freqmode-Referenz, keine erfundene Profilmatrix.
- Turtle/Donchian: 20-Tage Breakout, 10-Tage Exit, ATR14, 2xATR Stop, Long-only.
- IBS Mean Reversion: eToro-Aktien; 25/10/2.5/IBS<0.3, Exit Close > Vortageshoch.
- Crypto Momentum Rotation: OKX-Referenz nach dem oeffentlich beschriebenen 21/63/126-Tage-Regelgeruest. Keine Behauptung einer exakten privaten Replik.
- PULSAR wird nicht aus OHLCV erfunden; historische Attention-/Quellen-/Newsbelege fehlen.

## Vergleichsregeln
- Signal erst auf abgeschlossener Kerze, Entry/Signalexit fruehestens am naechsten beobachteten Open.
- Gemeinsames Kapital je Broker, Positionscap, offene Positionen und Tageslimit.
- 2025, 2026 bis heute und zusammenhaengender Gesamtzeitraum.
- Ungueltige Datenperioden werden nicht in Portfolio, Walk-Forward oder Rangfolge aufgenommen.
- Nullvolumen wird separat als Liquiditaetswarnung ausgewiesen; eine vorhandene Kerze ist kein Liquiditaetsbeweis.

## Validierung
- Lookahead-Prefix-Test auf mehreren Instrumenten; Signal- oder Indikatorabweichungen fuehren zu NICHT_BESTANDEN.
- Recursive-Signal-/Indikatorvergleich ueber verschiedene Startup-Historien.
- Quartalsweiser Fixed-Rule Walk-Forward mit explizitem NICHT_PRUEFBAR bei fehlenden Daten.
- Parameter-Nachbarschaft nur Robustheit, keine nachtraegliche Optimierung.
- Normal/Stress/Extrem, Moving-Block-Bootstrap, Marktregime, Gewinnkonzentration, Drawdown-Dauer und Strategie-Korrelation.
- Feste Regressionen: Drawdown-Peak, Signal-vor-Fill, Freqtrade-Synthetic-Gap, Same-Bar-Entry/Exit, History-Gap-Gate, Provideridentitaet und exposure-matched Benchmark.

## Benchmarks
- eToro: gleichgewichtetes Portfolio nur der fuer den jeweiligen Zeitraum datenqualitativ gueltigen Aktien.
- OKX: BTC Buy&Hold plus gleichgewichtetes gueltiges Kryptoportfolio.
- Hauptvergleich auf den Strategiekarten: derselbe Benchmark mit der tatsaechlichen taeglichen Brutto-Exposure der Strategie.

## Grenzen
Das heutige NEXUS-Universum wird rueckwirkend getestet. Historische Universe-Snapshots, Broker-Eligibility, News/Earnings, exakte Bid/Ask-Tiefe und reale Fills sind fuer 2025 nicht vollstaendig rekonstruierbar. Ein positives Ergebnis ist deshalb Forschungsbeleg, keine Handelsfreigabe.
"""
(RUN_DIR/"README_BACKTEST_V6.md").write_text(readme,encoding="utf-8")


def hfmt(v,d=2,suffix=""):
    if v is None or (isinstance(v,float) and not math.isfinite(v)):return "–"
    try:return f"{float(v):.{d}f}{suffix}"
    except Exception:return html.escape(str(v))

def pct(v):
    if v is None:return "–"
    try:return f"{float(v):+.2f}%"
    except Exception:return "–"

def css_return(v):
    try:return "pos" if float(v)>0 else ("neg" if float(v)<0 else "")
    except Exception:return ""

def short_version(text):
    import re
    m=re.search(r"(\d+\.\d+\.\d+(?:[-A-Za-z0-9.]*)?)",str(text or ""))
    return m.group(1) if m else str(text or "unbekannt")[:18]

# Findings based only on quality-eligible portfolio cards.
findings=[]
for broker in ("eToro","OKX"):
    ranked=sorted([x for x in cards if x["broker"]==broker and x.get("rank")],key=lambda x:x["rank"])
    if ranked:
        w=ranked[0];qual=" (eingeschraenkte Datenbasis)" if w.get("quality_status")=="EINGESCHRAENKT" else ""
        findings.append(f"{broker}: Rang 1 nach Calmar, danach Sharpe ist {w['label']} mit {w['return_pct']:+.2f}% Gesamt-Portfolio, Max-DD {w['max_drawdown_pct']:+.2f}% und Sharpe {hfmt(w.get('sharpe'))}{qual}.")
        if w.get("alpha_vs_exposure_matched_pct") is not None:
            findings.append(f"{broker}: Gegen den exposure-matched Benchmark liegt {w['label']} bei {w.get('alpha_vs_exposure_matched_pct',0):+.2f} Prozentpunkten; durchschnittliche Brutto-Exposure {w.get('avg_gross_exposure_pct',0):.1f}%.")
    else:
        findings.append(f"{broker}: Keine Strategie wird gerankt, weil Datenqualitaet oder Validierung fuer den Gesamtzeitraum nicht ausreicht.")
for c in cards:
    if c.get("quality_status")=="NICHT_AUSWERTBAR":findings.append(f"{c['broker']}: {c['label']} ist fuer die Hauptwertung NICHT AUSWERTBAR ({c.get('valid_symbols',0)}/{c.get('expected_symbols',0)} Instrumente mit vollstaendiger Historie).")
    if c.get("stress_return_pct") is not None and c.get("return_pct") is not None and c["return_pct"]>0 and c["stress_return_pct"]<0:findings.append(f"{c['broker']}: {c['label']} ist im Normalfall positiv, kippt im Stressmodell aber auf {c['stress_return_pct']:+.2f}%.")
    if c.get("mc_negative_probability_pct") is not None and c["mc_negative_probability_pct"]>25:findings.append(f"{c['broker']}: {c['label']} hat im Moving-Block-Bootstrap eine Verlustwahrscheinlichkeit von {c['mc_negative_probability_pct']:.1f}% fuer einen gleich langen Renditepfad.")
    if str(c.get("lookahead_status","")).startswith("NICHT_BESTANDEN"):findings.append(f"{c['broker']}: {c['label']} faellt den Lookahead-Test und wird deshalb nicht gerankt.")
if not findings:findings=["Noch keine ausreichend vollstaendigen Portfolioergebnisse fuer eine belastbare Rangfolge."]

# Strategy cards; research variants are rendered separately.
def render_card(c):
    spec=STRATEGY_SPECS.get(str(c.get('strategy') or ''),{})
    info=''
    if spec.get('beschreibung'):
        punkte=''.join(f'<li>{html.escape(str(x))}</li>' for x in (spec.get('details') or []))
        quelle=html.escape(str(spec.get('source') or ''))
        info=("<details class='strategy-info'><summary>Was macht diese Strategie?</summary>"
              f"<p>{html.escape(str(spec.get('beschreibung')))}</p><ul>{punkte}</ul>"
              f"<p class='muted'>Belegbarkeit: {html.escape(str(spec.get('fidelity') or ''))}"
              +(f" · <a href='{quelle}' rel='noopener'>Quelle</a>" if quelle.startswith('http') else '')+"</p></details>")
    invalid=c.get("quality_status")=="NICHT_AUSWERTBAR"
    cls="invalid" if invalid else ("good" if safe_float(c.get("return_pct"))>0 else "bad")
    rank=(f"Rang {c['rank']}" if c.get("rank") else ("Forschung" if c.get("class")=="RESEARCH" else "nicht gerankt"))
    validation="OK" if str(c.get("lookahead_status","")).startswith("BESTANDEN") else str(c.get("lookahead_status") or "–")
    hero=("NICHT AUSWERTBAR" if invalid else pct(c.get("return_pct")))
    qlabel=str(c.get("quality_status") or "UNBEKANNT")
    return f"""<article class='strategy-card {cls}'>
      <div class='strategy-head'><div><span class='eyebrow'>{html.escape(c['broker'])} · {html.escape(qlabel)}</span><h3>{html.escape(c['label'])}</h3></div><span class='rank'>{html.escape(rank)}</span></div>
      <div class='hero-metric'><span>Portfolio netto 2025+2026</span><strong class='{'' if invalid else css_return(c.get('return_pct'))}'>{html.escape(hero)}</strong></div>
      <div class='year-strip'><div><span>2025</span><b class='{css_return(c.get('return_2025_pct'))}'>{pct(c.get('return_2025_pct'))}</b></div><div><span>2026 bis heute</span><b class='{css_return(c.get('return_2026_pct'))}'>{pct(c.get('return_2026_pct'))}</b></div><div><span>vs risikogleicher Benchmark</span><b class='{css_return(c.get('alpha_vs_exposure_matched_pct'))}'>{pct(c.get('alpha_vs_exposure_matched_pct'))}</b></div></div>
      <div class='metrics'>
       <div><span>Ø Brutto-Exposure</span><b>{hfmt(c.get('avg_gross_exposure_pct'),1,'%')}</b></div><div><span>100%-Benchmark</span><b>{pct(c.get('benchmark_return_pct'))}</b></div>
       <div><span>Max Drawdown</span><b>{pct(c.get('max_drawdown_pct'))}</b></div><div><span>Sharpe</span><b>{hfmt(c.get('sharpe'))}</b></div>
       <div><span>Profit Factor</span><b>{hfmt(c.get('profit_factor'))}</b></div><div><span>Trefferquote Trades</span><b>{hfmt(c.get('win_rate_pct'),1,'%')}</b></div>
       <div><span>Positive testbare Quartale</span><b>{hfmt(c.get('oos_positive_windows_pct'),1,'%')}</b></div><div><span>Nicht pruefbare Quartale</span><b>{hfmt(c.get('oos_untestable_windows'),0)}</b></div>
       <div><span>Stress</span><b class='{css_return(c.get('stress_return_pct'))}'>{pct(c.get('stress_return_pct'))}</b></div><div><span>Extrem</span><b class='{css_return(c.get('extreme_return_pct'))}'>{pct(c.get('extreme_return_pct'))}</b></div>
       <div><span>MC 5%-Quantil</span><b class='{css_return(c.get('mc_p05_return_pct'))}'>{pct(c.get('mc_p05_return_pct'))}</b></div><div><span>MC Verlustpfade</span><b>{hfmt(c.get('mc_negative_probability_pct'),1,'%')}</b></div>
       <div><span>Drawdown-Dauer</span><b>{hfmt(c.get('max_drawdown_days'),0,' Tage')}</b></div><div><span>Datenabdeckung</span><b>{hfmt(c.get('coverage_pct'),1,'%')} ({int(c.get('valid_symbols') or 0)}/{int(c.get('expected_symbols') or 0)})</b></div>
      </div>
      <div class='validation'><span>Lookahead: <b>{html.escape(validation)}</b></span><span>Recursive: <b>{html.escape(str(c.get('recursive_status') or '–'))}</b></span></div>
      {info}
    </article>"""

main_cards="";research_cards=""
for broker in ("eToro","OKX"):
    main_cards+=f"<h3 class='broker-title'>{broker}</h3><div class='strategy-grid'>"+"".join(render_card(c) for c in sorted([x for x in cards if x['broker']==broker and x.get('class')!='RESEARCH'],key=lambda x:(x.get('rank') or 999,x['label'])))+"</div>"
    rc=[x for x in cards if x['broker']==broker and x.get('class')=='RESEARCH']
    if rc:research_cards+=f"<h4>{broker}</h4><div class='strategy-grid'>"+"".join(render_card(c) for c in rc)+"</div>"

# Generic HTML table helper.
def html_table(rows, fields, labels=None):
    labels=labels or {x:x for x in fields}
    if not rows:return "<p class='muted'>Keine Daten.</p>"
    out=["<div class='scroll'><table><thead><tr>"+"".join(f"<th>{html.escape(labels.get(f,f))}</th>" for f in fields)+"</tr></thead><tbody>"]
    for r in rows:
        out.append("<tr>")
        for f in fields:
            v=r.get(f)
            if isinstance(v,float):txt=hfmt(v,3)
            else:txt=html.escape(str(v if v is not None else "–"))
            cls=""
            if f.endswith("return_pct") or f in {"return_pct","alpha_vs_primary_benchmark_pct","alpha_vs_exposure_matched_pct","max_drawdown_pct"}:cls=css_return(v)
            out.append(f"<td class='{cls}'>{txt}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>");return "".join(out)

# Correlation matrices.
def corr_html(broker):
    rr=[x for x in correlation_rows if x['broker']==broker]
    names=sorted(set(x['strategy_a'] for x in rr))
    if not names:return "<p class='muted'>Keine Korrelationsmatrix.</p>"
    lookup={(x['strategy_a'],x['strategy_b']):x['correlation'] for x in rr}
    out=["<div class='scroll'><table class='corr'><thead><tr><th>Strategie</th>"+"".join(f"<th>{html.escape(STRATEGY_SPECS.get(n,{}).get('label',n))}</th>" for n in names)+"</tr></thead><tbody>"]
    for a in names:
        out.append(f"<tr><th>{html.escape(STRATEGY_SPECS.get(a,{}).get('label',a))}</th>")
        for b in names:
            v=lookup.get((a,b));out.append(f"<td>{hfmt(v,2)}</td>")
        out.append("</tr>")
    out.append("</tbody></table></div>");return "".join(out)

shortver=short_version(VERSION.get("version"))
state="SELBSTTEST" if args.selbsttest else "HISTORISCHER TEST"
stateclass="selftest" if args.selbsttest else "okchip"
find_html="".join(f"<li>{html.escape(x)}</li>" for x in findings)
source_rows=[]
for key,spec in STRATEGY_SPECS.items():
    source_rows.append({"strategy":key,"label":spec.get("label"),"brokers":", ".join(spec.get("brokers") or []),"fidelity":spec.get("fidelity"),"parameter_hash":spec.get("parameter_hash"),"source":spec.get("source")})

report=RUN_DIR/"BACKTEST_REPORT.html"
report.write_text(f"""<!doctype html><html lang='de'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>NEXUS Backtest V6</title><style>details.strategy-info{{margin-top:10px;font-size:13px;line-height:1.45}}details.strategy-info summary{{cursor:pointer;font-weight:600}}details.strategy-info ul{{margin:6px 0 4px 18px;padding:0}}
:root{{--bg:#07101d;--panel:#0e1a2b;--panel2:#132238;--line:#273955;--text:#edf4ff;--muted:#9db0c9;--pos:#5fe09a;--neg:#ff7b89;--warn:#f5c76a;--accent:#7db7ff;--shadow:0 12px 32px #0004}}*{{box-sizing:border-box}}html{{color-scheme:dark}}body{{margin:0;background:linear-gradient(180deg,#06101d,#0a1321 38%,#08101d);color:var(--text);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}main{{max-width:1600px;margin:auto;padding:22px}}h1{{font-size:clamp(26px,3vw,40px);margin:0}}h2{{font-size:21px;margin:30px 0 12px}}h3{{margin:3px 0 0}}h4{{margin:18px 0 8px}}.muted{{color:var(--muted)}}.top{{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap}}.chips{{display:flex;gap:7px;flex-wrap:wrap;margin-top:8px}}.chip{{border:1px solid var(--line);border-radius:999px;padding:5px 9px;background:#0d192a;color:var(--muted);font-size:12px}}.okchip{{color:var(--pos)}}.selftest{{color:#c8a7ff}}.headline{{background:linear-gradient(130deg,#10233b,#0d1929);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:16px 0}}.headline b{{color:var(--accent)}}.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:9px;margin:12px 0}}.summary div{{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:10px}}.summary span{{display:block;color:var(--muted);font-size:11px}}.summary strong{{font-size:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;display:block}}.broker-title{{margin:18px 0 8px;color:#cfe2ff;font-size:15px;text-transform:uppercase;letter-spacing:.08em}}.strategy-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(315px,1fr));gap:11px}}.strategy-card{{background:var(--panel);border:1px solid var(--line);border-top:3px solid var(--muted);border-radius:14px;padding:14px;box-shadow:var(--shadow)}}.strategy-card.good{{border-top-color:var(--pos)}}.strategy-card.bad{{border-top-color:var(--neg)}}.strategy-card.invalid{{border-top-color:var(--warn);opacity:.88}}.strategy-head{{display:flex;justify-content:space-between;gap:10px}}.eyebrow{{font-size:10px;color:var(--muted);letter-spacing:.1em}}.rank{{border:1px solid var(--line);border-radius:999px;padding:4px 8px;font-size:11px;color:var(--muted);height:max-content}}.hero-metric{{display:flex;justify-content:space-between;align-items:baseline;padding:11px 0;border-bottom:1px solid var(--line)}}.hero-metric span{{color:var(--muted)}}.hero-metric strong{{font-size:29px}}.year-strip{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:9px 0}}.year-strip div,.metrics div{{background:var(--panel2);border-radius:8px;padding:7px;min-width:0}}.year-strip span,.metrics span{{display:block;color:var(--muted);font-size:10px}}.year-strip b,.metrics b{{font-size:13px}}.metrics{{display:grid;grid-template-columns:repeat(2,1fr);gap:6px}}.validation{{display:flex;gap:12px;justify-content:space-between;flex-wrap:wrap;margin-top:9px;color:var(--muted);font-size:11px}}.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}.findings{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:9px 18px}}.findings li{{margin:7px 0}}details{{margin:13px 0;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:10px 12px}}summary{{cursor:pointer;font-weight:700}}.scroll{{overflow:auto;border:1px solid var(--line);border-radius:10px;margin-top:8px}}table{{width:100%;border-collapse:collapse;background:var(--panel)}}th,td{{padding:7px 9px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}}th{{background:#17253a;position:sticky;top:0;z-index:1;font-size:11px}}th:first-child,td:first-child{{text-align:left}}tr:hover td{{background:#122037}}.corr td{{text-align:center}}code{{color:#bfdcff;overflow-wrap:anywhere}}.note{{border-left:4px solid var(--warn);background:#161e2b;padding:10px 12px;border-radius:8px;margin:10px 0}}@media(max-width:720px){{main{{padding:12px}}.metrics{{grid-template-columns:1fr 1fr}}.year-strip{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class='top'><div><h1>NEXUS Strategie-Backtest V6</h1><div class='chips'><span class='chip {stateclass}'>{state}</span><span class='chip'>NEXUS {html.escape(shortver)}</span><span class='chip'>2025 + 2026 bis heute</span><span class='chip'>Portfolio-Replay</span></div></div><div class='muted'>{html.escape(now.strftime('%d.%m.%Y %H:%M UTC'))}</div></div>
<div class='headline'><b>Hauptwertung nur nach Datenqualitaets- und Identitaets-Gate.</b> Same-Bar-Entry/Exit wird korrekt verarbeitet. Fehlende Zeitfenster zaehlen nicht als 0%. Rangfolge: Calmar, danach Sharpe, danach Rendite; Lookahead-/Recursive-Signalfehler oder unzureichende Daten schliessen eine Strategie aus.</div>
<div class='summary'><div><span>NEXUS-Version</span><strong title='{html.escape(VERSION.get('version',''))}'>{html.escape(shortver)}</strong></div><div><span>eToro Universum</span><strong>{len(stocks)} Aktien</strong></div><div><span>OKX Universum</span><strong>{len(cryptos)} Coins</strong></div><div><span>Monte Carlo</span><strong>{int(args.monte_carlo)} Läufe</strong></div><div><span>Trial-Register</span><strong>{len(trial_registry)} Varianten</strong></div></div>
<h2>Direkter Strategie-Vergleich</h2>{main_cards}
<h2>Was der Lauf aussagt</h2><ul class='findings'>{find_html}</ul>
{('<h2>Experimentelle NEXUS-Forschung</h2><div class="note">Crossover ist keine zusaetzliche produktive NEXUS-Hauptstrategie. Er wird nur separat als Forschungsvergleich gezeigt.</div>'+research_cards) if research_cards else ''}
<details open><summary>Portfoliozahlen 2025 / 2026 / Gesamt</summary>{html_table(portfolio_rows,['broker','strategy','period','return_pct','cagr_pct','max_drawdown_pct','max_drawdown_days','sharpe','sortino','calmar','profit_factor','win_rate_pct','positive_month_pct','trades','rejected_entries','avg_gross_exposure_pct','benchmark_return_pct','exposure_matched_benchmark_return_pct','alpha_vs_exposure_matched_pct','alpha_vs_primary_benchmark_pct'])}</details>
<details><summary>Stress- und Extremszenarien</summary>{html_table(scenario_rows,['broker','strategy','scenario','return_pct','max_drawdown_pct','sharpe'])}</details>
<details><summary>Walk-Forward / feste Quartalsfenster</summary>{html_table(walkforward_rows,['broker','strategy','window','status','return_pct','data_coverage_pct','valid_symbols','expected_symbols','positive_windows_pct','median_window_return_pct','windows','untestable_windows'])}</details>
<details><summary>Monte-Carlo / Moving-Block-Bootstrap</summary>{html_table(mc_rows,['broker','strategy','iterations','block_days','median_return_pct','p25_return_pct','p05_return_pct','negative_probability_pct','median_max_drawdown_pct','p05_max_drawdown_pct'])}</details>
<details><summary>Parameter-Robustheit – keine Optimierung</summary><div class='note'>Nachbarparameter werden nur auf Stabilitaet geprueft. V5 waehlt niemals den besten Nachbarwert als neue Strategie.</div>{html_table(robustness_rows,['broker','strategy','status','variants','positive_variants_pct','same_sign_as_canonical_pct','median_return_pct','min_return_pct','max_return_pct'])}</details>
<details><summary>Lookahead- und Recursive-Pruefung</summary>{html_table(validation_rows,['broker','strategy','lookahead_status','recursive_status','detail'])}</details>
<details><summary>Benchmarkvergleich</summary><div class='note'>Die Strategiekarten bewerten primaer den exposure-matched Benchmark. Der 100%-Benchmark bleibt nur als Marktvergleich sichtbar.</div>{html_table(benchmark_rows,['broker','period','benchmark','return_pct','members'])}</details>
<details><summary>Performance nach Marktregime</summary>{html_table(regime_rows,['broker','strategy','period','regime','trades','pnl','win_rate_pct'])}</details>
<details><summary>Strategie-Korrelation eToro</summary>{corr_html('eToro')}</details>
<details><summary>Strategie-Korrelation OKX</summary>{corr_html('OKX')}</details>
<details><summary>Gewinnkonzentration / Drawdown-Dauer</summary>{html_table([x for x in portfolio_rows if x.get('period')==all_label],['broker','strategy','top1_profit_concentration_pct','top5_profit_concentration_pct','top10pct_profit_concentration_pct','max_drawdown_days','expectancy_currency'])}</details>
<details><summary>Einzeltests je Instrument</summary>{html_table(results_by_symbol,['broker','strategy','period','symbol','trades','win_rate_pct','profit_factor_trade_returns','avg_trade_return_pct','best_trade_pct','worst_trade_pct','data_source'])}</details>
<details open><summary>Datenqualitaets-Sperren</summary>{html_table(quality_blocks,['broker','strategy','period','symbol','quality','coverage_pct','reason','data_from','data_to'])}</details><details><summary>Datenabdeckung / nicht getestete Faelle</summary>{html_table(test_status,['broker','strategy','period','symbol','status','detail'])}</details>
<details><summary>Provider-Identitaet / Symbolmapping</summary>{html_table(symbol_mapping_rows,['etoro_symbol','provider_symbol','identity_status','method','currency','exchange','quality','coverage_pct'])}</details><details><summary>Corporate Actions / Preisbasis</summary><div class='note'>Yahoo-Intradaydaten werden mit auto_adjust geladen. Bei anderen Providern bleibt die Preisnormalisierung unbekannt, solange kein eigener Split-/Dividendennachweis vorliegt.</div>{html_table(corporate_checks,['symbol','provider_symbol','source','price_adjustment','split_like_moves','largest_abs_daily_move_pct','status'])}</details>
<details><summary>Strategiequellen und eingefrorene Parameter</summary>{html_table(source_rows,['strategy','label','brokers','fidelity','parameter_hash','source'])}</details>
<details><summary>Multiple Testing / Trial-Register</summary><div class='note'>{html.escape(multiple_testing['reason'])}</div>{html_table(trial_registry,['broker','strategy','variant','return_pct','purpose','error'])}</details>
<details><summary>Feste Regressionstests</summary>{html_table(regression_checks,['check','status','measured','expected','synthetic_gap_candles','gap_execution','detail'])}</details>
<details><summary>Universe-Snapshot fuer kuenftige Point-in-Time-Tests</summary><pre style='white-space:pre-wrap;color:var(--muted)'>{html.escape(json.dumps(universe_snapshot,ensure_ascii=False,indent=2))}</pre></details>
<details><summary>Fehler ({len(errors)})</summary>{html_table(errors,['broker','strategy','period','symbol','error'])}</details><details><summary>Warnungen ({len(warnings)})</summary><pre style='white-space:pre-wrap;color:var(--muted)'>{html.escape(json.dumps(warnings,ensure_ascii=False,indent=2,default=str))}</pre></details>
<details><summary>Methodik und Grenzen</summary><pre style='white-space:pre-wrap;color:var(--muted)'>{html.escape(readme)}</pre></details>
<p class='muted'>Rohdaten und Audit: portfolio_summary.csv · strategy_cards.csv · trade_events_all.csv · equity_*.csv · validation.csv · quality_blocks.csv · stress_scenarios.csv · walkforward_quarters.csv · parameter_robustness.csv · monte_carlo.csv · benchmarks.csv · regime_performance.csv · strategy_correlation.csv · stock_symbol_mapping.csv · corporate_action_checks.csv · trial_registry.csv · metadata.json · data_manifest.json · errors.json · warnings.json · run.log</p>
</main></body></html>""",encoding="utf-8")

# ZIP is the audit package; cache intentionally remains outside.
zip_path=OUT_BASE/f"{RUN_PREFIX}_{stamp}_V6.zip"
with zipfile.ZipFile(zip_path,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(RUN_DIR.rglob("*")):
        if p.is_file():z.write(p,p.relative_to(RUN_DIR.parent))

latest_prefix="LETZTER_SELBSTTEST_V6" if args.selbsttest else "LETZTER_BACKTEST_V6"
for link,target in ((OUT_BASE/f"{latest_prefix}.html",report),(OUT_BASE/f"{latest_prefix}.zip",zip_path)):
    try:
        if link.exists() or link.is_symlink():link.unlink()
        link.symlink_to(target)
    except Exception:
        try:shutil.copy2(target,link)
        except Exception:pass

log("")
log("="*86);log("V6 FERTIG");log("="*86)
for broker in ("eToro","OKX"):
    ranked=sorted([x for x in cards if x["broker"]==broker and x.get("rank")],key=lambda x:x["rank"])
    if ranked:
        w=ranked[0];log(f"{broker} Rang 1: {w['label']} | Gesamt {w['return_pct']:+.2f}% | DD {w['max_drawdown_pct']:+.2f}% | Sharpe {hfmt(w.get('sharpe'))}")
log(f"HTML: {report}");log(f"ZIP:  {zip_path}");log(f"SHA256 ZIP: {sha256_file(zip_path)}");log("NEXUS selbst wurde nicht veraendert; keine Brokerorder wurde gesendet.")

if args.html_oeffnen and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    try:
        import subprocess;subprocess.Popen(["xdg-open",str(report)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except Exception:pass
PY
