from types import SimpleNamespace

import pytest

from backend.config import settings
from backend.services.intent import IntentAnalyzer, _extract_json


def register(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "m2-intent@example.com",
            "password": "password123",
            "display_name": "测试教师",
        },
    )
    assert response.status_code == 201
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_missing_key_is_a_visible_sse_error(client, monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    headers = register(client)
    created = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"teacher_name": "测试教师", "subject": "物理"},
    )
    session_id = created.json()["session_id"]

    response = client.post(
        f"/api/v1/sessions/{session_id}/chat",
        headers=headers,
        json={"message": "设计一节浮力课"},
    )

    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"code": "AI_NOT_CONFIGURED"' in response.text
    assert "event: question" not in response.text


def test_intent_adapter_normalizes_nullable_fields():
    result = IntentAnalyzer._parse_intent(
        {
            "teaching_goal": None,
            "target_audience": None,
            "duration_minutes": None,
            "knowledge_points": None,
            "logic_flow": None,
            "style_preference": None,
            "missing_info": None,
            "is_complete": None,
        }
    )

    assert result.teaching_goal == ""
    assert result.duration_minutes == 45
    assert result.knowledge_points == []
    assert result.is_complete is False


def test_intent_adapter_rejects_non_object_response():
    with pytest.raises(TypeError, match="JSON object"):
        IntentAnalyzer._parse_intent(None)


def test_extract_json_accepts_wrapped_object():
    assert _extract_json('结果如下：\n{"is_complete": false}\n请继续补充') == {
        "is_complete": False
    }


def test_intent_analyzer_retries_empty_deepseek_content(monkeypatch):
    monkeypatch.setattr(settings, "deepseek_api_key", "test-key")
    empty_response = SimpleNamespace(
        model="deepseek-v4-flash",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="", reasoning_content=""),
            )
        ],
    )
    valid_response = SimpleNamespace(
        model="deepseek-v4-flash",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content='{"teaching_goal":"理解浮力", "is_complete":false}',
                    reasoning_content="",
                ),
            )
        ],
    )
    create = SimpleNamespace(side_effect=[empty_response, valid_response], calls=[])

    def fake_create(**kwargs):
        create.calls.append(kwargs)
        return create.side_effect.pop(0)

    analyzer = IntentAnalyzer()
    analyzer._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )

    result = analyzer.analyze("session-retry", [{"role": "user", "content": "90分钟"}])

    assert result.teaching_goal == "理解浮力"
    assert len(create.calls) == 2
    assert create.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "上一次响应为空或格式无效" in create.calls[1]["messages"][0]["content"]
