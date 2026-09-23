# easy-teach 项目问题审计与解决方案

> 审计日期：2026-08-29  
> 审计范围：FastAPI 后端、Vue 前端、AI/RAG、任务队列、文件生成、Docker Compose 与运行配置  
> 说明：本文档基于当前工作区代码和实际运行结果整理。文件位置均以仓库根目录为基准；行号可能随后续编辑变化。

## 1. 结论摘要

当前项目的 PPTX、DOCX、HTML 文件生成器可以正常生成文件，生成器相关测试通过；但项目聊天的 AI 意图分析因 DeepSeek 模型名错误而不可用，端到端教学需求确认流程因此无法正常工作。

生产部署前必须优先处理 P0/P1 问题，尤其是 AI 模型、匿名数据隔离、上传资源限制、Docker 文件共享和知识库写入权限。当前 `.env` 中还存在真实 API Key，不能将其复制到日志、Issue、CI 输出或文档中。

## 2. 严重级别

| 级别 | 含义 | 处理要求 |
| --- | --- | --- |
| P0 | 核心业务不可用或存在立即阻断 | 立即修复，修复后才能验收主流程 |
| P1 | 高风险安全、数据一致性或部署问题 | 上线前必须修复 |
| P2 | 明确的功能缺陷、性能问题或运维风险 | 近期修复并补充测试 |
| P3 | 中低风险的安全加固、可维护性或测试缺口 | 纳入迭代计划 |

## 3. P0：核心业务阻断

### P0-01 DeepSeek 意图分析使用了无效模型名

- **代码章节**：[backend/services/intent.py:91-100](../backend/services/intent.py#L91)
- **问题**：请求使用 `model="deepseek-Schat"`。
- **实测证据**：DeepSeek API 返回 HTTP 400，并明确提示当前支持 `deepseek-v4-pro`、`deepseek-v4-flash` 和 `deepseek-v4-flash-vision-exp`，不支持 `deepseek-Schat`。
- **影响**：项目聊天接口仍可能返回 SSE，但意图结果为空；需求确认单不能正确填充，后续蓝图生成链路拿不到可靠教学目标。
- **解决方案**：
  1. 将模型名改为实际账号可用的模型，并增加 `DEEPSEEK_INTENT_MODEL` 配置项。
  2. 启动时校验模型配置；不要等用户发送消息后才发现配置错误。
  3. 为 HTTP 400、超时、限流和 JSON 解析失败分别记录结构化错误码。
  4. 增加 mock LLM 测试和一次真实配置的集成测试，断言 teaching goal 能写入 brief。
- **验收标准**：有效 API Key 下，发送一条项目消息后，`GET /projects/{id}/brief` 的 `teaching_goal` 非空，且测试不再触发 400。

## 4. P1：安全、数据与部署风险

### P1-01 匿名会话之间没有真正的数据隔离

- **代码章节**：[backend/routers/session.py:73-84](../backend/routers/session.py#L73)、[backend/core/ownership.py:32-45](../backend/core/ownership.py#L32)
- **问题**：未登录请求统一查询 `user_id IS NULL`。所有匿名用户共享同一个逻辑身份。
- **影响**：`GET /api/v1/sessions` 可返回其他匿名用户的会话和消息；已知或猜测 ID 时，匿名文件、任务也可能被读取。
- **解决方案**：
  1. 新产品接口强制登录；兼容匿名模式时，为每个匿名客户端签发随机、不可伪造的 session capability token。
  2. 所有会话、文件、任务查询同时校验 capability token，不再只依赖 `user_id IS NULL`。
  3. 禁止匿名列出全部会话，只允许按 capability token 查询自己的会话。
  4. 增加两个匿名客户端互相访问会话、文件和任务的 403/404 测试。

### P1-02 匿名接口没有限流和资源配额

- **代码章节**：[backend/routers/upload.py:43-82](../backend/routers/upload.py#L43)、[backend/routers/generate.py:26-80](../backend/routers/generate.py#L26)、[backend/routers/speech.py:22-49](../backend/routers/speech.py#L22)
- **问题**：上传、语音转写、生成、任务状态和反馈等接口允许匿名访问；未见 IP 限流、匿名配额、LLM 预算或 CAPTCHA。
- **影响**：攻击者可以消耗 DeepSeek 额度、语音模型、CPU、内存和磁盘。
- **解决方案**：
  1. 生产环境对生成、聊天、转写和上传接口要求登录。
  2. 对 IP、用户和 capability token 分别设置速率限制。
  3. 增加单用户文件总量、每日 LLM token、任务并发数和语音时长配额。
  4. 对失败重试设置上限和指数退避，并把配额判断放在调用外部模型之前。

### P1-03 文件上传读取方式可能造成内存压力

- **代码章节**：[backend/routers/upload.py:69-82](../backend/routers/upload.py#L69)、[backend/routers/materials.py:78-88](../backend/routers/materials.py#L78)、[backend/routers/speech.py:34-40](../backend/routers/speech.py#L34)
- **问题**：普通上传先执行 `await file.read()` 后再检查大小；材料接口虽然读取了上限加 1 字节，但仍将整个上限内容放入内存；语音接口没有大小限制。
- **影响**：并发上传可造成内存耗尽，匿名语音接口风险更高。
- **解决方案**：
  1. 使用分块读取并边读边计数，超过上限立即中止并删除临时文件。
  2. 为语音增加字节数、时长和 MIME 类型限制。
  3. 反向代理同步设置 `client_max_body_size`，但不能替代后端校验。
  4. 增加 1 个超限文件、多个并发大文件和断点上传清理测试。

### P1-04 PDF/Word 图片解析使用固定临时文件名

- **代码章节**：[backend/services/parser.py:121-142](../backend/services/parser.py#L121)、[backend/services/parser.py:195-216](../backend/services/parser.py#L195)
- **问题**：临时图片写入当前工作目录，文件名由页码和计数器组成，没有请求级随机目录。
- **影响**：并发解析时可能互相覆盖、互相删除，导致 AI 读取错误图片；容器中当前目录也可能没有写权限。
- **解决方案**：
  1. 使用 `tempfile.TemporaryDirectory` 或配置目录下的 UUID 子目录。
  2. 使用 `Path` 管理临时文件，并在 `finally` 中清理整个临时目录。
  3. 限制图片尺寸、数量和总字节数，避免恶意文档触发资源放大。
  4. 增加两个并发 PDF/Word 解析任务的隔离测试。

### P1-05 图片解析返回类型与调用方契约不一致

- **代码章节**：[backend/services/parser.py:23-69](../backend/services/parser.py#L23)、[backend/services/orchestrator.py:391-408](../backend/services/orchestrator.py#L391)、[backend/schemas.py:464](../backend/schemas.py#L464)
- **问题**：`parse_image()` 返回包含 `status/message/data` 的 dict，但 `_load_references()` 将整个 dict 当作 `ReferenceMaterial.extracted_text` 字符串使用。
- **影响**：Pydantic 校验可能失败并被宽泛异常捕获，图片参考资料被静默跳过或产生错误文本。
- **解决方案**：
  1. 统一返回类型，例如定义 `ParsedImageResult`，成功和失败都使用同一结构。
  2. 调用方只取 `result.data`，失败时记录错误并明确标记该资料解析失败。
  3. 用类型检查或 mypy/pyright 阻止 dict 传入 str 字段。
  4. 增加图片成功、模型失败和空结果三种测试。

### P1-06 图片视觉模型配置与实际调用不匹配

- **代码章节**：[backend/services/parser.py:30-61](../backend/services/parser.py#L30)
- **问题**：请求携带 `image_url`，但调用的是普通 `deepseek-chat`；代码注释也说明后续应使用视觉模型。
- **影响**：图片资料解析可能被 API 拒绝，或无法得到可靠的 OCR/图表结果。
- **解决方案**：
  1. 增加独立的 `DEEPSEEK_VISION_MODEL` 配置。
  2. 启动或健康检查阶段验证该模型是否支持视觉输入。
  3. 对视觉模型不可用时明确返回“图片解析不可用”，不要伪装成成功。
  4. 测试图片、公式和表格输入，并检查结果确实进入生成上下文。

### P1-07 开发 Docker 后端目录挂载会破坏 Python 包路径

- **代码章节**：[docker-compose.dev.yml:23-29](../docker-compose.dev.yml#L23)、[Dockerfile.backend:3-10](../Dockerfile.backend#L3)、[backend/main.py:11-35](../backend/main.py#L11)
- **问题**：镜像工作目录是 `/app`，项目根目录内容包含 `backend`、`ai`、`gen`；开发 Compose 却把宿主机 `./backend` 覆盖挂载到 `/app`，同时执行 `uvicorn main:app`。
- **影响**：容器内 `/app/backend` 和 `/app/ai` 不存在，`from backend...` 等导入可能失败，热重载服务无法启动。
- **解决方案**：二选一：
  1. 挂载整个项目根目录到 `/app`，执行 `uvicorn backend.main:app`。
  2. 继续只挂载 `backend` 时，将工作目录、`PYTHONPATH`、导入方式和 AI 目录挂载全部改成一致的布局。
  3. 用 `docker compose up` 做一次真实启动检查，不只运行 config 解析。

### P1-08 开发 Docker 前端环境变量名称不一致

- **代码章节**：[docker-compose.dev.yml:43](../docker-compose.dev.yml#L43)、[frontend/vite.config.js:15-16](../frontend/vite.config.js#L15)
- **问题**：Compose 设置 `VITE_API_TARGET`，Vite 读取 `VITE_API_PROXY_TARGET`。
- **影响**：Vite 回退到 `http://localhost:8000`；在前端容器中 `localhost` 指向前端容器自身，API 代理失败。
- **解决方案**：统一使用 `VITE_API_PROXY_TARGET=http://backend:8000`，或修改 Vite 读取 Compose 使用的变量；增加容器内请求 `/api/v1/health` 的冒烟测试。

### P1-09 生产环境可能继承 `TASK_QUEUE_EAGER=true`

- **代码章节**：[.env](../.env)、[docker-compose.production.yml:34-56](../docker-compose.production.yml#L34)、[backend/services/task_queue.py:165-209](../backend/services/task_queue.py#L165)
- **问题**：生产 Compose 使用 `env_file: .env`，但没有明确覆盖 `TASK_QUEUE_EAGER=false`；当前本地 `.env` 为 true。
- **影响**：生成和导出可能在 API 请求内同步执行，阻塞 worker，并绕过 Celery/Redis 的异步模型。
- **解决方案**：
  1. 生产 Compose 显式设置 `TASK_QUEUE_EAGER: "false"`。
  2. 生产部署使用独立的 `.env.production`，不要复用开发 `.env`。
  3. 启动时打印非敏感运行模式，例如 `queue=celery/eager`，并在生产发现 eager 时拒绝启动。
  4. 增加真实 Redis + worker 的异步任务验收测试。

### P1-10 生产 API 与 worker 没有共享上传目录

- **代码章节**：[docker-compose.production.yml](../docker-compose.production.yml)、[backend/config.py:65-76](../backend/config.py#L65)、[backend/routers/upload.py:69-94](../backend/routers/upload.py#L69)
- **问题**：生产 `app_data` 只挂载 `/app/data`，当前 `UPLOAD_DIR=./uploads` 会解析到 `/app/uploads`，该目录未在 API 与 worker 间共享。
- **影响**：API 保存的 legacy `FileRecord` 文件，worker 可能无法读取；容器重建后上传文件也会丢失。
- **解决方案**：统一把上传目录配置为 `/app/data/uploads`，并让 API、worker 共享同一个 volume；同时为输出目录和上传目录分别设置备份与清理策略。

### P1-11 生产知识库目录只读，但知识库上传会写入其中

- **代码章节**：[docker-compose.production.yml](../docker-compose.production.yml)、[backend/services/knowledge.py:170-173](../backend/services/knowledge.py#L170)、[backend/routers/knowledge.py:75-111](../backend/routers/knowledge.py#L75)
- **问题**：Compose 将 `./knowledge-base` 以 `:ro` 挂载；知识库导入会把文件写入 `knowledge_base_dir/.managed/...`。
- **影响**：管理员上传知识库文档时可能因权限错误返回 500，知识库管理功能无法使用。
- **解决方案**：
  1. 将源知识库和运行时 `.managed` 分离：源目录继续只读，新增 `knowledge_managed_data:/app/knowledge-base/.managed` 可写卷。
  2. 或将 `KNOWLEDGE_BASE_DIR` 指向独立可写目录。
  3. 增加生产容器内管理员上传、索引、重启后读取的验收测试。

### P1-12 环境变量中的 API Key 存在被操作命令回显的风险

- **代码章节**：[.env](../.env)、[docker-compose.production.yml](../docker-compose.production.yml)
- **问题**：当前 `.env` 含真实 DeepSeek API Key；`docker compose config` 会将环境变量完整展开并打印。
- **影响**：终端历史、CI 日志、Issue、审计附件或聊天记录可能泄露密钥。
- **解决方案**：
  1. 如果该 Key 曾被复制、回显或提交到外部系统，立即在 DeepSeek 控制台轮换。
  2. 使用 Docker secrets、部署平台 secret 或受控环境变量，不把密钥放进 Compose 展开输出。
  3. 文档、日志和错误响应只保留“已配置/未配置”，绝不记录密钥值。
  4. 检查 Git 历史、CI 日志和团队共享目录是否曾出现该密钥。

### P1-13 成果版本号存在并发冲突

- **代码章节**：[backend/services/versions.py:64-71](../backend/services/versions.py#L64)、[backend/models/versioning.py:30-40](../backend/models/versioning.py#L30)、[backend/services/courseware.py:472-477](../backend/services/courseware.py#L472)
- **问题**：版本号通过 `MAX(version) + 1` 生成，数据库只有普通索引，没有 `(project_id, version)` 唯一约束。
- **影响**：并发编辑、恢复或生成时可能产生重复版本号，最新版本排序和导出绑定可能不确定。
- **解决方案**：
  1. 增加数据库唯一约束 `(project_id, version)`。
  2. 创建版本时使用项目行锁、数据库序列或冲突重试，而不是单纯查询 max。
  3. 课程蓝图版本也采用同样的并发安全策略。
  4. 增加并发创建版本的集成测试，确认只产生唯一连续版本。

## 5. P2：功能与性能问题

### P2-01 eager 模式下生成接口返回的任务状态可能过时

- **代码章节**：[backend/routers/courseware.py:143-159](../backend/routers/courseware.py#L143)、[backend/services/task_queue.py:180-209](../backend/services/task_queue.py#L180)
- **问题**：路由先创建并返回 `TaskInfo`，eager 调度随后在另一个数据库会话中同步完成任务；原来的响应对象不会刷新。
- **影响**：任务实际已完成时，HTTP 响应仍可能显示 `pending`，前端必须再次轮询才能看到真实状态。
- **解决方案**：生产禁用 eager；保留开发模式时，eager 执行后重新查询任务并返回最新状态，或明确在 API 契约中标记同步模式。

### P2-02 反馈接口只返回成功，不保存反馈

- **代码章节**：[backend/routers/generate.py:99-108](../backend/routers/generate.py#L99)
- **问题**：接口验证任务存在后直接返回 `feedback_received`，没有模型、表或审计记录。
- **影响**：用户反馈无法追踪、统计或触发重新生成，成功响应具有误导性。
- **解决方案**：增加 `feedback` 表，保存任务、用户、评分/文本、时间和版本；增加管理员查询和幂等键，并补充持久化测试。

### P2-03 视频功能对外宣称支持，但后端直接拒绝

- **代码章节**：[backend/routers/materials.py:95-101](../backend/routers/materials.py#L95)、[backend/services/materials.py:106-121](../backend/services/materials.py#L106)
- **问题**：前端和上传类型接受视频扩展名，后端返回 `VIDEO_PARSING_DEFERRED`；旧解析器也只读取时长，没有转写或关键帧分析。
- **影响**：用户以为视频可作为参考资料，实际上传失败。
- **解决方案**：短期从前端和接口声明中移除视频；长期实现 ffprobe、音频转写、关键帧提取和异步解析，并明确状态流转。

### P2-04 需求确认单保存会丢失知识点细节

- **代码章节**：[frontend/src/components/chat/TeachingBriefPanel.vue:96-117](../frontend/src/components/chat/TeachingBriefPanel.vue#L96)
- **问题**：加载时只显示知识点标题，保存时重新生成 `{order, title}`，会丢弃 `key_points`、`examples`、`difficulty` 和 `estimated_minutes`。
- **影响**：用户编辑一次确认单后，结构化教学信息被不可逆地简化。
- **解决方案**：前端保留原始知识点对象并按稳定 ID 更新；或设计明确的编辑器来维护所有字段；保存前后增加字段完整性断言。

### P2-05 `/requirements` 仍是旧 mock 页面

- **代码章节**：[frontend/src/views/RequirementsView.vue:103-111](../frontend/src/views/RequirementsView.vue#L103)
- **问题**：页面使用 `mocks` 数据，没有 API 请求，输入和多个 AI 操作按钮是禁用状态。
- **影响**：用户进入该路由会看到看似可用但实际不工作的原型，和真实 Chat/Brief 流程重复且容易造成误导。
- **解决方案**：将路由重定向到真实 Chat/Brief 页面，或彻底接入 brief API；删除 mock 数据和永久禁用按钮。

### P2-06 RAG 搜索在 async 路由内同步执行

- **代码章节**：[backend/services/rag.py:20-30](../backend/services/rag.py#L20)、[backend/routers/courseware.py:75-93](../backend/routers/courseware.py#L75)
- **问题**：`search()` 虽然声明为 async，但内部直接调用同步 Chroma 和 embedding 搜索，首次调用还可能加载模型。
- **影响**：会阻塞 FastAPI 事件循环，降低同一 worker 对聊天和 API 请求的响应能力。
- **解决方案**：使用 `run_in_threadpool` 或专用任务 worker 执行同步检索；启动时预热模型；设置超时、并发上限和缓存；增加并发响应时间测试。

### P2-07 Chroma 重建锁只在单进程内有效

- **代码章节**：[backend/services/knowledge.py:18](../backend/services/knowledge.py#L18)、[backend/services/knowledge.py:319-324](../backend/services/knowledge.py#L319)
- **问题**：使用 `threading.Lock`，多 API worker 或多容器之间并不共享该锁；`build_index()` 会删除并重建同一个 collection。
- **影响**：并发索引重建可能互相删除或写入，造成索引不完整或损坏。
- **解决方案**：将重建操作放入单独 worker，使用 Redis/数据库分布式锁；采用临时 collection 构建完成后原子切换；保留旧索引直到新索引通过校验。

### P2-08 验证错误日志可能记录敏感请求体

- **代码章节**：[backend/core/errors.py:91-108](../backend/core/errors.py#L91)
- **问题**：验证失败时将请求体前 500 字节写入日志。
- **影响**：登录请求可能包含密码，聊天或资料请求可能包含教师隐私和教学内容。
- **解决方案**：只记录字段名、错误码和 request id；对密码、token、文件内容和消息正文做脱敏；增加日志安全检查。

## 6. P3：安全加固与维护问题

### P3-01 默认 CORS 和 JWT 配置不适合生产

- **代码章节**：[backend/config.py:40-49](../backend/config.py#L40)、[backend/main.py:78-84](../backend/main.py#L78)
- **问题**：默认允许所有 Origin，同时允许 credentials；JWT 默认密钥是开发占位值。
- **影响**：错误部署配置时可能导致跨域策略过宽，默认密钥还可能使令牌被伪造。
- **解决方案**：生产启动时强制要求非默认 JWT 密钥和明确的 Origin 白名单；当 `allow_credentials=true` 时禁止 `*`；加入配置自检测试。

### P3-02 短 ID 不应承担授权职责

- **代码章节**：[backend/models/session.py:9-11](../backend/models/session.py#L9)、[backend/models/versioning.py:16](../backend/models/versioning.py#L16)
- **问题**：多类资源使用 UUID 前 8 位，约 32 bit 空间。
- **影响**：在 ID 泄露或接口存在枚举时，猜测成本较低。
- **解决方案**：使用完整 UUID/UUIDv7 或更长随机 ID；授权始终依赖用户、项目或 capability token，而不是依赖 ID 不可猜。

### P3-03 缺少浏览器 E2E 验证

- **验证章节**：前端 Playwright 配置与测试目录。
- **问题**：本次无法执行 Playwright Chromium，原因是本机缺少 `chrome-headless-shell.exe`。
- **影响**：尚未验证登录、路由守卫、SSE、上传、任务轮询和下载的真实浏览器行为。
- **解决方案**：在 CI 安装固定版本 Chromium，执行桌面和移动视口 E2E；至少覆盖登录、Chat SSE、确认 brief、生成任务、导出下载和权限拒绝。

## 7. 实测结果

| 检查项 | 结果 | 说明 |
| --- | --- | --- |
| 后端 pytest | 失败：25 passed, 4 failed | 1 个真实业务失败，3 个测试/环境契约问题 |
| `test_project_messages_persist_sse_events_and_brief` | 失败 | DeepSeek 无效模型导致 teaching goal 为空 |
| Chroma 空集合测试 | 失败 | Windows 非 ASCII 临时路径被归一化后，异常文案与测试期望不一致 |
| 两个版本/导出测试 | 失败 | 测试 monkeypatch 未接受当前 `raise_errors` 参数 |
| 前端 `npm run build` | 通过 | 最大 JS chunk 约 1.2 MB，gzip 约 404 KB |
| `ruff check .` | 通过 | 静态检查无错误 |
| Python 编译检查 | 通过 | `compileall` 通过 |
| PPTX/DOCX/HTML 生成器测试 | 通过 | 文件可生成且非空 |
| 后端 `/health` | `degraded` | database/chroma 正常，Redis 未启动 |
| Docker Compose config | 通过 | 仅证明 YAML/变量可展开，不等于容器已成功启动 |
| 浏览器 E2E | 未执行 | 本机缺少 Playwright Chromium |

## 8. 推荐修复顺序

1. 修复 P0-01，确认真实 LLM 配置和项目聊天主流程。
2. 修复 P1-01 至 P1-03，关闭匿名生产能力并补齐限流、配额和流式上传。
3. 修复 P1-07 至 P1-12，重新验证开发和生产 Compose，轮换可能暴露的 API Key。
4. 修复 P1-04 至 P1-06 和 P1-13，保证参考资料、图片解析和版本数据一致性。
5. 修复 P2 功能问题，删除或接通 mock 页面，并补齐 RAG、反馈、视频和 brief 字段测试。
6. 安装 Chromium 执行 E2E；所有 P0/P1 验收通过后再进行生产发布。

## 9. 发布前检查清单

- [ ] DeepSeek 意图模型和视觉模型均为账号实际支持的模型。
- [ ] 生产环境 `TASK_QUEUE_EAGER=false`，Redis 和 Celery worker 健康。
- [ ] 生产 API、worker 共享 uploads、outputs 和知识库运行时目录。
- [ ] 匿名接口已关闭，或已具备 capability token、限流和配额。
- [ ] 上传和语音接口采用流式大小限制，临时文件使用随机目录。
- [ ] API Key 已轮换并通过 secret 管理，日志和文档无密钥内容。
- [ ] CORS 白名单和 JWT 密钥已通过启动自检。
- [ ] 版本号和蓝图版本具有数据库级并发保护。
- [ ] Chat、Brief、生成、导出、下载全链路 E2E 通过。
