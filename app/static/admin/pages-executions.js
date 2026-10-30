/**
 * Execution list + detail (audit aggregation).
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-executions.js");
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

    async function pageExecutions(root) {
  let data;
  try {
    data = await api("/api/admin/executions");
  } catch (err) {
    return renderLoadError(root, "执行记录加载失败", err);
  }
  const q = (routeQuery().get("q") || "").toLowerCase();
  const kind = routeQuery().get("kind") || "";
  const all = data.items || [];
  let rows = all;
  if (kind) rows = rows.filter((r) => r.kind === kind);
  if (q) rows = rows.filter((r) => JSON.stringify(r).toLowerCase().includes(q));
  root.innerHTML = `
    <div class="list-page">
    ${demoModeBanner(rows.some((r) => r.is_demo), "列表中可能含演示 run（is_demo=true），与真实审计/工单隔离标识。")}
    <div class="filters">
      <label class="select-wide">关键词
        <input id="ex-q" class="ctrl" value="${escapeAttr(q)}" placeholder="run / 工具 / 摘要" />
      </label>
      <label>来源
        <select id="ex-kind" class="ctrl">
          <option value="">全部来源</option>
          <option value="chat_audit" ${kind === "chat_audit" ? "selected" : ""}>chat_audit</option>
          <option value="ticket_tool" ${kind === "ticket_tool" ? "selected" : ""}>ticket_tool</option>
        </select>
      </label>
      <button class="btn secondary" id="ex-filter">筛选</button>
      <button class="btn ghost" id="ex-clear">清空</button>
      <span class="meta">共 ${all.length} · 筛选后 ${rows.length} · 覆盖：${escapeHtml(data.coverage || "-")} ${metaBadge(data._meta)}</span>
    </div>
    <div class="panel">
      ${rows.length ? `
      <table class="data">
        <thead><tr><th>Run</th><th>来源</th><th>状态</th><th>关联</th><th>摘要</th></tr></thead>
        <tbody>
          ${rows.slice(0, 100).map((r) => `
            <tr>
              <td class="mono"><a href="#/audit/executions/detail?id=${encodeURIComponent(r.run_id)}">${escapeHtml(r.run_id)}</a>${r.is_demo ? " <span class='badge warn'>demo</span>" : ""}</td>
              <td>${escapeHtml(r.kind || "-")}</td>
              <td>${badge(r.status || "-")}</td>
              <td class="mono truncate" title="${escapeAttr(r.ticket_id || r.audit_id || "")}">${escapeHtml(r.ticket_id || r.audit_id || "-")}</td>
              <td class="truncate" title="${escapeAttr(r.error_summary || r.tool_name || r.topic || r.input_preview || "")}">${escapeHtml(r.error_summary || r.tool_name || r.topic || r.input_preview || "-")}</td>
            </tr>`).join("")}
        </tbody>
      </table>` : `<div class="empty">${all.length ? "无匹配记录，请清空筛选。" : "暂无执行记录（非完整观测系统）。可先在调试页发送消息产生审计。"}</div>`}
    </div>
    </div>`;
  root.querySelector("#ex-filter").onclick = () => {
    location.hash = `/audit/executions?q=${encodeURIComponent(root.querySelector("#ex-q").value)}&kind=${encodeURIComponent(root.querySelector("#ex-kind").value)}`;
  };
  root.querySelector("#ex-clear").onclick = () => { location.hash = "/audit/executions"; };
  bindFilterEnter(root, "#ex-filter", ["#ex-q", "#ex-kind"]);
}

    async function pageExecutionDetail(root) {
  const id = routeQuery().get("id");
  if (!id) {
    root.innerHTML = `<div class="empty">缺少 run id。<a href="#/audit/executions">返回列表</a></div>`;
    return;
  }
  let row;
  try {
    row = await api(`/api/admin/executions/${encodeURIComponent(id)}`);
  } catch (err) {
    return renderLoadError(root, "执行详情加载失败", err);
  }
  const ticketLink = row.ticket_id
    ? `<a href="#/tickets/risk/detail?id=${encodeURIComponent(row.ticket_id)}">${escapeHtml(row.ticket_id)}</a>`
    : "-";
  const sessionLink = row.session_id
    ? `<a href="#/audit/trace?session_id=${encodeURIComponent(row.session_id)}">${escapeHtml(row.session_id)}</a>`
    : "-";
  const auditLink = row.audit_id
    ? `<a href="#/audit/logs/detail?id=${encodeURIComponent(row.audit_id)}">${escapeHtml(row.audit_id)}</a>`
    : "-";
  root.innerHTML = `
    <div class="panel">
      ${detailHead("/audit/executions", "执行详情", `${metaBadge(row._meta)}${row.is_demo ? ' <span class="badge warn">demo</span>' : ""}`)}
      <dl class="kv">
        <dt>Run</dt><dd class="mono truncate" title="${escapeAttr(row.run_id || id)}">${escapeHtml(row.run_id || id)}</dd>
        <dt>来源</dt><dd>${escapeHtml(row.kind || "-")}</dd>
        <dt>状态</dt><dd>${badge(row.status || "-")}</dd>
        <dt>工作流/Runtime</dt><dd class="mono">${escapeHtml(row.workflow || "-")}</dd>
        <dt>触发</dt><dd>${escapeHtml(row.trigger || "-")}</dd>
        <dt>Agent</dt><dd class="mono">${escapeHtml(row.agent_id || "-")}</dd>
        <dt>工单</dt><dd class="mono">${ticketLink}</dd>
        <dt>审计</dt><dd class="mono">${auditLink}</dd>
        <dt>会话</dt><dd class="mono">${sessionLink}</dd>
        <dt>话题</dt><dd>${escapeHtml(row.topic || "-")}</dd>
        <dt>工具</dt><dd class="mono">${escapeHtml(row.tool_name || "-")}</dd>
        <dt>耗时</dt><dd>${escapeHtml(String(row.duration_ms ?? "-"))} ms</dd>
        <dt>摘要/错误</dt><dd class="truncate-2">${escapeHtml(row.error_summary || row.execution_result || "-")}</dd>
        <dt>输入预览</dt><dd class="truncate-2">${escapeHtml(row.input_preview || "-")}</dd>
        <dt>参数摘要</dt><dd class="mono truncate-2">${escapeHtml(JSON.stringify(row.args_masked || {}))}</dd>
      </dl>
      <p class="meta">只读聚合（审计+工单），非完整分布式 tracing。Demo run 有 is_demo 标记。会话链到 Trace，非重跑。</p>
    </div>`;
}

    return {
      "/audit/executions": pageExecutions,
      "/audit/executions/detail": pageExecutionDetail
    };
  });
})();
