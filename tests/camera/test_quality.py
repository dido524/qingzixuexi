import numpy as np
import pytest
from PIL import Image

from qingzi_learning.camera.quality import check_image_quality


@pytest.fixture
def clear_frame() -> np.ndarray:
    frame = np.full((1200, 1600, 3), 200, dtype=np.uint8)
    frame[:, ::80] = 40
    frame[::60, :] = 40
    return frame


@pytest.fixture
def blurry_dark_frame() -> np.ndarray:
    return np.full((1200, 1600, 3), 20, dtype=np.uint8)


def test_blurry_dark_image_is_rejected(blurry_dark_frame: np.ndarray) -> None:
    """Removing either quality check would admit a page the model cannot read."""
    result = check_image_quality(blurry_dark_frame)

    assert not result.acceptable
    assert {"too_dark", "too_blurry"}.issubset(result.reasons)


def test_clear_large_image_is_accepted(clear_frame: np.ndarray) -> None:
    """Rejecting a legible page would prevent normal capture."""
    result = check_image_quality(clear_frame)

    assert result.acceptable
    assert result.reasons == ()


def test_small_image_is_rejected(clear_frame: np.ndarray) -> None:
    """Removing the minimum-edge check would permit insufficient OCR evidence."""
    result = check_image_quality(clear_frame[:1199, :1199])

    assert not result.acceptable
    assert "too_small" in result.reasons


def test_pillow_rgb_and_opencv_bgr_equivalents_have_same_quality_result() -> None:
    """Treating Pillow RGB as BGR changes quality metrics for colored documents."""
    rgb = np.zeros((1200, 1600, 3), dtype=np.uint8)
    rgb[:, :, 0] = 210
    rgb[:, :, 1] = 120
    rgb[:, :, 2] = 70
    rgb[:, ::80] = (20, 230, 40)
    rgb[::60, :] = (240, 30, 190)

    pillow_result = check_image_quality(Image.fromarray(rgb, mode="RGB"))
    opencv_result = check_image_quality(rgb[:, :, ::-1])

    assert pillow_result.acceptable == opencv_result.acceptable
    assert pillow_result.reasons == opencv_result.reasons
    assert pillow_result.grayscale_mean == pytest.approx(opencv_result.grayscale_mean)
    assert pillow_result.laplacian_variance == pytest.approx(opencv_result.laplacian_variance)
