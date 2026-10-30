/**
 * Document list/detail/pipeline + segments browser.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-kb-docs.js");
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

    async function pageDocs(root) {
  let kbs = [];
  let docs = [];
  try {
    kbs = (await api("/api/admin/knowledge-bases")).items || [];
    if (!state.selectedKbId && kbs[0]) state.selectedKbId = kbs[0].id;
    docs = state.selectedKbId
      ? ((await api(`/api/admin/documents?kb_id=${encodeURIComponent(state.selectedKbId)}`)).items || [])
      : [];
  } catch (err) {
    return renderLoadError(root, "文档列表加载失败", err);
  }
  pageTitleActions(root, `<button class="btn" id="doc-upload">上传文档</button>`);
  root.innerHTML = `
    <div class="list-page">
    <div class="filters">
      <label class="select-wide">知识库
        <select id="doc-kb" class="ctrl">
          ${kbs.map((kb) => `<option value="${escapeAttr(kb.id)}" ${kb.id === state.selectedKbId ? "selected" : ""}>${escapeHtml(kb.name)}</option>`).join("")}
        </select>
      </label>
      <a class="btn secondary" href="#/kb/base">库列表</a>
      <span class="meta">共 ${docs.length} · 上传只存盘，不会自动向量化 · Mock≠真实 Embedding</span>
    </div>
    <div class="panel">
      ${docs.length ? `
      <table class="data">
        <thead><tr><th>文件</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          ${docs.map((doc) => `
            <tr>
              <td><span class="truncate" title="${escapeAttr(doc.filename)}">${escapeHtml(doc.filename)}</span>
                <div class="mono truncate" title="${escapeAttr(doc.id)}">${escapeHtml(doc.id)}</div></td>
              <td>${badge(doc.status)}</td>
              <td class="row-actions">
                <button class="btn secondary" data-act="view" data-id="${escapeAttr(doc.id)}">正文</button>
                <a class="btn secondary" href="#/kb/docs/detail?id=${encodeURIComponent(doc.id)}">详情</a>
                <button class="btn secondary" data-act="parse" data-id="${escapeAttr(doc.id)}">解析</button>
                <button class="btn secondary" data-act="chunk" data-id="${escapeAttr(doc.id)}">切分</button>
                <button class="btn secondary" data-act="vectorize" data-id="${escapeAttr(doc.id)}">向量化</button>
                <button class="btn secondary" data-act="segments" data-id="${escapeAttr(doc.id)}">看片段</button>
                <button class="btn danger" data-act="delete" data-id="${escapeAttr(doc.id)}">删除</button>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>` : `<div class="empty">${kbs.length ? "当前库无文档。请上传 Markdown/文本。" : "请先创建知识库。"}</div>`}
    </div>
    </div>`;
  root.querySelector("#doc-kb")?.addEventListener("change", (e) => {
    state.selectedKbId = e.target.value;
    renderRoute();
  });
  document.getElementById("doc-upload").onclick = () => {
    if (!state.selectedKbId) return toast("请先创建知识库");
    openModal({
      title: "上传文档（仅存盘）",
      body: `<div class="field"><label>选择文件</label><input id="m-file" type="file" accept=".md,.txt,.markdown" /></div>`,
      onOk: async () => {
        const file = document.getElementById("m-file").files[0];
        if (!file) throw new Error("请选择文件");
        const form = new FormData();
        form.append("file", file);
        const response = await fetch(`/api/admin/kb/${state.selectedKbId}/documents`, {
          method: "POST",
          headers: { Authorization: `Bearer ${state.token}` },
          body: form,
        });
        if (!response.ok) throw new Error((await response.json()).message || "上传失败");
        toast("已上传");
        await renderRoute();
      },
    });
  };
  root.querySelectorAll("[data-act]").forEach((btn) => {
    btn.onclick = async () => {
      const id = btn.dataset.id;
      const act = btn.dataset.act;
      if (act === "segments") {
        state.selectedDocId = id;
        location.hash = "/kb/segments";
        return;
      }
      if (act === "view") {
        await runBusy(btn, async () => {
          const detail = await api(`/api/admin/documents/${id}`);
          const body = detail.text || "";
          openModal({
            title: `正文 · ${detail.filename || id}`,
            body: `<pre class="mono code-block">${escapeHtml(body || "（无正文）")}</pre>
              <p class="meta">长度 ${body.length} · storage=${escapeHtml(detail.storage || "?")} · durable=${detail.body_durable ? "yes" : "no"}</p>`,
            hideCancel: true,
            okText: "关闭",
            onOk: async () => {},
          });
        });
        return;
      }
      if (act === "delete") {
        if (!window.confirm(`确认删除文档 ${id}？将清除元数据、片段与关联向量（跨存储最佳努力）。`)) return;
        await runBusy(btn, async () => {
          await api(`/api/admin/documents/${id}`, { method: "DELETE" });
          toast("已删除");
          await renderRoute();
        });
        return;
      }
      await runBusy(btn, async () => {
        if (act === "parse") await api(`/api/admin/documents/${id}/parse`, { method: "POST" });
        else if (act === "chunk") await api(`/api/admin/documents/${id}/chunk`, { method: "POST", json: {} });
        else if (act === "vectorize") await api(`/api/admin/documents/${id}/vectorize`, { method: "POST" });
        else throw new Error(`未知操作: ${act}`);
        toast(act === "vectorize" ? "向量化完成（Mock≠真实语义）" : "完成");
        await renderRoute();
      });
    };
  });
}

    async function pageDocDetail(root) {
  const id = routeQuery().get("id");
  if (!id) {
    root.innerHTML = `<div class="empty">缺少文档 id。<a href="#/kb/docs">返回文档列表</a></div>`;
    return;
  }
  root.innerHTML = typeof Kit.loadingHtml === "function" ? Kit.loadingHtml("加载文档…") : `<div class="empty">加载文档…</div>`;
  try {
    const detail = await api(`/api/admin/documents/${encodeURIComponent(id)}`);
    if (root.isCurrent && !root.isCurrent()) return;
    const body = detail.text || "";
    root.innerHTML = `
      <div class="panel">
        ${detailHead("/kb/docs", "文档详情", badge(detail.status || "-"))}
        <h3 class="truncate" title="${escapeAttr(detail.filename || id)}">${escapeHtml(detail.filename || id)}</h3>
        <p class="meta">id=<span class="mono">${escapeHtml(id)}</span> · storage=${escapeHtml(detail.storage || "?")} · durable=${detail.body_durable ? "yes" : "no"} · 长度 ${body.length}</p>
        <pre class="mono code-block">${escapeHtml(body || "（无正文；无 MinIO 时依赖 body_text）")}</pre>
        <div class="row-actions" style="margin-top:12px">
          <button class="btn secondary" id="dd-segments">看片段</button>
          <a class="btn secondary" href="#/kb/retrieve">试检索</a>
          <button class="btn danger" id="dd-delete">删除</button>
        </div>
      </div>`;
    root.querySelector("#dd-segments").onclick = () => {
      state.selectedDocId = id;
      location.hash = "/kb/segments";
    };
    root.querySelector("#dd-delete").onclick = async (ev) => {
      if (!window.confirm(`确认删除 ${id}？`)) return;
      await runBusy(ev.currentTarget, async () => {
        await api(`/api/admin/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
        toast("已删除");
        location.hash = "/kb/docs";
      });
    };
  } catch (err) {
    if (isAbortError(err)) return;
    return renderLoadError(root, "文档详情加载失败", err);
  }
}

    async function pageSegments(root) {
  let docs = [];
  try {
    docs = (await api("/api/admin/documents")).items || [];
  } catch (err) {
    return renderLoadError(root, "片段页文档列表加载失败", err);
  }
  if (!state.selectedDocId && docs[0]) state.selectedDocId = docs[0].id;
  root.innerHTML = `
    <div class="list-page">
    <div class="filters">
      <label class="select-wide">文档
        <select id="seg-doc" class="ctrl">
          ${docs.map((doc) => `<option value="${escapeAttr(doc.id)}" ${doc.id === state.selectedDocId ? "selected" : ""}>${escapeHtml(doc.filename)} (${escapeHtml(doc.status)})</option>`).join("")}
        </select>
      </label>
      <a class="btn secondary" href="#/kb/docs">文档列表</a>
      <span class="meta">共 ${docs.length} 文档 · 向量状态以片段字段为准（Mock 亦可）</span>
    </div>
    <div class="panel" id="seg-table"><div class="empty">选择文档</div></div>
    </div>`;
  const load = async () => {
    const box = root.querySelector("#seg-table");
    state.selectedDocId = root.querySelector("#seg-doc").value;
    if (!state.selectedDocId || !box) return;
    const docId = state.selectedDocId;
    box.innerHTML = typeof Kit.loadingHtml === "function" ? Kit.loadingHtml("加载片段…") : `<div class="empty">加载片段…</div>`;
    try {
      const rows = (await api(`/api/admin/documents/${docId}/segments`)).items || [];
      if (!box.isConnected || state.selectedDocId !== docId) return;
      box.innerHTML = rows.length ? `
        <table class="data">
          <thead><tr><th>类型</th><th>内容</th><th>向量状态</th><th>父块</th></tr></thead>
          <tbody>
            ${rows.map((row) => `
              <tr>
                <td>${badge(row.seg_type)}</td>
                <td class="truncate-2" title="${escapeAttr(row.content || "")}">${escapeHtml(row.content)}</td>
                <td>${badge(row.vector_status)}</td>
                <td class="mono truncate">${escapeHtml(row.parent_id || "-")}</td>
              </tr>`).join("")}
          </tbody>
        </table>
        <p class="meta">共 ${rows.length} 片段 · Mock 向量状态仅表示流水线走到了哪一步</p>` : `<div class="empty">尚无片段，请先在文档页执行切分。<a href="#/kb/docs">去文档页</a></div>`;
    } catch (err) {
      if (isAbortError(err) || !box.isConnected) return;
      box.innerHTML = `<div class="alert alert-error"><strong>加载失败</strong><p>${escapeHtml(err.message)}</p>
        <button class="btn secondary" id="seg-retry">重试</button></div>`;
      box.querySelector("#seg-retry").onclick = load;
    }
  };
  root.querySelector("#seg-doc").onchange = load;
  await load();
}

    return {
      "/kb/docs": pageDocs,
      "/kb/docs/upload": pageDocs,
      "/kb/docs/detail": pageDocDetail,
      "/kb/segments": pageSegments
    };
  });
})();
