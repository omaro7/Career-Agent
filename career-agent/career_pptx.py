"""Client-specific PPTX rendering: validated layouts, no source Excel access.

Templates are read-only administrator-installed files. A one-slide template with
unambiguously recognizable personal and career tables is automatically supported.
Other layouts require a vetted <stem>.mapping.json file alongside the .pptx.
Never silently guess where to write employee PII in an unknown format.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date
from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
import string
import re
from typing import Any
from zipfile import ZipFile, BadZipFile

from pptx import Presentation
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.util import Inches, Pt

MAX_TEMPLATE_BYTES = 50 * 1024 * 1024
MAX_MAPPING_BYTES = 100_000
MAX_PROJECTS = 100
MAX_TEMPLATES = 100
CAREER_PAGE_SCOPES = frozenset({"ALL", "FIRST_PAGE"})
# Upper bound on ZIP expansion, before python-pptx parses the XML.
MAX_UNCOMPRESSED_BYTES = 250 * 1024 * 1024

CAREER_KEYS = ('projectName', 'period', 'submittedRole', 'clientName', 'workDescription',
               'employerName', 'technologiesText', 'dbmsText', 'toolsText', 'osText',
               'communicationText', 'otherText', 'industryText', 'applicationText', 'hardwareText')
PERSONAL_KEYS = ('name', 'affiliation', 'positionTitle', 'ageYears', 'educationText',
                 'degree', 'certificationsText', 'displayGrade', 'businessRole',
                 'assignmentPeriod', 'assignmentMonths', 'allocationRate', 'customerName', 'schoolName', 'majorDegreeText')
LABELS = {
    'projectName': ('사업명', '프로젝트명', '프로젝트', '사업/프로젝트명', '수행사업명'),
    'period': ('참여기간', '수행기간', '프로젝트기간', '사업기간'),
    'submittedRole': ('담당업무', '담당역할', '수행역할', '역할'),
    'clientName': ('발주처', '고객사', '발주기관', '발주회사'),
    'workDescription': ('수행업무', '주요수행업무', '주요업무', '수행내용', '상세업무'),
    # STAGE02_7_TECH_CUSTOMER_MAPPING: optional existing customer columns only.
    'employerName': ('근무회사', '소속회사', '수행사'),
    'technologiesText': ('언어', '사용언어', '언어프레임워크', '사용언어프레임워크', '기술스택'),
    'dbmsText': ('DBMS', '데이터베이스'),
    'toolsText': ('TOOL', '개발도구'),
    'osText': ('OS', '운영체제'),
    'communicationText': ('통신',),
    'otherText': ('기타',),
    'industryText': ('산업',),
    'applicationText': ('응용',),
    'hardwareText': ('기종',),
}


def _norm(value: str) -> str:
    return re.sub(r'[\s\-_:·/()]+', '', str(value or '').lower())


def _shape(slide, name: str):
    found = [s for s in slide.shapes if s.name == name]
    if len(found) != 1:
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: shape {name!r} not found uniquely')
    return found[0]


def _table(slide, name: str):
    obj = _shape(slide, name)
    if not obj.has_table:
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: {name!r} is not a table')
    return obj


def _cell_unchanged_style(cell, value: Any, size: float | None = None):
    """Replace cell text while preserving formatting, without orphaned line breaks.

    PPTX cells may contain embedded <a:br/> elements between styled runs.
    Clearing only run.text leaves these XML line breaks behind, which creates
    an unintended empty third line after a two-line assignment period.
    Clearing paragraph content removes old runs AND breaks, while keeping the
    paragraph's centering, spacing, and other paragraph-level styling.  The
    original first run's character styling is copied to the replacement run.
    """
    frame = cell.text_frame
    if not frame.paragraphs:
        frame.add_paragraph()
    paragraph = frame.paragraphs[0]
    first_style = deepcopy(paragraph.runs[0]._r.rPr) if paragraph.runs and paragraph.runs[0]._r.rPr is not None else None
    paragraph.clear()  # remove old runs, <a:br/>, fields, and inline artifacts
    run = paragraph.add_run()
    if first_style is not None:
        run._r.insert(0, first_style)
    run.text = '' if value is None else str(value).strip()
    if size is not None:
        run.font.size = Pt(size)
    for extra in list(frame.paragraphs)[1:]:
        extra._element.getparent().remove(extra._element)


# Supported presentation-only year formats. Existing profile dates are never changed.
PROJECT_PERIOD_FORMATS = {'YYYY_MM', 'YY_MM'}


def _ym(value: Any, period_format: str = 'YYYY_MM') -> str:
    if value is None:
        return ''
    if isinstance(value, date):
        value = value.strftime('%Y-%m')
    raw = str(value).strip()
    match = re.fullmatch(r'(\d{4})[-./](\d{1,2})(?:[-./]\d{1,2})?', raw)
    if match:
        year, month = match.groups()
        if 1 <= int(month) <= 12:
            return f'{year[-2:] if period_format == "YY_MM" else year}.{int(month):02d}'
    return raw


def _period_fallback(value: Any, period_format: str) -> str:
    """Preserve free text; abbreviate only unmistakable 4-digit year.month tokens."""
    raw = str(value or '').strip()
    if period_format != 'YY_MM':
        return raw
    return re.sub(r'(?<!\d)(\d{4})[./-](0[1-9]|1[0-2])(?!\d)',
                  lambda match: f'{match.group(1)[-2:]}.{match.group(2)}', raw)


def _validated_assignment_months(data: dict) -> str:
    """Normalize numeric assignment months; older payloads may omit this value.

    Fractions are half-months (e.g. 10.5). Invalid direct Worker payloads
    are rejected even if Spring Boot validation is bypassed.
    """
    raw = data.get('assignmentMonths')
    if raw is None or raw == '':
        return ''
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        raise ValueError('INVALID_ASSIGNMENT_MONTHS: positive number in 0.5 increments required')
    raw = str(raw).strip()
    if not re.fullmatch(r'(?:0\.5|[1-9][0-9]{0,2}(?:\.[05])?)', raw):
        raise ValueError('INVALID_ASSIGNMENT_MONTHS: positive number in 0.5 increments required')
    from decimal import Decimal
    number = Decimal(raw)
    if not Decimal('0.5') <= number <= Decimal('600'):
        raise ValueError('INVALID_ASSIGNMENT_MONTHS: must be 0.5..600')
    return format(number.normalize(), 'f')


def _normalized_allocation_rate(value: Any) -> str:
    """Single trailing percent for present allocation rate; avoid 70%%.

    API calls are validated in Spring Boot, but the Worker also validates
    direct /render-customer-pptx requests to keep output consistent.
    """
    if value is None or value == '':
        return ''  # Existing direct Worker clients may omit this field.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError('INVALID_ALLOCATION_RATE: rate must be 0..100 (%)')
    raw = re.sub(r'\s+', '', str(value))
    if not re.fullmatch(r'\d{1,3}(?:\.\d{1,2})?%?', raw):
        raise ValueError('INVALID_ALLOCATION_RATE: rate must be 0..100 (%)')
    from decimal import Decimal
    numeric = raw.removesuffix('%')
    if Decimal(numeric) > 100:
        raise ValueError('INVALID_ALLOCATION_RATE: rate must be 0..100 (%)')
    return raw if raw.endswith('%') else raw + '%'


def _get_value(data: dict, field: str):
    if field == 'allocationRate':
        return _normalized_allocation_rate(data.get(field))
    if field == 'assignmentMonths':
        months = _validated_assignment_months(data)
        return f'{months}개월' if months else ''
    if field == 'ageYears':
        return f'만 {data["ageYears"]} 세' if data.get('ageYears') is not None else ''
    if field in ('schoolName', 'majorDegreeText'):
        # Current Spring Boot API exposes school and major in a single
        # "educationText": "school / major" string. Do not guess if the
        # delimiter is absent: preserve the complete text as school name.
        raw = str(data.get('educationText') or '').strip()
        school, sep, major = raw.partition(' / ')
        if field == 'schoolName':
            return school
        degree = str(data.get('degree') or '').strip()
        return f'{major} ({degree})' if sep and major and degree else (major if sep else degree)
    return data.get(field) or ''


def _nested_shape(slide, path: list[str]):
    """Locate text inside grouped PowerPoint shapes using a stable name path."""
    shapes = slide.shapes
    found = None
    for idx, name in enumerate(path):
        candidates = [shape for shape in shapes if shape.name == name]
        if len(candidates) != 1:
            raise ValueError('TEMPLATE_MAPPING_INVALID: grouped title shape is ambiguous')
        found = candidates[0]
        if idx < len(path)-1:
            if not hasattr(found, 'shapes'):
                raise ValueError('TEMPLATE_MAPPING_INVALID: non-group in title path')
            shapes = found.shapes
    if found is None or not found.has_text_frame:
        raise ValueError('TEMPLATE_MAPPING_INVALID: page title target must be text shape')
    return found


def _validate_title_format(fmt, field):
    if not isinstance(fmt, str) or not fmt.strip() or len(fmt) > 200:
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: {field} must be a short format string')
    try:
        fields = [name for _, name, _, _ in string.Formatter().parse(fmt) if name is not None]
    except ValueError as exc:
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: malformed {field}') from exc
    if any(name not in ('name', 'page', 'total') for name in fields):
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: invalid {field} placeholder')
    # Reject conversion/format specifiers, to keep formatting deterministic.
    for _, name, spec, conv in string.Formatter().parse(fmt):
        if name is not None and (spec or conv):
            raise ValueError(f'TEMPLATE_MAPPING_INVALID: {field} cannot format placeholders')


def _render_page_titles(prs, layout: dict, data: dict):
    spec = layout.get('pageTitle')
    if not spec:
        return
    path = spec['shapePath']
    total = len(prs.slides)
    for index, slide in enumerate(prs.slides):
        template = spec['firstFormat'] if index == 0 else spec['continuationFormat']
        value = template.format(name=str(data.get('name') or ''), page=index+1, total=total)
        _cell_unchanged_style(_nested_shape(slide, path), value)



def _project_value(item: dict, field: str, period_format: str = 'YYYY_MM'):
    if field == 'projectName':
        return item.get('projectName') or ''
    if field == 'period':
        a, b = _ym(item.get('startDate'), period_format), _ym(item.get('endDate'), period_format)
        return f'{a} ~ {b}' if a or b else _period_fallback(item.get('periodText'), period_format)
    if field == 'submittedRole':
        return item.get('submittedRole') or item.get('roleText') or ''
    if field == 'clientName':
        return item.get('clientName') or ''
    if field == 'workDescription':
        return item.get('workDescription') or ''
    if field in ('employerName', 'technologiesText', 'dbmsText', 'toolsText', 'osText',
                 'communicationText', 'otherText', 'industryText', 'applicationText', 'hardwareText'):
        return item.get(field) or ''
    raise ValueError('TEMPLATE_MAPPING_INVALID: unrecognized career field')


def _zip_safety(path: Path):
    try:
        with ZipFile(path) as archive:
            names = archive.infolist()
            if len(names) > 6000 or sum(i.file_size for i in names) > MAX_UNCOMPRESSED_BYTES:
                raise ValueError('TEMPLATE_INVALID: PPTX ZIP exceeds safe expansion limit')
            if not any(i.filename == 'ppt/presentation.xml' for i in names):
                raise ValueError('TEMPLATE_INVALID: not a PowerPoint presentation')
    except BadZipFile as exc:
        raise ValueError('TEMPLATE_INVALID: malformed PPTX ZIP') from exc


def _positive_int(value, key: str, low=0, high=200):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: {key} must be integer {low}..{high}')
    return value


def _auto_header(table):
    """Match 4/5 career columns irrespective of order; reject ambiguous headers."""
    for r in range(min(7, len(table.rows) - 1)):
        found = {}
        for c in range(len(table.columns)):
            txt = _norm(table.cell(r, c).text)
            for field, aliases in LABELS.items():
                if txt and txt in {_norm(a) for a in aliases}:
                    if field in found:
                        found = {}
                        break
                    found[field] = c
        if {'projectName', 'period', 'submittedRole', 'clientName'}.issubset(found):
            return r, found
    return None


def _auto_layout(prs):
    if len(prs.slides) != 1:
        raise ValueError('TEMPLATE_NEEDS_MAPPING: multi-slide template needs explicit mapping')
    slide = prs.slides[0]
    career_candidates = []
    personal_candidates = []
    for shape in slide.shapes:
        if not shape.has_table:
            continue
        t = shape.table
        header = _auto_header(t)
        if header:
            career_candidates.append((shape, header))
        if len(t.rows) >= 6 and len(t.columns) == 8 and \
                {'성명', '소속', '직책', '연령'}.issubset({_norm(t.cell(0, col).text) for col in range(8)}):
            personal_candidates.append(shape)
    if len(career_candidates) != 1 or len(personal_candidates) != 1:
        raise ValueError('TEMPLATE_NEEDS_MAPPING: personnel/career table structure differs; add .mapping.json')
    career, (header_row, columns) = career_candidates[0]
    personal = personal_candidates[0]
    # Default 8-column personal grid is intentionally exact. Never assume
    # arbitrary value cells for personal information.
    expected_personal = {(0,0):'성명', (0,2):'소속', (0,4):'직책', (0,6):'연령',
                         (1,4):'자격증', (4,4):'기술등급', (5,0):'본 사업\n담당업무',
                         (5,2):'본 사업\n투입기간', (5,6):'투입율'}
    if any(_norm(personal.table.cell(r,c).text) != _norm(label) for (r,c), label in expected_personal.items()):
        raise ValueError('TEMPLATE_NEEDS_MAPPING: personnel labels differ; add .mapping.json')
    # Require the original sample-style education/degree positions too.
    if _norm(personal.table.cell(1,3).text) != '학위':
        raise ValueError('TEMPLATE_NEEDS_MAPPING: degree position differs')
    first_capacity = len(career.table.rows) - header_row - 1
    if not 1 <= first_capacity <= 30:
        raise ValueError('TEMPLATE_NEEDS_MAPPING: no usable career row style')
    return {
        'version': 1,
        'sourceSlideIndex': 0,
        'careerTable': {
            'name': career.name,
            'headerRow': header_row,
            'dataStartRow': header_row + 1,
            'columnMap': columns,
            'firstPageCapacity': first_capacity,
            'continuationPageCapacity': 15,
            'titleCell': [0,0] if header_row > 0 and '경력' in career.table.cell(0,0).text else None,
            'continuationTopInches': round(personal.top / Inches(1), 4),
        },
        'personalCells': [
            {'table': personal.name, 'cell':[0,1], 'field':'name'},
            {'table': personal.name, 'cell':[0,3], 'field':'affiliation'},
            {'table': personal.name, 'cell':[0,5], 'field':'positionTitle'},
            {'table': personal.name, 'cell':[0,7], 'field':'ageYears'},
            {'table': personal.name, 'cell':[2,1], 'field':'educationText'},
            {'table': personal.name, 'cell':[2,3], 'field':'degree'},
            {'table': personal.name, 'cell':[1,5], 'field':'certificationsText'},
            {'table': personal.name, 'cell':[4,5], 'field':'displayGrade'},
            {'table': personal.name, 'cell':[5,1], 'field':'businessRole'},
            {'table': personal.name, 'cell':[5,3], 'field':'assignmentPeriod'},
            {'table': personal.name, 'cell':[5,7], 'field':'allocationRate'},
        ],
        'personalTextBoxes': [],
        'continuationRemoveShapes': [personal.name],
        'layoutMode': 'AUTO',
    }


def _load_mapping(path: Path):
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_MAPPING_BYTES:
            raise ValueError('TEMPLATE_MAPPING_INVALID: missing, symlink or oversized mapping')
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('TEMPLATE_MAPPING_INVALID: cannot read mapping JSON') from exc
    if not isinstance(value, dict) or value.get('version') != 1:
        raise ValueError('TEMPLATE_MAPPING_INVALID: version must equal 1')
    return value


def _validate_layout(prs, layout):
    """Validate all cell addresses and shape names before making the file selectable."""
    if not isinstance(layout, dict):
        raise ValueError('TEMPLATE_MAPPING_INVALID: layout object required')
    page = _positive_int(layout.get('sourceSlideIndex', 0), 'sourceSlideIndex', 0, 20)
    if len(prs.slides) not in (1, 2) or page >= len(prs.slides):
        raise ValueError('TEMPLATE_MAPPING_INVALID: template must have 1 slide or 2 including continuation')
    if page != 0:
        raise ValueError('TEMPLATE_MAPPING_INVALID: first page must be slide 0')
    continuation_index = layout.get('continuationSlideIndex')
    if len(prs.slides) == 2:
        if continuation_index != 1:
            raise ValueError('TEMPLATE_MAPPING_INVALID: second slide needs continuationSlideIndex=1')
    elif continuation_index is not None:
        raise ValueError('TEMPLATE_MAPPING_INVALID: continuationSlideIndex requires second slide')
    career = layout.get('careerTable')
    if not isinstance(career, dict):
        raise ValueError('TEMPLATE_MAPPING_INVALID: careerTable object required')
    name = career.get('name')
    if not isinstance(name, str) or not name or len(name) > 100:
        raise ValueError('TEMPLATE_MAPPING_INVALID: career table name required')
    slide = prs.slides[0]
    shape = _table(slide, name)
    t = shape.table
    row_start = _positive_int(career.get('dataStartRow'), 'dataStartRow', 1, 120)
    header_row = _positive_int(career.get('headerRow'), 'headerRow', 0, 120)
    if header_row >= row_start or row_start >= len(t.rows):
        raise ValueError('TEMPLATE_MAPPING_INVALID: dataStartRow/headerRow out of range')
    cmap = career.get('columnMap')
    if not isinstance(cmap, dict) or not {'projectName','period','submittedRole','clientName'}.issubset(cmap):
        raise ValueError('TEMPLATE_MAPPING_INVALID: columnMap must include 4 core columns')
    if any(k not in CAREER_KEYS for k in cmap):
        raise ValueError('TEMPLATE_MAPPING_INVALID: unknown career column key')
    if any(type(v) is not int or not 0 <= v < len(t.columns) for v in cmap.values()) or \
            len(set(cmap.values())) != len(cmap):
        raise ValueError('TEMPLATE_MAPPING_INVALID: columns missing, duplicated, or out of range')
    cap = _positive_int(career.get('firstPageCapacity'), 'firstPageCapacity', 1, 30)
    contcap = _positive_int(career.get('continuationPageCapacity', 15), 'continuationPageCapacity', 1, 30)
    font_size = career.get('fontSizePt')
    if font_size is not None and (not isinstance(font_size, (int, float)) or isinstance(font_size, bool) or not 6 <= font_size <= 18):
        raise ValueError('TEMPLATE_MAPPING_INVALID: fontSizePt must be 6..18')
    if cap > len(t.rows) - row_start:
        raise ValueError('TEMPLATE_MAPPING_INVALID: firstPageCapacity exceeds styled data rows')
    title = career.get('titleCell')
    if title is not None:
        _validate_cell(t, title, 'career titleCell')
    _validate_float_inches(career.get('continuationTopInches'), 'continuationTopInches', prs.slide_height)
    _validate_float_inches(career.get('bottomMarginInches', 0.42), 'bottomMarginInches', prs.slide_height)
    # All personal cells must be explicitly validated. Missing PII target fields
    # must not be populated with the prior employee's template data.
    cells = layout.get('personalCells', [])
    texts = layout.get('personalTextBoxes', [])
    if not isinstance(cells, list) or not isinstance(texts, list):
        raise ValueError('TEMPLATE_MAPPING_INVALID: personalCells and personalTextBoxes must be arrays')
    mapped = set()
    for item in cells:
        if not isinstance(item, dict) or item.get('field') not in PERSONAL_KEYS:
            raise ValueError('TEMPLATE_MAPPING_INVALID: personalCells field not recognized')
        table_name = item.get('table')
        if not isinstance(table_name, str):
            raise ValueError('TEMPLATE_MAPPING_INVALID: personal table name required')
        tbl = _table(slide, table_name).table
        _validate_cell(tbl, item.get('cell'), 'personal cell')
        if table_name == name:
            raise ValueError('TEMPLATE_MAPPING_INVALID: personal cell must not use career table')
        mapped.add((table_name, tuple(item['cell'])))
    if len(mapped) != len(cells):
        raise ValueError('TEMPLATE_MAPPING_INVALID: duplicate personal cell targets')
    for item in texts:
        if not isinstance(item, dict) or item.get('field') not in PERSONAL_KEYS:
            raise ValueError('TEMPLATE_MAPPING_INVALID: personalTextBoxes field not recognized')
        s = _shape(slide, item.get('shape', ''))
        if not s.has_text_frame or s.has_table:
            raise ValueError('TEMPLATE_MAPPING_INVALID: personal textbox target must be text shape')
    if layout.get('noPersonalDetails') is not True and 'name' not in [i.get('field') for i in cells + texts]:
        raise ValueError('TEMPLATE_MAPPING_INVALID: map employee name or set noPersonalDetails=true')
    for item in layout.get('clearCells', []):
        if not isinstance(item,dict) or not isinstance(item.get('table'),str):
            raise ValueError('TEMPLATE_MAPPING_INVALID: clearCells requires table and cell')
        _validate_cell(_table(slide,item['table']).table,item.get('cell'),'clear cell')
    for item in layout.get('clearTextBoxes', []):
        if not isinstance(item,str) or not _shape(slide,item).has_text_frame:
            raise ValueError('TEMPLATE_MAPPING_INVALID: clearTextBoxes requires text shapes')
    title_spec = layout.get('pageTitle')
    if title_spec is not None:
        if not isinstance(title_spec, dict):
            raise ValueError('TEMPLATE_MAPPING_INVALID: pageTitle must be an object')
        shape_path = title_spec.get('shapePath')
        if not isinstance(shape_path, list) or not 1 <= len(shape_path) <= 6 or \
                any(not isinstance(name, str) or not name or len(name) > 100 for name in shape_path):
            raise ValueError('TEMPLATE_MAPPING_INVALID: pageTitle.shapePath must contain 1..6 names')
        _nested_shape(slide, shape_path)
        _validate_title_format(title_spec.get('firstFormat'), 'firstFormat')
        _validate_title_format(title_spec.get('continuationFormat'), 'continuationFormat')
    if not isinstance(layout.get('continuationRemoveShapes', []),list):
        raise ValueError('TEMPLATE_MAPPING_INVALID: continuationRemoveShapes must be a list')
    for n in layout.get('continuationRemoveShapes', []):
        if not isinstance(n, str):
            raise ValueError('TEMPLATE_MAPPING_INVALID: invalid continuation shape name')
        _shape(slide, n)
        if n == name:
            raise ValueError('TEMPLATE_MAPPING_INVALID: do not remove the career table')
    if len(prs.slides) == 2:
        if not isinstance(layout.get('continuationRemoveShapesOnSecond', []),list):
            raise ValueError('TEMPLATE_MAPPING_INVALID: continuationRemoveShapesOnSecond must be array')
        for n in layout.get('continuationRemoveShapesOnSecond', []):
            if not isinstance(n,str):
                raise ValueError('TEMPLATE_MAPPING_INVALID: bad continuation shape')
            _shape(prs.slides[1],n)
        cont = layout.get('continuationCareerTable') or career
        if cont.get('name') in layout.get('continuationRemoveShapesOnSecond', []):
            raise ValueError('TEMPLATE_MAPPING_INVALID: cannot remove continuation career table')
        contshape = _table(prs.slides[1], cont.get('name', ''))
        ct = contshape.table
        data_start = _positive_int(cont.get('dataStartRow'), 'continuation dataStartRow', 1, 120)
        if data_start >= len(ct.rows):
            raise ValueError('TEMPLATE_MAPPING_INVALID: continuation table has no example data row')
        for field, col in (cont.get('columnMap') or {}).items():
            if field not in CAREER_KEYS or type(col) is not int or not 0 <= col < len(ct.columns):
                raise ValueError('TEMPLATE_MAPPING_INVALID: continuation columnMap invalid')
        if not {'projectName','period','submittedRole','clientName'}.issubset(cont.get('columnMap', {})):
            raise ValueError('TEMPLATE_MAPPING_INVALID: continuation columnMap lacks fields')
        for n in layout.get('continuationRemoveShapes', []):
            # A special continuation slide may omit personal shapes; that's fine.
            pass
    return layout


def _validate_cell(t, pos, what):
    if not isinstance(pos, list) or len(pos) != 2 or any(type(v) is not int for v in pos):
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: {what} requires [row, column]')
    r,c = pos
    if not 0 <= r < len(t.rows) or not 0 <= c < len(t.columns):
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: {what} outside table bounds')


def _validate_float_inches(v, key, slide_height):
    if v is not None and (not isinstance(v, (int,float)) or isinstance(v,bool) or v < 0 or v > slide_height / Inches(1)):
        raise ValueError(f'TEMPLATE_MAPPING_INVALID: {key} outside slide')


# STAGE02_7_REMARKS_TECH_MAPPING
# Bind a uniquely identified "비고" column to each project's reviewed technologiesText.
# The user's existing customer PPTX is NOT edited; only the in-memory layout is adapted.
def _map_remarks_to_technologies(prs, layout: dict) -> dict:
    career_layouts = [layout['careerTable']]
    continuation = layout.get('continuationCareerTable')
    if continuation is not None:
        career_layouts.append(continuation)

    for index, spec in enumerate(career_layouts):
        slide_index = (layout.get('continuationSlideIndex', 0)
                       if index == 1 else layout.get('sourceSlideIndex', 0))
        table = _table(prs.slides[slide_index], spec['name']).table
        data_start = spec.get('dataStartRow', 1)
        # Scan ONLY the career table header, never personal or project data rows.
        matches = {
            col for row in range(min(data_start, len(table.rows)))
            for col in range(len(table.columns))
            if _norm(table.cell(row, col).text) == _norm('비고')
        }
        if not matches:
            continue  # No "비고" column: keep customer mapping unchanged.
        if len(matches) != 1:
            raise ValueError('TEMPLATE_MAPPING_INVALID: ambiguous remarks columns')
        remarks_col = next(iter(matches))
        column_map = dict(spec['columnMap'])
        # Explicit "비고" request takes precedence over any prior field in that column.
        # Remove a previous tech target too, to avoid duplicate mapping to two columns.
        column_map = {field: col for field, col in column_map.items()
                      if field != 'technologiesText' and col != remarks_col}
        column_map['technologiesText'] = remarks_col
        spec['columnMap'] = column_map
    return layout


def _layout_for(path: Path, prs):
    mapping_path = path.with_suffix('.mapping.json')
    if mapping_path.exists():
        layout = _load_mapping(mapping_path)
        layout['layoutMode'] = 'MAPPED'
    else:
        layout = _auto_layout(prs)
    layout = _map_remarks_to_technologies(prs, layout)
    return _validate_layout(prs, layout)


@lru_cache(maxsize=128)
def _status_cached(path_text: str, modified_ns: int, size: int, map_modified_ns: int, map_size: int):
    try:
        file = Path(path_text)
        _zip_safety(file)
        p = Presentation(str(file))
        layout = _layout_for(file, p)
        name = layout.get('displayName') or file.stem.replace('_',' ').replace('-', ' ')
        if not isinstance(name, str) or len(name) > 100:
            raise ValueError('TEMPLATE_MAPPING_INVALID: displayName must be text up to 100 characters')
        career = layout['careerTable']
        cont_layout = layout.get('continuationCareerTable') or career
        return {'templateId': file.name, 'displayName': name, 'fileName': file.name,
                'status': 'READY', 'message': '자동 구조 인식' if layout['layoutMode']=='AUTO' else '고객사 매핑 적용',
                'layoutMode': layout['layoutMode'],
                'firstPageCapacity':career['firstPageCapacity'],
                'continuationPageCapacity':cont_layout.get('continuationPageCapacity', 15)}
    except Exception as exc:
        code = str(exc)
        status = 'NEEDS_MAPPING' if code.startswith('TEMPLATE_NEEDS_MAPPING') else 'INVALID'
        # Do not return untrusted shape names, raw file XML, or user PII in API diagnostics.
        reason = ('별도 고객사 필드 매핑이 필요합니다.' if status == 'NEEDS_MAPPING' else
                  'PPTX 또는 매핑 JSON 구조를 확인하세요.')
        return {'templateId': Path(path_text).name, 'displayName':Path(path_text).stem.replace('-',' '),
                'fileName':Path(path_text).name, 'status':status,'message':reason,
                'layoutMode':None,'firstPageCapacity':0,'continuationPageCapacity':0}


def available_templates(directory: Path) -> list[dict]:
    """List ALL admin-installed files, including templates pending mapping."""
    if not directory.is_dir():
        return []
    result = []
    for file in sorted(directory.iterdir(), key=lambda p:p.name.casefold()):
        if not file.is_file() or file.is_symlink() or file.suffix.lower() != '.pptx' or file.name.startswith('~$'):
            continue
        if len(file.name) > 180:
            continue
        try:
            stat = file.stat()
            if stat.st_size < 1 or stat.st_size > MAX_TEMPLATE_BYTES:
                result.append({'templateId':file.name,'fileName':file.name,'displayName':file.stem,
                               'status':'INVALID','message':'PPTX 파일 크기 제한(50MB)을 확인하세요.',
                               'layoutMode':None,'firstPageCapacity':0,'continuationPageCapacity':0})
            else:
                config = file.with_suffix('.mapping.json')
                config_stat = config.stat() if config.exists() else None
                result.append(_status_cached(str(file),stat.st_mtime_ns,stat.st_size,
                                             config_stat.st_mtime_ns if config_stat else -1,
                                             config_stat.st_size if config_stat else -1))
        except OSError:
            continue
        if len(result) >= MAX_TEMPLATES:
            break
    return result


def resolve_selected_template(directory: Path, template_id: str) -> Path:
    """Whitelist filenames and ensure template has verified layout status."""
    if not isinstance(template_id,str) or len(template_id) > 180 or not template_id or \
            '/' in template_id or '\\' in template_id or template_id in {'.','..'}:
        raise ValueError('INVALID_TEMPLATE_SELECTION: invalid template ID')
    for item in available_templates(directory):
        if item['templateId'] == template_id:
            if item['status'] != 'READY':
                raise ValueError('TEMPLATE_NOT_READY: customer mapping is not configured')
            return directory / item['fileName']
    raise ValueError('INVALID_TEMPLATE_SELECTION: unknown PPTX template')


def _fill_personal(slide, layout, data):
    # Some customer templates provide two independent target cells, while the
    # current bank templates expose one shared period+months target cell.
    fields = {i['field'] for i in layout.get('personalCells', []) + layout.get('personalTextBoxes', [])}
    separate_months_cell = 'assignmentMonths' in fields
    months = _validated_assignment_months(data)

    def value_for(field: str):
        if field == 'assignmentPeriod' and months and not separate_months_cell:
            period = str(data.get('assignmentPeriod') or '').strip()
            return f'{period}\n({months}개월)' if period else f'({months}개월)'
        return _get_value(data, field)

    for item in layout.get('clearCells', []):
        t = _table(slide,item['table']).table
        _cell_unchanged_style(t.cell(*item['cell']), '')
    for name in layout.get('clearTextBoxes', []):
        _cell_unchanged_style(_shape(slide,name), '')
    for item in layout.get('personalCells', []):
        t = _table(slide, item['table']).table
        r,c = item['cell']
        _cell_unchanged_style(t.cell(r,c), value_for(item['field']))
    for item in layout.get('personalTextBoxes', []):
        shape = _shape(slide, item['shape'])
        # Avoid replacing label fragments; placeholders may be used in textbox
        # (e.g. '소속: {{affiliation}}') without disturbing the surrounding text.
        tag = '{{' + item['field'] + '}}'
        existing = shape.text
        value = str(value_for(item['field']))
        if tag in existing:
            value = existing.replace(tag, value)
        _cell_unchanged_style(shape, value)


def _clone_slide(prs, source):
    """Clone shapes and relationship references, including embedded logos/photos.

    Directly copying XML without rId remapping breaks image relationships.
    Externally linked objects and active OLE are blocked to avoid unsafe exports.
    """
    from pptx.oxml.ns import qn
    dst = prs.slides.add_slide(source.slide_layout)
    for s in list(dst.shapes):
        dst.shapes._spTree.remove(s.element)
    idmap = {}
    for rel in source.part.rels.values():
        if rel.reltype == RT.SLIDE_LAYOUT:
            continue
        if rel.is_external:
            raise ValueError('TEMPLATE_UNSUPPORTED: external linked media is not allowed')
        if any(bad in rel.reltype.lower() for bad in ('oleobject', 'vbaproject','media','external')):
            raise ValueError('TEMPLATE_UNSUPPORTED: active media/OLE relationships')
        idmap[rel.rId] = dst.part.relate_to(rel.target_part, rel.reltype)
    rel_attrs = {qn('r:embed'), qn('r:link'), qn('r:id')}
    for shape in source.shapes:
        copied = deepcopy(shape.element)
        for node in copied.iter():
            for key,value in list(node.attrib.items()):
                if key in rel_attrs and value in idmap:
                    node.set(key,idmap[value])
        dst.shapes._spTree.insert_element_before(copied,'p:extLst')
    return dst


def _remove_slide(prs, slide_index: int):
    ids = prs.slides._sldIdLst
    rel_id = ids[slide_index].rId
    prs.part.drop_rel(rel_id)
    ids.remove(ids[slide_index])


def _fill_table(shape, layout, projects: list[dict], continued: bool,
                slide_height: int, continuation_top: float | None = None,
                period_format: str = 'YYYY_MM'):
    tbl = shape.table
    start = layout['dataStartRow']
    original = [deepcopy(r) for r in tbl._tbl.tr_lst]
    heads = original[:start]
    examples = original[start:]
    if not examples:
        raise ValueError('TEMPLATE_MAPPING_INVALID: no sample row')
    for row in list(tbl._tbl.tr_lst):
        tbl._tbl.remove(row)
    for row in heads:
        tbl._tbl.append(row)
    for i in range(len(projects)):
        tbl._tbl.append(deepcopy(examples[i % len(examples)]))
    if continued:
        if continuation_top is not None:
            shape.top = Inches(continuation_top)
        fixed_header = sum(tbl.rows[i].height for i in range(start))
        bottom_margin = layout.get('bottomMarginInches', 0.42)
        room = slide_height - Inches(bottom_margin) - shape.top - fixed_header
        if room <= 0:
            raise ValueError('TEMPLATE_MAPPING_INVALID: continuation table has no vertical space')
        if projects:
            standard = examples[0].get('h')
            original_size = int(standard) if standard else Inches(0.4)
            height = min(original_size, int(room // len(projects)))
            if height < Inches(0.26):
                raise ValueError('TEMPLATE_OVERFLOW: too many project rows for continuation slide')
            for row in list(tbl.rows)[start:]:
                row.height = height
    for i, project in enumerate(projects, start=start):
        # Clear unmapped data columns too: the template may contain a different
        # person's projects in its sample rows. Never leak stale data.
        for c in range(len(tbl.columns)):
            _cell_unchanged_style(tbl.cell(i,c), '')
        for field, c in layout['columnMap'].items():
            text = _project_value(project, field, period_format)
            n = len(str(text))
            base_size = layout.get('fontSizePt')
            f = min(base_size, 7.0) if base_size is not None and n > 50 else (
                min(base_size, 7.5) if base_size is not None and n > 38 else (
                    base_size if base_size is not None else (7.0 if n > 50 else 7.5 if n > 38 else None)))
            _cell_unchanged_style(tbl.cell(i, c), text, f)
    title_cell = layout.get('titleCell')
    if title_cell is not None:
        r,c = title_cell
        if continued:
            _cell_unchanged_style(tbl.cell(r,c), '경력사항 (계속)')
    shape.height = sum(row.height for row in tbl.rows)


def render_pptx(template: Path, data: dict) -> tuple[bytes, dict]:
    """Create a PPTX based on mapped or auto-discovered customer layout."""
    if not isinstance(data,dict):
        raise ValueError('Invalid profile JSON')
    projects = data.get('projects')
    if not isinstance(projects,list) or not 1 <= len(projects) <= MAX_PROJECTS or \
            any(not isinstance(p,dict) for p in projects):
        raise ValueError('projects must contain 1..100 objects')
    months = _validated_assignment_months(data)
    if months and '개월' in str(data.get('assignmentPeriod') or ''):
        raise ValueError('INVALID_ASSIGNMENT_PERIOD: enter months in assignmentMonths only')
    period_format = data.get('projectPeriodFormat') or 'YYYY_MM'
    if not isinstance(period_format, str) or period_format not in PROJECT_PERIOD_FORMATS:
        raise ValueError('INVALID_PROJECT_PERIOD_FORMAT: expected YYYY_MM or YY_MM')
    page_scope = data.get('careerPageScope', 'ALL')
    if not isinstance(page_scope, str) or page_scope not in CAREER_PAGE_SCOPES:
        raise ValueError('INVALID_CAREER_PAGE_SCOPE: expected ALL or FIRST_PAGE')
    _zip_safety(template)
    prs = Presentation(str(template))
    layout = _layout_for(template,prs)
    career = layout['careerTable']
    first_cap = career['firstPageCapacity']
    selected_count = len(projects)
    # A one-page export must never create a continuation slide, regardless of
    # how many project rows were submitted. Only top-ranked rows are printed.
    if page_scope == 'FIRST_PAGE':
        projects = projects[:first_cap]
    second_cap = (layout.get('continuationCareerTable') or career).get('continuationPageCapacity',career.get('continuationPageCapacity',15))
    original_cont = prs.slides[1] if layout.get('continuationSlideIndex') == 1 else None
    first = prs.slides[0]
    career_shape = _table(first,career['name'])
    first_chunk, rest = projects[:first_cap], projects[first_cap:]
    counts = [len(first_chunk)]
    _fill_personal(first,layout,data)
    _fill_table(career_shape, career, first_chunk, False, prs.slide_height, period_format=period_format)
    if not rest and original_cont is not None:
        _remove_slide(prs, 1)
    for offset in range(0,len(rest),second_cap):
        chunk = rest[offset: offset + second_cap]
        if original_cont is not None:
            cont_slide = original_cont if offset == 0 else _clone_slide(prs,original_cont)
            for shape_name in layout.get('continuationRemoveShapesOnSecond', []):
                hits = [s for s in cont_slide.shapes if s.name == shape_name]
                if len(hits) == 1:
                    cont_slide.shapes._spTree.remove(hits[0].element)
            cont_layout = layout.get('continuationCareerTable') or career
            cs = _table(cont_slide,cont_layout['name'])
            _fill_table(cs,cont_layout,chunk,True,prs.slide_height,
                        cont_layout.get('continuationTopInches'), period_format=period_format)
        else:
            cont_slide = _clone_slide(prs,first)
            # The source slide already contains employee data; remove all the
            # explicitly flagged personal shapes from cloned pages.
            remove = set(layout.get('continuationRemoveShapes',[]))
            remove.update(item['table'] for item in layout.get('personalCells',[]))
            remove.update(item['shape'] for item in layout.get('personalTextBoxes',[]))
            for name in remove:
                s = _shape(cont_slide,name)
                cont_slide.shapes._spTree.remove(s.element)
            cs = _table(cont_slide,career['name'])
            _fill_table(cs,career,chunk,True,prs.slide_height,career.get('continuationTopInches'), period_format=period_format)
        counts.append(len(chunk))
    _render_page_titles(prs, layout, data)
    stream=BytesIO()
    prs.save(stream)
    return stream.getvalue(), {'pageCount':len(prs.slides),'projectCount':len(projects),
                                'selectedProjectCount': selected_count, 'careerPageScope':page_scope,
                                'pageProjectCounts':counts, 'layoutMode':layout['layoutMode']}


def inspect_template(path: Path) -> dict:
    """Local-admin-only inspection: gives names and table header coordinates."""
    _zip_safety(path)
    prs=Presentation(str(path))
    result={'file':path.name,'slides':[]}
    for i,slide in enumerate(prs.slides):
        shapes=[]
        for s in slide.shapes:
            entry={'name':s.name,'type':str(s.shape_type),'topInches':round(s.top / Inches(1),3)}
            if s.has_table:
                known={_norm(label) for labels in LABELS.values() for label in labels}
                known.update({_norm(x) for x in ('성명','소속','직책','연령','학력','학위','전공',
                    '자격증','기술등급','본 사업 담당업무','본 사업 투입기간','투입율','경력사항')})
                entry['table']={'rows':len(s.table.rows),'columns':len(s.table.columns),
                                'topRows':[[s.table.cell(r,c).text[:40] if _norm(s.table.cell(r,c).text) in known
                                            else ('[value]' if s.table.cell(r,c).text.strip() else '')
                                           for c in range(len(s.table.columns))]
                                           for r in range(min(2,len(s.table.rows)))]}
            elif s.has_text_frame:
                entry['textPreview']='[text omitted]' if s.text.strip() else ''
                entry['tokens']=re.findall(r'\{\{([a-zA-Z]+)\}\}',s.text)
            shapes.append(entry)
        result['slides'].append({'index':i,'shapes':shapes})
    return result
