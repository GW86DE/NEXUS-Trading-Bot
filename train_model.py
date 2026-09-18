"""Kostenbewusstes ML-Training ueber mehrere Aktien statt nur ein Symbol."""
from __future__ import annotations
import warnings
warnings.filterwarnings("ignore", category=UserWarning)

import config
import json
from pathlib import Path
from datetime import datetime, timezone
from data import fetch_history_yfinance
from ml_model import train_model_multi


def main():
    items=list(config.STOCK_SYMBOLS)
    limit=int(getattr(config,"ML_TRAIN_SYMBOL_LIMIT",30) or 0)
    if limit>0: items=items[:limit]
    print(f"Lade Trainingsdaten fuer bis zu {len(items)} Aktien ...")
    data={}
    for i,item in enumerate(items,1):
        symbol=item["symbol"]
        try:
            df=fetch_history_yfinance(symbol,asset_type="stock",period="2y",interval="1h")
            data[symbol]={"df":df,"currency":item.get("currency","USD"),
                          "asset_type":"stock","underdog":bool(item.get("underdog",False))}
            print(f"[{i:3d}/{len(items)}] {symbol:7s} {len(df):5d} Balken")
        except Exception as exc:
            print(f"[{i:3d}/{len(items)}] {symbol:7s} UEBERSPRUNGEN: {exc}")
    print("\nTrainiere gemeinsames Modell mit chronologischem Split je Symbol ...\n")
    model,acc=train_model_multi(data,horizon=1,model_path=config.MODEL_PATH,verbose=True)
    Path(__file__).with_name("ml_training_status.json").write_text(json.dumps({"time":datetime.now(timezone.utc).isoformat(),"symbols":len(data),"accuracy":float(acc)},indent=2),encoding="utf-8")
    print("\nWICHTIG:")
    print("Accuracy allein reicht nicht. Erst Backtest + Walk-Forward NACH Kosten entscheiden,")
    print("ob USE_ML_FILTER aktiviert werden sollte.")

if __name__=="__main__": main()
