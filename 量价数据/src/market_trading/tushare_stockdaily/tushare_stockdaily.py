"""Crawl Tushare Pro A-share unadjusted daily bars."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

SRC_ROOT = Path(__file__).resolve().parents[2]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from utils.config import load_config
from utils.logger import get_logger


class TushareStockDailyCrawler:
    DOC_PREFIX = "px"
    CATEGORY = "market_trading"
    SOURCE = "tushare_stockdaily"
    ENDPOINT = "https://api.tushare.pro"
    FIELDS = (
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    )

    def __init__(
        self,
        universe: list[str],
        today_date: str,
        config: dict,
        requester: Callable[[dict], dict] | None = None,
        now: Callable[[], datetime] | None = None,
        token: str | None = None,
    ):
        if not universe:
            raise ValueError("universe cannot be empty")
        self.universe = [code.upper() for code in universe]
        for code in self.universe:
            if not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
                raise ValueError(f"unsupported ts_code: {code}")
        self.today_date = self._compact_date(today_date)
        self.config = config
        self.root = Path(config["root"])
        self.requester = requester or self._request
        self.now = now or datetime.now
        self._token = token
        self.logger = get_logger(self.CATEGORY, self.SOURCE, config)
        self._run_records: dict[str, dict] = {}

    @staticmethod
    def _compact_date(value: str) -> str:
        compact = value.replace("-", "")
        datetime.strptime(compact, "%Y%m%d")
        return compact

    @staticmethod
    def _event_date(value: str) -> str:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()

    @classmethod
    def _doc_id(cls, natural_key: str) -> str:
        digest = hashlib.sha256(f"{cls.SOURCE}:{natural_key}".encode()).hexdigest()[:8]
        return f"{cls.DOC_PREFIX}_{digest}"

    @classmethod
    def _source_bytes(cls, row: dict) -> bytes:
        source_row = {field: row.get(field) for field in cls.FIELDS}
        return json.dumps(source_row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _request(self, payload: dict) -> dict:
        request = urllib.request.Request(
            self.ENDPOINT,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "knowledge-base-pilot/1.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=int(self.config.get("http_timeout_seconds", 20))) as response:
            if response.status != 200:
                raise RuntimeError(f"HTTP {response.status} from Tushare")
            return json.load(response)

    def _watermark_path(self) -> Path:
        return self.root / "data" / "state" / "watermarks" / self.CATEGORY / f"{self.SOURCE}.json"

    def _read_watermark(self) -> dict | None:
        path = self._watermark_path()
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _partition_dir(self, kind: str, ts_code: str, event_date: str) -> Path:
        return self.root / "data" / kind / self.CATEGORY / self.SOURCE / ts_code / event_date

    @staticmethod
    def _version_from_path(path: Path) -> int:
        match = re.search(r"__v(\d+)\.", path.name)
        if not match:
            raise ValueError(f"invalid versioned filename: {path}")
        return int(match.group(1))

    @staticmethod
    def _existing_versions(directory: Path, doc_id: str) -> list[Path]:
        return sorted(directory.glob(f"{doc_id}__v*.json")) if directory.exists() else []

    def _request_rows(self, ts_code: str, today_date: str) -> list[dict]:
        token = self._token or os.environ.get("TUSHARE_TOKEN")
        if not token:
            raise RuntimeError("TUSHARE_TOKEN environment variable is required")
        payload = {
            "api_name": "daily",
            "token": token,
            "params": {"ts_code": ts_code, "trade_date": today_date},
            "fields": ",".join(self.FIELDS),
        }
        result = self.requester(payload)
        if result.get("code") != 0:
            raise RuntimeError(f"Tushare daily failed with code {result.get('code')}: {result.get('msg', '')}")
        data = result.get("data") or {}
        fields = data.get("fields") or []
        missing = sorted(set(self.FIELDS) - set(fields))
        if missing:
            raise RuntimeError(f"Tushare response missing fields: {', '.join(missing)}")
        rows = []
        for item in data.get("items") or []:
            if len(item) != len(fields):
                raise RuntimeError("Tushare response fields/items length mismatch")
            row = dict(zip(fields, item))
            if row["ts_code"] != ts_code:
                raise RuntimeError(f"Tushare returned unexpected ts_code {row['ts_code']} for {ts_code}")
            if row["trade_date"] != today_date:
                raise RuntimeError(f"Tushare returned unexpected trade_date {row['trade_date']} for {today_date}")
            rows.append(row)
        return rows

    def crawl_data(self) -> list[str]:
        fetched_at = self.now().replace(microsecond=0).isoformat()
        written: list[str] = []
        for ts_code in self.universe:
            self.logger.info("requesting %s for trade date %s", ts_code, self.today_date)
            rows = self._request_rows(ts_code, self.today_date)
            for row in sorted(rows, key=lambda value: value["trade_date"]):
                event_date = self._event_date(row["trade_date"])
                natural_key = f"{ts_code}_{row['trade_date']}"
                doc_id = self._doc_id(natural_key)
                content_hash = hashlib.sha256(self._source_bytes(row)).hexdigest()
                raw_dir = self._partition_dir("raw", ts_code, event_date)
                raw_dir.mkdir(parents=True, exist_ok=True)
                existing = self._existing_versions(raw_dir, doc_id)
                identical = None
                for path in existing:
                    old_row = json.loads(path.read_text(encoding="utf-8"))
                    if hashlib.sha256(self._source_bytes(old_row)).hexdigest() == content_hash:
                        identical = path
                        break
                if identical:
                    self.logger.info("unchanged %s v%d", doc_id, self._version_from_path(identical))
                    continue
                version = max((self._version_from_path(path) for path in existing), default=0) + 1
                raw_path = raw_dir / f"{doc_id}__v{version}.json"
                raw_payload = {field: row.get(field) for field in self.FIELDS}
                raw_payload["fetched_at"] = fetched_at
                raw_path.write_text(json.dumps(raw_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                self._run_records[doc_id] = {
                    "doc_id": doc_id,
                    "version": version,
                    "raw_path": raw_path,
                    "content_hash": content_hash,
                    "payload": raw_payload,
                }
                written.append(doc_id)
                self.logger.info("archived %s v%d for %s %s", doc_id, version, ts_code, event_date)
        return written

    def _load_latest_record(self, doc_id: str) -> dict:
        base = self.root / "data" / "raw" / self.CATEGORY / self.SOURCE
        candidates = list(base.glob(f"*/*/{doc_id}__v*.json"))
        if not candidates:
            raise ValueError(f"raw data not found for doc_id: {doc_id}")
        raw_path = max(candidates, key=self._version_from_path)
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        return {
            "doc_id": doc_id,
            "version": self._version_from_path(raw_path),
            "raw_path": raw_path,
            "content_hash": hashlib.sha256(self._source_bytes(payload)).hexdigest(),
            "payload": payload,
        }

    @staticmethod
    def _yaml_scalar(value) -> str:
        if value is None:
            return "null"
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _number(value, decimals: int = 2) -> str:
        return "无数据" if value is None else f"{float(value):,.{decimals}f}"

    def process_data(self, doc_ids: list[str]) -> None:
        for doc_id in doc_ids:
            item = self._run_records.get(doc_id) or self._load_latest_record(doc_id)
            self._run_records[doc_id] = item
            payload = item["payload"]
            event_date = self._event_date(payload["trade_date"])
            version = item["version"]
            processed_dir = self._partition_dir("processed", payload["ts_code"], event_date)
            processed_dir.mkdir(parents=True, exist_ok=True)
            output = processed_dir / f"{doc_id}__v{version}.md"
            if output.exists():
                self.logger.info("processed output already exists for %s v%d", doc_id, version)
                continue
            title = f"{payload['ts_code']} {event_date} 日线行情"
            metadata = {
                "doc_id": doc_id,
                "category": self.CATEGORY,
                "source": self.SOURCE,
                "ts_code": payload["ts_code"],
                "event_date": event_date,
                "crawl_time": payload["fetched_at"],
                "content_hash": item["content_hash"],
                "version": version,
                "title": title,
                "url": self.ENDPOINT,
            }
            frontmatter = "\n".join(f"{key}: {self._yaml_scalar(value)}" for key, value in metadata.items())
            body = (
                f"{event_date}，{payload['ts_code']} 未复权日线行情：开盘价{self._number(payload['open'])}元，"
                f"最高价{self._number(payload['high'])}元，最低价{self._number(payload['low'])}元，"
                f"收盘价{self._number(payload['close'])}元，昨收价{self._number(payload['pre_close'])}元，"
                f"涨跌额{self._number(payload['change'])}元，涨跌幅{self._number(payload['pct_chg'], 4)}%，"
                f"成交量{self._number(payload['vol'])}手，成交额{self._number(payload['amount'])}千元。\n"
            )
            output.write_text(f"---\n{frontmatter}\n---\n\n{body}", encoding="utf-8")
            self.logger.info("processed %s v%d", doc_id, version)

    def write_watermarks(self) -> None:
        previous = self._read_watermark() or {}
        if not self._run_records:
            self.logger.info("no new records; watermark unchanged")
            return
        by_code = dict(previous.get("last_event_date_by_ts_code", {}))
        for item in self._run_records.values():
            payload = item["payload"]
            event_date = self._event_date(payload["trade_date"])
            by_code[payload["ts_code"]] = max(by_code.get(payload["ts_code"], event_date), event_date)
        latest = max(
            self._run_records.values(),
            key=lambda item: (item["payload"]["trade_date"], item["payload"]["ts_code"], item["doc_id"]),
        )
        watermark = {
            "category": self.CATEGORY,
            "source": self.SOURCE,
            "last_event_date": max(by_code.values()),
            "last_event_date_by_ts_code": dict(sorted(by_code.items())),
            "last_doc_id": latest["doc_id"],
            "updated_at": self.now().replace(microsecond=0).isoformat(),
        }
        path = self._watermark_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(watermark, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        self.logger.info("advanced watermark through %s", watermark["last_event_date"])

    def upload_processed_documents(self) -> dict:
        """Upload pending processed Markdown files when IMA_UPLOAD_ENABLED is true."""
        from utils.ima_uploader import IMAUploader

        if not IMAUploader.is_enabled():
            return {"enabled": False, "uploaded": 0, "skipped": 0, "failed": 0}
        uploader = IMAUploader.from_environment(self.config, self.logger)
        processed_root = self.root / "data" / "processed" / self.CATEGORY / self.SOURCE
        return uploader.upload_tree(processed_root, self.SOURCE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", nargs="+", required=True)
    parser.add_argument("--today-date", default=datetime.now().date().isoformat())
    args = parser.parse_args()
    crawler = TushareStockDailyCrawler(args.universe, args.today_date, load_config())
    doc_ids = crawler.crawl_data()
    crawler.process_data(doc_ids)
    crawler.write_watermarks()
    upload_summary = crawler.upload_processed_documents()
    print(f"completed {len(doc_ids)} new documents: {', '.join(doc_ids) if doc_ids else 'none'}")
    if upload_summary.get("enabled"):
        print(f"IMA upload: {upload_summary['uploaded']} uploaded, {upload_summary['skipped']} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
