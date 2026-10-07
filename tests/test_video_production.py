from pathlib import Path

import pytest
from PIL import Image

from backend.config import settings
from backend.models.material import Material, MaterialAnalysis
from backend.models.project import Project
from backend.services import materials as material_service
from backend.services.courseware import _build_evidence_refs
from video_parser.interval_refinement import _select_visual_frames
from video_parser.parser import VideoParserBudgetError, _run_video_understanding, parse_video
from video_parser.schemas import (
    AlignmentDecision,
    Keyframe,
    KnowledgePointCandidate,
    SourceVideo,
    Transcript,
    VideoChapterCandidate,
    VideoMetadata,
    VideoModelProvenance,
    VideoParseOptions,
    VideoParseResult,
    VideoUnderstandingResult,
)
from video_parser.video_understanding import (
    BailianVideoClient,
    BailianVideoConfig,
    VideoRequestError,
    VideoResponseError,
)
from video_parser.vision import (
    BailianVisionClient,
    BailianVisionConfig,
    BailianVisionError,
    VisionResponseError,
)


def _candidate_result(video_path: Path) -> VideoParseResult:
    understanding = VideoUnderstandingResult(
        status="completed",
        video_summary="介绍完整回路。",
        provenance=VideoModelProvenance(
            model="qwen3.7-plus",
            input_mode="file_url",
            chunk_end_seconds=20,
            fps=1,
            input_sha256="a" * 64,
        ),
        chapters=[
            VideoChapterCandidate(
                chapter_id="chapter-circuit",
                title="完整回路",
                summary="画面展示通路和断路的区别。",
                start_seconds=1,
                end_seconds=12,
                confidence=0.86,
                knowledge_points=[
                    KnowledgePointCandidate(
                        knowledge_point_id="kp-circuit",
                        title="通路需要完整回路",
                        description="电流路径需要闭合。",
                        confidence=0.81,
                    )
                ],
            )
        ],
        alignment_decisions=[
            AlignmentDecision(
                candidate_id="chapter-circuit",
                original_start_seconds=1,
                original_end_seconds=12,
                aligned_start_seconds=0.8,
                aligned_end_seconds=12.2,
                score=0.8,
                status="accepted",
            )
        ],
    )
    return VideoParseResult(
        video_id="lesson-video",
        created_at="2026-09-14T00:00:00+00:00",
        source_video=SourceVideo(
            path=str(video_path),
            file_name=video_path.name,
            file_size_bytes=video_path.stat().st_size,
            sha1="b" * 40,
        ),
        metadata=VideoMetadata(duration_seconds=20),
        transcript=Transcript(status="not_requested"),
        video_understanding=understanding,
    )


def test_video_options_are_forwarded_from_application_settings(tmp_path, monkeypatch):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"\x00\x00\x00\x18ftypmp42fixture")
    monkeypatch.setattr(settings, "video_parser_output_dir", tmp_path / "outputs")
    monkeypatch.setattr(settings, "video_model", "configured-video-model")
    monkeypatch.setattr(settings, "bailian_vision_model", "configured-vision-model")
    monkeypatch.setattr(settings, "video_max_refinement_intervals", 3)
    monkeypatch.setattr(settings, "video_max_refinement_frames", 9)
    monkeypatch.setattr(settings, "video_max_visual_frames_per_interval", 2)

    def fake_parse(path, output_root, options):
        assert path == video
        assert output_root == tmp_path / "outputs"
        assert options.video_model == "configured-video-model"
        assert options.vision_model == "configured-vision-model"
        assert options.video_max_refinement_intervals == 3
        assert options.video_max_refinement_frames == 9
        assert options.video_max_visual_frames_per_interval == 2
        assert options.video_input_mode in {"file_url", "base64"}
        assert "bailian_api_key_file" not in options.model_dump(mode="json")
        return _candidate_result(video)

    monkeypatch.setattr(material_service, "video_parse_video", fake_parse)
    parsed = material_service.parse_material("video", video)

    assert parsed.result_json["ai_candidate_evidence_count"] == 1


def test_aligned_ai_candidate_is_searchable_but_explicitly_marked_for_review(
    tmp_path,
    db_session_factory,
):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"video")
    parsed = material_service._video_result_to_material(_candidate_result(video))

    assert len(parsed.chunks) == 1
    candidate = parsed.chunks[0]
    assert "通路需要完整回路" in candidate.text
    assert candidate.metadata["evidence_nature"] == "ai_inference"
    assert candidate.metadata["human_review_status"] == "pending"
    assert candidate.metadata["must_not_be_presented_as_observed_fact"] is True
    assert candidate.metadata["provenance"]["model"] == "qwen3.7-plus"
    assert candidate.locator["time_range"]["start_seconds"] == 0.8

    db = db_session_factory()
    try:
        project = Project(project_id="p_video_context", owner_id="u_video", title="视频课程")
        material = Material(
            material_id="mat_video_context",
            owner_id="u_video",
            project_id=project.project_id,
            original_name="lesson.mp4",
            file_type="video",
            stored_path=str(video),
            size_bytes=video.stat().st_size,
            checksum_sha256="c" * 64,
            status="processing",
        )
        analysis = MaterialAnalysis(
            analysis_id="analysis_video_context",
            material_id=material.material_id,
            run_number=1,
            parser_name="video-parser-model",
            status="processing",
        )
        db.add_all([project, material, analysis])
        db.flush()
        material_service.apply_parsed_material(db, material, analysis, parsed)
        db.commit()

        refs = _build_evidence_refs(db, project.project_id, [])
        assert refs
        assert refs[0].source_type == "uploaded_video"
        assert "AI 视频理解候选" in refs[0].quote
        assert "通路需要完整回路" in refs[0].quote
    finally:
        db.close()


def test_managed_video_path_rejects_escape_and_symlink_target(tmp_path, monkeypatch):
    upload_root = tmp_path / "uploads"
    upload_root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"video")
    monkeypatch.setattr(settings, "upload_dir", upload_root)

    with pytest.raises(material_service.MaterialValidationError) as exc_info:
        material_service._require_managed_video_path(outside)

    assert exc_info.value.code == "VIDEO_SOURCE_PATH_INVALID"


def test_duplicate_processing_delivery_does_not_call_parser(
    tmp_path,
    db_session_factory,
    monkeypatch,
):
    upload_root = tmp_path / "uploads"
    video = upload_root / "mat-duplicate" / "lesson.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    monkeypatch.setattr(settings, "upload_dir", upload_root)
    monkeypatch.setattr(material_service, "SessionLocal", db_session_factory)
    calls = 0

    def unexpected_parse(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(material_service, "video_parse_video", unexpected_parse)
    db = db_session_factory()
    try:
        material = Material(
            material_id="mat_duplicate_delivery",
            owner_id="u_video",
            original_name="lesson.mp4",
            file_type="video",
            stored_path=str(video),
            size_bytes=video.stat().st_size,
            checksum_sha256="e" * 64,
            status="processing",
        )
        analysis = MaterialAnalysis(
            analysis_id="analysis_duplicate_delivery",
            material_id=material.material_id,
            run_number=1,
            parser_name="video-parser-model",
            status="processing",
        )
        db.add_all([material, analysis])
        db.commit()
    finally:
        db.close()

    assert material_service.run_material_analysis("analysis_duplicate_delivery") == (
        "analysis_duplicate_delivery"
    )
    assert calls == 0


def test_racing_duplicate_material_task_is_stopped_before_provider_call(
    tmp_path,
    db_session_factory,
    monkeypatch,
):
    upload_root = tmp_path / "uploads"
    first_video = upload_root / "mat-first" / "lesson.mp4"
    duplicate_video = upload_root / "mat-second" / "renamed.mp4"
    first_video.parent.mkdir(parents=True)
    duplicate_video.parent.mkdir(parents=True)
    first_video.write_bytes(b"same-video")
    duplicate_video.write_bytes(b"same-video")
    monkeypatch.setattr(settings, "upload_dir", upload_root)
    monkeypatch.setattr(material_service, "SessionLocal", db_session_factory)
    calls = 0

    def unexpected_parse(*_args, **_kwargs):
        nonlocal calls
        calls += 1

    monkeypatch.setattr(material_service, "video_parse_video", unexpected_parse)
    db = db_session_factory()
    try:
        first = Material(
            material_id="mat_a_race",
            owner_id="u_video",
            project_id="p_video",
            original_name="lesson.mp4",
            file_type="video",
            stored_path=str(first_video),
            size_bytes=first_video.stat().st_size,
            checksum_sha256="f" * 64,
            status="queued",
        )
        duplicate = Material(
            material_id="mat_b_race",
            owner_id="u_video",
            project_id="p_video",
            original_name="renamed.mp4",
            file_type="video",
            stored_path=str(duplicate_video),
            size_bytes=duplicate_video.stat().st_size,
            checksum_sha256="f" * 64,
            status="queued",
        )
        first_analysis = MaterialAnalysis(
            analysis_id="analysis_a_race",
            material_id=first.material_id,
            run_number=1,
            parser_name="video-parser-model",
            status="pending",
        )
        duplicate_analysis = MaterialAnalysis(
            analysis_id="analysis_b_race",
            material_id=duplicate.material_id,
            run_number=1,
            parser_name="video-parser-model",
            status="pending",
        )
        db.add_all([first, duplicate, first_analysis, duplicate_analysis])
        db.commit()
    finally:
        db.close()

    assert material_service.run_material_analysis("analysis_b_race") == "analysis_b_race"
    assert calls == 0
    db = db_session_factory()
    try:
        stopped = db.query(MaterialAnalysis).filter_by(analysis_id="analysis_b_race").one()
        assert stopped.status == "failed"
        assert stopped.error_code == "VIDEO_MATERIAL_EXISTS"
    finally:
        db.close()


def test_remote_video_url_is_not_accepted_by_managed_input_mode(tmp_path):
    client = BailianVideoClient(
        config=BailianVideoConfig(
            api_key="unit-test-token",
            input_mode="file_url",
            cache_enabled=False,
        ),
        transport=lambda *_args: ({}, {}),
    )

    with pytest.raises(VideoRequestError):
        client.build_request(
            "https://attacker.example/video.mp4",
            asr_segments=None,
            video_duration_seconds=5,
        )


@pytest.mark.parametrize("provider_error", ["timeout", "rate limited"])
def test_full_video_provider_failure_retries_only_once(tmp_path, provider_error):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"fixture")
    calls = 0

    def transport(*_args):
        nonlocal calls
        calls += 1
        raise VideoRequestError(provider_error)

    client = BailianVideoClient(
        config=BailianVideoConfig(
            api_key="unit-test-token",
            max_retries=1,
            cache_enabled=False,
        ),
        transport=transport,
    )
    with pytest.raises(VideoRequestError):
        client.analyze_video(video, asr_segments=None, video_duration_seconds=5)
    assert calls == 2


@pytest.mark.parametrize("content", ["", "not-json", "[]"])
def test_full_video_empty_or_non_json_response_fails_safely(tmp_path, content):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"fixture")
    client = BailianVideoClient(
        config=BailianVideoConfig(
            api_key="unit-test-token",
            max_retries=0,
            cache_enabled=False,
        ),
        transport=lambda *_args: (
            {"choices": [{"message": {"content": content}}]},
            {},
        ),
    )
    with pytest.raises(VideoResponseError):
        client.analyze_video(video, asr_segments=None, video_duration_seconds=5)


def test_partial_chunk_failure_preserves_successful_results(tmp_path, monkeypatch):
    video = tmp_path / "lesson.mp4"
    video.write_bytes(b"fixture")

    class PartialClient:
        def __init__(self):
            self.calls = 0

        def analyze_video(self, _path, **kwargs):
            self.calls += 1
            if kwargs["chunk_offset_seconds"] == 5:
                raise VideoRequestError("rate limited")
            return VideoUnderstandingResult(status="completed", video_summary="第一段可用")

    client = PartialClient()

    def extract_segment(_source, output, start, end):
        output.write_text(f"{start}:{end}", encoding="utf-8")
        return output

    monkeypatch.setattr(
        "video_parser.parser.BailianVideoConfig.from_env",
        lambda **_kwargs: BailianVideoConfig(api_key="unit-test-token", cache_enabled=False),
    )
    monkeypatch.setattr("video_parser.parser.BailianVideoClient", lambda **_kwargs: client)
    monkeypatch.setattr("video_parser.parser.extract_video_segment", extract_segment)
    monkeypatch.setattr(
        "video_parser.parser.probe_video",
        lambda path: VideoMetadata(
            duration_seconds=float(path.read_text(encoding="utf-8").split(":")[1])
            - float(path.read_text(encoding="utf-8").split(":")[0])
        ),
    )
    options = VideoParseOptions(
        video_understanding=True,
        video_chunk_seconds=10,
        video_max_frames=10,
        video_chunk_overlap_seconds=5,
        video_cache_enabled=False,
        video_max_refinement_intervals=0,
    )
    warnings = []

    understanding, conflicts, *_rest, artifacts = _run_video_understanding(
        video,
        15,
        Transcript(status="not_requested"),
        [],
        [],
        [],
        options,
        tmp_path / "run",
        warnings,
        None,
    )

    assert client.calls == 2
    assert understanding.status == "partial"
    assert artifacts["failed_chunks"] == 1
    assert artifacts["chunks"][0]["status"] == "completed"
    assert artifacts["chunks"][1]["status"] == "failed"
    assert any(item.candidate_id == "chunk_002" for item in conflicts)


def test_visual_calls_are_capped_and_retry_at_most_once(tmp_path):
    frames = [
        Keyframe(
            id=f"kf-{index}",
            path=str(tmp_path / f"{index}.jpg"),
            timestamp_seconds=float(index),
            timecode=f"00:00:{index:02d}",
            kind="sample",
            reason="budget-test",
        )
        for index in range(12)
    ]
    assert len(_select_visual_frames(frames, 4)) == 4

    image = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8), "white").save(image)
    calls = 0

    def failing_transport(*_args):
        nonlocal calls
        calls += 1
        raise BailianVisionError("rate limited")

    client = BailianVisionClient(
        BailianVisionConfig(api_key="unit-test-token", max_retries=1),
        transport=failing_transport,
    )
    with pytest.raises(BailianVisionError):
        client.analyze_file(image, video_type="auto", timecode="00:00:00")
    assert calls == 2


@pytest.mark.parametrize("content", ["", "not-json", "[]"])
def test_visual_empty_or_non_json_response_degrades_after_one_retry(tmp_path, content):
    image = tmp_path / "frame.jpg"
    Image.new("RGB", (8, 8), "white").save(image)
    calls = 0

    def transport(*_args):
        nonlocal calls
        calls += 1
        return {"choices": [{"message": {"content": content}}]}, {}

    client = BailianVisionClient(
        BailianVisionConfig(api_key="unit-test-token", max_retries=1),
        transport=transport,
    )
    with pytest.raises(VisionResponseError):
        client.analyze_file(image, video_type="auto", timecode="00:00:00")
    assert calls == 2


def test_video_duration_budget_stops_before_any_model_call(tmp_path, monkeypatch):
    video = tmp_path / "long.mp4"
    video.write_bytes(b"fixture")
    monkeypatch.setattr(
        "video_parser.parser.probe_video",
        lambda _path: VideoMetadata(duration_seconds=3601),
    )

    with pytest.raises(VideoParserBudgetError):
        parse_video(video, output_root=tmp_path / "outputs")
