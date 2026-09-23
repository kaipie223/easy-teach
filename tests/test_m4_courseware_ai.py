import json
from types import SimpleNamespace

from backend.schemas import EvidenceRef
from backend.services.courseware_ai import PROMPT_VERSION, generate_courseware_spec


class FakeCompletions:
    def __init__(self, payloads, finish_reasons=None):
        self.payloads = list(payloads)
        self.finish_reasons = list(finish_reasons or ["stop"] * len(self.payloads))
        self.calls = 0
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        payload = self.payloads[self.calls]
        finish_reason = self.finish_reasons[self.calls]
        self.calls += 1
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=payload if isinstance(payload, str) else json.dumps(payload)
                    ),
                    finish_reason=finish_reason,
                )
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=200, total_tokens=300),
        )


def fake_client(*payloads):
    completions = FakeCompletions(payloads)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        completions_spy=completions,
    )


def fake_client_with_finish_reasons(payloads, finish_reasons):
    completions = FakeCompletions(payloads, finish_reasons)
    return SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        completions_spy=completions,
    )


def brief_content():
    return {
        "teaching_goal": "理解浮力并解释生活中的浮力现象",
        "target_audience": "初二学生",
        "duration_minutes": 45,
        "knowledge_points": [
            {"order": 1, "title": "浮力概念", "estimated_minutes": 15},
            {"order": 2, "title": "阿基米德原理", "estimated_minutes": 20},
        ],
        "logic_flow": ["情境导入", "实验探究", "规律应用", "总结"],
        "teaching_focus": "影响浮力大小的因素",
        "teaching_difficulties": "阿基米德原理的理解与应用",
        "output_types": ["pptx", "docx", "html"],
        "style_preference": "实验探究",
    }


def valid_plan():
    slides = [
        {
            "slide_id": f"untrusted-{index}",
            "order": 99,
            "title": f"浮力探究 {index}",
            "purpose": "通过具体实验建立浮力认识",
            "bullets": ["观察弹簧测力计示数", "比较物体浸入前后的变化"],
            "speaker_notes": "引导学生先记录数据，再解释变化原因。",
        }
        for index in range(1, 7)
    ]
    return {
        "title": "浮力及其应用",
        "target_audience": "会被后端覆盖",
        "duration_minutes": 1,
        "teaching_goal": "会被后端覆盖",
        "knowledge_points": [],
        "logic_flow": [],
        "slides": slides,
        "lesson_sections": [
            {
                "section_id": "bad-a",
                "order": 8,
                "title": "实验观察",
                "duration_minutes": 20,
                "objective": "发现物体浸入液体后测力计示数变化",
                "teacher_actions": ["组织测量并追问数据差异"],
                "student_actions": ["测量、记录并比较数据"],
                "assessment": "能根据数据判断浮力方向",
            },
            {
                "section_id": "bad-b",
                "order": 9,
                "title": "规律应用",
                "duration_minutes": 25,
                "objective": "运用阿基米德原理解释现象",
                "teacher_actions": ["提供轮船和潜水艇案例"],
                "student_actions": ["用排开液体体积解释案例"],
                "assessment": "能完整说明浮力变化原因",
            },
        ],
        "interactions": [
            {
                "interaction_id": "bad-interaction",
                "interaction_type": "ordering",
                "title": "实验步骤排序",
                "prompt": "按正确顺序排列浮力实验步骤",
                "items": ["测量空气中重力", "浸入水中", "记录示数"],
                "answer_groups": {
                    "正确顺序": ["测量空气中重力", "浸入水中", "记录示数"]
                },
            }
        ],
    }


def test_ai_blueprint_is_normalized_and_evidence_bound():
    client = fake_client(valid_plan(), valid_plan())
    refs = [
        EvidenceRef(
            evidence_id="evidence-1",
            source_type="material",
            source_name="浮力实验记录.pdf",
            quote="物体浸入水后测力计示数减小。",
        )
    ]

    result = generate_courseware_spec(brief_content(), refs, client=client)

    assert result.spec.target_audience == "初二学生"
    assert result.spec.duration_minutes == 45
    assert result.spec.slides[0].slide_id == "slide_001"
    assert result.spec.slides[0].evidence_refs[0].evidence_id == "evidence-1"
    assert sum(section.duration_minutes for section in result.spec.lesson_sections) == 45
    assert result.model_name == "deepseek-v4-flash"
    assert result.prompt_version == PROMPT_VERSION == "courseware-plan-v20-subject-adaptive"
    assert result.spec.output_specs.docx.homework
    assert result.spec.output_specs.pdf.assessment_checklist
    assert result.spec.output_specs.html.interaction_ids == ["interaction_001"]
    assert result.usage["total_tokens"] == 600
    assert client.completions_spy.calls == 2


def test_ai_blueprint_gets_one_controlled_repair():
    client = fake_client(None, valid_plan(), valid_plan())

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.title == "浮力及其应用"
    assert client.completions_spy.calls == 3


def test_ai_blueprint_regenerates_concisely_after_length_truncation():
    client = fake_client_with_finish_reasons(
        ['{"title":"被截断', valid_plan(), valid_plan()],
        ["length", "stop", "stop"],
    )

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.title == "浮力及其应用"
    assert client.completions_spy.calls == 3
    repair_payload = json.loads(
        client.completions_spy.requests[1]["messages"][1]["content"]
    )
    assert "长度限制" in repair_payload["task"]
    assert "invalid_output" not in repair_payload


def test_ai_blueprint_keeps_candidate_when_optional_review_hits_length_limit():
    client = fake_client_with_finish_reasons(
        [valid_plan(), '{"title":"审校被截断'],
        ["stop", "length"],
    )

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.title == "浮力及其应用"
    assert client.completions_spy.calls == 2
    assert "保留通过校验" in result.spec.generation_notes[-1]


def test_ai_blueprint_repairs_non_quiz_with_unassigned_items():
    invalid = valid_plan()
    invalid["interactions"][0]["answer_groups"] = {
        "正确顺序": ["测量空气中重力", "浸入水中"]
    }
    client = fake_client(invalid, valid_plan(), valid_plan())

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.interactions[0].answer_groups["正确顺序"][-1] == "记录示数"
    assert client.completions_spy.calls == 2


def test_ai_blueprint_drops_unanswerable_and_duplicate_classification_cards():
    payload = valid_plan()
    payload["interactions"][0].update(
        {
            "interaction_type": "classification",
            "items": ["木块", "铁块", "泡沫"],
            "answer_groups": {
                "漂浮": ["木块", "泡沫"],
                "下沉": ["铁块", "木块", "不存在的卡片"],
            },
        }
    )
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    interaction = result.spec.interactions[0]
    assert interaction.items == ["木块", "铁块", "泡沫"]
    assert interaction.answer_groups == {
        "漂浮": ["木块", "泡沫"],
        "下沉": ["铁块"],
    }


def test_ai_blueprint_scales_section_durations_to_exact_course_total():
    payload = valid_plan()
    payload["lesson_sections"][0]["duration_minutes"] = 70
    payload["lesson_sections"][1]["duration_minutes"] = 70
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    durations = [
        section.duration_minutes for section in result.spec.lesson_sections
    ]
    assert sum(durations) == 45
    assert all(duration >= 1 for duration in durations)
    assert client.completions_spy.calls == 2


def test_ai_blueprint_allows_quiz_distractors_outside_answer_groups():
    payload = valid_plan()
    payload["interactions"][0].update(
        {
            "interaction_type": "quiz",
            "items": ["浮力向上", "浮力向下", "没有浮力"],
            "answer_groups": {"正确": ["浮力向上"]},
        }
    )
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.interactions[0].answer_groups == {"正确": ["浮力向上"]}
    assert client.completions_spy.calls == 2


def test_ai_blueprint_numbers_repeated_ordering_cards():
    payload = valid_plan()
    interaction = payload["interactions"][0]
    interaction["items"].append("记录示数")
    interaction["answer_groups"]["正确顺序"].append("记录示数")
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.interactions[0].items == [
        "测量空气中重力",
        "浸入水中",
        "记录示数（1）",
        "记录示数（2）",
    ]
    assert result.spec.interactions[0].answer_groups["正确顺序"][-2:] == [
        "记录示数（1）",
        "记录示数（2）",
    ]
    assert client.completions_spy.calls == 2


def test_ai_blueprint_adds_missing_classification_labels_to_prompt():
    payload = valid_plan()
    payload["interactions"][0]["interaction_type"] = "classification"
    payload["interactions"][0]["prompt"] = "请完成分类"
    payload["interactions"][0]["answer_groups"] = {
        "实验前": ["测量空气中重力"],
        "实验中": ["浸入水中"],
        "实验后": ["记录示数"],
    }
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    prompt = result.spec.interactions[0].prompt
    assert all(label in prompt for label in ("实验前", "实验中", "实验后"))


def test_ai_blueprint_adds_local_standard_note_for_waste_classification():
    payload = valid_plan()
    payload["title"] = "垃圾分类"
    payload["slides"][0]["bullets"][1] = "回收1吨废纸可生产800千克再生纸"
    brief = brief_content()
    brief["teaching_goal"] = "理解垃圾分类并正确投放"
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief, [], client=client)

    assert "以授课地现行标准为准" in result.spec.slides[0].bullets[0]
    assert "1吨" not in "".join(result.spec.slides[0].bullets)
    assert "可靠来源" in "".join(result.spec.slides[0].bullets)


def test_ai_blueprint_normalizes_explicit_correct_and_incorrect_quiz_groups():
    invalid = valid_plan()
    invalid["interactions"][0].update(
        {
            "interaction_type": "quiz",
            "items": ["向上", "向下"],
            "answer_groups": {"正确": ["向上"], "错误": ["向下"]},
        }
    )
    client = fake_client(invalid, invalid)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.interactions[0].answer_groups == {"正确": ["向上"]}
    assert client.completions_spy.calls == 2


def test_ai_blueprint_normalizes_descriptive_quiz_group_labels():
    payload = valid_plan()
    payload["interactions"][0].update(
        {
            "interaction_type": "quiz",
            "items": ["sin 30° = 1/2", "sin 30° = 1", "sin 30° = 0"],
            "answer_groups": {
                "正确选项（参考答案）": ["sin 30° = 1/2"],
                "错误选项与干扰项": ["sin 30° = 1", "sin 30° = 0"],
            },
        }
    )
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.interactions[0].answer_groups == {
        "正确答案": ["sin 30° = 1/2"]
    }


def test_ai_blueprint_infers_quiz_answer_from_distractor_group():
    payload = valid_plan()
    payload["interactions"][0].update(
        {
            "interaction_type": "quiz",
            "items": ["π/6", "π/3", "π/2"],
            "answer_groups": {
                "干扰项": ["π/3", "π/2"],
                "错误答案": ["π/3", "π/2", "不在选项中"],
            },
        }
    )
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.interactions[0].answer_groups == {"正确答案": ["π/6"]}


def test_ai_blueprint_moves_mixed_rhythm_to_its_own_group():
    payload = valid_plan()
    payload["title"] = "音乐节奏型"
    payload["interactions"][0].update(
        {
            "interaction_type": "classification",
            "prompt": "请按音符类型分类",
            "items": ["ta", "ti-ti", "ta ti-ti"],
            "answer_groups": {
                "四分音符": ["ta"],
                "八分音符": ["ti-ti", "ta ti-ti"],
            },
        }
    )
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    interaction = result.spec.interactions[0]
    assert interaction.answer_groups["混合节奏"] == ["ta ti-ti"]
    assert "ta ti-ti" not in interaction.answer_groups["八分音符"]
    assert "混合节奏" in interaction.prompt


def test_ai_blueprint_adds_review_slide_when_model_returns_exactly_five():
    payload = valid_plan()
    payload["slides"] = payload["slides"][:5]
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert len(result.spec.slides) == 6
    assert result.spec.slides[-1].title == "课堂回顾与表达"
    assert result.spec.slides[-1].speaker_notes


def test_ai_blueprint_aligns_declared_bullet_limit_with_actual_slides():
    payload = valid_plan()
    payload["output_specs"] = {
        "pptx": {
            "narrative_arc": ["导入", "探究", "总结"],
            "visual_direction": "实验探究",
            "max_bullets_per_slide": 2,
            "speaker_notes_required": True,
        }
    }
    payload["slides"][0]["bullets"].append("形成实验结论")
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.output_specs.pptx.max_bullets_per_slide == 3


def test_ai_blueprint_collapses_numbered_ordering_groups():
    payload = valid_plan()
    payload["interactions"][0]["answer_groups"] = {
        "1": ["测量空气中重力"],
        "2": ["浸入水中"],
        "3": ["记录示数"],
    }
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.interactions[0].answer_groups == {
        "正确顺序": ["测量空气中重力", "浸入水中", "记录示数"]
    }


def test_ai_blueprint_qualifies_pigment_primary_colors():
    payload = valid_plan()
    payload["title"] = "幼儿颜色认知"
    payload["slides"][0]["bullets"][0] = "红、黄、蓝是三原色"
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert "指颜料混色" in result.spec.slides[0].bullets[0]


def test_ai_blueprint_corrects_common_iron_thiocyanate_equilibrium_formula():
    payload = valid_plan()
    payload["title"] = "化学平衡移动"
    payload["slides"][0]["bullets"][0] = "FeCl₃ + 3KSCN ⇌ Fe(SCN)₃ + 3KCl"
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.slides[0].bullets[0] == "Fe³⁺ + SCN⁻ ⇌ FeSCN²⁺"


def test_ai_blueprint_disambiguates_dark_reaction_quiz():
    payload = valid_plan()
    payload["title"] = "光合作用"
    payload["interactions"][0].update(
        {
            "interaction_type": "quiz",
            "prompt": "暗反应在黑暗条件下也能进行。",
            "items": ["正确", "错误"],
            "answer_groups": {"正确": ["正确"]},
            "explanation": "暗反应不需要光。",
        }
    )
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    interaction = result.spec.interactions[0]
    assert interaction.items == ["能", "不能"]
    assert interaction.answer_groups == {"正确": ["不能"]}
    assert "ATP" in interaction.explanation


def test_ai_blueprint_corrects_calvin_cycle_reduction_sequence():
    payload = valid_plan()
    payload["title"] = "光合作用"
    payload["slides"][0]["bullets"][0] = "C₃被NADPH还原为C₅和G3P"
    client = fake_client(payload, payload)

    result = generate_courseware_spec(brief_content(), [], client=client)

    bullet = result.spec.slides[0].bullets[0]
    assert "还原为G3P" in bullet
    assert "再生C₅" in bullet


def test_ai_blueprint_returns_independently_reviewed_content():
    candidate = valid_plan()
    candidate["slides"][0]["bullets"] = ["未经审校的候选内容"]
    reviewed = valid_plan()
    reviewed["slides"][0]["bullets"] = ["经过事实审校的课堂内容"]
    client = fake_client(candidate, reviewed)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.slides[0].bullets == ["经过事实审校的课堂内容"]
    assert "独立 AI 审校" in result.spec.generation_notes[-1]
    assert result.usage["total_tokens"] == 600


def test_ai_blueprint_accepts_single_review_wrapper_without_skipping_validation():
    reviewed = valid_plan()
    reviewed["slides"][0]["bullets"] = ["包装内的审校结果"]
    client = fake_client(valid_plan(), {"reviewed_courseware": reviewed})

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.slides[0].bullets == ["包装内的审校结果"]
    assert client.completions_spy.calls == 2


def test_ai_blueprint_repairs_review_structure_without_using_unreviewed_candidate():
    reviewed = valid_plan()
    reviewed["slides"][0]["bullets"] = ["保留审校后的事实修正"]
    client = fake_client(valid_plan(), None, reviewed)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.slides[0].bullets == ["保留审校后的事实修正"]
    assert client.completions_spy.calls == 3
    assert result.usage["total_tokens"] == 900


def test_ai_blueprint_keeps_valid_candidate_when_review_repair_is_invalid():
    candidate = valid_plan()
    candidate["slides"][0]["bullets"] = ["已通过结构校验的候选内容"]
    client = fake_client(candidate, None, None)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.slides[0].bullets == ["已通过结构校验的候选内容"]
    assert "保留通过校验" in result.spec.generation_notes[-1]
    assert client.completions_spy.calls == 3


# ── AI 自动配图 ────────────────────────────────────────


def offered_picture():
    return {
        "material_id": "mat_buoyancy",
        "name": "浮力实验.png",
        "teacher_note": "课上演示的测力计读数",
        "description": "弹簧测力计挂着物体在空气与水中的两次读数对比",
        "keywords": ["弹簧测力计", "浮力"],
        "vision_status": "ready",
    }


def test_ai_may_attach_an_offered_picture_to_a_slide():
    """候选清单既进提示词，也决定哪些引用可以保留。"""
    plan = valid_plan()
    plan["slides"][1]["image"] = {
        "material_id": "mat_buoyancy",
        "placement": "full",
        "caption": "弹簧测力计示数对比",
    }
    client = fake_client(plan, plan)

    result = generate_courseware_spec(
        brief_content(), [], available_images=[offered_picture()], client=client
    )

    image = result.spec.slides[1].image
    assert image is not None
    assert image.material_id == "mat_buoyancy"
    assert image.placement == "full"
    assert image.caption == "弹簧测力计示数对比"

    # 清单必须真的进了请求，否则模型无从选择
    request_body = json.loads(client.completions_spy.requests[0]["messages"][1]["content"])
    assert request_body["available_images"] == [offered_picture()]


def test_slide_picture_outside_the_offered_set_is_stripped():
    """模型编造或跨项目的图片引用必须被剔除；非法位置模式只退回默认。"""
    plan = valid_plan()
    plan["slides"][0]["image"] = {"material_id": "mat_invented", "placement": "right"}
    plan["slides"][1]["image"] = {"material_id": "mat_other_project", "placement": "right"}
    plan["slides"][2]["image"] = {"material_id": "mat_buoyancy", "placement": "diagonal"}
    client = fake_client(plan, plan)

    result = generate_courseware_spec(
        brief_content(), [], available_images=[offered_picture()], client=client
    )

    assert result.spec.slides[0].image is None
    assert result.spec.slides[1].image is None
    # 清单内但位置模式非法时只退回默认，不因为版面字段废掉整份蓝图
    assert result.spec.slides[2].image.material_id == "mat_buoyancy"
    assert result.spec.slides[2].image.placement == "right"


def test_no_slide_picture_is_kept_without_candidates():
    """一张候选图都没有时，模型不能凭空给任何一页配图。"""
    plan = valid_plan()
    plan["slides"][0]["image"] = {"material_id": "mat_whatever", "placement": "full"}
    client = fake_client(plan, plan)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert all(slide.image is None for slide in result.spec.slides)


# ── 关键词强调 ──────────────────────────────────────────


def test_emphasis_markup_is_limited_to_slide_bullets():
    """标记只在要点里保留；其它字段写入时就剥掉，避免文档出现裸星号。"""
    from backend.schemas import limit_emphasis_to_bullets

    payload = {
        "title": "**标题**不该带标记",
        "slides": [
            {
                "title": "**页标题**",
                "bullets": ["保留 **关键词** 标记"],
                "speaker_notes": "**讲稿**也不该带",
            }
        ],
        "output_specs": {"docx": {"homework": "**课后任务**"}},
    }

    cleaned = limit_emphasis_to_bullets(payload)

    assert cleaned["title"] == "标题不该带标记"
    assert cleaned["slides"][0]["title"] == "页标题"
    assert cleaned["slides"][0]["bullets"] == ["保留 **关键词** 标记"]
    assert cleaned["slides"][0]["speaker_notes"] == "讲稿也不该带"
    assert cleaned["output_specs"]["docx"]["homework"] == "课后任务"


def test_blueprint_keeps_bullet_emphasis_but_not_elsewhere():
    """端到端：要点保留标记，标题与讲稿里的标记被剥掉。"""
    plan = valid_plan()
    plan["title"] = "**浮力**及其应用"
    plan["slides"][0]["bullets"] = ["**弹簧测力计**示数差等于浮力"]
    plan["slides"][0]["speaker_notes"] = "**先预测**再测量。"
    client = fake_client(plan, plan)

    result = generate_courseware_spec(brief_content(), [], client=client)

    assert result.spec.title == "浮力及其应用"
    assert result.spec.slides[0].bullets == ["**弹簧测力计**示数差等于浮力"]
    assert result.spec.slides[0].speaker_notes == "先预测再测量。"
