/**
 * KB detail, jobs detail, feedback placeholder.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-ext-kb.js");
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

    async function pageKbBaseDetail(root) {
      const id = routeQuery().get("id") || state.selectedKbId;
      let items = [];
      try {
        items = (await api("/api/admin/knowledge-bases")).items || [];
      } catch (err) {
        return failPanel(root, "知识库详情加载失败", err);
      }
      const kb = items.find((k) => k.id === id) || items[0];
      if (!kb) {
        root.innerHTML = `<div class="empty">暂无知识库。<a href="#/kb/base">去创建</a></div>`;
        return;
      }
      state.selectedKbId = kb.id;
      let docs = [];
      let docsError = null;
      try {
        docs = (await api(`/api/admin/kb/${kb.id}/documents`)).items || [];
      } catch (err) {
        docsError = err;
        toast(String(err.message || err), "error");
      }
      const docsCountLabel = docsError ? "加载失败" : String(docs.length);
      root.innerHTML = `
        <div class="panel">
          <div class="panel-head"><strong>知识库详情</strong>
            <a class="btn ghost" href="#/kb/base">← 库列表</a></div>
          <p class="meta">${metaBadge({ source: "platform" })}</p>
          ${docsError ? `<div class="alert alert-error"><strong>文档列表加载失败</strong><p>${escapeHtml(docsError.message || String(docsError))}</p>
            <p class="meta">未将失败显示为「文档数=0」。</p></div>` : ""}
          <dl class="kv">
            <dt>名称</dt><dd>${escapeHtml(kb.name)}</dd>
            <dt>ID</dt><dd class="mono truncate" title="${escapeAttr(kb.id)}">${escapeHtml(kb.id)}</dd>
            <dt>编码</dt><dd class="mono">${escapeHtml(kb.code || "-")}</dd>
            <dt>状态</dt><dd>${badge(kb.status || "-")}</dd>
            <dt>切分策略</dt><dd class="mono truncate">${escapeHtml(kb.default_chunk_strategy_id || "-")}</dd>
            <dt>检索策略</dt><dd class="mono truncate">${escapeHtml(kb.default_retrieve_strategy_id || "-")}</dd>
            <dt>文档数</dt><dd>${escapeHtml(docsCountLabel)}</dd>
          </dl>
          <div class="actions ticket-actions" style="margin-top:12px">
            <a class="btn" href="#/kb/docs">文档列表${docsError ? "" : `（${docs.length}）`}</a>
            <a class="btn secondary" href="#/kb/retrieve">试检索</a>
            <a class="btn secondary" href="#/kb/qa">知识问答</a>
            <a class="btn ghost" href="#/kb/jobs">入库任务</a>
          </div>
        </div>`;
    }

    async function pageJobsDetail(root) {
      const id = routeQuery().get("id");
      let items = [];
      try {
        items = (await api("/api/admin/kb/jobs")).items || [];
      } catch (err) {
        return failPanel(root, "入库任务加载失败", err);
      }
      let job = id ? items.find((j) => j.id === id) : items[0];
      if (id && !job) {
        try { job = await api(`/api/admin/kb/jobs/${encodeURIComponent(id)}`); } catch { /* ignore */ }
      }
      if (!job) {
        root.innerHTML = `<div class="empty">暂无任务。<a href="#/kb/jobs">任务列表</a>
          <p class="meta">进程内镜像，非可恢复后台队列。</p></div>`;
        return;
      }
      root.innerHTML = `
        <div class="panel">
          <div class="panel-head"><strong>入库任务详情</strong>
            <a class="btn ghost" href="#/kb/jobs">← 任务列表</a></div>
          <div class="alert alert-warn readonly-banner"><span class="badge warn">进程内</span>
            <span>同步操作结果镜像；重启后历史会丢失。非可恢复队列。</span></div>
          <dl class="kv">
            <dt>ID</dt><dd class="mono truncate" title="${escapeAttr(job.id)}">${escapeHtml(job.id)}</dd>
            <dt>类型</dt><dd>${escapeHtml(job.kind || job.type || "-")}</dd>
            <dt>文档</dt><dd class="mono truncate">${escapeHtml(job.doc_id || "-")}</dd>
            <dt>状态</dt><dd>${badge(job.status || "-")}</dd>
            <dt>进度</dt><dd>${escapeHtml(String(job.progress ?? "-"))}%</dd>
            <dt>时间</dt><dd>${escapeHtml(job.created_at || job.updated_at || "-")}</dd>
          </dl>
          <div class="actions ticket-actions">
            ${job.doc_id ? `<a class="btn secondary" href="#/kb/docs/detail?id=${encodeURIComponent(job.doc_id)}">打开文档</a>` : ""}
            <a class="btn ghost" href="#/kb/docs">文档列表</a>
          </div>
        </div>`;
    }

    async function pageKbFeedback(root) {
      let kbs = [];
      let data = { items: [] };
      try {
        kbs = (await api("/api/admin/knowledge-bases")).items || [];
        data = await api("/api/admin/kb/feedback");
      } catch (err) {
        return failPanel(root, "反馈页加载失败", err);
      }
      root.innerHTML = `
        ${demoModeBanner(true, "反馈存于进程内存，重启清空；不伪装为生产质检库。")}
        <div class="panel">
          <div class="field"><label>知识库</label>
            <select id="fb-kb" class="ctrl"><option value="">（可选）</option>
              ${kbs.map((k) => `<option value="${escapeAttr(k.id)}">${escapeHtml(k.name)}</option>`).join("")}
            </select></div>
          <div class="field"><label>查询/问题</label><input id="fb-q" class="ctrl" placeholder="相关问题" /></div>
          <div class="field"><label>评分</label>
            <select id="fb-rating" class="ctrl"><option value="up">有帮助</option><option value="neutral" selected>一般</option><option value="down">无帮助</option></select>
          </div>
          <div class="field"><label>备注</label><textarea id="fb-c" class="ctrl" rows="2"></textarea></div>
          <button class="btn" id="fb-save">提交反馈</button>
          <a class="btn secondary" href="#/kb/qa" style="margin-left:8px">去问答试测</a>
        </div>
        <div class="panel" style="margin-top:12px">
          ${tablePanel(data.items || [], ["时间", "评分", "问题", "备注"], (row) => `
            <tr><td class="mono">${escapeHtml(row.created_at || "-")}</td><td>${badge(row.rating)}</td>
            <td class="truncate" title="${escapeAttr(row.query || "")}">${escapeHtml(row.query || "-")}</td>
            <td class="truncate" title="${escapeAttr(row.comment || "")}">${escapeHtml(row.comment || "-")}</td></tr>`)}
          <p class="meta">${metaBadge(data._meta)} · ${(data.items || []).length} 条</p>
        </div>`;
      if (typeof watchFormDirty === "function") watchFormDirty(root);
      root.querySelector("#fb-save").onclick = async (ev) => {
        const q = (root.querySelector("#fb-q")?.value || "").trim();
        if (!q) return toast("请填写查询/问题", "error");
        await runAction(ev.currentTarget, async () => {
          await api("/api/admin/kb/feedback", {
            method: "POST",
            json: {
              kb_id: root.querySelector("#fb-kb").value || null,
              query: q,
              rating: root.querySelector("#fb-rating").value,
              comment: root.querySelector("#fb-c").value,
            },
          });
          if (typeof markFormDirty === "function") markFormDirty(false);
          toast("已记录（进程内存）");
          await renderRoute();
        });
      };
    }

    return {
      "/kb/base/detail": pageKbBaseDetail,
      "/kb/jobs/detail": pageJobsDetail,
      "/kb/feedback": pageKbFeedback
    };
  });
})();
