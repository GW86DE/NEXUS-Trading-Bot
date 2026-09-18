"""Konsolenmenue fuer NEXUS-Intelligence; die GUI bleibt optional."""
from __future__ import annotations
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PYTHON=sys.executable

def run(name): subprocess.call([PYTHON,str(ROOT/name)],cwd=str(ROOT))

def main():
    while True:
        print('\n'+'='*66)
        print('TRADINGBOT 8.1.1 NEXUS – INTELLIGENCE')
        print('='*66)
        print(' 1  Intelligence-/Kosten-Status')
        print(' 2  Alpha Vantage für Quartalszahlen einrichten')
        print(' 3  Earnings-Kalender / Status')
        print(' 4  News- und Krisencheck')
        print(' 5  Intelligence-Einstellungen (GUI)')
        print(' 6  Intelligence-Offline-Tests')
        print(' 7  Backtest mit Kosten')
        print(' 8  Walk-Forward')
        print(' 9  ML kostenbewusst neu trainieren')
        print('10  Entscheidungsjournal')
        print(' 0  Zurück')
        w=input('Auswahl: ').strip()
        if w in ('0',''): return
        mapping={'1':'intelligence_status.py','2':'alpha_vantage_setup.py','3':'earnings_status.py',
                 '4':'news_check.py','5':'intelligence_einstellungen.py','6':'tests_intelligence.py',
                 '7':'run_backtest.py','8':'run_walkforward.py','9':'train_model.py','10':'decision_journal_status.py'}
        if w in mapping: run(mapping[w])
        else: print('Ungültige Auswahl.')

if __name__=='__main__': main()
