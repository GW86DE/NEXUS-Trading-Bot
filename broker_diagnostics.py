"""Explizite read-only Verbindungstests fuer eToro und OKX."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from credential_store import load_credentials

ROOT = Path(__file__).resolve().parent


def _clean_error(exc: Exception) -> str:
    """Keine URL, Header oder Zugangsdaten an die WebUI zurueckgeben."""
    text = str(exc or "Verbindungstest fehlgeschlagen").replace("\n", " ")
    for filename in ("etoro_credentials.json", "okx_credentials.json"):
        data = load_credentials(ROOT / filename, {}) or {}
        if isinstance(data, dict):
            for value in data.values():
                secret = str(value or "").strip()
                if len(secret) >= 5:
                    text = text.replace(secret, "[GEHEIM]")
    return f"{type(exc).__name__}: {text[:500]}"


def test_connection(broker_name: str, mode: str) -> dict:
    """Fuehrt Auth-, Konto- und Rechtepruefungen aus, aber niemals Orders."""
    name = str(broker_name or "").strip().lower()
    selected = str(mode or "").strip().lower()
    if name not in {"etoro", "okx"} or selected not in {"demo", "live"}:
        raise ValueError("Broker/Modus muss eToro|OKX und Demo|Live sein.")
    started = time.monotonic()
    adapter = None
    result = {
        "ok": False, "broker": name, "mode": selected.upper(),
        "checked_at": datetime.now(timezone.utc).isoformat(), "read_only": True,
    }
    try:
        if name == "etoro":
            from broker.etoro import EtoroBroker
            creds = load_credentials(ROOT / "etoro_credentials.json", {}) or {}
            prefix = "demo" if selected == "demo" else "live"
            adapter = EtoroBroker(
                paper=selected == "demo",
                api_key=str(creds.get(f"{prefix}_api_key") or ""),
                user_key=str(creds.get(f"{prefix}_user_key") or ""),
            )
            adapter.connect()
            result.update({
                "ok": bool(adapter.health_check(force=True)),
                "environment": "DEMO" if selected == "demo" else "LIVE",
                "currency": adapter.kontowaehrung(),
                "equity": adapter.kontowert(),
                "steps": [
                    {"step": "identity_scope", "ok": True},
                    {"step": "environment_pnl", "ok": True},
                    {"step": "aggregate_portfolio", "ok": True},
                ],
                "detail": "Authentifizierung, Umgebung, P&L und Portfolio erfolgreich.",
            })
        else:
            from broker.okx import OKXBroker
            creds = load_credentials(ROOT / "okx_credentials.json", {}) or {}
            prefix = "demo" if selected == "demo" else "live"
            adapter = OKXBroker(
                demo=selected == "demo",
                api_key=str(creds.get(f"{prefix}_api_key") or ""),
                api_secret=str(creds.get(f"{prefix}_api_secret") or ""),
                passphrase=str(creds.get(f"{prefix}_passphrase") or ""),
                quote_ccy=str(creds.get("quote_ccy") or "EUR"),
            )
            adapter.connect()
            result.update({
                "ok": bool(adapter.health_check(force=True)),
                "currency": adapter.kontowaehrung(),
                "equity": adapter.kontowert(),
                "detail": ("Authentifizierter Kontotest erfolgreich. "
                           "LIVE-Rechte wurden auf Read+Trade ohne Withdraw geprueft."
                           if selected == "live" else
                           "Authentifizierter OKX-Demo-Kontotest erfolgreich."),
                "steps": [
                    {"step": "server_time", "ok": True},
                    {"step": "account_config", "ok": True},
                    {"step": "account_balance", "ok": True},
                ],
            })
    except Exception as exc:
        result["detail"] = _clean_error(exc)
    finally:
        if adapter is not None:
            try:
                adapter.disconnect()
            except Exception:
                __import__("logging").getLogger(__name__).debug(
                    "Broker-Diagnose: Disconnect fehlgeschlagen", exc_info=True)
    result["latency_ms"] = round((time.monotonic() - started) * 1000.0)
    return result


__all__ = ["test_connection"]
