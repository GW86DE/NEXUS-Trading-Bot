"""Telegram-Testnachricht mit sichtbarem Ergebnis."""
from notifier import send_telegram, telegram_status

if __name__ == "__main__":
    print("=== TELEGRAM TEST ===")
    status = telegram_status()
    print(f"Aktiviert : {status['enabled']}")
    print(f"Konfiguriert: {status['configured']}")
    print(f"Bot       : @{status.get('bot_username','') or '-'}")
    print(f"Chat-ID   : {status.get('chat_id','') or '-'}")
    print(f"API       : {'OK' if status.get('api_ok') else 'FEHLER'}")
    print(f"Details   : {status.get('detail','')}")
    print()
    ok = send_telegram("✅ TradingBot Telegram-Testnachricht\nTelegram ist der einzige Benachrichtigungs- und Fernsteuerungskanal.")
    print("ERGEBNIS:", "OK" if ok else "FEHLER")
    input("Enter druecken ... ")
