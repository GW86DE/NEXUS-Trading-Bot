"""9.5.6 -- Coin- und Aktiennamen im Logbuch hervorheben.

Georgs Wunsch: "Kannst du im Logbuch die Namen von den Krypto oder Aktien
hervorheben? Also ZAMA oder LINK. Fett schreiben oder minimal groesser oder in
Farbe, dass man sofort erkennt, um welche Aktie oder welchen Coin es geht."

Die Falle dabei: ein Muster fuer Grossbuchstaben waere naheliegend und falsch.
Im Systemprotokoll stehen INFO, WARNING, OKX, USDC, CASH, ACCOUNT_ASSET,
BOT_MANAGED und HANDEL -- die duerfen nicht mitleuchten. Hervorgehoben wird
deshalb nur, was der Server als gefuehrtes Instrument kennt.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
WURZEL = Path(__file__).resolve().parents[1]
LOGBOOK_JS = WURZEL / "webui" / "static" / "logbook.js"


def hervorheben(zeilen, symbole):
    """Fuehrt die echten Funktionen aus logbook.js in node aus."""
    node = shutil.which("node")
    if not node:
        pytest.skip("node nicht verfuegbar")
    quelle = LOGBOOK_JS.read_text(encoding="utf-8")
    # Nur die beiden Funktionen herausloesen; der Rest braucht ein Dokument.
    anfang = quelle.index("let bekannteSymbole")
    ende = quelle.index("function qs()")
    ausschnitt = quelle[anfang:ende]
    skript = ausschnitt + """
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]));}
const eingabe = JSON.parse(process.argv[1]);
symbolMusterBauen(eingabe.symbole);
console.log(JSON.stringify(eingabe.zeilen.map(z => symboleHervorheben(esc(z)))));
"""
    lauf = subprocess.run(
        [node, "-e", skript, json.dumps({"zeilen": zeilen, "symbole": symbole})],
        capture_output=True, text=True, timeout=30)
    assert lauf.returncode == 0, lauf.stderr
    return json.loads(lauf.stdout)


SYMBOLE = ["ZAMA", "LINK", "AVGO", "XLM", "BNB", "ADA", "AMP"]


def test_der_coinname_wird_hervorgehoben():
    """Die echte Zeile aus dem Logbuch vom 03.09.2026."""
    zeile = ("2026-09-03 09:58:25 INFO crypto_engine: Krypto ZAMA: Restmenge "
             "0.01685 ZAMA (rund 0.0009 USDC) liegt unter der Mindestgroesse")
    (ergebnis,) = hervorheben([zeile], SYMBOLE)
    assert ergebnis.count('<span class="sym">ZAMA</span>') == 2


def test_symbol_im_instrument_wird_erkannt():
    """LINK-USDC: der Wert steckt im Instrumentnamen."""
    zeile = ("2026-09-03 09:55:03 WARNING broker.okx: OKX-Schutzorder "
             "LINK-USDC abgelehnt: OKX lehnt die Order ab (51008)")
    (ergebnis,) = hervorheben([zeile], SYMBOLE)
    assert '<span class="sym">LINK</span>-USDC' in ergebnis


def test_stoerwoerter_leuchten_nicht_mit():
    """Der eigentliche Grund fuer die serverseitige Liste."""
    zeile = ("2026-09-03 09:58:26 INFO crypto_engine: OKX-Guthaben: "
             "9x ACCOUNT_ASSET, 2x BOT_MANAGED, 3x CASH | WARNING HANDEL USDC")
    (ergebnis,) = hervorheben([zeile], SYMBOLE)
    assert "<span" not in ergebnis, (
        f"Kein Wort dieser Zeile ist ein Instrument: {ergebnis}")


def test_html_bleibt_escaped_und_unversehrt():
    """Der Text wird escaped, BEVOR hervorgehoben wird."""
    zeile = 'Angriff <script>alert(1)</script> und A & B mit AMP'
    (ergebnis,) = hervorheben([zeile], SYMBOLE)
    assert "<script>" not in ergebnis
    assert "&lt;script&gt;" in ergebnis
    # AMP als eigenes Wort wird hervorgehoben ...
    assert '<span class="sym">AMP</span>' in ergebnis
    # ... aber die Entity &amp; bleibt heil.
    assert "&amp;" in ergebnis and "&<span" not in ergebnis


def test_ohne_symbolliste_bleibt_alles_unveraendert():
    """Faellt die Liste aus, ist die Seite trotzdem benutzbar."""
    zeile = "2026-09-03 INFO irgendwas mit ZAMA"
    (ergebnis,) = hervorheben([zeile], [])
    assert "<span" not in ergebnis
    assert "ZAMA" in ergebnis


def test_serverliste_enthaelt_keine_stoerwoerter():
    """Die Quelle der Liste selbst -- nicht nur ihre Anwendung."""
    from webui.state import bekannte_symbole

    symbole = set(bekannte_symbole())
    assert symbole, "Ohne Symbole gaebe es nichts hervorzuheben"
    for wort in ("INFO", "WARNING", "ERROR", "OKX", "USDC", "CASH",
                 "HANDEL", "KRYPTO", "MARKT", "ACCOUNT_ASSET"):
        assert wort not in symbole, f"{wort} darf nicht als Instrument gelten"
