"""Einmaliger, konservativer Upgrade-Check fuer v5.2.0-Test-P&L."""
from pathlib import Path
from risk_state_sanity import quarantine_known_test_state

p = Path(__file__).with_name("risk_state.json")
backup = quarantine_known_test_state(p)
if backup:
    print("HINWEIS: Eindeutige v5.2.0-Test-P&L erkannt.")
    print("Der Testzustand wurde NICHT als echter Gewinn/Verlust uebernommen.")
    print("Backup:", backup.name)
else:
    print("RiskState-Pruefung: keine bekannte v5.2.0-Testkontamination gefunden.")
