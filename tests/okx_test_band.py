"""Explicit price-band endpoint for pre-band synthetic execution fixtures."""
import time


class DemoWithoutPriceBand:
    demo = True

    @staticmethod
    def price_limit(inst_id):
        return [dict(instId=inst_id, enabled=False, buyLmt='', sellLmt='',
                     ts=str(int(time.time()*1000)))]
