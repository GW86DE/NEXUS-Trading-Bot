"""Trainiert automatisch ein ML-Kandidatenmodell, ohne das aktive Modell zu ersetzen."""
from __future__ import annotations
import json, warnings
from pathlib import Path
from datetime import datetime, timezone
warnings.filterwarnings('ignore', category=UserWarning)
import config
from data import fetch_history_yfinance
from ml_model import train_model_multi

def main():
    items=list(config.STOCK_SYMBOLS)
    limit=int(getattr(config,'ML_TRAIN_SYMBOL_LIMIT',30) or 0)
    if limit>0: items=items[:limit]
    data={}
    for item in items:
        symbol=item['symbol']
        try:
            df=fetch_history_yfinance(symbol,asset_type='stock',period='2y',interval='1h')
            data[symbol]={'df':df,'currency':item.get('currency','USD'),'asset_type':'stock','underdog':bool(item.get('underdog',False))}
        except Exception:
            continue
    if not data: raise RuntimeError('Keine Trainingsdaten geladen.')
    candidate=Path(__file__).with_name('ml_model_candidate.joblib')
    _,acc=train_model_multi(data,horizon=1,model_path=str(candidate),verbose=True)
    Path(__file__).with_name('ml_candidate_status.json').write_text(json.dumps({'time':datetime.now(timezone.utc).isoformat(),'symbols':len(data),'accuracy':float(acc),'candidate':candidate.name,'activated':False},indent=2),encoding='utf-8')
    print('Kandidatenmodell erstellt:',candidate.name)
    print('Das aktive Modell wurde NICHT ersetzt.')
if __name__=='__main__':main()
