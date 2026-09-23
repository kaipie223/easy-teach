"""Per-step red/green probes for docs/VIDEO_PARSER_FIX_PLAN.md.

WHY THIS FILE EXISTS
--------------------
`python -m pytest -q` is NOT a sufficient check for the video_parser fixes:
the test suite has essentially no coverage of `video_parser/` (only two test
files reference it, and neither exercises the code paths this plan touches).
Every step in the plan therefore needs its own check, or the executor has no
way to tell "fixed" from "still broken".

HOW TO USE
----------
    ./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py          # all
    ./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py s2.4     # one step
    ./.venv/Scripts/python.exe scripts/verify_video_parser_fixes.py --list

Each check asserts the **fixed** behaviour. It prints `[BUG]` when it observes
the **broken** behaviour described in the plan. So:

    BEFORE the fix : the corresponding check prints [BUG]
    AFTER  the fix : the corresponding check prints [OK]

A check that prints [BUG] *before* you start a step is the expected state --
that is the probe proving the bug is real. A check that prints [OK] after your
edit is evidence the step is done. If a check prints [BUG] after your edit, the
fix is incomplete, no matter what pytest says.

Checks marked `decision` depend on a human decision recorded in §0.5 of the
plan; they encode the recommended option and must be re-read if the decision
goes the other way.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class Broken(AssertionError):
    """Raised by a check when it observes the documented BUG."""


CHECKS: dict[str, tuple[str, object]] = {}


def check(step: str, title: str):
    def register(func):
        CHECKS[step] = (title, func)
        return func

    return register


# --------------------------------------------------------------------------
# shared fixtures
# --------------------------------------------------------------------------


def _video_result(*, duration: float, file_name: str, keyframe_path: str | None = None,
                  warnings: list[str] | None = None, transcript_text: str | None = None):
    """Minimal but valid VideoParseResult, optionally with one real keyframe."""
    from video_parser.schemas import (
        EvidenceItem,
        Keyframe,
        SourceVideo,
        TimeRange,
        Transcript,
        VideoMetadata,
        VideoParseResult,
    )

    keyframes = []
    evidence = []
    if keyframe_path is not None:
        keyframes.append(
            Keyframe(
                id="kf_0001",
                path=keyframe_path,
                timestamp_seconds=3.0,
                timecode="00:03.000",
                kind="sample",
                reason="generic_uniform_coverage",
            )
        )
        evidence.append(
            EvidenceItem(
                id="ev_keyframe_0001",
                evidence_type="keyframe",
                source_id="kf_0001",
                source_path=keyframe_path,
                time_range=TimeRange(
                    start_seconds=3.0, end_seconds=3.0, start="00:03.000", end="00:03.000"
                ),
                content="sample frame at 00:03.000.",
                metadata={"reason": "generic_uniform_coverage"},
            )
        )
    transcript = Transcript(status="not_requested")
    if transcript_text is not None:
        transcript = Transcript(status="completed", text=transcript_text)
    return VideoParseResult(
        video_id="probe-video",
        created_at="2026-08-27T00:00:00+00:00",
        source_video=SourceVideo(
            path=f"/tmp/{file_name}", file_name=file_name, file_size_bytes=1024, sha1="a" * 40
        ),
        metadata=VideoMetadata(duration_seconds=duration),
        transcript=transcript,
        keyframes=keyframes,
        evidence=evidence,
        warnings=list(warnings or []),
    )


def _circuit_result(*, duration: float = 3600.0, transcript_text: str | None = None):
    """A result that trips `_is_circuit_lesson` (file stem + ASR contain 通路/断路/短路)."""
    text = transcript_text if transcript_text is not None else (
        "通路就是电路接通，断路是某处断开，短路则是导线绕过了用电器。"
    )
    return _video_result(duration=duration, file_name="通路断路短路.mp4", transcript_text=text)


# --------------------------------------------------------------------------
# 阶段 1 -- 让解析能真正跑通
# --------------------------------------------------------------------------


@check("s1.1", "whisper 模型加载失败 -> 整个解析任务崩溃")
def check_s1_1() -> None:
    from video_parser.transcription import TranscriptionError, load_whisper_model

    try:
        load_whisper_model("__no_such_model_zzz__")
    except TranscriptionError:
        return
    except Exception as exc:  # noqa: BLE001
        raise Broken(
            f"裸异常穿透：{type(exc).__name__}: {str(exc)[:160]}。"
            "解析侧只捕获 TranscriptionError，会因此丢掉整次解析。"
            "（注：这条 ValueError 来自 faster-whisper 的本地模型名白名单，不需要联网即可复现；"
            "真实下载失败抛的是 LocalEntryNotFoundError / OSError，同样必须被包住。）"
        ) from exc
    raise Broken("load_whisper_model 对不存在的模型没有抛任何异常")


@check("s1.2", "缺 opencc -> 整个 ASR 结果被丢弃")
def check_s1_2() -> None:
    from video_parser import text_normalization

    saved = sys.modules.get("opencc", "MISSING")
    sys.modules["opencc"] = None  # type: ignore[assignment]  # makes `import opencc` raise ImportError
    try:
        text_normalization._converter.cache_clear()  # noqa: SLF001
        try:
            got = text_normalization.to_simplified_chinese("測試")
        except Exception as exc:  # noqa: BLE001
            raise Broken(
                f"缺 opencc 时抛 {type(exc).__name__}: {str(exc)[:120]}。"
                "调用点（transcription.py:60 的 except Exception）会把它放大成 TranscriptError，"
                "整段 ASR 结果被丢弃。正确行为是返回原文并记 warning。"
            ) from exc
        if got != "測試":
            raise Broken(f"缺 opencc 时应原样返回输入，实际返回 {got!r}")
    finally:
        if saved == "MISSING":
            sys.modules.pop("opencc", None)
        else:
            sys.modules["opencc"] = saved  # type: ignore[assignment]
        text_normalization._converter.cache_clear()  # noqa: SLF001


@check("s1.3", "缺 ffmpeg/ffprobe 没有任何预检")
def check_s1_3() -> None:
    """Static check: the entry point must probe for the binaries, not just a config flag."""
    src = (REPO_ROOT / "backend" / "services" / "materials.py").read_text(encoding="utf-8")
    router = (REPO_ROOT / "backend" / "routers" / "materials.py").read_text(encoding="utf-8")
    haystack = src + router
    if "shutil.which" in haystack and "ffmpeg" in haystack:
        return
    raise Broken(
        "backend 里没有任何 ffmpeg/ffprobe 可用性探测（grep 不到 shutil.which + ffmpeg）。"
        "现在用户在缺 ffmpeg 的部署上能上传、能排队，直到解析阶段才以 FFmpegError 失败。"
    )


@check("s1.4", "ffmpeg/ffprobe 子进程没有 timeout")
def check_s1_4() -> None:
    from video_parser.ffmpeg import FFmpegError, _run_command

    start = time.time()
    try:
        _run_command([sys.executable, "-c", "import time; time.sleep(8)"])
    except FFmpegError as exc:
        if time.time() - start >= 7.5:
            raise Broken(f"超时抛了 FFmpegError，但耗时 {time.time() - start:.1f}s，说明没在超时点中断") from exc
        return
    raise Broken(
        f"命令跑满 {time.time() - start:.1f}s 才返回，_run_command 没有 timeout。"
        "网络盘掉线 / 畸形文件会让 worker 被永久占住且无取消路径。"
    )


# --------------------------------------------------------------------------
# 阶段 2 -- 清掉伪造与无关内容
# --------------------------------------------------------------------------

_CIRCUIT_WORDS = ("通路", "断路", "短路", "LED", "正负极", "电源")


@check("s2.1", "markdown 总结无条件写入硬编码电路课内容")
def check_s2_1() -> None:
    from video_parser.package import build_teaching_content_package
    from video_parser.planning import build_demo_generation_plan
    from video_parser.rendering import _render_markdown_summary

    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        result = _video_result(
            duration=120.0,
            file_name="三角形的面积.mp4",
            transcript_text="这个三角形面积的计算，我们先看底和高，然后相乘再除以二。",
        )
        package = build_teaching_content_package(result, tmp / "pkg")
        plan = build_demo_generation_plan(package)
        out = tmp / "content_summary.md"
        _render_markdown_summary(package, plan, out)
        text = out.read_text(encoding="utf-8")
        hits = [w for w in _CIRCUIT_WORDS if w in text]
        if hits:
            raise Broken(
                f"三角面积课的 content_summary.md 里出现电路课内容 {hits}。"
                "该文件在 artifact_manifest.json 里是正式交付产物。"
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@check("s2.2", "电路兜底模板写入伪造的证据时间戳")
def check_s2_2() -> None:
    """With ZERO evidence in the input, no block may cite a clock time as its source.

    `correction_reason` strings like “对应视频 03:23–04:05 的灯泡内部断开示例” are
    literal constants in `_build_circuit_ir`; they assert “at 03:23 the video said X”
    against a video that has no evidence at those times (in this probe: no evidence
    at all).  A correct fix either derives the citation from real evidence or drops
    the clock reference.
    """
    import re

    from video_parser.ir_builder import build_teaching_content_ir

    result = _circuit_result(duration=3600.0)
    if result.evidence:
        raise Broken("探针失效：输入不该有证据 —— 请人工确认")
    ir = build_teaching_content_ir(result)
    if not ir.teaching_units:
        raise Broken("电路兜底路径没有产出任何单元，探针失效 —— 请人工确认")
    cited = []
    for unit in ir.teaching_units:
        for block in unit.content_blocks:
            reason = str((block.metadata or {}).get("correction_reason") or "")
            found = re.findall(r"\d{1,2}:\d{2}", reason)
            if found:
                cited.append(f"{block.id}{found}")
    if cited:
        raise Broken(
            f"输入完全没有证据，却有 {len(cited)} 个 block 在 correction_reason 里引用具体时间点："
            f"{', '.join(cited[:6])}。这些时间戳是模板字面量，属于编造“视频 03:23 讲了 X”。"
        )


@check("s2.3", "电路兜底把 quality 写死，真实失败信号全丢")
def check_s2_3() -> None:
    from video_parser.ir_builder import build_teaching_content_ir

    marker = "parser warning: OCR failed (probe-marker-9f3a)"
    result = _circuit_result(duration=3600.0)
    result.warnings.append(marker)
    ir = build_teaching_content_ir(result)
    if marker not in ir.quality.warnings:
        raise Broken(
            "喂进 parsed.warnings 的真实失败信号没有出现在 ir.quality.warnings 里。"
            "OCR/ASR/vision 全失败时审核看不到任何真实信号。"
        )
    if ir.quality.status not in {"ok", "warning", "error"}:
        raise Broken(f"quality.status 取值异常：{ir.quality.status!r}")


@check("s2.4", "电路兜底时间戳被硬钳 -> 大量单元退化成零长度区间")
def check_s2_4() -> None:
    from video_parser.ir_builder import build_teaching_content_ir

    result = _circuit_result(duration=60.0)
    ir = build_teaching_content_ir(result)
    if not ir.teaching_units:
        raise Broken("电路兜底路径没有产出任何单元，探针失效 —— 请人工确认")
    empty = [
        f"{u.id}[{u.time_range.start_seconds},{u.time_range.end_seconds}]"
        for u in ir.teaching_units
        if u.time_range.end_seconds <= u.time_range.start_seconds
    ]
    if empty:
        raise Broken(
            f"{len(empty)}/{len(ir.teaching_units)} 个单元是零长度区间：{', '.join(empty[:6])}。"
            "60 秒视频配 1101.067 的魔数时长 + min(..., duration) 钳位导致它们全部指向视频末尾同一瞬间。"
        )


@check("s2.5", "单元标题被替换成「补充说明（待复核）」")
def check_s2_5() -> None:
    from video_parser.intermediate_schemas import PackageTimeRange, TeachingUnit
    from video_parser.planning import _presentation_title

    def unit(topic: str) -> TeachingUnit:
        return TeachingUnit(
            id="u1",
            time_range=PackageTimeRange(start_seconds=0.0, end_seconds=10.0),
            topic=topic,
        )

    bad = []
    for topic in ("这个红色的圆形", "然后我们看三角形的面积", "对吧，锐角三角形的定义"):
        got = _presentation_title(unit(topic))
        if got == "补充说明（待复核）":
            bad.append(topic)
    if bad:
        raise Broken(
            f"这些口语化但完全正常的主题被换成了占位符标题：{bad}。"
            "noisy_markers 里的“这个/然后/对吧”是中文口语高频词，命中后就掉进电路专用重命名分支。"
        )
    normal = _presentation_title(unit("认识了红色和黄色"))
    if normal != "认识了红色和黄色":
        raise Broken(
            f"原本正确的 '认识了红色和黄色' 现在变成了 {normal!r} —— 修过头了，这是回归。"
        )


@check("s2.6", "`--no-answer-key` 去不掉答案（decision: 采用推荐选项 a）")
def check_s2_6() -> None:
    """Recommended option (a): with `include_answer_key=False`, answer text must not
    reach student-facing output (content pages or the interactive HTML spec).

    An `answer` block is currently `_is_presentable_block == True` with
    `_presentation_priority == 90`, so it lands on ordinary content slides; the
    switch only controls the EXTRA “参考答案” slide.
    """
    from video_parser.intermediate_schemas import ContentBlock, DemoGenerationRequest
    from video_parser.package import build_teaching_content_package
    from video_parser.planning import build_demo_generation_plan, build_interactive_spec

    answer_text = "不亮。回路断开后没有电流通过灯泡。"
    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        package = build_teaching_content_package(_circuit_result(duration=3600.0), tmp / "pkg")
        unit = package.ir.teaching_units[0]
        unit.content_blocks.extend(
            [
                ContentBlock(id="blk_q", block_type="question", text="开关断开后灯泡还亮吗？"),
                ContentBlock(
                    id="blk_a",
                    block_type="answer",
                    text=answer_text,
                    metadata={"source": "vision", "answer_text": answer_text},
                ),
            ]
        )
        request = DemoGenerationRequest(include_answer_key=False)
        plan = build_demo_generation_plan(package, request)

        leaked = [
            stage.stage_id
            for stage in plan.stages
            if any(answer_text in item for item in stage.content)
        ]
        if leaked:
            raise Broken(
                f"include_answer_key=False，但答案文本仍出现在内容页 {leaked} 的正文里"
                "（answer 块被判为可展示、优先级 90）。学生版直接就能看到答案。"
            )

        interactive = build_interactive_spec(package, plan)
        if answer_text in str(interactive.model_dump(mode="json")):
            raise Broken(
                "include_answer_key=False，但答案文本仍进入 InteractiveSpec —— "
                "它会原样写进 interactive_html/index.html 的 SPEC 常量，查看源码即可读到。"
                "客户端判题靠 correct_answer 比对，所以“学生版 + 交互页”本质冲突，"
                "需按 §0.5 决策 2 的选项处理。"
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 阶段 3 -- 渲染器与产物完整性
# --------------------------------------------------------------------------


@check("s3.1", "artifact-tool 路线指向他人机器的硬编码路径")
def check_s3_1() -> None:
    rendering = (REPO_ROOT / "video_parser" / "rendering.py").read_text(encoding="utf-8")
    if "jjjj" in rendering:
        raise Broken(
            "video_parser/rendering.py 里仍有另一个开发者的机器路径（C:\\Users\\jjjj\\...）。"
            "无论选 A 还是 B，这一行都必须消失：它对任何其他机器都是无效配置。"
        )


@check("s3.3", "`_render_docx` 的死代码让缺依赖时报裸 ImportError")
def check_s3_3() -> None:
    saved = sys.modules.get("docx", "MISSING")
    sys.modules["docx"] = None  # type: ignore[assignment]
    try:
        from video_parser.rendering import _render_docx_compact

        try:
            _render_docx_compact(None, None, Path(tempfile.gettempdir()) / "probe.docx")  # type: ignore[arg-type]
        except ImportError as exc:
            if "RenderingError" in type(exc).__name__:
                return
            from video_parser.rendering import RenderingError

            raise Broken(
                "缺 python-docx 时抛的是裸 ImportError，不是 RenderingError。"
                "结果是“PPTX 已写出、DOCX 缺失”的半个产物，且错误对调用方不可识别。"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            from video_parser.rendering import RenderingError

            if isinstance(exc, RenderingError):
                return
            raise Broken(
                f"缺 python-docx 时抛的是 {type(exc).__name__}: {str(exc)[:140]}，不是 RenderingError。"
            ) from exc
        raise Broken("缺 python-docx 时没有抛任何异常")
    finally:
        if saved == "MISSING":
            sys.modules.pop("docx", None)
        else:
            sys.modules["docx"] = saved  # type: ignore[assignment]


@check("s3.4", "从包里重新打包会静默丢掉全部图像")
def check_s3_4() -> None:
    from video_parser.package import (
        build_teaching_content_package,
        load_teaching_content_package,
    )

    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        frame = tmp / "frame.jpg"
        frame.write_bytes(b"\xff\xd8\xff\xe0" + b"0" * 64)
        result = _video_result(duration=30.0, file_name="lesson.mp4", keyframe_path=str(frame))

        first = build_teaching_content_package(result, tmp / "pkg1")
        if not first.manifest.assets:
            raise Broken("探针失效：首次打包就没有 assets —— 请人工确认")
        repacked = build_teaching_content_package(
            load_teaching_content_package(tmp / "pkg1").result, tmp / "pkg2"
        )
        if not repacked.manifest.assets:
            raise Broken(
                f"重新打包后 assets={len(repacked.manifest.assets)}（首次是 {len(first.manifest.assets)}）。"
                "包内 keyframe 路径是 assets/keyframes/... 相对路径，_resolve_keyframe_path 的候选根里"
                "没有“输入 JSON 自身所在目录”，所以全部解析失败且命令仍 exit 0。"
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 阶段 4 -- 分片串号与对齐正确性
# --------------------------------------------------------------------------


def _understanding(chapters_spec: list[tuple[str, str, float, float, str]]) -> object:
    """Build a VideoUnderstandingResult from (chapter_id, title, start, end, interval_id) rows."""
    from video_parser.schemas import (
        CandidateEvidenceInterval,
        VideoChapterCandidate,
        VideoUnderstandingResult,
    )

    chapters = []
    for chapter_id, title, start, end, interval_id in chapters_spec:
        chapters.append(
            VideoChapterCandidate(
                chapter_id=chapter_id,
                title=title,
                start_seconds=start,
                end_seconds=end,
                confidence=0.8,
                candidate_intervals=[
                    CandidateEvidenceInterval(
                        interval_id=interval_id,
                        start_seconds=start,
                        end_seconds=end,
                        confidence=0.8,
                    )
                ],
            )
        )
    return VideoUnderstandingResult(status="completed", chapters=chapters)


@check("s4.1", "跨分片 ID 重名 -> 章节串号 + 假冲突")
def check_s4_1() -> None:
    from video_parser.parser import _merge_video_understanding_results
    from video_parser.video_understanding import BailianVideoConfig

    chunk_a = _understanding([("chapter_1", "第一段", 30.0, 90.0, "interval_1")])
    chunk_b = _understanding([("chapter_1", "第二段", 1830.0, 1890.0, "interval_1")])
    merged = _merge_video_understanding_results(
        [chunk_a, chunk_b], 2400.0, BailianVideoConfig(api_key="probe"), {}, 0
    )
    chapter_ids = [c.chapter_id for c in merged.chapters]
    interval_ids = [
        i.interval_id for c in merged.chapters for i in c.candidate_intervals
    ]
    if len(set(chapter_ids)) != len(chapter_ids) or len(set(interval_ids)) != len(interval_ids):
        raise Broken(
            f"合并两个分片后 ID 重复：chapter_id={chapter_ids}，interval_id={interval_ids}。"
            "每个分片是独立的一次模型请求，都从 chapter_1/interval_1 开始编号，"
            "而 _merge_video_understanding_results 只重写 provenance、不重写 ID。"
            "下游 {candidate_id: decision} 建字典会“后写覆盖先写”，导致章节挂到另一分片的时间与证据上。"
        )


@check("s4.2", "重复/未覆盖冲突检查恒不成立")
def check_s4_2() -> None:
    from video_parser.schemas import AlignmentDecision
    from video_parser.video_alignment import _duplicate_or_uncovered_conflicts

    understanding = _understanding(
        [
            ("chapter_1", "一", 10.0, 60.0, "interval_dup"),
            ("chapter_1", "二", 900.0, 960.0, "interval_dup"),
        ]
    )
    decisions = [
        AlignmentDecision(
            candidate_id="interval_dup",
            original_start_seconds=10.0,
            original_end_seconds=60.0,
            aligned_start_seconds=10.0,
            aligned_end_seconds=60.0,
            score=0.9,
            status="accepted",
        )
    ]
    conflicts = _duplicate_or_uncovered_conflicts(understanding, decisions, [], 2400.0)
    if not conflicts:
        raise Broken(
            "两个不同的区间用了同一个 interval_id，检查却返回 0 条冲突。"
            "该函数用 `interval_id not in decision_ids`（集合成员）判断，而 align 为每个区间都追加了决策，"
            "所以分支恒假；形参 anchors 完全未使用、duration_seconds 被 del —— "
            "函数名承诺的“重复 id”和“未覆盖区间”两类检查都不存在。"
        )


@check("s4.3", "零长度区间：单条坏数据作废整个分片")
def check_s4_3() -> None:
    from video_parser.video_understanding import (
        BailianVideoConfig,
        VideoRequest,
        BailianVideoClient,
    )

    client = BailianVideoClient(
        BailianVideoConfig(api_key="probe", cache_enabled=False),
        transport=lambda *a, **k: ({}, {}),
    )
    request = VideoRequest(
        endpoint="http://probe.invalid",
        payload={},
        headers={},
        timeout_seconds=10,
        input_mode="file_url",
        input_sha256="x",
        asr_sha256="y",
        chunk_start_seconds=0.0,
        chunk_end_seconds=2400.0,
    )
    payload = {
        "chapters": [
            {
                "chapter_id": "chapter_1",
                "title": "正常章节",
                "start_seconds": 10.0,
                "end_seconds": 60.0,
                "candidate_intervals": [
                    {"interval_id": "i1", "start_seconds": 10.0, "end_seconds": 60.0}
                ],
            },
            {
                "chapter_id": "chapter_2",
                "title": "零长度章节",
                "start_seconds": 100.0,
                "end_seconds": 100.0,
                "candidate_intervals": [
                    {"interval_id": "i2", "start_seconds": 100.0, "end_seconds": 100.0}
                ],
            },
        ]
    }
    try:
        result = client._convert_local_result(  # noqa: SLF001
            payload, request=request, video_duration_seconds=2400.0
        )
    except Exception as exc:  # noqa: BLE001
        raise Broken(
            f"一条 start==end 的坏章节让整批解析抛出 {type(exc).__name__}: {str(exc)[:140]}。"
            "服务端 schema 的 start/end 只有 minimum:0、没有 end>start，所以这种输出合法；"
            "而 pydantic 侧要求 end>start → model_validate 抛错 → 整片（含 90% 可用章节）被丢弃，"
            "用户看到的是“模型未返回可用分段”。"
        ) from exc
    if not result.chapters:
        raise Broken("零长度章节把可用的章节一起吞掉了：返回结果里一个 chapter 都没有")
    if len(result.chapters) != 1:
        raise Broken(
            f"期望只丢坏章节（保留 1 个），实际保留 {len(result.chapters)} 个 —— 请人工确认修法"
        )


@check("s4.4", "末位采样点 = duration -> 必然 FFmpegError")
def check_s4_4() -> None:
    """Any timestamp equal to duration_seconds makes ffmpeg fail, so extraction must
    be clamped to duration - epsilon (the other 7 extraction sites already do this)."""
    import video_parser.interval_refinement as refinement
    from video_parser.interval_refinement import RefinementConfig, refine_candidate_intervals

    recorded: list[float] = []
    real_extract = refinement.extract_frame

    def fake_extract(video_path, timestamp, path, **kwargs):
        recorded.append(round(float(timestamp), 4))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\xff\xd8\xff\xe0")
        return None

    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        video = tmp / "lesson.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42probe")
        refinement.extract_frame = fake_extract  # type: ignore[assignment]
        refine_candidate_intervals(
            video,
            _understanding([("chapter_1", "片尾", 3595.0, 3600.0, "interval_1")]),
            duration_seconds=3600.0,
            output_dir=tmp / "out",
            config=RefinementConfig(run_ocr=False, run_visual=False),
        )
    finally:
        refinement.extract_frame = real_extract  # type: ignore[assignment]
        shutil.rmtree(tmp, ignore_errors=True)

    if not recorded:
        raise Broken("探针失效：没有记录到任何抽帧时间戳 —— 请人工确认")
    at_end = [t for t in recorded if t >= 3600.0]
    if at_end:
        raise Broken(
            f"{len(at_end)} 个采样点等于视频总时长（{sorted(set(at_end))}）。"
            "buffered_end = min(duration_seconds, requested_end + buffer_seconds) 会取到 duration，"
            "而 plan_refinement_timestamps 保证末位 == end_seconds，于是以“视频总长”作 -ss 抽帧；"
            "extract_frame 末尾的 exists() 检查必然抛 FFmpegError，strict 模式下终止整个 refine 阶段。"
            "仓库里其它 7 处抽帧都 clamp 到 duration_seconds - 0.05，只有这一处没有。"
        )


@check("s4.5", "非法区间的“拒绝”分支自己抛 ValidationError")
def check_s4_5() -> None:
    from video_parser.schemas import CandidateEvidenceInterval, VideoUnderstandingResult
    from video_parser.video_alignment import align_video_understanding

    # model_construct bypasses the pydantic validator, which is exactly the
    # situation the reject branch exists for (see S4.5 reachability note).
    chapter = {
        "chapter_id": "chapter_1",
        "title": "非法区间",
        "start_seconds": 10.0,
        "end_seconds": 10.0,
        "candidate_intervals": [
            CandidateEvidenceInterval.model_construct(
                interval_id="interval_1",
                start_seconds=10.0,
                end_seconds=10.0,
                evidence_focus="general",
                rationale="",
                confidence=0.5,
                knowledge_point_ids=[],
            )
        ],
    }
    understanding = VideoUnderstandingResult.model_construct(
        status="completed",
        video_summary="",
        chapters=[__import__("video_parser.schemas", fromlist=["x"]).VideoChapterCandidate.model_construct(**chapter)],
        uncertainties=[],
        provenance=None,
        alignment_decisions=[],
        warnings=[],
    )
    try:
        alignment = align_video_understanding(understanding, duration_seconds=2400.0)
    except Exception as exc:  # noqa: BLE001
        raise Broken(
            f"start==end 的候选让对齐层抛出 {type(exc).__name__}: {str(exc)[:140]}。"
            "该分支本意是返回 status='rejected' 的决策并记一条 high 冲突，"
            "但 AlignmentDecision.validate_ranges 要求 end>start，构造决策时先被 pydantic 拒绝 —— "
            "结果既没有 rejected 决策也没有冲突记录，破坏了“对齐层永远保留可审阅记录”。"
        ) from exc
    rejected = [
        d
        for d in alignment.understanding.alignment_decisions
        if getattr(d, "status", None) == "rejected"
    ]
    if not rejected:
        raise Broken("非法候选没有产出 status='rejected' 的决策，可审阅记录仍然丢失")


@check("s4.6", "block_* ID 冲突：evidence_id 参数被忽略")
def check_s4_6() -> None:
    from video_parser.ir_builder import _visual_block

    payload = {"block_type": "text", "text": "样例文字", "status": "observed"}
    first = _visual_block("seg_0001", 0, payload, "ev_visual_0001", None)
    second = _visual_block("seg_0001", 0, payload, "ev_visual_0002", None)
    if first.id == second.id:
        raise Broken(
            f"同一 segment 下两条 visual evidence 生成了相同的 block id：{first.id!r}。"
            "index 只是单条 visual evidence 内部的 blocks 下标，evidence_id 作为形参传入却不参与 id 生成。"
            "一个镜头内落多个采样关键帧是常态（约每 8 秒一帧，segmenter 把同 shot 的关键帧全放进同一 segment），"
            "下游 _build_relations 用 block id 作 from_id/to_id，planning 把它拼进 element id。"
        )


# --------------------------------------------------------------------------
# 阶段 5 -- 静默失真与成本
# --------------------------------------------------------------------------


def _fps_keys(node: object, path: str = "") -> list[str]:
    """Recursively collect every dict key named 'fps' (case-insensitive)."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if str(key).lower() == "fps":
                found.append(f"{here}={value!r}")
            found.extend(_fps_keys(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_fps_keys(value, f"{path}[{index}]"))
    return found


@check("s5.1", "fps 从未随视频元素发送 -> 帧预算差 2 倍")
def check_s5_1() -> None:
    from video_parser.video_understanding import BailianVideoConfig, BailianVideoClient

    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        video = tmp / "lesson.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42probe")
        client = BailianVideoClient(
            BailianVideoConfig(api_key="probe", cache_enabled=False),
            transport=lambda *a, **k: ({}, {}),
        )
        request = client.build_request(
            video, asr_segments=None, video_duration_seconds=600.0
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    keys = _fps_keys(request.payload)
    if not keys:
        raise Broken(
            "请求体里没有作为字段传递的 fps —— 本地只在提示词文本里“声明”了 FPS，"
            "服务端按默认 2.0 抽样，而本地预算/缓存键/provenance 全按 1.0 计算。"
            "默认配置下实际抽帧是预算的 2 倍，超过单请求 2000 帧上限，"
            "长于约 16.7 分钟的视频每个分片请求超限失败。"
        )
    if abs(float(str(keys[0]).split("=")[-1].strip("'} ")) - 1.0) > 1e-6:
        raise Broken(f"请求体里的 fps 与本地预算不一致：{keys}（本地 config.fps=1.0）")


def _conformant_response_text(chapters: list[dict] | None = None) -> str:
    """A response body that satisfies the strict VIDEO_UNDERSTANDING_RESPONSE_SCHEMA."""
    import json

    return json.dumps(
        {
            "video_summary": "探针用摘要",
            "uncertainties": [],
            "chapters": chapters
            if chapters is not None
            else [
                {
                    "chapter_id": "chapter_1",
                    "title": "章节",
                    "summary": "章节摘要",
                    "start_seconds": 1.0,
                    "end_seconds": 30.0,
                    "confidence": 0.8,
                    "asr_segment_ids": [],
                    "knowledge_points": [],
                    "candidate_intervals": [
                        {
                            "interval_id": "i1",
                            "start_seconds": 1.0,
                            "end_seconds": 30.0,
                            "evidence_focus": "general",
                            "rationale": "",
                            "confidence": 0.8,
                            "knowledge_point_ids": [],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )


@check("s5.2", "视频理解缓存键缺少 video_type")
def check_s5_2() -> None:
    """Changing video_type changes the prompt, so it must not reuse the old cache."""
    from video_parser.video_understanding import BailianVideoConfig, BailianVideoClient

    response_text = _conformant_response_text()
    calls: list[str] = []

    def transport(endpoint, payload, headers, timeout):
        calls.append(str(payload))
        return (
            {
                "model": "probe-model",
                "id": "probe-request",
                "usage": {},
                "choices": [{"message": {"content": response_text}}],
            },
            {},
        )

    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        video = tmp / "lesson.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42probe")
        config = BailianVideoConfig(
            api_key="probe", cache_enabled=True, cache_dir=tmp / "cache"
        )
        for video_type in ("presentation", "whiteboard"):
            client = BailianVideoClient(config, transport=transport)
            client.analyze_video(
                video,
                asr_segments=None,
                video_duration_seconds=600.0,
                video_type=video_type,
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if len(calls) < 2:
        raise Broken(
            f"只调用了模型 {len(calls)} 次：换 video_type 后命中了上一次的缓存。"
            "prompt 里含“课程类型：{video_type}”，但 make_video_cache_key 的入参没有 video_type，"
            "于是返回的是上一次（不同 prompt、不同视角）的结果，用户无法察觉。"
            "注意：修好后旧缓存会全部失效，这是预期效果，请在报告里说明。"
        )


@check("s5.3", "文本密度门槛形同虚设 -> 空白帧全量进入付费 OCR")
def check_s5_3() -> None:
    from PIL import Image

    from video_parser.interval_refinement import RefinementConfig, estimate_text_density

    threshold = RefinementConfig().text_density_threshold
    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        blank = tmp / "blank.png"
        Image.new("RGB", (640, 360), (255, 255, 255)).save(blank)
        density = estimate_text_density(blank)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if density >= threshold:
        raise Broken(
            f"纯白空白帧的文本密度是 {density:.5f}，仍然 >= 阈值 {threshold} —— 门槛等于不存在。"
            "缩到 160×90 后压缩噪点/安全框/边缘底噪就足以超过它，判定退化为“全部通过”。"
            "ocr=True 时单视频最多约 2880 次腾讯云 GeneralAccurateOCR 付费调用。"
        )


def _install_fake_dashscope(record_endpoint: bool = True):
    """Install a fake `dashscope` module and return (module, captured calls)."""
    import types

    module = types.ModuleType("dashscope")
    module.api_key = None
    module.base_http_api_url = "https://dashscope.aliyuncs.com/api/v1"
    captured: list[dict] = []

    class _MultiModalConversation:
        @staticmethod
        def call(**kwargs):
            captured.append(
                {
                    "kwargs": dict(kwargs),
                    "base_http_api_url": module.base_http_api_url,
                }
            )
            return {
                "status_code": 200,
                "id": "probe-request",
                "output": {"choices": [{"message": {"content": "{}"}}]},
            }

    module.MultiModalConversation = _MultiModalConversation
    saved = sys.modules.get("dashscope", "MISSING")
    sys.modules["dashscope"] = module
    return module, captured, saved


def _restore_fake_dashscope(saved) -> None:
    if saved == "MISSING":
        sys.modules.pop("dashscope", None)
    else:
        sys.modules["dashscope"] = saved


@check("s5.4", "SDK timeout 被当成请求参数发出，600s 配置完全无效")
def check_s5_4() -> None:
    from video_parser.video_understanding import _dashscope_sdk_call

    module, captured, saved = _install_fake_dashscope()
    try:
        _dashscope_sdk_call(
            model="probe-model",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            response_format={"type": "json_object"},
            timeout_seconds=600,
            api_key="probe-key",
            max_tokens=128,
        )
    finally:
        _restore_fake_dashscope(saved)

    if not captured:
        raise Broken("探针失效：SDK 没有被调用 —— 请人工确认")
    kwargs = captured[0]["kwargs"]
    if "timeout" in kwargs or "timeout_seconds" in kwargs:
        raise Broken(
            f"传给 MultiModalConversation.call 的仍是 {sorted(kwargs)} 里的 timeout 类关键字。"
            "call 没有这个形参，未知 kwargs 会被 add_parameters(**kwargs) 塞进请求体 parameters，"
            "同时真正的 HTTP 超时取 SDK 全局默认值 300 —— 配置的 600 完全无效，"
            "还向原生端点发送了一个未定义参数。SDK 的超时关键字是 request_timeout"
            "（dashscope/common/constants.py: REQUEST_TIMEOUT_KEYWORD）。"
        )
    if kwargs.get("request_timeout") != 600:
        raise Broken(
            f"没有用 request_timeout 传递超时：kwargs={sorted(kwargs)}。"
            "正确关键字是 dashscope 的 REQUEST_TIMEOUT_KEYWORD（'request_timeout'）。"
        )


@check("s5.5", "SDK 通路忽略 config.base_url，本地文件模式静默直连公网")
def check_s5_5() -> None:
    import json

    from video_parser.video_understanding import BailianVideoConfig, BailianVideoClient

    response_text = json.dumps({"chapters": []}, ensure_ascii=False)
    module, captured, saved = _install_fake_dashscope()
    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        video = tmp / "lesson.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42probe")
        config = BailianVideoConfig(
            api_key="probe",
            base_url="https://internal-gateway.example.com/api/v1",
            input_mode="file_url",
            cache_enabled=False,
        )
        client = BailianVideoClient(config)  # no transport -> SDK path
        try:
            client.analyze_video(video, asr_segments=None, video_duration_seconds=600.0)
        except Exception:  # noqa: BLE001 - the response body is irrelevant here
            pass
    finally:
        _restore_fake_dashscope(saved)
        shutil.rmtree(tmp, ignore_errors=True)

    if not captured:
        raise Broken(
            "探针失效：SDK 通路没有被走到（input_mode=file_url 且无 transport 时应走 SDK）—— 请人工确认"
        )
    used = captured[0]["base_http_api_url"]
    if "internal-gateway.example.com" not in str(used):
        raise Broken(
            f"SDK 请求实际发往 {used!r}，而不是配置里的 base_url。"
            "REST 通路用 config.endpoint，SDK 通路完全不看该配置、走全局默认公网端点，"
            "而默认 input_mode='auto' + 本地文件恰好走 SDK 通路 —— "
            "部署把 DASHSCOPE_BASE_URL 指向企业代理时请求会静默绕过代理直连公网。"
        )
    assert response_text  # keep the probe honest about what the model would return


@check("s5.6", "base64 / 本地文件体积上限与服务端限制不符")
def check_s5_6() -> None:
    """Two independent gaps: file_url has NO size check at all, and the base64
    branch compares raw file bytes instead of the 4/3-expanded encoded size."""
    import pathlib

    from video_parser.video_understanding import BailianVideoConfig, BailianVideoClient

    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    real_stat = pathlib.Path.stat
    try:
        huge = tmp / "huge.mp4"
        huge.write_bytes(b"\x00\x00\x00\x18ftypmp42probe")

        fake_size = 200 * 1024 * 1024  # 200 MiB, well over any plausible local limit

        def patched_stat(self, **kwargs):
            if self.name == "huge.mp4":
                class _S:
                    st_size = fake_size
                    st_mode = 0o100644

                return _S()
            return real_stat(self, **kwargs)

        pathlib.Path.stat = patched_stat  # type: ignore[assignment]
        client = BailianVideoClient(
            BailianVideoConfig(api_key="probe", input_mode="file_url", cache_enabled=False),
            transport=lambda *a, **k: ({}, {}),
        )
        try:
            client.build_request(huge, asr_segments=None, video_duration_seconds=600.0)
        except Exception:  # noqa: BLE001 - any explicit rejection is acceptable
            pass
        else:
            raise Broken(
                "file_url 分支对一个 200MiB 的本地文件没有任何体积检查就放行了。"
                "默认 input_mode='auto' 走的正是 file_url，所以默认配置下本地不设任何上限，"
                "整个文件直接交给 SDK/服务端上传。"
            )
    finally:
        pathlib.Path.stat = real_stat  # type: ignore[assignment]
        shutil.rmtree(tmp, ignore_errors=True)

    # base64: raw bytes just under the configured limit, but 4/3-expanded over it.
    limit = 3_000_000
    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        video = tmp / "big.mp4"
        raw_size = 2_400_000  # encoded ~3.2MB > limit
        video.write_bytes(b"\x00" * raw_size)
        client = BailianVideoClient(
            BailianVideoConfig(
                api_key="probe",
                input_mode="base64",
                max_base64_bytes=limit,
                cache_enabled=False,
            ),
            transport=lambda *a, **k: ({}, {}),
        )
        try:
            client.build_request(video, asr_segments=None, video_duration_seconds=600.0)
        except Exception:  # noqa: BLE001
            return
        raise Broken(
            f"base64 分支放行了原始 {raw_size} 字节（编码后约 {raw_size * 4 // 3}）的文件，"
            f"而配置的上限是 {limit}。该分支用 path.stat().st_size 与“base64 上限”比较，"
            "没有乘 4/3 编码系数 —— 变量语义是编码后体积，比对的却是原始字节。"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 阶段 6 -- 低危收尾
# --------------------------------------------------------------------------


@check("s6.1", "同一条 warning 写两次")
def check_s6_1() -> None:
    """`_handle_warning_or_raise` already appends on the non-strict path; a caller
    that appends the same message again double-counts it."""
    import re

    source = (REPO_ROOT / "video_parser" / "parser.py").read_text(encoding="utf-8").splitlines()
    duplicated: list[int] = []
    for index, line in enumerate(source):
        match = re.search(r"_handle_warning_or_raise\(\s*options,\s*warnings,\s*([A-Za-z_][\w.]*)\s*,\s*\w+\s*\)", line)
        if not match:
            continue
        name = match.group(1)
        window = "\n".join(source[index + 1 : index + 4])
        if re.search(rf"\bwarnings\.append\(\s*{re.escape(name)}\s*\)", window):
            duplicated.append(index + 1)
    if duplicated:
        raise Broken(
            f"parser.py 第 {duplicated} 行调用了 _handle_warning_or_raise（它在非 strict 路径上已经 "
            "warnings.append(message)），紧接着又 append 同一条 —— 前端按条数统计会算错。"
        )


@check("s6.2", "NaN/inf fps 会抛未捕获异常")
def check_s6_2() -> None:
    """`cap.get(CAP_PROP_FPS) or 25.0` only catches 0; NaN/inf survive the `or`."""
    import math

    surviving = []
    for label, value in (("NaN", float("nan")), ("inf", float("inf"))):
        resolved = value or 25.0
        if not math.isfinite(resolved):
            surviving.append(label)
    if not surviving:
        return
    for module, path in (
        ("shot_detector", REPO_ROOT / "video_parser" / "shot_detector.py"),
        ("keyframe_strategies", REPO_ROOT / "video_parser" / "keyframe_strategies.py"),
    ):
        source = path.read_text(encoding="utf-8")
        if "isfinite" in source:
            continue
        raise Broken(
            f"{module}.py 用 `cap.get(CAP_PROP_FPS) or 25.0` 兜底，"
            f"{surviving} 是真值会被保留，随后 int(round(fps/sample_fps)) 对 NaN 抛 ValueError、"
            "对 inf 抛 OverflowError —— 两者都不在 parser.py:193 的捕获列表里，"
            "非 strict 模式也会整体失败，连“退化为单镜头”的兜底都走不到。"
        )


@check("s6.4", "extract_sample_keyframes 全有或全无")
def check_s6_4() -> None:
    """One bad frame must not discard the frames already written to disk."""
    import video_parser.ffmpeg as ffmpeg

    real_extract = ffmpeg.extract_frame
    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    calls = {"n": 0}

    def flaky_extract(video_path, timestamp, frame_path, **kwargs):
        calls["n"] += 1
        if calls["n"] > 2:
            raise ffmpeg.FFmpegError("probe: simulated extraction failure")
        Path(frame_path).parent.mkdir(parents=True, exist_ok=True)
        Path(frame_path).write_bytes(b"\xff\xd8\xff\xe0")
        return frame_path

    try:
        video = tmp / "lesson.mp4"
        video.write_bytes(b"\x00\x00\x00\x18ftypmp42probe")
        ffmpeg.extract_frame = flaky_extract  # type: ignore[assignment]
        try:
            frames = ffmpeg.extract_sample_keyframes(
                video, duration_seconds=60.0, output_dir=tmp / "frames", max_frames=10
            )
        except Exception as exc:  # noqa: BLE001
            raise Broken(
                f"第 3 帧抽帧失败就整体抛出 {type(exc).__name__}，前 2 帧虽然已写盘却被丢弃。"
                "profile 分支（parser.py:113-131）是逐帧 append、失败保留前几帧 —— 两条路径行为不一致，"
                "调用方的 except 会把它降级为 warning，此时该视频的 keyframe 列表为空。"
            ) from exc
        if not frames:
            raise Broken("抽帧失败后没有保留任何已完成帧")
    finally:
        ffmpeg.extract_frame = real_extract  # type: ignore[assignment]
        shutil.rmtree(tmp, ignore_errors=True)


@check("s6.5", "extract_frame 只检查文件存在、不检查大小")
def check_s6_5() -> None:
    """`extract_video_segment` rejects `st_size <= 0`; `extract_frame` must match."""
    import video_parser.ffmpeg as ffmpeg

    real_run = ffmpeg._run_command  # noqa: SLF001
    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))

    def fake_run(command):
        # Simulate ffmpeg "succeeding" while producing a 0-byte file.
        target = Path(command[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")

        class _Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return _Result()

    try:
        ffmpeg._run_command = fake_run  # type: ignore[assignment]  # noqa: SLF001
        frame = tmp / "frame.jpg"
        try:
            ffmpeg.extract_frame(tmp / "lesson.mp4", 3.0, frame)
        except ffmpeg.FFmpegError:
            return
        raise Broken(
            "ffmpeg 产出了 0 字节文件，extract_frame 却当作成功返回。"
            "它只做 `if not frame_path.exists()`，而 extract_video_segment（ffmpeg.py:147）"
            "检查的是 `st_size <= 0` —— 两处标准不一致，0 字节帧会一路进入关键帧/IR。"
        )
    finally:
        ffmpeg._run_command = real_run  # type: ignore[assignment]  # noqa: SLF001
        shutil.rmtree(tmp, ignore_errors=True)


@check("s6.8", "教案各环节时长之和 ≠ 声明时长")
def check_s6_8() -> None:
    from video_parser.intermediate_schemas import DemoGenerationRequest
    from video_parser.package import build_teaching_content_package
    from video_parser.planning import build_demo_generation_plan, build_lesson_plan_spec

    tmp = Path(tempfile.mkdtemp(prefix="vp_probe_"))
    try:
        package = build_teaching_content_package(_circuit_result(duration=3600.0), tmp / "pkg")
        request = DemoGenerationRequest(estimated_minutes=20)
        plan = build_demo_generation_plan(package, request)
        lesson = build_lesson_plan_spec(package, plan)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    total = sum(section.minutes for section in lesson.sections)
    declared = request.estimated_minutes
    if total != declared:
        raise Broken(
            f"教案各环节合计 {total} 分钟，而声明的时长是 {declared} 分钟"
            f"（{len(lesson.sections)} 个环节 × 向下取整的 per_stage）。"
            "标题页写的是声明时长，读者按环节加总会对不上。"
        )


@check("s6.11", "读不到的帧被算成“每帧都不同”")
def check_s6_11() -> None:
    """Unreadable frames must leave the duplicate-rate statistics, not improve them."""
    from video_parser.package import _keyframe_metrics

    result = _video_result(
        duration=60.0, file_name="lesson.mp4", keyframe_path="Z:/definitely/not/here.jpg"
    )
    metrics = _keyframe_metrics(result)
    if metrics["sample_keyframe_unique_count"] >= metrics["sample_keyframe_count"] > 0:
        raise Broken(
            f"关键帧文件全部读不到，质量指标却报 unique={metrics['sample_keyframe_unique_count']}"
            f"/{metrics['sample_keyframe_count']}、duplicate_rate={metrics['keyframe_duplicate_rate']}。"
            "_keyframe_metrics 把读不到的帧压成唯一化的 'missing:<id>' 占位符参与去重，"
            "于是“全部读不到”被算成“每帧都不同” —— 质量指标反向变好，掩盖真实故障。"
        )


@check("s6.13", "existing_evidence 形参收而不读")
def check_s6_13() -> None:
    import ast
    import inspect
    import textwrap

    from video_parser.interval_refinement import _refine_one_interval

    tree = ast.parse(textwrap.dedent(inspect.getsource(_refine_one_interval)))
    func = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))
    params = {arg.arg for arg in [*func.args.args, *func.args.kwonlyargs]}
    used = {node.id for node in ast.walk(func) if isinstance(node, ast.Name)}
    if "existing_evidence" not in params:
        raise Broken("探针失效：_refine_one_interval 没有 existing_evidence 形参 —— 请人工确认")
    if "existing_evidence" not in used:
        raise Broken(
            "_refine_one_interval 的 existing_evidence 形参在函数体内从未被引用，"
            "只用本区间新产生的证据时间戳 —— 形参承诺的“与已有证据对齐”没有发生。"
        )


@check("s6.14", "手工文本产生零长度转写段")
def check_s6_14() -> None:
    from video_parser.transcription import transcript_from_manual_text

    transcript = transcript_from_manual_text("这是一段手工输入的课堂文本。")
    empty = [
        segment.id
        for segment in transcript.segments
        if segment.end_seconds <= segment.start_seconds
    ]
    if empty:
        raise Broken(
            f"手工文本路径产出了零长度转写段 {empty}"
            "（start_seconds=0.0, end_seconds=0.0）。"
            "_normalize_asr_segments 把 end <= start 判为非法，schemas 的区间约束也要求正长度 —— "
            "这条路径与主路径标准不一致。"
        )


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    if "--list" in argv:
        for step, (title, _) in CHECKS.items():
            print(f"{step:6s} {title}")
        return 0

    wanted = [a for a in argv if not a.startswith("-")]
    selected = wanted or list(CHECKS)
    unknown = [s for s in selected if s not in CHECKS]
    if unknown:
        print(f"未知步骤：{unknown}（可用：{', '.join(CHECKS)}）")
        return 2

    broken: list[str] = []
    for step in selected:
        title, func = CHECKS[step]
        try:
            func()
        except Broken as exc:
            broken.append(step)
            print(f"[BUG] {step}  {title}\n      {exc}")
        except Exception:  # noqa: BLE001
            broken.append(step)
            print(f"[ERR] {step}  {title}  —— 探针自身报错，需要人工确认")
            traceback.print_exc()
        else:
            print(f"[OK ] {step}  {title}")

    print()
    print(f"合计 {len(selected)} 项：{len(selected) - len(broken)} 通过，{len(broken)} 未通过")
    if broken:
        print(f"未通过：{', '.join(broken)}")
    return 1 if broken else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
