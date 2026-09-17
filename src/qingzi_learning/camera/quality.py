"""Local image checks that keep unreadable pages out of the durable spool."""

from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image


MIN_EDGE_PIXELS = 1200
MIN_GRAYSCALE_MEAN = 45.0
MAX_GRAYSCALE_MEAN = 235.0
MIN_LAPLACIAN_VARIANCE = 80.0


@dataclass(frozen=True)
class ImageQualityResult:
    """The observable measurements used to accept or reject one camera frame."""

    acceptable: bool
    reasons: tuple[str, ...]
    minimum_edge: int
    grayscale_mean: float
    laplacian_variance: float


class ImageQualityRejected(ValueError):
    """Raised when a frame must not replace or create a captured page."""

    def __init__(self, result: ImageQualityResult) -> None:
        self.result = result
        super().__init__("图片质量不合格：" + ", ".join(result.reasons))


def check_image_quality(frame: np.ndarray | Image.Image) -> ImageQualityResult:
    """Evaluate a Pillow image or OpenCV-style array without modifying it."""
    is_rgb = isinstance(frame, Image.Image)
    image = _as_array(frame)
    height, width = image.shape[:2]
    grayscale = _to_grayscale(image, is_rgb=is_rgb)
    mean = float(grayscale.mean())
    laplacian_variance = float(cv2.Laplacian(grayscale, cv2.CV_64F).var())

    reasons: list[str] = []
    if min(height, width) < MIN_EDGE_PIXELS:
        reasons.append("too_small")
    if mean < MIN_GRAYSCALE_MEAN:
        reasons.append("too_dark")
    if mean > MAX_GRAYSCALE_MEAN:
        reasons.append("too_bright")
    if laplacian_variance < MIN_LAPLACIAN_VARIANCE:
        reasons.append("too_blurry")
    return ImageQualityResult(
        acceptable=not reasons,
        reasons=tuple(reasons),
        minimum_edge=min(height, width),
        grayscale_mean=mean,
        laplacian_variance=laplacian_variance,
    )


def _as_array(frame: np.ndarray | Image.Image) -> np.ndarray:
    image = np.asarray(frame.convert("RGB") if isinstance(frame, Image.Image) else frame)
    if image.ndim not in (2, 3) or not image.size:
        raise ValueError("无效图片帧")
    if image.ndim == 3 and image.shape[2] not in (1, 3, 4):
        raise ValueError("无效图片帧")
    return image


def _to_grayscale(image: np.ndarray, *, is_rgb: bool) -> np.ndarray:
    if image.ndim == 2:
        return image
    if image.shape[2] == 1:
        return image[:, :, 0]
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    conversion = cv2.COLOR_RGB2GRAY if is_rgb else cv2.COLOR_BGR2GRAY
    return cv2.cvtColor(image, conversion)
