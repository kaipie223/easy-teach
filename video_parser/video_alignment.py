from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from .schemas import (
    AlignmentDecision,
    ConflictItem,
    EvidenceItem,
    Keyframe,
    ParsedVideoSegment,
    Transcript,
    VideoChapterCandidate,
    VideoUnderstandingResult,
)
from .shot_detector import ShotSegmentData
from .utils import overlap_seconds


DEFAULT_ALIGNMENT_WINDOW_SECONDS = 8.0
AUTO_ACCEPT_SCORE = 0.75
LOW_CONFIDENCE_SCORE = 0.55


@dataclass(frozen=True)
class BoundaryAnchor:
    timestamp_seconds: float
    kind: str
    source_id: str
    weight: float
    text: str = ""


@dataclass
class VideoAlignmentResult:
    understanding: VideoUnderstandingResult
    decisions: list[AlignmentDecision]
    conflicts: list[ConflictItem]


def align_video_understanding(
    understanding: VideoUnderstandingResult,
    *,
    duration_seconds: float,
    transcript: Transcript | None = None,
    shots: Iterable[ShotSegmentData | ParsedVideoSegment | Any] = (),
    keyframes: Iterable[Keyframe] = (),
    evidence: Iterable[EvidenceItem] = (),
    window_seconds: float = DEFAULT_ALIGNMENT_WINDOW_SECONDS,
) -> VideoAlignmentResult:
    """Align model candidates to local time anchors without changing originals.

    The returned ``VideoUnderstandingResult`` remains a candidate layer.  It
    receives decisions for auditability, while accepted local Evidence is left
    to the refinement/parser stages.
    """

    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be positive")

    transcript = transcript or Transcript(status="not_requested")
    keyframes = list(keyframes)
    evidence = list(evidence)
    anchors = collect_alignment_anchors(transcript=transcript, shots=shots, keyframes=keyframes, evidence=evidence)
    decisions: list[AlignmentDecision] = []
    conflicts: list[ConflictItem] = []

    for chapter in understanding.chapters:
        decision, boundary_conflicts = _align_range(
            candidate_id=chapter.chapter_id,
            start_seconds=chapter.start_seconds,
            end_seconds=chapter.end_seconds,
            anchors=anchors,
            duration_seconds=duration_seconds,
            window_seconds=window_seconds,
            local_text=_local_text(chapter.start_seconds, chapter.end_seconds, transcript, keyframes, evidence),
            candidate_text=f"{chapter.title} {chapter.summary}",
            candidate_kind="chapter",
        )
        decisions.append(decision)
        conflicts.extend(boundary_conflicts)
        for interval in chapter.candidate_intervals:
            interval_decision, interval_conflicts = _align_range(
                candidate_id=interval.interval_id,
                start_seconds=interval.start_seconds,
                end_seconds=interval.end_seconds,
                anchors=anchors,
                duration_seconds=duration_seconds,
                window_seconds=window_seconds,
                local_text=_local_text(interval.start_seconds, interval.end_seconds, transcript, keyframes, evidence),
                candidate_text=interval.rationale,
                candidate_kind="interval",
            )
            decisions.append(interval_decision)
            conflicts.extend(interval_conflicts)

    conflicts.extend(_chapter_overlap_conflicts(understanding.chapters, decisions))
    conflicts.extend(_duplicate_or_uncovered_conflicts(understanding, decisions, anchors, duration_seconds))
    status_warnings = [
        f"{len(conflicts)} video-understanding conflict(s) require local review."
    ] if conflicts else []
    updated = understanding.model_copy(
        update={
            "alignment_decisions": decisions,
            "warnings": [*understanding.warnings, *status_warnings],
        }
    )
    return VideoAlignmentResult(understanding=updated, decisions=decisions, conflicts=conflicts)


def collect_alignment_anchors(
    *,
    transcript: Transcript,
    shots: Iterable[ShotSegmentData | ParsedVideoSegment | Any],
    keyframes: Iterable[Keyframe],
    evidence: Iterable[EvidenceItem],
) -> list[BoundaryAnchor]:
    """Collect stable, cross-modal time anchors near model boundaries."""

    anchors: list[BoundaryAnchor] = []
    transcript_segments = list(transcript.segments)
    for segment in transcript_segments:
        anchors.extend(
            [
                BoundaryAnchor(segment.start_seconds, "asr_sentence_start", segment.id, 0.25, segment.text),
                BoundaryAnchor(segment.end_seconds, "asr_sentence_end", segment.id, 0.25, segment.text),
            ]
        )
    for previous, current in zip(transcript_segments, transcript_segments[1:]):
        gap = current.start_seconds - previous.end_seconds
        if gap >= 1.0:
            anchors.append(
                BoundaryAnchor(
                    round(previous.end_seconds + gap / 2, 3),
                    "asr_pause",
                    f"pause:{previous.id}:{current.id}",
                    0.25,
                )
            )

    for shot in shots:
        shot_id = str(getattr(shot, "shot_id", getattr(shot, "id", "shot")))
        start = _number(getattr(shot, "start_seconds", None))
        end = _number(getattr(shot, "end_seconds", None))
        if start is not None:
            anchors.append(BoundaryAnchor(start, "shot_start", shot_id, 0.20))
        if end is not None:
            anchors.append(BoundaryAnchor(end, "shot_end", shot_id, 0.20))

    ordered_keyframes = sorted(list(keyframes), key=lambda item: item.timestamp_seconds)
    previous_ocr = ""
    for keyframe in ordered_keyframes:
        anchors.append(BoundaryAnchor(keyframe.timestamp_seconds, "keyframe", keyframe.id, 0.15))
        ocr_text = _keyframe_ocr_text(keyframe)
        if ocr_text and previous_ocr and ocr_text != previous_ocr:
            anchors.append(BoundaryAnchor(keyframe.timestamp_seconds, "ocr_change", keyframe.id, 0.15, ocr_text))
        elif ocr_text and not previous_ocr:
            anchors.append(BoundaryAnchor(keyframe.timestamp_seconds, "ocr_change", keyframe.id, 0.15, ocr_text))
        if ocr_text:
            previous_ocr = ocr_text

    for item in evidence:
        if item.time_range is None:
            continue
        if item.evidence_type == "ocr":
            anchors.append(
                BoundaryAnchor(
                    item.time_range.start_seconds,
                    "ocr_change",
                    item.id,
                    0.15,
                    item.content,
                )
            )

    return _deduplicate_anchors(anchors)


def _align_range(
    *,
    candidate_id: str,
    start_seconds: float,
    end_seconds: float,
    anchors: list[BoundaryAnchor],
    duration_seconds: float,
    window_seconds: float,
    local_text: str,
    candidate_text: str,
    candidate_kind: str,
) -> tuple[AlignmentDecision, list[ConflictItem]]:
    conflicts: list[ConflictItem] = []
    original_start = float(start_seconds)
    original_end = float(end_seconds)
    invalid_range = original_start < 0 or original_end <= original_start
    out_of_video = original_end > duration_seconds or original_start >= duration_seconds

    if invalid_range or out_of_video:
        conflicts.append(
            ConflictItem(
                id=f"conflict_range_{candidate_id}",
                conflict_type="schema_or_range_error",
                severity="high",
                candidate_id=candidate_id,
                model_value={"start_seconds": original_start, "end_seconds": original_end},
                local_value={"duration_seconds": duration_seconds},
                description=f"{candidate_kind} candidate is outside the available video range.",
                recommended_action="reject_candidate_and_review",
                status="open",
                review_required=True,
            )
        )
        aligned_start = 0.0
        aligned_end = duration_seconds
        if aligned_end <= aligned_start:
            aligned_end = aligned_start + 0.001
        # 决策自身必须满足 end > start，否则"拒绝"分支会在构造决策时先抛
        # ValidationError —— 冲突记下了，可审阅的 rejected 决策却一起丢了。
        # 真实的非法区间仍然完整记录在 conflicts 的 model_value 里。
        decision_start = max(0.0, original_start)
        decision_end = max(original_end, decision_start + 0.001)
        return (
            AlignmentDecision(
                candidate_id=candidate_id,
                original_start_seconds=decision_start,
                original_end_seconds=decision_end,
                aligned_start_seconds=aligned_start,
                aligned_end_seconds=aligned_end,
                score=0.0,
                status="rejected",
                anchors={},
                review_required=True,
                warnings=["candidate range was outside video duration"],
            ),
            conflicts,
        )

    start_anchor, start_info = _best_boundary(original_start, anchors, window_seconds)
    end_anchor, end_info = _best_boundary(original_end, anchors, window_seconds)
    aligned_start = start_anchor.timestamp_seconds if start_anchor else original_start
    aligned_end = end_anchor.timestamp_seconds if end_anchor else original_end
    aligned_start = max(0.0, min(aligned_start, duration_seconds))
    aligned_end = max(0.0, min(aligned_end, duration_seconds))
    if aligned_end <= aligned_start:
        aligned_start, aligned_end = original_start, original_end
    if aligned_end <= aligned_start:
        # 对齐后仍为零/负长度时也保证可构造（end > start），避免整条决策丢失。
        aligned_end = aligned_start + 0.001

    components = _alignment_components(
        original_start=original_start,
        original_end=original_end,
        aligned_start=aligned_start,
        aligned_end=aligned_end,
        anchors=anchors,
        window_seconds=window_seconds,
    )
    score = round(
        (components["semantic_continuity"] * 0.35)
        + (components["asr_boundary"] * 0.25)
        + (components["shot_boundary"] * 0.20)
        + (components["visual_change"] * 0.15)
        + (components["candidate_distance"] * 0.05),
        4,
    )
    if score >= AUTO_ACCEPT_SCORE:
        status = "accepted"
        review_required = False
    elif score >= LOW_CONFIDENCE_SCORE:
        status = "low_confidence"
        review_required = False
    else:
        status = "review_required"
        review_required = True

    if abs(aligned_start - original_start) > 2.0 or abs(aligned_end - original_end) > 2.0:
        conflicts.append(
            ConflictItem(
                id=f"conflict_boundary_{candidate_id}",
                conflict_type="boundary_mismatch",
                severity="medium" if score >= LOW_CONFIDENCE_SCORE else "high",
                candidate_id=candidate_id,
                model_value={"start_seconds": original_start, "end_seconds": original_end},
                local_value={"start_seconds": aligned_start, "end_seconds": aligned_end},
                related_evidence_ids=_anchor_ids(start_info, end_info),
                description="Local time anchors materially moved the model boundary.",
                recommended_action="review_aligned_boundary",
                status="open",
                review_required=True,
            )
        )

    if not start_info and not end_info:
        conflicts.append(
            ConflictItem(
                id=f"conflict_evidence_{candidate_id}",
                conflict_type="insufficient_evidence",
                severity="high",
                candidate_id=candidate_id,
                model_value={"start_seconds": original_start, "end_seconds": original_end},
                local_value={"available_anchor_count": 0},
                description="No ASR, shot, keyframe or OCR anchor was found near the candidate.",
                recommended_action="run_local_refinement_before_accepting",
                status="open",
                review_required=True,
            )
        )

    if candidate_text.strip() and local_text.strip() and _token_overlap(candidate_text, local_text) < 0.08:
        conflicts.append(
            ConflictItem(
                id=f"conflict_semantic_{candidate_id}",
                conflict_type="semantic_mismatch",
                severity="high",
                candidate_id=candidate_id,
                model_value={"text": candidate_text},
                local_value={"text": local_text},
                related_evidence_ids=_anchor_ids(start_info, end_info),
                description="Model candidate text has little overlap with local ASR/OCR evidence.",
                recommended_action="keep_candidate_out_of_ir_and_review",
                status="open",
                review_required=True,
            )
        )

    return (
        AlignmentDecision(
            candidate_id=candidate_id,
            original_start_seconds=round(original_start, 3),
            original_end_seconds=round(original_end, 3),
            aligned_start_seconds=round(aligned_start, 3),
            aligned_end_seconds=round(aligned_end, 3),
            score=score,
            status=status,
            anchors={"start": _anchor_ids(start_info), "end": _anchor_ids(end_info)},
            review_required=review_required,
            warnings=[
                f"{name}={value:.3f}"
                for name, value in components.items()
                if name != "candidate_distance" and value < 0.5
            ],
        ),
        conflicts,
    )


def _best_boundary(
    timestamp: float,
    anchors: list[BoundaryAnchor],
    window_seconds: float,
) -> tuple[BoundaryAnchor | None, list[BoundaryAnchor]]:
    nearby = [anchor for anchor in anchors if abs(anchor.timestamp_seconds - timestamp) <= window_seconds]
    if not nearby:
        return None, []
    grouped: dict[float, list[BoundaryAnchor]] = {}
    for anchor in nearby:
        grouped.setdefault(round(anchor.timestamp_seconds, 3), []).append(anchor)

    def group_score(items: list[BoundaryAnchor]) -> tuple[float, float, float]:
        distance = abs(items[0].timestamp_seconds - timestamp)
        support = sum(item.weight for item in items)
        proximity = max(0.0, 1.0 - (distance / window_seconds))
        return support * 0.7 + proximity * 0.3, -distance, support

    selected_timestamp, selected = max(grouped.items(), key=lambda item: group_score(item[1]))
    del selected_timestamp
    return selected[0], selected


def _alignment_components(
    *,
    original_start: float,
    original_end: float,
    aligned_start: float,
    aligned_end: float,
    anchors: list[BoundaryAnchor],
    window_seconds: float,
) -> dict[str, float]:
    boundaries = [original_start, original_end]

    def best(kind_prefixes: tuple[str, ...]) -> float:
        scores = []
        for boundary in boundaries:
            matching = [
                anchor
                for anchor in anchors
                if anchor.kind.startswith(kind_prefixes) and abs(anchor.timestamp_seconds - boundary) <= window_seconds
            ]
            scores.append(max((1.0 - abs(item.timestamp_seconds - boundary) / window_seconds for item in matching), default=0.0))
        return sum(scores) / len(scores)

    semantic = best(("asr_sentence", "asr_pause"))
    asr = best(("asr_",))
    shot = best(("shot_",))
    visual = best(("keyframe", "ocr_"))
    distance = max(
        0.0,
        1.0
        - (
            abs(aligned_start - original_start) + abs(aligned_end - original_end)
        )
        / (2.0 * window_seconds),
    )
    return {
        "semantic_continuity": semantic,
        "asr_boundary": asr,
        "shot_boundary": shot,
        "visual_change": visual,
        "candidate_distance": distance,
    }


def _chapter_overlap_conflicts(
    chapters: list[VideoChapterCandidate],
    decisions: list[AlignmentDecision],
) -> list[ConflictItem]:
    by_id = {item.candidate_id: item for item in decisions}
    conflicts: list[ConflictItem] = []
    for index, left in enumerate(chapters):
        left_decision = by_id.get(left.chapter_id)
        if left_decision is None:
            continue
        for right in chapters[index + 1 :]:
            right_decision = by_id.get(right.chapter_id)
            if right_decision is None:
                continue
            overlap = overlap_seconds(
                left_decision.aligned_start_seconds,
                left_decision.aligned_end_seconds,
                right_decision.aligned_start_seconds,
                right_decision.aligned_end_seconds,
            )
            if overlap <= 0.25:
                continue
            conflicts.append(
                ConflictItem(
                    id=f"conflict_overlap_{left.chapter_id}_{right.chapter_id}",
                    conflict_type="chapter_overlap",
                    severity="high",
                    candidate_id=left.chapter_id,
                    model_value={
                        "left": [left.start_seconds, left.end_seconds],
                        "right": [right.start_seconds, right.end_seconds],
                    },
                    local_value={
                        "left": [left_decision.aligned_start_seconds, left_decision.aligned_end_seconds],
                        "right": [right_decision.aligned_start_seconds, right_decision.aligned_end_seconds],
                        "left_candidate_id": left.chapter_id,
                        "right_candidate_id": right.chapter_id,
                        "overlap_seconds": round(overlap, 3),
                    },
                    description="Two model chapters overlap after local boundary alignment.",
                    recommended_action="review_or_merge_chapters",
                    status="open",
                    review_required=True,
                )
            )
    return conflicts


def _duplicate_or_uncovered_conflicts(
    understanding: VideoUnderstandingResult,
    decisions: list[AlignmentDecision],
    anchors: list[BoundaryAnchor],
    duration_seconds: float,
) -> list[ConflictItem]:
    del duration_seconds
    conflicts: list[ConflictItem] = []
    decision_ids = {item.candidate_id for item in decisions}
    intervals = [
        interval
        for chapter in understanding.chapters
        for interval in chapter.candidate_intervals
    ]
    # 同一个 interval_id 被多个区间使用时必须报冲突：下游把 candidate_id 当唯一键
    # 建字典，重名会让"后写覆盖先写"，章节因此挂到另一个区间的时间与证据上。
    occurrences: dict[str, int] = {}
    for interval in intervals:
        occurrences[interval.interval_id] = occurrences.get(interval.interval_id, 0) + 1
    for interval_id, count in sorted(occurrences.items()):
        if count < 2:
            continue
        interval = next(item for item in intervals if item.interval_id == interval_id)
        conflicts.append(
            ConflictItem(
                id=f"conflict_duplicate_candidate_{interval_id}",
                conflict_type="schema_or_range_error",
                severity="high",
                candidate_id=interval_id,
                model_value=interval.model_dump(mode="json"),
                local_value={"occurrences": count},
                description="Several candidate intervals share one interval_id; id-keyed lookups downstream would collapse them.",
                recommended_action="review_or_split_candidates",
                status="open",
                review_required=True,
            )
        )
    for interval in intervals:
        if occurrences[interval.interval_id] < 2 and interval.interval_id not in decision_ids:
                conflicts.append(
                    ConflictItem(
                        id=f"conflict_missing_decision_{interval.interval_id}",
                        conflict_type="insufficient_evidence",
                        severity="high",
                        candidate_id=interval.interval_id,
                        model_value=interval.model_dump(mode="json"),
                        local_value={"decision": None},
                        description="Candidate interval did not produce a local alignment decision.",
                        recommended_action="review_candidate",
                        status="open",
                        review_required=True,
                    )
                )
    return conflicts


def _local_text(
    start: float,
    end: float,
    transcript: Transcript,
    keyframes: list[Keyframe],
    evidence: list[EvidenceItem],
) -> str:
    texts = [segment.text for segment in transcript.segments if overlap_seconds(start, end, segment.start_seconds, segment.end_seconds) > 0]
    for keyframe in keyframes:
        if start <= keyframe.timestamp_seconds <= end:
            text = _keyframe_ocr_text(keyframe)
            if text:
                texts.append(text)
    for item in evidence:
        if item.time_range and overlap_seconds(start, end, item.time_range.start_seconds, item.time_range.end_seconds) > 0:
            if item.evidence_type in {"transcript", "ocr", "visual"}:
                texts.append(item.content)
    return " ".join(item.strip() for item in texts if item and item.strip())


def _token_overlap(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, len(left_tokens))


def _tokens(value: str) -> set[str]:
    normalized = re.sub(r"[^\w\u4e00-\u9fff]+", " ", value.lower())
    tokens: set[str] = set()
    for chunk in normalized.split():
        if len(chunk) <= 4:
            tokens.add(chunk)
        elif re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
            tokens.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
        else:
            tokens.add(chunk)
    return {token for token in tokens if token}


def _keyframe_ocr_text(keyframe: Keyframe) -> str:
    payload = keyframe.metadata.get("ocr") if isinstance(keyframe.metadata, dict) else None
    if not isinstance(payload, dict):
        return ""
    return re.sub(r"\s+", "", str(payload.get("text") or ""))


def _anchor_ids(*groups: list[BoundaryAnchor]) -> list[str]:
    ids: list[str] = []
    for group in groups:
        for item in group:
            if item.source_id not in ids:
                ids.append(item.source_id)
    return ids


def _deduplicate_anchors(anchors: list[BoundaryAnchor]) -> list[BoundaryAnchor]:
    result: dict[tuple[float, str, str], BoundaryAnchor] = {}
    for anchor in anchors:
        key = (round(anchor.timestamp_seconds, 3), anchor.kind, anchor.source_id)
        result[key] = anchor
    return sorted(result.values(), key=lambda item: (item.timestamp_seconds, item.kind, item.source_id))


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
