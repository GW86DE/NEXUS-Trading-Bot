import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import notifier
import config

class TelegramTradeQueueTests(unittest.TestCase):
    def test_trade_is_persisted_before_failed_delivery(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.dict('os.environ',{'TRADINGBOT_TEST_STATE_DIR':td}), \
             patch.object(config,'NOTIFY_TELEGRAM',True), \
             patch.object(config,'TELEGRAM_BOT_TOKEN','token'), \
             patch.object(config,'TELEGRAM_CHAT_ID','123'), \
             patch.object(notifier,'_send_one',return_value=(False,'NETWORK test',5.0)):
            # Force no stale queue from another test and prove the RAM bundle is unused.
            with notifier._TRADE_BUFFER_LOCK: notifier._TRADE_BUFFER.clear()
            notifier.notify_trade('KAUF','KAUF FLR.US bestaetigt · Position 3584875792',bundle=True)
            path=Path(td)/getattr(config,'TELEGRAM_QUEUE_FILE','telegram_queue.json')
            self.assertTrue(path.exists())
            state=json.loads(path.read_text())
            self.assertEqual(len(state.get('queue',[])),1)
            self.assertIn('FLR.US',state['queue'][0]['message'])
            self.assertEqual(state['queue'][0]['priority'],'critical')
            self.assertEqual(notifier.pending_trade_count(),0)

    def test_trade_stays_persisted_when_credentials_are_temporarily_missing(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.dict('os.environ', {'TRADINGBOT_TEST_STATE_DIR': td}), \
             patch.object(config, 'NOTIFY_TELEGRAM', True), \
             patch.object(config, 'TELEGRAM_BOT_TOKEN', ''), \
             patch.object(config, 'TELEGRAM_CHAT_ID', ''):
            ok = notifier.send_telegram('KAUF FLR.US bestaetigt', priority='critical', queue_on_fail=True)
            self.assertFalse(ok)
            path = Path(td) / getattr(config, 'TELEGRAM_QUEUE_FILE', 'telegram_queue.json')
            self.assertTrue(path.exists())
            state = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(len(state.get('queue', [])), 1)
            self.assertIn('FLR.US', state['queue'][0]['message'])
            self.assertEqual(state['queue'][0]['priority'], 'critical')

if __name__ == "__main__": unittest.main()
