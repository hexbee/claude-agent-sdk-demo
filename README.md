# claude-agent-sdk-demo

Minimal `uv` project for testing the published `claude-agent-sdk` package.

## Purpose

This demo is intentionally outside the SDK source repository so `uv` resolves
the package from the index instead of a local workspace checkout.

The script:

- installs `claude-agent-sdk` as a normal dependency
- points the SDK at `~/.claude/settings.json`
- runs a one-shot `query()` call

## Prerequisites

- `uv`
- `~/.claude/settings.json`

## Install

```bash
uv sync
```

## Run

```bash
uv run python main.py
```

## What to check

If this runs without a separate system `claude` install, that strongly suggests
the published SDK package is using its bundled Claude Code runtime for your
platform.
