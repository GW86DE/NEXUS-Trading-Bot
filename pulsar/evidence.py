"""PULSAR 2.0: Hype-Spur -- Social-Spike + Kursbestaetigung statt Belegscore.

10.3.0 ersetzt die alte Score-80-Maschine (Katalysator-Pflicht, 14-Tage-
Baseline, Terra-Doppelpruefung). Sie hat auf Squeeze-Zeitskalen strukturell
nie gekauft: Ein Kandidat brauchte ab Erstbeobachtung mindestens 14 volle
Beobachtungstage, bevor die Aufmerksamkeits-Komponente ueberhaupt zaehlte.

Die Hype-Spur belegt stattdessen drei Dinge GLEICHZEITIG aus sofort
verfuegbaren, datierten Anbieterfeldern:
1. Social-Spike (Reddit-Anbieterfelder bzw. begrenzte X-Stichprobe),
2. Kurs-/Volumenbestaetigung aus den FMP-Kerzen ("es passiert wirklich"),
3. unveraenderte Identitaets-/Qualitaetsbloecke der Karte (Profil, kein ETF,
   Kurs >= 5 USD, Marktkapitalisierung, Tagesumsatz, Kurssprung-Obergrenze).

Kein Kriterium wird aus fehlenden Daten erfunden (UNKNOWN bleibt UNKNOWN),
und die Spur gibt keine Order frei: Jede Nominierung braucht weiterhin die
persoenliche zweistufige Telegram-Bestaetigung plus alle Core-Gates.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
import time

REVISION = "PULSAR-2.2-TRIGGER-CONFIRM-MEASURED"
# 10.5.0: Spike gegen die eigene 14-Tage-Basislinie (Stundenmedian, research_history).
OWN_BASELINE_MULTIPLE = 3.0

# Social-Spike-Schwellen (Reddit-Anbieterfelder, Quervergleich statt eigener
# 14-Tage-Historie): entweder belegtes Wachstum oder ein Cold-Start-Neuzugang.
SPIKE_MENTIONS = 100
SPIKE_GROWTH = 3.0
COLDSTART_MENTIONS = 40
# X liefert nur eine begrenzte Stichprobe, nie eine Erwaehnungs-Vollzaehlung.
X_MIN_POSTS = 8
X_MIN_ACCOUNTS = 5
X_MAX_AGE = 86400
# Kursbestaetigung: Tagesplus mindestens +5 %; die Obergrenze (+20 % / +40 %)
# setzt weiterhin der Kartenblock "Starker Kurssprung: nur beobachten".
PRICE_MIN_GAIN = .05
VOLUME_MULTIPLE_DAILY = 3.0
# 10.4.0: Der FMP-Starter-Quote (ohnehin je Karte abgerufen) traegt price,
# previousClose, volume (Tagesvolumen bis jetzt) und timestamp. Er ersetzt
# die nie befuellte Intraday-Reihe: Ausbruch am selben Tag statt am Folgetag.
QUOTE_MAX_AGE = 900
QUOTE_VOLUME_MULTIPLE = 1.0
# 10.8.0: 15-Minuten-Kerzen von eToro (aktive Karten) gehen vor dem FMP-Quote,
# wenn die juengste Kerze hoechstens 45 Minuten alt ist; danach ist der Quote
# der Rueckfall, dann die Tageskerze. Gleiche Schwellen ueberall.
INTRADAY_FRESH_AGE = 2700


def _quote_source(card):
    for source in card.get("sources") or []:
        if (isinstance(source, dict) and source.get("provider") == "FMP"
                and source.get("kind") == "quote" and isinstance(source.get("data"), dict)
                and str(source["data"].get("symbol") or "").upper() == str(card.get("symbol") or "").upper()):
            return source["data"]
    return None


def _number(value):
    try:
        v = float(value)
        return v if math.isfinite(v) and v > 0 else None
    except (TypeError, ValueError):
        return None


def _number_signed(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# 10.4.0 Existenzrisiko-Block (Georg, 17.09.2026): Ein Hype-Kandidat wird
# NUR dann hart blockiert, wenn die Aktie waehrend des Trades aufhoeren
# koennte zu existieren -- Insolvenzantrag, Chapter 11, "going concern",
# Delisting, Handelsaussetzung, Betrugsermittlung. Ein Stop-Loss greift
# dann nicht (kein Handel), das Risiko waere die ganze Position statt
# 0,25 %. AUSDRUECKLICH KEIN Block fuer schwache Bilanzen (negativer
# Gewinn/Cashflow, hohe Schulden): das ist Teil des Squeeze-Musters
# (GME 2021, AMC) und bleibt nur als Information im Alarm sichtbar.
EXISTENCE_RISK_PATTERNS = (
    (r"\b(files?|filed|filing) for (chapter (7|11|15)|bankruptcy|insolvency)\b", "Insolvenzantrag"),
    (r"\bchapter (7|11|15)\b", "Chapter-Verfahren"),
    (r"\bbankruptcy (filing|protection|court|petition|proceedings?)\b", "Insolvenzverfahren"),
    (r"\bgoing[- ]concern\b", "Going-Concern-Warnung"),
    (r"\binsolvenz(antrag|verfahren)?\b|\binsolvent\b|\binsolvency\b", "Insolvenz"),
    (r"\bdelist(ed|ing)?\b|\bdelisting notice\b", "Delisting"),
    (r"\btrading (halt(ed)?|suspend(ed|sion))\b|\bhandelsaussetzung\b", "Handelsaussetzung"),
    (r"\b(sec|doj|fraud) (investigation|probe|charges?)\b|\baccounting fraud\b|\bsecurities fraud\b|\bbetrugsermittlung\b", "Betrugsermittlung"),
)
EXISTENCE_RISK_MAX_AGE = 90*86400


def _existence_risk(card, *, now):
    """Liefert (Blockgrund | None, Belege). Nur Existenzrisiken, keine Bewertung."""
    import re
    from datetime import datetime as _dt
    findings = []
    profile = next((s.get("data") for s in card.get("sources") or []
                    if isinstance(s, dict) and s.get("provider") == "FMP" and s.get("kind") == "profile"
                    and isinstance(s.get("data"), dict)), None) or {}
    if profile.get("symbol") == card.get("symbol") and profile.get("isActivelyTrading") is False:
        findings.append({"art": "Delisting", "quelle": "FMP-Profil", "beleg": "isActivelyTrading = false"})
    def recent(value):
        try:
            stamp = _dt.fromisoformat(str(value or "").replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return 0 <= now-stamp.timestamp() <= EXISTENCE_RISK_MAX_AGE
        except (ValueError, TypeError):
            return False
    for source in card.get("sources") or []:
        if not isinstance(source, dict):
            continue
        kind, data = source.get("kind"), source.get("data")
        texts = []
        if kind == "news" and isinstance(data, list):
            from .news_normalization import normalize_news
            for row in normalize_news(data, source.get("provider")):
                if recent(row.get("publishedDate")):
                    texts.append((str(row.get("title") or "")+" "+str(row.get("text") or ""), source.get("provider"), row.get("title")))
        elif kind == "primary_document" and isinstance(data, dict):
            if recent(data.get("published_at") or data.get("event_time")):
                texts.append((str(data.get("text") or "")[:20000], "SEC/Original", data.get("form") or data.get("url")))
        for text, provider, title in texts:
            lowered = text.lower()
            for pattern, label in EXISTENCE_RISK_PATTERNS:
                if re.search(pattern, lowered):
                    findings.append({"art": label, "quelle": str(provider), "beleg": str(title or "")[:160]})
                    break
    if not findings:
        return None, []
    arten = sorted({f["art"] for f in findings})
    return ("Existenzrisiko: " + ", ".join(arten) + " (Stop-Loss greift bei Aussetzung/Delisting nicht)"), findings[:8]


def _social_spike(card, *, now):
    """Belegter Aufmerksamkeits-Spike aus datierten Anbieterfeldern."""
    from .research import _count
    attention = card.get("attention") or {}
    observed = attention.get("observed_at")
    if type(observed) not in (int, float) or not math.isfinite(observed):
        return None, "Aufmerksamkeitsbeobachtung ohne Zeitbeleg"
    if attention.get("source") == "X":
        sample = attention.get("x_discovery") or {}
        posts = _count(sample.get("sampled_post_count"))
        accounts = _count(sample.get("distinct_accounts_in_sample"))
        if not 0 <= now-observed <= X_MAX_AGE:
            return None, "X-Stichprobe aelter als 24 Stunden"
        if posts is None or accounts is None:
            return None, "X-Stichprobe ohne Post-/Accountzahlen"
        if posts >= X_MIN_POSTS and accounts >= X_MIN_ACCOUNTS:
            return {"kind": "X_STICHPROBE", "posts": posts, "accounts": accounts,
                    "detail": f"{posts} Beitraege von {accounts} Accounts in der begrenzten "
                              "X-Stichprobe; keine Vollzaehlung und keine Kursaussage."}, None
        return None, (f"X-Stichprobe zu duenn ({posts} Beitraege / {accounts} Accounts; "
                      f"mindestens {X_MIN_POSTS}/{X_MIN_ACCOUNTS})")
    if not 0 <= now-observed <= 3600:
        return None, "Reddit-Aufmerksamkeit aelter als eine Stunde"
    mentions = _count(attention.get("mentions"))
    previous = _count(attention.get("mentions_24h_ago"))
    if mentions is None:
        return None, "Aktuelle Erwaehnungszahl fehlt"
    if previous is not None and previous > 0:
        growth = mentions/previous
        if mentions >= SPIKE_MENTIONS and growth >= SPIKE_GROWTH:
            return {"kind": "REDDIT_WACHSTUM", "mentions": mentions, "growth_ratio": growth,
                    "detail": f"{mentions} Erwaehnungen, {growth:.1f}x gegenueber Vortag "
                              "(Anbieterfelder; kein Nachweis unabhaengiger Menschen)."}, None
        return None, (f"Kein belegter Spike: {mentions} Erwaehnungen bei {growth:.1f}x Wachstum "
                      f"(noetig: >= {SPIKE_MENTIONS} und >= {SPIKE_GROWTH:g}x)")
    if mentions >= COLDSTART_MENTIONS:
        return {"kind": "REDDIT_NEUZUGANG", "mentions": mentions,
                "detail": f"Neu in der Topliste mit {mentions} Erwaehnungen; Vortageswert "
                          "unbekannt und wird nicht erfunden."}, None
    return None, (f"Neuzugang zu leise ({mentions} Erwaehnungen; "
                  f"mindestens {COLDSTART_MENTIONS} ohne Vortagesvergleich)")


def _price_confirmation(card, *, now):
    """Kurs- und Volumenbestaetigung aus abgeschlossenen Kerzen und dem Quote.

    Reihenfolge (10.8.0): frische 15-Minuten-Kerzen des laufenden Tages (eToro,
    aktive Karten; hoechstens 45 min alt) gegen den letzten Tagesschluss, plus
    heutiges Volumen mindestens auf Hoehe eines kompletten Durchschnittstages
    DERSELBEN Quelle (eToro-Kerzen zaehlen ihr Volumen anders als FMP; der
    Vergleich bleibt skalenfrei); bestaetigen die Kerzen nicht, entscheidet der
    frische FMP-Quote (Rueckfall) -- die Kerzen koennen eine Bestaetigung nur
    hinzufuegen, nie wegnehmen; sonst aeltere Intraday-Kerzen bis 2 h; sonst
    die letzte abgeschlossene Tageskerze (+5 % und 3x Durchschnittsvolumen).
    Nichts wird aus fehlenden Daten erfunden.
    """
    bars = card.get("bars") or []
    closes = [(_number(r.get("close")), _number(r.get("volume"))) for r in bars[-21:]]
    if len(bars) < 21 or any(c is None for c, _ in closes):
        return None, "Weniger als 21 vollstaendige Tageskerzen"
    volumes = [v for _, v in closes[:-1] if v is not None]
    if len(volumes) < 15:
        return None, "Tagesvolumen der Vergleichstage unvollstaendig"
    avg_volume = sum(volumes)/len(volumes)
    today = datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date().isoformat()
    intraday_today = []
    for row in card.get("intraday") or []:
        try:
            stamp = datetime.fromisoformat(str(row.get("date")))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo("America/New_York"))
            if stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat() != today:
                continue
            close, volume = _number(row.get("close")), _number(row.get("volume"))
            if close is not None and volume is not None and now-stamp.timestamp() >= 0:
                intraday_today.append((stamp.timestamp(), close, volume))
        except (TypeError, ValueError):
            continue
    prev_close = closes[-1][0]
    intraday_source = str(card.get("intraday_source") or "FMP")
    # eToro-Kerzen: Tagesdurchschnitt derselben Quelle (skalenfrei); ohne ihn
    # gibt es fuer eToro-Kerzen keine Volumenaussage und damit keine Bestaetigung.
    eigene_basis = _number(card.get("intraday_avg_day_volume")) if intraday_source == "ETORO_15M" else None
    basis = eigene_basis if intraday_source == "ETORO_15M" else avg_volume

    def _intraday_verdict():
        intraday_today.sort()
        last_close = intraday_today[-1][1]
        gain = last_close/prev_close-1
        traded = sum(v for _, _, v in intraday_today)
        quelle = "eToro-15-Minuten-Kerzen" if intraday_source == "ETORO_15M" else "Intraday-Kerzen"
        if basis is None:
            return None, f"Intraday ohne Vergleichsbasis ({quelle}): Tagesdurchschnitt derselben Quelle fehlt"
        if gain >= PRICE_MIN_GAIN and traded >= basis:
            return {"kind": "INTRADAY", "gain": gain, "volume_multiple": traded/basis, "source": intraday_source,
                    "detail": f"Heute {gain*100:.1f} % ueber dem letzten Tagesschluss bei "
                              f"{traded/basis:.1f}x eines Durchschnittstagesvolumens ({quelle})."}, None
        return None, (f"Intraday nicht bestaetigt ({quelle}): {gain*100:.1f} % Kursplus, "
                      f"{traded/basis:.1f}x Tagesvolumen (noetig: >= {PRICE_MIN_GAIN*100:.0f} % "
                      "und >= 1,0x)")

    # 10.8.0: Frische 15-Minuten-Kerzen (eToro, aktive Karten) zuerst -- nur
    # eine BESTAETIGUNG daraus zaehlt sofort; ein Nein faellt auf den Quote zurueck.
    intraday_gap = None
    if intraday_today and now-max(t for t, _, _ in intraday_today) <= INTRADAY_FRESH_AGE:
        verdict, intraday_gap = _intraday_verdict()
        if verdict:
            return verdict, None
    # 10.4.0: Frischer Starter-Quote gegen den Vortagesschluss. Das
    # Quote-Volumen ist das bisherige Tagesvolumen; es muss mindestens einen
    # vollen Durchschnittstag erreichen, damit ein fruehes Tagesviertel mit
    # duennem Umsatz nie als Ausbruch gilt.
    quote = _quote_source(card)
    if quote is not None:
        q_price = _number(quote.get("price"))
        q_prev = _number(quote.get("previousClose"))
        q_volume = _number(quote.get("volume"))
        q_stamp = quote.get("timestamp")
        fresh = (type(q_stamp) in (int, float) and math.isfinite(q_stamp)
                 and -60 <= now-q_stamp <= QUOTE_MAX_AGE)
        if fresh and q_price is not None and q_prev is not None and q_volume is not None:
            gain = q_price/q_prev-1
            if gain >= PRICE_MIN_GAIN and q_volume >= QUOTE_VOLUME_MULTIPLE*avg_volume:
                return {"kind": "QUOTE", "gain": gain, "volume_multiple": q_volume/avg_volume,
                        "detail": f"Aktueller Kurs {gain*100:.1f} % ueber dem Vortagesschluss bei "
                                  f"{q_volume/avg_volume:.1f}x eines Durchschnittstagesvolumens (FMP-Quote)."}, None
            # Ein frischer, aber unbestaetigter Quote ist die genaueste
            # Aussage ueber HEUTE; die Tageskerze von gestern ersetzt sie nicht.
            return None, (f"Quote nicht bestaetigt: {gain*100:.1f} % zum Vortagesschluss, "
                          f"{q_volume/avg_volume:.1f}x Tagesvolumen (noetig: >= {PRICE_MIN_GAIN*100:.0f} % "
                          f"und >= {QUOTE_VOLUME_MULTIPLE:g}x)"
                          + (f"; {intraday_gap}" if intraday_gap else ""))
    if intraday_gap:
        return None, intraday_gap
    # Der Kursstand muss aktuell sein; das Tagesvolumen summiert ALLE heutigen
    # abgeschlossenen 15-Minuten-Kerzen.
    if intraday_today and now-max(t for t, _, _ in intraday_today) > 2*3600:
        intraday_today = []
    if intraday_today:
        return _intraday_verdict()
    last_close, last_volume = closes[-1]
    prior_close = closes[-2][0]
    gain = last_close/prior_close-1
    if last_volume is None:
        return None, "Volumen der letzten Tageskerze fehlt"
    prior_volumes = [v for _, v in closes[:-1] if v is not None]
    avg_prior = sum(prior_volumes)/len(prior_volumes)
    if gain >= PRICE_MIN_GAIN and last_volume >= VOLUME_MULTIPLE_DAILY*avg_prior:
        return {"kind": "TAGESKERZE", "gain": gain, "volume_multiple": last_volume/avg_prior,
                "detail": f"Letzter Handelstag {gain*100:.1f} % bei "
                          f"{last_volume/avg_prior:.1f}x Durchschnittsvolumen."}, None
    return None, (f"Tageskerze nicht bestaetigt: {gain*100:.1f} % Kursplus, "
                  f"{last_volume/avg_prior:.1f}x Volumen (noetig: >= {PRICE_MIN_GAIN*100:.0f} % "
                  f"und >= {VOLUME_MULTIPLE_DAILY:g}x)")


def _x_confirmation(card, *, now):
    """Punkt 3 (10.8.0): Ergebnis der angeforderten X-Bestaetigungssuche als Social-Familie.

    Zaehlt nach derselben Regel wie die X-Stichprobe (>= 8 Beitraege von >= 5
    Konten, hoechstens 24 h alt) -- aber nur als ZWEITE Social-Familie fuer
    die Bestaetigung "zweite_social_familie", nie als Ausloeser und nie als
    eigener Bestaetigungsplatz. Keine Suche, kein Ergebnis: UNKNOWN.
    """
    from .research import _count
    context = (card.get("x_context") or {}).get("candidate_research") or {}
    if not context or not context.get("confirmation"):
        return None, None
    if context.get("state") != "PROCESSED":
        return None, "X-Bestaetigungssuche: " + str(context.get("state") or "offen")
    processed = context.get("processed_at")
    if type(processed) not in (int, float) or not 0 <= now-processed <= X_MAX_AGE:
        return None, "X-Bestaetigungssuche aelter als 24 Stunden"
    posts = _count(context.get("usable_posts"))
    accounts = _count(context.get("distinct_accounts_in_sample"))
    if posts is None or accounts is None:
        return None, "X-Bestaetigungssuche ohne Post-/Accountzahlen"
    if posts >= X_MIN_POSTS and accounts >= X_MIN_ACCOUNTS:
        return {"kind": "X_BESTAETIGUNG", "posts": posts, "accounts": accounts,
                "detail": f"{posts} Beitraege von {accounts} Accounts in der angeforderten X-Suche "
                          "(begrenzte Stichprobe; zaehlt nur als zweite Social-Familie)."}, None
    return None, (f"X-Bestaetigungssuche zu duenn ({posts} Beitraege / {accounts} Accounts; "
                  f"mindestens {X_MIN_POSTS}/{X_MIN_ACCOUNTS})")


def _own_baseline_spike(card):
    """Reddit-Spike gegen die EIGENE Stundenmedian-Basislinie (research_history).

    10.5.0: Die Anbieterfelder (mentions_24h_ago) bleiben der erste Weg; sobald
    NEXUS 14 Tage eigene Beobachtungen hat, zaehlt zusaetzlich das Verhaeltnis
    zur eigenen Basis. Ohne Basis: UNKNOWN, kein Spike.
    """
    from .research import _count
    attention = card.get("attention") or {}
    if attention.get("source") in {"X", "volume_watch", "stocktwits_trending"}:
        return None, "Eigene Reddit-Basislinie: nicht anwendbar"
    base = card.get("baseline") or {}
    mentions = _count(attention.get("mentions"))
    median = base.get("median_mentions")
    if not base.get("ready_14d") or not median:
        return None, f"Eigene Reddit-Basislinie noch nicht 14 Tage alt ({base.get('days', 0)} Tage)"
    if mentions is None:
        return None, "Aktuelle Erwaehnungszahl fehlt"
    ratio = mentions / max(20.0, float(median))
    if mentions >= 40 and ratio >= OWN_BASELINE_MULTIPLE:
        return {"kind": "REDDIT_EIGENE_BASIS", "mentions": mentions, "ratio": ratio,
                "detail": f"{mentions} Erwaehnungen, {ratio:.1f}x des eigenen 14-Tage-Medians ({median:.0f})."}, None
    return None, f"Kein Spike gegen eigene Basis: {mentions} Erwaehnungen bei {ratio:.1f}x (noetig >= 40 und >= {OWN_BASELINE_MULTIPLE:g}x)"


def evaluate(card, *, now=None):
    """10.5.0: Ausloeser + zwei von drei Bestaetigungen.

    Ausloeser (einer reicht): relatives Volumen >= 3x bei positivem Tageskurs,
    Reddit-Spike (Anbieterfelder oder eigene Basis), StockTwits-Spike,
    X-Stichprobe. Bestaetigungen (zwei von drei): relatives Volumen >= 2x,
    eine ZWEITE Social-Familie, Kursplus >= 5 % zum Vortagesschluss.
    Bloecke (Identitaet, Kurssprung, Existenzrisiko) und ein Luna-REJECT
    sperren weiterhin. Squeeze-Merkmal und Bilanz sind nur Information.
    """
    from .volume_watch import classify as classify_volume
    from .stocktwits import activity as stocktwits_activity
    from .short_interest import squeeze_profile
    now = time.time() if now is None else now
    gaps = list(card.get("missing", []))
    blocks = list(card.get("blocks", []))
    social, social_gap = _social_spike(card, now=now)
    own, own_gap = _own_baseline_spike(card)
    if social is None and own is not None:
        social, social_gap = own, None
    if social_gap:
        gaps.append("Social-Spike: " + social_gap)
    if own_gap and own is None and social is None:
        gaps.append("Eigene Basis: " + own_gap)
    st, st_gap = stocktwits_activity(card.get("stocktwits"), card.get("stocktwits_baseline"))
    if st_gap:
        gaps.append("StockTwits: " + st_gap)
    volume = classify_volume(card.get("volume"))
    if not volume["confirm"]:
        gaps.append("Relatives Volumen: " + volume["reason"])
    price, price_gap = _price_confirmation(card, now=now)
    if price_gap:
        gaps.append("Kursbestaetigung: " + price_gap)
    precheck = card.get("precheck") or {}
    verdict = (precheck.get("daten") or {}).get("verdict") if precheck.get("ok") else None
    if verdict == "REJECT":
        blocks.append("Luna-Vorpruefung verwirft den Kandidaten")
    if precheck.get("ok"):
        gaps.extend((precheck.get("daten") or {}).get("missing") or [])
    existence_block, existence_findings = _existence_risk(card, now=now)
    if existence_block:
        blocks.append(existence_block)
    finance_note = None
    try:
        from fmp_data import annual_context
        annual = next((s.get("data") for s in card.get("sources") or []
                       if isinstance(s, dict) and s.get("kind") == "annual_financials"
                       and isinstance(s.get("data"), dict)), None)
        latest = (annual_context(annual) if annual and annual.get("schema_version") is None else (annual or {})).get("latest") or {}
        if latest:
            parts = []
            for key, label in (("net_income", "Gewinn"), ("free_cashflow", "Free Cashflow"), ("equity", "Eigenkapital")):
                value = _number_signed(latest.get(key))
                if value is not None:
                    parts.append(f"{label} {'negativ' if value < 0 else 'positiv'}")
            if parts:
                finance_note = "Bilanz (nur Information): " + ", ".join(parts)
    except Exception:
        finance_note = None
    quote = _quote_source(card) or {}
    squeeze = squeeze_profile(card.get("short_interest"), _number(quote.get("sharesOutstanding")), now=now)
    # --- Ausloeser -------------------------------------------------------
    # Relatives Volumen: zeitanteilig aus Quote/Stundenkerzen (volume_watch);
    # ersatzweise das Volumenvielfache der Kursbestaetigung (Tageskerze 3x,
    # Quote/Intraday >= 1 Durchschnittstag) -- kein Volumen wird doppelt gezaehlt,
    # es gilt der jeweils belegte Wert.
    from .volume_watch import TRIGGER_MULTIPLE, CONFIRM_MULTIPLE
    price_multiple = _number((price or {}).get("volume_multiple")) if price else None
    price_gain = (price or {}).get("gain")
    volume_trigger = bool(volume["trigger"]) or bool(price_multiple and price_multiple >= TRIGGER_MULTIPLE
                                                     and price_gain is not None and price_gain > 0)
    volume_confirm = bool(volume["confirm"]) or bool(price_multiple and price_multiple >= CONFIRM_MULTIPLE
                                                     and price_gain is not None and price_gain > 0)
    volume_detail = (volume["reason"] if volume["confirm"] or volume["trigger"] else
                     (f"{price_multiple:.1f}x Durchschnittsvolumen laut Kursbestaetigung" if price_multiple else volume["reason"]))
    families = {}
    if social:
        families["X" if social.get("kind") == "X_STICHPROBE" else "REDDIT"] = social
    if st:
        families["STOCKTWITS"] = st
    triggers = []
    if volume_trigger:
        triggers.append(("VOLUMEN", volume_detail))
    for family in ("REDDIT", "STOCKTWITS", "X"):
        if family in families:
            triggers.append((family, families[family].get("detail", "")))
    trigger = None
    if triggers:
        trigger = {"kind": "+".join(k for k, _ in triggers), "family": triggers[0][0],
                   "parts": [{"kind": k, "detail": d} for k, d in triggers],
                   "detail": "; ".join(f"{k}: {d}" for k, d in triggers)}
    # --- Bestaetigungen (zwei von drei) --------------------------------------
    # 10.8.0 Punkt 3: Die angeforderte X-Bestaetigungssuche zaehlt NUR als
    # zweite Social-Familie -- nach den Ausloesern, damit sie nie selbst einer wird.
    x_confirm, x_confirm_gap = _x_confirmation(card, now=now)
    if x_confirm_gap:
        gaps.append(x_confirm_gap)
    if x_confirm and "X" not in families:
        families["X"] = x_confirm
    social_families_hit = set(families)
    first_social = next((k for k, _ in triggers if k != "VOLUMEN"), None)
    other_social = sorted(social_families_hit - ({first_social} if first_social else set()))
    confirmations = {
        "volumen": {"ok": volume_confirm, "detail": volume_detail},
        "zweite_social_familie": {"ok": bool(other_social), "families": sorted(families),
                                  "detail": ("Belegt: " + ", ".join(other_social)) if other_social else
                                            ("Nur eine Social-Familie belegt (" + first_social + ")" if first_social else
                                             "Keine Social-Familie belegt (Reddit, StockTwits, X)")},
        "kurs": {"ok": bool(price), "detail": (price or {}).get("detail") or price_gap},
    }
    confirmed = sum(1 for c in confirmations.values() if c["ok"])
    from .requirements import next_earnings
    earnings = next_earnings(card, now=now)
    hype = {"trigger": trigger, "confirmations": confirmations, "confirmed_count": confirmed,
            "social": social, "stocktwits": st, "x_confirmation": x_confirm,
            "volume": card.get("volume") or {}, "price": price,
            "luna": {"verdict": verdict, "available": bool(precheck.get("ok"))},
            "existence_risk": {"blocked": bool(existence_block), "findings": existence_findings},
            "finance_note": finance_note, "squeeze": squeeze,
            "detail": "Ausloeser (Volumen, Reddit, StockTwits oder X) plus zwei von drei Bestaetigungen "
                      "(Volumen >= 2x, zweite Social-Familie, Kursplus >= 5 %) und mindestens eine belegte "
                      "Social-Familie; keine Kursprognose, "
                      "keine Orderfreigabe. Jeder Einstieg braucht die persoenliche Telegram-Bestaetigung "
                      "und alle Core-Gates."}
    checks = [
        {"name": "identitaet", "status": "BELEGT" if not card.get("blocks") else "BLOCKIERT",
         "detail": "Profilidentitaet, keine ETF/Fonds, Kurs-/Groessen-/Umsatzgrenzen der Karte"},
        {"name": "existenzrisiko", "status": "BLOCKIERT" if existence_block else "KEIN_BEFUND",
         "detail": existence_block or "Kein Insolvenz-/Delisting-/Aussetzungs-/Betrugsbeleg in News (90 Tage), SEC-Dokumenten oder Profil"},
        {"name": "ausloeser", "status": "BELEGT" if trigger else "OFFEN",
         "detail": trigger["kind"] + ": " + trigger["detail"] if trigger else "Kein Ausloeser (Volumen >= 3x, Reddit-, StockTwits- oder X-Spike)"},
        {"name": "volumen", "status": "BELEGT" if confirmations["volumen"]["ok"] else "OFFEN", "detail": confirmations["volumen"]["detail"]},
        {"name": "zweite_social_familie", "status": "BELEGT" if confirmations["zweite_social_familie"]["ok"] else "OFFEN",
         "detail": confirmations["zweite_social_familie"]["detail"]},
        {"name": "kursbestaetigung", "status": "BELEGT" if price else "OFFEN",
         "detail": (price or {}).get("detail") or price_gap},
        {"name": "squeeze_merkmal", "status": {True: "JA", False: "NEIN"}.get(squeeze.get("flag"), "UNBEKANNT"),
         "detail": squeeze.get("detail")},
        {"name": "luna_warnfilter", "status": ("VERWORFEN" if verdict == "REJECT" else
                                               "BELEGT" if precheck.get("ok") else "OFFEN"),
         "detail": "Schnelle KI-Vorpruefung; nur ein REJECT blockiert"},
        {"name": "earnings", "status": "KALENDERDATUM" if earnings else "OFFEN",
         "detail": earnings["date"] if earnings else
         "Kein aktueller Earnings-Termin geliefert; der Core prueft den Abstand vor jeder Nominierung"},
        {"name": "broker", "status": "VOR_FREIGABE", "detail":
         "Handelbarkeit, Konto, Kosten und Schutz prueft der Core unmittelbar vor einer Anfrage"},
    ]
    # Ohne eine einzige Social-Familie ist es ein Ausbruch, kein Hype: gemessen
    # wird er trotzdem (Vergleichsgruppe), nominiert nie.
    eligible = bool(trigger and confirmed >= 2 and families and not blocks)
    if trigger and not families:
        gaps.append("Hype-Spur: Ausloeser ohne Social-Familie (Reddit, StockTwits, X) -- nur Messung")
    return {"score": None, "score_components": {}, "score_weights": {},
            "rules_version": REVISION,
            "hype": hype, "checks": checks, "earnings": earnings,
            "discovery_allowed": True,
            "qualification_status": "HYPE_QUALIFIED" if eligible else "EVIDENCE_PENDING",
            "eligible": eligible, "missing": list(dict.fromkeys(gaps)),
            "blocks": list(dict.fromkeys(blocks)),
            "state": "HYPE_KANDIDAT" if eligible else "AUSLOESER" if trigger else "BEOBACHTUNG",
            "score_reason": "Hype-Spur: Ausloeser + zwei von drei Bestaetigungen statt Belegscore; "
                            "kein Punktwert und keine Gewinnwahrscheinlichkeit"}


def valid_financials(sec, symbol, now):
    """No USD scores from unknown units, periods or instrument identities."""
    failure = "SEC-Finanzbeleg: Identitaet, USD-Einheit oder passende aktuelle Berichtsperiode fehlt"
    if not isinstance(sec, dict) or sec.get("symbol") != symbol or sec.get("symbol_identity_verified") is not True:
        return False, failure
    try:
        if int(sec.get("cik") or 0) <= 0:
            return False, failure
        profit, cashflow = sec.get("net_income") or {}, sec.get("operating_cashflow") or {}
        for fact in (profit, cashflow):
            if fact.get("unit") != "USD" or type(fact.get("value")) not in (int, float) or not math.isfinite(fact["value"]):
                return False, failure
            start, end, filed = [datetime.strptime(str(fact.get(k) or ""), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                                 for k in ("start", "end", "filed")]
            if not (0 < (end-start).days <= 371 and start <= end <= filed
                    and 0 <= now-filed.timestamp() <= 550*86400 and 0 <= now-end.timestamp() <= 550*86400):
                return False, failure
        if any(profit[k] != cashflow[k] for k in ("start", "end", "unit")):
            return False, "SEC-Finanzbeleg: Gewinn und Cashflow beziehen sich auf unterschiedliche Perioden"
    except (TypeError, ValueError, KeyError, OverflowError):
        return False, failure
    return True, "SEC-Zuordnung und gemeinsame USD-Berichtsperiode " + profit["start"] + " bis " + profit["end"] + " belegt"
