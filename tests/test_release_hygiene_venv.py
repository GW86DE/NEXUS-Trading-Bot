from pathlib import Path

import volltest


def test_static_hygiene_ignores_virtualenv_foreign_code(tmp_path, monkeypatch):
    """A Pi-created .venv is installation output, not TradingBot release code."""
    monkeypatch.setattr(volltest, "ROOT", tmp_path)

    foreign = tmp_path / ".venv" / "lib" / "python3.13" / "site-packages" / "foreign_pkg"
    foreign.mkdir(parents=True)
    # Deliberately violates both hygiene rules, but only inside .venv.
    (foreign / "bad.py").write_text(
        "def x():\n"
        "    try:\n"
        "        return 1\n"
        "    except Exception:\n"
        "        pass\n"
        "# " + "al" + "paca" + "\n",
        encoding="utf-8",
    )

    code, issues = volltest._static_hygiene()
    assert code == 0
    assert issues == []


def test_static_hygiene_still_checks_own_source(tmp_path, monkeypatch):
    """The .venv exclusion must not weaken checks of TradingBot-owned files."""
    monkeypatch.setattr(volltest, "ROOT", tmp_path)
    (tmp_path / "own_bad.py").write_text(
        "def x():\n"
        "    try:\n"
        "        return 1\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )

    code, issues = volltest._static_hygiene()
    assert code == 1
    assert any("stummer Exception-Pfad" in issue for issue in issues)


def test_static_hygiene_rejects_runtime_state_in_release(tmp_path, monkeypatch):
    monkeypatch.setattr(volltest, "ROOT", tmp_path)
    (tmp_path / "web_ui_settings.json").write_text("{}", encoding="utf-8")
    monkeypatch.delenv("TRADINGBOT_ALLOW_LOCAL_STATE", raising=False)

    code, issues = volltest._static_hygiene()

    assert code == 1
    assert any("web_ui_settings.json" in issue for issue in issues)


def test_static_hygiene_allows_state_only_for_installed_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(volltest, "ROOT", tmp_path)
    (tmp_path / "web_ui_settings.json").write_text("{}", encoding="utf-8")
    (tmp_path / "web_ui_credentials.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("TRADINGBOT_ALLOW_LOCAL_STATE", "1")

    code, issues = volltest._static_hygiene()

    assert code == 0
    assert issues == []
