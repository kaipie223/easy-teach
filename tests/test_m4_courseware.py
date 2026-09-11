import asyncio
import zipfile
from pathlib import Path

import fitz
from docx import Document

from backend.db.database import get_db
from backend.main import app
from backend.models.material import EvidenceChunk, Material
from backend.services.generator import generate_docx, generate_html, generate_pdf, generate_pptx


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
        json={"generation_mode": "template"},
    )
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["status"] == "ready"
    assert plan["generation_mode"] == "template"
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
        json={"force_rebuild": True, "generation_mode": "template"},
    )
    assert rebuilt.status_code == 201, rebuilt.text
    assert rebuilt.json()["version"] == 2
    assert rebuilt.json()["brief_id"] == plan["brief_id"]

    revised_content = rebuilt.json()["content"]
    revised_content["title"] = "TCP 连接建立与确认机制"
    revised_content["slides"][0]["title"] = "从一次连接请求开始"
    revised_content["output_specs"]["docx"]["homework"] = "绘制报文时序图并解释三个报文。"
    revised = client.post(
        f"/api/v1/projects/{project_id}/plan/revisions",
        headers=headers,
        json={
            "base_plan_id": rebuilt.json()["plan_id"],
            "content": revised_content,
            "summary": "调整导入页与课后任务",
        },
    )
    assert revised.status_code == 201, revised.text
    assert revised.json()["version"] == 3
    assert revised.json()["generation_mode"] == "manual"
    assert revised.json()["content"]["slides"][0]["slide_id"] == "slide_001"
    assert revised.json()["content"]["output_specs"]["docx"]["homework"].startswith("绘制")

    stale_revision = client.post(
        f"/api/v1/projects/{project_id}/plan/revisions",
        headers=headers,
        json={"base_plan_id": rebuilt.json()["plan_id"], "content": revised_content},
    )
    assert stale_revision.status_code == 409
    assert stale_revision.json()["error"]["code"] == "PLAN_VERSION_CONFLICT"


def test_four_renderers_consume_the_same_plan(tmp_path, monkeypatch):
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
                "speaker_notes": "引入问题并观察学生已有认识",
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
                "items": ["SYN", "ACK", "</script><script>alert(1)</script>"],
                "answer_groups": {"核心": ["SYN", "ACK"]},
                "evidence_refs": [],
            }
        ],
        "evidence_refs": [],
        "output_specs": {
            "pptx": {
                "narrative_arc": ["问题", "解释", "练习"],
                "visual_direction": "使用时序图呈现报文方向",
                "max_bullets_per_slide": 5,
                "speaker_notes_required": True,
            },
            "docx": {
                "teacher_preparation": ["准备抓包截图"],
                "differentiation": ["为初学者提供报文提示"],
                "homework": "绘制三次握手时序图。",
                "reflection_prompts": ["学生最容易混淆哪个确认号？"],
            },
            "pdf": {
                "printable_summary": "三次握手通过 SYN、SYN-ACK 和 ACK 建立连接。",
                "assessment_checklist": ["能按顺序写出三个报文"],
                "include_sources": True,
            },
            "html": {
                "interaction_ids": ["interaction_001"],
                "completion_message": "你已经完成连接建立练习。",
                "allow_retry": False,
                "accessibility_notes": ["支持键盘操作"],
            },
        },
    }
    pptx_path = Path(asyncio.run(generate_pptx(plan)))
    docx_path = Path(asyncio.run(generate_docx(plan)))
    pdf_path = Path(asyncio.run(generate_pdf(plan)))
    html_path = Path(asyncio.run(generate_html(plan)))
    assert pptx_path.is_file() and pptx_path.stat().st_size > 0
    assert docx_path.is_file() and docx_path.stat().st_size > 0
    assert pdf_path.read_bytes().startswith(b"%PDF-")
    with fitz.open(pdf_path) as pdf:
        assert pdf.page_count == 3
        pdf_text = "".join(page.get_text() for page in pdf)
        assert "学习评价清单" in pdf_text
        assert "三次握手通过" in pdf_text
    document_text = "\n".join(paragraph.text for paragraph in Document(docx_path).paragraphs)
    assert "准备抓包截图" in document_text
    assert "绘制三次握手时序图" in document_text
    with zipfile.ZipFile(pptx_path) as archive:
        notes_xml = b"".join(
            archive.read(name) for name in archive.namelist() if name.startswith("ppt/notesSlides/")
        )
        assert "引入问题并观察学生已有认识".encode() in notes_xml
    html_text = html_path.read_text(encoding="utf-8")
    assert "报文排序" in html_text
    assert "提交答案" in html_text and "重新作答" not in html_text
    assert "你已经完成连接建立练习" in html_text
    assert "answerGroups" in html_text
    assert "</script><script>alert(1)</script>" not in html_text
