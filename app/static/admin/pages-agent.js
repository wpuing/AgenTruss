/**
 * Agent list + detail.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-agent.js");
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

    async function pageAgents(root) {
  const q = (routeQuery().get("q") || "").toLowerCase();
  const statusFilter = routeQuery().get("status") || "";
  let agents = [];
  let kbs = [];
  let counts = {};
  try {
    const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
    const agentsPayload = await api(`/api/admin/agents${qs}`);
    agents = agentsPayload.items || [];
    counts = agentsPayload.counts || {};
    kbs = (await api("/api/admin/knowledge-bases")).items || [];
  } catch (err) {
    return failPanel(root, "Agent 列表加载失败", err);
  }
  const filtered = agents.filter((a) => {
    if (statusFilter && a.status !== statusFilter) return false;
    if (!q) return true;
    return `${a.name} ${a.industry} ${a.agent_id}`.toLowerCase().includes(q);
  });
  root.innerHTML = `
    <div class="list-page">
    <div class="filters">
      <label class="select-wide">搜索
        <input id="ag-q" class="ctrl" value="${escapeAttr(q)}" placeholder="名称 / 行业 / agent_id" />
      </label>
      <label>状态
        <select id="ag-status" class="ctrl">
          <option value="">全部状态</option>
          <option value="published" ${statusFilter === "published" ? "selected" : ""}>published</option>
          <option value="draft" ${statusFilter === "draft" ? "selected" : ""}>draft</option>
          <option value="disabled" ${statusFilter === "disabled" ? "selected" : ""}>disabled</option>
        </select>
      </label>
      <button class="btn secondary" id="ag-filter">筛选</button>
      <button class="btn ghost" id="ag-clear">清空</button>
      <a class="btn secondary" href="#/agent/config/graph">工作流</a>
      <span class="meta">本页 ${agents.length} · 筛选后 ${filtered.length}
        · 可对话 ${counts.chat_callable ?? "—"} / 全部 ${counts.total ?? agents.length}
        · PATCH 绑定/启停；无「一键新建」假成功</span>
    </div>
    <div class="panel">
      ${filtered.length ? `
      <table class="data">
        <thead><tr><th>名称</th><th>Agent ID</th><th>行业</th><th>状态</th><th>可对话</th><th>知识库绑定</th><th>操作</th></tr></thead>
        <tbody>
          ${filtered.map((agent) => {
            // Trust backend chat_callable only — do not invent from status.
            const callable = agent.chat_callable === true;
            return `
            <tr>
              <td><a href="#/agent/detail?id=${encodeURIComponent(agent.agent_id)}">${escapeHtml(agent.name)}</a></td>
              <td class="mono truncate" title="${escapeAttr(agent.agent_id)}">${escapeHtml(agent.agent_id)}</td>
              <td>${escapeHtml(agent.industry)}</td>
              <td>${badge(agent.status)}</td>
              <td>${callable ? '<span class="badge ok">是</span>' : '<span class="badge warn">否</span>'}</td>
              <td class="mono truncate" title="${escapeAttr(agent.kb_binding?.kb_id || "")}">${escapeHtml(agent.kb_binding?.kb_id || "未绑定")} / rag=${agent.kb_binding?.rag_enabled}</td>
              <td class="row-actions">
                <select data-kb-for="${escapeAttr(agent.agent_id)}" class="ctrl">
                  <option value="">选择知识库</option>
                  ${kbs.map((kb) => `<option value="${escapeAttr(kb.id)}" ${agent.kb_binding?.kb_id === kb.id ? "selected" : ""}>${escapeHtml(kb.name)}</option>`).join("")}
                </select>
                <button class="btn secondary" data-bind="${escapeAttr(agent.agent_id)}">绑定并启用 RAG</button>
                <button class="btn secondary" data-detail="${escapeAttr(agent.agent_id)}">详情</button>
                <button class="btn" data-debug="${escapeAttr(agent.agent_id)}" ${callable ? "" : "disabled title=\"未发布，不可调试\""}>去调试</button>
              </td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>` : `<div class="empty">${agents.length ? "无匹配 Agent，请清空筛选。" : "暂无 Agent。种子随租户固定 id。"}
        <p><a class="btn secondary" href="#/agent/config/graph">查看工作流</a></p></div>`}
    </div>
    </div>`;
  root.querySelector("#ag-filter").onclick = () => {
    const nq = root.querySelector("#ag-q").value.trim();
    const st = root.querySelector("#ag-status").value;
    location.hash = `/agent/list?q=${encodeURIComponent(nq)}&status=${encodeURIComponent(st)}`;
  };
  root.querySelector("#ag-clear").onclick = () => { location.hash = "/agent/list"; };
  bindFilterEnter(root, "#ag-filter", ["#ag-q", "#ag-status"]);
  root.querySelectorAll("[data-bind]").forEach((btn) => {
    btn.onclick = async () => {
      const kbId = root.querySelector(`[data-kb-for="${btn.dataset.bind}"]`).value;
      if (!kbId) return toast("请选择知识库", "error");
      await runBusy(btn, async () => {
        await api(`/api/admin/agents/${btn.dataset.bind}`, {
          method: "PATCH",
          json: { kb_id: kbId, rag_enabled: true },
        });
        toast("已绑定");
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-detail]").forEach((btn) => {
    btn.onclick = () => { location.hash = `/agent/detail?id=${encodeURIComponent(btn.dataset.detail)}`; };
  });
  root.querySelectorAll("[data-debug]").forEach((btn) => {
    btn.onclick = () => {
      if (btn.disabled) return toast("已停用 Agent 不可从列表直调调试", "error");
      state.chatAgentId = btn.dataset.debug;
      location.hash = "/chat/playground";
    };
  });
}

    async function pageAgentDetail(root) {
  const id = routeQuery().get("id");
  if (!id) {
    root.innerHTML = `<div class="empty">缺少 agent id。<a href="#/agent/list">返回列表</a></div>`;
    return;
  }
  let detail;
  try {
    detail = await api(`/api/admin/agents/${encodeURIComponent(id)}`);
  } catch (err) {
    return failPanel(root, "Agent 详情加载失败", err);
  }
  root.innerHTML = `
    <div class="panel">
      ${detailHead("/agent/list", "Agent 详情", metaBadge(detail._meta))}
      <dl class="kv">
        <dt>名称</dt><dd>${escapeHtml(detail.name || "")}</dd>
        <dt>ID</dt><dd class="mono truncate" title="${escapeAttr(detail.agent_id || "")}">${escapeHtml(detail.agent_id || "")}</dd>
        <dt>租户</dt><dd class="mono">${escapeHtml(detail.tenant_id || "-")}</dd>
        <dt>行业</dt><dd>${escapeHtml(detail.industry || "")}</dd>
        <dt>状态</dt><dd>${badge(detail.status || "-")}</dd>
        <dt>可对话</dt><dd>${detail.chat_callable === true
          ? '<span class="badge ok">chat_callable</span>'
          : '<span class="badge warn">不可调用</span>'}</dd>
        <dt>执行模式</dt><dd class="mono">${escapeHtml(detail.execution_mode || "-")}（来自 AGENTSCOPE_MODEL_MODE；stub≠Live）</dd>
        <dt>工具</dt><dd class="mono truncate-2">${escapeHtml((detail.enabled_tools || []).join(", ") || "-")}</dd>
        <dt>知识库</dt><dd class="mono truncate">${escapeHtml(detail.kb_binding?.kb_id || "未绑定")} · RAG=${detail.kb_binding?.rag_enabled}</dd>
        <dt>提示词摘要</dt><dd class="truncate-2">${escapeHtml(detail.prompt_preview || "（无独立 system prompt 字段）")}</dd>
      </dl>
      <div class="actions ticket-actions" style="margin-top:12px">
        <button class="btn" id="ag-go-chat" ${detail.chat_callable === true ? "" : "disabled"}>去调试</button>
        <button class="btn secondary" id="ag-toggle-status">${detail.status === "disabled" ? "启用" : "停用"}</button>
        <a class="btn secondary" href="#/agent/config/basic?id=${encodeURIComponent(detail.agent_id)}">配置</a>
        <a class="btn secondary" href="#/agent/config/graph">工作流</a>
        <a class="btn secondary" href="#/audit/executions">执行记录</a>
      </div>
      <p class="meta">密钥字段永不回显。Live 模式未配模型密钥时不得假装执行成功。停用后后端拒绝对话调用。</p>
    </div>`;
  root.querySelector("#ag-go-chat").onclick = () => {
    if (detail.chat_callable !== true) {
      return toast("未发布 Agent 不可调试调用", "error");
    }
    state.chatAgentId = detail.agent_id;
    location.hash = "/chat/playground";
  };
  root.querySelector("#ag-toggle-status").onclick = async (ev) => {
    const btn = ev.currentTarget;
    const next = detail.status === "disabled" ? "published" : "disabled";
    if (next === "disabled" && !confirm("停用后对话不可调用该 Agent，确认？")) return;
    await runBusy(btn, async () => {
      await api(`/api/admin/agents/${encodeURIComponent(detail.agent_id)}`, {
        method: "PATCH",
        json: { status: next },
      });
      toast(next === "published" ? "已启用" : "已停用");
      if (root.isCurrent && root.isCurrent()) await pageAgentDetail(root);
    });
  };
}

    return {
      "/agent/list": pageAgents,
      "/agent/detail": pageAgentDetail,
    };
  });
})();
