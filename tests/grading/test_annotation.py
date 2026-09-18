from hashlib import sha256

from PIL import Image

from qingzi_learning.domain import (
    AnalysisResult, CapturedDocument, CapturedPage, GradingMode,
    QuestionAnalysis, QuestionStatus, QuestionType, Subject,
)
from qingzi_learning.grading.annotation import AnnotationRenderer


def test_renders_two_page_gallery_without_changing_originals(tmp_path):
    pages = []
    before = {}
    for number in (1, 2):
        path = tmp_path / f"page_{number:03d}.jpg"
        Image.new("RGB", (900, 1200), "white").save(path)
        digest = sha256(path.read_bytes()).hexdigest()
        before[path] = digest
        pages.append(CapturedPage(number, path, digest))
    document = CapturedDocument("doc", tuple(pages))
    questions = tuple(
        QuestionAnalysis(str(number), QuestionType.CALCULATION, number, "1+1", "3", "2",
                         QuestionStatus.INCORRECT, "model", ("加法",), ("计算错误",), .98,
                         "答案应为2", answer_bbox=(.25, .35, .30, .08))
        for number in (1, 2)
    )
    analysis = AnalysisResult("doc", Subject.MATH, .99, "作业", GradingMode.AUTO_GRADE,
                              (), questions, "两题需要确认")

    result = AnnotationRenderer(tmp_path).render(document, analysis)

    assert len(result.pages) == 2
    assert all(path.exists() and path.suffix == ".png" for path in result.pages)
    assert result.gallery.exists()
    html = result.gallery.read_text(encoding="utf-8")
    assert "第 1 页" in html and "第 2 页" in html and "window.print" in html
    assert all(sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())
    with Image.open(result.pages[0]) as rendered:
        assert rendered.convert("RGB").getpixel((225, 420)) != (255, 255, 255)
