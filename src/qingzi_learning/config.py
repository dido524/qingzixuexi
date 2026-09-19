from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class AppConfig:
    knowledge_root: Path
    subjects: tuple[str, ...]
    camera_vid: int
    camera_pid: int
    spool_root: Path
    app_data_root: Path
    subject_confidence_threshold: float = 0.85
    review_all_model_questions: bool = False


def load_config() -> AppConfig:
    local = Path(os.environ["LOCALAPPDATA"]) / "QingziLearningAssistant"
    return AppConfig(
        knowledge_root=Path(r"C:\晴子知识库\5th grade"),
        subjects=("语文", "数学", "英语"),
        camera_vid=0xBC15,
        camera_pid=0x2C1B,
        spool_root=local / "spool",
        app_data_root=local,
        review_all_model_questions=True,
    )
