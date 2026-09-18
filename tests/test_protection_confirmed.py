import unittest
from position_manager import protection_result_confirmed

class ProtectionConfirmationTests(unittest.TestCase):
    def test_human_text_cannot_enable_management(self):
        self.assertFalse(protection_result_confirmed({"checked":True,"detail":"Schutz vorhanden"}))
        self.assertFalse(protection_result_confirmed({"checked":True,"detail":"Schutzorders vollständig"}))
        self.assertTrue(protection_result_confirmed({"checked":True,"protection_confirmed":True,"detail":"beliebiger Text"}))

if __name__ == "__main__": unittest.main()
