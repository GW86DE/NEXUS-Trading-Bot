"""Host-Zugriff des Installers und der Reparaturwerkzeuge (10.8.0, Schritt 2).

Bis 10.7.1 lag ``Host`` (Kommandos, systemd-Zustaende, Schreiber-Pruefung im
Quellordner) in ``nexus_update``; die Reparaturskripte importierten es von dort,
und der Installer importierte die Reparaturskripte: Import-Zyklus
``nexus_update <-> repair_okx_accounting/repair_okx_verified_history``.
``nexus_update`` exportiert ``UNITS``, ``UpdateError`` und ``Host`` weiter, damit
Installer-Tests (FakeHost erbt von ``nexus_update.Host``) unveraendert bleiben.
Verhalten unveraendert; Kommandos enthalten nur Pfade/Optionen, nie Zugangsdaten.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

UNITS = ('tradingbot-pi5.service', 'tradingbot-webui.service')


class UpdateError(RuntimeError):
    pass


class Host:
    def command(self, args, *, cwd=None, capture=False, timeout=1800):
        # Commands contain only paths/options, NEVER credentials.
        print('+ '+ ' '.join(str(x) for x in args), flush=True)
        try:
            r = subprocess.run([str(x) for x in args], cwd=cwd, check=True,
                               stdout=subprocess.PIPE if capture else None,
                               stderr=subprocess.PIPE if capture else None,
                               text=True, encoding='utf-8', errors='replace', timeout=timeout)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise UpdateError('Schritt fehlgeschlagen: '+str(args[0])+' (Details im Installationsprotokoll)') from exc
        return r.stdout if capture else ''

    def states(self):
        result = {}
        for unit in UNITS:
            text = self.command(['systemctl','show',unit,'--no-pager',
                                 '-p','WorkingDirectory','-p','ExecStart','-p','ActiveState',
                                 '-p','UnitFileState','-p','FragmentPath'], capture=True, timeout=15)
            result[unit] = dict(line.split('=',1) for line in text.splitlines() if '=' in line)
        return result

    def stop(self):
        self.command(['sudo','systemctl','stop',*UNITS], timeout=180)
        if any(x.get('ActiveState') not in {'inactive','failed'} for x in self.states().values()):
            raise UpdateError('Nicht alle Dienste sind gestoppt')

    def other_writers(self, source):
        for entry in Path('/proc').iterdir():
            if not entry.name.isdigit() or int(entry.name) == os.getpid():
                continue
            try:
                cwd = (entry/'cwd').resolve()
                cmd = (entry/'cmdline').read_bytes().split(b'\0')
            except (OSError, RuntimeError):
                continue
            entrypoints = {b'nexus_start.py',b'pi_service.py',b'live_trader.py',b'webui_start.py',b'gui_app.py'}
            related_script = any(Path(os.fsdecode(x)).name.encode() in entrypoints for x in cmd[1:])
            related_cwd = source.parent == cwd.parent or cwd == source
            absolute_script = any(os.fsdecode(x).startswith(str(source.parent)+'/') for x in cmd[1:])
            if any(b'python' in x for x in cmd[:1]) and (cwd == source or (related_script and (related_cwd or absolute_script))):
                raise UpdateError('Zusaetzlicher Python-Writer im Quellordner, PID '+entry.name+
                                  '. Zugehoeriges Botfenster schliessen, nichts pauschal killen.')
