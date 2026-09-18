"""Read-only daily/all-time confirmed trading results, strictly domain-scoped.

Percent = confirmed net / entry capital of the same closed trades. It is NOT
account return, TWR, annualized yield or a sum of individual trade percentages.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import sqlite3
from zoneinfo import ZoneInfo

from broker_display_context import context
from ledger_result import confirmed_net, finite_number, is_residual, entry_capital

ZONE = ZoneInfo('Europe/Berlin')


class _Totals:
    def __init__(self,rows=()):
        self.known=self.unknown=self.wins=self.losses=0
        self.net=self.capital=Decimal(0);self.capital_known=True
        self.add(rows)

    def add(self,rows):
        for row in rows:
            if not confirmed_net(row):self.unknown+=1;continue
            self.known+=1;value=Decimal(str(row['netto_pnl']));self.net+=value
            self.wins+=int(value>0);self.losses+=int(value<0)
            cost=entry_capital(row)
            if cost is None:self.capital_known=False
            else:self.capital+=Decimal(str(cost))

    def metric(self):
        return dict(net=finite_number(self.net) if self.known or not self.unknown else None,
            known=self.known,unknown=self.unknown,complete=self.unknown==0,
            capital=finite_number(self.capital) if self.capital_known and self.known else None,
            percent=finite_number(100*self.net/self.capital) if self.capital_known and self.capital>0 else None,
            wins=self.wins,losses=self.losses)


def _metric(rows):
    return _Totals(rows).metric()


def aggregate(rows, *, now=None, days=90):
    now=now or datetime.now(timezone.utc)
    if now.tzinfo is None: raise ValueError('Zeit ohne Zeitzone')
    today=now.astimezone(ZONE).date(); days=max(1,min(365,int(days)))
    start=today-timedelta(days=days-1)
    groups={}; excluded=0
    for row in rows:
        if is_residual(row):continue
        if row.get('superseded_by') is not None or row.get('reconciliation_status') in {
                'DISMISSED','ACCOUNT_ASSET_CONFIRMED'}:
            continue
        c=context(row)
        if not c['bound'] or c['currency']=='UNKNOWN':
            excluded+=1; continue
        g=groups.setdefault(c['group_key'],dict(context=c,closed=[],open_count=0))
        if not row.get('ausgestiegen_am'):
            g['open_count']+=1; continue
        try:
            stamp=datetime.fromisoformat(str(row['ausgestiegen_am']).replace('Z','+00:00'))
            if stamp.tzinfo is None or stamp>now: raise ValueError('Keine sichere Abschlusszeit')
            day=stamp.astimezone(ZONE).date()
        except (ValueError,TypeError):
            excluded+=1; continue
        g['closed'].append((day,row))
    output=[]
    for key,g in sorted(groups.items()):
        ordered=sorted(g['closed'],key=lambda x:(x[0],int(x[1]['trade_id'])))
        all_rows=[r for _,r in ordered]
        by_day={}
        for day,row in ordered: by_day.setdefault(day,[]).append(row)
        running=_Totals(r for day,r in ordered if day<start)
        series=[]
        for offset in range(days):
            day=start+timedelta(days=offset); subset=by_day.get(day,[])
            daily=_metric(subset); running.add(subset); cumulative=running.metric()
            series.append(dict(day=day.isoformat(),daily=daily,cumulative=cumulative))
        output.append(dict(**g['context'],open_count=g['open_count'],total=_metric(all_rows),
            today=_metric(by_day.get(today,[])),series=series))
    return dict(as_of=now.isoformat(),timezone='Europe/Berlin',days=days,groups=output,
        excluded_rows=excluded,method='Bestaetigte realisierte Nettoergebnisse nach Gebuehren. '
        'Prozent = Netto / eingesetztes Einstiegskapital derselben abgeschlossenen Trades. '
        'Keine Kontorendite; offene Buchgewinne/-verluste sind nicht enthalten. '
        'Bei unbekannten Ergebnissen werden nur bestaetigte Teilsummen ausgewiesen.')


def snapshot(*, days=90):
    from decision_analytics import db_pfad
    path=db_pfad()
    if not path.is_file(): return aggregate([],days=days)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as con:
        con.row_factory=sqlite3.Row; con.execute('PRAGMA query_only=ON'); con.execute('BEGIN')
        if not con.execute("SELECT 1 FROM sqlite_master WHERE name='trades' AND type='table'").fetchone():
            return aggregate([],days=days)
        rows=[dict(r) for r in con.execute('SELECT * FROM trades')]
    return aggregate(rows,days=days)
