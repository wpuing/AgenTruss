"""Product backlog of unfinished / blocked work for the Admin console.

This is an operator-facing checklist — not a claim that listed items are done.
Statuses: blocked | partial | planned | note
"""

from __future__ import annotations

from app.admin.meta import envelope, utc_now_iso


def list_backlog() -> dict:
    """Return detailed unfinished-work groups for the 未完成代办 page."""
    groups = [
        {
            "id": "live_external",
            "title": "外部 Live / 沙箱（缺密钥或协议）",
            "priority": "P0-阻塞",
            "summary": "代码契约与 fail-closed 已具备；缺真实密钥、业务沙箱或渠道协议前不得标为 Live Verified。",
            "items": [
                {
                    "id": "embedding-live",
                    "title": "Embedding 真实调用验收",
                    "status": "blocked",
                    "status_label": "BLOCKED",
                    "why": "默认 Mock；openai/dashscope 缺 API Key 时 fail-closed。契约测已绿，真调用需密钥。",
                    "done_when": [
                        "配置 OPENAI_API_KEY 或 DASHSCOPE_API_KEY",
                        "RUN_EMBEDDING_LIVE=1 下 embed_one 成功且维度匹配",
                        "知识库向量化 / 试检索走真实 Provider 且可审计",
                    ],
                    "entry": "#/system/model · Embedding 卡片",
                    "tests": "tests/test_embedding_provider.py（契约）；Live 需环境变量",
                    "owner_hint": "运维提供密钥后在组件配置中心测连",
                },
                {
                    "id": "agentscope-live",
                    "title": "AgentScope Live 对话验收",
                    "status": "blocked",
                    "status_label": "BLOCKED",
                    "why": "Stub 明确 llm_called=False；Live 需 agentscope + 模型密钥 + 显式开关。",
                    "done_when": [
                        "DASHSCOPE_API_KEY 或 OPENAI_API_KEY 可用",
                        "AGENTSCOPE_MODEL_MODE=live + RUN_AGENTSCOPE_LIVE=1",
                        "对话响应 llm_called=True 且审计落库，CI skip≠PASS",
                    ],
                    "entry": "#/chat/playground · 组件「大模型服务」",
                    "tests": "CI live-e2e（无 Secret 时 skip）；本地 Live 手工/门禁测",
                    "owner_hint": "启用 agentscope 后勿把 stub 成功当成 Live",
                },
                {
                    "id": "durable-adapter",
                    "title": "生产 Durable Adapter / 业务沙箱",
                    "status": "blocked",
                    "status_label": "BLOCKED",
                    "why": "Echo / JSONPlaceholder 仅演示；durable_contract 校验元数据，无真实执行沙箱。",
                    "done_when": [
                        "提供沙箱 URL、认证、execution id、结果查询协议",
                        "注册 supports_result_lookup 的生产 Adapter",
                        "批准→执行→lookup/对账全链路在沙箱验证（仍非 Exactly-once）",
                    ],
                    "entry": "#/agent/config/tools · #/tickets/risk",
                    "tests": "tests/test_durable_contract.py；Echo/JP ≠ 生产验收",
                    "owner_hint": "业务方协议就绪后再接，禁止用 Mock 冒充 Durable",
                },
                {
                    "id": "channel-messaging",
                    "title": "企微 / 公众号真实消息收发",
                    "status": "partial",
                    "status_label": "配置可存 · 协议未接",
                    "why": "Phase5.28b 已支持凭证保存与诚实测连（protocol_not_integrated）；无回调/推送实现。",
                    "done_when": [
                        "企业微信 / 公众号开放平台账号与回调 URL",
                        "验签、收消息、回消息端到端通过",
                        "测连与运行态区分：配置已保存 / 连接通过 / 消息收发通过",
                    ],
                    "entry": "#/system/channel/wecom · #/system/channel/mp",
                    "tests": "test_channel_and_authz_gaps（保存≠接通）",
                    "owner_hint": "有账号后再做 Provider，勿把「已保存」显示成「已接通」",
                },
            ],
        },
        {
            "id": "ops_partial",
            "title": "管理端与运维能力缺口（可独立推进）",
            "priority": "P1",
            "summary": "主流程可测；下列为体验或运维闭环未做完的部分。",
            "items": [
                {
                    "id": "ratelimit-write",
                    "title": "限流在线写入",
                    "status": "planned",
                    "status_label": "只读",
                    "why": "限流页仅展示登录限流 / Redis / Ready；改阈值需改 .env 并重启。",
                    "done_when": [
                        "受控写入 API + 权限 + 审计",
                        "多实例下配置权威来源明确（非单进程内存）",
                    ],
                    "entry": "#/system/ratelimit",
                    "tests": "—",
                    "owner_hint": "需产品确认是否允许运行时改限流",
                },
                {
                    "id": "jobs-queue",
                    "title": "独立任务队列 / 后台作业",
                    "status": "planned",
                    "status_label": "未集成",
                    "why": "组件卡 jobs=not_integrated；入库任务仍为进程内，无 RabbitMQ/Kafka。",
                    "done_when": [
                        "可选消息队列与 worker",
                        "任务状态可跨实例查询；失败可重试策略可配置",
                    ],
                    "entry": "#/kb/jobs · #/system/phase2",
                    "tests": "—",
                    "owner_hint": "方案文档标为二期可选",
                },
                {
                    "id": "minio-probe",
                    "title": "MinIO 无副作用健康探针",
                    "status": "done",
                    "status_label": "已完成",
                    "why": "object_storage 测连走 bucket_exists 只读探针（带 connect/read 超时）；未配置 fail-closed。一致性脚本仍只读。",
                    "done_when": [],
                    "entry": "#/system/model · 对象存储",
                    "tests": "test_component_iam_strategy · test_minio_integration（真 MinIO）",
                    "owner_hint": "—",
                },
                {
                    "id": "tenant-online-create",
                    "title": "在线创建租户",
                    "status": "planned",
                    "status_label": "YAML 只读",
                    "why": "租户来自 config/tenants.yaml；无管理端创建租户 API。",
                    "done_when": [
                        "租户 CRUD + 行业绑定 + 隔离验收",
                        "禁止跨租户读写回归测",
                    ],
                    "entry": "#/iam/tenants",
                    "tests": "—",
                    "owner_hint": "多租户交付前再开",
                },
                {
                    "id": "segment-edit-api",
                    "title": "知识片段正文在线编辑",
                    "status": "planned",
                    "status_label": "有意未开放",
                    "why": "防误改向量基线；当前引导重新切分/向量化。",
                    "done_when": [
                        "受控编辑 + 版本/审计 + 自动失效旧向量",
                    ],
                    "entry": "#/kb/segments/edit",
                    "tests": "—",
                    "owner_hint": "需明确一致性策略后再做",
                },
                {
                    "id": "workflow-designer",
                    "title": "真实 LangGraph 可视化拖拽设计器",
                    "status": "planned",
                    "status_label": "阶段轨 + Mock 草稿",
                    "why": "工作流页展示真实阶段轨；可视化草稿不写真实图。",
                    "done_when": [
                        "可编辑图并持久化且与运行时一致",
                        "与 Checkpointer / 审批断点语义对齐",
                    ],
                    "entry": "#/agent/config/graph",
                    "tests": "—",
                    "owner_hint": "非当前脚手架 MVP 范围",
                },
            ],
        },
        {
            "id": "security_quality",
            "title": "安全与工程债（持续）",
            "priority": "P2",
            "summary": "P0 主路径已加固；下列为可继续收紧的项，勿用扫描绿代替真实威胁模型。",
            "items": [
                {
                    "id": "ruff-b008-noise",
                    "title": "Ruff B008（Depends 默认参数）历史噪音",
                    "status": "note",
                    "status_label": "已知",
                    "why": "FastAPI 惯用 Depends(need(...))；全量 ruff 会报大量 B008。",
                    "done_when": [
                        "按目录配置 per-file-ignores 或逐步改为模块级依赖单例",
                    ],
                    "entry": "pyproject.toml / CI",
                    "tests": "CI Ruff 任务",
                    "owner_hint": "勿为清噪音大范围无关重构",
                },
                {
                    "id": "jwt-perm-snapshot",
                    "title": "JWT 内嵌 perms 与实时重算双轨说明",
                    "status": "partial",
                    "status_label": "已即时重算",
                    "why": "current_auth 已按平台用户重算 roles/perms；JWT 仍携带历史 claim 仅作签发快照。",
                    "done_when": [
                        "文档写清「权限以服务端用户表为准」",
                        "可选：缩短 JWT TTL 或角色变更批量吊销 jti",
                    ],
                    "entry": "登录 / IAM 停用",
                    "tests": "test_component_iam_strategy（停用→401）",
                    "owner_hint": "生产可再收紧 TTL",
                },
                {
                    "id": "outbox",
                    "title": "Outbox / Exactly-once",
                    "status": "note",
                    "status_label": "明确不做",
                    "why": "产品决策：不新增 Outbox，不宣称跨系统原子与 Exactly-once。",
                    "done_when": [
                        "保持现状；不确定结果走 reconciliation_required",
                    ],
                    "entry": "审批执行 / lookup-resolve",
                    "tests": "reliability / reconcile 相关测",
                    "owner_hint": "勿在后续迭代偷偷引入假 Exactly-once",
                },
            ],
        },
        {
            "id": "release",
            "title": "发布与环境收尾",
            "priority": "运维",
            "summary": "本轮代码已在本地可测；公开 GitHub 推送与生产迁移按需执行。",
            "items": [
                {
                    "id": "alembic-006-prod",
                    "title": "生产 / 共享库执行 Alembic 至 006",
                    "status": "planned",
                    "status_label": "待运维",
                    "why": "新增 settings_kv、chunk/retrieve_strategies；测试库 create_all/upgrade 已验。",
                    "done_when": [
                        "目标库 alembic upgrade head",
                        "启动 hydrate 策略与组件覆盖正常",
                    ],
                    "entry": "alembic/versions/006_settings_and_strategies.py",
                    "tests": "test_alembic_pg_upgrade · test_strategy_settings_pg",
                    "owner_hint": "仅对 *_test/_ci 做 schema reset；生产禁止 reset",
                },
                {
                    "id": "github-push",
                    "title": "公开 GitHub 清洁快照推送",
                    "status": "done",
                    "status_label": "已完成",
                    "why": "脱敏推送至 `7aec922`；后续变更仍走 sanitize，禁止推 docs/scripts/AGENTS。",
                    "done_when": [],
                    "entry": "scripts/cicd/01-push_github_clean.py",
                    "tests": "Actions #40801263957 success",
                    "owner_hint": "本地 dirty 再推需用户确认",
                },
                {
                    "id": "public-ci-stamp",
                    "title": "公开 CI 能力基线盖章更新",
                    "status": "done",
                    "status_label": "已完成",
                    "why": "`7aec922` / Actions #40801263957 整 run success（含 browser-e2e）；live-e2e skipped≠PASS。",
                    "done_when": [],
                    "entry": "README · .cursor/rules/docs-and-mvp-memory.mdc",
                    "tests": "GitHub Actions",
                    "owner_hint": "以最新 Actions 为准",
                },
            ],
        },
        {
            "id": "done_recent",
            "title": "近期已闭合（对照，避免重复建设）",
            "priority": "参考",
            "summary": "下列已可测；新需求请先核验再开单。",
            "items": [
                {
                    "id": "done-component",
                    "title": "组件配置写入 + 测连",
                    "status": "done",
                    "status_label": "已完成",
                    "why": "PUT + test-connection；保存≠连通；密钥脱敏。",
                    "done_when": [],
                    "entry": "#/system/model",
                    "tests": "test_component_iam_strategy",
                    "owner_hint": "—",
                },
                {
                    "id": "done-iam",
                    "title": "IAM 用户创建 / 改角色 / 停用",
                    "status": "done",
                    "status_label": "已完成",
                    "why": "后端强制鉴权；停用即时失效。",
                    "done_when": [],
                    "entry": "#/iam/users/form",
                    "tests": "test_component_iam_strategy · test_channel_and_authz_gaps",
                    "owner_hint": "—",
                },
                {
                    "id": "done-strategy-pg",
                    "title": "策略 PATCH → PostgreSQL",
                    "status": "done",
                    "status_label": "已完成",
                    "why": "稳定 cs-*/rs-*；expected_version 冲突 409；重启 hydrate。",
                    "done_when": [],
                    "entry": "#/kb/chunk-strategy · retrieve-strategy",
                    "tests": "test_strategy_settings_pg",
                    "owner_hint": "—",
                },
                {
                    "id": "done-agent-config-pages",
                    "title": "Agent 配置子页真实 API",
                    "status": "done",
                    "status_label": "已完成",
                    "why": "wizard/templates/basic/kb/topics/prompts/guardrails/publish；YAML 只读标明。",
                    "done_when": [],
                    "entry": "#/agent/config/*",
                    "tests": "test_admin_browser_e2e · test_console_pages_api",
                    "owner_hint": "—",
                },
                {
                    "id": "done-browser-e2e-ci",
                    "title": "浏览器 E2E CI 绿",
                    "status": "done",
                    "status_label": "已完成",
                    "why": "组件测连 handler 竞态修复；#40801263957 browser-e2e success。",
                    "done_when": [],
                    "entry": "tests/test_admin_browser_e2e.py",
                    "tests": "RUN_BROWSER_E2E=1",
                    "owner_hint": "—",
                },
                {
                    "id": "done-admin-spa-ui26",
                    "title": "Admin SPA 架构收敛 + 前后端 Agent 可调用语义对齐",
                    "status": "done",
                    "status_label": "已完成",
                    "why": (
                        "19 pages + admin-kit/ui26；agents 列表 chat_callable/for_chat；"
                        "停用对话 404 detail；system/basic 限流只读字段。"
                    ),
                    "done_when": [],
                    "entry": "#/agent/list · #/chat/playground · #/system/ratelimit",
                    "tests": "test_admin_browser_e2e · test_mainflow_gaps · test_admin_spa_backend_align",
                    "owner_hint": "—",
                },
            ],
        },
    ]
    counts = {"blocked": 0, "partial": 0, "planned": 0, "note": 0, "done": 0}
    for g in groups:
        raw_items = g.get("items") if isinstance(g, dict) else None
        if not isinstance(raw_items, list):
            continue
        for it in raw_items:
            if not isinstance(it, dict):
                continue
            st = str(it.get("status") or "note")
            if st in counts:
                counts[st] += 1
    return envelope(
        {
            "title": "未完成代办",
            "updated_at": utc_now_iso(),
            "baseline_note": (
                "主路径与公开 CI（含 browser-e2e）已闭合；Admin SPA ui26 与 Agent chat_callable 已对齐。"
                "下列为外部阻塞与后续可选工作；禁止把 BLOCKED 标成已通过。"
            ),
            "counts": counts,
            "groups": groups,
        },
        source="admin_backlog_static",
        is_demo=False,
        note="静态清单随版本更新；非工单系统",
    )
