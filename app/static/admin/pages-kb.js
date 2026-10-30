/**
 * KB list, strategies, ingestion job mirror.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-kb.js");
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

    async function pageKb(root) {
  pageTitleActions(root, `<button class="btn" id="kb-create">新建知识库</button>
    <a class="btn secondary" href="#/kb/retrieve" style="margin-left:8px">试检索</a>`);
  let rows = [];
  try {
    rows = (await api("/api/admin/knowledge-bases")).items || [];
  } catch (err) {
    return renderLoadError(root, "知识库列表加载失败", err);
  }
  root.innerHTML = `
    <div class="list-page">
    <div class="panel" style="margin-bottom:12px">
      <span class="badge warn">Mock 检索</span>
      <span class="meta"> 默认 Mock Embedding + 内存向量，用于流程验证，不代表真实语义检索。共 ${rows.length} 个库。</span>
    </div>
    <div class="panel">
      ${rows.length ? `
      <table class="data">
        <thead><tr><th>名称</th><th>编码</th><th>状态</th><th>默认检索策略</th><th>操作</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td><a href="#/kb/base/detail?id=${encodeURIComponent(row.id)}">${escapeHtml(row.name)}</a></td>
              <td class="mono">${escapeHtml(row.code)}</td>
              <td>${badge(row.status)}</td>
              <td class="mono">${escapeHtml(row.default_retrieve_strategy_id)}</td>
              <td class="row-actions">
                <a class="btn secondary" href="#/kb/base/detail?id=${encodeURIComponent(row.id)}">详情</a>
                <button class="btn secondary" data-use="${escapeAttr(row.id)}">用于文档页</button>
                <button class="btn danger" data-del="${escapeAttr(row.id)}">删除</button>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>` : `<div class="empty">还没有知识库，点击右上角新建。</div>`}
    </div>
    </div>`;
  document.getElementById("kb-create").onclick = () => openModal({
    title: "新建知识库",
    body: `
      <div class="field"><label>名称</label><input id="m-name" value="帮助中心" /></div>
      <div class="field"><label>编码</label><input id="m-code" value="help" /></div>`,
    onOk: async () => {
      await api("/api/admin/knowledge-bases", {
        method: "POST",
        json: { name: document.getElementById("m-name").value, code: document.getElementById("m-code").value },
      });
      toast("已创建");
      await renderRoute();
    },
  });
  root.querySelectorAll("[data-del]").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("确认删除？库内有文档时会失败。")) return;
      await runBusy(btn, async () => {
        await api(`/api/admin/knowledge-bases/${btn.dataset.del}`, { method: "DELETE" });
        toast("已删除");
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-use]").forEach((btn) => {
    btn.onclick = () => {
      state.selectedKbId = btn.dataset.use;
      location.hash = "/kb/docs";
    };
  });
}

    async function pageChunkStrategies(root) {
  let rows = [];
  try {
    rows = (await api("/api/admin/chunk-strategies")).items || [];
  } catch (err) {
    return renderLoadError(root, "切分策略加载失败", err);
  }
  root.innerHTML = `<div class="list-page"><p class="meta">共 ${rows.length} · PATCH 带 expected_version；冲突 409 请重载</p>
    ${tablePanel(rows, ["名称", "chunk", "child", "overlap", "种子", "操作"], (row) => `
    <tr><td>${escapeHtml(row.name)}</td><td>${row.chunk_size}</td><td>${row.child_size}</td><td>${row.overlap}</td><td>${row.is_seed ? "是" : "否"}</td>
    <td><a href="#/kb/chunk-strategy/form?id=${encodeURIComponent(row.id)}">编辑</a></td></tr>`)}</div>`;
}

    async function pageRetrieveStrategies(root) {
  let rows = [];
  try {
    rows = (await api("/api/admin/retrieve-strategies")).items || [];
  } catch (err) {
    return renderLoadError(root, "检索策略加载失败", err);
  }
  root.innerHTML = `<div class="list-page"><p class="meta">共 ${rows.length} · PATCH 带 expected_version；冲突 409 请重载</p>
    ${tablePanel(rows, ["名称", "向量阈值", "重排阈值", "TopK", "种子", "操作"], (row) => `
    <tr><td>${escapeHtml(row.name)}</td><td>${row.vector_min_score}</td><td>${row.rerank_min_score}</td><td>${row.top_k}</td><td>${row.is_seed ? "是" : "否"}</td>
    <td><a href="#/kb/retrieve-strategy/form?id=${encodeURIComponent(row.id)}">编辑</a></td></tr>`)}</div>`;
}

    async function pageJobs(root) {
  let rows = [];
  try {
    rows = (await api("/api/admin/kb/jobs")).items || [];
  } catch (err) {
    return renderLoadError(root, "入库任务加载失败", err);
  }
  root.innerHTML = `
    <div class="list-page">
    <div class="panel" style="margin-bottom:12px">
      <span class="badge warn">进程内记录</span>
      <span class="meta"> 同步操作结果镜像（platform.jobs），非可恢复后台队列；重启后历史会丢失。共 ${rows.length}</span>
    </div>
    ${rows.length ? tablePanel(rows, ["任务", "类型", "文档", "状态", "进度"], (row) => `
    <tr><td class="mono"><a href="#/kb/jobs/detail?id=${encodeURIComponent(row.id)}">${escapeHtml(row.id)}</a></td><td>${escapeHtml(row.type)}</td><td class="mono">${escapeHtml(row.doc_id)}</td><td>${badge(row.status)}</td><td>${row.progress}%</td></tr>`)
    : `<div class="panel empty">暂无入库操作记录。在文档页执行上传/解析/切分/向量化后会出现。</div>`}
    </div>`;
}

    return {
      "/kb/base": pageKb,
      "/kb/chunk-strategy": pageChunkStrategies,
      "/kb/retrieve-strategy": pageRetrieveStrategies,
      "/kb/jobs": pageJobs
    };
  });
})();
