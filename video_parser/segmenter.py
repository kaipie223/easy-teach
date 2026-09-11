from __future__ import annotations

import re
from typing import Iterable

from .schemas import (
    ConflictItem,
    EvidenceItem,
    Keyframe,
    ParsedVideoSegment,
    RefinementRecord,
    TimeRange,
    Transcript,
    VideoMetadata,
    VideoUnderstandingResult,
)
from .shot_detector import ShotSegmentData
from .utils import compact_timecode, overlap_seconds, shorten


def build_segments_and_evidence(
    metadata: VideoMetadata,
    transcript: Transcript,
    keyframes: list[Keyframe],
    shots: list[ShotSegmentData],
    ocr_evidence: list[EvidenceItem] | None = None,
    visual_evidence: list[EvidenceItem] | None = None,
    *,
    understanding: VideoUnderstandingResult | None = None,
    refinements: Iterable[RefinementRecord] = (),
    conflicts: Iterable[ConflictItem] = (),
) -> tuple[list[ParsedVideoSegment], list[EvidenceItem]]:
    """Build local segments and project video-understanding chapters as drafts.

    Local evidence audits the draft instead of deciding whether its teaching
    content is allowed to exist.  The model response itself is never promoted
    to an EvidenceItem; review state remains explicit on every projected
    chapter.
    """

    ocr_evidence = ocr_evidence or []
    visual_evidence = visual_evidence or []
    refinement_records = list(refinements)
    video_conflicts = list(conflicts)
    evidence: list[EvidenceItem] = [_metadata_evidence(metadata)]
    transcript_evidence = [_transcript_evidence(segment) for segment in transcript.segments]
    keyframe_evidence = [_keyframe_evidence(keyframe) for keyframe in keyframes]
    evidence.extend(transcript_evidence)
    evidence.extend(keyframe_evidence)
    evidence.extend(ocr_evidence)
    evidence.extend(visual_evidence)

    transcript_ev_by_segment_id = {item.source_id: item.id for item in transcript_evidence}
    keyframe_ev_by_keyframe_id = {item.source_id: item.id for item in keyframe_evidence}
    ocr_ev_by_keyframe_id = {item.source_id: item.id for item in ocr_evidence}
    visual_ev_by_keyframe_id = {item.source_id: item.id for item in visual_evidence}

    segments: list[ParsedVideoSegment] = []
    for shot in shots:
        matching_transcript = [
            segment
            for segment in transcript.segments
            if _segment_overlaps_shot(segment.start_seconds, segment.end_seconds, shot)
        ]
        matching_keyframes = [
            keyframe
            for keyframe in keyframes
            if keyframe.shot_id == shot.shot_id
        ]
        speech = " ".join(segment.text for segment in matching_transcript).strip()
        transcript_ids = [segment.id for segment in matching_transcript]
        keyframe_ids = [keyframe.id for keyframe in matching_keyframes]
        visual_text = "；".join(
            item.content.strip()
            for item in visual_evidence
            if item.source_id in keyframe_ids and item.content.strip()
        )
        summary = _summary_for_shot(shot, speech, bool(matching_keyframes), visual_text)
        evidence_ids = [
            *(transcript_ev_by_segment_id[item] for item in transcript_ids if item in transcript_ev_by_segment_id),
            *(keyframe_ev_by_keyframe_id[item] for item in keyframe_ids if item in keyframe_ev_by_keyframe_id),
            *(ocr_ev_by_keyframe_id[item] for item in keyframe_ids if item in ocr_ev_by_keyframe_id),
            *(visual_ev_by_keyframe_id[item] for item in keyframe_ids if item in visual_ev_by_keyframe_id),
        ]
        if not evidence_ids:
            evidence_ids = ["ev_metadata_0001"]

        segments.append(
            ParsedVideoSegment(
                id=f"seg_{shot.index:04d}",
                index=shot.index,
                time_range=_time_range(shot.start_seconds, shot.end_seconds),
                summary=summary,
                keywords=_extract_keywords(f"{speech} {visual_text}".strip()),
                transcript_segment_ids=transcript_ids,
                keyframe_ids=keyframe_ids,
                evidence_ids=evidence_ids,
                metadata={
                    "shot_id": shot.shot_id,
                    "duration_seconds": round(shot.duration_seconds, 3),
                    "change_score": round(shot.change_score, 4) if shot.change_score is not None else None,
                },
            )
        )
    if understanding is not None:
        candidate_segments = _build_candidate_draft_segments(
            understanding,
            transcript=transcript,
            keyframes=keyframes,
            evidence=evidence,
            refinements=refinement_records,
            conflicts=video_conflicts,
        )
        if candidate_segments:
            segments = _merge_candidate_and_fallback_segments(candidate_segments, segments)
    return segments, evidence


def _build_candidate_draft_segments(
    understanding: VideoUnderstandingResult,
    *,
    transcript: Transcript,
    keyframes: list[Keyframe],
    evidence: list[EvidenceItem],
    refinements: list[RefinementRecord],
    conflicts: list[ConflictItem],
) -> list[ParsedVideoSegment]:
    """Turn model chapters into reviewable drafts with a separate audit state."""

    decisions = {item.candidate_id: item for item in understanding.alignment_decisions}
    evidence_by_id = {item.id: item for item in evidence}
    transcript_by_id = {item.id: item for item in transcript.segments}
    refinements_by_candidate: dict[str, list[RefinementRecord]] = {}
    for record in refinements:
        refinements_by_candidate.setdefault(record.candidate_id, []).append(record)

    projected: list[ParsedVideoSegment] = []
    for chapter_index, chapter in enumerate(understanding.chapters, start=1):
        decision = decisions.get(chapter.chapter_id)
        interval_ids = {item.interval_id for item in chapter.candidate_intervals}
        related_ids = {chapter.chapter_id, *interval_ids}
        blocking_conflicts = [item for item in conflicts if _blocks_candidate(item, related_ids)]

        verified_records = []
        for interval in chapter.candidate_intervals:
            interval_decision = decisions.get(interval.interval_id)
            if (
                interval_decision is None
                or interval_decision.status not in {"accepted", "low_confidence"}
                or interval_decision.review_required
            ):
                continue
            for record in refinements_by_candidate.get(interval.interval_id, []):
                local_evidence_ids = [
                    evidence_id
                    for evidence_id in record.evidence_ids
                    if evidence_id in evidence_by_id
                    and evidence_by_id[evidence_id].evidence_type in {"ocr", "visual"}
                ]
                if record.status == "completed" and local_evidence_ids and not record.review_required:
                    verified_records.append((interval, record, local_evidence_ids))

        alignment_usable = decision is not None and decision.status in {"accepted", "low_confidence"}
        start = decision.aligned_start_seconds if alignment_usable else chapter.start_seconds
        end = decision.aligned_end_seconds if alignment_usable else chapter.end_seconds
        transcript_ids = [
            item.id
            for item in transcript.segments
            if item.id in set(chapter.asr_segment_ids)
            and _range_overlaps(start, end, item.start_seconds, item.end_seconds)
        ]
        if not transcript_ids:
            transcript_ids = [
                item.id
                for item in transcript.segments
                if _range_overlaps(start, end, item.start_seconds, item.end_seconds)
            ]

        local_evidence_ids = _candidate_evidence_ids(
            start,
            end,
            transcript_ids=transcript_ids,
            keyframes=keyframes,
            evidence=evidence,
        )
        for _, _, record_evidence_ids in verified_records:
            local_evidence_ids.extend(record_evidence_ids)
        local_evidence_ids = _unique(local_evidence_ids)
        if not local_evidence_ids:
            local_evidence_ids = ["ev_metadata_0001"]

        keyframe_ids = _keyframe_ids_for_evidence(
            start,
            end,
            keyframes=keyframes,
            evidence=evidence,
            evidence_ids=set(local_evidence_ids),
        )
        local_text = " ".join(
            transcript_by_id[item_id].text
            for item_id in transcript_ids
            if item_id in transcript_by_id and transcript_by_id[item_id].text.strip()
        ).strip()
        summary_parts = [chapter.title.strip(), chapter.summary.strip()]
        if local_text:
            summary_parts.append(f"本地 ASR：{shorten(local_text, 180)}")
        summary = "；".join(item for item in summary_parts if item)
        verified_interval_ids = [interval.interval_id for interval, _, _ in verified_records]
        refinement_ids = [record.refinement_id for _, record, _ in verified_records]
        is_verified = bool(
            alignment_usable
            and decision is not None
            and not decision.review_required
            and verified_records
            and not blocking_conflicts
        )
        knowledge_points = [
            item.model_dump(mode="json")
            for item in chapter.knowledge_points
        ]
        alignment_payload = (
            decision.model_dump(mode="json")
            if decision is not None
            else {
                "candidate_id": chapter.chapter_id,
                "status": "review_required",
                "score": 0.0,
                "review_required": True,
                "reasons": ["没有本地时间对齐结果，保留原始章节时间供人工审阅。"],
                "aligned_start_seconds": start,
                "aligned_end_seconds": end,
            }
        )
        candidate_metadata = {
            "source": "video_understanding_chapter",
            "candidate_id": chapter.chapter_id,
            "title": chapter.title,
            "summary": chapter.summary,
            "knowledge_points": knowledge_points,
            "verified_interval_ids": verified_interval_ids,
            "refinement_ids": refinement_ids,
            "alignment_decision": alignment_payload,
            "provenance": understanding.provenance.model_dump(mode="json") if understanding.provenance else None,
            "validation": {
                "status": "verified" if is_verified else "review_required",
                "formal_ir_eligible": is_verified,
                "human_review_required": not is_verified,
                "local_evidence_ids": local_evidence_ids,
                "blocking_conflict_ids": [item.id for item in blocking_conflicts],
                "audit_note": (
                    "本章节已通过本地时间与画面证据核验。"
                    if is_verified
                    else "本章节来自全视频内容理解；本地证据不足或存在冲突，内容保留并等待人工复核。"
                ),
                "model_candidate_is_not_evidence": True,
            },
        }
        projected.append(
            ParsedVideoSegment(
                id=f"seg_candidate_{_safe_identifier(chapter.chapter_id, chapter_index)}",
                index=10_000 + chapter_index,
                time_range=_time_range(start, end),
                summary=summary or f"视频章节 {chapter.chapter_id}",
                keywords=_extract_keywords(" ".join(
                    [chapter.title, chapter.summary]
                    + [item.title + " " + item.description for item in chapter.knowledge_points]
                    + [local_text]
                )),
                transcript_segment_ids=transcript_ids,
                keyframe_ids=keyframe_ids,
                evidence_ids=local_evidence_ids,
                metadata={"candidate": candidate_metadata},
            )
        )
    return projected


def _merge_candidate_and_fallback_segments(
    candidates: list[ParsedVideoSegment],
    fallback: list[ParsedVideoSegment],
) -> list[ParsedVideoSegment]:
    """Prefer coherent chapter drafts while retaining disjoint shot coverage."""

    # Review-only qwen drafts must not hide the local fallback coverage.  Only
    # a candidate that passed local refinement is allowed to replace an
    # overlapping shot segment in the parse result; formal IR applies an even
    # stricter gate independently.
    coverage_candidates = [item for item in candidates if _candidate_is_formally_eligible(item)]
    kept_fallback: list[ParsedVideoSegment] = []
    for segment in fallback:
        overlaps_candidate = any(
            overlap_seconds(
                segment.time_range.start_seconds,
                segment.time_range.end_seconds,
                candidate.time_range.start_seconds,
                candidate.time_range.end_seconds,
            ) > 0
            for candidate in coverage_candidates
        )
        if not overlaps_candidate:
            kept_fallback.append(segment)
    merged = sorted([*candidates, *kept_fallback], key=lambda item: (item.time_range.start_seconds, item.time_range.end_seconds, item.id))
    return [item.model_copy(update={"index": index}) for index, item in enumerate(merged, start=1)]


def _candidate_is_formally_eligible(segment: ParsedVideoSegment) -> bool:
    metadata = segment.metadata if isinstance(segment.metadata, dict) else {}
    candidate = metadata.get("candidate")
    if not isinstance(candidate, dict):
        return False
    validation = candidate.get("validation")
    return isinstance(validation, dict) and validation.get("formal_ir_eligible") is True


def _blocks_candidate(conflict: ConflictItem, candidate_ids: set[str]) -> bool:
    if conflict.conflict_type == "chapter_overlap" and conflict.status == "open":
        local_value = conflict.local_value if isinstance(conflict.local_value, dict) else {}
        if any(local_value.get(key) in candidate_ids for key in ("left_candidate_id", "right_candidate_id")):
            return True
    if conflict.candidate_id not in candidate_ids:
        return False
    if conflict.status != "open":
        return False
    return conflict.review_required or conflict.severity == "high"


def _candidate_evidence_ids(
    start: float,
    end: float,
    *,
    transcript_ids: list[str],
    keyframes: list[Keyframe],
    evidence: list[EvidenceItem],
) -> list[str]:
    transcript_set = set(transcript_ids)
    result: list[str] = []
    for item in evidence:
        if item.source_id in transcript_set or (
            item.time_range is not None
            and _range_overlaps(start, end, item.time_range.start_seconds, item.time_range.end_seconds)
        ):
            if item.evidence_type in {"transcript", "keyframe", "ocr", "visual"}:
                result.append(item.id)
    return _unique(result)


def _keyframe_ids_for_evidence(
    start: float,
    end: float,
    *,
    keyframes: list[Keyframe],
    evidence: list[EvidenceItem],
    evidence_ids: set[str],
) -> list[str]:
    source_ids = {
        item.source_id
        for item in evidence
        if item.id in evidence_ids and item.source_id
    }
    result: list[str] = []
    for keyframe in keyframes:
        if keyframe.id in source_ids and _range_overlaps(start, end, keyframe.timestamp_seconds, keyframe.timestamp_seconds):
            result.append(keyframe.id)
    return _unique(result)


def _range_overlaps(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    if left_end == left_start and right_end == right_start:
        return abs(left_start - right_start) <= 0.001
    return overlap_seconds(left_start, left_end, right_start, right_end) > 0 or (
        left_start <= right_start <= left_end or left_start <= right_end <= left_end
    )


def _safe_identifier(value: str, index: int) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned or f"{index:04d}"


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _metadata_evidence(metadata: VideoMetadata) -> EvidenceItem:
    video = metadata.video_streams[0] if metadata.video_streams else None
    resolution = f"{video.width}x{video.height}" if video and video.width and video.height else "unknown resolution"
    fps = f"{video.fps:.2f}fps" if video and video.fps else "unknown fps"
    return EvidenceItem(
        id="ev_metadata_0001",
        evidence_type="metadata",
        source_id="metadata",
        content=f"Video duration {metadata.duration_seconds:.3f}s, {resolution}, {fps}.",
        metadata={
            "duration_seconds": metadata.duration_seconds,
            "format_name": metadata.format_name,
            "audio_stream_count": len(metadata.audio_streams),
            "video_stream_count": len(metadata.video_streams),
        },
    )


def _transcript_evidence(segment) -> EvidenceItem:
    return EvidenceItem(
        id=segment.id.replace("tr_", "ev_transcript_"),
        evidence_type="transcript",
        source_id=segment.id,
        time_range=_time_range(segment.start_seconds, segment.end_seconds),
        content=segment.text,
        metadata={"start": segment.start, "end": segment.end},
    )


def _keyframe_evidence(keyframe: Keyframe) -> EvidenceItem:
    return EvidenceItem(
        id=keyframe.id.replace("kf_", "ev_keyframe_"),
        evidence_type="keyframe",
        source_id=keyframe.id,
        source_path=keyframe.path,
        time_range=_time_range(keyframe.timestamp_seconds, keyframe.timestamp_seconds),
        content=f"{keyframe.kind} frame at {keyframe.timecode}.",
        metadata={"reason": keyframe.reason, "shot_id": keyframe.shot_id},
    )


def _segment_overlaps_shot(start: float, end: float, shot: ShotSegmentData) -> bool:
    if end <= start:
        return shot.start_seconds <= start <= shot.end_seconds
    return overlap_seconds(start, end, shot.start_seconds, shot.end_seconds) > 0


def _summary_for_shot(shot: ShotSegmentData, speech: str, has_keyframe: bool, visual_text: str = "") -> str:
    range_text = f"{compact_timecode(shot.start_seconds)}-{compact_timecode(shot.end_seconds)}"
    if speech and visual_text:
        return f"{range_text} 语音内容集中在：{shorten(speech, 110)}；画面内容：{shorten(visual_text, 110)}"
    if speech:
        return f"{range_text} 片段的语音内容集中在：{shorten(speech, 150)}"
    if visual_text:
        return f"{range_text} 画面内容集中在：{shorten(visual_text, 180)}"
    if has_keyframe:
        return f"{range_text} 片段已抽取镜头代表帧，可作为后续视觉理解证据。"
    return f"{range_text} 片段暂无可用语音或视觉描述，仅保留时间范围。"


def _extract_keywords(text: str, limit: int = 8) -> list[str]:
    if not text.strip():
        return []
    candidates: list[str] = []
    for chunk in re.split(r"[\s，。！？、,.!?;；:：\[\]\(\)（）\"']+", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", chunk):
            candidates.extend(_han_windows(chunk))
        elif len(chunk) >= 2:
            candidates.append(chunk[:24].lower())

    seen: set[str] = set()
    keywords: list[str] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        keywords.append(item)
        if len(keywords) >= limit:
            break
    return keywords


def _han_windows(value: str) -> list[str]:
    if len(value) <= 8:
        return [value]
    return [value[index : index + 4] for index in range(0, min(len(value) - 3, 16), 4)]


def _time_range(start: float, end: float) -> TimeRange:
    return TimeRange(
        start_seconds=round(start, 3),
        end_seconds=round(end, 3),
        start=compact_timecode(start),
        end=compact_timecode(end),
    )
