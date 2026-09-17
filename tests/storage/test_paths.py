from pathlib import Path

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.storage.paths import KnowledgePaths


@pytest.fixture
def paths(tmp_path: Path) -> KnowledgePaths:
    root = tmp_path / "knowledge"
    for subject in ("语文", "数学", "英语"):
        (root / subject).mkdir(parents=True)
    config = AppConfig(
        knowledge_root=root,
        subjects=("语文", "数学", "英语"),
        camera_vid=1,
        camera_pid=2,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "app-data",
    )
    return KnowledgePaths(config)


def test_subject_tree_rejects_path_traversal(paths: KnowledgePaths) -> None:
    """Removing the whitelist check would let a caller escape the knowledge root."""
    with pytest.raises(ValueError, match="非法科目"):
        paths.ensure_subject_tree(r"..\其他")


def test_subject_tree_is_under_existing_subject_folder(paths: KnowledgePaths) -> None:
    """A wrong base directory would place captured evidence outside its subject."""
    tree = paths.ensure_subject_tree("数学")

    assert tree.raw.parent.name == "数学"
    assert tree.raw.name == "原始资料"
    assert tree.analysis.name == "分析记录"
    assert tree.pending.name == "待处理"


def test_safe_file_path_rejects_path_components(paths: KnowledgePaths) -> None:
    """Dropping filename validation would permit an archive write outside its document folder."""
    with pytest.raises(ValueError, match="非法文件名"):
        paths.safe_file_path("英语", "原始资料", "../outside.jpg")


def test_safe_file_path_accepts_document_image_name(paths: KnowledgePaths) -> None:
    path = paths.safe_file_path("英语", "原始资料", "page_001.jpg")

    assert path.parent == paths.ensure_subject_tree("英语").raw
    assert path.name == "page_001.jpg"
