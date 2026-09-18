"""Jedes Modul muss tatsaechlich importierbar sein.

WARUM DIESER TEST EXISTIERT
===========================
In v5.12.0 importierte live_trader.py zwei Funktionen, die es im Zielmodul
nicht gab. Der Bot liess sich dadurch nicht starten -- und der Volltest war
trotzdem gruen.

Grund: compileall prueft nur die Syntax. Ob ein "from x import y" den Namen y
wirklich findet, zeigt sich erst beim echten Import. Und keine Testreihe
importierte das Hauptmodul.

Dieser Test schliesst die Luecke. Er laedt jedes Modul im Projekt wirklich.
Oberflaechenmodule werden uebersprungen, wenn tkinter fehlt (Server/Container),
denn das ist eine Eigenschaft der Umgebung und kein Programmfehler.
"""
from __future__ import annotations

import importlib
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Module, die beim Import bewusst etwas tun (Menues, Netzwerk) und deshalb
# nicht blind geladen werden duerfen.
AUSGENOMMEN = {
    "volltest", "conftest", "setup",
}


def _hat_tkinter() -> bool:
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


def _modulnamen():
    for pfad in sorted(ROOT.glob("*.py")):
        name = pfad.stem
        if name.startswith("_") or name in AUSGENOMMEN or name.startswith("test"):
            continue
        yield name, pfad
    for pfad in sorted((ROOT / "broker").glob("*.py")):
        if pfad.stem.startswith("_"):
            continue
        yield f"broker.{pfad.stem}", pfad


class ModulImportTest(unittest.TestCase):

    # Module koennen tkinter auch INDIREKT ueber ein anderes Projektmodul
    # ziehen. Eine reine Textsuche in der eigenen Datei reicht deshalb nicht --
    # es wird eine Ebene tief mitgeprueft.
    def _braucht_tkinter(self, pfad, tiefe: int = 1) -> bool:
        try:
            quelle = pfad.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return False
        if "import tkinter" in quelle or "from tkinter" in quelle:
            return True
        if tiefe <= 0:
            return False
        import re as _re
        for treffer in _re.findall(r"^\s*(?:from|import)\s+([a-z_][a-z0-9_]*)", quelle, _re.M):
            nachbar = ROOT / f"{treffer}.py"
            if nachbar.exists() and nachbar != pfad:
                if self._braucht_tkinter(nachbar, tiefe - 1):
                    return True
        return False

    def test_jedes_modul_laesst_sich_importieren(self):
        os.environ.setdefault("TRADINGBOT_TEST_STATE_DIR", os.environ.get("TMPDIR", "/tmp"))
        tk_da = _hat_tkinter()
        fehler = []
        uebersprungen = []

        for name, pfad in _modulnamen():
            if not tk_da and self._braucht_tkinter(pfad):
                uebersprungen.append(name)
                continue
            with self.subTest(modul=name):
                try:
                    importlib.import_module(name)
                except Exception as exc:
                    fehler.append(f"{name}: {type(exc).__name__}: {exc}")

        if uebersprungen:
            print(f"\n  Ohne tkinter uebersprungen: {len(uebersprungen)} Oberflaechenmodule")
        self.assertFalse(
            fehler,
            "Diese Module lassen sich nicht importieren:\n  " + "\n  ".join(fehler),
        )

    def test_hauptmodul_ist_importierbar(self):
        """Ausdruecklich eigener Test: Ohne live_trader startet der Bot nicht."""
        try:
            importlib.import_module("live_trader")
        except Exception as exc:
            self.fail(f"live_trader.py nicht importierbar -- der Bot wuerde nicht starten: {exc}")


if __name__ == "__main__":
    unittest.main()
