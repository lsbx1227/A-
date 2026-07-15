"""Sequentially run all sources registered in etc/partition.yaml."""

from __future__ import annotations

import argparse
import importlib
import sys
from datetime import date
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from utils.config import load_config, load_partition_config
from utils.logger import get_logger


def import_crawler(source_config: dict):
    module = importlib.import_module(source_config["module"])
    return getattr(module, source_config["class"])


class Manager:
    def __init__(self, universe: list[str], today_date: str, config: dict):
        self.universe = universe
        self.today_date = today_date
        self.config = config

    def run(self) -> dict[str, str]:
        results: dict[str, str] = {}
        sources = load_partition_config()["sources"]
        for category_source, source_config in sources.items():
            category, source = category_source.split("/", 1)
            log = get_logger(category, source, self.config)
            try:
                crawler_cls = import_crawler(source_config)
                crawler = crawler_cls(self.universe, self.today_date, self.config)
                doc_ids = crawler.crawl_data()
                crawler.process_data(doc_ids)
                crawler.write_watermarks()
                upload_summary = None
                if hasattr(crawler, "upload_processed_documents"):
                    upload_summary = crawler.upload_processed_documents()
                upload_text = ""
                if upload_summary and upload_summary.get("enabled"):
                    upload_text = f", uploaded: {upload_summary['uploaded']}, skipped: {upload_summary['skipped']}"
                results[category_source] = f"ok: {len(doc_ids)} documents{upload_text}"
                log.info("completed source with %d documents", len(doc_ids))
            except Exception as exc:
                results[category_source] = f"failed: {exc}"
                log.exception("%s failed, continuing", category_source)
        return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", nargs="+", required=True, help="ts_codes, e.g. 600519.SH")
    parser.add_argument("--today-date", default=date.today().isoformat())
    args = parser.parse_args()
    results = Manager(args.universe, args.today_date, load_config()).run()
    for source, result in results.items():
        print(f"{source}: {result}")
    return 1 if any(result.startswith("failed") for result in results.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
