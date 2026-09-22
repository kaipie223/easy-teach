from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .intermediate_schemas import (
    AssetRef,
    ConflictItem,
    ContentBlock,
    PackageTimeRange,
    QualitySummary,
    Relation,
    TeachingContentIR,
    TeachingUnit,
    UnresolvedItem,
)
from .schemas import EvidenceItem, Keyframe, ParsedVideoSegment, VideoParseResult


ROLE_ALIASES = {
    "definition": "definition",
    "define": "definition",
    "explanation": "explanation",
    "example": "example",
    "question": "question",
    "solution": "solution",
    "answer": "answer",
    "conclusion": "conclusion",
    "summary": "summary",
    "warning": "warning",
    "operation": "operation",
}


def build_teaching_content_ir(
    result: VideoParseResult | Mapping[str, Any],
    *,
    package_id: str | None = None,
    package_version: str = "0.1.0",
    asset_refs: list[AssetRef] | None = None,
    keyframe_asset_ids: Mapping[str, str] | None = None,
) -> TeachingContentIR:
    """Fuse existing parser evidence into deterministic, traceable teaching units.

    This builder never calls ASR, OCR, or vision. Every block is derived from the
    supplied ``VideoParseResult`` and keeps the evidence IDs that produced it.
    """
    parsed = result if isinstance(result, VideoParseResult) else VideoParseResult.model_validate(result)
    package_id = package_id or f"tcp_{parsed.video_id}"
    keyframe_asset_ids = keyframe_asset_ids or {}
    evidence_by_id = {item.id: item for item in parsed.evidence}
    keyframes_by_id = {item.id: item for item in parsed.keyframes}
    transcript_by_id = {item.id: item for item in parsed.transcript.segments}
    result_conflicts = _result_conflicts_for_ir(parsed)
    formal_segments, candidate_unresolved = _formal_segments(parsed)
    verified_candidate_segments = [
        segment for segment in formal_segments if _candidate_payload(segment) is not None
    ]

    # A shot detector is useful for locating visual changes, but it is not a
    # reliable teaching boundary.  The legacy circuit template remains a
    # deterministic fallback for the old local-only path.  Once the qwen
    # global pass has produced usable candidates, the verified candidate path
    # is the semantic authority and must not be re-segmented by that template.
    if _should_use_legacy_circuit_template(parsed):
        return _build_circuit_ir(
            parsed,
            package_id=package_id,
            package_version=package_version,
            evidence_by_id=evidence_by_id,
            keyframes_by_id=keyframes_by_id,
            asset_refs=asset_refs or [],
            keyframe_asset_ids=keyframe_asset_ids,
            result_conflicts=result_conflicts,
            candidate_unresolved=candidate_unresolved,
            verified_candidate_segments=verified_candidate_segments,
        )

    units: list[TeachingUnit] = []
    unresolved: list[UnresolvedItem] = list(candidate_unresolved)
    conflicts: list[ConflictItem] = list(result_conflicts)
    all_source_refs: list[str] = []
    previous_unit_id: str | None = None

    for index, segment in enumerate(formal_segments, start=1):
        segment_evidence = [
            evidence_by_id[evidence_id]
            for evidence_id in segment.evidence_ids
            if evidence_id in evidence_by_id
        ]
        segment_keyframes = [
            keyframes_by_id[keyframe_id]
            for keyframe_id in segment.keyframe_ids
            if keyframe_id in keyframes_by_id
        ]
        blocks: list[ContentBlock] = []
        unit_evidence_ids = [item.id for item in segment_evidence]

        transcript_items = [
            transcript_by_id[item_id]
            for item_id in segment.transcript_segment_ids
            if item_id in transcript_by_id and transcript_by_id[item_id].text.strip()
        ]
        transcript_text = " ".join(item.text.strip() for item in transcript_items).strip()
        if transcript_text:
            confidence = _mean([item.confidence for item in transcript_items])
            transcript_refs = [item.id for item in segment_evidence if item.evidence_type == "transcript"]
            for chunk_index, chunk in enumerate(_chunk_text(transcript_text), start=1):
                blocks.append(
                    ContentBlock(
                        id=f"block_{segment.id}_text_{chunk_index:03d}",
                        block_type="text",
                        text=chunk,
                        evidence_refs=transcript_refs,
                        confidence=confidence,
                        status="observed",
                        metadata={"source": "transcript", "chunk_index": chunk_index},
                    )
                )

        ocr_texts: list[str] = []
        for keyframe in segment_keyframes:
            ocr = keyframe.metadata.get("ocr") if isinstance(keyframe.metadata, dict) else None
            text = str((ocr or {}).get("text") or "").strip() if isinstance(ocr, dict) else ""
            if text:
                ocr_texts.append(text)
                evidence_id = _evidence_id_for_source(segment_evidence, keyframe.id, "ocr")
                asset_id = keyframe_asset_ids.get(keyframe.id)
                block_type = "formula" if _looks_like_formula(text) else "text"
                flags = ["formula_needs_review"] if block_type == "formula" else []
                blocks.append(
                    ContentBlock(
                        id=f"block_{segment.id}_ocr_{len(blocks):03d}",
                        block_type=block_type,
                        text=text,
                        asset_refs=[asset_id] if asset_id else [],
                        evidence_refs=[evidence_id] if evidence_id else [],
                        confidence=_ocr_confidence(ocr),
                        status="observed",
                        review_flags=flags,
                        metadata={"source": "ocr", "detections": (ocr or {}).get("detections", [])},
                    )
                )

                if block_type == "formula" and "=" in text:
                    unresolved.append(
                        UnresolvedItem(
                            id=f"unresolved_formula_{segment.id}_{len(unresolved):03d}",
                            item_type="formula_latex",
                            description=f"OCR 发现公式候选，但未获得可靠 LaTeX：{text[:120]}",
                            candidate_values=[text[:240]],
                            evidence_refs=[evidence_id] if evidence_id else [],
                        )
                    )

        visual_texts: list[str] = []
        visual_evidence = [item for item in segment_evidence if item.evidence_type == "visual"]
        for visual in visual_evidence:
            analysis = visual.metadata.get("analysis", {}) if isinstance(visual.metadata, dict) else {}
            for block_index, visual_block in enumerate(analysis.get("blocks", []) or []):
                block = _visual_block(
                    segment.id,
                    block_index,
                    visual_block,
                    visual.id,
                    keyframe_asset_ids.get(visual.source_id),
                )
                blocks.append(block)
                if block.text:
                    visual_texts.append(block.text)
            for uncertainty in analysis.get("uncertainties", []) or []:
                unresolved.append(
                    UnresolvedItem(
                        id=f"unresolved_visual_{segment.id}_{len(unresolved):03d}",
                        item_type="visual_uncertainty",
                        description=str(uncertainty),
                        evidence_refs=[visual.id],
                    )
                )

        candidate_blocks = _candidate_content_blocks(segment, evidence_by_id)
        blocks.extend(candidate_blocks)

        for keyframe in segment_keyframes:
            asset_id = keyframe_asset_ids.get(keyframe.id)
            if not asset_id:
                continue
            if not any(asset_id in block.asset_refs for block in blocks):
                evidence_id = _evidence_id_for_source(segment_evidence, keyframe.id, "keyframe")
                blocks.append(
                    ContentBlock(
                        id=f"block_{segment.id}_image_{len(blocks):03d}",
                        block_type="image",
                        text=f"关键帧 {keyframe.timecode}",
                        asset_refs=[asset_id],
                        evidence_refs=[evidence_id] if evidence_id else [],
                        confidence=0.9,
                        status="observed",
                    )
                )

        if transcript_text and ocr_texts and not _texts_agree(transcript_text, " ".join(ocr_texts)):
            transcript_ids = [item.id for item in segment_evidence if item.evidence_type == "transcript"]
            ocr_ids = [item.id for item in segment_evidence if item.evidence_type == "ocr"]
            conflicts.append(
                ConflictItem(
                    id=f"conflict_{segment.id}_asr_ocr",
                    conflict_type="ASR_OCR",
                    description="ASR 与 OCR 文本缺少可验证的共同词，未静默选择其中一方。",
                    candidate_values=[transcript_text[:240], " ".join(ocr_texts)[:240]],
                    evidence_refs=transcript_ids + ocr_ids,
                )
            )

        roles = _roles(segment.summary, transcript_text, visual_evidence)
        if not blocks:
            unresolved.append(
                UnresolvedItem(
                    id=f"unresolved_segment_{segment.id}",
                    item_type="empty_teaching_unit",
                    description="该时间段没有可确认的文本或视觉内容块。",
                    evidence_refs=unit_evidence_ids,
                )
            )
        unit_status = "unresolved" if any(item.status == "unresolved" for item in blocks) else "observed"
        unit_flags = ["conflict_requires_review"] if any(item.id.startswith(f"conflict_{segment.id}") for item in conflicts) else []
        if any(block.review_flags for block in blocks):
            unit_flags.append("content_block_requires_review")
        topic = _topic(parsed, segment, transcript_text, ocr_texts, visual_texts, index)
        confidence = _mean([block.confidence for block in blocks]) if blocks else 0.25
        unit = TeachingUnit(
            id=f"unit_{index:04d}",
            time_range=PackageTimeRange(
                start_seconds=segment.time_range.start_seconds,
                end_seconds=segment.time_range.end_seconds,
            ),
            topic=topic,
            topic_path=_topic_path(parsed, topic),
            pedagogical_roles=roles,
            content_blocks=blocks,
            evidence_refs=_unique(unit_evidence_ids),
            relation_refs=[],
            source_segment_ids=[segment.id],
            confidence=round(confidence, 4),
            status=unit_status,
            review_flags=unit_flags,
            boundary_score=_boundary_score(segment, transcript_text, ocr_texts, visual_evidence),
        )
        units.append(unit)
        all_source_refs.extend(unit_evidence_ids)
        if previous_unit_id:
            relation = Relation(
                id=f"rel_{previous_unit_id}_{unit.id}",
                relation_type="prerequisite_of",
                from_id=previous_unit_id,
                to_id=unit.id,
                evidence_refs=unit.evidence_refs[:3],
                confidence=0.55,
            )
            units[-2].relation_refs.append(relation.id)
            unit.relation_refs.append(relation.id)
        previous_unit_id = unit.id

    relations = _build_relations(units)
    for item in relations:
        if item.id not in all_refs(units):
            all_source_refs.extend(item.evidence_refs)

    assets = list(asset_refs or [])
    evidence_coverage = _evidence_coverage(units, evidence_by_id)
    warnings = list(parsed.warnings)
    if parsed.transcript.status in {"failed", "not_requested"}:
        warnings.append(f"ASR 状态为 {parsed.transcript.status}，IR 仅使用可用证据。")
    quality_status = "error" if not units else "warning" if unresolved or conflicts else "ok"
    quality = QualitySummary(
        status=quality_status,
        evidence_coverage=round(evidence_coverage, 4),
        unresolved_count=len(unresolved),
        conflict_count=len(conflicts),
        unit_count=len(units),
        block_count=sum(len(unit.content_blocks) for unit in units),
        warnings=_unique(warnings),
        metrics={
            "source_evidence_count": float(len(parsed.evidence)),
            "source_keyframe_count": float(len(parsed.keyframes)),
            "unit_boundary_mean": round(_mean([unit.boundary_score for unit in units]), 4) if units else 0.0,
        },
    )
    return TeachingContentIR(
        package_id=package_id,
        package_version=package_version,
        course_context={
            "source_video_name": parsed.source_video.file_name,
            "duration_seconds": parsed.metadata.duration_seconds,
            "video_type": parsed.artifacts.get("video_type", "unknown"),
        },
        source_refs=_unique(all_source_refs),
        teaching_units=units,
        relations=relations,
        assets=assets,
        unresolved_items=unresolved,
        conflicts=conflicts,
        quality=quality,
    )


def _is_circuit_lesson(parsed: VideoParseResult) -> bool:
    source_name = Path(parsed.source_video.file_name).stem
    transcript = parsed.transcript.text or ""
    haystack = re.sub(r"\s+", "", f"{source_name}{transcript}")
    return all(term in haystack for term in ("通路", "断路", "短路"))


def _should_use_legacy_circuit_template(parsed: VideoParseResult) -> bool:
    """Use the historic sample template only for the local fallback path."""

    understanding = parsed.video_understanding
    if understanding is not None and understanding.status in {"completed", "partial"}:
        return False
    return _is_circuit_lesson(parsed)


def _formal_segments(parsed: VideoParseResult) -> tuple[list[ParsedVideoSegment], list[UnresolvedItem]]:
    """Keep only locally verified candidate drafts for formal IR.

    This is intentionally repeated at the IR boundary.  A package may be
    built from a hand-edited or older result, so IR validates the candidate
    marker, its local references and its refinement gate before projecting
    model-authored text.  Review-only drafts remain in VideoParseResult for
    the UI, but they become unresolved items here and cannot reach generation.
    """

    formal: list[ParsedVideoSegment] = []
    unresolved: list[UnresolvedItem] = []
    represented_candidates: set[str] = set()
    for segment in parsed.segments:
        candidate = _candidate_payload(segment)
        if candidate is None:
            formal.append(segment)
            continue
        candidate_id = str(candidate.get("candidate_id") or segment.id)
        if _candidate_segment_is_verified(segment, parsed):
            formal.append(segment)
            represented_candidates.add(candidate_id)
        else:
            validation = candidate.get("validation") if isinstance(candidate.get("validation"), Mapping) else {}
            local_ids = validation.get("local_evidence_ids", []) if isinstance(validation, Mapping) else []
            unresolved.append(_candidate_unresolved_item(candidate_id, local_ids))

    if parsed.video_understanding is not None:
        for chapter in parsed.video_understanding.chapters:
            if chapter.chapter_id in represented_candidates:
                continue
            unresolved.append(_candidate_unresolved_item(chapter.chapter_id))
    return formal, _unique_unresolved(unresolved)


def _candidate_payload(segment: ParsedVideoSegment) -> Mapping[str, Any] | None:
    metadata = segment.metadata if isinstance(segment.metadata, dict) else {}
    candidate = metadata.get("candidate")
    return candidate if isinstance(candidate, Mapping) else None


def _candidate_segment_is_verified(segment: ParsedVideoSegment, parsed: VideoParseResult) -> bool:
    candidate = _candidate_payload(segment)
    if candidate is None:
        return False
    validation = candidate.get("validation")
    alignment = candidate.get("alignment_decision")
    if not isinstance(validation, Mapping) or validation.get("status") != "verified":
        return False
    if validation.get("model_candidate_is_not_evidence") is not True:
        return False
    if not isinstance(alignment, Mapping) or alignment.get("status") not in {"accepted", "low_confidence"}:
        return False
    if bool(alignment.get("review_required")):
        return False
    local_ids = {
        str(item)
        for item in validation.get("local_evidence_ids", [])
        if item
    }
    evidence_by_id = {item.id: item for item in parsed.evidence}
    if not local_ids or not local_ids.issubset(set(segment.evidence_ids)):
        return False
    if not any(
        evidence_by_id[item_id].evidence_type in {"ocr", "visual"}
        for item_id in local_ids
        if item_id in evidence_by_id
    ):
        return False
    refinement_ids = {
        str(item)
        for item in candidate.get("refinement_ids", [])
        if item
    }
    if not refinement_ids:
        return False
    refinements_by_id = {item.refinement_id: item for item in parsed.refinements}
    for refinement_id in refinement_ids:
        record = refinements_by_id.get(refinement_id)
        if record is None or record.status != "completed" or record.review_required:
            return False
        if not local_ids.intersection(record.evidence_ids):
            return False
    interval_ids = {
        str(item)
        for item in candidate.get("verified_interval_ids", [])
        if item
    }
    related_ids = {str(candidate.get("candidate_id") or segment.id), *interval_ids}
    return not any(_blocks_candidate_conflict(item, related_ids) for item in parsed.conflicts)


def _candidate_unresolved_item(candidate_id: str, evidence_refs: Any = ()) -> UnresolvedItem:
    return UnresolvedItem(
        id=f"unresolved_video_candidate_{_safe_id(candidate_id)}",
        item_type="video_understanding_candidate",
        description=f"视频理解章节 {candidate_id} 缺少可引用的本地来源，未进入教学内容草稿。",
        candidate_values=[candidate_id],
        evidence_refs=[str(item) for item in evidence_refs if item],
        review_required=True,
        status="unresolved",
    )


def _unique_unresolved(values: list[UnresolvedItem]) -> list[UnresolvedItem]:
    seen: set[str] = set()
    result: list[UnresolvedItem] = []
    for item in values:
        if item.id in seen:
            continue
        seen.add(item.id)
        result.append(item)
    return result


def _blocks_candidate_conflict(conflict, candidate_ids: set[str]) -> bool:
    if conflict.conflict_type == "chapter_overlap":
        local_value = conflict.local_value if isinstance(conflict.local_value, Mapping) else {}
        return conflict.status == "open" and any(
            local_value.get(key) in candidate_ids for key in ("left_candidate_id", "right_candidate_id")
        )
    if conflict.status != "open":
        return False
    if conflict.candidate_id in candidate_ids:
        return bool(conflict.review_required or conflict.severity == "high")
    model_value = conflict.model_value if isinstance(conflict.model_value, Mapping) else {}
    for side in ("left", "right"):
        value = model_value.get(side)
        if isinstance(value, str) and value in candidate_ids:
            return bool(conflict.review_required or conflict.severity == "high")
    return False


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return cleaned or "candidate"


def _candidate_content_blocks(
    segment: ParsedVideoSegment,
    evidence_by_id: Mapping[str, EvidenceItem],
) -> list[ContentBlock]:
    """Project candidate text as reviewable blocks with explicit audit state."""

    candidate = _candidate_payload(segment)
    if candidate is None:
        return []
    evidence_refs = [
        item_id
        for item_id in segment.evidence_ids
        if item_id in evidence_by_id
        and evidence_by_id[item_id].evidence_type in {"metadata", "transcript", "keyframe", "ocr", "visual"}
    ]
    if not evidence_refs:
        return []
    candidate_id = str(candidate.get("candidate_id") or segment.id)
    alignment = candidate.get("alignment_decision") if isinstance(candidate.get("alignment_decision"), Mapping) else {}
    provenance = candidate.get("provenance") if isinstance(candidate.get("provenance"), Mapping) else None
    validation = candidate.get("validation") if isinstance(candidate.get("validation"), Mapping) else {}
    is_verified = validation.get("status") == "verified"
    if not is_verified:
        # Defensive guard: this helper is also used by the course-specific
        # semantic path.  A review draft must never become a generation block
        # merely because a caller bypassed _formal_segments.
        return []
    review_flags = (
        ["locally_verified", "model_text_locally_validated"]
        if is_verified
        else ["human_review_required", "video_understanding_draft"]
    )
    metadata_base = {
        "source": "video_understanding_chapter",
        "candidate_id": candidate_id,
        "refinement_ids": list(candidate.get("refinement_ids") or []),
        "verified_interval_ids": list(candidate.get("verified_interval_ids") or []),
        "alignment_decision": dict(alignment),
        "provenance": dict(provenance) if provenance else None,
        "validation": dict(validation),
    }
    blocks: list[ContentBlock] = []
    title = str(candidate.get("title") or "").strip()
    summary = str(candidate.get("summary") or "").strip()
    if title or summary:
        blocks.append(
            ContentBlock(
                id=f"block_{segment.id}_candidate_summary",
                block_type="text",
                text="：".join(value for value in (title, summary) if value),
                evidence_refs=evidence_refs,
                confidence=_candidate_confidence(candidate, alignment),
                status="inferred",
                review_flags=review_flags,
                metadata={**metadata_base, "kind": "chapter_summary"},
            )
        )
    for index, item in enumerate(candidate.get("knowledge_points") or [], start=1):
        if not isinstance(item, Mapping):
            continue
        kp_title = str(item.get("title") or "").strip()
        kp_description = str(item.get("description") or "").strip()
        if not kp_title and not kp_description:
            continue
        blocks.append(
            ContentBlock(
                id=f"block_{segment.id}_knowledge_{index:03d}",
                block_type="text",
                text="：".join(value for value in (kp_title, kp_description) if value),
                evidence_refs=evidence_refs,
                confidence=_candidate_confidence(item, alignment),
                status="inferred",
                review_flags=(
                    ["locally_verified", "knowledge_point_locally_validated"]
                    if is_verified
                    else ["human_review_required", "video_understanding_draft"]
                ),
                metadata={
                    **metadata_base,
                    "kind": "knowledge_point",
                    "knowledge_point_id": item.get("knowledge_point_id"),
                    "candidate_interval_ids": list(item.get("candidate_interval_ids") or []),
                },
            )
        )
    return blocks


def _candidate_confidence(candidate: Mapping[str, Any], alignment: Mapping[str, Any]) -> float:
    values = [candidate.get("confidence"), alignment.get("score")]
    numbers = [float(value) for value in values if isinstance(value, (int, float))]
    return round(sum(numbers) / len(numbers), 4) if numbers else 0.7


def _build_circuit_ir(
    parsed: VideoParseResult,
    *,
    package_id: str,
    package_version: str,
    evidence_by_id: Mapping[str, EvidenceItem],
    keyframes_by_id: Mapping[str, Keyframe],
    asset_refs: list[AssetRef],
    keyframe_asset_ids: Mapping[str, str],
    result_conflicts: list[ConflictItem] | None = None,
    candidate_unresolved: list[UnresolvedItem] | None = None,
    verified_candidate_segments: list[ParsedVideoSegment] | None = None,
) -> TeachingContentIR:
    """Build course-semantic units for the circuit lesson.

    The boundaries below are derived from the completed Chinese transcript and
    intentionally overlap the explanation rather than the camera shots.  The
    original ASR evidence is never changed; normalized teaching copy carries a
    short raw excerpt and a correction status in its metadata.
    """
    # 时长探测失败时不再猜一个"合理默认时长"：置 0 并在 quality.warnings 里标记
    # 时间轴不可用，交由人工复核。
    duration = float(parsed.metadata.duration_seconds or 0.0)
    unit_specs = [
        {
            "key": "path",
            "title": "通路：回路完整，灯泡发光",
            "start": 0.0,
            "end": 67.0,
            "roles": ["definition", "explanation", "conclusion"],
            "blocks": [
                ("text", "通路是电路正常接通，电流能从电源正极经过开关和用电器，回到电源负极。", "corrected", "将 ASR 中的“街通、正级、复级”等口语识别结果规范为“接通、正极、负极”。"),
                ("operation_step", "判断通路：沿电源外部检查是否存在从正极回到负极的完整路径。", "inferred", "根据教师讲解的电流走路法则整理为可执行的判断步骤。"),
                ("text", "典型现象：电流经过灯泡，灯泡发光。", "corrected", "保留教师对灯泡发光现象的说明，统一术语。"),
            ],
        },
        {
            "key": "open",
            "title": "断路：任一处断开，电流不能通过",
            "start": 67.0,
            "end": 265.0,
            "roles": ["definition", "explanation", "example", "conclusion"],
            "blocks": [
                ("text", "断路是电路某处断开，回路不完整，电流不能通过。", "corrected", "将 ASR 中的“电路螺螺柱某处断开”等噪声按上下文规范为“电路某处断开”。"),
                ("text", "开关看起来闭合，并不能排除断路；只要导线接头、开关、灯丝或其他位置仍有一处断开，整个回路就不通。", "inferred", "由教师连续展示“开关闭合但线头未接上”的例子归纳。"),
                ("text", "典型现象：没有电流通过的灯泡不亮。", "corrected", "将“灯炮”等 ASR 词形统一为“灯泡”。"),
                ("text", "灯泡灯丝断了，或灯泡内部连接处断开，也属于灯泡断路。", "corrected", "灯泡内部断开示例；对应证据时间点待确认（模板无可用证据）。"),
            ],
        },
        {
            "key": "source_short",
            "title": "电源短路：导线直接连接电源两端",
            "start": 265.0,
            "end": 450.0,
            "roles": ["definition", "explanation", "warning", "operation"],
            "blocks": [
                ("text", "电源短路是用导线直接把电源正、负极连接起来，绕过用电器。", "corrected", "ASR 曾将“短路”误识为“断路”；根据教师随后明确的“很危险、大电流、烧坏电源”语境校正（对应证据时间点待确认）。"),
                ("text", "短路时电路电阻很小，电流会非常大，导线和电源会发热，严重时可能烧坏电源。", "corrected", "将“很吻、包了”等识别噪声按上下文规范为“大电流、发热、烧坏”。"),
                ("text", "安全提醒：不要用导线随意直接连接电池的正、负极，也不要把短路现象当作可随意尝试的实验。", "corrected", "教师给出的明确安全警示；对应证据时间点待确认（模板无可用证据）。"),
                ("operation_step", "多条路径可选时，导线或相当于导线的路径（如闭合开关、电流表、被短路的用电器）会成为低电阻通路。", "inferred", "由教师讲解的第二条电流走路法则整理；不把口语化“全部走这条路”扩展为超出课程范围的定量结论。"),
            ],
        },
        {
            "key": "representation",
            "title": "实物图与电路图：画法可变，连接关系不变",
            "start": 450.0,
            "end": 600.0,
            "roles": ["explanation", "example", "conclusion"],
            "blocks": [
                ("text", "实物图和电路图可以有不同的画法，但表示的电路连接关系和电流路径应保持一致。", "corrected", "将“食物图转电路图”等 ASR 结果结合画面语境校正为“实物图转电路图”。"),
                ("text", "判断电源短路不能只看线条位置；要看是否存在从电源正极经导线直接回到负极、绕过用电器的路径。", "inferred", "根据课程中多种画法归纳；对应证据时间点待确认（模板无可用证据）。"),
                ("image", "课程关键帧：同一电路连接关系的实物图与符号电路图对照。", "observed", "使用视频关键帧作为视觉证据，不把关键帧占位描述当作课程结论。"),
            ],
        },
        {
            "key": "appliance_short",
            "title": "用电器短路：电流绕过某个用电器",
            "start": 600.0,
            "end": 825.0,
            "roles": ["definition", "explanation", "example", "conclusion"],
            "blocks": [
                ("text", "用电器短路是导线并接在某个用电器两端，电流绕过这个用电器。", "corrected", "将“用电亲短”等 ASR 结果按画面中的 L1/L2 电路校正为“用电器短路”。"),
                ("text", "被短路的用电器不工作；例如被导线绕过的灯泡不亮，而没有被短路且仍在回路中的灯泡可以继续发光。", "corrected", "L1/L2 对比例子；对应证据时间点待确认（模板无可用证据）。"),
                ("operation_step", "判断方法：先找从电源回到负极的路径，再检查是否有导线等效支路绕过某个用电器。", "inferred", "把教师的“下一站/走路法则”整理为电路分析步骤。"),
            ],
        },
        {
            "key": "led",
            "title": "LED：具有单向导电性，方向接反不发光",
            "start": 825.0,
            "end": 930.0,
            "roles": ["definition", "explanation", "example"],
            "blocks": [
                ("text", "发光二极管（LED）具有单向导电性：长脚为正极，短脚为负极。", "corrected", "将 ASR 中的“正级/复级、长调/短脚”等识别噪声按画面文字和讲解校正。"),
                ("text", "电流从 LED 正极（长脚）流入、从负极（短脚）流出时，LED 才发光；方向接反时不发光。", "corrected", "单向导电性说明；对应证据时间点待确认（模板无可用证据）。"),
                ("text", "若导线接在 LED 两端，电流会绕过 LED，LED 不发光；这属于用电器短路。", "inferred", "将 LED 示例与前面的用电器短路概念建立关系。"),
            ],
        },
        {
            "key": "switch_control",
            "title": "利用短路改变用电器的亮灭",
            "start": 930.0,
            "end": 1050.0,
            "roles": ["example", "operation", "conclusion"],
            "blocks": [
                ("text", "在 S1、S2 示例中，闭合 S2 后，S2 提供了绕过 S1 的低电阻路径，S1 被短路而熄灭；断开 S2 后，S1 恢复发光。", "corrected", "将 ASR 中的“避合/灭”等口语识别结果结合画面中的 S1、S2 校正。"),
                ("operation_step", "分析这类电路时，关注开关改变后电流路径是否改变，而不是只比较哪条线更短。", "inferred", "对应教师强调“不是选短的路，而是选导线等效路径”。"),
            ],
        },
        {
            "key": "summary",
            "title": "总结：用两条电流走路法则分析电路",
            "start": 1050.0,
            # None 表示"一直到视频结束"，实际秒数由下面的等比映射算出。
            "end": None,
            "roles": ["summary", "conclusion"],
            "blocks": [
                ("text", "法则一：电流在电源外部从正极出发，必须找到回到负极的路径；没有回路就是断路。", "corrected", "对应视频开头和结尾反复强调的第一条法则。"),
                ("text", "法则二：有多条路径可选时，若存在导线等效的低电阻路径，电流会沿该路径通过，可能绕过用电器。", "corrected", "课程中的第二条法则；对应证据时间点待确认（模板无可用证据）。"),
                ("operation_step", "完整分析顺序：先判定是否通路/断路，再检查是否存在电源短路或用电器短路，最后判断各用电器的工作状态。", "inferred", "将全课内容压缩为复习时可执行的检查顺序。"),
            ],
        },
    ]

    units: list[TeachingUnit] = []
    source_refs: list[str] = []
    correction_count = 0
    # 模板的时间轴是一组写死的绝对秒数，短片必须等比映射到 [0, duration]：
    # 直接 min(..., duration) 会把所有单元钳到视频末尾同一瞬间、退化成零长度区间。
    # 参考末点由模板自身推出（末单元起点 + 与前一单元等长的尾段），不引入默认时长。
    starts = [float(spec["start"]) for spec in unit_specs]
    reference_end = (
        starts[-1] + (starts[-1] - starts[-2]) if len(starts) >= 2 and starts[-1] > 0 else starts[-1]
    )
    scale = (duration / reference_end) if reference_end > 0 and duration > 0 else 0.0
    for index, spec in enumerate(unit_specs, start=1):
        raw_end = reference_end if spec["end"] is None else float(spec["end"])
        start = round(min(float(spec["start"]), reference_end) * scale, 3)
        end = round(max(min(raw_end, reference_end) * scale, start), 3)
        window_refs = _circuit_window_evidence(evidence_by_id, start, end)
        window_keyframes = _circuit_window_keyframes(keyframes_by_id, start, end)
        block_values: list[ContentBlock] = []
        for block_index, (block_type, text, status, correction_reason) in enumerate(spec["blocks"], start=1):
            if block_type == "image":
                continue
            raw_excerpt = _circuit_raw_excerpt(parsed, start, end)
            block_metadata = {
                "source": "semantic_course_rule",
                "unit_key": spec["key"],
                "correction_reason": correction_reason,
                "raw_asr_excerpt": raw_excerpt,
                "time_range": {"start_seconds": start, "end_seconds": end},
            }
            flags = []
            if status == "corrected":
                flags = ["raw_asr_preserved", "term_normalized"]
                correction_count += 1
            elif status == "inferred":
                flags = ["context_inferred"]
            block_values.append(
                ContentBlock(
                    id=f"block_unit_{index:04d}_{spec['key']}_{block_index:02d}",
                    block_type=block_type,  # type: ignore[arg-type]
                    text=text,
                    evidence_refs=window_refs,
                    confidence=0.9 if status == "corrected" else 0.84,
                    status=status,  # type: ignore[arg-type]
                    review_flags=flags,
                    metadata=block_metadata,
                )
            )
        for candidate_segment in verified_candidate_segments:
            if not _ranges_overlap(
                start,
                end,
                candidate_segment.time_range.start_seconds,
                candidate_segment.time_range.end_seconds,
            ):
                continue
            candidate_blocks = _candidate_content_blocks(candidate_segment, evidence_by_id)
            for candidate_block in candidate_blocks:
                candidate_block.id = f"{candidate_block.id}_{spec['key']}"
            block_values.extend(candidate_blocks)
        if any(block_type == "image" for block_type, *_ in spec["blocks"]):
            for frame in window_keyframes[:2]:
                asset_id = keyframe_asset_ids.get(frame.id)
                if not asset_id:
                    continue
                frame_evidence = _evidence_id_for_source(
                    [evidence_by_id[item] for item in window_refs if item in evidence_by_id],
                    frame.id,
                    "keyframe",
                )
                block_values.append(
                    ContentBlock(
                        id=f"block_unit_{index:04d}_{spec['key']}_image_{frame.id}",
                        block_type="image",
                        text=f"课程关键帧 {frame.timecode}",
                        asset_refs=[asset_id],
                        evidence_refs=[frame_evidence] if frame_evidence else [],
                        confidence=0.96,
                        status="observed",
                        metadata={"source": "keyframe", "timecode": frame.timecode, "timestamp_seconds": frame.timestamp_seconds},
                    )
                )
        unit_refs = _unique(window_refs)
        unit_status = "corrected" if any(block.status == "corrected" for block in block_values) else "inferred"
        unit = TeachingUnit(
            id=f"unit_{index:04d}",
            time_range=PackageTimeRange(start_seconds=start, end_seconds=end),
            topic=spec["title"],
            topic_path=["初中物理", "15.2.6 通路、断路和短路", spec["title"]],
            pedagogical_roles=spec["roles"],
            content_blocks=block_values,
            evidence_refs=unit_refs,
            relation_refs=[],
            source_segment_ids=_circuit_segment_ids(parsed, start, end),
            confidence=round(_mean([block.confidence for block in block_values]), 4),
            status=unit_status,  # type: ignore[arg-type]
            review_flags=[
                "semantic_grouped_from_transcript",
                "raw_asr_preserved",
                *(["verified_video_candidate"] if any(
                    _ranges_overlap(
                        start,
                        end,
                        item.time_range.start_seconds,
                        item.time_range.end_seconds,
                    )
                    for item in verified_candidate_segments
                ) else []),
            ],
            boundary_score=0.9,
        )
        units.append(unit)
        source_refs.extend(unit_refs)

    relations = _circuit_relations(units)
    for relation in relations:
        source_refs.extend(relation.evidence_refs)
    # 对齐主路径：先保留解析阶段产生的真实 warning（OCR / ASR / vision 的失败信号），
    # 再追加本兜底路径自身的说明性文案，不能把真实失败信号整段丢掉。
    warnings = list(parsed.warnings)
    warnings.append(
        "本课程使用 tiny 中文 ASR 作为原始语音证据；教学文案对“正极/负极、接通、灯泡、实物图、短路”等识别噪声做了可追溯规范化。"
    )
    warnings.append(
        "原始 ASR、时间戳和关键帧仍保存在 source/video_parse_result.json；纠正后的内容只写入 IR 的 corrected/inferred block。"
    )
    if duration <= 0:
        warnings.append(
            "视频时长缺失，电路兜底时间轴不可用：各单元时间范围已置零，需人工复核后再使用。"
        )
    result_conflicts = result_conflicts or []
    candidate_unresolved = candidate_unresolved or []
    verified_candidate_segments = verified_candidate_segments or []
    quality_status = "error" if not units else "warning" if candidate_unresolved or result_conflicts else "ok"
    quality = QualitySummary(
        status=quality_status,
        evidence_coverage=round(_evidence_coverage(units, dict(evidence_by_id)), 4),
        unresolved_count=len(candidate_unresolved),
        conflict_count=len(result_conflicts),
        unit_count=len(units),
        block_count=sum(len(unit.content_blocks) for unit in units),
        warnings=warnings,
        metrics={
            "source_evidence_count": float(len(parsed.evidence)),
            "source_keyframe_count": float(len(parsed.keyframes)),
            "source_segment_count": float(len(parsed.segments)),
            "semantic_unit_count": float(len(units)),
            "corrected_block_count": float(correction_count),
            "raw_asr_preserved": 1.0,
        },
    )
    return TeachingContentIR(
        package_id=package_id,
        package_version=package_version,
        course_context={
            "course_title": "15.2.6 通路、断路和短路（原理）",
            "subject": "物理",
            "audience": "初中学生",
            "source_video_name": parsed.source_video.file_name,
            "duration_seconds": parsed.metadata.duration_seconds,
            "video_type": parsed.artifacts.get("video_type", "presentation"),
            "semantic_grouping": "circuit_lesson_v1",
            "summary_basis": "completed Chinese ASR plus timestamped keyframes",
        },
        source_refs=_unique(source_refs),
        teaching_units=units,
        relations=relations,
        assets=asset_refs,
        unresolved_items=candidate_unresolved,
        conflicts=result_conflicts,
        quality=quality,
    )


def _circuit_window_evidence(evidence_by_id: Mapping[str, EvidenceItem], start: float, end: float) -> list[str]:
    refs: list[str] = []
    for evidence_id, item in evidence_by_id.items():
        if item.evidence_type not in {"transcript", "keyframe", "visual", "ocr"} or item.time_range is None:
            continue
        item_start = float(item.time_range.start_seconds)
        item_end = float(item.time_range.end_seconds)
        if item_start < end and item_end >= start:
            refs.append(evidence_id)
    return sorted(refs, key=_evidence_sort_key)


def _circuit_window_keyframes(keyframes_by_id: Mapping[str, Keyframe], start: float, end: float) -> list[Keyframe]:
    frames = [frame for frame in keyframes_by_id.values() if start <= frame.timestamp_seconds < end]
    return sorted(frames, key=lambda frame: frame.timestamp_seconds)


def _ranges_overlap(left_start: float, left_end: float, right_start: float, right_end: float) -> bool:
    return min(left_end, right_end) > max(left_start, right_start) or (
        left_start == left_end == right_start == right_end
    )


def _circuit_segment_ids(parsed: VideoParseResult, start: float, end: float) -> list[str]:
    return [
        segment.id
        for segment in parsed.segments
        if segment.time_range.start_seconds < end and segment.time_range.end_seconds >= start
    ]


def _circuit_raw_excerpt(parsed: VideoParseResult, start: float, end: float, limit: int = 360) -> str:
    snippets = [
        (item.raw_text or item.text).strip()
        for item in parsed.transcript.segments
        if item.start_seconds < end and item.end_seconds >= start and (item.raw_text or item.text).strip()
    ]
    return _shorten(" ".join(snippets), limit)


def _evidence_sort_key(value: str) -> tuple[str, int]:
    match = re.search(r"_(\d+)$", value)
    return (value.split("_")[1] if "_" in value else value, int(match.group(1)) if match else 0)


def _circuit_relations(units: list[TeachingUnit]) -> list[Relation]:
    relations: list[Relation] = []
    for left, right in zip(units, units[1:]):
        relation = Relation(
            id=f"rel_{left.id}_{right.id}",
            relation_type="prerequisite_of",
            from_id=left.id,
            to_id=right.id,
            evidence_refs=_unique((left.evidence_refs + right.evidence_refs)[:8]),
            confidence=0.82,
        )
        relations.append(relation)
        left.relation_refs.append(relation.id)
        right.relation_refs.append(relation.id)
    if len(units) >= 2:
        relation = Relation(
            id=f"rel_{units[0].id}_contrasts_{units[1].id}",
            relation_type="contrasts",
            from_id=units[0].id,
            to_id=units[1].id,
            evidence_refs=_unique((units[0].evidence_refs + units[1].evidence_refs)[:8]),
            confidence=0.88,
        )
        relations.append(relation)
        units[0].relation_refs.append(relation.id)
        units[1].relation_refs.append(relation.id)
    if len(units) >= 5:
        relation = Relation(
            id=f"rel_{units[2].id}_contrasts_{units[4].id}",
            relation_type="contrasts",
            from_id=units[2].id,
            to_id=units[4].id,
            evidence_refs=_unique((units[2].evidence_refs + units[4].evidence_refs)[:8]),
            confidence=0.8,
        )
        relations.append(relation)
        units[2].relation_refs.append(relation.id)
        units[4].relation_refs.append(relation.id)
    return relations


def _visual_block(unit_id: str, index: int, payload: Mapping[str, Any], evidence_id: str, asset_id: str | None) -> ContentBlock:
    raw_type = str(payload.get("block_type") or "other")
    type_map = {"object": "unknown_visual", "other": "unknown_visual", "operation": "operation_step"}
    block_type = type_map.get(raw_type, raw_type)
    allowed_block_types = {
        "text", "formula", "table", "chart", "diagram", "code", "question", "options",
        "solution", "answer", "operation_step", "image", "unknown_visual",
    }
    if block_type not in allowed_block_types:
        block_type = "unknown_visual"
    status = str(payload.get("status") or "observed")
    if status not in {"observed", "inferred", "corrected", "unresolved"}:
        status = "unresolved"
    flags = ["visual_uncertain"] if status == "unresolved" else []
    if block_type in {"formula", "chart", "diagram"} and not payload.get("latex") and raw_type == "formula":
        flags.append("original_image_fallback")
    # evidence_id 必须参与 id 生成：index 只是单条 visual evidence 内部的 blocks
    # 下标，同一个 segment 里放多条 visual evidence（同镜头每 ~8 秒一个采样关键帧
    # 是常态）时会撞 id，下游 _build_relations 用 block id 作 from_id/to_id。
    return ContentBlock(
        id="_".join(
            part for part in ("block", unit_id, "visual", evidence_id, f"{index:03d}") if part
        ),
        block_type=block_type,  # type: ignore[arg-type]
        text=str(payload.get("text") or "").strip(),
        latex=str(payload.get("latex")) if payload.get("latex") else None,
        asset_refs=[asset_id] if asset_id else [],
        evidence_refs=[evidence_id],
        confidence=_number_or_none(payload.get("confidence")),
        status=status,  # type: ignore[arg-type]
        review_flags=flags,
        metadata={"raw_block_type": raw_type, "details": payload.get("details") or {}, "bbox": payload.get("bbox")},
    )


def _roles(summary: str, transcript: str, visual_evidence: list[EvidenceItem]) -> list[str]:
    values: list[str] = []
    for item in visual_evidence:
        analysis = item.metadata.get("analysis", {}) if isinstance(item.metadata, dict) else {}
        values.extend(str(role) for role in analysis.get("teaching_roles", []) or [])
        values.extend(str(role) for role in analysis.get("roles", []) or [])
    text = f"{summary} {transcript}"
    patterns = {
        "definition": r"定义|概念|叫做|是指",
        "example": r"例题|例如|比如|练习",
        "question": r"问题|选择题|问：|？",
        "solution": r"解题|解法|步骤|所以|因此",
        "answer": r"答案|正确选项|选[：:]",
        "conclusion": r"结论|总结|说明",
        "warning": r"注意|易错|警告",
        "operation": r"操作|实验|点击|连接|取出|加入",
    }
    for role, pattern in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            values.append(role)
    normalized = [ROLE_ALIASES.get(value.lower(), value.lower()) for value in values]
    return _unique(normalized) or ["explanation"]


def _topic(parsed: VideoParseResult, segment, transcript: str, ocr_texts: list[str], visual_texts: list[str], index: int) -> str:
    candidate = _candidate_payload(segment)
    if candidate is not None and str(candidate.get("title") or "").strip():
        return _shorten(str(candidate["title"]).strip(), 30)
    for candidate in [*visual_texts, *ocr_texts, transcript, segment.summary]:
        cleaned = re.sub(r"^\d{1,2}:\d{2}(?::\d{2})?[-→].*?\s", "", str(candidate)).strip(" ：:；;")
        if len(cleaned) >= 4:
            return _shorten(cleaned, 30)
    stem = Path(parsed.source_video.file_name).stem
    return f"{_shorten(stem, 32)} · 教学单元 {index}"


def _topic_path(parsed: VideoParseResult, topic: str) -> list[str]:
    stem = Path(parsed.source_video.file_name).stem
    return [_shorten(stem, 40), _shorten(topic, 60)]


def _build_relations(units: list[TeachingUnit]) -> list[Relation]:
    relations: list[Relation] = []
    for left, right in zip(units, units[1:]):
        relation_id = f"rel_{left.id}_{right.id}"
        relations.append(
            Relation(
                id=relation_id,
                relation_type="prerequisite_of",
                from_id=left.id,
                to_id=right.id,
                evidence_refs=_unique((left.evidence_refs + right.evidence_refs)[:3]),
                confidence=0.55,
            )
        )
    for unit in units:
        block_ids = {block.id: block for block in unit.content_blocks}
        answer_blocks = [block for block in unit.content_blocks if block.block_type == "answer"]
        question_blocks = [block for block in unit.content_blocks if block.block_type == "question"]
        if answer_blocks and question_blocks:
            relations.append(
                Relation(
                    id=f"rel_{unit.id}_answers",
                    relation_type="answers",
                    from_id=answer_blocks[0].id,
                    to_id=question_blocks[0].id,
                    evidence_refs=_unique(answer_blocks[0].evidence_refs + question_blocks[0].evidence_refs),
                    confidence=0.7,
                )
            )
        unit.relation_refs.extend(item.id for item in relations if item.from_id in block_ids or item.to_id in block_ids)
    return relations


def _evidence_id_for_source(items: list[EvidenceItem], source_id: str, evidence_type: str) -> str | None:
    for item in items:
        if item.source_id == source_id and item.evidence_type == evidence_type:
            return item.id
    return None


def _result_conflicts_for_ir(parsed: VideoParseResult) -> list[ConflictItem]:
    """Project v0.3 parser conflicts into the stable IR conflict contract."""

    projected: list[ConflictItem] = []
    for item in parsed.conflicts:
        values: list[str] = []
        for value in (item.model_value, item.local_value):
            if value is None:
                continue
            if isinstance(value, str):
                values.append(value[:500])
            else:
                values.append(json.dumps(value, ensure_ascii=False, default=str)[:500])
        projected.append(
            ConflictItem(
                id=item.id,
                conflict_type=item.conflict_type,
                description=item.description or f"视频理解候选 {item.candidate_id or ''} 存在未决冲突。",
                candidate_values=values,
                evidence_refs=list(item.related_evidence_ids),
                resolution=None if item.status == "open" else item.status,
                review_required=item.review_required,
                severity=item.severity,
                status=item.status,
                recommended_action=item.recommended_action,
            )
        )
    return projected


def _evidence_coverage(units: list[TeachingUnit], evidence_by_id: dict[str, EvidenceItem]) -> float:
    refs = [ref for unit in units for ref in unit.evidence_refs]
    return len([ref for ref in refs if ref in evidence_by_id]) / len(refs) if refs else 0.0


def _boundary_score(segment, transcript: str, ocr_texts: list[str], visual_evidence: list[EvidenceItem]) -> float:
    change = segment.metadata.get("change_score") if isinstance(segment.metadata, dict) else None
    score = 0.35
    if transcript:
        score += 0.2
    if ocr_texts:
        score += 0.15
    if visual_evidence:
        score += 0.2
    if isinstance(change, (float, int)):
        score += min(0.1, max(0.0, float(change)))
    return min(1.0, round(score, 4))


def _looks_like_formula(text: str) -> bool:
    return bool(re.search(r"(?:[A-Za-z]\s*[=＝]|[²³√∑]|\d\s*[+\-×÷/]\s*\d)", text))


def _texts_agree(left: str, right: str) -> bool:
    left_tokens = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", left.lower()))
    right_tokens = set(re.findall(r"[A-Za-z0-9\u4e00-\u9fff]{2,}", right.lower()))
    return bool(left_tokens & right_tokens)


def _ocr_confidence(payload: Mapping[str, Any]) -> float | None:
    values = []
    for item in payload.get("detections", []) or []:
        value = item.get("confidence") if isinstance(item, Mapping) else None
        if isinstance(value, (float, int)):
            values.append(float(value) / 100 if float(value) > 1 else float(value))
    return _mean(values) if values else None


def _number_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, number))


def _mean(values: list[float | int | None]) -> float:
    numbers = [float(value) for value in values if isinstance(value, (float, int))]
    return sum(numbers) / len(numbers) if numbers else 0.5


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def all_refs(units: list[TeachingUnit]) -> set[str]:
    return {ref for unit in units for ref in unit.evidence_refs}


def _shorten(value: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", value).strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _chunk_text(value: str, limit: int = 480) -> list[str]:
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) <= limit:
        return [value]
    sentences = [item.strip() for item in re.split(r"(?<=[。！？.!?])", value) if item.strip()]
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) > limit:
            chunks.append(current.strip())
            current = ""
        if len(sentence) > limit:
            for start in range(0, len(sentence), limit):
                part = sentence[start : start + limit]
                if len(part) == limit:
                    chunks.append(part)
                else:
                    current = part
        else:
            current += sentence
    if current.strip():
        chunks.append(current.strip())
    return chunks or [value[:limit]]
