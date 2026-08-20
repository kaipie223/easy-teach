import asyncio
from pathlib import Path

from backend.db.database import get_db
from backend.main import app
from backend.models.material import EvidenceChunk, Material
from backend.services.generator import generate_docx, generate_html, generate_pptx


def register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "M4 Teacher"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return payload, {"Authorization": f"Bearer {payload['access_token']}"}


def complete_brief():
    return {
        "teaching_goal": "帮助学生理解 TCP 三次握手并解释每次报文的作用",
        "target_audience": "大一新生",
        "duration_minutes": 45,
        "knowledge_points": [
            {
                "order": 1,
                "title": "连接建立过程",
                "difficulty": "basic",
                "key_points": ["SYN", "SYN-ACK", "ACK"],
                "examples": ["客户端与服务器建立连接"],
                "estimated_minutes": 20,
            },
            {
                "order": 2,
                "title": "报文确认机制",
                "difficulty": "intermediate",
                "key_points": ["序列号", "确认号"],
                "examples": [],
                "estimated_minutes": 15,
            },
        ],
        "logic_flow": ["问题导入", "过程讲解", "例题练习", "总结"],
        "teaching_focus": "三次报文交换的时序和作用",
        "teaching_difficulties": "区分 SYN 与 ACK 的含义",
        "output_types": ["pptx", "docx", "html"],
        "interaction_ideas": "让学生根据时序图补全报文",
    }


def test_courseware_plan_is_versioned_and_evidence_backed(client, monkeypatch):
    async def no_external_rag(*args, **kwargs):
        return []

    monkeypatch.setattr("backend.routers.courseware.rag_search", no_external_rag)
    payload, headers = register(client, "m4-plan@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "M4 蓝图测试", "scenario": "通用课程"},
    )
    assert project.status_code == 201
    project_id = project.json()["project_id"]
    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": "TCP", "project_id": project_id},
    )
    assert session.status_code == 201

    db = next(app.dependency_overrides[get_db]())
    try:
        material = Material(
            material_id="mat_m4test",
            owner_id=payload["user"]["user_id"],
            project_id=project_id,
            original_name="tcp.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            stored_path="/tmp/tcp.pdf",
            size_bytes=10,
            checksum_sha256="a" * 64,
            status="ready",
            metadata_json={},
        )
        db.add(material)
        db.add(
            EvidenceChunk(
                evidence_id="evidence_m4test",
                material_id=material.material_id,
                source_type="uploaded_pdf",
                chunk_index=0,
                locator_json={"page": 3},
                text="SYN、SYN-ACK 和 ACK 完成连接建立。",
                metadata_json={},
                usage_tags=[],
                content_hash="b" * 64,
                is_valid=True,
            )
        )
        db.commit()
    finally:
        db.close()

    updated = client.patch(
        f"/api/v1/projects/{project_id}/brief",
        headers=headers,
        json=complete_brief(),
    )
    assert updated.status_code == 200, updated.text
    confirmed = client.post(
        f"/api/v1/projects/{project_id}/brief/confirm",
        headers=headers,
        json={"expected_version": updated.json()["version"]},
    )
    assert confirmed.status_code == 200, confirmed.text

    created = client.post(
        f"/api/v1/projects/{project_id}/plan",
        headers=headers,
        json={},
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["status"] == "ready"
    assert plan["version"] == 1
    assert len(plan["content"]["slides"]) >= 4
    assert plan["content"]["slides"][0]["slide_id"] == "slide_001"
    assert plan["content"]["lesson_sections"]
    assert plan["content"]["interactions"][0]["items"]
    assert plan["source_refs"][0]["evidence_id"] == "evidence_m4test"
    assert plan["source_refs"][0]["locator"]["page"] == 3

    fetched = client.get(f"/api/v1/projects/{project_id}/plan", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["plan_id"] == plan["plan_id"]

    rebuilt = client.post(
        f"/api/v1/projects/{project_id}/plan",
        headers=headers,
        json={"force_rebuild": True},
    )
    assert rebuilt.status_code == 201, rebuilt.text
    assert rebuilt.json()["version"] == 2
    assert rebuilt.json()["brief_id"] == plan["brief_id"]


def test_three_renderers_consume_the_same_plan(tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "output_dir", Path(tmp_path))
    plan = {
        "title": "TCP 三次握手",
        "teaching_goal": "理解连接建立过程",
        "target_audience": "大一新生",
        "duration_minutes": 45,
        "teaching_focus": "报文顺序",
        "teaching_difficulties": "SYN 与 ACK 的区别",
        "slides": [
            {
                "slide_id": "slide_001",
                "order": 1,
                "title": "TCP 三次握手",
                "purpose": "导入",
                "bullets": ["SYN", "SYN-ACK", "ACK"],
                "speaker_notes": "引入问题",
                "evidence_refs": [{"source_name": "tcp.pdf", "locator": {"page": 3}}],
            }
        ],
        "lesson_sections": [
            {
                "section_id": "section_001",
                "order": 1,
                "title": "问题导入",
                "duration_minutes": 10,
                "objective": "提出问题",
                "teacher_actions": ["展示连接场景"],
                "student_actions": ["提出猜想"],
                "assessment": "能够复述问题",
                "evidence_refs": [],
            }
        ],
        "interactions": [
            {
                "interaction_id": "interaction_001",
                "interaction_type": "classification",
                "title": "报文排序",
                "prompt": "请选出报文",
                "items": ["SYN", "ACK"],
                "answer_groups": {"核心": ["SYN", "ACK"]},
                "evidence_refs": [],
            }
        ],
        "evidence_refs": [],
    }
    pptx_path = Path(asyncio.run(generate_pptx(plan)))
    docx_path = Path(asyncio.run(generate_docx(plan)))
    html_path = Path(asyncio.run(generate_html(plan)))
    assert pptx_path.is_file() and pptx_path.stat().st_size > 0
    assert docx_path.is_file() and docx_path.stat().st_size > 0
    assert html_path.is_file() and "报文排序" in html_path.read_text(encoding="utf-8")
