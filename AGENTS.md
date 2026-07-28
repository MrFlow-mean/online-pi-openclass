# 开放课堂（OpenClass）— AI 协作指南

开放课堂（OpenClass）是一个 AI 课程工作台。产品介绍、安装与 provider 配置见 `README.md` 和 `.env.example`。本文件只列日常协作必须知道的事。

## 先读哪一份

| 文件 | 什么时候必须读 |
| --- | --- |
| 本文件 | 每次改动前 |
| `docs/architecture/ai-collaboration.md` | 改 AI 链路、角色边界、需求清单、写入授权、路由审计时 |
| `docs/operations/deployment.md` | 部署、重启、排查线上问题时 |
| `apps/web/AGENTS.md` | 改前端时 |
| `docs/product/openclass-prd.md` | 需要产品意图背景时 |

## 仓库地图

```text
.
├── apps/
│   ├── api/              # FastAPI 后端（Python 3.13）
│   │   ├── app/main.py       # 应用组装 + 健康检查
│   │   ├── app/routers/      # workspace / documents / chat / realtime / resources
│   │   ├── app/services/     # 业务逻辑、状态、AI、存储、历史
│   │   └── data/             # 本地运行数据，已 gitignore
│   └── web/              # Next.js 前端，详见 apps/web/AGENTS.md
├── docs/                 # 架构宪法、运维手册、产品文档
├── launcher/             # 可双击的本地入口 HTML
├── package.json          # 根 workspace 脚本
├── pyproject.toml        # 后端依赖 + pytest（单一来源）
└── .env.example          # 环境变量示例
```

## 常用命令（仓库根执行）

```bash
npm run setup            # 首次安装：npm install + .venv + editable 装后端
npm run dev              # 同时启动前后端（:3000 / :8000）
npm run dev:web | dev:api
npm run lint:web | typecheck:web | test:api | build:web
npm run test:e2e         # Playwright 主流程 smoke（默认 :3110 / :8110）
npm run verify           # 提交前 gate：file-size guard + lint + typecheck + test:api + build:web
```

后端虚拟环境固定在仓库根 `.venv/`，不要在子目录另建。依赖变更后重跑 `npm run setup:api`，否则 `test:api` 会因缺少可选解析库而整片失败。

## 不要做

- 不要在 router 里直接拼 SQL 或绕过 service 事务。
- 不要把 SQLite 文件、上传文件、日志放在 repo / `.next/` / 临时目录 / 会被部署覆盖的位置。
- 不要在线上手改 sqlite，除非已停服务并备份。
- 不要让多个独立后端进程同时写同一 sqlite。
- 不要在迁移到 SQLite 时顺手大改前端 UI；先收口存储与一致性。
- 不要为了单个 demo、单份资料或单次测试把特殊规则写进核心 service。

## 核心约束（详见 `docs/architecture/ai-collaboration.md`）

OpenClass 是通用 AI 课程工作台，不是学科模板系统。两条宪法级约束在任何改动中都不可绕过：

1. **通用能力优先**：核心代码只处理通用学习能力、内容形态、资料结构、用户意图、文档操作和模型调用。学科 / 教材 / 考试 / 语法点 / demo 关键词分支一律禁止进入核心默认路径。
2. **链路兼容优先**：新能力必须接入现有协作协议，不得重写、抢占或绕过它。标准回合流程固定为：

```text
用户输入
→ TurnDecision 判断本轮任务
→ ResolveTarget 定位板书、选区、资料证据或对话上下文
→ BuildContext 构造最小必要上下文
→ ExecuteRole 执行唯一主动作
→ PersistHistory 写入历史、commit 与可追踪 metadata
→ UpdateRequirement 记录需求变化
```

改动前必须能说清本次属于标准链路的哪一步；说不清就不要改代码。涉及自然语言规则、角色写权限、写入确认或路由裁决时，先读宪法全文。

文件边界（这些文件不得继续吞职责）：

- `lesson_factory.py`：只做 lesson、requirements、teaching guide 初始化。
- `fallback_generator.py`：只做领域无关 fallback，不得成为模板仓库。
- `renderer.py`：只做渲染路径选择，不写具体课程内容。
- `resource_library.py`：只做通用资料解析，不内置教材目录。
- `openai_course_ai.py`：只做模型调用、prompt、schema 解析，不写学科分支。
- `course-studio.tsx`：只做顶层组合，不继续堆状态、effect、realtime、editor、model selection 逻辑。

## 后端约定：router 处理 HTTP，service 承担业务

- 新接口归入 `workspace / documents / chat / realtime / resources` 之一。
- 状态读写走 `app/services/workspace_state.py` 的 helper；新增代码优先经 `get_store()` / `get_course_store()` 取得 store，为后续依赖注入保留替换点。
- 课程包持久化用 `SqliteCourseStore`；新增写路径复用 service 层事务，不要恢复 `store.json` 写入。
- auth 表读写收口在 `AuthStore`；`auth_service.py` 负责认证流程、密码/OAuth 规则和错误转换，不继续新增裸 SQL。
- 任何改动课程包 / lesson / 文档 / 版本历史 / 资源库的操作必须在事务内。
- 返回前端前剥离资料原文与本地路径。
- 读取可选环境变量用 `app/services/config.py` 的 `env_setting()`；`.env` 里的 `NAME=` 表示「保持默认」，`os.getenv` 会把它当成已配置的空串。

## AI 生成架构约束

- 核心 service 必须遵守「通用能力优先」。
- 不得写入 demo、教材、学科专属生成逻辑；不得把固定讲义全文或「关键词→专用模板」作为默认路径。
- 线上行为只能由用户输入、上传资料、课程 metadata、模型输出与通用规则驱动。
- 术语表、章节目录、知识点扩展从资料或模型来，不写死在 workflow / factory / resource_library。
- 任何课程级示例与 fixture 仅允许在 tests、fixtures、文档中出现，不得污染真实请求的默认逻辑。
- 当前真实启用的 AI 入口以 `/api/ai-models`、`/api/lessons/{lesson_id}/chat` 和文档相关 service 为准；realtime 后端默认关闭，只有 `OPENCLASS_REALTIME_ENABLED=true` 时才会接入 OpenAI WebRTC，且仍作为同一个 Chatbot 的实时形态。`BoardTeachingGuide` / `BoardTeachingProgress` 一类类型属于保留兼容 / future workflow schema，不能当作已完整接入的教学运行框架。

## 数据存储

- SQLite 主库默认 `apps/api/data/openclass.sqlite3`，线上设 `OPENCLASS_DATABASE_PATH=/var/lib/openclass/openclass.sqlite3`。开 WAL，设合理 `busy_timeout`。
- 上传文件落盘到持久化目录（线上 `/var/lib/openclass/uploads/`），DB 只存 metadata、原始文件名、mime、大小、路径。
- 旧 `apps/api/data/store.json` 仅作首次迁移来源，导入后归档为 `store.migrated-*.json`，不再作运行存储。
- AI 输入输出走 `apps/api/data/logs/ai-usage.jsonl`，不入主业务表。

主要表（`SqliteCourseStore`）：

| 表 | 内容 |
| --- | --- |
| `course_packages` | 课程包标题、摘要、排序、当前打开状态 |
| `lessons` | lesson 基础信息、所属 package、当前文档、学习需求、教学指南 |
| `lesson_commits` | 历史快照、commit metadata、父 commit、分支名 |
| `lesson_branches` | 分支名、head commit、base commit |
| `course_graph_edges` | 课程图谱关系 |
| `resources` | 上传资料 metadata、抽取状态、文件路径 |
| `resource_chapters` | 资料章节 outline |
| `workspace_settings` | active package、打开标签页等全局 workspace 状态 |

富文本 `content_json` / `content_html` / `content_text` 暂作 JSON/text 字段存在 `lessons` 与 `lesson_commits`，不拆 block 表。

## 环境与日志

- 复制 `.env.example` 为仓库根 `.env`，不要提交。
- 线上额外配置：`OPENCLASS_DATABASE_PATH`、`OPENCLASS_UPLOAD_DIR`、`OPENCLASS_EXPORT_DIR` 都指到 `/var/lib/openclass/` 下。
- 前端「选择模型」读 `/api/ai-models`，未配置 key 的 provider 显示为未配置。
- 测试不读仓库 `.env`：`apps/api/tests/conftest.py` 会把 provider 相关变量清空，保证 `test:api` 离线、确定、不打真实 API。新增 provider 变量时同步加进那份清单。

## 测试与完成标准

- 任何自然语言规则、路由规则、任务判断，必须先写 golden fixture；每个正例至少配两个反例。
- 没有反例，不允许新增自然语言规则。
- 一个任务算完成，必须满足：行为有测试或 fixture 覆盖、改动文件数最小、公开行为变化有文档、相关测试已跑并报告结果、剩余风险已明确列出。

## 提交前

- 跑 `npm run verify`（或至少 `lint:web` + `typecheck:web` + 受影响的 `test:api`）。
- 不要提交 `.env`、`.venv/`、`apps/api/data/` 下的运行数据、`node_modules/`、`.next/`。

## 风格

- 注释只解释非显而易见的意图或约束，不复述代码。
- 不主动新建 README / 文档；扩充本指南或对应 README 即可。
