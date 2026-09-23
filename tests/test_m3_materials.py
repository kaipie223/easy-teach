import io

import fitz
from PIL import Image

from backend.config import settings
from backend.services import materials as material_service
from video_parser.schemas import (
    EvidenceItem,
    SourceVideo,
    TimeRange,
    Transcript,
    VideoMetadata,
    VideoParseResult,
)


def register(client, email: str):
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "password123", "display_name": "资料教师"},
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    return {"Authorization": f"Bearer {payload['access_token']}"}


def test_pdf_material_analysis_evidence_and_binding(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    headers = register(client, "m3-materials@example.com")

    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "资料解析测试", "scenario": "M3"},
    )
    assert project.status_code == 201
    project_id = project.json()["project_id"]

    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": "PDF", "project_id": project_id},
    )
    assert session.status_code == 201
    session_id = session.json()["session_id"]

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "页面一的证据内容")
    pdf_bytes = document.tobytes()
    document.close()

    uploaded = client.post(
        f"/api/v1/projects/{project_id}/materials",
        headers=headers,
        data={"session_id": session_id, "ref_description": "内容依据"},
        files={"file": ("lesson.pdf", pdf_bytes, "application/pdf")},
    )
    assert uploaded.status_code == 201, uploaded.text
    material = uploaded.json()
    assert material["status"] == "ready"
    assert material["file_type"] == "pdf"

    analysis = client.get(
        f"/api/v1/materials/{material['material_id']}/analysis", headers=headers
    )
    assert analysis.status_code == 200
    assert analysis.json()["page_count"] == 1
    assert analysis.json()["status"] == "completed"

    evidence = client.get(
        f"/api/v1/materials/{material['material_id']}/evidence", headers=headers
    )
    assert evidence.status_code == 200
    assert evidence.json()[0]["locator_json"]["page"] == 1

    bindings = client.put(
        f"/api/v1/materials/{material['material_id']}/bindings",
        headers=headers,
        json={
            "bindings": [{
                "usage_type": "content_basis",
                "target_type": "whole_course",
                "confirmed_by_teacher": True,
            }]
        },
    )
    assert bindings.status_code == 200
    assert bindings.json()[0]["confirmed_by_teacher"] is True

    listed = client.get(f"/api/v1/projects/{project_id}/materials", headers=headers)
    assert listed.status_code == 200
    assert [item["material_id"] for item in listed.json()] == [material["material_id"]]

    downloaded = client.get(
        f"/api/v1/materials/{material['material_id']}/download", headers=headers
    )
    assert downloaded.status_code == 200
    assert downloaded.content == pdf_bytes

    deleted = client.delete(f"/api/v1/materials/{material['material_id']}", headers=headers)
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "archived"
    assert not [path for path in settings.upload_dir.rglob("*") if path.is_file()]
    assert client.get(f"/api/v1/materials/{material['material_id']}", headers=headers).status_code == 404


def test_video_upload_is_queued_for_async_parser(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "video_parser_output_dir", tmp_path / "video-parser")
    monkeypatch.setattr(settings, "video_parser_enabled", True)
    enqueued = []
    monkeypatch.setattr(
        "backend.routers.materials.enqueue_material_analysis",
        lambda analysis_id, **_kwargs: enqueued.append(analysis_id) or analysis_id,
    )
    headers = register(client, "m3-video@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "视频后置测试"},
    )
    project_id = project.json()["project_id"]
    session = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"subject": "video", "project_id": project_id},
    )

    response = client.post(
        f"/api/v1/projects/{project_id}/materials",
        headers=headers,
        data={"session_id": session.json()["session_id"]},
        files={"file": ("lesson.mp4", b"\x00\x00\x00\x18ftypmp42video-bytes", "video/mp4")},
    )
    assert response.status_code == 201, response.text
    material = response.json()
    assert material["status"] == "queued"
    assert material["file_type"] == "video"
    assert len(enqueued) == 1

    analysis = client.get(
        f"/api/v1/materials/{material['material_id']}/analysis", headers=headers
    )
    assert analysis.status_code == 200
    assert analysis.json()["parser_name"] == "video-parser-model"
    assert analysis.json()["status"] == "pending"

    evidence = client.get(
        f"/api/v1/materials/{material['material_id']}/evidence", headers=headers
    )
    assert evidence.status_code == 200
    assert evidence.json() == []


def test_invalid_video_content_is_rejected_before_parser(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    headers = register(client, "m3-invalid-video@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "视频类型校验"},
    )
    project_id = project.json()["project_id"]

    response = client.post(
        f"/api/v1/projects/{project_id}/materials",
        headers=headers,
        files={"file": ("lesson.mp4", b"not-video", "video/mp4")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MATERIAL_TYPE_MISMATCH"


def test_valid_video_is_rejected_while_capability_is_disabled(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(settings, "video_parser_enabled", False)
    headers = register(client, "m3-disabled-video@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "视频能力关闭测试"},
    )

    response = client.post(
        f"/api/v1/projects/{project.json()['project_id']}/materials",
        headers=headers,
        files={"file": ("lesson.mp4", b"\x00\x00\x00\x18ftypmp42video-bytes", "video/mp4")},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "VIDEO_CAPABILITY_DISABLED"


def test_image_is_saved_without_fake_vision_evidence(client, tmp_path, monkeypatch):
    # 本用例验证"未配置视觉时不伪装识别成功"，所以显式关闭视觉：否则本地 .env
    # 一旦启用视觉模型，这里就会变成一次真实的付费调用，测试结果也随环境漂移。
    monkeypatch.setattr(settings, "deepseek_vision_model", "")
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    image_bytes = io.BytesIO()
    Image.new("RGB", (8, 6), color="white").save(image_bytes, format="PNG")
    headers = register(client, "m3-image@example.com")
    project = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "图片能力状态测试"},
    )
    project_id = project.json()["project_id"]

    uploaded = client.post(
        f"/api/v1/projects/{project_id}/materials",
        headers=headers,
        files={"file": ("diagram.png", image_bytes.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    material = uploaded.json()
    assert material["status"] == "ready"

    analysis = client.get(
        f"/api/v1/materials/{material['material_id']}/analysis",
        headers=headers,
    )
    assert analysis.status_code == 200
    assert analysis.json()["result_json"]["vision_status"] == "not_configured"
    evidence = client.get(
        f"/api/v1/materials/{material['material_id']}/evidence",
        headers=headers,
    )
    assert evidence.status_code == 200
    assert evidence.json() == []


def test_image_description_becomes_evidence_when_vision_is_configured(
    client, tmp_path, monkeypatch
):
    """配置视觉模型后，图片描述要成为正常证据，而不是只留一行"未配置"。"""
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
    monkeypatch.setattr(
        "backend.services.materials.describe_image",
        lambda path: {
            "description": "弹簧测力计在空气和水中两次读数的对比照片",
            "keywords": ["弹簧测力计", "浮力"],
            "suggested_use": "讲解称重法测浮力",
            "model_name": "deepseek-v4-flash-vision-exp",
            "prompt_version": "image-vision-v1",
        },
    )
    image_bytes = io.BytesIO()
    Image.new("RGB", (8, 6), color="white").save(image_bytes, format="PNG")
    headers = register(client, "m3-image-vision@example.com")
    project_id = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "图片视觉测试"},
    ).json()["project_id"]

    uploaded = client.post(
        f"/api/v1/projects/{project_id}/materials",
        headers=headers,
        files={"file": ("diagram.png", image_bytes.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    material_id = uploaded.json()["material_id"]

    analysis = client.get(f"/api/v1/materials/{material_id}/analysis", headers=headers)
    assert analysis.status_code == 200
    result = analysis.json()["result_json"]
    assert result["vision_status"] == "ready"
    assert result["description"] == "弹簧测力计在空气和水中两次读数的对比照片"
    assert result["keywords"] == ["弹簧测力计", "浮力"]

    evidence = client.get(f"/api/v1/materials/{material_id}/evidence", headers=headers)
    assert evidence.status_code == 200
    chunks = evidence.json()
    assert chunks, "视觉描述必须成为可检索证据"
    assert any("两次读数" in item["text"] for item in chunks)


def test_image_vision_failure_is_reported_instead_of_faked(client, tmp_path, monkeypatch):
    """视觉调用失败要如实标记，不能伪装成识别成功。"""
    from backend.services.image_vision import ImageVisionError

    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")

    def broken(path):
        raise ImageVisionError("图片理解调用失败：APIConnectionError")

    monkeypatch.setattr("backend.services.materials.describe_image", broken)
    image_bytes = io.BytesIO()
    Image.new("RGB", (8, 6), color="white").save(image_bytes, format="PNG")
    headers = register(client, "m3-image-vision-broken@example.com")
    project_id = client.post(
        "/api/v1/projects",
        headers=headers,
        json={"title": "视觉失败测试"},
    ).json()["project_id"]

    uploaded = client.post(
        f"/api/v1/projects/{project_id}/materials",
        headers=headers,
        files={"file": ("diagram.png", image_bytes.getvalue(), "image/png")},
    )
    assert uploaded.status_code == 201, uploaded.text
    material_id = uploaded.json()["material_id"]

    analysis = client.get(f"/api/v1/materials/{material_id}/analysis", headers=headers)
    assert analysis.status_code == 200
    result = analysis.json()["result_json"]
    assert result["vision_status"] == "failed"
    assert "图片理解调用失败" in result["warning"]

    evidence = client.get(f"/api/v1/materials/{material_id}/evidence", headers=headers)
    assert evidence.json() == []


def test_list_project_images_exposes_descriptions_for_the_model(
    client, db_session_factory, tmp_path
):
    """候选清单要带上视觉描述与教师备注，且只含本项目可用的图片。"""
    from datetime import datetime, timezone

    from backend.models.material import Material, MaterialAnalysis
    from backend.services.materials import list_project_images

    signup = client.post(
        "/api/v1/auth/register",
        json={
            "email": "m3-image-list@example.com",
            "password": "password123",
            "display_name": "M3 Image List",
        },
    )
    account = signup.json()
    headers = {"Authorization": f"Bearer {account['access_token']}"}
    project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "候选清单测试"}
    ).json()["project_id"]
    other_project_id = client.post(
        "/api/v1/projects", headers=headers, json={"title": "另一个项目"}
    ).json()["project_id"]

    figure = tmp_path / "figure.png"
    Image.new("RGB", (8, 6), color="white").save(figure)

    db = db_session_factory()
    try:
        def material(material_id, project, **extra):
            return Material(
                material_id=material_id,
                owner_id=account["user"]["user_id"],
                project_id=project,
                original_name=f"{material_id}.png",
                file_type="image",
                stored_path=str(figure),
                size_bytes=1,
                checksum_sha256=material_id,
                **extra,
            )

        db.add_all(
            [
                material("mat_done", project_id, ref_description="我自己写的备注"),
                material("mat_other_project", other_project_id),
                material("mat_archived", project_id, deleted_at=datetime.now(timezone.utc)),
            ]
        )
        db.add(
            MaterialAnalysis(
                analysis_id="analysis_mat_done",
                material_id="mat_done",
                run_number=1,
                parser_name="image_vision",
                status="completed",
                result_json={
                    "vision_status": "ready",
                    "description": "二叉树三种遍历的对照示意图",
                    "keywords": ["二叉树", "遍历"],
                },
            )
        )
        # 非图片资料不能进入配图候选
        db.add(
            Material(
                material_id="mat_pdf",
                owner_id=account["user"]["user_id"],
                project_id=project_id,
                original_name="notes.pdf",
                file_type="pdf",
                stored_path=str(figure),
                size_bytes=1,
                checksum_sha256="mat_pdf",
            )
        )
        db.commit()

        images = list_project_images(db, project_id)

        assert [item["material_id"] for item in images] == ["mat_done"]
        assert images[0]["description"] == "二叉树三种遍历的对照示意图"
        assert images[0]["teacher_note"] == "我自己写的备注"
        assert images[0]["keywords"] == ["二叉树", "遍历"]
        assert images[0]["vision_status"] == "ready"
    finally:
        db.close()


def test_video_parse_result_is_mapped_to_material_service(tmp_path, monkeypatch):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42video-bytes")
    monkeypatch.setattr(settings, "video_parser_output_dir", tmp_path / "video-parser")

    def fake_video_parse(path, output_root, options):
        assert path == video
        assert output_root == tmp_path / "video-parser"
        assert options.transcribe is settings.video_parser_transcribe
        return VideoParseResult(
            video_id="lesson-video",
            created_at="2026-08-27T00:00:00+00:00",
            source_video=SourceVideo(
                path=str(video),
                file_name="lesson.mp4",
                file_size_bytes=video.stat().st_size,
                sha1="a" * 40,
            ),
            metadata=VideoMetadata(duration_seconds=8.4),
            transcript=Transcript(status="not_requested"),
            evidence=[
                EvidenceItem(
                    id="ev_keyframe_0001",
                    evidence_type="keyframe",
                    source_id="kf_0001",
                    source_path="keyframes/sample.jpg",
                    time_range=TimeRange(
                        start_seconds=3.0,
                        end_seconds=3.0,
                        start="00:03.000",
                        end="00:03.000",
                    ),
                    content="sample frame at 00:03.000.",
                    metadata={"reason": "generic_uniform_coverage"},
                )
            ],
        )

    monkeypatch.setattr(material_service, "video_parse_video", fake_video_parse)

    parsed = material_service.parse_material("video", video)

    assert parsed.duration_seconds == 8
    assert parsed.result_json["parser"]["name"] == "video-parser-model"
    assert parsed.result_json["video_id"] == "lesson-video"
    assert parsed.chunks[0].locator["timestamp"] == "00:03.000"
    assert parsed.chunks[0].metadata["source_path"] == "keyframes/sample.jpg"
