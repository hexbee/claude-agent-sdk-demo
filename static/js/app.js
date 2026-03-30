(function () {
  const state = {
    session: null,
    messages: [],
    timeline: [],
    capabilities: {},
    last_error: null,
  };

  let connectionState = "connecting";
  let stream = null;
  let newSessionPending = false;

  const elements = {
    sessionMeta: document.getElementById("session-meta"),
    capabilitiesPanel: document.getElementById("capabilities-panel"),
    errorBanner: document.getElementById("error-banner"),
    connectionPill: document.getElementById("connection-pill"),
    messages: document.getElementById("messages"),
    timeline: document.getElementById("timeline"),
    composer: document.getElementById("composer"),
    composerHint: document.getElementById("composer-hint"),
    messageInput: document.getElementById("message-input"),
    sendButton: document.getElementById("send-button"),
    newSessionButton: document.getElementById("new-session-button"),
  };

  function escapeHtml(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function formatTime(timestamp) {
    if (!timestamp) {
      return "未知";
    }
    return new Date(timestamp).toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function formatDateTime(timestamp) {
    if (!timestamp) {
      return "未知";
    }
    return new Date(timestamp).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function fetchJson(url, options = {}) {
    return fetch(url, {
      headers: {
        "Content-Type": "application/json",
      },
      ...options,
    }).then(async (response) => {
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const message = payload.error || "请求失败。";
        throw new Error(message);
      }
      return payload;
    });
  }

  function upsertById(list, item) {
    const index = list.findIndex((candidate) => candidate.id === item.id);
    if (index >= 0) {
      list[index] = { ...list[index], ...item };
      return list[index];
    }
    list.push(item);
    return item;
  }

  function applySnapshot(snapshot) {
    state.session = snapshot.session || null;
    state.messages = snapshot.messages || [];
    state.timeline = snapshot.timeline || [];
    state.capabilities = snapshot.capabilities || {};
    state.last_error = snapshot.last_error || null;
  }

  function applyEvent(event) {
    switch (event.type) {
      case "session_reset":
        applySnapshot(event.state);
        break;
      case "session_status":
        state.session = event.session || state.session;
        if (Object.prototype.hasOwnProperty.call(event, "last_error")) {
          state.last_error = event.last_error;
        }
        break;
      case "capabilities":
        state.capabilities = event.capabilities || {};
        break;
      case "user_message":
        upsertById(state.messages, event.message);
        break;
      case "assistant_message":
        upsertById(state.messages, event.message);
        break;
      case "assistant_delta": {
        const fallbackMessage = {
          id: event.message_id,
          role: "assistant",
          text: "",
          status: "streaming",
          created_at: event.timestamp,
        };
        const message = upsertById(state.messages, fallbackMessage);
        message.text = `${message.text || ""}${event.delta || ""}`;
        message.status = message.status || "streaming";
        break;
      }
      case "tool_started":
      case "tool_finished":
      case "task_started":
      case "task_progress":
      case "task_notification":
      case "skill_activity":
        upsertById(state.timeline, event.entry);
        break;
      case "turn_result":
        if (state.session && event.result?.session_id) {
          state.session = {
            ...state.session,
            claude_session_id: event.result.session_id,
          };
        }
        break;
      case "error":
        state.last_error = event.error || "发生未知错误。";
        break;
      default:
        break;
    }
  }

  function renderChipList(values, emptyText = "暂无") {
    if (!values || values.length === 0) {
      return `<span class="chip empty">${escapeHtml(emptyText)}</span>`;
    }
    return values
      .map((value) => `<span class="chip">${escapeHtml(value)}</span>`)
      .join("");
  }

  function renderSessionMeta() {
    if (!state.session) {
      elements.sessionMeta.innerHTML = "";
      return;
    }

    elements.sessionMeta.innerHTML = `
      <div class="meta-card">
        <div class="meta-row">
          <span class="meta-label">本地 Session</span>
          <span class="meta-value">${escapeHtml(state.session.id || "未知")}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">Claude Session</span>
          <span class="meta-value">${escapeHtml(state.session.claude_session_id || "尚未建立")}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">状态</span>
          <span class="meta-value">${escapeHtml(state.session.status || "未知")}</span>
        </div>
        <div class="meta-row">
          <span class="meta-label">创建时间</span>
          <span class="meta-value">${escapeHtml(formatDateTime(state.session.created_at))}</span>
        </div>
      </div>
    `;
  }

  function renderCapabilities() {
    const capabilities = state.capabilities || {};
    const mcpServers = capabilities.mcp_servers || [];
    const connectedServers = mcpServers.filter((server) => server.status === "connected");
    const configuredMcp = capabilities.configured_mcp_servers || [];
    const knownSkills = capabilities.known_skills || [];

    const mcpServerMarkup =
      mcpServers.length === 0
        ? `<span class="chip empty">当前未检测到 Claude 侧 MCP</span>`
        : mcpServers
            .map((server) => {
              const label = `${server.name || "未命名"} · ${server.status || "unknown"}`;
              return `<span class="chip">${escapeHtml(label)}</span>`;
            })
            .join("");

    elements.capabilitiesPanel.innerHTML = `
      <div class="capability-card">
        <div class="capability-row">
          <span class="capability-label">Settings</span>
          <span class="capability-value">${escapeHtml(capabilities.settings_path || "未知")}</span>
        </div>
        <div class="capability-row">
          <span class="capability-label">Claude CLI</span>
          <span class="capability-value">${escapeHtml(capabilities.resolved_cli || "待探测")}</span>
        </div>
        <div class="capability-row">
          <span class="capability-label">Settings 状态</span>
          <span class="capability-value">${capabilities.settings_exists ? "可用" : "缺失"}</span>
        </div>
      </div>
      <div class="capability-card">
        <div class="capability-row">
          <span class="capability-label">已知 Skills</span>
          <span class="capability-value chip-list">${renderChipList(knownSkills, "未从 settings 中提取到 skills")}</span>
        </div>
        <div class="capability-row">
          <span class="capability-label">Configured Agents</span>
          <span class="capability-value chip-list">${renderChipList(capabilities.configured_agents || [], "无")}</span>
        </div>
      </div>
      <div class="capability-card">
        <div class="capability-row">
          <span class="capability-label">Live MCP</span>
          <span class="capability-value">${connectedServers.length} / ${mcpServers.length}</span>
        </div>
        <div class="capability-row">
          <span class="capability-label">Configured MCP</span>
          <span class="capability-value chip-list">${renderChipList(configuredMcp, "无")}</span>
        </div>
        <div class="capability-row">
          <span class="capability-label">MCP Servers</span>
          <span class="capability-value chip-list">${mcpServerMarkup}</span>
        </div>
        <div class="capability-row">
          <span class="capability-label">MCP Tools</span>
          <span class="capability-value chip-list">${renderChipList(capabilities.mcp_tools || [], "暂无")}</span>
        </div>
      </div>
    `;
  }

  function renderErrorBanner() {
    if (!state.last_error) {
      elements.errorBanner.classList.add("is-hidden");
      elements.errorBanner.textContent = "";
      return;
    }

    elements.errorBanner.classList.remove("is-hidden");
    elements.errorBanner.textContent = state.last_error;
  }

  function renderConnectionPill() {
    const sessionStatus = state.session?.status || "loading";
    let label = "连接中";

    if (connectionState === "open") {
      label = state.session?.busy ? "执行中" : `已连接 · ${sessionStatus}`;
    } else if (connectionState === "error") {
      label = "事件流重连中";
    }

    elements.connectionPill.textContent = label;
  }

  function renderMessages() {
    const messages = [...state.messages].sort((left, right) => left.created_at - right.created_at);
    if (messages.length === 0) {
      elements.messages.innerHTML = `
        <div class="empty-state">
          还没有对话内容。发送第一条消息后，这里会实时显示 assistant 输出，并在右侧同步展示工具、MCP 与 skills 的执行痕迹。
        </div>
      `;
      return;
    }

    elements.messages.innerHTML = messages
      .map((message) => {
        const role = message.role || "assistant";
        const roleLabel =
          role === "user" ? "User" : role === "assistant" ? "Assistant" : "System";
        const status = message.status === "streaming" ? "生成中..." : "已完成";
        const metaParts = [];
        if (message.model) {
          metaParts.push(message.model);
        }
        if (message.session_id) {
          metaParts.push(`session ${message.session_id}`);
        }

        return `
          <article class="message ${escapeHtml(role)}">
            <div class="message-header">
              <span class="message-role">${escapeHtml(roleLabel)}</span>
              <span class="message-meta">${escapeHtml(
                `${formatTime(message.created_at)}${metaParts.length ? ` · ${metaParts.join(" · ")}` : ""}`
              )}</span>
            </div>
            <div class="message-text">${escapeHtml(message.text || "") || "..."}</div>
            <div class="message-status">${escapeHtml(status)}</div>
          </article>
        `;
      })
      .join("");

    elements.messages.scrollTop = elements.messages.scrollHeight;
  }

  function timelineBadge(entry) {
    if (entry.entry_type === "task") {
      return { label: "TASK", className: "task" };
    }
    if (entry.entry_type === "skill") {
      return { label: "SKILL", className: "skill" };
    }
    if (entry.kind === "mcp") {
      return { label: "MCP", className: "mcp" };
    }
    if (entry.kind === "skill") {
      return { label: "SKILL", className: "skill" };
    }
    return { label: "BUILTIN", className: "builtin" };
  }

  function renderTimeline() {
    const timeline = [...state.timeline].sort((left, right) => {
      const leftAt = left.started_at || left.finished_at || 0;
      const rightAt = right.started_at || right.finished_at || 0;
      return rightAt - leftAt;
    });

    if (timeline.length === 0) {
      elements.timeline.innerHTML = `
        <div class="empty-state">
          右侧会按时间线显示本轮执行轨迹。工具调用会区分内置工具与 MCP，skills 则以 best-effort 方式展示。
        </div>
      `;
      return;
    }

    elements.timeline.innerHTML = timeline
      .map((entry) => {
        const badge = timelineBadge(entry);
        const metaTags = [];

        if (entry.status) {
          metaTags.push(`状态: ${entry.status}`);
        }
        if (entry.duration_ms != null) {
          metaTags.push(`耗时: ${entry.duration_ms} ms`);
        }
        if (entry.task_type) {
          metaTags.push(`任务类型: ${entry.task_type}`);
        }
        if (entry.last_tool_name) {
          metaTags.push(`最近工具: ${entry.last_tool_name}`);
        }

        const details = [];
        if (entry.summary) {
          details.push(`
            <div class="timeline-detail">
              <span class="timeline-detail-label">摘要</span>
              <pre>${escapeHtml(entry.summary)}</pre>
            </div>
          `);
        }
        if (entry.input_details) {
          details.push(`
            <div class="timeline-detail">
              <span class="timeline-detail-label">输入</span>
              <pre>${escapeHtml(entry.input_details)}</pre>
            </div>
          `);
        }
        if (entry.output_details) {
          details.push(`
            <div class="timeline-detail">
              <span class="timeline-detail-label">输出</span>
              <pre>${escapeHtml(entry.output_details)}</pre>
            </div>
          `);
        }
        if (entry.details) {
          details.push(`
            <div class="timeline-detail">
              <span class="timeline-detail-label">详情</span>
              <pre>${escapeHtml(entry.details)}</pre>
            </div>
          `);
        }

        return `
          <details class="timeline-item" ${entry.status === "running" ? "open" : ""}>
            <summary>
              <div class="timeline-title">
                <span class="timeline-badge ${escapeHtml(badge.className)}">${escapeHtml(badge.label)}</span>
                <span class="timeline-name">${escapeHtml(entry.name || entry.id)}</span>
              </div>
              <span class="timeline-status">${escapeHtml(entry.status || "unknown")}</span>
            </summary>
            <div class="timeline-body">
              <div class="timeline-meta">
                ${metaTags.map((tag) => `<span class="timeline-tag">${escapeHtml(tag)}</span>`).join("")}
              </div>
              ${details.join("") || `<div class="timeline-detail"><pre>暂无更多详情</pre></div>`}
            </div>
          </details>
        `;
      })
      .join("");
  }

  function renderComposerState() {
    const session = state.session;
    const disabled =
      !session ||
      session.busy ||
      !session.settings_exists;

    elements.sendButton.disabled = disabled;
    elements.messageInput.disabled = disabled;
    elements.newSessionButton.disabled = newSessionPending;

    if (!session) {
      elements.composerHint.textContent = "正在加载当前 session。";
      return;
    }
    if (!session.settings_exists) {
      elements.composerHint.textContent =
        "未检测到 Claude settings，先准备好 `~/.claude/settings.json`。";
      return;
    }
    if (session.busy) {
      elements.composerHint.textContent = "本轮还在执行中，等待 assistant 或工具链完成。";
      return;
    }
    elements.composerHint.textContent =
      "支持多轮对话。点击“新建 Session”会清空当前上下文并重新开始。";
  }

  function renderAll() {
    renderSessionMeta();
    renderCapabilities();
    renderErrorBanner();
    renderConnectionPill();
    renderMessages();
    renderTimeline();
    renderComposerState();
  }

  async function hydrate() {
    const snapshot = await fetchJson("/api/state");
    applySnapshot(snapshot);
    renderAll();
  }

  function connectStream() {
    if (stream) {
      stream.close();
    }

    connectionState = "connecting";
    renderConnectionPill();

    stream = new EventSource("/api/stream");
    stream.addEventListener("update", (messageEvent) => {
      connectionState = "open";
      const payload = JSON.parse(messageEvent.data);
      applyEvent(payload);
      renderAll();
    });
    stream.addEventListener("ping", () => {
      connectionState = "open";
      renderConnectionPill();
    });
    stream.onerror = () => {
      connectionState = "error";
      renderConnectionPill();
    };
  }

  async function handleNewSession() {
    try {
      newSessionPending = true;
      renderComposerState();
      const payload = await fetchJson("/api/session/new", {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (payload.state) {
        applySnapshot(payload.state);
        renderAll();
      }
    } catch (error) {
      state.last_error = error.message;
      renderErrorBanner();
    } finally {
      newSessionPending = false;
      renderComposerState();
    }
  }

  async function handleSendMessage(event) {
    event.preventDefault();
    const text = elements.messageInput.value.trim();
    if (!text) {
      return;
    }

    try {
      await fetchJson("/api/message", {
        method: "POST",
        body: JSON.stringify({ message: text }),
      });
      elements.messageInput.value = "";
      elements.messageInput.focus();
    } catch (error) {
      state.last_error = error.message;
      renderErrorBanner();
      renderComposerState();
    }
  }

  function bindEvents() {
    elements.composer.addEventListener("submit", handleSendMessage);
    elements.newSessionButton.addEventListener("click", handleNewSession);
  }

  async function bootstrap() {
    bindEvents();
    await hydrate();
    connectStream();
  }

  bootstrap().catch((error) => {
    state.last_error = error.message;
    renderAll();
  });
})();
