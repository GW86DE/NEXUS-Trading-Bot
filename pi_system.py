"""Leichtgewichtige Raspberry-Pi-/Linux-Systemtelemetrie ohne Zusatzpakete.

Die Funktionen lesen ausschliesslich Kernel-/proc-/sys-Informationen und sind
absichtlich best-effort: Ein fehlender Sensor darf den TradingBot nie stoppen.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").replace("\x00", "").strip()
    except Exception:
        return ""


def model_name() -> str:
    return _read_text("/proc/device-tree/model") or platform.machine()


def memory_mb() -> tuple[float, float]:
    total = available = 0.0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("MemTotal:"):
                total = float(line.split()[1]) / 1024.0
            elif line.startswith("MemAvailable:"):
                available = float(line.split()[1]) / 1024.0
    except Exception:
        __import__("logging").getLogger(__name__).debug("Best-effort-Ausnahme unterdrueckt", exc_info=True)
    return total, available


def cpu_temperature_c() -> float | None:
    candidates = [
        Path("/sys/class/thermal/thermal_zone0/temp"),
        Path("/sys/devices/virtual/thermal/thermal_zone0/temp"),
    ]
    for path in candidates:
        try:
            raw = float(path.read_text(encoding="ascii").strip())
            if raw > 1000:
                raw /= 1000.0
            if -20.0 < raw < 150.0:
                return raw
        except Exception:
            continue
    return None


def disk_free_mb(path: str | Path = ROOT) -> float:
    try:
        return shutil.disk_usage(Path(path)).free / (1024.0 * 1024.0)
    except Exception:
        return 0.0


def load_average() -> tuple[float, float, float] | None:
    try:
        a, b, c = os.getloadavg()
        return float(a), float(b), float(c)
    except Exception:
        return None



def throttled_state() -> dict:
    """Liest Raspberry-Pi-Unterspannungs-/Throttle-Status via vcgencmd.

    Fehlt vcgencmd (z.B. bei Tests oder Nicht-Pi-Linux), bleibt die Funktion
    bewusst best-effort und liefert nur ``available=False``.
    """
    out = {"available": False}
    try:
        cp = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        text = (cp.stdout or "").strip().lower()
        if cp.returncode != 0 or "=" not in text:
            return out
        raw = text.split("=", 1)[1].strip()
        value = int(raw, 16 if raw.startswith("0x") else 0)
        out = {
            "available": True,
            "raw": f"0x{value:x}",
            "undervoltage_now": bool(value & 0x1),
            "frequency_capped_now": bool(value & 0x2),
            "throttled_now": bool(value & 0x4),
            "soft_temp_limit_now": bool(value & 0x8),
            "undervoltage_occurred": bool(value & 0x10000),
            "frequency_capped_occurred": bool(value & 0x20000),
            "throttling_occurred": bool(value & 0x40000),
            "soft_temp_limit_occurred": bool(value & 0x80000),
        }
    except (FileNotFoundError, PermissionError, subprocess.SubprocessError, ValueError, OSError):
        pass
    return out


_CPU_SAMPLE = None

def system_uptime_seconds() -> float | None:
    try:
        return float(Path('/proc/uptime').read_text(encoding='utf-8').split()[0])
    except Exception:
        return None


def _cpu_ticks() -> tuple[int, int] | None:
    try:
        parts = Path('/proc/stat').read_text(encoding='utf-8').splitlines()[0].split()
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        return total, idle
    except Exception:
        return None


def cpu_usage_percent() -> float | None:
    """CPU-Auslastung seit der letzten Telemetrieabfrage, ohne Sleep."""
    global _CPU_SAMPLE
    cur = _cpu_ticks()
    if cur is None:
        return None
    old = _CPU_SAMPLE
    _CPU_SAMPLE = cur
    if old is None:
        return None
    dt = cur[0] - old[0]
    di = cur[1] - old[1]
    if dt <= 0:
        return None
    return max(0.0, min(100.0, (1.0 - di / dt) * 100.0))


def disk_stats_mb(path: str | Path = ROOT) -> tuple[float, float, float]:
    try:
        d = shutil.disk_usage(path)
        return d.total / 1024**2, d.used / 1024**2, d.free / 1024**2
    except Exception:
        return 0.0, 0.0, 0.0

def metrics(path: str | Path = ROOT) -> dict:
    total, available = memory_mb()
    temp = cpu_temperature_c()
    load = load_average()
    disk_total, disk_used, disk_free = disk_stats_mb(path)
    cpu_pct = cpu_usage_percent()
    uptime = system_uptime_seconds()
    out = {
        "platform": platform.system(),
        "architecture": platform.machine(),
        "device_model": model_name(),
        "cpu_count": os.cpu_count() or 0,
        "memory_total_mb": round(total, 1),
        "memory_available_mb": round(available, 1),
        "memory_used_mb": round(max(0.0, total - available), 1),
        "disk_total_mb": round(disk_total, 1),
        "disk_used_mb": round(disk_used, 1),
        "disk_free_mb": round(disk_free, 1),
    }
    if cpu_pct is not None:
        out["cpu_usage_pct"] = round(cpu_pct, 1)
    if uptime is not None:
        out["system_uptime_seconds"] = round(uptime, 1)
    if temp is not None:
        out["cpu_temperature_c"] = round(temp, 1)
    if load is not None:
        out["load_1m"], out["load_5m"], out["load_15m"] = [round(x, 2) for x in load]
    power = throttled_state()
    if power.get("available"):
        out["pi_throttled_hex"] = power.get("raw")
        for key in (
            "undervoltage_now", "frequency_capped_now", "throttled_now",
            "soft_temp_limit_now", "undervoltage_occurred",
            "frequency_capped_occurred", "throttling_occurred",
            "soft_temp_limit_occurred",
        ):
            out[key] = bool(power.get(key, False))
    return out


def health_flags(path: str | Path = ROOT) -> list[str]:
    """Konservative lokale Warnungen; keine Handelsentscheidung."""
    m = metrics(path)
    flags: list[str] = []
    temp = m.get("cpu_temperature_c")
    if isinstance(temp, (int, float)) and temp >= 80.0:
        flags.append(f"CPU-Temperatur hoch ({temp:.1f} C)")
    avail = float(m.get("memory_available_mb", 0.0) or 0.0)
    if avail and avail < 500.0:
        flags.append(f"wenig freier RAM ({avail:.0f} MB)")
    disk = float(m.get("disk_free_mb", 0.0) or 0.0)
    if disk and disk < 1024.0:
        flags.append(f"wenig freier Speicher ({disk:.0f} MB)")
    if m.get("undervoltage_now"):
        flags.append("AKTUELLE Unterspannung erkannt")
    elif m.get("undervoltage_occurred"):
        flags.append("Unterspannung seit Boot bereits aufgetreten")
    if m.get("throttled_now") or m.get("frequency_capped_now"):
        flags.append("CPU aktuell gedrosselt/frequenzbegrenzt")
    elif m.get("throttling_occurred") or m.get("frequency_capped_occurred"):
        flags.append("CPU-Drosselung seit Boot bereits aufgetreten")
    if m.get("soft_temp_limit_now"):
        flags.append("Soft-Temperaturlimit aktuell aktiv")
    return flags


def trading_safety_reasons(path: str | Path = ROOT) -> list[str]:
    """Pi-only Hardwaregründe, die neue Käufe vorübergehend blockieren sollen.

    Historische Throttle-Bits blockieren nicht: relevant ist nur ein aktuell
    unsicherer Zustand. Bestehende Positionen/Schutzverkäufe bleiben davon
    ausdrücklich unberührt.
    """
    m = metrics(path)
    reasons: list[str] = []
    avail = float(m.get("memory_available_mb", 0.0) or 0.0)
    disk = float(m.get("disk_free_mb", 0.0) or 0.0)
    temp = m.get("cpu_temperature_c")
    if avail and avail < 500.0:
        reasons.append(f"zu wenig freier RAM ({avail:.0f} MB)")
    if disk and disk < 1024.0:
        reasons.append(f"zu wenig freier Speicher ({disk:.0f} MB)")
    if m.get("undervoltage_now"):
        reasons.append("aktuelle Unterspannung")
    if m.get("throttled_now") or m.get("frequency_capped_now"):
        reasons.append("CPU aktuell gedrosselt/frequenzbegrenzt")
    if m.get("soft_temp_limit_now"):
        reasons.append("Temperaturlimit aktuell aktiv")
    # Fallback, falls vcgencmd nicht verfuegbar ist, der Kernel-Sensor aber schon.
    if isinstance(temp, (int, float)) and temp >= 82.0 and not any("Temperatur" in x for x in reasons):
        reasons.append(f"CPU-Temperatur kritisch ({temp:.1f} C)")
    return reasons
