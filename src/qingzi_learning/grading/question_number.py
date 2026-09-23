"""Turn legacy model identifiers into worksheet-facing question numbers."""

from __future__ import annotations

import re


_CIRCLED = tuple("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")
_LEGACY_ID = re.compile(
    r"^(?:p(?P<page>\d+)[_-])?q(?P<major>\d+)(?:[_-](?P<sub>\d+))?(?P<letter>[a-z])?$",
    re.IGNORECASE,
)


def display_question_number(question_id: str) -> str:
    """Return a concise number that a parent can locate on the paper.

    Older analyses used storage-oriented IDs such as ``p1_q4_2`` and
    ``q2_1a``.  Keep those IDs untouched in storage, but translate their
    hierarchy at every visual boundary.
    """
    value = str(question_id).strip()
    if re.fullmatch(r"p\d+[_-]thinking", value, re.IGNORECASE):
        return "思考题"
    match = _LEGACY_ID.fullmatch(value)
    if match is None:
        return value
    number = str(int(match.group("major")))
    if match.group("sub") is not None:
        number += f"（{int(match.group('sub'))}）"
    if match.group("letter") is not None:
        position = ord(match.group("letter").lower()) - ord("a")
        number += _CIRCLED[position] if 0 <= position < len(_CIRCLED) else match.group("letter").lower()
    return number
