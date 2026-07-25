# 开放课堂（OpenClass）

<p align="center">
  <img src="docs/assets/openclass-product-cover.png" alt="OpenClass 产品封面" width="280" />
</p>

> 当前后端 Agent runtime（智能体运行时）采用 [Pi](https://pi.dev/)，OpenClass 自己保留教学工作流、资料工具、写入授权、校验和历史持久化。

开放课堂（OpenClass）是一个面向严肃学习、研究、写作和知识工作的 AI document workspace（AI 文档工作台）。它把 AI conversation（AI 对话）和 AI document writing（AI 文档编写）放在同一个工作空间里：左侧 Pi Agent（Pi 智能体）通过 OpenClass 受控工作流理解需求并执行任务，右侧 Board（板书文档）沉淀结构化成果、支持继续编辑、导入导出和版本回退。

当前仓库是一个本地优先的课程 / 文档工作台：前端提供 OpenClass Studio、课程包、lesson（工作单元 / 文档单元）、富文本文档编辑器、模型选择、Realtime（实时输入输出）入口和版本历史；后端提供 FastAPI（Python API 服务框架）、SQLite（本地关系型数据库）持久化、AI workflow（AI 工作链路）、文档导入导出和审计日志。

OpenClass 不做固定学科模板系统，也不向 general agent（通用智能代理）方向扩张。产品需求、目标用户和边界见 [OpenClass PRD](docs/product/openclass-prd.md)。

## 当前能力

- 工作空间与课程包：创建、打开、重命名、删除课程包，按 lesson 组织严肃学习或文档工作。
- Pi Agent + Board 双工作面：Pi 负责模型推理与受控工具调用；Board 负责保存正式文档内容，OpenClass 负责上下文构造、写入授权、校验和历史提交。
- 空白 Board 生成：空板书时维护 LearningRequirementSheet（学习需求清单），冻结后由 Pi 根据冻结需求生成结构化文档。
- 已有 Board 任务：Pi 根据当前板书、用户请求和明确选区生成讲解或修改提案；后端负责授权、保存结果、校验文档结构并写入历史。
- 富文本文档编辑：右侧类 Word 编辑器支持标题、段落、列表、表格、强调、数学公式、手动编辑和自动保存。
- 文档格式约束：正式 `content_text` 以 Markdown（轻量标记文本格式）/ 普通文本为事实来源；HTML（超文本标记语言）只作为渲染层或导出层结果。
- 导入导出：支持 DOCX（Word 文档格式）导入、DOCX 导出和 HTML 导出。
- 版本历史：lesson 支持 commit（提交记录）、branch（分支）、restore（恢复）和图谱化历史查看。
- 模型目录：`/api/ai-models` 暴露可用模型和 provider（模型提供方）状态，前端使用统一的文本模型选择。
- Realtime：默认关闭；开启后作为同一个 Chatbot 的实时语音 / 实时输入输出形态，而不是新的独立教师角色。
- 登录与管理：支持邮箱登录、游客登录、可选 OAuth（第三方登录授权）和基础 admin（管理员）总览。

## 产品 Workflow

### 从空白 Board 到文档

1. 用户提出学习、研究、写作或文档任务。
2. 后端判断当前 Board 是否为空，并识别本轮是否需要生成文档。
3. Requirement Manager（需求管理器）维护最小必要需求清单；信息不足时只追问关键缺口。
4. 需求达到可执行条件后写入 frozen requirement（冻结需求快照）。
5. Pi 只根据冻结快照和必要资料摘要生成右侧 Board。
6. 系统写入 lesson commit，并保留 requirement run（需求运行记录）与 metadata（元数据）。
7. Pi 承接下一步，不把临时聊天内容当作正式文档事实来源。

### 围绕已有 Board 工作

1. 用户发起讲解、补充、改写、练习或互动请求。
2. 后端构造当前 Board、用户明确选区和经过验证的资料上下文。
3. Pi 在同一 turn（一次用户请求到模型响应的回合）内生成讲解或文档操作提案。
4. 后端校验 Pi 的结构化结果、Markdown（轻量标记文本格式）、富文本结构、授权状态和资源引用。
5. 成功执行后写入 commit / chat history（聊天历史）与 Pi turn metadata（回合元数据），保留可追溯历史。

## 仓库地图

```text
.
├── apps/
│   ├── api/                         # FastAPI 后端
│   │   ├── app/main.py              # 应用组装、CORS（跨源资源共享）、健康检查、模型目录
│   │   ├── app/routers/             # API route（接口路由）：auth / workspace / documents / chat / realtime / resources
│   │   ├── app/services/            # service layer（服务层）：AI workflow、文档、资料、历史、模型、Realtime
│   │   ├── app/models.py            # model/schema（数据结构）：Board、lesson、资源、任务、响应模型
│   │   ├── tests/                   # pytest（Python 测试框架）用例
│   │   └── data/                    # 本地运行数据，已 gitignore（Git 忽略）
│   └── web/                         # Next.js（React 应用框架）前端
│       ├── src/app/                 # 页面路由：home / studio / course / auth / admin
│       ├── src/components/          # frontend UI（前端界面）组件
│       ├── src/components/course-studio/
│       ├── src/hooks/course-studio/ # hook（前端状态逻辑）
│       └── src/lib/                 # 前端 API、格式、模型和状态工具
├── docs/
│   ├── assets/                      # README 和产品展示素材
│   └── product/openclass-prd.md     # PRD（产品需求文档）
├── launcher/                        # 本地入口 HTML
├── launchd/                         # macOS 后台守护配置
├── scripts/                         # 本地守护、安装和 guard（守卫检查）脚本
├── package.json                     # 根 workspace（工作区）脚本
├── pyproject.toml                   # 后端依赖与 pytest 配置
└── .env.example                     # 环境变量示例
```

## 本地运行

需要 Node.js（JavaScript 运行时）20+ 和 Python（后端语言）3.13+。

```bash
npm run setup            # 首次安装：npm install + .venv + editable（可编辑模式）安装后端
cp .env.example .env     # 配置至少一个 provider（模型提供方）
npm run dev              # 同时启动前后端
```

- 前端：http://localhost:3000
- 后端：http://localhost:8000
- 健康检查：http://localhost:8000/health
- Open Notebook（资料处理后端）：默认使用 `http://localhost:5055`；需单独启动其 API 和 worker（任务执行器）
- SQLite 主库：`apps/api/data/openclass.sqlite3`
- AI 调用日志：`apps/api/data/logs/ai-usage.jsonl`

也可以双击 `start-ai-board.command`，它会通过本地守护进程启动前后端，并打开 `launcher/personal-home.html`。日常长时间使用优先用这个入口。

使用 LaunchAgent（macOS 后台启动服务）长期运行时，先安装守护配置：

```bash
./scripts/install-launch-agents.sh
```

安装器会在首次运行时把当前 `.env` 复制到 `~/.config/openclass/runtime.env`，后续 API 直接读取这份工作树外配置。切换 Git worktree（Git 工作树）时必须使用：

```bash
./scripts/switch-local-runtime.sh /absolute/path/to/openclass-worktree
```

切换脚本会先停止服务，再替换 `.openclass-launch`，最后验证 Web、API 和认证提供方；健康检查或已有认证能力减少时会自动回滚。不要直接改 `.openclass-launch` 符号链接，也不要在多个工作树中复制包含密钥的 `.env`。

生产或长期运行时，建议把数据目录指到稳定持久化路径：

```bash
OPENCLASS_DATABASE_PATH=/var/lib/openclass/openclass.sqlite3
OPENCLASS_UPLOAD_DIR=/var/lib/openclass/uploads
OPENCLASS_EXPORT_DIR=/var/lib/openclass/exports
OPENCLASS_PUBLIC_ORIGIN=https://your-domain.example
OPENCLASS_WEB_ORIGIN=https://your-domain.example
```

## 模型与 Provider（模型提供方）

最小配置：

```bash
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_COMPAT_API=chat_completions
AI_TEXT_PROVIDER=openai
AI_REALTIME_PROVIDER=openai
OPENAI_MODEL=gpt-5.5
OPENAI_PM_MODEL=gpt-5.5
OPENAI_BOARD_MODEL=gpt-5.5
OPENAI_CHATBOT_MODEL=gpt-5.5
OPENCLASS_REALTIME_ENABLED=false
OPENCLASS_REALTIME_TOOLS_ENABLED=true
OPENAI_REALTIME_MODEL=gpt-realtime-2.1
OPENAI_REALTIME_REASONING_EFFORT=low
OPENAI_IMAGE_MODEL=gpt-image-2
OPENCLASS_SPEECH_PROVIDER=volcengine
VOLCENGINE_TTS_API_KEY=your_volcengine_speech_api_key
VOLCENGINE_TTS_RESOURCE_ID=seed-tts-2.0
VOLCENGINE_TTS_SPEAKER=zh_female_vv_uranus_bigtts
```

`.env.example` 还包含 Pi Agent 凭据目录、DeepSeek、OpenAI Realtime 和旧 Codex app-server（Codex 应用服务）回退适配器配置。

默认的 `OPENCLASS_SOURCE_BACKEND=native` 使用 OpenClass 本地工具和 Pi 资料 Agent 建立资料目录；OpenClass 负责文件隔离、范围读取、机械校验和持久化。需要接入 Open Notebook 时，可显式设置 `OPENCLASS_SOURCE_BACKEND=open_notebook`。

Realtime 默认关闭；只有设置 `OPENCLASS_REALTIME_ENABLED=true` 才会启用后端实时连接。`OPENCLASS_REALTIME_TOOLS_ENABLED=true` 时，浏览器通过 OpenAI WebRTC（网页实时通信）接收 function call（函数调用），再交给经过用户与 lesson 权限校验的 OpenClass 后端读取受限板书范围或调用同一条 Chatbot workflow，最后只把受控结果返回 Realtime；关闭时只做麦克风转写，再把文本交给普通 Chatbot。`OPENAI_REALTIME_REASONING_EFFORT=low` 是语音默认推理强度，可按延迟和复杂度调成 `medium` 或 `high`。

聊天回复的自动播报使用独立的 TTS（文字转语音）链路。默认 adapter（适配器）通过 `VOLCENGINE_TTS_API_KEY` 调用豆包语音 V3 HTTP Chunked API（第三版 HTTP 分块接口），不复用 Pi 的模型认证。模型版本、音色和语速分别由 `VOLCENGINE_TTS_RESOURCE_ID`、`VOLCENGINE_TTS_SPEAKER`、`VOLCENGINE_TTS_SPEECH_RATE` 配置；密钥只由 FastAPI 后端读取，不能放进 `NEXT_PUBLIC_*` 前端变量。右侧「课程工作台辅助」里的“AI 回复自动播报”开关控制新回复是否自动播放，聊天消息下方的“播报”按钮可手动重播单条回复。

## 数据与文档格式

- AI 写入 Board 的正式正文必须是 Markdown / 普通文本，不能把模型直接返回的 HTML 保存为正式 `content_text`。
- 前端编辑器可以把文档渲染成 HTML DOM（浏览器文档对象模型），但这只是展示层，不改变后端事实来源。
- DOCX 导出走后端原生渲染路径，和网页富文本渲染保持分离。
- 资料解析以通用结构为边界：章节、页面、片段、引用范围和 evidence，不在核心代码里内置具体学科、教材或 demo 内容。

## 测试与验证

`npm run verify` 是本地和 CI（持续集成）的主验证入口，不需要真实 LLM（大语言模型）API key（接口密钥）：

```bash
npm run guard:file-sizes
npm run lint:web
npm run typecheck:web
npm run test:api
npm run build:web
npm run verify
```

GitHub Actions 会在 PR（Pull Request，合并请求）和 `main` 分支 push（推送）时运行 `.github/workflows/verify.yml` 中的 verify workflow（验证工作流）。

Playwright（浏览器端到端测试工具）主流程 smoke test（冒烟测试）是可选验证，默认不作为合并门禁：

```bash
npm run test:e2e          # 默认启动 127.0.0.1:3110 / 127.0.0.1:8110
```

## 协作约定

- 工程与 AI 协作规则见 [AGENTS.md](AGENTS.md)。
- 前端协作规则见 [apps/web/AGENTS.md](apps/web/AGENTS.md)。
- 提交前优先运行 `npm run verify`。
- 新功能应接入现有 AI workflow，不绕过需求清单、目标定位、资料选择、写入确认、讲解授权和历史审计。
- OpenClass 保持通用能力优先：不要把具体学科、教材、考试、固定讲义或 demo 样例写入核心默认路径。
