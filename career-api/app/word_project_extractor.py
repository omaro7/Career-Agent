"""Stage 02-7: deterministic, evidence-backed project extraction from reviewed DOCX blocks.

Works with Stage 02-5 `career_word_import.reviewed` JSON (no LLM call).
No DB writes. Missing/ambiguous data stays blank for operator review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_CANONICAL = {
    'projectName': ('프로젝트명', '프로젝트명담당업무명', '프로젝트', '사업명', '사업프로젝트명',
                    '수행사업명', '과제명', '업무명', '프로젝트명및업무명', '프로젝트명담당업무'),
    'periodText': ('참여기간', '수행기간', '수행일자', '프로젝트기간', '근무기간', '사업기간',
                   '기간', '투입기간', '수행년도'),
    'clientName': ('고객사', '발주처', '발주기관', '발주회사', '고객기관', '고객명', '발주사'),
    'employerName': ('근무회사', '소속회사', '수행사', '수행회사', '참여회사', '소속사', '업체명'),
    'submittedRole': ('역할', '담당역할', '수행역할', '담당직무', '직무', '포지션', '수행포지션'),
    'workDescription': ('수행업무', '담당업무', '주요업무', '상세업무', '주요수행업무',
                        '담당업무내용', '업무내용', '담당내용', '주요수행내용'),
    'technologiesText': ('언어', '개발언어', '사용언어', '프로그래밍언어', '기술스택',
                         '사용기술', '개발기술', '개발환경언어', '프레임워크', 'framework'),
    'dbmsText': ('dbms', 'db', '데이터베이스', 'database'),
    'toolsText': ('tool', 'tools', '개발도구', '사용도구', '도구'),
    'osText': ('os', '운영체제'),
    'industryText': ('산업', '산업분야'),
    'applicationText': ('응용', '응용분야'),
    'hardwareText': ('기종', '하드웨어'),
    'communicationText': ('통신', '통신기술'),
    'otherText': ('기타', '비고'),
}


def _norm(value: str) -> str:
    return re.sub(r'[^0-9a-z가-힣]', '', value.casefold())


_ALIASES = {_norm(alias): key for key, aliases in _CANONICAL.items() for alias in aliases}
_PREFIX_ALIASES = sorted(_ALIASES, key=len, reverse=True)
_DATE = re.compile(r'(?<!\d)(\d{4})\s*[./년-]\s*(\d{1,2})(?:\s*[./월-]\s*\d{1,2}\s*일?)?(?!\d)')
_RANGE = re.compile(r'(?:~|〜|–|—|－|부터|to|until)', re.I)
_PROJECT_SECTION = re.compile(r'(?:프로젝트|수행사업|주요사업|경력사항|업무경력|skill\s*inventory)', re.I)
_IGNORED = {'', '-', '—', '없음', '해당없음', 'n/a'}


def _header_key(cell: str) -> str | None:
    normalized = _norm(cell)
    direct = _ALIASES.get(normalized)
    if direct:
        return direct
    # Original table headers often contain a parenthetical note: '프로젝트명 / (담당업무명)'.
    if normalized.startswith('프로젝트명') and ('담당업무' in normalized or len(normalized) < 21):
        return 'projectName'
    return None


def _cells(text: str) -> list[str]:
    return [c.strip() for c in text.split('|')]


def _parse_period(value: str) -> tuple[str, str]:
    found = _DATE.findall(value)
    if not found:
        return '', ''
    first = found[0]
    if not 1 <= int(first[1]) <= 12:
        return '', ''
    start = f'{first[0]}-{int(first[1]):02d}-01'
    if len(found) < 2:
        return start, ''
    last = found[1]
    if not 1 <= int(last[1]) <= 12:
        return start, ''
    return start, f'{last[0]}-{int(last[1]):02d}-01'


def _blank() -> dict[str, Any]:
    return {**{key: '' for key in _CANONICAL}, 'startDate': '', 'endDate': '',
            'include': True, 'sourceBlockIds': [], 'extractionMethod': 'RULE', 'confidence': 0.0}


# STAGE02_7_TECH_MAPPING
# Normalize known technology spelling only. Preserve every original source cell.
_TECH_CANONICAL = {
    'springboot': 'Spring Boot', 'spring boot': 'Spring Boot',
    'spring-boot': 'Spring Boot', 'spring': 'Spring',
    'react': 'React', 'reactjs': 'React', 'react.js': 'React',
    'typescript': 'TypeScript', 'ts': 'TypeScript',
    'javascript': 'JavaScript', 'js': 'JavaScript',
    'vue': 'Vue', 'vuejs': 'Vue', 'vue.js': 'Vue',
    'java': 'Java', 'jsp': 'JSP',
    'thymeleaf': 'Thymeleaf',
    'mybatis': 'MyBatis',
    'node.js': 'Node.js', 'nodejs': 'Node.js',
    'python': 'Python', 'kotlin': 'Kotlin', 'c#': 'C#', 'c++': 'C++',
}


def normalize_technologies(value: str) -> str:
    """Split known compound names like react+typescript; do not split C++/unknown names.

    A display normalization, NOT an assertion that a technology is a programming
    language. The original string remains available as `technologiesRaw`.
    """
    normalized: list[str] = []
    seen: set[str] = set()
    for part in re.split(r'[,，;；\n]+', str(value or '')):
        part = re.sub(r'\s+', ' ', part).strip()
        if not part:
            continue
        # Word line-break flattening may create 'Java / Spring Boot'. Split only
        # when every component is known; preserve unknown slash-bearing names.
        for delimiter in ('+', '/'):
            maybe = [p.strip() for p in part.split(delimiter)]
            if len(maybe) > 1 and all(p.casefold() in _TECH_CANONICAL for p in maybe):
                parts = maybe
                break
        else:
            parts = [part]
        for item in parts:
            display = _TECH_CANONICAL.get(item.casefold(), item)
            if display.casefold() not in seen:
                normalized.append(display)
                seen.add(display.casefold())
    return ', '.join(normalized)[:500]


def _fill(project: dict, key: str, value: str) -> None:
    clean = re.sub(r'\s+', ' ', value or '').strip()
    if _norm(clean) in _IGNORED or not clean:
        return
    # Do not infer details; preserve source wording, only compact whitespace.
    if key == 'technologiesText':
        project['technologiesRaw'] = clean[:500]
        project[key] = normalize_technologies(clean)
    else:
        project[key] = clean[:500]
    if key == 'periodText':
        project['startDate'], project['endDate'] = _parse_period(clean)


def _confidence(project: dict) -> float:
    # A transparent rule coverage score, not a statistical likelihood.
    return round(min(1.0, 0.55 + (0.15 if project['startDate'] else 0)
                     + (0.12 if project['endDate'] else 0)
                     + (0.10 if project['clientName'] else 0)
                     + (0.08 if project['submittedRole'] else 0)), 2)


def _header_fields(cells: list[str]) -> list[str | None] | None:
    mapping = [_header_key(cell) for cell in cells]
    recognized = {field for field in mapping if field}
    if 'projectName' in recognized and len(recognized) >= 3 and (
        'periodText' in recognized or 'clientName' in recognized
    ):
        return mapping
    return None


def _merge_header(prior: list[str | None] | None, new: list[str | None]) -> list[str | None]:
    if prior is None or len(prior) != len(new):
        return new
    # Subsequent merged header rows provide the detail under '개발환경'.
    return [later or earlier for earlier, later in zip(prior, new)]


def _add(result: list[dict], seen: dict[tuple, int], project: dict) -> None:
    if not project.get('projectName'):
        return
    project['confidence'] = _confidence(project)
    key = (_norm(project['projectName']), project['startDate'], project['endDate'],
           _norm(project['clientName']))
    if key in seen:
        existing = result[seen[key]]
        for evidence in project['sourceBlockIds']:
            if evidence not in existing['sourceBlockIds']:
                existing['sourceBlockIds'].append(evidence)
        # When two entries genuinely duplicate each other, preserve the first record.
        return
    seen[key] = len(result)
    result.append(project)


def _paragraph_pair(text: str) -> tuple[str, str] | None:
    matched = re.match(r'^\s*([^:：|]{1,30})\s*[:：|]\s*(.+?)\s*$', text, re.S)
    if matched and _header_key(matched.group(1)):
        return matched.group(1), matched.group(2)
    return None


def extract_projects(reviewed: dict[str, Any]) -> dict[str, Any]:
    """Return project proposals and B1-based sources; never alter `reviewed`.

    `reviewed` is the current Stage 02-5 JSON: {'title': ..., 'blocks': [
        {'kind': 'table'|'paragraph'|'heading', 'text': ...}, ...]}
    """
    blocks = reviewed.get('blocks', [])
    if not isinstance(blocks, list) or len(blocks) > 300:
        raise ValueError('Word 원문 블록은 최대 300개까지 지원합니다.')
    result: list[dict] = []
    seen: dict[tuple, int] = {}
    header: list[str | None] | None = None
    current: dict | None = None
    in_project_section = False
    header_count = 0
    parsed_table_rows = 0
    parsed_paragraph_rows = 0
    ignored_rows = 0
    warnings: list[str] = []

    def flush() -> None:
        nonlocal current
        if current and current.get('projectName'):
            _add(result, seen, current)
        current = None

    for index, block in enumerate(blocks, 1):
        if not isinstance(block, dict):
            ignored_rows += 1
            continue
        kind = str(block.get('kind', 'paragraph'))
        text = str(block.get('text', '')).strip()
        if not text:
            continue
        source_id = f'B{index}'
        if kind == 'heading':
            flush()
            header = None
            in_project_section = bool(_PROJECT_SECTION.search(text))
            continue
        if kind == 'table':
            cells = _cells(text)
            detected = _header_fields(cells)
            if detected:
                flush()
                header = _merge_header(header, detected)
                in_project_section = True
                header_count += 1
                continue
            if header and len(cells) == len(header):
                project = _blank()
                for key, value in zip(header, cells):
                    if key:
                        _fill(project, key, value)
                if project['projectName'] and _norm(project['projectName']) not in ('프로젝트명', '사업명'):
                    project['sourceBlockIds'].append(source_id)
                    _add(result, seen, project)
                    parsed_table_rows += 1
                    continue
            # A table can contain non-project rows before or after the inventory.
            if header and len(cells) != len(header):
                header = None
        else:
            # Never project a table header onto later free text.
            header = None

        pair = _paragraph_pair(text)
        if pair:
            key = _header_key(pair[0])
            assert key is not None
            if key == 'projectName':
                flush()
                current = _blank()
                _fill(current, key, pair[1])
                current['sourceBlockIds'].append(source_id)
                in_project_section = True
                parsed_paragraph_rows += 1
            elif current is not None:
                _fill(current, key, pair[1])
                if source_id not in current['sourceBlockIds']:
                    current['sourceBlockIds'].append(source_id)
            continue
        # Conservative support for numbered/unlabelled titles inside an explicit section:
        # "1. 프로젝트명 (2024.01 ~ 2024.12)". Date alone is insufficient.
        if in_project_section and kind in ('paragraph', 'table') and not header:
            match = re.match(r'^\s*(?:\d+[.)]\s*|[•●■]\s*)(.{5,140}?)\s*\((\d{4}[./-]\d{1,2}\s*~\s*\d{4}[./-]\d{1,2})\)\s*$', text)
            if match:
                flush()
                current = _blank()
                _fill(current, 'projectName', match.group(1))
                _fill(current, 'periodText', match.group(2))
                current['sourceBlockIds'].append(source_id)
                parsed_paragraph_rows += 1
    flush()
    if not result:
        warnings.append('프로젝트 헤더나 명시적인 프로젝트 항목을 찾지 못했습니다. 원문 검토가 필요합니다.')
    if any(p['startDate'] and not p['endDate'] for p in result):
        warnings.append('일부 프로젝트의 종료일이 명시되지 않았습니다. 원문 참여기간을 확인하세요.')
    if any(not p['periodText'] for p in result):
        warnings.append('기간이 없는 프로젝트는 날짜를 추측하지 않았습니다.')
    if len(result) > 100:
        result = result[:100]
        warnings.append('프로젝트를 최대 100건까지만 제안했습니다.')
    return {'projects': result, 'warnings': warnings,
            'diagnostics': {'blockCount': len(blocks), 'projectCount': len(result),
                            'headerRows': header_count, 'tableRows': parsed_table_rows,
                            'paragraphProjects': parsed_paragraph_rows,
                            'ignoredRows': ignored_rows,
                            'engine': 'RULE_V1', 'llmCalled': False}}
