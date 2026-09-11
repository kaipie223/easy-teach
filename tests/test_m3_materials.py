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
