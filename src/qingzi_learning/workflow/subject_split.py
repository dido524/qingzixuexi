"""Pure, deterministic per-page subject assignment and analysis splitting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

from qingzi_learning.domain import (
    AnalysisResult,
    CapturedDocument,
    CapturedPage,
    PageSubjectAssignment,
    Subject,
)


SUBJECT_SUFFIX = {
    Subject.CHINESE: "chinese",
    Subject.MATH: "math",
    Subject.ENGLISH: "english",
}


@dataclass(frozen=True)
class PendingPageSubject:
    page: int
    image_path: Path
    suggested_subject: Subject
    confidence: float
    reason: str


@dataclass(frozen=True)
class SubjectPageGroup:
    subject: Subject
    document_id: str
    page_numbers: tuple[int, ...]


@dataclass(frozen=True)
class ResolvedPageSubject:
    """The authoritative outcome for one page after model or human resolution."""

    page: int
    subject: Subject
    source: Literal["model", "human"]
    confidence: float


@dataclass(frozen=True)
class SubjectSplitPlan:
    parent_document_id: str
    groups: tuple[SubjectPageGroup, ...]
    unresolved_pages: tuple[PendingPageSubject, ...]
    resolved_pages: tuple[ResolvedPageSubject, ...]


def build_subject_split_plan(
    document: CapturedDocument,
    analysis: AnalysisResult,
    overrides: Mapping[int, Subject],
    threshold: float,
) -> SubjectSplitPlan:
    """Resolve page subjects without side effects and build stable child groups."""
    _validate_threshold(threshold)
    if analysis.document_id != document.document_id:
        raise ValueError("分析结果与拍摄资料不匹配")

    pages_by_number = _pages_by_number(document)
    assignments = _assignments_by_page(analysis, pages_by_number)
    needs_confirmation = {
        page
        for page, assignment in assignments.items()
        if assignment.needs_confirmation or assignment.confidence < threshold
    }
    _validate_overrides(overrides, needs_confirmation)

    unresolved_pages = tuple(
        PendingPageSubject(
            page=page,
            image_path=pages_by_number[page].path,
            suggested_subject=assignments[page].suggested_subject,
            confidence=assignments[page].confidence,
            reason=assignments[page].reason,
        )
        for page in sorted(needs_confirmation - overrides.keys())
    )
    unresolved_page_numbers = {item.page for item in unresolved_pages}
    resolved_pages = tuple(
        ResolvedPageSubject(
            page=page,
            subject=overrides[page] if page in overrides else assignments[page].suggested_subject,
            source="human" if page in overrides else "model",
            confidence=1.0 if page in overrides else assignments[page].confidence,
        )
        for page in sorted(set(pages_by_number) - unresolved_page_numbers)
    )
    if unresolved_pages:
        return SubjectSplitPlan(document.document_id, (), unresolved_pages, resolved_pages)

    groups = _groups_for(document.document_id, resolved_pages)
    return SubjectSplitPlan(document.document_id, groups, (), resolved_pages)


def split_analysis(analysis: AnalysisResult, plan: SubjectSplitPlan) -> tuple[AnalysisResult, ...]:
    """Create child analyses using only each group's final, resolved page subjects."""
    if plan.parent_document_id != analysis.document_id:
        raise ValueError("拆分计划与分析结果不匹配")
    if plan.unresolved_pages:
        raise ValueError("仍有页面学科待确认")

    final_assignments = _resolved_by_page(plan)
    original_assignments = _analysis_assignments_by_page(analysis)
    canonical_groups = _groups_for(
        plan.parent_document_id,
        tuple(final_assignments[page] for page in sorted(final_assignments)),
    )
    if (
        not plan.groups
        or set(final_assignments) != set(original_assignments)
        or plan.groups != canonical_groups
    ):
        raise ValueError("拆分计划必须完整覆盖页面")
    group_pages = set()
    children = []
    for group in plan.groups:
        if not group.page_numbers or not isinstance(group.subject, Subject):
            raise ValueError("拆分分组不能为空")
        if group_pages.intersection(group.page_numbers):
            raise ValueError("拆分页面不能重复")
        group_pages.update(group.page_numbers)
        resolved = tuple(final_assignments.get(page) for page in group.page_numbers)
        if any(item is None or item.subject != group.subject for item in resolved):
            raise ValueError("拆分分组与页面分配不一致")
        page_subjects = tuple(
            PageSubjectAssignment(
                page=item.page,
                suggested_subject=item.subject,
                confidence=item.confidence,
                needs_confirmation=False,
                reason=original_assignments[item.page].reason,
            )
            for item in resolved
        )
        page_set = set(group.page_numbers)
        questions = tuple(question for question in analysis.questions if question.page in page_set)
        children.append(
            AnalysisResult(
                document_id=group.document_id,
                subject=group.subject,
                subject_confidence=min(item.confidence for item in resolved),
                document_type=analysis.document_type,
                grading_mode=analysis.grading_mode,
                teacher_mark_evidence=analysis.teacher_mark_evidence,
                questions=questions,
                summary=(
                    f"来自批次 {plan.parent_document_id}，共 {len(group.page_numbers)} 页、"
                    f"{len(questions)} 题。"
                ),
                page_subjects=page_subjects,
            )
        )
    if group_pages != set(final_assignments):
        raise ValueError("拆分分组未覆盖全部页面")
    return tuple(children)


def _validate_threshold(threshold: float) -> None:
    if not 0 <= threshold <= 1:
        raise ValueError("学科置信度阈值必须在 0 到 1 之间")


def _pages_by_number(document: CapturedDocument) -> dict[int, CapturedPage]:
    pages = {page.page_number: page for page in document.pages}
    if not pages or len(pages) != len(document.pages):
        raise ValueError("拍摄页面必须非空且页码唯一")
    return pages


def _assignments_by_page(
    analysis: AnalysisResult, pages_by_number: Mapping[int, CapturedPage]
) -> dict[int, PageSubjectAssignment]:
    assignments = _analysis_assignments_by_page(analysis)
    if set(assignments) != set(pages_by_number):
        raise ValueError("页面学科映射必须完整且仅覆盖拍摄页面")
    return assignments


def _analysis_assignments_by_page(analysis: AnalysisResult) -> dict[int, PageSubjectAssignment]:
    assignments = {assignment.page: assignment for assignment in analysis.page_subjects}
    if len(assignments) != len(analysis.page_subjects):
        raise ValueError("页面学科映射不能重复")
    if any(not isinstance(assignment.suggested_subject, Subject) for assignment in analysis.page_subjects):
        raise ValueError("页面学科映射包含无效学科")
    return assignments


def _validate_overrides(overrides: Mapping[int, Subject], needs_confirmation: set[int]) -> None:
    if not set(overrides).issubset(needs_confirmation):
        raise ValueError("只能确认待确认页面")
    if any(not isinstance(subject, Subject) for subject in overrides.values()):
        raise ValueError("人工确认的学科无效")


def _groups_for(
    parent_document_id: str, resolved_pages: tuple[ResolvedPageSubject, ...]
) -> tuple[SubjectPageGroup, ...]:
    pages_by_subject = {
        subject: tuple(item.page for item in resolved_pages if item.subject == subject)
        for subject in Subject
    }
    subjects = tuple(subject for subject in Subject if pages_by_subject[subject])
    if not subjects:
        raise ValueError("拆分分组不能为空")
    multiple_subjects = len(subjects) > 1
    return tuple(
        SubjectPageGroup(
            subject=subject,
            document_id=(
                f"{parent_document_id}--{SUBJECT_SUFFIX[subject]}"
                if multiple_subjects
                else parent_document_id
            ),
            page_numbers=pages_by_subject[subject],
        )
        for subject in subjects
    )


def _resolved_by_page(plan: SubjectSplitPlan) -> dict[int, ResolvedPageSubject]:
    assignments = {item.page: item for item in plan.resolved_pages}
    if len(assignments) != len(plan.resolved_pages):
        raise ValueError("最终页面学科映射不能重复")
    return assignments
