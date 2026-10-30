/**
 * Chat playground + sessions list.
 * Registers via window.AgenTrussPageModules (traditional script, no bundler).
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-chat.js");
    return;
  }
  KitNS.registerPages(function (S) {
    const {
      api, escapeHtml, escapeAttr, badge, toast, routeQuery, metaBadge,
      tablePanel, openModal, state, renderRoute, pageTitleActions, demoModeBanner,
      watchFormDirty, markFormDirty, runBusy, isAbortError, agentConfigNav,
      agentConfigSubnav, renderLoadError, bindFilterEnter, detailHead, applyTheme,
      runAction, failPanel, Kit, loadingHtml, emptyHtml, isLive,
    } = S;


    async function pageChat(root) {
  let agents = [];
  try {
    // Backend for_chat=true is the source of truth for dialable Agents.
    agents = (await api("/api/admin/agents?for_chat=true")).items || [];
  } catch (err) {
    return renderLoadError(root, "无法加载可对话 Agent 列表", err);
  }
  // Empty when none published / chat_callable — do not fall back to disabled/draft.
  if (!agents.length) {
    state.chatAgentId = null;
    root.innerHTML = `<div class="empty">当前租户没有可对话的 published Agent。
      <p class="meta">对话列表仅加载 <code>for_chat=true</code>（后端 <code>chat_callable</code>）。请先在 Agent 列表发布。</p>
      <a class="btn" href="#/agent/list">去 Agent 列表</a>
      <a class="btn secondary" href="#/agent/config/publish">去发布页</a></div>`;
    return;
  }
  const preferred = agents.find((a) => a.chat_callable === true) || agents[0];
  if (!state.chatAgentId || !agents.some((a) => a.agent_id === state.chatAgentId)) {
    state.chatAgentId = preferred.agent_id;
  }
  const current = agents.find((a) => a.agent_id === state.chatAgentId) || preferred;
  // for_chat list is already filtered; still trust chat_callable only.
  const notCallable = current?.chat_callable !== true;
  root.innerHTML = `
    ${agentConfigSubnav("/agent/config/playground", state.chatAgentId, "调试对话走 Stub/护栏 JSON；Live 需密钥与开关。仅 published（chat_callable）可发送。")}
    <div class="chat-layout">
      <div class="panel chat-box">
        <div class="filters">
          <label class="select-wide">Agent
            <select id="chat-agent" class="ctrl">${agents.map((a) => `<option value="${escapeAttr(a.agent_id)}" ${a.agent_id === state.chatAgentId ? "selected" : ""}>${escapeHtml(a.name)} (${escapeHtml(a.status)})</option>`).join("")}</select>
          </label>
          <button class="btn secondary" id="chat-reset">新会话</button>
          <a class="btn ghost" href="#/chat/sessions">会话列表</a>
          <span class="badge warn" id="chat-llm-badge" title="以每次响应的 llm_mode / llm_called 为准">Stub / 护栏模式</span>
          <span class="meta mono" id="chat-session">session: ${escapeHtml(state.sessionId || "未开始")}</span>
        </div>
        ${notCallable ? `<div class="alert alert-warn">当前 Agent 不可对话（chat_callable=false）；请选择 published Agent 或先发布。</div>` : ""}
        <div class="chat-log" id="chat-log"><div class="chat-empty">还没有消息。可试：「退货政策」或「订单申请退款」。</div></div>
        <div class="chat-input chat-input-tall">
          <textarea id="chat-input" rows="2" placeholder="Enter 发送 · Shift+Enter 换行&#10;例：查询订单 / 订单申请退款 / 退货政策" ${notCallable ? "disabled" : ""}></textarea>
          <button class="btn" id="chat-send" ${notCallable ? "disabled" : ""}>发送</button>
        </div>
        <p class="meta">同步 JSON 响应（无 SSE）。徽章以本轮 <code>llm_called</code> 为准，Stub 不会显示成 Live。</p>
      </div>
      <aside class="panel chat-side">
        <div class="panel-head"><strong>运行信息</strong>
          <a href="#/agent/detail?id=${encodeURIComponent(current?.agent_id || "")}">详情</a></div>
        <dl class="kv">
          <dt>Agent</dt><dd>${escapeHtml(current?.name || "-")}</dd>
          <dt>行业</dt><dd>${escapeHtml(current?.industry || "-")}</dd>
          <dt>状态</dt><dd>${badge(current?.status || "-")}</dd>
          <dt>可对话</dt><dd>${current?.chat_callable === true ? '<span class="badge ok">chat_callable</span>' : '<span class="badge warn">否</span>'}</dd>
          <dt>知识库</dt><dd class="mono truncate">${escapeHtml(current?.kb_binding?.kb_id || "未绑定")}</dd>
          <dt>RAG</dt><dd>${current?.kb_binding?.rag_enabled ? "开" : "关"}</dd>
          <dt>工具</dt><dd class="mono truncate">${escapeHtml((current?.enabled_tools || []).join(", ") || "-")}</dd>
        </dl>
        <div id="chat-last-meta" class="meta chat-side-meta">发送后显示来源 / 工单 / 审计。</div>
      </aside>
    </div>`;
  const log = root.querySelector("#chat-log");
  let sendGen = 0;
  const clearEmpty = () => {
    const ph = log.querySelector(".chat-empty");
    if (ph) ph.remove();
  };
  const send = async () => {
    const input = root.querySelector("#chat-input");
    const message = input.value.trim();
    if (!message || notCallable) return;
    const sendBtn = root.querySelector("#chat-send");
    if (sendBtn?.classList.contains("is-busy")) return;
    const agentId = root.querySelector("#chat-agent").value;
    const sessionBefore = state.sessionId;
    const myGen = ++sendGen;
    clearEmpty();
    appendBubble(log, "user", message);
    input.value = "";
    if (sendBtn) {
      sendBtn.disabled = true;
      sendBtn.classList.add("is-busy");
    }
    try {
      state.chatAgentId = agentId;
      const data = await api("/api/chat", {
        method: "POST",
        json: { message, session_id: sessionBefore, agent_id: agentId || null },
      });
      // Drop stale replies after agent/session switch or route leave.
      if (!log.isConnected || (root.isCurrent && !root.isCurrent()) || myGen !== sendGen) return;
      if (root.querySelector("#chat-agent")?.value !== agentId) return;
      state.sessionId = data.session_id;
      const sessEl = root.querySelector("#chat-session");
      if (sessEl) sessEl.textContent = `session: ${state.sessionId}`;
      const llmMode = data.llm_mode || (data.llm_called ? "live" : "stub");
      const modeBadge = root.querySelector("#chat-llm-badge");
      if (modeBadge) {
        modeBadge.textContent = data.llm_called ? `Live (${llmMode})` : `Stub / 护栏 (${llmMode})`;
        modeBadge.className = data.llm_called ? "badge ok" : "badge warn";
        modeBadge.title = `llm_called=${!!data.llm_called}; runtime=${data.runtime_id || "-"}`;
      }
      const ticketHtml = data.ticket_id
        ? `<a href="#/tickets/risk/detail?id=${encodeURIComponent(data.ticket_id)}">${escapeHtml(data.ticket_id)}</a>`
        : "-";
      const meta = [
        data.guardrail_hit ? `护栏:${data.block_reason}` : null,
        data.sources?.length ? `来源:${data.sources.length}` : null,
        data.ticket_id ? `工单:${data.ticket_id}` : null,
        data.audit_id ? `审计:${data.audit_id}` : null,
        data.runtime_id ? `runtime:${data.runtime_id}` : null,
        `llm:${llmMode}`,
      ].filter(Boolean).join(" · ");
      appendBubble(log, "bot", data.reply, meta || "Stub 回复");
      const side = root.querySelector("#chat-last-meta");
      if (side) {
        side.innerHTML = `
        <div><strong>最近一轮</strong></div>
        <div>sources: ${escapeHtml(JSON.stringify(data.sources || []))}</div>
        <div>ticket: ${ticketHtml}${data.need_human && data.ticket_id ? ' · <a href="#/tickets/risk/detail?id=' + encodeURIComponent(data.ticket_id) + '">去审批</a>' : ""}</div>
        <div>audit: ${escapeHtml(data.audit_id || "-")}
          ${data.session_id ? ` · <a href="#/audit/trace?session_id=${encodeURIComponent(data.session_id)}">Trace</a>` : ""}</div>
        <div>need_human: ${escapeHtml(String(!!data.need_human))}</div>
        <div>llm_called: ${escapeHtml(String(!!data.llm_called))} · mode: ${escapeHtml(llmMode)}</div>`;
      }
    } catch (err) {
      if (isAbortError(err) || !log.isConnected || myGen !== sendGen) return;
      if (root.querySelector("#chat-agent")?.value !== agentId) return;
      appendBubble(log, "bot", err.message, "error");
      toast(err.message, "error");
    } finally {
      if (sendBtn && log.isConnected && myGen === sendGen) {
        sendBtn.disabled = notCallable;
        sendBtn.classList.remove("is-busy");
      }
    }
  };
  root.querySelector("#chat-send").onclick = send;
  root.querySelector("#chat-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  root.querySelector("#chat-agent").onchange = () => {
    sendGen += 1;
    state.chatAgentId = root.querySelector("#chat-agent").value;
    state.sessionId = null;
    renderRoute();
  };
  root.querySelector("#chat-reset").onclick = () => {
    sendGen += 1;
    state.sessionId = null;
    log.innerHTML = `<div class="chat-empty">还没有消息。可试：「退货政策」或「订单申请退款」。</div>`;
    root.querySelector("#chat-session").textContent = "session: 未开始";
    root.querySelector("#chat-last-meta").textContent = "发送后显示来源 / 工单 / 审计。";
  };
}

    function appendBubble(log, role, text, meta) {
  const node = document.createElement("div");
  const isErr = meta === "error";
  node.className = `bubble ${role === "user" ? "user" : "bot"}${isErr ? " bubble-error" : ""}`;
  node.innerHTML = `${escapeHtml(text)}${meta ? `<span class="meta">${escapeHtml(meta)}</span>` : ""}`;
  log.appendChild(node);
  log.scrollTop = log.scrollHeight;
}

    async function pageSessions(root) {
  let payload;
  try {
    payload = await api("/api/admin/sessions");
  } catch (err) {
    return renderLoadError(root, "会话列表加载失败", err);
  }
  const rows = payload.items || [];
  const source = payload.source || "unknown";
  root.innerHTML = `
    <div class="list-page">
    <div class="filters">
      <a class="btn" href="#/chat/playground">打开调试</a>
      <button class="btn secondary" id="sess-refresh">刷新</button>
      <span class="meta">数据源 ${escapeHtml(source)} · 共 ${rows.length} · 摘要经脱敏</span>
    </div>
    <div class="panel">
      ${rows.length ? `
      <table class="data">
        <thead><tr><th>会话</th><th>用户</th><th>轮数</th><th>操作</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td class="mono truncate" title="${escapeAttr(row.session_id)}">${escapeHtml(row.session_id)}</td>
              <td class="mono">${escapeHtml(row.user_id)}</td>
              <td>${row.turn_count}</td>
              <td class="row-actions">
                <button class="btn secondary" data-sid="${escapeAttr(row.session_id)}">查看摘要</button>
                <a class="btn ghost" href="#/audit/trace?session_id=${encodeURIComponent(row.session_id)}">Trace</a>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>` : `<div class="empty">暂无会话。先在调试页发送几轮对话。<p class="meta">内存空时可能从 PG hydrate</p></div>`}
      <div id="session-detail" class="session-detail empty${rows.length ? "" : " hidden"}">选择一条会话查看脱敏摘要</div>
    </div>
    </div>`;
  root.querySelector("#sess-refresh")?.addEventListener("click", () => renderRoute());
  root.querySelectorAll("[data-sid]").forEach((btn) => {
    btn.onclick = async () => {
      if (btn.classList.contains("is-busy")) return;
      const box = root.querySelector("#session-detail");
      const sid = btn.dataset.sid;
      box.classList.remove("empty", "hidden");
      box.innerHTML = typeof Kit.loadingHtml === "function"
        ? Kit.loadingHtml("加载摘要…")
        : `<div class="page-loading"><span class="spinner"></span>加载摘要…</div>`;
      root.querySelectorAll("tr.is-selected").forEach((tr) => tr.classList.remove("is-selected"));
      btn.closest("tr")?.classList.add("is-selected");
      btn.classList.add("is-busy");
      try {
        const detail = await api(`/api/admin/sessions/${sid}`);
        if (!box.isConnected || (root.isCurrent && !root.isCurrent())) return;
        if (root.querySelector("tr.is-selected [data-sid]")?.dataset.sid !== sid
            && btn.closest("tr") && !btn.closest("tr").classList.contains("is-selected")) {
          return;
        }
        box.innerHTML = `
          <div class="panel-head"><strong>会话 ${escapeHtml(detail.session_id)}</strong>
            <a class="btn secondary" href="#/audit/trace?session_id=${encodeURIComponent(detail.session_id)}">打开 Trace</a>
          </div>
          <p class="meta">source=${escapeHtml(detail.source || source)}</p>
          ${(detail.turns || []).map((t, i) => `
            <div class="turn-card">
              <div class="meta">第 ${i + 1} 轮</div>
              <div class="bubble user compact-bubble">${escapeHtml(t.user)}</div>
              <div class="bubble bot compact-bubble">${escapeHtml(t.assistant)}</div>
            </div>`).join("") || "<div class='empty compact'>无摘要（可能被护栏阻断未写入）</div>"}`;
      } catch (err) {
        if (isAbortError(err) || !box.isConnected) return;
        box.innerHTML = `<div class="alert alert-error"><strong>加载失败</strong><p>${escapeHtml(err.message)}</p></div>`;
        toast(err.message, "error");
      } finally {
        btn.classList.remove("is-busy");
      }
    };
  });
}

    return {
      "/chat/playground": pageChat,
      "/agent/config/playground": pageChat,
      "/chat/sessions": pageSessions
    };
  });
})();
