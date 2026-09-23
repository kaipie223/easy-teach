import json
from types import SimpleNamespace

from backend.models.brief import TeachingBrief
from backend.schemas import CoursewarePlanSpec
from backend.services.courseware import compile_plan_content
from backend.services.revision_ai import regenerate_target


class FakeCompletions:
    def __init__(self, payload):
        self.payloads = payload if isinstance(payload, list) else [payload]
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))],
            usage=SimpleNamespace(prompt_tokens=40, completion_tokens=60, total_tokens=100),
        )


def test_revision_ai_preserves_target_identity_and_other_content():
    brief = TeachingBrief(
        content_json={
            "teaching_goal": "理解浮力并解释生活现象",
            "target_audience": "初二学生",
            "duration_minutes": 45,
            "knowledge_points": [
                {"order": 1, "title": "浮力概念", "estimated_minutes": 15},
                {"order": 2, "title": "阿基米德原理", "estimated_minutes": 20},
            ],
            "logic_flow": ["导入", "实验", "应用", "总结"],
            "teaching_focus": "影响浮力大小的因素",
            "teaching_difficulties": "排开液体体积的理解",
            "output_types": ["pptx", "docx", "pdf", "html"],
        }
    )
    snapshot = compile_plan_content(brief, [])
    original = snapshot.model_copy(deep=True)
    target = snapshot.slides[2]
    payload = {
        "slide": {
            "slide_id": "untrusted-id",
            "order": 999,
            "layout": "untrusted-layout",
            "title": "从测力计示数变化发现浮力",
            "purpose": "用实验数据建立浮力概念",
            "bullets": [
                "记录物体在空气中的示数",
                "记录物体浸入水后的示数",
                "比较两次示数差",
                "分析示数差的方向",
                "解释浮力与示数差的关系",
                "这条要点超过页面规范，应由后端裁剪",
            ],
            "speaker_notes": "先让学生预测，再测量并用示数差解释浮力。",
            "evidence_refs": [{"source_type": "fake", "source_name": "伪造来源"}],
        }
    }
    completions = FakeCompletions(payload)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = regenerate_target(
        snapshot,
        target_type="slide",
        target_id=target.slide_id,
        instruction="改为实验数据驱动的页面",
        client=client,
    )

    changed = result.spec.slides[2]
    assert changed.slide_id == target.slide_id
    assert changed.order == target.order
    assert changed.layout == target.layout
    assert changed.evidence_refs == target.evidence_refs
    assert changed.title == "从测力计示数变化发现浮力"
    assert len(changed.bullets) == snapshot.output_specs.pptx.max_bullets_per_slide
    assert result.spec.slides[0] == original.slides[0]
    assert result.spec.lesson_sections == original.lesson_sections
    assert result.prompt_version == "artifact-target-v3"
    assert result.usage["total_tokens"] == 100
    assert completions.calls == 1


def test_revision_ai_repairs_noop_and_accepts_provider_specific_wrapper():
    brief = TeachingBrief(
        content_json={
            "teaching_goal": "理解浮力并解释生活现象",
            "target_audience": "初二学生",
            "duration_minutes": 45,
            "knowledge_points": [{"order": 1, "title": "浮力概念", "estimated_minutes": 20}],
            "logic_flow": ["导入", "实验", "应用", "总结"],
            "teaching_focus": "影响浮力大小的因素",
            "teaching_difficulties": "排开液体体积的理解",
            "output_types": ["pptx", "docx", "pdf", "html"],
        }
    )
    snapshot = compile_plan_content(brief, [])
    target = snapshot.interactions[0]
    payloads = [
        {"result": target.model_dump(mode="json")},
        {
            "regenerated_interaction": {
                "title": "测力计实验判断",
                "prompt": "根据两次测力计示数判断浮力大小，并选择理由。",
                "explanation": "空气与水中示数之差等于浮力。",
            }
        },
    ]
    completions = FakeCompletions(payloads)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))

    result = regenerate_target(
        snapshot,
        target_type="interaction",
        target_id=target.interaction_id,
        instruction="改成测力计实验判断题",
        client=client,
    )

    changed = result.spec.interactions[0]
    assert changed.interaction_id == target.interaction_id
    assert changed.prompt != target.prompt
    assert completions.calls == 2


# ── 局部重生成里的配图 ────────────────────────────────


def _buoyancy_snapshot():
    brief = TeachingBrief(
        content_json={
            "teaching_goal": "理解浮力并解释生活现象",
            "target_audience": "初二学生",
            "duration_minutes": 45,
            "knowledge_points": [{"order": 1, "title": "浮力概念", "estimated_minutes": 20}],
            "logic_flow": ["导入", "实验", "应用", "总结"],
            "teaching_focus": "影响浮力大小的因素",
            "teaching_difficulties": "排开液体体积的理解",
            "output_types": ["pptx", "docx", "pdf", "html"],
        }
    )
    return compile_plan_content(brief, [])


def _client_for(payload):
    completions = FakeCompletions(payload)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions)), completions


def test_revision_ai_may_attach_an_offered_picture():
    """重写整页时，模型可以从候选清单里选一张图配上。"""
    snapshot = _buoyancy_snapshot()
    target = snapshot.slides[0]
    client, _ = _client_for(
        {
            "slide": {
                "title": "用测力计示数引入浮力",
                "image": {
                    "material_id": "mat_lab",
                    "placement": "right",
                    "caption": "两次读数对比",
                },
            }
        }
    )

    result = regenerate_target(
        snapshot,
        target_type="slide",
        target_id=target.slide_id,
        instruction="给这一页配一张实验照片",
        client=client,
        available_images=[
            {"material_id": "mat_lab", "name": "lab.png", "description": "测力计读数对比"}
        ],
    )

    image = result.spec.slides[0].image
    assert image is not None
    assert image.material_id == "mat_lab"
    assert image.placement == "right"
    assert image.caption == "两次读数对比"


def test_revision_ai_drops_a_picture_reference_that_was_not_offered():
    """模型编造或跨项目的图片引用必须被剔除，其余改动照常生效。"""
    snapshot = _buoyancy_snapshot()
    target = snapshot.slides[0]
    client, _ = _client_for(
        {"slide": {"title": "换个标题", "image": {"material_id": "mat_invented", "placement": "full"}}}
    )

    result = regenerate_target(
        snapshot,
        target_type="slide",
        target_id=target.slide_id,
        instruction="配一张图并改标题",
        client=client,
        available_images=[{"material_id": "mat_lab", "name": "lab.png"}],
    )

    assert result.spec.slides[0].title == "换个标题"
    assert result.spec.slides[0].image is None


def test_revision_ai_keeps_an_existing_picture_on_a_text_only_rewrite():
    """模型没提 image 时，之前绑定的配图必须原样保留。

    这里刻意不给候选清单：素材后来被删除或归档时，一次纯文字重写绝不能顺手
    把老师已经配好的图抹掉。
    """
    data = _buoyancy_snapshot().model_dump(mode="json")
    data["slides"][0]["image"] = {
        "material_id": "mat_bound_before",
        "placement": "background",
        "caption": "早就配好的图",
    }
    snapshot = CoursewarePlanSpec.model_validate(data)
    target = snapshot.slides[0]
    client, _ = _client_for({"slide": {"title": "只改标题"}})

    result = regenerate_target(
        snapshot,
        target_type="slide",
        target_id=target.slide_id,
        instruction="把标题改得更具体",
        client=client,
    )

    image = result.spec.slides[0].image
    assert image is not None
    assert image.material_id == "mat_bound_before"
    assert image.placement == "background"
    assert image.caption == "早就配好的图"
    assert result.spec.slides[0].title == "只改标题"
