/**
 * Risk tickets list/detail + human-queue fallback.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-tickets.js");
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

    async function pageTickets(root) {
  const statusFilter = routeQuery().get("status") || "";
  let rowsAll = [];
  try {
    rowsAll = (await api("/api/admin/tickets/risk")).items || [];
  } catch (err) {
    return renderLoadError(root, "高风险工单加载失败", err);
  }
  const rows = statusFilter ? rowsAll.filter((r) => r.status === statusFilter) : rowsAll;
  root.innerHTML = `
    <div class="list-page">
    <div class="filters">
      <label>状态
        <select id="tk-status" class="ctrl">
          <option value="">全部状态</option>
          ${["pending", "approved", "rejected", "succeeded", "failed", "executing", "revalidated", "reconciliation_required"].map((s) =>
            `<option value="${s}" ${statusFilter === s ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </label>
      <button class="btn secondary" id="tk-filter">筛选</button>
      <button class="btn ghost" id="tk-clear">清空</button>
      <a class="btn secondary" href="#/chat/playground">调试页</a>
      <span class="meta">共 ${rowsAll.length} · 筛选后 ${rows.length} · 批准 ≠ 执行；参数仅脱敏摘要</span>
    </div>
    <div class="panel">
      ${rows.length ? `
      <table class="data">
        <thead><tr><th>单号</th><th>工具</th><th>参数摘要</th><th>状态</th><th>已执行</th><th>操作</th></tr></thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td class="mono truncate" title="${escapeAttr(row.id)}"><a href="#/tickets/risk/detail?id=${encodeURIComponent(row.id)}">${escapeHtml(row.id)}</a></td>
              <td class="truncate">${escapeHtml(row.tool_name)}</td>
              <td class="mono truncate" title="${escapeAttr(JSON.stringify(row.args_masked || {}))}">${escapeHtml(JSON.stringify(row.args_masked || {}))}</td>
              <td>${badge(row.status)}</td>
              <td>${row.executed ? "是" : "否"}</td>
              <td class="row-actions">
                ${row.status === "pending" ? `<button class="btn" data-approve="${escapeAttr(row.id)}">批准</button>
                  <button class="btn ghost" data-reject="${escapeAttr(row.id)}">拒绝</button>` : ""}
                ${row.status === "approved" || row.status === "revalidated" ? `<button class="btn" data-execute="${escapeAttr(row.id)}">执行</button>` : ""}
                ${row.status === "reconciliation_required" ? `<a class="btn secondary" href="#/tickets/risk/detail?id=${encodeURIComponent(row.id)}">对账</a>` : ""}
                ${!["pending", "approved", "revalidated", "reconciliation_required"].includes(row.status)
                  ? `<span class="meta">无列表快捷操作</span>` : ""}
                <a class="btn ghost" href="#/tickets/risk/detail?id=${encodeURIComponent(row.id)}">详情</a>
              </td>
            </tr>`).join("")}
        </tbody>
      </table>` : `<div class="empty">${rowsAll.length ? "无匹配状态，请清空筛选。" : "暂无工单。可在调试页发送「订单申请退款」触发。"}</div>`}
    </div>
    </div>`;
  root.querySelector("#tk-filter").onclick = () => {
    location.hash = `/tickets/risk?status=${encodeURIComponent(root.querySelector("#tk-status").value)}`;
  };
  root.querySelector("#tk-clear").onclick = () => { location.hash = "/tickets/risk"; };
  bindFilterEnter(root, "#tk-filter", ["#tk-status"]);
  root.querySelectorAll("[data-approve]").forEach((btn) => {
    btn.onclick = async () => {
      await runBusy(btn, async () => {
        await api(`/api/admin/tickets/${btn.dataset.approve}/approve`, { method: "POST", json: {} });
        toast("已批准（需再点执行）");
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-reject]").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("确认拒绝该工单？")) return;
      await runBusy(btn, async () => {
        await api(`/api/admin/tickets/${btn.dataset.reject}/reject`, { method: "POST", json: { reason: "ui_reject" } });
        toast("已拒绝");
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-execute]").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("确认执行？批准≠执行，将走服务端 CAS/revalidation。")) return;
      await runBusy(btn, async () => {
        await api(`/api/admin/tickets/${btn.dataset.execute}/execute`, { method: "POST" });
        toast("已执行");
        await renderRoute();
      });
    };
  });
}

    async function pageTicketDetail(root) {
  const id = new URLSearchParams((location.hash.split("?")[1] || "")).get("id");
  if (!id) {
    root.innerHTML = `<div class="empty">缺少工单 id。请从 <a href="#/tickets/risk">审批列表</a> 进入。</div>`;
    return;
  }
  let row;
  try {
    row = await api(`/api/admin/tickets/risk/${id}`);
  } catch (err) {
    return renderLoadError(root, "工单详情加载失败", err);
  }
  root.innerHTML = `
    <div class="panel">
      ${detailHead("/tickets/risk", "工单详情")}
      <dl class="kv">
        <dt>单号</dt><dd class="mono truncate" title="${escapeAttr(row.id)}">${escapeHtml(row.id)}</dd>
        <dt>状态</dt><dd>${badge(row.status)}</dd>
        <dt>工具</dt><dd>${escapeHtml(row.tool_name || "")}</dd>
        <dt>申请人</dt><dd class="mono">${escapeHtml(row.applicant_id || row.user_id || "")}</dd>
        <dt>审批人</dt><dd class="mono">${escapeHtml(row.approver_id || "-")}</dd>
        <dt>参数摘要</dt><dd class="mono truncate-2">${escapeHtml(JSON.stringify(row.args_masked || {}))}</dd>
        <dt>决定</dt><dd>${escapeHtml(row.decision || "-")}</dd>
        <dt>原因</dt><dd>${escapeHtml(row.reason || "-")}</dd>
        <dt>执行结果</dt><dd class="truncate-2">${escapeHtml(row.execution_result || "-")}</dd>
      </dl>
      <div class="actions ticket-actions" style="margin-top:12px">
        ${row.status === "pending" ? `<button class="btn" data-approve="${escapeAttr(row.id)}">批准</button>
          <button class="btn ghost" data-reject="${escapeAttr(row.id)}">拒绝</button>` : ""}
        ${row.status === "approved" || row.status === "revalidated" ? `<button class="btn" data-execute="${escapeAttr(row.id)}">执行</button>` : ""}
        ${row.status === "reconciliation_required" ? `<p class="meta note-line">⚠ 执行结果不确定：禁止自动重跑工具。可先尝试外部结果查询；无适配器时仍须人工对账。LOOKUP ≠ 重新执行。</p>
          <button class="btn ghost" data-lookup-resolve="${escapeAttr(row.id)}">查询外部结果</button>
          <button class="btn" data-reconcile-ok="${escapeAttr(row.id)}">对账：确认成功</button>
          <button class="btn ghost" data-reconcile-fail="${escapeAttr(row.id)}">对账：确认失败</button>` : ""}
        ${!["pending", "approved", "revalidated", "reconciliation_required"].includes(row.status)
          ? `<p class="meta">当前状态无可写操作（终态或处理中）。</p>` : ""}
      </div>
    </div>`;
  root.querySelectorAll("[data-approve]").forEach((btn) => {
    btn.onclick = async () => {
      await runBusy(btn, async () => {
        await api(`/api/admin/tickets/${btn.dataset.approve}/approve`, { method: "POST", json: {} });
        toast("已批准（需再点执行）");
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-reject]").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("确认拒绝该工单？")) return;
      await runBusy(btn, async () => {
        await api(`/api/admin/tickets/${btn.dataset.reject}/reject`, { method: "POST", json: { reason: "ui_reject" } });
        toast("已拒绝");
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-execute]").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("确认执行？批准≠执行，将走服务端 CAS/revalidation。")) return;
      await runBusy(btn, async () => {
        await api(`/api/admin/tickets/${btn.dataset.execute}/execute`, { method: "POST" });
        toast("已执行");
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-lookup-resolve]").forEach((btn) => {
    btn.onclick = async () => {
      await runBusy(btn, async () => {
        const t = await api(`/api/admin/tickets/${btn.dataset.lookupResolve}/lookup-resolve`, {
          method: "POST",
        });
        if (t.status === "reconciliation_required") {
          toast("查询无定论，仍需人工对账（未重跑工具）");
        } else {
          toast(`外部查询已关单：${t.status}（未重跑工具）`);
        }
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-reconcile-ok]").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("确认对账为成功？不会重跑工具。")) return;
      await runBusy(btn, async () => {
        await api(`/api/admin/tickets/${btn.dataset.reconcileOk}/reconcile`, {
          method: "POST",
          json: { outcome: "succeeded", note: "ui_external_verified" },
        });
        toast("已对账为成功（未重跑工具）");
        await renderRoute();
      });
    };
  });
  root.querySelectorAll("[data-reconcile-fail]").forEach((btn) => {
    btn.onclick = async () => {
      if (!confirm("确认对账为失败？不会重跑工具。")) return;
      await runBusy(btn, async () => {
        await api(`/api/admin/tickets/${btn.dataset.reconcileFail}/reconcile`, {
          method: "POST",
          json: { outcome: "failed", note: "ui_no_side_effect_or_confirmed_fail" },
        });
        toast("已对账为失败（未重跑工具）");
        await renderRoute();
      });
    };
  });
}

    return {
      "/tickets/risk": pageTickets,
      "/tickets/risk/detail": pageTicketDetail,
    };
  });
})();
