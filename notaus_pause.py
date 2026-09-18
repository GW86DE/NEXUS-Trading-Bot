"""Telegram-unabhaengiger Not-Aus fuer neue Kaeufe.

Der sichere Notfallzustand ist PAUSIERT: keine neuen Kaeufe, waehrend bereits
bestehende Positionen weiterhin durch die normale Schutz-/Exitlogik betreut
werden duerfen. Das ist absichtlich nicht GESTOPPT, weil GESTOPPT auch
automatische Schutzverkaeufe unterbindet.
"""
from __future__ import annotations

from pathlib import Path

from bot_zustand import BotZustand, PAUSIERT

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "bot_zustand.json"


def set_emergency_pause(state_path: str | Path = STATE, source: str = "notaus_local") -> dict:
    bz = BotZustand(str(state_path))
    bz.setze(
        PAUSIERT,
        "lokaler Telegram-unabhaengiger Not-Aus: neue Kaeufe blockiert",
        source,
    )
    return bz.momentaufnahme()


if __name__ == "__main__":
    state = set_emergency_pause()
    print("NOT-AUS AKTIV: Zustand = PAUSIERT")
    print("Neue Kaeufe sind blockiert; bestehende Schutz-/Exitlogik bleibt aktiv.")
    print(f"Quelle: {state.get('geaendert_von', '-')}")
