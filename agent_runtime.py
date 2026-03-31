from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

DEFAULT_SETTING_SOURCES = ["user", "project"]
DEFAULT_PERMISSION_MODE = "bypassPermissions"
DEFAULT_MAX_TURNS = 8
INSTRUCTION_LOAD_LOG = Path(".claude/runtime/instructions_loaded.jsonl")


def project_root() -> Path:
    return Path(__file__).resolve().parent


def resolve_user_settings() -> Path:
    return Path(os.path.expanduser("~/.claude/settings.json"))


def resolve_instruction_load_log(cwd: str | Path | None = None) -> Path:
    base_dir = Path(cwd).expanduser().resolve() if cwd else project_root()
    return base_dir / INSTRUCTION_LOAD_LOG


def ensure_settings_file(settings_path: str | Path | None = None) -> Path:
    candidate = Path(settings_path).expanduser() if settings_path else resolve_user_settings()
    if not candidate.is_file():
        raise FileNotFoundError(f"Claude settings file not found: {candidate}")
    return candidate


def read_settings_payload(
    settings_path: str | Path | None = None,
    *,
    raise_on_missing: bool = True,
) -> dict[str, Any]:
    path = Path(settings_path).expanduser() if settings_path else resolve_user_settings()
    if raise_on_missing:
        path = ensure_settings_file(path)
    elif not path.is_file():
        return {}

    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    return payload if isinstance(payload, dict) else {}


def _collect_recursive_strings(node: Any, key_name: str) -> set[str]:
    results: set[str] = set()

    if isinstance(node, dict):
        for key, value in node.items():
            if key == key_name:
                if isinstance(value, str) and value.strip():
                    results.add(value.strip())
                elif isinstance(value, list):
                    for item in value:
                        if isinstance(item, str) and item.strip():
                            results.add(item.strip())
                elif isinstance(value, dict):
                    for sub_key in value:
                        if isinstance(sub_key, str) and sub_key.strip():
                            results.add(sub_key.strip())
            results.update(_collect_recursive_strings(value, key_name))
    elif isinstance(node, list):
        for item in node:
            results.update(_collect_recursive_strings(item, key_name))

    return results


def _collect_recursive_mapping_keys(node: Any, key_name: str) -> set[str]:
    results: set[str] = set()

    if isinstance(node, dict):
        for key, value in node.items():
            if key == key_name and isinstance(value, dict):
                for child_key in value:
                    if isinstance(child_key, str) and child_key.strip():
                        results.add(child_key.strip())
            results.update(_collect_recursive_mapping_keys(value, key_name))
    elif isinstance(node, list):
        for item in node:
            results.update(_collect_recursive_mapping_keys(item, key_name))

    return results


def _scan_agent_markdown_dirs(settings_path: Path, cwd: Path | None = None) -> list[str]:
    """Scan ~/.claude/agents/ and .claude/agents/ for subagent Markdown files.

    Returns a sorted list of agent names extracted from the `name:` frontmatter
    field, falling back to the stem of the filename when the field is absent.
    """
    dirs: list[Path] = [
        settings_path.parent / "agents",
    ]
    if cwd:
        dirs.append(Path(cwd) / ".claude" / "agents")
    else:
        dirs.append(project_root() / ".claude" / "agents")

    names: set[str] = set()
    for agents_dir in dirs:
        if not agents_dir.is_dir():
            continue
        for md_file in agents_dir.glob("*.md"):
            try:
                content = md_file.read_text(encoding="utf-8")
            except OSError:
                continue
            name: str | None = None
            if content.startswith("---"):
                end = content.find("---", 3)
                if end != -1:
                    for line in content[3:end].splitlines():
                        if line.startswith("name:"):
                            name = line[5:].strip().strip('"').strip("'")
                            break
            names.add(name if name else md_file.stem)
    return sorted(names)


def load_settings_summary(settings_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(settings_path).expanduser() if settings_path else resolve_user_settings()
    summary: dict[str, Any] = {
        "settings_path": str(path),
        "settings_exists": path.is_file(),
        "known_skills": [],
        "configured_agents": [],
        "configured_mcp_servers": [],
        "error": None,
    }

    try:
        payload = read_settings_payload(path, raise_on_missing=False)
    except (OSError, json.JSONDecodeError) as exc:
        summary["error"] = str(exc)
        return summary

    summary["known_skills"] = sorted(_collect_recursive_strings(payload, "skills"))

    agents_from_settings = sorted(_collect_recursive_mapping_keys(payload, "agents"))
    agents_from_files = _scan_agent_markdown_dirs(path)
    summary["configured_agents"] = sorted(set(agents_from_settings) | set(agents_from_files))

    summary["configured_mcp_servers"] = sorted(
        _collect_recursive_mapping_keys(payload, "mcpServers")
    )
    return summary


def _is_relative_to(candidate: Path, base: Path) -> bool:
    try:
        candidate.relative_to(base)
    except ValueError:
        return False
    return True


def _display_instruction_path(path_value: str | None, *, repo_root: Path) -> str | None:
    if not path_value:
        return None

    try:
        candidate = Path(path_value).expanduser().resolve()
    except OSError:
        return path_value

    home_dir = Path.home().resolve()
    if _is_relative_to(candidate, repo_root):
        relative = candidate.relative_to(repo_root)
        return f"./{relative.as_posix()}" if relative.parts else "."
    if _is_relative_to(candidate, home_dir):
        relative = candidate.relative_to(home_dir)
        return f"~/{relative.as_posix()}" if relative.parts else "~"
    return str(candidate)


def read_instruction_load_entries(
    log_path: str | Path | None = None,
    *,
    since_ms: int | None = None,
    cwd: str | Path | None = None,
) -> list[dict[str, Any]]:
    path = Path(log_path).expanduser() if log_path else resolve_instruction_load_log(cwd)
    if not path.is_file():
        return []

    repo_root = Path(cwd).expanduser().resolve() if cwd else project_root()
    entries: list[dict[str, Any]] = []

    with path.open(encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue

            timestamp_ms = payload.get("timestamp_ms")
            if not isinstance(timestamp_ms, int):
                continue
            if since_ms is not None and timestamp_ms < since_ms:
                continue

            event_cwd = payload.get("cwd")
            if isinstance(event_cwd, str) and event_cwd.strip():
                try:
                    resolved_event_cwd = Path(event_cwd).expanduser().resolve()
                except OSError:
                    continue
                if not (
                    _is_relative_to(resolved_event_cwd, repo_root)
                    or _is_relative_to(repo_root, resolved_event_cwd)
                ):
                    continue

            entries.append(payload)

    return entries


def summarize_instruction_load_entries(
    entries: list[dict[str, Any]],
    *,
    repo_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    resolved_repo_root = (
        Path(repo_root).expanduser().resolve() if repo_root else project_root()
    )
    grouped: dict[str, dict[str, Any]] = {}

    for entry in sorted(entries, key=lambda item: int(item.get("timestamp_ms", 0))):
        file_path = entry.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            continue

        timestamp_ms = int(entry.get("timestamp_ms", 0))
        load_reason = entry.get("load_reason")
        memory_type = entry.get("memory_type")
        parent_file_path = entry.get("parent_file_path")
        trigger_file_path = entry.get("trigger_file_path")
        globs = entry.get("globs") if isinstance(entry.get("globs"), list) else []

        summary = grouped.get(file_path)
        if summary is None:
            summary = {
                "id": file_path,
                "file_path": file_path,
                "display_path": _display_instruction_path(
                    file_path,
                    repo_root=resolved_repo_root,
                )
                or file_path,
                "memory_type": memory_type if isinstance(memory_type, str) else "",
                "load_reasons": [],
                "load_count": 0,
                "loaded_at": timestamp_ms,
                "parent_file_path": None,
                "parent_display_path": None,
                "trigger_file_path": None,
                "trigger_display_path": None,
                "globs": [],
            }
            grouped[file_path] = summary

        summary["load_count"] += 1
        if isinstance(load_reason, str) and load_reason and load_reason not in summary["load_reasons"]:
            summary["load_reasons"].append(load_reason)
        if isinstance(globs, list):
            for glob_value in globs:
                if isinstance(glob_value, str) and glob_value and glob_value not in summary["globs"]:
                    summary["globs"].append(glob_value)

        if timestamp_ms >= int(summary.get("loaded_at", 0)):
            summary["loaded_at"] = timestamp_ms
            if isinstance(memory_type, str) and memory_type:
                summary["memory_type"] = memory_type
            if isinstance(parent_file_path, str) and parent_file_path:
                summary["parent_file_path"] = parent_file_path
                summary["parent_display_path"] = _display_instruction_path(
                    parent_file_path,
                    repo_root=resolved_repo_root,
                )
            else:
                summary["parent_file_path"] = None
                summary["parent_display_path"] = None
            if isinstance(trigger_file_path, str) and trigger_file_path:
                summary["trigger_file_path"] = trigger_file_path
                summary["trigger_display_path"] = _display_instruction_path(
                    trigger_file_path,
                    repo_root=resolved_repo_root,
                )
            else:
                summary["trigger_file_path"] = None
                summary["trigger_display_path"] = None

    return sorted(
        grouped.values(),
        key=lambda item: (-int(item.get("loaded_at", 0)), item.get("display_path", "")),
    )


def build_agent_options(
    *,
    settings_path: str | Path | None = None,
    include_partial_messages: bool = False,
    cwd: str | Path | None = None,
    stderr: Callable[[str], None] | None = None,
) -> ClaudeAgentOptions:
    resolved_settings = ensure_settings_file(settings_path)
    resolved_cwd = Path(cwd).expanduser().resolve() if cwd else project_root()

    return ClaudeAgentOptions(
        settings=str(resolved_settings),
        setting_sources=list(DEFAULT_SETTING_SOURCES),
        permission_mode=DEFAULT_PERMISSION_MODE,
        max_turns=DEFAULT_MAX_TURNS,
        include_partial_messages=include_partial_messages,
        cwd=str(resolved_cwd),
        stderr=stderr,
    )


def resolve_cli_path(
    options: ClaudeAgentOptions | None = None,
    *,
    settings_path: str | Path | None = None,
    cwd: str | Path | None = None,
) -> str | None:
    try:
        candidate_options = options or build_agent_options(
            settings_path=settings_path,
            cwd=cwd,
        )
        transport = SubprocessCLITransport(prompt="", options=candidate_options)
        return transport._cli_path
    except Exception:
        return None


class LoggingSubprocessCLITransport(SubprocessCLITransport):
    def __init__(
        self,
        *,
        prompt: str,
        options: ClaudeAgentOptions,
        on_resolved_cli: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(prompt=prompt, options=options)
        self._on_resolved_cli = on_resolved_cli

    async def connect(self) -> None:
        await super().connect()
        if self._on_resolved_cli is not None:
            self._on_resolved_cli(self._cli_path)
        else:
            print(f"Resolved Claude CLI: {self._cli_path}")


def extract_assistant_text(message: AssistantMessage) -> list[str]:
    return [
        block.text
        for block in message.content
        if isinstance(block, TextBlock) and block.text
    ]


async def run_text_query(
    prompt: str,
    *,
    on_resolved_cli: Callable[[str], None] | None = None,
) -> list[str]:
    options = build_agent_options()
    transport = LoggingSubprocessCLITransport(
        prompt=prompt,
        options=options,
        on_resolved_cli=on_resolved_cli,
    )

    lines: list[str] = []
    async for message in query(prompt=prompt, options=options, transport=transport):
        if isinstance(message, AssistantMessage):
            lines.extend(extract_assistant_text(message))
    return lines
