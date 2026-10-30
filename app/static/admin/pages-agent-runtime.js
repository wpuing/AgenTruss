/**
 * Tools catalog + workflow rail (readonly / demo).
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-agent-runtime.js");
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

    const TOOLS_WF_HINT = "工具目录与工作流阶段轨为只读查看；不可直接执行生产工具，无拖拽设计器。";

    async function pageTools(root) {
  let tools = [];
  let agents = [];
  try {
    const [toolsRes, agentsRes] = await Promise.all([
      api("/api/admin/tools"),
      api("/api/admin/agents"),
    ]);
    tools = toolsRes.items || [];
    agents = agentsRes.items || [];
  } catch (err) {
    return renderLoadError(root, "工具目录加载失败", err);
  }
  root.innerHTML = `
    ${agentConfigSubnav("/agent/config/tools", null, TOOLS_WF_HINT)}
    <div class="list-page">
    <div class="filters">
      <label class="select-wide">搜索
        <input id="tool-q" class="ctrl" placeholder="工具名 / 描述" />
      </label>
      <label>风险
        <select id="tool-risk" class="ctrl">
          <option value="">全部风险</option>
          <option value="high">仅高风险</option>
          <option value="normal">仅普通</option>
        </select>
      </label>
      <button class="btn secondary" id="tool-filter">筛选</button>
      <button class="btn ghost" id="tool-clear">清空</button>
      <span class="badge warn">目录只读 · 不可直接执行</span>
      <span class="meta">共 ${tools.length} · local_demo≠production</span>
    </div>
    <div class="panel" id="tool-table"></div>
    </div>`;
  const render = () => {
    const q = (root.querySelector("#tool-q").value || "").toLowerCase();
    const risk = root.querySelector("#tool-risk").value;
    const rows = tools.filter((t) => {
      if (risk === "high" && !t.high_risk) return false;
      if (risk === "normal" && t.high_risk) return false;
      if (!q) return true;
      return `${t.name} ${t.description}`.toLowerCase().includes(q);
    });
    root.querySelector("#tool-table").innerHTML = rows.length ? `
      <table class="data">
        <thead><tr><th>工具</th><th>描述</th><th>能力层</th><th>结果查询</th><th>风险</th><th>范围</th><th>角色</th><th>关联 Agent</th></tr></thead>
        <tbody>
          ${rows.map((t) => {
            const linked = agents.filter((a) => (a.enabled_tools || []).includes(t.name)).map((a) => a.name);
            const tier = t.capability_tier || "local_demo";
            const lookup = t.supports_result_lookup ? "lookup" : "no-lookup";
            return `<tr>
              <td class="mono">${escapeHtml(t.name)}</td>
              <td>${escapeHtml(t.description)}</td>
              <td class="mono">${escapeHtml(tier)}</td>
              <td class="mono">${escapeHtml(lookup)}</td>
              <td>${t.high_risk ? badge("high_risk") : badge("ok")}</td>
              <td>${escapeHtml(t.scope || "-")}</td>
              <td class="mono">${escapeHtml((t.required_roles || []).join(", "))}</td>
              <td>${escapeHtml(linked.join(", ") || "-")}</td>
            </tr>`;
          }).join("")}
        </tbody>
      </table>
      <p class="meta">capability_tier 仅作诚实标注；恢复/LOOKUP_FIRST 以 supports_result_lookup 为准。local_demo ≠ production。Echo/JSONPlaceholder≠生产业务。</p>` : `<div class="empty">${tools.length ? "无匹配工具，请清空筛选。" : "工具注册表为空。"}</div>`;
  };
  root.querySelector("#tool-filter").onclick = render;
  root.querySelector("#tool-clear").onclick = () => {
    root.querySelector("#tool-q").value = "";
    root.querySelector("#tool-risk").value = "";
    render();
  };
  bindFilterEnter(root, "#tool-filter", ["#tool-q", "#tool-risk"]);
  render();
}

    async function pageWorkflow(root) {
  let data;
  try {
    data = await api("/api/admin/workflows");
  } catch (err) {
    return renderLoadError(root, "工作流加载失败", err);
  }
  const items = data.items || [];
  const real = items.find((w) => w.workflow_id === "langgraph_chat");
  const demos = items.filter((w) => w.is_demo);
  const stages = real?.stages || [];
  let selectedDemo = demos[0] || null;
  const renderDemo = () => {
    const box = root.querySelector("#wf-demo");
    if (!box || !selectedDemo) {
      if (box) box.innerHTML = `<div class="empty compact">无演示工作流。开启 ADMIN_DEMO_MODE 后可保存演示草稿（≠生产图）。本页无拖拽编排。</div>`;
      return;
    }
    const nodes = selectedDemo.nodes || [];
    const edges = selectedDemo.edges || [];
    box.innerHTML = `
      ${demoModeBanner(true, "演示草稿不会写入真实 LangGraph 图。")}
      <div class="panel-head"><strong>${escapeHtml(selectedDemo.name || selectedDemo.workflow_id)}</strong>
        <span class="badge warn">demo</span></div>
      <div class="workflow-rail">
        ${nodes.map((n, i) => `
          <button type="button" class="wf-node" data-node="${escapeAttr(n.id)}">
            <div class="wf-index">${i + 1}</div>
            <div><strong>${escapeHtml(n.title || n.id)}</strong>
              <div class="mono">${escapeHtml(n.type || "")}</div></div>
          </button>
          ${i < nodes.length - 1 ? `<div class="wf-arrow">→</div>` : ""}`).join("")}
      </div>
      <p class="meta">边：${escapeHtml(edges.map((e) => `${e.from}→${e.to}`).join(", ") || "无")}</p>
      <div id="wf-node-panel" class="meta">点击节点查看配置（演示）</div>
      <div class="actions" style="margin-top:10px">
        <button class="btn secondary" id="wf-add-node" ${data.admin_demo_mode ? "" : "disabled"}>增加演示节点</button>
        <button class="btn" id="wf-save" ${data.admin_demo_mode ? "" : "disabled"}>保存演示草稿</button>
        <button class="btn ghost" id="wf-reset" ${data.admin_demo_mode ? "" : "disabled"}>重置演示</button>
      </div>
      ${data.admin_demo_mode ? "" : `<p class="meta">保存/重置需 ADMIN_DEMO_MODE=true；当前仅可浏览。</p>`}`;
    box.querySelectorAll("[data-node]").forEach((btn) => {
      btn.onclick = () => {
        const node = nodes.find((n) => n.id === btn.dataset.node);
        box.querySelector("#wf-node-panel").textContent =
          `节点 ${node?.id} · 类型 ${node?.type} · 标题 ${node?.title}（演示配置面板）`;
      };
    });
    const addBtn = box.querySelector("#wf-add-node");
    if (addBtn) addBtn.onclick = () => {
      const id = `n_${Date.now().toString(36)}`;
      selectedDemo.nodes = [...nodes, { id, type: "custom", title: "演示节点", x: 40, y: 40 }];
      if (nodes.length) {
        selectedDemo.edges = [...edges, { from: nodes[nodes.length - 1].id, to: id }];
      }
      renderDemo();
    };
    const saveBtn = box.querySelector("#wf-save");
    if (saveBtn) saveBtn.onclick = async (ev) => {
      await runBusy(ev.currentTarget, async () => {
        const saved = await api(`/api/admin/workflows/${selectedDemo.workflow_id}/demo-draft`, {
          method: "PUT",
          json: { name: selectedDemo.name, nodes: selectedDemo.nodes, edges: selectedDemo.edges },
        });
        selectedDemo = { ...selectedDemo, ...saved, is_demo: true };
        toast("演示草稿已保存（进程内，≠真实 LangGraph）");
        renderDemo();
      });
    };
    const resetBtn = box.querySelector("#wf-reset");
    if (resetBtn) resetBtn.onclick = async (ev) => {
      if (!confirm("重置演示草稿？不会影响权威 LangGraph 阶段轨。")) return;
      await runBusy(ev.currentTarget, async () => {
        const row = await api(`/api/admin/workflows/${selectedDemo.workflow_id}/demo-reset`, { method: "POST" });
        selectedDemo = { ...row, is_demo: true };
        toast("已重置演示");
        renderDemo();
      });
    };
  };
  root.innerHTML = `
    ${agentConfigSubnav("/agent/config/graph", null, TOOLS_WF_HINT)}
    <div class="list-page">
    <div class="panel" style="margin-bottom:12px">
      <p class="meta" style="margin:0">权威阶段轨 <span class="mono">langgraph_chat</span> 来自后端节点；下方可视化为演示草稿（≠拖拽设计器）。
        visualizer=${escapeHtml(data.visualizer_mode || "-")} · admin_demo_mode=${data.admin_demo_mode ? "on" : "off"}
        ${metaBadge(data._meta)}</p>
      <p class="meta">工作流列表：${items.map((w) => `${escapeHtml(w.workflow_id)}${w.is_demo ? "(demo)" : ""}`).join(" · ") || "空"}</p>
    </div>
    <div class="panel" style="margin-bottom:12px">
      <div class="panel-head"><strong>权威阶段轨</strong> <span class="badge ok">real</span></div>
      <div class="workflow-rail">
      ${stages.length ? stages.map((s, i) => `
        <div class="wf-node">
          <div class="wf-index">${i + 1}</div>
          <div>
            <strong>${escapeHtml(s.title)}</strong>
            <div class="mono">${escapeHtml(s.id)}</div>
            <div class="meta">${escapeHtml(s.desc || "")}</div>
          </div>
        </div>
        ${i < stages.length - 1 ? `<div class="wf-arrow">→</div>` : ""}`).join("") : `<div class="empty compact">未返回阶段轨</div>`}
      </div>
    </div>
    <div class="panel" style="margin-top:14px" id="wf-demo"></div>
    <div class="panel" style="margin-top:14px">
      <div class="panel-head"><strong>本租户 Agent</strong></div>
      <table class="data">
        <thead><tr><th>名称</th><th>Agent ID</th><th>状态</th><th>操作</th></tr></thead>
        <tbody>
          ${(data.agents || []).map((a) => `
            <tr>
              <td><a href="#/agent/detail?id=${encodeURIComponent(a.agent_id)}">${escapeHtml(a.name)}</a></td>
              <td class="mono">${escapeHtml(a.agent_id)}</td>
              <td>${badge(a.status)}</td>
              <td class="row-actions">
                <a class="btn secondary" href="#/agent/detail?id=${encodeURIComponent(a.agent_id)}">详情</a>
                <button class="btn secondary" data-debug="${escapeAttr(a.agent_id)}" ${a.chat_callable === true ? "" : "disabled"} title="${a.chat_callable === true ? "打开调试对话" : "仅 chat_callable 可调试"}">调试</button>
              </td>
            </tr>`).join("") || `<tr><td colspan="4"><div class="empty compact">暂无 Agent</div></td></tr>`}
        </tbody>
      </table>
    </div>
    </div>`;
  renderDemo();
  root.querySelectorAll("[data-debug]").forEach((btn) => {
    btn.onclick = () => {
      if (btn.disabled) return toast("仅 published（chat_callable）Agent 可调试", "error");
      state.chatAgentId = btn.dataset.debug;
      location.hash = "/chat/playground";
    };
  });
}

    return {
      "/agent/config/tools": pageTools,
      "/agent/config/graph": pageWorkflow
    };
  });
})();
