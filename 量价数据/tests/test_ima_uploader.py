"""Smoke test for IMA duplicate check -> COS upload -> add knowledge."""

from __future__ import annotations

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

from utils.ima_uploader import IMAUploader


class IMAUploaderSmokeTest(unittest.TestCase):
    def test_upload_and_state_based_idempotency(self):
        temporary = tempfile.mkdtemp()
        try:
            root = Path(temporary)
            processed = root / "data/processed/market_trading/tushare_stockdaily/600519.SH/2026-07-13"
            processed.mkdir(parents=True)
            document = processed / "px_example__v1.md"
            document.write_text("---\nts_code: 600519.SH\nevent_date: 2026-07-13\n---\n\n行情。\n", encoding="utf-8")
            calls, cos_calls, created_folders = [], [], []

            def fake_api(endpoint, payload):
                calls.append((endpoint, payload))
                if endpoint == "get_knowledge_list":
                    return {"knowledge_list": [], "is_end": True, "next_cursor": ""}
                if endpoint == "create_folder":
                    folder_id = f"folder_{len(created_folders) + 1}"
                    created_folders.append((payload["name"], payload.get("folder_id"), folder_id))
                    return {"media_id": folder_id}
                if endpoint == "check_repeated_names":
                    return {"results": [{"name": payload["params"][0]["name"], "is_repeated": False}]}
                if endpoint == "create_media":
                    return {
                        "media_id": "media_test",
                        "cos_credential": {
                            "secret_id": "temporary-id",
                            "secret_key": "temporary-key",
                            "token": "temporary-token",
                            "bucket_name": "bucket-123",
                            "region": "ap-test",
                            "cos_key": "upload/test.md",
                        },
                    }
                if endpoint == "add_knowledge":
                    return {"media_id": payload["media_id"]}
                raise AssertionError(endpoint)

            def fake_cos(path, credential, content_type):
                cos_calls.append((path, credential, content_type))

            uploader = IMAUploader(
                root=root,
                client_id="client-test",
                api_key="api-test",
                knowledge_base_id="kb-test",
                api_caller=fake_api,
                cos_uploader=fake_cos,
                now=lambda: datetime(2026, 7, 14, 17, 0, 0),
            )
            first = uploader.upload_tree(root / "data/processed/market_trading/tushare_stockdaily", "tushare_stockdaily")
            self.assertEqual((first["uploaded"], first["skipped"], first["failed"]), (1, 0, 0))
            self.assertEqual(
                [item[0] for item in created_folders],
                ["tushare_stockdaily", "600519.SH", "2026-07-13"],
            )
            self.assertEqual(created_folders[0][1], None)
            self.assertEqual(created_folders[1][1], "folder_1")
            self.assertEqual(created_folders[2][1], "folder_2")
            self.assertEqual(calls[-1][1]["folder_id"], "folder_3")
            self.assertEqual(len(cos_calls), 1)

            state_path = root / "data/state/uploads/ima/tushare_stockdaily.json"
            state_text = state_path.read_text(encoding="utf-8")
            self.assertNotIn("api-test", state_text)
            self.assertNotIn("temporary-key", state_text)
            second = uploader.upload_tree(root / "data/processed/market_trading/tushare_stockdaily", "tushare_stockdaily")
            self.assertEqual((second["uploaded"], second["skipped"]), (0, 1))
            self.assertEqual(len(calls), 9)
        finally:
            logging.shutdown()
            shutil.rmtree(temporary)


if __name__ == "__main__":
    unittest.main()
