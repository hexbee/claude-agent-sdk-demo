from __future__ import annotations

import asyncio
import copy
import json
import queue
import threading
import time
import uuid
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    StreamEvent,
    SystemMessage,
    TaskNotificationMessage,
    TaskProgressMessage,
    TaskStartedMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from agent_runtime import (
    build_agent_options,
    load_settings_summary,
    project_root,
    read_instruction_load_entries,
    resolve_cli_path,
    resolve_instruction_load_log,
    resolve_user_settings,
    summarize_instruction_load_entries,
)

UNCHANGED = object()

# Tool names that represent subagent / Task invocations
_AGENT_TOOL_NAMES: frozenset[str] = frozenset({"Agent", "Task"})


def now_ms() -> int:
    return int(time.time() * 1000)


def make_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def truncate_text(value: str, *, limit: int = 600) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def extract_stream_text_delta(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")

    if event_type == "content_block_start":
        content_block = event.get("content_block", {})
        if content_block.get("type") == "text":
            text = content_block.get("text")
            if isinstance(text, str) and text:
                return text

    if event_type == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            text = delta.get("text")
            if isinstance(text, str) and text:
                return text

    return None


def classify_tool_kind(
    tool_name: str,
    *,
    mcp_tool_names: Iterable[str],
    known_skills: Iterable[str],
) -> str:
    # Agent / Task tool spawns a subagent — highest priority
    if tool_name in _AGENT_TOOL_NAMES:
        return "agent"

    mcp_name_set = set(mcp_tool_names)
    candidate_names = {tool_name}
    if tool_name.startswith("mcp__"):
        parts = tool_name.split("__")
        if len(parts) >= 3:
            candidate_names.add(parts[-1])
            candidate_names.add("__".join(parts[2:]))

    if candidate_names & mcp_name_set:
        return "mcp"

    normalized_tool_name = normalize_text(tool_name)
    for skill_name in known_skills:
        skill = skill_name.strip()
        if skill and normalize_text(skill) in normalized_tool_name:
            return "skill"

    return "builtin"


def match_skill_hints(text: str, known_skills: Iterable[str]) -> list[str]:
    haystack = normalize_text(text)
    matches: list[str] = []
    for skill_name in known_skills:
        candidate = skill_name.strip()
        if candidate and normalize_text(candidate) in haystack:
            matches.append(candidate)
    return sorted(set(matches))


def serialize_for_json(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): serialize_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [serialize_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [serialize_for_json(item) for item in value]
    return repr(value)


def pretty_json(value: Any) -> str:
    serialized = serialize_for_json(value)
    if isinstance(serialized, str):
        return serialized
    return json.dumps(serialized, ensure_ascii=False, indent=2, sort_keys=True)


def summarize_tool_result_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return truncate_text(content)
    return truncate_text(pretty_json(content))


def extract_server_skill_hints(server_info: dict[str, Any] | None) -> list[str]:
    if not isinstance(server_info, dict):
        return []

    hints: set[str] = set()
    for command in server_info.get("commands", []):
        if not isinstance(command, dict):
            continue
        name = command.get("name")
        description = command.get("description", "")
        if not isinstance(name, str) or not name.strip():
            continue
        if "(bundled)" in description or "(user)" in description:
            hints.add(name.strip())
    return sorted(hints)


class AgentSessionWorker:
    def __init__(self, *, repo_root: Path | None = None) -> None:
        self._repo_root = Path(repo_root or project_root()).resolve()
        self._settings_path = resolve_user_settings()
        self._settings_summary = load_settings_summary(self._settings_path)
        self._instruction_log_path = resolve_instruction_load_log(self._repo_root)

        self._state_lock = threading.Lock()
        self._subscribers: set[queue.Queue[dict[str, Any]]] = set()
        self._event_counter = 0
        self._generation = 0
        self._state = self._build_empty_state()

        self._loop_ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client_lock: asyncio.Lock | None = None
        self._client: ClaudeSDKClient | None = None
        self._client_generation: int | None = None

        self._thread = threading.Thread(
            target=self._run_loop,
            name="claude-agent-session-worker",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait()

        self.new_session()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._client_lock = asyncio.Lock()
        self._loop_ready.set()
        loop.run_forever()

    def _build_empty_state(self) -> dict[str, Any]:
        resolved_cli = None
        if self._settings_summary.get("settings_exists"):
            resolved_cli = resolve_cli_path(
                settings_path=self._settings_path,
                cwd=self._repo_root,
            )

        settings_error = self._settings_summary.get("error")
        if settings_error:
            status = "error"
        elif self._settings_summary.get("settings_exists"):
            status = "connecting"
        else:
            status = "needs_config"

        return {
            "session": {
                "id": make_id("session"),
                "claude_session_id": None,
                "status": status,
                "busy": False,
                "created_at": now_ms(),
                "project_root": str(self._repo_root),
                "settings_path": str(self._settings_path),
                "settings_exists": bool(self._settings_summary.get("settings_exists")),
                "stderr_tail": [],
            },
            "messages": [],
            "timeline": [],
            "capabilities": {
                "settings_path": str(self._settings_path),
                "settings_exists": bool(self._settings_summary.get("settings_exists")),
                "configured_agents": list(
                    self._settings_summary.get("configured_agents", [])
                ),
                "configured_mcp_servers": list(
                    self._settings_summary.get("configured_mcp_servers", [])
                ),
                "known_skills": list(self._settings_summary.get("known_skills", [])),
                "settings_skills": list(self._settings_summary.get("known_skills", [])),
                "mcp_servers": [],
                "mcp_tools": [],
                "resolved_cli": resolved_cli,
                "server_info": None,
                "instruction_log_path": str(self._instruction_log_path),
                "loaded_instructions": [],
                "loaded_instructions_error": None,
            },
            "last_error": settings_error,
        }

    def _next_event_id(self) -> int:
        self._event_counter += 1
        return self._event_counter

    def _emit(self, event: dict[str, Any]) -> None:
        packet = {
            "id": self._next_event_id(),
            "timestamp": now_ms(),
            **serialize_for_json(event),
        }

        with self._state_lock:
            subscribers = tuple(self._subscribers)

        for subscriber in subscribers:
            subscriber.put(packet)

    def _submit_coroutine(self, coroutine: Any) -> None:
        if self._loop is None:
            raise RuntimeError("Agent worker loop is not ready.")

        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)

        def _consume_result(done_future: Any) -> None:
            try:
                done_future.result()
            except Exception as exc:
                print(f"Agent worker task failed: {exc}")

        future.add_done_callback(_consume_result)

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue()
        with self._state_lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self._state_lock:
            self._subscribers.discard(subscriber)

    def get_state_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return copy.deepcopy(self._state)

    def new_session(self) -> dict[str, Any]:
        self._settings_summary = load_settings_summary(self._settings_path)

        with self._state_lock:
            self._generation += 1
            generation = self._generation
            self._state = self._build_empty_state()
            snapshot = copy.deepcopy(self._state)

        self._emit({"type": "session_reset", "state": snapshot})
        self._submit_coroutine(self._prepare_session(generation))
        return snapshot

    def send_message(self, text: str) -> dict[str, Any]:
        message_text = text.strip()
        if not message_text:
            return {"ok": False, "status": 400, "error": "消息不能为空。"}

        with self._state_lock:
            if self._state["session"]["busy"]:
                return {
                    "ok": False,
                    "status": 409,
                    "error": "当前 session 仍在执行，请等待这一轮完成。",
                }

            generation = self._generation
            message = {
                "id": make_id("message"),
                "role": "user",
                "text": message_text,
                "status": "complete",
                "created_at": now_ms(),
            }
            self._state["messages"].append(message)
            self._state["session"]["busy"] = True
            self._state["session"]["status"] = "running"
            self._state["last_error"] = None
            session = copy.deepcopy(self._state["session"])

        self._emit({"type": "user_message", "message": message})
        self._emit(
            {
                "type": "session_status",
                "session": session,
                "last_error": None,
            }
        )
        self._submit_coroutine(self._run_turn(message_text, generation))
        return {"ok": True, "status": 202, "session_id": session["id"]}

    async def _prepare_session(self, generation: int) -> None:
        try:
            await self._ensure_client(generation)
            if not self._read_session_busy(generation):
                self._set_session_status(
                    generation,
                    status="idle",
                    busy=False,
                    last_error=None,
                )
        except FileNotFoundError as exc:
            self._set_session_status(
                generation,
                status="needs_config",
                busy=False,
                last_error=str(exc),
            )
        except Exception as exc:
            self._set_session_status(
                generation,
                status="error",
                busy=False,
                last_error=str(exc),
            )

    async def _ensure_client(self, generation: int) -> ClaudeSDKClient | None:
        if self._client_lock is None:
            raise RuntimeError("Agent client lock is not ready.")

        async with self._client_lock:
            if generation != self._generation:
                return None

            if self._client and self._client_generation == generation:
                return self._client

            if self._client is not None:
                stale_client = self._client
                self._client = None
                self._client_generation = None
                asyncio.create_task(self._disconnect_client_quietly(stale_client))

            options = build_agent_options(
                include_partial_messages=True,
                cwd=self._repo_root,
                stderr=lambda chunk: self._capture_stderr(chunk, generation),
            )
            client = ClaudeSDKClient(options=options)
            await client.connect()

            self._client = client
            self._client_generation = generation

            server_info = await client.get_server_info()
            self._update_capabilities(
                generation,
                server_info=serialize_for_json(server_info),
            )
            await self._refresh_capabilities(generation)
            self._refresh_loaded_instructions(generation)
            return client

    async def _disconnect_client_quietly(self, client: ClaudeSDKClient) -> None:
        await self._disconnect_client_safely(client, quiet=True)

    @staticmethod
    def _is_cross_task_close_error(exc: BaseException) -> bool:
        return isinstance(exc, RuntimeError) and (
            "different task than it was entered in" in str(exc)
        )

    async def _disconnect_client_safely(
        self,
        client: ClaudeSDKClient,
        *,
        quiet: bool,
    ) -> None:
        try:
            await client.disconnect()
            return
        except Exception as exc:
            if not self._is_cross_task_close_error(exc):
                if quiet:
                    return
                raise

        # The SDK query can own AnyIO task groups entered by a different task
        # than the one performing shutdown. Fall back to transport-level cleanup
        # so Ctrl+C and session resets do not emit noisy atexit tracebacks.
        transport = getattr(client, "_transport", None)
        if transport is not None:
            with suppress(Exception):
                await transport.close()

        if hasattr(client, "_query"):
            client._query = None
        if hasattr(client, "_transport"):
            client._transport = None

    async def _refresh_capabilities(self, generation: int) -> None:
        if generation != self._generation:
            return
        if self._client is None or self._client_generation != generation:
            return

        status = await self._client.get_mcp_status()
        mcp_servers = []
        mcp_tools: set[str] = set()

        for server in status.get("mcpServers", []):
            tools = [tool.get("name") for tool in server.get("tools", []) if tool.get("name")]
            mcp_tools.update(tools)
            mcp_servers.append(
                {
                    "name": server.get("name"),
                    "status": server.get("status"),
                    "scope": server.get("scope"),
                    "error": server.get("error"),
                    "tools": tools,
                    "tool_count": len(tools),
                    "server_info": serialize_for_json(server.get("serverInfo")),
                }
            )

        self._update_capabilities(
            generation,
            mcp_servers=sorted(mcp_servers, key=lambda item: item["name"] or ""),
            mcp_tools=sorted(mcp_tools),
        )

    def _refresh_loaded_instructions(self, generation: int) -> None:
        with self._state_lock:
            if generation != self._generation:
                return
            created_at = int(self._state["session"].get("created_at", 0))

        try:
            entries = read_instruction_load_entries(
                self._instruction_log_path,
                since_ms=created_at,
                cwd=self._repo_root,
            )
            loaded_instructions = summarize_instruction_load_entries(
                entries,
                repo_root=self._repo_root,
            )
        except Exception as exc:
            self._update_capabilities(
                generation,
                loaded_instructions=[],
                loaded_instructions_error=str(exc),
            )
            return

        self._update_capabilities(
            generation,
            loaded_instructions=loaded_instructions,
            loaded_instructions_error=None,
        )

    def _capture_stderr(self, chunk: str, generation: int) -> None:
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        if not lines:
            return

        with self._state_lock:
            if generation != self._generation:
                return
            tail = self._state["session"].setdefault("stderr_tail", [])
            tail.extend(lines)
            if len(tail) > 20:
                del tail[:-20]

    def _update_capabilities(
        self,
        generation: int,
        *,
        server_info: Any = UNCHANGED,
        mcp_servers: Any = UNCHANGED,
        mcp_tools: Any = UNCHANGED,
        loaded_instructions: Any = UNCHANGED,
        loaded_instructions_error: Any = UNCHANGED,
    ) -> None:
        with self._state_lock:
            if generation != self._generation:
                return

            capabilities = self._state["capabilities"]
            if server_info is not UNCHANGED:
                capabilities["server_info"] = server_info
                server_skills = extract_server_skill_hints(server_info)
                capabilities["known_skills"] = sorted(
                    set(capabilities.get("settings_skills", [])) | set(server_skills)
                )
            if mcp_servers is not UNCHANGED:
                capabilities["mcp_servers"] = mcp_servers
            if mcp_tools is not UNCHANGED:
                capabilities["mcp_tools"] = mcp_tools
            if loaded_instructions is not UNCHANGED:
                capabilities["loaded_instructions"] = loaded_instructions
            if loaded_instructions_error is not UNCHANGED:
                capabilities["loaded_instructions_error"] = loaded_instructions_error

            payload = copy.deepcopy(capabilities)

        self._emit({"type": "capabilities", "capabilities": payload})

    def _set_session_status(
        self,
        generation: int,
        *,
        status: str | None = None,
        busy: bool | None = None,
        claude_session_id: str | None | object = UNCHANGED,
        last_error: str | None | object = UNCHANGED,
    ) -> None:
        with self._state_lock:
            if generation != self._generation:
                return

            session = self._state["session"]
            if status is not None:
                session["status"] = status
            if busy is not None:
                session["busy"] = busy
            if claude_session_id is not UNCHANGED:
                session["claude_session_id"] = claude_session_id
            if last_error is not UNCHANGED:
                self._state["last_error"] = last_error

            session_payload = copy.deepcopy(session)
            error_payload = copy.deepcopy(self._state["last_error"])

        self._emit(
            {
                "type": "session_status",
                "session": session_payload,
                "last_error": error_payload,
            }
        )

    async def _run_turn(self, text: str, generation: int) -> None:
        turn_context = {
            "streaming_message_id": None,
            "subagent_streams": {},  # parent_tool_use_id -> message_id
            "tool_names": {},
            "seen_skills": set(),
            "result_error": None,
        }

        try:
            client = await self._ensure_client(generation)
            if client is None:
                return

            session_id = self._read_active_session_id(generation)
            await client.query(text, session_id=session_id)

            async for message in client.receive_response():
                if generation != self._generation:
                    return
                self._handle_message(message, generation, turn_context)

            await self._refresh_capabilities(generation)
            self._refresh_loaded_instructions(generation)
            if turn_context["result_error"]:
                self._set_session_status(
                    generation,
                    status="error",
                    busy=False,
                    last_error=turn_context["result_error"],
                )
            else:
                self._set_session_status(
                    generation,
                    status="idle",
                    busy=False,
                    last_error=None,
                )
        except Exception as exc:
            self._set_session_status(
                generation,
                status="error",
                busy=False,
                last_error=str(exc),
            )
            self._emit(
                {
                    "type": "error",
                    "error": str(exc),
                }
            )

    def _read_active_session_id(self, generation: int) -> str:
        with self._state_lock:
            if generation != self._generation:
                return "session-stale"
            return str(self._state["session"]["id"])

    def _read_session_busy(self, generation: int) -> bool:
        with self._state_lock:
            if generation != self._generation:
                return False
            return bool(self._state["session"]["busy"])

    def _handle_message(
        self,
        message: Any,
        generation: int,
        turn_context: dict[str, Any],
    ) -> None:
        self._refresh_loaded_instructions(generation)

        if isinstance(message, StreamEvent):
            delta = extract_stream_text_delta(message.event)
            if not delta:
                return
            parent_id = message.parent_tool_use_id
            if parent_id:
                # Streaming text from within a subagent
                message_id = turn_context["subagent_streams"].get(parent_id)
                if message_id is None:
                    message_id = self._create_streaming_assistant_message(
                        generation,
                        session_id=message.session_id,
                        subagent_id=parent_id,
                    )
                    turn_context["subagent_streams"][parent_id] = message_id
            else:
                message_id = turn_context["streaming_message_id"]
                if message_id is None:
                    message_id = self._create_streaming_assistant_message(
                        generation,
                        session_id=message.session_id,
                    )
                    turn_context["streaming_message_id"] = message_id
            self._append_assistant_delta(generation, message_id, delta)
            return

        if isinstance(message, AssistantMessage):
            self._handle_assistant_message(message, generation, turn_context)
            return

        if isinstance(message, UserMessage):
            if isinstance(message.content, list):
                self._process_content_blocks(
                    message.content,
                    generation,
                    turn_context,
                    parent_tool_use_id=message.parent_tool_use_id,
                )
            return

        if isinstance(message, TaskStartedMessage):
            is_agent = bool(message.task_type)
            self._upsert_task_entry(
                generation,
                entry_id=f"task-{message.task_id}",
                payload={
                    "id": f"task-{message.task_id}",
                    "entry_type": "agent" if is_agent else "task",
                    "kind": "agent" if is_agent else "builtin",
                    "name": message.description,
                    "status": "running",
                    "task_id": message.task_id,
                    "task_type": message.task_type,
                    "agent_type": message.task_type,
                    "tool_use_id": message.tool_use_id,
                    "summary": "子 Agent 开始执行" if is_agent else "任务开始执行",
                    "details": "",
                    "usage": None,
                    "started_at": now_ms(),
                    "finished_at": None,
                },
                event_type="task_started",
            )
            self._register_skill_mentions(
                generation,
                message.description,
                turn_context,
                source="task",
            )
            return

        if isinstance(message, TaskProgressMessage):
            self._update_task_progress(message, generation)
            self._register_skill_mentions(
                generation,
                message.description,
                turn_context,
                source="task",
            )
            return

        if isinstance(message, TaskNotificationMessage):
            self._complete_task(message, generation)
            self._register_skill_mentions(
                generation,
                f"{message.summary}\n{message.output_file}",
                turn_context,
                source="task",
            )
            return

        if isinstance(message, ResultMessage):
            if turn_context.get("streaming_message_id"):
                self._complete_streaming_assistant_message(
                    generation,
                    turn_context["streaming_message_id"],
                )
                turn_context["streaming_message_id"] = None
            if message.is_error:
                turn_context["result_error"] = message.result or "Claude 返回了错误结果。"
                self._emit(
                    {
                        "type": "error",
                        "error": turn_context["result_error"],
                    }
                )
            self._set_session_status(
                generation,
                claude_session_id=message.session_id,
            )
            self._emit(
                {
                    "type": "turn_result",
                    "result": {
                        "session_id": message.session_id,
                        "duration_ms": message.duration_ms,
                        "duration_api_ms": message.duration_api_ms,
                        "is_error": message.is_error,
                        "num_turns": message.num_turns,
                        "stop_reason": message.stop_reason,
                        "total_cost_usd": message.total_cost_usd,
                        "usage": serialize_for_json(message.usage),
                        "result": message.result,
                    },
                }
            )
            return

        if isinstance(message, SystemMessage):
            self._emit(
                {
                    "type": "system_event",
                    "system": {
                        "subtype": message.subtype,
                        "data": serialize_for_json(message.data),
                    },
                }
            )

    def _handle_assistant_message(
        self,
        message: AssistantMessage,
        generation: int,
        turn_context: dict[str, Any],
    ) -> None:
        text_blocks = [
            block.text for block in message.content if isinstance(block, TextBlock) and block.text
        ]
        full_text = "".join(text_blocks)
        parent_id = message.parent_tool_use_id  # non-None means from within a subagent

        if full_text:
            if parent_id:
                # Subagent's own assistant response
                message_id = turn_context["subagent_streams"].get(parent_id)
                if message_id is None:
                    message_id = self._create_streaming_assistant_message(
                        generation,
                        model=message.model,
                        subagent_id=parent_id,
                    )
                    turn_context["subagent_streams"][parent_id] = message_id
            else:
                message_id = turn_context.get("streaming_message_id")
                if message_id is None:
                    message_id = self._create_streaming_assistant_message(
                        generation,
                        model=message.model,
                    )

            self._finalize_assistant_message(
                generation,
                message_id,
                text=full_text,
                model=message.model,
                usage=serialize_for_json(message.usage),
                error=message.error,
                subagent_id=parent_id,
            )
            if parent_id:
                turn_context["subagent_streams"].pop(parent_id, None)
            else:
                turn_context["streaming_message_id"] = None
            self._register_skill_mentions(
                generation,
                full_text,
                turn_context,
                source="assistant",
            )

        self._process_content_blocks(
            message.content,
            generation,
            turn_context,
            parent_tool_use_id=parent_id,
        )

        if message.error:
            self._emit(
                {
                    "type": "error",
                    "error": message.error,
                }
            )

    def _create_streaming_assistant_message(
        self,
        generation: int,
        *,
        session_id: str | None = None,
        model: str | None = None,
        subagent_id: str | None = None,
    ) -> str:
        message = {
            "id": make_id("message"),
            "role": "assistant",
            "text": "",
            "status": "streaming",
            "model": model,
            "session_id": session_id,
            "subagent_id": subagent_id,
            "usage": None,
            "error": None,
            "created_at": now_ms(),
        }

        with self._state_lock:
            if generation != self._generation:
                return message["id"]
            self._state["messages"].append(message)

        self._emit({"type": "assistant_message", "message": message})
        return message["id"]

    def _append_assistant_delta(
        self,
        generation: int,
        message_id: str,
        delta: str,
    ) -> None:
        with self._state_lock:
            if generation != self._generation:
                return

            message = next(
                (
                    item
                    for item in self._state["messages"]
                    if item["id"] == message_id
                ),
                None,
            )
            if message is None:
                return

            message["text"] += delta

        self._emit(
            {
                "type": "assistant_delta",
                "message_id": message_id,
                "delta": delta,
            }
        )

    def _finalize_assistant_message(
        self,
        generation: int,
        message_id: str,
        *,
        text: str,
        model: str | None,
        usage: Any,
        error: str | None,
        subagent_id: str | None = None,
    ) -> None:
        with self._state_lock:
            if generation != self._generation:
                return

            message = next(
                (
                    item
                    for item in self._state["messages"]
                    if item["id"] == message_id
                ),
                None,
            )
            if message is None:
                return

            message["text"] = text
            message["model"] = model
            message["usage"] = usage
            message["error"] = error
            message["status"] = "complete"
            if subagent_id is not None:
                message["subagent_id"] = subagent_id
            payload = copy.deepcopy(message)

        self._emit({"type": "assistant_message", "message": payload})

    def _complete_streaming_assistant_message(
        self,
        generation: int,
        message_id: str,
    ) -> None:
        with self._state_lock:
            if generation != self._generation:
                return

            message = next(
                (
                    item
                    for item in self._state["messages"]
                    if item["id"] == message_id
                ),
                None,
            )
            if message is None or message["status"] == "complete":
                return

            message["status"] = "complete"
            payload = copy.deepcopy(message)

        self._emit({"type": "assistant_message", "message": payload})

    def _process_content_blocks(
        self,
        blocks: list[Any],
        generation: int,
        turn_context: dict[str, Any],
        parent_tool_use_id: str | None = None,
    ) -> None:
        for block in blocks:
            if isinstance(block, ToolUseBlock):
                self._start_tool(block, generation, turn_context, parent_tool_use_id)
            elif isinstance(block, ToolResultBlock):
                self._finish_tool(block, generation, turn_context, parent_tool_use_id)

    def _read_tool_classification_context(self, generation: int) -> tuple[set[str], list[str]]:
        with self._state_lock:
            if generation != self._generation:
                return set(), []
            capabilities = self._state["capabilities"]
            return (
                set(capabilities.get("mcp_tools", [])),
                list(capabilities.get("known_skills", [])),
            )

    def _start_tool(
        self,
        block: ToolUseBlock,
        generation: int,
        turn_context: dict[str, Any],
        parent_tool_use_id: str | None = None,
    ) -> None:
        mcp_tool_names, known_skills = self._read_tool_classification_context(generation)
        kind = classify_tool_kind(
            block.name,
            mcp_tool_names=mcp_tool_names,
            known_skills=known_skills,
        )
        turn_context["tool_names"][block.id] = block.name

        entry: dict[str, Any] = {
            "id": block.id,
            "entry_type": "agent" if kind == "agent" else "tool",
            "kind": kind,
            "name": block.name,
            "status": "running",
            "summary": "子 Agent 调用中" if kind == "agent" else "工具正在执行",
            "input_preview": truncate_text(pretty_json(block.input)),
            "input_details": pretty_json(block.input),
            "output_preview": "",
            "output_details": "",
            "error": None,
            "started_at": now_ms(),
            "finished_at": None,
            "duration_ms": None,
        }
        if parent_tool_use_id:
            entry["subagent_id"] = parent_tool_use_id
        if kind == "agent" and isinstance(block.input, dict):
            entry["agent_type"] = block.input.get("agent_type") or block.input.get("description", "")
            entry["subagent_type"] = block.input.get("subagent_type", "")
        self._upsert_timeline_entry(generation, entry, event_type="tool_started")

        if kind == "skill":
            self._register_skill_mentions(
                generation,
                block.name,
                turn_context,
                source="tool",
            )

    def _finish_tool(
        self,
        block: ToolResultBlock,
        generation: int,
        turn_context: dict[str, Any],
        parent_tool_use_id: str | None = None,
    ) -> None:
        tool_name = turn_context["tool_names"].get(block.tool_use_id, block.tool_use_id)
        mcp_tool_names, known_skills = self._read_tool_classification_context(generation)
        kind = classify_tool_kind(
            tool_name,
            mcp_tool_names=mcp_tool_names,
            known_skills=known_skills,
        )

        entry: dict[str, Any] = {
            "id": block.tool_use_id,
            "entry_type": "agent" if kind == "agent" else "tool",
            "kind": kind,
            "name": tool_name,
            "status": "failed" if block.is_error else "completed",
            "summary": "工具执行失败" if block.is_error else "工具执行完成",
            "output_preview": summarize_tool_result_content(block.content),
            "output_details": pretty_json(block.content) if block.content is not None else "",
            "error": bool(block.is_error),
            "finished_at": now_ms(),
        }
        if parent_tool_use_id:
            entry["subagent_id"] = parent_tool_use_id
        self._upsert_timeline_entry(generation, entry, event_type="tool_finished")

    def _update_task_progress(
        self,
        message: TaskProgressMessage,
        generation: int,
    ) -> None:
        entry = {
            "id": f"task-{message.task_id}",
            "entry_type": "agent",
            "kind": "agent",
            "name": message.description,
            "status": "running",
            "summary": "子 Agent 仍在执行",
            "details": (
                f"最近工具: {message.last_tool_name or '无'}\n"
                f"Tokens: {message.usage.get('total_tokens', 0)}\n"
                f"Tool uses: {message.usage.get('tool_uses', 0)}"
            ),
            "usage": serialize_for_json(message.usage),
            "last_tool_name": message.last_tool_name,
        }
        self._upsert_task_entry(
            generation,
            entry_id=f"task-{message.task_id}",
            payload=entry,
            event_type="task_progress",
        )

    def _complete_task(
        self,
        message: TaskNotificationMessage,
        generation: int,
    ) -> None:
        status = {
            "completed": "completed",
            "failed": "failed",
            "stopped": "stopped",
        }.get(message.status, "completed")

        entry = {
            "id": f"task-{message.task_id}",
            "entry_type": "agent",
            "kind": "agent",
            "name": message.summary or message.task_id,
            "status": status,
            "summary": message.summary,
            "details": message.output_file,
            "usage": serialize_for_json(message.usage),
            "finished_at": now_ms(),
        }
        self._upsert_task_entry(
            generation,
            entry_id=f"task-{message.task_id}",
            payload=entry,
            event_type="task_notification",
        )

    def _upsert_task_entry(
        self,
        generation: int,
        *,
        entry_id: str,
        payload: dict[str, Any],
        event_type: str,
    ) -> None:
        payload = {**payload, "id": entry_id}
        self._upsert_timeline_entry(generation, payload, event_type=event_type)

    def _upsert_timeline_entry(
        self,
        generation: int,
        entry: dict[str, Any],
        *,
        event_type: str,
    ) -> None:
        with self._state_lock:
            if generation != self._generation:
                return

            timeline = self._state["timeline"]
            existing = next((item for item in timeline if item["id"] == entry["id"]), None)
            if existing is None:
                merged = {
                    "duration_ms": None,
                    "finished_at": None,
                    **entry,
                }
                timeline.append(merged)
            else:
                existing.update(entry)
                merged = existing

            started_at = merged.get("started_at")
            finished_at = merged.get("finished_at")
            if isinstance(started_at, int) and isinstance(finished_at, int):
                merged["duration_ms"] = max(0, finished_at - started_at)

            payload = copy.deepcopy(merged)

        self._emit({"type": event_type, "entry": payload})

    def _register_skill_mentions(
        self,
        generation: int,
        text: str,
        turn_context: dict[str, Any],
        *,
        source: str,
    ) -> None:
        with self._state_lock:
            if generation != self._generation:
                return
            known_skills = list(self._state["capabilities"].get("known_skills", []))

        for skill_name in match_skill_hints(text, known_skills):
            if skill_name in turn_context["seen_skills"]:
                continue
            turn_context["seen_skills"].add(skill_name)
            entry = {
                "id": make_id("skill"),
                "entry_type": "skill",
                "kind": "skill",
                "name": skill_name,
                "status": "detected",
                "summary": f"从 {source} 内容中检测到 skill 使用痕迹",
                "details": truncate_text(text, limit=800),
                "started_at": now_ms(),
                "finished_at": now_ms(),
                "duration_ms": 0,
            }
            self._upsert_timeline_entry(
                generation,
                entry,
                event_type="skill_activity",
            )

    async def shutdown(self) -> None:
        if self._client is not None:
            try:
                await self._disconnect_client_safely(self._client, quiet=False)
            finally:
                self._client = None
                self._client_generation = None

    def close(self) -> None:
        if self._loop is None:
            return

        future = asyncio.run_coroutine_threadsafe(self.shutdown(), self._loop)
        try:
            future.result(timeout=10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)
