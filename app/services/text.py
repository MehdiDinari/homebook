from __future__ import annotations

import re


_HASHTAG_RE = re.compile(r"(?<!\w)#([A-Za-z0-9_]{2,40})")
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9._-]{0,31})")


def extract_hashtags(text: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _HASHTAG_RE.finditer(text)})


def extract_mentions(text: str) -> list[str]:
    out = sorted({m.group(1).lower() for m in _MENTION_RE.finditer(text)})
    return out[:20]
