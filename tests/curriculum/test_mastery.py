from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from qingzi_learning.curriculum.graph import load_math_graph
from qingzi_learning.curriculum.mastery import (
    build_mastery_projection,
    load_course_mappings,
)


NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def point(
    label,
    *,
    exposure_count=1,
    correct_count=0,
    partial_count=0,
    incorrect_count=1,
    last_seen_at="2026-09-20 12:00:00",
    trend="no_data",
):
    return {
        "subject": "数学",
        "knowledge_point": label,
        "exposure_count": exposure_count,
        "correct_count": correct_count,
        "partial_count": partial_count,
        "incorrect_count": incorrect_count,
        "needs_review": 0,
        "review_priority": 0,
        "trend": trend,
        "last_seen_at": last_seen_at,
        "mastery_rate": None,
    }


def math_snapshot(points):
    return {
        "subject": {},
        "period": {},
        "documents": [],
        "knowledge_points": points,
        "error_bank": [],
        "pending_questions": [],
    }


def test_only_exact_confirmed_aliases_contribute():
    snapshot = math_snapshot(
        [
            point(
                "小数乘法",
                exposure_count=6,
                correct_count=4,
                partial_count=1,
                incorrect_count=1,
            ),
            point(
                "小数乘法易错",
                exposure_count=9,
                correct_count=0,
                partial_count=0,
                incorrect_count=9,
            ),
        ]
    )
    projection = build_mastery_projection(load_math_graph(), snapshot, now=NOW)
    assert projection.by_concept["number-decimal-multiply"].evidence_count == 6
    assert projection.audit_by_label["小数乘法易错"].status == "unmapped"


@pytest.mark.parametrize(
    "label", ["情态动词can", "like doing", "would like to do", "一般将来时"]
)
def test_english_grammar_under_math_is_quarantined(label):
    projection = build_mastery_projection(
        load_math_graph(), math_snapshot([point(label)]), now=NOW
    )
    assert projection.audit_by_label[label].status == "cross_subject"
    assert sum(item.evidence_count for item in projection.by_concept.values()) == 0


def test_two_correct_answers_are_not_called_mastered():
    projection = build_mastery_projection(
        load_math_graph(),
        math_snapshot(
            [
                point(
                    "小数乘法",
                    exposure_count=2,
                    correct_count=2,
                    partial_count=0,
                    incorrect_count=0,
                )
            ]
        ),
        now=NOW,
    )
    assert projection.by_concept["number-decimal-multiply"].state == "evidence_insufficient"
    assert projection.by_concept["number-decimal-multiply"].weighted_rate == 1.0


def test_weighted_rate_and_states_use_confirmed_aggregate_counts():
    projection = build_mastery_projection(
        load_math_graph(),
        math_snapshot(
            [
                point("小数乘法", exposure_count=10, correct_count=8, partial_count=1, incorrect_count=1),
                point("小数加减法", exposure_count=10, correct_count=6, partial_count=2, incorrect_count=2),
                point("可能性", exposure_count=10, correct_count=4, partial_count=2, incorrect_count=4),
            ]
        ),
        now=NOW,
    )
    assert projection.by_concept["number-decimal-multiply"].state == "stable"
    assert projection.by_concept["number-decimal-add-subtract"].state == "basic"
    assert projection.by_concept["statistics-possibility"].state == "unstable"


def test_current_book_mappings_are_explicit_and_validated():
    mappings = load_course_mappings()
    assert len(mappings) == 12
    assert {mapping.status for mapping in mappings} == {"confirmed", "pending"}
    assert all(mapping.source_type == "textbook" for mapping in mappings)
    assert all(mapping.provider == "北京师范大学出版社" for mapping in mappings)
    assert all(mapping.grades == ("5",) and mapping.terms == ("upper",) for mapping in mappings)
    assert any(
        mapping.source_id.endswith("u03")
        and mapping.target_ids == ("number-decimal-multiply",)
        and mapping.status == "confirmed"
        for mapping in mappings
    )


def test_repository_owned_cumulative_trend_requires_five_confirmed_samples():
    projection = build_mastery_projection(
        load_math_graph(),
        math_snapshot([point("小数乘法", exposure_count=7, trend="declining")]),
        now=NOW,
    )
    mastery = projection.by_concept["number-decimal-multiply"]
    assert mastery.recent_count == 0
    assert mastery.trend == "declining"

    too_small = build_mastery_projection(
        load_math_graph(),
        math_snapshot([point("小数乘法", exposure_count=1, trend="declining")]),
        now=NOW,
    )
    assert too_small.by_concept["number-decimal-multiply"].trend == "unknown"


def test_mapping_directory_loads_multiple_provider_neutral_catalogs(tmp_path):
    source = (
        Path(__file__).parents[2]
        / "src" / "qingzi_learning" / "curriculum" / "mappings"
        / "bnu_math_g5_upper_2024.json"
    )
    first = json.loads(source.read_text("utf-8"))
    second = json.loads(source.read_text("utf-8"))
    second["catalog_id"] = "calculation-training-g5-upper"
    second["source_context"] = {
        "provider": "计算训练示例",
        "publisher": "",
        "edition": "2026",
        "grades": ["5"],
        "terms": ["upper"],
    }
    for mapping in second["mappings"]:
        mapping["source_id"] = "training-" + mapping["source_id"]
        mapping["source_type"] = "calculation_training"
        mapping["relation"] = "trains"
    (tmp_path / "book.json").write_text(json.dumps(first, ensure_ascii=False), "utf-8")
    (tmp_path / "training.json").write_text(json.dumps(second, ensure_ascii=False), "utf-8")

    mappings = load_course_mappings(tmp_path)

    assert len(mappings) == 24
    assert {mapping.provider for mapping in mappings} == {"北京师范大学出版社", "计算训练示例"}
