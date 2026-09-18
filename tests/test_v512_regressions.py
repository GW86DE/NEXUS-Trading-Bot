from __future__ import annotations
import io
from pathlib import Path
import os
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[1]


def test_telegram_report_uses_attention_instance_not_undefined_ai():
    text=(ROOT/"live_trader.py").read_text(encoding="utf-8")
    start=text.index("def _instant_report")
    block=text[start:start+900]
    assert "ai=attention" in block
    assert "ai=ai" not in block


def test_gui_tool_reader_does_not_wait_for_newline():
    from tool_runner import iter_stream_chunks
    stream=io.StringIO("US-Ticker: ")
    assert "".join(iter_stream_chunks(stream,1)) == "US-Ticker: "


def test_gui_child_forces_utf8():
    from tool_runner import utf8_child_env
    env=utf8_child_env({"X":"1"})
    assert env["PYTHONIOENCODING"]=="utf-8"
    assert env["PYTHONUTF8"]=="1"
    assert env["PYTHONUNBUFFERED"]=="1"


def test_fmp_is_not_blocked_by_legacy_master_switch(monkeypatch):
    import config
    from news_sources import MultiSourceNews
    monkeypatch.setattr(config,"NEWS_LEGACY_OPTIONAL_SOURCES_ENABLED",False,raising=False)
    monkeypatch.setattr(config,"NEWS_SOURCE_FMP_ENABLED",True,raising=False)
    monkeypatch.setattr(config, "FMP_NEWS_ENABLED", True, raising=False)
    import fmp_service
    monkeypatch.setattr(fmp_service, "settings", lambda: ("AUTO", "STARTER"))
    monkeypatch.setattr(config,"FMP_API_KEY","secret",raising=False)
    assert MultiSourceNews().provider_configuration()["FMP"] is True


class _Resp:
    def __init__(self,status=200,data=None,reason=""):
        self.status_code=status; self._data=data; self.reason=reason; self.content=b"x"; self.ok=200<=status<300
    def json(self): return self._data

class _Session:
    def __init__(self,resp): self.resp=resp; self.calls=[]; self.headers={}
    def get(self,url,params=None,timeout=None,headers=None,**kwargs):
        self.calls.append((url,dict(params or {})))
        return self.resp


def test_fmp_stable_symbol_search_uses_current_endpoint(monkeypatch):
    import config
    from news_sources import MultiSourceNews
    monkeypatch.setattr(config,"NEWS_SOURCE_FMP_ENABLED",True,raising=False)
    monkeypatch.setattr(config, "FMP_NEWS_ENABLED", True, raising=False)
    import fmp_service
    monkeypatch.setattr(fmp_service, "settings", lambda: ("AUTO", "STARTER"))
    monkeypatch.setattr(config,"FMP_API_KEY","TOPSECRET",raising=False)
    c=MultiSourceNews(); fake=_Session(_Resp(200,[{"symbol":"AAPL","name":"Apple Inc."}]))
    c.session=fake
    rows=c.fmp_search_symbol("AAPL")
    assert rows[0]["symbol"]=="AAPL"
    url,params=fake.calls[0]
    assert url.endswith("/stable/search-symbol")
    assert params["query"]=="AAPL"
    assert "apikey" not in params  # Authentication uses the header, covered by transport tests.


def test_fmp_http_error_does_not_leak_api_key(monkeypatch):
    import config
    from news_sources import MultiSourceNews
    monkeypatch.setattr(config,"NEWS_SOURCE_FMP_ENABLED",True,raising=False)
    monkeypatch.setattr(config, "FMP_NEWS_ENABLED", True, raising=False)
    import fmp_service
    monkeypatch.setattr(fmp_service, "settings", lambda: ("AUTO", "STARTER"))
    monkeypatch.setattr(config,"FMP_API_KEY","TOPSECRET",raising=False)
    c=MultiSourceNews(); c.session=_Session(_Resp(429,{"message":"Too many requests"},"Too Many Requests"))
    with pytest.raises(RuntimeError) as ei:
        c.fmp_search_symbol("AAPL")
    assert "TOPSECRET" not in str(ei.value)
    assert "429" in str(ei.value)


def test_crypto_diagnose_accepts_etoro_dict_quote():
    from contracts import Instrument,SimpleContract
    from crypto_diagnose import diagnose_one
    class B:
        def instrument_metadata(self,inst): return {"instrumentId":123}
        def instrument_handelbar(self,inst): return True,"REAL/Hebel 1"
        def latest_bid_ask(self,inst): return {"bid":100.0,"ask":100.2,"last":100.1,"source":"fake"}
    inst=Instrument("BTC",SimpleContract("BTC"),"crypto","USD")
    row=diagnose_one(B(),inst)
    assert row["resolved"] is True
    assert row["tradable"] is True
    assert row["bid"]==100.0 and row["ask"]==100.2
    assert row["spread_pct"] == pytest.approx((0.2/100.1)*100)


def test_all_gui_referenced_diagnostic_scripts_exist():
    gui=(ROOT/"gui_app.py").read_text(encoding="utf-8")
    for name in ("crypto_diagnose.py","tests_integration_v560.py","tests_intelligence.py","research_snapshot.py","news_check.py"):
        assert name in gui
        assert (ROOT/name).is_file()


def test_installer_creates_gui_desktop_and_checks_python_version():
    text=(ROOT/"Pi_Installieren.sh").read_text(encoding="utf-8")
    assert "TradingBot_GUI" in text
    assert "xdg-user-dir DESKTOP" in text
    assert "sys.version_info >= (3, 11)" in text
    assert "REQUIRED_RELEASE_FILES" in text


def test_news_status_has_no_problematic_en_dash_header():
    text=(ROOT/"news_sources_status.py").read_text(encoding="utf-8")
    assert "RESEARCH / NEWS STATUS –" not in text

def _recent_iso(stunden: int = 2) -> str:
    """Zeitstempel relativ zu JETZT.

    Ein fest verdrahtetes Datum laesst diesen Test zwangslaeufig
    veralten: _within_hours() verwirft die Meldung, sobald sie aelter
    als das Zeitfenster ist. Der Test schlug dadurch ab dem dritten Tag
    fehl, ohne dass sich am Code etwas geaendert hatte.
    """
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(hours=stunden)).isoformat().replace('+00:00', 'Z')


def test_fmp_symbol_news_uses_search_stock_news_endpoint(monkeypatch):
    import config
    from news_sources import MultiSourceNews
    monkeypatch.setattr(config,"NEWS_SOURCE_FMP_ENABLED",True,raising=False)
    monkeypatch.setattr(config, "FMP_NEWS_ENABLED", True, raising=False)
    import fmp_service
    monkeypatch.setattr(fmp_service, "settings", lambda: ("AUTO", "STARTER"))
    monkeypatch.setattr(config,"FMP_API_KEY","TOPSECRET",raising=False)
    c=MultiSourceNews()
    fake=_Session(_Resp(200,[{
        "symbol":"AAPL","publishedDate":_recent_iso(),
        "title":"Apple test headline","text":"summary","url":"https://example.com/a"
    }]))
    c.session=fake
    rows=c._fmp("AAPL",hours=48)
    assert len(rows)==1 and rows[0].source=="FMP"
    url,params=fake.calls[0]
    assert url.endswith("/stable/news/stock")
    assert params["symbols"]=="AAPL"
    assert "apikey" not in params  # Authentication uses the header, covered by transport tests.


def test_fmp_market_news_uses_stock_latest(monkeypatch):
    import config
    from news_sources import MultiSourceNews
    monkeypatch.setattr(config,"NEWS_SOURCE_FMP_ENABLED",True,raising=False)
    monkeypatch.setattr(config, "FMP_NEWS_ENABLED", True, raising=False)
    import fmp_service
    monkeypatch.setattr(fmp_service, "settings", lambda: ("AUTO", "STARTER"))
    monkeypatch.setattr(config,"FMP_API_KEY","TOPSECRET",raising=False)
    c=MultiSourceNews()
    fake=_Session(_Resp(200,[])); c.session=fake
    assert c._fmp(None,hours=48,market=True)==[]
    url,params=fake.calls[0]
    assert url.endswith("/stable/news/stock-latest")
    assert params["page"]==0


def test_news_rate_limits_get_longer_backoff(monkeypatch):
    import config
    from news_sources import MultiSourceNews
    monkeypatch.setattr(config,"NEWS_SOURCE_RATE_LIMIT_BACKOFF_SECONDS",3600,raising=False)
    monkeypatch.setattr(config,"NEWS_SOURCE_DAILY_LIMIT_BACKOFF_SECONDS",21600,raising=False)
    c=MultiSourceNews()
    # GDELT bekommt seit 6.0 eine eigene, laengere Sperre: Die kostenlose
    # Schnittstelle antwortet nach einer kurzen Pause sofort wieder mit 429.
    monkeypatch.setattr(config,"NEWS_SOURCE_GDELT_RATE_BACKOFF_SECONDS",7200,raising=False)
    assert c._failure_backoff("GDELT",RuntimeError("429 Too Many Requests"))==7200
    assert c._failure_backoff("Yahoo Finance",RuntimeError("429 Too Many Requests"))==3600
    assert c._failure_backoff("Alpha Vantage",RuntimeError("25 requests per day"))==21600


def test_news_focused_defaults_nutzen_kostenlose_quellen():
    """Fuer Einzelwerte muessen die Quellen aktiv sein, die im kostenlosen
    Tarif tatsaechlich antworten.

    Bis 5.12 war es umgekehrt: FMP war aktiv (antwortet frei mit HTTP 402
    Payment Required), Finnhub war aus (company-news ist frei nutzbar).
    """
    text=(ROOT / "config.py").read_text(encoding="utf-8")
    # Alpha Vantage bleibt aus: das 25/Tag-Budget wird fuer Quartalszahlen gebraucht.
    assert "NEWS_FOCUSED_ALPHA_VANTAGE_ENABLED = False" in text
    # FMP aus: liefert im Gratistarif 402 statt Nachrichten.
    assert "NEWS_FOCUSED_FMP_ENABLED = False" in text
    # Finnhub an: company-news ist im Gratistarif enthalten.
    assert "NEWS_FOCUSED_FINNHUB_ENABLED = True" in text
    # Die tragenden kostenlosen Quellen bleiben aktiv.
    for name in ("YAHOO", "GOOGLE_NEWS", "SEC", "NASDAQ_HALTS"):
        assert f"NEWS_FOCUSED_{name}_ENABLED = True" in text


def test_fmp_diagnostic_respects_provider_backoff():
    text=(ROOT / "news_sources.py").read_text(encoding="utf-8")
    block=text[text.index("def fmp_diagnostic"):text.index("def _fmp", text.index("def fmp_diagnostic"))]
    assert '_backoff_remaining("FMP")' in block


def test_desktop_start_stop_keep_safety_wrappers():
    start=(ROOT / "TradingBot_Starten.desktop.template").read_text(encoding="utf-8")
    stop=(ROOT / "TradingBot_Stoppen.desktop.template").read_text(encoding="utf-8")
    status=(ROOT / "TradingBot_Status.desktop.template").read_text(encoding="utf-8")
    assert "Pi_Service_Starten.sh" in start and "systemctl start" not in start
    assert "Pi_Service_Stoppen.sh" in stop and "systemctl stop" not in stop
    assert "Pi_Service_Status.sh" in status
    assert "Terminal=true" in start and "Terminal=true" in stop


def test_installer_early_check_includes_service_template():
    text=(ROOT / "Pi_Installieren.sh").read_text(encoding="utf-8")
    assert "tradingbot-pi5.service.template" in text


def test_news_check_status_survives_latin1_parent():
    env=os.environ.copy()
    env["PYTHONIOENCODING"]="latin-1"
    proc=subprocess.run(
        [sys.executable, "-c", "import news_sources; news_sources.MultiSourceNews.active_health_test=lambda self,*a,**k: {'ok':True}; import runpy; runpy.run_path('news_check.py',run_name='__main__')"], cwd=ROOT, input="6\n0\n",
        text=True, capture_output=True, env=env, timeout=10,
    )
    assert proc.returncode == 0, proc.stderr
    # Version bewusst nicht fest verdrahten -- sonst bricht der Test bei
    # jeder Umbenennung, ohne dass sich am geprueften Verhalten etwas aendert.
    assert "RESEARCH / NEWS STATUS" in proc.stdout
    assert "codec can't encode" not in proc.stdout.lower()


def test_research_snapshot_prompt_is_emitted_without_network_call():
    proc=subprocess.run(
        [sys.executable, "research_snapshot.py"], cwd=ROOT, input="\n",
        text=True, capture_output=True, timeout=10,
    )
    assert proc.returncode == 2
    assert "US-Ticker (z.B. AAPL):" in proc.stdout
    assert "Kein Symbol eingegeben." in proc.stdout


def test_telegram_report_alias_reaches_report_callback():
    from telegram_steuerung import parse_command
    from berichte import Befehlsverarbeitung

    class DummyState:
        pass

    command,args=parse_command("/report")
    assert command == "BERICHT"
    handler=Befehlsverarbeitung(DummyState(), hole_bericht=lambda: "REPORT_OK_V512")
    assert handler.ausfuehren(command, args=args) == "REPORT_OK_V512"
