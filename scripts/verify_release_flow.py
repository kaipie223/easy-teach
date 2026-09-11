"""Run one cost-bounded production acceptance flow without printing credentials or AI text."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone

import httpx


BASE_URL = os.getenv("EASY_TEACH_VERIFY_URL", "http://127.0.0.1").rstrip("/")
API = f"{BASE_URL}/api/v1"
PLAN_MODE = os.getenv("EASY_TEACH_VERIFY_PLAN_MODE", "ai")
RUN_BOOTSTRAP = os.getenv("EASY_TEACH_VERIFY_BOOTSTRAP", "true").lower() == "true"


def require(response: httpx.Response, *statuses: int) -> dict:
    if response.status_code not in statuses:
        request_id = response.headers.get("x-request-id", "missing")
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} failed: "
            f"status={response.status_code}, request_id={request_id}"
        )
    if response.status_code == 204:
        return {}
    return response.json()


def register(client: httpx.Client, label: str) -> tuple[dict[str, str], str]:
    suffix = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{secrets.token_hex(4)}"
    payload = require(
        client.post(
            f"{API}/auth/register",
            json={
                "email": f"release-{label}-{suffix}@example.com",
                "password": secrets.token_urlsafe(24),
                "display_name": f"Release {label}",
            },
        ),
        201,
    )
    return {"Authorization": f"Bearer {payload['access_token']}"}, payload["user"]["user_id"]


def poll_task(client: httpx.Client, headers: dict[str, str], task_id: str) -> dict:
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        task = require(client.get(f"{API}/tasks/{task_id}/status", headers=headers), 200)
        if task["status"] == "completed":
            return task
        if task["status"] == "failed":
            raise RuntimeError(f"generation failed: code={task.get('error_code') or 'unknown'}")
        time.sleep(2)
    raise RuntimeError("generation task timed out")


def poll_export(client: httpx.Client, headers: dict[str, str], export_id: str) -> dict:
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        item = require(client.get(f"{API}/exports/{export_id}", headers=headers), 200)
        if item["status"] == "completed":
            return item
        if item["status"] == "failed":
            raise RuntimeError(f"export failed: format={item['format']}")
        time.sleep(2)
    raise RuntimeError("export task timed out")


def main() -> None:
    project_id: str | None = None
    document_id: str | None = None
    owner_headers: dict[str, str] | None = None
    summary: dict[str, object] = {}
    with httpx.Client(timeout=httpx.Timeout(300, connect=15)) as client:
        try:
            owner_headers, _ = register(client, "owner")
            other_headers, _ = register(client, "other")
            project = require(
                client.post(
                    f"{API}/projects",
                    headers=owner_headers,
                    json={"title": "Release 验收课程", "scenario": "TCP 三次握手"},
                ),
                201,
            )
            project_id = project["project_id"]
            session = require(
                client.post(
                    f"{API}/sessions",
                    headers=owner_headers,
                    json={
                        "subject": "面向大一新生的 45 分钟 TCP 三次握手课程",
                        "project_id": project_id,
                    },
                ),
                201,
            )
            session_id = session["session_id"]

            bootstrap_status: int | None = None
            if RUN_BOOTSTRAP:
                bootstrap = client.post(
                    f"{API}/sessions/{session_id}/start",
                    headers=owner_headers,
                )
                if bootstrap.status_code not in {200, 204}:
                    require(bootstrap, 200, 204)
                bootstrap_status = bootstrap.status_code
            persisted_session = require(
                client.get(f"{API}/sessions/{session_id}", headers=owner_headers), 200
            )

            brief = require(
                client.patch(
                    f"{API}/projects/{project_id}/brief",
                    headers=owner_headers,
                    json={
                        "teaching_goal": "理解 TCP 三次握手，并能解释每次报文的作用",
                        "target_audience": "大一新生",
                        "duration_minutes": 45,
                        "knowledge_points": [
                            {
                                "order": 1,
                                "title": "连接建立时序",
                                "difficulty": "basic",
                                "key_points": ["SYN", "SYN-ACK", "ACK"],
                                "estimated_minutes": 20,
                            },
                            {
                                "order": 2,
                                "title": "序列号与确认号",
                                "difficulty": "intermediate",
                                "key_points": ["序列号", "确认号"],
                                "estimated_minutes": 15,
                            },
                        ],
                        "logic_flow": ["问题导入", "时序讲解", "报文练习", "总结反馈"],
                        "teaching_focus": "三次报文交换的时序和作用",
                        "teaching_difficulties": "区分 SYN 与 ACK 的语义",
                        "output_types": ["pptx", "docx", "pdf", "html"],
                        "interaction_ideas": "根据时序图补全报文并即时评分",
                    },
                ),
                200,
            )
            require(
                client.post(
                    f"{API}/projects/{project_id}/brief/confirm",
                    headers=owner_headers,
                    json={"expected_version": brief["version"]},
                ),
                200,
            )

            plan = require(
                client.post(
                    f"{API}/projects/{project_id}/plan",
                    headers=owner_headers,
                    json={
                        "force_rebuild": True,
                        "generation_mode": PLAN_MODE,
                        "allow_template_fallback": False,
                    },
                ),
                201,
            )
            if plan["generation_mode"] != PLAN_MODE:
                raise RuntimeError("plan did not use the requested generation path")

            idempotency_key = f"release-{secrets.token_hex(12)}"
            generation_body = {
                "plan_id": plan["plan_id"],
                "idempotency_key": idempotency_key,
            }
            task = require(
                client.post(
                    f"{API}/projects/{project_id}/generate",
                    headers=owner_headers,
                    json=generation_body,
                ),
                202,
            )
            repeated_task = require(
                client.post(
                    f"{API}/projects/{project_id}/generate",
                    headers=owner_headers,
                    json=generation_body,
                ),
                202,
            )
            if repeated_task["task_id"] != task["task_id"]:
                raise RuntimeError("generation idempotency key created a duplicate task")
            completed_task = poll_task(client, owner_headers, task["task_id"])

            versions = require(
                client.get(f"{API}/projects/{project_id}/versions", headers=owner_headers), 200
            )
            version_id = completed_task.get("artifact_version_id") or versions[0][
                "artifact_version_id"
            ]
            export_body = {
                "artifact_version_id": version_id,
                "formats": ["pptx", "docx", "pdf", "html"],
            }
            exports = require(
                client.post(
                    f"{API}/projects/{project_id}/exports",
                    headers=owner_headers,
                    json=export_body,
                ),
                202,
            )["exports"]
            repeated_exports = require(
                client.post(
                    f"{API}/projects/{project_id}/exports",
                    headers=owner_headers,
                    json=export_body,
                ),
                202,
            )["exports"]
            if {item["export_id"] for item in repeated_exports} != {
                item["export_id"] for item in exports
            }:
                raise RuntimeError("repeated export request created duplicate records")

            expected = {"pptx": b"PK", "docx": b"PK", "pdf": b"%PDF-", "html": b"<!DOCTYPE html>"}
            completed_exports = [
                poll_export(client, owner_headers, item["export_id"]) for item in exports
            ]
            checksums: dict[str, bool] = {}
            for item in completed_exports:
                download = client.get(
                    f"{API}/exports/{item['export_id']}/download", headers=owner_headers
                )
                if download.status_code != 200 or not download.content.startswith(expected[item["format"]]):
                    raise RuntimeError(f"invalid downloaded file: format={item['format']}")
                checksums[item["format"]] = (
                    hashlib.sha256(download.content).hexdigest() == item["checksum_sha256"]
                )

            knowledge = require(
                client.post(
                    f"{API}/knowledge/documents",
                    headers=owner_headers,
                    files={"file": ("release-note.txt", b"TCP uses SYN, SYN-ACK, ACK.", "text/plain")},
                    data={"title": "Release private note", "enabled": "false"},
                ),
                201,
            )
            document_id = knowledge["document_id"]

            isolation_statuses = {
                "project": client.get(
                    f"{API}/projects/{project_id}", headers=other_headers
                ).status_code,
                "plan": client.get(
                    f"{API}/projects/{project_id}/plan", headers=other_headers
                ).status_code,
                "versions": client.get(
                    f"{API}/projects/{project_id}/versions", headers=other_headers
                ).status_code,
                "export": client.get(
                    f"{API}/exports/{completed_exports[0]['export_id']}/download",
                    headers=other_headers,
                ).status_code,
                "knowledge": client.patch(
                    f"{API}/knowledge/documents/{document_id}",
                    headers=other_headers,
                    json={"title": "must not change"},
                ).status_code,
            }
            if set(isolation_statuses.values()) != {404}:
                raise RuntimeError(f"ownership isolation failed: {isolation_statuses}")

            usage = plan.get("usage") or {}
            summary = {
                "status": "passed",
                "bootstrap_status": bootstrap_status,
                "persisted_messages": len(persisted_session.get("messages") or []),
                "model": plan.get("model_name"),
                "prompt_version": plan.get("prompt_version"),
                "slides": len(plan["content"]["slides"]),
                "lesson_sections": len(plan["content"]["lesson_sections"]),
                "interactions": len(plan["content"]["interactions"]),
                "model_total_tokens": usage.get("total_tokens"),
                "generation_idempotent": True,
                "exports_idempotent": True,
                "downloads": checksums,
                "isolation_statuses": isolation_statuses,
            }
        finally:
            if document_id and owner_headers:
                client.delete(f"{API}/knowledge/documents/{document_id}", headers=owner_headers)
            if project_id and owner_headers:
                client.delete(f"{API}/projects/{project_id}", headers=owner_headers)
    print(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
