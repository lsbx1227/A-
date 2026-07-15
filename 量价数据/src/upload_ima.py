"""Upload pending tushare_stockdaily processed Markdown files to IMA."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from utils.config import load_config
from utils.ima_uploader import IMAUploader
from utils.logger import get_logger


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--processed-root",
        help="defaults to data/processed/market_trading/tushare_stockdaily under config root",
    )
    args = parser.parse_args()
    config = load_config()
    root = Path(config["root"])
    processed_root = (
        Path(args.processed_root).resolve()
        if args.processed_root
        else root / "data" / "processed" / "market_trading" / "tushare_stockdaily"
    )
    logger = get_logger("market_trading", "tushare_stockdaily", config)
    uploader = IMAUploader.from_environment(config, logger)
    summary = uploader.upload_tree(processed_root, "tushare_stockdaily")
    print(f"IMA upload: {summary['uploaded']} uploaded, {summary['skipped']} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

