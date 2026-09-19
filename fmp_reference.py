"""FMP 9.8.9: plan-aware shared reference, history and research data."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import requests

from fmp_service import Store, Budget, FMPTarifFehlt, request, ENDPOINTS, encoded
from safe_persistence import atomic_write_json
from state_lock import critical_state_lock

logger = logging.getLogger(__name__)

FMP_BASE = "https://financialmodelingprep.com/stable"

# Endpunkte, die der Gratistarif nachweislich NICHT enthaelt.
NEWS_ENDPUNKTE = ("/news/stock", "/news/general-latest", "/news/stock-latest",
                  "/news/press-releases-latest")


class FMPReferenz:
    """Referenz-, Kurs- und Fundamentaldaten von FMP mit Tagesbudget."""

    def __init__(self, api_key: str = "", *, base_url: str = FMP_BASE,
                 tageslimit: int = 0, timeout: float = 12.0):
        import live_settings
        self._key = str(api_key or live_settings.fmp_key() or "").strip()
        self.base_url = str(base_url or FMP_BASE).rstrip("/")
        self.timeout = float(timeout)
        import config
        self.store = Store(self._key, self.base_url, tageslimit)
        self.budget = Budget(self.store)
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json",
                                     "User-Agent": "TradingBot-v8-NEXUS"})
        self._status: dict[str, Any] = {"ok": None, "detail": "noch nicht abgefragt",
                                        "zeit": "", "count": 0}
        self._faehigkeiten: dict[str, dict] = {}
        self._lock = threading.RLock()

    @property
    def konfiguriert(self) -> bool:
        return bool(self._key)

    # -- Kernaufruf ---------------------------------------------------------
    def _get(self, pfad: str, params: Optional[dict] = None, *,
             purpose: str = "automatic", max_age: Optional[float] = None) -> Any:
        try:
            data = request(self.store, self.session, self._key, pfad, params,
                           timeout=self.timeout, purpose=purpose, max_age=max_age)
            self._merke(True, "FMP-Abruf oder gemeinsamer Cache verfuegbar")
            return data
        except RuntimeError as exc:
            self._merke(False, str(exc))
            raise

    def erlaubt(self, pfad):
        return self.konfiguriert and self.store.permits(pfad)

    def starter(self):
        return self.konfiguriert and self.store.status()["effective"] == "STARTER"

    def cached_source(self, pfad, params):
        return self.store.cached(encoded([pfad, params]))

    def _merke(self, ok: bool, detail: str, count: int = 0) -> None:
        with self._lock:
            self._status = {"ok": bool(ok), "detail": str(detail)[:240],
                            "zeit": datetime.now(timezone.utc).isoformat(), "count": int(count)}

    def _merke_faehigkeit(self, pfad: str, ok: bool, detail: str) -> None:
        with self._lock:
            self._faehigkeiten[pfad] = {
                "ok": bool(ok), "detail": detail,
                "zeit": datetime.now(timezone.utc).isoformat(),
            }

    # -- Fachliche Abfragen -------------------------------------------------
    def search_symbol(self, symbol: str, limit: int = 10, *,
                      purpose: str = "automatic") -> list[dict]:
        rows = self._get("/search-symbol", {"query": str(symbol).upper(),
                                            "limit": max(1, min(50, int(limit)))},
                         purpose=purpose)
        return list(rows or []) if isinstance(rows, list) else []

    def _single(self, path, symbol, purpose, max_age=None):
        symbol = str(symbol).strip().upper()
        rows = self._get(path, {"symbol": symbol}, purpose=purpose, max_age=max_age)
        row = rows[0] if isinstance(rows, list) and len(rows) == 1 else rows if isinstance(rows, dict) else {}
        if not isinstance(row, dict) or row.get("symbol") != symbol:
            raise RuntimeError("FMP: Antwort nicht eindeutig dem angefragten Ticker zugeordnet")
        return dict(row)

    def quote(self, symbol: str, *, purpose: str = "automatic", max_age: Optional[float] = None) -> dict:
        """Aktueller Quote; ``max_age`` (Sekunden) erzwingt einen frischeren Stand als der Cache (10.8.0)."""
        return self._single("/quote", symbol, purpose, max_age=max_age)

    def profil(self, symbol: str, *, purpose: str = "automatic") -> dict:
        return self._single("/profile", symbol, purpose)

    def tageskerzen(self, symbol: str, tage: int = 30, *,
                    purpose: str = "automatic") -> list[dict]:
        """Incremental OHLCV, newest first, shared across all consumers.

        Initial Starter request covers five years; subsequent requests overlap
        ten sessions. A quarterly rebuild catches older split corrections.
        Free keeps its short history requests; saved long history is archival.
        """
        from fmp_data import history
        return history(self, symbol, tage, purpose=purpose)

    def nachrichten(self, symbol="", *, market="stock", purpose="automatic"):
        if market not in {"stock", "crypto", "forex"}:
            raise ValueError("Unbekannter Nachrichtenmarkt")
        path = "/news/" + market + ("" if symbol else "-latest")
        params = {"symbols": str(symbol).upper(), "limit": 20} if symbol else {"page": 0, "limit": 20}
        data = self._get(path, params, purpose=purpose)
        return [r for r in data if isinstance(r, dict)] if isinstance(data, list) else []

    def jahresdaten(self, symbol, *, purpose="automatic"):
        from fmp_data import financials
        return financials(self, symbol, purpose=purpose)

    # -- Abgeleitete Kennzahlen --------------------------------------------
    def dollar_volumen(self, symbol: str, tage: int = 20) -> float:
        """Mittlerer Tagesumsatz in USD -- das Liquiditaetsmass fuer Aktien."""
        try:
            zeilen = self.tageskerzen(symbol, tage=tage)
        except (RuntimeError, FMPTarifFehlt):
            return 0.0
        werte = []
        for zeile in zeilen:
            volumen = float(zeile.get("volume") or 0.0)
            preis = float(zeile.get("vwap") or zeile.get("close") or 0.0)
            if volumen > 0 and preis > 0:
                werte.append(volumen * preis)
        return sum(werte) / len(werte) if werte else 0.0

    def ist_aktiv(self, symbol: str) -> Optional[bool]:
        """Wird der Wert noch gehandelt? None = nicht feststellbar.

        Beantwortet die Delisting-Frage aus dem Profil-Endpunkt, der im
        Gratistarif enthalten ist.
        """
        try:
            profil = self.profil(symbol)
        except (RuntimeError, FMPTarifFehlt):
            return None
        if not profil:
            return None
        if "isActivelyTrading" in profil:
            return bool(profil.get("isActivelyTrading"))
        return None

    def sektor(self, symbol: str) -> str:
        try:
            return str(self.profil(symbol).get("sector") or "")
        except (RuntimeError, FMPTarifFehlt):
            return ""

    # -- Diagnose -----------------------------------------------------------
    def verbindungstest(self) -> dict:
        """Prueft genau die Faehigkeit, die der Bot wirklich benutzt."""
        if not self.konfiguriert:
            return {"ok": False, "detail": "Kein API-Key hinterlegt"}
        try:
            treffer = self.search_symbol("AAPL", limit=1, purpose="manual")
        except FMPTarifFehlt as exc:
            return {"ok": False, "detail": str(exc), "tarif": True}
        except RuntimeError as exc:
            return {"ok": False, "detail": str(exc)}
        self._merke(True, "Symbolsuche erreichbar", len(treffer))
        return {"ok": True, "count": len(treffer),
                "detail": f"Symbolsuche erreichbar ({len(treffer)} Treffer), "
                          f"Budget {self.budget.verbraucht}/{self.budget.limit} heute"}

    def faehigkeitstest(self) -> dict:
        """Prueft der Reihe nach, was der gebuchte Tarif wirklich kann.

        Bis zu sechs Datenarten, mit gemeinsamem Cache und Abrufbudget.
        Zusatzdaten werden nur im gewaehlten Starter-Profil geprueft;
        eine erfolgreiche Abfrage aendert die Tarifvorgabe nicht.
        """
        pruefungen = [
            ("Symbolsuche", lambda: self.search_symbol("AAPL", 1, purpose="manual")),
            ("Kurs", lambda: self.quote("AAPL", purpose="manual")),
            ("Profil (Sektor, Delisting)", lambda: self.profil("AAPL", purpose="manual")),
            ("Tageskerzen", lambda: self.tageskerzen("AAPL", 5, purpose="manual")),
        ]
        ergebnis: dict[str, dict] = {}
        for name, aufruf in pruefungen:
            try:
                daten = aufruf()
                ergebnis[name] = {"ok": True, "detail": f"{len(daten) if hasattr(daten, '__len__') else 1} Datensatz"}
            except FMPTarifFehlt as exc:
                ergebnis[name] = {"ok": False, "tarif": True, "detail": str(exc)}
            except RuntimeError as exc:
                ergebnis[name] = {"ok": False, "detail": str(exc)}
        for name, pfad in (("Nachrichten", "/news/stock"), ("Jahresabschluss", "/income-statement")):
            if not self.erlaubt(pfad):
                ergebnis[name] = {"ok": False, "tarif": True, "detail": "Im gewaehlten Tarif nicht aktiv / Berechtigung pausiert"}
                continue
            try:
                params = {"symbols": "AAPL", "limit": 1} if name == "Nachrichten" else {"symbol": "AAPL", "period": "annual", "limit": 1}
                self._get(pfad, params, purpose="manual")
                ergebnis[name] = {"ok": True, "detail": "Abruf erreichbar; Tarifvorgabe wird dadurch nicht hochgesetzt"}
            except RuntimeError as exc:
                ergebnis[name] = {"ok": False, "detail": str(exc)}
        return ergebnis

    # -- Zwischenspeicher ---------------------------------------------------
    # 250 Anfragen pro Tag reichen NICHT, um bei jedem Universumslauf alle
    # 100 Aktien neu abzufragen: 32 Laeufe am Tag mal 100 Werte waeren 3200
    # Anfragen. Referenzdaten aendern sich aber langsam (Sektor praktisch
    # nie, Delisting selten, Dollar-Volumen ueber 20 Tage gemittelt).
    # Deshalb werden Referenzen unter Free bis zu sieben Tage, unter Starter
    # bis zu einen Tag gespeichert; pro Lauf werden aeltere Werte nachgezogen.
    def _cache_datei(self):
        import os
        from pathlib import Path
        wurzel = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
                      or Path(__file__).resolve().parent)
        return wurzel / "fmp_reference_cache.json"

    def _cache_laden(self) -> dict:
        import json
        datei = self._cache_datei()
        try:
            with critical_state_lock(datei):
                daten = json.loads(datei.read_text(encoding="utf-8"))
            return daten if isinstance(daten, dict) else {}
        except Exception:
            return {}

    def _cache_speichern(self, daten: dict) -> None:
        datei = self._cache_datei()
        with critical_state_lock(datei):
            atomic_write_json(datei, daten)

    def _cache_setze_symbol(self, symbol: str, eintrag: dict) -> None:
        """Ein Symbol ohne Lost-Update mit parallelen Prozessen speichern."""
        import json
        datei = self._cache_datei()
        with critical_state_lock(datei):
            try:
                aktuell = json.loads(datei.read_text(encoding="utf-8")) \
                    if datei.exists() else {}
            except Exception:
                aktuell = {}
            if not isinstance(aktuell, dict):
                aktuell = {}
            aktuell[str(symbol).upper()] = dict(eintrag)
            atomic_write_json(datei, aktuell)

    def referenzdaten(self, symbol: str, *, max_alter_stunden: float | None = None,
                      erlaube_abruf: bool = True) -> dict:
        """Sektor, Delisting-Status und Dollar-Volumen mit Langzeitcache.

        Liefert immer einen auswertbaren Datensatz. 'frisch' sagt, ob die
        Werte aus einem aktuellen Abruf stammen oder aus dem Speicher.
        """
        symbol = str(symbol).upper().strip()
        if max_alter_stunden is None:
            import config
            max_alter_stunden = 24.0 if self.starter() else float(getattr(
                config, "FMP_REFERENCE_MAX_AGE_HOURS", 168.0))
        speicher = self._cache_laden()
        eintrag = speicher.get(symbol) or {}
        alter = float("inf")
        if eintrag.get("zeit"):
            try:
                gelesen = datetime.fromisoformat(str(eintrag["zeit"]))
                if gelesen.tzinfo is None:
                    gelesen = gelesen.replace(tzinfo=timezone.utc)
                alter = (datetime.now(timezone.utc) - gelesen).total_seconds() / 3600.0
            except ValueError:
                alter = float("inf")
        if alter <= max_alter_stunden:
            return {**eintrag, "frisch": False, "alter_stunden": round(alter, 1)}
        if not erlaube_abruf or not self.konfiguriert:
            return {**eintrag, "frisch": False, "alter_stunden": None}

        neu = dict(eintrag)
        try:
            profil = self.profil(symbol)
            if profil:
                if profil.get("currency") != "USD":
                    return {**eintrag, "frisch": False, "fehler": "USD-Profil fuer Dollar-Liquiditaet nicht belegt"}
                neu["bezeichnung"] = str(profil.get("companyName") or "")
                neu["ist_etf"] = bool(profil.get("isEtf", False))
                neu["ist_fonds"] = bool(profil.get("isFund", False))
                neu["sektor"] = str(profil.get("sector") or "")
                neu["branche"] = str(profil.get("industry") or "")
                neu["aktiv"] = bool(profil.get("isActivelyTrading", True))
                neu["preis"] = float(profil.get("price") or 0.0)
                neu["marktkapitalisierung"] = float(profil.get("marketCap") or 0.0)
        except FMPTarifFehlt as exc:
            neu["hinweis"] = str(exc)
        except RuntimeError as exc:
            return {**eintrag, "frisch": False, "fehler": str(exc)}

        umsatz = self.dollar_volumen(symbol, tage=20)
        if umsatz > 0:
            neu["dollar_volumen"] = umsatz
        else:
            # Never refresh the timestamp of an old liquidity observation when
            # only the company profile succeeded.
            return {**eintrag, "frisch": False, "fehler": "Aktuelle Tageskurse/Volumen fehlen"}
        if self.starter():
            from fmp_data import daily_metrics
            cached = self.store.cached("history:" + symbol, stale=True)
            neu["tagesanalyse"] = daily_metrics((cached or {}).get("data", {}).get("rows", []))
            try:
                finances = self.jahresdaten(symbol)
                from fmp_data import annual_context
                neu["jahresanalyse"] = annual_context(finances)
            except RuntimeError as exc:
                neu["jahresanalyse"] = {"available": False, "reason": str(exc)}
        neu["zeit"] = datetime.now(timezone.utc).isoformat()

        self._cache_setze_symbol(symbol, neu)
        return {**neu, "frisch": True, "alter_stunden": 0.0}

    def aktualisiere_stapel(self, symbole, *, max_anfragen: int = 20) -> dict:
        """Zieht die aeltesten Eintraege nach, ohne das Tagesbudget zu sprengen.

        Jedes Symbol kostet zwei Anfragen (Profil + Kerzen). Es werden
        deshalb hoechstens max_anfragen/2 Symbole je Lauf aktualisiert.
        """
        speicher = self._cache_laden()

        def alter_von(sym: str) -> float:
            eintrag = speicher.get(str(sym).upper()) or {}
            if not eintrag.get("zeit"):
                return float("inf")
            try:
                gelesen = datetime.fromisoformat(str(eintrag["zeit"]))
                if gelesen.tzinfo is None:
                    gelesen = gelesen.replace(tzinfo=timezone.utc)
                return (datetime.now(timezone.utc) - gelesen).total_seconds()
            except ValueError:
                return float("inf")

        sortiert = sorted({str(s).upper() for s in symbole or []},
                          key=alter_von, reverse=True)
        grenze = max(0, int(max_anfragen) // (7 if self.starter() else 2))
        aktualisiert = []
        for symbol in sortiert[:grenze]:
            frei, _ = self.budget.frei(purpose="automatic")
            if not frei:
                break
            ergebnis = self.referenzdaten(symbol, erlaube_abruf=True)
            if ergebnis.get("frisch"):
                aktualisiert.append(symbol)
        return {"aktualisiert": aktualisiert, "geprueft": len(sortiert),
                "budget": self.budget.als_dict()}

    def status(self) -> dict:
        with self._lock:
            eintrag = dict(self._status)
            faehigkeiten = dict(self._faehigkeiten)
        eintrag["budget"] = self.budget.als_dict()
        eintrag["konfiguriert"] = self.konfiguriert
        eintrag["faehigkeiten"] = faehigkeiten
        eintrag["news_im_tarif"] = self.erlaubt("/news/stock")
        eintrag["tarif"] = eintrag["budget"]
        eintrag["faehigkeiten"] = {r["path"]: r for r in eintrag["budget"]["capabilities"]}
        return eintrag


_CLIENT: Optional[FMPReferenz] = None
_CLIENT_LOCK = threading.RLock()


def client() -> FMPReferenz:
    """Gemeinsame Instanz, damit das Tagesbudget prozessweit stimmt."""
    global _CLIENT
    with _CLIENT_LOCK:
        import live_settings
        wanted_key = str(live_settings.fmp_key() or "").strip()
        if _CLIENT is None or _CLIENT._key != wanted_key or _CLIENT.store.path.parent != __import__("fmp_service").root():
            _CLIENT = FMPReferenz(wanted_key)
        return _CLIENT


def neu_laden() -> None:
    """Nach einer Schluesseländerung die Instanz verwerfen."""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None


__all__ = ["FMPReferenz", "FMPTarifFehlt", "client", "neu_laden", "FMP_BASE", "NEWS_ENDPUNKTE"]
