/**
 * Segment editor + chunk/retrieve strategy forms.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-ext-kb-forms.js");
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

    async function pageSegmentsEdit(root) {
      const segId = routeQuery().get("id");
      let docs = [];
      try {
        docs = (await api("/api/admin/documents")).items || [];
      } catch (err) {
        return failPanel(root, "文档列表加载失败", err);
      }
      if (!state.selectedDocId && docs[0]) state.selectedDocId = docs[0].id;
      const docId = routeQuery().get("doc") || state.selectedDocId;
      if (!docId) {
        root.innerHTML = `<div class="empty">请先选择文档。<a href="#/kb/segments">片段浏览</a></div>`;
        return;
      }
      state.selectedDocId = docId;
      let rows = [];
      try {
        rows = (await api(`/api/admin/documents/${docId}/segments`)).items || [];
      } catch (err) {
        return failPanel(root, "片段加载失败", err);
      }
      const seg = segId ? rows.find((r) => r.id === segId) : rows[0];
      root.innerHTML = `
        <div class="filters">
          <label class="select-wide">文档
            <select id="se-doc" class="ctrl">${docs.map((d) =>
            `<option value="${escapeAttr(d.id)}" ${d.id === docId ? "selected" : ""}>${escapeHtml(d.filename)}</option>`).join("")}</select>
          </label>
          <a class="btn secondary" href="#/kb/segments">返回浏览</a>
          <a class="btn ghost" href="#/kb/docs">文档列表</a>
        </div>
        <div class="panel">
          <div class="alert alert-warn readonly-banner"><span class="badge warn">正文只读</span>
            <span>片段编辑 API 未开放（防误改向量基线）；请重新切分/向量化更新内容。Mock≠真实语义。</span></div>
          ${seg ? `<dl class="kv">
            <dt>片段 ID</dt><dd class="mono truncate">${escapeHtml(seg.id)}</dd>
            <dt>类型</dt><dd>${badge(seg.seg_type)}</dd>
            <dt>向量</dt><dd>${badge(seg.vector_status)}</dd>
            <dt>父块</dt><dd class="mono truncate">${escapeHtml(seg.parent_id || "-")}</dd>
            <dt>内容</dt><dd><pre class="mono code-block">${escapeHtml(seg.content || "")}</pre></dd>
          </dl>
          <div class="actions ticket-actions"><button class="btn secondary" id="se-rechunk">重新切分文档</button>
            <button class="btn" id="se-revec">重新向量化</button></div>` : `<div class="empty">无片段 · <a href="#/kb/docs">去文档页切分</a></div>`}
        </div>`;
      root.querySelector("#se-doc").onchange = () => {
        location.hash = `/kb/segments/edit?doc=${encodeURIComponent(root.querySelector("#se-doc").value)}`;
      };
      const rechunk = root.querySelector("#se-rechunk");
      if (rechunk) rechunk.onclick = async (ev) => {
        await runAction(ev.currentTarget, async () => {
          await api(`/api/admin/documents/${docId}/chunk`, { method: "POST", json: {} });
          toast("已重新切分");
          await renderRoute();
        });
      };
      const revec = root.querySelector("#se-revec");
      if (revec) revec.onclick = async (ev) => {
        await runAction(ev.currentTarget, async () => {
          await api(`/api/admin/documents/${docId}/vectorize`, { method: "POST" });
          toast("已重新向量化（Mock≠真实语义）");
          await renderRoute();
        });
      };
    }

    async function pageStrategyForm(root, kind) {
      const id = routeQuery().get("id");
      const path = kind === "chunk" ? "/api/admin/chunk-strategies" : "/api/admin/retrieve-strategies";
      const patchBase = kind === "chunk" ? "/api/admin/chunk-strategies/" : "/api/admin/retrieve-strategies/";
      const listPath = kind === "chunk" ? "/kb/chunk-strategy" : "/kb/retrieve-strategy";
      const rows = (await api(path)).items || [];
      const row = rows.find((r) => r.id === id) || rows[0];
      if (!row) {
        root.innerHTML = `<div class="empty">无策略。<a href="#${listPath}">返回列表</a></div>`;
        return;
      }
      const fields = kind === "chunk"
        ? `<div class="field"><label>名称</label><input id="f-name" value="${escapeAttr(row.name || "")}" /></div>
           <div class="field"><label>chunk_size</label><input id="f-chunk" type="number" value="${escapeAttr(row.chunk_size)}" /></div>
           <div class="field"><label>child_size</label><input id="f-child" type="number" value="${escapeAttr(row.child_size)}" /></div>
           <div class="field"><label>overlap</label><input id="f-overlap" type="number" value="${escapeAttr(row.overlap)}" /></div>`
        : `<div class="field"><label>名称</label><input id="f-name" value="${escapeAttr(row.name || "")}" /></div>
           <div class="field"><label>vector_min_score</label><input id="f-vmin" type="number" step="0.01" value="${escapeAttr(row.vector_min_score)}" /></div>
           <div class="field"><label>rerank_min_score</label><input id="f-rmin" type="number" step="0.01" value="${escapeAttr(row.rerank_min_score)}" /></div>
           <div class="field"><label>top_k</label><input id="f-topk" type="number" value="${escapeAttr(row.top_k)}" /></div>`;
      root.innerHTML = `
        <div class="panel">
          <p><a href="#${listPath}">← 列表</a> · id <span class="mono">${escapeHtml(row.id)}</span>
            ${row.is_seed ? '<span class="badge warn">seed</span>' : ""}</p>
          ${fields}
          <p class="meta">version=${escapeHtml(String(row.version || 1))} · postgres 为权威源；重启后 hydrate。</p>
          <button class="btn" id="f-save">保存策略</button>
        </div>`;
      if (typeof watchFormDirty === "function") watchFormDirty(root);
      root.querySelector("#f-save").onclick = async (ev) => {
        await runAction(ev.currentTarget, async () => {
          const body = kind === "chunk"
            ? {
                name: root.querySelector("#f-name").value,
                chunk_size: Number(root.querySelector("#f-chunk").value),
                child_size: Number(root.querySelector("#f-child").value),
                overlap: Number(root.querySelector("#f-overlap").value),
                expected_version: row.version || 1,
              }
            : {
                name: root.querySelector("#f-name").value,
                vector_min_score: Number(root.querySelector("#f-vmin").value),
                rerank_min_score: Number(root.querySelector("#f-rmin").value),
                top_k: Number(root.querySelector("#f-topk").value),
                expected_version: row.version || 1,
              };
          try {
            const saved = await api(patchBase + encodeURIComponent(row.id), { method: "PATCH", json: body });
            if (typeof markFormDirty === "function") markFormDirty(false);
            toast(`已保存 version=${saved.version || "?"}`);
            await renderRoute();
          } catch (err) {
            if (err && err.conflict) {
              const box = root.querySelector(".panel");
              if (box && !root.querySelector("#f-conflict")) {
                const note = document.createElement("div");
                note.id = "f-conflict";
                note.className = "alert alert-warn";
                note.innerHTML = `<strong>版本冲突 (409)</strong><p>${escapeHtml(err.message)}</p>
                  <button type="button" class="btn secondary" id="f-reload">重新加载最新版本</button>`;
                box.prepend(note);
                root.querySelector("#f-reload")?.addEventListener("click", () => renderRoute());
              }
            }
            throw err;
          }
        });
      };
    }

    async function pageChunkStrategyForm(root) { return pageStrategyForm(root, "chunk"); }

    async function pageRetrieveStrategyForm(root) { return pageStrategyForm(root, "retrieve"); }

    return {
      "/kb/segments/edit": pageSegmentsEdit,
      "/kb/chunk-strategy/form": pageChunkStrategyForm,
      "/kb/retrieve-strategy/form": pageRetrieveStrategyForm
    };
  });
})();
