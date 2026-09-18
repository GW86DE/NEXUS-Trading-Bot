"""Historical follow-up prices for decision analytics.

A due 1h observation must not accidentally use "whatever the price is now" if
the bot was offline for a day. We therefore fetch a bounded historical series
once per symbol and select the bar nearest the intended target timestamp.
Only a few due records are processed per scanner cycle.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

import pandas as pd

from decision_analytics import due_outcomes, set_outcome
from instrument_identity import canonical_key

logger=logging.getLogger(__name__)


def _target(value):
    d=datetime.fromisoformat(str(value).replace("Z","+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _price_at(df, target_at):
    if df is None or len(df)==0 or "close" not in df.columns:
        return 0.0
    try:
        idx=pd.to_datetime(df.index,utc=True,errors="coerce")
        valid=~idx.isna()
        if not valid.any():
            return 0.0
        work=df.loc[valid].copy(); idx=idx[valid]
        target=pd.Timestamp(target_at).tz_convert("UTC") if pd.Timestamp(target_at).tzinfo else pd.Timestamp(target_at,tz="UTC")
        distances=(idx-target).to_series(index=work.index).abs()
        pos=distances.values.argmin()
        # Stocks can legitimately have a weekend/overnight gap. More than 3
        # days means the requested historical point is not trustworthy.
        if distances.iloc[pos] > pd.Timedelta(days=3):
            return 0.0
        return float(work["close"].iloc[pos] or 0)
    except Exception:
        return 0.0


def update_due(broker,instrument_by_symbol:dict,limit:int=6)->int:
    rows=due_outcomes(limit=max(1,int(limit)))
    if not rows:return 0
    history_cache={};done=0
    for row in rows:
        sym=str(row.get("symbol") or "").upper()
        asset_type=str(row.get("asset_type") or "stock").lower()
        try:
            identity=canonical_key(sym, asset_type)
        except Exception as exc:
            logger.debug("Outcome-Identitaet ungueltig %s/%s: %s", sym, asset_type, exc)
            continue
        inst=instrument_by_symbol.get(identity)
        if inst is None:
            continue
        try:
            if identity not in history_cache:
                history_cache[identity]=broker.historie(
                    inst,"10 D","1 hour",nur_handelszeiten=getattr(inst,"use_rth",True)
                )
            px=_price_at(history_cache[identity],_target(row["target_at_utc"]))
            if px>0 and set_outcome(
                row["decision_id"],row["horizon"],px,
                source=f"{getattr(broker,'name','broker')}:historical-nearest",
            ):
                done+=1
        except Exception as exc:
            logger.debug("Outcome %s/%s nicht aktualisiert: %s",sym,row.get("horizon"),exc)
    return done
