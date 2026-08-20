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

生产 Compose 文件会启动 PostgreSQL、Redis、API、Celery worker、Vue Web 和 Nginx：

```bash
docker compose -f docker-compose.production.yml up -d --build
docker compose -f docker-compose.production.yml ps
curl http://127.0.0.1/health
```

API 和 worker 共用 `/app/data` 持久化卷，生成文件、上传资料和 Chroma 数据不会
随着容器重建丢失。Nginx 将 `/api/` 转发到 API，其余请求转发到 Web，并关闭 API
响应缓冲以避免长任务状态接口被缓存。

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
- `docker compose ... logs worker` 应出现 `easy_teach.generate` 或 `easy_teach.export`。
- 任务状态从 `pending` 到 `processing` 再到 `completed`/`failed`；失败时检查
  `error_code` 和 `retry_count`，不要直接改写任务记录。
- 导出记录必须绑定 `artifact_version_id`，旧版本下载不能被新版本覆盖。
