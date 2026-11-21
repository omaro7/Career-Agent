"""Deterministic, auditable proposal generator for customer PPTX layout mappings.

Never sends template text or employee data to an external model.  A proposal is
not considered READY until the existing renderer validates it.  Low-confidence
proposals require review; no speculative personal-field writes are permitted.
"""
from __future__ import annotations

from pathlib import Path
import math
import re
from typing import Any

from pptx import Presentation
from pptx.util import Inches

from career_pptx import (_norm, _auto_header, _validate_layout, _zip_safety)

PERSONAL_LABELS: dict[str, tuple[str, ...]] = {
    'name': ('성명', '이름'),
    'affiliation': ('소속', '소속회사', '회사명'),
    'positionTitle': ('직책', '직급'),
    'ageYears': ('연령', '나이'),
    'certificationsText': ('자격증', '보유자격증', '보유자격'),
    'displayGrade': ('기술등급', '기술자등급'),
    'businessRole': ('본 사업 담당업무', '본사업담당업무', '담당업무'),
    'assignmentPeriod': ('본 사업 투입기간', '본사업투입기간', '본 프로젝트 참여기간', '투입기간', '프로젝트참여기간'),
    'assignmentMonths': ('투입개월', '투입개월수', '투입기간(개월)', '참여개월'),
    'allocationRate': ('투입율', '투입률', '참여율', '참여률'),
    'schoolName': ('최종학교(학위)', '최종학교', '학교명'),
    'majorDegreeText': ('전공',),
    'degree': ('학위',),
}

KNOWN_MISC = ('학력', '경력', '해당분야근무경력', '근무경력', '재직기간')
SAFE_OPTIONAL_HEADERS = ('비고', '특이사항', '참고', '기술', '기술스택')


def _labels() -> dict[str, str]:
    out = {}
    for field, names in PERSONAL_LABELS.items():
        for name in names:
            out[_norm(name)] = field
    return out


_LABEL_TO_FIELD = _labels()
_ALL_LABELS = set(_LABEL_TO_FIELD) | {_norm(s) for s in KNOWN_MISC}


def _find_value_cell(table, r: int, c: int, label: str, field: str):
    """Find a writable value to the right or just underneath a known heading."""
    rows, cols = len(table.rows), len(table.columns)
    # Prefer the immediately adjacent cell if available (Woori major is to
    # the right); otherwise try the same column below (Sinhyup degree/major).
    if c + 1 < cols:
        neighbor = table.cell(r, c + 1)
        if not neighbor.is_spanned and _norm(neighbor.text) not in _ALL_LABELS:
            return [r, c + 1]
    if field in ('majorDegreeText', 'degree') and r + 1 < rows:
        below = table.cell(r + 1, c)
        if not below.is_spanned and _norm(below.text) not in _ALL_LABELS:
            return [r + 1, c]
    for cc in range(c + 1, cols):
        cell = table.cell(r, cc)
        if cell.is_spanned:
            continue
        text = _norm(cell.text)
        if text in _ALL_LABELS:
            break  # Another field's heading starts here.
        return [r, cc]
    if r + 1 < rows:
        for cc in (c, min(c + 1, cols - 1)):
            cell = table.cell(r + 1, cc)
            if not cell.is_spanned and _norm(cell.text) not in _ALL_LABELS:
                return [r + 1, cc]
    return None


def _find_title(shapes, prefix=None):
    prefix = prefix or []
    out = []
    for shape in shapes:
        path = prefix + [shape.name]
        if shape.has_text_frame:
            text = str(shape.text).strip()
            # A conservative template-specific pattern.  Do not guess from
            # arbitrary person names in other textboxes.
            if re.search(r'\(\s*1\s*/\s*1\s*\)', text) and '이력사항' in text:
                out.append((path, text))
        if hasattr(shape, 'shapes'):
            out.extend(_find_title(shape.shapes, path))
    return out


def _text_shapes(shapes, prefix=None):
    prefix = prefix or []
    for shape in shapes:
        path = prefix + [shape.name]
        if shape.has_text_frame:
            yield path, str(shape.text or '')
        if hasattr(shape, 'shapes'):
            yield from _text_shapes(shape.shapes, path)


def _person_candidate(shape):
    table = shape.table
    hits = []
    for r in range(len(table.rows)):
        for c in range(len(table.columns)):
            cell = table.cell(r, c)
            if cell.is_spanned:
                continue
            field = _LABEL_TO_FIELD.get(_norm(cell.text))
            if field:
                hits.append((field, r, c))
    return hits


def _personal_layout(shape, warnings):
    table = shape.table
    hits = _person_candidate(shape)
    seen = set()
    cells = []
    source_label_cells = set()
    assigned = set()
    for field, r, c in hits:
        source_label_cells.add((r, c))
        if field in seen:
            warnings.append('중복된 인적사항 표제가 있어 해당 필드가 확정되지 않았습니다: ' + field)
            continue
        label = _norm(table.cell(r, c).text)
        dest = _find_value_cell(table, r, c, label, field)
        if dest is None:
            warnings.append('값을 기록할 셀을 찾지 못했습니다: ' + field)
            continue
        # Adjacent school/degree labels in 8-column grid may share a row.
        if field == 'majorDegreeText' and label == _norm('전공') and dest[0] > r:
            field = 'educationText'
        elif field == 'majorDegreeText' and label == _norm('전공') and any(_norm(table.cell(rr, cc).text) == _norm('최종학교(학위)') for rr in range(len(table.rows)) for cc in range(len(table.columns))):
            field = 'majorDegreeText'
        key = tuple(dest)
        if key in assigned or key in source_label_cells or _norm(table.cell(*key).text) in _ALL_LABELS:
            warnings.append('대상 셀 중복 또는 표제 충돌로 제외되었습니다: ' + field)
            continue
        seen.add(field)
        assigned.add(key)
        cells.append({'table': shape.name, 'cell': dest, 'field': field})
    if 'name' not in seen:
        warnings.append('성명 매핑을 확정하지 못했습니다.')
    # Clear sample values the customer template contains but this engine does
    # not know how to populate (e.g. '해당분야 근무경력 20년 3월').
    extra_clear = []
    for r in range(len(table.rows)):
        for c in range(len(table.columns)):
            cell = table.cell(r, c)
            text = _norm(cell.text)
            if cell.is_spanned or not text or text in _ALL_LABELS or (r, c) in assigned:
                continue
            # Detect already-populated cells, not headings of unknown fields.
            # Unknown fields are blanked instead of accidentally reusing an
            # example employee's personal data.
            extra_clear.append({'table': shape.name, 'cell': [r, c]})
    return cells, extra_clear


def propose_mapping(path: Path) -> dict[str, Any]:
    """Return a renderer-schema-compatible mapping and conservative assessment.

    This method is read only; the Spring Boot operator workflow persists the
    JSON with its existing PUT /mapping endpoint after approval.
    """
    _zip_safety(path)
    prs = Presentation(str(path))
    warnings: list[str] = []
    if len(prs.slides) != 1:
        return {'status': 'REVIEW_REQUIRED', 'autoApplicable': False,
                'reason': '1장의 인력정보·경력 표 구조만 자동 제안합니다. 여러 장은 수동 확인이 필요합니다.',
                'mapping': None, 'warnings': ['여러 슬라이드 매핑은 검토가 필요합니다.'], 'confidence': 0}
    slide = prs.slides[0]
    career_candidates = []
    personal_candidates = []
    for sh in slide.shapes:
        if not sh.has_table:
            continue
        header = _auto_header(sh.table)
        if header:
            career_candidates.append((sh, header))
        else:
            labels = _person_candidate(sh)
            score = len(set(field for field, _, _ in labels))
            if score >= 4 and any(f == 'name' for f, _, _ in labels):
                personal_candidates.append((sh, score))
    if len(career_candidates) != 1 or len(personal_candidates) != 1:
        return {'status': 'REVIEW_REQUIRED', 'autoApplicable': False, 'confidence': 0,
                'mapping': None, 'warnings': ['경력 표와 인적사항 표를 각각 하나씩 명확히 식별하지 못했습니다.'],
                'reason': '표 구조를 확인하여 수동 매핑이 필요합니다.'}
    career_shape, (header_row, cmap) = career_candidates[0]
    person_shape, personal_score = personal_candidates[0]
    if career_shape.name == person_shape.name:
        raise ValueError('AUTO_MAPPING_INVALID: personal and career tables are identical')
    table = career_shape.table
    first_capacity = len(table.rows) - header_row - 1
    if first_capacity < 1 or first_capacity > 30:
        return {'status': 'REVIEW_REQUIRED', 'autoApplicable': False, 'confidence': 0,
                'mapping': None, 'warnings': ['경력 데이터 행의 개수가 지원 범위가 아닙니다.'],
                'reason': 'PPTX 경력사항 표를 확인하세요.'}
    p_cells, clear_cells = _personal_layout(person_shape, warnings)
    # Correct degree / major headings whose data is below rather than beside.
    # A known ambiguous '비고' column must NOT be interpreted as project detail.
    if len(cmap) == 4 and len(table.columns) > 4:
        headers = [_norm(table.cell(header_row, c).text) for c in range(len(table.columns))]
        if any(t in {_norm(h) for h in SAFE_OPTIONAL_HEADERS} for t in headers):
            warnings.append('비고/기술 열은 의미가 명확하지 않아 자동 채우지 않습니다.')
    pages_title = _find_title(slide.shapes)
    if len(pages_title) > 1:
        warnings.append('페이지 제목이 둘 이상이라 자동 페이지 번호를 설정하지 않았습니다.')
    top = round(person_shape.top / Inches(1), 4)
    header_height = sum(table.rows[r].height for r in range(header_row + 1))
    room = int(prs.slide_height - Inches(0.42) - person_shape.top - header_height)
    continuation_cap = min(15, max(1, int(math.floor(room / Inches(0.26)))))
    title_cell = [0, 0] if header_row > 0 and '경력' in table.cell(0, 0).text else None
    mapping: dict[str, Any] = {
        'version': 1,
        'displayName': path.stem.replace('-', ' ').replace('_', ' '),
        'sourceSlideIndex': 0,
        'careerTable': {
            'name': career_shape.name,
            'headerRow': header_row,
            'dataStartRow': header_row + 1,
            'columnMap': cmap,
            'firstPageCapacity': first_capacity,
            'continuationPageCapacity': continuation_cap,
            'titleCell': title_cell,
            'continuationTopInches': top,
            'bottomMarginInches': 0.42,
        },
        'personalCells': p_cells,
        'clearCells': clear_cells,
        'continuationRemoveShapes': [person_shape.name],
    }
    if len(pages_title) == 1:
        path_names, raw_title = pages_title[0]
        prefix = re.split(r'\s*[-–—]\s*', raw_title)[0].strip()
        if prefix and len(prefix) <= 80:
            mapping['pageTitle'] = {'shapePath': path_names,
                'firstFormat': f'{prefix} - {{name}} ({{page}}/{{total}})',
                'continuationFormat': f'{prefix} ({{page}}/{{total}})'}
        else:
            warnings.append('페이지 제목 형식을 안전하게 분석하지 못했습니다.')
    original_name = ''
    for item in p_cells:
        if item['field'] == 'name':
            original_name = person_shape.table.cell(*item['cell']).text.strip()
            break
    if original_name and len(original_name) >= 2:
        authorized_title_path = mapping.get('pageTitle', {}).get('shapePath')
        for name_path, text in _text_shapes(slide.shapes):
            if original_name in text and name_path != authorized_title_path:
                warnings.append('템플릿의 다른 텍스트 상자에 기존 직원 이름이 포함되어 있어 확인이 필요합니다.')
                break
    # Avoid mapping example employee values into the wrong table cell.
    try:
        _validate_layout(prs, mapping)
    except ValueError as ex:
        return {'status': 'REVIEW_REQUIRED', 'autoApplicable': False, 'mapping': mapping,
                'warnings': warnings + ['생성 후보를 엔진이 검증하지 못했습니다.'], 'confidence': 0,
                'reason': '수정 후 검증 및 저장이 필요합니다.', 'validationCode': str(ex).split(':')[0]}
    mapped_fields = {item['field'] for item in p_cells}
    essential = {'name', 'affiliation', 'positionTitle'}
    confidence = round(min(0.99, 0.50 + 0.04 * len(mapped_fields) + 0.05 * len(cmap)), 2)
    # Auto apply only when the most important identity fields are unequivocal,
    # no unrecognized example values remain, and title is handled when present.
    severe = [w for w in warnings if any(word in w for word in ('충돌','중복된','확정','찾지','다른 텍스트 상자'))]
    auto = essential.issubset(mapped_fields) and not severe and len(mapped_fields) >= 8 and (not pages_title or 'pageTitle' in mapping)
    return {
        'status': 'CANDIDATE_READY' if auto else 'REVIEW_REQUIRED',
        'autoApplicable': auto, 'confidence': confidence,
        'reason': '자동 생성된 매핑입니다. 실제 출력물을 제출하기 전 확인하세요.',
        'mapping': mapping, 'warnings': warnings,
        'detected': {'personalFields': sorted(mapped_fields), 'careerFields': sorted(cmap),
                     'personalTable': person_shape.name, 'careerTable': career_shape.name,
                     'firstPageCapacity': first_capacity, 'continuationPageCapacity': continuation_cap},
    }
