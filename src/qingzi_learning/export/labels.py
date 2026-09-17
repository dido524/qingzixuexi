"""Chinese presentation labels for persisted enum-like values."""

from __future__ import annotations


_TREND_LABELS = {
    "declining": "下降",
    "improving": "提升",
    "steady": "平稳",
    "no_data": "暂无数据",
    "insufficient_data": "样本不足",
}


def trend_label(value: object) -> str:
    """Return a user-facing Chinese trend without changing stored facts."""
    text = str(value)
    return _TREND_LABELS.get(text, text)
