from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .intermediate_schemas import (
    AssetRef,
    BUILDER_VERSION,
    PackageFile,
    PackageTimeRange,
    TeachingContentIR,
    TeachingContentManifest,
)
from .ir_builder import build_teaching_content_ir
from .schemas import VideoParseResult


SOURCE_RESULT_PATH = "source/video_parse_result.json"
IR_PATH = "ir/teaching_content_ir.json"
QUALITY_PATH = "quality/parse_quality_report.json"


class PackageError(ValueError):
    pass


class PackageValidationError(PackageError):
    pass


@dataclass
class LoadedTeachingContentPackage:
    root: Path
    manifest: TeachingContentManifest
    result: VideoParseResult
    ir: TeachingContentIR
    quality_report: dict[str, Any]
    read_log: list[str] = field(default_factory=list)

    def read_bytes(self, relative_path: str) -> bytes:
        relative = _safe_relative(relative_path)
        path = self.root / Path(*relative.parts)
        if not path.is_file():
            raise PackageValidationError(f"Package file does not exist: {relative.as_posix()}")
        self.read_log.append(relative.as_posix())
        return path.read_bytes()

    def read_asset(self, asset_id: str) -> bytes:
        asset = next((item for item in self.ir.assets if item.asset_id == asset_id), None)
        if asset is None:
            raise PackageValidationError(f"Unknown asset reference: {asset_id}")
        return self.read_bytes(asset.path)

    def asset_path(self, asset_id: str) -> Path:
        asset = next((item for item in self.ir.assets if item.asset_id == asset_id), None)
        if asset is None:
            raise PackageValidationError(f"Unknown asset reference: {asset_id}")
        relative = _safe_relative(asset.path)
        path = self.root / Path(*relative.parts)
        if not path.is_file():
            raise PackageValidationError(f"Asset file does not exist: {asset_id}")
        self.read_log.append(relative.as_posix())
        return path

    def audit_snapshot(self) -> list[str]:
        return list(self.read_log)


def build_teaching_content_package(
    result: VideoParseResult | Mapping[str, Any],
    output_dir: str | Path,
    *,
    package_id: str | None = None,
    package_version: str = "0.1.0",
) -> LoadedTeachingContentPackage:
    parsed = result if isinstance(result, VideoParseResult) else VideoParseResult.model_validate(result)
    root = Path(output_dir).expanduser().resolve()
    for directory in (root / "source", root / "ir", root / "assets" / "keyframes", root / "assets" / "crops", root / "assets" / "formulas", root / "assets" / "charts", root / "assets" / "diagrams", root / "quality"):
        directory.mkdir(parents=True, exist_ok=True)

    package_id = package_id or f"tcp_{parsed.video_id}"
    source_paths = _source_path_candidates(parsed)
    asset_refs: list[AssetRef] = []
    keyframe_asset_ids: dict[str, str] = {}
    warnings: list[str] = []
    for keyframe in parsed.keyframes:
        source_path = _resolve_keyframe_path(keyframe.path, source_paths)
        asset_id = f"asset_{_slug(keyframe.id)}"
        if source_path and source_path.is_file():
            suffix = source_path.suffix.lower() or ".jpg"
            relative = f"assets/keyframes/{asset_id}{suffix}"
            target = root / Path(*PurePosixPath(relative).parts)
            shutil.copy2(source_path, target)
            asset_refs.append(
                AssetRef(
                    asset_id=asset_id,
                    kind="keyframe",
                    path=relative,
                    sha256=file_sha256(target),
                    size_bytes=target.stat().st_size,
                    mime_type=mimetypes.guess_type(target.name)[0] or "image/jpeg",
                    required=keyframe.kind == "sample" or bool(keyframe.shot_id),
                    source_id=keyframe.id,
                    time_range=PackageTimeRange(start_seconds=keyframe.timestamp_seconds, end_seconds=keyframe.timestamp_seconds),
                    metadata={"kind": keyframe.kind, "reason": keyframe.reason, "timecode": keyframe.timecode},
                )
            )
            keyframe_asset_ids[keyframe.id] = asset_id
        else:
            warnings.append(f"Missing optional keyframe asset for {keyframe.id}; image output may be reduced.")

    ir = build_teaching_content_ir(
        parsed,
        package_id=package_id,
        package_version=package_version,
        asset_refs=asset_refs,
        keyframe_asset_ids=keyframe_asset_ids,
    )
    sanitized_result = _sanitize_result(parsed, keyframe_asset_ids, asset_refs)
    _write_json(root / SOURCE_RESULT_PATH, sanitized_result)
    _write_json(root / IR_PATH, ir.model_dump(mode="json"))
    quality_report = _quality_report(parsed, ir, asset_refs, warnings)
    _write_json(root / QUALITY_PATH, quality_report)

    files = _package_files(root)
    manifest = TeachingContentManifest(
        package_id=package_id,
        package_version=package_version,
        builder_version=BUILDER_VERSION,
        source_video={
            "file_name": parsed.source_video.file_name,
            "sha1": parsed.source_video.sha1,
            "duration_seconds": parsed.metadata.duration_seconds,
            "original_path_available": False,
        },
        schema_versions={
            "video_parse_result": parsed.schema_version,
            "teaching_content_ir": ir.schema_version,
            "manifest": "0.1",
        },
        files=files,
        assets=[asset.asset_id for asset in asset_refs],
        warnings=warnings,
        status="partial" if warnings else "complete",
    )
    _write_json(root / "manifest.json", manifest.model_dump(mode="json"))
    return load_teaching_content_package(root)


def load_teaching_content_package(package_dir: str | Path) -> LoadedTeachingContentPackage:
    root = Path(package_dir).expanduser().resolve()
    if not root.is_dir():
        raise PackageValidationError(f"Package directory does not exist: {root}")
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise PackageValidationError("Package is missing manifest.json")
    try:
        manifest = TeachingContentManifest.model_validate(json.loads(manifest_path.read_text(encoding="utf-8")))
        source_payload = json.loads((root / Path(*PurePosixPath(manifest.source_result_path).parts)).read_text(encoding="utf-8"))
        ir_payload = json.loads((root / Path(*PurePosixPath(manifest.ir_path).parts)).read_text(encoding="utf-8"))
        quality_path = root / Path(*PurePosixPath(manifest.quality_report_path).parts)
        quality_report = json.loads(quality_path.read_text(encoding="utf-8")) if quality_path.is_file() else {}
        result = VideoParseResult.model_validate(source_payload)
        ir = TeachingContentIR.model_validate(ir_payload)
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise PackageValidationError(f"Package JSON validation failed: {exc}") from exc

    _assert_no_absolute_paths(manifest.model_dump(mode="json"), "manifest")
    _assert_no_absolute_paths(source_payload, "source result")
    _assert_no_absolute_paths(ir_payload, "IR")
    _validate_package_files(root, manifest)
    _validate_references(result, ir, manifest)
    # 记住这份结果是从哪个包目录读出来的：包内 keyframe 路径是相对包根的
    # （assets/keyframes/...），只在内存里带出去，重新打包时才能凭它把图像找回来。
    # 写包时 _sanitize_result 的 artifacts 白名单不含 package_root，不会落盘。
    if isinstance(result.artifacts, dict):
        result.artifacts["package_root"] = str(root)
    return LoadedTeachingContentPackage(root=root, manifest=manifest, result=result, ir=ir, quality_report=quality_report)


def _source_path_candidates(parsed: VideoParseResult) -> list[Path]:
    candidates: list[Path] = []
    artifacts = parsed.artifacts if isinstance(parsed.artifacts, dict) else {}
    run_dir = artifacts.get("run_dir")
    if run_dir:
        candidates.append(Path(str(run_dir)))
    # 从包里读出的结果带有来源包根：包内 keyframe 路径是相对包根的
    # assets/keyframes/...，重新打包时只有这个根能把图像解析出来。
    package_root = artifacts.get("package_root")
    if package_root:
        candidates.append(Path(str(package_root)))
    if parsed.source_video.path:
        candidates.append(Path(parsed.source_video.path).expanduser())
    return candidates


def _resolve_keyframe_path(value: str, roots: list[Path]) -> Path | None:
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_file():
        return path.resolve()
    for root in roots:
        candidate = root / value if not Path(value).is_absolute() else Path(value)
        if candidate.is_file():
            return candidate.resolve()
        candidate = root / Path(value).name
        if candidate.is_file():
            return candidate.resolve()
    return None


def _sanitize_result(parsed: VideoParseResult, keyframe_asset_ids: Mapping[str, str], assets: list[AssetRef]) -> dict[str, Any]:
    payload = parsed.model_dump(mode="json")
    payload["source_video"]["path"] = ""
    payload["source_video"]["original_path_available"] = False
    transcript = payload.get("transcript", {})
    for transcript_path_field in ("source_audio_path", "text_path", "json_path"):
        transcript[transcript_path_field] = None
    for keyframe in payload.get("keyframes", []):
        keyframe["path"] = next((asset.path for asset in assets if asset.source_id == keyframe.get("id")), "")
    for evidence in payload.get("evidence", []):
        source_id = evidence.get("source_id")
        evidence["source_path"] = next((asset.path for asset in assets if asset.source_id == source_id), None)
    raw_artifacts = payload.get("artifacts", {})
    payload["artifacts"] = {
        key: value
        for key, value in raw_artifacts.items()
        if key in {
            "video_type",
            "keyframe_strategy",
            "ocr",
            "vision",
            "video_understanding",
            "parser",
            "options",
            "options_sha256",
        }
    }
    payload["artifacts"]["package_snapshot"] = True
    return payload


def _quality_report(parsed: VideoParseResult, ir: TeachingContentIR, assets: list[AssetRef], warnings: list[str]) -> dict[str, Any]:
    visual = sum(1 for item in parsed.evidence if item.evidence_type == "visual")
    ocr = sum(1 for item in parsed.evidence if item.evidence_type == "ocr")
    keyframe_metrics = _keyframe_metrics(parsed)
    ocr_metrics = _ocr_metrics(parsed)
    candidate_segments = [
        segment
        for segment in parsed.segments
        if isinstance(segment.metadata, dict) and isinstance(segment.metadata.get("candidate"), dict)
    ]
    verified_candidates = []
    for segment in candidate_segments:
        candidate = segment.metadata.get("candidate") if isinstance(segment.metadata, dict) else None
        validation = candidate.get("validation") if isinstance(candidate, dict) else None
        if isinstance(validation, dict) and validation.get("formal_ir_eligible") is True:
            verified_candidates.append(segment)
    video_understanding = parsed.video_understanding
    model_chapter_count = len(video_understanding.chapters) if video_understanding is not None else 0
    model_interval_count = (
        sum(len(chapter.candidate_intervals) for chapter in video_understanding.chapters)
        if video_understanding is not None
        else 0
    )
    return {
        "report_version": "0.3",
        "status": "warning" if warnings or ir.quality.status != "ok" else "ok",
        "video_id": parsed.video_id,
        "source_modality": {
            "transcript_status": parsed.transcript.status,
            "ocr_evidence_count": ocr,
            "visual_evidence_count": visual,
            "keyframe_count": len(parsed.keyframes),
            "video_understanding_status": video_understanding.status if video_understanding is not None else "not_requested",
            "video_understanding_model": (
                video_understanding.provenance.model
                if video_understanding is not None and video_understanding.provenance is not None
                else None
            ),
            "video_model_chapter_count": model_chapter_count,
            "video_model_interval_count": model_interval_count,
        },
        "ir": ir.quality.model_dump(mode="json"),
        "assets": {"count": len(assets), "missing_keyframe_assets": len(parsed.keyframes) - len(assets)},
        "metrics": {
            **keyframe_metrics,
            **ocr_metrics,
            "video_candidate_segment_count": float(len(candidate_segments)),
            "video_verified_candidate_segment_count": float(len(verified_candidates)),
            "video_review_candidate_segment_count": float(len(candidate_segments) - len(verified_candidates)),
            "video_conflict_count": float(len(parsed.conflicts)),
            "video_refinement_count": float(len(parsed.refinements)),
            "video_understanding_completed": float(video_understanding is not None and video_understanding.status == "completed"),
            "video_understanding_degraded": float(video_understanding is not None and video_understanding.status in {"failed", "partial"}),
        },
        "thresholds": {
            "keyframe_duplicate_rate_max": 0.15,
            "ocr_nonempty_text_rate_min_when_requested": 0.0,
        },
        "warnings": warnings,
        "generation_boundary": {"original_video_included": False, "original_video_access_required": False},
    }


def _keyframe_metrics(parsed: VideoParseResult) -> dict[str, float]:
    sample_frames = [item for item in parsed.keyframes if item.kind == "sample"]
    hashes: list[str] = []
    unreadable = 0
    for frame in sample_frames:
        try:
            hashes.append(file_sha256(Path(frame.path)))
        except (OSError, ValueError):
            # 读不到的帧不能算"每帧都不同"：以前它被压成唯一的 'missing:<id>'
            # 占位符参与去重，"全部读不到"于是报成 unique=100%、duplicate_rate=0，
            # 质量指标反向变好、真实故障被掩盖。这里把它排除在统计之外并单独计数。
            unreadable += 1
    unique_count = len(set(hashes))
    duplicate_count = max(0, len(hashes) - unique_count)
    return {
        "sample_keyframe_count": float(len(sample_frames)),
        "sample_keyframe_readable_count": float(len(hashes)),
        "sample_keyframe_unreadable_count": float(unreadable),
        "sample_keyframe_unique_count": float(unique_count),
        "keyframe_duplicate_count": float(duplicate_count),
        "keyframe_duplicate_rate": round(duplicate_count / len(hashes), 6) if hashes else 0.0,
    }


def _ocr_metrics(parsed: VideoParseResult) -> dict[str, float]:
    statuses: list[str] = []
    texts: list[str] = []
    for frame in parsed.keyframes:
        ocr_payload = frame.metadata.get("ocr") if isinstance(frame.metadata, dict) else None
        if not isinstance(ocr_payload, dict):
            continue
        statuses.append(str(ocr_payload.get("status", "unknown")))
        texts.append(re.sub(r"\s+", "", str(ocr_payload.get("text", ""))))
    counts = Counter(statuses)
    processed = len(statuses)
    completed = counts.get("completed", 0) + counts.get("completed_no_text", 0)
    failed = counts.get("failed", 0)
    nonempty = sum(bool(text) for text in texts)
    status_changes = sum(left != right for left, right in zip(statuses, statuses[1:]))
    text_changes = sum(left != right for left, right in zip(texts, texts[1:]))
    denominator = max(1, processed - 1)
    return {
        "ocr_processed_count": float(processed),
        "ocr_completed_count": float(completed),
        "ocr_failed_count": float(failed),
        "ocr_nonempty_text_rate": round(nonempty / processed, 6) if processed else 0.0,
        "ocr_success_rate": round(completed / processed, 6) if processed else 0.0,
        "ocr_status_change_rate": round(status_changes / denominator, 6) if processed > 1 else 0.0,
        "ocr_text_change_rate": round(text_changes / denominator, 6) if processed > 1 else 0.0,
    }


def _package_files(root: Path) -> list[PackageFile]:
    files: list[PackageFile] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        relative = path.relative_to(root).as_posix()
        role = "asset" if relative.startswith("assets/") else "source" if relative.startswith("source/") else "ir" if relative.startswith("ir/") else "quality" if relative.startswith("quality/") else "artifact"
        files.append(PackageFile(path=relative, sha256=file_sha256(path), size_bytes=path.stat().st_size, required=role != "asset", role=role))
    return files


def _validate_package_files(root: Path, manifest: TeachingContentManifest) -> None:
    for item in manifest.files:
        path = root / Path(*PurePosixPath(item.path).parts)
        if not path.is_file():
            if item.required:
                raise PackageValidationError(f"Required package file is missing: {item.path}")
            continue
        if file_sha256(path) != item.sha256:
            raise PackageValidationError(f"Package file hash mismatch: {item.path}")
        if path.stat().st_size != item.size_bytes:
            raise PackageValidationError(f"Package file size mismatch: {item.path}")


def _validate_references(result: VideoParseResult, ir: TeachingContentIR, manifest: TeachingContentManifest) -> None:
    evidence_ids = {item.id for item in result.evidence}
    asset_ids = {item.asset_id for item in ir.assets}
    if set(manifest.assets) != asset_ids:
        raise PackageValidationError("Manifest asset list does not match IR asset list")
    for unit in ir.teaching_units:
        missing = set(unit.evidence_refs) - evidence_ids
        if missing:
            raise PackageValidationError(f"Teaching unit {unit.id} references missing evidence: {sorted(missing)}")
        for block in unit.content_blocks:
            if set(block.evidence_refs) - evidence_ids:
                raise PackageValidationError(f"Content block {block.id} references missing evidence")
            if set(block.asset_refs) - asset_ids:
                raise PackageValidationError(f"Content block {block.id} references missing assets")
    if set(ir.source_refs) - evidence_ids:
        raise PackageValidationError("IR source_refs contains unknown evidence IDs")


def _assert_no_absolute_paths(value: Any, label: str) -> None:
    matches: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, str):
            if Path(item).is_absolute() or re.match(r"^[A-Za-z]:[\\/]", item) or item.startswith("\\\\"):
                matches.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    if matches:
        raise PackageValidationError(f"{label} contains absolute paths: {matches[:2]}")


def _safe_relative(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or normalized.startswith("/") or ":" in normalized.split("/")[0] or ".." in path.parts:
        raise PackageValidationError(f"Unsafe package path: {value}")
    return path


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")
    return slug or "asset"
