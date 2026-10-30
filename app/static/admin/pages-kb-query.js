/**
 * KB retrieve-test and QA playground pages.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-kb-query.js");
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

    async function pageRetrieve(root) {
  let kbs = [];
  let strategies = [];
  try {
    kbs = (await api("/api/admin/knowledge-bases")).items || [];
    strategies = (await api("/api/admin/retrieve-strategies")).items || [];
  } catch (err) {
    return renderLoadError(root, "试检索页加载失败", err);
  }
  if (!kbs.length) {
    root.innerHTML = `<div class="empty">请先创建知识库。<a class="btn" href="#/kb/base">知识库列表</a></div>`;
    return;
  }
  root.innerHTML = `
    <div class="panel query-panel">
      <div class="filters">
        <label>知识库<select id="r-kb" class="ctrl">${kbs.map((kb) => `<option value="${escapeAttr(kb.id)}">${escapeHtml(kb.name)}</option>`).join("")}</select></label>
        <label>策略<select id="r-st" class="ctrl">${strategies.map((s) => `<option value="${escapeAttr(s.id)}">${escapeHtml(s.name)}</option>`).join("")}</select></label>
        <label class="select-wide">查询词<input id="r-q" class="ctrl" placeholder="如：退货政策" /></label>
        <button class="btn" id="r-go">试检索</button>
      </div>
      <div id="r-out" class="empty">输入查询后查看命中片段与分数 · Enter 发送</div>
    </div>`;
  const run = async () => {
    const btn = root.querySelector("#r-go");
    const out = root.querySelector("#r-out");
    const q = (root.querySelector("#r-q")?.value || "").trim();
    if (!q) return toast("请输入查询词", "error");
    await runBusy(btn, async () => {
      out.innerHTML = typeof Kit.loadingHtml === "function" ? Kit.loadingHtml("检索中…") : `<div class="empty">检索中…</div>`;
      try {
        const data = await api("/api/admin/kb/retrieve-test", {
          method: "POST",
          json: {
            kb_id: root.querySelector("#r-kb").value,
            strategy_id: root.querySelector("#r-st").value,
            query: q,
          },
        });
        if (!out.isConnected) return;
        const hits = data.hits || [];
        const mode = data.provider_mode || "unknown";
        const modeNote = mode.includes("mock")
          ? "当前为 Mock 检索流程，不具备真实语义检索能力"
          : "当前为配置中的检索后端（以 API 返回为准）";
        out.innerHTML = `
        <div class="muted" style="margin-bottom:10px">检索模式：<code>${escapeHtml(mode)}</code> · 命中 ${hits.length} 条 · ${escapeHtml(modeNote)}</div>
        ${hits.length ? `
        <table class="data"><thead><tr><th>分数</th><th>来源</th><th>片段</th></tr></thead>
        <tbody>${hits.map((h) => `<tr><td>${h.score}</td><td class="truncate">${escapeHtml(h.source || h.segment_id || "")}</td><td class="truncate-2">${escapeHtml(h.text || "")}</td></tr>`).join("")}</tbody></table>`
          : `<div class="empty">无命中（可能阈值过高或尚未向量化）</div>`}`;
      } catch (err) {
        if (out.isConnected && !isAbortError(err)) {
          out.innerHTML = `<div class="alert alert-error"><strong>检索失败</strong><p>${escapeHtml(err.message)}</p></div>`;
        }
        throw err;
      }
    });
  };
  root.querySelector("#r-go").onclick = run;
  bindFilterEnter(root, "#r-go", ["#r-q"]);
  // Changing scope clears prior hits so users do not mistake stale results for a new query.
  root.querySelector("#r-kb")?.addEventListener("change", () => {
    const out = root.querySelector("#r-out");
    if (out) out.innerHTML = `<div class="empty">知识库已切换，请重新检索</div>`;
  });
  root.querySelector("#r-st")?.addEventListener("change", () => {
    const out = root.querySelector("#r-out");
    if (out) out.innerHTML = `<div class="empty">策略已切换，请重新检索</div>`;
  });
}

    async function pageQa(root) {
  let kbs = [];
  try {
    kbs = (await api("/api/admin/knowledge-bases")).items || [];
  } catch (err) {
    return renderLoadError(root, "试问答页加载失败", err);
  }
  if (!kbs.length) {
    root.innerHTML = `<div class="empty">请先创建知识库。<a class="btn" href="#/kb/base">知识库列表</a></div>`;
    return;
  }
  root.innerHTML = `
    <div class="panel query-panel">
      <div class="filters">
        <label>知识库<select id="q-kb" class="ctrl">${kbs.map((kb) => `<option value="${escapeAttr(kb.id)}">${escapeHtml(kb.name)}</option>`).join("")}</select></label>
        <label class="select-wide">问题<input id="q-q" class="ctrl" placeholder="问题" /></label>
        <button class="btn" id="q-go">试问答</button>
      </div>
      <p class="meta">模板合成回答 · llm_called 以响应为准 · 无 sources 视为不合格</p>
      <div id="q-out" class="empty">回答必须带来源 sources[] · Enter 发送</div>
    </div>`;
  const run = async () => {
    const btn = root.querySelector("#q-go");
    const out = root.querySelector("#q-out");
    const q = (root.querySelector("#q-q")?.value || "").trim();
    if (!q) return toast("请输入问题", "error");
    await runBusy(btn, async () => {
      out.innerHTML = typeof Kit.loadingHtml === "function" ? Kit.loadingHtml("问答中…") : `<div class="empty">问答中…</div>`;
      try {
        const data = await api("/api/admin/kb/qa", {
          method: "POST",
          json: { kb_id: root.querySelector("#q-kb").value, query: q },
        });
        if (!out.isConnected) return;
        const mode = data.provider_mode || "unknown";
        const modeNote = mode.includes("mock")
          ? "Mock 检索模板合成，llm_called=false"
          : "以 API 返回的检索后端为准；仍为模板合成非真实大模型";
        const sources = data.sources || [];
        out.innerHTML = `
        <div class="muted" style="margin-bottom:8px">模式：<code>${escapeHtml(mode)}</code> · llm_called=${data.llm_called ? "true" : "false"} · ${escapeHtml(modeNote)}</div>
        <div class="bubble bot">${escapeHtml(data.reply)}</div>
        <div class="meta">sources (${sources.length}): ${sources.map(escapeHtml).join(", ") || "无"}</div>`;
      } catch (err) {
        if (out.isConnected && !isAbortError(err)) {
          out.innerHTML = `<div class="alert alert-error"><strong>问答失败</strong><p>${escapeHtml(err.message)}</p></div>`;
        }
        throw err;
      }
    });
  };
  root.querySelector("#q-go").onclick = run;
  bindFilterEnter(root, "#q-go", ["#q-q"]);
  root.querySelector("#q-kb")?.addEventListener("change", () => {
    const out = root.querySelector("#q-out");
    if (out) out.innerHTML = `<div class="empty">知识库已切换，请重新问答</div>`;
  });
}

    return {
      "/kb/retrieve": pageRetrieve,
      "/kb/qa": pageQa
    };
  });
})();
