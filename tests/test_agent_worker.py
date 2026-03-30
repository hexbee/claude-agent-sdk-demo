from __future__ import annotations

import unittest

from agent_worker import (
    classify_tool_kind,
    extract_server_skill_hints,
    extract_stream_text_delta,
    match_skill_hints,
    summarize_tool_result_content,
)


class StreamEventTests(unittest.TestCase):
    def test_extracts_text_from_block_start(self) -> None:
        event = {
            "type": "content_block_start",
            "content_block": {
                "type": "text",
                "text": "你好",
            },
        }
        self.assertEqual(extract_stream_text_delta(event), "你好")

    def test_extracts_text_from_block_delta(self) -> None:
        event = {
            "type": "content_block_delta",
            "delta": {
                "type": "text_delta",
                "text": "继续输出",
            },
        }
        self.assertEqual(extract_stream_text_delta(event), "继续输出")

    def test_ignores_non_text_stream_event(self) -> None:
        event = {
            "type": "content_block_delta",
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"foo":"bar"}',
            },
        }
        self.assertIsNone(extract_stream_text_delta(event))


class ToolClassificationTests(unittest.TestCase):
    def test_classifies_mcp_tool_first(self) -> None:
        result = classify_tool_kind(
            "context7_resolve-library-id",
            mcp_tool_names={"context7_resolve-library-id"},
            known_skills={"debugging"},
        )
        self.assertEqual(result, "mcp")

    def test_classifies_prefixed_mcp_tool_name(self) -> None:
        result = classify_tool_kind(
            "mcp__context7__query-docs",
            mcp_tool_names={"query-docs"},
            known_skills={"brainstorming"},
        )
        self.assertEqual(result, "mcp")

    def test_classifies_skill_by_name_hint(self) -> None:
        result = classify_tool_kind(
            "brainstorming",
            mcp_tool_names=set(),
            known_skills={"brainstorming"},
        )
        self.assertEqual(result, "skill")

    def test_defaults_to_builtin(self) -> None:
        result = classify_tool_kind(
            "Bash",
            mcp_tool_names=set(),
            known_skills={"brainstorming"},
        )
        self.assertEqual(result, "builtin")


class SkillHintTests(unittest.TestCase):
    def test_matches_skill_names_case_insensitively(self) -> None:
        matches = match_skill_hints(
            "Agent 会尝试调用 BrainStorming skill 继续处理。",
            ["brainstorming", "debugging"],
        )
        self.assertEqual(matches, ["brainstorming"])

    def test_extracts_skill_hints_from_server_info(self) -> None:
        hints = extract_server_skill_hints(
            {
                "commands": [
                    {"name": "brainstorming", "description": "Creative helper (user)"},
                    {"name": "compact", "description": "Clear conversation history"},
                    {"name": "claude-api", "description": "API builder (bundled)"},
                ]
            }
        )
        self.assertEqual(hints, ["brainstorming", "claude-api"])

    def test_summarizes_non_string_tool_result(self) -> None:
        summary = summarize_tool_result_content({"status": "ok", "count": 2})
        self.assertIn('"status": "ok"', summary)


if __name__ == "__main__":
    unittest.main()
