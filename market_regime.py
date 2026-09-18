"""Einfacher Marktregime-Filter aus SPY/QQQ/IWM und VIX.

Wird gecacht und dient als Risikofilter, nicht als alleiniger Kaufgrund.

Seit v8.1.3 wird das zuletzt ermittelte Regime zusaetzlich in einer kleinen
Datei abgelegt. Grund: Aktien und Krypto laufen in GETRENNTEN Prozessen. Der
Kryptoprozess soll die Marktphase eines Trades mitschreiben koennen, darf
dafuer aber weder einen Downloadlauf ausloesen noch im Kaufpfad blockieren.
``letzter_bekannter()`` liest deshalb nur -- es rechnet und laedt nie.
"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json
import logging
import os
import time
import config

logger = logging.getLogger(__name__)

CACHE_DATEI = "market_regime.json"


def _wurzel() -> Path:
    return Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                or Path(__file__).resolve().parent)


@dataclass
class MarketRegime:
    name:str="UNKNOWN"; score:int=50; vix:float=0.0; details:str=""; checked:bool=False

class MarketRegimeClient:
    def __init__(self):self.cache=(0,MarketRegime())
    def get(self):
        if time.time()-self.cache[0]<float(getattr(config,"MARKET_REGIME_CACHE_MINUTES",20))*60:return self.cache[1]
        try:
            import yfinance as yf
            score=50; parts=[]
            for sym in ["SPY","QQQ","IWM"]:
                d=yf.download(sym,period="3mo",interval="1d",progress=False,auto_adjust=True,threads=False)
                if len(d)>=50:
                    c=d["Close"]; c=c.iloc[:,0] if getattr(c,"ndim",1)>1 else c
                    last=float(c.iloc[-1]); sma20=float(c.rolling(20).mean().iloc[-1]); sma50=float(c.rolling(50).mean().iloc[-1])
                    if last>sma20>sma50:score+=10
                    elif last<sma20<sma50:score-=12
                    parts.append(f"{sym}:{'+' if last>sma20 else '-'}")
            v=yf.download("^VIX",period="10d",interval="1d",progress=False,auto_adjust=True,threads=False)
            vix=0.0
            if len(v):
                c=v["Close"]; c=c.iloc[:,0] if getattr(c,"ndim",1)>1 else c; vix=float(c.iloc[-1])
                if vix>=35:score-=25
                elif vix>=25:score-=15
                elif vix<=16:score+=8
            score=max(0,min(100,score)); name="RISK_ON" if score>=65 else "RISK_OFF" if score<=35 else "NEUTRAL"
            out=MarketRegime(name,score,vix," ".join(parts),True)
        except Exception as exc:out=MarketRegime("UNKNOWN",50,0,str(exc),False)
        self.cache=(time.time(),out)
        if out.checked:
            _merke(out)
        return out


def _merke(regime: MarketRegime) -> None:
    """Das ermittelte Regime fuer andere Prozesse ablegen (best effort)."""
    try:
        from safe_persistence import best_effort_json
        best_effort_json(_wurzel() / CACHE_DATEI,
                         {"name": regime.name, "score": regime.score,
                          "vix": regime.vix, "gemessen_am": time.time()},
                         label="Marktregime-Cache", durable=False)
    except Exception:
        logger.debug("Marktregime nicht ablegbar", exc_info=True)


def letzter_bekannter(max_alter_minuten: float = 180.0) -> str:
    """Der zuletzt gemessene Regimename -- ohne Netzzugriff, ohne Blockieren.

    Gibt "" zurueck, wenn nichts bekannt oder der Wert zu alt ist. Leer heisst
    ausdruecklich "nicht bekannt": eine geratene Marktphase wuerde jede
    Auswertung nach Marktphase still verfaelschen.
    """
    try:
        datei = _wurzel() / CACHE_DATEI
        if not datei.exists():
            return ""
        daten = json.loads(datei.read_text(encoding="utf-8"))
        alter = (time.time() - float(daten.get("gemessen_am") or 0)) / 60.0
        if alter > max(1.0, float(max_alter_minuten)):
            return ""
        name = str(daten.get("name") or "")
        return "" if name in ("", "UNKNOWN") else name
    except Exception:
        logger.debug("Marktregime-Cache nicht lesbar", exc_info=True)
        return ""
