"""Architektur-Leitplanken (10.8.0, Schritte 0-2).

WARUM DIESE TESTS EXISTIEREN
============================
Der Architektur-Audit vom 19.09.2026 fand Import-Zyklen ueber 44 Module,
Adapter, die den Kern importieren, und neun Module ueber 1.500 Zeilen. Der
CSCO-Vorfall (10.7.0) entstand genau in dieser Verflechtung: drei Stellen
registrierten Risikobelege, keine kannte die andere.

Diese Suite misst den Importgraphen des Quellbaums SELBST -- per ``ast``,
ohne Fremdpaket, auch auf dem Pi -- und vergleicht gegen
``validation/ARCHITEKTUR_BASELINE.json``. Die Regel ist einfach: Zyklen,
Schichtverstoesse und Grossmodule duerfen nur sinken. Wer sie erhoeht, sieht
hier rot und muss die Baseline bewusst aendern und im Changelog begruenden.

Schritt 1 (nexus/-Paket, Weichen) und Schritt 2 (sechs kurze Zyklen gebrochen)
werden hier ebenfalls festgehalten.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nexus.architektur import importgraph, schichten  # noqa: E402

BASELINE = ROOT / "validation" / "ARCHITEKTUR_BASELINE.json"


@pytest.fixture(scope="module")
def graph():
    return importgraph.graph(ROOT)


@pytest.fixture(scope="module")
def module():
    return importgraph.quellmodule(ROOT)


@pytest.fixture(scope="module")
def karte():
    return schichten.lade()


@pytest.fixture(scope="module")
def baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


# --- Schritt 0: Leitplanken -------------------------------------------------

def test_baseline_traegt_version_und_stand(baseline):
    assert baseline["version"] == "10.8.0"
    assert baseline["stand"] == "2026-09-19"
    for k in ("zyklen", "schichtverstoesse", "grossmodule", "kennzahlen"):
        assert k in baseline


def test_jedes_modul_hat_genau_eine_schicht(module, karte):
    fehlend = schichten.ohne_schicht(module, karte)
    assert not fehlend, ("Neue Module ohne Schicht -- in nexus/architektur/schichten.json einsortieren: "
                         + ", ".join(fehlend))
    gesehen = {}
    for schicht, namen in karte["schichten"].items():
        for n in namen:
            assert n not in gesehen, f"{n} steht in {gesehen[n]} UND {schicht}"
            gesehen[n] = schicht


def test_karte_ohne_verwaiste_eintraege(module, karte):
    verwaist = schichten.verwaist(module, karte)
    assert not verwaist, "Karte nennt Module, die es nicht mehr gibt: " + ", ".join(verwaist)


def test_regeln_kennen_jede_schicht(karte):
    for schicht in karte["schichten"]:
        assert schicht in karte["regeln"], schicht
    for schicht, erlaubt in karte["regeln"].items():
        for ziel in erlaubt:
            assert ziel == "*" or ziel in karte["regeln"], (schicht, ziel)


def test_zyklen_nur_sinkend(graph, baseline):
    """Jede heutige Komponente muss Teilmenge einer Baseline-Komponente sein."""
    erlaubt = [set(k) for k in baseline["zyklen"]["komponenten"]]
    heute = importgraph.zyklen(graph)
    for komp in heute:
        menge = set(komp)
        assert any(menge <= alt for alt in erlaubt), (
            "Neuer oder gewachsener Import-Zyklus: " + ", ".join(komp)
            + " | kurze Kreise: " + "; ".join(" -> ".join(k) for k in importgraph.kuerzeste_kreise(graph, komp)[:5]))
    assert len(heute) <= len(erlaubt)
    assert sum(len(k) for k in heute) <= sum(len(k) for k in erlaubt)


def test_schichtverstoesse_nur_sinkend(graph, karte, baseline):
    heute = {tuple(v) for v in schichten.verstoesse(graph, karte)}
    alt = {tuple(v) for v in baseline["schichtverstoesse"]}
    neu = sorted(heute - alt)
    assert not neu, ("Neue Importrichtung gegen die Schichtenkarte (Quelle -> Ziel, Schichten):\n  "
                     + "\n  ".join(f"{q} -> {z} ({r})" for q, z, r in neu))


def test_domain_importiert_nur_domain_und_konfiguration(graph, karte):
    """Harte Regel: Fachregeln kennen weder Broker noch Dateien noch Netz."""
    zu = schichten.zuordnung(karte)
    fehler = []
    for m in karte["schichten"]["domain"]:
        for z in sorted(graph.get(m, ())):
            zs = schichten.fachschicht(z, karte, zu)
            if zs not in (None, "domain", "konfiguration"):
                fehler.append(f"{m} -> {z} ({zs})")
    assert not fehler, "\n".join(fehler)


def test_webui_importiert_keinen_broker(graph):
    """Harte Regel: die Oberflaeche spricht nie direkt mit einem Broker-Adapter."""
    kanten = sorted((a, b) for a, z in graph.items() if a.startswith("webui")
                    for b in z if b == "broker" or b.startswith("broker."))
    assert not kanten, kanten


def test_adapter_nach_kern_nur_sinkend(graph, karte, baseline):
    zu = schichten.zuordnung(karte)
    heute = {(a, b) for a, z in graph.items() if a.startswith("broker.")
             for b in z if not b.startswith("broker")
             and schichten.fachschicht(b, karte, zu) in {"application", "interfaces"}}
    alt = {tuple(k) for k in baseline["adapter_nach_kern"]}
    assert not (heute - alt), sorted(heute - alt)


def test_pulsar_nach_aussen_nur_sinkend(graph, baseline):
    heute = {(a, b) for a, z in graph.items() if a.startswith("pulsar.")
             for b in z if not b.startswith("pulsar")}
    alt = {tuple(k) for k in baseline["pulsar_nach_aussen"]}
    assert not (heute - alt), sorted(heute - alt)


def test_grossmodule_nur_sinkend(module, baseline):
    limit = int(baseline["grossmodule"]["limit_zeilen"])
    toleranz = float(baseline["grossmodule"]["toleranz"])
    ausnahmen = baseline["grossmodule"]["ausnahmen"]
    fehler = []
    for m, p in sorted(module.items()):
        n = importgraph.zeilen(p)
        if n <= limit:
            continue
        if m not in ausnahmen:
            fehler.append(f"{m}: {n} Zeilen > {limit} (neues Grossmodul)")
        elif n > int(ausnahmen[m] * (1 + toleranz)):
            fehler.append(f"{m}: {n} Zeilen, Baseline {ausnahmen[m]} (+{toleranz:.0%} erlaubt)")
    assert not fehler, "\n".join(fehler)


def test_kennzahlen_der_baseline_stimmen_mit_dem_baum_ueberein(graph, module, karte, baseline):
    """Die Baseline ist ein Messwert, keine Behauptung: Zahlen muessen zum Baum passen (oder besser sein)."""
    k = baseline["kennzahlen"]
    komponenten = importgraph.zyklen(graph)
    assert len(komponenten) <= k["zyklen_komponenten"]
    assert sum(len(x) for x in komponenten) <= k["zyklen_module"]
    assert len(schichten.verstoesse(graph, karte)) <= k["schichtverstoesse"]
    assert k["zyklen_module"] <= 21 and k["zyklen_komponenten"] <= 2  # Stand nach Schritt 2


# --- Schritt 1: nexus-Paket und Weichen -------------------------------------

def test_weiche_liefert_ein_modulobjekt(karte):
    import importlib
    for alt, neu in karte["alias"].items():
        a = importlib.import_module(alt)
        n = importlib.import_module(neu)
        assert a is n, (alt, neu)
        assert sys.modules[alt] is sys.modules[neu]
        assert a.__name__ == neu
        datei = ROOT / (alt.replace(".", "/") + ".py")
        text = datei.read_text(encoding="utf-8")
        assert "umleiten(__name__" in text and len(text.splitlines()) <= 6, alt
        assert Path(n.__file__).resolve() == (ROOT / (neu.replace(".", "/") + ".py")).resolve()


def test_alias_module_stehen_als_alias_in_der_karte(karte):
    for alt, neu in karte["alias"].items():
        assert alt in karte["schichten"]["alias"], alt
        assert neu not in karte["schichten"]["alias"], neu
        assert any(neu in namen for s, namen in karte["schichten"].items() if s != "alias"), neu


def test_paketskelett_vollstaendig():
    for name in ("domain", "application", "ports", "adapters", "state", "interfaces", "architektur"):
        init = ROOT / "nexus" / name / "__init__.py"
        assert init.is_file(), init
        assert init.read_text(encoding="utf-8").lstrip().startswith('"""'), name
    for datei in ("nexus/__init__.py", "nexus/weiche.py", "nexus/pfade.py",
                  "nexus/architektur/importgraph.py", "nexus/architektur/schichten.py",
                  "nexus/architektur/schichten.json"):
        assert (ROOT / datei).is_file(), datei


def test_umgezogene_zustandsdatei_bleibt_im_projektordner(monkeypatch):
    """risk_levels liegt in nexus/application/, risiko_stufen.json weiter an der Wurzel."""
    import risk_levels
    from nexus import pfade
    assert pfade.PROJEKT_WURZEL == ROOT
    monkeypatch.delenv("TRADINGBOT_TEST_STATE_DIR", raising=False)
    assert risk_levels.path().parent == ROOT
    assert risk_levels.path().name == "risiko_stufen.json"
    monkeypatch.setenv("TRADINGBOT_TEST_STATE_DIR", str(ROOT / "tests"))
    assert risk_levels.path().parent == ROOT / "tests"


def test_umgezogene_module_liegen_in_der_richtigen_schicht(karte):
    assert "nexus.domain.ledger_result" in karte["schichten"]["domain"]
    assert "nexus.domain.okx_receipt_math" in karte["schichten"]["domain"]
    assert "nexus.application.handelsfreigabe" in karte["schichten"]["application"]
    assert "nexus.application.risk_levels" in karte["schichten"]["application"]


# --- Schritt 2: kurze Zyklen sind gebrochen ---------------------------------

@pytest.mark.parametrize("a,b", [
    ("repair_okx_verified_history", "nexus_update"),
    ("repair_okx_accounting", "nexus_update"),
    ("risk_basis_review", "risk_manager"),
    ("etoro_risk_maintenance", "risk_basis_review"),
    ("etoro_protection_repair", "broker.etoro"),
    ("etoro_fee_recovery", "etoro_reconciliation"),
    ("etoro_cancellations", "etoro_reconciliation"),
    ("nasdaq_halt_feed", "news_sources"),
    ("market_intelligence.account_registry", "market_intelligence.service"),
    ("approved_universe", "config"),
    ("fmp_data", "fmp_reference"),
    ("fmp_service", "fmp_reference"),
    ("risk_pots", "risk_levels"),
])
def test_kurzer_zyklus_ist_gebrochen(graph, karte, a, b):
    """Genau eine Richtung darf bleiben -- nie beide (Aliasse zaehlen wie ihr Ziel)."""
    alias = karte["alias"]

    def importiert(quelle, ziel):
        namen_q = {quelle, alias.get(quelle, quelle)}
        namen_z = {ziel, alias.get(ziel, ziel)}
        return any(z in namen_z for q in namen_q for z in graph.get(q, ()))

    assert not (importiert(a, b) and importiert(b, a)), f"{a} <-> {b} importieren sich weiterhin gegenseitig"


def test_gemeinsame_helfer_liegen_unten(graph):
    """Die in Schritt 2 nach unten gezogenen Helfer greifen nicht nach oben."""
    assert graph.get("installer_host", set()) == set()
    assert graph.get("risk_basis_status", set()) == set()
    assert graph.get("news_model", set()) == set()
    assert graph.get("risk_grenzen", set()) <= {"config"}
    assert graph.get("etoro_protection_readback", set()) <= {"etoro_protection_evidence"}
    assert graph.get("market_intelligence.store", set()) == set()
    assert "config" not in graph.get("approved_universe", set())


def test_reexporte_fuer_alte_importpfade():
    """Aufrufer und Tests aus 10.7.1 finden die Namen weiter am alten Ort."""
    import installer_host
    try:
        import nexus_update  # braucht fcntl (Pi); auf Windows ohne Shim nicht ladbar
    except ModuleNotFoundError as exc:
        assert exc.name == "fcntl", exc
    else:
        assert nexus_update.Host is installer_host.Host
        assert nexus_update.UpdateError is installer_host.UpdateError
    import risk_basis_review, risk_basis_status
    assert risk_basis_review.status is risk_basis_status.status
    assert risk_basis_review.scope is risk_basis_status.scope
    import risk_pots, risk_grenzen
    assert risk_pots.TopfGrenzen is risk_grenzen.TopfGrenzen
    import etoro_protection_repair, etoro_protection_readback
    assert etoro_protection_repair.merge_breakdown is etoro_protection_readback.merge_breakdown
    import news_sources, news_model
    assert news_sources.NewsItem is news_model.NewsItem
    assert news_sources._safe_dt is news_model._safe_dt
    from market_intelligence import service, store
    assert service.digest is store.digest


def test_etoro_nachlauf_laeuft_als_parameter_nicht_als_import(graph):
    """background_tick kennt Storno und Gebuehren nur als uebergebene Schritte."""
    import etoro_reconciliation, etoro_nachlauf
    assert "etoro_cancellations" not in graph["etoro_reconciliation"]
    assert "etoro_fee_recovery" not in graph["etoro_reconciliation"]
    assert etoro_nachlauf.SCHRITTE == (etoro_nachlauf.storno, etoro_nachlauf.gebuehren)
    import inspect
    sig = inspect.signature(etoro_reconciliation.background_tick)
    assert "nachlauf" in sig.parameters and sig.parameters["nachlauf"].default == ()
    quelle = Path(sys.modules["broker.etoro"].__file__ if "broker.etoro" in sys.modules
                  else ROOT / "broker" / "etoro.py").read_text(encoding="utf-8")
    assert "nachlauf=etoro_nachlauf.SCHRITTE" in quelle
