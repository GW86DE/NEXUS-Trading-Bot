"""Zeigt Konfiguration UND tatsaechlich zuletzt gemessene Erreichbarkeit."""
from __future__ import annotations

from news_sources import MultiSourceNews
from console_io import configure_utf8_console

configure_utf8_console()


def _fmt_age(seconds):
    if seconds is None:
        return "nie"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds//60}m"
    return f"{seconds/3600:.1f}h"


def main():
    client = MultiSourceNews()
    print('Pruefe aktivierte Quellen; Anbieterpausen und gemeinsame RSS-Caches bleiben wirksam.', flush=True)
    for name, enabled in client.provider_configuration().items():
        if enabled:
            print(f'Pruefung: {name}', flush=True)
            client.active_health_test(name)
    health = client.health_snapshot()
    print("=" * 94)
    print("RESEARCH / NEWS STATUS - " + __import__("config").VERSION_NEXUS)
    print("=" * 94)
    print(f"{'QUELLE':22} {'KONFIG':9} {'STATUS':11} {'ALTER':8} DETAILS")
    print("-" * 94)
    configured = healthy = 0
    for name, row in health.items():
        if row['configured'] and row.get('role') != 'Referenzdaten':
            configured += 1
        if row['healthy'] and row.get('role') != 'Referenzdaten':
            healthy += 1
        state = row['state'].upper()
        if state == 'BACKOFF':
            state = f"PAUSE {max(1,row['backoff_seconds']//60)}m"
        labels = {'STALE': 'ALT GEPRUEFT', 'UNKNOWN': 'UNGEPRUEFT', 'DISABLED': 'AUS',
                  'ENTITLEMENT': 'TARIF', 'AUTHENTICATION': 'ZUGANG'}
        state = labels.get(state, state)
        print(
            f"{name:22} {'JA' if row['configured'] else 'NEIN':9} "
            f"{state:11} {_fmt_age(row['age_seconds']):8} {row['detail'][:160]}"
        )
    print("-" * 94)
    print(f"Aktuell erfolgreich gepruefte Newsquellen: {healthy}/{configured} der eingerichteten Quellen")
    print()
    print("Hinweis: 'KONFIGURIERT' ist nicht dasselbe wie 'ERREICHBAR'.")
    print("ALTER bezeichnet die letzte Pruefung, nicht das Alter einer Nachricht. ALT GEPRUEFT ist kein Ausfallnachweis.")
    print("Symbolsuche wird als Referenzdatenquelle separat gefuehrt.")
    print("HTTP-403/Netzwerkfehler werden mit Backoff pausiert, damit keine Log-/Request-Schleife entsteht.")


if __name__ == '__main__':
    main()
