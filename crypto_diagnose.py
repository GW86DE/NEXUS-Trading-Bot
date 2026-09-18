"""Read-only Diagnose der OKX-EEA-Spotanbindung; erzeugt niemals Orders."""
from __future__ import annotations

import argparse

import config
from broker import get_broker
from console_io import configure_utf8_console
from contracts import Instrument, SimpleContract

configure_utf8_console()


def _instrument(symbol: str, quote: str) -> Instrument:
    symbol = str(symbol).upper()
    quote = str(quote).upper()
    inst_id = f"{symbol}-{quote}"
    return Instrument(
        name=symbol,
        contract=SimpleContract(symbol, quote, localSymbol=inst_id),
        asset_type="crypto",
        currency=quote,
        sector="crypto",
        exchange="OKX",
    )


def diagnose_one(broker, instrument: Instrument) -> dict:
    """Prueft ein Instrument read-only und normalisiert Broker-Quotes.

    Die Funktion ist absichtlich brokerneutral: eToro und OKX liefern beide
    Dictionaries, aber Metadaten und Hinweise unterscheiden sich.
    """
    metadata = {}
    try:
        if hasattr(broker, "instrument_metadata"):
            metadata = broker.instrument_metadata(instrument) or {}
    except Exception:
        metadata = {}
    try:
        tradable, reason = broker.instrument_handelbar(instrument)
    except Exception as exc:
        tradable, reason = False, f"{type(exc).__name__}: {exc}"
    try:
        quote = broker.latest_bid_ask(instrument) or {}
    except Exception as exc:
        quote = {"error": f"{type(exc).__name__}: {exc}"}
    bid = float(quote.get("bid") or 0.0)
    ask = float(quote.get("ask") or 0.0)
    last = float(quote.get("last") or 0.0)
    middle = (bid + ask) / 2.0 if bid > 0 and ask >= bid else 0.0
    spread = ((ask - bid) / middle * 100.0) if middle > 0 else None
    return {
        "symbol": str(getattr(instrument, "name", "") or "").upper(),
        "resolved": bool(metadata) or bool(bid > 0 or ask > 0 or last > 0),
        "tradable": bool(tradable),
        "reason": str(reason or ""),
        "bid": bid,
        "ask": ask,
        "last": last,
        "spread_pct": spread,
        "source": str(quote.get("source") or ""),
        "metadata": metadata if isinstance(metadata, dict) else {},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only OKX-EEA-Kryptodiagnose")
    parser.add_argument("--limit", type=int, default=5, help="Anzahl Werte (1 bis 25)")
    parser.add_argument("--voll", action="store_true",
                        help="zusaetzlich Konto, Gebuehren, offene Orders und Fills pruefen")
    args = parser.parse_args(argv)
    limit = max(1, min(25, int(args.limit)))
    quote = str(getattr(config, "OKX_QUOTE_CCY", "EUR")).upper()
    symbols = list(dict.fromkeys(
        list(getattr(config, "CRYPTO_CORE_SYMBOLS", ("BTC", "ETH", "SOL")))
        + [str(x.get("symbol", "")).upper() for x in getattr(config, "CRYPTO_SYMBOLS", [])]
    ))[:limit]

    print("=" * 74)
    print("OKX EEA SPOT-DIAGNOSE - TradingBot 8.2 NEXUS")
    print("=" * 74)
    print("Read-only: keine Order und keine LIVE-Freischaltung.")
    print(f"Modus: {'DEMO/PAPER' if not config.OKX_LIVE_TRADING else 'LIVE (nur Verbindungstest)'}")
    broker = get_broker("okx")
    try:
        broker.connect()
        print(f"REST-Verbindung: OK | {broker.beschreibung()}")
        status = broker.stream_status()
        print("Privatstream:", "gestartet" if status.get("running") else "REST-Fallback",
              "|", status.get("last_error") or "Anmeldung laeuft")
        if args.voll:
            konto = broker.konto_snapshot(fills_limit=20)
            konfig = konto.get("konto_config") or {}
            print("-" * 74)
            print("KONTO-SCHNAPPSCHUSS (read-only)")
            print(f"EEA-Endpunkt: {konto.get('eea_endpoint')} | Modus: {konto.get('modus')}")
            print("Konto: Level", konfig.get("acctLv") or "?",
                  "| Positionmodus", konfig.get("posMode") or "?",
                  "| Rechte", konfig.get("perm") or "?")
            print(f"Equity: {float(konto.get('equity_usd') or 0.0):.2f} USD")
            fee = konto.get("gebuehren") or {}
            print("Spot-Gebuehren: Maker", f"{float(fee.get('maker') or 0.0) * 100:.4f} %",
                  "| Taker", f"{float(fee.get('taker') or 0.0) * 100:.4f} %")
            print("Guthaben:")
            for ccy, row in sorted((konto.get("guthaben") or {}).items()):
                total = float((row or {}).get("gesamt") or 0.0)
                free = float((row or {}).get("cash") or 0.0)
                frozen = float((row or {}).get("frozen") or 0.0)
                if total > 0 or free > 0 or frozen > 0:
                    print(f"  {ccy:5s} gesamt {total:.8g} | frei {free:.8g} | gebunden {frozen:.8g}")
            print("Offene Standardorders:", len(konto.get("offene_standard_orders") or []),
                  "| OCO-Schutzorders:", len(konto.get("schutzorders_oco") or []),
                  "| Conditional-Schutzorders:", len(konto.get("schutzorders_conditional") or []),
                  "| letzte Fills:", len(konto.get("letzte_fills") or []))
            stream = konto.get("stream") or {}
            print("Privatstream-Status:", "verbunden" if stream.get("connected") else "REST-Fallback",
                  "| authentifiziert", bool(stream.get("authenticated")),
                  "| Reconnects", int(stream.get("reconnects") or 0))
            if konto.get("fehler"):
                print("Teilpruefung nicht verfuegbar:", ", ".join(sorted(konto["fehler"])))
        good = 0
        for symbol in symbols:
            inst = _instrument(symbol, quote)
            ok, reason = broker.instrument_handelbar(inst)
            ticker = broker.latest_bid_ask(inst) or {}
            bid, ask = float(ticker.get("bid") or 0), float(ticker.get("ask") or 0)
            spread = ((ask - bid) / ((ask + bid) / 2) * 100
                      if bid > 0 and ask >= bid else None)
            spread_text = f"{spread:.4f} %" if spread is not None else "n/v"
            print(f"{symbol:10s} | {symbol}-{quote:4s} | Handel {'OK' if ok else 'NEIN':4s} "
                  f"| Bid {bid:.8g} / Ask {ask:.8g} | Spread {spread_text}")
            if reason:
                print("  Hinweis:", reason)
            if ok and bid > 0 and ask > 0:
                good += 1
        print("-" * 74)
        print(f"Diagnose: {good}/{len(symbols)} Werte handelbar und mit Bid/Ask.")
        return 0 if good else 1
    except Exception as exc:
        print(f"OKX-Diagnose fehlgeschlagen: {type(exc).__name__}: {exc}")
        return 1
    finally:
        try:
            broker.disconnect()
        except Exception:
            import logging
            logging.getLogger(__name__).debug("OKX-Disconnect fehlgeschlagen", exc_info=True)


if __name__ == "__main__":
    raise SystemExit(main())
