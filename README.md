# AgenTruss

可复用的企业级 AI Agent 平台脚手架。原则：**换行业不改底座**（配置 / 提示词 / 知识库 / 工具插件）。

## 实现状态（以源码与测试为准）

| 状态 | 能力 |
| --- | --- |
| **已实现并通过测试** | JWT + 注销；脱敏；工单 Fernet/`args_digest`/进程锁；上传消毒；登录限流；Redis fail-closed；崩溃→`reconciliation_required`；LangGraph；`/ready` 503；**Mock Embedding + Mock Vector Store**；`OpenAICompatibleEmbedding` 契约（缺钥 fail-closed）；能力门禁 + `test_tools_architecture`；LOOKUP_FIRST（权威=`supports_result_lookup`）；**`mypy app` 全包** |
| **已实现、需外部依赖才完整** | Alembic **`004`–`006`（agent_configs / settings_kv / 策略表）**；组件 PUT+测连；IAM 用户写；策略 PG；企微/MP **配置可存≠接通**；PG CAS；Redis/Milvus/MinIO；Echo/JP≠生产；真实 Embedding 需密钥 |
| **公开 CI 绿 [`e7e13e6`](https://github.com/wpuing/AgenTruss/commit/e7e13e6) / [#40801969720](https://github.com/wpuing/AgenTruss/actions/runs/40801969720)** | test + browser-e2e + 集成 success；MinIO `bucket_exists` 探针；`live-e2e` skipped≠PASS |
| **明确 BLOCKED / skipped≠PASS** | `live-e2e`；`RUN_EMBEDDING_LIVE`；AgentScope Live；生产 Durable Adapter（缺沙箱）；企微/MP **消息收发**未接；限流在线写入；在线建租户 |
| **渐进 DDD（局部）** | `approvals` / `knowledge`；Application 不直连 `app.tools` |
| **明确不做** | Outbox；Exactly-once 宣称；PG↔MinIO 跨系统原子备份；独立 Vite 管理端；无证据的大规模 Workflow 迁移；支付网关 |

## 快速开始（开发 · 内存 + Mock RAG）

```bash
pip install -e .
copy .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
pytest
# 可选浏览器 E2E（需 Chromium）：
# pip install -e ".[e2e]" && playwright install chromium
# set RUN_BROWSER_E2E=1 && pytest tests/test_admin_browser_e2e.py -q
# 真 PG（仅测试库名 *_test / *_ci，且 ALLOW_PG_SCHEMA_RESET=1）：
# set RUN_PG_INTEGRATION=1
# set ALLOW_PG_SCHEMA_RESET=1
# set DATABASE_URL=postgresql+psycopg://.../agenttruss_test
# pytest tests/test_alembic_pg_upgrade.py tests/test_approvals_pg_cas.py -vv
```

演示账号（仅 development + `ALLOW_DEMO_USERS=true`）：`admin` / `changeme` / 租户 `demo`。

管理端（内嵌 SPA，非独立 Vite 工程）：打开 http://127.0.0.1:8000/admin  
静态资源：`app/static/admin/`（hash 路由 + 真实 JWT）。截图见本地 `docs/ui-screenshots/`（不进公开 GitHub）。

可选：`ADMIN_DEMO_MODE=true` 启用管理端演示草稿（工作流可视化等）；**生产禁止**。失败的真实 API **不会**静默换成演示成功。详见本地 `docs/admin-phase4-implementation.md`。

默认 `EMBEDDING_PROVIDER=mock`、`VECTOR_STORE_BACKEND=mock`：**无需 API Key、无需 Milvus**。Mock 向量不具备真实语义。  
`STORAGE_BACKEND=postgres` 且未配 MinIO 时，文档全文写入 `kb_documents.body_text`，服务重启后可恢复；仅内存模式或未落库的 Mock 向量仍会随进程丢失。

## 生产 / Compose

1. `.env.example` → `.env`，强密钥 + `STORAGE_BACKEND=postgres` + `DATABASE_URL` + `REDIS_URL`。
2. `docker compose up -d`：postgres → **migrate（alembic upgrade head，含 004）** → api；默认 **不** 启 MinIO/Milvus。
3. 可选对象存储：`docker compose --profile object-store up -d`；Milvus：`--profile milvus`。
4. 手工迁移（无 Compose）：依赖就绪后先 `alembic upgrade head`，再启 API（生产跳过 `create_all()`）。

配置了 `VECTOR_STORE_BACKEND=milvus` 但缺少 `MILVUS_URI` / 客户端失败时：**直接报错**，不会静默切回 Mock。  
生产未配 MinIO 时正文走 PG `body_text`；若配置了 MinIO 但读写失败，上传/删除 **不会** 静默改记为成功。

## Mock RAG 说明

- 流程：上传 → 解析 → 切分 → Mock Embedding → Mock Vector Store → 检索（余弦相似度）→ 返回片段。
- `kb/qa` 仍为检索模板，`llm_called=false`，**不**宣称真实大模型问答。
- 扩展点：`OpenAICompatibleEmbedding`、`MilvusVectorStore`；后续换真实实现保持同一接口。

## 安全摘要

凭证与 PII 全出口脱敏；工单参数密文 + digest；生产禁弱密钥 / 演示账号 / 内存后端。

`/health` = 存活；`/ready` = 依赖检查，未就绪 **503**。
