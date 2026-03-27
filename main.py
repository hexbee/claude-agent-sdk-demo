from __future__ import annotations

import asyncio
import os
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, TextBlock, query
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport


class LoggingSubprocessCLITransport(SubprocessCLITransport):
    async def connect(self) -> None:
        await super().connect()
        print(f"Resolved Claude CLI: {self._cli_path}")


def _resolve_user_settings() -> str:
    return os.path.expanduser("~/.claude/settings.json")


async def run_demo() -> None:
    settings_path = _resolve_user_settings()
    if not Path(settings_path).is_file():
        raise FileNotFoundError(f"Claude settings file not found: {settings_path}")

    options = ClaudeAgentOptions(
        settings=settings_path,
        setting_sources=["user"],
        max_turns=1,
    )

    prompt = (
        "Reply with one short sentence confirming whether you can access the Claude "
        "runtime. Do not use any tools."
    )

    transport = LoggingSubprocessCLITransport(prompt=prompt, options=options)

    async for message in query(prompt=prompt, options=options, transport=transport):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text)


def main() -> None:
    asyncio.run(run_demo())


if __name__ == "__main__":
    main()
