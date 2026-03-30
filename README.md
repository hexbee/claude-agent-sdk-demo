# claude-agent-sdk-demo

一个最小可运行的 Python Demo，包含：

- 一个 CLI 示例，用来通过 `claude-agent-sdk` 调起 Claude CLI
- 一个基于 Flask + HTML + CSS + JS 的本地 Agent 页面
- 对多轮对话、工具调用、MCP 工具和 skills 的可视化展示

## 项目说明

这个仓库现在主要包含两部分：

1. CLI Demo

- 读取用户本地 Claude 配置：`~/.claude/settings.json`
- 使用 `claude-agent-sdk` 创建查询请求
- 打印实际解析到的 Claude CLI 路径
- 向 Claude 发送一个简单 prompt，列出当前可用的 skills

2. Flask Agent UI

- 支持单个活动 session 的多轮对话
- 支持 `新建 Session`
- Assistant 文本流式展示
- 右侧执行时间线展示工具调用、任务进度和工具结果
- 自动区分 Claude 侧 MCP 工具与内置工具
- skills 以 best-effort 方式展示：优先显示 Claude 初始化信息和已知 skill 名称，并在时间线上标记可识别的 skill 痕迹

入口文件是 [main.py](/var/fpwork/aujia/hzlinc45-5gl3/claude-agent-sdk-demo/main.py)。
Web 入口文件是 [app.py](/var/fpwork/aujia/hzlinc45-5gl3/claude-agent-sdk-demo/app.py)。

## 环境要求

- Python 3.11 或更高版本
- 已安装并可用的 Claude CLI
- 本地存在 Claude 配置文件：`~/.claude/settings.json`
- 建议使用 `uv` 安装依赖和运行

## 安装依赖

```bash
uv sync
```

## 运行方式

CLI:

```bash
uv run python main.py
```

Web 页面:

```bash
uv run python app.py
```

启动 Web 页面后，访问 [http://127.0.0.1:5000](http://127.0.0.1:5000)。

## 关键配置

共享运行时当前使用的 SDK 选项包括：

- `setting_sources=["user", "project"]`
- `permission_mode="bypassPermissions"`
- `max_turns=8`
- `include_partial_messages=True`（Web 页面流式模式）

如果本地没有 Claude 配置文件，CLI 会直接抛出 `FileNotFoundError`；Web 页面会在状态区显示配置缺失。

## 依赖

项目依赖定义在 [pyproject.toml](/var/fpwork/aujia/hzlinc45-5gl3/claude-agent-sdk-demo/pyproject.toml)：

- `claude-agent-sdk>=0.1.50`
- `flask>=3.1.3`

## 适用场景

## Web 页面说明

页面主要分为三块：

- 左侧：session 状态、settings 路径、MCP 与 skills 能力概览
- 中间：聊天对话区和输入框
- 右侧：执行时间线，可展开查看工具输入、输出、耗时和任务信息

当前边界：

- session 保存在 Flask 进程内，重启服务后不会保留
- 外部 MCP 只展示 Claude Agent SDK / Claude CLI 真正连接成功的服务
- skill 调用轨迹是 best-effort，不保证每一次都能被精确识别

## 适用场景

这个仓库适合用于：

- 验证本地 Claude CLI 与 Python SDK 是否能正常联通
- 参考最小化的 SDK 调用方式
- 快速搭一个本地 Agent 调试页面
- 在此基础上继续扩展自定义 prompt、transport、session 存储或更完整的前端交互
