"""Smoke test for Tushare daily crawl -> process -> watermark."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_ROOT))

from market_trading.tushare_stockdaily.tushare_stockdaily import TushareStockDailyCrawler


FIELDS = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close", "change", "pct_chg", "vol", "amount"]
ITEM = ["600519.SH", "20260710", 1680.0, 1695.5, 1675.2, 1688.3, 1670.0, 18.3, 1.0958, 21345.0, 359872.1]


class TushareStockDailySmokeTest(unittest.TestCase):
    def test_three_step_flow_idempotency_and_raw_retry(self):
        temporary = tempfile.mkdtemp()
        try:
            requested = []

            def fake_request(payload):
                requested.append(payload)
                return {"code": 0, "msg": "", "data": {"fields": FIELDS, "items": [ITEM]}}

            config = {"root": temporary, "http_timeout_seconds": 1}
            fixed_now = lambda: datetime(2026, 7, 14, 16, 0, 0)
            crawler = TushareStockDailyCrawler(
                ["600519.SH"], "2026-07-10", config, requester=fake_request, now=fixed_now, token="test-token"
            )
            doc_ids = crawler.crawl_data()
            crawler.process_data(doc_ids)
            crawler.write_watermarks()

            self.assertEqual(len(doc_ids), 1)
            doc_id = doc_ids[0]
            raw = next(Path(temporary).glob(f"data/raw/**/{doc_id}__v1.json"))
            processed = next(Path(temporary).glob(f"data/processed/**/{doc_id}__v1.md"))
            self.assertEqual(raw.parent.name, "2026-07-10")
            self.assertEqual(processed.parent.name, "2026-07-10")
            state_path = Path(temporary) / "data/state/watermarks/market_trading/tushare_stockdaily.json"
            payload = json.loads(raw.read_text(encoding="utf-8"))
            source_bytes = TushareStockDailyCrawler._source_bytes(payload)
            digest = hashlib.sha256(source_bytes).hexdigest()
            self.assertNotIn("test-token", raw.read_text(encoding="utf-8"))
            self.assertIn(f'content_hash: "{digest}"', processed.read_text(encoding="utf-8"))
            self.assertEqual(json.loads(state_path.read_text(encoding="utf-8"))["last_event_date"], "2026-07-10")
            self.assertEqual(requested[0]["api_name"], "daily")

            rerun = TushareStockDailyCrawler(
                ["600519.SH"], "2026-07-10", config, requester=fake_request, now=fixed_now, token="test-token"
            )
            self.assertEqual(rerun.crawl_data(), [])
            processed.unlink()
            rerun.process_data([doc_id])
            rerun.write_watermarks()
            self.assertEqual(len(list(Path(temporary).glob(f"data/raw/**/{doc_id}__v*.json"))), 1)
            self.assertTrue(processed.exists())
            self.assertEqual(requested[-1]["params"]["trade_date"], "20260710")
        finally:
            logging.shutdown()
            shutil.rmtree(temporary)


if __name__ == "__main__":
    unittest.main()
