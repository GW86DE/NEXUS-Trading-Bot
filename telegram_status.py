"""Telegram-Konfigurations- und API-Status."""
from notifier import telegram_status

if __name__ == "__main__":
    s = telegram_status()
    print("=" * 58)
    print(" TELEGRAM STATUS")
    print("=" * 58)
    print(f"Aktiviert     : {'JA' if s['enabled'] else 'NEIN'}")
    print(f"Konfiguriert  : {'JA' if s['configured'] else 'NEIN'}")
    print(f"Bot           : @{s.get('bot_username','') or '-'}")
    print(f"Chat-ID       : {s.get('chat_id','') or '-'}")
    print(f"Bot API       : {'OK' if s.get('api_ok') else 'FEHLER'}")
    print(f"Queue         : {s.get('queued',0)} Nachricht(en)")
    print(f"Zustellstau   : {'JA' if s.get('delivery_stalled') else 'NEIN'}")
    if s.get('delivery_stalled'):
        print(f"Stau-Grund    : {s.get('stall_reason','')}")
    print(f"Details       : {s.get('detail','')}")
    print("\nTelegram bleibt der einzige aktive Nachrichtenkanal; fuer Not-Aus/Pause gibt es zusaetzlich GUI, lokale Steuerung und Notaus_Pause.bat.")
    input("Enter druecken ... ")
