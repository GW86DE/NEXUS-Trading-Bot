"""10.1.10: Quellen-Reparaturen ohne Sicherheitslockerung.

GDELT bekommt einen eigenen, laengeren Timeout (die Quelle antwortete mit
HTTP 200 nach 13-14 s und wurde vom globalen 10-s-Timeout faelschlich fuer
tot erklaert). Tradestie wechselt auf die Hauptdomain mit gueltigem
TLS-Zertifikat; die Zertifikatspruefung selbst bleibt unveraendert aktiv.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NewsSourceFixes(unittest.TestCase):
    def test_gdelt_timeout_is_configured_and_used(self):
        config_text = (ROOT / "config.py").read_text(encoding="utf-8")
        found = re.search(r"NEWS_SOURCE_GDELT_TIMEOUT_SECONDS\s*=\s*(\d+)", config_text)
        self.assertIsNotNone(found, "GDELT-Timeout fehlt in config.py")
        self.assertGreaterEqual(int(found.group(1)), 20)
        news_text = (ROOT / "news_sources.py").read_text(encoding="utf-8")
        self.assertIn("NEWS_SOURCE_GDELT_TIMEOUT_SECONDS", news_text)
        self.assertIn("_source_timeout", news_text)
        self.assertIn("timeout=self._source_timeout(source)", news_text)

    def test_gdelt_timeout_never_shortens_the_global_timeout(self):
        news_text = (ROOT / "news_sources.py").read_text(encoding="utf-8")
        self.assertIn("max(self.timeout", news_text,
                      "GDELT-Timeout muss den globalen Timeout als Untergrenze behalten")

    def test_tradestie_uses_valid_certificate_host(self):
        research_text = (ROOT / "pulsar" / "research.py").read_text(encoding="utf-8")
        self.assertIn('"tradestie": "https://tradestie.com/api/v1/apps/reddit"', research_text)
        self.assertNotIn('"tradestie": "https://api.tradestie.com', research_text)

    def test_no_tls_verification_bypass_was_introduced(self):
        for name in ("news_sources.py", "pulsar/research.py", "public_news_http.py"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn("verify=False", text, name)
            self.assertNotIn("_create_unverified_context", text, name)


if __name__ == "__main__":
    unittest.main()
