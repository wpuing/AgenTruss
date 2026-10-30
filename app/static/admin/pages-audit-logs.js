/**
 * Audit log list with client-side filters.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-audit-logs.js");
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

    async function pageAudits(root) {
  let rows = [];
  let source = "unknown";
  try {
    const data = await api("/api/admin/audit-logs");
    rows = [...(data.items || [])].reverse();
    source = data.source || "unknown";
  } catch (err) {
    return renderLoadError(root, "审计列表加载失败", err);
  }
  const initialQ = routeQuery().get("q") || "";
  const initialBlocked = routeQuery().get("blocked") || "";
  root.innerHTML = `
    <div class="list-page">
    <div class="filters">
      <label class="select-wide">关键词
        <input id="audit-q" class="ctrl" value="${escapeAttr(initialQ)}" placeholder="审计号 / 话题 / 原因" />
      </label>
      <label>结果
        <select id="audit-blocked" class="ctrl">
          <option value="">全部结果</option>
          <option value="1" ${initialBlocked === "1" ? "selected" : ""}>仅拦截</option>
          <option value="0" ${initialBlocked === "0" ? "selected" : ""}>仅通过</option>
        </select>
      </label>
      <button class="btn secondary" id="audit-filter">筛选</button>
      <button class="btn ghost" id="audit-clear">清空</button>
      <span class="meta">source=${escapeHtml(source)} · 脱敏摘要；不重新拼接敏感原文</span>
    </div>
    <div class="panel" id="audit-table"></div>
    </div>`;
  const render = () => {
    const q = (root.querySelector("#audit-q").value || "").trim().toLowerCase();
    const blocked = root.querySelector("#audit-blocked").value;
    const filtered = rows.filter((row) => {
      if (blocked === "1" && !row.blocked) return false;
      if (blocked === "0" && row.blocked) return false;
      if (!q) return true;
      const hay = `${row.audit_id || ""} ${row.topic || ""} ${row.block_reason || ""} ${row.input_message || ""}`.toLowerCase();
      return hay.includes(q);
    });
    root.querySelector("#audit-table").innerHTML = filtered.length ? `
      <table class="data">
        <thead><tr><th>时间/审计号</th><th>会话</th><th>话题</th><th>结果</th><th>原因</th><th>输入摘要</th></tr></thead>
        <tbody>
          ${filtered.slice(0, 100).map((row) => `
            <tr>
              <td class="mono"><a href="#/audit/logs/detail?id=${encodeURIComponent(row.audit_id)}">${escapeHtml(row.audit_id)}</a><div class="meta">${escapeHtml(row.created_at || "")}</div></td>
              <td class="mono">${row.session_id ? `<a href="#/audit/trace?session_id=${encodeURIComponent(row.session_id)}">${escapeHtml(row.session_id)}</a>` : "-"}</td>
              <td>${escapeHtml(row.topic || "-")}</td>
              <td>${row.blocked ? badge("blocked") : badge("ok")}</td>
              <td>${escapeHtml(row.block_reason || "-")}</td>
              <td>${escapeHtml((row.input_message || "").slice(0, 48))}</td>
            </tr>`).join("")}
        </tbody>
      </table>` : `<div class="empty">${rows.length ? "无匹配审计，请清空筛选。" : "暂无审计。发起对话后会自动落库。"}</div>`;
  };
  root.querySelector("#audit-filter").onclick = () => {
    const q = root.querySelector("#audit-q").value.trim();
    const blocked = root.querySelector("#audit-blocked").value;
    location.hash = `/audit/logs?q=${encodeURIComponent(q)}&blocked=${encodeURIComponent(blocked)}`;
    render();
  };
  root.querySelector("#audit-clear").onclick = () => {
    location.hash = "/audit/logs";
    root.querySelector("#audit-q").value = "";
    root.querySelector("#audit-blocked").value = "";
    render();
  };
  bindFilterEnter(root, "#audit-filter", ["#audit-q", "#audit-blocked"]);
  render();
}

    return {
      "/audit/logs": pageAudits
    };
  });
})();
