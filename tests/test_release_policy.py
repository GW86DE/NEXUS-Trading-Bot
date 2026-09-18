from pathlib import Path
import config

ROOT = Path(__file__).resolve().parents[1]


def test_broker_package_kennt_genau_zwei_adapter():
    """v7 handelt mit zwei Brokern -- und ausdruecklich nur mit diesen zwei.

    Der Test ist die Gegenprobe zur v6-Regel 'nur eToro'. Ein dritter
    Adapter darf nicht unbemerkt hinzukommen, weil jeder Broker eigene
    Sicherheitsannahmen (Stop-Faehigkeit, Kontotrennung, Identitaet) hat.
    """
    py_files = {p.name for p in (ROOT / "broker").glob("*.py")}
    assert py_files == {
        "__init__.py", "base.py", "connectivity.py", "history_safety.py",
        "price_precision.py", "etoro.py", "etoro_stream.py", "okx.py",
        "okx_stream.py", "multi.py", "okx_price_limits.py",
    }
    assert config.BROKER == "etoro", "Aktienbroker bleibt eToro"

    import broker
    assert broker.BEKANNTE_BROKER == ("etoro", "okx")
    assert broker.broker_fuer_asset("stock") == "etoro"
    assert broker.broker_fuer_asset("crypto") == "okx"


def test_unbekannter_broker_ist_ein_harter_fehler():
    import broker
    try:
        broker.get_broker("binance")
    except broker.BrokerFehler as exc:
        assert "binance" in str(exc)
    else:
        raise AssertionError("Ein unbekannter Broker muss abgelehnt werden")


def test_okx_handelt_ausschliesslich_spot():
    """Margin/Futures koennen ueber den Einsatz hinaus verlieren.

    Der Bot kann so etwas strukturell nicht absichern, deshalb darf im
    Orderpfad nur der Cash-Modus vorkommen.
    """
    quelle = (ROOT / "broker" / "okx.py").read_text(encoding="utf-8")
    assert '"tdMode": "cash"' in quelle
    for verboten in ("isolated", "cross", "SWAP", "FUTURES", "OPTION"):
        assert f'"{verboten}"' not in quelle


def test_runtime_dependencies_are_minimal_and_reproducible():
    expected = {
        "pandas", "numpy", "scikit-learn", "joblib", "yfinance", "requests",
        "fastapi", "uvicorn", "python-multipart", "websocket-client",
    }
    for filename in ("requirements.txt", "requirements-lock.txt"):
        names = {
            line.split("==", 1)[0].strip().lower()
            for line in (ROOT / filename).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        assert names == expected



def test_pi_installer_installs_pinned_pytest_before_volltest():
    test_req = (ROOT / "requirements-test.txt").read_text(encoding="utf-8").splitlines()
    deps = [line.strip() for line in test_req if line.strip() and not line.lstrip().startswith("#")]
    assert set(deps) == {"pytest==9.0.2", "httpx==0.28.1"}
    assert len(deps) == 2
    installer = (ROOT / "Pi_Installieren.sh").read_text(encoding="utf-8")
    install_pos = installer.index("-r requirements-test.txt")
    test_pos = installer.index("$PY\" volltest.py") if "$PY\" volltest.py" in installer else installer.index('"$PY" volltest.py')
    assert install_pos < test_pos


def test_live_trader_uses_candidate_decision_contract():
    source = (ROOT / "live_trader.py").read_text(encoding="utf-8")
    assert "final_gate.approved" in source
    assert "final_gate.allowed" not in source


def test_v511_human_gate_and_strategy_analyst_have_no_order_rights():
    # Bewusst KEINE feste Versionsnummer mehr pruefen: Der Test soll die
    # Sicherheitseigenschaft absichern, nicht bei jeder Umbenennung brechen.
    version=(ROOT/"VERSION.txt").read_text(encoding="utf-8").strip()
    assert version, "VERSION.txt darf nicht leer sein"
    for filename in ("universe_research.py","strategy_analyst.py","universe_proposals.py"):
        source=(ROOT/filename).read_text(encoding="utf-8")
        assert "kaufe_mit_absicherung" not in source
        assert "submit_protected_buy" not in source
    research=(ROOT/"universe_research.py").read_text(encoding="utf-8")
    assert "add_approved_stock" not in research


def test_pi_installer_leaves_new_service_stopped_until_human_activation():
    installer = (ROOT / "Pi_Installieren.sh").read_text(encoding="utf-8")
    # A previously installed TradingBot service is stopped/disabled during the
    # handover, and the new unit must not be enabled or started by the installer.
    disable_pos = installer.index('systemctl disable --now "$SERVICE_NAME"')
    test_pos = installer.index('"$PY" volltest.py')
    unit_install_pos = installer.index('sudo install -m 0644 "$SERVICE_TMP"')
    assert test_pos < disable_pos < unit_install_pos
    assert 'systemctl enable "$SERVICE_NAME"' not in installer
    assert 'systemctl restart "$SERVICE_NAME"' not in installer
    assert 'BOT NOCH NICHT GESTARTET' in installer
    assert './Pi_GUI_Starten.sh' in installer
    assert './Pi_Service_Aktivieren.sh' in installer


def test_explicit_service_activation_remains_separate_human_step():
    activation = (ROOT / "Pi_Service_Aktivieren.sh").read_text(encoding="utf-8")
    assert "systemctl enable --now tradingbot-pi5.service" in activation
