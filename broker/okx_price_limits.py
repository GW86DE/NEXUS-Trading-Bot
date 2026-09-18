"""OKX price bands narrow NEXUS limits; they never widen the risk budget.

Public price-limit data belongs to the same DEMO/LIVE domain as the order
book. No network fallback, cached band or number parsed from an error message
may substitute for a current, instrument-bound response.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR

from .base import BrokerFehler


class OKXPriceBandError(BrokerFehler):
    """No primary order was sent, or OKX explicitly rejected its placement."""

    def __init__(self, message, *, code=""):
        super().__init__(message)
        self.okx_code = str(code)


def positive(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise BrokerFehler("OKX-Preisgrenze: ungueltige Zahl; keine Order gesendet.") from None
    if not number.is_finite() or number <= 0:
        raise BrokerFehler("OKX-Preisgrenze: positive endliche Zahl erforderlich; keine Order gesendet.")
    return number


def fresh(timestamp_ms, now_ms, max_age_seconds, label):
    stamp = positive(timestamp_ms)
    age = (Decimal(str(now_ms)) - stamp) / 1000
    if age < -5 or age > positive(max_age_seconds):
        raise BrokerFehler(f"OKX: {label} nicht frisch genug ({age:.2f} s); keine Order gesendet.")


@dataclass(frozen=True)
class PriceBand:
    instrument: str
    enabled: bool
    timestamp_ms: str
    buy: Decimal | None
    sell: Decimal | None

    @classmethod
    def parse(cls, rows, instrument, *, now_ms, max_age_seconds):
        if (not isinstance(rows, list) or len(rows) != 1
                or not isinstance(rows[0], dict)):
            raise BrokerFehler("OKX-Preisgrenze fehlt oder ist mehrdeutig; keine Order gesendet.")
        row = rows[0]
        if row.get("instId") != instrument or type(row.get("enabled")) is not bool:
            raise BrokerFehler("OKX-Preisgrenze: Instrument/Status widerspruechlich; keine Order gesendet.")
        fresh(row.get("ts"), now_ms, max_age_seconds, "Preisgrenze")
        enabled = row["enabled"]
        return cls(instrument, enabled, str(row["ts"]),
                   positive(row.get("buyLmt")) if enabled else None,
                   positive(row.get("sellLmt")) if enabled else None)

    def evidence(self, environment):
        return dict(instId=self.instrument, enabled=self.enabled, ts=self.timestamp_ms,
                    buyLmt=str(self.buy) if self.buy is not None else "",
                    sellLmt=str(self.sell) if self.sell is not None else "",
                    environment=environment, source="/api/v5/public/price-limit")


def bounded_limit(*, side, requested, tick, own_limit, best, worst, band):
    tick = positive(tick)
    requested, own_limit, best, worst = map(positive, (requested, own_limit, best, worst))

    def floor(value):
        return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick

    def ceil(value):
        return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick

    if side == "sell":
        price = max(floor(requested), ceil(own_limit), ceil(band.sell) if band.enabled else Decimal(0))
        # IOC may fill less than planned, but at least the best bid must be
        # reachable. Exact terminal fills, not the quote, drive accounting.
        executable = price <= best
    elif side == "buy":
        price = min(ceil(requested), floor(own_limit), floor(band.buy) if band.enabled else ceil(requested))
        executable = price >= worst  # FOK entry still requires full depth.
    else:
        raise BrokerFehler("OKX-Preisgrenze: ungueltige Orderseite.")
    if price <= 0 or not executable:
        raise OKXPriceBandError(
            f"{band.instrument}: OKX-Preisgrenze und ausfuehrbares Orderbuch haben "
            f"keinen passenden Preis-Tick ({side}, Limit {price}); keine Order gesendet.")
    return price
