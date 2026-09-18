"""Rein lesende OKX-Kerzendaten fuer die WebUI-Tradeanalyse."""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from broker.okx import OKXClient
from trade_ledger import trade_detail

ROOT = Path(__file__).resolve().parent
PAIR = re.compile(r"^[A-Z0-9]{2,20}-[A-Z0-9]{2,10}$")
_CACHE: dict[int, tuple[float, dict]] = {}
_LIVE_CACHE: dict[tuple[str, str, str, float], tuple[float, dict]] = {}
_LOCK = threading.RLock()
logger = logging.getLogger(__name__)
_CLIENT_FACTORY = lambda: OKXClient(
    demo=True, base_url=str(getattr(config, "OKX_BASE_URL", "https://eea.okx.com")),
    timeout=min(3.0, float(getattr(config, "OKX_TIMEOUT_SECONDS", 15.0))),
)


def _client_for_environment(environment: str) -> OKXClient:
    if environment not in {"DEMO", "LIVE"}:
        raise ValueError("Handelsumgebung des Trades nicht eindeutig gespeichert")
    client = _CLIENT_FACTORY()
    # The factory creates a fresh public client; select the stored trade's
    # environment before its first request, never today's global account mode.
    client.demo = environment == "DEMO"
    return client


def _utc(value) -> datetime:
    dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _timeframe(seconds: float) -> tuple[str, int]:
    """Kleinste Kerze, mit der der Trade in hoechstens 100 Punkten sichtbar ist."""
    for bar, size in (("5m", 300), ("15m", 900), ("1H", 3600),
                      ("4H", 14_400), ("1Dutc", 86_400)):
        if seconds <= size * 92:
            return bar, size
    return "1Dutc", 86_400


# 10.4.0: waehlbare Zeitrahmen der Kerzenansicht (ECharts mit Zoom).
OKX_BARS = {"5m": ("5m", 300), "15m": ("15m", 900), "1h": ("1H", 3600), "4h": ("4H", 14_400), "1d": ("1Dutc", 86_400)}
ETORO_BARS = {"15m": 900, "1h": 3600, "1d": 86_400}
MAX_CANDLES = 500


def _axis_bounds(candles: list[dict], extra: list[float]) -> dict:
    """Y-Achse auf das 1./99. Perzentil der Kerzenkoerper klemmen.

    Ein einzelner Docht-Ausreisser (DOGE 17.09.: 0,0847 bei Koerpern um
    0,080) staucht sonst die ganze Ansicht. Dochte ausserhalb werden vom
    Frontend markiert, nicht verschwiegen.
    """
    values = sorted(v for c in candles for v in (float(c["open"]), float(c["close"])))
    if not values:
        return {"min": None, "max": None, "clipped": 0}
    lo = values[max(0, int(len(values) * 0.01))]
    hi = values[min(len(values) - 1, int(len(values) * 0.99))]
    for v in extra:
        lo, hi = min(lo, float(v)), max(hi, float(v))
    span = max(hi - lo, abs(hi) * 0.002, 1e-12)
    lo, hi = lo - span * 0.08, hi + span * 0.08
    clipped = sum(1 for c in candles if float(c["high"]) > hi or float(c["low"]) < lo)
    return {"min": lo, "max": hi, "clipped": clipped}


def _pick_bar(available: dict, requested: str, trade_seconds: float, default_seconds_map) -> str:
    if requested in available:
        return requested
    # Automatik: kleinste Kerze, die den Trade in <= 150 Punkten zeigt.
    for key in available:
        if trade_seconds <= default_seconds_map[key] * 150:
            return key
    return list(available)[-1]


def _etoro_chart(trade: dict, key: int, requested_bar: str) -> dict:
    """eToro-Kerzen aus dem Kern-Snapshot (etoro_chart_store); kein Brokerabruf."""
    import etoro_chart_store as store
    from broker_display_context import environment as trade_environment
    environment = trade_environment(trade)
    account = str(trade.get("broker_account_fingerprint") or "")
    symbol = str(trade.get("symbol") or "").upper()
    if environment not in {"DEMO", "LIVE"} or not account or not symbol:
        return {"ok": False, "fehler": "Konto/Umgebung des eToro-Trades nicht eindeutig gespeichert"}
    entry_time = _utc(trade.get("eingestiegen_am"))
    exit_time = (_utc(trade.get("ausgestiegen_am")) if trade.get("ausgestiegen_am")
                 else datetime.now(timezone.utc))
    available = store.verfuegbare_bars(account, environment, symbol)
    if not available:
        return {"ok": False, "fehler": ("Noch keine eToro-Kerzen fuer " + symbol + " gesichert. Der Handelskern "
                                        "speichert Stundenkerzen bei jedem Scan und 15m-/Tageskerzen fuer Trades "
                                        "der letzten 7 Tage (ab 10.4.0).")}
    bars = {b: ETORO_BARS[b] for b in available}
    bar = _pick_bar(bars, requested_bar, (exit_time - entry_time).total_seconds(), ETORO_BARS)
    size = ETORO_BARS[bar]
    window_start = entry_time - timedelta(seconds=size * 40)
    data = store.lade(account, environment, symbol, bar,
                      start_ts=int(window_start.timestamp()) if trade.get("ausgestiegen_am") else None,
                      end_ts=int((exit_time + timedelta(seconds=size * 20)).timestamp()) if trade.get("ausgestiegen_am") else None,
                      limit=MAX_CANDLES)
    candles = data["candles"]
    if not candles:
        return {"ok": False, "fehler": f"Keine gesicherten {bar}-Kerzen fuer {symbol} im Tradezeitraum"}
    offen = not bool(trade.get("ausgestiegen_am"))
    lines = {"stop": None, "take_profit": None}
    if offen:
        try:
            positions = json.loads((ROOT / "stock_positions.json").read_text(encoding="utf-8")).get("positionen") or []
            from broker_display_context import match_position
            p = match_position(trade, [dict(x, broker="etoro") for x in positions]) or {}
            lines = {"stop": p.get("broker_stop") or p.get("planned_stop"),
                     "take_profit": p.get("broker_take_profit") or p.get("planned_take_profit")}
        except (OSError, ValueError, TypeError):
            logger.debug("eToro-Schutzlinien nicht lesbar", exc_info=True)
    extra = [v for v in (trade.get("einstieg_preis"), trade.get("ausstieg_preis"), lines.get("stop"), lines.get("take_profit")) if v]
    saved_age = (time.time() - float(data["saved_at"])) if data.get("saved_at") else None
    return {
        "ok": True, "trade_id": key, "symbol": symbol,
        "display_context": __import__("broker_display_context").context(trade),
        "instrument": symbol, "bar": bar, "bar_seconds": size, "available_bars": available,
        "nur_abgeschlossene_kerzen": True, "candles": candles, "axis": _axis_bounds(candles, extra),
        "kauf": {"zeit": entry_time.isoformat(), "preis": trade.get("einstieg_preis")},
        "verkauf": ({"zeit": _utc(trade["ausgestiegen_am"]).isoformat(), "preis": trade.get("ausstieg_preis")}
                     if trade.get("ausgestiegen_am") else None),
        "stop": lines.get("stop"), "take_profit": lines.get("take_profit"),
        "active_roi_price": None, "active_roi_pct": None,
        "current_price": None, "current_source": "",
        "letzter_schluss": float(candles[-1]["close"]),
        "offenes_ergebnis": None, "waehrung": str(trade.get("waehrung") or "USD"),
        "settlement_currency": str(trade.get("waehrung") or "USD"),
        "entry_strategy_mode": trade.get("entry_strategy_mode") or "NEXUS_STANDARD",
        "strategie_version": trade.get("strategie_version") or "UNBEKANNT",
        "data_source": "eToro-Historie (Kern-Snapshot)",
        "data_age_seconds": saved_age,
        "hinweis": "Kerzen stammen aus dem eToro-Abruf des Handelskerns; Kauf und Verkauf markieren die Ledger-Zeitpunkte.",
    }


def _position_lines(symbol: str, instrument: str = "", *, trade=None) -> dict:
    try:
        from broker_display_context import match_position
        path = ROOT / "crypto_positions.json"
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return match_position(trade or {}, data.get("positionen") or [])
    except (OSError, ValueError, TypeError):
        logger.debug("Schutzlinien fuer die Trade-Grafik nicht lesbar", exc_info=True)
        return {}


def _instrument(trade: dict) -> str:
    broker_pair = str(trade.get("broker_position_id") or "").strip().upper()
    if PAIR.fullmatch(broker_pair):
        return broker_pair
    raise ValueError("Keine gespeicherte OKX-Instrument-ID; ein Markt wird nicht aus Symbol und Waehrung geraten")


def _sell_vwap(book: dict, quantity: float) -> float:
    remaining = float(quantity or 0.0)
    if remaining <= 0:
        return 0.0
    value = 0.0
    for price, available in book.get("bids") or []:
        take = min(remaining, max(0.0, float(available or 0.0)))
        value += take * float(price or 0.0)
        remaining -= take
        if remaining <= max(1e-12, quantity * 1e-9):
            return value / float(quantity)
    return 0.0


def _gemessener_taker_satz(_client=None, *, trade=None) -> float:
    """Der bei OKX gemessene Taker-Satz, mit der config als Rueckfall.

    Die Oberflaeche hat bewusst keine Brokerverbindung. Der Handelskern misst
    den Satz und schreibt ihn in ``runtime_status_okx.json`` -- dieselbe
    Quelle, aus der das Dashboard ihn schon anzeigt. Vorher rechnete diese
    Seite mit der Annahme aus der config, waehrend der Bot mit dem gemessenen
    Satz verkaufte: die ROI-Linie lag ueber dem echten Verkaufskurs, und
    dieselbe Gebuehr stand in derselben Oberflaeche mit zwei Zahlen.
    """
    try:
        import okx_status
        if trade is not None:
            from broker_display_context import context
            own, current = context(trade), context(okx_status.lies(), broker="okx")
            age = okx_status.alter_sekunden(okx_status.lies())
            if (not own["bound"] or not current["bound"]
                    or own["domain_key"] != current["domain_key"]
                    or age is None or not 0 <= age <= 180):
                return float(getattr(config, "OKX_TAKER_FEE_PCT", 0.0035))
        satz = okx_status.gemessener_taker_satz()
        if satz and satz > 0:
            return float(satz)
    except Exception:
        logger.debug("Gemessener Gebuehrensatz nicht verfuegbar", exc_info=True)
    return float(getattr(config, "OKX_TAKER_FEE_PCT", 0.0035))


def _live_quote(client: OKXClient, instrument: str, quantity: float,
                tickers: dict) -> dict:
    environment = "DEMO" if client.demo else "LIVE"
    key = (str(getattr(client, "base_url", "")), environment,
           instrument, round(float(quantity or 0.0), 12))
    now = time.monotonic()
    with _LOCK:
        cached = _LIVE_CACHE.get(key)
        if cached and now - cached[0] <= 15.0:
            return dict(cached[1])
    result = {"current_price": None, "current_source": "",
              "current_executable": False, "current_at": ""}
    try:
        book = client.orderbook(instrument, depth=max(
            20, min(400, int(getattr(config, "OKX_EXECUTION_BOOK_DEPTH", 100)))))
        price = _sell_vwap(book, quantity)
        ts_ms = int(book.get("timestamp_ms") or 0)
        age = (time.time() * 1000.0 - ts_ms) / 1000.0 if ts_ms else float("inf")
        if price > 0 and -5.0 <= age <= max(
                0.5, float(getattr(config, "OKX_ORDERBOOK_MAX_AGE_SECONDS", 3.0))):
            result = {
                "current_price": price,
                "current_source": f"OKX {environment} Orderbuch · Verkauf-VWAP volle Menge",
                "current_executable": True,
                "current_at": (datetime.fromtimestamp(
                    ts_ms / 1000.0, timezone.utc).isoformat() if ts_ms else ""),
            }
    except Exception:
        logger.debug("Orderbuchkurs fuer %s nicht verfuegbar", instrument, exc_info=True)
    if result["current_price"] is None:
        ticker = tickers.get(instrument)
        bid = float(getattr(ticker, "bid", 0.0) or 0.0) if ticker else 0.0
        ticker_ts = int(getattr(ticker, "timestamp_ms", 0) or 0) if ticker else 0
        ticker_age = ((time.time() * 1000.0 - ticker_ts) / 1000.0
                      if ticker_ts else float("inf"))
        if bid > 0 and -5.0 <= ticker_age <= 30.0:
            result = {
                "current_price": bid, "current_source": f"OKX {environment} bester Geldkurs",
                "current_executable": False,
                "current_at": (datetime.fromtimestamp(
                    ticker_ts / 1000.0,
                    timezone.utc).isoformat()
                               if ticker_ts else ""),
            }
    result["market_data_environment"] = environment
    with _LOCK:
        _LIVE_CACHE[key] = (now, result)
        if len(_LIVE_CACHE) > 128:
            oldest = min(_LIVE_CACHE, key=lambda item: _LIVE_CACHE[item][0])
            _LIVE_CACHE.pop(oldest, None)
    return dict(result)


def live_metrics_for_trades(rows: list[dict]) -> list[dict]:
    """Ergaenzt offene OKX-Trades rein lesend um Live-, SL- und ROI-Werte."""
    output = [dict(row) for row in (rows or [])]
    okx_rows = [row for row in output
                if str(row.get("broker") or "").lower() == "okx"
                and not row.get("ausgestiegen_am")
                # Ohne die beim Fill gespeicherte instId wird kein Livepaar
                # geraten. Genau dieses Raten verwechselte USD/USDC/EUR.
                and PAIR.fullmatch(str(row.get("broker_position_id") or "").upper())]
    if not okx_rows:
        return output
    from broker_display_context import environment as trade_environment
    clients = {}
    for row in okx_rows:
        try:
            environment = trade_environment(row)
            if environment not in {"DEMO", "LIVE"}:
                row.update(current_price=None, current_executable=False,
                           live_result_quality="ENVIRONMENT_UNKNOWN")
                continue
            if environment not in clients:
                client = _client_for_environment(environment)
                clients[environment] = (client, client.tickers())
            client, tickers = clients[environment]
            instrument = _instrument(row)
            quantity = float(row.get("menge") or 0.0)
            position = _position_lines(
                str(row.get("symbol") or "").upper(), instrument, trade=row)
            row["instrument"] = instrument
            row["market_quote_ccy"] = instrument.rsplit("-", 1)[-1]
            row["settlement_currency"] = str(
                position.get("trade_quote_ccy") or row.get("waehrung") or "").upper()
            row["stop_price"] = position.get("stop")
            row["broker_take_profit"] = position.get("take_profit")
            row.update(_live_quote(client, instrument, quantity, tickers))
            current = float(row.get("current_price") or 0.0)
            entry = float(row.get("einstieg_preis") or 0.0)
            entry_fee = float(row.get("einstieg_gebuehr") or 0.0)
            # v9.1: der GEMESSENE Satz, wie im Handelskern -- nicht die
            # Annahme aus der config. Vorher rechnete diese Seite mit 0,35 %,
            # waehrend der Bot mit dem bei OKX abgefragten Satz (typisch
            # 0,10 %) verkaufte. Die eingezeichnete ROI-Linie lag dadurch ueber
            # dem Kurs, bei dem tatsaechlich verkauft wird, und das Dashboard
            # zeigte daneben den gemessenen Satz -- zwei Zahlen fuer dieselbe
            # Sache in derselben Oberflaeche.
            fee_pct = _gemessener_taker_satz(client, trade=row)
            row["live_result_quality"] = "ESTIMATED"
            compatible_ccy = (row["settlement_currency"] == row["market_quote_ccy"]
                              and str(row.get("waehrung") or "").upper() == row["market_quote_ccy"])
            if not compatible_ccy:
                row.update(open_net_pnl=None, open_net_pct=None, active_roi_price=None,
                           stop_price=None, broker_take_profit=None,
                           live_result_quality="CURRENCY_MISMATCH")
            if entry > 0 and quantity > 0 and row.get("einstieg_gebuehr") is not None and compatible_ccy:
                open_cost = entry * quantity + entry_fee
                if current > 0:
                    exit_fee = current * quantity * fee_pct
                    net = (current - entry) * quantity - entry_fee - exit_fee
                    row["open_net_pnl"] = round(net, 10)
                    row["open_net_pct"] = round(
                        100.0 * net / open_cost, 6) if open_cost > 0 else None
                    stop = float(row.get("stop_price") or 0.0)
                    target = float(row.get("broker_take_profit") or 0.0)
                    row["stop_distance_pct"] = (
                        round(100.0 * (stop / current - 1.0), 4) if stop > 0 else None)
                    row["broker_take_distance_pct"] = (
                        round(100.0 * (target / current - 1.0), 4)
                        if target > 0 else None)
                # v9.1: Auch eine PAUSIERTE Position bekommt kein ROI-Ziel
                # mehr angezeigt. Der Kern ruft _strategy_exit nur fuer AUTO
                # auf -- bei BEOBACHTEN wartete der Nutzer auf einen Ausstieg,
                # der nie kommt. Genau diesen Zustand setzt der Kern selbst,
                # wenn ein Strategie-Snapshot nicht reproduzierbar ist.
                if (str(row.get("entry_strategy_mode") or "") == "FREQTRADE_SAMPLE"
                        and str(position.get("verwaltung") or "UNKNOWN").upper() == "AUTO"):
                    from freqtrade_sample_strategy import roi_exit_price, roi_threshold
                    opened = _utc(row.get("eingestiegen_am"))
                    elapsed = max(0.0, (datetime.now(timezone.utc) - opened).total_seconds() / 60.0)
                    threshold = roi_threshold(elapsed)
                    entry_fee_pct = entry_fee / (entry * quantity) if entry * quantity > 0 else 0.0
                    row["elapsed_minutes"] = round(elapsed, 2)
                    row["active_roi_pct"] = threshold * 100.0
                    row["active_roi_price"] = roi_exit_price(
                        entry, threshold, entry_fee_pct=entry_fee_pct,
                        exit_fee_pct=fee_pct)
        except Exception:
            logger.debug("Livewerte fuer Trade %s nicht berechenbar",
                         row.get("trade_id"), exc_info=True)
    return output


def chart_for_trade(trade_id: int, *, force: bool = False, bar: str = "") -> dict:
    """Kerzen, Kauf-/Verkaufsmarker und bekannte Schutzlinien (OKX und eToro)."""
    key = int(trade_id)
    requested_bar = str(bar or "").lower()
    cache_key = (key, requested_bar)
    now_mono = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(cache_key)
        if cached and not force and now_mono - cached[0] < 60:
            return dict(cached[1])

    trade = trade_detail(key)
    if not trade:
        return {"ok": False, "fehler": "Trade nicht gefunden"}
    broker_name = str(trade.get("broker") or "").lower()
    if broker_name == "etoro":
        try:
            result = _etoro_chart(trade, key, requested_bar)
        except Exception as exc:
            result = {"ok": False, "fehler": f"eToro-Kerzendaten nicht verfügbar: {type(exc).__name__}: {str(exc)[:180]}"}
        with _LOCK:
            _CACHE[cache_key] = (now_mono, result)
        return dict(result)
    if broker_name != "okx":
        return {"ok": False, "fehler": "Kerzen sind fuer OKX- und eToro-Trades verfuegbar; dieser Broker ist unbekannt."}
    try:
        entry_time = _utc(trade.get("eingestiegen_am"))
        exit_time = (_utc(trade.get("ausgestiegen_am"))
                     if trade.get("ausgestiegen_am") else datetime.now(timezone.utc))
        if exit_time < entry_time:
            raise ValueError("Ausstiegszeit liegt vor der Einstiegszeit")
        trade_seconds = (exit_time - entry_time).total_seconds()
        if requested_bar in OKX_BARS:
            bar_key = requested_bar
        else:
            auto_bar, _ = _timeframe(trade_seconds)
            bar_key = next(k for k, v in OKX_BARS.items() if v[0] == auto_bar)
        okx_bar, bar_seconds = OKX_BARS[bar_key]
        # 10.4.0: bis zu MAX_CANDLES (OKX liefert 100 je Seite, rueckwaerts
        # paginiert); Vorlauf 40 Kerzen vor dem Einstieg, Nachlauf 20.
        window_start = entry_time - timedelta(seconds=bar_seconds * 40)
        window_end = exit_time + timedelta(seconds=bar_seconds * 20)
        instrument = _instrument(trade)
        if str(trade.get("waehrung") or "").upper() != instrument.rsplit("-",1)[-1]:
            raise ValueError("Ledgerwaehrung und Kerzenwaehrung verschieden; keine Preislinien ohne belegten Umrechnungskurs")
        from broker_display_context import environment as trade_environment
        client = _client_for_environment(trade_environment(trade))
        import pandas as pd
        pages = []
        end_ms = int(window_end.timestamp() * 1000)
        for _ in range(MAX_CANDLES // 100):
            frame = client.historical_candles(instrument, bar=okx_bar, limit=100,
                                              end_ms=end_ms, nur_abgeschlossen=True)
            if frame.empty:
                break
            pages.append(frame)
            oldest = frame.index.min()
            if oldest <= window_start:
                break
            end_ms = int(oldest.timestamp() * 1000)
        if not pages:
            return {"ok": False, "fehler": "OKX liefert für diesen Zeitraum keine abgeschlossenen Kerzen"}
        frame = pd.concat(pages)
        frame = frame[~frame.index.duplicated(keep="first")].sort_index()
        frame = frame[(frame.index >= window_start) & (frame.index <= window_end)]
        if frame.empty:
            return {"ok": False, "fehler": "Der historische OKX-Ausschnitt enthält den Tradezeitraum nicht"}
        candles = [{
            "zeit": index.to_pydatetime().astimezone(timezone.utc).isoformat(),
            "open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"]),
            "volume": float(row["volume"]),
        } for index, row in frame.iterrows()]
        letzter = float(candles[-1]["close"])
        einstieg = float(trade.get("einstieg_preis") or 0)
        menge = float(trade.get("menge") or 0)
        offen = not bool(trade.get("ausgestiegen_am"))
        instrument = _instrument(trade)
        lines = _position_lines(
            str(trade.get("symbol") or "").upper(), instrument, trade=trade) if offen else {
            "stop": None, "take_profit": None}
        live = live_metrics_for_trades([trade])[0] if offen else {}
        extra = [v for v in (trade.get("einstieg_preis"), trade.get("ausstieg_preis"), lines.get("stop"),
                             lines.get("take_profit"), live.get("active_roi_price"), live.get("current_price")) if v]
        result = {
            "ok": True, "trade_id": key, "symbol": trade.get("symbol"),
            "display_context": __import__("broker_display_context").context(trade),
            "instrument": instrument, "bar": bar_key, "bar_seconds": bar_seconds,
            "available_bars": list(OKX_BARS), "axis": _axis_bounds(candles, extra),
            "data_source": f"OKX {trade_environment(trade)} Historie",
            "nur_abgeschlossene_kerzen": True, "candles": candles,
            "kauf": {"zeit": entry_time.isoformat(), "preis": trade.get("einstieg_preis")},
            "verkauf": ({"zeit": _utc(trade["ausgestiegen_am"]).isoformat(),
                          "preis": trade.get("ausstieg_preis")}
                         if trade.get("ausgestiegen_am") else None),
            "stop": lines.get("stop"), "take_profit": lines.get("take_profit"),
            "active_roi_price": live.get("active_roi_price"),
            "active_roi_pct": live.get("active_roi_pct"),
            "current_price": live.get("current_price"),
            "current_source": live.get("current_source"),
            "letzter_schluss": letzter,
            "offenes_ergebnis": None,  # A historical close is not a live executable net result.
            "waehrung": instrument.rsplit("-", 1)[-1],
            "settlement_currency": live.get("settlement_currency") or "",
            "entry_strategy_mode": trade.get("entry_strategy_mode") or "UNBEKANNT",
            "strategie_version": trade.get("strategie_version") or "UNBEKANNT",
            "hinweis": "Kauf und Verkauf markieren die tatsächlichen Ledger-Zeitpunkte/Fills.",
        }
    except Exception as exc:
        result = {"ok": False, "fehler": f"Kerzendaten nicht verfügbar: {type(exc).__name__}: {str(exc)[:180]}"}
    with _LOCK:
        if len(_CACHE) >= 64:
            oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
            _CACHE.pop(oldest, None)
        _CACHE[cache_key] = (now_mono, result)
    return dict(result)


__all__ = ["chart_for_trade", "live_metrics_for_trades"]
