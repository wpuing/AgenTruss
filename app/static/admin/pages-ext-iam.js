/**
 * IAM user list + create/edit form.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-ext-iam.js");
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

    async function pageUsers(root) {
      pageTitleActions(root, `<a class="btn" href="#/iam/users/form">新建用户</a>`);
      let rows = [];
      let source = "unknown";
      try {
        const data = await api("/api/admin/users");
        rows = data.items || [];
        source = data.source || "unknown";
      } catch (err) {
        return renderLoadError(root, "用户列表加载失败", err);
      }
      if (!rows.length) {
        root.innerHTML = `<div class="panel empty">暂无用户。<a class="btn" href="#/iam/users/form">新建用户</a>
          <p class="meta">source=${escapeHtml(source)}</p></div>`;
        return;
      }
      root.innerHTML = `<div class="list-page"><p class="meta">source=${escapeHtml(source)} · 共 ${rows.length} · 角色变更与停用以后端实时权限为准</p>
        ${tablePanel(rows, ["用户名", "租户", "角色", "状态", "操作"], (row) => `
        <tr>
          <td>${escapeHtml(row.username)}</td>
          <td class="mono">${escapeHtml(row.tenant_id)}</td>
          <td>${escapeHtml((row.roles || []).join(", "))}</td>
          <td>${badge(row.status)}</td>
          <td><a class="btn secondary" href="#/iam/users/form?id=${encodeURIComponent(row.id)}">编辑</a></td>
        </tr>`)}</div>`;
    }

    async function pageUsersForm(root) {
      const id = routeQuery().get("id");
      const users = (await api("/api/admin/users")).items || [];
      const user = id ? users.find((u) => u.id === id || u.username === id) : null;
      let detail = user;
      if (user) {
        try { detail = await api(`/api/admin/users/${encodeURIComponent(user.id)}`); } catch { /* list row ok */ }
      }
      root.innerHTML = `
        <div class="panel">
          <p><a href="#/iam/users">← 用户列表</a></p>
          ${detail ? `
            <dl class="kv">
              <dt>用户名</dt><dd>${escapeHtml(detail.username)}</dd>
              <dt>ID</dt><dd class="mono">${escapeHtml(detail.id)}</dd>
              <dt>租户</dt><dd>${escapeHtml(detail.tenant_id)}</dd>
              <dt>有效权限数</dt><dd>${(detail.perms || []).length}</dd>
            </dl>
            <div class="field"><label>角色（可多选）</label>
              <select id="u-roles" class="ctrl" multiple size="3">
                <option value="tenant_admin" ${(detail.roles||[]).includes("tenant_admin")?"selected":""}>tenant_admin</option>
                <option value="auditor" ${(detail.roles||[]).includes("auditor")?"selected":""}>auditor</option>
              </select></div>
            <div class="field"><label>状态</label>
              <select id="u-status" class="ctrl">
                <option value="active" ${detail.status==="active"?"selected":""}>active</option>
                <option value="disabled" ${detail.status==="disabled"?"selected":""}>disabled</option>
              </select></div>
            <div class="field"><label>重置密码（可选）</label><input id="u-pass" type="password" autocomplete="new-password" /></div>
            <button class="btn" id="u-save">保存用户</button>
            <button class="btn secondary" id="u-disable" style="margin-left:8px">停用</button>
          ` : `
            <h3>新建用户</h3>
            <div class="field"><label>用户名</label><input id="u-name" /></div>
            <div class="field"><label>密码</label><input id="u-pass" type="password" autocomplete="new-password" /></div>
            <div class="field"><label>角色（可多选）</label>
              <select id="u-roles" class="ctrl" multiple size="3">
                <option value="tenant_admin">tenant_admin</option>
                <option value="auditor" selected>auditor</option>
              </select></div>
            <button class="btn" id="u-create">创建</button>
          `}
          <p class="meta">权限由后端强制；不可跨租户；不可给自己提权。演示账号生产禁用。</p>
        </div>`;
      if (typeof watchFormDirty === "function") watchFormDirty(root);
      const rolesVal = () => Array.from(root.querySelector("#u-roles")?.selectedOptions || []).map((o) => o.value);
      root.querySelector("#u-create")?.addEventListener("click", async (ev) => {
        await runAction(ev.currentTarget, async () => {
          await api("/api/admin/users", {
            method: "POST",
            json: { username: root.querySelector("#u-name").value, password: root.querySelector("#u-pass").value, roles: rolesVal() },
          });
          if (typeof markFormDirty === "function") markFormDirty(false);
          toast("用户已创建");
          location.hash = "#/iam/users";
        });
      });
      root.querySelector("#u-save")?.addEventListener("click", async (ev) => {
        await runAction(ev.currentTarget, async () => {
          const body = { roles: rolesVal(), status: root.querySelector("#u-status").value };
          const pass = root.querySelector("#u-pass")?.value;
          if (pass) body.password = pass;
          await api(`/api/admin/users/${encodeURIComponent(detail.id)}`, { method: "PATCH", json: body });
          if (typeof markFormDirty === "function") markFormDirty(false);
          toast("用户已更新");
          await renderRoute();
        });
      });
      root.querySelector("#u-disable")?.addEventListener("click", async (ev) => {
        if (!confirm("确认停用该用户？其现有会话将立即失效。")) return;
        await runAction(ev.currentTarget, async () => {
          await api(`/api/admin/users/${encodeURIComponent(detail.id)}`, { method: "PATCH", json: { status: "disabled" } });
          if (typeof markFormDirty === "function") markFormDirty(false);
          toast("已停用");
          await renderRoute();
        });
      });
    }

    return {
      "/iam/users": pageUsers,
      "/iam/users/form": pageUsersForm
    };
  });
})();
