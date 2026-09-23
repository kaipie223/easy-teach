"""每页自动配图：生成时配好、整批只产生一个版本、单页失败不阻断。

"每配一张图就多一个版本"是这条功能的真正风险：一册十几页，一次生成就能把版本历史
冲垮。所以这里既验证图片配到了每一页，也验证版本数只增加一个。
"""

import os
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from backend.config import settings
from backend.models.courseware import CoursewarePlan
from backend.models.material import Material
from backend.models.project import Project
from backend.models.versioning import ArtifactVersion
from backend.schemas import IntentResult
from backend.services.generator import TEXT_ONLY_LAYOUTS, _slide_layout
from backend.services.image_generation import GeneratedImage, ImageGenerationError
from backend.services.orchestrator import get_orchestrator
from backend.services.slide_illustration import (
    AUTO_PLACEMENT,
    illustration_prompt,
    illustrate_slides,
    slides_missing_image,
)
from backend.services.versions import ensure_initial_version

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-bytes" * 8


def fake_generated() -> GeneratedImage:
    return GeneratedImage(data=PNG_BYTES, mime_type="image/png", extension=".png")


def register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "配图教师"},
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_project_with_plan(client, headers):
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "自动配图", "scenario": "每页配图"},
    )
    assert project.status_code == 201, project.text
    project_id = project.json()["project_id"]
    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": "HTTP", "project_id": project_id},
    )
    assert session.status_code == 201, session.text
    brief = client.patch(
        f"/api/v1/projects/{project_id}/brief",
        headers=headers,
        json={
            "teaching_goal": "理解 TCP 三次握手",
            "target_audience": "大一新生",
            "duration_minutes": 45,
            "knowledge_points": [
                {
                    "order": 1,
                    "title": "连接建立过程",
                    "key_points": ["SYN", "SYN-ACK", "ACK"],
                    "estimated_minutes": 20,
                }
            ],
            "logic_flow": ["问题导入", "过程讲解"],
            "teaching_focus": "报文时序",
            "teaching_difficulties": "SYN 与 ACK 的区别",
            "output_types": ["pptx", "docx", "html"],
        },
    )
    assert brief.status_code == 200, brief.text
    confirmed = client.post(
        f"/api/v1/projects/{project_id}/brief/confirm",
        headers=headers,
        json={"expected_version": brief.json()["version"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    plan = client.post(
        f"/api/v1/projects/{project_id}/plan",
        headers=headers,
        json={"generation_mode": "template"},
    )
    assert plan.status_code in (200, 201), plan.text
    return project_id, plan.json()["plan_id"]


def illustrated_slides(slides):
    return [slide for slide in slides if (slide.get("image") or {}).get("material_id")]


def structured_slides(slides):
    """会被渲染成结构版式（卡片/流程/目录/小结…）的页面。"""
    total = len(slides)
    return [
        slide
        for index, slide in enumerate(slides)
        if _slide_layout(slide, index, total) in TEXT_ONLY_LAYOUTS
    ]


def fake_lock_intent(self, session_id):
    return IntentResult(
        teaching_goal="测试课件",
        knowledge_points=[],
        target_audience="学生",
        duration_minutes=45,
        style_preference="清晰",
        logic_flow="",
        extra_requirements="",
    )


def make_file_stub(ext: str):
    def stub(*args, **kwargs):
        path = settings.output_dir / f"gen_{os.urandom(6).hex()}.{ext}"
        path.write_bytes(b"fake generated courseware")
        return str(path)

    return stub


def eager_dispatch(task_name: str, entity_id: str):
    """不连 Redis/Celery，直接在进程内跑真实编排器。"""
    get_orchestrator().run_generation(entity_id)

    class _Result:
        id = entity_id

    return _Result()


def test_prompt_uses_slide_content_and_skips_illustrated_pages():
    slide = {
        "title": "连接建立过程",
        "purpose": "讲解三次握手",
        "bullets": ["第一次握手：客户端发送 SYN", "第二次握手：服务器回 SYN-ACK"],
    }
    prompt = illustration_prompt(slide)
    assert "连接建立过程" in prompt
    assert "第一次握手" in prompt
    # 插图里不允许出现文字，否则图上的字会和页面正文打架
    assert "不要出现任何文字" in prompt

    slides = [
        {"slide_id": "slide_001", "title": "封面"},
        {"slide_id": "slide_002", "image": {"material_id": "mat_keep", "placement": "right"}},
        {"slide_id": "slide_003", "image": {"material_id": "", "placement": "right"}},
    ]
    pending = slides_missing_image(slides)
    assert [item["slide_id"] for item in pending] == ["slide_001", "slide_003"]


def test_illustrate_slides_fills_only_missing_pages(client, db_session_factory, tmp_path, monkeypatch):
    """已有的配图（含人工选的）必须原样保留，只给缺图页补图。"""
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "ark_api_key", "test-key")
    prompts: list[str] = []

    def fake_generate(prompt, **kwargs):
        prompts.append(prompt)
        return fake_generated()

    monkeypatch.setattr("backend.services.slide_illustration.generate_image", fake_generate)

    headers = register(client, "illustrate-service@example.com")
    project_resp = client.post(
        "/api/v1/projects", headers=headers, json={"title": "配图服务", "scenario": "服务层"}
    )
    project_id = project_resp.json()["project_id"]

    db = db_session_factory()
    project = db.query(Project).filter(Project.project_id == project_id).one()
    slides = [
        {"slide_id": "slide_001", "order": 1, "title": "封面"},
        {
            "slide_id": "slide_002",
            "order": 2,
            "title": "已有配图",
            "image": {"material_id": "mat_keep", "placement": "background", "caption": "留着"},
        },
        {"slide_id": "slide_003", "order": 3, "title": "第三页", "bullets": ["要点一", "要点二"]},
    ]

    attached = illustrate_slides(db, project, slides, user_id=project.owner_id)

    assert len(attached) == 2
    assert len(prompts) == 2
    assert slides[0]["image"]["placement"] == AUTO_PLACEMENT
    assert slides[1]["image"] == {"material_id": "mat_keep", "placement": "background", "caption": "留着"}
    assert slides[2]["image"]["material_id"] == attached[1]
    for material_id in attached:
        material = db.query(Material).filter(Material.material_id == material_id).one()
        assert material.file_type == "image"
        assert material.project_id == project_id
        assert Path(material.stored_path).is_file()
    db.close()


def test_illustrate_slides_skips_only_the_failing_page(client, db_session_factory, tmp_path, monkeypatch):
    """一张图生成不出来，只跳过那一页，不能让整份课件生成失败。"""
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "ark_api_key", "test-key")

    def flaky_generate(prompt, **kwargs):
        if "坏页" in prompt:
            raise ImageGenerationError("模型拒绝了这次生成")
        return fake_generated()

    monkeypatch.setattr("backend.services.slide_illustration.generate_image", flaky_generate)

    headers = register(client, "illustrate-resilient@example.com")
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "配图容错", "scenario": "单页失败"}
    ).json()["project_id"]

    db = db_session_factory()
    project = db.query(Project).filter(Project.project_id == project_id).one()
    slides = [
        {"slide_id": "slide_001", "order": 1, "title": "坏页"},
        {"slide_id": "slide_002", "order": 2, "title": "好页"},
    ]

    attached = illustrate_slides(db, project, slides, user_id=project.owner_id)

    assert len(attached) == 1
    assert slides[0].get("image") is None
    assert slides[1]["image"]["material_id"] == attached[0]
    db.close()


def test_illustrate_slides_respects_the_limit(client, db_session_factory, tmp_path, monkeypatch):
    """插图既慢又费额度：超过上限的页面留白，等教师按需补，不能无限生成。"""
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "ark_api_key", "test-key")
    monkeypatch.setattr("backend.services.slide_illustration.generate_image", lambda *a, **k: fake_generated())

    headers = register(client, "illustrate-limit@example.com")
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "配图上限", "scenario": "上限"}
    ).json()["project_id"]

    db = db_session_factory()
    project = db.query(Project).filter(Project.project_id == project_id).one()
    slides = [{"slide_id": f"slide_{index:03d}", "order": index, "title": f"第 {index} 页"} for index in range(1, 6)]

    attached = illustrate_slides(db, project, slides, user_id=project.owner_id, limit=2)

    assert len(attached) == 2
    assert all(slide.get("image") for slide in slides[:2])
    assert all(slide.get("image") is None for slide in slides[2:])
    db.close()


def test_illustrate_slides_skips_structured_layouts(client, db_session_factory, tmp_path, monkeypatch):
    """结构版式页不自动配图：加了配图这类版面会整页退回普通要点页。"""
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "ark_api_key", "test-key")
    monkeypatch.setattr(
        "backend.services.slide_illustration.generate_image",
        lambda *a, **k: fake_generated(),
    )

    headers = register(client, "illustrate-layout@example.com")
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "版式判断", "scenario": "结构版式"}
    ).json()["project_id"]

    db = db_session_factory()
    project = db.query(Project).filter(Project.project_id == project_id).one()
    slides = [
        {"slide_id": "slide_001", "order": 1, "title": "封面"},
        {"slide_id": "slide_002", "order": 2, "title": "目录", "layout": "agenda"},
        {"slide_id": "slide_003", "order": 3, "title": "讲解页"},
        {"slide_id": "slide_004", "order": 4, "title": "卡片", "layout": "cards"},
        {"slide_id": "slide_005", "order": 5, "title": "小结"},
    ]

    attached = illustrate_slides(db, project, slides, user_id=project.owner_id, limit=10)

    # 封面与普通讲解页配图；目录、卡片、末页小结（无版式名但会渲染成小结页）留白
    assert len(attached) == 2
    assert slides[0]["image"]["material_id"] == attached[0]
    assert slides[1].get("image") is None
    assert slides[2]["image"]["material_id"] == attached[1]
    assert slides[3].get("image") is None
    assert slides[4].get("image") is None
    db.close()


def test_generation_illustrates_every_slide_without_extra_versions(
    client, db_session_factory, tmp_path, monkeypatch
):
    """真实跑一遍生成：每页都配上图，且项目只多出一个成果版本。"""
    engine = db_session_factory().bind
    test_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr("backend.db.database.SessionLocal", test_session)
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "ark_api_key", "test-key")
    # 这里要验"每页都有图"，所以放开上限；上限本身由上面的用例单独验证
    monkeypatch.setattr(settings, "slide_illustration_limit", 50)
    monkeypatch.setattr(
        "backend.services.slide_illustration.generate_image",
        lambda prompt, **kwargs: fake_generated(),
    )
    monkeypatch.setattr("backend.services.task_queue._dispatch", eager_dispatch)
    monkeypatch.setattr("backend.services.orchestrator.search_sync", lambda *a, **k: [])
    monkeypatch.setattr("backend.services.intent.IntentAnalyzer.lock_intent", fake_lock_intent)
    monkeypatch.setattr("backend.services.orchestrator.resolve_slide_images", lambda *a, **k: {})
    monkeypatch.setattr(
        "backend.services.orchestrator.require_courseware_quality", lambda *a, **k: {"status": "passed"}
    )
    monkeypatch.setattr("backend.services.orchestrator.generate_pptx", make_file_stub("pptx"))
    monkeypatch.setattr("backend.services.orchestrator.generate_docx", make_file_stub("docx"))
    monkeypatch.setattr("backend.services.orchestrator.generate_html", make_file_stub("html"))

    headers = register(client, "illustrate-generation@example.com")
    project_id, plan_id = create_project_with_plan(client, headers)

    response = client.post(
        f"/api/v1/projects/{project_id}/generate",
        headers=headers,
        json={"plan_id": plan_id},
    )
    assert response.status_code == 202, response.text

    db = db_session_factory()
    versions = (
        db.query(ArtifactVersion)
        .filter(ArtifactVersion.project_id == project_id)
        .order_by(ArtifactVersion.version)
        .all()
    )
    # 一册课件的配图属于同一个版本，不是一页一个版本
    assert [version.version for version in versions] == [1]
    slides = versions[0].snapshot_json["slides"]
    illustrated = illustrated_slides(slides)
    assert illustrated, "生成时要给页面配上图"
    assert all(slide["image"]["placement"] == AUTO_PLACEMENT for slide in illustrated)
    # 结构版式页留白：给它们配图会被渲染器退回普通要点页
    assert structured_slides(slides), "模板蓝图应包含结构版式页"
    assert not illustrated_slides(structured_slides(slides))
    db.close()


def test_illustrate_endpoint_creates_exactly_one_version(client, db_session_factory, tmp_path, monkeypatch):
    """给已有版本补配图：整批一个新版本；再点一次要明确说"没有缺图页"。"""
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "ark_api_key", "test-key")
    monkeypatch.setattr(settings, "slide_illustration_limit", 50)
    monkeypatch.setattr(
        "backend.services.slide_illustration.generate_image",
        lambda prompt, **kwargs: fake_generated(),
    )

    headers = register(client, "illustrate-endpoint@example.com")
    project_id, plan_id = create_project_with_plan(client, headers)

    db = db_session_factory()
    project = db.query(Project).filter(Project.project_id == project_id).one()
    plan = db.query(CoursewarePlan).filter(CoursewarePlan.plan_id == plan_id).one()
    version = ensure_initial_version(db, project, plan, user_id=project.owner_id)
    db.commit()
    version_id = version.artifact_version_id
    before = db.query(ArtifactVersion).filter(ArtifactVersion.project_id == project_id).count()

    created = client.post(
        f"/api/v1/projects/{project_id}/versions/{version_id}/illustrate",
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["version"] == 2
    assert "生成配图" in body["summary"]
    slides = body["snapshot"]["slides"]
    illustrated = illustrated_slides(slides)
    assert illustrated, "补配图要真的配上"
    assert not illustrated_slides(structured_slides(slides))

    db.expire_all()
    after = db.query(ArtifactVersion).filter(ArtifactVersion.project_id == project_id).count()
    assert after == before + 1

    # 没有缺图页时不该悄悄新建一个空版本
    repeat = client.post(
        f"/api/v1/projects/{project_id}/versions/{body['artifact_version_id']}/illustrate",
        headers=headers,
    )
    assert repeat.status_code == 409
    assert repeat.json()["error"]["code"] == "NO_SLIDE_NEEDS_IMAGE"
    db.expire_all()
    assert db.query(ArtifactVersion).filter(ArtifactVersion.project_id == project_id).count() == after
    db.close()
