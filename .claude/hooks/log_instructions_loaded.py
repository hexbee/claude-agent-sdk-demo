from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

LOG_PATH = Path(".claude/runtime/instructions_loaded.jsonl")
MAX_LOG_BYTES = 512_000
MAX_LOG_LINES = 2_000


def compact_log(log_path: Path) -> None:
    if not log_path.is_file() or log_path.stat().st_size <= MAX_LOG_BYTES:
        return

    lines = log_path.read_text(encoding="utf-8").splitlines()[-MAX_LOG_LINES:]
    log_path.write_text(
        "".join(f"{line}\n" for line in lines),
        encoding="utf-8",
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    if not isinstance(payload, dict):
        return 0

    project_dir = Path(
        os.environ.get("CLAUDE_PROJECT_DIR")
        or payload.get("cwd")
        or "."
    ).expanduser().resolve()
    log_path = project_dir / LOG_PATH
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp_ms": int(time.time() * 1000),
        "session_id": payload.get("session_id"),
        "cwd": payload.get("cwd"),
        "hook_event_name": payload.get("hook_event_name"),
        "file_path": payload.get("file_path"),
        "memory_type": payload.get("memory_type"),
        "load_reason": payload.get("load_reason"),
        "globs": payload.get("globs") or [],
        "trigger_file_path": payload.get("trigger_file_path"),
        "parent_file_path": payload.get("parent_file_path"),
        "transcript_path": payload.get("transcript_path"),
    }

    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    compact_log(log_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
