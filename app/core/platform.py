"""In-memory platform state used by the verifiable scaffold.

Production swaps Redis, PostgreSQL, Milvus, and MinIO behind the same service calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import yaml

from app.core.config import ROOT, get_settings
from app.core.passwords import hash_password
from app.core.security import AuthContext

ALL_PERMS = {
    "dashboard:view",
    "chat:debug:view",
    "chat:session:view",
    "chat:session:clear",
    "kb:view",
    "kb:base:view",
    "kb:base:create",
    "kb:base:update",
    "kb:base:delete",
    "kb:doc:view",
    "kb:doc:upload",
    "kb:doc:delete",
    "kb:doc:parse",
    "kb:doc:chunk",
    "kb:doc:vectorize",
    "kb:segment:view",
    "kb:retrieve:view",
    "kb:retrieve:test",
    "kb:qa:view",
    "kb:qa:test",
    "kb:chunk_strategy:view",
    "kb:retrieve_strategy:view",
    "kb:job:view",
    "kb:feedback:view",
    "kb:feedback:write",
    "agent:view",
    "agent:instance:view",
    "agent:instance:create",
    "agent:instance:update",
    "agent:instance:publish",
    "agent:template:view",
    "agent:topic:view",
    "agent:prompt:view",
    "agent:tool:view",
    "agent:guard:view",
    "agent:graph:view",
    "agent:graph:update",
    "ticket:view",
    "ticket:human:view",
    "ticket:human:handle",
    "ticket:risk:view",
    "ticket:risk:approve",
    "ticket:risk:reject",
    "audit:view",
    "audit:log:view",
    "audit:log:export",
    "audit:trace:view",
    "audit:metrics:view",
    "audit:exec:view",
    "sys:view",
    "sys:basic:view",
    "sys:theme:view",
    "sys:theme:update",
    "sys:model:view",
    "sys:ratelimit:view",
    "sys:channel:view",
    "sys:channel:update",
    "sys:phase2:view",
    "iam:view",
    "iam:user:view",
    "iam:user:create",
    "iam:user:update",
    "iam:role:view",
    "iam:tenant:view",
    "sys:model:update",
    "kb:chunk_strategy:update",
    "kb:retrieve_strategy:update",
}

AUDITOR_PERMS = {
    "dashboard:view",
    "audit:view",
    "audit:log:view",
    "audit:exec:view",
    "audit:trace:view",
}


def _m(code: str, title: str, path: str | None, perm: str, phase: str, children: list | None = None, **extra) -> dict:
    """Build one menu node. ``path=None`` means a folder that only groups children."""
    node = {"code": code, "title": title, "perm": perm, "phase": phase, **extra}
    if path is not None:
        node["path"] = path
    if children is not None:
        node["children"] = children
        node["folder"] = True
    return node


# Nested menu tree: L1 group → L2 page/folder → L3 leaf (product/01 · rag/01).
MENU_TREE = [
    _m("dashboard", "工作台", "/dashboard", "dashboard:view", "MVP", group="概览"),
    _m("chat", "对话与会话", None, "chat:debug:view", "MVP", group="对话", children=[
        _m("chat.playground", "在线调试对话", "/chat/playground", "chat:debug:view", "MVP"),
        _m("chat.sessions", "会话列表", "/chat/sessions", "chat:session:view", "P1"),
    ]),
    _m("kb", "知识库", None, "kb:view", "MVP", group="知识库", children=[
        _m("kb.base", "知识库管理", None, "kb:base:view", "MVP", children=[
            _m("kb.base.list", "库列表", "/kb/base", "kb:base:view", "MVP"),
            _m("kb.base.detail", "库详情", "/kb/base/detail", "kb:base:view", "MVP"),
        ]),
        _m("kb.docs", "文档管理", None, "kb:doc:view", "MVP", children=[
            _m("kb.docs.list", "文档列表", "/kb/docs", "kb:doc:view", "MVP"),
            _m("kb.docs.upload", "上传向导", "/kb/docs/upload", "kb:doc:upload", "MVP"),
            _m("kb.docs.detail", "文档详情", "/kb/docs/detail", "kb:doc:view", "MVP"),
        ]),
        _m("kb.segments", "片段管理", None, "kb:segment:view", "MVP", children=[
            _m("kb.segments.browse", "按文档浏览", "/kb/segments", "kb:segment:view", "MVP"),
            _m("kb.segments.edit", "片段编辑", "/kb/segments/edit", "kb:segment:view", "MVP"),
        ]),
        _m("kb.chunk_strategy", "切分策略", None, "kb:chunk_strategy:view", "MVP", children=[
            _m("kb.chunk_strategy.list", "策略列表", "/kb/chunk-strategy", "kb:chunk_strategy:view", "MVP"),
            _m("kb.chunk_strategy.form", "策略表单", "/kb/chunk-strategy/form", "kb:chunk_strategy:view", "MVP"),
        ]),
        _m("kb.retrieve_strategy", "检索策略", None, "kb:retrieve_strategy:view", "MVP", children=[
            _m("kb.retrieve_strategy.list", "策略列表", "/kb/retrieve-strategy", "kb:retrieve_strategy:view", "MVP"),
            _m("kb.retrieve_strategy.form", "策略表单", "/kb/retrieve-strategy/form", "kb:retrieve_strategy:view", "MVP"),
        ]),
        _m("kb.jobs", "入库任务", None, "kb:job:view", "MVP", children=[
            _m("kb.jobs.list", "任务列表", "/kb/jobs", "kb:job:view", "MVP"),
            _m("kb.jobs.detail", "任务详情", "/kb/jobs/detail", "kb:job:view", "MVP"),
        ]),
        _m("kb.retrieve", "检索试测", "/kb/retrieve", "kb:retrieve:view", "MVP"),
        _m("kb.qa", "知识问答试测", "/kb/qa", "kb:qa:view", "MVP"),
        _m("kb.feedback", "质量反馈", "/kb/feedback", "kb:feedback:view", "P1"),
    ]),
    _m("agent", "Agent 中心", None, "agent:view", "MVP", group="Agent", children=[
        _m("agent.list", "Agent 实例列表", "/agent/list", "agent:instance:view", "MVP"),
        _m("agent.wizard", "搭建向导", "/agent/wizard", "agent:instance:create", "MVP"),
        _m("agent.config", "配置台", None, "agent:instance:view", "MVP", children=[
            _m("agent.detail.basic", "基本信息与行业模板", "/agent/config/basic", "agent:instance:view", "MVP"),
            _m("agent.detail.kb", "知识库绑定", "/agent/config/kb", "agent:instance:view", "MVP"),
            _m("agent.detail.topics", "话题与转人工", "/agent/config/topics", "agent:topic:view", "MVP"),
            _m("agent.detail.prompts", "提示词模板", "/agent/config/prompts", "agent:prompt:view", "MVP"),
            _m("agent.detail.tools", "工具白名单", "/agent/config/tools", "agent:tool:view", "MVP"),
            _m("agent.detail.guardrails", "护栏与阈值", "/agent/config/guardrails", "agent:guard:view", "MVP"),
            _m("agent.detail.graph", "编排说明与检查点", "/agent/config/graph", "agent:graph:view", "P1"),
            _m("agent.detail.publish", "发布与版本", "/agent/config/publish", "agent:instance:publish", "MVP"),
            _m("agent.playground", "调试对话", "/agent/config/playground", "chat:debug:view", "MVP"),
        ]),
        _m("agent.templates", "行业模板库", "/agent/templates", "agent:template:view", "MVP"),
    ]),
    _m("ticket", "工单与审批", None, "ticket:view", "P1", group="工单", children=[
        _m("ticket.human", "转人工工单", None, "ticket:human:view", "P1", children=[
            _m("ticket.human.list", "工单列表", "/tickets/human", "ticket:human:view", "P1"),
            _m("ticket.human.detail", "处理台", "/tickets/human/detail", "ticket:human:view", "P1"),
        ]),
        _m("ticket.risk", "高风险审批", None, "ticket:risk:view", "P1", children=[
            _m("ticket.risk.list", "审批列表", "/tickets/risk", "ticket:risk:view", "P1"),
            _m("ticket.risk.detail", "审批台", "/tickets/risk/detail", "ticket:risk:view", "P1"),
        ]),
    ]),
    _m("audit", "审计与观测", None, "audit:view", "MVP", group="审计", children=[
        _m("audit.logs", "审计日志", None, "audit:log:view", "MVP", children=[
            _m("audit.logs.list", "日志列表", "/audit/logs", "audit:log:view", "MVP"),
            _m("audit.logs.detail", "日志详情", "/audit/logs/detail", "audit:log:view", "MVP"),
        ]),
        _m("audit.exec", "执行记录", "/audit/executions", "audit:exec:view", "P1"),
        _m("audit.trace", "会话追踪", "/audit/trace", "audit:trace:view", "P1"),
        _m("audit.metrics", "运行指标", "/audit/metrics", "audit:metrics:view", "P2"),
    ]),
    _m("sys", "系统配置", None, "sys:view", "MVP", group="系统", children=[
        _m("sys.basic", "基础参数", "/system/basic", "sys:basic:view", "MVP"),
        _m("sys.theme", "主题切换", "/system/theme", "sys:theme:view", "MVP"),
        _m("sys.model", "模型与 Embedding", "/system/model", "sys:model:view", "MVP"),
        _m("sys.ratelimit", "限流与熔断", "/system/ratelimit", "sys:ratelimit:view", "MVP"),
        _m("sys.channel", "渠道接入", None, "sys:channel:view", "P2", children=[
            _m("sys.channel.web", "Web", "/system/channel/web", "sys:channel:view", "P2"),
            _m("sys.channel.wecom", "企业微信", "/system/channel/wecom", "sys:channel:view", "P2"),
            _m("sys.channel.mp", "公众号", "/system/channel/mp", "sys:channel:view", "P2"),
        ]),
        _m("sys.phase2", "二期组件开关", "/system/phase2", "sys:phase2:view", "P2"),
        _m("sys.backlog", "未完成代办", "/backlog", "dashboard:view", "MVP"),
    ]),
    _m("iam", "权限与组织", None, "iam:view", "MVP", group="权限", children=[
        _m("iam.users", "用户管理", None, "iam:user:view", "MVP", children=[
            _m("iam.users.list", "用户列表", "/iam/users", "iam:user:view", "MVP"),
            _m("iam.users.form", "新建/编辑", "/iam/users/form", "iam:user:view", "MVP"),
        ]),
        _m("iam.roles", "角色权限", None, "iam:role:view", "MVP", children=[
            _m("iam.roles.list", "角色列表", "/iam/roles", "iam:role:view", "MVP"),
            _m("iam.roles.tree", "权限树", "/iam/roles/tree", "iam:role:view", "MVP"),
        ]),
        _m("iam.tenants", "租户管理", "/iam/tenants", "iam:tenant:view", "MVP"),
    ]),
    # Last top-level row: unfinished work checklist (pause point Phase5.28b).
    _m(
        "backlog",
        "未完成代办",
        None,
        "dashboard:view",
        "MVP",
        group="规划",
        children=[
            _m("backlog.list", "代办清单", "/backlog", "dashboard:view", "MVP"),
        ],
    ),
]

# Backward-compatible flat list of leaves (tests / title map).
MENUS: list[dict] = []


def _walk_leaves(nodes: list[dict], group: str | None = None) -> None:
    for node in nodes:
        g = node.get("group") or group
        kids = node.get("children") or []
        if kids:
            _walk_leaves(kids, g)
        elif node.get("path"):
            MENUS.append({**node, "group": g or "其他"})


_walk_leaves(MENU_TREE)


def new_id(prefix: str) -> str:
    """Return a short unique id."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def load_yaml(path) -> dict:
    """Load a YAML mapping."""
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# Admin console theme catalog (id must match themes.css data-theme).
THEME_CATALOG = [
    {"id": "ember", "name": "暖橙珊瑚", "blurb": "默认运营台：暖色活力，适合日常巡检。", "swatch": ["#ff5a36", "#ff7a4d", "#0f8f6c"]},
    {"id": "ocean", "name": "深蓝海岸", "blurb": "冷静蓝调，偏技术与监控观感。", "swatch": ["#1f6feb", "#4c8df5", "#0e8a7a"]},
    {"id": "forest", "name": "墨绿松林", "blurb": "自然绿系，偏知识库与内容运营。", "swatch": ["#2f7d4a", "#4a9a64", "#1b7a5a"]},
    {"id": "slate", "name": "冷灰商务", "blurb": "低饱和中性灰，偏企业管理后台。", "swatch": ["#475569", "#64748b", "#0f766e"]},
    {"id": "midnight", "name": "暗夜控制台", "blurb": "深色护眼，适合夜间值班与调试。", "swatch": ["#38bdf8", "#0b1020", "#34d399"]},
    {"id": "sand", "name": "砂岩纸质", "blurb": "暖褐纸感，偏文档与阅读场景。", "swatch": ["#8b5e34", "#a87445", "#4f6f52"]},
    {"id": "sky", "name": "晴空天蓝", "blurb": "清爽天蓝，偏客服与对外门户。", "swatch": ["#0ea5e9", "#38bdf8", "#14b8a6"]},
    {"id": "rose", "name": "玫瑰石英", "blurb": "柔和玫粉，偏品牌与活动运营。", "swatch": ["#c45c78", "#d87a93", "#2f8f7b"]},
    {"id": "graphite", "name": "石墨高对比", "blurb": "黑白硬朗、直角边，偏极简信息密度。", "swatch": ["#111111", "#333333", "#ececec"]},
    {"id": "aurora", "name": "极光青绿", "blurb": "青绿极光，偏增长与实验看板。", "swatch": ["#0d9488", "#14b8a6", "#059669"]},
    {"id": "matcha", "name": "细腻抹茶", "blurb": "淡雅茶绿与米白，偏细腻内容与知识阅读。", "swatch": ["#6b8f71", "#a3b18a", "#dad7cd"]},
    {"id": "living", "name": "极简生活", "blurb": "留白与低对比，偏日常轻运营与生活感界面。", "swatch": ["#5c6b73", "#c9d1d3", "#f4f1ea"]},
]


@dataclass
class Platform:
    """Process-local stores. One instance per application object."""

    tenants: dict = field(default_factory=dict)
    users: dict = field(default_factory=dict)
    kbs: dict = field(default_factory=dict)
    documents: dict = field(default_factory=dict)
    segments: dict = field(default_factory=dict)
    chunk_strategies: dict = field(default_factory=dict)
    retrieve_strategies: dict = field(default_factory=dict)
    jobs: dict = field(default_factory=dict)
    agents: dict = field(default_factory=dict)
    audits: list = field(default_factory=list)
    tickets: dict = field(default_factory=dict)
    memories: dict = field(default_factory=dict)
    session_index: dict = field(default_factory=dict)
    vectors: list = field(default_factory=list)
    rate_buckets: dict = field(default_factory=dict)
    industries: dict = field(default_factory=dict)
    settings_doc: dict = field(default_factory=dict)
    # Component config overlay (settings_kv hydrate / admin save). Secrets stay in-memory values only.
    component_overrides: dict = field(default_factory=dict)
    # Channel overlays (wecom/mp); saved≠connected.
    channel_overrides: dict = field(default_factory=dict)

    @classmethod
    def seed(cls) -> "Platform":
        """Build a platform with two industries, users, strategies, and agents."""
        settings = get_settings()
        platform = cls()
        tenants = load_yaml(ROOT / "config" / "tenants.yaml")["tenants"]
        for code, meta in tenants.items():
            industry = load_yaml(ROOT / "config" / f"{meta['industry']}.yaml")
            platform.industries[meta["industry"]] = industry
            platform.tenants[code] = {"code": code, "name": meta["name"], "industry": meta["industry"], "status": "active"}
        platform._seed_strategies()
        # Demo accounts only when explicitly allowed (default: development).
        if settings.allow_demo_users:
            platform._seed_users(settings.demo_password)
        platform._seed_agents()
        platform.settings_doc = {
            "app.name": "AgenTruss",
            "openapi.enabled": True,
            "model.api_key": "",
            "ui.theme": "ember",
        }
        return platform

    def _seed_strategies(self) -> None:
        # Stable ids so PATCH + PG hydrate + KB bindings survive restart.
        loose = "rs-loose"
        strict = "rs-strict"
        base = "cs-baseline"
        fine = "cs-fine"
        self.retrieve_strategies[loose] = {
            "id": loose,
            "name": "loose",
            "vector_min_score": 0.15,
            "rerank_min_score": 0.15,
            "top_k": 5,
            "is_seed": True,
            "version": 1,
        }
        self.retrieve_strategies[strict] = {
            "id": strict,
            "name": "strict",
            "vector_min_score": 0.95,
            "rerank_min_score": 0.95,
            "top_k": 5,
            "is_seed": True,
            "version": 1,
        }
        self.chunk_strategies[base] = {
            "id": base,
            "name": "baseline",
            "chunk_size": 80,
            "child_size": 40,
            "overlap": 0,
            "is_seed": True,
            "version": 1,
        }
        self.chunk_strategies[fine] = {
            "id": fine,
            "name": "fine",
            "chunk_size": 20,
            "child_size": 10,
            "overlap": 0,
            "is_seed": True,
            "version": 1,
        }
        self.default_retrieve_id = loose
        self.default_chunk_id = base
        self.strict_retrieve_id = strict

    def _seed_users(self, password: str) -> None:
        from app.core.rbac import perms_for_roles

        specs = [
            ("admin", "demo", ["tenant_admin"]),
            ("auditor", "demo", ["auditor"]),
            ("finadmin", "finance", ["tenant_admin"]),
        ]
        hashed = hash_password(password)
        for username, tenant, roles in specs:
            uid = new_id("usr")
            self.users[username] = {
                "id": uid,
                "username": username,
                "password": hashed,
                "tenant_id": tenant,
                "roles": roles,
                "perms": perms_for_roles(roles),
                "status": "active",
            }

    def _seed_agents(self) -> None:
        # Stable ids so RAG bindings / SPA selection survive process restart (with PG hydrate).
        for tenant_id, tenant in self.tenants.items():
            agent_id = f"agt-{tenant_id}-default"
            self.agents[agent_id] = {
                "agent_id": agent_id,
                "tenant_id": tenant_id,
                "name": f"{tenant['industry']}-default",
                "code": "default",
                "industry": tenant["industry"],
                "status": "published",
                "version": 1,
                "is_default": True,
                "kb_binding": {"kb_id": None, "rag_enabled": False},
                "enabled_tools": list(self.industries[tenant["industry"]]["tools"]),
            }

    def user_by_name(self, username: str) -> dict | None:
        return self.users.get(username)

    def auth_for(self, user: dict) -> AuthContext:
        return AuthContext(user["id"], user["tenant_id"], list(user["roles"]), set(user["perms"]))

    def default_agent(self, tenant_id: str) -> dict:
        for agent in self.agents.values():
            if agent["tenant_id"] == tenant_id and agent["is_default"] and agent["status"] == "published":
                return agent
        raise KeyError(tenant_id)

    def visible_menus(self, perms: set[str], phase: str) -> list[dict]:
        """Return nested menus filtered by permission and phase."""
        return _filter_menu_tree(MENU_TREE, perms, phase)


def _phase_ok(node: dict, phase: str) -> bool:
    if phase == "all":
        return True
    return node.get("phase", "MVP") == "MVP"


def _filter_menu_tree(nodes: list[dict], perms: set[str], phase: str) -> list[dict]:
    result: list[dict] = []
    for node in nodes:
        if not _phase_ok(node, phase):
            continue
        if node.get("perm") and node["perm"] not in perms:
            continue
        kids = node.get("children") or []
        if kids:
            filtered_kids = _filter_menu_tree(kids, perms, phase)
            if not filtered_kids:
                continue
            item = {k: v for k, v in node.items() if k != "children"}
            item["children"] = filtered_kids
            result.append(item)
            continue
        if not node.get("path"):
            continue
        result.append(dict(node))
    return result
