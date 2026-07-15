"""Shared file logging for all crawler sources."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from .config import load_config


def get_logger(category: str, source: str, config: dict | None = None) -> logging.Logger:
    config = config or load_config()
    log_dir = Path(config["root"]) / "log" / category / source
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"knowledge_base.{category}.{source}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    log_path = (log_dir / f"{date.today().isoformat()}.log").resolve()
    # Tests and multi-environment runs can reuse the same named logger with a
    # different project root. Drop stale file handlers instead of writing to a
    # directory that no longer exists.
    for handler in list(logger.handlers):
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename).resolve() != log_path:
            logger.removeHandler(handler)
            handler.close()
    if not any(
        isinstance(handler, logging.FileHandler)
        and Path(handler.baseFilename).resolve() == log_path
        for handler in logger.handlers
    ):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger
