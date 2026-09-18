"""Regressionen fuer die asset-sichere Favoritenzuordnung in NEXUS 8.2.2."""
from __future__ import annotations

import pytest

import favorites
from favorites import Favorite, merge_stock_favorite_rows, resolve_favorite, save_favorites
from universe.crypto_selector import CryptoUniverseSelector
from universe.modelle import UniverseKandidat, UniverseScore, UniverseZustand
from universe.manager import UniverseManager
from universe.stock_selector import StockUniverseSelector


def test_auto_routes_known_stock_and_crypto_to_their_own_brokers():
    stock, _ = resolve_favorite("AAPL", "auto")
    crypto, _ = resolve_favorite("BTC-EUR", "auto")

    assert (stock.asset_type, stock.broker, stock.exchange) == ("stock", "etoro", "ETORO")
    assert (crypto.asset_type, crypto.broker, crypto.exchange, crypto.currency) == (
        "crypto", "okx", "OKX", "EUR")


def test_unknown_auto_symbol_never_silently_guesses_a_broker():
    with pytest.raises(ValueError, match="nicht eindeutig"):
        resolve_favorite("NICHTBEKANNT123", "auto")


def test_stock_favorite_is_added_only_to_etoro_candidate_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(favorites, "PATH", tmp_path / "favorites.json")
    save_favorites([
        Favorite("NEUEAKTIE", "stock", "USD", "SMART"),
        Favorite("NEUERCOIN", "crypto", "EUR", "OKX"),
    ])

    rows = merge_stock_favorite_rows([
        {"symbol": "AAPL", "currency": "USD", "sector": "Tech"},
    ])
    by_symbol = {row["symbol"]: row for row in rows}

    assert set(by_symbol) == {"AAPL", "NEUEAKTIE"}
    assert by_symbol["NEUEAKTIE"]["exchange"] == "ETORO"
    assert by_symbol["NEUEAKTIE"]["broad"] is True


def test_stock_selector_includes_stock_favorite_but_not_crypto_favorite(tmp_path, monkeypatch):
    monkeypatch.setattr(favorites, "PATH", tmp_path / "favorites.json")
    save_favorites([
        Favorite("NEUEAKTIE", "stock"),
        Favorite("NEUERCOIN", "crypto"),
    ])

    class Cfg:
        STOCK_SYMBOLS = [{"symbol": "AAPL", "currency": "USD", "sector": "Tech"}]

    symbols = {item.name for item in StockUniverseSelector(cfg=Cfg()).katalog()}
    assert symbols == {"AAPL", "NEUEAKTIE"}


def test_crypto_favorite_reaches_quality_check_even_outside_normal_preselection(monkeypatch):
    class Cfg:
        OKX_QUOTE_CCY = "EUR"
        OKX_ALLOWED_QUOTE_CCY = ("EUR", "USDC")
        CRYPTO_UNIVERSE_MIN_AGE_DAYS = 30
        CRYPTO_UNIVERSE_MIN_QUOTE_VOLUME = 2_000_000
        CRYPTO_UNIVERSE_MAX_SPREAD_PCT = 0.006
        CRYPTO_UNIVERSE_PRESELECTION = 1
        CRYPTO_UNIVERSE_BLOCKLIST = ()
        CRYPTO_ESTABLISHED_MIN_AGE_DAYS = 365
        CRYPTO_ESTABLISHED_MIN_QUOTE_VOLUME = 50_000_000
        CRYPTO_ESTABLISHED_MAX_SPREAD_PCT = 0.0015
        CRYPTO_CORE_SYMBOLS = ()

    top = UniverseKandidat("TOP", "okx", asset_type="crypto", inst_id="TOP-EUR")
    favorite = UniverseKandidat("FAV", "okx", asset_type="crypto", inst_id="FAV-EUR")
    normal = [UniverseKandidat(f"N{index}", "okx", asset_type="crypto",
                                inst_id=f"N{index}-EUR") for index in range(1, 10)]
    selector = CryptoUniverseSelector(client=object(), cfg=Cfg())
    pool = [top, *normal, favorite]
    monkeypatch.setattr(selector, "eligible_pool", lambda: (pool, []))
    monkeypatch.setattr(selector, "cheap_ranking", lambda _: [
        *[(candidate, UniverseScore(0.9 - index / 100))
          for index, candidate in enumerate([top, *normal])],
        (favorite, UniverseScore(0.1)),
    ])
    received = []

    def quality(candidates, *, bar):
        received[:] = list(candidates)
        return [(candidate, UniverseScore(0.9 if candidate is top else 0.1))
                for candidate in candidates]

    monkeypatch.setattr(selector, "quality_ranking", quality)
    monkeypatch.setattr(selector, "einstufung", lambda _: ("KANDIDAT", "Test"))

    result = selector.auswahl(favoriten=["FAV"])

    assert [candidate.symbol for candidate in received][-1] == "FAV"
    assert len(received) == 11
    assert result["favoriten_bewertet"] == ["FAV"]


def test_favorite_cannot_bypass_normal_universe_rank(tmp_path):
    class Cfg:
        CRYPTO_UNIVERSE_ACTIVE_LIMIT = 1
        CRYPTO_UNIVERSE_FOCUS_LIMIT = 1
        CRYPTO_UNIVERSE_REMOVAL_RANK = 2
        CRYPTO_UNIVERSE_MIN_RESIDENCE_HOURS = 6
        CRYPTO_UNIVERSE_REMOVAL_CONFIRMATIONS = 3
        CRYPTO_UNIVERSE_PROBATION_HOURS = 24
        CRYPTO_UNIVERSE_FAVORITE_SLOTS = 5
        CRYPTO_UNIVERSE_MAX_CHANGES_PER_RUN = 8
        CRYPTO_CORE_SYMBOLS = ()

    manager = UniverseManager(UniverseZustand(tmp_path / "universe.json"), cfg=Cfg())
    candidate = UniverseKandidat("FAV", "okx", asset_type="crypto", inst_id="FAV-EUR")
    result = {"broker": "okx", "rangliste": [{
        "kandidat": candidate, "score": UniverseScore(0.1), "rang": 2,
        "tier": "KANDIDAT", "tier_begruendung": "schwacher Rang",
    }]}

    manager.lauf(result, favoriten=["FAV"])

    assert manager.zustand.hole("okx", "FAV") is None
