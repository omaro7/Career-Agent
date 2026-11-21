"""Project-aware Word splitting for short, evidence-grounded local LLM calls.

Keeps Stage 02-6 evidence block IDs (B001, B002...) intact and does not infer
unknown career facts. A heading boundary is a heuristic, not a guarantee.
"""
from __future__ import annotations

import os
import re
from typing import Any

from word_ai_core import MAX_BLOCKS, MAX_TEXT

PROJECT_START = re.compile(
    r"^\s*(?:[\d]{1,3}[.)]\s*)?(?:프로젝트\s*명|수행\s*프로젝트|프로젝트\s*개요|사업\s*명|"
    r"프로젝트\s*[:：]|Project\s*(?:Name)?\s*[:：])\s*",
    flags=re.IGNORECASE,
)


def _split_project_sections(text: str) -> list[tuple[str, bool]]:
    """Preserve all characters, flag a new section before project-name headings."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    sections: list[tuple[str, bool]] = []
    buffer = ''
    new_section = False
    for line in lines:
        boundary = bool(PROJECT_START.match(line))
        if boundary and buffer.strip():
            sections.append((buffer, new_section))
            buffer = ''
            new_section = True
        elif boundary:
            new_section = True
        buffer += line
    if buffer:
        sections.append((buffer, new_section))
    return sections


def _split_long(text: str, limit: int) -> list[str]:
    """Try line ends before falling back to character-level splitting."""
    result: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining.rfind('\n', int(limit * 0.5), limit + 1)
        if cut < 0:
            cut = limit
        else:
            cut += 1
        result.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        result.append(remaining)
    return result


def semantic_split_blocks(blocks: list[dict[str, Any]],
                          chunk_chars: int | None = None) -> tuple[list[list[dict[str, str]]], dict[str, str]]:
    """Keep natural project sections together, hard-limit each LLM input chunk.

    A Word source block can be split across chunks while retaining its stable ID.
    The original full block remains the evidence-verification source.
    """
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_BLOCKS:
        raise ValueError('AI 분석은 원문 블록 1~300개를 지원합니다.')
    limit = chunk_chars if chunk_chars is not None else int(os.getenv('WORD_AI_CHUNK_CHARS', '1500'))
    if limit < 300 or limit > 4500:
        raise ValueError('WORD_AI_CHUNK_CHARS는 300~4500 범위여야 합니다.')
    max_chunks = int(os.getenv('WORD_AI_MAX_CHUNKS', '120'))
    if not 1 <= max_chunks <= 150:
        raise ValueError('WORD_AI_MAX_CHUNKS는 1~150 범위여야 합니다.')

    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    current_len = 0
    originals: dict[str, str] = {}
    total = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            chunks.append(current)
            current = []
            current_len = 0
            if len(chunks) > max_chunks:
                raise ValueError(f'AI 청크가 {max_chunks}개를 초과했습니다. 원문을 나누거나 CHUNK_CHARS를 조정하세요.')

    for idx, block in enumerate(blocks, 1):
        raw = str(block.get('text') or '').strip()
        if not raw:
            continue
        total += len(raw)
        if total > MAX_TEXT:
            raise ValueError('AI 분석 가능한 원문은 최대 15만 자입니다.')
        source_id = f'B{idx:03d}'
        originals[source_id] = raw
        kind = str(block.get('kind') or 'paragraph')
        segments = _split_project_sections(raw)
        for segment_text, is_project in segments:
            if (kind == 'heading' or is_project) and current:
                flush()
            for part_index, fragment in enumerate(_split_long(segment_text, limit)):
                if current and (current_len + len(fragment) > limit or part_index > 0):
                    flush()
                current.append({'id': source_id, 'kind': kind, 'text': fragment})
                current_len += len(fragment)
    flush()
    if not chunks:
        raise ValueError('AI 분석에 사용할 Word 원문이 없습니다.')
    return chunks, originals
