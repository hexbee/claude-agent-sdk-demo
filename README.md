# claude-agent-sdk-demo

一个最小可运行的 Python Demo，包含：

- 一个 CLI 示例，用来通过 `claude-agent-sdk` 调起 Claude CLI
- 一个基于 Flask + SSE + 原生 JS 的本地 Agent Web 页面
- 对多轮对话、工具调用（内置 / MCP）和 Skills 的实时可视化

## 项目结构

```
claude-agent-sdk-demo/
├── app.py              # Flask 应用，REST + SSE 接口
├── agent_worker.py     # 单 session 状态机，管理 claude-agent-sdk 生命周期
├── agent_runtime.py    # SDK 选项构建、settings 读取、CLI 路径解析
├── main.py             # CLI 独立入口（不含 Web）
├── templates/
│   └── index.html      # 单页 HTML 模板
└── static/
    ├── css/app.css     # 全量样式
    └── js/app.js       # 前端逻辑（SSE 监听、增量渲染、Markdown）
```

## 功能说明

### CLI Demo (`main.py`)

- 读取 `~/.claude/settings.json`，解析本地 Claude CLI 路径
- 通过 `claude-agent-sdk` 发送单次 prompt，打印 Assistant 回复

### Flask Agent Web UI (`app.py`)

三列布局，实时展示 Agent 执行过程：

**左侧边栏**
- Session 状态（ID、Claude Session ID、创建时间）
- 连接状态指示（已连接 / 执行中 / 错误，附动画）
- `Loaded Instructions` 面板：显示当前 Claude session 通过 `InstructionsLoaded` hook 观测到的已加载指令文件
- Capabilities 折叠面板：环境配置、MCP 服务器连接状态、Skills & Agents 列表

**中间主区**
- 多轮聊天，用户消息与 Assistant 回复实时流式展示
- Assistant 回复支持 **Markdown 渲染**（代码块、表格、列表等）
- 流式生成时显示闪烁光标动画
- 输入框自适应高度，`Enter` 发送，`Shift+Enter` 换行

**右侧时间线**
- 工具调用（内置工具 / MCP 工具）按时间线展示
- 每项可展开查看调用输入、输出、耗时
- 区分 `TOOL`（内置）、`MCP`、`SKILL`、`TASK` 四种类型并分色标注
- 执行中条目自动展开，完成后保留展开状态

**后端 API**

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 主页面 |
| `GET` | `/api/state` | 获取当前完整状态快照 |
| `POST` | `/api/session/new` | 新建 Session，重置对话上下文 |
| `POST` | `/api/message` | 发送用户消息 |
| `GET` | `/api/stream` | SSE 事件流（实时推送状态变化） |

## 环境要求

- Python 3.11+
- 已安装并可用的 Claude CLI
- `~/.claude/settings.json` 存在且有效
- 建议使用 `uv` 管理依赖

## 安装与运行

```bash
# 安装依赖
uv sync

# 启动 Web 页面
uv run python app.py
# 访问 http://127.0.0.1:5000

# 或仅运行 CLI Demo
uv run python main.py
```

## 关键 SDK 配置

`agent_runtime.py` 中的默认运行时选项：

| 选项 | 值 | 说明 |
|------|----|------|
| `setting_sources` | `["user", "project"]` | 加载用户和项目级 settings |
| `permission_mode` | `bypassPermissions` | 跳过权限确认 |
| `max_turns` | `8` | 单次对话最大轮数 |
| `include_partial_messages` | `True` | 开启流式增量消息 |

## InstructionsLoaded 观测

- 项目内置了 `.claude/settings.json`，注册 `InstructionsLoaded` hook
- hook 脚本位于 `.claude/hooks/log_instructions_loaded.py`
- 运行时日志会写入 `.claude/runtime/instructions_loaded.jsonl`（已加入 `.gitignore`）
- 左侧 `Loaded Instructions` 面板会按当前 Web session 的创建时间过滤并汇总这些事件，展示 `CLAUDE.md`、`.claude/rules/*.md` 和 `@include` 导入文件

## 边界说明

- Session 保存在 Flask 进程内存中，重启后不保留历史
- MCP 服务器连接状态来自 Claude CLI 真实握手结果，未连接的服务不会出现
- Skill 调用轨迹为 best-effort 识别，基于文本匹配，不保证完全准确

## 依赖

```toml
claude-agent-sdk >= 0.1.50
flask >= 3.1.3
```

## 适用场景

- 验证本地 Claude CLI 与 Python SDK 是否正常联通
- 参考最小化 SDK 调用方式
- 快速搭建本地 Agent 调试页面，观察工具调用和 MCP 行为
- 在此基础上扩展自定义 prompt、session 持久化或更完整的前端交互
