from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .intermediate_schemas import DemoGenerationRequest, EvaluationReport, schema_bundle
from .parser import parse_video
from .package import build_teaching_content_package, load_teaching_content_package
from .planning import build_demo_generation_plan
from .rendering import render_demo_outputs
from .schemas import VideoParseOptions
from .quality import compare_evaluations


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "parse":
        return _parse_command(args)
    if args.command == "package":
        return _package_command(args)
    if args.command == "generate":
        return _generate_command(args)
    if args.command == "closed-loop":
        return _closed_loop_command(args)
    if args.command == "export-schemas":
        return _export_schemas_command(args)
    if args.command == "compare-evaluations":
        return _compare_evaluations_command(args)

    parser.print_help()
    return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="video-parser", description="Parse video into metadata, transcript, keyframes, segments, and evidence.")
    subparsers = parser.add_subparsers(dest="command")

    parse_cmd = subparsers.add_parser("parse", help="Parse a local video file.")
    parse_cmd.add_argument("video_path", help="Path to a local video file.")
    parse_cmd.add_argument("--output-root", default="outputs", help="Directory for parser artifacts.")
    parse_cmd.add_argument(
        "--video-type",
        choices=["auto", "presentation", "whiteboard", "operation"],
        default="auto",
        help="Keyframe strategy: presentation, whiteboard, operation, or auto fallback.",
    )
    parse_cmd.add_argument("--no-transcribe", action="store_true", help="Skip audio extraction and ASR.")
    parse_cmd.add_argument("--ocr", action="store_true", help="Run Tencent Cloud OCR on presentation/whiteboard keyframes.")
    parse_cmd.add_argument("--ocr-max-keyframes", type=int, default=12, help="Maximum strategy keyframes sent to OCR.")
    parse_cmd.add_argument("--vision", action="store_true", help="Run Alibaba Cloud Bailian visual understanding on strategy keyframes.")
    parse_cmd.add_argument("--vision-max-keyframes", type=int, default=12, help="Maximum strategy keyframes sent to Bailian vision.")
    parse_cmd.add_argument("--whisper-model", default="base", help="faster-whisper model size, for example tiny/base/small.")
    parse_cmd.add_argument("--language", default="zh", help="ASR language hint. Use empty string for auto.")
    parse_cmd.add_argument("--beam-size", type=int, default=5)
    parse_cmd.add_argument("--device", default="cpu")
    parse_cmd.add_argument("--compute-type", default="int8")
    parse_cmd.add_argument("--max-sample-keyframes", type=int, default=12)
    parse_cmd.add_argument("--max-shots", type=int, default=36)
    parse_cmd.add_argument("--shot-sample-fps", type=float, default=2.0)
    parse_cmd.add_argument("--shot-threshold", type=float, default=0.34)
    parse_cmd.add_argument("--min-shot-seconds", type=float, default=1.0)
    parse_cmd.add_argument("--output-width", type=int, default=960)
    parse_cmd.add_argument("--strict", action="store_true", help="Fail fast instead of returning partial results with warnings.")
    _add_video_understanding_options(parse_cmd)
    parse_cmd.add_argument("--print-json", action="store_true", help="Print the full result JSON to stdout.")

    package_cmd = subparsers.add_parser("package", help="Build a portable TeachingContentPackage from a parse result JSON.")
    package_cmd.add_argument("result_json", help="Path to video_parse_result.json.")
    package_cmd.add_argument("--output-dir", required=True, help="Directory for the portable package.")
    package_cmd.add_argument("--package-version", default="0.1.0")

    generate_cmd = subparsers.add_parser("generate", help="Generate PPTX, DOCX and offline HTML from a package only.")
    generate_cmd.add_argument("package_dir", help="TeachingContentPackage directory.")
    generate_cmd.add_argument("--output-dir", required=True, help="Directory for generated artifacts.")
    generate_cmd.add_argument("--title", default=None)
    generate_cmd.add_argument("--audience", default="初中学生")
    generate_cmd.add_argument("--max-slides", type=int, default=12)
    generate_cmd.add_argument("--no-answer-key", action="store_true")
    generate_cmd.add_argument("--run-label", choices=["baseline", "candidate", "golden"], default="candidate")

    closed_loop_cmd = subparsers.add_parser("closed-loop", help="Parse a video, build a package, and generate all demo outputs.")
    _add_parse_options(closed_loop_cmd, include_video_path=True)
    closed_loop_cmd.add_argument("--package-dir", required=True)
    closed_loop_cmd.add_argument("--artifact-dir", required=True)

    schemas_cmd = subparsers.add_parser("export-schemas", help="Write JSON Schema fixtures for intermediate artifacts.")
    schemas_cmd.add_argument("--output-dir", default="schemas")

    compare_cmd = subparsers.add_parser("compare-evaluations", help="Compare baseline and candidate evaluation reports.")
    compare_cmd.add_argument("baseline_report")
    compare_cmd.add_argument("candidate_report")
    compare_cmd.add_argument("--output", default=None, help="Optional JSON output path.")
    return parser


def _add_parse_options(command: argparse.ArgumentParser, *, include_video_path: bool = False) -> None:
    if include_video_path:
        command.add_argument("video_path", help="Path to a local video file.")
    command.add_argument("--output-root", default="outputs", help="Directory for parser artifacts.")
    command.add_argument("--video-type", choices=["auto", "presentation", "whiteboard", "operation"], default="auto")
    command.add_argument("--no-transcribe", action="store_true")
    command.add_argument("--ocr", action="store_true")
    command.add_argument("--ocr-max-keyframes", type=int, default=12)
    command.add_argument("--vision", action="store_true")
    command.add_argument("--vision-max-keyframes", type=int, default=12)
    command.add_argument("--whisper-model", default="base")
    command.add_argument("--language", default="zh")
    command.add_argument("--beam-size", type=int, default=5)
    command.add_argument("--device", default="cpu")
    command.add_argument("--compute-type", default="int8")
    command.add_argument("--max-sample-keyframes", type=int, default=12)
    command.add_argument("--max-shots", type=int, default=36)
    command.add_argument("--shot-sample-fps", type=float, default=2.0)
    command.add_argument("--shot-threshold", type=float, default=0.34)
    command.add_argument("--min-shot-seconds", type=float, default=1.0)
    command.add_argument("--output-width", type=int, default=960)
    command.add_argument("--strict", action="store_true")
    _add_video_understanding_options(command)


def _add_video_understanding_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("--video-understanding", action="store_true", help="Enable qwen3.7-plus global video candidates and local refinement.")
    command.add_argument("--video-model", default="qwen3.7-plus")
    command.add_argument("--video-input-mode", choices=["auto", "file_url", "https_url", "base64"], default="auto")
    command.add_argument("--video-fps", type=float, default=1.0)
    command.add_argument("--video-max-frames", type=int, default=1800)
    command.add_argument("--video-chunk-seconds", type=float, default=1800.0)
    command.add_argument("--video-chunk-overlap-seconds", type=float, default=15.0)
    command.add_argument("--video-timeout", type=int, default=600)
    command.add_argument("--video-max-retries", type=int, default=1)
    command.add_argument("--video-nonstrict-schema", action="store_true", help="Allow provider payload normalization before local validation.")
    command.add_argument("--max-refinement-intervals", type=int, default=24)
    command.add_argument("--max-refinement-frames", type=int, default=120)
    command.add_argument("--no-video-cache", action="store_true")


def _parse_command(args: argparse.Namespace) -> int:
    options = VideoParseOptions(
        video_type=args.video_type,
        transcribe=not args.no_transcribe,
        ocr=args.ocr,
        ocr_max_keyframes=args.ocr_max_keyframes,
        vision=args.vision,
        vision_max_keyframes=args.vision_max_keyframes,
        whisper_model_size=args.whisper_model,
        language=args.language or None,
        beam_size=args.beam_size,
        device=args.device,
        compute_type=args.compute_type,
        max_sample_keyframes=args.max_sample_keyframes,
        max_shots=args.max_shots,
        shot_sample_fps=args.shot_sample_fps,
        shot_threshold=args.shot_threshold,
        min_shot_seconds=args.min_shot_seconds,
        output_width=args.output_width,
        strict=args.strict,
        video_understanding=args.video_understanding,
        video_model=args.video_model,
        video_input_mode=args.video_input_mode,
        video_fps=args.video_fps,
        video_max_frames=args.video_max_frames,
        video_chunk_seconds=args.video_chunk_seconds,
        video_chunk_overlap_seconds=args.video_chunk_overlap_seconds,
        video_timeout_seconds=args.video_timeout,
        video_max_retries=args.video_max_retries,
        video_strict_schema=not args.video_nonstrict_schema,
        video_max_refinement_intervals=args.max_refinement_intervals,
        video_max_refinement_frames=args.max_refinement_frames,
        video_cache_enabled=not args.no_video_cache,
    )
    try:
        result = parse_video(Path(args.video_path), output_root=Path(args.output_root), options=options)
    except Exception as exc:  # noqa: BLE001 - CLI should show actionable failure.
        print(f"video-parser failed: {exc}", file=sys.stderr)
        return 2

    if args.print_json:
        print(result.model_dump_json(indent=2))
    else:
        print(f"result: {result.artifacts.get('result_path')}")
        print(f"video_id: {result.video_id}")
        print(f"duration_seconds: {result.metadata.duration_seconds:.3f}")
        print(f"transcript_segments: {len(result.transcript.segments)} ({result.transcript.status})")
        print(f"keyframes: {len(result.keyframes)}")
        print(f"segments: {len(result.segments)}")
        print(f"evidence: {len(result.evidence)}")
        print(f"video_understanding: {result.artifacts.get('video_understanding', {}).get('status', 'not_requested')}")
        print(f"video_candidates: {len(result.video_understanding.chapters) if result.video_understanding else 0}")
        print(f"video_conflicts: {len(result.conflicts)}")
        print(f"video_refinements: {len(result.refinements)}")
        if result.warnings:
            print("warnings:")
            for warning in result.warnings:
                print(f"- {warning}")
    return 0


def _package_command(args: argparse.Namespace) -> int:
    try:
        payload = json.loads(Path(args.result_json).read_text(encoding="utf-8"))
        package = build_teaching_content_package(payload, args.output_dir, package_version=args.package_version)
    except Exception as exc:  # noqa: BLE001 - CLI should show actionable failure.
        print(f"video-parser package failed: {exc}", file=sys.stderr)
        return 2
    print(f"package: {package.root}")
    print(f"package_id: {package.manifest.package_id}")
    print(f"package_version: {package.manifest.package_version}")
    print(f"teaching_units: {len(package.ir.teaching_units)}")
    print(f"assets: {len(package.ir.assets)}")
    if package.manifest.warnings:
        print("warnings:")
        for warning in package.manifest.warnings:
            print(f"- {warning}")
    return 0


def _generate_command(args: argparse.Namespace) -> int:
    try:
        package = load_teaching_content_package(args.package_dir)
        request = DemoGenerationRequest(
            title=args.title,
            audience=args.audience,
            max_slides=args.max_slides,
            include_answer_key=not args.no_answer_key,
        )
        plan = build_demo_generation_plan(package, request)
        result = render_demo_outputs(package, args.output_dir, plan=plan, run_label=args.run_label)
    except Exception as exc:  # noqa: BLE001 - CLI should show actionable failure.
        print(f"video-parser generate failed: {exc}", file=sys.stderr)
        return 2
    print(f"artifacts: {result['output_dir']}")
    print(f"quality: {result['quality'].status}")
    print(f"source_video_reads: {result['audit']['source_video_reads']}")
    return 0


def _closed_loop_command(args: argparse.Namespace) -> int:
    options = _options_from_args(args)
    try:
        result = parse_video(Path(args.video_path), output_root=Path(args.output_root), options=options)
        package = build_teaching_content_package(result, args.package_dir)
        artifacts = render_demo_outputs(package, args.artifact_dir)
    except Exception as exc:  # noqa: BLE001 - CLI should show actionable failure.
        print(f"video-parser closed-loop failed: {exc}", file=sys.stderr)
        return 2
    print(f"video_result: {result.artifacts.get('result_path')}")
    print(f"package: {package.root}")
    print(f"artifacts: {artifacts['output_dir']}")
    print(f"source_video_reads: {artifacts['audit']['source_video_reads']}")
    return 0


def _export_schemas_command(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, schema in schema_bundle().items():
        (output_dir / f"{name}.schema.json").write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"schemas: {output_dir.resolve()}")
    return 0


def _compare_evaluations_command(args: argparse.Namespace) -> int:
    try:
        baseline = EvaluationReport.model_validate(json.loads(Path(args.baseline_report).read_text(encoding="utf-8")))
        candidate = EvaluationReport.model_validate(json.loads(Path(args.candidate_report).read_text(encoding="utf-8")))
        comparison = compare_evaluations(baseline, candidate)
        payload = json.dumps(comparison, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            Path(args.output).write_text(payload, encoding="utf-8")
        print(payload, end="")
        return 0 if comparison["candidate_non_regression"] else 2
    except Exception as exc:  # noqa: BLE001 - CLI should report invalid fixture paths/contracts.
        print(f"video-parser compare-evaluations failed: {exc}", file=sys.stderr)
        return 2


def _options_from_args(args: argparse.Namespace) -> VideoParseOptions:
    return VideoParseOptions(
        video_type=args.video_type,
        transcribe=not args.no_transcribe,
        ocr=args.ocr,
        ocr_max_keyframes=args.ocr_max_keyframes,
        vision=args.vision,
        vision_max_keyframes=args.vision_max_keyframes,
        whisper_model_size=args.whisper_model,
        language=args.language or None,
        beam_size=args.beam_size,
        device=args.device,
        compute_type=args.compute_type,
        max_sample_keyframes=args.max_sample_keyframes,
        max_shots=args.max_shots,
        shot_sample_fps=args.shot_sample_fps,
        shot_threshold=args.shot_threshold,
        min_shot_seconds=args.min_shot_seconds,
        output_width=args.output_width,
        strict=args.strict,
        video_understanding=args.video_understanding,
        video_model=args.video_model,
        video_input_mode=args.video_input_mode,
        video_fps=args.video_fps,
        video_max_frames=args.video_max_frames,
        video_chunk_seconds=args.video_chunk_seconds,
        video_chunk_overlap_seconds=args.video_chunk_overlap_seconds,
        video_timeout_seconds=args.video_timeout,
        video_max_retries=args.video_max_retries,
        video_strict_schema=not args.video_nonstrict_schema,
        video_max_refinement_intervals=args.max_refinement_intervals,
        video_max_refinement_frames=args.max_refinement_frames,
        video_cache_enabled=not args.no_video_cache,
    )
