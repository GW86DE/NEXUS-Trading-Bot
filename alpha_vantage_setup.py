"""GUI-Einstieg für Alpha Vantage. Mit --cli bleibt die Texteingabe verfügbar."""
import sys
if "--cli" in sys.argv:
    from pathlib import Path
    import getpass
    from credential_store import save_credentials
    p=Path(__file__).with_name("alpha_vantage_credentials.json")
    print("ALPHA VANTAGE – EARNINGS EINRICHTUNG")
    key=getpass.getpass("API-Key: ").strip()
    if not key:
        print("Abgebrochen."); raise SystemExit(1)
    ziel=save_credentials(p,{"api_key":key})
    print(f"Sicher gespeichert: {ziel.name}. Bot/GUI bitte neu starten.")
else:
    from alpha_vantage_setup_gui import main
    if __name__ == "__main__":
        main()
