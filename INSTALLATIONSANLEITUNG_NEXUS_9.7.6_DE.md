# NEXUS 9.7.6 auf dem Raspberry Pi

Dieses Paket korrigiert die Vermischung von Live-Marktdaten mit OKX-Demo-Orders und die im Befund beschriebenen Verkaufszustände. Es enthält den kompletten Bot. Der Updateweg übernimmt den aktuellen Zustand aus dem tatsächlich in beiden Diensten eingestellten Ordner.

## Mit dem selbstenthaltenden Installer

Speichere `NEXUS_9.7.6_Installieren.sh` im Ordner `/home/georg/Georg`. Starte ihn als Benutzer `georg` im Terminal:

```bash
bash "$HOME/Georg/NEXUS_9.7.6_Installieren.sh"
```

Der Starter prüft die eingebettete ZIP und entpackt nach `/home/georg/Georg/TradingBot_v9.7.6_NEXUS`. Der bestehende Updater prüft Abhängigkeiten und den isolierten Volltest, stoppt und sichert die bisherigen Dienste, übernimmt den Zustand, führt die vorhandene beleggestützte Reparatur aus und kontrolliert die neu gestarteten Dienste. Bei Systemschritten kann dein sudo-Passwort nötig sein.

Voraussetzung ist Raspberry Pi OS mit 64 Bit/ARM64, Python mindestens 3.11 und ein eindeutig bestätigter DEMO-/Paper-Ausgangsstand. Ein Live- oder unklarer Kontomodus wird vom automatischen Startweg abgewiesen. Es werden keine Zugangsdaten umgestellt.

Nur die Datei prüfen, ohne Installation oder Dienstaktionen:

```bash
bash "$HOME/Georg/NEXUS_9.7.6_Installieren.sh" --paket-pruefen
```

## Wenn du die ZIP verwendest

Entpacke `TradingBot_v9.7.6_NEXUS.zip` als neuen eigenen Ordner unter `/home/georg/Georg`. Starte danach:

```bash
bash "$HOME/Georg/TradingBot_v9.7.6_NEXUS/Nexus_Update.sh"
```

Verwende nur einen dieser beiden Wege. Den alten 9.7.5-Installer brauchst du dafür nicht erneut auszuführen. Die vorherige Installation wird nicht durch Entpacken überschrieben.

## Woran du den Wechsel erkennst

Am Ende muss `UPDATE UND STARTKONTROLLE ERFOLGREICH` erscheinen. Die WebUI zeigt 9.7.6. Beide Dienste sollen den neuen Ordner verwenden:

```bash
systemctl show tradingbot-pi5.service tradingbot-webui.service --no-pager -p ActiveState -p SubState -p WorkingDirectory
```

Die nächsten OKX-Verkaufszeilen nennen `umgebung=DEMO` und eine Buchzeit. Das bestätigt die angeforderte Umgebung, noch keinen Verkaufsfill. Ein Abschluss benötigt passende Order-ID, terminalen Brokerstatus, echte Fills und den zugehörigen Ledgerabschluss. Ist das Demo-Buch zu dünn oder nicht verfügbar, muss eine verständliche Sperre erscheinen.

## Falls eine Phase abbricht oder SUI weiter nicht ausgeführt wird

Bewahre `nexus_update.log`, das Phasenjournal und die angelegte Sicherung auf. Nach einem möglichen neuen Dienststart keine alten Handelsdaten zurückspielen und keine zwei Cores starten. Der Updater unterscheidet diese Grenze selbst.

Mit folgendem Export erhältst du die aktuellen lokalen SUI-Belege in einer kleinen ZIP:

```bash
python3 "$HOME/Georg/TradingBot_v9.7.6_NEXUS/Nexus_SUI_Diagnose.py"
```

Das Skript ermittelt den aktuellen Ordner aus systemd, selbst wenn die Dienste noch auf die vorherige Version zeigen. Es liest nur, sendet keine Brokeranfragen und gibt den Pfad der neuen Diagnose-ZIP aus. Widersprechen sich die Dienstordner, verlangt es ausdrücklich einen `--source`-Pfad. Die JSON-Dateien und SQLite-Daten werden nacheinander gelesen und sind kein atomarer gemeinsamer Snapshot.

Die volle historische Matching-Ursache kann dieser lokale Export nur erklären, soweit die damals gespeicherten Order- und Buchbelege sie enthalten. Fehlende Börsenbelege werden nicht nachträglich erfunden.
