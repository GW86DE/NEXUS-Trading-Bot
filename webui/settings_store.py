"""Strikt erlaubnislistenbasierte WebUI-Einstellungen; Geheimnisse nie ausgeben."""
from __future__ import annotations

import json
import ipaddress
from pathlib import Path

import sys
_WURZEL = Path(__file__).resolve().parents[1]
if str(_WURZEL) not in sys.path:
    sys.path.insert(0, str(_WURZEL))
import config       # noqa: E402  -- Standardwerte der Second Opinion

from credential_store import load_credentials, save_credentials
from safe_persistence import atomic_write_json
from safe_persistence import atomic_write_text

ROOT = Path(__file__).resolve().parents[1]


def validate_bind_host(value: str) -> str:
    """Nur Loopback oder private/VPN-IP; kein DNS-Name und keine Public-IP."""
    host = str(value or "127.0.0.1").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError(
            "Bind-Adresse muss 127.0.0.1 oder eine private WireGuard-IP sein."
        ) from None
    if address.is_unspecified or not (address.is_loopback or address.is_private):
        raise ValueError("Oeffentliche oder Wildcard-Bind-Adressen sind gesperrt.")
    return host


def _load(name: str) -> dict:
    data = load_credentials(ROOT / name, {}) or {}
    return data if isinstance(data, dict) else {}


def _secret_state(data: dict, keys: tuple[str, ...]) -> dict:
    return {f"{key}_set": bool(str(data.get(key) or "").strip()) for key in keys}


def snapshot() -> dict:
    etoro = _load("etoro_credentials.json")
    okx = _load("okx_credentials.json")
    telegram = _load("telegram_credentials.json")
    news = _load("news_sources_credentials.json")
    alpha = _load("alpha_vantage_credentials.json")
    massive = _load("massive_credentials.json")
    openai = _load("openai_ai_settings.json")
    second = _load("second_opinion_settings.json")
    handel = _load("handel_settings.json")
    router_path = ROOT / "ai_router_settings.json"
    web_path = ROOT / "web_ui_settings.json"
    try:
        router = json.loads(router_path.read_text(encoding="utf-8")) if router_path.exists() else {}
    except Exception:
        router = {}
    try:
        web = json.loads(web_path.read_text(encoding="utf-8")) if web_path.exists() else {}
    except Exception:
        web = {}
    enabled = news.get("enabled", {}) if isinstance(news.get("enabled"), dict) else {}
    try:
        etoro_mode = (ROOT / "handelsmodus.txt").read_text(encoding="utf-8").strip().lower()
    except Exception:
        etoro_mode = "paper"
    etoro_mode = "live" if etoro_mode == "live" else "demo"
    okx_mode = "live" if bool(okx.get("live_trading", False)) else "demo"
    try:
        import profiles
        profile_rows = {
            name: {
                "description": row["beschreibung"],
                "risk_per_trade_pct": row["werte"]["RISK_PER_TRADE_PCT"] * 100,
                "max_position_pct": row["werte"]["MAX_POSITION_PCT"] * 100,
                "max_open_positions": row["werte"]["MAX_OPEN_POSITIONS"],
                "daily_loss_pct": row["werte"]["MAX_DAILY_LOSS_PCT"] * 100,
            }
            for name, row in profiles.PROFILES.items()
        }
    except Exception:
        profile_rows = {}
    try:
        profile_active = (ROOT / "aktives_profil.txt").read_text(encoding="utf-8").strip().lower()
    except Exception:
        profile_active = "ausgewogen"
    # 10.6.0: Einsatzstufe je Broker. Die Uebersicht liefert ausdruecklich
    # auch die WIRKSAMEN Zahlen, nicht nur die gewaehlte Stufe -- sonst
    # koennte die Seite etwas anderes anzeigen, als der Kaufpfad rechnet.
    try:
        import risk_levels
        risk_level_rows = risk_levels.overview()
    except Exception as exc:
        risk_level_rows = {"error": f"Einsatzstufen nicht lesbar: {type(exc).__name__}"}
    from broker_live_arming import status as arm_status
    etoro_arm = arm_status("etoro", root=ROOT)
    okx_arm = arm_status("okx", root=ROOT)
    try:
        from crypto_strategy_mode import status as crypto_strategy_status
        crypto_strategy = crypto_strategy_status()
    except Exception as exc:
        crypto_strategy = {"active_mode": "CRYPTO_PAUSED", "error": str(exc)}
    try:
        from etoro_strategy_mode import status as etoro_strategy_status
        etoro_strategy = etoro_strategy_status()
    except Exception as exc:
        etoro_strategy = {"active_mode": "NEXUS_STANDARD", "error": str(exc)}
    okx_quote = "EUR"
    _stored_quotes = {str(x).upper() for x in okx.get("allowed_quote_ccy", ["EUR", "USDC"])}
    usdc_allowed = bool(okx.get("allow_usdc", "USDC" in _stored_quotes))
    # 10.1.10: USD/USDG sind nur nach ausdruecklicher Nutzerfreigabe erlaubt
    # (OKX stellt Instrumente auf USD-Quotes um; Parallelphase ab 23.09.2026).
    okx_allowed = (["EUR"] + (["USDC"] if usdc_allowed else [])
                   + [c for c in ("USD", "USDG") if c in _stored_quotes])
    return {
        "etoro": {
            **_secret_state(etoro, ("demo_api_key", "demo_user_key", "live_api_key", "live_user_key")),
            "allow_cfd": bool(etoro.get("allow_cfd", False)),
            "require_cost_quote": bool(etoro.get("require_cost_quote", True)),
            "mode": etoro_mode,
            "live_armed": etoro_arm[0], "live_arm_detail": etoro_arm[1],
        },
        "okx": {
            **_secret_state(okx, ("demo_api_key", "demo_api_secret", "demo_passphrase",
                                  "live_api_key", "live_api_secret", "live_passphrase")),
            "enabled": bool(okx.get("enabled", False)),
            "mode": okx_mode,
            "live_armed": okx_arm[0], "live_arm_detail": okx_arm[1],
            "quote_ccy": okx_quote,
            "allowed_quote_ccy": okx_allowed,
            "allow_usdc": usdc_allowed,
            "allow_usd": "USD" in okx_allowed,
            "allow_usdg": "USDG" in okx_allowed,
        },
        "telegram": {
            **_secret_state(telegram, ("bot_token",)),
            "enabled": bool(telegram.get("enabled", False)),
            "chat_id": str(telegram.get("chat_id") or ""),
            "user_id": str(telegram.get("user_id") or ""),
            "notification_mode": str(telegram.get("notification_mode") or "ON").upper(),
        },
        "news": {
            **_secret_state(news, ("finnhub_api_key", "fmp_api_key")),
            "alpha_api_key_set": bool(alpha.get("api_key")),
            "massive_api_key_set": bool(massive.get("api_key")),
            "sec_contact_email": str(news.get("sec_contact_email") or ""),
            "enabled": {**__import__("live_settings").alle_schalter(), **enabled},
            "massive_enabled": bool(massive.get("enabled", False)),
            "massive_daily_limit": int(massive.get("daily_limit", 0) or 0),
            "fmp_plan": str(news.get("fmp_plan", "AUTO")),
            "fmp_subscription": str(news.get("fmp_subscription", "FREE")),
            "fmp_daily_limit": __import__("live_settings").fmp_tageslimit(),
            "fmp_status": fmp_status(),
            "massive_status": massive_status(),
        },
        "news_rules": __import__("live_settings").news_rules(),
        "openai": {
            "api_key_set": bool(openai.get("api_key")),
            "enabled": bool(openai.get("enabled", False)),
            "model": str(openai.get("model") or "gpt-5.6-luna"),
            "research_enabled": bool(openai.get("research_enabled", False)),
            "luna_model": str(router.get("luna_model") or "gpt-5.6-luna"),
            "terra_model": str(router.get("terra_model") or "gpt-5.6-terra"),
            "max_cost_per_day_usd": float(router.get("max_cost_per_day_usd", 0.50)),
        },
        # GPT-Second-Opinion: nur beratend, ohne Wartestatus/Orderfreigabe.
        "second_opinion": {
            "mode": str(second.get("mode") or getattr(config, "AI_SECOND_OPINION_MODE", "aus")),
            "timeout_seconds": float(second.get("timeout_seconds",
                getattr(config, "AI_SECOND_OPINION_TIMEOUT_SECONDS", 8.0)) or 8.0),
            "daily_limit": int(second.get("daily_limit",
                getattr(config, "AI_SECOND_OPINION_DAILY_LIMIT", 40)) or 40),
            "cooldown_minutes": float(second.get("cooldown_minutes",
                getattr(config, "AI_SECOND_OPINION_COOLDOWN_MINUTES", 15.0)) or 15.0),
            "critical_requires_approval": bool(second.get(
                "critical_requires_approval",
                getattr(config, "AI_CRITICAL_HUMAN_GATE", True))),
        },
        # v8.1.5: Datenfrische. Am 25.08.2026 sperrte ein 5 h alter GOOGL-Kurs
        # den Einstieg -- richtig, aber die Grenze war nur ueber eine
        # Umgebungsvariable erreichbar.
        "handel": {
            "market_session_quote_max_age_seconds": float(handel.get(
                "market_session_quote_max_age_seconds",
                getattr(config, "MARKET_SESSION_QUOTE_MAX_AGE_SECONDS", 180.0)) or 180.0),
            "crypto_ticker_max_age_seconds": float(handel.get(
                "crypto_ticker_max_age_seconds",
                getattr(config, "CRYPTO_TICKER_MAX_AGE_SECONDS", 120.0)) or 120.0),
        },
        "webui": {
            "bind_host": str(web.get("bind_host") or "127.0.0.1"),
            "port": int(web.get("port", 8780) or 8780),
            "cookie_secure": bool(web.get("cookie_secure", False)),
        },
        "risk_profile": {"active": profile_active, "profiles": profile_rows},
        "risk_levels": risk_level_rows,
        "crypto_strategy": crypto_strategy,
        "etoro_strategy": etoro_strategy,
    }


def _schreibe_json(name: str, werte: dict) -> None:
    """Eine einfache Einstellungsdatei zusammenfuehren und speichern."""
    pfad = ROOT / name
    try:
        vorher = json.loads(pfad.read_text(encoding="utf-8")) if pfad.exists() else {}
    except Exception:
        vorher = {}
    vorher.update(werte)
    atomic_write_json(pfad, vorher)


def _merge_secrets(name: str, updates: dict, allowed: set[str], secret_keys: set[str]) -> None:
    current = _load(name)
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key in secret_keys and not str(value or "").strip():
            continue
        current[key] = value
    save_credentials(ROOT / name, current)


def save(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("Einstellungen muessen ein Objekt sein.")
    etoro = payload.get("etoro") or {}
    _merge_secrets("etoro_credentials.json", etoro,
                   {"demo_api_key", "demo_user_key", "live_api_key", "live_user_key",
                    "allow_cfd", "require_cost_quote"},
                   {"demo_api_key", "demo_user_key", "live_api_key", "live_user_key"})

    okx = dict(payload.get("okx") or {})
    if okx:
        okx["quote_ccy"] = "EUR"
        # 10.1.10: USD/USDG lassen sich nur mit exakter Bestaetigungsphrase
        # freigeben; die Freigabe ist eine bewusste Nutzerentscheidung und
        # wird nie automatisch gesetzt (OKX-USD-Umstellung ab 23.09.2026).
        confirm = str(okx.pop("confirm_currencies", "") or "").strip()
        flags = {}
        for key, ccy in (("allow_usdc", "USDC"), ("allow_usd", "USD"), ("allow_usdg", "USDG")):
            if key in okx:
                if not isinstance(okx[key], bool):
                    raise ValueError("Waehrungszulassungen muessen ja oder nein sein.")
                flags[ccy] = okx.pop(key)
        if flags:
            stored = _load("okx_credentials.json")
            previous = {str(x).upper() for x in stored.get("allowed_quote_ccy", ["EUR", "USDC"])}
            if stored.get("allow_usdc") is False:
                previous.discard("USDC")
            previous.add("EUR")
            quotes = ["EUR"] + [c for c in ("USDC", "USD", "USDG")
                                if flags.get(c, c in previous)]
            newly = ({"USD", "USDG"} & set(quotes)) - previous
            if newly and confirm != "WAEHRUNGEN FREIGEBEN":
                raise ValueError("Freigabe von USD/USDG erfordert die exakte Bestaetigung "
                                 "'WAEHRUNGEN FREIGEBEN'.")
            okx["allowed_quote_ccy"] = quotes
            okx["allow_usdc"] = "USDC" in quotes
    _merge_secrets("okx_credentials.json", okx,
                   {"demo_api_key", "demo_api_secret", "demo_passphrase", "live_api_key",
                    "live_api_secret", "live_passphrase", "enabled",
                    "quote_ccy", "allowed_quote_ccy", "allow_usdc"},
                   {"demo_api_key", "demo_api_secret", "demo_passphrase", "live_api_key",
                    "live_api_secret", "live_passphrase"})

    telegram = payload.get("telegram") or {}
    mode = str(telegram.get("notification_mode") or "ON").upper()
    if mode not in {"ON", "SILENT", "OFF"}:
        raise ValueError("Telegram-Modus muss ON, SILENT oder OFF sein.")
    telegram["notification_mode"] = mode
    _merge_secrets("telegram_credentials.json", telegram,
                   {"enabled", "bot_token", "chat_id", "user_id", "notification_mode"}, {"bot_token"})
    try:
        import live_settings
        live_settings.verwerfe_cache()
    except Exception as exc:
        __import__("logging").getLogger(__name__).warning(
            "Telegram-Laufzeitcache konnte nach dem Speichern nicht verworfen werden: %s", exc
        )

    news = dict(payload.get("news") or {})
    for field, allowed in (("fmp_plan", {"AUTO", "FREE", "STARTER"}), ("fmp_subscription", {"FREE", "STARTER"})):
        if field in news:
            value = str(news[field]).upper()
            if value not in allowed:
                raise ValueError("Ungueltige FMP-Tarifauswahl")
            news[field] = value
    if "fmp_daily_limit" in news:
        value = int(news["fmp_daily_limit"])
        if not 1 <= value <= 250:
            raise ValueError("FMP-Free-Tageslimit muss zwischen 1 und 250 liegen")
        news["fmp_daily_limit"] = value
    enabled = news.pop("enabled", None)
    alpha_key = news.pop("alpha_api_key", "")
    massive_key = news.pop("massive_api_key", "")
    massive_enabled = bool(news.pop("massive_enabled", False))
    massive_limit = max(0, int(news.pop("massive_daily_limit", 0) or 0))
    if enabled is not None:
        allowed_sources = {"alpha_vantage", "finnhub", "fmp", "sec_edgar",
                           "gdelt", "yahoo_finance", "google_news", "nasdaq_halts", "federal_reserve"}
        # MERGEN, nicht ersetzen. Vorher wurde die gesamte enabled-Liste durch
        # die Schalter der Seite ueberschrieben. Quellen ohne Checkbox
        # (Yahoo, Google News, Nasdaq Halts, finanzen.net) verschwanden damit
        # bei jedem Speichern still aus der Datei.
        bestand = _load("news_sources_credentials.json").get("enabled", {})
        zusammen = dict(bestand) if isinstance(bestand, dict) else {}
        zusammen.update({k: bool(v) for k, v in dict(enabled).items() if k in allowed_sources})
        # Aus Vorgängerversionen übernommene finanzen.net-Schalter bereinigen:
        # Die Quelle ist in 8.2 absichtlich aus dem automatischen Abruf raus.
        zusammen.pop("finanzen_net", None)
        news["enabled"] = zusammen
    _merge_secrets("news_sources_credentials.json", news,
                   {"finnhub_api_key", "fmp_api_key", "sec_contact_email", "enabled",
                    "fmp_daily_limit", "fmp_plan", "fmp_subscription"},
                   {"finnhub_api_key", "fmp_api_key"})
    # Aenderungen sollen sofort wirken, nicht erst nach einem Neustart.
    try:
        import live_settings
        live_settings.verwerfe_cache()
        import fmp_reference
        fmp_reference.neu_laden()
    except Exception:
        __import__("logging").getLogger(__name__).warning(
            "Zwischenspeicher der Quelleneinstellungen nicht verworfen; "
            "die Aenderung wirkt spaetestens nach wenigen Sekunden.", exc_info=True)
    _merge_secrets("alpha_vantage_credentials.json", {"api_key": alpha_key}, {"api_key"}, {"api_key"})
    _merge_secrets("massive_credentials.json",
                   {"api_key": massive_key, "enabled": massive_enabled, "daily_limit": massive_limit},
                   {"api_key", "enabled", "daily_limit"}, {"api_key"})

    # Second Opinion. Sofort wirksam, ohne Neustart und rein beratend.
    second = payload.get("second_opinion") or {}
    if second:
        erlaubt = {"mode", "timeout_seconds", "daily_limit", "cooldown_minutes",
                   "critical_requires_approval"}
        sauber = {k: v for k, v in second.items() if k in erlaubt}
        if "mode" in sauber:
            wert = str(sauber["mode"]).strip().lower()
            sauber["mode"] = wert if wert in ("aus", "nur_live", "immer") else "aus"
        if "critical_requires_approval" in sauber:
            sauber["critical_requires_approval"] = bool(sauber["critical_requires_approval"])
        if sauber:
            _schreibe_json("second_opinion_settings.json", sauber)

    # v8.1.5: Datenfrische-Schwellen. Bewusst eng begrenzt und geklammert --
    # eine zu kleine Grenze wuerde jeden Einstieg sperren, eine zu grosse
    # wuerde auf veralteten Kursen handeln.
    handel = payload.get("handel") or {}
    if handel:
        sauber = {}
        for schluessel, unten, oben in (
                ("market_session_quote_max_age_seconds", 30.0, 86400.0),
                ("crypto_ticker_max_age_seconds", 15.0, 3600.0)):
            if schluessel in handel:
                try:
                    wert = float(handel[schluessel])
                except (TypeError, ValueError):
                    raise ValueError(f"{schluessel} muss eine Zahl in Sekunden sein.")
                if not (unten <= wert <= oben):
                    raise ValueError(
                        f"{schluessel} muss zwischen {unten:.0f} und {oben:.0f} "
                        "Sekunden liegen.")
                sauber[schluessel] = wert
        if sauber:
            _schreibe_json("handel_settings.json", sauber)
            try:
                import live_settings
                live_settings.verwerfe_cache()
            except Exception:
                __import__("logging").getLogger(__name__).warning(
                    "Zwischenspeicher nicht verworfen; die Aenderung wirkt "
                    "spaetestens nach wenigen Sekunden.", exc_info=True)

    openai = payload.get("openai") or {}
    _merge_secrets("openai_ai_settings.json", openai,
                   {"api_key", "enabled", "model", "research_enabled"}, {"api_key"})
    router = {
        key: openai[key] for key in ("luna_model", "terra_model", "max_cost_per_day_usd")
        if key in openai
    }
    if "enabled" in openai:
        router["enabled"] = bool(openai["enabled"])
    if router:
        path = ROOT / "ai_router_settings.json"
        try:
            current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except Exception:
            current = {}
        current.update(router)
        atomic_write_json(path, current)

    web = payload.get("webui") or {}
    if web:
        host = validate_bind_host(web.get("bind_host") or "127.0.0.1")
        port = max(1024, min(65535, int(web.get("port", 8780))))
        atomic_write_json(ROOT / "web_ui_settings.json", {
            "bind_host": host, "port": port,
            "cookie_secure": bool(web.get("cookie_secure", False)),
        })
    if "news_rules" in payload:
        from live_settings import NEWS_RULE_DEFAULTS
        values = payload["news_rules"]
        clean = {}
        for name, value in values.items():
            if name not in NEWS_RULE_DEFAULTS or isinstance(value, bool) or float(value) != int(value) or not 1 <= int(value) <= 100:
                raise ValueError('News-Schwellen muessen ganze Zahlen zwischen 1 und 100 sein')
            clean[name] = int(value)
        atomic_write_json(ROOT / 'news_rules_settings.json', clean)
        __import__('live_settings').verwerfe_cache()
    return snapshot()


def set_telegram_runtime(enabled: bool) -> dict:
    """Schaltet Versand und Fernsteuerung atomisch ohne Core-Neustart."""
    data = _load("telegram_credentials.json")
    if bool(enabled):
        if not str(data.get("bot_token") or "").strip():
            raise ValueError("Telegram kann ohne gespeicherten Bot-Token nicht aktiviert werden.")
        if not str(data.get("chat_id") or "").strip():
            raise ValueError("Telegram kann ohne gespeicherte Chat-ID nicht aktiviert werden.")
    data["enabled"] = bool(enabled)
    save_credentials(ROOT / "telegram_credentials.json", data)
    try:
        import live_settings
        live_settings.verwerfe_cache()
    except Exception as exc:
        __import__("logging").getLogger(__name__).warning(
            "Telegram-Laufzeitcache konnte nach dem Umschalten nicht verworfen werden: %s", exc
        )
    return snapshot()


def set_broker_mode(broker: str, mode: str, confirm: str = "") -> dict:
    """Waehlt Demo/LIVE, disarmt aber immer alle Neueinstiege."""
    name = str(broker or "").strip().lower()
    selected = str(mode or "").strip().lower()
    if name not in {"etoro", "okx"} or selected not in {"demo", "live"}:
        raise ValueError("Unbekannter Broker oder Modus.")
    if selected == "live" and str(confirm or "") != f"{name.upper()} LIVE AUSWAHL":
        raise ValueError(f"Bestaetigung '{name.upper()} LIVE AUSWAHL' erforderlich.")
    if selected == "live":
        credentials = _load(f"{name}_credentials.json")
        required = (("live_api_key", "live_user_key") if name == "etoro" else
                    ("live_api_key", "live_api_secret", "live_passphrase"))
        missing = [key for key in required if not str(credentials.get(key) or "").strip()]
        if missing:
            raise ValueError(
                f"{name.upper()} LIVE kann erst nach Speichern aller LIVE-Zugangsdaten "
                f"ausgewaehlt werden ({', '.join(missing)} fehlt)."
            )
    from broker_live_arming import disarm
    disarm(name, root=ROOT)
    # Alte gemeinsame eToro-Freigabe ebenfalls entfernen.
    try:
        (ROOT / "live_trading_arm.json").unlink(missing_ok=True)
    except OSError:
        pass
    if name == "etoro":
        atomic_write_text(ROOT / "handelsmodus.txt", "live\n" if selected == "live" else "paper\n")
    else:
        data = _load("okx_credentials.json")
        data["live_trading"] = selected == "live"
        save_credentials(ROOT / "okx_credentials.json", data)
    return snapshot()


def set_risk_profile(name: str, confirm: str = "") -> dict:
    selected = str(name or "").strip().lower()
    if selected == "offensiv" and str(confirm or "") != "OFFENSIV AKTIVIEREN":
        raise ValueError("Bestaetigung 'OFFENSIV AKTIVIEREN' erforderlich.")
    from risk_profile_control import activate_risk_profile
    activate_risk_profile(selected)
    return snapshot()


def set_risk_level(broker: str, level: str, confirm: str = "") -> dict:
    """Einsatzstufe je Broker setzen (10.6.0); wirkt ohne Neustart.

    Die hoechste Stufe braucht dieselbe ausdrueckliche Bestaetigung wie das
    Profil OFFENSIV: Sie vervielfacht den Einsatz je Trade, und ein
    verrutschter Klick darf das nicht erledigen.
    """
    import risk_levels
    key = risk_levels.normalise_broker(broker)
    selected = str(level or "").strip().lower()
    if selected not in risk_levels.VALID_LEVELS:
        raise ValueError(f"Unbekannte Einsatzstufe: {level!r}")
    if selected == risk_levels.ERHOEHT and str(confirm or "") != "EINSATZ ERHOEHEN":
        raise ValueError("Bestaetigung 'EINSATZ ERHOEHEN' erforderlich.")
    risk_levels.set_level(key, selected, source="webui",
                          reason="WebUI-Laufzeitwechsel", notify=True)
    return snapshot()


def set_crypto_strategy_mode(mode: str, *, source: str, confirm: str = "",
                             reason: str = "") -> dict:
    from crypto_strategy_mode import (
        CRYPTO_PAUSED, FREQTRADE_SAMPLE, NEXUS_STANDARD, ZUSATZ_MODES, set_mode,
    )
    aliases = {
        "standard": NEXUS_STANDARD, "nexus_standard": NEXUS_STANDARD,
        "freqtrade": FREQTRADE_SAMPLE, "freqtrade_sample": FREQTRADE_SAMPLE,
        "paused": CRYPTO_PAUSED, "pause": CRYPTO_PAUSED,
        "crypto_paused": CRYPTO_PAUSED,
    }
    selected = aliases.get(str(mode or "").strip().lower(), str(mode or "").strip().upper())
    if selected == FREQTRADE_SAMPLE and str(confirm or "") != "FREQTRADE AKTIVIEREN":
        raise ValueError("Bestaetigung 'FREQTRADE AKTIVIEREN' erforderlich.")
    # 10.2.0: Eine Zusatzstrategie ist eine bewusste Entscheidung wie der
    # Freqtrade-Modus und braucht dieselbe Art ausdruecklicher Bestaetigung.
    if selected in ZUSATZ_MODES and str(confirm or "") != "STRATEGIE AKTIVIEREN":
        raise ValueError("Bestaetigung 'STRATEGIE AKTIVIEREN' erforderlich.")
    set_mode(selected, source=source, reason=reason, notify=True)
    return snapshot()


def set_etoro_strategy_mode(mode: str, *, source: str, confirm: str = "",
                            reason: str = "") -> dict:
    """Aktien-Einstiegsstrategie (10.2.0); wirkt nur auf neue eToro-Einstiege."""
    from etoro_strategy_mode import NEXUS_STANDARD, ZUSATZ_MODES, set_mode
    aliases = {"standard": NEXUS_STANDARD, "nexus_standard": NEXUS_STANDARD}
    selected = aliases.get(str(mode or "").strip().lower(), str(mode or "").strip().upper())
    if selected in ZUSATZ_MODES and str(confirm or "") != "STRATEGIE AKTIVIEREN":
        raise ValueError("Bestaetigung 'STRATEGIE AKTIVIEREN' erforderlich.")
    set_mode(selected, source=source, reason=reason, notify=True)
    return snapshot()


def fmp_status():
    """Passive local status; viewing settings never calls a provider."""
    try:
        import fmp_reference
        client = fmp_reference.client()
        from webui.display_evidence import saved_fmp_packet_usage
        from pulsar.research import path as research_path
        return {**(client.status() if client.konfiguriert else {"konfiguriert": False}),
                "packet_usage": saved_fmp_packet_usage(research_path())}
    except Exception as exc:
        return {"ok": False, "detail": "FMP-Status nicht lesbar (" + type(exc).__name__ + ")"}


def massive_status():
    """Read a budget observation without creating SQLite files or API calls."""
    try:
        from massive_service import readonly_status
        import live_settings
        return readonly_status(api_key=live_settings.massive_key())
    except Exception as exc:
        return {"state": "unknown", "detail": "Massive-Status nicht lesbar (" + type(exc).__name__ + ")"}
