from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.domain import (
    AnalysisResult,
    GradingMode,
    QuestionAnalysis,
    QuestionStatus,
    QuestionType,
    Subject,
)
from qingzi_learning.export.markdown import MarkdownExporter
from qingzi_learning.knowledge.updater import KnowledgeUpdater
from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import KnowledgeRepository


@pytest.fixture
def repo(tmp_path: Path) -> KnowledgeRepository:
    config = AppConfig(
        knowledge_root=tmp_path / "knowledge",
        subjects=("语文", "数学", "英语"),
        camera_vid=1,
        camera_pid=2,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "app-data",
    )
    repository = KnowledgeRepository(config)
    yield repository
    repository.close()


@pytest.fixture
def stored_analysis(repo: KnowledgeRepository) -> AnalysisResult:
    analysis = AnalysisResult(
        document_id="doc-20260915-001",
        subject=Subject.MATH,
        subject_confidence=0.96,
        document_type="试卷",
        grading_mode=GradingMode.MIXED,
        teacher_mark_evidence=("page_002: red cross",),
        questions=(
            QuestionAnalysis(
                question_id="4",
                question_type=QuestionType.APPLICATION,
                page=2,
                prompt_summary="分数除法应用题",
                student_answer="12",
                reference_answer="18",
                status=QuestionStatus.INCORRECT,
                decision_source="teacher",
                knowledge_points=("分数除法",),
                error_categories=("列式",),
                confidence=0.93,
                reason="单位1识别错误",
            ),
        ),
        summary="本次主要问题是单位1识别。",
    )
    archived_page = (
        KnowledgePaths(repo.config).document_directory("数学", 2026, 9, analysis.document_id)
        / "page_002.jpg"
    )
    archived_page.write_bytes(b"test image")
    repo.create_document(
        document_id=analysis.document_id,
        subject=analysis.subject.value,
        document_type=analysis.document_type,
        pages=((2, str(archived_page), "b" * 64),),
    )
    KnowledgeUpdater(repo, now=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc)).apply(analysis)
    return analysis


@pytest.fixture
def exporter(repo: KnowledgeRepository) -> MarkdownExporter:
    return MarkdownExporter(repo, KnowledgePaths(repo.config))


def test_analysis_markdown_links_question_to_original_page(
    exporter: MarkdownExporter, stored_analysis: AnalysisResult
) -> None:
    path = exporter.export_document(stored_analysis.document_id)
    text = path.read_text("utf-8")

    assert "source_page: 2" in text
    assert "page_002.jpg" in text
    assert "status: incorrect" in text
    assert "../%E5%8E%9F%E5%A7%8B%E8%B5%84%E6%96%99/2026/09/doc-20260915-001/page_002.jpg" in text
    assert exporter.knowledge_point_path("数学", "分数除法").exists()


def test_subject_export_is_deterministic_and_surfaces_traceable_weakness(
    exporter: MarkdownExporter, stored_analysis: AnalysisResult
) -> None:
    first = exporter.export_subject("数学")
    first_text = first.read_text("utf-8")
    second = exporter.export_subject("数学")

    assert second == first
    assert first_text == second.read_text("utf-8")
    assert "# 数学知识库" in first_text
    assert "分数除法" in first_text
    assert "趋势: 下降" in first_text
    assert "## 错题本" in first_text
    assert "第 2 页第 4 题" in first_text
    assert "doc-20260915-001.md" in first_text
    assert "../%E5%88%86%E6%9E%90%E8%AE%B0%E5%BD%95/" in first_text


def test_document_export_rejects_missing_document(exporter: MarkdownExporter) -> None:
    with pytest.raises(ValueError, match="不存在"):
        exporter.export_document("missing")


@pytest.mark.parametrize("document_id", ("../outside", r"C:\\outside", "nested/document"))
def test_document_export_refuses_untrusted_identifier_without_writing_outside_root(
    repo: KnowledgeRepository, exporter: MarkdownExporter, tmp_path: Path, document_id: str
) -> None:
    repo.create_document(
        document_id=document_id,
        subject="数学",
        document_type="作业",
        pages=((1, str(tmp_path / "knowledge" / "数学" / "原始资料" / "page_001.jpg"), "c" * 64),),
    )

    with pytest.raises(ValueError, match="文件名"):
        exporter.export_document(document_id)

    assert not (tmp_path / "outside.md").exists()
    assert not (tmp_path / "knowledge" / "数学" / "分析记录" / "outside.md").exists()


def test_subject_export_writes_complete_error_index_and_resolvable_note_links(
    repo: KnowledgeRepository, exporter: MarkdownExporter
) -> None:
    def save(subject: Subject, index: int) -> None:
        document_id = f"{subject.value}-{index:03d}"
        page = repo.config.knowledge_root / subject.value / "原始资料" / f"page_{index:03d}.jpg"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_bytes(b"image")
        repo.create_document(
            document_id=document_id,
            subject=subject.value,
            document_type="作业",
            pages=((1, str(page), f"{index + 1:064x}"),),
        )
        repo.save_analysis(
            AnalysisResult(
                document_id=document_id,
                subject=subject,
                subject_confidence=0.96,
                document_type="作业",
                grading_mode=GradingMode.AUTO_GRADE,
                teacher_mark_evidence=(),
                questions=(
                    QuestionAnalysis(
                        question_id="1",
                        question_type=QuestionType.OBJECTIVE,
                        page=1,
                        prompt_summary=f"题目 {index}",
                        student_answer="A",
                        reference_answer="B",
                        status=QuestionStatus.INCORRECT,
                        decision_source="model",
                        knowledge_points=("完整索引知识点",),
                        error_categories=("粗心",),
                        confidence=0.96,
                        reason="fixture",
                    ),
                ),
                summary="fixture",
            )
        )

    for index in range(105):
        save(Subject.MATH, index)
    for index in range(25):
        save(Subject.CHINESE, index)

    exporter.export_subject("数学")
    math_tree = KnowledgePaths(repo.config).ensure_subject_tree("数学")
    error_index = math_tree.mistakes / "错题索引.md"
    knowledge_page = exporter.knowledge_point_path("数学", "完整索引知识点")
    index_text = error_index.read_text("utf-8")

    assert index_text.count("第 1 页第 1 题") == 105
    assert "数学\\-000" in index_text and "数学\\-104" in index_text
    assert "语文-000" not in index_text
    assert (math_tree.analysis / "数学-000.md").exists()
    assert (math_tree.analysis / "数学-104.md").exists()
    assert knowledge_page.exists()
    assert quote("数学-104.md", safe="-._~") in knowledge_page.read_text("utf-8")


def test_knowledge_point_pages_use_hash_namespace_without_reserved_or_case_collisions(
    repo: KnowledgeRepository, exporter: MarkdownExporter
) -> None:
    points = ("科目总览", "Case", "case", "带 空格 # 知识点")
    analysis = AnalysisResult(
        document_id="collision-doc",
        subject=Subject.MATH,
        subject_confidence=0.96,
        document_type="作业",
        grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(),
        questions=tuple(
            QuestionAnalysis(
                question_id=str(index), question_type=QuestionType.OBJECTIVE, page=1,
                prompt_summary=point, student_answer="A", reference_answer="B",
                status=QuestionStatus.INCORRECT, decision_source="model",
                knowledge_points=(point,), error_categories=(), confidence=0.96, reason="fixture",
            )
            for index, point in enumerate(points, start=1)
        ),
        summary="fixture",
    )
    page = repo.config.knowledge_root / "数学" / "原始资料" / "collision.jpg"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"image")
    repo.create_document("collision-doc", "数学", "作业", ((1, str(page), "f" * 64),))
    KnowledgeUpdater(repo).apply(analysis)

    exporter.export_subject("数学")
    point_paths = [exporter.knowledge_point_path("数学", point) for point in points]
    index_path = KnowledgePaths(repo.config).safe_file_path("数学", "知识点", "科目总览.md")

    assert len({str(path).casefold() for path in point_paths}) == len(points)
    assert index_path not in point_paths
    assert all(path.name.startswith("知识点-") and path.exists() for path in point_paths)


def test_markdown_escapes_model_text_and_url_encodes_source_paths(
    repo: KnowledgeRepository, exporter: MarkdownExporter
) -> None:
    source = repo.config.knowledge_root / "数学" / "原始资料" / "page # 1.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"image")
    analysis = AnalysisResult(
        document_id="escape-doc",
        subject=Subject.MATH,
        subject_confidence=0.96,
        document_type="作业",
        grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=("<b>evidence</b> [run](javascript:alert(1))",),
        questions=(
            QuestionAnalysis(
                question_id="1", question_type=QuestionType.OBJECTIVE, page=1,
                prompt_summary="<script>bad()</script> [run](javascript:alert(1))",
                student_answer="[answer](javascript:alert(1))", reference_answer="<b>reference</b>",
                status=QuestionStatus.INCORRECT, decision_source="model",
                knowledge_points=("A [point]",), error_categories=("<bad>",), confidence=0.96,
                reason="<img src=x onerror=alert(1)>",
            ),
        ),
        summary="<script>alert(1)</script>",
    )
    repo.create_document("escape-doc", "数学", "作业", ((1, str(source), "a" * 64),))
    KnowledgeUpdater(repo).apply(analysis)

    text = exporter.export_document("escape-doc").read_text("utf-8")

    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "[run](javascript:" not in text
    assert "page%20%23%201.jpg" in text


def test_markdown_blocks_tilde_fences_and_normalizes_multiline_inline_labels(
    repo: KnowledgeRepository, exporter: MarkdownExporter
) -> None:
    page = repo.config.knowledge_root / "数学" / "原始资料" / "fence.jpg"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"image")
    analysis = AnalysisResult(
        document_id="fence-doc", subject=Subject.MATH, subject_confidence=0.96,
        document_type="作业", grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=("老师依据\n\n~~~\n不是代码",),
        questions=(
            QuestionAnalysis(
                question_id="1\n\n~~~", question_type=QuestionType.OBJECTIVE, page=1,
                prompt_summary="题干\n\n~~~\n后续文字", student_answer="A", reference_answer="B",
                status=QuestionStatus.INCORRECT, decision_source="model",
                knowledge_points=("知识点\n\n~~~\n标签",), error_categories=(), confidence=0.96, reason="fixture",
            ),
        ), summary="第一段\n\n~~~\n不能开启代码块\n\n第二段",
    )
    repo.create_document("fence-doc", "数学", "作业", ((1, str(page), "d" * 64),))
    KnowledgeUpdater(repo).apply(analysis)

    text = exporter.export_document("fence-doc").read_text("utf-8")

    assert "~~~" not in text
    assert "\\~\\~\\~" in text
    assert "- 老师依据 \\~\\~\\~ 不是代码" in text
    assert "[知识点 \\~\\~\\~ 标签]" in text
    assert "## 老师批改依据" in text and "## 题目分析" in text
