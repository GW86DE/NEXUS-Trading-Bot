import unittest
from instrument_identity import canonical_key, etoro_market_symbol_matches, same_instrument

class InstrumentIdentityTests(unittest.TestCase):
    def test_flr_stock_and_crypto_have_different_identity(self):
        self.assertEqual(canonical_key("FLR.US","stock"),"stock:FLR")
        self.assertEqual(canonical_key("FLR","crypto"),"crypto:FLR")
        self.assertNotEqual(canonical_key("FLR.US","stock"),canonical_key("FLR","crypto"))
        self.assertFalse(same_instrument("FLR.US","stock","FLR","crypto"))

    def test_crypto_flr_never_matches_us_stock(self):
        self.assertFalse(etoro_market_symbol_matches("FLR","crypto","FLR.US"))
        self.assertTrue(etoro_market_symbol_matches("FLR","crypto","FLR"))
        self.assertTrue(etoro_market_symbol_matches("FLR","stock","FLR.US"))

    def test_stock_suffix_cannot_be_normalized_as_crypto(self):
        with self.assertRaises(ValueError):
            canonical_key("FLR.US","crypto")

if __name__ == "__main__": unittest.main()
