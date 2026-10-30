/**
 * Dashboard / overview cards.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-dashboard.js");
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

    async function pageDashboard(root) {
  pageTitleActions(root, `<button class="btn secondary" id="dash-refresh">刷新</button>`);
  let overview;
  let status;
  let components;
  let statusErr = null;
  let componentsErr = null;
  try {
    [overview, status, components] = await Promise.all([
      api("/api/admin/overview"),
      api("/api/admin/system/status").catch((e) => { statusErr = e; return null; }),
      api("/api/admin/components").catch((e) => { componentsErr = e; return null; }),
    ]);
  } catch (err) {
    return renderLoadError(root, "无法加载工作台", err);
  }
  const meta = overview._meta || {};
  const pending = (overview.recent_tickets || []).filter((t) => t.status === "pending").slice(0, 5);
  const recentAudits = overview.recent_audits || [];
  const env = status?.app_env || "development";
  const checkedAt = meta.updated_at ? new Date(meta.updated_at).toLocaleString() : "-";
  const agentTotal = overview.agents?.total ?? 0;
  const agentPublished = overview.agents?.published ?? overview.agents?.chat_callable;
  const agentLabel = agentPublished != null
    ? `${agentPublished}/${agentTotal}`
    : String(agentTotal);
  const cards = [
    { label: "Agent（可对话/全部）", value: agentLabel, href: "#/agent/list", hint: "仅 published 可对话" },
    { label: "对话次数", value: overview.chat_count ?? 0, href: "#/chat/playground", hint: "对话" },
    { label: "护栏命中", value: overview.guardrail_hits ?? 0, href: "#/audit/logs?blocked=1", hint: "审计拦截" },
    { label: "待审批", value: overview.tickets?.pending ?? 0, href: "#/tickets/risk?status=pending", hint: "审批" },
    { label: "知识库", value: overview.knowledge?.kb_count ?? 0, href: "#/kb/base", hint: "KB" },
    { label: "文档", value: overview.knowledge?.doc_count ?? 0, href: "#/kb/docs", hint: "文档" },
    { label: "执行记录", value: "→", href: "#/audit/executions", hint: "聚合只读" },
    { label: "组件配置", value: "→", href: "#/system/model", hint: "系统组件" },
    { label: "未完成代办", value: "→", href: "#/backlog", hint: "规划清单" },
  ];
  const unknownNote = overview.agents?.note || "";
  root.innerHTML = `
    ${demoModeBanner(overview.admin_demo_mode)}
    ${statusErr ? `<div class="alert alert-warn">系统状态局部失败：${escapeHtml(statusErr.message)}（工作台其余卡片仍可用）</div>` : ""}
    ${componentsErr ? `<div class="alert alert-warn">组件列表局部失败：${escapeHtml(componentsErr.message)}</div>` : ""}
    <div class="dash-hero">
      <div>
        <div class="eyebrow-inline">运营总览 · ${metaBadge(meta)}</div>
        <h3 class="dash-welcome">欢迎回来，${escapeHtml(state.username || "运营")}</h3>
        <p class="meta">环境 <span class="badge">${escapeHtml(env)}</span>
          · Ready ${escapeHtml(status?.ready?.status || (statusErr ? "未知" : "-"))}
          · 更新于 ${escapeHtml(checkedAt)}
          · 执行模式 ${escapeHtml(overview.agent_execution_mode || "-")}
          · 工作流可视化 ${escapeHtml(overview.workflows?.visualizer_mode || "-")}</p>
        ${unknownNote ? `<p class="meta note-line">ℹ ${escapeHtml(unknownNote)}</p>` : ""}
        ${overview.tickets?.note ? `<p class="meta note-line">ℹ ${escapeHtml(overview.tickets.note)}</p>` : ""}
      </div>
      <div class="dash-hero-actions">
        <a class="btn" href="#/chat/playground">开始对话</a>
        <a class="btn secondary" href="#/tickets/risk?status=pending">处理审批</a>
        <a class="btn secondary" href="#/agent/config/graph">工作流</a>
      </div>
    </div>
    <div class="grid-cards">
      ${cards.map((c) => `
        <a class="stat stat-link" href="${c.href}" title="${escapeAttr(c.hint)}">
          <div class="label">${escapeHtml(c.label)}</div>
          <div class="value">${escapeHtml(String(c.value))}</div>
        </a>`).join("")}
    </div>
    <div class="dash-grid">
      <div class="panel">
        <div class="panel-head"><strong>待审批工单</strong><a href="#/tickets/risk?status=pending">筛选</a></div>
        ${pending.length ? `
          <ul class="feed-list">
            ${pending.map((t) => `
              <li>
                <a href="#/tickets/risk/detail?id=${encodeURIComponent(t.id)}">
                  <span class="mono">${escapeHtml(t.id)}</span>
                  <span>${escapeHtml(t.tool_name || "")}</span>
                  ${badge(t.status)}
                </a>
              </li>`).join("")}
          </ul>` : `<div class="empty compact">暂无待审批。</div>`}
      </div>
      <div class="panel">
        <div class="panel-head"><strong>最近审计</strong><a href="#/audit/logs">全部</a></div>
        ${recentAudits.length ? `
          <ul class="feed-list">
            ${recentAudits.map((a) => `
              <li>
                <a href="#/audit/logs?q=${encodeURIComponent(a.audit_id || "")}">
                  <span class="mono">${escapeHtml((a.audit_id || "").slice(0, 12))}</span>
                  <span>${escapeHtml(a.topic || "-")}</span>
                  ${a.blocked ? badge("blocked") : badge("ok")}
                </a>
              </li>`).join("")}
          </ul>` : `<div class="empty compact">暂无审计记录。</div>`}
      </div>
    </div>
    <div class="panel" style="margin-top:12px">
      <div class="panel-head"><strong>组件状态（未配置≠健康）</strong><a href="#/system/model">配置中心</a></div>
      <div class="status-chips">
        ${(components?.items || []).slice(0, 8).map((c) => {
          const st = String(c.status || c.mode || "-");
          const warn = st.includes("not_") || st.includes("unreachable") || st === "mock" || st === "stub";
          return `<span class="chip ${warn ? "warn" : "ok"}" title="${escapeAttr(c.note || "")}">${escapeHtml(c.title)} · ${escapeHtml(st)}</span>`;
        }).join("") || `<span class="chip warn">组件接口未接入</span>`}
      </div>
      ${(status?.notes || []).map((n) => `<p class="meta note-line">⚠ ${escapeHtml(n)}</p>`).join("")}
    </div>`;
  document.getElementById("dash-refresh")?.addEventListener("click", () => renderRoute());
}

    return {
      "/dashboard": pageDashboard
    };
  });
})();
