import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.config import Settings, settings
from backend.core.errors import ApiError
from backend.models.session import gen_id
from backend.models.task import Task
from backend.models import EvidenceChunk, KnowledgeDocument, Material, MaterialAnalysis, Project, User
from backend.services.courseware import _build_evidence_refs
from backend.services.limits import (
    consume_model_quota,
    consume_video_parse_quota,
    ensure_task_capacity,
    reset_local_limits,
)


def register(client, email: str = "security-limits@example.com"):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "安全测试"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload, {"Authorization": f"Bearer {payload['access_token']}"}


def test_request_rate_limit_returns_retry_after(client, monkeypatch):
    reset_local_limits()
    monkeypatch.setattr(settings, "rate_limit_auth_requests_per_minute", 1)

    first = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "password123"},
    )
    second = client.post(
        "/api/v1/auth/login",
        json={"email": "missing@example.com", "password": "password123"},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "REQUEST_RATE_LIMITED"
    assert int(second.headers["Retry-After"]) >= 1
    reset_local_limits()


def test_security_headers_and_request_id_sanitization(client):
    valid = client.get("/", headers={"X-Request-ID": "qa-request-123"})
    assert valid.headers["X-Request-ID"] == "qa-request-123"
    assert valid.headers["X-Content-Type-Options"] == "nosniff"
    assert valid.headers["X-Frame-Options"] == "DENY"
    assert valid.headers["Referrer-Policy"] == "no-referrer"

    invalid = client.get("/", headers={"X-Request-ID": "x" * 1024})
    assert invalid.headers["X-Request-ID"] != "x" * 1024
    assert len(invalid.headers["X-Request-ID"]) == 36


def test_production_health_check_hides_internal_details(client, monkeypatch):
    import backend.main as main_module

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(
        main_module,
        "_dependency_checks",
        lambda: {
            "database": {"status": "ok", "engine": "postgresql"},
            "redis": {"status": "error", "message": "private-host:6379 unavailable"},
            "chroma": {"status": "not_indexed", "path": "/app/data/chroma"},
        },
    )

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["checks"] == {
        "database": {"status": "ok"},
        "redis": {"status": "error"},
        "chroma": {"status": "not_indexed"},
    }


def test_generated_resource_ids_have_at_least_96_bits_of_randomness():
    generated = {gen_id("evidence") for _ in range(100)}
    assert len(generated) == 100
    assert all(len(value.removeprefix("evidence_")) == 24 for value in generated)


def test_daily_model_quota_is_enforced(monkeypatch):
    reset_local_limits()


def test_daily_video_parse_quota_is_enforced(monkeypatch):
    reset_local_limits()
    monkeypatch.setattr(settings, "video_parser_daily_user_limit", 1)

    consume_video_parse_quota("u_video_quota_test")
    with pytest.raises(ApiError) as exc_info:
        consume_video_parse_quota("u_video_quota_test")

    assert exc_info.value.code == "VIDEO_DAILY_QUOTA_EXCEEDED"
    assert exc_info.value.status_code == 429
    reset_local_limits()
    monkeypatch.setattr(settings, "daily_model_request_limit", 1)

    consume_model_quota("u_quota_test")
    with pytest.raises(ApiError) as exc_info:
        consume_model_quota("u_quota_test")

    assert exc_info.value.code == "MODEL_DAILY_QUOTA_EXCEEDED"
    assert exc_info.value.status_code == 429
    reset_local_limits()


def test_task_capacity_counts_pending_work(db_session_factory, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_tasks_per_user", 1)
    db = db_session_factory()
    try:
        task = Task(
            task_id="task_capacity",
            user_id="u_capacity",
            session_id="s_capacity",
            task_type="generation",
            status="pending",
        )
        db.add(task)
        db.commit()

        with pytest.raises(ApiError) as exc_info:
            ensure_task_capacity(db, "u_capacity")
        assert exc_info.value.code == "TASK_CONCURRENCY_LIMIT_EXCEEDED"

        task.status = "completed"
        db.commit()
        ensure_task_capacity(db, "u_capacity")
    finally:
        db.close()


def test_task_capacity_counts_pending_video_analysis(db_session_factory, monkeypatch):
    monkeypatch.setattr(settings, "max_concurrent_tasks_per_user", 1)
    db = db_session_factory()
    try:
        material = Material(
            material_id="mat_capacity_video",
            owner_id="u_capacity_video",
            original_name="lesson.mp4",
            file_type="video",
            stored_path="managed/lesson.mp4",
            size_bytes=20,
            checksum_sha256="d" * 64,
            status="queued",
        )
        analysis = MaterialAnalysis(
            analysis_id="analysis_capacity_video",
            material_id=material.material_id,
            run_number=1,
            parser_name="video-parser-model",
            status="pending",
        )
        db.add_all([material, analysis])
        db.commit()

        with pytest.raises(ApiError) as exc_info:
            ensure_task_capacity(db, "u_capacity_video")
        assert exc_info.value.code == "TASK_CONCURRENCY_LIMIT_EXCEEDED"

        analysis.status = "completed"
        db.commit()
        ensure_task_capacity(db, "u_capacity_video")
    finally:
        db.close()


def test_oversized_material_upload_removes_partial_file(client, tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(settings, "upload_dir", upload_dir)
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)
    _, headers = register(client, "stream-limit@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "流式上传限制"},
    ).json()

    response = client.post(
        f"/api/v1/projects/{project['project_id']}/materials",
        headers=headers,
        files={"file": ("large.pdf", b"%PDF-content", "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "MATERIAL_TOO_LARGE"
    assert not [path for path in upload_dir.rglob("*") if path.is_file()]


def test_legacy_upload_rejects_unconfigured_visual_material(client):
    _, headers = register(client, "legacy-image-disabled@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "视觉能力关闭"},
    ).json()
    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": "图片", "project_id": project["project_id"]},
    ).json()

    response = client.post(
        "/api/v1/upload",
        headers=headers,
        files={"file": ("diagram.png", b"not-used", "image/png")},
        data={"session_id": session["session_id"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_format"


def _secure_production_settings(tmp_path: Path) -> dict:
    data_dir = tmp_path / "data"
    return {
        "_env_file": None,
        "environment": "production",
        "debug": False,
        "allowed_origins": ["https://teach.example.com"],
        "deepseek_api_key": "sk-production-placeholder-for-validation",
        "jwt_secret_key": "a-secure-production-jwt-secret-with-32-chars",
        "database_url": "postgresql+psycopg://user:pass@db/easy_teach",
        "task_queue_enabled": True,
        "task_queue_eager": False,
        "data_dir": data_dir,
        "upload_dir": data_dir / "uploads",
        "GENERATED_DIR": data_dir / "generated",
        "chroma_persist_dir": data_dir / "chroma",
    }


def test_production_configuration_rejects_unsafe_defaults(tmp_path):
    values = _secure_production_settings(tmp_path)
    values.update(
        {
            "debug": True,
            "allowed_origins": ["*"],
            "jwt_secret_key": "short",
            "task_queue_eager": True,
        }
    )
    with pytest.raises(ValidationError, match="Unsafe production configuration"):
        Settings(**values)


def test_secure_production_configuration_is_accepted(tmp_path):
    configured = Settings(**_secure_production_settings(tmp_path))
    assert configured.environment == "production"
    assert configured.upload_dir.is_relative_to(configured.data_dir)


def test_production_secret_files_override_plain_values(tmp_path):
    deepseek_file = tmp_path / "deepseek.txt"
    jwt_file = tmp_path / "jwt.txt"
    database_file = tmp_path / "database.txt"
    deepseek_file.write_text("sk-from-docker-secret", encoding="utf-8")
    jwt_file.write_text("jwt-secret-from-file-that-is-long-enough", encoding="utf-8")
    database_file.write_text("db-password@from-file", encoding="utf-8")
    values = _secure_production_settings(tmp_path)
    values.update(
        {
            "deepseek_api_key": "",
            "deepseek_api_key_file": deepseek_file,
            "jwt_secret_key": "short",
            "jwt_secret_key_file": jwt_file,
            "database_url": "sqlite:///unsafe.db",
            "database_password_file": database_file,
        }
    )

    configured = Settings(**values)

    assert configured.deepseek_api_key == "sk-from-docker-secret"
    assert configured.jwt_secret_key == "jwt-secret-from-file-that-is-long-enough"
    assert "db-password%40from-file" in configured.database_url


def test_courseware_does_not_scan_another_teacher_knowledge(db_session_factory):
    db = db_session_factory()
    try:
        alice = User(
            user_id="u_evidence_alice",
            email="evidence-alice@example.com",
            display_name="Alice",
            password_hash="hash",
        )
        bob = User(
            user_id="u_evidence_bob",
            email="evidence-bob@example.com",
            display_name="Bob",
            password_hash="hash",
        )
        project = Project(
            project_id="p_evidence_alice",
            owner_id=alice.user_id,
            title="Alice project",
            scenario="",
        )
        document = KnowledgeDocument(
            document_id="kb_evidence_bob",
            owner_id=bob.user_id,
            collection_id="default",
            title="Bob private document",
            source_path="knowledge/u_bob/private.txt",
            file_type="text",
            checksum_sha256="b" * 64,
            enabled=True,
            index_status="ready",
        )
        evidence = EvidenceChunk(
            evidence_id="evidence_bob_private",
            knowledge_document_id=document.document_id,
            source_type="knowledge_text",
            text="Bob only content",
            content_hash="c" * 64,
            is_valid=True,
        )
        db.add_all([alice, bob, project, document, evidence])
        db.commit()

        refs = _build_evidence_refs(db, project.project_id, [])

        assert refs == []
    finally:
        db.close()


def test_deployment_backup_and_restore_keep_secrets_and_use_stopped_volume_safely():
    project_root = Path(__file__).resolve().parents[1]
    backup = (project_root / "scripts" / "backup.sh").read_text(encoding="utf-8")
    restore = (project_root / "scripts" / "restore.sh").read_text(encoding="utf-8")
    gitignore = (project_root / ".gitignore").read_text(encoding="utf-8")

    assert "umask 077" in backup
    assert 'deployment-config.tar.gz' in backup
    assert '.env secrets "${COMPOSE_FILE}" deploy scripts' in backup
    assert "--clean --if-exists" in backup
    assert "sha256sum" in backup
    assert "backups/" in gitignore

    assert "sha256sum -c SHA256SUMS" in restore
    assert "./scripts/backup.sh" in restore
    assert 'run --rm --no-deps -T api' in restore
    assert 'readlink -f /app/data' in restore
    assert 'exec -T api sh -c \'rm -rf' not in restore


def test_nginx_blocks_dotfiles_and_sets_browser_security_headers():
    project_root = Path(__file__).resolve().parents[1]
    edge_config = (project_root / "deploy" / "nginx.conf").read_text(encoding="utf-8")
    web_config = (project_root / "frontend" / "nginx.conf").read_text(encoding="utf-8")

    assert "server_tokens off" in edge_config
    assert "X-Content-Type-Options" in edge_config
    assert "Content-Security-Policy" in edge_config
    assert "location ~ /\\.(?!well-known/)" in edge_config
    assert "location = /openapi.json { return 404; }" in edge_config
    assert "location = /docs { return 404; }" in edge_config
    assert "location = /redoc { return 404; }" in edge_config
    assert "location ~ /\\.(?!well-known/)" in web_config


def test_docker_build_context_excludes_runtime_credentials_and_backups():
    project_root = Path(__file__).resolve().parents[1]
    dockerignore = (project_root / ".dockerignore").read_text(encoding="utf-8").splitlines()

    assert ".env" in dockerignore
    assert "secrets" in dockerignore
    assert "backups" in dockerignore
    assert "hotfix-backups" in dockerignore
    assert "knowledge-base/.managed" in dockerignore
    # 教材原始 PDF 不进构建上下文：知识库在运行期导入到 data/，镜像里不该带 1 GB 原始文件
    assert "knowledge-base/教材PDF/" in dockerignore


def test_gitignore_excludes_textbooks_and_generated_artifacts():
    """原始教材与系统生成的成品不进版本库。

    实际翻过一次车：28 个教材 PDF（约 1 GB）被提交进来，仓库 pack 涨到 1.13 GiB，
    其中两个还贴着 GitHub 单文件 100 MB 上限。规则钉在这里，别再发生。
    """
    project_root = Path(__file__).resolve().parents[1]
    rules = (project_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    for rule in (
        "*.pdf",
        "*.pptx",
        "*.docx",
        "*.xlsx",
        "backend/output/",
        "knowledge-base/教材PDF/",
        "knowledge-base/教材OCR文本/",
    ):
        assert rule in rules, f".gitignore 缺少规则：{rule}"


def test_no_textbooks_or_generated_outputs_are_tracked():
    """再问一次 git 本身：这些目录里现在有没有文件被跟踪。

    只看目录、不看扩展名 —— 仓库里还有历史遗留的测试夹具（ai/test.pdf、
    gen/parse/test_document.docx），按扩展名断言会和它们打架。
    """
    project_root = Path(__file__).resolve().parents[1]
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],  # -z：不转义非 ASCII 文件名，否则中文路径带引号前缀
            cwd=project_root,
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("环境里没有可用的 git")
    if result.returncode != 0:
        pytest.skip("当前目录不是 git 工作树")

    tracked = [item for item in result.stdout.decode("utf-8", "replace").split("\0") if item]
    for prefix in ("knowledge-base/", "data/", "backend/output/", "frontend/.ui-shots/"):
        offenders = [path for path in tracked if path.startswith(prefix)]
        assert not offenders, f"这些文件不该进版本库：{offenders[:5]}"
