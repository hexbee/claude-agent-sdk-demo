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


def project_root() -> Path:
    return Path(__file__).resolve().parent


def resolve_user_settings() -> Path:
    return Path(os.path.expanduser("~/.claude/settings.json"))


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
    summary["configured_agents"] = sorted(
        _collect_recursive_mapping_keys(payload, "agents")
    )
    summary["configured_mcp_servers"] = sorted(
        _collect_recursive_mapping_keys(payload, "mcpServers")
    )
    return summary


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
