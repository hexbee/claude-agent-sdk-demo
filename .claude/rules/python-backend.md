---
paths:
  - "app.py"
  - "main.py"
  - "agent_*.py"
---

# Python 后端规则

- Flask 路由保持精简，只负责请求编排，不承载复杂业务逻辑。
- Claude SDK 相关的运行时、会话管理和流式处理逻辑，优先放在独立的 runtime 或 worker 模块中。
- 暴露给前端的状态对象应保持可序列化，避免塞入复杂对象实例。
- 修改后端代码时，尽量不要破坏现有的流式响应和非阻塞处理方式。
- 完成 Python 改动后，如条件允许，使用 `uv run python -m compileall <changed files>` 做一次轻量语法校验。
