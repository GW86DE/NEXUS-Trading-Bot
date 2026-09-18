#!/usr/bin/env python3
"""Rein lesender Buchungsnachweis; keine Brokeraufrufe, Orders oder Migration."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys
from etoro_accounting_resolution import project, read_ledger


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--domain',default='',help='Optional: etoro:demo:<Kontofingerprint>')
    parser.add_argument('--output',type=Path,help='Neuer Diagnosebericht, wird niemals ueberschrieben')
    args=parser.parse_args(argv)
    root=args.source.expanduser().resolve()
    try:
        data=json.loads((root/'etoro_reconciliation.json').read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        data={'storage_error':type(exc).__name__}
    report=project(data,read_ledger(root/'decision_history.sqlite'),domain=args.domain)
    text=json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        try:
            with args.output.expanduser().open('x',encoding='utf-8') as handle:
                handle.write(text+'\n')
        except OSError as exc:
            print(f'Bericht nicht geschrieben: {exc}',file=sys.stderr);return 2
    print(text)
    return 1 if report['storage_error'] or report['ledger_error'] else 0


if __name__=='__main__':
    raise SystemExit(main())
