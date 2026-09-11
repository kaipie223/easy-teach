# M6 部署与恢复

## 本地开发

本地开发继续使用服务器 PostgreSQL 的 SSH 隧道和本机 Redis：

```powershell
ssh -N -L 15432:127.0.0.1:5432 -i "$env:USERPROFILE\.ssh\id_ed25519" ubuntu@124.222.207.47
uv run alembic upgrade head
uv run uvicorn backend.main:app --reload --port 8000
uv run celery -A backend.celery_app.celery_app worker --loglevel=INFO --pool=solo
```

不要把服务器数据库密码提交到 `.env.example` 或代码。当前 worktree 的 `.env`
只属于本机开发配置。

## Compose 部署

生产环境不再从 `.env` 向容器注入 DeepSeek Key、JWT Secret 或数据库密码。
先在服务器创建仅部署账号可读的 secret 文件：

```bash
install -d -m 700 secrets
printf '%s' '新轮换的 DeepSeek Key' > secrets/deepseek_api_key.txt
openssl rand -hex 48 > secrets/jwt_secret_key.txt
openssl rand -base64 36 > secrets/postgres_password.txt
chmod 600 secrets/*.txt
```

`.env` 只保存非敏感配置和 secret 文件路径：

```dotenv
DEEPSEEK_API_KEY_SECRET_FILE=./secrets/deepseek_api_key.txt
JWT_SECRET_KEY_SECRET_FILE=./secrets/jwt_secret_key.txt
POSTGRES_PASSWORD_SECRET_FILE=./secrets/postgres_password.txt
ALLOWED_ORIGINS=["https://你的正式域名"]
```

当前曾在沟通中出现过的 DeepSeek Key 必须先在平台撤销并重新生成，不能写入上述文件继续使用。

生产 Compose 文件会启动 PostgreSQL、Redis、API、Celery worker、Vue Web 和 Nginx：

```bash
docker compose -f docker-compose.production.yml up -d --build
docker compose -f docker-compose.production.yml ps
curl http://127.0.0.1/health
```

API 和 worker 共用 `/app/data` 持久化卷，生成文件、上传资料和 Chroma 数据不会
随着容器重建丢失。Nginx 将 `/api/` 转发到 API，其余请求转发到 Web，并关闭 API
响应缓冲以避免长任务状态接口被缓存。

生产配置会在 API、worker 和 Alembic 导入配置时自检。以下任一情况会拒绝启动：
`DEBUG=true`、`ALLOWED_ORIGINS=*`、弱 JWT、缺少 DeepSeek Key、SQLite、eager 队列，
或上传/生成/Chroma 目录不在 `/app/data` 下。

## 备份和恢复

```bash
./scripts/backup.sh
CONFIRM_RESTORE=YES ./scripts/restore.sh backups/<timestamp>
```

备份目录包含 `postgres.sql` 和 `data.tar.gz`。恢复前应停止 API 和 worker，确认
备份目录来自同一环境；恢复脚本会清理并重新导入数据库和应用数据卷，因此默认
要求显式的 `CONFIRM_RESTORE=YES`。

## 运行检查

- `/health` 必须报告 database、redis 为 `ok`；Chroma 可以是 `ok` 或 `not_indexed`。
- 所有会话、聊天、上传、语音、生成、任务状态和下载接口都必须携带 Bearer Token。
- API 默认按 IP 和登录令牌限制每分钟请求量；AI 日额度、排队任务数和个人存储空间
  可通过 `RATE_LIMIT_*`、`DAILY_MODEL_REQUEST_LIMIT`、
  `MAX_CONCURRENT_TASKS_PER_USER` 和 `STORAGE_QUOTA_MB_PER_USER` 调整。
- `VIDEO_PARSER_ENABLED=false` 是当前发布默认值，API 会以
  `VIDEO_CAPABILITY_DISABLED` 拒绝视频上传。后续启用时，后端镜像才必须包含
  `ffmpeg`/`ffprobe` 并完成独立视频验收。
- `docker compose ... logs worker` 应出现 `easy_teach.generate`、`easy_teach.export` 或 `easy_teach.parse_material`。
- 任务状态从 `pending` 到 `processing` 再到 `completed`/`failed`；失败时检查
  `error_code` 和 `retry_count`，不要直接改写任务记录。
- 图片当前只保存原文件，分析结果标记 `vision_status=not_configured`，不得宣称已完成视觉识别。
- 导出记录必须绑定 `artifact_version_id`，旧版本下载不能被新版本覆盖。
