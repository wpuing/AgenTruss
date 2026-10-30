/**
 * Roles / permission tree / tenants (mostly readonly) + backlog.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-ext-rbac.js");
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

    async function pageRoles(root) {
      let data;
      try {
        data = await api("/api/admin/roles");
      } catch (err) {
        root.innerHTML = `<div class="panel alert alert-error"><strong>角色列表加载失败</strong><p>${escapeHtml(err.message)}</p>
          <button class="btn secondary" id="page-retry">重试</button></div>`;
        root.querySelector("#page-retry")?.addEventListener("click", () => renderRoute());
        return;
      }
      const items = data.items || [];
      root.innerHTML = `
        <div class="alert alert-warn readonly-banner"><span class="badge warn">只读</span>
          <span>角色与权限由后端矩阵定义；本页不可在线改角色。</span></div>
        <div class="panel">
          ${items.length ? items.map((r) => `
            <div class="panel" style="margin-bottom:10px">
              <div class="panel-head"><strong>${escapeHtml(r.name)}</strong> <span class="mono">${escapeHtml(r.code)}</span></div>
              <p class="meta">${escapeHtml(r.note || "")}</p>
              <p class="mono truncate-2" title="${escapeAttr((r.perms || []).join(", "))}">${escapeHtml((r.perms || []).join(", "))}</p>
            </div>`).join("") : `<div class="empty">暂无角色定义</div>`}
          <a class="btn secondary" href="#/iam/roles/tree">权限树</a>
          <a class="btn secondary" href="#/iam/users" style="margin-left:8px">用户列表</a>
        </div>`;
    }

    async function pageRolesTree(root) {
      let data;
      try {
        data = await api("/api/admin/permissions");
      } catch (err) {
        root.innerHTML = `<div class="panel alert alert-error"><strong>权限树加载失败</strong><p>${escapeHtml(err.message)}</p>
          <button class="btn secondary" id="page-retry">重试</button></div>`;
        root.querySelector("#page-retry")?.addEventListener("click", () => renderRoute());
        return;
      }
      root.innerHTML = `
        <div class="alert alert-warn readonly-banner"><span class="badge warn">只读</span>
          <span>权限字典只读展示，不可在此授权。</span></div>
        <div class="panel">
          ${(data.items || []).map((g) => `
            <div style="margin-bottom:12px">
              <strong>${escapeHtml(g.group)}</strong>
              <ul class="perm-list">
                ${(g.perms || []).map((p) => `<li class="mono">${escapeHtml(p)}</li>`).join("")}
              </ul>
            </div>`).join("") || `<div class="empty">暂无权限分组</div>`}
          <p class="meta">${metaBadge(data._meta)} · 只读权限字典</p>
          <a class="btn secondary" href="#/iam/roles">返回角色</a>
        </div>`;
    }

    async function pageTenants(root) {
      let data;
      try {
        data = await api("/api/admin/tenants");
      } catch (err) {
        root.innerHTML = `<div class="panel alert alert-error"><strong>租户列表加载失败</strong><p>${escapeHtml(err.message)}</p>
          <button class="btn secondary" id="page-retry">重试</button></div>`;
        root.querySelector("#page-retry")?.addEventListener("click", () => renderRoute());
        return;
      }
      root.innerHTML = `
        <div class="alert alert-warn readonly-banner"><span class="badge warn">只读</span>
          <span>来自 <code>config/tenants.yaml</code>；无在线创建租户。</span></div>
        ${tablePanel(data.items || [], ["编码", "名称", "行业", "状态"], (row) => `
        <tr><td class="mono">${escapeHtml(row.code)}</td><td>${escapeHtml(row.name)}</td>
        <td>${escapeHtml(row.industry)}</td><td>${badge(row.status)}</td></tr>`)}
        <p class="meta">${metaBadge(data._meta)}</p>`;
    }

    async function pageBacklog(root) {
      let data;
      try {
        data = await api("/api/admin/backlog");
      } catch (err) {
        root.innerHTML = `<div class="panel alert alert-error"><strong>代办清单加载失败</strong><p>${escapeHtml(err.message)}</p>
          <button class="btn secondary" id="page-retry">重试</button></div>`;
        root.querySelector("#page-retry")?.addEventListener("click", () => renderRoute());
        return;
      }
      const counts = data.counts || {};
      const filter = routeQuery().get("status") || "";
      const statusBadge = (st, label) => {
        const map = { blocked: "warn", partial: "warn", planned: "", note: "", done: "ok" };
        return `<span class="badge ${map[st] || ""}">${escapeHtml(label || st || "-")}</span>`;
      };
      const itemHtml = (it) => {
        if (filter && it.status !== filter) return "";
        return `
        <article class="panel backlog-item" data-status="${escapeAttr(it.status || "")}" style="margin:10px 0" id="bl-${escapeAttr(it.id)}">
          <div class="panel-head">
            <strong>${escapeHtml(it.title)}</strong>
            ${statusBadge(it.status, it.status_label)}
          </div>
          <dl class="kv">
            <dt>现状 / 原因</dt><dd>${escapeHtml(it.why || "-")}</dd>
            <dt>入口</dt><dd class="mono">${(it.entry || "-").split(" · ").map((p) => {
              const t = p.trim();
              if (t.startsWith("#/")) return `<a href="${escapeAttr(t)}">${escapeHtml(t)}</a>`;
              return escapeHtml(t);
            }).join(" · ")}</dd>
            <dt>验收 / 测试</dt><dd class="mono truncate-2">${escapeHtml(it.tests || "-")}</dd>
            <dt>负责人提示</dt><dd>${escapeHtml(it.owner_hint || "-")}</dd>
          </dl>
          ${(it.done_when && it.done_when.length) ? `
            <p class="meta" style="margin-top:8px"><strong>完成条件</strong></p>
            <ul style="margin:4px 0 0;padding-left:18px">
              ${it.done_when.map((c) => `<li>${escapeHtml(c)}</li>`).join("")}
            </ul>` : ""}
        </article>`;
      };
      const chip = (key, label, cls, count) =>
        `<a class="chip ${cls}${filter === key ? " is-selected" : ""}" href="#/backlog${key ? `?status=${encodeURIComponent(key)}` : ""}"><strong>${escapeHtml(label)}</strong> ${count ?? 0}</a>`;
      root.innerHTML = `
        <div class="panel">
          <p>${escapeHtml(data.baseline_note || "")}</p>
          <div class="status-chips backlog-filters" style="margin:12px 0">
            ${chip("", "全部", "", (counts.blocked || 0) + (counts.partial || 0) + (counts.planned || 0) + (counts.note || 0) + (counts.done || 0))}
            ${chip("blocked", "BLOCKED", "warn", counts.blocked)}
            ${chip("partial", "部分完成", "warn", counts.partial)}
            ${chip("planned", "计划中", "", counts.planned)}
            ${chip("note", "备注", "", counts.note)}
            ${chip("done", "已闭合", "ok", counts.done)}
          </div>
          <p class="meta">${metaBadge(data._meta)} · 更新于 ${escapeHtml(data.updated_at || "-")}
            ${filter ? ` · 筛选 status=${escapeHtml(filter)}` : ""} · 静态规划清单，非工单系统</p>
        </div>
        ${(data.groups || []).map((g) => {
          const body = (g.items || []).map(itemHtml).join("");
          if (filter && !body.trim()) return "";
          return `
          <section class="panel" style="margin-top:14px">
            <div class="panel-head">
              <strong>${escapeHtml(g.title)}</strong>
              <span class="badge">${escapeHtml(g.priority || "")}</span>
            </div>
            <p class="meta">${escapeHtml(g.summary || "")}</p>
            ${body || `<div class="empty compact">本组无匹配项</div>`}
          </section>`;
        }).join("")}
        <p class="meta" style="margin-top:16px">推进时请同步改 <code>app/admin/backlog.py</code>；本页不会伪造完成状态。</p>`;
    }

    return {
      "/iam/roles": pageRoles,
      "/iam/roles/tree": pageRolesTree,
      "/iam/tenants": pageTenants,
      "/backlog": pageBacklog
    };
  });
})();
