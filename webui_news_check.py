"""Nicht-interaktiver Markt-/Krisencheck fuer den WebUI-Analyseworker.

Die Bewertung bleibt vollstaendig im vorhandenen NachrichtenFilter. Dieses
kleine Frontend ersetzt nur das Konsolenmenue, das in einem Browserauftrag
keine Eingabe entgegennehmen kann.
"""
from __future__ import annotations

from news_check import _kopf
from news_filter import NachrichtenFilter


def main() -> None:
    _kopf()
    print("MARKTLAGE / KRISENCHECK")
    print("-" * 74)
    lage = NachrichtenFilter().marktlage()
    print(lage.details() if hasattr(lage, "details") else lage)
    print()


if __name__ == "__main__":
    main()
