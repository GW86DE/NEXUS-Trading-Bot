"""GUI-Einstieg für Telegram. Mit --cli bleibt die Textoberfläche verfügbar."""
import sys
if "--cli" in sys.argv:
    from telegram_setup_cli import main
else:
    from telegram_setup_gui import main
if __name__ == "__main__":
    main()
