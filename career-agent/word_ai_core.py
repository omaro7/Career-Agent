"""Pure validation and merging for evidence-grounded Word career extraction.

No model or database dependency here. The model does not get to invent block IDs;
values without evidence in the supplied Word text are rejected.
"""
from __future__ import annotations

import json
import re
from datetime import date
import unicodedata
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

FIELDS = (
    'name', 'affiliation', 'positionTitle', 'birthDate', 'ageYears',
    'educationText', 'degree', 'certificationsText', 'displayGrade',
    'businessRole', 'assignmentPeriod', 'assignmentMonths', 'allocationRate',
    'customerName', 'skillsText', 'careerSummary',
)
PROJECT_FIELDS = (
    'projectName', 'startDate', 'endDate', 'periodText',
    'submittedRole', 'clientName', 'workDescription',
)
ALL_FIELDS = frozenset(FIELDS + PROJECT_FIELDS)
MAX_BLOCKS = 300
MAX_TEXT = 150_000
CHUNK_CHARS = 4500
MAX_CHUNKS = 40


class EvidenceValue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: str = Field(default='', max_length=500)
    sourceBlockIds: list[str] = Field(default_factory=list, max_length=8)


class ExtractedProject(BaseModel):
    model_config = ConfigDict(extra='forbid')
    projectName: EvidenceValue = Field(default_factory=EvidenceValue)
    startDate: EvidenceValue = Field(default_factory=EvidenceValue)
    endDate: EvidenceValue = Field(default_factory=EvidenceValue)
    periodText: EvidenceValue = Field(default_factory=EvidenceValue)
    submittedRole: EvidenceValue = Field(default_factory=EvidenceValue)
    clientName: EvidenceValue = Field(default_factory=EvidenceValue)
    workDescription: EvidenceValue = Field(default_factory=EvidenceValue)


class ChunkResult(BaseModel):
    model_config = ConfigDict(extra='forbid')
    fields: dict[str, EvidenceValue] = Field(default_factory=dict)
    projects: list[ExtractedProject] = Field(default_factory=list, max_length=40)
    warnings: list[str] = Field(default_factory=list, max_length=12)


def _normal(s: str) -> str:
    return ''.join(ch for ch in unicodedata.normalize('NFKC', s).casefold()
                   if ch.isalnum())


def supported(value: str, originals: Iterable[str]) -> bool:
    """Conservative evidence gate. No generative paraphrases are accepted as facts."""
    combined = ' '.join(originals)
    val = _normal(value)
    doc = _normal(combined)
    if not val or not doc:
        return False
    if val in doc:
        return True
    # Date formatting normalization: 2024.01 -> 2024-01. Do not invent days.
    if re.fullmatch(r'\d{4}[-/.]\d{1,2}(?:[-/.]\d{1,2})?', value.strip()):
        digits = ''.join(x for x in value if x.isdigit())
        return len(digits) >= 6 and digits in ''.join(x for x in combined if x.isdigit())
    # An abbreviation explicitly given in parentheses is supported by the first case.
    return False


def split_blocks(blocks: list[dict[str, Any]]) -> tuple[list[list[dict[str, str]]], dict[str, str]]:
    if not isinstance(blocks, list) or not 1 <= len(blocks) <= MAX_BLOCKS:
        raise ValueError('AI 분석은 원문 블록 1~300개를 지원합니다.')
    chunks: list[list[dict[str, str]]] = []
    chunk: list[dict[str, str]] = []
    current = 0
    originals: dict[str, str] = {}
    total = 0
    for idx, entry in enumerate(blocks, 1):
        text = str(entry.get('text') or '').strip()
        if not text:
            continue
        block_id = f'B{idx:03d}'
        originals[block_id] = text
        total += len(text)
        if total > MAX_TEXT:
            raise ValueError('AI 분석 가능한 원문은 최대 15만 자입니다.')
        # Large paragraphs split, but retain the original evidence ID.
        for start in range(0, len(text), CHUNK_CHARS):
            fragment = text[start:start + CHUNK_CHARS]
            if current + len(fragment) > CHUNK_CHARS and chunk:
                chunks.append(chunk)
                chunk = []
                current = 0
            chunk.append({'id': block_id, 'kind': str(entry.get('kind') or 'paragraph'), 'text': fragment})
            current += len(fragment)
    if chunk:
        chunks.append(chunk)
    if not chunks:
        raise ValueError('AI 분석에 사용할 Word 원문이 없습니다.')
    if len(chunks) > MAX_CHUNKS:
        raise ValueError(f'AI 분석 청크가 {MAX_CHUNKS}개를 초과했습니다. 원문을 분할해서 등록하세요.')
    return chunks, originals


def _take(raw: EvidenceValue | dict[str, Any] | None, scope: set[str], originals: dict[str, str],
          label: str, warnings: list[str]) -> dict[str, Any]:
    if raw is None:
        return {'value': '', 'sourceBlockIds': []}
    try:
        ev = raw if isinstance(raw, EvidenceValue) else EvidenceValue.model_validate(raw)
    except Exception:
        warnings.append(f'{label}: AI 응답 구조가 잘못되어 제외했습니다.')
        return {'value': '', 'sourceBlockIds': []}
    value = ev.value.strip()
    ids = list(dict.fromkeys(i for i in ev.sourceBlockIds if i in scope and i in originals))
    if not value:
        return {'value': '', 'sourceBlockIds': []}
    if not ids:
        warnings.append(f'{label}: 원문 블록 근거가 없어 제외했습니다.')
        return {'value': '', 'sourceBlockIds': []}
    if not supported(value, (originals[i] for i in ids)):
        warnings.append(f'{label}: 근거 원문에서 값을 확인할 수 없어 제외했습니다. ({value[:40]})')
        return {'value': '', 'sourceBlockIds': []}
    return {'value': value, 'sourceBlockIds': ids}


def combine(chunk_results: list[ChunkResult | dict[str, Any]], chunks: list[list[dict[str, str]]],
            originals: dict[str, str], model: str) -> dict[str, Any]:
    if len(chunk_results) != len(chunks):
        raise ValueError('AI 추출 청크 수가 맞지 않습니다.')
    profile: dict[str, Any] = {key: '' for key in FIELDS}
    profile['projects'] = []
    profile['extractionWarnings'] = []
    evidence: dict[str, Any] = {'fields': {}, 'projects': []}
    warnings: list[str] = []
    projects_by_key: dict[str, int] = {}

    for number, (raw_chunk, chunk) in enumerate(zip(chunk_results, chunks), 1):
        parsed = raw_chunk if isinstance(raw_chunk, ChunkResult) else ChunkResult.model_validate(raw_chunk)
        scope = {b['id'] for b in chunk}
        for key, raw in parsed.fields.items():
            if key not in FIELDS:
                continue
            taken = _take(raw, scope, originals, key, warnings)
            if not taken['value']:
                continue
            previous = profile[key]
            if not previous:
                profile[key] = taken['value']
                evidence['fields'][key] = taken
            elif _normal(previous) != _normal(taken['value']):
                warnings.append(f'{key}: 서로 다른 값이 추출되어 첫 번째 값을 유지했습니다. 원문 검토 필요.')
        for project in parsed.projects:
            details = {field: _take(getattr(project, field), scope, originals,
                                    f'프로젝트 {number} {field}', warnings) for field in PROJECT_FIELDS}
            if not details['projectName']['value']:
                warnings.append('프로젝트명과 근거가 없는 프로젝트 항목을 제외했습니다.')
                continue
            project_value = {key: details[key]['value'] for key in PROJECT_FIELDS}
            # Stage 02-5's worker_payload accepts full ISO dates only.
            # Month-only dates cannot be converted to days without inventing data.
            for date_field in ('startDate', 'endDate'):
                raw_date = project_value[date_field]
                if not raw_date:
                    continue
                normalized_date = re.sub(r'[./]', '-', raw_date)
                try:
                    if not re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', normalized_date):
                        raise ValueError('incomplete date')
                    date.fromisoformat(normalized_date)
                    project_value[date_field] = normalized_date
                except ValueError:
                    warnings.append(f'프로젝트 {date_field}: 월 단위 기간은 일자를 추측하지 않습니다. 원문 기간(periodText)을 확인하세요.')
                    project_value[date_field] = ''
                    details[date_field] = {'value':'', 'sourceBlockIds':[]}
            project_value['include'] = True
            identity = '|'.join(_normal(project_value[x]) for x in ('projectName', 'startDate', 'endDate'))
            # Do not merge records from different date ranges.
            if identity in projects_by_key:
                pos = projects_by_key[identity]
                for field in PROJECT_FIELDS:
                    if not profile['projects'][pos][field] and details[field]['value']:
                        profile['projects'][pos][field] = details[field]['value']
                        evidence['projects'][pos][field] = details[field]
            else:
                projects_by_key[identity] = len(profile['projects'])
                profile['projects'].append(project_value)
                evidence['projects'].append(details)
        warnings.extend(parsed.warnings[:8])
    if not profile['name']:
        warnings.append('성명 근거를 확인하지 못했습니다. 직접 입력해 주세요.')
    if not profile['projects']:
        warnings.append('프로젝트 경력을 확인하지 못했습니다. 원문을 검토하고 직접 추가해 주세요.')
    if len(profile['projects']) > 100:
        profile['projects'] = profile['projects'][:100]
        evidence['projects'] = evidence['projects'][:100]
        warnings.append('프로젝트는 최대 100건까지만 전달합니다.')
    # Bound output to the existing Stage 02-5 API field limits (except new optional fields).
    limit_by_field = {'ageYears': 3, 'assignmentMonths': 10, 'allocationRate': 16,
                      'birthDate': 20, 'degree': 80, 'certificationsText': 300,
                      'educationText': 250, 'name': 120, 'careerSummary': 500, 'skillsText': 500}
    if profile['birthDate']:
        birth = re.sub(r'[./]', '-', profile['birthDate'])
        try:
            if not re.fullmatch(r'\d{4}-\d{1,2}-\d{1,2}', birth):
                raise ValueError('incomplete date')
            date.fromisoformat(birth)
            profile['birthDate'] = birth
        except ValueError:
            warnings.append('생년월일에 일이 없거나 날짜가 유효하지 않아 제외했습니다.')
            profile['birthDate'] = ''
            evidence['fields'].pop('birthDate', None)
    for key in FIELDS:
        limit = limit_by_field.get(key, 120)
        if len(profile[key]) > limit:
            warnings.append(f'{key}: 허용 길이를 초과해 원문 검토가 필요합니다.')
            profile[key] = ''
            evidence['fields'].pop(key, None)
    for pos, p in enumerate(profile['projects']):
        lengths = {'projectName':250, 'startDate':20, 'endDate':20, 'periodText':160,
                   'submittedRole':250, 'clientName':120, 'workDescription':300}
        for field in PROJECT_FIELDS:
            if len(p[field]) > lengths[field]:
                warnings.append(f'프로젝트 {pos + 1}의 {field} 길이 초과로 값을 제외했습니다.')
                p[field] = ''
                evidence['projects'][pos][field] = {'value':'', 'sourceBlockIds':[]}
    profile['extractionWarnings'] = list(dict.fromkeys(warnings))[:30]
    return {'structured': profile, 'evidence': evidence,
            'warnings': profile['extractionWarnings'],
            'meta': {'model': model, 'chunkCount': len(chunks),
                     'sourceBlockCount': len(originals), 'projectCount': len(profile['projects'])}}
