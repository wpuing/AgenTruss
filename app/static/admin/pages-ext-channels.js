/**
 * Channel config (web/wecom/mp) + phase2 alias page.
 */
(function () {
  "use strict";
  const KitNS = window.AgenTrussKit;
  if (!KitNS || typeof KitNS.registerPages !== "function") {
    console.error("AgenTrussKit.registerPages missing for pages-ext-channels.js");
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





    function formatProbe(res) {
      return `测连: ${res.status} · ok=${res.ok} · ${res.detail || ""}`;
    }
    async function pageChannel(root, channelId) {
      let row = null;
      try {
        row = await api(`/api/admin/channels/${encodeURIComponent(channelId)}`);
      } catch (detailErr) {
        try {
          const data = await api("/api/admin/channels");
          row = (data.items || []).find((c) => c.id === channelId) || null;
          if (row) toast("渠道详情接口失败，已回退列表摘要", "error");
        } catch (listErr) {
          return failPanel(root, "渠道配置加载失败", listErr || detailErr);
        }
      }
      if (!row) {
        root.innerHTML = `<div class="empty">未找到渠道 <code>${escapeHtml(channelId)}</code>。<a class="btn" href="#/system/basic">系统状态</a></div>`;
        return;
      }
      const writable = channelId === "web" ? [] : (row.writable || []);
      const secrets = channelId === "web" ? [] : (row.writable_secrets || []);
      const fields = row.fields || {};
      const formBits = [];
      for (const key of writable) {
        const val = fields[key] == null ? "" : String(fields[key]);
        if (key === "enabled") {
          formBits.push(`<div class="field"><label>enabled</label>
            <select id="ch-${escapeAttr(key)}"><option value="false" ${val==="true"?"":"selected"}>false</option>
            <option value="true" ${val==="true"?"selected":""}>true</option></select></div>`);
        } else {
          formBits.push(`<div class="field"><label>${escapeHtml(key)}</label>
            <input id="ch-${escapeAttr(key)}" value="${escapeAttr(val)}" /></div>`);
        }
      }
      for (const key of secrets) {
        const conf = fields[key]?.configured;
        formBits.push(`<div class="field"><label>${escapeHtml(key)} ${conf ? "(已配置)" : ""}</label>
          <input id="ch-${escapeAttr(key)}" type="password" placeholder="${conf ? "留空保留" : "输入密钥"}" autocomplete="off" /></div>`);
      }
      root.innerHTML = `
        <div class="panel">
          <dl class="kv">
            <dt>渠道</dt><dd>${escapeHtml(row.name || channelId)}</dd>
            <dt>启用</dt><dd>${row.enabled ? badge("ok") : badge("warn")}</dd>
            <dt>模式</dt><dd class="mono">${escapeHtml(row.mode || "-")}</dd>
            <dt>连通</dt><dd>${row.connected === true
              ? '<span class="badge ok">已连通</span>'
              : '<span class="badge warn">未接通</span> <span class="meta">配置保存 ≠ 消息收发</span>'}</dd>
            <dt>入口</dt><dd class="mono truncate">${escapeHtml(row.endpoint || "未配置")}</dd>
          </dl>
          ${formBits.length ? formBits.join("") : "<p class=\"meta\">Web 渠道内置，无可写凭证字段。</p>"}
          ${channelId !== "web" ? `<div class="alert alert-warn">测连可能返回 protocol_not_integrated；不得把「已保存」当成收发可用。</div>` : ""}
          <p class="meta" id="ch-probe"></p>
          <p class="meta">${escapeHtml(row.note || "")}</p>
          <p class="meta">${metaBadge(row._meta)} · 保存≠消息收发通过</p>
          ${channelId !== "web" ? `<button class="btn" id="ch-save">保存配置</button>
            <button class="btn secondary" id="ch-test" style="margin-left:8px">测试连接</button>` : ""}
          <a class="btn secondary" href="#/system/basic" style="margin-left:8px">系统状态</a>
        </div>`;
      if (typeof watchFormDirty === "function") watchFormDirty(root);
      root.querySelector("#ch-save")?.addEventListener("click", async (ev) => {
        await runAction(ev.currentTarget, async () => {
          const body = {};
          for (const key of [...writable, ...secrets]) {
            const el = root.querySelector(`#ch-${key}`);
            if (!el) continue;
            if (el.type === "password" && !el.value) continue;
            if (key === "enabled") body[key] = el.value === "true";
            else body[key] = el.value;
          }
          await api(`/api/admin/channels/${encodeURIComponent(channelId)}`, { method: "PUT", json: body });
          if (typeof markFormDirty === "function") markFormDirty(false);
          toast("渠道配置已保存（≠已接通）");
          await renderRoute();
        });
      });
      root.querySelector("#ch-test")?.addEventListener("click", async (ev) => {
        await runAction(ev.currentTarget, async () => {
          const probe = root.querySelector("#ch-probe");
          const res = await api(`/api/admin/channels/${encodeURIComponent(channelId)}/test-connection`, { method: "POST" });
          if (probe) probe.textContent = formatProbe(res);
          toast(res.ok ? "探测通过" : `未接通: ${res.status}`, res.ok ? undefined : "error");
        });
      });
    }
    async function pagePhase2(root) {
      let comps;
      try {
        comps = await api("/api/admin/components");
      } catch (err) {
        return failPanel(root, "二期组件总览加载失败", err);
      }
      root.innerHTML = `
        <div class="panel">
          <p class="meta">二期组件开关总览。写入与测连请到组件配置中心。</p>
          <table class="data"><thead><tr><th>组件</th><th>模式</th><th>配置</th><th>状态</th></tr></thead><tbody>
            ${(comps.items || []).map((c) => `<tr>
              <td>${escapeHtml(c.title)}</td><td class="mono">${escapeHtml(c.mode || "-")}</td>
              <td>${c.configured ? "是" : "否"}</td><td>${escapeHtml(c.status || "-")}</td></tr>`).join("")}
          </tbody></table>
          <p class="meta">${metaBadge(comps._meta)}</p>
          <a class="btn secondary" href="#/system/model">组件配置中心</a>
        </div>`;
    }

    async function pageChannelWeb(root) { return pageChannel(root, "web"); }
    async function pageChannelWecom(root) { return pageChannel(root, "wecom"); }
    async function pageChannelMp(root) { return pageChannel(root, "mp"); }

    return {
      "/system/channel/web": pageChannelWeb,
      "/system/channel/wecom": pageChannelWecom,
      "/system/channel/mp": pageChannelMp,
      "/system/phase2": pagePhase2
    };
  });
})();
