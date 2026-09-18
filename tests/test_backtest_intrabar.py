import unittest
from backtest import intrabar_exit

class IntrabarTests(unittest.TestCase):
    def test_stop_detected_even_when_close_recovers(self):
        reason,price=intrabar_exit(100,94,104,95,110)
        self.assertEqual(reason,'stop_loss'); self.assertEqual(price,95)

    def test_both_hit_same_bar_uses_conservative_stop_first(self):
        reason,price=intrabar_exit(100,94,112,95,110)
        self.assertEqual((reason,price),('stop_loss',95))

    def test_gap_below_stop_uses_open(self):
        reason,price=intrabar_exit(92,90,101,95,110)
        self.assertEqual((reason,price),('stop_loss',92))

if __name__ == "__main__": unittest.main()
