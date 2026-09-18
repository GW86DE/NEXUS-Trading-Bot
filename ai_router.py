"""AIRouter -- entscheidet zentral, WELCHES Modell eine Aufgabe bekommt (AIU-001).

DAS PROBLEM
===========
Bis v6.0 ging jede KI-Anfrage an dasselbe Modell (Terra). Das ist bei
haeufigen, einfachen Aufgaben unnoetig teuer: eine Klassifizierung
"HIGH/NORMAL/LOW" braucht kein Modell fuer 2 USD je Million Token, wenn es
eines fuer 0,20 USD genauso gut kann.

DIE LOESUNG
===========
Drei Stufen statt einer:

    KEINE AI   deterministisch loesbar -> gar keine Anfrage
    LUNA       einfach und haeufig     -> guenstiges Modell
    TERRA      komplex und selten      -> starkes Modell

Die Routing-Entscheidung faellt VOR der Anfrage anhand der Aufgabenart und
des Kontexts, nicht danach.

HARTE GRENZEN (AIU-005, AIU-006)
================================
    - Die KI darf Aufmerksamkeit steuern, klassifizieren und erklaeren.
    - Sie darf NIEMALS ein Instrument handelbar machen, einen harten Filter
      ueberschreiben, das Candidate Gate umgehen oder eine Position
      vergroessern.
    - Faellt die KI aus oder ist das Budget leer, laeuft die deterministische
      Pipeline unveraendert weiter. Der Zustand heisst AI_DEGRADED und ist
      ein Hinweis, kein Fehler.
"""

from __future__ import annotations

import hashlib
import json
import math
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import requests

import config
from safe_persistence import best_effort_json

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
STATE_ROOT = Path(os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip() or ROOT)


def _zustandswurzel() -> Path:
    """Wo Budget, Zwischenspeicher und Pruefspur liegen -- beim Zugriff bestimmt.

    v8.1.5: ``STATE_ROOT`` wurde beim IMPORT festgelegt. Ein Test, der
    ``ai_router`` importiert bevor er ``TRADINGBOT_TEST_STATE_DIR`` setzt,
    schrieb sein Tagesbudget deshalb in den Release-Baum. Dort summierte es
    sich ueber Testlaeufe hinweg auf, bis ein voellig anderer Test scheiterte,
    weil "das Terra-Tagesbudget aufgebraucht" war -- ein Fehlerbild, das mit
    dem gepruefen Verhalten nichts zu tun hatte.
    """
    test_dir = os.getenv("TRADINGBOT_TEST_STATE_DIR", "").strip()
    return Path(test_dir) if test_dir else STATE_ROOT

LUNA = "luna"
TERRA = "terra"
KEINE = "keine"

OPENAI_URL = "https://api.openai.com/v1/responses"


# ---------------------------------------------------------------------------
# Aufgabenkatalog
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Aufgabe:
    """Beschreibt eine KI-Aufgabe und ihre Standardstufe."""
    name: str
    stufe: str                     # LUNA oder TERRA
    beschreibung: str
    cache_stunden: float = 6.0
    max_output_tokens: int = 900
    reasoning: str = "low"
    web_suche: bool = False


AUFGABEN: dict[str, Aufgabe] = {
    "pulsar_web_research": Aufgabe("pulsar_web_research", LUNA,
        "Begrenzte Suche nach Originalmeldungen und Gegenbelegen", 0, 1600, "low", web_suche=True),
    "pulsar_event_check": Aufgabe("pulsar_event_check", LUNA,
        "Wirtschaftlichen Anlass im Originaltext einordnen", 0, 900, "low"),
    "pulsar_precheck": Aufgabe("pulsar_precheck", LUNA,
        "PULSAR-Quellenpakete gebuendelt vorpruefen", cache_stunden=0, max_output_tokens=3000),
    "pulsar_analysis": Aufgabe("pulsar_analysis", TERRA,
        "PULSAR-Thesen nur anhand zitierter Belege", cache_stunden=0, max_output_tokens=1800),
    "pulsar_countercheck": Aufgabe("pulsar_countercheck", TERRA,
        "Unabhaengiger PULSAR-Gegencheck desselben Quellenpakets", cache_stunden=0, max_output_tokens=1200),
    "universe_kandidat": Aufgabe(
        "universe_kandidat", LUNA,
        "Einen beobachteten Wert einstufen: HIGH / NORMAL / LOW",
        cache_stunden=6.0, max_output_tokens=700),
    "universe_batch": Aufgabe(
        "universe_batch", LUNA,
        "Mehrere Focus-Kandidaten in EINER Anfrage priorisieren",
        cache_stunden=6.0, max_output_tokens=1400),
    "news_relevanz": Aufgabe(
        "news_relevanz", LUNA,
        "Ist diese Meldung fuer dieses Instrument relevant?",
        cache_stunden=3.0, max_output_tokens=600),
    "duplikate": Aufgabe(
        "duplikate", LUNA,
        "Doppelte Meldungen zusammenfassen",
        cache_stunden=3.0, max_output_tokens=600),
    "focus_ranking": Aufgabe(
        "focus_ranking", LUNA,
        "Reihenfolge des Focus Sets vorschlagen",
        cache_stunden=2.0, max_output_tokens=900),
    "anomalie_research": Aufgabe(
        "anomalie_research", TERRA,
        "Ursache einer starken, unerklaerten Bewegung untersuchen",
        cache_stunden=12.0, max_output_tokens=2600, reasoning="medium", web_suche=True),
    "widerspruch": Aufgabe(
        "widerspruch", TERRA,
        "Widerspruechliche Meldungen einordnen",
        cache_stunden=12.0, max_output_tokens=2200, reasoning="medium", web_suche=True),
    "krypto_event": Aufgabe(
        "krypto_event", TERRA,
        "Listing, Unlock, Exploit oder Regulierung bewerten",
        cache_stunden=12.0, max_output_tokens=2400, reasoning="medium", web_suche=True),
    "wochen_research": Aufgabe(
        "wochen_research", TERRA,
        "Woechentliches Research fuer Universumsvorschlaege",
        cache_stunden=24.0, max_output_tokens=5000, reasoning="medium", web_suche=True),
    "strategie_auswertung": Aufgabe(
        "strategie_auswertung", TERRA,
        "Aggregierte lokale Strategie-Statistik vorsichtig interpretieren",
        cache_stunden=24.0, max_output_tokens=3500, reasoning="medium", web_suche=False),
    # v8.1.4: Zweite Meinung vor einer Kauforder. Reine Warn- und
    # Dokumentationsschicht -- die KI loest keine Order aus und hebt keine
    # Ablehnung auf. Kein Zwischenspeicher: eine Einschaetzung von vor
    # Stunden waere fuer genau diesen Einstieg wertlos. Keine Websuche,
    # damit der Kaufpfad nicht auf externe Dienste wartet.
    "second_opinion": Aufgabe(
        "second_opinion", TERRA,
        "Zweite Meinung zu einem bereits freigegebenen Kaufkandidaten",
        cache_stunden=0.0, max_output_tokens=900, reasoning="low", web_suche=False),
    "core_volume_identity": Aufgabe(
        "core_volume_identity", LUNA,
        "Monatlichen CORE_VOLUME_20-Diff nur auf Identitaet/Rebranding/Risiko pruefen",
        cache_stunden=720.0, max_output_tokens=700, reasoning="low", web_suche=False),
}


# Ausloeser, die eine einfache Aufgabe zur komplexen hochstufen (AIU-003).
ESKALATIONS_GRUENDE = (
    "marktanomalie", "widerspruch", "exploit", "hack", "unlock",
    "delisting", "regulierung", "uebernahme", "insolvenz",
)


# ---------------------------------------------------------------------------
# Antwortschemata
# ---------------------------------------------------------------------------
SCHEMA_EINSTUFUNG = {
    "type": "object",
    "properties": {
        "attention": {"type": "string", "enum": ["HIGH", "NORMAL", "LOW"]},
        "research_needed": {"type": "boolean"},
        "risiken": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
        "begruendung": {"type": "string"},
    },
    "required": ["attention", "research_needed", "risiken", "begruendung"],
    "additionalProperties": False,
}

SCHEMA_BATCH = {
    "type": "object",
    "properties": {
        "bewertungen": {
            "type": "array",
            "maxItems": 40,
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "attention": {"type": "string", "enum": ["HIGH", "NORMAL", "LOW"]},
                    "research_needed": {"type": "boolean"},
                    "begruendung": {"type": "string"},
                },
                "required": ["symbol", "attention", "research_needed", "begruendung"],
                "additionalProperties": False,
            },
        },
        "hinweis": {"type": "string"},
    },
    "required": ["bewertungen", "hinweis"],
    "additionalProperties": False,
}

SCHEMA_CORE_VOLUME_IDENTITY = {
    "type": "object",
    "properties": {"warnings": {"type": "array", "items": {"type": "string"}, "maxItems": 20}},
    "required": ["warnings"], "additionalProperties": False,
}

SCHEMA_RESEARCH = {
    "type": "object",
    "properties": {
        "ursache": {"type": "string"},
        "einordnung": {"type": "string", "enum": ["POSITIV", "NEGATIV", "NEUTRAL", "UNKLAR"]},
        "vertrauen": {"type": "string", "enum": ["HOCH", "MITTEL", "NIEDRIG"]},
        "quellen": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "risiken": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "zusammenfassung": {"type": "string"},
    },
    "required": ["ursache", "einordnung", "vertrauen", "quellen", "risiken", "zusammenfassung"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------
from ai_budget import AIBudget


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
ANALYSIS_CACHE_REVISION = "nexus-analysis-cache-v2"


def analysis_cache_key(task, *, model, tier, payload, schema, instruction,
                       revision="", data_identity=None, tool_policy=None):
    """Identity of an analysis, not a source-fetch TTL or a trading authority.

    Callers include full-source hashes and observed/published times in payload
    or data_identity when projecting a smaller model input. No current clock
    is invented here: repeated identical evidence remains reusable.
    """
    contract = {"namespace": ANALYSIS_CACHE_REVISION, "task": task,
        "model": model, "tier": tier, "payload": payload, "schema": schema,
        "instruction": instruction, "revision": revision,
        "data_identity": data_identity, "tool_policy": tool_policy}
    return hashlib.sha256(json.dumps(contract, sort_keys=True, ensure_ascii=False,
                                    default=str).encode("utf-8")).hexdigest()


class AICache:
    """Antwortcache mit TTL je Aufgabenart (AIU-009).

    Der Cache ist bewusst dateibasiert: er muss einen Neustart ueberleben,
    sonst kostet jeder Neustart wieder ein volles Tagesbudget.
    """

    def __init__(self, datei: Optional[Path] = None, max_eintraege: int = 400):
        self.datei = Path(datei or _zustandswurzel() / "ai_router_cache.json")
        self.max_eintraege = int(max_eintraege)
        self._daten: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._laden()

    def _laden(self) -> None:
        try:
            roh = json.loads(self.datei.read_text(encoding="utf-8"))
            if isinstance(roh, dict):
                self._daten = roh.get("eintraege", {}) or {}
        except Exception:
            self._daten = {}

    def _speichern(self) -> None:
        best_effort_json(self.datei, {"version": 1, "eintraege": self._daten},
                         label="AI-Router-Cache")

    @staticmethod
    def schluessel(aufgabe: str, nutzlast: Any) -> str:
        roh = json.dumps({"a": aufgabe, "p": nutzlast}, sort_keys=True, ensure_ascii=False,
                         default=str)
        return hashlib.sha256(roh.encode("utf-8")).hexdigest()[:32]

    def hole(self, schluessel: str, ttl_stunden: float) -> Optional[dict]:
        with self._lock:
            eintrag = self._daten.get(schluessel)
        if not eintrag:
            return None
        alter = time.time() - float(eintrag.get("zeit", 0.0))
        if alter > max(0.0, ttl_stunden) * 3600.0:
            return None
        return eintrag.get("antwort")

    def setze(self, schluessel: str, antwort: dict, *, aufgabe: str = "") -> None:
        with self._lock:
            self._daten[schluessel] = {"zeit": time.time(), "antwort": antwort,
                                       "aufgabe": aufgabe}
            if len(self._daten) > self.max_eintraege:
                aeltere = sorted(self._daten.items(), key=lambda x: x[1].get("zeit", 0.0))
                for k, _ in aeltere[:len(self._daten) - self.max_eintraege]:
                    self._daten.pop(k, None)
            self._speichern()

    def verwerfe(self, *, aufgabe: str = "", symbol: str = "") -> int:
        """Invalidierung bei neuem Ereignis (AIU-009)."""
        entfernt = 0
        with self._lock:
            for k in list(self._daten):
                eintrag = self._daten[k]
                if aufgabe and str(eintrag.get("aufgabe", "")) != aufgabe:
                    continue
                if symbol:
                    text = json.dumps(eintrag.get("antwort", {}), ensure_ascii=False)
                    if symbol.upper() not in text.upper():
                        continue
                self._daten.pop(k, None)
                entfernt += 1
            if entfernt:
                self._speichern()
        return entfernt


# ---------------------------------------------------------------------------
# Nutzungsprotokoll
# ---------------------------------------------------------------------------
def _audit_pfad() -> Path:
    return _zustandswurzel() / "ai_usage_audit.jsonl"


_AUDIT_LOCK = threading.RLock()


def schreibe_ai_audit(eintrag: dict) -> None:
    """Ein Eintrag je KI-Nutzung (AIU-011)."""
    eintrag = {"zeit": datetime.now(timezone.utc).isoformat(), **eintrag}
    try:
        with _AUDIT_LOCK:
            datei = _audit_pfad()
            datei.parent.mkdir(parents=True, exist_ok=True)
            if datei.exists() and datei.stat().st_size > 3 * 1024 * 1024:
                datei.replace(datei.with_suffix(".1.jsonl"))
            with datei.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(eintrag, ensure_ascii=False) + "\n")
    except Exception:
        logger.debug("AI-Audit konnte nicht geschrieben werden", exc_info=True)


class _RequestTrace:
    """Bounded metadata, never prompts, API keys or an invented server receipt."""
    def __init__(self, task, payload):
        self.task = task
        self.model = ""
        self.tier = KEINE
        self.started_clock = None
        self.fields = {"schema_version": 1, "phase": "NOT_STARTED",
            "local_request_id": uuid.uuid4().hex, "provider_request_id": "",
            "request_dispatched": False, "response_received": False,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None, "finished_at": None, "duration_seconds": None,
            "cache_used": False, "error_code": ""}
        candidates = payload.get("candidates")
        candidates = candidates if isinstance(candidates, list) else []
        symbols = payload.get("symbol") or payload.get("symbole") or [
            row.get("symbol") for row in candidates if isinstance(row, dict)]
        self.symbols = str(symbols or "")[:300]

    def emit(self, **extra):
        schreibe_ai_audit({"aufgabe": self.task, "modell": self.model, "stufe": self.tier,
            "instrumente": self.symbols, "execution": dict(self.fields), **extra})

    def dispatch(self):
        # Local HTTP invocation only. It proves neither server acceptance nor
        # reachability; provider_request_id requires an actual response.
        self.started_clock = time.monotonic()
        self.fields.update(phase="REQUEST_STARTED", request_dispatched=True,
                           started_at=datetime.now(timezone.utc).isoformat())
        self.emit()

    def received(self, data):
        self.fields["response_received"] = True
        if isinstance(data, dict):
            self.fields["provider_request_id"] = str(data.get("id") or "")[:120]

    def finish(self, answer, phase, code=""):
        self.fields.update(phase=phase, error_code=code, cache_used=answer.cache_treffer,
            finished_at=datetime.now(timezone.utc).isoformat(),
            duration_seconds=(round(time.monotonic()-self.started_clock, 3)
                              if self.started_clock is not None else None))
        if phase in {"NOT_STARTED", "CACHE_HIT"}:
            self.fields["request_dispatched"] = False
        answer.execution = dict(self.fields)
        self.emit(ok=answer.ok, cache_hit=answer.cache_treffer, grund=answer.grund,
                  kosten=answer.kosten, usage_confirmed=answer.usage_confirmed)
        return answer


def request_diagnostics(limit=20):
    """Read a bounded audit tail. Old rows stay UNKNOWN, never 'not executed'."""
    records = []
    try:
        with _audit_pfad().open("rb") as fh:
            fh.seek(0, 2)
            start = max(0, fh.tell()-65536)
            fh.seek(start)
            if start:
                fh.readline()
            lines = fh.read(65536).decode("utf-8", errors="replace").splitlines()
        by_request = {}
        for line in lines:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            execution = row.get("execution") if isinstance(row, dict) else None
            if not isinstance(execution, dict) or not execution.get("local_request_id"):
                continue
            record = {k: row.get(k) for k in ("aufgabe", "modell", "stufe", "instrumente", "zeit", "grund")}
            record["execution"] = execution
            local_id = execution["local_request_id"]
            if row.get("usage_only") and local_id in by_request:
                by_request[local_id]["late_usage"] = {"zeit": row.get("zeit"), "kosten": row.get("kosten")}
            elif not row.get("usage_only"):
                earlier = by_request.pop(local_id, {})
                if earlier.get("late_usage"):
                    record["late_usage"] = earlier["late_usage"]
                by_request[local_id] = record
        records = list(by_request.values())[-max(1, min(50, int(limit))):][::-1]
    except (OSError, ValueError):
        pass
    return {"schema_version": 1, "last_execution": records[0] if records else None,
            "recent_executions": records,
            "detail": "Historische lokale Aufrufbelege; kein aktueller Erreichbarkeitstest"}


# Bound the wait in the trading loop, including a stalled DNS or response body.
# The worker only performs HTTP. A timed-out answer can never later change a trade/cache.
_HTTP_SLOTS = threading.BoundedSemaphore(2)

class AIRequestNotSent(TimeoutError):
    """The local transport did not dispatch an HTTP request."""


def _post_bounded(url, *, timeout, on_late_response=None, on_dispatch=None, **kwargs):
    if not _HTTP_SLOTS.acquire(blocking=False):
        raise AIRequestNotSent("KI-Transport noch beschaeftigt; keine neue Anfrage gesendet")
    done = threading.Event()
    outcome = {}
    lock = threading.Lock()
    expired = False
    def run():
        value = {"error": RuntimeError("KI-Transport unerwartet beendet")}
        try:
            if callable(on_dispatch):
                on_dispatch()
            response = requests.post(url, timeout=timeout, **kwargs)
            # requests eagerly reads content, so completion includes the body.
            value = {"response": response}
        except Exception as exc:
            value = {"error": exc}
        finally:
            with lock:
                outcome.update(value)
                late = expired
            _HTTP_SLOTS.release()
            done.set()
        if late and "response" in value and callable(on_late_response):
            try:
                # Usage accounting only. Never adopt expired analysis or orders.
                on_late_response(value['response'])
            except Exception as exc:
                logger.warning('Verspaeteter KI-Verbrauch nicht abrechenbar: %s',type(exc).__name__)
    try:
        threading.Thread(target=run, name="nexus-ai-http", daemon=True).start()
    except Exception as exc:
        _HTTP_SLOTS.release()
        raise AIRequestNotSent("KI-Transport konnte lokal nicht gestartet werden") from exc
    if not done.wait(timeout):
        with lock:
            if not outcome:
                expired = True
                raise TimeoutError("KI-Zeitrahmen abgelaufen; Budget bis zum Verbrauchsbeleg reserviert")
    if "error" in outcome:
        raise outcome["error"]
    return outcome["response"]


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
def pulsar_web_reserve(price_in, price_out):
    """Two tools, three model steps at the documented 128k search context.

    Prices are configured per million tokens. Default returned-token budget,
    no unlimited search. Unknown billing retains this conservative reserve.
    """
    if not all(math.isfinite(x) and x > 0 for x in (price_in, price_out)):
        raise ValueError("Modellpreise fuer Websuche fehlen")
    return ((3 * 128000 + 16384) * price_in + 1600 * price_out) / 1_000_000 + .02


def web_receipts(response):
    """Only server tool records establish consulted URLs, never model JSON."""
    calls, found = 0, {}
    from urllib.parse import urlsplit
    for item in response.get("output", []) or []:
        if not isinstance(item, dict) or item.get("type") != "web_search_call":
            continue
        if item.get("status") != "completed":
            raise ValueError("Unvollstaendiger Websuchbeleg")
        calls += 1
        for source in (item.get("action") or {}).get("sources", []) or []:
            if not isinstance(source, dict):
                continue
            url = source.get("url") or ""
            p = urlsplit(url)
            if p.scheme == "https" and p.hostname and not p.username and not p.password:
                found[url] = {"url": url, "title": str(source.get("title") or "")[:200]}
    return calls, list(found.values())[:30]


@dataclass
class AIAntwort:
    """Ergebnis einer Routeranfrage -- immer auswertbar, auch bei Ausfall."""
    ok: bool = False
    daten: dict = field(default_factory=dict)
    modell: str = ""
    stufe: str = KEINE
    cache_treffer: bool = False
    kosten: float = 0.0
    grund: str = ""
    web_sources: list = field(default_factory=list)
    web_calls: int = 0
    usage_confirmed: bool = False
    execution: dict = field(default_factory=dict)

    def als_dict(self) -> dict:
        return {"ok": self.ok, "daten": self.daten, "modell": self.modell,
                "stufe": self.stufe, "cache": self.cache_treffer,
                "kosten": round(self.kosten, 6), "grund": self.grund,
                "web_sources": self.web_sources, "web_calls": self.web_calls,
                "usage_confirmed": self.usage_confirmed, "execution": self.execution}


class AIRouter:
    """Zentrale Anlaufstelle fuer alle KI-Anfragen des Bots."""

    def __init__(self, cfg=None, *, budget: Optional[AIBudget] = None,
                 cache: Optional[AICache] = None):
        self.cfg = cfg or config
        self.budget = budget or AIBudget(cfg=self.cfg)
        self.cache = cache or AICache()
        self.letzter_fehler = ""
        self._degraded = False
        self._degraded_grund = ""

    # -- Grundzustand -------------------------------------------------------
    @property
    def api_key(self) -> str:
        return str(getattr(self.cfg, "OPENAI_API_KEY", "") or "").strip()

    @property
    def aktiv(self) -> bool:
        from ai_control import read_mode
        return read_mode() != "OFF" and bool(getattr(self.cfg, "AI_ROUTER_ENABLED", False)) and bool(self.api_key)

    def modellname(self, stufe: str) -> str:
        if stufe == LUNA:
            return str(getattr(self.cfg, "AI_LUNA_MODEL", "gpt-5.6-luna"))
        return str(getattr(self.cfg, "AI_TERRA_MODEL", "gpt-5.6-terra"))

    def preise(self, stufe: str) -> tuple[float, float]:
        """(USD je 1M Input-Token, USD je 1M Output-Token)."""
        if stufe == LUNA:
            return (float(getattr(self.cfg, "AI_LUNA_PRICE_INPUT_PER_M", 0.20)),
                    float(getattr(self.cfg, "AI_LUNA_PRICE_OUTPUT_PER_M", 1.20)))
        return (float(getattr(self.cfg, "AI_TERRA_PRICE_INPUT_PER_M", 2.00)),
                float(getattr(self.cfg, "AI_TERRA_PRICE_OUTPUT_PER_M", 12.00)))

    @property
    def degraded(self) -> bool:
        return self._degraded

    def _setze_degraded(self, grund: str) -> None:
        if not self._degraded:
            logger.warning("AI_DEGRADED: %s -- die deterministische Pipeline laeuft weiter.", grund)
        self._degraded = True
        self._degraded_grund = str(grund)[:300]

    def _degraded_aufheben(self) -> None:
        if self._degraded:
            logger.info("KI wieder verfuegbar.")
        self._degraded = False
        self._degraded_grund = ""

    # -- Routing ------------------------------------------------------------
    def waehle_stufe(self, aufgabe_name: str, kontext: Optional[dict] = None) -> tuple[str, str]:
        """Welche Stufe bearbeitet diese Aufgabe? (AIU-001)"""
        aufgabe = AUFGABEN.get(aufgabe_name)
        if aufgabe is None:
            return KEINE, f"Unbekannte Aufgabe {aufgabe_name!r}"
        if not self.aktiv:
            return KEINE, "KI ist deaktiviert oder kein Schluessel hinterlegt"

        stufe = aufgabe.stufe
        kontext = kontext or {}

        # Eskalation: eine einfache Aufgabe kann durch ihren Inhalt komplex werden.
        if stufe == LUNA:
            text = " ".join(str(v) for v in kontext.values()).lower()
            treffer = [g for g in ESKALATIONS_GRUENDE if g in text]
            if treffer or bool(kontext.get("eskalieren")):
                stufe = TERRA
                grund_text = ", ".join(treffer) or "ausdrueckliche Eskalation"
                frei, grund = self.budget.frei(TERRA)
                if not frei:
                    # Kein Terra-Budget -> lieber Luna als gar nichts.
                    return LUNA, f"Eskalation ({grund_text}) nicht moeglich: {grund}"
                return TERRA, f"Eskalation wegen {grund_text}"

        frei, grund = self.budget.frei(stufe)
        if not frei:
            if stufe == TERRA and not aufgabe_name.startswith("pulsar_"):
                frei_luna, _ = self.budget.frei(LUNA)
                if frei_luna and bool(getattr(self.cfg, "AI_TERRA_FALLBACK_TO_LUNA", True)):
                    return LUNA, f"Terra nicht verfuegbar ({grund}), Luna uebernimmt"
            return KEINE, grund
        return stufe, "Standardrouting"

    # -- Anfrage ------------------------------------------------------------
    def frage(self, aufgabe_name: str, nutzlast: dict, schema: dict, *,
              anweisung: str = "", kontext: Optional[dict] = None,
              cache_erlaubt: bool = True, timeout_seconds: float | None = None,
              usage_callback=None) -> AIAntwort:
        """Fuehrt genau eine KI-Anfrage aus -- oder liefert sauber 'nicht ok'."""
        trace = _RequestTrace(aufgabe_name, nutzlast)
        def deliver_usage(receipt):
            if callable(usage_callback):
                try:
                    usage_callback(receipt)
                except Exception as exc:
                    logger.warning("KI-Verbrauch beim Aufrufer noch offen: %s", type(exc).__name__)
        def not_sent():
            deliver_usage(dict(kosten=0.0,input_tokens=0,output_tokens=0,web_calls=0,not_sent=True,late=False))
        aufgabe = AUFGABEN.get(aufgabe_name)
        if aufgabe is None:
            not_sent()
            return trace.finish(AIAntwort(grund=f"Unbekannte Aufgabe {aufgabe_name!r}"),
                                "NOT_STARTED", "AI_TASK_UNKNOWN")

        if not self.aktiv:
            not_sent()
            return trace.finish(AIAntwort(grund="KI ist ausgeschaltet oder kein Schluessel hinterlegt"),
                                "NOT_STARTED", "AI_DISABLED")
        from provider_safety import redact, validate_json
        stufe, grund = self.waehle_stufe(aufgabe_name, kontext)
        if stufe == KEINE:
            not_sent()
            self.budget.zaehle("abgelehnt")
            self._setze_degraded(grund)
            schreibe_ai_audit({"aufgabe": aufgabe_name, "cache_hit": False,
                               "modell": "", "abgelehnt": True, "grund": grund})
            return trace.finish(AIAntwort(grund=grund), "NOT_STARTED", "AI_ROUTING_BLOCKED")

        modell = self.modellname(stufe)
        trace.model, trace.tier = modell, stufe
        payload = {
            "model": modell,
            "reasoning": {"effort": aufgabe.reasoning},
            "input": [
                {"role": "developer", "content": anweisung or self._standard_anweisung()},
                {"role": "user", "content": json.dumps(nutzlast, ensure_ascii=False, default=str)},
            ],
            "text": {"format": {"type": "json_schema", "name": aufgabe_name,
                                "strict": True, "schema": schema}},
            "max_output_tokens": int(aufgabe.max_output_tokens),
            "store": False,
        }
        web_erlaubt = bool(getattr(self.cfg, "AI_WEB_SEARCH_ENABLED", True))
        pulsar_web = aufgabe_name == "pulsar_web_research"
        if pulsar_web:
            from pulsar.control import settings
            state = settings()
            if state["mode"] == "AUS" or not state.get("web_search", True):
                not_sent()
                return trace.finish(AIAntwort(grund="PULSAR-Websuche ausgeschaltet"),
                                    "NOT_STARTED", "PULSAR_WEB_DISABLED")
            web_erlaubt = True
        if aufgabe_name == "wochen_research" and bool(
                getattr(self.cfg, "AI_RESEARCH_ENABLED", False)):
            # Das Ergebnis verlangt pruefbare Quellen; ohne Websuche darf der
            # Job keine Vorschlaege erzeugen.
            web_erlaubt = True
        if aufgabe.web_suche and web_erlaubt:
            payload["tools"] = [{"type": "web_search",
                                 "search_context_size": str(getattr(
                                     self.cfg, "AI_SEARCH_CONTEXT_SIZE", "low"))}]
            payload["tool_choice"] = "auto"
            if pulsar_web:
                from pulsar.sources import search_domains
                payload["tools"][0]["filters"] = {"allowed_domains": search_domains(nutzlast.get("website", ""))}
                payload["tools"][0]["search_context_size"] = "low"
                payload["tool_choice"] = "required"
                payload["max_tool_calls"] = 2
                payload["include"] = ["web_search_call.action.sources"]

        schluessel = analysis_cache_key(aufgabe_name, model=modell, tier=stufe,
            payload=nutzlast, schema=schema, instruction=payload["input"][0]["content"],
            revision={"reasoning": aufgabe.reasoning, "max_output_tokens": aufgabe.max_output_tokens},
            tool_policy={k: payload.get(k) for k in ("tools", "tool_choice", "max_tool_calls", "include")})
        if cache_erlaubt and aufgabe.cache_stunden > 0:
            treffer = self.cache.hole(schluessel, aufgabe.cache_stunden)
            if treffer is not None:
                try:
                    if not treffer:
                        raise ValueError("Leere Antwort")
                    validate_json(treffer, schema)
                except ValueError:
                    treffer = None
            if treffer is not None:
                not_sent()
                self.budget.zaehle("cache_treffer")
                return trace.finish(AIAntwort(ok=True, daten=treffer, cache_treffer=True,
                    modell=modell, stufe=stufe, grund="aus dem modellgebundenen Zwischenspeicher"), "CACHE_HIT")

        start = time.time()
        preis_in, preis_out = self.preise(stufe)
        # Byte count is a conservative input-token bound, with protocol/schema reserve.
        estimated = ((len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 4096)
                     * preis_in + aufgabe.max_output_tokens * preis_out) / 1_000_000
        if payload.get("tools"):
            estimated += float(getattr(self.cfg, "AI_WEB_SEARCH_RESERVE_USD", .10))
        if pulsar_web:
            estimated = pulsar_web_reserve(preis_in, preis_out)
        usage_receipt = None
        def settle_usage(data, *, late=False):
            usage = data.get('usage') if isinstance(data,dict) else None
            if not isinstance(usage,dict) or not {'input_tokens','output_tokens'} <= set(usage):
                return None
            counts=[]
            for key in ('input_tokens','output_tokens'):
                raw=usage[key]
                count=int(raw)
                if isinstance(raw,bool) or count<0 or float(raw)!=count:
                    raise ValueError('Ungueltiger KI-Verbrauchsbeleg')
                counts.append(count)
            calls,_=web_receipts(data)
            cost=(counts[0]*preis_in+counts[1]*preis_out)/1_000_000
            if pulsar_web:cost += .01*calls
            if not math.isfinite(cost) or cost<0:raise ValueError('Ungueltiger KI-Verbrauchsbeleg')
            charged=cost+(float(getattr(self.cfg,'AI_WEB_SEARCH_RESERVE_USD',.10))
                if payload.get('tools') and not pulsar_web else 0)
            self.budget.abschliessen(token,input_tokens=counts[0],output_tokens=counts[1],kosten=charged)
            receipt=dict(kosten=cost,input_tokens=counts[0],output_tokens=counts[1],web_calls=calls,not_sent=False,late=late)
            deliver_usage(receipt)
            if late:
                trace.emit(
                    usage_only=True,late=True,input_tokens=counts[0],output_tokens=counts[1],kosten=cost,
                    request_id=str(data.get('id') or '')[:120])
            return receipt
        def late_response(response):
            if response.content:
                settle_usage(response.json(),late=True)
        try:
            token, blocked = self.budget.reserviere(stufe, max(.000001, estimated))
            if not token:
                not_sent()
                return trace.finish(AIAntwort(grund=blocked), "NOT_STARTED", "AI_BUDGET_BLOCKED")
            if not self.aktiv:
                self.budget.abschliessen(token, input_tokens=0, output_tokens=0, kosten=0)
                not_sent()
                return trace.finish(AIAntwort(grund="KI wurde vor der Anfrage ausgeschaltet"),
                                    "NOT_STARTED", "AI_DISABLED")
            if pulsar_web:
                state = settings()
                if state["mode"] == "AUS" or not state.get("web_search", True):
                    self.budget.abschliessen(token, input_tokens=0, output_tokens=0, kosten=0)
                    not_sent()
                    return trace.finish(AIAntwort(grund="PULSAR-Websuche wurde ausgeschaltet"),
                                        "NOT_STARTED", "PULSAR_WEB_DISABLED")
            timeout = float(timeout_seconds if timeout_seconds is not None else
                getattr(self.cfg, "AI_SECOND_OPINION_TIMEOUT_SECONDS", 8)
                if aufgabe_name == "second_opinion" else
                getattr(self.cfg, "AI_ROUTER_TIMEOUT_SECONDS", 90))
            if not math.isfinite(timeout) or timeout <= 0 or timeout > 120:
                raise ValueError("Ungueltiger KI-Zeitrahmen")
            # Unknown until the worker confirms dispatch, or explicitly proves
            # it did not send. A wall timeout alone does not prove non-delivery.
            trace.fields["request_dispatched"] = None
            antwort = _post_bounded(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {self.api_key}",
                         "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
                on_late_response=late_response,
                on_dispatch=trace.dispatch,
            )
            trace.received({})
            daten = antwort.json() if antwort.content else {}
            trace.received(daten)
            # Usage is independent of schema-valid text. Invalid content can
            # still have a real bill, which must not strand both reservations.
            usage_receipt = settle_usage(daten)
            if not antwort.ok:
                meldung = str((daten.get("error") or {}).get("message") or "")[:300]
                raise RuntimeError(f"OpenAI HTTP {antwort.status_code}: {meldung}")
            text = self._ausgabetext(daten)
            if not text or daten.get("status") in {"incomplete", "failed", "cancelled"}:
                raise ValueError("KI-Antwort fehlt oder ist unvollstaendig")
            geparst = json.loads(text)
            if not isinstance(geparst, dict) or not geparst:
                raise ValueError("Leere oder ungueltige KI-Antwort")
            validate_json(geparst, schema)
            web_calls, web_sources = web_receipts(daten)
            if pulsar_web and not 1 <= web_calls <= 2:
                raise ValueError("Keine bestaetigte begrenzte Websuche in der API-Antwort")
        except Exception as exc:
            proven_not_sent = (isinstance(exc, AIRequestNotSent)
                               or trace.fields["request_dispatched"] is False)
            if proven_not_sent and "token" in locals() and token:
                self.budget.abschliessen(token,input_tokens=0,output_tokens=0,kosten=0)
                not_sent()
            self.letzter_fehler = redact(exc, [self.api_key])[:400]
            self.budget.zaehle("fehler")
            self._setze_degraded(f"{aufgabe_name}: {self.letzter_fehler}")
            schreibe_ai_audit({"aufgabe": aufgabe_name, "cache_hit": False, "modell": modell,
                               "stufe": stufe, "fehler": self.letzter_fehler})
            phase = ("NOT_STARTED" if proven_not_sent else
                     "TIMED_OUT" if isinstance(exc, (TimeoutError, requests.exceptions.Timeout)) else "FAILED")
            code = ("AI_TRANSPORT_BUSY" if isinstance(exc, AIRequestNotSent) else
                    "AI_NOT_SENT" if proven_not_sent else
                    "AI_TIMEOUT" if phase == "TIMED_OUT" else
                    "AI_HTTP_"+str(antwort.status_code) if "antwort" in locals() and not antwort.ok else
                    "AI_RESPONSE_INVALID" if trace.fields["response_received"] and isinstance(exc, ValueError) else
                    "AI_CONNECTION_ERROR" if isinstance(exc, requests.exceptions.ConnectionError) else "AI_REQUEST_FAILED")
            return trace.finish(AIAntwort(modell=modell, stufe=stufe, grund=self.letzter_fehler,
                kosten=usage_receipt['kosten'] if usage_receipt else 0.0,
                usage_confirmed=bool(usage_receipt)), phase, code)

        try:
            nutzung = daten.get("usage") or {}
            eingabe = int(nutzung.get("input_tokens", 0) or 0)
            ausgabe = int(nutzung.get("output_tokens", 0) or 0)
            preis_in, preis_out = self.preise(stufe)
            kosten = (eingabe / 1_000_000.0) * preis_in + (ausgabe / 1_000_000.0) * preis_out
            if pulsar_web:
                kosten += .01 * web_calls
            if eingabe < 0 or ausgabe < 0 or not math.isfinite(kosten) or kosten < 0:
                raise ValueError('Ungueltiger KI-Verbrauchsbeleg')
        except (TypeError, ValueError, OverflowError, AttributeError) as exc:
            return trace.finish(AIAntwort(grund=redact(exc, [self.api_key])), "FAILED", "AI_USAGE_INVALID")

        try:
            # No usage receipt means an uncertain bill; retain the reservation.
            if not usage_receipt and "input_tokens" in nutzung and "output_tokens" in nutzung:
                # Search cost is not itemised in token usage: preserve its reserve.
                charged = kosten + (float(getattr(self.cfg, "AI_WEB_SEARCH_RESERVE_USD", .10))
                                    if payload.get("tools") and not pulsar_web else 0)
                self.budget.abschliessen(token, input_tokens=eingabe, output_tokens=ausgabe, kosten=charged)
        except Exception as exc:
            return trace.finish(AIAntwort(grund=redact(exc, [self.api_key])), "FAILED", "AI_USAGE_UNSETTLED")
        if not self.aktiv:
            return trace.finish(AIAntwort(grund="KI wurde waehrend der Anfrage ausgeschaltet"),
                                "DISCARDED", "AI_DISABLED_DURING_REQUEST")
        if cache_erlaubt and aufgabe.cache_stunden > 0:
            self.cache.setze(schluessel, geparst, aufgabe=aufgabe_name)
        self._degraded_aufheben()

        schreibe_ai_audit({
            "aufgabe": aufgabe_name, "cache_hit": False, "modell": modell, "stufe": stufe,
            "input_tokens": eingabe, "output_tokens": ausgabe, "kosten": round(kosten, 6),
            "dauer_sekunden": round(time.time() - start, 2),
            "routing_grund": grund,
            "instrumente": nutzlast.get("symbol") or nutzlast.get("symbole") or "",
        })
        return trace.finish(AIAntwort(ok=True, daten=geparst, modell=modell, stufe=stufe, kosten=kosten,
                         grund=grund, web_sources=web_sources, web_calls=web_calls,
                         usage_confirmed="input_tokens" in nutzung and "output_tokens" in nutzung), "SUCCEEDED")

    @staticmethod
    def _standard_anweisung() -> str:
        return (
            "Du unterstuetzt einen regelbasierten TradingBot. Du gibst KEINE Kauf- oder "
            "Verkaufsempfehlung, keine Freigabe und keine Positionsgroesse. Du darfst "
            "ausschliesslich einordnen, priorisieren und Risiken benennen. Erfinde keine "
            "Instrumente und keine Zahlen. Antworte ausschliesslich im vorgegebenen "
            "JSON-Schema und auf Deutsch."
        )

    @staticmethod
    def _ausgabetext(daten: dict) -> str:
        if isinstance(daten.get("output_text"), str):
            return daten["output_text"]
        teile = []
        for eintrag in daten.get("output", []) or []:
            if eintrag.get("type") != "message":
                continue
            for inhalt in eintrag.get("content", []) or []:
                if inhalt.get("type") in ("output_text", "text") and inhalt.get("text"):
                    teile.append(str(inhalt["text"]))
        return "\n".join(teile).strip()

    # -- Fertige Aufgaben ---------------------------------------------------
    def bewerte_universe_kandidat(self, daten: dict) -> dict:
        """Einstufung eines beobachteten Werts. Rueckgabe immer auswertbar."""
        antwort = self.frage(
            "universe_kandidat", daten, SCHEMA_EINSTUFUNG,
            kontext={"symbol": daten.get("symbol", "")},
            anweisung=(self._standard_anweisung() + " Bewerte, ob dieser Wert die "
                       "Aufmerksamkeit des Bots verdient. LOW bedeutet nur: im Focus Set "
                       "spaeter priorisieren. Du kannst weder Beobachtung noch Handel "
                       "freigeben oder blockieren."),
        )
        if not antwort.ok:
            return {"attention": "", "modell": "", "grund": antwort.grund}
        ergebnis = dict(antwort.daten)
        ergebnis["modell"] = antwort.modell
        return ergebnis

    def bewerte_universe_batch(self, eintraege: list[dict]) -> dict:
        """Mehrere Kandidaten in EINER Anfrage (AIU-010).

        Batching ist der zweitgroesste Kostenhebel nach dem Cache: 20
        Einzelanfragen kosten das Zwanzigfache derselben Sammelanfrage.
        """
        if not eintraege:
            return {}
        begrenzt = eintraege[:40]
        antwort = self.frage(
            "universe_batch", {"kandidaten": begrenzt,
                               "symbole": [e.get("symbol") for e in begrenzt]},
            SCHEMA_BATCH,
            anweisung=(self._standard_anweisung() + " Bewerte JEDEN uebergebenen Wert "
                       "einzeln. Verwende ausschliesslich die uebergebenen Symbole."),
        )
        if not antwort.ok:
            return {}
        out: dict[str, dict] = {}
        erlaubt = {str(e.get("symbol", "")).upper() for e in begrenzt}
        for zeile in antwort.daten.get("bewertungen", []) or []:
            symbol = str(zeile.get("symbol", "")).upper()
            if symbol in erlaubt:
                out[symbol] = {**zeile, "modell": antwort.modell}
        return out

    def pruefe_core_volume_diff(self, diff: dict) -> dict:
        antwort = self.frage(
            "core_volume_identity", diff, SCHEMA_CORE_VOLUME_IDENTITY,
            anweisung=(self._standard_anweisung() + " Prüfe ausschließlich die genannten "
                       "hinzugefügten/entfernten Kürzel auf mögliche Namensverwechslung, "
                       "Rebranding oder besondere dokumentierbare Risiken. Verändere die "
                       "Rangfolge nicht und füge kein Symbol hinzu."),
        )
        if not antwort.ok:
            raise RuntimeError(antwort.grund or "GPT-Prüfung fehlgeschlagen")
        return dict(antwort.daten)

    def research_anomalie(self, symbol: str, kontext: dict) -> dict:
        """Terra-Research fuer eine unerklaerte Bewegung (AIU-003)."""
        nutzlast = {"symbol": str(symbol).upper(), **(kontext or {})}
        antwort = self.frage(
            "anomalie_research", nutzlast, SCHEMA_RESEARCH,
            kontext={"anlass": "marktanomalie", "symbol": symbol},
            anweisung=(self._standard_anweisung() + " Untersuche die wahrscheinliche "
                       "Ursache der beschriebenen Bewegung. Nenne Quellen, wenn du "
                       "welche gefunden hast, und markiere Unsicherheit ehrlich als UNKLAR."),
        )
        if not antwort.ok:
            return {"ursache": "", "einordnung": "UNKLAR", "modell": "", "grund": antwort.grund}
        return {**antwort.daten, "modell": antwort.modell, "kosten": round(antwort.kosten, 6)}

    # -- Status -------------------------------------------------------------
    def status(self) -> dict:
        return {
            "aktiv": self.aktiv,
            "degraded": self._degraded,
            "degraded_grund": self._degraded_grund,
            "letzter_fehler": self.letzter_fehler,
            "luna_modell": self.modellname(LUNA),
            "terra_modell": self.modellname(TERRA),
            "budget": self.budget.status(),
            "request_diagnostics": request_diagnostics(),
        }


__all__ = [
    "AIRouter", "AIBudget", "AICache", "AIAntwort", "Aufgabe", "AUFGABEN",
    "LUNA", "TERRA", "KEINE", "SCHEMA_EINSTUFUNG", "SCHEMA_BATCH", "SCHEMA_RESEARCH", "SCHEMA_CORE_VOLUME_IDENTITY",
    "schreibe_ai_audit",
]
