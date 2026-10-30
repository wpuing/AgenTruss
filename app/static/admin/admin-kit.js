/**
 * Shared Admin SPA helpers (IIFE, no build step).
 * Loaded before console-pages.js / app.js; exposed as window.AgenTrussKit.
 */
(function (global) {
  "use strict";

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replaceAll("'", "&#39;");
  }

  const BADGE_KIND = {
    uploaded: "warn", parsed: "warn", chunked: "warn", vectorized: "ok",
    pending: "warn", approved: "ok", rejected: "danger", published: "ok",
    succeeded: "ok", failed: "danger", active: "ok",
    blocked: "danger", ok: "ok", high_risk: "danger", executing: "warn",
    reconciliation_required: "warn", revalidated: "warn", disabled: "warn",
  };

  function badge(status) {
    const key = status == null ? "" : String(status);
    return `<span class="badge ${BADGE_KIND[key] || ""}">${escapeHtml(key || "-")}</span>`;
  }

  function loadingHtml(label) {
    return `<div class="empty page-loading" aria-busy="true"><span class="spinner" aria-hidden="true"></span><span>${escapeHtml(label || "加载中…")}</span></div>`;
  }

  function emptyHtml(message, extraHtml) {
    return `<div class="empty">${escapeHtml(message || "暂无数据")}${extraHtml || ""}</div>`;
  }

  function alertHtml(kind, title, body) {
    const k = kind === "warn" || kind === "error" ? kind : "error";
    return `<div class="panel alert alert-${k}"><strong>${escapeHtml(title || "提示")}</strong>
      <p>${escapeHtml(body || "")}</p></div>`;
  }

  function isAbortError(err) {
    if (!err) return false;
    if (err.aborted) return true;
    if (err.name === "AbortError") return true;
    const msg = String(err.message || "");
    return msg === "aborted" || /aborted|AbortError/i.test(msg);
  }

  /**
   * Disable button while work runs; toast errors unless aborted.
   * @param {HTMLElement|null} btn
   * @param {() => Promise<void>} work
   * @param {{ toast?: Function, keepDisabled?: boolean }} [opts]
   */
  async function runBusy(btn, work, opts) {
    const options = opts || {};
    const toastFn = options.toast;
    if (btn) {
      btn.disabled = true;
      btn.classList.add("is-busy");
    }
    try {
      await work();
    } catch (e) {
      if (isAbortError(e)) return;
      if (typeof toastFn === "function") toastFn(String(e.message || e), "error");
      else throw e;
    } finally {
      if (btn && !options.keepDisabled) {
        btn.disabled = false;
        btn.classList.remove("is-busy");
      } else if (btn) {
        btn.classList.remove("is-busy");
      }
    }
  }

  /** Agent config family sub-nav; optional agentId query on editable steps. */
  function agentConfigNav(active, agentId, hint) {
    const q = agentId ? `?id=${encodeURIComponent(agentId)}` : "";
    const links = [
      ["/agent/wizard", "向导"],
      ["/agent/templates", "模板"],
      ["/agent/config/basic", "基础"],
      ["/agent/config/kb", "知识库"],
      ["/agent/config/topics", "话题"],
      ["/agent/config/prompts", "提示词"],
      ["/agent/config/tools", "工具"],
      ["/agent/config/guardrails", "护栏"],
      ["/agent/config/graph", "工作流"],
      ["/agent/config/publish", "发布"],
      ["/agent/config/playground", "调试"],
    ];
    const withId = (href) => {
      if (href === "/agent/config/tools" || href === "/agent/config/graph" || href === "/agent/config/playground") {
        return href;
      }
      if (href.startsWith("/agent/config/") || href === "/agent/wizard" || href === "/agent/templates") {
        return `${href}${q}`;
      }
      return href;
    };
    const defaultHint = "行业 YAML / system prompt 只读；可写项仅 KB 绑定与启停。Stub ≠ Live。";
    return `<nav class="subnav agent-subnav" aria-label="Agent 配置步骤">${links.map(([href, label]) => {
      const on = active === href;
      return `<a class="subnav-link${on ? " active" : ""}" href="#${withId(href)}">${escapeHtml(label)}</a>`;
    }).join("")}</nav>
    <p class="meta subnav-hint">${escapeHtml(hint || defaultHint)}</p>`;
  }

  /** True if node is still in the live document (stale page guard). */
  function isLive(node) {
    return !!(node && node.isConnected);
  }

  /**
   * Enter in listed inputs triggers the apply button (list filters).
   * @param {ParentNode} root
   * @param {string} applyBtnSelector
   * @param {string[]} [inputSelectors]
   */
  function bindFilterEnter(root, applyBtnSelector, inputSelectors) {
    if (!root) return;
    const apply = root.querySelector(applyBtnSelector);
    if (!apply) return;
    const sels = inputSelectors && inputSelectors.length
      ? inputSelectors
      : ["input.ctrl", "select.ctrl"];
    sels.forEach((sel) => {
      root.querySelectorAll(sel).forEach((node) => {
        if (node.dataset.filterEnterBound === "1") return;
        node.dataset.filterEnterBound = "1";
        node.addEventListener("keydown", (e) => {
          if (e.key !== "Enter" || e.shiftKey) return;
          if (node.tagName === "TEXTAREA") return;
          e.preventDefault();
          apply.click();
        });
      });
    });
  }

  /**
   * Breadcrumb HTML for a hash path using menu title map.
   * @param {string} path
   * @param {Record<string, string>} titleMap
   */
  function pageCrumbs(path, titleMap) {
    const map = titleMap || {};
    const parts = String(path || "").split("/").filter(Boolean);
    if (!parts.length) return "";
    const crumbs = [];
    let acc = "";
    parts.forEach((seg, idx) => {
      acc += `/${seg}`;
      const label = map[acc] || (idx === parts.length - 1 ? (map[path] || seg) : seg);
      if (idx === parts.length - 1) {
        crumbs.push(`<span class="crumb current">${escapeHtml(label)}</span>`);
      } else if (map[acc]) {
        crumbs.push(`<a class="crumb" href="#${acc}">${escapeHtml(label)}</a>`);
      } else {
        crumbs.push(`<span class="crumb muted">${escapeHtml(label)}</span>`);
      }
    });
    return `<nav class="page-crumbs" aria-label="面包屑">${crumbs.join('<span class="crumb-sep">/</span>')}</nav>`;
  }

  /** Shared detail panel header with back link. */
  function detailHead(backHref, backLabel, trailingHtml) {
    return `<div class="panel-head">
      <strong>${escapeHtml(backLabel || "详情")}</strong>
      <span class="detail-head-actions">
        <a class="btn ghost" href="#${backHref || "/dashboard"}">← 返回</a>
        ${trailingHtml || ""}
      </span>
    </div>`;
  }

  /**
   * Render a load-failure panel with retry.
   * @param {HTMLElement} root
   * @param {string} title
   * @param {Error|string|null} err
   * @param {() => void} [onRetry]
   */
  function mountFailPanel(root, title, err, onRetry) {
    if (!root) return;
    if (isAbortError(err)) return;
    const msg = err && err.message ? err.message : String(err || "加载失败");
    root.innerHTML = `<div class="panel alert alert-error"><strong>${escapeHtml(title || "加载失败")}</strong>
      <p>${escapeHtml(msg)}</p>
      <button type="button" class="btn secondary" id="page-retry">重试</button></div>`;
    const btn = root.querySelector("#page-retry");
    if (btn && typeof onRetry === "function") {
      btn.addEventListener("click", () => onRetry());
    }
  }

  /** Shared Agent picker helpers for config subpages. */
  function buildAgentFilterHtml(escapeHtmlFn, escapeAttrFn, agents, agent, selectId) {
    const esc = escapeHtmlFn || escapeHtml;
    const esa = escapeAttrFn || escapeAttr;
    const selectedId = agent && agent.agent_id;
    return `<div class="filters"><label class="select-wide">Agent
      <select id="${esa(selectId)}" class="ctrl">${(agents || []).map((a) =>
        `<option value="${esa(a.agent_id)}" ${a.agent_id === selectedId ? "selected" : ""}>${esc(a.name)} · ${esc(a.status)}</option>`).join("")}
      </select></label></div>`;
  }

  /**
   * Normalize shell helpers for page modules (one destructure + shared fail/busy/nav).
   * @param {Record<string, any>} H helpers from app.js buildRouteHandlers
   */
  function createPageScope(H) {
    const helpers = H || {};
    const {
      api, escapeHtml: escHtml, escapeAttr: escAttr, badge: badgeFn, toast,
      routeQuery, metaBadge, tablePanel, openModal, state, renderRoute,
      pageTitleActions, demoModeBanner, watchFormDirty, markFormDirty,
      runBusy: runBusyFn, isAbortError: isAbortFn, detailHead: detailHeadFn,
      bindFilterEnter: bindEnterFn, renderLoadError, applyTheme,
      agentConfigNav: agentConfigNavFromShell,
    } = helpers;

    const escapeHtmlLocal = typeof escHtml === "function" ? escHtml : escapeHtml;
    const escapeAttrLocal = typeof escAttr === "function" ? escAttr : escapeAttr;
    const badgeLocal = typeof badgeFn === "function" ? badgeFn : badge;
    const isAbortLocal = typeof isAbortFn === "function" ? isAbortFn : isAbortError;

    function nav(active, agentId, hint) {
      if (typeof agentConfigNavFromShell === "function") {
        return agentConfigNavFromShell(active, agentId, hint);
      }
      return agentConfigNav(active, agentId, hint);
    }

    async function runAction(btn, work) {
      if (typeof runBusyFn === "function") return runBusyFn(btn, work);
      return runBusy(btn, work, { toast });
    }

    function failPanel(root, title, err) {
      return mountFailPanel(root, title, err, renderRoute);
    }

    async function pickAgent() {
      // Config pages need the full list (incl. draft/disabled); chat uses for_chat separately.
      const agents = (await api("/api/admin/agents")).items || [];
      const preferred = agents.find((a) => a.chat_callable === true) || agents[0] || null;
      const id = (routeQuery && routeQuery().get("id"))
        || (state && state.chatAgentId)
        || (preferred && preferred.agent_id);
      const agent = agents.find((a) => a.agent_id === id) || preferred || null;
      return { agents, agent };
    }

    function agentFilter(agents, agent, selectId) {
      return buildAgentFilterHtml(escapeHtmlLocal, escapeAttrLocal, agents, agent, selectId);
    }

    return {
      Kit: global.AgenTrussKit,
      api,
      escapeHtml: escapeHtmlLocal,
      escapeAttr: escapeAttrLocal,
      badge: badgeLocal,
      toast,
      routeQuery,
      metaBadge,
      tablePanel,
      openModal,
      state,
      renderRoute,
      pageTitleActions,
      demoModeBanner,
      watchFormDirty,
      markFormDirty,
      runBusy: runBusyFn || runBusy,
      isAbortError: isAbortLocal,
      detailHead: detailHeadFn || detailHead,
      bindFilterEnter: bindEnterFn || bindFilterEnter,
      renderLoadError: renderLoadError || failPanel,
      applyTheme,
      agentConfigNav: nav,
      agentConfigSubnav: nav,
      runAction,
      failPanel,
      pickAgent,
      agentFilter,
      loadingHtml,
      emptyHtml,
      isLive,
    };
  }

  /**
   * Register a page module: fn receives createPageScope(H).
   * @param {(scope: ReturnType<typeof createPageScope>) => Record<string, Function>} factory
   */
  function registerPages(factory) {
    global.AgenTrussPageModules = global.AgenTrussPageModules || [];
    global.AgenTrussPageModules.push(function register(H) {
      return factory(createPageScope(H)) || {};
    });
  }

  global.AgenTrussKit = {
    escapeHtml,
    escapeAttr,
    badge,
    loadingHtml,
    emptyHtml,
    alertHtml,
    isAbortError,
    runBusy,
    agentConfigNav,
    isLive,
    bindFilterEnter,
    pageCrumbs,
    detailHead,
    mountFailPanel,
    buildAgentFilterHtml,
    createPageScope,
    registerPages,
  };
})(typeof window !== "undefined" ? window : globalThis);
