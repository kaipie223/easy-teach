from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from dataclasses import replace
import hashlib
import json
from pathlib import Path

from .ffmpeg import (
    FFmpegError,
    extract_audio,
    extract_frame,
    extract_sample_keyframes,
    extract_video_segment,
    probe_video,
)
from .interval_refinement import RefinementConfig, refine_candidate_intervals
from .keyframe_strategies import KeyframeStrategyConfig, KeyframeStrategyError, plan_keyframes
from .ocr import OCRNotConfiguredError, OCRSDKUnavailableError, OCRResponse, TencentOCRError, TencentOCRClient
from .schemas import (
    ConflictItem,
    EvidenceItem,
    Keyframe,
    SourceVideo,
    TimeRange,
    Transcript,
    VideoParseOptions,
    VideoParseResult,
    VideoUnderstandingResult,
)
from .segmenter import build_segments_and_evidence
from .shot_detector import ShotDetectionConfig, ShotDetectionError, ShotSegmentData, detect_shots
from .transcription import TranscriptionError, transcribe_audio
from .utils import ensure_dir, file_sha1, json_path, safe_stem, timecode
from .vision import BailianVisionClient, BailianVisionError, VisionNotConfiguredError
from .video_alignment import align_video_understanding
from .video_cache import VideoUnderstandingCache
from .video_understanding import BailianVideoClient, BailianVideoConfig, BailianVideoError

ProgressCallback = Callable[..., None]
PARSER_VERSION = "0.3.0"


def parse_video(
    video_path: str | Path,
    output_root: str | Path = "outputs",
    options: VideoParseOptions | None = None,
    progress_callback: ProgressCallback | None = None,
) -> VideoParseResult:
    options = options or VideoParseOptions()
    source_path = Path(video_path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"Video file does not exist: {source_path}")

    digest = file_sha1(source_path)
    video_id = f"{safe_stem(source_path.stem)}-{digest[:12]}"
    run_dir = ensure_dir(Path(output_root).expanduser().resolve() / video_id)
    frames_dir = ensure_dir(run_dir / "keyframes")
    shots_dir = ensure_dir(frames_dir / "shots")
    samples_dir = ensure_dir(frames_dir / "samples")
    audio_dir = ensure_dir(run_dir / "audio")
    transcript_dir = ensure_dir(run_dir / "transcripts")
    warnings: list[str] = []

    _report_progress(progress_callback, "preparing", "已接收视频，准备读取元信息", 5)
    metadata = probe_video(source_path)
    _report_progress(
        progress_callback,
        "metadata",
        "已读取视频与音频元信息",
        15,
        {
            "duration_seconds": metadata.duration_seconds,
            "format_name": metadata.format_name,
            "video_stream_count": len(metadata.video_streams),
            "audio_stream_count": len(metadata.audio_streams),
            "video": metadata.video_streams[0].model_dump(mode="json") if metadata.video_streams else None,
            "audio": metadata.audio_streams[0].model_dump(mode="json") if metadata.audio_streams else None,
        },
    )

    keyframes: list[Keyframe] = []
    try:
        if options.video_type == "auto":
            planned_keyframes = None
            samples = extract_sample_keyframes(
                source_path,
                samples_dir,
                metadata.duration_seconds,
                max_frames=options.max_sample_keyframes,
                output_width=options.output_width,
            )
            for index, (timestamp, frame_path) in enumerate(samples, start=1):
                keyframes.append(
                    Keyframe(
                        id=f"kf_sample_{index:04d}",
                        path=json_path(frame_path) or "",
                        timestamp_seconds=round(timestamp, 3),
                        timecode=timecode(timestamp),
                        kind="sample",
                        reason="generic_uniform_coverage",
                        metadata={"extractor": "ffmpeg", "strategy": "generic"},
                    )
                )
        else:
            planned_keyframes = plan_keyframes(
                source_path,
                metadata.duration_seconds,
                options.video_type,
                config=KeyframeStrategyConfig(max_frames=options.max_sample_keyframes),
            )
            for index, planned in enumerate(planned_keyframes, start=1):
                frame_path = samples_dir / f"keyframe_{index:03d}.jpg"
                extract_frame(source_path, planned.timestamp_seconds, frame_path, output_width=options.output_width)
                keyframes.append(
                    Keyframe(
                        id=f"kf_sample_{index:04d}",
                        path=json_path(frame_path) or "",
                        timestamp_seconds=round(planned.timestamp_seconds, 3),
                        timecode=timecode(planned.timestamp_seconds),
                        kind="sample",
                        reason=planned.reason,
                        metadata={
                            "extractor": "ffmpeg",
                            "strategy": options.video_type,
                            "score": planned.score,
                            **planned.metadata,
                        },
                    )
                )
    except (FFmpegError, KeyframeStrategyError) as exc:
        _handle_warning_or_raise(options, warnings, f"Profile keyframe extraction failed: {exc}", exc)
    _report_progress(
        progress_callback,
        "keyframes",
        f"已抽取 {len(keyframes)} 张 {options.video_type} 策略关键帧",
        32,
        {
            "video_type": options.video_type,
            "count": len(keyframes),
            "keyframes": [
                {"id": item.id, "timecode": item.timecode, "kind": item.kind, "reason": item.reason}
                for item in keyframes[:12]
            ],
        },
    )

    transcript = Transcript(status="not_requested")
    audio_path: Path | None = None
    if not options.transcribe:
        transcript = Transcript(status="not_requested")
    elif not metadata.has_audio:
        transcript = Transcript(status="no_audio")
        warnings.append("Video has no audio stream; transcription skipped.")
    else:
        try:
            audio_path = extract_audio(source_path, audio_dir)
            transcript = transcribe_audio(
                audio_path,
                transcript_dir,
                model_size=options.whisper_model_size,
                language=options.language,
                beam_size=options.beam_size,
                device=options.device,
                compute_type=options.compute_type,
            )
        except (FFmpegError, TranscriptionError) as exc:
            transcript = Transcript(status="failed")
            _handle_warning_or_raise(options, warnings, f"Transcription failed: {exc}", exc)
    _report_progress(
        progress_callback,
        "transcript",
        f"语音转写状态：{transcript.status}",
        52,
        {
            "status": transcript.status,
            "segment_count": len(transcript.segments),
            "language": transcript.language,
            "preview": [{"start": item.start, "end": item.end, "text": item.text} for item in transcript.segments[:5]],
        },
    )

    shot_config = ShotDetectionConfig(
        sample_fps=options.shot_sample_fps,
        threshold=options.shot_threshold,
        min_shot_seconds=options.min_shot_seconds,
        max_shots=options.max_shots,
        output_width=options.output_width,
    )
    try:
        shots = detect_shots(source_path, shots_dir, metadata.duration_seconds, config=shot_config)
    except ShotDetectionError as exc:
        _handle_warning_or_raise(options, warnings, f"Shot detection failed, falling back to one segment: {exc}", exc)
        shots = _fallback_single_shot(source_path, shots_dir, metadata.duration_seconds, options.output_width)
    _report_progress(
        progress_callback,
        "shots",
        f"已识别 {len(shots)} 个镜头片段",
        74,
        {
            "shot_count": len(shots),
            "shots": [
                {
                    "id": shot.shot_id,
                    "start": timecode(shot.start_seconds),
                    "end": timecode(shot.end_seconds),
                    "representative": timecode(shot.representative_timestamp_seconds),
                }
                for shot in shots[:12]
            ],
        },
    )

    _assign_keyframes_to_shots(keyframes, shots)
    for shot in shots:
        keyframes.append(
            Keyframe(
                id=f"kf_{shot.shot_id}",
                path=json_path(shot.representative_frame_path) or "",
                timestamp_seconds=round(shot.representative_timestamp_seconds, 3),
                timecode=timecode(shot.representative_timestamp_seconds),
                kind="shot_representative",
                reason="shot_midpoint_representative",
                shot_id=shot.shot_id,
                metadata={
                    "shot_index": shot.index,
                    "change_score": round(shot.change_score, 4) if shot.change_score is not None else None,
                },
            )
        )

    # Keep the existing sparse OCR path available as a local alignment source.
    # When the new flow is enabled, dense OCR/visual verification happens only
    # inside candidate intervals below.
    ocr_evidence, ocr_artifacts = _run_ocr(
        keyframes,
        options,
        warnings,
        progress_callback,
    )
    visual_evidence: list[EvidenceItem] = []
    vision_artifacts: dict = {"status": "deferred_to_refinement" if options.video_understanding else "not_requested"}
    video_understanding_result: VideoUnderstandingResult | None = None
    video_conflicts: list[ConflictItem] = []
    refinement_records = []
    video_artifacts: dict = {"status": "not_requested"}

    if options.video_understanding:
        (
            video_understanding_result,
            video_conflicts,
            refinement_records,
            refinement_keyframes,
            refinement_evidence,
            video_artifacts,
        ) = _run_video_understanding(
            source_path,
            metadata.duration_seconds,
            transcript,
            keyframes,
            shots,
            ocr_evidence,
            options,
            run_dir,
            warnings,
            progress_callback,
        )
        if video_understanding_result.status == "failed" and options.vision:
            # A provider outage must preserve the pre-existing local visual
            # path rather than turning the opt-in flag into a hard dependency.
            visual_evidence, vision_artifacts = _run_vision(
                keyframes,
                transcript,
                options,
                warnings,
                progress_callback,
            )
            video_artifacts["legacy_visual_fallback"] = True
        _assign_keyframes_to_shots(refinement_keyframes, shots)
        keyframes.extend(refinement_keyframes)
        ocr_evidence.extend(item for item in refinement_evidence if item.evidence_type == "ocr")
        visual_evidence.extend(item for item in refinement_evidence if item.evidence_type == "visual")
        _report_progress(
            progress_callback,
            "alignment",
            f"视频候选对齐完成：{len(video_conflicts)} 个冲突",
            82,
            {
                "status": "completed" if video_understanding_result and video_understanding_result.status == "completed" else "partial",
                "candidate_chapter_count": len(video_understanding_result.chapters) if video_understanding_result else 0,
                "conflict_count": len(video_conflicts),
            },
        )
    else:
        visual_evidence, vision_artifacts = _run_vision(
            keyframes,
            transcript,
            options,
            warnings,
            progress_callback,
        )
    segments, evidence = build_segments_and_evidence(
        metadata,
        transcript,
        keyframes,
        shots,
        ocr_evidence,
        visual_evidence,
        understanding=video_understanding_result if options.video_understanding else None,
        refinements=refinement_records,
        conflicts=video_conflicts,
    )
    _report_progress(
        progress_callback,
        "segments",
        f"已生成 {len(segments)} 个解析片段和 {len(evidence)} 条证据",
        93,
        {
            "segment_count": len(segments),
            "evidence_count": len(evidence),
            "ocr_evidence_count": len(ocr_evidence),
            "visual_evidence_count": len(visual_evidence),
            "segments": [
                {
                    "id": item.id,
                    "time_range": item.time_range.model_dump(mode="json"),
                    "summary": item.summary,
                    "keywords": item.keywords,
                    "evidence_count": len(item.evidence_ids),
                }
                for item in segments[:12]
            ],
        },
    )
    result_path = run_dir / "video_parse_result.json"
    result = VideoParseResult(
        video_id=video_id,
        created_at=datetime.now(timezone.utc),
        source_video=SourceVideo(
            path=json_path(source_path) or "",
            file_name=source_path.name,
            file_size_bytes=source_path.stat().st_size,
            sha1=digest,
        ),
        metadata=metadata,
        transcript=transcript,
        keyframes=keyframes,
        segments=segments,
        evidence=evidence,
        artifacts={
            "run_dir": json_path(run_dir),
            "result_path": json_path(result_path),
            "audio_path": json_path(audio_path),
            "frames_dir": json_path(frames_dir),
            "sample_frames_dir": json_path(samples_dir),
            "shot_frames_dir": json_path(shots_dir),
            "transcript_dir": json_path(transcript_dir),
            "video_type": options.video_type,
            "keyframe_strategy": "generic" if options.video_type == "auto" else options.video_type,
            "ocr": ocr_artifacts,
            "vision": vision_artifacts,
            "video_understanding": video_artifacts,
            "pipeline": {
                "semantic_authority": (
                    "qwen_global_then_local_verification"
                    if options.video_understanding
                    else "local_asr_shot_keyframe_fallback"
                ),
                "formal_ir_requires_verified_candidate": True,
                "review_drafts_are_not_formal_ir": True,
            },
            "parser": {"name": "video-parser-model", "version": PARSER_VERSION},
            "options": options.model_dump(mode="json"),
            "options_sha256": hashlib.sha256(
                json.dumps(options.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
        warnings=warnings,
        video_understanding=video_understanding_result,
        conflicts=video_conflicts,
        refinements=refinement_records,
    )
    result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    _report_progress(
        progress_callback,
        "completed",
        "视频解析完成",
        100,
        {
            "video_id": result.video_id,
            "result_path": result.artifacts.get("result_path"),
            "keyframe_count": len(result.keyframes),
            "segment_count": len(result.segments),
            "evidence_count": len(result.evidence),
        },
    )
    return result


def _run_ocr(
    keyframes: list[Keyframe],
    options: VideoParseOptions,
    warnings: list[str],
    progress_callback: ProgressCallback | None,
) -> tuple[list[EvidenceItem], dict]:
    allowed_types = {"auto", "presentation", "whiteboard"}
    if not options.ocr:
        _report_progress(progress_callback, "ocr", "OCR 未启用，保留原始关键帧", 86, {"status": "not_requested"})
        return [], {"status": "not_requested", "keyframes_processed": 0, "evidence_count": 0}
    if options.video_type not in allowed_types:
        message = f"OCR 已跳过：{options.video_type} 策略暂不处理文字识别。"
        warnings.append(message)
        _report_progress(progress_callback, "ocr", message, 86, {"status": "skipped_for_video_type", "video_type": options.video_type})
        return [], {"status": "skipped_for_video_type", "keyframes_processed": 0, "evidence_count": 0}

    candidates = [keyframe for keyframe in keyframes if keyframe.kind == "sample"][: max(1, options.ocr_max_keyframes)]
    if not candidates:
        _report_progress(progress_callback, "ocr", "没有可供 OCR 的策略关键帧", 86, {"status": "no_keyframes"})
        return [], {"status": "no_keyframes", "keyframes_processed": 0, "evidence_count": 0}
    try:
        client = TencentOCRClient()
    except (OCRNotConfiguredError, OCRSDKUnavailableError, TencentOCRError) as exc:
        warnings.append(f"OCR 未执行，基础解析已保留：{exc}")
        _report_progress(progress_callback, "ocr", "OCR 配置不可用，已跳过并保留基础结果", 86, {"status": "unavailable"})
        return [], {"status": "unavailable", "keyframes_processed": 0, "evidence_count": 0}

    ocr_evidence: list[EvidenceItem] = []
    failed = 0
    for index, keyframe in enumerate(candidates, start=1):
        try:
            response = client.recognize_file(keyframe.path)
            keyframe.metadata["ocr"] = _ocr_metadata(response, client.config.action)
            if response.text:
                ocr_evidence.append(_ocr_evidence(keyframe, response, client.config.action))
        except TencentOCRError as exc:
            failed += 1
            keyframe.metadata["ocr"] = {"status": "failed", "error": str(exc)}
            warnings.append(f"OCR failed for {keyframe.id}; the keyframe was kept: {exc}")
        percent = 76 + round(index / len(candidates) * 10)
        details = {
            "status": keyframe.metadata.get("ocr", {}).get("status", "completed"),
            "index": index,
            "total": len(candidates),
            "keyframe_id": keyframe.id,
            "timecode": keyframe.timecode,
            "text": keyframe.metadata.get("ocr", {}).get("text", ""),
            "detection_count": keyframe.metadata.get("ocr", {}).get("detection_count", 0),
        }
        if details["status"] == "failed":
            details["error"] = keyframe.metadata["ocr"].get("error", "unknown error")
        _report_progress(
            progress_callback,
            "ocr",
            f"OCR 已处理 {index}/{len(candidates)} 张关键帧" if details["status"] == "completed" else (f"OCR 已处理 {index}/{len(candidates)} 张关键帧，未检测到文字" if details["status"] == "completed_no_text" else f"OCR 处理 {index}/{len(candidates)} 张关键帧失败，已保留原帧"),
            percent,
            details,
        )

    status = "completed" if not failed else "completed_with_errors"
    return ocr_evidence, {
        "status": status,
        "action": client.config.action,
        "keyframes_processed": len(candidates),
        "evidence_count": len(ocr_evidence),
        "failed_keyframes": failed,
    }


def _run_video_understanding(
    source_path: Path,
    duration_seconds: float,
    transcript: Transcript,
    keyframes: list[Keyframe],
    shots: list[ShotSegmentData],
    local_evidence: list[EvidenceItem],
    options: VideoParseOptions,
    run_dir: Path,
    warnings: list[str],
    progress_callback: ProgressCallback | None,
) -> tuple[VideoUnderstandingResult, list[ConflictItem], list, list[Keyframe], list[EvidenceItem], dict]:
    """Run the opt-in global candidate -> local verification path.

    The function is deliberately isolated from the legacy visual path.  A
    provider failure returns a failed candidate record and leaves the caller
    with the already-produced local parse inputs.
    """

    _report_progress(progress_callback, "video_understanding", "准备 qwen3.7-plus 全局视频理解", 76, {"status": "starting"})
    base_config = BailianVideoConfig.from_env()
    config = replace(
        base_config,
        model=options.video_model,
        input_mode=options.video_input_mode,
        fps=options.video_fps,
        max_frames=options.video_max_frames,
        max_chunk_seconds=options.video_chunk_seconds,
        chunk_overlap_seconds=options.video_chunk_overlap_seconds,
        timeout_seconds=options.video_timeout_seconds,
        max_retries=options.video_max_retries,
        strict_schema=options.video_strict_schema,
        cache_enabled=options.video_cache_enabled,
        cache_dir=run_dir / "video_understanding_cache" if options.video_cache_enabled else None,
        prompt_version=options.video_prompt_version,
        schema_version=options.video_schema_version,
    )
    cache = VideoUnderstandingCache(config.cache_dir) if config.cache_enabled and config.cache_dir else None
    try:
        client = BailianVideoClient(config=config, cache=cache)
    except BailianVideoError as exc:
        message = f"qwen3.7-plus 视频理解不可用，已降级旧本地流程：{_safe_video_error(exc)}"
        _handle_warning_or_raise(options, warnings, message, exc)
        failed = VideoUnderstandingResult(status="failed", warnings=[message])
        _report_progress(progress_callback, "video_understanding", message, 78, {"status": "unavailable", "degraded": True})
        return failed, [], [], [], [], {"status": "unavailable", "degraded": True, "model": options.video_model}

    chapter_results: list[VideoUnderstandingResult] = []
    effective_chunk_seconds = _effective_video_chunk_seconds(config)
    chunk_ranges = _video_chunk_ranges(duration_seconds, effective_chunk_seconds, config.chunk_overlap_seconds)
    chunk_dir = ensure_dir(run_dir / "video_understanding_chunks") if len(chunk_ranges) > 1 else None
    chunk_artifacts: list[dict] = []
    chunk_conflicts: list[ConflictItem] = []
    failed_chunks = 0
    total_usage: dict[str, int | float] = {}
    for chunk_index, (chunk_start, chunk_end) in enumerate(chunk_ranges, start=1):
        chunk_video_path = source_path
        chunk_artifact = {
            "chunk_index": chunk_index,
            "start_seconds": chunk_start,
            "end_seconds": chunk_end,
            "duration_seconds": round(chunk_end - chunk_start, 3),
            "requested_start_seconds": chunk_start,
            "requested_end_seconds": chunk_end,
            "source_file": source_path.name,
            "path": source_path.name,
            "status": "pending",
        }
        request_chunk_duration = chunk_end - chunk_start
        try:
            if chunk_dir is not None:
                chunk_video_path = chunk_dir / f"chunk_{chunk_index:03d}_{chunk_start:.3f}_{chunk_end:.3f}.mp4"
                extract_video_segment(source_path, chunk_video_path, chunk_start, chunk_end)
                actual_duration = probe_video(chunk_video_path).duration_seconds
                if actual_duration <= 0 or actual_duration > request_chunk_duration + 0.01:
                    raise FFmpegError(
                        "extracted chunk duration is outside the requested range: "
                        f"{actual_duration:.3f}s vs {request_chunk_duration:.3f}s"
                    )
                request_chunk_duration = min(request_chunk_duration, actual_duration)
                chunk_artifact["actual_duration_seconds"] = round(actual_duration, 3)
                chunk_artifact["actual_end_seconds"] = round(chunk_start + actual_duration, 3)
                chunk_artifact["path"] = f"video_understanding_chunks/{chunk_video_path.name}"
                chunk_artifact["status"] = "extracted"
            result = client.analyze_video(
                chunk_video_path,
                asr_segments=transcript,
                video_duration_seconds=duration_seconds,
                video_type=options.video_type,
                chunk_offset_seconds=chunk_start,
                chunk_duration_seconds=request_chunk_duration,
            )
            chapter_results.append(result)
            if result.provenance:
                for key, value in result.provenance.usage.items():
                    total_usage[key] = total_usage.get(key, 0) + value
            chunk_artifact["status"] = "completed"
            _report_progress(
                progress_callback,
                "video_understanding",
                f"qwen3.7-plus 已完成第 {chunk_index}/{len(chunk_ranges)} 个视频分段",
                76 + round(chunk_index / max(1, len(chunk_ranges)) * 5),
                {
                    "status": "completed",
                    "chunk_index": chunk_index,
                    "chunk_count": len(chunk_ranges),
                    "chapter_count": len(result.chapters),
                    "cache_hit": bool(result.provenance and result.provenance.cache_hit),
                },
            )
        except (BailianVideoError, FFmpegError) as exc:
            failed_chunks += 1
            chunk_artifact["status"] = "failed"
            chunk_artifact["error"] = _safe_video_error(exc)
            chunk_id = f"chunk_{chunk_index:03d}"
            chunk_conflicts.append(
                ConflictItem(
                    id=f"conflict_{chunk_id}_failure",
                    conflict_type="insufficient_evidence",
                    severity="high",
                    candidate_id=chunk_id,
                    model_value={
                        "chunk_index": chunk_index,
                        "start_seconds": chunk_start,
                        "end_seconds": chunk_end,
                    },
                    local_value={
                        "status": "failed",
                        "error": _safe_video_error(exc),
                    },
                    description="A video-understanding chunk failed; its time range has no model candidate coverage.",
                    recommended_action="review_failed_chunk_before_accepting_related_content",
                    status="open",
                    review_required=True,
                )
            )
            message = f"qwen3.7-plus 分段 {chunk_index} 失败，保留其他分段：{_safe_video_error(exc)}"
            warnings.append(message)
            if options.strict:
                raise
        finally:
            chunk_artifacts.append(chunk_artifact)

    if not chapter_results:
        message = "qwen3.7-plus 未返回可用分段，已降级旧本地流程。"
        warnings.append(message)
        failed = VideoUnderstandingResult(status="failed", warnings=[message])
        return failed, chunk_conflicts, [], [], [], {
            "status": "failed",
            "degraded": True,
            "model": options.video_model,
            "failed_chunks": failed_chunks,
            "chunk_count": len(chunk_ranges),
            "effective_chunk_seconds": effective_chunk_seconds,
            "chunks": chunk_artifacts,
        }

    understanding = _merge_video_understanding_results(chapter_results, duration_seconds, config, total_usage, failed_chunks)
    alignment = align_video_understanding(
        understanding,
        duration_seconds=duration_seconds,
        transcript=transcript,
        shots=shots,
        keyframes=keyframes,
        evidence=local_evidence,
    )
    failed_ranges = [
        (float(item["start_seconds"]), float(item["end_seconds"]))
        for item in chunk_artifacts
        if item.get("status") == "failed"
    ]
    conflicts = chunk_conflicts + _failed_chunk_candidate_conflicts(alignment.understanding, failed_ranges)
    conflicts.extend(alignment.conflicts)
    refinement_records = []
    refinement_keyframes: list[Keyframe] = []
    refinement_evidence: list[EvidenceItem] = []
    if alignment.understanding.chapters:
        try:
            refinement = refine_candidate_intervals(
                source_path,
                alignment.understanding,
                duration_seconds=duration_seconds,
                output_dir=run_dir / "refinements",
                video_type=options.video_type,
                transcript=transcript,
                existing_evidence=local_evidence,
                config=RefinementConfig(
                    max_intervals=options.video_max_refinement_intervals,
                    max_frames_per_interval=options.video_max_refinement_frames,
                    output_width=options.output_width,
                    # The opt-in video-understanding flow includes local
                    # second-pass verification by default.  The explicit
                    # legacy flags remain additive, while missing cloud
                    # Local OCR and frame-vision are separate, explicit paid
                    # options. Full-video understanding alone still produces
                    # reviewable chapters instead of multiplying cloud calls.
                    run_ocr=options.ocr,
                    run_visual=options.vision,
                    strict=options.strict,
                ),
                ocr_client_factory=(lambda: TencentOCRClient()) if options.ocr else None,
                vision_client_factory=(lambda: BailianVisionClient()) if options.vision else None,
                progress_callback=lambda phase, message, details: _report_progress(
                    progress_callback, phase, message, 84, details
                ),
            )
            refinement_records = refinement.records
            refinement_keyframes = refinement.keyframes
            refinement_evidence = refinement.evidence
            warnings.extend(refinement.warnings)
            conflicts.extend(_refinement_conflicts(refinement.records))
        except Exception as exc:  # noqa: BLE001 - refinement is a recoverable local stage.
            message = f"候选区间局部二次取证失败，保留全局候选并标记复核：{_safe_video_error(exc)}"
            _handle_warning_or_raise(options, warnings, message, exc)
            warnings.append(message)

    video_artifacts = {
        "status": "completed" if not failed_chunks else "completed_with_errors",
        "degraded": bool(failed_chunks),
        "provider": "aliyun_bailian",
        "model": options.video_model,
        "chunk_count": len(chunk_ranges),
        "failed_chunks": failed_chunks,
        "effective_chunk_seconds": effective_chunk_seconds,
        "chunks": chunk_artifacts,
        "candidate_chapter_count": len(understanding.chapters),
        "candidate_interval_count": sum(len(item.candidate_intervals) for item in understanding.chapters),
        "conflict_count": len(conflicts),
        "refinement_count": len(refinement_records),
        "usage": total_usage,
    }
    return alignment.understanding, conflicts, refinement_records, refinement_keyframes, refinement_evidence, video_artifacts


def _video_chunk_ranges(duration_seconds: float, max_chunk_seconds: float, overlap_seconds: float) -> list[tuple[float, float]]:
    if duration_seconds <= max_chunk_seconds:
        return [(0.0, duration_seconds)]
    overlap_seconds = max(0.0, overlap_seconds)
    if overlap_seconds >= max_chunk_seconds:
        # A requested overlap cannot be as long as (or longer than) the
        # chunk itself without making the cursor stall.  Preserve the
        # configured overlap whenever it is feasible; otherwise use a
        # bounded half-chunk overlap so every next range makes progress.
        overlap_seconds = max_chunk_seconds * 0.5
    ranges: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_seconds - 1e-6:
        end = min(duration_seconds, start + max_chunk_seconds)
        ranges.append((round(start, 3), round(end, 3)))
        if end >= duration_seconds:
            break
        start = max(start + 0.001, end - overlap_seconds)
    return ranges


def _effective_video_chunk_seconds(config: BailianVideoConfig) -> float:
    """Apply both provider duration and sampled-frame limits locally."""

    return min(config.max_chunk_seconds, config.max_frames / config.fps)


def _failed_chunk_candidate_conflicts(
    understanding: VideoUnderstandingResult,
    failed_ranges: list[tuple[float, float]],
) -> list[ConflictItem]:
    """Associate failed input ranges with every overlapping candidate.

    The standalone chunk conflict is useful for operations, while these
    candidate-specific conflicts make the formal segment/IR gates aware of a
    failed range even when a neighboring overlapping chunk returned a result.
    """

    conflicts: list[ConflictItem] = []
    for range_index, (failed_start, failed_end) in enumerate(failed_ranges, start=1):
        for chapter in understanding.chapters:
            candidates = [(chapter.chapter_id, chapter.start_seconds, chapter.end_seconds)]
            candidates.extend(
                (interval.interval_id, interval.start_seconds, interval.end_seconds)
                for interval in chapter.candidate_intervals
            )
            for candidate_id, candidate_start, candidate_end in candidates:
                if max(failed_start, candidate_start) >= min(failed_end, candidate_end):
                    continue
                conflicts.append(
                    ConflictItem(
                        id=f"conflict_failed_chunk_{range_index}_{candidate_id}",
                        conflict_type="insufficient_evidence",
                        severity="high",
                        candidate_id=candidate_id,
                        model_value={
                            "candidate_start_seconds": candidate_start,
                            "candidate_end_seconds": candidate_end,
                        },
                        local_value={
                            "failed_chunk_start_seconds": failed_start,
                            "failed_chunk_end_seconds": failed_end,
                        },
                        description="Candidate overlaps a failed video-understanding range and requires review.",
                        recommended_action="keep_candidate_out_of_formal_ir_until_failed_range_review",
                        status="open",
                        review_required=True,
                    )
                )
    return conflicts


def _namespaced_chapter(chapter, chunk_index: int):
    """给单个分片的章节 / 区间 ID 加上分片前缀。

    每个分片是一次独立的模型请求，模型看不到其它分片，所以各分片都从
    chapter_1 / interval_1 开始编号 —— 直接拼接必然重名；下游按 candidate_id
    建字典会"后写覆盖先写"，章节因此挂到另一个分片的时间与证据上。
    """
    prefix = f"c{chunk_index}_"
    renamed = {
        interval.interval_id: f"{prefix}{interval.interval_id}"
        for interval in chapter.candidate_intervals
    }
    intervals = [
        interval.model_copy(update={"interval_id": renamed[interval.interval_id]})
        for interval in chapter.candidate_intervals
    ]
    knowledge_points = [
        point.model_copy(
            update={
                "candidate_interval_ids": [
                    renamed.get(item, item) for item in point.candidate_interval_ids
                ]
            }
        )
        for point in chapter.knowledge_points
    ]
    return chapter.model_copy(
        update={
            "chapter_id": f"{prefix}{chapter.chapter_id}",
            "candidate_intervals": intervals,
            "knowledge_points": knowledge_points,
        }
    )


def _merge_video_understanding_results(
    results: list[VideoUnderstandingResult],
    duration_seconds: float,
    config: BailianVideoConfig,
    usage: dict[str, int | float],
    failed_chunks: int,
) -> VideoUnderstandingResult:
    # 只有真正合并多个分片时才需要命名空间：单个分片内部编号本来就是唯一的，
    # 加了前缀反而会让产物 ID 形态与以往不一致。
    needs_namespace = len(results) > 1
    chapters = [
        _namespaced_chapter(chapter, chunk_index) if needs_namespace else chapter
        for chunk_index, result in enumerate(results, start=1)
        for chapter in result.chapters
    ]
    uncertainties = [item for result in results for item in result.uncertainties]
    warnings = [item for result in results for item in result.warnings]
    provenance = results[0].provenance
    if provenance:
        provenance = provenance.model_copy(
            update={
                "chunk_start_seconds": 0.0,
                "chunk_end_seconds": duration_seconds,
                "usage": usage,
                "cache_hit": all(bool(item.provenance and item.provenance.cache_hit) for item in results),
            }
        )
    return VideoUnderstandingResult(
        status="partial" if failed_chunks else "completed",
        video_summary="\n".join(item.video_summary for item in results if item.video_summary),
        chapters=chapters,
        uncertainties=list(dict.fromkeys(uncertainties)),
        provenance=provenance,
        warnings=list(dict.fromkeys(warnings)),
    )


def _refinement_conflicts(records: list) -> list[ConflictItem]:
    conflicts: list[ConflictItem] = []
    for record in records:
        if record.status == "completed" and record.evidence_ids and not record.review_required:
            continue
        conflicts.append(
            ConflictItem(
                id=f"conflict_refinement_{record.refinement_id}",
                conflict_type="insufficient_evidence",
                severity="high" if not record.evidence_ids else "medium",
                candidate_id=record.candidate_id,
                model_value={
                    "requested_start_seconds": record.requested_start_seconds,
                    "requested_end_seconds": record.requested_end_seconds,
                },
                local_value={
                    "status": record.status,
                    "evidence_ids": record.evidence_ids,
                    "warnings": record.warnings,
                },
                related_evidence_ids=record.evidence_ids,
                description="Local refinement did not produce a fully reviewable evidence record.",
                recommended_action="keep_candidate_out_of_formal_ir_until_review",
                status="open",
                review_required=True,
            )
        )
    return conflicts


def _safe_video_error(exc: Exception) -> str:
    import re

    return re.sub(r"(?i)(bearer\s+|sk-[a-z0-9_-]{8,})[^\s,;]*", "[redacted]", str(exc))[:400]


def _run_vision(
    keyframes: list[Keyframe],
    transcript: Transcript,
    options: VideoParseOptions,
    warnings: list[str],
    progress_callback: ProgressCallback | None,
) -> tuple[list[EvidenceItem], dict]:
    if not options.vision:
        _report_progress(progress_callback, "vision", "百炼视觉理解未启用", 90, {"status": "not_requested"})
        return [], {"status": "not_requested", "keyframes_processed": 0, "evidence_count": 0}

    sample_candidates = [keyframe for keyframe in keyframes if keyframe.kind == "sample"]
    candidates = (sample_candidates or keyframes)[: max(1, options.vision_max_keyframes)]
    if not candidates:
        _report_progress(progress_callback, "vision", "没有可供视觉模型分析的关键帧", 90, {"status": "no_keyframes"})
        return [], {"status": "no_keyframes", "keyframes_processed": 0, "evidence_count": 0}
    try:
        client = BailianVisionClient()
    except (VisionNotConfiguredError, BailianVisionError) as exc:
        _handle_warning_or_raise(options, warnings, f"百炼视觉模型未执行，基础解析已保留：{exc}", exc)
        _report_progress(progress_callback, "vision", "百炼视觉模型配置不可用，已保留基础结果", 90, {"status": "unavailable"})
        return [], {"status": "unavailable", "keyframes_processed": 0, "evidence_count": 0}

    visual_evidence: list[EvidenceItem] = []
    failed = 0
    aggregate_usage: dict[str, int | float] = {}
    actual_models: set[str] = set()
    for index, keyframe in enumerate(candidates, start=1):
        try:
            response = client.analyze_file(
                keyframe.path,
                video_type=options.video_type,
                timecode=keyframe.timecode,
                transcript_context=_transcript_context(
                    transcript,
                    keyframe.timestamp_seconds,
                    options.vision_context_seconds,
                ),
                ocr_text=str(keyframe.metadata.get("ocr", {}).get("text", "")),
            )
            analysis = response.analysis.model_dump(mode="json")
            keyframe.metadata["visual"] = {
                "status": "completed",
                "provider": "aliyun_bailian",
                "model": response.model,
                "request_id": response.request_id,
                "usage": response.usage,
                "analysis": analysis,
            }
            visual_evidence.append(_visual_evidence(keyframe, response.model, response.request_id, response.usage, analysis))
            actual_models.add(response.model)
            for key, value in response.usage.items():
                aggregate_usage[key] = aggregate_usage.get(key, 0) + value
        except BailianVisionError as exc:
            failed += 1
            keyframe.metadata["visual"] = {"status": "failed", "error": str(exc)}
            _handle_warning_or_raise(options, warnings, f"Visual analysis failed for {keyframe.id}; the keyframe was kept: {exc}", exc)
        percent = 86 + round(index / len(candidates) * 6)
        visual = keyframe.metadata.get("visual", {})
        analysis = visual.get("analysis", {})
        _report_progress(
            progress_callback,
            "vision",
            f"百炼视觉模型已分析 {index}/{len(candidates)} 张关键帧" if visual.get("status") == "completed" else f"百炼视觉模型分析 {index}/{len(candidates)} 张关键帧失败，已保留原帧",
            percent,
            {
                "status": visual.get("status", "failed"),
                "index": index,
                "total": len(candidates),
                "keyframe_id": keyframe.id,
                "timecode": keyframe.timecode,
                "model": visual.get("model", client.config.model),
                "summary": analysis.get("summary", ""),
                "block_count": len(analysis.get("blocks", [])),
                "uncertainties": analysis.get("uncertainties", []),
            },
        )

    status = "completed" if not failed else ("failed" if failed == len(candidates) else "completed_with_errors")
    return visual_evidence, {
        "status": status,
        "provider": "aliyun_bailian",
        "configured_model": client.config.model,
        "models": sorted(actual_models),
        "keyframes_processed": len(candidates),
        "evidence_count": len(visual_evidence),
        "failed_keyframes": failed,
        "usage": aggregate_usage,
    }


def _ocr_metadata(response: OCRResponse, action: str) -> dict:
    return {
        "status": "completed" if response.text else "completed_no_text",
        "action": action,
        "text": response.text,
        "detection_count": len(response.detections),
        "request_id": response.request_id,
        "detections": response.detections,
    }


def _ocr_evidence(keyframe: Keyframe, response: OCRResponse, action: str) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev_ocr_{keyframe.id}",
        evidence_type="ocr",
        source_id=keyframe.id,
        source_path=keyframe.path,
        time_range=TimeRange(
            start_seconds=keyframe.timestamp_seconds,
            end_seconds=keyframe.timestamp_seconds,
            start=keyframe.timecode,
            end=keyframe.timecode,
        ),
        content=response.text,
        metadata={
            "action": action,
            "request_id": response.request_id,
            "detection_count": len(response.detections),
            "detections": response.detections,
        },
    )


def _visual_evidence(
    keyframe: Keyframe,
    model: str,
    request_id: str | None,
    usage: dict[str, int | float],
    analysis: dict,
) -> EvidenceItem:
    return EvidenceItem(
        id=f"ev_visual_{keyframe.id}",
        evidence_type="visual",
        source_id=keyframe.id,
        source_path=keyframe.path,
        time_range=TimeRange(
            start_seconds=keyframe.timestamp_seconds,
            end_seconds=keyframe.timestamp_seconds,
            start=keyframe.timecode,
            end=keyframe.timecode,
        ),
        content=str(analysis.get("summary") or f"百炼视觉模型已分析 {keyframe.timecode} 的关键帧。"),
        metadata={
            "provider": "aliyun_bailian",
            "model": model,
            "request_id": request_id,
            "usage": usage,
            "analysis": analysis,
        },
    )


def _assign_keyframes_to_shots(keyframes: list[Keyframe], shots: list[ShotSegmentData]) -> None:
    if not shots:
        return
    for keyframe in keyframes:
        if keyframe.shot_id:
            continue
        matching = next(
            (
                shot
                for shot in shots
                if shot.start_seconds <= keyframe.timestamp_seconds <= shot.end_seconds
            ),
            None,
        )
        if matching is None:
            matching = min(
                shots,
                key=lambda shot: abs(shot.representative_timestamp_seconds - keyframe.timestamp_seconds),
            )
        keyframe.shot_id = matching.shot_id


def _transcript_context(transcript: Transcript, timestamp_seconds: float, window_seconds: float) -> str:
    start = max(0.0, timestamp_seconds - max(0.0, window_seconds))
    end = timestamp_seconds + max(0.0, window_seconds)
    text = " ".join(
        segment.text
        for segment in transcript.segments
        if segment.end_seconds >= start and segment.start_seconds <= end
    ).strip()
    return text[:1600]


def _fallback_single_shot(video_path: Path, shots_dir: Path, duration_seconds: float, output_width: int) -> list[ShotSegmentData]:
    ensure_dir(shots_dir)
    timestamp = max(0.0, min(duration_seconds - 0.05, duration_seconds / 2 if duration_seconds > 0 else 0.0))
    frame_path = shots_dir / "shot_001.jpg"
    extract_frame(video_path, timestamp, frame_path, output_width=output_width)
    return [
        ShotSegmentData(
            shot_id="shot_001",
            index=1,
            start_seconds=0.0,
            end_seconds=max(0.0, duration_seconds),
            representative_timestamp_seconds=timestamp,
            representative_frame_path=frame_path,
        )
    ]


def _handle_warning_or_raise(options: VideoParseOptions, warnings: list[str], message: str, exc: Exception) -> None:
    if options.strict:
        raise exc
    warnings.append(message)


def _report_progress(callback: ProgressCallback | None, phase: str, message: str, percent: int, details: dict | None = None) -> None:
    if callback is None:
        return
    try:
        try:
            callback(phase, message, percent, details or {})
        except TypeError:
            # Keep compatibility with existing three-argument Python callbacks.
            callback(phase, message, percent)
    except Exception:
        # Progress reporting must never make video parsing fail.
        return
