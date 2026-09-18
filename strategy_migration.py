"""Explizite Migration offener Freqtrade-Positionen auf eine neue Version.

WARUM ES DIESES MODUL GIBT
==========================
Am 31.08.2026 hat ein Update BNB, LINK und XLM aus den offenen Botpositionen
entfernt. In v9.1 wurde ``STARTUP_CANDLES`` von 200 auf 30 gesetzt -- ein Wert,
der in den Strategie-Hash einging. Der gespeicherte Hash der offenen Positionen
passte damit nicht mehr, die Positionen wurden pausiert, und weil der
Eigentumsnachweis am Verwaltungsmodus hing, erschienen sie anschliessend als
"Externer Bestand · Lokaler Eintrag ohne identische ordId/clOrdId/tradeId-Kette".
Die ID-Kette war die ganze Zeit vollstaendig vorhanden.

DIE REGEL
=========
    In den Eigentumsnachweis darf nichts eingehen, was ein Software-Update
    aendern kann.

Der Nachweis besteht aus dem, was der Broker vergeben hat: orderId, clOrdId,
tradeId, Kontofingerprint, Instrument. Eine Strategieaenderung darf hoechstens
bestimmen, WELCHE Logik verwalten darf -- niemals, OB die Position dem Bot
gehoert.

WAS DIESE MIGRATION TUT -- UND WAS NICHT
========================================
Sie fasst AUSSCHLIESSLICH Strategiefelder an. Sie storniert keine Schutzorder,
legt keine an, sendet keine Order und veraendert keine Menge. Damit sind die
gefaehrlichen Faelle strukturell ausgeschlossen statt durch Sonderlogik
abgesichert.

Den gespeicherten Hash einfach zu ueberschreiben waere die naheliegende und die
falsche Loesung: sie wuerde eine geaenderte Strategie als die urspruengliche
Einstiegsstrategie ausgeben. Deshalb wird jede Migration mit Herkunft,
Zeitpunkt und Brokerbeweis protokolliert und ist im Nachhinein nachvollziehbar.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# Strategieidentitaeten, die semantisch auf die aktuelle Version migriert
# werden duerfen. Der Schluessel ist (Version, Hash) des EINSTIEGS.
#
# 9.9.0 aendert bewusst Warmup, ROI-Grenze und Ausstiegsprioritaet.
# Nur bekannte historische Hashes sind migrierbar; der Wechsel wird mit
# alter und neuer Strategieidentitaet protokolliert. Es ist keine Behauptung
# unveraenderter Handelsentscheidungen. Unbekannte Hashes bleiben gesperrt.
MIGRIERBAR = {
    ("NEXUS-FT-SAMPLE-V2", "a1d6d20d0c890539eef7f91ee97645f803e7341e9e2f778fdc31fa3ecce21581"):
        "9.9.0: offizielle SampleStrategy mit 200 Startkerzen, Signal vor Stop/ROI "
        "und strikter ROI-Schwelle; Strategieaenderung ausdruecklich protokolliert",
    ("NEXUS-FT-SAMPLE-V1",
     "c8c1883383675aafd52da451a1d4cdcf924677be3da53ee66c958f11821f3f37"):
        "V1 (9.0.15): identische Handelsbedingungen, geaenderte "
        "Ausstiegsreihenfolge und kerzenunabhaengiger Stop/ROI",
    # Zwischenstand aus 9.1: derselbe Name V1, aber bereits der neue
    # Ausstiegspfad. Fachlich schon V2, nur falsch etikettiert.
    ("NEXUS-FT-SAMPLE-V1",
     "5611109f87491637ddaa6727534b3ce09284788be194a632d257f8fc8d98d5a2"):
        "V1-Etikett aus 9.1 mit bereits neuem Ausstiegspfad",
}

STATUS_MIGRATION_NOETIG = "STRATEGY_MIGRATION_REQUIRED"
STATUS_KONTO_ABWEICHUNG = "ACCOUNT_MISMATCH"


def _jetzt() -> str:
    return datetime.now(timezone.utc).isoformat()


def aktuelle_identitaet() -> tuple[str, str]:
    from freqtrade_sample_strategy import PARAMETER_HASH, STRATEGY_VERSION
    return str(STRATEGY_VERSION), str(PARAMETER_HASH)


def ist_aktuell(position) -> bool:
    version, hash_ = aktuelle_identitaet()
    return (str(getattr(position, "strategy_version", "")) == version
            and str(getattr(position, "strategy_parameter_hash", "")) == hash_)


def pruefe(position, *, account_fingerprint: str = "") -> tuple[bool, str, str]:
    """Darf diese Position migriert werden?

    Rueckgabe: (erlaubt, statuskennung, Klartext). Es wird nichts veraendert.
    """
    if ist_aktuell(position):
        return False, "", "Strategieidentitaet ist bereits aktuell"

    # 1. Eigentum. Ohne vollstaendige Broker-ID-Kette wird nichts migriert --
    #    und ein fehlender Nachweis wird niemals durch den Kontosaldo ersetzt.
    if not getattr(position, "ownership_chain_complete", False):
        return (False, STATUS_MIGRATION_NOETIG,
                "Broker-ID-Kette unvollstaendig; keine automatische Migration")

    # 2. Konto. Eine Position aus einem anderen Konto (Demo/Live, Unterkonto)
    #    wird nie stillschweigend uebernommen.
    gespeichert = str(getattr(position, "account_fingerprint", "") or "")
    aktuell = str(account_fingerprint or "")
    if gespeichert and aktuell and gespeichert != aktuell:
        return (False, STATUS_KONTO_ABWEICHUNG,
                "Position gehoert zu einem anderen OKX-Konto")

    # 3. Bekannte Vorgaengeridentitaet.
    schluessel = (str(getattr(position, "strategy_version", "")),
                  str(getattr(position, "strategy_parameter_hash", "")))
    grund = MIGRIERBAR.get(schluessel)
    if not grund:
        return (False, STATUS_MIGRATION_NOETIG,
                f"Unbekannte Strategieidentitaet {schluessel[0]}/"
                f"{schluessel[1][:12]}…; keine automatische Migration")
    return True, "", grund


def migriere(position, *, actor: str = "auto", account_fingerprint: str = "") -> dict:
    """Die Position ausdruecklich auf die aktuelle Strategieversion heben.

    Idempotent: ein zweiter Aufruf tut nichts. Es werden ausschliesslich
    Strategiefelder geschrieben -- keine Order, keine Menge, kein Schutz.
    """
    erlaubt, status, grund = pruefe(
        position, account_fingerprint=account_fingerprint)
    if not erlaubt:
        if status:
            position.management_status = status
        return {"migriert": False, "status": status, "grund": grund}

    version, hash_ = aktuelle_identitaet()
    vorher_version = str(getattr(position, "strategy_version", ""))
    vorher_hash = str(getattr(position, "strategy_parameter_hash", ""))

    from freqtrade_sample_strategy import parameter_snapshot
    schnappschuss = parameter_snapshot()

    position.strategy_version = version
    position.strategy_parameter_hash = hash_
    position.strategy_name = str(schnappschuss.get("strategy_name") or
                                 position.strategy_name)
    position.strategy_parameters = dict(schnappschuss)
    position.strategy_migrated_at = _jetzt()
    position.strategy_migrated_from_version = vorher_version
    position.strategy_migrated_from_hash = vorher_hash
    position.strategy_migration_reason = str(grund)[:300]
    position.strategy_migration_actor = str(actor or "auto")[:80]
    position.management_status = ""

    logger.warning(
        "Strategie-Migration %s: %s/%s -> %s/%s (%s, durch %s)",
        getattr(position, "symbol", "?"), vorher_version, vorher_hash[:12],
        version, hash_[:12], grund, actor)
    return {"migriert": True, "status": "", "grund": grund,
            "von_version": vorher_version, "von_hash": vorher_hash,
            "nach_version": version, "nach_hash": hash_}


__all__ = ["MIGRIERBAR", "STATUS_KONTO_ABWEICHUNG", "STATUS_MIGRATION_NOETIG",
           "aktuelle_identitaet", "ist_aktuell", "migriere", "pruefe"]
