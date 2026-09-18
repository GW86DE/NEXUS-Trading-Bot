from credential_store import migrate_known_credentials
if __name__ == '__main__':
    moved=migrate_known_credentials()
    print('DPAPI-Migration abgeschlossen.')
    if moved:
        print('Verschluesselt:', ', '.join(moved))
    else:
        print('Keine Klartext-Credential-Dateien zu migrieren (oder nicht Windows).')
