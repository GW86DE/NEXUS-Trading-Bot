import pytest

import config
import profiles
import risk_profile_control
from berichte import Befehlsverarbeitung


class _State:
    def zustand(self):
        return "aktiv"


def _protect_config(monkeypatch):
    keys = {"ACTIVE_PROFILE"}
    for profile in profiles.PROFILES.values():
        keys.update(profile["werte"].keys())
    for key in keys:
        if hasattr(config, key):
            monkeypatch.setattr(config, key, getattr(config, key))


def test_activate_risk_profile_persists_atomically_and_applies_config(monkeypatch, tmp_path):
    _protect_config(monkeypatch)
    target = tmp_path / "aktives_profil.txt"
    monkeypatch.setattr(risk_profile_control, "PROFILE_FILE", target)

    result = risk_profile_control.activate_risk_profile("konservativ")

    assert result == "konservativ"
    assert target.read_text(encoding="utf-8") == "konservativ"
    assert config.ACTIVE_PROFILE == "konservativ"
    assert config.RISK_PER_TRADE_PCT == profiles.PROFILES["konservativ"]["werte"]["RISK_PER_TRADE_PCT"]


def test_invalid_risk_profile_is_rejected_before_persistence(monkeypatch, tmp_path):
    target = tmp_path / "aktives_profil.txt"
    monkeypatch.setattr(risk_profile_control, "PROFILE_FILE", target)

    with pytest.raises(KeyError):
        risk_profile_control.activate_risk_profile("ultra")

    assert not target.exists()


def test_risk1_and_risk2_still_use_immediate_profile_path():
    commands = Befehlsverarbeitung(_State())
    from unittest.mock import patch
    with patch("risk_profile_control.activate_risk_profile", return_value="konservativ") as activate:
        text = commands.ausfuehren("RISK1", quelle="telegram")
    activate.assert_called_once_with("konservativ")
    assert "KONSERVATIV" in text
