"""Conservative DOCX text-block -> career-agent PPTX payload adapter.

No Ollama guesses: fields that cannot be reliably recognized stay empty until reviewed.
The actual slide/cell layout is rendered by the existing career-agent/career_pptx.py.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

FIELDS = (
    'name', 'affiliation', 'positionTitle', 'birthDate', 'ageYears', 'educationText',
    'degree', 'certificationsText', 'displayGrade', 'businessRole',
    'assignmentPeriod', 'assignmentMonths', 'allocationRate', 'customerName',
)
PROJECT_FIELDS = ('projectName', 'period', 'submittedRole', 'clientName', 'workDescription')
HEADERS = {
    'projectName': ('프로젝트명', '사업명', '프로젝트', '사업/프로젝트명', '수행사업명', '프로젝트명(담당업무명)'),
    'period': ('참여기간', '수행기간', '기간', '프로젝트기간', '사업기간', '근무기간'),
    'submittedRole': ('담당역할', '담당업무', '수행역할', '역할', '포지션'),
    'clientName': ('발주처', '고객사', '발주기관', '발주회사'),
    'workDescription': ('수행업무', '주요업무', '수행내용', '상세업무', '담당업무내용'),
}
PERSONAL = {
    'name': ('성명', '성 명', '이름', '이 름'),
    'affiliation': ('소속', '소속회사'),  # 회사명은 경력 표 컬럼이므로 소속으로 추출하지 않음
    'positionTitle': ('직급', '직책'),  # 직위는 경력 표 컬럼이므로 직급으로 추출하지 않음
    'birthDate': ('생년월일', '출생일'),
    'ageYears': ('만나이', '나이', '연령'),
    'educationText': ('학력', '최종학력', '학교', '학교/전공'),
    'degree': ('학위',),
    'certificationsText': ('자격증', '보유자격증', '자격사항'),
    'displayGrade': ('기술등급', '기술자등급', '등급'),
    'businessRole': ('본사업담당업무', '본 사업 담당업무'),
    'assignmentPeriod': ('본사업투입기간', '투입기간', '본 사업 투입기간'),
    'assignmentMonths': ('투입개월', '투입개월수'),
    'allocationRate': ('투입율', '투입률', '참여율'),
    'customerName': ('제출고객사', '제출처'),
}


def _norm(s: str) -> str:
    return re.sub(r'[\s/_()\-:·]+', '', str(s or '')).casefold()


_PERSONAL_MAP = {_norm(alias): field for field, aliases in PERSONAL.items() for alias in aliases}
_PROJECT_MAP = {_norm(alias): field for field, aliases in HEADERS.items() for alias in aliases}
_DATE_PAIR = re.compile(r'(\d{4})\s*[.\-/년]\s*(\d{1,2})(?:\s*[.\-/월]\s*\d{1,2}\s*일?)?\s*(?:~|〜|–|—|\-|부터|to)\s*(\d{4})\s*[.\-/년]\s*(\d{1,2})', re.I)
_DATE_SINGLE = re.compile(r'(?<!\d)(\d{4})\s*[.\-/년]\s*(\d{1,2})(?!\d)')


# STAGE02_7_EDUCATION: parse school-type/major cells from DOCX tables.
# These are type-based degree *proposals*, not independently verified diplomas.
_EDUCATION_TYPES = ('고등학교', '전문대학', '대학교', '대학원')
_EDUCATION_DEGREE_PRIORITY = {'전문학사': 1, '학사': 2, '석사': 3, '박사': 4}
_EDUCATION_NOT_COMPLETED = ('재학', '휴학', '중퇴', '자퇴', '수료')


def infer_degree(school_type: str) -> str | None:
    """Suggest a degree from a school type; explicit doctoral labels win."""
    normalized = _norm(school_type)
    if '박사' in normalized:
        return '박사'
    if '석사' in normalized:
        return '석사'
    if '대학원' in normalized:
        return '석사'
    if '전문대학' in normalized:
        return '전문학사'
    if '대학교' in normalized or normalized == '대학':
        return '학사'
    return None


def extract_education_row(raw: str) -> dict | None:
    """Parse such rows as '인제 | 대 학 교 | 통계학 | 과 졸업 | ...'.

    Returns None for missing school names and generic table headings.
    """
    cells = [re.sub(r'\s+', '', part) for part in str(raw or '').split('|')]
    if len(cells) < 3:
        return None

    for index, cell in enumerate(cells):
        school_type = None
        if cell in _EDUCATION_TYPES:
            school_type = cell
        elif cell == '대학원박사' or cell == '대학원(박사)':
            school_type = '대학원(박사)'
        elif index == 0 and cell.endswith(('고등학교', '전문대학', '대학교', '대학원')):
            # A merged school-name cell (e.g. '인제대학교 | 통계학 | 졸업').
            school_type = next(t for t in ('전문대학', '고등학교', '대학교', '대학원') if cell.endswith(t))
        if not school_type:
            continue

        if index == 0:
            school = cell
            next_index = 1
        else:
            school = cells[index - 1]
            next_index = index + 1
            if not school or school in ('학교명', '학교', '성명', '이름'):
                continue
            if school_type.startswith('대학원') and school.endswith(('대학교', '대학')):
                school += ' 대학원'
            elif not school.endswith(('고등학교', '전문대학', '대학교', '대학원', '대학')):
                school += '대학원' if school_type.startswith('대학원') else school_type

        if not school or school in _EDUCATION_TYPES or next_index >= len(cells):
            continue
        major = cells[next_index].removesuffix('과') if cells[next_index].endswith('학과') else cells[next_index]
        if major in ('학과', '전공', '과', '과졸업', '졸업', '학교', '학교명'):
            major = ''
        context = cells[next_index + 1: next_index + 5]
        status = next((item for item in ('중퇴', '자퇴', '재학', '휴학', '수료', '졸업')
                       if any(item in value for value in context)), '')
        degree = infer_degree(school_type)
        # Merely attending or completing coursework does not prove a degree.
        if status in _EDUCATION_NOT_COMPLETED:
            degree = None
        return {
            'schoolName': school,
            'schoolType': school_type,
            'major': major,
            'graduationStatus': status,
            'graduationDate': None,
            'degree': degree,
        }
    return None


def split_period(raw: str) -> tuple[str, str]:
    matched = _DATE_PAIR.search(raw or '')
    if matched:
        y1, m1, y2, m2 = map(int, matched.groups())
        if all(1 <= m <= 12 for m in (m1, m2)):
            return f'{y1:04d}-{m1:02d}-01', f'{y2:04d}-{m2:02d}-01'
    matches = _DATE_SINGLE.findall(raw or '')
    if len(matches) == 2 and all(1 <= int(m) <= 12 for _, m in matches):
        return f'{int(matches[0][0]):04d}-{int(matches[0][1]):02d}-01', f'{int(matches[1][0]):04d}-{int(matches[1][1]):02d}-01'
    return '', ''


def blank_profile() -> dict:
    return {**{key: '' for key in FIELDS}, 'projects': [], 'extractionWarnings': []}


def _project() -> dict:
    return {'projectName': '', 'startDate': '', 'endDate': '', 'periodText': '',
            'submittedRole': '', 'clientName': '', 'workDescription': '', 'include': True}


# STAGE02_7_BIRTHDATE_PATCH
_BIRTH_DATE_PATTERN = re.compile(
    r"(?<!\d)(\d{4}|\d{2})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?(?!\d)"
)


def normalize_birthdate(raw: str, *, as_of: date | None = None) -> str:
    """Return YYYY-MM-DD. Two-digit years use 00..today's yy => 20xx, else 19xx.

    Dates in the future or ages greater than 120 are not accepted as birth dates.
    Ambiguous 2-digit birth years should remain user-reviewable.
    """
    reference = as_of or datetime.now(ZoneInfo('Asia/Seoul')).date()
    match = _BIRTH_DATE_PATTERN.search(str(raw or ''))
    if not match:
        return ''
    year_raw, month_raw, day_raw = match.groups()
    year = int(year_raw)
    if len(year_raw) == 2:
        year = 2000 + year if year <= reference.year % 100 else 1900 + year
    try:
        birth = date(year, int(month_raw), int(day_raw))
    except ValueError:
        return ''
    age = reference.year - birth.year - ((reference.month, reference.day) < (birth.month, birth.day))
    return birth.isoformat() if 0 <= age <= 120 else ''


# STAGE02_7_RRN_FALLBACK: only the six-digit date portion is used.
# A seven-character suffix, when present, is inspected only for its century code.
# Never add a resident-registration-number field to the structured profile.
_RRN_LABELS = frozenset({
    _norm('주민등록번호'),
    _norm('주 민 등 록 번 호'),
    _norm('주민번호'),
    _norm('주민등록번호 앞 6자리'),
})
_RRN_PATTERN = re.compile(
    r'^\s*(\d{6})(?:\s*[-‐‑–]\s*([1-8Xx*])[0-9Xx*]{0,6}|([1-8])[0-9Xx*]{6})?\s*$'
)


def birthdate_from_rrn(raw: str, *, as_of: date | None = None) -> tuple[str, bool]:
    """Return (YYYY-MM-DD, century_inferred) from an explicitly labelled RRN.

    With six digits alone, century is estimated by the existing current-year
    pivot and must be reviewed by the user. With a known 7th digit, infer the
    century from the official digit groups. Never return/store the identifier.
    """
    reference = as_of or datetime.now(ZoneInfo('Asia/Seoul')).date()
    match = _RRN_PATTERN.fullmatch(str(raw or ''))
    if not match:
        return '', False
    six, code1, code2 = match.groups()
    year2, month, day = int(six[:2]), int(six[2:4]), int(six[4:6])
    century_digit = code1 or code2
    if century_digit in ('1', '2', '5', '6'):
        year = 1900 + year2
        inferred = False
    elif century_digit in ('3', '4', '7', '8'):
        year = 2000 + year2
        inferred = False
    else:
        year = (2000 if year2 <= reference.year % 100 else 1900) + year2
        inferred = True
    try:
        born = date(year, month, day)
    except ValueError:
        return '', False
    if not 0 <= reference.year - born.year - (
        (reference.month, reference.day) < (born.month, born.day)
    ) <= 120:
        return '', False
    return born.isoformat(), inferred


def full_age(birth_date: str, *, as_of: date | None = None) -> str:
    reference = as_of or datetime.now(ZoneInfo('Asia/Seoul')).date()
    try:
        born = date.fromisoformat(birth_date)
    except (ValueError, TypeError):
        return ''
    age = reference.year - born.year - ((reference.month, reference.day) < (born.month, born.day))
    return str(age) if 0 <= age <= 120 else ''


# Prevent generic employment-table headers being mistaken for personal values.
_PERSONAL_HEADER_ARTIFACTS = {
    'affiliation': {'기간', '근무기간', '참여기간', '직위', '직급', '직책', '담당업무', '담당역할', '회사명'},
    'positionTitle': {'기간', '근무기간', '참여기간', '담당업무', '담당역할', '회사명', '근무회사', '직위'},
}


def sanitize_personal_header_values(profile: dict) -> dict:
    """Return a copy with known employment header artifacts cleared.

    Useful for already-saved reviewed profiles; never changes unrelated values.
    """
    cleaned = dict(profile)
    for field, invalid in _PERSONAL_HEADER_ARTIFACTS.items():
        if _norm(cleaned.get(field, '')) in invalid:
            cleaned[field] = ''
    return cleaned


def _is_employment_table_header(cells: list[str]) -> bool:
    """Recognize e.g. '회 사 명 | 기 간 | 직 위 | 담 당 업 무'."""
    norms = {_norm(cell) for cell in cells}
    return bool(
        norms.intersection({'회사명', '근무회사'})
        and norms.intersection({'기간', '근무기간', '재직기간'})
        and norms.intersection({'직위', '직급', '직책'})
        and norms.intersection({'담당업무', '수행업무', '역할'})
    )


def _field_value(result: dict, field: str, text: str):
    text = text.strip()
    if not text:
        return
    if field in _PERSONAL_HEADER_ARTIFACTS and _norm(text) in _PERSONAL_HEADER_ARTIFACTS[field]:
        return
    if field == 'ageYears':
        if result.get('birthDate'):
            return  # Current Korean full age takes precedence over source age text.
        m = re.search(r'(\d{1,3})', text)
        if m and int(m.group(1)) <= 120:
            result[field] = m.group(1)
        return
    if field == 'birthDate':
        birth_date = normalize_birthdate(text)
        if birth_date and not result.get('birthDate'):
            result['birthDate'] = birth_date
            result['ageYears'] = full_age(birth_date)
        return
    if field == 'assignmentMonths':
        m = re.search(r'\d+(?:\.5)?', text)
        if m: result[field] = m.group()
        return
    if field == 'allocationRate':
        m = re.search(r'\d+(?:\.\d+)?\s*%?', text)
        if m: result[field] = m.group().replace(' ', '')
        return
    # Preserve first occurrence; overwriting with later unrelated sections risks PII corruption.
    if not result[field]:
        result[field] = text[:300]


def _set_project_value(target: dict, field: str, value: str):
    value = value.strip()
    if not value:
        return
    if field == 'period':
        target['periodText'] = normalize_period_display(value)[:160]
        target['startDate'], target['endDate'] = split_period(value)
    else:
        target[field] = value[:500]


# STAGE02_7_PERSONAL_ROW_LABEL_FIX
# Extract explicit personal fields from pipe-delimited DOCX table rows.
# Repeated Word labels and empty merged cells do not shift values to other fields.
_PERSONAL_ROW_BARRIER_LABELS = frozenset(
    _norm(label) for label in (
        '성별', '성 별', '경력', '근무경력', '총경력', '등급', '등 급',
        '현주소', '주소', '연락처', '전화번호', '휴대전화', '핸드폰',
        '이메일', 'E-MAIL', '회사명', '근무회사', '직위',
        '기간', '기 간', '참여기간', '담당업무', '담 당 업 무',
        '학교명', '학교', '프로젝트명',
    )
)


def _read_personal_cells(result: dict, cells: list[str], rrn_capture=None) -> bool:
    """Read '성 명 | 한정훈 | 생년월일 | 생년월일 | 1988. 04. 26'.

    Consumes only an explicitly recognised label's next actual value. A new
    label is a hard boundary; never infer affiliation from a career row.
    """
    rrn_labels = globals().get('_RRN_LABELS', frozenset())
    boundaries = set(_PERSONAL_MAP) | set(rrn_labels) | _PERSONAL_ROW_BARRIER_LABELS
    recognized = False
    for i, cell in enumerate(cells):
        label = _norm(cell)
        field = _PERSONAL_MAP.get(label)
        is_rrn = label in rrn_labels
        if not field and not is_rrn:
            continue
        recognized = True
        for candidate in cells[i + 1:]:
            value = str(candidate or '').strip()
            code = _norm(value)
            if not value or code == label:  # blank merged cells and repeated labels
                continue
            if code in boundaries:  # the next field starts here; no value for this label
                break
            if field == 'name' and (len(value) > 60 or re.search(r'[|\r\n]', value)):
                break
            if field == 'affiliation' and re.fullmatch(r'\d+\s*년(?:\s*\d+\s*개월)?', value):
                break
            if is_rrn:
                if rrn_capture is not None:
                    rrn_capture(cell, value)
            else:
                _field_value(result, field, value)
            break
    return recognized


def from_blocks(draft: dict) -> dict:
    """Extract only explicit labels/headers; ambiguous content requires manual review."""
    result = blank_profile()
    current: dict | None = None
    table_header: list[str | None] | None = None
    best_education: dict | None = None
    best_education_rank = -1
    rrn_birth_fallback = ''
    rrn_century_inferred = False

    def capture_rrn(label: str, value: str) -> bool:
        nonlocal rrn_birth_fallback, rrn_century_inferred
        if _norm(label) not in _RRN_LABELS:
            return False
        if not rrn_birth_fallback:
            candidate, inferred = birthdate_from_rrn(value)
            if candidate:
                rrn_birth_fallback = candidate
                rrn_century_inferred = inferred
        return True

    def flush():
        nonlocal current
        if current and current['projectName']:
            result['projects'].append(current)
        current = None

    for block in draft.get('blocks', []):
        kind = block.get('kind', 'paragraph')
        text = str(block.get('text', '')).strip()
        if not text:
            continue
        if kind == 'heading':
            table_header = None
        # DOCX adapters may label a table-like pipe row as a paragraph.
        # Remove only outer pipe delimiters, keeping empty interior cells.
        cells = [cell.strip() for cell in text.strip('|').split('|')] if '|' in text else []
        # Employment-history column headings are not personal details.
        if cells and _is_employment_table_header(cells):
            table_header = None
            continue
        # The education row can share a Word table with certificate columns.
        # Match the school-type cell rather than hard-coding Word block numbers.
        if kind == 'table':
            education = extract_education_row(text)
            if education:
                rank = _EDUCATION_DEGREE_PRIORITY.get(
                    infer_degree(education['schoolType']) or '', 0
                )
                if rank > best_education_rank:
                    best_education = education
                    best_education_rank = rank
                continue
        if cells and len(cells) >= 3:
            candidate = [_PROJECT_MAP.get(_norm(c)) for c in cells]
            if 'projectName' in candidate and len(set(x for x in candidate if x)) >= 3:
                flush()
                table_header = candidate
                continue
            if table_header and len(cells) == len(table_header):
                project = _project()
                for col, cell in zip(table_header, cells):
                    if col: _set_project_value(project, col, cell)
                if project['projectName']:
                    result['projects'].append(project)
                    continue
        if kind != 'table':
            table_header = None

        if cells and _read_personal_cells(result, cells, capture_rrn):
            continue

        # Allow key:value lines including single rows from tables.
        m = re.match(r'^\s*([^:：|]{1,35})\s*[:：|]\s*(.*?)\s*$', text, re.S)
        if not m:
            continue
        label, value = m.group(1).strip(), m.group(2).strip()
        if capture_rrn(label, value):
            continue
        code = _norm(label)
        pfield = _PROJECT_MAP.get(code)
        if pfield == 'projectName' and value:
            flush()
            current = _project()
            _set_project_value(current, pfield, value)
            continue
        if pfield and current is not None:
            _set_project_value(current, pfield, value)
            continue
        field = _PERSONAL_MAP.get(code)
        if field:
            _field_value(result, field, value)
    flush()
    # Explicit birth date always wins, regardless of row order.
    # The raw registration number is intentionally not copied into the profile.
    if not result['birthDate'] and rrn_birth_fallback:
        result['birthDate'] = rrn_birth_fallback
        result['ageYears'] = full_age(rrn_birth_fallback)
        if rrn_century_inferred:
            result['extractionWarnings'].append(
                '주민등록번호 앞 6자리로 출생연도의 세기를 추정했습니다. 확인해 주세요.'
            )
    # Pick the highest school level found; preserve explicitly labelled values.
    if best_education:
        if not result['educationText']:
            result['educationText'] = ' / '.join(
                part for part in (
                    best_education['schoolName'],
                    best_education['major'],
                    best_education['graduationStatus'],
                ) if part
            )[:300]
        if not result['degree'] and best_education['degree']:
            result['degree'] = best_education['degree']
        elif not result['degree'] and best_education['graduationStatus'] in _EDUCATION_NOT_COMPLETED:
            result['extractionWarnings'].append(
                '최종 학력이 재학·휴학·수료·중퇴 상태라 학위를 자동 확정하지 않았습니다.'
            )
    if not result['name']:
        result['extractionWarnings'].append('성명 자동 추출 실패: 검토 화면에서 입력하세요.')
    if not result['projects']:
        result['extractionWarnings'].append('프로젝트 표/항목을 인식하지 못했습니다. 경력 프로젝트를 직접 추가하세요.')
    if len(result['projects']) > 100:
        result['projects'] = result['projects'][:100]
        result['extractionWarnings'].append('프로젝트 100건까지만 인식되었습니다.')
    if any(not p['startDate'] or not p['endDate'] for p in result['projects']):
        result['extractionWarnings'].append('일부 프로젝트의 참여기간을 날짜로 변환하지 못했습니다. 원문 기간은 보존됩니다.')
    return recover_periods_from_reviewed(fill_missing_project_dates(enrich_projects_from_review(sanitize_personal_header_values(result), draft)[0]), draft)[0]


# STAGE02_7_REGISTER_AUTOFILL: deterministic Word project field enrichment.
# Called as part of *initial* structured extraction, not only from a UI button.
def enrich_projects_from_review(profile: dict, reviewed: dict) -> tuple[dict, bool]:
    """Add only missing project fields, preserving already reviewed values.

    No database mutation occurs here. Existing project rows are matched by
    source evidence, or by normalized title + participation month range.
    """
    from .word_project_extractor import extract_projects

    proposals = extract_projects(reviewed).get('projects', [])
    if not proposals:
        return profile, False

    projects = [dict(p) for p in (profile.get('projects') or []) if isinstance(p, dict)]
    changed = False

    def normalized(text: object) -> str:
        import re
        return re.sub(r'[^0-9a-z가-힣]', '', str(text or '').casefold())

    def month(text: object) -> str:
        import re
        m = re.search(r'(\d{4})[^0-9]?(\d{1,2})', str(text or ''))
        return f'{m.group(1)}-{int(m.group(2)):02d}' if m else ''

    for candidate in proposals:
        name = normalized(candidate.get('projectName'))
        if not name:
            continue
        sources = set(candidate.get('sourceBlockIds') or [])
        by_source = [i for i, p in enumerate(projects) if sources and
                     sources.intersection(p.get('sourceBlockIds') or [])]
        if len(by_source) == 1:
            idx = by_source[0]
        else:
            same = [i for i, p in enumerate(projects) if normalized(p.get('projectName')) == name]
            same_period = [i for i in same
                           if (not month(candidate.get('startDate')) or
                               month(projects[i].get('startDate')) == month(candidate.get('startDate')))
                           and (not month(candidate.get('endDate')) or
                               month(projects[i].get('endDate')) == month(candidate.get('endDate')))]
            # Avoid joining unrelated records when project names are ambiguous.
            idx = same_period[0] if len(same_period) == 1 else None
            if idx is None and len(same) == 1 and (
                not candidate.get('startDate') or
                (not projects[same[0]].get('startDate') and
                 not projects[same[0]].get('endDate'))
            ):
                idx = same[0]

        if idx is None:
            if len(projects) >= 100:
                continue
            projects.append(dict(candidate))
            changed = True
            continue

        project = projects[idx]
        for key, value in candidate.items():
            if key == 'include':
                continue
            existing = project.get(key)
            if isinstance(value, str) and value.strip() and not str(existing or '').strip():
                project[key] = value
                changed = True
            elif key == 'sourceBlockIds' and value and not existing:
                project[key] = list(value)
                changed = True
            elif key == 'confidence' and value is not None and existing is None:
                project[key] = value
                changed = True

    if not changed:
        return profile, False
    return {**profile, 'projects': projects}, True


def _clean(value, limit=300) -> str:
    return str(value or '').strip()[:limit]


# STAGE02_7_PERIOD_BACKFILL
# Derive ISO dates only when a reviewed project date is missing. Preserve
# manually reviewed start/end values, including intentionally changed dates.
def fill_missing_project_dates(profile: dict) -> dict:
    if not isinstance(profile, dict):
        return profile
    originals = profile.get('projects')
    if not isinstance(originals, list):
        return profile
    fixed = []
    changed = False
    for item in originals:
        if not isinstance(item, dict):
            fixed.append(item)
            continue
        project = dict(item)
        period = str(project.get('periodText') or project.get('period') or '')
        start, end = split_period(period)
        if start and not str(project.get('startDate') or '').strip():
            project['startDate'] = start
            changed = True
        if end and not str(project.get('endDate') or '').strip():
            project['endDate'] = end
            changed = True
        fixed.append(project)
    return {**profile, 'projects': fixed} if changed else profile


# STAGE02_7_PERIOD_EVIDENCE_FIX
# Recover dates from the same reviewed Word row as a project, even when the
# earlier structured project lost periodText entirely. Never overwrite edits.
def recover_periods_from_reviewed(profile: dict, reviewed: dict) -> tuple[dict, bool]:
    if not isinstance(profile, dict) or not isinstance(reviewed, dict):
        return profile, False
    projects = profile.get('projects')
    blocks = reviewed.get('blocks')
    if not isinstance(projects, list) or not isinstance(blocks, list):
        return profile, False

    def namekey(value: object) -> str:
        return re.sub(r'[^0-9a-z가-힣]', '', str(value or '').casefold())

    recovered = [dict(p) if isinstance(p, dict) else p for p in projects]
    changed = False
    for n, block in enumerate(blocks, 1):
        if not isinstance(block, dict) or block.get('kind') not in ('table', 'paragraph'):
            continue
        text = str(block.get('text') or '')
        if '|' not in text:
            continue
        cells = [cell.strip() for cell in text.split('|')]
        if len(cells) < 2:
            continue
        source = f'B{n}'
        for cell_i, cell in enumerate(cells[:-1]):
            candidate_name = namekey(cell)
            if not candidate_name:
                continue
            indexes = [i for i, p in enumerate(recovered)
                       if isinstance(p, dict) and namekey(p.get('projectName')) == candidate_name]
            # Match a unique project name, or a unique original source block.
            if len(indexes) != 1:
                indexes = [i for i in indexes if source in (recovered[i].get('sourceBlockIds') or [])]
            if len(indexes) != 1:
                continue
            idx = indexes[0]
            project = recovered[idx]
            for value in cells[cell_i + 1:cell_i + 4]:
                # Both dates must occur in one period cell; unrelated dates
                # in narrative or other projects must not be borrowed.
                start, end = split_period(value)
                if not (start and end):
                    continue
                # Source period only fills an empty field. User-edited values win.
                if not str(project.get('periodText') or '').strip():
                    project['periodText'] = normalize_period_display(value)[:160]
                    changed = True
                # Prefer the already reviewed period if it contains both dates.
                verified_start, verified_end = split_period(str(project.get('periodText') or ''))
                if not (verified_start and verified_end):
                    verified_start, verified_end = start, end
                if not str(project.get('startDate') or '').strip():
                    project['startDate'] = verified_start
                    changed = True
                if not str(project.get('endDate') or '').strip():
                    project['endDate'] = verified_end
                    changed = True
                break
    return ({**profile, 'projects': recovered}, True) if changed else (profile, False)


# STAGE02_7_PERIOD_DISPLAY_FIX
_PERIOD_DISPLAY_RE = re.compile(
    r'^\s*(\d{4})\s*[./-]\s*(\d{1,2})\s*~\s*/?\s*(\d{4})\s*[./-]\s*(\d{1,2})\s*$'
)


def normalize_period_display(raw: str) -> str:
    """Format a two-month period as YYYY.MM ~ YYYY.MM; preserve other text."""
    text = str(raw or '').strip()
    match = _PERIOD_DISPLAY_RE.fullmatch(text)
    if not match:
        return text
    y1, m1, y2, m2 = map(int, match.groups())
    if not (1 <= m1 <= 12 and 1 <= m2 <= 12):
        return text
    return f'{y1:04d}.{m1:02d} ~ {y2:04d}.{m2:02d}'


def normalize_profile_period_display(profile: dict) -> dict:
    """Non-destructively normalize structured project periodText on API read."""
    if not isinstance(profile, dict) or not isinstance(profile.get('projects'), list):
        return profile
    changed = False
    projects = []
    for item in profile['projects']:
        if not isinstance(item, dict):
            projects.append(item)
            continue
        old = item.get('periodText')
        new = normalize_period_display(old) if isinstance(old, str) else old
        if new != old:
            projects.append({**item, 'periodText': new})
            changed = True
        else:
            projects.append(item)
    return {**profile, 'projects': projects} if changed else profile


def worker_payload(reviewed: dict, *, template_id: str, template_customer_name: str = '', as_of_date: str = '') -> dict:
    """Convert user-reviewed data into the EXISTING /render-customer-pptx contract."""
    reviewed = fill_missing_project_dates(reviewed)
    name = _clean(reviewed.get('name'), 120)
    if not name:
        raise ValueError('성명을 확인하고 입력해 주세요.')
    raw_projects = reviewed.get('projects', [])
    if not isinstance(raw_projects, list):
        raise ValueError('경력 프로젝트 목록이 올바르지 않습니다.')
    projects = []
    for project in raw_projects:
        if project.get('include', True) is False:
            continue
        title = _clean(project.get('projectName'), 250)
        if not title:
            raise ValueError('출력할 모든 프로젝트에 프로젝트명을 입력해 주세요.')
        start = _clean(project.get('startDate'), 20)
        end = _clean(project.get('endDate'), 20)
        for d in (start, end):
            if d:
                try: date.fromisoformat(d)
                except ValueError as exc: raise ValueError('프로젝트 날짜는 YYYY-MM-DD 형식이어야 합니다.') from exc
        projects.append({'projectName': title, 'startDate': start, 'endDate': end,
                         'periodText': _clean(project.get('periodText'), 160),
                         'submittedRole': _clean(project.get('submittedRole'), 250),
                         'clientName': _clean(project.get('clientName'), 120),
                         'workDescription': _clean(project.get('workDescription'), 300),
                         # STAGE02_7_TECH_PAYLOAD: reviewed, project-scoped values
                         'employerName': _clean(project.get('employerName'), 500),
                         'technologiesText': _clean(project.get('technologiesText'), 500),
                         'dbmsText': _clean(project.get('dbmsText'), 500),
                         'toolsText': _clean(project.get('toolsText'), 500),
                         'osText': _clean(project.get('osText'), 500),
                         'communicationText': _clean(project.get('communicationText'), 500),
                         'otherText': _clean(project.get('otherText'), 500),
                         'industryText': _clean(project.get('industryText'), 500),
                         'applicationText': _clean(project.get('applicationText'), 500),
                         'hardwareText': _clean(project.get('hardwareText'), 500)})
    if not 1 <= len(projects) <= 100:
        raise ValueError('출력할 경력 프로젝트를 1~100건 선택해 주세요.')
    age = None
    raw_age = _clean(reviewed.get('ageYears'), 3)
    if raw_age:
        if not raw_age.isdigit() or int(raw_age) > 120:
            raise ValueError('만 나이는 0~120 숫자로 입력해 주세요.')
        age = int(raw_age)
    elif reviewed.get('birthDate'):
        try:
            birth = date.fromisoformat(reviewed['birthDate'])
            ref = date.fromisoformat(as_of_date) if as_of_date else datetime.now(ZoneInfo('Asia/Seoul')).date()
            age = ref.year - birth.year - ((ref.month, ref.day) < (birth.month, birth.day))
            if not 0 <= age <= 120: raise ValueError('age out of range')
        except ValueError as exc:
            raise ValueError('생년월일 또는 기준일이 올바르지 않습니다.') from exc
    months = _clean(reviewed.get('assignmentMonths'), 10)
    if months:
        if not re.fullmatch(r'(?:0\.5|[1-9]\d{0,2}(?:\.[05])?)', months) or not 0.5 <= float(months) <= 600:
            raise ValueError('투입개월은 0.5~600의 0.5 단위 숫자로 입력하세요.')
    rate = _clean(reviewed.get('allocationRate'), 16)
    if rate:
        if not re.fullmatch(r'\d{1,3}(?:\.\d{1,2})?%?', rate) or float(rate.rstrip('%')) > 100:
            raise ValueError('투입율은 0~100 사이의 숫자 또는 % 형태여야 합니다.')
    cleaned_personal = sanitize_personal_header_values(reviewed)
    return {'templateId': template_id, 'name': name,
            'affiliation': _clean(cleaned_personal.get('affiliation'), 120),
            'positionTitle': _clean(cleaned_personal.get('positionTitle'), 120),
            'ageYears': age, 'educationText': _clean(reviewed.get('educationText'), 250),
            'degree': _clean(reviewed.get('degree'), 80),
            'certificationsText': _clean(reviewed.get('certificationsText'), 300),
            'displayGrade': _clean(reviewed.get('displayGrade'), 80),
            'businessRole': _clean(reviewed.get('businessRole'), 120),
            'assignmentPeriod': _clean(reviewed.get('assignmentPeriod'), 120),
            'assignmentMonths': months, 'allocationRate': rate,
            'customerName': _clean(reviewed.get('customerName') or template_customer_name, 100),
            'careerPageScope': 'ALL', 'projectPeriodFormat': 'YYYY_MM',
            'projects': projects}
