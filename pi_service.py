"""systemd-Einstiegspunkt fuer Raspberry Pi 5."""
from __future__ import annotations

import os
import signal
from pathlib import Path

from instance_lock import SingleInstanceLock, InstanceAlreadyRunning
from broker import AuthentifizierungsFehler
from pi_service_event import consume_problem

ROOT = Path(__file__).resolve().parent


def _term(_signum, _frame):
    raise KeyboardInterrupt


def _starte_handel() -> None:
    """Startet im Dienstbetrieb genau das, was auch von Hand laufen wuerde.

    Ist OKX eingerichtet, laufen Aktien und Krypto gemeinsam ueber
    nexus_start.laufe(). Sonst bleibt es beim reinen Aktienkern wie in v6 --
    ein Dienst darf nicht daran scheitern, dass eine neue Anbindung noch
    nicht konfiguriert ist.
    """
    import config

    hinweis = str(getattr(config, "OKX_SETUP_HINWEIS", "") or "")
    if hinweis:
        print(f"Krypto ist nicht aktiv: {hinweis}")
    # NEXUS startet beide Domaenen unabhaengig. Ist OKX noch nicht
    # eingerichtet, endet nur der Krypto-Thread; eToro laeuft weiter.
    import nexus_start
    nexus_start.laufe()


def main() -> int:
    os.umask(0o077)
    try:
        signal.signal(signal.SIGTERM, _term)
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    try:
        signal.signal(signal.SIGINT, _term)
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    try:
        with SingleInstanceLock(ROOT / "tradingbot.instance.lock"):
            previous = consume_problem()
            if previous:
                try:
                    from notifier import notify
                    result=str(previous.get("service_result") or "unbekannt")
                    detail=f"Vorheriger systemd-Lauf endete mit {result}; Exit={previous.get('exit_code','')}/{previous.get('exit_status','')}. Der Bot wurde automatisch neu gestartet."
                    if result.lower()=="oom-kill":
                        detail="Vorheriger Bot-Prozess wurde wegen Speicherüberschreitung (OOM) beendet. systemd hat den Trader automatisch neu gestartet."
                    notify("PI-DIENST AUTOMATISCH NEU GESTARTET", detail, priority="critical")
                except Exception:
                    __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
            _starte_handel()
        return 0
    except InstanceAlreadyRunning as exc:
        print(f"SICHERHEITSSTOPP: {exc}")
        return 75
    except AuthentifizierungsFehler as exc:
        # Falsche/abgelaufene Broker-Zugangsdaten werden durch Neustarts nicht
        # gesund. Stop statt 20-Sekunden-Restartschleife/Telegram-Spam.
        print(f"SICHERHEITSSTOPP: Broker-Authentifizierung fehlgeschlagen: {exc}")
        return 78
    except RuntimeError as exc:
        # Fehlende LIVE-Freigabe/Konfiguration ist kein Crash, der alle 20 s
        # neu gestartet werden soll. systemd behandelt Exit 78 separat.
        if "LIVE ist gesperrt" in str(exc):
            print(f"SICHERHEITSSTOPP: {exc}")
            return 78
        raise


if __name__ == "__main__":
    raise SystemExit(main())
