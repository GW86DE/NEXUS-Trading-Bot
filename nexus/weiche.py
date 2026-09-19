"""Import-Weiche fuer umgezogene Module (10.8.0, Schritt 1).

Ein Modul, das von der Wurzel nach ``nexus/<schicht>/`` umzieht, laesst am
alten Ort eine Datei mit zwei Zeilen zurueck::

    from nexus.weiche import umleiten
    umleiten(__name__, "nexus.domain.ledger_result")

Python fuehrt die alte Datei aus, ``umleiten`` laedt das neue Modul und traegt
es unter dem ALTEN Namen in ``sys.modules`` ein. Der Importmechanismus liefert
nach der Ausfuehrung das Objekt aus ``sys.modules`` zurueck -- also das neue
Modul. Damit gilt ohne Anpassung der Aufrufer:

* ``import ledger_result`` und ``from ledger_result import x`` funktionieren,
* ``ledger_result is nexus.domain.ledger_result`` (EIN Modulobjekt, kein Zwilling),
* ``monkeypatch.setattr(ledger_result, ...)`` in Tests wirkt im echten Modul,
* Installer-Dateilisten, die die alte Datei erwarten, finden sie weiterhin.

Die Weiche ist bewusst dumm: kein Lazy-Import, kein Proxy, keine Magie.
"""
from __future__ import annotations

import importlib
import sys
from types import ModuleType


def umleiten(alter_name: str, neuer_name: str) -> ModuleType:
    modul = importlib.import_module(neuer_name)
    sys.modules[alter_name] = modul
    return modul
