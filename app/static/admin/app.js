(() => {
  const state = {
    token: localStorage.getItem("ag_token") || "",
    username: localStorage.getItem("ag_user") || "",
    tenant: localStorage.getItem("ag_tenant") || "",
    menus: [],
    route: location.hash.slice(1).split("?")[0] || "/dashboard",
    /** Full hash path including query — used to roll back dirty navigations accurately. */
    routeHash: location.hash.slice(1) || "/dashboard",
    sessionId: null,
    chatAgentId: "",
    selectedDocId: "",
    selectedKbId: "",
    // v2: default collapsed; ignore legacy open-all preferences.
    openGroups: JSON.parse(localStorage.getItem("ag_open_groups_v2") || "null") || {},
  };

  const el = {
    loginView: document.getElementById("login-view"),
    shell: document.getElementById("shell"),
    nav: document.getElementById("nav"),
    content: document.getElementById("content"),
    userMeta: document.getElementById("user-meta"),
    loginForm: document.getElementById("login-form"),
    loginError: document.getElementById("login-error"),
    logoutBtn: document.getElementById("logout-btn"),
    navToggle: document.getElementById("nav-toggle"),
    modalRoot: document.getElementById("modal-root"),
  };

  const Kit = window.AgenTrussKit || {};
  let toastStack = 0;
  function toast(message, kind) {
    let host = document.getElementById("toast-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "toast-host";
      host.className = "toast-host";
      host.setAttribute("aria-live", "polite");
      document.body.appendChild(host);
    }
    // Cap stack so rapid errors cannot bury the viewport.
    while (host.children.length >= 4) host.firstChild.remove();
    const node = document.createElement("div");
    node.className = `toast${kind ? ` toast-${kind}` : ""}`;
    node.setAttribute("role", kind === "error" ? "alert" : "status");
    node.textContent = message;
    host.appendChild(node);
    toastStack += 1;
    const ttl = kind === "error" ? 4200 : 2200;
    setTimeout(() => {
      node.remove();
      toastStack = Math.max(0, toastStack - 1);
    }, ttl);
  }

  /** Bump on each navigation so stale async page renders cannot overwrite the current view. */
  let routeSeq = 0;
  let routeAbort = null;

  function isAbortError(err) {
    return typeof Kit.isAbortError === "function" ? Kit.isAbortError(err) : !!(err && (err.aborted || err.name === "AbortError"));
  }

  async function api(path, options = {}) {
    const headers = Object.assign({}, options.headers || {});
    if (state.token) headers.Authorization = `Bearer ${state.token}`;
    const fetchOpts = { ...options, headers };
    if (options.json !== undefined) {
      headers["Content-Type"] = "application/json";
      fetchOpts.body = JSON.stringify(options.json);
    }
    // Default to current route AbortSignal so leaving a page cancels in-flight loads.
    // Pass signal: null to opt out (login / logout must not ride page navigation abort).
    if (options.signal === null) {
      delete fetchOpts.signal;
    } else if (!fetchOpts.signal && routeAbort) {
      fetchOpts.signal = routeAbort.signal;
    }
    delete fetchOpts.json;
    let response;
    try {
      response = await fetch(path, fetchOpts);
    } catch (err) {
      if (err && (err.name === "AbortError" || (routeAbort && routeAbort.signal.aborted))) {
        const aborted = new Error("aborted");
        aborted.aborted = true;
        aborted.name = "AbortError";
        throw aborted;
      }
      throw err;
    }
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
    if (!response.ok) {
      const message = (data && (data.message || data.detail)) || `HTTP ${response.status}`;
      const error = new Error(typeof message === "string" ? message : JSON.stringify(message));
      error.status = response.status;
      error.detail = data && data.detail;
      error.payload = data;
      if (response.status === 409) {
        error.conflict = true;
        error.message = (typeof message === "string" ? message : "版本冲突") + "（请重新加载后再保存）";
      }
      throw error;
    }
    return data;
  }

  /** Track dirty forms in page body; warn on in-app navigation when dirty. */
  let formDirty = false;
  function markFormDirty(flag) {
    formDirty = !!flag;
  }
  function watchFormDirty(root) {
    if (!root) return;
    root.querySelectorAll("input, select, textarea").forEach((node) => {
      if (node.dataset.dirtyBound === "1") return;
      // Skip controls that cannot represent user edits worth leaving-confirm.
      if (node.disabled || node.readOnly) return;
      if (node.dataset.noDirty === "1") return;
      if (node.closest(".filters") || node.closest(".agent-subnav")) return;
      if (node.type === "hidden" || node.type === "button" || node.type === "submit" || node.type === "reset") return;
      node.dataset.dirtyBound = "1";
      node.addEventListener("input", () => { formDirty = true; });
      node.addEventListener("change", () => { formDirty = true; });
    });
  }
  function confirmLeaveIfDirty() {
    if (!formDirty) return true;
    return window.confirm("有未保存的修改，确定离开？");
  }

  /** Shared list/detail load failure panel with retry. */
  function renderLoadError(root, title, err) {
    if (typeof Kit.mountFailPanel === "function") {
      return Kit.mountFailPanel(root, title, err, () => renderRoute());
    }
    if (isAbortError(err)) return;
    root.innerHTML = `<div class="panel alert alert-error"><strong>${escapeHtml(title)}</strong>
      <p>${escapeHtml(err && err.message ? err.message : String(err || "加载失败"))}</p>
      <button class="btn secondary" id="page-retry">重试</button></div>`;
    root.querySelector("#page-retry").onclick = () => renderRoute();
  }

  function badge(status) {
    return typeof Kit.badge === "function" ? Kit.badge(status) : `<span class="badge">${escapeHtml(status || "-")}</span>`;
  }

  function escapeHtml(value) {
    return typeof Kit.escapeHtml === "function"
      ? Kit.escapeHtml(value)
      : String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
  }

  /** Attribute-safe escaping for ids placed into HTML attributes. */
  function escapeAttr(value) {
    return typeof Kit.escapeAttr === "function" ? Kit.escapeAttr(value) : escapeHtml(value).replaceAll("'", "&#39;");
  }

  async function runBusy(btn, work, opts) {
    if (typeof Kit.runBusy === "function") {
      return Kit.runBusy(btn, work, Object.assign({ toast }, opts || {}));
    }
    if (btn) { btn.disabled = true; btn.classList.add("is-busy"); }
    try { await work(); } catch (e) {
      if (!isAbortError(e)) toast(String(e.message || e), "error");
    } finally {
      if (btn) { btn.disabled = false; btn.classList.remove("is-busy"); }
    }
  }

  function bindFilterEnter(root, applySel, inputSels) {
    if (typeof Kit.bindFilterEnter === "function") {
      Kit.bindFilterEnter(root, applySel, inputSels);
    }
  }

  function detailHead(backHref, backLabel, trailingHtml) {
    return typeof Kit.detailHead === "function"
      ? Kit.detailHead(backHref, backLabel, trailingHtml)
      : `<p><a href="#${backHref}">← ${escapeHtml(backLabel || "返回")}</a></p>`;
  }

  function setAuth(payload) {
    state.token = payload.access_token;
    state.username = payload.username;
    state.tenant = payload.tenant_id;
    localStorage.setItem("ag_token", state.token);
    localStorage.setItem("ag_user", state.username);
    localStorage.setItem("ag_tenant", state.tenant);
  }

  function clearAuth() {
    state.token = "";
    state.username = "";
    state.tenant = "";
    state.menus = [];
    localStorage.removeItem("ag_token");
    localStorage.removeItem("ag_user");
    localStorage.removeItem("ag_tenant");
  }

  async function boot() {
    if (!state.token) {
      showLogin();
      return;
    }
    try {
      const me = await api("/api/admin/me");
      state.username = me.username;
      state.tenant = me.tenant_id;
      // phase=all shows P1/P2 pages now wired; use ?phase=MVP to hide non-MVP.
      const phase = new URLSearchParams(location.hash.split("?")[1] || "").get("phase") || "all";
      const menus = await api(`/api/admin/me/menus?phase=${encodeURIComponent(phase)}`);
      state.menus = menus.menus || [];
      await syncThemeFromServer();
      showShell();
      // Login lands on 工作台; folders stay collapsed unless the route needs them.
      if (!ensureDefaultRoute()) return;
      renderNav();
      await renderRoute();
    } catch (err) {
      clearAuth();
      showLogin();
      el.loginError.textContent = "登录已失效，请重新登录。";
    }
  }

  function showLogin() {
    el.loginView.classList.remove("hidden");
    el.shell.classList.add("hidden");
  }

  function showShell() {
    el.loginView.classList.add("hidden");
    el.shell.classList.remove("hidden");
    el.userMeta.textContent = `${state.username} · 租户 ${state.tenant}`;
  }

  function flattenLeaves(nodes, out = []) {
    for (const node of nodes || []) {
      const kids = node.children || [];
      if (kids.length) flattenLeaves(kids, out);
      else if (node.path) out.push(node);
    }
    return out;
  }

  function findMenuByPath(path) {
    return flattenLeaves(state.menus).find((item) => item.path === path) || null;
  }

  function isGroupOpen(code) {
    if (Object.prototype.hasOwnProperty.call(state.openGroups, code)) {
      return !!state.openGroups[code];
    }
    // Folders stay collapsed until the user opens them (or current route needs ancestors).
    return false;
  }

  function expandAncestorsForRoute() {
    // Only open the chain that leads to the active leaf; leave other folders closed.
    const walk = (nodes, ancestors) => {
      for (const node of nodes || []) {
        const kids = node.children || [];
        if (node.path === state.route) {
          ancestors.forEach((code) => { state.openGroups[code] = true; });
          return true;
        }
        if (kids.length && walk(kids, ancestors.concat(node.code))) return true;
      }
      return false;
    };
    walk(state.menus, []);
    // Accordion: at most one top-level (L1) group open.
    const openL1 = (state.menus || [])
      .filter((n) => (n.children || []).length && state.openGroups[n.code])
      .map((n) => n.code);
    if (openL1.length > 1) {
      const keep = openL1[openL1.length - 1];
      (state.menus || []).forEach((n) => {
        if ((n.children || []).length && n.code !== keep) state.openGroups[n.code] = false;
      });
    }
  }

  function findMenuNode(code, nodes) {
    for (const node of nodes || []) {
      if (node.code === code) return node;
      const hit = findMenuNode(code, node.children || []);
      if (hit) return hit;
    }
    return null;
  }

  function closeSiblingL1(exceptCode) {
    (state.menus || []).forEach((node) => {
      if (!(node.children || []).length) return;
      if (node.code === exceptCode) return;
      state.openGroups[node.code] = false;
      const esc = (typeof CSS !== "undefined" && CSS.escape)
        ? CSS.escape(node.code)
        : String(node.code).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      const block = el.nav.querySelector(`.nav-block[data-depth="0"][data-group="${esc}"]`);
      if (!block) return;
      const parentBtn = block.querySelector(":scope > .nav-parent");
      parentBtn?.classList.remove("open");
      parentBtn?.setAttribute("aria-expanded", "false");
      block.querySelector(":scope > .nav-children")?.classList.remove("open");
    });
  }

  function renderFlyoutHtml(nodes) {
    return (nodes || []).map((node) => {
      const kids = node.children || [];
      if (kids.length) {
        return `
          <div class="flyout-section">
            <div class="flyout-section-title">${escapeHtml(node.title)}</div>
            ${renderFlyoutHtml(kids)}
          </div>`;
      }
      return `
        <a class="flyout-link ${state.route === node.path ? "active" : ""}" href="#${escapeAttr(node.path || "")}">
          ${escapeHtml(node.title)}
          ${node.phase && node.phase !== "MVP" ? `<span class="phase-tag">${escapeHtml(node.phase)}</span>` : ""}
        </a>`;
    }).join("");
  }

  function ensureNavFlyout() {
    let flyout = document.getElementById("nav-flyout");
    if (flyout) return flyout;
    flyout = document.createElement("div");
    flyout.id = "nav-flyout";
    flyout.className = "nav-flyout hidden";
    flyout.setAttribute("role", "menu");
    document.body.appendChild(flyout);
    return flyout;
  }

  let flyoutHideTimer = null;

  function hideNavFlyout() {
    const flyout = document.getElementById("nav-flyout");
    if (flyout) {
      flyout.classList.add("hidden");
      flyout.innerHTML = "";
    }
    el.nav?.querySelectorAll(".nav-block.flyout-open").forEach((n) => n.classList.remove("flyout-open"));
  }

  function scheduleHideNavFlyout() {
    clearTimeout(flyoutHideTimer);
    flyoutHideTimer = setTimeout(hideNavFlyout, 180);
  }

  function showNavFlyout(block, menuNode) {
    clearTimeout(flyoutHideTimer);
    const kids = menuNode?.children || [];
    if (!kids.length) return;
    const flyout = ensureNavFlyout();
    el.nav.querySelectorAll(".nav-block.flyout-open").forEach((n) => n.classList.remove("flyout-open"));
    block.classList.add("flyout-open");
    flyout.innerHTML = `
      <div class="flyout-head">${escapeHtml(menuNode.title)}</div>
      <div class="flyout-body">${renderFlyoutHtml(kids)}</div>`;
    flyout.classList.remove("hidden");
    const rect = block.getBoundingClientRect();
    const pad = 8;
    let top = rect.top;
    flyout.style.left = `${Math.round(rect.right + 10)}px`;
    flyout.style.top = `${Math.round(top)}px`;
    // Keep inside viewport after paint.
    requestAnimationFrame(() => {
      const box = flyout.getBoundingClientRect();
      let nextTop = top;
      if (box.bottom > window.innerHeight - pad) {
        nextTop = Math.max(pad, window.innerHeight - box.height - pad);
      }
      if (nextTop < pad) nextTop = pad;
      flyout.style.top = `${Math.round(nextTop)}px`;
    });
    flyout.onmouseenter = () => clearTimeout(flyoutHideTimer);
    flyout.onmouseleave = () => scheduleHideNavFlyout();
    flyout.onclick = (ev) => {
      const link = ev.target.closest("a.flyout-link");
      if (link) hideNavFlyout();
    };
  }

  function pathOnly(hashPath) {
    return String(hashPath || "").split("?")[0] || "/dashboard";
  }

  function ensureDefaultRoute() {
    const hash = location.hash.slice(1);
    if (!hash || hash === "/") {
      state.route = "/dashboard";
      state.routeHash = "/dashboard";
      if (location.hash !== "#/dashboard") {
        location.hash = "/dashboard";
        return false;
      }
    } else {
      state.route = pathOnly(hash);
      state.routeHash = hash;
    }
    return true;
  }

  function renderNavNode(node, depth) {
    const kids = node.children || [];
    if (!kids.length) {
      const depthClass = depth >= 2 ? "nav-item nav-l3" : depth === 1 ? "nav-item" : "nav-leaf";
      return `
        <a class="${depthClass} ${state.route === node.path ? "active" : ""}" href="#${escapeAttr(node.path || "")}">
          ${escapeHtml(node.title)}
          ${node.phase && node.phase !== "MVP" ? `<span class="phase-tag">${escapeHtml(node.phase)}</span>` : ""}
        </a>`;
    }
    const open = isGroupOpen(node.code);
    const parentClass = depth === 0 ? "nav-parent" : "nav-parent nav-parent-l2";
    return `
      <div class="nav-block" data-depth="${depth}" data-group="${escapeAttr(node.code)}">
        <button type="button" class="${parentClass} ${open ? "open" : ""}" data-toggle-group="${escapeAttr(node.code)}" data-depth="${depth}" aria-expanded="${open ? "true" : "false"}">
          <span>${escapeHtml(node.title)}</span>
          <span class="caret">›</span>
        </button>
        <div class="nav-children ${open ? "open" : ""}" data-depth="${depth}">
          ${kids.map((child) => renderNavNode(child, depth + 1)).join("")}
        </div>
      </div>`;
  }

  function renderNav() {
    expandAncestorsForRoute();
    localStorage.setItem("ag_open_groups_v2", JSON.stringify(state.openGroups));
    el.nav.innerHTML = state.menus
      .map((node, idx) => {
        const html = renderNavNode(node, 0);
        // Emphasize the final planning block so it is hard to miss when scrolling.
        if (node.code === "backlog") {
          return `<div class="nav-section-label">规划</div>${html}`;
        }
        if (idx === 0) return html;
        return html;
      })
      .join("");
    hideNavFlyout();
    // Keep backlog group expanded by default.
    if (state.menus.some((m) => m.code === "backlog") && state.openGroups.backlog !== false) {
      state.openGroups.backlog = true;
      const block = el.nav.querySelector('.nav-block[data-group="backlog"]');
      const btn = block?.querySelector("[data-toggle-group]");
      const children = block?.querySelector(":scope > .nav-children");
      if (btn && children) {
        btn.classList.add("open");
        btn.setAttribute("aria-expanded", "true");
        children.classList.add("open");
      }
    }

    el.nav.querySelectorAll("[data-toggle-group]").forEach((btn) => {
      btn.onclick = () => {
        const group = btn.dataset.toggleGroup;
        const depth = Number(btn.dataset.depth || "0");
        const children = btn.parentElement.querySelector(":scope > .nav-children");
        const willOpen = !children.classList.contains("open");
        if (depth === 0 && willOpen) {
          // Accordion: opening one L1 closes other L1 groups.
          closeSiblingL1(group);
        }
        state.openGroups[group] = willOpen;
        localStorage.setItem("ag_open_groups_v2", JSON.stringify(state.openGroups));
        btn.classList.toggle("open", willOpen);
        btn.setAttribute("aria-expanded", willOpen ? "true" : "false");
        children.classList.toggle("open", willOpen);
      };
    });

    // Hover flyout: show subordinate menus beside the sidebar item.
    el.nav.querySelectorAll(".nav-block[data-group]").forEach((block) => {
      const code = block.dataset.group;
      const menuNode = findMenuNode(code, state.menus);
      if (!menuNode || !(menuNode.children || []).length) return;
      block.addEventListener("mouseenter", () => showNavFlyout(block, menuNode));
      block.addEventListener("mouseleave", () => scheduleHideNavFlyout());
    });
  }

  function pageCrumbs(path, titleMap) {
    if (typeof Kit.pageCrumbs === "function") return Kit.pageCrumbs(path, titleMap);
    return "";
  }

  async function renderRoute() {
    const rawHash = location.hash.slice(1) || state.routeHash || state.route || "/dashboard";
    const nextPath = pathOnly(rawHash);
    const nextHash = rawHash || nextPath;
    if (formDirty) {
      if (!confirmLeaveIfDirty()) {
        // Same path with different query (e.g. Agent id) must also roll back.
        if (nextHash !== (state.routeHash || state.route)) {
          history.replaceState(null, "", `#${state.routeHash || state.route || "/dashboard"}`);
        }
        return;
      }
      formDirty = false;
    }
    if (routeAbort) {
      try { routeAbort.abort(); } catch { /* ignore */ }
    }
    routeAbort = new AbortController();
    const seq = ++routeSeq;
    state.route = nextPath;
    state.routeHash = nextHash;
    formDirty = false;
    const path = state.route;
    const titleMap = Object.fromEntries(flattenLeaves(state.menus).map((m) => [m.path, m.title]));
    const pageTitle = titleMap[path] || "页面";
    const topTitle = document.querySelector(".topbar .title");
    if (topTitle) topTitle.textContent = pageTitle;
    const handlers = buildRouteHandlers();
    const handler = handlers[path] || pagePlaceholder;
    const loading = typeof Kit.loadingHtml === "function"
      ? Kit.loadingHtml("加载中…")
      : `<div class="empty page-loading" aria-busy="true"><span class="spinner" aria-hidden="true"></span><span>加载中…</span></div>`;
    el.content.innerHTML = `<div class="page-title"><div>${pageCrumbs(path, titleMap)}<h2>${escapeHtml(pageTitle)}</h2><p class="page-path mono">${escapeHtml(path)}</p></div></div><div id="page-body">${loading}</div>`;
    renderNav();
    const bodyEl = document.getElementById("page-body");
    // Guard writes so a slower previous navigation cannot clobber the current page.
    const root = new Proxy(bodyEl, {
      set(target, prop, value) {
        if (prop === "innerHTML" && seq !== routeSeq) return true;
        target[prop] = value;
        return true;
      },
      get(target, prop) {
        if (prop === "isCurrent") return () => seq === routeSeq;
        const v = target[prop];
        if (typeof v === "function") return v.bind(target);
        return v;
      },
    });
    try {
      await handler(root);
    } catch (err) {
      if (seq !== routeSeq || isAbortError(err)) return;
      bodyEl.innerHTML = `<div class="panel alert alert-error"><strong>页面加载失败</strong><p>${escapeHtml(err.message)}</p><button class="btn secondary" id="page-retry">重试</button></div>`;
      document.getElementById("page-retry")?.addEventListener("click", () => renderRoute());
    }
  }

  function agentConfigSubnav(active, agentId, hint) {
    if (typeof Kit.agentConfigNav === "function") {
      return Kit.agentConfigNav(active, agentId, hint);
    }
    return `<nav class="subnav agent-subnav" aria-label="Agent 配置步骤"></nav>
      <p class="meta subnav-hint">${escapeHtml(hint || "")}</p>`;
  }

  /** Merge page modules + console-pages once; helpers close over mutable state. */
  let routeHandlersCache = null;
  function buildRouteHandlers() {
    if (routeHandlersCache) return routeHandlersCache;
    const pageHelpers = {
      api, escapeHtml, escapeAttr, badge, toast, routeQuery, metaBadge,
      tablePanel, openModal, state, renderRoute, pageTitleActions, demoModeBanner,
      watchFormDirty, markFormDirty, runBusy, isAbortError,
      agentConfigNav: agentConfigSubnav, renderLoadError, bindFilterEnter, detailHead,
      applyTheme,
    };
    const handlers = {};
    // Later modules override earlier (ext-* after core pages-* for config/detail routes).
    for (const register of (window.AgenTrussPageModules || [])) {
      try {
        Object.assign(handlers, register(pageHelpers) || {});
      } catch (err) {
        console.error("page module register failed", err);
      }
    }
    if (!handlers["/system/phase2"] && handlers["/system/model"]) {
      handlers["/system/phase2"] = handlers["/system/model"];
    }
    routeHandlersCache = handlers;
    // Debug/test aid: list of registered hash paths (no handlers leaked).
    window.__AgenTrussRoutes = Object.keys(handlers).sort();
    return handlers;
  }

  function pageTitleActions(body, html) {
    const host = el.content.querySelector(".page-title");
    if (host) {
      const box = document.createElement("div");
      box.innerHTML = html;
      host.appendChild(box);
    }
  }

  function routeQuery() {
    return new URLSearchParams((location.hash.split("?")[1] || ""));
  }

  function metaBadge(meta) {
    if (!meta) return "";
    const demo = meta.is_demo ? `<span class="badge warn">演示数据</span>` : `<span class="badge ok">真实数据</span>`;
    return `<span class="meta-source">${demo} <span class="meta">source=${escapeHtml(meta.source || "-")}</span></span>`;
  }

  function demoModeBanner(flag, text) {
    if (!flag) return "";
    return `<div class="panel demo-banner"><span class="badge warn">演示 / Mock 模式</span> ${escapeHtml(text || "当前启用 ADMIN_DEMO_MODE；演示操作不会写入生产权威数据。")}</div>`;
  }

  function applyTheme(themeId) {
    const id = themeId || "ember";
    document.documentElement.setAttribute("data-theme", id);
    localStorage.setItem("ag_theme", id);
  }

  async function syncThemeFromServer() {
    try {
      const data = await api("/api/admin/system/themes");
      if (data.current) applyTheme(data.current);
    } catch {
      applyTheme(localStorage.getItem("ag_theme") || "ember");
    }
  }

  function pagePlaceholder(root) {
    const menu = findMenuByPath(state.route);
    const phase = menu?.phase || "—";
    const title = menu?.title || "未命名页面";
    const related = {
      "/kb/docs/detail": { path: "/kb/docs", label: "文档列表" },
      "/system/ratelimit": { path: "/system/basic", label: "基础参数（限流只读）" },
      "/backlog": { path: "/dashboard", label: "工作台" },
    };
    const hint = related[state.route];
    const agentExtras = String(state.route || "").startsWith("/agent/")
      ? `<p style="margin:8px 0 0;display:flex;flex-wrap:wrap;gap:8px">
           <a class="btn secondary" href="#/agent/list">Agent 列表</a>
           <a class="btn secondary" href="#/agent/config/graph">工作流</a>
           <a class="btn secondary" href="#/agent/config/tools">工具目录</a>
           <a class="btn secondary" href="#/chat/playground">调试页</a>
         </p>`
      : "";
    root.innerHTML = `
      <div class="panel">
        <div class="empty" style="text-align:left;padding:24px">
          <div style="font-family:var(--font-display);font-size:18px;font-weight:700;margin-bottom:8px">未交付</div>
          <p style="margin:0 0 10px;color:var(--muted)">「${escapeHtml(title)}」已挂入侧栏菜单，本阶段<strong>尚未交付</strong>，无后端完整能力或仅占位。不会伪造保存成功。</p>
          <p style="margin:0 0 6px"><span class="badge warn">${escapeHtml(phase)}</span>
            <span class="mono" style="margin-left:8px">${escapeHtml(state.route)}</span>
          </p>
          ${hint ? `<p style="margin:12px 0 0"><a class="btn secondary" href="#${hint.path}">前往可测入口：${escapeHtml(hint.label)}</a></p>` : ""}
          ${agentExtras}
          <p style="margin:12px 0 0;display:flex;flex-wrap:wrap;gap:8px">
            <a class="btn ghost" href="#/dashboard">工作台</a>
            <a class="btn ghost" href="#/backlog">未完成代办</a>
          </p>
          <p style="margin:12px 0 0;color:var(--muted);font-size:12px">
            可测能力见 AGENTS.md；占位勿当作已完成功能
          </p>
        </div>
      </div>`;
  }

  function tablePanel(rows, headers, rowHtml) {
    if (!rows.length) return `<div class="panel empty">暂无数据</div>`;
    return `<div class="panel"><table class="data"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.map(rowHtml).join("")}</tbody></table></div>`;
  }

  function openModal({ title, body, onOk, okText, hideCancel }) {
    el.modalRoot.innerHTML = `
      <div class="modal-mask" id="modal-mask">
        <div class="modal" role="dialog" aria-modal="true" aria-label="${escapeAttr(title)}">
          <h3>${escapeHtml(title)}</h3>
          <div>${body}</div>
          <div class="error-text" id="modal-error"></div>
          <div class="modal-actions">
            ${hideCancel ? "" : `<button class="btn secondary" id="modal-cancel">取消</button>`}
            <button class="btn" id="modal-ok">${escapeHtml(okText || "确定")}</button>
          </div>
        </div>
      </div>`;
    const close = () => {
      el.modalRoot.innerHTML = "";
      document.removeEventListener("keydown", onKey);
    };
    const onKey = (e) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", onKey);
    const cancelBtn = el.modalRoot.querySelector("#modal-cancel");
    if (cancelBtn) cancelBtn.onclick = close;
    el.modalRoot.querySelector("#modal-mask")?.addEventListener("click", (e) => {
      if (e.target && e.target.id === "modal-mask") close();
    });
    el.modalRoot.querySelector("#modal-ok").onclick = async () => {
      const ok = el.modalRoot.querySelector("#modal-ok");
      if (ok.classList.contains("is-busy")) return;
      ok.disabled = true;
      ok.classList.add("is-busy");
      try {
        if (onOk) await onOk();
        close();
      } catch (err) {
        if (isAbortError(err)) return;
        const errEl = el.modalRoot.querySelector("#modal-error");
        if (errEl) errEl.textContent = err.message || String(err);
        toast(err.message || String(err), "error");
      } finally {
        if (ok.isConnected) {
          ok.disabled = false;
          ok.classList.remove("is-busy");
        }
      }
    };
    const firstInput = el.modalRoot.querySelector("input, select, textarea, button");
    if (firstInput && typeof firstInput.focus === "function") firstInput.focus();
  }

  el.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    el.loginError.textContent = "";
    const submitBtn = el.loginForm.querySelector('button[type="submit"]');
    if (submitBtn?.classList.contains("is-busy")) return;
    const data = Object.fromEntries(new FormData(event.target).entries());
    await runBusy(submitBtn, async () => {
      try {
        // Login must not use route AbortSignal (no shell yet).
        const payload = await api("/api/auth/login", {
          method: "POST",
          json: data,
          signal: null,
        });
        setAuth(payload);
        await boot();
      } catch (err) {
        if (isAbortError(err)) return;
        el.loginError.textContent = "登录失败：" + err.message;
        // Swallow so runBusy does not double-toast login failures already shown inline.
      }
    });
  });

  el.logoutBtn.addEventListener("click", async () => {
    // Always clear local creds; attempt server revoke. Failure still clears UI.
    await runBusy(el.logoutBtn, async () => {
      let serverOk = false;
      try {
        if (state.token) {
          await api("/api/auth/logout", { method: "POST", signal: null });
          serverOk = true;
        }
      } catch (_err) {
        serverOk = false;
      }
      clearAuth();
      showLogin();
      toast(serverOk ? "已退出登录（服务端已注销）" : "已清除本地登录；服务端注销未确认");
    });
  });

  if (el.navToggle) {
    el.navToggle.addEventListener("click", () => {
      el.shell.classList.toggle("nav-open");
    });
  }

  window.addEventListener("hashchange", async () => {
    // Do not rewrite state.route here — renderRoute compares dirty form vs next path.
    el.shell.classList.remove("nav-open");
    // Drop stale toasts so a previous page's errors do not linger after navigation.
    const toastHost = document.getElementById("toast-host");
    if (toastHost) toastHost.innerHTML = "";
    if (state.token) await renderRoute();
  });

  window.addEventListener("beforeunload", (event) => {
    if (!formDirty) return;
    event.preventDefault();
    event.returnValue = "";
  });

  // Narrow drawer: tap main content closes sidebar overlay.
  el.content?.addEventListener("click", () => {
    if (el.shell.classList.contains("nav-open")) el.shell.classList.remove("nav-open");
  });

  boot();
})();
