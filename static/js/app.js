/* global marked */
(function () {
  "use strict";

  // ─── State ───────────────────────────────────────────────────────────────────
  const state = {
    session: null,
    messages: [],
    timeline: [],
    capabilities: {},
    last_error: null,
  };

  let connectionState = "connecting"; // connecting | open | error
  let sse = null;
  let newSessionLock = false;

  // ─── DOM refs ────────────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);

  const el = {
    connectionDot:  $("connection-dot"),
    errorArea:      $("error-area"),
    errorText:      $("error-text"),
    loadedInstructions: $("loaded-instructions"),
    capabilities:   $("capabilities"),
    statusBadge:    $("status-badge"),
    sessionIds:     $("session-ids"),
    turnStats:      $("turn-stats"),
    messages:       $("messages"),
    timeline:       $("timeline"),
    timelineCount:  $("timeline-count"),
    composer:       $("composer"),
    composerHint:   $("composer-hint"),
    msgInput:       $("msg-input"),
    sendBtn:        $("send-btn"),
    newSessionBtn:  $("new-session-btn"),
  };

  // ─── Helpers ─────────────────────────────────────────────────────────────────
  function esc(v) {
    return String(v)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function fmtTime(ts) {
    if (!ts) return "";
    return new Date(ts).toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function fmtMs(ms) {
    if (ms == null) return "";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function renderMd(text) {
    if (typeof marked !== "undefined") {
      try {
        return marked.parse(String(text || ""), { breaks: true, gfm: true });
      } catch (_) { /* fall through */ }
    }
    return `<span style="white-space:pre-wrap">${esc(text)}</span>`;
  }

  async function fetchJson(url, opts = {}) {
    const res = await fetch(url, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
    return body;
  }

  function upsert(list, item) {
    const idx = list.findIndex((x) => x.id === item.id);
    if (idx >= 0) {
      list[idx] = { ...list[idx], ...item };
      return list[idx];
    }
    list.push(item);
    return item;
  }

  // ─── State apply ─────────────────────────────────────────────────────────────
  function applySnapshot(snap) {
    state.session      = snap.session      || null;
    state.messages     = snap.messages     || [];
    state.timeline     = snap.timeline     || [];
    state.capabilities = snap.capabilities || {};
    state.last_error   = snap.last_error   || null;
  }

  function applyEvent(ev) {
    switch (ev.type) {
      case "session_reset":
        applySnapshot(ev.state);
        break;

      case "session_status":
        if (ev.session) state.session = ev.session;
        if (Object.prototype.hasOwnProperty.call(ev, "last_error"))
          state.last_error = ev.last_error;
        break;

      case "capabilities":
        state.capabilities = ev.capabilities || {};
        break;

      case "user_message":
      case "assistant_message":
        upsert(state.messages, ev.message);
        break;

      case "assistant_delta": {
        const placeholder = {
          id: ev.message_id,
          role: "assistant",
          text: "",
          status: "streaming",
          created_at: ev.timestamp,
        };
        const msg = upsert(state.messages, placeholder);
        msg.text = `${msg.text || ""}${ev.delta || ""}`;
        msg.status = msg.status || "streaming";
        break;
      }

      case "tool_started":
      case "tool_finished":
      case "task_started":
      case "task_progress":
      case "task_notification":
      case "skill_activity":
        upsert(state.timeline, ev.entry);
        break;

      case "turn_result":
        if (ev.result && state.session) {
          if (ev.result.session_id)
            state.session = { ...state.session, claude_session_id: ev.result.session_id };
        }
        break;

      case "error":
        state.last_error = ev.error || "发生未知错误。";
        break;
    }
  }

  // ─── Render: Connection dot ───────────────────────────────────────────────────
  function renderConnection() {
    el.connectionDot.className = "connection-dot";
    if (connectionState === "open")       el.connectionDot.classList.add("is-open");
    else if (connectionState === "error") el.connectionDot.classList.add("is-error");
    else                                  el.connectionDot.classList.add("is-connecting");

    const labels = { open: "已连接", error: "重连中", connecting: "连接中" };
    el.connectionDot.title = labels[connectionState] || "未知";
  }

  // ─── Render: Status badge ─────────────────────────────────────────────────────
  function renderStatusBadge() {
    const s = state.session;
    if (!s) {
      el.statusBadge.textContent = "加载中";
      el.statusBadge.className = "status-badge";
      return;
    }
    const status = s.busy ? "running" : (s.status || "");
    const labels = {
      idle:          "就绪",
      running:       "执行中",
      error:         "错误",
      needs_config:  "未配置",
      connecting:    "连接中",
    };
    el.statusBadge.textContent = labels[status] || status;
    el.statusBadge.className = `status-badge is-${status}`;
  }

  // ─── Render: Session IDs (merged, low-profile) ───────────────────────────────
  function renderSessionIds() {
    const s = state.session;
    if (!s) {
      el.sessionIds.innerHTML = '<div class="info-skeleton" style="width: 120px; height: 14px;"></div>';
      return;
    }

    const sessionId = s.id ? `…${s.id.slice(-8)}` : "—";
    const claudeId = s.claude_session_id ? `…${s.claude_session_id.slice(-8)}` : "未建立";

    el.sessionIds.innerHTML = `
      <div class="session-id-item">
        <span class="session-id-label">会话:</span>
        <span class="session-id-value" title="${esc(s.id || "")}">${esc(sessionId)}</span>
      </div>
      <span class="session-id-divider" style="color: var(--text-3); opacity: 0.5;">|</span>
      <div class="session-id-item">
        <span class="session-id-label">Claude:</span>
        <span class="session-id-value" title="${esc(s.claude_session_id || "")}">${esc(claudeId)}</span>
      </div>`;
  }

  // ─── Render: Error ────────────────────────────────────────────────────────────
  function renderError() {
    if (state.last_error) {
      el.errorArea.classList.remove("is-hidden");
      el.errorText.textContent = state.last_error;
    } else {
      el.errorArea.classList.add("is-hidden");
      el.errorText.textContent = "";
    }
  }

  // ─── Render: Loaded instructions ─────────────────────────────────────────────
  function renderLoadedInstructions() {
    const caps = state.capabilities || {};
    const items = caps.loaded_instructions || [];
    const error = caps.loaded_instructions_error || "";

    if (error) {
      el.loadedInstructions.innerHTML = `
        <div class="instruction-empty">
          <span class="instruction-empty-text">${esc(error)}</span>
        </div>`;
      return;
    }

    if (items.length === 0) {
      el.loadedInstructions.innerHTML = `
        <div class="instruction-empty">
          <span class="instruction-empty-text">等待 InstructionsLoaded 事件</span>
        </div>`;
      return;
    }

    el.loadedInstructions.innerHTML = items.map((item) => {
      const sourceTag = item.memory_type
        ? `<span class="tag tag-blue">${esc(item.memory_type)}</span>`
        : "";
      const reasonTags = (item.load_reasons || [])
        .map((reason) => `<span class="tag tag-muted">${esc(reason)}</span>`)
        .join("");

      let detailText = "";
      if (item.parent_display_path) {
        detailText = `include 来自 ${item.parent_display_path}`;
      } else if (item.trigger_display_path) {
        detailText = `由 ${item.trigger_display_path} 触发`;
      } else if ((item.globs || []).length > 0) {
        detailText = `paths: ${(item.globs || []).join(", ")}`;
      }

      return `
        <div class="instruction-item">
          <div class="instruction-head">
            <div class="instruction-path" title="${esc(item.file_path || item.display_path || "")}">
              ${esc(item.display_path || item.file_path || "未知文件")}
            </div>
            <div class="instruction-tags">
              ${sourceTag}
              ${reasonTags}
            </div>
          </div>
          <div class="instruction-meta">
            <span>${esc(fmtTime(item.loaded_at) || "刚刚")}</span>
            ${item.load_count > 1 ? `<span>${esc(`加载 ${item.load_count} 次`)}</span>` : ""}
          </div>
          ${detailText ? `<div class="instruction-detail">${esc(detailText)}</div>` : ""}
        </div>`;
    }).join("");
  }

  // ─── Render: Capabilities ────────────────────────────────────────────────────
  function tagHtml(items, cls = "tag-blue", empty = "暂无") {
    if (!items || items.length === 0) return `<span class="tag tag-muted">${esc(empty)}</span>`;
    return items.map((v) => `<span class="tag ${cls}">${esc(v)}</span>`).join("");
  }

  function mcpToolListHtml(items, empty = "暂无工具") {
    if (!items || items.length === 0) {
      return `<span class="mcp-tool-name mcp-tool-name--empty">${esc(empty)}</span>`;
    }
    return items
      .map((v) => `<span class="mcp-tool-name" title="${esc(v)}">${esc(v)}</span>`)
      .join("");
  }

  function capGroup(title, countOrBadge, bodyHtml, open = false) {
    return `
      <details class="cap-group" ${open ? "open" : ""}>
        <summary>
          <div class="cap-group-title">
            ${esc(title)}
            <span class="cap-count">${esc(String(countOrBadge))}</span>
          </div>
          <svg class="cap-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none">
            <path d="M9 18l6-6-6-6" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>
          </svg>
        </summary>
        <div class="cap-body">${bodyHtml}</div>
      </details>`;
  }

  function capTagPanel(items, cls, emptyText) {
    if (!items || items.length === 0) {
      return `<div class="cap-tag-panel"><span class="cap-empty-text">${esc(emptyText)}</span></div>`;
    }
    return `
      <div class="cap-tag-panel">
        <div class="cap-tag-list">${tagHtml(items, cls, emptyText)}</div>
      </div>`;
  }

  function renderCapabilities() {
    const caps = state.capabilities || {};
    const mcpServers   = caps.mcp_servers || [];
    const knownSkills  = caps.known_skills || [];
    const agents       = caps.configured_agents || [];

    const connected = mcpServers.filter((s) => s.status === "connected").length;
    const mcpToolOpenStates = {};
    el.capabilities.querySelectorAll("details.mcp-server-tools-more").forEach((d) => {
      const serverKey = d.dataset.serverKey;
      if (serverKey) mcpToolOpenStates[serverKey] = d.open;
    });

    // Config group
    const configBody = `
      <div class="cap-row">
        <span class="cap-key">Settings</span>
        <span class="cap-val">${caps.settings_exists ? "✓ 可用" : "✗ 缺失"}</span>
      </div>
      <div class="cap-row">
        <span class="cap-key">CLI</span>
        <span class="cap-val">${esc(caps.resolved_cli ? "已解析" : "待探测")}</span>
      </div>`;

    // MCP group
    const mcpServerRows = mcpServers.length
      ? mcpServers.map((srv) => {
          const tools = srv.tools || [];
          const status = srv.status || "?";
          const scope = srv.scope || "";
          const displayName = srv.name || srv.server_info?.name || "?";
          const titleText =
            srv.server_info?.name && srv.server_info.name !== displayName
              ? `服务器标识: ${srv.server_info.name}`
              : displayName;
          const serverKey = srv.name || displayName;
          const visibleTools = tools.slice(0, 2);
          const hiddenTools = tools.slice(2);
          const isExpanded = Boolean(mcpToolOpenStates[serverKey]);
          const moreTools = hiddenTools.length
            ? `
              <details class="mcp-server-tools-more" data-server-key="${esc(serverKey)}" ${isExpanded ? "open" : ""}>
                <summary class="mcp-server-tools-toggle">
                  <span class="mcp-server-tools-toggle-collapsed">还有 ${hiddenTools.length} 个工具</span>
                  <span class="mcp-server-tools-toggle-expanded">收起</span>
                </summary>
                <div class="mcp-server-tools mcp-server-tools-hidden">
                  ${mcpToolListHtml(hiddenTools, "暂无工具")}
                </div>
              </details>`
            : "";
          return `
            <div class="mcp-server-card">
              <div class="mcp-server-head">
                <div class="mcp-server-title" title="${esc(titleText)}">${esc(displayName)}</div>
                <div class="mcp-server-meta">
                  <span class="mcp-server-meta-item mcp-server-meta-item--status is-${esc(status)}">${esc(status)}</span>
                  <span class="mcp-server-meta-item">${tools.length} tools</span>
                  ${scope ? `<span class="mcp-server-meta-item">${esc(scope)}</span>` : ""}
                </div>
              </div>
              <div class="mcp-server-tools">
                ${mcpToolListHtml(visibleTools, "暂无工具")}
              </div>
              ${moreTools}
            </div>`;
        }).join("")
      : `<div class="cap-row"><span class="cap-key" style="color:var(--text-3)">暂无已连接的 MCP</span></div>`;

    const mcpBody = `${mcpServerRows}`;

    const skillsBody = capTagPanel(knownSkills, "tag-orange", "未从配置提取到");
    const agentsBody = capTagPanel(agents, "tag-purple", "未配置");

    // Snapshot current open states before replacing DOM (key = group title)
    const openStates = {};
    el.capabilities.querySelectorAll("details.cap-group").forEach((d) => {
      const title = d.querySelector(".cap-group-title")?.firstChild?.textContent?.trim();
      if (title) openStates[title] = d.open;
    });

    const defaultOpen = (title, fallback) =>
      Object.prototype.hasOwnProperty.call(openStates, title) ? openStates[title] : fallback;

    el.capabilities.innerHTML =
      capGroup("环境配置", caps.settings_exists ? "OK" : "!", configBody, defaultOpen("环境配置", true)) +
      capGroup("MCP 服务器", `${connected}/${mcpServers.length}`, mcpBody, defaultOpen("MCP 服务器", mcpServers.length > 0)) +
      capGroup("Skills", knownSkills.length, skillsBody, defaultOpen("Skills", true)) +
      capGroup("Agents", agents.length, agentsBody, defaultOpen("Agents", true));
  }

  // ─── Render: Messages ────────────────────────────────────────────────────────
  let prevMessageCount = 0;

  function renderMessages() {
    const msgs = [...state.messages].sort((a, b) => a.created_at - b.created_at);

    if (msgs.length === 0) {
      if (prevMessageCount !== 0) {
        el.messages.innerHTML = `
          <div class="empty-hint">
            <div class="empty-hint-icon">
              <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2v10z" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
              </svg>
            </div>
            <p class="empty-hint-title">开始对话</p>
            <p class="empty-hint-desc">发送消息后，Assistant 的回复会在这里实时流式展示，工具与 MCP 调用轨迹会同步出现在右侧时间线。</p>
          </div>`;
        prevMessageCount = 0;
      }
      return;
    }

    const wasAtBottom =
      el.messages.scrollTop + el.messages.clientHeight >=
      el.messages.scrollHeight - 60;

    // Incremental update: update/append each message DOM node
    msgs.forEach((msg) => {
      const existing = el.messages.querySelector(`[data-msg-id="${CSS.escape(msg.id)}"]`);
      const html = buildMsgHtml(msg);
      if (existing) {
        // Only redraw if content changed (streaming delta)
        const newEl = htmlToElement(html);
        if (existing.dataset.msgHash !== msgHash(msg)) {
          existing.replaceWith(newEl);
        }
      } else {
        el.messages.appendChild(htmlToElement(html));
      }
    });

    // Remove stale nodes
    el.messages.querySelectorAll("[data-msg-id]").forEach((node) => {
      if (!msgs.find((m) => m.id === node.dataset.msgId)) {
        node.remove();
      }
    });

    prevMessageCount = msgs.length;

    if (wasAtBottom) {
      el.messages.scrollTop = el.messages.scrollHeight;
    }
  }

  function msgHash(msg) {
    return `${msg.status}:${(msg.text || "").length}:${msg.model || ""}:${msg.subagent_id || ""}`;
  }

  function buildMsgHtml(msg) {
    const role = msg.role || "assistant";
    const roleLabel = role === "user" ? "You" : role === "assistant" ? "Claude" : "System";
    const streaming = msg.status === "streaming";
    const isSubagent = Boolean(msg.subagent_id);

    const metaParts = [fmtTime(msg.created_at)];
    if (msg.model) metaParts.push(msg.model.replace("claude-", "").replace(/-\d{8}$/, ""));

    const subagentBadge = isSubagent
      ? `<span class="msg-subagent-badge" title="来自 sub-agent">↳ agent</span>`
      : "";

    let bodyContent;
    if (role === "user") {
      bodyContent = `<div class="md-content"><span style="white-space:pre-wrap">${esc(msg.text || "")}</span></div>`;
    } else {
      const html = renderMd(msg.text || (streaming ? "" : "…"));
      const cursor = streaming ? '<span class="streaming-cursor"></span>' : "";
      bodyContent = `<div class="md-content">${html}${cursor}</div>`;
    }

    return `
      <div class="msg msg--${esc(role)}" data-msg-id="${esc(msg.id)}" data-msg-hash="${esc(msgHash(msg))}">
        <div class="msg-meta">
          <span class="msg-role">${esc(roleLabel)}</span>
          ${subagentBadge}
          <span>${esc(metaParts.join(" · "))}</span>
        </div>
        <div class="msg-body">${bodyContent}</div>
      </div>`;
  }

  function htmlToElement(html) {
    const t = document.createElement("template");
    t.innerHTML = html.trim();
    return t.content.firstChild;
  }

  // ─── Render: Timeline ────────────────────────────────────────────────────────
  let prevTimelineCount = 0;
  // Tracks entries that were auto-opened because they were running.
  // Persists across renders so we can detect when a running→completed transition happens.
  let tlAutoOpenedIds = new Set();

  function renderTimeline() {
    const entries = [...state.timeline].sort((a, b) => {
      const at = a.started_at || a.finished_at || 0;
      const bt = b.started_at || b.finished_at || 0;
      return bt - at;
    });

    if (entries.length === 0) {
      if (prevTimelineCount !== 0) {
        el.timeline.innerHTML = `
          <div class="empty-hint empty-hint--sm">
            <p class="empty-hint-desc">工具调用、MCP 请求和 Skills 使用将在此处按时间顺序展示。</p>
          </div>`;
        prevTimelineCount = 0;
      }
      tlAutoOpenedIds = new Set();
      el.timelineCount.classList.add("is-hidden");
      return;
    }

    // Build parent→children map first so we can check childRunning below.
    const idSet = new Set(entries.map((e) => e.id));
    const childMap = {};
    const childIdSet = new Set();

    entries.forEach((entry) => {
      let parentId = entry.subagent_id || null;
      if (!parentId && entry.tool_use_id && idSet.has(entry.tool_use_id)) {
        parentId = entry.tool_use_id;
      }
      if (parentId) {
        (childMap[parentId] = childMap[parentId] || []).push(entry);
        childIdSet.add(entry.id);
      }
    });

    // Snapshot manually-opened entries using the PREVIOUS render's autoOpenedIds.
    // Any entry that is open AND was auto-opened last render is skipped —
    // this correctly handles the running→completed transition where the DOM
    // still has the details open but the entry is no longer running.
    const openIds = new Set();
    el.timeline.querySelectorAll("details[data-tl-id]").forEach((n) => {
      if (n.open && !tlAutoOpenedIds.has(n.dataset.tlId)) {
        openIds.add(n.dataset.tlId);
      }
    });

    // Recompute autoOpenedIds for THIS render cycle.
    tlAutoOpenedIds = new Set();
    entries.forEach((entry) => {
      const isRunning =
        entry.status === "running" ||
        (childMap[entry.id] || []).some((c) => c.status === "running");
      if (isRunning) tlAutoOpenedIds.add(entry.id);
    });

    const rootEntries = entries.filter((e) => !childIdSet.has(e.id));

    el.timelineCount.textContent = entries.length;
    el.timelineCount.classList.remove("is-hidden");

    el.timeline.innerHTML = rootEntries
      .map((entry) => {
        const children = (childMap[entry.id] || []).sort(
          (a, b) => (a.started_at || 0) - (b.started_at || 0)
        );
        return buildTlHtml(entry, children, openIds);
      })
      .join("");

    prevTimelineCount = entries.length;
  }

  function tlHash(entry) {
    return `${entry.status}:${entry.duration_ms}:${entry.summary || ""}`;
  }

  function buildTlHtml(entry, children = [], openIds = new Set()) {
    const kindMap = {
      task:    "task",
      skill:   "skill",
      mcp:     "mcp",
      builtin: "builtin",
      agent:   "agent",
    };
    const kind =
      entry.entry_type === "agent" ? "agent"  :
      entry.entry_type === "task"  ? "task"   :
      entry.entry_type === "skill" ? "skill"  :
      kindMap[entry.kind] || "builtin";

    const badgeLabels = {
      task: "TASK", skill: "SKILL", mcp: "MCP", builtin: "TOOL", agent: "AGENT",
    };

    const statusIcon =
      entry.status === "running"   ? "↻" :
      entry.status === "completed" ? "✓" :
      entry.status === "failed"    ? "✗" :
      entry.status === "detected"  ? "◈" : "○";

    const statusClass = `status-${entry.status || "unknown"}`;

    const tags = [];
    if (entry.status) tags.push(statusTag(entry.status));
    if (entry.duration_ms != null) tags.push(`<span class="tag tag-muted">${fmtMs(entry.duration_ms)}</span>`);
    if (entry.task_type)   tags.push(`<span class="tag tag-muted">${esc(entry.task_type)}</span>`);
    if (entry.agent_type && entry.agent_type !== entry.task_type) tags.push(`<span class="tag tag-teal">${esc(entry.agent_type)}</span>`);
    if (entry.last_tool_name) tags.push(`<span class="tag tag-muted">↳ ${esc(entry.last_tool_name)}</span>`);

    const details = [];
    if (entry.summary) {
      details.push(`<div><div class="tl-detail-label">摘要</div><pre class="tl-pre">${esc(entry.summary)}</pre></div>`);
    }
    if (entry.input_details) {
      details.push(`<div><div class="tl-detail-label">输入</div><pre class="tl-pre">${esc(entry.input_details)}</pre></div>`);
    }
    if (entry.output_details) {
      details.push(`<div><div class="tl-detail-label">输出</div><pre class="tl-pre">${esc(entry.output_details)}</pre></div>`);
    }
    if (entry.details && entry.details !== entry.summary) {
      details.push(`<div><div class="tl-detail-label">详情</div><pre class="tl-pre">${esc(entry.details)}</pre></div>`);
    }

    const hasChildren = children.length > 0;
    const childRunning = children.some((c) => c.status === "running");
    const isOpen = openIds.has(entry.id) || entry.status === "running" || childRunning;

    // Nested children section (sub-agent steps)
    const childrenHtml = hasChildren
      ? `<div class="tl-children">
          ${children.map((child) => buildTlHtml(child, [], openIds)).join("")}
        </div>`
      : "";

    // Small badge on the summary row showing number of nested steps
    const childCountBadge = hasChildren
      ? `<span class="tl-child-count">${children.length} 步</span>`
      : "";

    return `
      <details class="tl-item kind-${esc(kind)} is-${esc(entry.status || "unknown")}"
               data-tl-id="${esc(entry.id)}"
               ${isOpen ? "open" : ""}>
        <summary class="tl-summary ${statusClass}"${entry.subagent_type ? ` title="${esc(entry.subagent_type)}"` : ""}>
          <div class="tl-kind-dot"></div>
          <span class="tl-name">${esc(entry.name || entry.id)}</span>
          ${childCountBadge}
          <span class="tl-badge">${esc(badgeLabels[kind] || "TOOL")}</span>
          <span class="tl-status-icon">${statusIcon}</span>
          ${entry.duration_ms != null
            ? `<span class="tl-duration">${fmtMs(entry.duration_ms)}</span>`
            : ""}
        </summary>
        <div class="tl-body">
          ${tags.length ? `<div class="tl-tags">${tags.join("")}</div>` : ""}
          ${details.join("") || (!hasChildren ? `<div style="color:var(--text-3);font-size:12px">暂无详情</div>` : "")}
          ${childrenHtml}
        </div>
      </details>`;
  }

  function statusTag(status) {
    const map = {
      running:   "tag-orange",
      completed: "tag-green",
      failed:    "tag tag-muted",
      detected:  "tag-blue",
      stopped:   "tag tag-muted",
    };
    const labels = {
      running: "执行中", completed: "完成", failed: "失败",
      detected: "检测", stopped: "已停止",
    };
    return `<span class="tag ${map[status] || "tag-muted"}">${esc(labels[status] || status)}</span>`;
  }

  // ─── Render: Composer ────────────────────────────────────────────────────────
  function renderComposer() {
    const s = state.session;
    const noConfig   = !s || !s.settings_exists;
    const isBusy     = s && s.busy;
    const disabled   = noConfig || isBusy;

    el.sendBtn.disabled    = disabled;
    el.msgInput.disabled   = disabled;
    el.newSessionBtn.disabled = newSessionLock;

    if (!s) {
      el.composerHint.textContent = "正在加载…";
    } else if (noConfig) {
      el.composerHint.textContent = "请先准备 ~/.claude/settings.json 配置文件。";
    } else if (isBusy) {
      el.composerHint.textContent = "执行中，请等待本轮完成…";
    } else {
      el.composerHint.textContent = "Enter 发送 · Shift+Enter 换行 · 支持多轮连续对话";
    }
  }

  // ─── Render: Turn stats ───────────────────────────────────────────────────────
  function renderTurnStats() {
    const assistantMsgs = state.messages.filter((m) => m.role === "assistant" && m.status === "complete");
    if (assistantMsgs.length === 0) {
      el.turnStats.classList.add("is-hidden");
      return;
    }
    el.turnStats.classList.remove("is-hidden");
    el.turnStats.textContent = `${assistantMsgs.length} 轮对话`;
  }

  // ─── Render all ──────────────────────────────────────────────────────────────
  function renderAll() {
    renderConnection();
    renderStatusBadge();
    renderSessionIds();
    renderError();
    renderLoadedInstructions();
    renderCapabilities();
    renderMessages();
    renderTimeline();
    renderComposer();
    renderTurnStats();
  }

  // ─── SSE ─────────────────────────────────────────────────────────────────────
  let reconnectTimer = null;
  let reconnectDelay = 2000;

  function connectSSE() {
    if (sse) { sse.close(); sse = null; }
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }

    connectionState = "connecting";
    renderConnection();

    sse = new EventSource("/api/stream");

    sse.addEventListener("update", (e) => {
      connectionState = "open";
      reconnectDelay = 2000;
      try {
        const ev = JSON.parse(e.data);
        applyEvent(ev);
        renderAll();
      } catch (_) { /* ignore */ }
    });

    sse.addEventListener("ping", () => {
      connectionState = "open";
      renderConnection();
    });

    sse.onerror = () => {
      connectionState = "error";
      renderConnection();
      sse.close();
      sse = null;
      reconnectTimer = setTimeout(() => {
        reconnectDelay = Math.min(reconnectDelay * 1.5, 15000);
        connectSSE();
      }, reconnectDelay);
    };
  }

  // ─── Hydrate ──────────────────────────────────────────────────────────────────
  async function hydrate() {
    const snap = await fetchJson("/api/state");
    applySnapshot(snap);
    renderAll();
  }

  // ─── Actions ─────────────────────────────────────────────────────────────────
  async function handleNewSession() {
    try {
      newSessionLock = true;
      renderComposer();
      const payload = await fetchJson("/api/session/new", {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (payload.state) {
        applySnapshot(payload.state);
        renderAll();
      }
    } catch (err) {
      state.last_error = err.message;
      renderError();
    } finally {
      newSessionLock = false;
      renderComposer();
    }
  }

  async function handleSend(e) {
    e.preventDefault();
    const text = el.msgInput.value.trim();
    if (!text) return;

    try {
      await fetchJson("/api/message", {
        method: "POST",
        body: JSON.stringify({ message: text }),
      });
      el.msgInput.value = "";
      autoResizeTextarea();
      el.msgInput.focus();
    } catch (err) {
      state.last_error = err.message;
      renderError();
      renderComposer();
    }
  }

  // ─── Auto-resize textarea ────────────────────────────────────────────────────
  function autoResizeTextarea() {
    el.msgInput.style.height = "auto";
    el.msgInput.style.height = `${Math.min(el.msgInput.scrollHeight, 200)}px`;
  }

  // ─── Key bindings ────────────────────────────────────────────────────────────
  function handleKey(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!el.sendBtn.disabled) {
        el.composer.dispatchEvent(new Event("submit", { cancelable: true }));
      }
    }
  }

  // ─── Boot ────────────────────────────────────────────────────────────────────
  function bind() {
    el.composer.addEventListener("submit", handleSend);
    el.newSessionBtn.addEventListener("click", handleNewSession);
    el.msgInput.addEventListener("input", autoResizeTextarea);
    el.msgInput.addEventListener("keydown", handleKey);
  }

  async function boot() {
    bind();
    await hydrate().catch((err) => {
      state.last_error = err.message;
      renderAll();
    });
    connectSSE();
  }

  boot();
})();
