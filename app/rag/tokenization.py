from __future__ import annotations

import re


def tokenize(text: str) -> list[str]:
    """Tokenize Chinese and English text for the local lexical index."""
    normalized = text.lower()
    english = re.findall(r"[a-z0-9_]+(?:-[a-z0-9_]+)*", normalized)
    chinese = re.findall(r"[\u4e00-\u9fff]", normalized)
    chinese_bigrams = [
        "".join(chinese[index : index + 2])
        for index in range(max(0, len(chinese) - 1))
    ]
    return english + chinese + chinese_bigrams


def to_fts_query(text: str) -> str:
    """Build a safe OR query for SQLite FTS5 from normalized tokens."""
    tokens = list(dict.fromkeys(tokenize(text)))
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
