# claude-agent-sdk-demo

一个最小可运行的 Python Demo，用来通过 `claude-agent-sdk` 调起 Claude CLI，并打印当前已加载设置中的可用 skills。

## 项目说明

这个示例做了几件事：

- 读取用户本地 Claude 配置：`~/.claude/settings.json`
- 使用 `claude-agent-sdk` 创建查询请求
- 通过自定义 `SubprocessCLITransport` 打印实际解析到的 Claude CLI 路径
- 向 Claude 发送一个简单 prompt，列出当前可用的 skills

入口文件是 [main.py](/var/fpwork/aujia/hzlinc45-5gl3/claude-agent-sdk-demo/main.py)。

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

```bash
uv run python main.py
```

运行后会：

1. 检查 `~/.claude/settings.json` 是否存在
2. 输出当前解析到的 Claude CLI 路径
3. 请求 Claude 返回当前已加载 settings 中的可用 skills
4. 将返回的文本内容直接打印到终端

## 关键配置

代码中当前使用的 SDK 选项包括：

- `setting_sources=["user", "project"]`
- `permission_mode="bypassPermissions"`
- `max_turns=2`

如果本地没有 Claude 配置文件，程序会直接抛出 `FileNotFoundError`。

## 依赖

项目依赖定义在 [pyproject.toml](/var/fpwork/aujia/hzlinc45-5gl3/claude-agent-sdk-demo/pyproject.toml)：

- `claude-agent-sdk>=0.1.50`

## 适用场景

这个仓库适合用于：

- 验证本地 Claude CLI 与 Python SDK 是否能正常联通
- 参考最小化的 SDK 调用方式
- 在此基础上继续扩展自定义 prompt、transport 或 agent 行为
