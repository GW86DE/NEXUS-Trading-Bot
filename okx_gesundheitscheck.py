#!/usr/bin/env python3
"""Laeuft OKX sauber? (NEXUS 9.5.x)

NUR LESEN. Keine Order, keine Stornierung, keine Aenderung an Dateien.

Geprueft wird in dieser Reihenfolge, weil die spaeteren Punkte auf den
frueheren aufbauen:

  1. Antwortet die API wieder?
  2. Stimmen Positionsbuch und tatsaechlicher Kontobestand ueberein?
  3. Liegt fuer JEDE Botposition wirklich eine Schutzorder bei OKX?
  4. Ist das Universum nach dem Ausfall zurueck?
  5. Haengt noch eine ungeklaerte Order im Register?

Punkt 3 ist der wichtigste: im Statusfile vom 02.09. stand bei allen drei
Positionen "protection_status": "PENDING" ohne algoId. Das waere ein
Bestand ohne Stop -- und das muss man wissen.

Aufruf im Installationsverzeichnis:

    ./.venv/bin/python okx_gesundheitscheck.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

WURZEL = Path(__file__).resolve().parent
sys.path.insert(0, str(WURZEL))

_print = print


def print(*teile, **kwargs):          # noqa: A001  (nur in diesem Skript)
    text = " ".join(str(x) for x in teile)
    try:
        _print(text, **kwargs)
    except UnicodeEncodeError:
        kodierung = getattr(sys.stdout, "encoding", "") or "ascii"
        _print(text.encode(kodierung, "replace").decode(kodierung), **kwargs)


BEFUNDE: list[tuple[str, str]] = []          # (OK|WARNUNG|FEHLER, Text)


def merke(stufe: str, text: str) -> None:
    BEFUNDE.append((stufe, text))
    print(f"  [{stufe}] {text}")


def kopf(text: str) -> None:
    print()
    print("=" * 74)
    print(text)
    print("=" * 74)


def lies_json(name: str):
    pfad = WURZEL / name
    if not pfad.exists():
        return None
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except Exception:
        return None


def main() -> None:
    print("=" * 74)
    print("OKX-GESUNDHEITSCHECK")
    print("=" * 74)

    # ------------------------------------------------------------------
    kopf("1. ANTWORTET DIE API?")
    from broker.okx import OKXBroker

    broker = OKXBroker(demo=True)
    api_ok = False
    try:
        print(f"  Serverzeit             : {broker.client.server_time_ms()}")
        konto = broker.client.account_config()
        print(f"  Kontoabfrage           : OK (uid vorhanden: "
              f"{bool(konto.get('uid') or konto.get('mainUid'))})")
        guthaben = broker.client.balances()
        print(f"  Guthaben               : {len(guthaben)} Waehrung(en)")
        api_ok = True
        merke("OK", "Die private OKX-API antwortet wieder.")
    except Exception as exc:
        merke("FEHLER", f"API antwortet nicht: {type(exc).__name__}: {str(exc)[:110]}")
        print()
        print("  Die folgenden Punkte brauchen eine Verbindung und werden")
        print("  uebersprungen. Sobald OKX wieder antwortet, erneut ausfuehren.")

    # ------------------------------------------------------------------
    kopf("2. POSITIONSBUCH GEGEN KONTOBESTAND")
    buch = (lies_json("crypto_positions.json") or {}).get("positionen") or []
    if not buch:
        merke("WARNUNG", "Kein Positionsbuch gefunden oder es ist leer.")
    else:
        print(f"  Positionsbuch: {len(buch)} Position(en)")
    bestaende = {}
    if api_ok:
        try:
            for p in broker.positionen():
                bestaende[str(p.symbol).upper()] = float(p.quantity or 0.0)
        except Exception as exc:
            merke("WARNUNG", f"Kontobestand nicht abrufbar: {type(exc).__name__}")
    for p in buch:
        symbol = str(p.get("symbol") or "").upper()
        menge = float(p.get("menge") or 0.0)
        if not api_ok:
            print(f"  {symbol:6} Buch {menge:<16.8f}  (Konto nicht pruefbar)")
            continue
        real = bestaende.get(symbol)
        if real is None:
            merke("FEHLER", f"{symbol}: im Buch {menge:g}, im OKX-Konto NICHT vorhanden.")
        elif abs(real - menge) > max(1e-9, abs(menge) * 1e-6):
            merke("WARNUNG", f"{symbol}: Buch {menge:g}, Konto {real:g} -- Abweichung.")
        else:
            print(f"  {symbol:6} Buch {menge:<16.8f}  Konto stimmt ueberein")

    # ------------------------------------------------------------------
    kopf("3. LIEGT FUER JEDE POSITION EINE SCHUTZORDER BEI OKX?")
    offene_algos: list = []
    if not api_ok:
        merke("WARNUNG", "Ohne Verbindung nicht pruefbar -- das ist der "
                         "wichtigste Punkt, bitte spaeter wiederholen.")
    elif not buch:
        print("  Keine Positionen zu pruefen.")
    else:
        try:
            offene_algos = broker.client.pending_algo_orders()
        except Exception as exc:
            offene_algos = []
            merke("WARNUNG", f"Algo-Orders nicht abrufbar: {type(exc).__name__}")
        nach_inst: dict[str, list] = {}
        for row in offene_algos or []:
            nach_inst.setdefault(str(row.get("instId") or "").upper(), []).append(row)
        print(f"  Offene OCO-/Algo-Orders bei OKX: {len(offene_algos or [])}")
        # 9.5.x: ALLE auflisten, nicht nur die zugeordneten. Beim ersten Lauf
        # meldete das Skript zwei Algo-Orders und zeigte nur eine -- die
        # zweite gehoerte zu keinem Instrument aus dem Buch und blieb
        # unsichtbar. Genau die ist aber interessant.
        for row in offene_algos or []:
            print(f"     algoId {row.get('algoId')}  instId {row.get('instId')}  "
                  f"state={row.get('state')}  sz={row.get('sz')}  "
                  f"sl={row.get('slTriggerPx') or '-'}  tp={row.get('tpTriggerPx') or '-'}")
        bekannte = {str(p.get("inst_id") or "").upper() for p in buch}
        verwaist = [r for r in (offene_algos or [])
                    if str(r.get("instId") or "").upper() not in bekannte]
        if verwaist:
            merke("WARNUNG", f"{len(verwaist)} Algo-Order(s) ohne zugehoerige "
                             f"Position im Buch: "
                             + ", ".join(str(r.get("instId")) for r in verwaist))
        print()
        for p in buch:
            symbol = str(p.get("symbol") or "").upper()
            inst = str(p.get("inst_id") or "").upper()
            status = str(p.get("protection_status") or "")
            algo_id = str(p.get("protection_algo_id") or "")
            flagge = bool(p.get("broker_schutz"))
            treffer = nach_inst.get(inst) or []
            print(f"  {symbol:6} inst={inst:12} broker_schutz={flagge} "
                  f"status={status or '(leer)'} algoId={algo_id or '(leer)'}")
            if treffer:
                for row in treffer:
                    print(f"         -> OKX fuehrt algoId {row.get('algoId')} "
                          f"state={row.get('state')} "
                          f"sl={row.get('slTriggerPx') or '-'} "
                          f"tp={row.get('tpTriggerPx') or '-'}")
                if algo_id and algo_id not in {str(r.get("algoId")) for r in treffer}:
                    merke("WARNUNG", f"{symbol}: gespeicherte algoId {algo_id} "
                                     f"steht nicht unter den offenen Algo-Orders.")
                else:
                    merke("OK", f"{symbol}: Schutzorder liegt bei OKX.")
            else:
                merke("FEHLER", f"{symbol}: KEINE Schutzorder bei OKX gefunden -- "
                                f"die Position laeuft ohne Stop.")

    # ------------------------------------------------------------------
    kopf("3b. GESPERRTES GUTHABEN -- der zweite Schutzbeweis")
    print("  Eine offene Verkaufs-/Schutzorder SPERRT den Bestand bei OKX.")
    print("  'frei' deutlich kleiner als 'gesamt' ist deshalb ein unabhaengiger")
    print("  Beleg dafuer, dass ein Schutz liegt -- auch ohne bekannte algoId.")
    print()
    if not api_ok:
        merke("WARNUNG", "Ohne Verbindung nicht pruefbar.")
    else:
        try:
            rohguthaben = broker.client.balances()
        except Exception as exc:
            rohguthaben = []
            merke("WARNUNG", f"Guthaben nicht abrufbar: {type(exc).__name__}")
        # balances() liefert bereits {'EUR': {'cash':.., 'gesamt':.., 'frozen':..}}
        # -- kein Rohformat mit 'details'. Genau daran ist der erste Lauf
        # gescheitert.
        nach_ccy = {}
        for ccy, werte in (rohguthaben or {}).items():
            if not isinstance(werte, dict):
                continue
            gesamt = float(werte.get("gesamt") or 0.0)
            frei = float(werte.get("cash") or 0.0)
            nach_ccy[str(ccy).upper()] = (gesamt, frei)
        for p in buch:
            symbol = str(p.get("symbol") or "").upper()
            gesamt, frei = nach_ccy.get(symbol, (None, None))
            if gesamt is None:
                print(f"  {symbol:6} nicht im Guthaben gefunden")
                continue
            gesperrt = gesamt - frei
            print(f"  {symbol:6} gesamt {gesamt:<18.8f} frei {frei:<18.8f} "
                  f"gesperrt {gesperrt:.8f}")
            if gesperrt > max(1e-9, gesamt * 1e-6):
                print("         -> Bestand ist gesperrt, ein Schutz liegt also.")
            elif gesamt > 0:
                merke("FEHLER", f"{symbol}: nichts gesperrt und keine Algo-Order "
                                f"-- der Bestand von {gesamt:g} laeuft ungeschuetzt.")

        # Und umgekehrt: gesperrte Bestaende, zu denen KEINE Position im Buch
        # steht. Genau so ist ETH-EUR aufgefallen -- 1,04 ETH mit lebender
        # OCO-Order, im Positionsbuch aber gar nicht vorhanden.
        print()
        im_buch = {str(p.get("symbol") or "").upper() for p in buch}
        for ccy, (gesamt, frei) in sorted(nach_ccy.items()):
            if ccy in im_buch or gesamt <= 0:
                continue
            gesperrt = gesamt - frei
            if gesperrt > max(1e-9, gesamt * 1e-6):
                merke("WARNUNG",
                      f"{ccy}: {gesperrt:g} von {gesamt:g} gesperrt, aber KEINE "
                      f"Position im Buch. Geschuetzter Bestand ohne Verwaltung.")

    # ------------------------------------------------------------------
    kopf("3c. WAS IST MIT BNB PASSIERT?")
    print("  Heute Morgen stand BNB im Buch mit 0,15443757, jetzt mit einem")
    print("  Staubrest. Die letzten Fills zeigen, ob verkauft wurde.")
    print()
    if not api_ok:
        merke("WARNUNG", "Ohne Verbindung nicht pruefbar.")
    else:
        weitere = {str(r.get("instId") or "") for r in (offene_algos or [])}
        for inst in sorted(({str(p.get("inst_id") or "") for p in buch}
                            | weitere | {"BNB-USDC"}) - {""}):
            if not inst:
                continue
            try:
                fills = broker.client.request(
                    "GET", "/trade/fills", params={"instId": inst, "limit": "5"},
                    private=True)
            except Exception as exc:
                print(f"  {inst:14} Fills nicht abrufbar: {type(exc).__name__}")
                continue
            if not fills:
                print(f"  {inst:14} keine Fills in der jüngsten Historie")
                continue
            print(f"  {inst:14} {len(fills)} Fill(s):")
            for row in fills:
                print(f"       {row.get('ts')}  {row.get('side'):4} "
                      f"sz={row.get('fillSz')} px={row.get('fillPx')} "
                      f"ordId={row.get('ordId')}")

    # ------------------------------------------------------------------
    kopf("4. IST DAS UNIVERSUM ZURUECK?")
    status = lies_json("runtime_status_okx.json") or {}
    universum = status.get("universum") or {}
    if not universum:
        merke("WARNUNG", "runtime_status_okx.json enthaelt kein Universum.")
    else:
        pool = int(universum.get("aktiv") or universum.get("gesamt") or 0)
        kern = int(universum.get("kern") or 0)
        kern_limit = int(universum.get("kern_limit") or 0)
        print(f"  aktiv {pool}   Kern {kern}/{kern_limit}   "
              f"handelbar {universum.get('handelbar')}   "
              f"gepinnt {universum.get('gepinnt')}")
        if pool <= 0 or kern <= 0:
            merke("FEHLER", "Das Universum ist leer. Nach dem Ausfall vom "
                            "02.09. muss es wieder gefuellt sein.")
        elif kern < kern_limit:
            merke("WARNUNG", f"Kern nur {kern} von {kern_limit}.")
        else:
            merke("OK", f"Universum vollstaendig ({pool} aktiv, Kern {kern}/{kern_limit}).")

    bereit = status.get("handelsbereitschaft") or {}
    if bereit:
        offen = bereit.get("offen") or []
        print(f"  Handelsbereit: {bereit.get('trading_ready')}   "
              f"Kaeufe erlaubt: {bereit.get('kaeufe_erlaubt')}")
        if offen:
            merke("WARNUNG", f"Offene Bedingungen: {', '.join(map(str, offen))}")
        elif bereit.get("kaeufe_erlaubt"):
            merke("OK", "Alle Handelsbedingungen erfuellt.")

    # ------------------------------------------------------------------
    kopf("5. HAENGT NOCH EINE UNGEKLAERTE ORDER IM REGISTER?")
    try:
        from order_ownership import OrderOwnershipRegistry
        import config
        register = OrderOwnershipRegistry(
            WURZEL / str(getattr(config, "BOT_ORDER_REGISTRY_FILE",
                                 "bot_order_registry.json")))
        if getattr(register, "storage_error", ""):
            merke("FEHLER", f"Register unlesbar: {register.storage_error}")
        else:
            offen = register.pending_for(broker="okx", environment="DEMO",
                                         asset_type="crypto")
            print(f"  Eintraege in 'pending' gesamt : {len(register.pending)}")
            print(f"  davon offen fuer OKX/Krypto   : {len(offen)}")
            for schluessel, meta in offen.items():
                print(f"     {meta.get('symbol')} zustand={meta.get('zustand')} "
                      f"ordId={meta.get('ord_id') or meta.get('order_id') or '-'}")
            if offen:
                merke("WARNUNG", f"{len(offen)} ungeklaerte OKX-Order(s) -- "
                                 "neue Kaeufe bleiben gesperrt.")
            else:
                merke("OK", "Keine ungeklaerte eigene OKX-Order.")
    except Exception as exc:
        merke("WARNUNG", f"Register nicht pruefbar: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    kopf("ZUSAMMENFASSUNG")
    fehler = [t for s, t in BEFUNDE if s == "FEHLER"]
    warnungen = [t for s, t in BEFUNDE if s == "WARNUNG"]
    print(f"  {len(BEFUNDE)} Pruefpunkte: "
          f"{len(BEFUNDE) - len(fehler) - len(warnungen)} OK, "
          f"{len(warnungen)} Warnung(en), {len(fehler)} Fehler")
    for text in fehler:
        print(f"  FEHLER  : {text}")
    for text in warnungen:
        print(f"  WARNUNG : {text}")
    if not fehler and not warnungen:
        print()
        print("  OKX laeuft sauber.")
    print()
    print("  Bitte die komplette Ausgabe schicken. Sie enthaelt keine")
    print("  Zugangsdaten -- nur Symbole, Mengen und Zustaende.")


if __name__ == "__main__":
    main()
