from __future__ import annotations

import re


_CITATION_PATTERN = re.compile(r"\[\[([^\[\]\r\n]+)\]\]")
_CODE_PATTERN = re.compile(r"```.*?```|`[^`\r\n]*`", re.DOTALL)


def extract_file_citations(answer: str) -> tuple[str, ...]:
    citations: list[str] = []
    prose = _CODE_PATTERN.sub("", answer)
    for match in _CITATION_PATTERN.finditer(prose):
        citation = match.group(1).replace("\\", "/")
        if citation not in citations:
            citations.append(citation)
    return tuple(citations)
