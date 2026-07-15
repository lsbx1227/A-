"""Load the small, dependency-free YAML configuration used by the pipeline."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any


def _coerce(value: str) -> Any:
    value = value.strip()
    if not value:
        return {}
    if value.lower() in {"null", "none"}:
        return None
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        return [item.strip().strip("'\"") for item in value[1:-1].split(",") if item.strip()]
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value.strip("'\"")


def _load_simple_yaml(path: Path) -> dict[str, Any]:
    """Parse the limited mapping/list syntax used by this repository's config files."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if ":" not in stripped:
            raise ValueError(f"Unsupported YAML at {path}:{line_number}")
        key, value = stripped.split(":", 1)
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        parsed = _coerce(value)
        parent[key.strip()] = parsed
        if isinstance(parsed, dict):
            stack.append((indent, parsed))
    return root


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else _project_root() / "etc" / "config.yaml"
    config = _load_simple_yaml(path)
    config["root"] = str(Path(config["root"]).expanduser().resolve())
    return config


def load_partition_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(config_path) if config_path else _project_root() / "etc" / "partition.yaml"
    return _load_simple_yaml(path)

