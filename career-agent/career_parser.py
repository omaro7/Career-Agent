"""Parse supported BWG Channel Unit profile Excel templates (read-only).

Uses only Python's standard-library ZIP/XML reader in the Docker Worker.
Supports legacy Form Control checkboxes from the worksheet VML drawing.
Accepts verified project heading variations; rejects unrecognised layouts rather than guessing fields.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
import posixpath
import re
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import ZipFile, BadZipFile

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
DOC_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
VML = 'urn:schemas-microsoft-com:vml'
EXCEL = 'urn:schemas-microsoft-com:office:excel'
SHEET_NAME = '프로파일'
TEMPLATE_VERSION = 'channel-unit-profile-v1'
GRADE_CHOICES = ('기술사', '특급', '고급', '중급', '초급')
BASE_DATE = date(1899, 12, 30)
MAX_WORKBOOK_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 60 * 1024 * 1024

# The personnel, education and certificates fields remain anchored to the
# checked Channel Unit template. Only the project section can vary in title or
# vertical position. Do not loosen validation across unrelated templates.
PROJECT_NAME_LABELS = {
    '프로젝트명', '프로젝트', '프로젝트명칭', '사업명',
    '프로젝트사업명', '사업프로젝트명', '수행프로젝트',
    '수행프로젝트명', '참여프로젝트명', '과제명', '업무명',
}
PROJECT_START_LABELS = {'시작월', '시작일', '시작년월', '시작일자', '시작'}
PROJECT_END_LABELS = {'종료월', '종료일', '종료년월', '종료일자', '종료'}
PROJECT_ROLE_LABELS = {'담당업무역할', '담당업무', '역할', '수행업무', '수행역할'}
PROJECT_CLIENT_LABELS = {'발주처', '발주기관', '고객사', '고객사명', '수행기관'}


def _header_key(value: Any) -> str:
    """Normalise *labels only*; never apply this to project data."""
    value = normalize_text(value) or ''
    return re.sub(r'[\s()\[\]/\-_·:：]+', '', value).casefold()


def _project_name_header(value: Any) -> bool:
    key = _header_key(value)
    # Some Channel Unit editions annotate the name with '(사업명)' or '(필수)'.
    # This is only trusted together with the date/role/client column anchors.
    return (key in PROJECT_NAME_LABELS or
            ('프로젝트' in key and len(key) <= 30) or
            ('사업명' in key and len(key) <= 30))


def _find_project_header(cells: dict[str, Any]) -> tuple[int, str]:
    """Detect the known B:H project grid while allowing limited header moves.

    The neighboring date, role and client headings are mandatory anchors. A
    different spreadsheet schema is rejected rather than mapped incorrectly.
    """
    for row in range(45, 96):
        label = normalize_text(cells.get(f'B{row}')) or ''
        if not _project_name_header(label):
            continue
        if (_header_key(cells.get(f'C{row + 1}')) in PROJECT_START_LABELS
                and _header_key(cells.get(f'D{row + 1}')) in PROJECT_END_LABELS
                and _header_key(cells.get(f'F{row}')) in PROJECT_ROLE_LABELS
                and _header_key(cells.get(f'H{row}')) in PROJECT_CLIENT_LABELS):
            return row, label
    found = ', '.join(f'{cell}={normalize_text(cells.get(cell))!r}'
                      for cell in ('B55', 'C56', 'D56', 'F55', 'H55'))
    raise ValueError(
        'TEMPLATE_MISMATCH: 지원되는 프로젝트 표 머리글을 찾지 못했습니다. '
        f'주변 값: {found[:250]}. '
        '직원 프로파일의 프로젝트 표(B:H)에 시작월/종료월/담당업무/발주처가 있는지 확인해 주세요.')


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    value = re.sub(r'\s+', ' ', str(value)).strip()
    return value or None


def excel_date(value: Any) -> date | None:
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        n = int(value)
        if n != value or n < 61 or n > 2958465:
            raise ValueError('Invalid Excel date serial')
        return BASE_DATE + timedelta(days=n)
    s = normalize_text(value)
    if not s:
        return None
    for fmt in ('%Y-%m-%d', '%Y.%m.%d', '%Y/%m/%d', '%Y%m%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Invalid date value: {s[:35]}')


def age_on(born: date, reference: date) -> int:
    if reference < born:
        raise ValueError('Reference date precedes birth date')
    return reference.year - born.year - ((reference.month, reference.day) < (born.month, born.day))


def issue(code: str, severity: str, cell: str, message: str, project_row: int | None = None) -> dict:
    return {'code': code, 'severity': severity, 'source_cell': cell,
            'project_source_row': project_row, 'message': message}


def _related_path(parent: str, target: str) -> str:
    if target.startswith('/'):
        return target.lstrip('/')
    return posixpath.normpath(posixpath.join(posixpath.dirname(parent), target))


def _sheet_path(z: ZipFile, name: str) -> str:
    workbook = ET.fromstring(z.read('xl/workbook.xml'))
    props = workbook.find(f'{{{MAIN}}}workbookPr')
    if props is not None and props.get('date1904') in ('1', 'true'):
        raise ValueError('Unsupported workbook 1904 date system')
    sheet = next((s for s in workbook.findall(f'.//{{{MAIN}}}sheet')
                  if s.get('name') == name), None)
    if sheet is None:
        raise ValueError(f'TEMPLATE_MISMATCH: worksheet {name} not found')
    relid = sheet.get(f'{{{DOC_REL}}}id')
    rels = ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
    rel = next((r for r in rels.findall(f'{{{PKG_REL}}}Relationship')
                if r.get('Id') == relid), None)
    if rel is None:
        raise ValueError('TEMPLATE_MISMATCH: worksheet relationship missing')
    path = _related_path('xl/workbook.xml', rel.get('Target', ''))
    if path not in z.namelist():
        raise ValueError('TEMPLATE_MISMATCH: worksheet file missing')
    return path


def _shared_strings(z: ZipFile) -> list[str]:
    if 'xl/sharedStrings.xml' not in z.namelist():
        return []
    root = ET.fromstring(z.read('xl/sharedStrings.xml'))
    return [''.join(node.itertext()) for node in root.findall(f'{{{MAIN}}}si')]


def _cells(z: ZipFile, sheet_path: str) -> tuple[dict[str, Any], dict[str, str]]:
    shared = _shared_strings(z)
    cells: dict[str, Any] = {}
    formulas: dict[str, str] = {}
    root = ET.fromstring(z.read(sheet_path))
    for c in root.iter(f'{{{MAIN}}}c'):
        addr = c.get('r', '')
        if not addr:
            continue
        value_node = c.find(f'{{{MAIN}}}v')
        formula_node = c.find(f'{{{MAIN}}}f')
        inline = c.find(f'{{{MAIN}}}is')
        if formula_node is not None:
            formulas[addr] = formula_node.text or ''
        kind = c.get('t', '')
        raw = value_node.text if value_node is not None else None
        if kind == 's' and raw is not None:
            idx = int(raw)
            if idx < 0 or idx >= len(shared):
                raise ValueError('Invalid shared string index')
            cells[addr] = shared[idx]
        elif kind == 'inlineStr' and inline is not None:
            cells[addr] = ''.join(inline.itertext())
        elif kind in ('str', 'e', 'd'):
            cells[addr] = raw
        elif kind == 'b':
            cells[addr] = raw == '1'
        elif raw is not None:
            try:
                cells[addr] = float(raw) if '.' in raw or 'E' in raw.upper() else int(raw)
            except ValueError:
                cells[addr] = raw
    return cells, formulas


def _checkboxes(z: ZipFile, sheet_path: str, row_number: int) -> dict[str, bool]:
    rels_path = posixpath.join(posixpath.dirname(sheet_path), '_rels', posixpath.basename(sheet_path) + '.rels')
    if rels_path not in z.namelist():
        return {}
    rels = ET.fromstring(z.read(rels_path))
    states: dict[str, bool] = {}
    for rel in rels.findall(f'{{{PKG_REL}}}Relationship'):
        if not rel.get('Type', '').endswith('/vmlDrawing'):
            continue
        path = _related_path(sheet_path, rel.get('Target', ''))
        if path not in z.namelist():
            raise ValueError('Referenced checkbox drawing not found')
        vml = ET.fromstring(z.read(path))
        for shape in vml.findall(f'.//{{{VML}}}shape'):
            client = shape.find(f'{{{EXCEL}}}ClientData')
            if client is None or client.get('ObjectType', '').lower() != 'checkbox':
                continue
            textbox = shape.find(f'{{{VML}}}textbox')
            caption = normalize_text(''.join(textbox.itertext())) if textbox is not None else None
            if caption not in GRADE_CHOICES:
                continue
            nums = [int(n) for n in re.findall(r'-?\d+', client.findtext(f'{{{EXCEL}}}Anchor') or '')]
            if len(nums) < 8 or nums[2] + 1 != row_number:
                continue
            checked = (client.findtext(f'{{{EXCEL}}}Checked') or '0').strip().lower()
            if checked not in ('0', '1', 'false', 'true'):
                raise ValueError('Unexpected checkbox state')
            if caption in states:
                raise ValueError('Duplicated checkbox control caption')
            states[caption] = checked in ('1', 'true')
    return states


def parse_profile_bytes(contents: bytes, as_of: date, source_name: str = 'uploaded.xlsx') -> dict:
    if not contents or len(contents) > MAX_WORKBOOK_BYTES:
        raise ValueError('Excel file must be 1 byte to 10 MB')
    if not contents.startswith(b'PK'):
        raise ValueError('Only .xlsx files are supported')
    try:
        with ZipFile(BytesIO(contents), 'r') as z:
            members = z.infolist()
            if len(members) > 2000 or sum(m.file_size for m in members) > MAX_EXPANDED_BYTES:
                raise ValueError('Excel archive exceeds size limits')
            path = _sheet_path(z, SHEET_NAME)
            cells, formulas = _cells(z, path)
            checked = _checkboxes(z, path, 13)
    except (BadZipFile, KeyError, ET.ParseError, IndexError) as exc:
        raise ValueError('Invalid or unsupported XLSX file') from exc
    expected = {
        'C7': '성명(한글)', 'C8': '직급', 'C10': '생년월일', 'C13': '기술자분류',
    }
    for addr, label in expected.items():
        if normalize_text(cells.get(addr)) != label:
            raise ValueError(f'TEMPLATE_MISMATCH: {addr} must be {label}')
    project_header_row, project_label = _find_project_header(cells)

    issues: list[dict] = []
    if project_header_row != 55 or normalize_text(project_label) != '프로젝트명':
        issues.append(issue(
            'PROJECT_HEADER_VARIANT', 'WARNING', f'B{project_header_row}',
            f'프로젝트 표 머리글({project_label})을 {project_header_row}행에서 인식했습니다. '
            '추출한 프로젝트 경력을 검토해 주세요.'))
    name = normalize_text(cells.get('D7'))
    if not name:
        issues.append(issue('MISSING_NAME', 'ERROR', 'D7', '성명이 비어 있습니다.'))
    try:
        birth = excel_date(cells.get('D10'))
        if birth and birth > as_of:
            raise ValueError('Birth date is later than reference date')
    except ValueError:
        birth = None
        issues.append(issue('INVALID_BIRTH_DATE', 'ERROR', 'D10', '생년월일 값이 올바르지 않습니다.'))
    if birth is None and not any(x['code'] == 'INVALID_BIRTH_DATE' for x in issues):
        issues.append(issue('MISSING_BIRTH_DATE', 'ERROR', 'D10', '생년월일이 비어 있습니다.'))
    position = normalize_text(cells.get('D8'))
    if not position:
        issues.append(issue('MISSING_POSITION', 'WARNING', 'D8', '직급이 비어 있습니다.'))

    grade = normalize_text(cells.get('D13'))
    selected = [label for label in GRADE_CHOICES if checked.get(label)]
    if len(selected) > 1:
        grade = None
        issues.append(issue('MULTIPLE_ENGINEER_GRADES', 'ERROR', 'D13', '기술자분류 체크가 여러 개입니다.'))
    elif len(selected) == 1:
        if grade and grade != selected[0]:
            issues.append(issue('ENGINEER_GRADE_CONFLICT', 'ERROR', 'D13', '셀 값과 체크박스가 다릅니다.'))
        grade = selected[0]
    else:
        if grade not in GRADE_CHOICES:
            grade = None
        if grade is None:
            issues.append(issue('MISSING_ENGINEER_GRADE', 'WARNING', 'D13', '기술자분류가 선택되지 않았습니다.'))
    if not checked:
        issues.append(issue('CHECKBOX_CONTROL_MISSING', 'WARNING', 'D13', '기술자분류 체크박스 컨트롤이 없습니다.'))
    elif len(checked) != len(GRADE_CHOICES):
        issues.append(issue('CHECKBOX_OPTIONS_INCOMPLETE', 'WARNING', 'D13', '일부 기술자분류 체크박스가 누락되었습니다.'))
    if 'YEAR(TODAY())-YEAR(D10)' in formulas.get('F10', '').upper().replace(' ', ''):
        issues.append(issue('AGE_FORMULA_INACCURATE', 'WARNING', 'F10', '기존 수식 대신 생일 기준 만 나이를 계산합니다.'))

    education: list[dict] = []
    for row in range(18, 23):
        school = normalize_text(cells.get(f'C{row}'))
        if school:
            obj = {'level': normalize_text(cells.get(f'B{row}')),
                   'school_name': school, 'major': normalize_text(cells.get(f'D{row}')),
                   'admission_date': None, 'graduation_date': None,
                   'completion_status': normalize_text(cells.get(f'G{row}')),
                   'source_row': row}
            for letter, field in [('E', 'admission_date'), ('F', 'graduation_date')]:
                try:
                    d = excel_date(cells.get(f'{letter}{row}'))
                    obj[field] = d.isoformat() if d else None
                except ValueError:
                    issues.append(issue('INVALID_EDUCATION_DATE', 'WARNING', f'{letter}{row}', '학력 날짜를 확인해 주세요.'))
            education.append(obj)

    certs: list[dict] = []
    for row in range(27, 35):
        cert = normalize_text(cells.get(f'B{row}'))
        if cert:
            obtained = None
            try:
                d = excel_date(cells.get(f'D{row}'))
                obtained = d.isoformat() if d else None
            except ValueError:
                issues.append(issue('INVALID_CERT_DATE', 'WARNING', f'D{row}', '자격증 취득일을 확인해 주세요.'))
            certs.append({'certification_name': cert, 'issuer': normalize_text(cells.get(f'C{row}')),
                          'obtained_date': obtained, 'source_row': row})

    projects: list[dict] = []
    fingerprints: dict[tuple, int] = {}
    blank_rows = 0
    for row in range(project_header_row + 2, max(251, project_header_row + 196)):
        vals = [cells.get(f'{letter}{row}') for letter in 'BCDEFGH']
        if not any(v is not None and normalize_text(v) for v in vals):
            blank_rows += 1
            if blank_rows >= 5:
                break
            continue
        blank_rows = 0
        project_name = normalize_text(vals[0])
        if not project_name:
            issues.append(issue('MISSING_PROJECT_NAME', 'ERROR', f'B{row}', '프로젝트명이 누락되었습니다.', row))
            continue
        try:
            start = excel_date(vals[1]); end = excel_date(vals[2])
        except ValueError:
            start = end = None
            issues.append(issue('INVALID_PROJECT_DATE', 'ERROR', f'C{row}:D{row}', '날짜 형식을 확인해 주세요.', row))
        if start is None or end is None:
            issues.append(issue('MISSING_PROJECT_DATE', 'ERROR', f'C{row}:D{row}', '프로젝트 시작일 또는 종료일이 없습니다.', row))
        elif end < start:
            issues.append(issue('END_BEFORE_START', 'ERROR', f'C{row}:D{row}', '종료일이 시작일보다 빠릅니다.', row))
        p = {'project_name': project_name,
             'start_date': start.isoformat() if start else None,
             'end_date': end.isoformat() if end else None,
             'source_duration_text': normalize_text(vals[3]),
             'role_text': normalize_text(vals[4]),
             'technology_text': normalize_text(vals[5]),
             'client_name': normalize_text(vals[6]),
             'source_row': row, 'duplicate_suspected': False}
        key = (p['project_name'], p['start_date'], p['end_date'], p['role_text'], p['technology_text'], p['client_name'])
        if key in fingerprints:
            p['duplicate_suspected'] = True
            issues.append(issue('DUPLICATE_PROJECT_SUSPECTED', 'WARNING', f'B{row}:H{row}',
                                f'{fingerprints[key]}행과 동일한 프로젝트입니다. 검토가 필요합니다.', row))
        else:
            fingerprints[key] = row
        projects.append(p)
    if not projects:
        issues.append(issue('NO_PROJECTS', 'WARNING', 'B57:H250', '프로젝트 수행 경력이 없습니다.'))
    projects.sort(key=lambda p: (p['start_date'] or '', p['source_row']), reverse=True)
    result = {
        'schema_version': '1.0.0',
        'source': {'file_name': source_name, 'sha256': sha256(contents).hexdigest(),
                   'sheet': SHEET_NAME, 'template_version': TEMPLATE_VERSION},
        'profile': {'name': name, 'birth_date': birth.isoformat() if birth else None,
                    'position_title': position, 'engineer_grade': grade,
                    'education': education, 'certifications': certs, 'projects': projects},
        'computed': {'as_of_date': as_of.isoformat(), 'age_years': age_on(birth, as_of) if birth else None},
        'validation_issues': issues,
        'summary': {'project_rows': len(projects), 'unique_project_candidates': len(fingerprints),
                    'error_count': sum(x['severity'] == 'ERROR' for x in issues),
                    'warning_count': sum(x['severity'] == 'WARNING' for x in issues)},
    }
    return result
