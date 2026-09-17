from datetime import datetime, timedelta, timezone
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
from qingzi_learning.export.dashboard import DashboardExporter
from qingzi_learning.knowledge.updater import KnowledgeUpdater
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
def dashboard(repo: KnowledgeRepository) -> DashboardExporter:
    return DashboardExporter(repo)


def test_dashboard_is_self_contained_and_marks_future_actions(
    dashboard: DashboardExporter,
) -> None:
    path = dashboard.export()
    html = path.read_text("utf-8")

    assert path.name == "知识库首页.html"
    assert "晴子学习知识库" in html
    assert "错题本" in html and "知识点地图" in html
    assert "生成考前复习包（后续功能）" in html
    assert "发起掌握度检验（后续功能）" in html
    assert "http://" not in html and "https://" not in html
    assert "尚无数据" in html
    assert "掌握度 0.0%" not in html
    assert '<section id="知识点地图" class="panel">' in html
    assert '<section id="最近更新" class="panel">' in html
    assert not path.with_suffix(".html.part").exists()


def test_dashboard_embeds_escaped_snapshot_and_renders_weak_point(
    repo: KnowledgeRepository, dashboard: DashboardExporter
) -> None:
    analysis = AnalysisResult(
        document_id="doc-dashboard",
        subject=Subject.ENGLISH,
        subject_confidence=0.96,
        document_type="作业",
        grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(),
        questions=(
            QuestionAnalysis(
                question_id="1",
                question_type=QuestionType.OBJECTIVE,
                page=1,
                prompt_summary="一般现在时",
                student_answer="go",
                reference_answer="goes",
                status=QuestionStatus.INCORRECT,
                decision_source="model",
                knowledge_points=("一般现在时</script><script>bad()",),
                error_categories=("第三人称单数",),
                confidence=0.96,
                reason="缺少 s",
            ),
        ),
        summary="需要复习。",
    )
    repo.create_document(
        document_id=analysis.document_id,
        subject="英语",
        document_type="作业",
        pages=((1, "C:/spool/page_001.jpg", "a" * 64),),
    )
    KnowledgeUpdater(repo, now=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc)).apply(analysis)

    html = dashboard.export().read_text("utf-8")

    assert "一般现在时&lt;/script&gt;&lt;script&gt;bad()" in html
    assert "一般现在时\\u003c/script\\u003e" in html
    assert "样本 1" in html
    assert "0.0%" in html


def test_weakness_map_groups_points_by_subject_and_localizes_cumulative_trends(
    dashboard: DashboardExporter,
) -> None:
    points = [
        {
            "subject": "英语", "knowledge_point": "英语短板", "review_priority": 30,
            "mastery_rate": 0.5, "exposure_count": 4, "trend": "steady", "incorrect_count": 2,
        },
        {
            "subject": "语文", "knowledge_point": "语文短板", "review_priority": 50,
            "mastery_rate": 0.25, "exposure_count": 4, "trend": "declining", "incorrect_count": 3,
        },
        {
            "subject": "数学", "knowledge_point": "数学短板", "review_priority": 40,
            "mastery_rate": 0.75, "exposure_count": 4, "trend": "improving", "incorrect_count": 1,
        },
    ]

    rendered = dashboard._weak_points(points)

    chinese = rendered.index('data-subject="语文"')
    math = rendered.index('data-subject="数学"')
    english = rendered.index('data-subject="英语"')
    assert chinese < math < english
    assert "语文短板" in rendered[chinese:math]
    assert "数学短板" in rendered[math:english]
    assert "英语短板" in rendered[english:]
    assert "累计趋势 下降" in rendered
    assert "累计趋势 提升" in rendered
    assert "累计趋势 平稳" in rendered
    assert "declining" not in rendered
    assert "improving" not in rendered
    assert "steady" not in rendered


def test_weakness_map_keeps_all_subject_sections_when_only_one_has_data(
    dashboard: DashboardExporter,
) -> None:
    rendered = dashboard._weak_points(
        [{
            "subject": "数学", "knowledge_point": "分数除法", "review_priority": 50,
            "mastery_rate": 0.5, "exposure_count": 2, "trend": "declining", "incorrect_count": 1,
        }]
    )

    assert rendered.count('class="weak-subject"') == 3
    assert rendered.count("暂无重点短板。") == 2


def test_dashboard_shows_monthly_collection_and_recent_trend_with_honest_samples(
    repo: KnowledgeRepository, dashboard: DashboardExporter
) -> None:
    now = datetime.now(timezone.utc)
    updater = KnowledgeUpdater(repo, now=lambda: now)

    def save(document_id: str, created_at: datetime, status: QuestionStatus) -> None:
        analysis = AnalysisResult(
            document_id=document_id,
            subject=Subject.MATH,
            subject_confidence=0.96,
            document_type="试卷",
            grading_mode=GradingMode.AUTO_GRADE,
            teacher_mark_evidence=(),
            questions=(
                QuestionAnalysis(
                    question_id="1",
                    question_type=QuestionType.OBJECTIVE,
                    page=1,
                    prompt_summary="分数应用题",
                    student_answer="A",
                    reference_answer="B",
                    status=status,
                    decision_source="model",
                    knowledge_points=("分数应用",),
                    error_categories=("审题",),
                    confidence=0.96,
                    reason="fixture",
                ),
            ),
            summary="fixture",
        )
        page = repo.config.knowledge_root / "数学" / "原始资料" / f"{document_id}.jpg"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_bytes(b"image")
        repo.create_document(document_id, "数学", "试卷", ((1, str(page), "d" * 64),))
        updater.apply(analysis)
        repo.connection.execute(
            "UPDATE documents SET created_at = ?, updated_at = ? WHERE document_id = ?",
            (created_at.strftime("%Y-%m-%d %H:%M:%S"), created_at.strftime("%Y-%m-%d %H:%M:%S"), document_id),
        )

    save("math-old", now - timedelta(days=60), QuestionStatus.INCORRECT)
    save("math-new", now - timedelta(days=5), QuestionStatus.CORRECT)
    updater.recompute("数学", ("分数应用",))
    html = dashboard.export().read_text("utf-8")

    assert "本月收录资料" in html
    assert "最近90天" in html
    assert "提升（最近90天；资料 2，题目样本 2）" in html
    assert "累计至今" in html
    assert "样本 2" in html
    encoded_math = quote("数学", safe="-._~")
    encoded_points = quote("知识点", safe="-._~")
    encoded_analysis = quote("分析记录", safe="-._~")
    encoded_raw = quote("原始资料", safe="-._~")
    assert f'href="{encoded_math}/{encoded_points}/' in html
    assert f'href="{encoded_math}/{encoded_analysis}/math-new.md"' in html
    assert f'href="{encoded_math}/{encoded_raw}/math-old.jpg"' in html
    assert DashboardExporter(repo).markdown.knowledge_point_path("数学", "分数应用").exists()
    assert (repo.config.knowledge_root / "数学" / "分析记录" / "math-new.md").exists()


def test_dashboard_does_not_claim_a_recent_trend_from_one_confirmed_question(
    repo: KnowledgeRepository, dashboard: DashboardExporter
) -> None:
    analysis = AnalysisResult(
        document_id="english-one",
        subject=Subject.ENGLISH,
        subject_confidence=0.96,
        document_type="作业",
        grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(),
        questions=(
            QuestionAnalysis(
                question_id="1",
                question_type=QuestionType.OBJECTIVE,
                page=1,
                prompt_summary="时态",
                student_answer="go",
                reference_answer="goes",
                status=QuestionStatus.CORRECT,
                decision_source="model",
                knowledge_points=("一般现在时",),
                error_categories=(),
                confidence=0.96,
                reason="fixture",
            ),
        ),
        summary="fixture",
    )
    repo.create_document("english-one", "英语", "作业", ((1, "C:/spool/page_001.jpg", "e" * 64),))
    KnowledgeUpdater(repo).apply(analysis)

    html = dashboard.export().read_text("utf-8")

    assert "样本不足，暂不判断趋势（最近90天；资料 1，题目样本 1）" in html


def test_dashboard_weakness_card_uses_uncapped_aggregate(repo: KnowledgeRepository, dashboard: DashboardExporter) -> None:
    questions = tuple(
        QuestionAnalysis(
            question_id=str(index), question_type=QuestionType.OBJECTIVE, page=1,
            prompt_summary=f"题目{index}", student_answer="A", reference_answer="B",
            status=QuestionStatus.INCORRECT, decision_source="model",
            knowledge_points=(f"短板{index}",), error_categories=(), confidence=0.96, reason="fixture",
        )
        for index in range(1, 22)
    )
    analysis = AnalysisResult(
        document_id="many-weaknesses", subject=Subject.MATH, subject_confidence=0.96,
        document_type="作业", grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(), questions=questions, summary="fixture",
    )
    repo.create_document("many-weaknesses", "数学", "作业", ((1, "C:/spool/page.jpg", "b" * 64),))
    KnowledgeUpdater(repo).apply(analysis)

    snapshot = repo.dashboard_snapshot()
    html = dashboard.export().read_text("utf-8")

    assert len(snapshot["weak_knowledge_points"]) == 20
    assert snapshot["summary"]["weak_knowledge_point_count"] == 21
    assert '<div>重点短板</div><div class="number">21</div>' in html


def test_dashboard_url_encodes_special_character_source_filename(
    repo: KnowledgeRepository, dashboard: DashboardExporter
) -> None:
    source = repo.config.knowledge_root / "数学" / "原始资料" / "page # 1.jpg"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"image")
    analysis = AnalysisResult(
        document_id="special-source", subject=Subject.MATH, subject_confidence=0.96,
        document_type="作业", grading_mode=GradingMode.AUTO_GRADE, teacher_mark_evidence=(),
        questions=(
            QuestionAnalysis(
                question_id="1", question_type=QuestionType.OBJECTIVE, page=1,
                prompt_summary="特殊文件名", student_answer="A", reference_answer="B",
                status=QuestionStatus.INCORRECT, decision_source="model",
                knowledge_points=("特殊来源",), error_categories=(), confidence=0.96, reason="fixture",
            ),
        ), summary="fixture",
    )
    repo.create_document("special-source", "数学", "作业", ((1, str(source), "c" * 64),))
    KnowledgeUpdater(repo).apply(analysis)

    html = dashboard.export().read_text("utf-8")

    assert "page%20%23%201.jpg" in html
