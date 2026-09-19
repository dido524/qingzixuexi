from pathlib import Path

from qingzi_learning.config import load_config


def test_default_config_uses_fixed_knowledge_root(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    cfg = load_config()
    assert cfg.knowledge_root == Path(r"C:\晴子知识库\5th grade")
    assert cfg.subjects == ("语文", "数学", "英语")
    assert (cfg.camera_vid, cfg.camera_pid) == (0xBC15, 0x2C1B)
    assert cfg.spool_root == tmp_path / "QingziLearningAssistant" / "spool"
    assert cfg.review_all_model_questions is True
