"""Dynamische Krypto-Auswahl auf Basis der OKX-Spotmaerkte (DU-003).

ABLAUF
======
    1. Broker-Catalog       oeffentliche Entdeckung plus privater Kontokatalog
    2. Harte Filter         SPOT, Bot-Cash EUR/USD/USDC, Unified-USD nur mit
                            passender tradeQuoteCcyList, Status LIVE, Mindestalter,
                            Mindestumsatz, Spanne, bekannte Handelsregeln,
                            Sperrliste (Hebel-Token, Stablecoins)
    3. Cheap Ranking        aus EINEM tickers-Aufruf fuer alle Maerkte
    4. Vorauswahl           die besten N (Standard 100)
    5. Quality Ranking      Orderbuch + Kerzen/ATR nur fuer diese N
    6. Einstufung           ETABLIERT (direkt handelbar) oder
                            KANDIDAT (erst Beobachtung)

WARUM DIE SPERRLISTE WICHTIG IST
================================
OKX listet Produkte, die aussehen wie ein Coin, aber keiner sind:

    BTC3L / BTC3S   gehebelte Token mit taeglichem Rebalancing. Sie
                    verlieren auch in einem Seitwaertsmarkt Wert.
    USDC / DAI      Stablecoins. Sie bewegen sich definitionsgemaess nicht
                    und wuerden nur Analyseplaetze blockieren.

Beides gehoert nicht in ein Trendfolge-Universum und wird hart gesperrt --
unabhaengig davon, wie gut Umsatz oder Spanne aussehen.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Iterable, Optional

import pandas as pd

from .modelle import TIER_ETABLIERT, TIER_KANDIDAT, UniverseKandidat, UniverseScore
from .scoring import cheap_score, quality_score

logger = logging.getLogger(__name__)

# Hebel-Token erkennt man zuverlaessig am Namensmuster.
HEBEL_MUSTER = re.compile(r"^[A-Z0-9]{2,}(3L|3S|5L|5S|UP|DOWN|BULL|BEAR)$")

# Stablecoins sind faktisch Devisenpaare und blockieren Analyseplaetze.
STABLECOINS = {
    "USDC", "USDT", "DAI", "TUSD", "USDP", "FDUSD", "PYUSD", "EURT",
    "EURC", "USDD", "GUSD", "LUSD", "USDE", "SUSD", "BUSD", "EUR", "USD",
}


class CryptoUniverseSelector:
    """Waehlt aus allen OKX-Spotmaerkten das aktive Krypto-Universum."""

    def __init__(self, client, cfg=None):
        """client: OKXClient (nur oeffentliche Endpunkte werden benutzt)."""
        import config as _config
        self.client = client
        self.cfg = cfg or _config

        self.quote = str(getattr(self.cfg, "OKX_QUOTE_CCY", "EUR")).upper()
        quotes = getattr(
            self.cfg, "OKX_ALLOWED_QUOTE_CCY", ("EUR", "USD", "USDC"))
        self.quotes = tuple(dict.fromkeys(str(x).upper() for x in quotes)) or (self.quote,)
        self.require_funded_quote = bool(getattr(
            self.cfg, "OKX_REQUIRE_FUNDED_TRADE_QUOTE", True))
        self.min_alter_tage = float(getattr(self.cfg, "CRYPTO_UNIVERSE_MIN_AGE_DAYS", 30.0))
        self.min_umsatz = float(getattr(self.cfg, "CRYPTO_UNIVERSE_MIN_QUOTE_VOLUME", 2_000_000.0))
        self.max_spread = float(getattr(self.cfg, "CRYPTO_UNIVERSE_MAX_SPREAD_PCT", 0.006))
        self.vorauswahl = int(getattr(self.cfg, "CRYPTO_UNIVERSE_PRESELECTION", 100))
        self.sperrliste = {s.upper() for s in getattr(self.cfg, "CRYPTO_UNIVERSE_BLOCKLIST", ())}

        # Schwellen, ab denen ein Coin als "etabliert" gilt und ohne
        # Beobachtungsphase direkt gehandelt werden darf.
        self.etabliert_alter_tage = float(getattr(self.cfg, "CRYPTO_ESTABLISHED_MIN_AGE_DAYS", 365.0))
        self.etabliert_umsatz = float(getattr(self.cfg, "CRYPTO_ESTABLISHED_MIN_QUOTE_VOLUME", 50_000_000.0))
        self.etabliert_spread = float(getattr(self.cfg, "CRYPTO_ESTABLISHED_MAX_SPREAD_PCT", 0.0015))
        self._preferred_core = tuple(
            str(s).upper() for s in getattr(self.cfg, "CRYPTO_CORE_SYMBOLS", ())
            if str(s).strip())
        # Bis zum ersten Kontokatalog-Lauf nur fuer Diagnose/Einzeltests. Im
        # echten Lauf wird diese Wunschliste vollstaendig durch den
        # kontoseitig ausfuehrbaren Kern ersetzt.
        self.kernliste: set[str] = set(self._preferred_core)

    # -- Schritt 1+2: Catalog und harte Filter ------------------------------
    def eligible_pool(self) -> tuple[list[UniverseKandidat], list[tuple[str, str]]]:
        """Exakt 20 feste plus bis zu 30 monatlich dynamische Basiswerte.

        Die Filter hier entscheiden nur ueber die Aufnahme in die Beobachtung.
        Der Geldpfad prueft unmittelbar vor einem Kauf erneut und strenger.
        """
        # Freqtrade-artige breite Entdeckung, aber mit einer entscheidenden
        # Sicherheitsgrenze: Nur die Schnittmenge mit dem privaten
        # Kontokatalog darf spaeter handelbar werden.
        try:
            public_instrumente = self.client.public_instruments()
        except Exception as exc:
            logger.info("Oeffentlicher OKX-Katalog nicht abrufbar: %s", exc)
            public_instrumente = {}
        instrumente = self.client.instruments()
        tickers = self.client.tickers()
        self._public_catalog_count = len(public_instrumente)
        self._account_catalog_count = len(instrumente)
        self._public_only_count = len(set(public_instrumente) - set(instrumente))

        # Jeder Kanal besitzt sein eigenes Guthaben. Ein Coin darf nur dann
        # als handelbar erscheinen, wenn wenigstens eine von OKX fuer genau
        # dieses Instrument erlaubte Abrechnungswaehrung auch finanziert ist.
        self._funded_quotes: set[str] | None = None
        self._quote_cash: dict[str, float] = {}
        if self.require_funded_quote:
            try:
                balances = self.client.balances()
                self._quote_cash = {
                    quote: float((balances.get(quote) or {}).get(
                        "cash", (balances.get(quote) or {}).get("frei", 0.0)) or 0.0)
                    for quote in self.quotes
                }
                self._funded_quotes = {
                    quote for quote, amount in self._quote_cash.items() if amount > 0}
            except Exception as exc:
                if bool(getattr(self.client, "hat_zugangsdaten", False)):
                    logger.warning("OKX-Handelskanal-Guthaben nicht abrufbar: %s", exc)
                    self._funded_quotes = set()
                else:
                    # Reine Offline-/Komponententests besitzen kein Konto.
                    self._funded_quotes = None

        # Das Alter gehoert zum Basiswert, nicht zu einer einzelnen neu
        # eingefuehrten Quote-Variante. LINK-USD darf nicht als 14 Tage alter
        # Coin gelten, wenn LINK auf OKX seit Jahren gelistet ist.
        self._base_age_days: dict[str, float] = {}
        age_catalog = dict(public_instrumente)
        age_catalog.update(instrumente)
        for meta in age_catalog.values():
            if getattr(meta, "ist_live", False):
                base = str(getattr(meta, "base_ccy", "")).upper()
                self._base_age_days[base] = max(
                    self._base_age_days.get(base, 0.0),
                    float(getattr(meta, "alter_tage", 0.0) or 0.0),
                )

        # Jede Quote wird nur mit einem tatsaechlich beobachteten Spotkurs nach
        # EUR vergleichbar gemacht. Es gibt keine still angenommene USD- oder
        # USDC-Paritaet.
        rates = {"EUR": 1.0}
        # Nur beobachtete Spotkurse, aber auch sichere Zwei-Schritt-Pfade
        # (z. B. USDC->USD->EUR). Damit wird kein Stablecoin still mit 1 USD
        # gleichgesetzt und trotzdem nicht fast das ganze USD-Universum
        # verworfen, nur weil OKX gerade kein direktes USDC-EUR listet.
        graph: dict[str, dict[str, float]] = {}
        for iid, ticker in tickers.items():
            last = float(getattr(ticker, "last", 0.0) or 0.0)
            if last <= 0 or "-" not in str(iid):
                continue
            base, quote = str(iid).upper().split("-", 1)
            graph.setdefault(base, {})[quote] = last
            graph.setdefault(quote, {})[base] = 1.0 / last
        def _rate_to_eur(source: str, max_hops: int = 3) -> float | None:
            """Nur beobachtete Spot-Kreuzkurse; keine Stablecoin-Paritaet."""
            source = str(source).upper()
            if source == "EUR":
                return 1.0
            queue = [(source, 1.0, 0)]
            visited = {source}
            while queue:
                ccy, rate, hops = queue.pop(0)
                if hops >= max_hops:
                    continue
                for nxt, edge in sorted(graph.get(ccy, {}).items()):
                    if nxt in visited or edge <= 0:
                        continue
                    value = rate * edge
                    if nxt == "EUR":
                        return value
                    visited.add(nxt)
                    queue.append((nxt, value, hops + 1))
            return None

        market_quotes = tuple(dict.fromkeys(
            str(meta.quote_ccy).upper() for meta in instrumente.values()
            if str(getattr(meta, "quote_ccy", "")).strip()))
        for source in market_quotes:
            if source == "EUR":
                continue
            observed = _rate_to_eur(source)
            if observed and observed > 0:
                rates[source] = observed

        pair_reasons: dict[str, str] = {}
        eligible_pairs: set[str] = set()
        for inst_id, meta in instrumente.items():
            grund = self._harter_filter(
                meta, tickers.get(inst_id), quote_rate=rates.get(str(meta.quote_ccy).upper()))
            pair_reasons[str(inst_id).upper()] = grund
            if not grund:
                eligible_pairs.add(str(inst_id).upper())

        # Nur wirklich fuer dieses Konto und die erlaubten Bot-Cashwaehrungen
        # ausfuehrbare Paare duerfen den stabilen 20er-Kern bilden. Der
        # statische Wunschzettel liefert nur die bevorzugte Reihenfolge.
        import okx_tradeable_core
        self.kernliste, self._core_state = okx_tradeable_core.select(
            instruments=instrumente, tickers=tickers,
            pair_reasons=pair_reasons, quote_rates=rates,
            preferred=self._preferred_core,
            limit=int(getattr(self.cfg, "CRYPTO_CORE_LIMIT", 20)),
            allowed_quotes=self.quotes,
            funded_quotes=self._funded_quotes,
        )

        # Der aktuelle Snapshot bildet sofort 30 dynamische Beobachtungswerte;
        # danach wird ihre Mitgliedschaft nur monatlich aus der lokalen
        # 30-Tage-Historie neu berechnet.
        try:
            import crypto_dynamic_30
            self._dynamic_state = crypto_dynamic_30.collect_and_update(
                instrumente, tickers, allowed_quotes=self.quotes,
                quote_rates=rates, eligible_pairs=eligible_pairs,
                fixed_bases=set(self.kernliste),
            )
        except Exception as exc:
            logger.warning("DYNAMIC_30 Datensammlung fehlgeschlagen; letzte Mitgliedschaft bleibt: %s", exc)
            try:
                import crypto_dynamic_30
                self._dynamic_state = crypto_dynamic_30.mark_stale(str(exc))
            except Exception:
                self._dynamic_state = {"status": "STALE", "items": [], "error": str(exc)[:300]}
                logger.warning("DYNAMIC_30 STALE-Status konnte nicht persistiert werden", exc_info=True)

        import crypto_dynamic_30
        dynamisch = crypto_dynamic_30.current_bases() - set(self.kernliste)
        ausgewaehlt = set(self.kernliste) | set(dynamisch)
        kandidaten: list[UniverseKandidat] = []
        abgelehnt: list[tuple[str, str]] = []

        # Pro Basis wird das liquideste vergleichbare Paar gewaehlt. Ein
        # BASE-USD-Paar ist nur zulaessig, wenn seine tradeQuoteCcyList eine
        # echte und finanzierte Bot-Cashwaehrung (EUR/USD/USDC) ausweist.
        # Die alte Implementierung nahm das erste bevorzugte Quote-Paar und
        # konnte dadurch ein duennes EUR-Paar vor ein liquides USDC-Paar setzen.
        quote_rank = {quote: index for index, quote in enumerate(self.quotes)}
        pro_basis: dict[str, list[tuple[str, object, object]]] = {}
        for inst_id, meta in instrumente.items():
            iid = str(inst_id).upper()
            grund = pair_reasons.get(iid, "")
            if grund:
                abgelehnt.append((iid, grund))
                continue
            base = str(meta.base_ccy).upper()
            if base not in ausgewaehlt:
                abgelehnt.append((iid, "nicht im kontoseitig handelbaren Kern oder in DYNAMIC_30"))
                continue
            pro_basis.setdefault(base, []).append((iid, meta, tickers[inst_id]))

        def _pair_key(item):
            iid, meta, ticker = item
            quote = str(meta.quote_ccy).upper()
            rate = rates.get(quote)
            vergleichbar = float(ticker.vol_24h_quote) * rate if rate else -1.0
            direct_rank = quote_rank.get(quote, 100 if quote == "USD" else 999)
            # Nur Paare nach harten Kosten-/Spread-/Guthabenfiltern. EUR ist
            # die Standardabrechnung; Umsatz allein ist kein Kostenvorteil.
            lane_rank = 0 if self._trade_lane(meta) == "EUR" else 1
            return (lane_rank, -vergleichbar, direct_rank, iid)

        for base in sorted(pro_basis):
            paare = sorted(pro_basis[base], key=_pair_key)
            inst_id, meta, t = paare[0]
            for secondary, _meta, _ticker in paare[1:]:
                abgelehnt.append((secondary, "sekundaeres Quote-Paar; EUR-Praeferenz nach Eignungspruefung"))
            t = tickers[inst_id]
            trade_quote = self._trade_lane(meta)
            kandidaten.append(UniverseKandidat(
                symbol=meta.base_ccy,
                broker="okx",
                asset_type="crypto",
                inst_id=inst_id,
                preis=t.last,
                bid=t.bid,
                ask=t.ask,
                spread_pct=t.spread_pct,
                volumen_quote_24h=t.vol_24h_quote,
                volumen_basis_24h=t.vol_24h_base,
                change_24h_pct=t.change_24h_pct,
                alter_tage=self._base_age_days.get(base, meta.alter_tage),
                handelbar=True,
                datenalter_sekunden=max(0.0, time.time() - (t.timestamp_ms / 1000.0)) if t.timestamp_ms else 0.0,
                name=meta.base_ccy,
                zusatz={"tick_size": meta.tick_size, "lot_size": meta.lot_size,
                        "min_size": meta.min_size, "quote": meta.quote_ccy,
                        "trade_quote_ccy_list": list(
                            getattr(meta, "trade_quote_ccy_list", ()) or (meta.quote_ccy,)),
                        "trade_quote_ccy": trade_quote,
                        "funded_trade_quotes": [
                            q for q in self.quotes
                            if q in set(getattr(meta, "trade_quote_ccy_list", ()) or
                                        (meta.quote_ccy,))
                            and (self._funded_quotes is None or q in self._funded_quotes)],
                        "quote_rate_to_eur": rates.get(str(meta.quote_ccy).upper()),
                        "origin": ("FIXED_CORE_20" if base in self.kernliste else "DYNAMIC_30")},
            ))

        # Laufzeitkern und Pool werden getrennt ausgewiesen. Ein Kernwert ist
        # hier per Definition bereits voll geeignet; alte statische Kernwerte
        # werden vom Manager entfernt statt dauerhaft als tote Plaetze zu
        # erscheinen.
        eligible_bases = {str(k.symbol).upper() for k in kandidaten}
        self._ineligible_reasons = {}
        for base in sorted(self.kernliste - eligible_bases):
            relevante = []
            for iid, meta in instrumente.items():
                if str(getattr(meta, "base_ccy", "")).upper() == base:
                    grund = pair_reasons.get(str(iid).upper(), "")
                    if grund and grund not in relevante:
                        relevante.append(grund)
            self._ineligible_reasons[base] = "; ".join(relevante[:3]) or (
                f"kein live Spot-Paar gegen {', '.join(self.quotes)}")
        hard_bases = {
            str(meta.base_ccy).upper() for iid, meta in instrumente.items()
            if str(iid).upper() in eligible_pairs
        }
        self._hard_eligible_bases = set(hard_bases)
        logger.info(
            "OKX-Universum: %d Kontoinstrumente, %d geeignete Basen, "
            "Kern %d/%d, Dynamik %d/%d, Pool %d.",
            len(instrumente), len(hard_bases), len(self.kernliste),
            int(getattr(self.cfg, "CRYPTO_CORE_LIMIT", 20)), len(dynamisch),
            int(getattr(self.cfg, "CRYPTO_UNIVERSE_DYNAMIC_LIMIT", 30)),
            len(kandidaten))
        return kandidaten, abgelehnt

    def _trade_lane(self, meta) -> str:
        """Deterministische, kontoseitig erlaubte Abrechnungswaehrung."""
        accepted = {
            str(x).upper() for x in
            (getattr(meta, "trade_quote_ccy_list", ()) or (meta.quote_ccy,))
            if str(x).strip()
        }
        for quote in self.quotes:
            if quote not in accepted:
                continue
            if self._funded_quotes is not None and quote not in self._funded_quotes:
                continue
            return quote
        return ""

    def _harter_filter(self, meta, ticker, *, quote_rate: float | None = None) -> str:
        """Gibt den Ablehnungsgrund zurueck oder '' bei bestanden."""
        trade_quotes = {
            str(x).upper() for x in
            (getattr(meta, "trade_quote_ccy_list", ()) or (meta.quote_ccy,))
            if str(x).strip()
        }
        executable = trade_quotes.intersection(set(self.quotes))
        direct_market = str(meta.quote_ccy).upper() in set(self.quotes)
        unified_usd = str(meta.quote_ccy).upper() == "USD" and bool(executable)
        if not direct_market and not unified_usd:
            return (f"Marktquote {meta.quote_ccy} ist keine Bot-Cashwaehrung; "
                    f"tradeQuoteCcyList={','.join(sorted(trade_quotes)) or 'leer'}, "
                    f"erlaubt sind {', '.join(self.quotes)}")
        if not executable:
            return ("keine nutzbare OKX-Abrechnungswaehrung; tradeQuoteCcyList="
                    + ",".join(sorted(trade_quotes)))
        if self._funded_quotes is not None and not executable.intersection(
                self._funded_quotes):
            return ("kein finanzierter Handelskanal; tradeQuoteCcyList="
                    + ",".join(sorted(trade_quotes)) + "; frei="
                    + ", ".join(f"{q} {self._quote_cash.get(q, 0.0):.2f}"
                                for q in self.quotes))
        if not meta.ist_live:
            return f"Instrumentstatus {meta.state!r} statt 'live'"
        base = meta.base_ccy.upper()
        if base in self.sperrliste:
            return "steht auf der Sperrliste"
        if base in STABLECOINS:
            return "Stablecoin"
        if HEBEL_MUSTER.match(base):
            return "gehebeltes Produkt (taegliches Rebalancing)"
        if not meta.tick_size or not meta.lot_size:
            return "Handelsregeln (Tick/Lot) unbekannt"
        if float(meta.min_size or 0) <= 0:
            return "Mindestgroesse unbekannt"
        base_age = float(getattr(self, "_base_age_days", {}).get(base, meta.alter_tage) or 0.0)
        if base_age < self.min_alter_tage:
            return f"erst {base_age:.0f} Tage gelistet (Minimum {self.min_alter_tage:.0f})"
        if ticker is None:
            return "kein Ticker verfuegbar"
        if ticker.last <= 0:
            return "kein gueltiger Preis"
        if not quote_rate or quote_rate <= 0:
            return f"keine belastbare EUR-Umrechnung fuer {meta.quote_ccy}"
        normalized_volume = float(ticker.vol_24h_quote) * float(quote_rate)
        if normalized_volume <= 0:
            return "kein positiver Tagesumsatz"
        # Seit 9.0.12 ist der Tagesumsatz kein aktienaehnlicher harter
        # Mindestfilter mehr. Er bestimmt die Reihenfolge fuer Kern und
        # DYNAMIC_30. Die Ausfuehrung prueft zusaetzlich die Orderbuchtiefe
        # fuer die konkrete Ordergroesse.
        if ticker.bid <= 0 or ticker.ask <= 0:
            return "kein beidseitiges Orderbuch"
        spread = ticker.spread_pct
        if spread <= 0:
            return "Spanne nicht berechenbar"
        if spread > self.max_spread:
            return f"Spanne {spread * 100:.2f} % ueber Grenze {self.max_spread * 100:.2f} %"
        return ""

    # -- Schritt 3+4: guenstige Vorauswahl ----------------------------------
    def cheap_ranking(self, kandidaten: list[UniverseKandidat]) -> list[tuple[UniverseKandidat, UniverseScore]]:
        bewertet = [(k, cheap_score(k, asset_type="crypto")) for k in kandidaten]
        bewertet.sort(key=lambda x: x[1].gesamt, reverse=True)
        return bewertet

    # -- Schritt 5: teure Qualitaetsbewertung -------------------------------
    def quality_ranking(self, vorauswahl: list[UniverseKandidat], *,
                        bar: str = "15m", kerzen: int = 96
                        ) -> list[tuple[UniverseKandidat, UniverseScore]]:
        """Reichert die Vorauswahl mit Orderbuch und ATR an und bewertet neu."""
        ergebnis: list[tuple[UniverseKandidat, UniverseScore]] = []
        for kandidat in vorauswahl:
            try:
                self._reichere_an(kandidat, bar=bar, kerzen=kerzen)
            except Exception as exc:
                # Ein einzelner Datenfehler darf nicht den ganzen Lauf kippen.
                logger.debug("Qualitaetsdaten %s nicht verfuegbar: %s", kandidat.inst_id, exc)
                kandidat.zusatz["quality_fehler"] = str(exc)[:200]
            ergebnis.append((kandidat, quality_score(kandidat, asset_type="crypto")))
        ergebnis.sort(key=lambda x: x[1].gesamt, reverse=True)
        return ergebnis

    def _reichere_an(self, kandidat: UniverseKandidat, *, bar: str, kerzen: int) -> None:
        buch = self.client.orderbook(kandidat.inst_id, depth=20)
        tiefe = 0.0
        for preis, menge in (buch.get("bids") or [])[:10]:
            tiefe += preis * menge
        for preis, menge in (buch.get("asks") or [])[:10]:
            tiefe += preis * menge
        kandidat.orderbuch_tiefe_quote = tiefe

        df = self.client.candles(kandidat.inst_id, bar=bar, limit=kerzen, nur_abgeschlossen=True)
        kandidat.kerzen_anzahl = int(len(df))
        # An inactive candle market must be visible per instrument, even when
        # the ticker/24h turnover is fresh. Existing score and hard entry gates
        # remain unchanged: these observations are not a new strategy filter.
        observation = dict(df.attrs.get("candle_quality", {}))
        if observation:
            kandidat.zusatz["candle_quality"] = observation
            if observation.get("reasons"):
                kandidat.zusatz["candle_quality_warning"] = "; ".join(observation["reasons"])
        if df.empty or len(df) < 10:
            kandidat.kerzen_luecken = max(0, kerzen - len(df))
            return
        kandidat.atr_pct = self._atr_prozent(df)
        kandidat.kerzen_luecken = self._luecken(df, bar)
        kandidat.spread_stabilitaet = self._spread_stabilitaet(df)

    @staticmethod
    def _atr_prozent(df: pd.DataFrame, laenge: int = 14) -> float:
        """Durchschnittliche Kerzenspanne relativ zum Preis."""
        hoch, tief, schluss = df["high"], df["low"], df["close"]
        vorher = schluss.shift(1)
        spanne = pd.concat([
            hoch - tief,
            (hoch - vorher).abs(),
            (tief - vorher).abs(),
        ], axis=1).max(axis=1)
        atr = spanne.rolling(min(laenge, max(2, len(df) - 1))).mean().iloc[-1]
        letzter = float(schluss.iloc[-1] or 0.0)
        if letzter <= 0 or pd.isna(atr):
            return 0.0
        return float(atr) / letzter

    @staticmethod
    def _luecken(df: pd.DataFrame, bar: str) -> int:
        """Zaehlt fehlende Kerzen -- ein direkter Datenqualitaetsindikator."""
        from broker.okx import BAR_SECONDS
        sekunden = BAR_SECONDS.get(bar, 900)
        if len(df) < 3:
            return 0
        abstaende = df.index.to_series().diff().dt.total_seconds().dropna()
        return int((abstaende > sekunden * 1.5).sum())

    @staticmethod
    def _spread_stabilitaet(df: pd.DataFrame) -> float:
        """Naeherung ueber die Streuung der Kerzenspannen.

        Ein echtes Spread-Zeitreihenmass braeuchte Tickdaten. Die Streuung
        der High-Low-Spannen ist ein guter, kostenloser Stellvertreter:
        springende Spannen bedeuten in der Praxis auch springende Spreads.
        """
        spanne = ((df["high"] - df["low"]) / df["close"].replace(0, pd.NA)).dropna()
        if len(spanne) < 5:
            return 1.0
        mittel = float(spanne.mean())
        if mittel <= 0:
            return 1.0
        streuung = float(spanne.std()) / mittel
        # Streuung 0 -> 1,0 ; Streuung 2 und mehr -> 0,0
        return max(0.0, min(1.0, 1.0 - streuung / 2.0))

    # -- Schritt 6: Einstufung ---------------------------------------------
    def einstufung(self, kandidat: UniverseKandidat) -> tuple[str, str]:
        """ETABLIERT (direkt handelbar) oder KANDIDAT (erst Beobachtung)?"""
        base = kandidat.symbol.upper()
        if base in self.kernliste:
            return TIER_ETABLIERT, "steht auf der Kernliste"
        gruende = []
        if kandidat.alter_tage < self.etabliert_alter_tage:
            gruende.append(f"erst {kandidat.alter_tage:.0f} Tage gelistet")
        if kandidat.volumen_quote_24h < self.etabliert_umsatz:
            gruende.append(f"Umsatz {kandidat.volumen_quote_24h / 1e6:.1f} Mio unter "
                           f"{self.etabliert_umsatz / 1e6:.0f} Mio")
        if kandidat.spread_pct > self.etabliert_spread:
            gruende.append(f"Spanne {kandidat.spread_pct * 100:.3f} % ueber "
                           f"{self.etabliert_spread * 100:.3f} %")
        if gruende:
            return TIER_KANDIDAT, "; ".join(gruende)
        return TIER_ETABLIERT, (f"{kandidat.alter_tage:.0f} Tage gelistet, "
                                f"Umsatz {kandidat.volumen_quote_24h / 1e6:.0f} Mio, "
                                f"Spanne {kandidat.spread_pct * 100:.3f} %")

    # -- Gesamtlauf ---------------------------------------------------------
    def auswahl(self, *, bar: str = "15m", favoriten: Iterable[str] = ()) -> dict:
        """Fuehrt den kompletten Auswahlprozess aus.

        Rueckgabe enthaelt bewusst auch die Abgelehnten: ohne sie laesst sich
        spaeter nicht beantworten, warum ein bestimmter Coin fehlt.
        """
        start = time.time()
        pool, abgelehnt = self.eligible_pool()
        billig = self.cheap_ranking(pool)
        vorauswahl = [k for k, _ in billig[:max(10, self.vorauswahl)]]

        # Maximal fuenf Favoriten duerfen zusaetzlich bis zur vollstaendigen
        # Qualitaetsbewertung gelangen. Das ist KEINE Aufnahmegarantie: sie
        # stehen nur neben der normalen Vorauswahl und koennen weiterhin an
        # Handelsstatus, Mindestalter, Umsatz, Spread, Score oder Bewaehrung
        # scheitern.
        favoriten_set = {str(x).strip().upper().split("-", 1)[0]
                         for x in (favoriten or ()) if str(x).strip()}
        bereits = {k.symbol.upper() for k in vorauswahl}
        favoriten_kandidaten = [k for k, _ in billig
                                 if k.symbol.upper() in favoriten_set
                                 and k.symbol.upper() not in bereits]
        vorauswahl.extend(favoriten_kandidaten)
        hochwertig = self.quality_ranking(vorauswahl, bar=bar)

        eingestuft = []
        for rang, (kandidat, score) in enumerate(hochwertig, start=1):
            tier, begruendung = self.einstufung(kandidat)
            eingestuft.append({
                "kandidat": kandidat, "score": score, "rang": rang,
                "tier": tier, "tier_begruendung": begruendung,
            })

        dauer = time.time() - start
        logger.info("OKX-Auswahl: %d Instrumente -> %d im Pool -> %d bewertet (%.1fs)",
                    len(pool) + len(abgelehnt), len(pool), len(hochwertig), dauer)
        return {
            "broker": "okx",
            "asset_type": "crypto",
            "katalog_gesamt": len(pool) + len(abgelehnt),
            "public_catalog_gesamt": int(getattr(self, "_public_catalog_count", 0)),
            "account_catalog_gesamt": int(getattr(self, "_account_catalog_count", 0)),
            "public_only_gesamt": int(getattr(self, "_public_only_count", 0)),
            "volume_policy": "RANK_ONLY",
            "trade_lanes": {
                quote: {"free": round(float(amount), 8),
                        "funded": float(amount) > 0}
                for quote, amount in getattr(self, "_quote_cash", {}).items()
            },
            "pool": len(pool),
            "abgelehnt": abgelehnt,
            "cheap_ranking": [(k.symbol, round(s.gesamt, 4)) for k, s in billig[:self.vorauswahl]],
            "rangliste": eingestuft,
            "eligible_symbols": sorted({k.symbol.upper() for k in pool}),
            "hard_eligible_bases": sorted(getattr(self, "_hard_eligible_bases", set())),
            "core_symbols": sorted(self.kernliste),
            "core_state": dict(getattr(self, "_core_state", {})),
            "ineligible_reasons": dict(getattr(self, "_ineligible_reasons", {})),
            "dynamic_30": dict(getattr(self, "_dynamic_state", {})),
            "favoriten_bewertet": [k.symbol for k in favoriten_kandidaten],
            "dauer_sekunden": round(dauer, 2),
        }


__all__ = ["CryptoUniverseSelector", "HEBEL_MUSTER", "STABLECOINS"]
