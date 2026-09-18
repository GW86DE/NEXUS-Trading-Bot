"""
Handelsuniversum: Kernwerte, Underdogs und Kryptowaehrungen.

AUFBAU
======
  KERN     190 grosse/mittelgrosse, liquide Werte (175 US + 15 Europa)
  UNDERDOGS 25 kleinere Werte -- NUR Kandidaten, kein Freibrief
  KRYPTO    16 Coins

WICHTIG ZU DEN UNDERDOGS
=========================
Die Liste unten ist eine KANDIDATENLISTE, keine Empfehlung und keine
Aussage darueber, dass diese Firmen gute Zahlen liefern. Ob ein Wert
tatsaechlich handelbar ist, entscheidet das Kennzahlen-Screening
(underdog_screening.py) anhand AKTUELLER Daten:

  - Umsatzwachstum
  - Profitabilitaet (positive Marge / positiver Gewinn)
  - Verschuldung im Rahmen
  - ausreichende Liquiditaet (Handelsvolumen)
  - Marktkapitalisierung im Zielbereich

Nur Werte, die dieses Screening bestehen, werden vom Bot ueberhaupt
gekauft. Damit beruht "gute Zahlen" auf gemessenen Daten und nicht auf
einer Behauptung.

WARUM UNDERDOGS RISKANTER SIND -- unabhaengig von den Zahlen:
  - duennere Handelsvolumen -> groessere Spannen, schlechtere Ausfuehrung
  - staerkere Kursausschlaege, haeufigere Kurslucken ueber Nacht
  - weniger Analystenabdeckung -> Nachrichten schlagen haerter durch
  - hoehere Insolvenzgefahr in Abschwungphasen
Deshalb bekommen sie in allen Risiko-Profilen kleinere Positionen als
Kernwerte (siehe UNDERDOG_SIZE_FACTOR in config.py).
"""

# ---------------------------------------------------------------------------
# KERNWERTE -- gross, liquide, breit gestreut
# ---------------------------------------------------------------------------

US_KERN = [
    # Technologie / Halbleiter
    ("AAPL", "Technologie"), ("MSFT", "Software"), ("NVDA", "Halbleiter"),
    ("GOOGL", "Internet"), ("AMZN", "Handel"), ("META", "Internet"),
    ("AVGO", "Halbleiter"), ("AMD", "Halbleiter"), ("QCOM", "Halbleiter"),
    ("TXN", "Halbleiter"), ("MU", "Halbleiter"),
    ("ADBE", "Software"), ("CRM", "Software"), ("ORCL", "Software"),
    ("NOW", "Software"), ("INTU", "Software"), ("IBM", "IT-Dienste"),
    ("ACN", "IT-Dienste"), ("CSCO", "Netzwerk"),
    # Gesundheit
    ("JNJ", "Pharma"), ("LLY", "Pharma"), ("ABBV", "Pharma"),
    ("MRK", "Pharma"), ("PFE", "Pharma"), ("TMO", "Medizintechnik"),
    ("ABT", "Medizintechnik"), ("DHR", "Medizintechnik"),
    ("UNH", "Versicherung"), ("AMGN", "Biotech"),
    # Finanzen
    ("JPM", "Banken"), ("BAC", "Banken"), ("WFC", "Banken"),
    ("GS", "Banken"), ("MS", "Banken"), ("BLK", "Vermoegen"),
    ("SPGI", "Finanzdaten"), ("V", "Zahlungen"), ("MA", "Zahlungen"),
    ("AXP", "Zahlungen"),
    # Konsum
    ("PG", "Konsumgueter"), ("KO", "Getraenke"), ("PEP", "Getraenke"),
    ("COST", "Handel"), ("WMT", "Handel"), ("HD", "Handel"),
    ("MCD", "Gastronomie"), ("NKE", "Bekleidung"),
    # Industrie / Energie / Rohstoffe
    ("CAT", "Maschinenbau"), ("DE", "Maschinenbau"), ("HON", "Industrie"),
    ("GE", "Industrie"), ("BA", "Luftfahrt"), ("LMT", "Ruestung"),
    ("RTX", "Ruestung"), ("UNP", "Transport"), ("XOM", "Energie"), ("CVX", "Energie"), ("COP", "Energie"),
    ("NEE", "Versorger"), ("LIN", "Chemie"),
]

# v5.2: Erweiterung auf 175 US-Kernwerte – bewusst über Branchen verteilt.
US_KERN += [
    # Technologie / Halbleiter
    ("AMAT", "Halbleiter"),
    ("LRCX", "Halbleiter"),
    ("KLAC", "Halbleiter"),
    ("ADI", "Halbleiter"),
    ("MCHP", "Halbleiter"),
    ("NXPI", "Halbleiter"),
    ("ON", "Halbleiter"),
    ("MRVL", "Halbleiter"),
    ("CDNS", "Software"),
    ("SNPS", "Software"),
    ("ANET", "Netzwerk"),
    ("PANW", "Cybersecurity"),
    ("CRWD", "Cybersecurity"),
    ("FTNT", "Cybersecurity"),
    ("DDOG", "Software"),
    ("NET", "Internet"),
    ("PLTR", "Software"),
    ("UBER", "Mobilitaet"),
    ("SHOP", "Handel"),
    ("ABNB", "Reisen"),
    ("DELL", "Hardware"),
    ("HPQ", "Hardware"),
    # Gesundheit
    ("GILD", "Biotech"),
    ("VRTX", "Biotech"),
    ("REGN", "Biotech"),
    ("ISRG", "Medizintechnik"),
    ("SYK", "Medizintechnik"),
    ("BSX", "Medizintechnik"),
    ("MDT", "Medizintechnik"),
    ("ZTS", "Tiergesundheit"),
    ("CI", "Versicherung"),
    ("ELV", "Versicherung"),
    ("CVS", "Gesundheit"),
    ("BMY", "Pharma"),
    ("HCA", "Kliniken"),
    ("DXCM", "Medizintechnik"),
    ("IDXX", "Diagnostik"),
    ("BDX", "Medizintechnik"),
    ("EW", "Medizintechnik"),
    # Finanzen
    ("C", "Banken"),
    ("SCHW", "Broker"),
    ("CB", "Versicherung"),
    ("MRSH", "Versicherung"),
    ("AON", "Versicherung"),
    ("CME", "Boerse"),
    ("ICE", "Boerse"),
    ("COF", "Banken"),
    ("PNC", "Banken"),
    ("TFC", "Banken"),
    ("USB", "Banken"),
    ("MCO", "Finanzdaten"),
    ("AJG", "Versicherung"),
    ("APO", "Alternative Assets"),
    ("BX", "Alternative Assets"),
    # Konsum
    ("TGT", "Handel"),
    ("LOW", "Handel"),
    ("TJX", "Handel"),
    ("ROST", "Handel"),
    ("ORLY", "Automobil"),
    ("AZO", "Automobil"),
    ("SBUX", "Gastronomie"),
    ("CMG", "Gastronomie"),
    ("MAR", "Reisen"),
    ("BKNG", "Reisen"),
    ("HLT", "Reisen"),
    ("LULU", "Bekleidung"),
    ("EL", "Konsumgueter"),
    ("CL", "Konsumgueter"),
    ("MDLZ", "Konsumgueter"),
    ("PM", "Tabak"),
    ("MO", "Tabak"),
    # Industrie
    ("ETN", "Industrie"),
    ("EMR", "Industrie"),
    ("ITW", "Industrie"),
    ("GD", "Ruestung"),
    ("NOC", "Ruestung"),
    ("MMM", "Industrie"),
    ("PH", "Industrie"),
    ("WM", "Entsorgung"),
    ("RSG", "Entsorgung"),
    ("FDX", "Transport"),
    ("UPS", "Transport"),
    ("CSX", "Transport"),
    ("NSC", "Transport"),
    ("URI", "Maschinenbau"),
    ("PCAR", "Maschinenbau"),
    ("CMI", "Maschinenbau"),
    # Energie / Rohstoffe / Versorger
    ("EOG", "Energie"),
    ("SLB", "Energie"),
    ("MPC", "Energie"),
    ("PSX", "Energie"),
    ("OXY", "Energie"),
    ("VLO", "Energie"),
    ("KMI", "Energie"),
    ("FCX", "Rohstoffe"),
    ("NEM", "Rohstoffe"),
    ("APD", "Chemie"),
    ("SHW", "Chemie"),
    ("DUK", "Versorger"),
    ("SO", "Versorger"),
    # Zusätzliche breite Streuung: 175 US-Kernwerte + 25 US-Underdogs
    # ergeben genau 200 US-Aktienkandidaten.
    ("FICO", "Finanzdaten"),
    ("CPRT", "Automobil"),
    ("CARR", "Industrie"),
    ("ROP", "Industrie"),
    ("FAST", "Industrie"),
    ("GWW", "Industrie"),
    ("WMB", "Energie"),
    ("EQT", "Energie"),
    ("TROW", "Vermoegen"),
    ("MET", "Versicherung"),
    ("PRU", "Versicherung"),
    ("KHC", "Konsumgueter"),
    ("GIS", "Konsumgueter"),
    ("KR", "Handel"),
    ("ECL", "Chemie"),
]

EU_KERN = [
    ("SAP", "Software", "EUR"), ("SIE", "Industrie", "EUR"),
    ("ALV", "Versicherung", "EUR"), ("MUV2", "Rueckversicherung", "EUR"),
    ("BAS", "Chemie", "EUR"), ("BAYN", "Pharma", "EUR"),
    ("DTE", "Telekom", "EUR"), ("RWE", "Versorger", "EUR"),
    ("ASML", "Halbleiter", "EUR"), ("MC", "Luxus", "EUR"),
    ("OR", "Konsumgueter", "EUR"), ("AIR", "Luftfahrt", "EUR"),
    ("TTE", "Energie", "EUR"), ("SU", "Industrie", "EUR"),
    ("ITX", "Bekleidung", "EUR"),
]

# ---------------------------------------------------------------------------
# UNDERDOG-KANDIDATEN -- muessen das Screening bestehen (siehe Kopf)
# ---------------------------------------------------------------------------
# Bewusst gemischt: gewachsene profitable Nischenanbieter, zyklische Werte
# und einige jüngere Wachstumsfirmen. Die Auswahl ist ein Ausgangspunkt zum
# PRUEFEN, keine Aussage ueber Qualitaet -- das entscheidet das Screening
# anhand aktueller Zahlen.
UNDERDOG_KANDIDATEN = [
    ("CROX", "Bekleidung"),      # Schuhe
    ("DECK", "Bekleidung"),      # Schuhe
    ("SIG", "Einzelhandel"),     # Schmuck
    ("PENN", "Freizeit"),        # Gluecksspiel
    ("CAVA", "Gastronomie"),     # Restaurant
    ("WING", "Gastronomie"),     # Restaurant
    ("TXRH", "Gastronomie"),     # Restaurant
    ("HIMS", "Gesundheit"),      # Telemedizin
    ("MD", "Gesundheit"),        # Kliniken
    ("EVER", "Versicherung"),    # Versicherungsmarktplatz
    ("DUOL", "Software"),        # Bildung
    ("APPF", "Software"),        # Immobiliensoftware
    ("PCTY", "Software"),        # Personalsoftware
    ("EXLS", "IT-Dienste"),      # Analytik
    ("ONTO", "Halbleiter"),      # Messtechnik
    ("FORM", "Halbleiter"),      # Testtechnik
    ("POWI", "Halbleiter"),      # Leistungshalbleiter
    ("RMBS", "Halbleiter"),      # Chip-Technologie
    ("CRC", "Energie"),          # Oel und Gas
    ("MTDR", "Energie"),         # Oel und Gas
    ("ORN", "Bau"),              # Infrastruktur
    ("STRL", "Bau"),             # Infrastruktur
    ("SMP", "Automobil"),        # Ersatzteile
    ("BCC", "Baustoffe"),        # Holzprodukte
    ("SKYW", "Luftfahrt"),       # Regionalfluggesellschaft
]

# ---------------------------------------------------------------------------
# KRYPTO
# ---------------------------------------------------------------------------
# Verfuegbarkeit unterscheidet sich je Broker und Region -- nicht
# handelbare Werte werden beim Start automatisch aussortiert.
KRYPTO = [
    "BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "DOT", "LINK",
    "LTC", "BCH", "UNI", "AAVE", "XTZ", "ALGO", "SHIB", "DOGE",
]


# ---------------------------------------------------------------------------
# Aufbereitung fuer config.py
# ---------------------------------------------------------------------------

def us_aktien():
    return [{"symbol": s, "exchange": "SMART", "currency": "USD",
             "sector": b, "gruppe": "kern"} for s, b in US_KERN]


def eu_aktien():
    return [{"symbol": s, "exchange": "SMART", "currency": w,
             "sector": b, "gruppe": "kern"} for s, b, w in EU_KERN]


def underdogs():
    return [{"symbol": s, "exchange": "SMART", "currency": "USD",
             "sector": b, "gruppe": "underdog"} for s, b in UNDERDOG_KANDIDATEN]


def krypto(exchange="OKX"):
    return [{"symbol": s, "exchange": exchange, "currency": "USD",
             "sector": "Krypto", "gruppe": "krypto"} for s in KRYPTO]


def alle_aktien():
    return us_aktien() + eu_aktien() + underdogs()


def uebersicht() -> str:
    return (f"Kernwerte : {len(US_KERN)} US + {len(EU_KERN)} EU = "
            f"{len(US_KERN) + len(EU_KERN)}\n"
            f"Underdogs : {len(UNDERDOG_KANDIDATEN)} Kandidaten (Screening entscheidet)\n"
            f"Aktien    : {len(US_KERN) + len(EU_KERN) + len(UNDERDOG_KANDIDATEN)}\n"
            f"Krypto    : {len(KRYPTO)}")


if __name__ == "__main__":
    print(uebersicht())
