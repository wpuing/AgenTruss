/**
 * System status, theme, rate-limit (readonly), component config.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-system.js");
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

    async function pageRateLimit(root) {
  pageTitleActions(root, `<button class="btn secondary" id="rl-refresh">刷新</button>
    <a class="btn secondary" href="#/system/model" style="margin-left:8px">组件配置中心</a>`);
  let status;
  let comps;
  let basic = {};
  try {
    [status, comps, basic] = await Promise.all([
      api("/api/admin/system/status"),
      api("/api/admin/components"),
      api("/api/admin/system/basic").catch(() => ({})),
    ]);
  } catch (err) {
    return renderLoadError(root, "限流页加载失败", err);
  }
  const sec = (comps.items || []).find((c) => c.id === "security") || {};
  const redis = (comps.items || []).find((c) => c.id === "redis") || {};
  const fields = sec.fields || {};
  // Prefer system/basic snapshot (authoritative settings); fall back to component fields.
  const chatLimit = basic.rate_limit_per_minute ?? fields.rate_limit_per_minute ?? "RATE_LIMIT_PER_MINUTE / .env";
  const loginLimit = basic.login_rate_limit_per_minute ?? fields.login_rate_limit_per_minute ?? "-";
  root.innerHTML = `
    <div class="alert alert-warn readonly-banner">
      <span class="badge warn">只读</span>
      <span>本页<strong>无保存按钮</strong>（rate_limit_writable=${escapeHtml(String(basic.rate_limit_writable === false ? "false" : basic.rate_limit_writable ?? "false"))}）。
        在线写入限流未实现。改阈值请编辑部署 <code>.env</code> 后重启进程。</span>
    </div>
    <div class="panel">
      <div class="panel-head"><strong>限流与熔断（只读展示）</strong></div>
      <dl class="kv">
        <dt>对话限流</dt><dd class="mono">${escapeHtml(String(chatLimit))} / 分钟</dd>
        <dt>登录限流</dt><dd class="mono">${escapeHtml(String(loginLimit))} / 分钟</dd>
        <dt>组件状态</dt><dd>${escapeHtml(sec.status || "-")} · 模式=${escapeHtml(sec.mode || "-")}</dd>
        <dt>Redis</dt><dd>${escapeHtml(redis.status || redis.mode || "-")} · 已配置=${redis.configured ? "是" : "否"} · 连通=${redis.connected === true ? "是" : redis.connected === false ? "否" : "未检测"}</dd>
        <dt>Ready</dt><dd>${badge(status.ready?.status || "-")} · health=${escapeHtml(status.health?.status || "-")}</dd>
        <dt>应用名</dt><dd>${escapeHtml(basic["app.name"] || "-")}</dd>
      </dl>
      <p class="meta">${escapeHtml(basic.note || "区分：配置已展示 ≠ 已在线改写。")}</p>
      <p class="meta">${metaBadge(comps._meta)} · 检查于 ${escapeHtml(comps.checked_at || status.checked_at || "-")}</p>
    </div>`;
  document.getElementById("rl-refresh")?.addEventListener("click", () => renderRoute());
}

    async function pageComponents(root) {
  pageTitleActions(root, `<button class="btn secondary" id="comp-refresh">刷新</button>`);
  let data;
  try {
    data = await api("/api/admin/components");
  } catch (err) {
    return renderLoadError(root, "组件配置加载失败", err);
  }
  const writableIds = new Set(["llm", "embedding", "vector", "object_storage", "redis", "agent_runtime", "workflow", "security"]);
  root.innerHTML = `
    ${demoModeBanner(data.admin_demo_mode)}
    <p class="meta">${metaBadge(data._meta)} · 检查于 ${escapeHtml(data.checked_at || "-")} · 保存≠连通</p>
    <div class="dash-grid">
      ${(data.items || []).map((c) => `
        <div class="panel" data-cid="${escapeAttr(c.id)}">
          <div class="panel-head"><strong>${escapeHtml(c.title)}</strong>
            <span class="badge ${String(c.status).includes("not_") || c.status === "mock" || c.status === "stub" ? "warn" : "ok"}">${escapeHtml(c.status || c.mode || "-")}</span>
          </div>
          <dl class="kv">
            <dt>模式</dt><dd class="mono">${escapeHtml(c.mode || "-")}</dd>
            <dt>已配置</dt><dd>${c.configured ? "是" : "否"} ${c.configured_label ? `(${escapeHtml(c.configured_label)})` : ""}</dd>
            <dt>连通</dt><dd class="comp-conn">${c.connected === true ? "是" : c.connected === false ? "否/未接入" : "未检测"}</dd>
          </dl>
          <div class="comp-fields" id="fields-${escapeAttr(c.id)}"><p class="meta">加载可写字段…</p></div>
          <p class="meta">${escapeHtml(c.note || "")}</p>
          <p class="meta comp-probe" id="probe-${escapeAttr(c.id)}"></p>
          <button class="btn secondary comp-test" data-id="${escapeAttr(c.id)}" ${writableIds.has(c.id) ? "" : "disabled"}>测试连接</button>
          <button class="btn comp-save" data-id="${escapeAttr(c.id)}" ${writableIds.has(c.id) ? "" : "disabled"}>保存配置</button>
        </div>`).join("")}
    </div>
    <p class="meta" style="margin-top:12px">密钥空值=保留原值；查询仅掩码。连接失败不会回滚已保存配置。</p>`;
  document.getElementById("comp-refresh")?.addEventListener("click", () => renderRoute());
  // Bind actions before async field hydrate so E2E/CI clicks are never no-ops.
  root.querySelectorAll(".comp-save").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-id");
      const box = document.getElementById(`fields-${id}`);
      const body = collectComponentFields(box);
      await runBusy(btn, async () => {
        await api(`/api/admin/components/${encodeURIComponent(id)}`, { method: "PUT", json: body });
        markFormDirty(false);
        toast("配置已保存（≠连通成功）");
        await renderRoute();
      });
    });
  });
  root.querySelectorAll(".comp-test").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const id = btn.getAttribute("data-id");
      const probe = document.getElementById(`probe-${id}`);
      if (probe) probe.textContent = "测试中…";
      await runBusy(btn, async () => {
        const res = await api(`/api/admin/components/${encodeURIComponent(id)}/test-connection`, { method: "POST" });
        if (probe && probe.isConnected) {
          probe.textContent = `测连: ${res.status} · ok=${res.ok} · ${res.detail || ""}`;
        }
        toast(res.ok ? "连接探测成功" : `连接探测未通过: ${res.status}`, res.ok ? undefined : "error");
      });
    });
  });
  for (const c of data.items || []) {
    if (!writableIds.has(c.id)) continue;
    try {
      const detail = await api(`/api/admin/components/${encodeURIComponent(c.id)}`);
      if (root.isCurrent && !root.isCurrent()) return;
      const box = document.getElementById(`fields-${c.id}`);
      if (!box) continue;
      const fields = detail.fields || {};
      const parts = [];
      for (const key of detail.writable || []) {
        parts.push(`<div class="field"><label>${escapeHtml(key)}</label>
          <input data-field="${escapeAttr(key)}" value="${escapeAttr(fields[key] == null ? "" : String(fields[key]))}" /></div>`);
      }
      for (const key of detail.writable_secrets || []) {
        const conf = fields[key]?.configured;
        parts.push(`<div class="field"><label>${escapeHtml(key)} ${conf ? "(已配置)" : "(未配置)"}</label>
          <input data-field="${escapeAttr(key)}" type="password" placeholder="${conf ? "留空保留原密钥" : "输入密钥"}" autocomplete="off" /></div>`);
      }
      box.innerHTML = parts.length ? parts.join("") : `<p class="meta">无可写字段</p>`;
      watchFormDirty(box);
    } catch (err) {
      if (isAbortError(err)) return;
      const box = document.getElementById(`fields-${c.id}`);
      if (box) box.innerHTML = `<p class="meta">详情加载失败：${escapeHtml(err.message || "未知错误")}</p>`;
    }
  }
}

    function collectComponentFields(box) {
  const body = {};
  box?.querySelectorAll("[data-field]").forEach((inp) => {
    const key = inp.getAttribute("data-field");
    const val = inp.value;
    if (inp.type === "password" && !val) return;
    if (key === "agentscope_enabled") body[key] = val === "true" || val === "1";
    else if (["embedding_dim", "login_rate_limit_per_minute", "rate_limit_per_minute"].includes(key)) body[key] = Number(val);
    else body[key] = val;
  });
  return body;
}

    async function pageSystem(root) {
  pageTitleActions(root, `<button class="btn secondary" id="sys-refresh">刷新状态</button>
    <a class="btn secondary" href="#/system/model" style="margin-left:8px">组件配置中心</a>`);
  let basic;
  let status;
  try {
    [basic, status] = await Promise.all([
      api("/api/admin/system/basic"),
      api("/api/admin/system/status"),
    ]);
  } catch (err) {
    return renderLoadError(root, "系统状态加载失败", err);
  }
  const checks = status.ready?.checks || {};
  const chip = (label, value, ok) => {
    const cls = ok === true ? "ok" : ok === false ? "warn" : "";
    return `<span class="chip ${cls}"><strong>${escapeHtml(label)}</strong> ${escapeHtml(String(value))}</span>`;
  };
  root.innerHTML = `
    <div class="dash-grid">
      <div class="panel">
        <div class="panel-head"><strong>健康与就绪</strong>
          <span class="badge ${status.ready_ok ? "ok" : "warn"}">${escapeHtml(status.ready?.status || "-")}</span>
        </div>
        <div class="status-chips">
          ${chip("Health", status.health?.status || "-", status.health?.status === "ok")}
          ${chip("Ready", status.ready?.status || "-", !!status.ready_ok)}
          ${chip("PostgreSQL / 内存", checks.postgres ?? checks.platform ?? "-", checks.postgres !== false)}
          ${chip("Redis", checks.redis ?? "n/a", checks.redis === true || checks.redis === "not_configured")}
          ${chip("向量", checks.vector_store ?? status.vector_store_backend, checks.vector_store !== "mock")}
          ${chip("Mock RAG", checks.mock_rag ? "启用(非生产语义)" : "否", false)}
          ${chip("MinIO", checks.minio ?? "n/a", checks.minio === true)}
        </div>
        <p class="meta">检查时间 ${escapeHtml(status.checked_at || "-")} · 环境 ${escapeHtml(status.app_env || "-")}</p>
        ${(status.notes || []).map((n) => `<p class="meta note-line">⚠ ${escapeHtml(n)}</p>`).join("")}
      </div>
      <div class="panel">
        <div class="panel-head"><strong>安全配置概览</strong></div>
        <dl class="kv">
          <dt>应用名</dt><dd>${escapeHtml(basic["app.name"] || "-")}</dd>
          <dt>存储后端</dt><dd class="mono">${escapeHtml(status.storage_backend || "-")}</dd>
          <dt>Embedding</dt><dd class="mono">${escapeHtml(status.embedding_provider || "-")}</dd>
          <dt>向量库</dt><dd class="mono">${escapeHtml(status.vector_store_backend || "-")}</dd>
          <dt>工作流引擎</dt><dd class="mono">${escapeHtml(status.workflow_engine || "-")}</dd>
          <dt>Agent Runtime</dt><dd class="mono">${escapeHtml(status.agent_runtime || "-")}</dd>
          <dt>AgentScope</dt><dd>${status.agentscope_enabled ? "已启用" : "未启用"}</dd>
          <dt>模型密钥</dt><dd class="mono">${escapeHtml(basic["model.api_key"] || "********")}</dd>
        </dl>
        <p class="meta">禁止展示 JWT / DB / Redis / Ticket 密钥。密钥字段仅掩码。</p>
        <p style="margin-top:12px"><a class="btn secondary" href="#/system/theme">主题切换</a>
          <a class="btn secondary" href="#/system/model">组件卡</a></p>
      </div>
    </div>`;
  document.getElementById("sys-refresh")?.addEventListener("click", () => renderRoute());
}

    async function pageTheme(root) {
  const data = await api("/api/admin/system/themes");
  const current = data.current || "ember";
  applyTheme(current);
  const items = data.items || [];
  const currentMeta = items.find((t) => t.id === current);
  root.innerHTML = `
    <div class="panel theme-intro">
      <div class="panel-head">
        <strong>外观主题</strong>
        <span class="badge ok">当前 · ${escapeHtml(currentMeta?.name || current)}</span>
      </div>
      <p class="meta" style="margin:0">共 ${items.length} 套。点击卡片立即应用并写入进程配置；本机 localStorage 同步记住。窄屏下卡片纵向排列。</p>
    </div>
    <div class="theme-grid" role="listbox" aria-label="主题列表">
      ${items.map((theme) => {
        const [c1, c2, c3] = theme.swatch || ["#888", "#aaa", "#ccc"];
        const active = theme.id === current;
        return `
          <button type="button" class="theme-card${active ? " active" : ""}" data-theme-id="${escapeAttr(theme.id)}"
            role="option" aria-selected="${active ? "true" : "false"}"
            style="--card-c1:${escapeAttr(c1)};--card-c2:${escapeAttr(c2)};--card-c3:${escapeAttr(c3)}">
            <div class="swatch" aria-hidden="true"></div>
            <strong>${escapeHtml(theme.name)}</strong>
            <p class="truncate-2">${escapeHtml(theme.blurb || "")}</p>
            ${active ? `<span class="theme-current-tag">使用中</span>` : ""}
          </button>`;
      }).join("")}
    </div>`;
  root.querySelectorAll("[data-theme-id]").forEach((btn) => {
    btn.onclick = async () => {
      if (btn.classList.contains("is-busy")) return;
      const themeId = btn.dataset.themeId;
      if (themeId === (document.documentElement.getAttribute("data-theme") || current)
          && btn.classList.contains("active")) {
        toast("已是当前主题");
        return;
      }
      await runBusy(btn, async () => {
        await api("/api/admin/system/theme", { method: "PUT", json: { theme_id: themeId } });
        applyTheme(themeId);
        toast(`已切换：${themeId}`);
        if (root.isCurrent && root.isCurrent()) await pageTheme(root);
      });
    };
  });
}

    return {
      "/system/basic": pageSystem,
      "/system/theme": pageTheme,
      "/system/model": pageComponents,
      "/system/ratelimit": pageRateLimit
    };
  });
})();
