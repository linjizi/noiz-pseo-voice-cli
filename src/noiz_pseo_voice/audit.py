"""Local JSONL audit trail for CLI invocations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def append(log_path: Path, entry: dict[str, Any]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def query(
    log_path: Path,
    since: Optional[str] = None,
    caller: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if not log_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if since and row.get("t", "") < since:
            continue
        if caller and row.get("caller") != caller:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r.get("t", ""), reverse=True)
    return rows[: max(1, limit)]

