from copy import deepcopy
import json

import pytest

from qingzi_learning.curriculum.catalog import load_catalog, load_catalogs, validate_catalog


def test_photographed_book_sequence_is_preserved():
    catalog = load_catalog()
    assert catalog["catalog_id"] == "bnu-math-g5-upper-2024-review"
    assert [(node["title"], node["page"]) for node in catalog["nodes"]] == [
        ("小数的再认识和加减法", 2), ("三角形的再认识", 21),
        ("小小设计师", 31), ("小数乘法", 35),
        ("用字母表示（一）", 50), ("多边形的面积", 60),
        ("鸡兔同笼", 80), ("图形的位置与运动（一）", 82),
        ("倍数与因数", 89), ("可能性", 102),
        ("多少落叶能铺满", 109), ("总复习", 113),
    ]
    assert catalog["track"] == "school"
    assert catalog["grade"] == "5"


@pytest.mark.parametrize("mutate", [
    lambda c: c["nodes"].append(deepcopy(c["nodes"][0])),
    lambda c: c["edges"].append({"from": "u01", "to": "missing", "type": "application", "basis": "suggestion"}),
    lambda c: c.update(catalog_id="../escape"),
    lambda c: c["nodes"][0].update(page=-1),
])
def test_invalid_catalog_cannot_be_published(mutate):
    catalog = deepcopy(load_catalog())
    mutate(catalog)
    with pytest.raises(ValueError):
        validate_catalog(catalog)


def test_other_grade_or_enrichment_catalog_is_independent():
    catalog = deepcopy(load_catalog())
    catalog.update(catalog_id="club-math-g6-upper-v1", track="enrichment", grade="6")
    assert validate_catalog(catalog)["catalog_id"] == "club-math-g6-upper-v1"


def test_catalog_directory_discovers_new_tracks_and_rejects_duplicate_ids(tmp_path):
    school = load_catalog()
    club = deepcopy(school)
    club.update(catalog_id="club-math-g6-upper-v1", track="enrichment", grade="6")
    (tmp_path / "school.json").write_text(json.dumps(school, ensure_ascii=False), "utf-8")
    (tmp_path / "club.json").write_text(json.dumps(club, ensure_ascii=False), "utf-8")
    assert {item["catalog_id"] for item in load_catalogs(tmp_path)} == {
        "bnu-math-g5-upper-2024-review", "club-math-g6-upper-v1",
    }
    club["catalog_id"] = school["catalog_id"]
    (tmp_path / "club.json").write_text(json.dumps(club, ensure_ascii=False), "utf-8")
    with pytest.raises(ValueError, match="重复"):
        load_catalogs(tmp_path)
