/**
 * Agent KB binding + publish/enable controls.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-ext-agent.js");
    return;
  }
  KitNS.registerPages(function (S) {
    const {
      api, escapeHtml, escapeAttr, badge, toast, routeQuery, metaBadge,
      tablePanel, openModal, state, renderRoute, pageTitleActions, demoModeBanner,
      watchFormDirty, markFormDirty, runBusy, isAbortError, agentConfigNav,
      agentConfigSubnav, renderLoadError, bindFilterEnter, detailHead, applyTheme,
      runAction, failPanel, Kit, loadingHtml, emptyHtml, isLive,
      pickAgent, agentFilter,
    } = S;

    async function pageAgentConfigKb(root) {
      let agents;
      let agent;
      try {
        ({ agents, agent } = await pickAgent());
      } catch (err) {
        return failPanel(root, "Agent 列表加载失败", err);
      }
      if (!agent) {
        root.innerHTML = `<div class="empty">无 Agent。<a href="#/agent/list">去实例列表</a></div>`;
        return;
      }
      let kbs = [];
      try {
        kbs = (await api("/api/admin/knowledge-bases")).items || [];
      } catch (err) {
        return failPanel(root, "知识库列表加载失败", err);
      }
      const bound = agent.kb_binding?.kb_id || "";
      const isDisabled = agent.status === "disabled";
      root.innerHTML = `
        ${agentConfigNav("/agent/config/kb", agent.agent_id)}
        ${agentFilter(agents, agent, "kb-agent")}
        ${isDisabled ? `<div class="alert alert-warn">当前 Agent 已停用：仍可改绑定，但调试对话不可调用；请先到<a href="#/agent/config/publish?id=${encodeURIComponent(agent.agent_id)}">发布页</a>启用。</div>` : ""}
        <div class="panel">
          <div class="panel-head"><strong>知识库绑定</strong><span class="badge">${agent.kb_binding?.rag_enabled ? "RAG 开" : "RAG 关"}</span>
            ${isDisabled ? `<span class="badge warn">disabled</span>` : ""}</div>
          <dl class="kv">
            <dt>当前绑定</dt><dd class="mono truncate">${escapeHtml(bound || "未绑定")}</dd>
            <dt>Agent</dt><dd class="mono truncate">${escapeHtml(agent.agent_id)}</dd>
          </dl>
          <div class="field"><label>选择知识库</label>
            <select id="kb-bind" class="ctrl">
              <option value="">— 不绑定 —</option>
              ${kbs.map((kb) => `<option value="${escapeAttr(kb.id)}" ${kb.id === bound ? "selected" : ""}>${escapeHtml(kb.name)} (${escapeHtml(kb.code || kb.id)})</option>`).join("")}
            </select></div>
          <div class="field"><label>启用 RAG</label>
            <select id="kb-rag" class="ctrl">
              <option value="true" ${agent.kb_binding?.rag_enabled ? "selected" : ""}>true</option>
              <option value="false" ${!agent.kb_binding?.rag_enabled ? "selected" : ""}>false</option>
            </select></div>
          <div class="actions">
            <button class="btn" id="kb-save">保存绑定</button>
            <a class="btn secondary" href="#/kb/base">知识库列表</a>
            <a class="btn secondary${isDisabled ? " is-disabled" : ""}" href="${isDisabled ? `#/agent/config/publish?id=${encodeURIComponent(agent.agent_id)}` : "#/chat/playground"}">${isDisabled ? "去发布启用" : "调试对话"}</a>
          </div>
          <p class="meta">写入走 PATCH /api/admin/agents/{id}；停用 Agent 不可被对话调用。YAML 话题/提示词仍只读。</p>
        </div>`;
      if (typeof watchFormDirty === "function") watchFormDirty(root);
      root.querySelector("#kb-agent").onchange = () => {
        location.hash = `/agent/config/kb?id=${encodeURIComponent(root.querySelector("#kb-agent").value)}`;
      };
      root.querySelector("#kb-save").onclick = async (ev) => {
        await runAction(ev.currentTarget, async () => {
          const kbId = root.querySelector("#kb-bind").value || null;
          const rag = root.querySelector("#kb-rag").value === "true";
          await api(`/api/admin/agents/${encodeURIComponent(agent.agent_id)}`, {
            method: "PATCH",
            json: { kb_id: kbId, rag_enabled: rag },
          });
          if (typeof markFormDirty === "function") markFormDirty(false);
          toast("绑定已保存");
          await renderRoute();
        });
      };
    }

    async function pageAgentPublish(root) {
      let agents;
      let agent;
      try {
        ({ agents, agent } = await pickAgent());
      } catch (err) {
        return failPanel(root, "Agent 列表加载失败", err);
      }
      if (!agent) { root.innerHTML = `<div class="empty">无 Agent。<a href="#/agent/list">去实例列表</a></div>`; return; }
      root.innerHTML = `
        ${agentConfigNav("/agent/config/publish", agent.agent_id)}
        ${agentFilter(agents, agent, "pub-agent")}
        <div class="panel">
          <dl class="kv">
            <dt>当前状态</dt><dd>${badge(agent.status)}</dd>
            <dt>版本字段</dt><dd>${escapeHtml(String(agent.version ?? 1))}（种子版本号；无独立发布流水线）</dd>
            <dt>默认 Agent</dt><dd>${agent.is_default ? "是" : "否"}</dd>
          </dl>
          <p class="meta">published 可被对话调用；disabled/draft 返回 404。无灰度发布历史表。</p>
          <button class="btn" id="pub-go">${agent.status === "published" ? "停用（disabled）" : "发布（published）"}</button>
        </div>`;
      root.querySelector("#pub-agent").onchange = () => {
        location.hash = `/agent/config/publish?id=${encodeURIComponent(root.querySelector("#pub-agent").value)}`;
      };
      root.querySelector("#pub-go").onclick = async (ev) => {
        const next = agent.status === "published" ? "disabled" : "published";
        if (next === "disabled" && !confirm("停用后对话不可调用该 Agent，确认？")) return;
        await runAction(ev.currentTarget, async () => {
          await api(`/api/admin/agents/${agent.agent_id}`, { method: "PATCH", json: { status: next } });
          toast(next === "published" ? "已发布" : "已停用");
          await renderRoute();
        });
      };
    }

    return {
      "/agent/config/kb": pageAgentConfigKb,
      "/agent/config/publish": pageAgentPublish
    };
  });
})();
