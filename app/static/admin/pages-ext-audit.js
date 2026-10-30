/**
 * Human tickets + audit detail / trace / metrics.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-ext-audit.js");
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





    async function pageHumanTicketsFull(root) {
      let data;
      try {
        data = await api("/api/admin/tickets/human");
      } catch (err) {
        root.innerHTML = `<div class="panel alert alert-error"><strong>转人工队列加载失败</strong><p>${escapeHtml(err.message)}</p>
          <button class="btn secondary" id="page-retry">重试</button>
          <a class="btn secondary" href="#/tickets/risk">高风险审批</a></div>`;
        root.querySelector("#page-retry")?.addEventListener("click", () => renderRoute());
        return;
      }
      const items = data.items || [];
      const suggestions = data.suggestions || [];
      const id = routeQuery().get("id");
      if (id) {
        const row = items.find((t) => t.id === id);
        const sess = row?.session_id
          ? `<a href="#/audit/trace?session_id=${encodeURIComponent(row.session_id)}">${escapeHtml(row.session_id)}</a>`
          : "-";
        root.innerHTML = `
          <div class="panel">
            <div class="panel-head"><strong>人工单详情</strong>
              <a class="btn ghost" href="#/tickets/human">← 列表</a></div>
            ${row ? `<dl class="kv">
              <dt>单号</dt><dd class="mono truncate" title="${escapeAttr(row.id)}">${escapeHtml(row.id)}</dd>
              <dt>状态</dt><dd>${badge(row.status)}</dd>
              <dt>原因</dt><dd>${escapeHtml(row.reason || "-")}</dd>
              <dt>备注</dt><dd class="mono truncate-2">${escapeHtml(JSON.stringify(row.args_masked || {}))}</dd>
              <dt>会话</dt><dd class="mono">${sess}</dd>
            </dl>
            ${row.status === "pending" ? `<button class="btn" id="hum-resolve">标记已处理</button>` : `<p class="meta">当前状态无可写操作。</p>`}` : `<div class="empty">未找到工单（可能是建议项）。</div>`}
            <p class="meta">高风险退款等仍走 <a href="#/tickets/risk">高风险审批</a> · 人工单 ≠ 工具执行</p>
          </div>`;
        const btn = root.querySelector("#hum-resolve");
        if (btn) btn.onclick = async (ev) => {
          await runAction(ev.currentTarget, async () => {
            await api(`/api/admin/tickets/human/${encodeURIComponent(id)}/resolve`, { method: "POST", json: { note: "handled_in_console" } });
            toast("已处理");
            location.hash = "/tickets/human";
          });
        };
        return;
      }
      root.innerHTML = `
        <div class="panel">
          <div class="actions" style="margin-bottom:12px">
            <button class="btn" id="hum-new">新建人工单</button>
            <a class="btn secondary" href="#/tickets/risk">高风险审批</a>
            <a class="btn secondary" href="#/chat/playground">调试页</a>
          </div>
          <p class="meta">${escapeHtml(data.note || "")} ${metaBadge(data._meta)}</p>
          <h3 style="margin:12px 0 8px;font-size:14px">人工工单</h3>
          ${items.length ? `<table class="data"><thead><tr><th>单号</th><th>状态</th><th>原因</th><th></th></tr></thead><tbody>
            ${items.map((r) => `<tr>
              <td class="mono"><a href="#/tickets/human/detail?id=${encodeURIComponent(r.id)}">${escapeHtml(r.id)}</a></td>
              <td>${badge(r.status)}</td><td>${escapeHtml(r.reason || "-")}</td>
              <td>${r.status === "pending" ? "待处理" : "-"}</td></tr>`).join("")}
          </tbody></table>` : `<div class="empty compact">暂无人工工单，可新建或从建议转来。</div>`}
          <h3 style="margin:16px 0 8px;font-size:14px">对话软拒建议（low_score / off_topic）</h3>
          ${suggestions.length ? `<table class="data"><thead><tr><th>建议</th><th>原因</th><th>输入</th><th>审计</th></tr></thead><tbody>
            ${suggestions.map((s) => `<tr>
              <td class="mono">${escapeHtml(s.id)}</td><td>${badge(s.reason)}</td>
              <td>${escapeHtml(s.input_preview || "-")}</td>
              <td class="mono"><a href="#/audit/logs/detail?id=${encodeURIComponent(s.audit_id || "")}">${escapeHtml(s.audit_id || "-")}</a></td>
            </tr>`).join("")}
          </tbody></table>` : `<div class="empty compact">暂无建议。可在调试页发送越界或无依据问题。</div>`}
        </div>`;
      root.querySelector("#hum-new").onclick = async (ev) => {
        const note = window.prompt("处理说明 / 用户诉求摘要", "需人工跟进");
        if (note == null) return;
        await runAction(ev.currentTarget, async () => {
          const row = await api("/api/admin/tickets/human", { method: "POST", json: { note, reason: "manual" } });
          toast("已创建");
          location.hash = `/tickets/human/detail?id=${encodeURIComponent(row.id)}`;
        });
      };
    }
    async function pageAuditLogDetail(root) {
      const id = routeQuery().get("id");
      if (!id) {
        root.innerHTML = `<div class="empty">缺少 audit id。<a href="#/audit/logs">返回</a></div>`;
        return;
      }
      let row;
      try {
        row = await api(`/api/admin/audit-logs/${encodeURIComponent(id)}`);
      } catch (err) {
        return failPanel(root, "审计详情加载失败", err);
      }
      root.innerHTML = `
        <div class="panel">
          <p><a href="#/audit/logs">← 审计列表</a></p>
          <dl class="kv">
            <dt>审计号</dt><dd class="mono">${escapeHtml(row.audit_id || id)}</dd>
            <dt>会话</dt><dd class="mono">${row.session_id ? `<a href="#/audit/trace?session_id=${encodeURIComponent(row.session_id)}">${escapeHtml(row.session_id)}</a>` : "-"}</dd>
            <dt>话题</dt><dd>${escapeHtml(row.topic || "-")}</dd>
            <dt>拦截</dt><dd>${row.blocked ? badge("blocked") : "否"} · ${escapeHtml(row.block_reason || "-")}</dd>
            <dt>llm_called</dt><dd>${escapeHtml(String(!!row.llm_called))}</dd>
            <dt>工单</dt><dd class="mono">${row.ticket_id ? `<a href="#/tickets/risk/detail?id=${encodeURIComponent(row.ticket_id)}">${escapeHtml(row.ticket_id)}</a>` : "-"}</dd>
            <dt>输入</dt><dd>${escapeHtml(row.input_message || "-")}</dd>
            <dt>输出</dt><dd>${escapeHtml(row.output_message || "-")}</dd>
            <dt>耗时</dt><dd>${escapeHtml(String(row.latency_ms ?? "-"))} ms</dd>
          </dl>
        </div>`;
    }
    async function pageAuditTrace(root) {
      let sessions = [];
      try {
        sessions = (await api("/api/admin/sessions")).items || [];
      } catch (err) {
        root.innerHTML = `<div class="panel alert alert-error"><strong>会话列表加载失败</strong><p>${escapeHtml(err.message)}</p>
          <button class="btn secondary" id="page-retry">重试</button></div>`;
        root.querySelector("#page-retry")?.addEventListener("click", () => renderRoute());
        return;
      }
      const sid = routeQuery().get("session_id") || (sessions[0] && (sessions[0].session_id || sessions[0].id));
      root.innerHTML = `
        <div class="filters">
          <label class="select-wide">会话
            <select id="tr-ses" class="ctrl">${sessions.map((s) => {
              const id = s.session_id || s.id;
              return `<option value="${escapeAttr(id)}" ${id === sid ? "selected" : ""}>${escapeHtml(id)} (${s.turn_count || 0})</option>`;
            }).join("")}</select>
          </label>
          <button class="btn secondary" id="tr-load">加载追踪</button>
          <a class="btn ghost" href="#/chat/sessions">会话列表</a>
          <span class="meta">Trace 只读聚合，不会重跑对话或工具</span>
        </div>
        <div class="panel" id="tr-body"><div class="empty">${sessions.length ? "选择会话并加载" : "暂无会话"}</div></div>`;
      const load = async () => {
        const id = root.querySelector("#tr-ses")?.value;
        const body = root.querySelector("#tr-body");
        const btn = root.querySelector("#tr-load");
        if (!id || !body) return;
        // Changing session via hash remounts the page; avoid double-fetch + Abort.
        const nextHash = `#/audit/trace?session_id=${encodeURIComponent(id)}`;
        if (location.hash !== nextHash) {
          location.hash = nextHash;
          return;
        }
        await runAction(btn, async () => {
          body.innerHTML = (window.AgenTrussKit && window.AgenTrussKit.loadingHtml)
            ? window.AgenTrussKit.loadingHtml("加载追踪…")
            : `<div class="page-loading"><span class="spinner"></span>加载追踪…</div>`;
          const data = await api(`/api/admin/trace/${encodeURIComponent(id)}`);
          if (!body.isConnected) return;
          body.innerHTML = `
          <p class="meta">source=${escapeHtml(data.source || "-")} · turns=${(data.turns || []).length} · audits=${(data.audits || []).length}</p>
          <h3 style="font-size:14px">轮次</h3>
          ${(data.turns || []).map((t, i) => `<div class="turn-card"><div class="meta">#${i + 1}</div>
            <div class="bubble user compact-bubble">${escapeHtml(t.user || "")}</div>
            <div class="bubble bot compact-bubble">${escapeHtml(t.assistant || "")}</div></div>`).join("") || "<div class='empty compact'>无轮次</div>"}
          <h3 style="font-size:14px;margin-top:12px">审计</h3>
          ${(data.audits || []).length ? `<table class="data"><thead><tr><th>审计</th><th>话题</th><th>原因</th></tr></thead><tbody>
            ${(data.audits || []).map((a) => `<tr>
              <td class="mono truncate"><a href="#/audit/logs/detail?id=${encodeURIComponent(a.audit_id)}">${escapeHtml(a.audit_id)}</a></td>
              <td>${escapeHtml(a.topic || "-")}</td><td class="truncate">${escapeHtml(a.block_reason || "-")}</td></tr>`).join("")}
          </tbody></table>` : `<div class="empty compact">无关联审计</div>`}`;
        });
      };
      root.querySelector("#tr-load").onclick = load;
      if (sid) await load();
    }
    async function pageAuditMetrics(root) {
      let overview;
      let status;
      try {
        [overview, status] = await Promise.all([
          api("/api/admin/overview"),
          api("/api/admin/system/status"),
        ]);
      } catch (err) {
        root.innerHTML = `<div class="panel alert alert-error"><strong>指标页加载失败</strong><p>${escapeHtml(err.message)}</p>
          <button class="btn secondary" id="page-retry">重试</button></div>`;
        root.querySelector("#page-retry")?.addEventListener("click", () => renderRoute());
        return;
      }
      const cards = overview.cards || overview.components || overview.items || [];
      const metrics = overview.metrics || overview.summary || overview;
      root.innerHTML = `
        <div class="panel">
          <p class="meta">非 Prometheus；聚合自 overview / status。未接入指标不显示虚构成功率。</p>
          <dl class="kv">
            <dt>环境</dt><dd>${escapeHtml(status.app_env || "-")}</dd>
            <dt>Ready</dt><dd>${badge(status.ready?.status || "-")}</dd>
            <dt>存储</dt><dd class="mono">${escapeHtml(status.storage_backend || "-")}</dd>
            <dt>Embedding</dt><dd class="mono">${escapeHtml(status.embedding_provider || "-")}</dd>
            <dt>向量</dt><dd class="mono">${escapeHtml(status.vector_store_backend || "-")}</dd>
          </dl>
        </div>
        <div class="dash-grid" style="margin-top:12px">
          ${Array.isArray(cards) && cards.length ? cards.map((c) => `
            <div class="panel"><div class="panel-head"><strong>${escapeHtml(c.title || c.label || c.id || "指标")}</strong></div>
              <div style="font-size:22px">${escapeHtml(String(c.value ?? c.count ?? "-"))}</div>
              <p class="meta">${escapeHtml(c.hint || c.note || "")}</p></div>`).join("") : `
            <div class="panel"><pre class="mono" style="white-space:pre-wrap;max-height:280px;overflow:auto">${escapeHtml(JSON.stringify(metrics, null, 2).slice(0, 4000))}</pre></div>`}
        </div>
        <p class="meta">${metaBadge(overview._meta)}</p>`;
    }

    return {
      "/tickets/human": pageHumanTicketsFull,
      "/tickets/human/detail": pageHumanTicketsFull,
      "/audit/logs/detail": pageAuditLogDetail,
      "/audit/trace": pageAuditTrace,
      "/audit/metrics": pageAuditMetrics
    };
  });
})();
