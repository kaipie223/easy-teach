# easy-teach — 多模态AI互动式教学智能体

基于 FastAPI + Vue 3 的多模态 AI 教学课件生成平台。

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | Vue 3 + Vite + Element Plus |
| 后端 | FastAPI (Python 3.11) |
| 数据库 | PostgreSQL（开发/上线）或 SQLite 兼容模式 |
| 任务 | Celery + Redis |
| LLM | DeepSeek-V3 API |
| 向量库 | ChromaDB |
| RAG | LangChain |
| 语音 | faster-whisper |
| 文档 | python-pptx / python-docx |

## 快速开始

### 后端

```bash
uv sync
uv run alembic upgrade head
uv run uvicorn backend.main:app --reload --port 8000
```

另开一个终端启动长任务 worker（Windows 本地开发使用 `solo` 池）：

```bash
uv run celery -A backend.celery_app.celery_app worker --loglevel=INFO --pool=solo
```

worker 未启动时，生成和导出接口会明确返回 `TASK_QUEUE_UNAVAILABLE` 或任务失败，
不会把长任务挂在 FastAPI 请求进程中。生产环境可直接使用
`docker-compose.production.yml`，其中包含 API、worker、Redis、PostgreSQL、Web 和 Nginx。

首次构建本地知识库：

```bash
uv run python -m ai.build_kb
```

API 健康检查：`http://localhost:8000/health`。运行目录、SQLite 数据库、
生成文件和 Chroma 索引统一位于 `data/`，可通过 `.env` 覆盖。

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端唯一产品工程是 `frontend/`，默认通过 Vite 代理访问 `/api/v1`。

### 生产部署和备份

```bash
docker compose -f docker-compose.production.yml up -d --build
./scripts/backup.sh
```

备份脚本同时保存 PostgreSQL dump 和应用数据卷。恢复脚本默认拒绝执行，必须显式设置
`CONFIRM_RESTORE=YES`，避免误覆盖线上数据；详见 `docs/DEPLOYMENT_M6.md`。

## 项目结构

```
easy-teach-dev/
├── backend/             # FastAPI 入口、路由、模型和服务
├── ai/                  # 意图、语音和 RAG 能力
├── frontend/            # 唯一的 Vue 3 产品前端
│   └── src/
│       ├── views/       # 页面视图
│       ├── components/  # 页面和业务组件
│       ├── api/         # 后端 API 封装
│       └── stores/      # Pinia 状态管理
├── alembic/             # 数据库迁移
├── tests/               # API 和契约测试
└── data/knowledge/      # 按教师账号隔离的知识库文件（运行时生成）
```
