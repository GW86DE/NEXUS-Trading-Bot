"""Authenticated, bounded access to stored source receipts; never fetch a URL."""
from html import escape
import json
import re
import sqlite3
import time


def source_receipt(symbol, identifier):
    from pulsar import research, control
    if not research.TICKER.fullmatch(symbol) or not re.fullmatch(r"[0-9a-f]{64}", identifier):
        raise ValueError("Ungueltige Belegkennung")
    path = research.path()
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("Gespeicherter Quellenbeleg fehlt")
    budget = 16_000_000
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as con:
        con.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 2
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        con.execute("BEGIN")
        selections = [("SELECT payload FROM cache WHERE key='top5' AND length(payload)<=8000000", ())]
        for (aid, size) in con.execute("SELECT id,length(payload) FROM assessments WHERE symbol=? ORDER BY at DESC LIMIT 40", (symbol,)):
            if size <= 4_000_000:
                selections.append(("SELECT payload FROM assessments WHERE id=?", (aid,)))
        for sql, params in selections:
            row = con.execute(sql, params).fetchone()
            if not row or len(row[0]) > budget:
                continue
            budget -= len(row[0])
            value = json.loads(row[0])
            cards = value if isinstance(value, list) else [value]
            for card in cards:
                if not isinstance(card, dict) or card.get("symbol") != symbol:
                    continue
                for source in card.get("sources") or []:
                    if source.get("id") != identifier:
                        continue
                    verified = control.digest(research.facts(source)) == identifier
                    if not verified:
                        raise ValueError("Pruefsumme des gespeicherten Belegs stimmt nicht")
                    from NEXUS_10_Diagnose import Scrubber
                    scrub = Scrubber()
                    scrub.learn(source)
                    # Also mask configured provider secrets if echoed as free text.
                    import live_settings
                    for getter in (live_settings.fmp_key, live_settings.massive_key):
                        secret = getter()
                        if secret:
                            scrub.values.add(str(secret))
                    public = scrub.clean(source)
                    body = json.dumps(public, ensure_ascii=False, indent=2, allow_nan=False)
                    limited = len(body) > 500_000
                    return {"symbol": symbol, "source_id": identifier, "verified": True,
                        "redacted": scrub.replacements > 0, "truncated": limited,
                        "provider": public.get("provider"), "kind": public.get("kind"),
                        "observed_at": public.get("observed_at"), "text": body[:500_000]}
    raise FileNotFoundError("Beleg in den gespeicherten aktuellen/jüngsten Bewertungen nicht gefunden")


def html_receipt(symbol, identifier):
    row = source_receipt(symbol, identifier)
    note = "Anzeige gekürzt; der ursprüngliche Beleg bleibt gespeichert." if row["truncated"] else "Gespeicherter Quellenbeleg vollständig angezeigt."
    return ('<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>NEXUS Quellenbeleg</title><link rel="stylesheet" href="/static/app.css"><link rel="stylesheet" href="/static/nexus.css">'
        '<main class="container"><p><a href="/pulsar">Zurück zu PULSAR</a></p><section class="panel"><h1>' +
        escape(str(row["provider"])) + ' · ' + escape(symbol) + '</h1><p>' + escape(str(row["kind"])) +
        '</p><p>Prüfsumme des gespeicherten Originals bestätigt. Dieser Aufruf liest vorhandene Daten in NEXUS.</p><p>' +
        escape(note) + (' Zugangsdaten wurden maskiert.' if row["redacted"] else '') +
        '</p><p class="small">Beleg: ' + escape(identifier) + '</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' +
        escape(row["text"]) + '</pre></section></main></html>')
