import fitz

from backend.config import settings


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
    assert client.get(f"/api/v1/materials/{material['material_id']}", headers=headers).status_code == 404


def test_video_upload_is_explicitly_deferred(client, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")
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
        files={"file": ("lesson.mp4", b"not-video", "video/mp4")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VIDEO_PARSING_DEFERRED"
