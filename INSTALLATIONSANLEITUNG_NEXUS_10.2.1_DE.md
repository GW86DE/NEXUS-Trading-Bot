# NEXUS 10.2.1 – Installation auf dem Raspberry Pi 5 (Hotfix)

**Build:** `10.2.1-COMPOSITE-EXIT-SETTLEMENT` · Basis: 10.2.0-NEXUS (Rev. 2) · Diagnose 1.8.1

Hotfix für die seit dem 17.09. bestehende OKX-Kaufsperre (DOGE-Mischverkauf). Nach der Installation erkennt NEXUS den zusammengesetzten Verkauf (eigener Take-Profit-Teilfill + deine zwei DOGE/EUR-Orders) automatisch, verbucht ihn mit den echten OKX-Belegen, beziffert das Ergebnis über belegte Referenzkurse und **gibt die OKX-Kaufdomäne selbstständig wieder frei** — du musst nichts weiter tun.

## Phase 1 – Paketpruefung (keine Dienste, keine Brokeraktion)

```bash
bash "$HOME/Downloads/NEXUS_10.2.1_Installieren.sh" --paket-pruefen
```

Erwartete Ausgabe: `PAKETPRUEFUNG OK: NEXUS 10.2.1, ... Quellhashes geprueft.`

## Phase 2 – Installation (als normaler Benutzer, NICHT mit sudo)

```bash
bash "$HOME/Downloads/NEXUS_10.2.1_Installieren.sh"
```

Ablauf wie gewohnt: Dienste stoppen, Sicherung, Entpacken nach `~/Georg/TradingBot_v10.2.1_NEXUS`, Datenübernahme, Volltest, Neustart. Bei Fehlschlag bricht das Update ab, 10.2.0 bleibt lauffähig, und das Staging räumt sich selbst weg.

## Phase 3 – Abnahme der DOGE-Freigabe (wenige Minuten nach dem Start)

Beobachte auf der Übersicht/im Logbuch die Kette (typisch innerhalb von 1–5 Minuten):
1. „Krypto DOGE: Verkauf vollständig belegt aus 1 eigener Schutz- und 2 Nutzer-Order(s). Netto-Erlöse nativ: … EUR; … USDC."
2. „EUR-Referenzbewertung für Trade … gespeichert: netto … EUR"
3. Die Meldung „Käufe gesperrt … Buchungs-/Bestandsbelege offen" verschwindet; OKX kauft wieder (Demo).

Falls Schritt 2 ausbleibt (z. B. Kursbeleg vorübergehend nicht abrufbar), versucht es der Bot alle ~10 Minuten erneut — die Sperre löst sich dann entsprechend später. Danach wie gewohnt optional die 30-Minuten-Diagnose:

```bash
cd "$HOME/Georg/TradingBot_v10.2.1_NEXUS"
./NEXUS_10.2.1_Diagnose_Starten.sh
```

## Hinweis zur OKX-API (deine Frage „deutsche Version/Demo")

Geprüft gegen die offizielle EEA-Doku: NEXUS nutzt bereits die korrekte EU-Basis `https://eea.okx.com` für Live **und** Demo (Header `x-simulated-trading: 1`). `my.okx.com` ist nur die Weboberfläche derselben EU-Entität. Es war **keine** Anpassung nötig; Details im Prüfbericht.

## Optionen (unveraendert)

`--paket-pruefen` · `--nur-entpacken` · `--plan` · `--okx-neues-konto` · `--source` · `--receipts` · `--verified-trades` · `--verified-fx` · `--fmp-starter`
