# M6 P0/P1 验收矩阵

更新时间：2026-09-10

本文记录当前 `easy-teach-dev` 工作区的验收状态。`通过` 只表示已有自动化或真实本地烟测证据；`部分覆盖` 表示实现存在但仍缺少真实环境、人工或跨软件证据；`未覆盖` 不作为本轮完成项。

## P0

| 能力 | 状态 | 当前证据 | 剩余风险或缺口 |
| --- | --- | --- | --- |
| 文字对话、主动追问、TeachingBrief 确认 | 通过 | `tests/test_m2_brief.py`、Playwright 主流程 | 暂无 P0 缺口 |
| 语音输入与失败降级 | 部分覆盖 | `/api/v1/speech/transcribe` 路由和解析器 | 未完成真实录音、无声、噪音、超时回归 |
| PDF/DOCX/PPTX 资料解析；图片/视频能力边界 | 通过 | `tests/test_m3_materials.py`；图片明确标记未配置视觉，视频默认由前后端共同关闭 | 后续启用图片或视频模型时必须作为独立里程碑重新验收 |
| 本地知识库、向量检索、来源定位 | 通过 | 教师私有文本真实上传并建立独立 Chroma collection；唯一标识和语义查询均返回文档 ID、来源、`offset:0` 定位；第二教师列表/搜索为 0 且直接访问返回 404 | 生产数据卷与备份恢复仍需目标服务器演练 |
| TeachingBrief + Evidence -> CoursewarePlan | 通过 | `courseware-plan-v14-reviewed-normalized` 使用生成与独立审校两轮 AI；20 条跨学科真实 DeepSeek 样本结构通过 20/20、人工通过 20/20，平均 99.6、最低 96；私有 RAG 证据链通过 | 教师仍应对高风险学科事实做最终确认 |
| PPTX、DOCX、PDF、HTML 四种成果生成 | 通过 | 真实语文 AI 版本导出 PPTX 8 页且每页有讲稿、DOCX 63 段、PDF 7 页、HTML 含答案分组/提交/重做/CSP；用户已完成 Office/WPS 人工检查 | 暂无 P0 缺口 |
| 成果预览与下载 | 通过 | Playwright 模板闭环和真实 AI 四格式下载均验证签名、记录大小及课程正文；用户已人工打开真实文件 | 暂无 P0 缺口 |
| 指定页/章节局部修改、版本不可变、回滚 | 通过 | `tests/test_m5_versioning.py`、`tests/test_revision_ai.py`；真实 `deepseek-v4-flash` 页面、章节和互动题重生成均通过，记录 `artifact-target-v2` 与 token 用量，旧版本和无关内容保持不变；空修改会修复或拒绝 | 暂无多人并发修改压力测试 |
| 任务队列、幂等、进度、重试、过期恢复 | 通过 | `tests/test_m6_queue.py`；独立 SQLite、真实 Redis/Celery 下完成生成与导出，并演练 worker 停止、stale 重派、重启后完成 | 尚未在 Linux 生产容器中演练硬超时和主机重启 |
| 生产 Compose、备份与恢复 | 部分覆盖 | 生产容器健康；2026-09-10 已生成并校验数据库、应用数据、`.env`、secrets 与部署配置备份；`scripts/backup.sh`、`scripts/restore.sh` 已通过语法检查 | 尚未在隔离栈使用生产备份副本执行完整恢复演练 |

## P1

| 能力 | 状态 | 当前证据 | 剩余风险或缺口 |
| --- | --- | --- | --- |
| 混合检索 | 通过 | `ai/rag/retriever.py` 的向量 0.7 + 关键词 0.3；固定回归 | 尚未完成独立 reranker 评测 |
| 自动质量检查 | 通过 | `backend/services/quality.py`、独立 AI 审校、互动答案归一化和 20 条人工复核报告 | 模型审校不能替代教师对新增学科的专业审核 |
| 版本恢复 | 通过 | `tests/test_m5_versioning.py` | 尚无可视化版本 diff |
| 多模板与风格记忆 | 未覆盖 | 当前只有基础确定性模板 | 不纳入本轮 M6 收尾 |
| 配对/排序/选择题/分类互动扩展 | 部分覆盖 | 固定安全互动引擎已支持四类判题、计分、反馈、解析和重置 | 仍需补四类独立浏览器交互验收 |

## 本轮验证命令

```powershell
uv run pytest -q
uv run ruff check backend tests ai gen
uv run alembic check
uv lock --check
cd frontend
pnpm run build
pnpm run test:e2e
```

## 当前结论

文字主链路、私有知识库 RAG、不可变版本、四格式导出、真实队列恢复、本地浏览器闭环、生产真实 DeepSeek 蓝图、生产四格式下载以及跨账号隔离均已通过。2026-09-10 已部署并完成生产 API 验收；当前 P0 为 0。剩余发布运维风险为 HTTPS/域名、防火墙、容器非 root、隔离恢复演练、Chroma 上游安全公告和日志治理。图片、视频和语音不纳入本轮文字版承诺，视频能力继续关闭。

## 发布前人工门槛

- [x] 已在 DeepSeek 平台撤销沟通中暴露过的 Key，创建发布候选 Key；本地通过 Secret 文件注入，生产继续使用 Docker secret。
- [x] 发布候选 Secret 已完成 20 条跨学科真实 AI 蓝图、人工质量评分、页面/章节/互动题局部重生成及四格式真实下载。
- [x] 已使用 Office/WPS 人工检查真实 AI PPTX、DOCX、PDF，并检查互动 HTML。
- [ ] 在隔离栈使用生产备份副本执行 `alembic upgrade head`，验证备份恢复与应用回滚。
- [ ] 配置域名与 HTTPS，验证登录、SSE、文件下载、限流和跨账号隔离。

## 2026-09-10 生产验收补充

- 后端完整测试：120 passed。
- 本地教师端 Playwright 主流程：1 passed；管理员流程因未提供专用测试凭据而 skipped。
- 前端生产构建通过；生产依赖审计为 0 漏洞。开发构建链仍有 2 个 Vite/esbuild 告警，运行时 Web 镜像仅包含 Nginx 静态文件。
- 真实 DeepSeek v15 结构化烟测通过；生产真实蓝图生成通过。为控制费用，修复导出问题后的复验使用模板蓝图，不重复调用模型。
- 生产 PPTX、DOCX、PDF、互动 HTML 均通过文件签名、校验和、下载及幂等检查。
- 第二教师对第一教师的项目、蓝图、版本、导出和知识库访问全部返回 404。
- 公网 `/.env`、`/.git/config`、`/openapi.json`、`/docs`、`/redoc` 与 SSRF 探测路径均返回 404；`/health` 只返回组件状态，不返回内部地址或错误细节。
- 2026-09-11 已完整删除学业帮扶平台的容器、MySQL 数据卷、网络和项目目录；删除前已建立受限恢复归档。为清除旧入口遗留的浏览器缓存，`/academic-support` 路径在过渡期跳转到带版本参数的 Easy Teach 首页；SPA HTML 禁止缓存，真实 Chromium 已验证最终进入 Easy Teach 登录页。
- 需求确认卡已补齐新版字段中文映射与输出格式中文名称，并增加长文本防重叠布局；资料中心证据片段改为逐行横条布局。Playwright 已验证中文标签、标签和值不重叠，以及证据条目同宽纵向排列。
- 当前剩余计数：P0 0、P1 6、P2 5；明细见 `docs/PROJECT_REMEDIATION_PLAN.md` 第 11 节。

## 本地真实模型验收产物

- 20 条质量报告：`data/acceptance/ai-quality-20/summary.md` 与 `summary.json`，最终提示词 `courseware-plan-v14-reviewed-normalized`，通过率 100%。

- PPTX：`data/acceptance/rc-red-cliff-v4.pptx`，8 页，8 个讲稿页。
- DOCX：`data/acceptance/rc-red-cliff-v4.docx`，63 段正文。
- PDF：`data/acceptance/rc-red-cliff-v4.pdf`，7 页。
- HTML：`data/acceptance/rc-red-cliff-v4.html`，包含答案分组、提交、重做和 CSP。
