"""Authentifizierte WebUI mit lesender Diagnose und getrennten Steuerauftraegen.

Bestätigte Positionsaufträge werden vom Handelskern erneut validiert. Keine
Diagnoseroute erzeugt Brokerorders oder aktiviert einen Handelsmodus.
"""
from __future__ import annotations

import hmac
import re
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from . import auth
from .settings_store import (
    save as save_settings, set_broker_mode, set_crypto_strategy_mode,
    set_risk_level, set_risk_profile,
    set_telegram_runtime,
    snapshot as settings_snapshot,
)
from .state import (
    bekannte_symbole, core_online, dashboard, decision_log, system_log,
    trade_analysis, universe,
)

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent

import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import config       # noqa: E402  -- fuer die Versionsnummer

app = FastAPI(title=f"TradingBot {getattr(config, 'VERSION_NEXUS', '')}",
              docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

_LOGIN_LOCK = threading.Lock()
_LOGIN_FAILURES: dict[str, list[float]] = {}
_LOGIN_WINDOW_SECONDS = 10 * 60
_LOGIN_MAX_FAILURES = 5


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    return response


def _login_key(request: Request) -> str:
    return str(request.client.host if request.client else "unknown")[:96]


def _login_blocked(key: str) -> bool:
    now = time.monotonic()
    with _LOGIN_LOCK:
        recent = [x for x in _LOGIN_FAILURES.get(key, []) if now - x < _LOGIN_WINDOW_SECONDS]
        _LOGIN_FAILURES[key] = recent
        return len(recent) >= _LOGIN_MAX_FAILURES


def _login_failed(key: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.setdefault(key, []).append(time.monotonic())


def _login_succeeded(key: str) -> None:
    with _LOGIN_LOCK:
        _LOGIN_FAILURES.pop(key, None)


def _session(request: Request, *, required: bool = True) -> dict | None:
    session = auth.decode_session(request.cookies.get(auth.COOKIE, ""))
    if required and not session:
        raise HTTPException(status_code=401, detail="Anmeldung erforderlich")
    return session


def _csrf(request: Request, session: dict) -> None:
    supplied = request.headers.get("x-csrf-token", "")
    expected = str(session.get("csrf") or "")
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=403, detail="CSRF-Pruefung fehlgeschlagen")


def _version() -> str:
    """Die Version kommt ausschliesslich aus der config.

    Bis v8.1.3 stand "8.1.2 NEXUS" an neun Stellen fest im Code -- nach jedem
    Update zeigte die Oberflaeche deshalb die alte Nummer.
    """
    roh = str(getattr(config, "VERSION_NEXUS", "") or "").strip()
    return roh.replace("-NEXUS", "").replace("NEXUS", "").strip() or "?"


def _page(name: str, session: dict) -> HTMLResponse:
    text = (HERE / "templates" / name).read_text(encoding="utf-8")
    text = text.replace("{{CSRF}}", str(session.get("csrf") or ""))
    text = text.replace("{{USER}}", str(session.get("u") or ""))
    text = text.replace("{{VERSION}}", _version())
    return HTMLResponse(text, headers={"Cache-Control": "no-store"})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _session(request, required=False):
        return RedirectResponse("/", status_code=303)
    text = (HERE / "templates" / "login.html").read_text(encoding="utf-8")
    setup = "" if auth.configured() else (
        '<p class="alert danger">Noch kein WebUI-Benutzer eingerichtet. Lokal ausfuehren: '
        '<code>python3 webui_setup.py</code></p>'
    )
    text = text.replace("{{SETUP}}", setup).replace("{{VERSION}}", _version())
    return HTMLResponse(text, headers={"Cache-Control": "no-store"})


@app.post("/login")
async def login(request: Request):
    if not auth.configured():
        return HTMLResponse("WebUI nicht eingerichtet", status_code=503)
    form = await request.form()
    username, password = str(form.get("username") or ""), str(form.get("password") or "")
    key = _login_key(request)
    if _login_blocked(key):
        return HTMLResponse("Zu viele Fehlversuche. Bitte spaeter erneut versuchen.", status_code=429)
    if not auth.authenticate(username, password):
        _login_failed(key)
        return HTMLResponse("Anmeldung fehlgeschlagen", status_code=401)
    _login_succeeded(key)
    token, _ = auth.issue_session(username)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict",
                        secure=auth.secure_cookie(), max_age=8 * 3600, path="/")
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    session = _session(request, required=False)
    return _page("dashboard.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    session = _session(request, required=False)
    return _page("settings.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/diagnosis", response_class=HTMLResponse)
async def diagnosis_page(request: Request):
    session = _session(request, required=False)
    return _page("diagnosis.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/api/diagnosis")
async def diagnosis_status(request: Request):
    _session(request)
    from .diagnosis_jobs import status
    return await run_in_threadpool(status)


@app.post("/api/diagnosis")
async def diagnosis_start(request: Request):
    _csrf(request, _session(request))
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungueltige Diagnoseparameter") from None
    if not isinstance(body, dict) or set(body) - {"mode", "telegram"}:
        raise HTTPException(400, "Ungueltige Diagnoseparameter")
    from .diagnosis_jobs import start
    try:
        return await run_in_threadpool(start, body.get("mode"), body.get("telegram", False))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/diagnosis/{identity}/download")
async def diagnosis_download(identity: str, request: Request):
    _session(request)
    from .diagnosis_jobs import archive_path
    try:
        p = await run_in_threadpool(archive_path, identity)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Diagnose-ZIP nicht verfuegbar") from None
    return FileResponse(p, media_type="application/zip", filename=p.name,
                        headers={"Cache-Control": "no-store"})


@app.get("/api/diagnosis/{identity}/summary")
async def diagnosis_summary(identity: str, request: Request):
    """10.5.0: aufbereitete Zusammenfassung aus der verifizierten Diagnose-ZIP (nur lesend)."""
    _session(request)
    from .diagnosis_jobs import summary
    try:
        return JSONResponse(await run_in_threadpool(summary, identity), headers={"Cache-Control": "no-store"})
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from None


@app.get("/diagnosis/{identity}/bericht", response_class=HTMLResponse)
async def diagnosis_report_page(identity: str, request: Request):
    session = _session(request, required=False)
    if not session:
        return RedirectResponse("/login", status_code=303)
    if not re.fullmatch(r"[0-9a-f]{32}", identity):
        raise HTTPException(404, "Ungueltige Diagnosekennung")
    response = _page("diagnosis_report.html", session)
    return HTMLResponse(response.body.decode("utf-8").replace("{{DIAGNOSIS_ID}}", identity),
                        headers={"Cache-Control": "no-store"})


@app.post("/api/diagnosis/{identity}/telegram")
async def diagnosis_telegram(identity: str, request: Request):
    _csrf(request, _session(request))
    from .diagnosis_jobs import send
    try:
        return await run_in_threadpool(send, identity)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/backtest", response_class=HTMLResponse)
async def backtest_page(request: Request):
    session = _session(request, required=False)
    return _page("backtest.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/api/backtest")
async def backtest_status(request: Request):
    _session(request)
    from .backtest_jobs import status
    return await run_in_threadpool(status)


@app.post("/api/backtest")
async def backtest_start(request: Request):
    _csrf(request, _session(request))
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungueltige Backtestparameter") from None
    from .backtest_jobs import start
    try:
        return await run_in_threadpool(start, body)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/backtest/{identity}/download")
async def backtest_download(identity: str, request: Request):
    _session(request)
    from .backtest_jobs import archive_path
    try:
        p = await run_in_threadpool(archive_path, identity)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Backtest-ZIP nicht verfuegbar") from None
    return FileResponse(p, media_type="application/zip", filename=p.name,
                        headers={"Cache-Control": "no-store"})


@app.get("/api/backtest/{identity}/report", response_class=HTMLResponse)
async def backtest_report(identity: str, request: Request):
    _session(request)
    from .backtest_jobs import report_path
    try:
        p = await run_in_threadpool(report_path, identity)
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Backtest-Bericht nicht verfuegbar") from None
    return FileResponse(p, media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-store",
                                 "Content-Security-Policy":
                                 "default-src 'none'; style-src 'unsafe-inline'; img-src data:"})


@app.get("/api/backtest/{identity}/log")
async def backtest_log(identity: str, request: Request):
    _session(request)
    from .backtest_jobs import log_tail
    try:
        return {"lines": await run_in_threadpool(log_tail, identity)}
    except (ValueError, FileNotFoundError):
        raise HTTPException(404, "Backtest-Protokoll nicht verfuegbar") from None


@app.get("/universe", response_class=HTMLResponse)
@app.get("/underdogs", response_class=HTMLResponse)
async def universe_page(request: Request):
    session = _session(request, required=False)
    return _page("universe.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/api/universe")
async def api_universe(request: Request):
    _session(request)
    return await run_in_threadpool(universe)


@app.get("/pulsar", response_class=HTMLResponse)
async def pulsar_page(request: Request):
    session = _session(request, required=False)
    return _page("pulsar.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/sources", response_class=HTMLResponse)
async def sources_page(request: Request):
    session = _session(request, required=False)
    return _page("sources.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/api/market-intelligence")
async def market_intelligence_status(request: Request):
    _session(request)
    from .market_sources import snapshot
    return JSONResponse(await run_in_threadpool(snapshot), headers={"Cache-Control": "no-store"})


@app.post("/api/market-intelligence/settings")
async def market_intelligence_settings(request: Request):
    session = _session(request)
    _csrf(request, session)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungueltige Einstellungen") from None
    allowed = {"enabled", "monthly_budget_eur", "symbols", "priority_accounts",
               "pricing_acknowledged", "bearer_token"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise HTTPException(400, "Unbekannte X-Einstellung")
    token = payload.pop("bearer_token", None)
    if token is not None and (not isinstance(token, str) or len(token) > 4096):
        raise HTTPException(400, "Ungueltiges Tokenformat")
    from market_intelligence import save_settings
    try:
        result = await run_in_threadpool(save_settings, payload, bearer_token=token or None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    return JSONResponse({"settings": result, "detail": "X-Einstellungen gespeichert. Der gemeinsame Sammler beachtet Zeitplan und Budget."},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/market-intelligence/registry")
async def market_intelligence_registry(request: Request):
    session = _session(request)
    _csrf(request, session)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, "Ungueltige Registry-Anfrage") from None
    if (not isinstance(payload, dict)
            or set(payload) - {"account_id", "action", "note"}
            or payload.get("action") not in {"activate", "quarantine", "remove"}):
        raise HTTPException(400, "Ungueltige Registry-Anfrage")
    from market_intelligence import account_registry
    actor = f"webui:{session.get('u')}"
    note = str(payload.get("note", ""))
    handlers = {"activate": lambda: account_registry.activate(
                    payload["account_id"], actor=actor, identity_note=note),
                "quarantine": lambda: account_registry.quarantine(
                    payload["account_id"], actor=actor, reason=note),
                "remove": lambda: account_registry.remove(
                    payload["account_id"], actor=actor, reason=note)}
    try:
        result = await run_in_threadpool(handlers[payload["action"]])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    return JSONResponse({"row": result, "detail": "Registry aktualisiert; keine Handelswirkung."},
                        headers={"Cache-Control": "no-store"})


@app.post("/api/okx/account-confirmation/retry")
async def okx_confirmation_retry(request: Request):
    session = _session(request)
    _csrf(request, session)
    try:
        payload = await request.json()
        if not isinstance(payload, dict) or set(payload) != {'acknowledged', 'revision'} or payload['acknowledged'] is not True:
            raise ValueError('Bitte zuerst die Erklärung im eigenen OKX-Konto prüfen und bestätigen')
        from . import state as _state
        runtime = _state._runtime('runtime_status_okx.json')
        action = runtime.get('account_action') or {}
        if not runtime.get('worker_alive') or not action.get('required'):
            raise ValueError('Kein aktueller Kontohinweis des Handelskerns vorhanden')
        from okx_account_action import allow_retry
        result = await run_in_threadpool(allow_retry, action.get('account'), action.get('environment'),
            revision=payload['revision'], actor=str(session.get('u') or 'WebUI'))
    except (ValueError, TypeError) as exc:
        raise HTTPException(409, str(exc)) from None
    return JSONResponse(result, headers={'Cache-Control': 'no-store'})


@app.get("/api/pulsar")
async def pulsar_snapshot(request: Request):
    _session(request)
    from pulsar.presentation import snapshot
    return await run_in_threadpool(snapshot)


@app.get("/api/pulsar/source/{symbol}/{identifier}", response_class=HTMLResponse)
async def pulsar_source(symbol: str, identifier: str, request: Request):
    _session(request)
    from .source_receipts import html_receipt
    try:
        value = await run_in_threadpool(html_receipt, symbol, identifier)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, str(exc)) from None
    return HTMLResponse(value, headers={"Cache-Control": "no-store"})


@app.get("/api/pulsar/measurements")
async def pulsar_measurements(request: Request, limit: int = 200):
    """10.5.0: Vorwaertsmessungen der Hype-Spur (Papierergebnis je Ausloeser), nur lesend."""
    _session(request)
    from pulsar.measurement import rows, public_row, summarize
    def lesen():
        data = rows(limit=max(1, min(2000, int(limit))))
        return {"rows": [public_row(r) for r in data], "summary": summarize(data)}
    return await run_in_threadpool(lesen)


@app.get("/api/pulsar/export")
async def pulsar_export(request: Request):
    _session(request)
    from fastapi.responses import Response
    from pulsar.presentation import export_json
    body = await run_in_threadpool(export_json)
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": 'attachment; filename="NEXUS_PULSAR_Archiv.json"'})


@app.post("/api/pulsar/settings")
async def pulsar_settings(request: Request):
    session = _session(request)
    _csrf(request, session)
    payload = await request.json()
    from pulsar.control import set_mode
    try:
        result = await run_in_threadpool(set_mode, str(payload.get("mode") or ""),
                                         tradestie=payload.get("tradestie"), web_search=payload.get("web_search"),
                                         stocktwits=payload.get("stocktwits"), finra=payload.get("finra"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"mode": result["mode"], "revision": result["revision"]}


# ---------------------------------------------------------------------------
# Trade-Analyse (rein lesend, neu in v9.0.1)
# ---------------------------------------------------------------------------
@app.get("/trades", response_class=HTMLResponse)
async def trades_page(request: Request):
    session = _session(request, required=False)
    return _page("trades.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/api/trades")
async def api_trades(request: Request, broker: str = "", tage: int = 90):
    _session(request)
    return await run_in_threadpool(trade_analysis, broker=broker, tage=tage)


# ---------------------------------------------------------------------------
# Analyse & Training (dieselben Programme wie in der Desktop-GUI)
# ---------------------------------------------------------------------------
@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page(request: Request):
    session = _session(request, required=False)
    return _page("analysis.html", session) if session else RedirectResponse("/login", status_code=303)


@app.get("/api/analysis")
async def api_analysis(request: Request):
    _session(request)
    from webui import analysis_jobs
    return await run_in_threadpool(analysis_jobs.overview)


@app.post("/api/analysis/{task}")
async def api_analysis_start(task: str, request: Request):
    session = _session(request); _csrf(request, session)
    from webui import analysis_jobs
    try:
        job = await run_in_threadpool(
            analysis_jobs.start, task, f"webui:{session.get('u')}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return {"ok": True, "job": job,
            "detail": "Analyse lokal gestartet. Handelslogik und Broker bleiben unangetastet."}


@app.get("/api/analysis/jobs/{job_id}")
async def api_analysis_output(job_id: str, request: Request):
    _session(request)
    from webui import analysis_jobs
    try:
        return await run_in_threadpool(analysis_jobs.output, job_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail="Analyseauftrag nicht gefunden") from exc


@app.post("/api/trades/{trade_id}/reconciliation")
async def api_trade_reconciliation(trade_id: int, request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    import okx_reconciliation_actions as actions
    try:
        action = await run_in_threadpool(
            actions.anfordern, trade_id, str(payload.get("action") or ""),
            symbol=str(payload.get("symbol") or ""),
            actor=f"webui:{session.get('u')}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "action": action,
            "detail": ("Klärungsaktion sicher vorgemerkt. Der OKX-Kern prüft sie "
                       "im nächsten Zyklus; Brokerbestand und Orders bleiben unangetastet.")}


@app.get("/api/etoro/settlement/{trade_id}")
async def api_etoro_settlement_preview(trade_id: int, request: Request):
    _session(request)
    from etoro_settlement_review import preview
    try:
        return await run_in_threadpool(preview, trade_id)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Abrechnungsbelege derzeit nicht lesbar") from None


@app.post("/api/etoro/settlement/{trade_id}")
async def api_etoro_settlement_confirm(trade_id: int, request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Ungültige Bestätigung")
    from etoro_settlement_review import confirm
    try:
        return await run_in_threadpool(confirm, trade_id,
            token=str(payload.get("token") or ""), actor=f"webui:{session.get('u')}",
            no_other_cashflows=payload.get("no_other_cashflows"))
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except sqlite3.Error:
        raise HTTPException(status_code=503, detail="Abrechnung konnte nicht gespeichert werden") from None


@app.get("/api/etoro/cancellations")
async def api_etoro_cancellations(request: Request):
    _session(request)
    from etoro_cancellations import overview, available
    return {"requests": await run_in_threadpool(overview),
            "available": await run_in_threadpool(available)}


@app.post("/api/etoro/cancellations")
async def api_etoro_cancel_request(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Ungültiger Auftrag")
    from etoro_cancellations import enqueue
    try:
        return await run_in_threadpool(enqueue, account=str(payload.get('account') or ''),
            paper=payload.get('paper'), order_id=str(payload.get('order_id') or ''),
            actor=f"webui:{session.get('u')}")
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.get("/api/manual-trades")
async def api_manual_trades(request: Request):
    _session(request)
    import manual_trade_control
    return await run_in_threadpool(manual_trade_control.overview)


@app.post("/api/manual-trades/{trade_id}")
async def api_manual_trade_request(trade_id: int, request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    import manual_trade_control
    try:
        command = await run_in_threadpool(
            manual_trade_control.request, trade_id,
            str(payload.get("action") or ""),
            stop=payload.get("stop") or 0.0,
            take_profit=payload.get("take_profit") or 0.0,
            lock=str(payload.get("lock") or "6H"),
            actor=f"webui:{session.get('u')}",
            confirmation=str(payload.get("confirmation") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "command": command,
            "detail": ("Manueller Einmalauftrag vorgemerkt. Der OKX-Kern prueft "
                       "ihn im naechsten Positionszyklus und zeigt danach den "
                       "bestaetigten Brokerzustand.")}


@app.post("/api/manual-trades/locks/{lock_id}/release")
async def api_manual_trade_lock_release(lock_id: str, request: Request):
    session = _session(request); _csrf(request, session)
    import manual_trade_control
    changed = await run_in_threadpool(
        manual_trade_control.release_lock, lock_id, f"webui:{session.get('u')}")
    if not changed:
        raise HTTPException(status_code=404, detail="Aktive Sperre nicht gefunden.")
    return {"ok": True, "detail": "Wiedereinstiegssperre aufgehoben."}


@app.get("/api/trades/{trade_id}/candles")
async def api_trade_candles(trade_id: int, request: Request, bar: str = ""):
    _session(request)
    from trade_chart_data import chart_for_trade
    # 10.4.0: waehlbarer Zeitrahmen (5m/15m/1h/4h/1d); leer = Automatik.
    safe_bar = bar if bar in {"", "5m", "15m", "1h", "4h", "1d"} else ""
    return await run_in_threadpool(chart_for_trade, trade_id, bar=safe_bar)


# ---------------------------------------------------------------------------
# Positionen (neu in v8.1.5)
# ---------------------------------------------------------------------------
# Georg wollte eine selbst gekaufte Aktie an den Bot uebergeben und hatte
# dafuer in der WebUI keine Stelle. Diese Seite ist die Stelle.
#
# Die Oberflaeche fasst dabei NIE selbst den Broker oder das Positionsbuch an.
# Sie zeigt die Momentaufnahme des Kerns (stock_positions.json) und legt
# Auftraege ab, die der Kern in seiner eigenen Schleife ausfuehrt.
@app.get("/positions", response_class=HTMLResponse)
async def positions_page(request: Request):
    session = _session(request, required=False)
    return RedirectResponse("/trades#bestand" if session else "/login", status_code=303)


@app.get("/api/executions")
async def api_executions(request: Request, broker: str = ""):
    _session(request)
    if broker not in ("", "okx", "etoro"):
        raise HTTPException(status_code=400, detail="Unbekannter Broker")
    from webui.diagnostics import execution_history
    return await run_in_threadpool(execution_history, broker=broker, limit=100, active_only=True)


@app.get("/api/executions/history")
async def api_execution_history(request: Request, broker: str = "", page: int = 1, limit: int = 20):
    _session(request)
    from webui.diagnostics import execution_history
    try:
        return await run_in_threadpool(execution_history, broker=broker, page=page, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/executions/detail")
async def api_execution_detail(request: Request, broker: str = "", account: str = "",
                               environment: str = "", client_id: str = ""):
    _session(request)
    import sqlite3
    from webui.diagnostics import execution_detail
    try:
        return await run_in_threadpool(execution_detail, broker=broker, account=account,
                                      environment=environment, client_id=client_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail="EXECUTION_DETAILS_UNAVAILABLE: Orderbelege nicht lesbar") from None


@app.get("/api/ai-diagnostics")
async def api_ai_diagnostics(request: Request):
    _session(request)
    from webui.diagnostics import ai_diagnostics
    return await run_in_threadpool(ai_diagnostics)


@app.get("/api/positions")
async def api_positions(request: Request):
    _session(request)
    import positions_auftraege
    from webui import state as _state
    daten = _state._json("stock_positions.json",
                         {"positionen": [], "kurse_verfuegbar": False})
    krypto = _state._json("crypto_positions.json", {"positionen": []})
    etoro_runtime = _state._runtime("runtime_status.json")
    fmp_cache = _state._json("fmp_reference_cache.json", {})
    from webui.display_evidence import position_risk_display
    daten = {**daten, "positionen": [dict(row,
        protection_evidence=_state.protection_display_evidence(row, etoro_runtime),
        account_risk_display=position_risk_display(row, etoro_runtime),
        fmp_context=_state.fmp_display_context(row.get("symbol"), fmp_cache))
        for row in (daten.get("positionen") or [])]}
    # Nur vollstaendig bewiesene, automatisch verwaltete Positionen. Alte
    # BEOBACHTEN-/synthetische Fill-Zeilen gehoeren in "Klaerung noetig".
    krypto = {**krypto, "positionen": [row for row in (krypto.get("positionen") or [])
              if bool(row.get("ownership_verified"))
              and str(row.get("verwaltung") or "").upper() in {"AUTO", "MANUELL"}
              and bool(row.get("fill_ids"))]}
    # Das Positionsbuch enthaelt bewusst nur Bot-Positionen. Das getrennte
    # Kontoobjekt zeigt deshalb Guthaben wie XRP/ETH/SOL im OKX-Demokonto,
    # ohne sie fälschlich als offene Orders auszugeben.
    return {"etoro": daten, "okx": krypto, "okx_konto": _state._okx_detail(),
            "auftraege": await run_in_threadpool(positions_auftraege.uebersicht),
            "standard_stop_pct": round(float(getattr(config, "STOP_LOSS_PCT", 0.025)) * 100, 2),
            "standard_ziel_pct": round(float(getattr(config, "TAKE_PROFIT_PCT", 0.05)) * 100, 2),
            "gueltig_minuten": positions_auftraege.AUFTRAG_GUELTIG_MINUTEN}


@app.post("/api/positions/{aktion}")
async def api_positions_auftrag(aktion: str, request: Request):
    session = _session(request); _csrf(request, session)
    import positions_auftraege
    if aktion not in positions_auftraege.AKTIONEN:
        raise HTTPException(status_code=404, detail="unbekannte Aktion")
    payload = await request.json()
    try:
        auftrag = await run_in_threadpool(
            positions_auftraege.anfordern,
            str(payload.get("symbol") or ""), aktion,
            stop_pct=payload.get("stop_pct", 0.0),
            ziel_pct=payload.get("ziel_pct", 0.0),
            record_id=str(payload.get("record_id") or ""),
            position_ids=payload.get("position_ids") or [],
            instrument_id=str(payload.get("instrument_id") or ""),
            account_fingerprint=str(payload.get("account_fingerprint") or ""),
            snapshot_id=str(payload.get("snapshot_id") or ""),
            von=f"webui:{session.get('u')}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "auftrag": auftrag,
            "detail": ("Auftrag hinterlegt. Der Handelskern führt ihn beim "
                       "nächsten Durchlauf am aktuellen Kurs aus — Stop und "
                       "Ziel werden dann gegen den echten Kurs geprüft.")}


@app.get("/logbook", response_class=HTMLResponse)
async def logbook_page(request: Request):
    session = _session(request, required=False)
    return _page("logbook.html", session) if session else RedirectResponse("/login", status_code=303)


@app.post("/api/logout")
async def logout(request: Request):
    session = _session(request); _csrf(request, session)
    response = JSONResponse({"ok": True})
    response.delete_cookie(auth.COOKIE, path="/")
    return response


@app.get("/api/dashboard")
async def api_dashboard(request: Request):
    _session(request)
    return await run_in_threadpool(dashboard)


@app.get("/api/performance")
async def api_performance(request: Request, days: int = 90):
    _session(request)
    if not 1 <= days <= 365:
        raise HTTPException(status_code=400, detail="Zeitraum muss 1 bis 365 Tage betragen")
    from trade_performance import snapshot
    return await run_in_threadpool(snapshot, days=days)


@app.get("/api/broker-contexts")
async def api_broker_contexts(request: Request):
    _session(request)
    from webui.state import broker_contexts
    return await run_in_threadpool(broker_contexts)


@app.get("/api/accounting")
async def api_accounting(request: Request):
    _session(request)
    from webui.state import accounting_overview
    return await run_in_threadpool(accounting_overview)


@app.get("/api/logs/scoped")
async def api_logs_scoped(request: Request, search: str = "", level: str = "", broker: str = "", limit: int = 300):
    _session(request)
    from webui.state import system_log_scoped
    return {"entries": await run_in_threadpool(system_log_scoped, search=search, level=level, broker=broker, limit=limit),
            "symbole": await run_in_threadpool(bekannte_symbole)}


@app.get("/api/settings")
async def api_settings(request: Request):
    _session(request)
    return await run_in_threadpool(settings_snapshot)


@app.post("/api/settings")
async def api_settings_save(request: Request):
    session = _session(request); _csrf(request, session)
    try:
        payload = await request.json()
        result = await run_in_threadpool(save_settings, payload)
        return {"ok": True, "settings": result,
                "detail": ("Gespeichert. News-Quellen, API-Schluessel und Budgets wirken SOFORT -- ohne Neustart. Broker-Zugaenge, Handelsmodus und Risikogrenzen werden wie bisher beim naechsten Start des Trading-Core uebernommen.")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/telegram/runtime")
async def api_telegram_runtime(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    try:
        result = await run_in_threadpool(
            set_telegram_runtime, bool(payload.get("enabled")))
        aktiv = bool(result.get("telegram", {}).get("enabled"))
        return {"ok": True, "settings": result,
                "detail": ("Telegram ist sofort aktiviert; Versand und Fernsteuerung laufen."
                           if aktiv else
                           "Telegram ist sofort deaktiviert; es werden keine Nachrichten gesendet und keine Befehle angenommen.")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/logs/scan-uebersicht")
async def api_scan_uebersicht(request: Request, limit: int = 12):
    _session(request)
    from scan_uebersicht import lesen
    try:
        return await run_in_threadpool(lesen, max(1, min(48, int(limit))))
    except (OSError, ValueError):
        raise HTTPException(status_code=503, detail="Scan-Uebersicht derzeit nicht lesbar") from None


@app.get("/api/logs/decisions")
async def api_decisions(request: Request, page: int = 1, per_page: int = 15,
                        broker: str = "", asset_type: str = "", status: str = "",
                        source: str = "", symbol: str = "", day: str = "",
                        search: str = "", strategy: str = "", reason: str = ""):
    _session(request)
    import sqlite3
    try:
        return await run_in_threadpool(
            decision_log, page=page, per_page=per_page, broker=broker,
            asset_type=asset_type, status=status, source=source, symbol=symbol, day=day,
            search=search[:200], strategy=strategy[:120], reason=reason[:200],
        )
    except (OSError, sqlite3.Error):
        raise HTTPException(status_code=503, detail="DECISION_HISTORY_UNAVAILABLE: Entscheidungsdaten nicht vollständig lesbar") from None


@app.get("/api/logs/system")
async def api_system_log(request: Request, search: str = "", level: str = "", limit: int = 300):
    _session(request)
    # 9.5.6: Die Symbolliste kommt mit, damit die Oberflaeche Coin- und
    # Aktiennamen hervorheben kann, ohne raten zu muessen. Ein Muster fuer
    # Grossbuchstaben wuerde INFO, WARNING, OKX und USDC mitfaerben.
    return {"rows": await run_in_threadpool(system_log, search=search,
                                            level=level, limit=limit),
            "symbole": await run_in_threadpool(bekannte_symbole)}


@app.post("/api/providers/test")
async def api_provider_test(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    source = str(payload.get("source") or "")
    from news_sources import MultiSourceNews
    return await run_in_threadpool(MultiSourceNews().active_health_test, source, "AAPL")


@app.post("/api/ai/test")
async def api_ai_test(request: Request):
    """Prueft den KI-Zugang mit einer kleinen, billigen Anfrage.

    Bis v8.1.3 gab es fuer eToro, OKX und die Nachrichtenquellen je einen
    Testknopf -- fuer die KI keinen. Ob der OpenAI-Schluessel stimmt, liess
    sich nur daran erkennen, dass irgendwann keine Antwort kam.
    """
    session = _session(request); _csrf(request, session)
    return await run_in_threadpool(_ai_test)


def _ai_test() -> dict:
    import time as _time
    try:
        from ai_router import AIRouter
    except Exception as exc:
        return {"ok": False, "detail": f"KI-Router nicht ladbar: {exc}"}

    try:
        router = AIRouter(config)
    except Exception as exc:
        return {"ok": False, "detail": f"KI-Router nicht startbar: {exc}"}

    if not getattr(router, "aktiv", False):
        return {"ok": False, "detail": "KI ist ausgeschaltet oder kein Schluessel hinterlegt.",
                "status": router.status() if hasattr(router, "status") else {}}

    schema = {"type": "object", "additionalProperties": False,
              "required": ["antwort"],
              "properties": {"antwort": {"type": "string", "maxLength": 40}}}
    start = _time.monotonic()
    try:
        antwort = router.frage(
            "second_opinion", {"pruefung": "verbindungstest"}, schema,
            anweisung="Antworte ausschliesslich mit {\"antwort\": \"bereit\"}.",
            cache_erlaubt=False)
    except Exception as exc:
        return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    dauer = round(_time.monotonic() - start, 2)
    if not getattr(antwort, "ok", False):
        return {"ok": False, "detail": str(getattr(antwort, "grund", "keine Antwort")),
                "dauer_s": dauer}
    return {
        "ok": True,
        "detail": f"Antwort in {dauer} s",
        "modell": str(getattr(antwort, "modell", "")),
        "kosten_usd": round(float(getattr(antwort, "kosten", 0.0) or 0.0), 6),
        "dauer_s": dauer,
        "status": router.status() if hasattr(router, "status") else {},
    }


@app.post("/api/brokers/test")
async def api_broker_test(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    from broker_diagnostics import test_connection
    try:
        return await run_in_threadpool(
            test_connection, str(payload.get("broker") or ""), str(payload.get("mode") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/brokers/mode")
async def api_broker_mode(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    try:
        result = await run_in_threadpool(
            set_broker_mode, str(payload.get("broker") or ""),
            str(payload.get("mode") or ""), str(payload.get("confirm") or ""),
        )
        return {"ok": True, "settings": result,
                "detail": "Modus gespeichert und LIVE-Arming entfernt. Trading-Core neu starten."}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/risk-profile")
async def api_risk_profile(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    try:
        result = await run_in_threadpool(
            set_risk_profile, str(payload.get("profile") or ""),
            str(payload.get("confirm") or ""),
        )
        return {"ok": True, "settings": result,
                "detail": "Risikoprofil gespeichert. Es gilt nach Neustart sicher fuer beide Domaenen."}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/risk-level")
async def api_risk_level(request: Request):
    """Einsatzstufe je Broker (10.6.0) -- wirkt ohne Neustart des Trading-Core."""
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    try:
        broker = str(payload.get("broker") or "")
        result = await run_in_threadpool(
            set_risk_level, broker, str(payload.get("level") or ""),
            str(payload.get("confirm") or ""),
        )
        row = (result.get("risk_levels") or {}).get(broker.strip().lower(), {})
        wirksam = row.get("wirksam") or {}
        risiko = float(wirksam.get("risiko_pro_trade_pct") or 0.0) * 100
        deckel = float(wirksam.get("max_position_pct") or 0.0) * 100
        return {"ok": True, "settings": result,
                "detail": (f"{broker.upper()}: Einsatz {wirksam.get('label') or ''} aktiv -- "
                           f"{risiko:.2f} % Risiko je Trade, hoechstens {deckel:.0f} % je Position. "
                           "Gilt ab dem naechsten Pruefzyklus ohne Neustart und nur fuer neue "
                           "Einstiege; offene Positionen behalten ihre Groesse.")}
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/crypto-strategy")
async def api_crypto_strategy(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    try:
        result = await run_in_threadpool(
            set_crypto_strategy_mode, str(payload.get("mode") or ""),
            source=f"webui:{session.get('u')}",
            confirm=str(payload.get("confirm") or ""),
            reason=str(payload.get("reason") or "WebUI-Laufzeitwechsel"),
        )
        return {"ok": True, "settings": result,
                "detail": ("OKX-Strategiemodus sofort gespeichert. Der Wechsel gilt nur "
                           "fuer neue Einstiege; offene Positionen behalten ihre "
                           "gespeicherte Einstiegsstrategie. Kein Neustart erforderlich.")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/etoro-strategy")
async def api_etoro_strategy(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    try:
        from webui.settings_store import set_etoro_strategy_mode
        result = await run_in_threadpool(
            set_etoro_strategy_mode, str(payload.get("mode") or ""),
            source=f"webui:{session.get('u')}",
            confirm=str(payload.get("confirm") or ""),
            reason=str(payload.get("reason") or "WebUI-Laufzeitwechsel"),
        )
        return {"ok": True, "settings": result,
                "detail": ("eToro-Strategiemodus sofort gespeichert. Der Wechsel gilt nur "
                           "fuer neue Aktien-Einstiege; offene Positionen behalten ihre "
                           "gespeicherte Einstiegsstrategie. Kein Neustart erforderlich.")}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/migration")
async def api_migration(request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    if str(payload.get("confirm") or "") != "UEBERNEHMEN":
        raise HTTPException(status_code=400, detail="Bestaetigung 'UEBERNEHMEN' erforderlich")
    if await run_in_threadpool(core_online):
        raise HTTPException(status_code=409, detail="Trading-Core zuerst stoppen; WebUI darf weiterlaufen.")
    from settings_migration import bericht, migrate_from
    source = Path(str(payload.get("source") or "")).resolve()
    allowed = {Path(x).resolve() for x in bericht(ROOT).get("gefundene_vorgaengerversionen", [])}
    if source not in allowed:
        raise HTTPException(status_code=400, detail="Quelle ist keine erkannte Vorgaengerversion.")
    copied, skipped = await run_in_threadpool(migrate_from, source, ROOT, False)
    return {"ok": True, "copied": copied, "skipped": skipped,
            "detail": ("Uebernahme abgeschlossen. Beide Broker wurden sicher auf Demo/Paper "
                       "gesetzt und alle LIVE-Freigaben entfernt.")}


@app.post("/api/control/{action}")
async def api_control(action: str, request: Request):
    session = _session(request); _csrf(request, session)
    payload = await request.json()
    mapping = {
        "pause": ("pausiert", "PAUSIEREN"),
        "resume": ("aktiv", "AKTIVIEREN"),
        "stop": ("gestoppt", "VOLLSTAENDIG STOPPEN"),
    }
    if action not in mapping:
        raise HTTPException(status_code=404, detail="unbekannte Aktion")
    state, phrase = mapping[action]
    if str(payload.get("confirm") or "") != phrase:
        raise HTTPException(status_code=400, detail=f"Bestaetigung '{phrase}' erforderlich")
    from bot_zustand import BotZustand
    bot = BotZustand(str(ROOT / "bot_zustand.json"))
    previous = bot.setze(state, str(payload.get("reason") or "WebUI"), f"webui:{session.get('u')}")
    return {"ok": True, "previous": previous, "state": state}
