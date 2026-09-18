import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
import config
import ai_attention
import ai_router

class Resp:
    ok=True; status_code=200; content=b'1'; text=''
    def json(self):
        return {"output_text":json.dumps({"ordered_keys":["stock:AAPL","stock:TSLA","crypto:BTC"],"reason":"news priority"}),
                "usage":{"input_tokens":10,"output_tokens":5},"output":[]}

class AIAttentionTests(unittest.TestCase):
    def test_unknown_ticker_is_discarded_and_no_signal_is_returned(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(config,'AI_ATTENTION_ENABLED',True), patch.object(config,'OPENAI_API_KEY','x'), \
             patch.object(config,'AI_ROUTER_ENABLED',True), \
             patch.object(config,'AI_ATTENTION_WEB_SEARCH',False), patch.object(config,'AI_ATTENTION_MAX_CALLS_PER_DAY',50), \
             patch.object(ai_router,'STATE_ROOT',Path(td)), patch('ai_router.requests.post',return_value=Resp()), \
             patch('ai_control.read_mode',return_value='AUTO'):
            ai=ai_attention.AIAttentionPrioritizer()
            universe=[SimpleNamespace(name='AAPL',asset_type='stock',sector='tech'),SimpleNamespace(name='BTC',asset_type='crypto',sector='crypto')]
            result=ai.prioritize(universe,max_items=10)
            self.assertEqual(result.ordered_keys,['stock:AAPL','crypto:BTC'])
            self.assertNotIn('stock:TSLA',result.ordered_keys)
            self.assertFalse(hasattr(result,'decision'))

    def test_failure_changes_only_ordering_feature(self):
        with patch.object(config,'AI_ATTENTION_ENABLED',True), patch.object(config,'OPENAI_API_KEY','x'), \
             patch.object(config,'AI_ROUTER_ENABLED',True), patch.object(config,'AI_ATTENTION_WEB_SEARCH',False), \
             patch('ai_router.requests.post',side_effect=RuntimeError('offline')), \
             patch('ai_control.read_mode',return_value='ON'):
            ai=ai_attention.AIAttentionPrioritizer()
            result=ai.prioritize([SimpleNamespace(name='AAPL',asset_type='stock',sector='tech')])
            self.assertEqual(result.ordered_keys,[])

if __name__ == "__main__": unittest.main()
