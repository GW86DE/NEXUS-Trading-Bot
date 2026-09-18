import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import config
from risk_manager import RiskState, can_open_new_position

class EquityGuardTests(unittest.TestCase):
    def test_unrealized_equity_loss_blocks_only_new_entries(self):
        with tempfile.TemporaryDirectory() as td, patch.object(config,'RISK_STATE_FILE',str(Path(td)/'risk.json')), patch.object(config,'MAX_UNREALIZED_DAILY_LOSS_PCT',0.03):
            r=RiskState(); r.update_equity_guard(10000)
            self.assertFalse(r.equity_guard_halted)
            r.update_equity_guard(9699)
            self.assertTrue(r.equity_guard_halted)
            self.assertFalse(r.trading_halted)
            self.assertFalse(can_open_new_position(r))

if __name__ == "__main__": unittest.main()
