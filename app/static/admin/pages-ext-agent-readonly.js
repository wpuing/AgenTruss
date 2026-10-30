/**
 * Agent wizard/templates and readonly YAML/prompt/guardrail views.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-ext-agent-readonly.js");
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

    async function safePickAgent(root, title) {
      try {
        return await pickAgent();
      } catch (err) {
        failPanel(root, title || "Agent 列表加载失败", err);
        return null;
      }
    }

    async function loadIndustryConfig(root, agent) {
      try {
        return await api(`/api/admin/agents/${encodeURIComponent(agent.agent_id)}/industry-config`);
      } catch (err) {
        failPanel(root, "行业配置加载失败", err);
        return null;
      }
    }

    function emptyAgent() {
      return `<div class="empty">无 Agent。<a href="#/agent/list">去实例列表</a></div>`;
    }

    async function pageAgentWizard(root) {
      const picked = await safePickAgent(root);
      if (!picked) return;
      const { agents, agent } = picked;
      root.innerHTML = `
        ${agentConfigNav("/agent/wizard", agent?.agent_id)}
        <div class="panel">
          <ol class="step-list">
            <li>在「行业模板库」确认 topics / 工具白名单</li>
            <li>绑定知识库并向量化文档（Mock 向量即可验收流水线）</li>
            <li>在调试页发送库内问题与「订单申请退款」验证护栏/审批</li>
            <li>需要启停时在实例详情或「发布」页切换 status</li>
          </ol>
          <p class="meta">脚手架不提供「一键创建 Agent」假成功；种子 Agent 随租户固定 id。</p>
          <div class="actions">
            <a class="btn" href="#/agent/templates">行业模板</a>
            <a class="btn secondary" href="#/agent/list">实例列表（${agents.length}）</a>
            <a class="btn secondary" href="#/chat/playground">调试对话</a>
          </div>
        </div>`;
    }

    async function pageAgentTemplates(root) {
      const picked = await safePickAgent(root, "Agent 加载失败");
      if (!picked) return;
      const { agent } = picked;
      let data;
      try {
        data = await api("/api/admin/industries");
      } catch (err) {
        return failPanel(root, "行业模板加载失败", err);
      }
      root.innerHTML = `
        ${agentConfigNav("/agent/templates", agent?.agent_id)}
        <div class="panel">
          <p class="meta readonly-banner"><span class="badge">只读</span> 来自 config/*.yaml · 不可在线保存</p>
          ${(data.items || []).map((ind) => `
            <div class="panel nested-panel">
              <div class="panel-head"><strong>${escapeHtml(ind.industry)}</strong>
                <span class="badge ok">config</span></div>
              <dl class="kv">
                <dt>话题</dt><dd>${escapeHtml((ind.topics || []).join("、"))}</dd>
                <dt>工具</dt><dd class="mono truncate">${escapeHtml((ind.tools || []).join(", "))}</dd>
                <dt>置信度阈值</dt><dd>${escapeHtml(String(ind.confidence_threshold))}</dd>
                <dt>越界话术</dt><dd>${escapeHtml(ind.off_topic_message || "-")}</dd>
                <dt>模板</dt><dd class="mono truncate">${escapeHtml(ind.template_path || "-")}</dd>
              </dl>
            </div>`).join("") || `<div class="empty">无行业配置</div>`}
          <p class="meta">${metaBadge(data._meta)} · 换行业改 YAML/templates，不改底座代码</p>
        </div>`;
    }

    async function pageAgentConfigBasic(root) {
      const picked = await safePickAgent(root);
      if (!picked) return;
      const { agents, agent } = picked;
      if (!agent) { root.innerHTML = emptyAgent(); return; }
      const cfg = await loadIndustryConfig(root, agent);
      if (!cfg) return;
      root.innerHTML = `
        ${agentConfigNav("/agent/config/basic", agent.agent_id)}
        ${agentFilter(agents, agent, "ac-agent")}
        <div class="panel">
          <dl class="kv">
            <dt>名称</dt><dd>${escapeHtml(agent.name)}</dd>
            <dt>ID</dt><dd class="mono truncate">${escapeHtml(agent.agent_id)}</dd>
            <dt>行业</dt><dd>${escapeHtml(agent.industry)}</dd>
            <dt>状态</dt><dd>${badge(agent.status)}</dd>
            <dt>执行模式</dt><dd><span class="badge warn">以详情页 / 对话响应为准</span> Stub ≠ Live</dd>
            <dt>知识库</dt><dd class="mono truncate">${escapeHtml(agent.kb_binding?.kb_id || "未绑定")} · RAG=${agent.kb_binding?.rag_enabled}</dd>
            <dt>工具</dt><dd class="mono truncate">${escapeHtml((agent.enabled_tools || []).join(", "))}</dd>
          </dl>
          <p class="meta">${escapeHtml(cfg.note || "")}</p>
          <div class="actions">
            <button class="btn" id="ac-toggle">${agent.status === "disabled" ? "启用" : "停用"}</button>
            <a class="btn secondary" href="#/agent/detail?id=${encodeURIComponent(agent.agent_id)}">完整详情</a>
            ${agent.chat_callable === true
              ? `<a class="btn secondary" href="#/chat/playground">调试对话</a>`
              : `<span class="meta">不可对话（需 published / chat_callable）</span>`}
          </div>
        </div>`;
      root.querySelector("#ac-agent").onchange = () => {
        location.hash = `/agent/config/basic?id=${encodeURIComponent(root.querySelector("#ac-agent").value)}`;
      };
      root.querySelector("#ac-toggle").onclick = async (ev) => {
        const next = agent.status === "disabled" ? "published" : "disabled";
        if (next === "disabled" && !confirm("停用后对话不可调用该 Agent，确认？")) return;
        await runAction(ev.currentTarget, async () => {
          await api(`/api/admin/agents/${agent.agent_id}`, { method: "PATCH", json: { status: next } });
          toast(next === "published" ? "已启用" : "已停用");
          await renderRoute();
        });
      };
    }

    async function pageAgentTopics(root) {
      const picked = await safePickAgent(root);
      if (!picked) return;
      const { agents, agent } = picked;
      if (!agent) { root.innerHTML = emptyAgent(); return; }
      const cfg = await loadIndustryConfig(root, agent);
      if (!cfg) return;
      root.innerHTML = `
        ${agentConfigNav("/agent/config/topics", agent.agent_id)}
        ${agentFilter(agents, agent, "at-agent")}
        <div class="panel">
          <p class="meta readonly-banner"><span class="badge">只读</span> config/${escapeHtml(agent.industry)}.yaml</p>
          <dl class="kv">
            <dt>话题白名单</dt><dd>${escapeHtml((cfg.topics || []).join("、") || "-")}</dd>
            <dt>置信度阈值</dt><dd>${escapeHtml(String(cfg.confidence_threshold))}</dd>
            <dt>越界回复</dt><dd>${escapeHtml(cfg.off_topic_message || "-")}</dd>
            <dt>消息上限</dt><dd>${escapeHtml(String(cfg.max_message_length))}</dd>
          </dl>
          <p class="meta">越界/低分会进入转人工建议队列；不可在线改 YAML。</p>
          <a class="btn secondary" href="#/tickets/human">查看转人工队列</a>
        </div>`;
      root.querySelector("#at-agent").onchange = () => {
        location.hash = `/agent/config/topics?id=${encodeURIComponent(root.querySelector("#at-agent").value)}`;
      };
    }

    async function pageAgentPrompts(root) {
      const picked = await safePickAgent(root);
      if (!picked) return;
      const { agents, agent } = picked;
      if (!agent) { root.innerHTML = emptyAgent(); return; }
      const cfg = await loadIndustryConfig(root, agent);
      if (!cfg) return;
      root.innerHTML = `
        ${agentConfigNav("/agent/config/prompts", agent.agent_id)}
        ${agentFilter(agents, agent, "ap-agent")}
        <div class="panel">
          <p class="meta readonly-banner"><span class="badge">只读</span> templates/${escapeHtml(agent.industry)}/system.txt + ${escapeHtml(cfg.security_rules_path || "")} · 长度 ${cfg.prompt_length || 0}</p>
          <pre class="mono code-block">${escapeHtml(cfg.prompt_full || cfg.prompt_preview || "")}</pre>
          <p class="meta">禁止把用户输入拼进 system；控制台只读，无保存按钮。</p>
        </div>`;
      root.querySelector("#ap-agent").onchange = () => {
        location.hash = `/agent/config/prompts?id=${encodeURIComponent(root.querySelector("#ap-agent").value)}`;
      };
    }

    async function pageAgentGuardrails(root) {
      const picked = await safePickAgent(root);
      if (!picked) return;
      const { agents, agent } = picked;
      if (!agent) { root.innerHTML = emptyAgent(); return; }
      const cfg = await loadIndustryConfig(root, agent);
      if (!cfg) return;
      root.innerHTML = `
        ${agentConfigNav("/agent/config/guardrails", agent.agent_id)}
        ${agentFilter(agents, agent, "ag-agent")}
        <div class="panel">
          <p class="meta readonly-banner"><span class="badge">只读</span> 行业阈值 · 护栏在底座运行</p>
          <dl class="kv">
            <dt>置信度阈值</dt><dd>${escapeHtml(String(cfg.confidence_threshold))}</dd>
            <dt>风险承诺拦截</dt><dd>${escapeHtml((cfg.risky_promises || []).join(" / ") || "-")}</dd>
            <dt>话题边界</dt><dd>${escapeHtml((cfg.topics || []).join("、"))}</dd>
            <dt>工具白名单</dt><dd class="mono truncate">${escapeHtml((cfg.enabled_tools || []).join(", "))}</dd>
          </dl>
          <p class="meta">不可在线改 YAML；用调试页验证拦截效果。</p>
          <a class="btn secondary" href="#/chat/playground">去调试验证护栏</a>
        </div>`;
      root.querySelector("#ag-agent").onchange = () => {
        location.hash = `/agent/config/guardrails?id=${encodeURIComponent(root.querySelector("#ag-agent").value)}`;
      };
    }

    return {
      "/agent/wizard": pageAgentWizard,
      "/agent/templates": pageAgentTemplates,
      "/agent/config/basic": pageAgentConfigBasic,
      "/agent/config/topics": pageAgentTopics,
      "/agent/config/prompts": pageAgentPrompts,
      "/agent/config/guardrails": pageAgentGuardrails
    };
  });
})();
