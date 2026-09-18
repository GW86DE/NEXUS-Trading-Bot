"""Komfortable eToro-Public-API-Einrichtung (Demo/PAPER und LIVE)."""
try:
    from etoro_setup_gui import main
except Exception:
    main = None

if __name__ == "__main__":
    if main is None:
        raise SystemExit("eToro Setup GUI konnte nicht geladen werden. Python/Tkinter pruefen.")
    main()
