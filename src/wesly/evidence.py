from __future__ import annotations

import re


_CITATION_PATTERN = re.compile(r"\[\[([^\[\]\r\n]+)\]\]")


def extract_file_citations(answer: str) -> tuple[str, ...]:
    citations: list[str] = []
    for match in _CITATION_PATTERN.finditer(answer):
        citation = match.group(1).replace("\\", "/")
        if citation not in citations:
            citations.append(citation)
    return tuple(citations)
