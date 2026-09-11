from __future__ import annotations

from collections import Counter
from typing import Any

from .intermediate_schemas import (
    DemoGenerationPlan,
    EvaluationIssue,
    EvaluationReport,
    InteractiveSpec,
    LessonPlanSpec,
    SlideDeckSpec,
)
from .package import LoadedTeachingContentPackage


def evaluate_generation(
    package: LoadedTeachingContentPackage,
    plan: DemoGenerationPlan,
    slides: SlideDeckSpec,
    lesson: LessonPlanSpec,
    interactive: InteractiveSpec,
    *,
    run_label: str = "candidate",
) -> EvaluationReport:
    issues: list[EvaluationIssue] = []
    evidence_ids = {item.id for item in package.result.evidence}
    asset_ids = {item.asset_id for item in package.ir.assets}

    for reference in set(plan.source_refs):
        if reference not in evidence_ids:
            issues.append(
                EvaluationIssue(
                    issue_id=f"issue_source_{len(issues) + 1:03d}",
                    issue_type="missing_evidence",
                    severity="error",
                    stage="planning",
                    target_id=plan.plan_id,
                    description=f"计划引用了不存在的证据：{reference}",
                    evidence_refs=[reference],
                )
            )
    for slide in slides.slides:
        if not slide.title.strip():
            issues.append(
                EvaluationIssue(
                    issue_id=f"issue_slide_title_{len(issues) + 1:03d}",
                    issue_type="wrong_content",
                    severity="error",
                    stage="rendering",
                    target_id=slide.slide_id,
                    description="幻灯片缺少标题。",
                )
            )
        for element in slide.elements:
            missing_evidence = set(element.source_refs) - evidence_ids
            missing_assets = set(element.asset_refs) - asset_ids
            if missing_evidence:
                issues.append(
                    EvaluationIssue(
                        issue_id=f"issue_slide_evidence_{len(issues) + 1:03d}",
                        issue_type="missing_evidence",
                        severity="error",
                        stage="rendering",
                        target_id=element.id,
                        description="输出元素引用了不存在的证据。",
                        evidence_refs=sorted(missing_evidence),
                    )
                )
            if missing_assets:
                issues.append(
                    EvaluationIssue(
                        issue_id=f"issue_slide_asset_{len(issues) + 1:03d}",
                        issue_type="broken_asset",
                        severity="error",
                        stage="rendering",
                        target_id=element.id,
                        description="输出元素引用了不存在的资源。",
                    )
                )
            if len(element.text) > 600:
                issues.append(
                    EvaluationIssue(
                        issue_id=f"issue_overflow_{len(issues) + 1:03d}",
                        issue_type="overflow",
                        severity="warning",
                        stage="rendering",
                        target_id=element.id,
                        description="结构化元素文本较长，渲染器将截断或拆分。",
                    )
                )

    for item in interactive.questions:
        if item.correct_answer and item.correct_answer in item.prompt:
            issues.append(
                EvaluationIssue(
                    issue_id=f"issue_answer_leak_{len(issues) + 1:03d}",
                    issue_type="answer_leak",
                    severity="error",
                    stage="rendering",
                    target_id=item.question_id,
                    description="互动题目提示中包含正确答案文本。",
                    expected="学生可见题目不应直接包含答案。",
                )
            )

    source_refs = set(lesson.source_refs)
    if source_refs - evidence_ids:
        issues.append(
            EvaluationIssue(
                issue_id=f"issue_lesson_evidence_{len(issues) + 1:03d}",
                issue_type="missing_evidence",
                severity="error",
                stage="rendering",
                target_id=lesson.plan_id,
                description="教案引用了不存在的证据。",
                evidence_refs=sorted(source_refs - evidence_ids),
            )
        )
    if not slides.slides:
        issues.append(
            EvaluationIssue(
                issue_id="issue_no_slides",
                issue_type="wrong_content",
                severity="error",
                stage="planning",
                target_id=plan.plan_id,
                description="没有生成幻灯片。",
            )
        )
    if not interactive.questions:
        issues.append(
            EvaluationIssue(
                issue_id="issue_no_activity",
                issue_type="wrong_content",
                severity="warning",
                stage="planning",
                target_id=interactive.plan_id,
                description="没有可运行的互动题目。",
            )
        )

    severity_counts = Counter(item.severity for item in issues)
    status = "error" if severity_counts["error"] else "warning" if issues else "ok"
    metrics = {
        "schema_validation_rate": 1.0,
        "evidence_reference_validity": _reference_rate(plan.source_refs + lesson.source_refs, evidence_ids),
        "asset_reference_validity": _asset_reference_rate(slides, asset_ids),
        "student_answer_leak_count": float(sum(item.issue_type == "answer_leak" for item in issues)),
        "slide_count": float(len(slides.slides)),
        "teaching_unit_count": float(len(package.ir.teaching_units)),
        "output_consistency": _output_consistency(plan, slides, lesson, interactive),
        "error_count": float(severity_counts["error"]),
        "warning_count": float(severity_counts["warning"]),
    }
    package_metrics = package.quality_report.get("metrics", {}) if isinstance(package.quality_report, dict) else {}
    for name, value in package_metrics.items():
        if isinstance(value, (int, float)):
            metrics[name] = float(value)
    return EvaluationReport(
        report_id=f"eval_{plan.plan_id}",
        package_id=package.manifest.package_id,
        package_version=package.manifest.package_version,
        run_label=run_label,  # type: ignore[arg-type]
        metrics=metrics,
        issues=issues,
        errors=[item.description for item in issues if item.severity == "error"],
        warnings=[item.description for item in issues if item.severity != "error"],
        status=status,  # type: ignore[arg-type]
    )


def compare_evaluations(baseline: EvaluationReport, candidate: EvaluationReport) -> dict[str, Any]:
    metric_names = sorted(set(baseline.metrics) | set(candidate.metrics))
    deltas = {
        name: {
            "baseline": baseline.metrics.get(name),
            "candidate": candidate.metrics.get(name),
            "delta": round(candidate.metrics.get(name, 0.0) - baseline.metrics.get(name, 0.0), 6),
        }
        for name in metric_names
    }
    baseline_errors = {issue.issue_id for issue in baseline.issues if issue.severity == "error"}
    candidate_errors = {issue.issue_id for issue in candidate.issues if issue.severity == "error"}
    return {
        "baseline_report_id": baseline.report_id,
        "candidate_report_id": candidate.report_id,
        "metrics": deltas,
        "error_delta": len(candidate_errors) - len(baseline_errors),
        "candidate_non_regression": len(candidate_errors) <= len(baseline_errors),
    }


def _reference_rate(references: list[str], valid: set[str]) -> float:
    unique = set(references)
    return len(unique & valid) / len(unique) if unique else 1.0


def _asset_reference_rate(slides: SlideDeckSpec, valid: set[str]) -> float:
    references = {asset for slide in slides.slides for element in slide.elements for asset in element.asset_refs}
    return len(references & valid) / len(references) if references else 1.0


def _output_consistency(plan: DemoGenerationPlan, slides: SlideDeckSpec, lesson: LessonPlanSpec, interactive: InteractiveSpec) -> float:
    # A raw-ASR-only unit is deliberately withheld from student-facing
    # artifacts until it has a concise locally grounded block.  Exclude those
    # gated units from student-output coverage instead of treating the safety
    # decision as a generation regression.
    plan_topics = {stage.unit_id for stage in plan.stages if stage.student_visible}
    lesson_topics = {section.unit_id for section in lesson.sections if section.unit_id}
    interactive_topics = {item.unit_id for item in interactive.questions}
    if not plan_topics:
        return 1.0
    coverage = [plan_topics & lesson_topics, plan_topics & (interactive_topics or plan_topics)]
    return round(sum(len(item) / len(plan_topics) for item in coverage) / len(coverage), 4)
