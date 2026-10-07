"""互动教具：安全表达式、形状归一化、HTML 渲染。

背景：互动网页以前只能渲染"题目 + 答案分组"，所以无论怎么改提示词都只能产出做题页。
教具把"可动手探究的模型"独立出来——模型只写参数与公式，画布与读数由后端渲染。
这里盯住三件事：公式必须安全可算、坏教具必须被丢掉而不是拖垮整份蓝图、
产出的 HTML 必须真的带画布与控件。
"""

import json
import re
import shutil
import subprocess

import pytest

from backend.services.courseware_ai import _normalize_tools
from backend.services.expressions import (
    ExpressionError,
    compile_expression,
    evaluate,
    referenced_names,
    to_javascript,
)
from backend.services.generator import generate_html

# ── 安全表达式 ────────────────────────────────────────────


def test_expression_evaluates_and_translates_to_javascript():
    run, js_source = compile_expression("q / (pi * (d2/200)**2)", {"q", "d2"})
    assert run({"q": 0.004, "d2": 3}) == pytest.approx(5.6588, rel=1e-3)
    assert "Math.PI" in js_source and "**" in js_source
    assert referenced_names("q / (pi * (d2/200)**2)") == {"q", "d2"}


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('dir')",  # 代码执行
        "open('/etc/passwd').read()",       # 文件访问
        "(1).__class__",                    # 属性穿透
        "[x for x in range(3)]",            # 推导式
        "lambda x: x",                      # lambda
        "unknown_name + 1",                 # 未定义变量
        "q if q else 0",                    # 条件表达式
        "f'{q}'",                           # f-string
        "q @ d2",                           # 矩阵乘
    ],
)
def test_expression_rejects_unsafe_syntax(expression):
    with pytest.raises(ExpressionError):
        compile_expression(expression, {"q", "d2"})


def test_expression_rejects_division_by_zero_instead_of_crashing():
    with pytest.raises(ExpressionError):
        evaluate("1 / 0", {})


def test_javascript_translation_matches_python_for_allowed_functions():
    expression = "sqrt(v**2 + 1) + abs(0 - 2) + max(1, 2) + min(3, 4)"
    run, js_source = compile_expression(expression, {"v"})
    assert run({"v": 3}) == pytest.approx((10) ** 0.5 + 2 + 2 + 3)
    assert "Math.sqrt" in js_source and "Math.abs" in js_source


# ── 形状归一化 ────────────────────────────────────────────

FLUID_TOOL = {
    "tool_id": "tool_001",
    "title": "管径、流速与静压的联动",
    "goal": "学生能通过调节粗细管径说明流速大处压强小成立的条件",
    "engine": "pipe",  # 别名，应归一为 flow
    "model_note": "Q = A·v；p + ½ρv² = 常数",
    "variables": [
        {"key": "d1", "label": "粗管直径", "unit": "cm", "min": "4", "max": "12", "default": "8"},
        {"key": "d2", "label": "细管直径", "unit": "cm", "min": 6, "max": 1, "default": 99},
    ],
    "constants": {"q": "0.004", "p0": 120, "rho": 1000},
    "outputs": [
        {"key": "v2", "label": "细管流速", "unit": "m/s", "expression": "q / (pi * (d2/200)**2)"},
        {"key": "p2", "label": "细管静压", "unit": "kPa",
         "expression": "p0 - 0.5 * rho * (q / (pi * (d2/200)**2))**2 / 1000"},
    ],
    "predict_prompts": ["细管变细，压强往哪变？"],
    "guided_steps": ["先预测", "把细管调到 1cm", "解释现象并说明前提"],
}


def test_normalize_tools_coerces_shapes_and_engine_alias():
    tools = _normalize_tools([FLUID_TOOL])
    assert len(tools) == 1
    tool = tools[0]
    assert tool["engine"] == "flow"          # pipe → flow
    assert tool["order"] == 1
    first, second = tool["variables"]
    assert first["min"] == 4.0 and first["max"] == 12.0 and first["default"] == 8.0
    # 区间写反就换回来；默认值越界就夹回区间
    assert second["min"] == 1.0 and second["max"] == 6.0
    assert second["min"] <= second["default"] <= second["max"]
    assert tool["constants"]["q"] == pytest.approx(0.004)
    assert [output["key"] for output in tool["outputs"]] == ["v2", "p2"]


def test_normalize_tools_drops_bad_expression_but_keeps_good_ones():
    tool = json.loads(json.dumps(FLUID_TOOL))
    tool["outputs"].append({"key": "bad", "label": "坏公式", "expression": "q / (pi * (d3)**2)"})
    tools = _normalize_tools([tool])
    assert [output["key"] for output in tools[0]["outputs"]] == ["v2", "p2"]


def test_normalize_tools_drops_toy_without_effective_variable():
    """所有读数都不随参数变化 → 丢弃：滑块不能只是玩具。"""
    tool = json.loads(json.dumps(FLUID_TOOL))
    tool["variables"] = [{"key": "d1", "label": "没用", "min": 1, "max": 10, "default": 5}]
    tool["outputs"] = [{"key": "fixed", "label": "常数", "expression": "42"}]
    assert _normalize_tools([tool]) == []


def test_normalize_tools_survives_garbage():
    assert _normalize_tools(None) == []
    assert _normalize_tools("不是数组") == []
    assert _normalize_tools([None, 42, {}, {"variables": [], "outputs": []}]) == []


# ── HTML 渲染 ────────────────────────────────────────────

PLAN = {
    "title": "伯努利方程：管径变化如何影响流速与静压",
    "target_audience": "大学本科生",
    "duration_minutes": 45,
    "teaching_goal": "能解释管径变化引起的流速与压强变化",
    "knowledge_points": [{"point_id": "kp_1", "order": 1, "title": "连续性方程"}],
    "interactive_tools": _normalize_tools([FLUID_TOOL]),
    "interactions": [
        {
            "interaction_id": "interaction_001",
            "interaction_type": "classification",
            "title": "判断哪些说法成立",
            "prompt": "归类。",
            "items": ["流速大处压强小"],
            "answer_groups": {"成立": ["流速大处压强小"]},
        }
    ],
    "output_specs": {"html": {"tool_ids": ["tool_001"], "interaction_ids": ["interaction_001"]}},
}


def test_html_contains_simulation_canvas_controls_and_guidance(tmp_path, monkeypatch):
    from backend.config import settings

    monkeypatch.setattr(settings, "output_dir", tmp_path)
    html = open(generate_html(PLAN), encoding="utf-8").read()

    # 画布 + 滑块 + 读数
    assert 'id="canvas_tool_001"' in html
    assert 'data-var="d2"' in html and 'type="range"' in html
    assert 'data-readout="tool_001:p2"' in html
    # 教具的引导与原理
    assert "先想一想再动手" in html and "先预测" in html
    assert "伯努利" in html
    # 检测环节仍在
    assert "检测环节" in html and "提交答案" in html
    # 公式变成具名函数注册表，且不收窄 CSP
    assert "const TOOL_COMPUTE" in html
    assert "eval(" not in html and "new Function" not in html


# 每个引擎一份最小可用教具：只要能证明"这个引擎真的被画出来了"
_ENGINE_TOOLS = {
    "flow": (
        "toolDrawFlow",
        [
            {"key": "d1", "label": "粗管", "min": 2, "max": 8, "default": 6},
            {"key": "d2", "label": "细管", "min": 1, "max": 4, "default": 2},
        ],
        [{"key": "v2", "label": "细管流速", "expression": "d1 / d2"}],
    ),
    "curve": (
        "toolDrawCurve",
        [{"key": "x", "label": "自变量", "min": 0, "max": 10, "default": 4}],
        [{"key": "y", "label": "因变量", "expression": "x * 2"}],
    ),
    "field": (
        "toolDrawField",
        [
            {"key": "px", "label": "位置 x", "min": -5, "max": 5, "default": 1},
            {"key": "py", "label": "位置 y", "min": -5, "max": 5, "default": 1},
        ],
        [{"key": "value", "label": "场值", "expression": "px**2 + py**2"}],
    ),
    "particles": (
        "toolDrawParticles",
        [{"key": "temp", "label": "温度", "min": 100, "max": 600, "default": 300}],
        [{"key": "speed", "label": "平均速率", "expression": "sqrt(temp)"}],
    ),
    "balance": (
        "toolDrawBalance",
        [
            {"key": "f1", "label": "左侧力", "min": 0, "max": 50, "default": 20},
            {"key": "f2", "label": "右侧力", "min": 0, "max": 50, "default": 30},
        ],
        [{"key": "net", "label": "合力差", "expression": "f2 - f1"}],
    ),
    "circuit": (
        "toolDrawCircuit",
        [
            {"key": "r1", "label": "电阻 1", "min": 1, "max": 20, "default": 4},
            {"key": "r2", "label": "电阻 2", "min": 1, "max": 20, "default": 8},
        ],
        [{"key": "current", "label": "电流", "expression": "12 / (r1 + r2)"}],
    ),
    "scene": (
        "toolDrawScene",
        [
            {"key": "w", "label": "角速度倍率", "min": 0.2, "max": 2, "default": 1},
        ],
        [{"key": "speed", "label": "线速度", "expression": "7.9 / w"}],
    ),
}

# 个别引擎需要的专属字段（矩阵与并排用例共用的最小图元集合）
_ENGINE_EXTRAS = {
    "scene": {
        "scene": {
            "entities": [
                {"kind": "circle", "cx": 440, "cy": 170, "r": 16, "fill": "accent"},
                {"kind": "circle", "cx": "440 + 140 * cos(w * t)",
                 "cy": "170 + 120 * sin(w * t)", "r": 8, "fill": "primary"},
                {"kind": "readout", "x": 60, "y": 40, "output": "speed"},
            ]
        }
    },
}


@pytest.mark.parametrize("engine", sorted(_ENGINE_TOOLS))
def test_every_engine_is_really_rendered(tmp_path, monkeypatch, engine):
    """七个引擎都必须真的实现，而不是被悄悄降级成 curve。"""
    from backend.config import settings
    from backend.schemas import RENDERED_TOOL_ENGINES, TOOL_ENGINES

    assert engine in TOOL_ENGINES
    assert engine in RENDERED_TOOL_ENGINES

    draw_function, variables, outputs = _ENGINE_TOOLS[engine]
    tool = {
        "tool_id": f"tool_{engine}",
        "title": f"{engine} 教具",
        "goal": "学生能通过调节参数观察结果变化",
        "engine": engine,
        "variables": variables,
        "outputs": outputs,
        "predict_prompts": ["先预测再动手"],
        "guided_steps": ["先预测", "调节参数", "解释现象"],
    }
    tool.update(_ENGINE_EXTRAS.get(engine, {}))
    tools = _normalize_tools([tool])
    assert len(tools) == 1 and tools[0]["engine"] == engine

    monkeypatch.setattr(settings, "output_dir", tmp_path)
    plan = json.loads(json.dumps(PLAN))
    plan["interactive_tools"] = tools
    html = open(generate_html(plan), encoding="utf-8").read()
    assert draw_function in html
    assert f'id="canvas_tool_{engine}"' in html
    if engine == "scene":
        assert 'data-scene-toggle="tool_scene"' in html
        assert 'data-scene-reset="tool_scene"' in html


def test_seven_tools_render_side_by_side(tmp_path, monkeypatch):
    """一课里放多个教具时互不干扰：每个都有自己的画布与控件。"""
    from backend.config import settings

    tools = []
    for engine in sorted(_ENGINE_TOOLS):
        _, variables, outputs = _ENGINE_TOOLS[engine]
        tool = {
            "tool_id": f"tool_{engine}",
            "title": f"{engine} 教具",
            "goal": "观察参数与结果的关系",
            "engine": engine,
            "variables": variables,
            "outputs": outputs,
            "guided_steps": ["先预测", "调节参数", "解释现象"],
        }
        tool.update(_ENGINE_EXTRAS.get(engine, {}))
        tools.append(tool)
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    plan = json.loads(json.dumps(PLAN))
    plan["interactive_tools"] = _normalize_tools(tools)
    assert len(plan["interactive_tools"]) == 7
    html = open(generate_html(plan), encoding="utf-8").read()
    for engine in _ENGINE_TOOLS:
        assert f'id="canvas_tool_{engine}"' in html
    assert html.count('type="range"') >= 8


def test_html_without_tools_is_unchanged(tmp_path, monkeypatch):
    """老蓝图（没有教具）渲染结果不带任何教具痕迹，行为与以前一致。"""
    from backend.config import settings

    monkeypatch.setattr(settings, "output_dir", tmp_path)
    plan = json.loads(json.dumps(PLAN))
    plan.pop("interactive_tools")
    plan["output_specs"] = {"html": {"interaction_ids": ["interaction_001"]}}
    html = open(generate_html(plan), encoding="utf-8").read()
    assert "动手探究" not in html
    assert "TOOL_COMPUTE" not in html
    assert "检测环节" not in html
    assert "提交答案" in html


# ── scene 引擎：按教学目标自由搭建的动态场景 ──────────────
#
# 背景：旧引擎是有限的六个固定场景，模型想演示"随时间演化的过程"（轨道、振动、
# 波）时无处安放。scene 让模型按图元目录自由搭建，几何属性仍是受限表达式（作用域
# 里多了场景时钟 t 与采样参数 s），服务端编译成具名函数——安全红线不变。


def _orbit_tool():
    """万有引力一课的卫星轨道探究器；事故复盘用的坏版本就在测试里就地改坏。"""
    return {
        "tool_id": "tool_orbit",
        "title": "卫星轨道参数探究器",
        "goal": "学生能通过改变轨道半径观察线速度与周期的变化",
        "engine": "orbit",  # 别名，应归一为 scene
        "constants": {"g0": 9.8},
        "variables": [
            {"key": "r", "label": "轨道半径", "unit": "千公里",
             "min": 7000, "max": 42000, "default": 12000, "step": 100},
        ],
        "outputs": [
            {"key": "speed", "label": "线速度", "unit": "km/s", "expression": "7.9 * sqrt(6371 / r)"},
            {"key": "period", "label": "周期", "unit": "min",
             "expression": "2 * pi * sqrt(r**3 / 398600.4418) / 60"},
        ],
        "scene": {
            "background": "white",
            "entities": [
                {"kind": "circle", "cx": 440, "cy": 170, "r": 16, "fill": "accent"},
                {"kind": "path", "x": "440 + 200 * cos(s * 2 * pi)",
                 "y": "170 + 150 * sin(s * 2 * pi)", "stroke": "grid", "dash": [6, 6]},
                {"kind": "circle",
                 "cx": "440 + 200 * (r - 7000) / 35000 * cos(2 * pi * t * 60 / period)",
                 "cy": "170 + 150 * (r - 7000) / 35000 * sin(2 * pi * t * 60 / period)",
                 "r": 8, "fill": "primary"},
                {"kind": "line", "x1": 440, "y1": 170,
                 "x2": "440 + 200 * (r - 7000) / 35000", "y2": 170, "stroke": "steel"},
                {"kind": "readout", "x": 60, "y": 40, "output": "speed"},
                {"kind": "text", "x": 60, "y": 310, "content": "半径越大，线速度越小、周期越长"},
            ],
        },
        "predict_prompts": ["半径变大，线速度往哪变？"],
        "guided_steps": ["先预测", "拖动轨道半径", "用 v=sqrt(GM/r) 解释"],
    }


def test_scene_expressions_share_the_clock_and_sampler_scope():
    run, js_source = compile_expression("x0 + (x1 - x0) * s", {"x0", "x1", "s"})
    assert run({"x0": 40, "x1": 840, "s": 0.5}) == pytest.approx(440)
    run, js_source = compile_expression("440 + 200 * cos(2 * pi * t / T)", {"T", "t"})
    assert run({"T": 4, "t": 0}) == pytest.approx(640)
    assert "Math.PI" in js_source


def test_scene_tool_survives_normalization():
    tools = _normalize_tools([_orbit_tool()])
    assert len(tools) == 1
    tool = tools[0]
    assert tool["engine"] == "scene"  # orbit → scene
    entities = tool["scene"]["entities"]
    assert [entity["kind"] for entity in entities] == [
        "circle", "path", "circle", "line", "readout", "text",
    ]
    # 样式属性被钳制成确定值；表达式保持原始字符串（可持久化、可重复归一化）
    assert entities[1]["samples"] == 120
    assert entities[1]["dash"] == [6.0, 6.0]
    assert entities[1]["stroke"] == "grid"
    assert entities[2]["cx"].startswith("440 + 200")
    assert entities[4]["output"] == "speed"


def test_scene_drops_entity_that_references_undeclared_constants():
    """真实事故复刻：实体公式引用了未声明的 G、M —— 只丢这个实体，并留下原因。"""
    tool = _orbit_tool()
    tool["scene"]["entities"][2] = {
        "kind": "circle",
        "cx": "440 + 200 * cos(G * M / r ** 2 * t)",
        "cy": "170 + 150 * sin(G * M / r ** 2 * t)",
        "r": 8,
        "fill": "primary",
    }
    report = []
    tools = _normalize_tools([tool], report=report)
    assert len(tools) == 1 and tools[0]["engine"] == "scene"
    assert len(tools[0]["scene"]["entities"]) == 5
    scene_issues = [issue for issue in report if issue["stage"] == "scene"]
    assert scene_issues and "公式引用了未定义的量" in scene_issues[0]["reason"]
    assert scene_issues[0]["tool_id"] == "tool_orbit"


def test_scene_degrades_to_curve_when_unusable():
    """scene 不可用时保留参数与读数按 curve 渲染——比整件丢弃更有用，且报告原因。"""
    tool = _orbit_tool()
    tool["scene"] = {"entities": "不是列表"}
    report = []
    tools = _normalize_tools([tool], report=report)
    assert len(tools) == 1
    assert tools[0]["engine"] == "curve"
    assert tools[0]["scene"] == {}
    assert tools[0]["variables"] and tools[0]["outputs"]
    assert any("降级" in issue["reason"] for issue in report)


def test_scene_rejects_tools_that_use_the_clock_names():
    """t/s 被场景时钟与采样参数占用：教具自己再定义同名参数就得降级。"""
    tool = _orbit_tool()
    tool["variables"].append({"key": "t", "label": "时间", "min": 0, "max": 10, "default": 1})
    report = []
    tools = _normalize_tools([tool], report=report)
    assert tools[0]["engine"] == "curve"
    assert any("占用了 t" in issue["reason"] and "降级" in issue["reason"] for issue in report)


def test_scene_rejects_pure_decoration():
    """只随场景时钟变化、与滑块无关的是装饰动画，退回 curve。"""
    tool = _orbit_tool()
    tool["scene"]["entities"] = [
        {"kind": "circle", "cx": "440 + 100 * cos(2 * t)", "cy": 170, "r": 8},
    ]
    report = []
    tools = _normalize_tools([tool], report=report)
    assert tools[0]["engine"] == "curve"
    assert any("装饰" in issue["reason"] for issue in report)


def test_normalize_tools_report_is_optional():
    """不传 report 时行为与旧版一致：坏实体照样丢、工具照样降级，只是没有报告。"""
    tool = _orbit_tool()
    tool["scene"]["entities"][2]["cx"] = "G * M * t"
    tools = _normalize_tools([tool])
    assert len(tools) == 1 and tools[0]["engine"] == "scene"

    broken = _orbit_tool()
    broken["outputs"] = [
        {"key": "speed", "label": "线速度", "expression": "G * M / r"},
        {"key": "period", "label": "周期", "expression": "G * M / r ** 3"},
    ]
    assert _normalize_tools([broken]) == []


def test_scene_tool_renders_playback_controls_and_compiled_geometry(tmp_path, monkeypatch):
    from backend.config import settings

    tools = _normalize_tools([_orbit_tool()])
    assert len(tools) == 1 and tools[0]["engine"] == "scene"
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    plan = json.loads(json.dumps(PLAN))
    plan["interactive_tools"] = tools
    plan["output_specs"] = {"html": {"tool_ids": ["tool_orbit"]}}
    html = open(generate_html(plan), encoding="utf-8").read()

    assert "toolDrawScene" in html
    assert 'data-scene-toggle="tool_orbit"' in html and 'data-scene-reset="tool_orbit"' in html
    # 几何属性编译成具名函数，数据里以 compute 键引用；颜色 token 换成画布色值
    assert "_e3_cx" in html and '"compute"' in html
    assert "#8fb4e8" in html and '"grid"' not in html
    assert "eval(" not in html and "new Function" not in html


# ── 客户端绘制路径：真跑一遍页面脚本 ──────────────────────

_NODE = shutil.which("node")

# 只断言 HTML 里"有 toolDrawScene"挡不住这类事故：客户端的 toolSceneEntity
# 少接一个参数，读数图元每次绘制都抛 ReferenceError，又被逐实体的 try/catch
# 吞掉——画布上整整一类图元悄悄消失，字符串断言全绿（2026-10-05 实测）。
# 这段 harness 把导出的内联脚本放进 Node，用带记录的假画布真跑一次绘制：
# 任何图元抛错都会被记进 toolSceneWarned，空集合即"没有被吞掉的图元"。
_NODE_HARNESS = """
const fs = require('fs');
const script = fs.readFileSync(process.argv[2], 'utf8');
const drawn = [];
const context = new Proxy({}, {
  get(target, prop) {
    if (prop === 'measureText') return (text) => ({ width: String(text).length * 8 });
    if (prop === 'fillText') return (text) => { drawn.push(String(text)); };
    return () => {};
  },
  set() { return true; },
});
globalThis.window = globalThis;
// 桩元素：任何 DOM 方法都是空操作，属性写入可读回；画布方法接到记录用 context。
const makeElement = () => {
  const store = {};
  const target = {
    style: {},
    dataset: {},
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    width: 880,
    height: 340,
    children: [],
    getContext: () => context,
  };
  return new Proxy(target, {
    get(t, prop) {
      if (typeof prop === 'symbol' || prop in t) return t[prop];
      if (prop in store) return store[prop];
      return () => {};
    },
    set(t, prop, value) { store[prop] = value; return true; },
  });
};
globalThis.document = {
  readyState: 'complete',
  // 滑块返回 null，让 toolReadValues 回落到变量默认值；其余 DOM 接线用桩元素顶住。
  querySelector: (selector) => (String(selector).startsWith('input[') ? null : makeElement()),
  querySelectorAll: () => [],
  addEventListener: () => {},
  getElementById: () => makeElement(),
  createElement: () => makeElement(),
  body: makeElement(),
};
globalThis.matchMedia = () => ({ matches: false, addEventListener: () => {}, addListener: () => {} });
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};

const factory = new Function(
  script + ';return { toolDrawScene, toolReadValues, toolCall, toolSceneWarned, TOOL_DATA };',
);
const api = factory();
const tool = api.TOOL_DATA.filter((item) => item.engine === 'scene')[0];
const values = api.toolReadValues(tool);
const outputs = {};
(tool.outputs || []).forEach((output) => { outputs[output.key] = api.toolCall(output, values); });
api.toolDrawScene({ width: 880, height: 340, getContext: () => context }, tool, values, outputs);
console.log(JSON.stringify({
  warned: Object.keys(api.toolSceneWarned),
  drawn: drawn,
}));
"""


@pytest.mark.skipif(_NODE is None, reason="需要 Node 才能执行页面内联脚本")
def test_scene_client_draws_every_entity_kind_without_swallowing_errors(tmp_path, monkeypatch):
    """覆盖全部七种图元的绘制路径（rect、arrow 由本用例补进场景）。"""
    from backend.config import settings

    tools = _normalize_tools([_orbit_tool()])
    assert len(tools) == 1
    entities = tools[0]["scene"]["entities"]
    entities.append(
        {"kind": "arrow", "x1": 440, "y1": 170, "x2": "440 + 100 * cos(t)",
         "y2": "170 + 100 * sin(t)", "stroke": "accent", "head": 12}
    )
    entities.append(
        {"kind": "rect", "x": 700, "y": 40, "w": 120, "h": 60, "fill": "pale", "radius": 8}
    )
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    plan = json.loads(json.dumps(PLAN))
    plan["interactive_tools"] = tools
    html = open(generate_html(plan), encoding="utf-8").read()

    script_match = re.search(r"<script>\n(.*?)</script>", html, re.S)
    assert script_match, "页面里找不到内联脚本"
    (tmp_path / "scene_script.js").write_text(script_match.group(1), encoding="utf-8")
    (tmp_path / "harness.js").write_text(_NODE_HARNESS, encoding="utf-8")

    completed = subprocess.run(
        [_NODE, str(tmp_path / "harness.js"), str(tmp_path / "scene_script.js")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    assert result["warned"] == [], f"有图元绘制失败：{result['warned']}"
    drawn = "\n".join(result["drawn"])
    # readout 的完整形态是"标签 + 数值 + 单位"；只查"线速度"会被 text 图元里的
    # 提示语蒙混过关（变异验证过：撤掉修复后裸断言依然绿）。
    assert re.search(r"线速度 [\d.]+ km/s", drawn), drawn
    assert "半径越大" in drawn, drawn        # text 提示写上了画布
