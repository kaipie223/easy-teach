from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TimeRange(BaseModel):
    start_seconds: float
    end_seconds: float
    start: str
    end: str


class SourceVideo(BaseModel):
    path: str
    file_name: str
    file_size_bytes: int
    sha1: str


class VideoStreamMetadata(BaseModel):
    index: int | None = None
    codec_name: str | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    pix_fmt: str | None = None
    bit_rate: int | None = None
    duration_seconds: float | None = None


class AudioStreamMetadata(BaseModel):
    index: int | None = None
    codec_name: str | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bit_rate: int | None = None
    duration_seconds: float | None = None
    language: str | None = None


class VideoMetadata(BaseModel):
    format_name: str | None = None
    format_long_name: str | None = None
    duration_seconds: float
    size_bytes: int | None = None
    bit_rate: int | None = None
    video_streams: list[VideoStreamMetadata] = Field(default_factory=list)
    audio_streams: list[AudioStreamMetadata] = Field(default_factory=list)
    raw_format_tags: dict[str, str] = Field(default_factory=dict)

    @property
    def has_audio(self) -> bool:
        return bool(self.audio_streams)


class TranscriptSegment(BaseModel):
    id: str
    start_seconds: float
    end_seconds: float
    start: str
    end: str
    text: str
    raw_text: str | None = None
    confidence: float | None = None


class Transcript(BaseModel):
    source_audio_path: str | None = None
    text_path: str | None = None
    json_path: str | None = None
    language: str | None = None
    language_probability: float | None = None
    duration_seconds: float | None = None
    text: str = ""
    raw_text: str = ""
    text_normalization: Literal["simplified_chinese", "none"] = "simplified_chinese"
    segments: list[TranscriptSegment] = Field(default_factory=list)
    status: Literal["not_requested", "no_audio", "completed", "failed"] = "not_requested"


class Keyframe(BaseModel):
    id: str
    path: str
    timestamp_seconds: float
    timecode: str
    kind: Literal["sample", "shot_representative"]
    reason: str
    shot_id: str | None = None
    metadata: dict = Field(default_factory=dict)


class VisualContentBlock(BaseModel):
    block_type: Literal[
        "text",
        "formula",
        "table",
        "chart",
        "diagram",
        "code",
        "object",
        "operation",
        "question",
        "answer",
        "other",
    ] = "other"
    text: str = ""
    latex: str | None = None
    bbox: list[float] | None = None
    details: dict = Field(default_factory=dict)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    status: Literal["observed", "inferred", "corrected", "unresolved"] = "observed"


class VisualFrameAnalysis(BaseModel):
    summary: str = ""
    frame_type: Literal[
        "presentation",
        "whiteboard",
        "operation",
        "talking_head",
        "mixed",
        "other",
    ] = "other"
    teaching_roles: list[str] = Field(default_factory=list)
    blocks: list[VisualContentBlock] = Field(default_factory=list)
    spatial_relations: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class EvidenceItem(BaseModel):
    id: str
    evidence_type: Literal["metadata", "transcript", "keyframe", "visual", "ocr", "derived"]
    source_id: str
    source_path: str | None = None
    time_range: TimeRange | None = None
    content: str
    metadata: dict = Field(default_factory=dict)


class ParsedVideoSegment(BaseModel):
    id: str
    index: int
    time_range: TimeRange
    summary: str
    keywords: list[str] = Field(default_factory=list)
    transcript_segment_ids: list[str] = Field(default_factory=list)
    keyframe_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class VideoContractModel(BaseModel):
    """Strict models used for the structured video-understanding contract.

    The legacy parser models intentionally keep their permissive behaviour so
    that old v0.2 results remain readable.  Model responses, however, must not
    silently grow unknown fields before they reach local validation.
    """

    model_config = ConfigDict(extra="forbid", protected_namespaces=())


class VideoModelProvenance(VideoContractModel):
    provider: str = "aliyun_bailian"
    model: str
    input_mode: Literal["auto", "file_url", "https_url", "base64"] = "auto"
    chunk_start_seconds: float = Field(default=0.0, ge=0)
    chunk_end_seconds: float | None = Field(default=None, ge=0)
    fps: float = Field(default=1.0, gt=0)
    input_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    asr_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_version: str = "v1"
    schema_version: str = "v1"
    request_id: str | None = None
    usage: dict[str, int | float] = Field(default_factory=dict)
    elapsed_ms: float | None = Field(default=None, ge=0)
    cache_hit: bool = False

    @model_validator(mode="after")
    def validate_chunk_range(self) -> "VideoModelProvenance":
        if self.chunk_end_seconds is not None and self.chunk_end_seconds < self.chunk_start_seconds:
            raise ValueError("chunk_end_seconds must be greater than or equal to chunk_start_seconds")
        return self


class CandidateEvidenceInterval(VideoContractModel):
    interval_id: str
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    evidence_focus: Literal[
        "general",
        "presentation",
        "whiteboard",
        "operation",
        "formula",
        "chart",
        "diagram",
        "text",
    ] = "general"
    rationale: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    knowledge_point_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self) -> "CandidateEvidenceInterval":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("candidate evidence interval must have end_seconds > start_seconds")
        return self


class KnowledgePointCandidate(VideoContractModel):
    knowledge_point_id: str
    title: str
    description: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)
    asr_segment_ids: list[str] = Field(default_factory=list)
    candidate_interval_ids: list[str] = Field(default_factory=list)


class VideoChapterCandidate(VideoContractModel):
    chapter_id: str
    title: str
    summary: str = ""
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(ge=0)
    confidence: float = Field(default=0.5, ge=0, le=1)
    asr_segment_ids: list[str] = Field(default_factory=list)
    knowledge_points: list[KnowledgePointCandidate] = Field(default_factory=list)
    candidate_intervals: list[CandidateEvidenceInterval] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_range(self) -> "VideoChapterCandidate":
        if self.end_seconds <= self.start_seconds:
            raise ValueError("chapter candidate must have end_seconds > start_seconds")
        return self


class AlignmentDecision(VideoContractModel):
    candidate_id: str
    original_start_seconds: float = Field(ge=0)
    original_end_seconds: float = Field(ge=0)
    aligned_start_seconds: float = Field(ge=0)
    aligned_end_seconds: float = Field(ge=0)
    score: float = Field(ge=0, le=1)
    status: Literal["accepted", "low_confidence", "review_required", "rejected"]
    method: str = "local_boundary_alignment"
    anchors: dict[str, list[str]] = Field(default_factory=dict)
    review_required: bool = False
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ranges(self) -> "AlignmentDecision":
        if self.original_end_seconds <= self.original_start_seconds:
            raise ValueError("original candidate range must have end_seconds > start_seconds")
        if self.aligned_end_seconds <= self.aligned_start_seconds:
            raise ValueError("aligned candidate range must have end_seconds > start_seconds")
        if self.status in {"review_required", "rejected"} and not self.review_required:
            self.review_required = True
        return self


class VideoUnderstandingResult(VideoContractModel):
    """Model-discovered candidates plus local alignment, never final Evidence."""

    status: Literal["not_requested", "completed", "partial", "failed"] = "not_requested"
    video_summary: str = ""
    chapters: list[VideoChapterCandidate] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    provenance: VideoModelProvenance | None = None
    alignment_decisions: list[AlignmentDecision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConflictItem(VideoContractModel):
    id: str
    conflict_type: Literal[
        "boundary_mismatch",
        "semantic_mismatch",
        "chapter_overlap",
        "insufficient_evidence",
        "schema_or_range_error",
    ]
    severity: Literal["low", "medium", "high"] = "medium"
    candidate_id: str | None = None
    model_value: Any = None
    local_value: Any = None
    related_evidence_ids: list[str] = Field(default_factory=list)
    description: str = ""
    recommended_action: str = "review"
    status: Literal["open", "resolved", "accepted", "rejected"] = "open"
    review_required: bool = True


class RefinementRecord(VideoContractModel):
    refinement_id: str
    candidate_id: str
    requested_start_seconds: float = Field(ge=0)
    requested_end_seconds: float = Field(ge=0)
    buffered_start_seconds: float = Field(ge=0)
    buffered_end_seconds: float = Field(ge=0)
    refined_start_seconds: float | None = Field(default=None, ge=0)
    refined_end_seconds: float | None = Field(default=None, ge=0)
    fps: float = Field(gt=0)
    frame_count: int = Field(default=0, ge=0)
    ocr_frame_count: int = Field(default=0, ge=0)
    visual_frame_count: int = Field(default=0, ge=0)
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["not_requested", "completed", "partial", "failed"] = "not_requested"
    warnings: list[str] = Field(default_factory=list)
    review_required: bool = False

    @model_validator(mode="after")
    def validate_ranges(self) -> "RefinementRecord":
        if self.requested_end_seconds <= self.requested_start_seconds:
            raise ValueError("requested refinement range must have end_seconds > start_seconds")
        if self.buffered_end_seconds <= self.buffered_start_seconds:
            raise ValueError("buffered refinement range must have end_seconds > start_seconds")
        if (self.refined_start_seconds is None) != (self.refined_end_seconds is None):
            raise ValueError("refined_start_seconds and refined_end_seconds must be provided together")
        if self.refined_start_seconds is not None and self.refined_end_seconds <= self.refined_start_seconds:
            raise ValueError("refined range must have end_seconds > start_seconds")
        return self


class VideoParseOptions(BaseModel):
    video_type: Literal["auto", "presentation", "whiteboard", "operation"] = "auto"
    transcribe: bool = True
    ocr: bool = False
    ocr_max_keyframes: int = 12
    vision: bool = False
    vision_max_keyframes: int = 12
    vision_context_seconds: float = 12.0
    whisper_model_size: str = "base"
    language: str | None = "zh"
    beam_size: int = 5
    device: str = "cpu"
    compute_type: str = "int8"
    max_sample_keyframes: int = 12
    max_shots: int = 36
    shot_sample_fps: float = 2.0
    shot_threshold: float = 0.34
    min_shot_seconds: float = 1.0
    output_width: int = 960
    strict: bool = False
    video_understanding: bool = False
    video_model: str = "qwen3.7-plus"
    video_input_mode: Literal["auto", "file_url", "https_url", "base64"] = "auto"
    video_fps: float = Field(default=1.0, gt=0)
    video_max_frames: int = Field(default=1800, ge=1)
    video_chunk_seconds: float = Field(default=1800.0, gt=0)
    video_chunk_overlap_seconds: float = Field(default=15.0, ge=0)
    video_timeout_seconds: int = Field(default=600, ge=1)
    video_max_retries: int = Field(default=1, ge=0)
    video_strict_schema: bool = True
    video_max_refinement_intervals: int = Field(default=24, ge=0)
    video_max_refinement_frames: int = Field(default=120, ge=1)
    video_cache_enabled: bool = True
    video_prompt_version: str = "v1"
    video_schema_version: str = "v1"


class VideoParseResult(BaseModel):
    schema_version: str = "0.3"
    video_id: str
    created_at: datetime
    source_video: SourceVideo
    metadata: VideoMetadata
    transcript: Transcript
    keyframes: list[Keyframe] = Field(default_factory=list)
    segments: list[ParsedVideoSegment] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    artifacts: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    video_understanding: VideoUnderstandingResult | None = None
    conflicts: list[ConflictItem] = Field(default_factory=list)
    refinements: list[RefinementRecord] = Field(default_factory=list)
