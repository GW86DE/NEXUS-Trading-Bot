"""Bounded discovery sources. A selected account is not a verified statement.

References document why an account was selected; the API query alone does not
prove the returned author's current identity. No paid user lookup is required.
"""
REGISTRY_VERSION = "2026-09-14"
ACCOUNTS = (
    {"handle": "realDonaldTrump", "group": "POLICY", "role": "POLITICAL",
     "reference": "https://x.com/realDonaldTrump", "selection": "USER_REQUESTED"},
    {"handle": "WhiteHouse", "group": "POLICY", "role": "GOVERNMENT",
     "reference": "https://www.whitehouse.gov/", "selection": "OFFICIAL_PUBLIC_REFERENCE"},
    {"handle": "POTUS", "group": "POLICY", "role": "GOVERNMENT",
     "reference": "https://x.com/POTUS", "selection": "OFFICIAL_PUBLIC_REFERENCE"},
    {"handle": "USTreasury", "group": "FINANCIAL", "role": "GOVERNMENT",
     "reference": "https://home.treasury.gov/", "selection": "OFFICIAL_PUBLIC_REFERENCE"},
    {"handle": "federalreserve", "group": "FINANCIAL", "role": "CENTRAL_BANK",
     "reference": "https://www.federalreserve.gov/newsevents/pressreleases/other20120314a.htm",
     "selection": "OFFICIAL_PUBLIC_REFERENCE"},
    {"handle": "SECGov", "group": "FINANCIAL", "role": "REGULATOR",
     "reference": "https://www.sec.gov/newsroom/social-media", "selection": "OFFICIAL_PUBLIC_REFERENCE"},
)

# This query can discover companies outside the current NEXUS universe. Its ten
# results are a sample, not a complete market scan or evidence of unusual volume.
DISCOVERY_QUERY = '(earnings OR revenue OR guidance OR "FDA approval" OR acquisition OR "contract award") lang:en -is:retweet'


def search_plan(slot, custom_accounts=(), dynamic_account_ids=()):
    mode = slot % 3
    if mode == 2:
        return DISCOVERY_QUERY, "__STOCK_DISCOVERY__"
    group = "POLICY" if mode == 0 else "FINANCIAL"
    handles = [a["handle"] for a in ACCOUNTS if a["group"] == group]
    if mode == 0:
        handles = list(dict.fromkeys(handles + list(custom_accounts)))
    sources = ["from:" + h for h in handles]
    if mode == 1:
        # Vom Nutzer aktivierte dynamische Registry-Konten (numerische IDs aus
        # der beobachtungsbasierten Registry) laufen im Finanz-Slot mit. Der
        # from:-Operator akzeptiert Konto-IDs; es wird kein Profil abgerufen
        # und keine zusaetzliche Anfrage erzeugt.
        sources += ["from:" + str(i) for i in list(dynamic_account_ids)[:5]
                    if str(i).isdigit()]
    sources = list(dict.fromkeys(sources))
    return "(" + " OR ".join(sources) + ") lang:en -is:retweet", "__" + group + "_SOURCES__"


def public_registry():
    return [{**row, "selection_checked_at": REGISTRY_VERSION,
             "api_identity_verified": False, "statement_confirmed": False} for row in ACCOUNTS]
