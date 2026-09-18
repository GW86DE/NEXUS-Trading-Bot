"""Raspberry-Pi-5-Preflight fuer den TradingBot-Dauerbetrieb."""
from __future__ import annotations

import argparse
import importlib
import os
import platform
import sys
from pathlib import Path

from pi_system import metrics, health_flags

ROOT = Path(__file__).resolve().parent
REQUIRED_IMPORTS = (
    "numpy", "pandas", "sklearn", "joblib", "requests", "yfinance",
    "fastapi", "uvicorn", "websocket",
)


def checks(strict_pi: bool = False, check_imports: bool = True) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    m = metrics(ROOT)
    arch = str(m.get("architecture", "")).lower()
    model = str(m.get("device_model", ""))

    if strict_pi:
        if platform.system().lower() != "linux":
            errors.append("Pi-Service ist fuer Linux/Raspberry Pi OS gedacht.")
        if arch not in {"aarch64", "arm64"}:
            errors.append(f"64-Bit ARM erforderlich; erkannt: {arch or 'unbekannt'}")
        if "raspberry pi 5" not in model.lower():
            warnings.append(f"Ziel ist Raspberry Pi 5; erkannt: {model or 'unbekannt'}")

    if sys.version_info < (3, 11):
        errors.append(f"Python >= 3.11 erforderlich; erkannt: {platform.python_version()}")
    if sys.maxsize <= 2**32:
        errors.append("32-Bit Python erkannt. Fuer Pi 5 / 8 GB bitte 64-Bit Raspberry Pi OS verwenden.")

    total = float(m.get("memory_total_mb", 0.0) or 0.0)
    avail = float(m.get("memory_available_mb", 0.0) or 0.0)
    disk = float(m.get("disk_free_mb", 0.0) or 0.0)
    if strict_pi and total and total < 6500:
        warnings.append(f"Weniger als ~8 GB RAM erkannt ({total:.0f} MB).")
    if avail and avail < 500:
        errors.append(f"Zu wenig verfuegbarer RAM fuer sicheren Start: {avail:.0f} MB")
    if disk and disk < 1024:
        errors.append(f"Weniger als 1 GB freier Speicher: {disk:.0f} MB")

    # Unterspannung/aktuelle Drosselung ist fuer einen unbeaufsichtigten
    # Trading-Dienst ein Hardware-Stabilitaetsproblem, kein kosmetischer Wert.
    if m.get("undervoltage_now"):
        errors.append("Raspberry Pi meldet aktuelle Unterspannung. Netzteil/Kabel pruefen.")
    if m.get("throttled_now") or m.get("frequency_capped_now"):
        errors.append("Raspberry Pi ist beim Start aktuell gedrosselt/frequenzbegrenzt. Kuehlung/Netzteil pruefen.")
    if m.get("soft_temp_limit_now"):
        errors.append("Raspberry Pi meldet beim Start ein aktives Temperatur-Limit. Kuehlung pruefen.")

    if check_imports:
        missing = []
        for name in REQUIRED_IMPORTS:
            try:
                importlib.import_module(name)
            except Exception as exc:
                missing.append(f"{name} ({type(exc).__name__}: {exc})")
        if missing:
            errors.append("Python-Abhaengigkeiten fehlen/fehlerhaft: " + "; ".join(missing))

    # Zugangsdaten auf Linux sollen nie gruppen-/weltlesbar sein.
    for pattern in ("*_credentials.json", "openai_ai_settings.json"):
        for path in ROOT.glob(pattern):
            try:
                mode = path.stat().st_mode & 0o777
                if mode & 0o077:
                    warnings.append(f"Berechtigungen zu offen: {path.name} ({oct(mode)}), empfohlen 0o600")
            except Exception:
                __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)

    warnings.extend(x for x in health_flags(ROOT) if x not in warnings)
    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", action="store_true", help="strikte Pi-5/64-Bit-Pruefung")
    ap.add_argument("--no-imports", action="store_true")
    args = ap.parse_args()

    m = metrics(ROOT)
    print("=== TradingBot Raspberry-Pi Preflight ===")
    print(f"Modell      : {m.get('device_model')}")
    print(f"Architektur : {m.get('architecture')}")
    print(f"CPU-Kerne   : {m.get('cpu_count')}")
    print(f"Python      : {platform.python_version()} ({'64' if sys.maxsize > 2**32 else '32'} Bit)")
    print(f"RAM         : {m.get('memory_total_mb',0):.0f} MB gesamt / {m.get('memory_available_mb',0):.0f} MB frei")
    print(f"Speicher    : {m.get('disk_free_mb',0)/1024.0:.1f} GB frei")
    if m.get("cpu_temperature_c") is not None:
        print(f"CPU-Temp.   : {m.get('cpu_temperature_c'):.1f} C")
    if m.get("pi_throttled_hex") is not None:
        print(f"Power/Throttle: {m.get('pi_throttled_hex')} | Unterspannung={bool(m.get('undervoltage_now'))} | Drosselung={bool(m.get('throttled_now') or m.get('frequency_capped_now'))}")

    errors, warnings = checks(strict_pi=args.service, check_imports=not args.no_imports)
    for w in warnings:
        print("WARNUNG:", w)
    for e in errors:
        print("FEHLER :", e)
    if errors:
        print("PREFLIGHT FEHLGESCHLAGEN")
        return 78
    print("PREFLIGHT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
