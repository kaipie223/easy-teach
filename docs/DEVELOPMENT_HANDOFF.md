# Easy-Teach 开发交接说明

> 本文档用于新开发对话的上下文恢复。新对话应在本文档所在的 worktree 中继续，不要回到旧工作区直接开发。

## 1. 当前开发位置

- 项目名称：Easy-Teach 多模态 AI 互动式教学智能体
- 当前 worktree：`D:\morii\2026暑期服务外包\easy-teach-dev`
- 当前分支：`codex/project-implementation`
- 远程仓库：`https://github.com/kaipie223/easy-teach.git`
- 当前基线：`origin/main` / `ffff342`（原始 worktree 基线为 `a8a0afa`）
- 基线状态：当前分支的提交已合并到 `origin/main`；本地保留未提交的 M0-M4 实现改动
- 网络代理：涉及 GitHub 或模型服务网络请求时，使用本机 `7897` 端口

PowerShell 示例：

```powershell
cd D:\morii\2026暑期服务外包\easy-teach-dev
git status
git log --oneline --decorate -5
```

获取远程更新：

```powershell
git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 fetch origin --prune
```

### 1.1 当前进度（2026-08-20）

- M0-M3 已完成并通过对应测试：工程化、账号/项目、TeachingBrief、资料解析、证据链和本地 RAG。
- M4 已完成：确认后的 `TeachingBrief` 可编译为版本化 `CoursewarePlan`，并由同一份蓝图生成 PPTX、DOCX 和 HTML5 互动内容。
- 数据库使用服务器 PostgreSQL 的本地 SSH 隧道：`127.0.0.1:15432`，数据库 `easy_teach_shared`；Alembic 当前为 `0007_task_queue_quality`。
- 本地 RAG 索引可读，当前包含 2196 条证据；M4 使用确定性蓝图编译，不要求 `DEEPSEEK_API_KEY`。
- M4 真实 HTTP 烟测已通过：PDF -> Evidence -> CoursewarePlan -> 生成任务 -> PPTX/DOCX/HTML 下载。
- M5 已完成核心闭环：不可变成果快照、受限 `RevisionPatch`、版本冲突保护、恢复生成新版本、按版本幂等导出记录和下载；前端成果编辑页已接入版本历史与局部修改。
- M6 队列基础已完成：Celery/Redis worker、生成与导出入队、任务幂等键、心跳、超时配置、自动重试、过期任务恢复和确定性质量报告；新增 `0007_task_queue_quality` 迁移及队列回归测试。
- `origin/codex/project-implementation` 已在合并后被远程删除；继续开发使用当前 worktree 和 `origin/main`。
- M6 剩余工作：Playwright 主流程、PPTX/DOCX 视觉验收、固定 AI/RAG 回归、生产 Compose 实机验收和最终 P0/P1 矩阵。

## 2. 旧工作区说明

旧工作区位于 `D:\morii\2026暑期服务外包\easy-teach`，不要把它当作后续开发目录。

旧工作区当时的状态：

- 分支 `main` 停在 `e966761`，比远程 `origin/main` 落后 2 个提交。
- 有未提交的本地修改：
  - `backend/services/intent.py`
  - `backend/services/rag.py`
  - `src/api/session.ts`
  - `src/utils/request.ts`
  - `vite.config.ts`
- 还有旧对话生成的 `docs/project-plan.md`。

这些修改没有被强制覆盖或删除。新 worktree 从远程最新代码重新开始，若需要旧修改，必须逐文件审阅后再移植，不能直接复制覆盖。

## 3. 产品目标和范围

产品不是简单的“一键生成 PPT”，核心闭环是：

```text
教师表达想法
  -> AI 主动澄清
  -> 上传资料并绑定用途
  -> 多模态解析与 RAG 检索
  -> 确认 TeachingBrief
  -> 生成 CoursewarePlan
  -> 生成 PPT / DOCX 教案 / HTML5 互动内容
  -> 在线预览
  -> 局部修改
  -> 版本管理、回滚和导出
```

本期范围：完成 PRD 的 P0 + P1；P2 暂不作为本期交付门槛。

首个场景：特殊教育“认识红、黄、蓝”，但代码必须保持通用教学平台能力，不能写死特教字段或课程内容。

目标用户：教师和管理员。

## 4. 已确认的技术决策

| 领域 | 已确认方案 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Vite + Element Plus + Pinia |
| 前端目录 | 统一使用 `frontend/`，不再把功能放进根目录 `src/` |
| 后端 | FastAPI + Python |
| Python 依赖 | `uv` + `pyproject.toml` + `uv.lock` |
| 数据库 | PostgreSQL；开发可保留 SQLite 兼容模式 |
| 迁移 | Alembic |
| 文件 | 服务器本地磁盘，Docker 持久化卷，定时备份数据库和文件 |
| 异步任务 | Celery + Redis |
| 对话 | SSE，消息持久化 |
| 生成进度 | 任务状态轮询 |
| 模型 | DeepSeek + OpenAI 兼容接口，均需真实验证 |
| 模型密钥 | 仅服务端环境变量；后台不能保存明文密钥 |
| 输出 | PPTX、DOCX、HTML5 互动包 |
| 版本 | 不可变快照；支持局部修改、回滚、重新导出 |
| 安全 | Argon2id/bcrypt 密码哈希、JWT、接口限流、HTTPS |
| 测试 | 单元测试、API 集成测试、Playwright、固定案例回归、导出视觉测试 |
| 部署 | Docker Compose：Nginx、Web、API、Worker、Redis、PostgreSQL |

注册采用开放注册，首期不做邮箱验证。管理员首期覆盖用户、公共知识库、模型提供商/模型选择；复杂组织权限、完整审计和学生端属于后续范围。

## 5. PRD 位置和重点

PRD 文件：

`D:\morii\2026暑期服务外包\A12_多模态AI互动式教学智能体_PRD_v1.0.md`

重点章节：

- 第 3 章：P0/P1/P2 范围
- 第 7 至 9 章：信息架构、流程和功能需求
- 第 10 章：TeachingBrief、Evidence、CoursewarePlan、RevisionPatch
- 第 11 至 14 章：AI 工作流、技术架构、API 和非功能要求
- 第 15 至 16 章：页面要求、测试和验收
- 第 17 章：推荐开发顺序

核心对象：

- `TeachingBrief`：教师确认后的结构化教学需求
- `MaterialUsageBinding`：资料用途和作用范围
- `EvidenceChunk`：有来源定位、可回溯的证据片段
- `CoursewarePlan`：统一驱动 PPT、教案和互动内容的教学蓝图
- `SlideSpec`：单页 PPT 的结构化描述
- `LessonPlanSpec`：教案结构化描述
- `RevisionPatch`：针对页面、章节或互动内容的局部修改操作

## 6. 当前远程代码的实际情况

### 已有能力

- `backend/` 已有 FastAPI 入口、健康检查、CORS、请求日志和错误处理。
- 已有会话、SSE 对话、上传、生成任务、文件下载和语音转写路由。
- `ai/` 已有意图分析、RAG 分块/检索、语音转写和提示词模块。
- `gen/` 已有 PPTX、DOCX、动画 HTML 生成器和部分文件解析器。
- `frontend/src/` 有工作台、需求共创、对话、资料中心、蓝图、成果编辑、导出与版本页面雏形。
- `knowledge-base/` 已有 4 本计算机相关教材 PDF。
- 远程最新提交包含 BGE 中文向量模型、`sentence-transformers`、Chroma 测试和 Docker 相关调整。

### 尚未完成或需要重构

1. 根目录 `src/` 和 `frontend/src/` 是两套前端。后续以 `frontend/src/` 为产品基础，迁移已验证的接口逻辑，最终只保留 `frontend/` 作为前端工程。
2. `frontend/src/` 大量使用 JavaScript 和 mock 数据，需要逐步迁移 TypeScript，并替换为真实 API。
3. 前后端 API 的历史字段和路径不统一，正式接口统一为 `/api/v1`。
4. 后端当前主要是 SQLite，使用 `Base.metadata.create_all`，还没有 PostgreSQL + Alembic 的完整链路。
5. 现有模型只有 Session、ChatMessage、FileRecord、Task，缺少用户、项目、TeachingBrief、资料绑定、Evidence、蓝图、成果版本、修改补丁和导出记录。
6. 现有生成任务使用 FastAPI `BackgroundTasks`，需要迁移为 Celery + Redis。
7. RAG 的构建目录和后端读取目录曾不一致，Windows 下 Chroma 有 `WindowsPath` 类型问题，需要先修复并加入测试。
8. 生成文件、数据库文件记录和下载接口尚未形成稳定闭环，必须避免生成成功但下载 404。
9. 现有生成/解析代码和模型服务调用需要收敛到统一的适配器、Schema 校验、重试和降级机制。

## 7. 推荐开发顺序

### M0：基线和工程化

- 检查远程提交内容和当前代码。
- 建立 `pyproject.toml`、`uv.lock`、Alembic 和统一环境变量。
- 固定 `/api/v1`、错误结构、鉴权依赖和任务状态枚举。
- 固定前端唯一启动方式，确认 `frontend/` 的开发和构建命令。
- 修复 Windows 下 RAG/Chroma 路径问题。

完成标准：前端、API、PostgreSQL/SQLite、Redis 和 Chroma 健康检查可以明确报告状态。

### M1：账号、项目和数据持久化

- 用户注册、登录、JWT、当前用户、角色权限。
- 项目创建、列表、详情、软删除和恢复。
- Alembic 初始迁移。
- 把消息、资料、任务关联到用户和项目。

完成标准：教师登录后创建项目，刷新页面仍能看到自己的数据；普通用户不能访问管理员接口。

### M2：需求共创

- 持久化消息和 SSE 事件。
- 迁移现有意图分析状态机。
- 生成、修改、确认 TeachingBrief 版本。
- 语音转写结果进入同一消息流水线。

完成标准：教师通过自然语言完成主动追问和需求确认，得到可校验的 TeachingBrief。

### M3：资料解析和知识库

- 五类文件上传、大小限制、真实类型检测和安全存储。
- PDF/DOCX/PPTX/图片/视频统一解析结果。
- 资料用途绑定和证据定位。
- 管理员知识库导入、启用、停用、删除和索引状态。
- 向量检索、元数据过滤；P1 增加 BM25 混合检索和 reranker。

完成标准：PDF 和视频资料能够被真实解析，并在生成内容中展示页码或时间戳来源。

### M4：蓝图和生成

- TeachingBrief -> CoursewarePlan。
- 固定 `SlideSpec`、`LessonPlanSpec`、`InteractionSpec` Schema。
- 通用教学模板、特教低刺激模板。
- PPTX、DOCX、HTML5 配对/分类互动生成。

完成标准：同一份蓝图能同时驱动三种产物，浏览器预览和导出内容一致。

### M5：局部修改、版本和导出

- `RevisionPatch` 解析和白名单操作。
- 稳定 `slide_id`、不可变版本、回滚和重新导出。
- 长任务进度、重试、超时、幂等和失败恢复（Celery worker 的基础实现已在 M6 落地）。
- 导出文件记录、权限校验和下载链路。

完成标准：只修改指定页面时，其他页面不发生变化；旧版本仍可下载。

### M6：质量、部署和验收

- Playwright 主流程。
- API 集成测试、固定 AI 案例、RAG 评测。
- PPTX/DOCX 渲染后的视觉检查。
- Docker Compose、Nginx、备份恢复和部署文档。
- 完成 P0 + P1 验收矩阵。

## 8. 建议的目标数据表

至少需要以下表：

```text
users
projects
messages
teaching_briefs
materials
material_bindings
material_analyses
evidence_chunks
knowledge_documents
courseware_plans
artifact_versions
slide_specs
lesson_plan_specs
interaction_specs
revision_patches
jobs
exports
model_calls
```

必须遵守：

- 成果绑定已确认的 Brief 和蓝图版本。
- 版本不可变，修改必须指定 `base_version`。
- `slide_id` 稳定，局部修改不得全量重写。
- Evidence 删除后只标记失效，不改写历史成果。
- 导出文件绑定具体成果版本，不能只保存“当前文件”路径。
- 文件使用随机 ID 和隔离目录，下载校验用户权限。

## 9. 正式 API 方向

统一前缀：`/api/v1`。

```text
POST   /auth/register
POST   /auth/login
GET    /auth/me

GET    /projects
POST   /projects
GET    /projects/{id}
DELETE /projects/{id}

POST   /projects/{id}/messages       # SSE
GET    /projects/{id}/brief
PATCH  /projects/{id}/brief
POST   /projects/{id}/brief/confirm

POST   /projects/{id}/materials
GET    /materials/{id}
GET    /materials/{id}/analysis
PUT    /materials/{id}/bindings

GET    /knowledge/documents
POST   /knowledge/documents
PATCH  /knowledge/documents/{id}

POST   /projects/{id}/plan
POST   /projects/{id}/generate
GET    /jobs/{id}

POST   /projects/{id}/revisions/interpret
POST   /projects/{id}/revisions/apply
GET    /projects/{id}/versions
POST   /projects/{id}/versions/{version_id}/restore

POST   /projects/{id}/exports
GET    /exports/{id}
GET    /exports/{id}/download
```

错误响应统一包含：`code`、`message`、`details`、`request_id`、`recoverable`、`suggested_action`。

## 10. 测试要求

- 后端单元测试：Schema、权限、文件路径安全、解析器、RAG、RevisionPatch、导出记录。
- API 集成测试：注册登录、项目、SSE、上传、异步任务、版本和下载。
- Worker 测试：任务重试、超时、幂等、进度和失败恢复。
- Playwright：注册 -> 创建项目 -> 对话 -> 上传 -> 蓝图 -> 生成 -> 修改 -> 导出。
- AI 回归：20 至 30 个固定输入，检查 Brief 字段、澄清问题、RAG 命中、真实引用和修改定位。
- 导出视觉测试：PPTX/DOCX 渲染后检查中文字体、溢出、遮挡、分页和乱码。

固定案例：

1. 通用课程：TCP 三次握手，教材 PDF + 教学视频。
2. 特教课程：认识红、黄、蓝，颜色认知 PDF + 图片 + 短视频。

## 11. 开发约束

- 先读现有代码和 PRD，再改动；不要假设远程代码与旧工作区相同。
- 不要在根目录 `src/` 继续增加前端功能。
- 不要用 mock 数据冒充真实接口完成度。
- 不要把模型调用直接散落到路由中，统一进入服务/适配器层。
- 不要让模型直接返回或执行任意 HTML/JavaScript；互动内容使用固定模板 + JSON 数据。
- 不要用 `create_all` 替代正式迁移。
- 不要把 API Key 写入数据库、代码、日志或提交记录。
- 所有长任务都要有状态、进度、错误、重试和幂等策略。
- 变更前端接口时同步更新后端 Schema、API 文档和契约测试。
- 每完成一个里程碑都运行对应测试并记录结果。

## 12. 新对话建议启动语

可以将下面内容作为新对话的第一条消息：

```text
请在 D:\morii\2026暑期服务外包\easy-teach-dev worktree 中继续开发 Easy-Teach。
先阅读 docs/DEVELOPMENT_HANDOFF.md 和 D:\morii\2026暑期服务外包\A12_多模态AI互动式教学智能体_PRD_v1.0.md，检查当前 git 状态和远程基线。
当前分支是 codex/project-implementation，基于 origin/main 的 a8a0afa。
不要使用旧的 D:\morii\2026暑期服务外包\easy-teach 工作区，不要在根目录 src/ 增加前端功能。
按交接文档从 M0 开始，先做基线核对、uv/pyproject/uv.lock、Alembic、前后端契约和 RAG 路径修复；每一步先检查现状再修改并运行验证。
涉及 GitHub 网络请求使用 7897 端口。
```
