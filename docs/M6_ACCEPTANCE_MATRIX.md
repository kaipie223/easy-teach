# M6 P0/P1 验收矩阵

更新时间：2026-08-20

本文记录当前 `easy-teach-dev` 工作区的验收状态。`通过` 只表示已有自动化或真实本地烟测证据；`部分覆盖` 表示实现存在但仍缺少真实环境、人工或跨软件证据；`未覆盖` 不作为本轮完成项。

## P0

| 能力 | 状态 | 当前证据 | 剩余风险或缺口 |
| --- | --- | --- | --- |
| 文字对话、主动追问、TeachingBrief 确认 | 通过 | `tests/test_m2_brief.py`、Playwright 主流程 | 未覆盖 20-30 条 AI 质量样本 |
| 语音输入与失败降级 | 部分覆盖 | `/api/v1/speech/transcribe` 路由和解析器 | 未完成真实录音、无声、噪音、超时回归 |
| PDF/DOCX/PPTX/图片/视频资料解析 | 部分覆盖 | `tests/test_m3_materials.py`、本地解析器 | 视频真实关键帧/时间戳案例尚未纳入固定烟测 |
| 本地知识库、向量检索、来源定位 | 部分覆盖 | TCP 真实本地索引命中教材页码；`tests/test_m6_fixed_cases.py` 离线固定 RAG | 红黄蓝案例目前使用离线固定证据，未进入共享知识库 |
| TeachingBrief + Evidence -> CoursewarePlan | 通过 | `tests/test_m4_courseware.py`、固定 TCP/红黄蓝蓝图回归 | 仍是确定性编译路径，未验证外部模型响应 |
| PPTX、DOCX、HTML 三种成果生成 | 部分覆盖 | 固定案例结构回归；PPTX 已渲染并通过溢出检查 | DOCX 当前环境缺少 LibreOffice，无法完成 PNG 视觉验收；固定案例页数尚未达到 PRD 约定的 TCP 10 页/红黄蓝 8 页 |
| 成果预览与下载 | 通过 | Playwright 主流程、真实 HTTP 生成/下载烟测 | 尚未在 Office 与 WPS 各人工打开一次 |
| 指定页/章节局部修改、版本不可变、回滚 | 通过 | `tests/test_m5_versioning.py` | 暂无多人并发修改压力测试 |
| 任务队列、幂等、进度、重试、过期恢复 | 通过 | `tests/test_m6_queue.py`、Celery/Redis HTTP 烟测 | 未做 worker 进程崩溃和真实超时演练 |
| 生产 Compose、备份与恢复 | 部分覆盖 | `docker-compose.production.yml`、`scripts/backup.sh`、`scripts/restore.sh` | 尚未在目标服务器执行完整 Compose 实机恢复演练 |

## P1

| 能力 | 状态 | 当前证据 | 剩余风险或缺口 |
| --- | --- | --- | --- |
| 混合检索 | 通过 | `ai/rag/retriever.py` 的向量 0.7 + 关键词 0.3；固定回归 | 尚未完成独立 reranker 评测 |
| 自动质量检查 | 通过 | `backend/services/quality.py`、版本质量报告 | 当前输出确定性错误/警告，无自动修改建议 |
| 版本恢复 | 通过 | `tests/test_m5_versioning.py` | 尚无可视化版本 diff |
| 多模板与风格记忆 | 未覆盖 | 当前只有基础确定性模板 | 不纳入本轮 M6 收尾 |
| 配对/排序/选择题/分类互动扩展 | 部分覆盖 | 当前分类模板和排序语义可生成 | 仍缺少四种独立模板及前端验收 |

## 本轮验证命令

```powershell
uv run pytest -q
uv run ruff check backend ai tests
uv run alembic check
uv lock --check
cd frontend; npm run build; npm run test:e2e
```

## 当前结论

队列、版本、蓝图和三种产物的应用链路已具备继续演示的基础，M6 仍不能标记为“所有 P0 关闭”：DOCX 视觉渲染、视频真实固定案例、语音真实回归、红黄蓝共享知识库数据和 Office/WPS 人工打开仍是发布前门槛。
