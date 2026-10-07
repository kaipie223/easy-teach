"""提示词库：通用规则恒定保留，学科规则只注入给对应学科。

以前学科条款全部写死在主提示词里，一门语文课也会读到浮力、密码强度、拍值核算。
这里把"只给对应学科"这个行为钉住：以后往 SUBJECT_PROFILES 里加条款，必须按学科归类，
不能再堆回通用部分。
"""

from backend.services.prompt_library import (
    build_generation_prompt,
    build_review_prompt,
    normalize_stage,
    normalize_subject,
)


def test_subject_and_stage_names_are_normalized():
    assert normalize_subject("英语") == "外语"
    assert normalize_subject("编程") == "信息技术"
    assert normalize_subject("道法") == "道德与法治"
    # 识别不出就不注入学科块，只走通用要求
    assert normalize_subject("冷门学科") is None
    assert normalize_subject(None) is None
    assert normalize_stage("高一") == "高中"
    assert normalize_stage("小学三年级") == "小学"
    assert normalize_stage(None) is None


def test_generation_prompt_keeps_generic_rules_for_every_subject():
    for subject in (None, "语文", "物理", "音乐"):
        prompt = build_generation_prompt(subject, "高一")
        # 与学科无关的通用条款必须一直存在
        assert "只返回 JSON 对象" in prompt, subject
        assert "layout" in prompt, subject
        assert "available_images" in prompt, subject
        assert "双星号" in prompt, subject


def test_subject_structure_is_injected_only_for_the_matching_subject():
    assert "语篇" in build_generation_prompt("语文", "高一")
    assert "变量控制" in build_generation_prompt("物理", "初三")
    # 语文课不该被灌进理科的实验要求
    assert "变量控制" not in build_generation_prompt("语文", "高一")


def test_review_prompt_carries_only_this_subject_traps():
    physics = build_review_prompt("物理")
    chinese = build_review_prompt("语文")
    assert "浮力" in physics
    # 浮力条款与语文无关，不该出现
    assert "浮力" not in chinese
    assert "三原色" not in physics
    assert "三原色" in build_review_prompt("美术")
    # 通用审校条款对所有学科都在
    assert "事实" in chinese
    assert "互动题" in chinese


def test_unknown_subject_falls_back_to_generic_rules_only():
    generic = build_generation_prompt(None, None)
    assert "学段适配" not in generic
    assert "学科适配" not in generic
    assert "只返回 JSON 对象" in generic
