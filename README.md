# easy-teach — 多模态AI互动式教学智能体

基于 FastAPI + Vue 3 的多模态AI教学课件生成平台。

## 技术栈

| 层级  | 技术                          |
| --- | --------------------------- |
| 前端  | Vue 3 + Vite + Element Plus |
| 后端  | FastAPI (Python 3.11)       |
| 数据库 | SQLite（开发）→ PostgreSQL（上线）  |
| LLM | DeepSeek-V3 API             |
| 向量库 | ChromaDB                    |
| RAG | LangChain                   |
| 语音  | faster-whisper              |
| 文档  | python-pptx / python-docx   |

## 快速开始

### 后端

```bash
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

## 项目结构

```
easy-teach-app/
├── backend/          # FastAPI 后端
│   ├── api/          # 路由控制器
│   ├── models/       # Pydantic 数据模型
│   ├── services/     # 业务逻辑层
│   └── db/           # 数据库配置
├── frontend/         # Vue 3 前端
│   └── src/
│       ├── views/    # 页面视图
│       ├── components/  # 通用组件
│       ├── api/      # 后端 API 封装
│       └── stores/   # Pinia 状态管理
└── knowledge-base/   # 知识库资料目录
```
