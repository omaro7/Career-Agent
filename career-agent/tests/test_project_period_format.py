"""Regression for React -> Spring API -> PPTX project participation period formats.

Run from worker directory: python -m pytest -q tests/test_project_period_format.py
Uses only synthetic employee data and a generated PPTX, no personal records.
"""
from io import BytesIO

import pytest
from pptx import Presentation
from pptx.util import Inches

from career_pptx import _ym, _project_value, render_pptx


def _template(tmp_path):
    file = tmp_path / 'customer-profile.pptx'
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10.83), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    person = slide.shapes.add_table(6, 8, Inches(.4), Inches(.9), Inches(10), Inches(2.3)).table
    for c, label in {0: '성명', 2: '소속', 4: '직책', 6: '연령'}.items():
        person.cell(0, c).text = label
    for (r, c), value in {(1, 4): '자격증', (1, 3): '학위', (4, 4): '기술등급',
                          (5, 0): '본 사업\n담당업무', (5, 2): '본 사업\n투입기간',
                          (5, 6): '투입율'}.items():
        person.cell(r, c).text = value
    career = slide.shapes.add_table(11, 5, Inches(.4), Inches(3.5), Inches(10), Inches(3.4)).table
    career.cell(0, 0).text = '경력사항'
    for c, name in enumerate(['사업명', '참여기간', '담당업무', '발주처', '수행업무']):
        career.cell(1, c).text = name
    prs.save(file)
    return file


def _data(count, period_format):
    return {
        'projectPeriodFormat': period_format,
        'name': '테스트 인력', 'affiliation': '테스트 회사', 'positionTitle': '책임',
        'ageYears': 42, 'educationText': '대학 / 전공', 'degree': '학사',
        'certificationsText': '자격증', 'displayGrade': '특급',
        'businessRole': '개발', 'assignmentPeriod': '2026.12 ~ 2027.12',
        'allocationRate': '70%',
        'projects': [
            {'projectName': f'프로젝트 {i:02}', 'startDate': '2026-02-01',
             'endDate': '2026-04-30', 'submittedRole': '개발',
             'clientName': '고객사', 'workDescription': '테스트'}
            for i in range(count)
        ],
    }


@pytest.mark.parametrize('fmt,expected', [
    ('YYYY_MM', '2026.02 ~ 2026.04'),
    ('YY_MM', '26.02 ~ 26.04'),
])
def test_period_format_written_on_all_pages(tmp_path, fmt, expected):
    raw, meta = render_pptx(_template(tmp_path), _data(19, fmt))
    prs = Presentation(BytesIO(raw))
    assert meta['pageProjectCounts'] == [9, 10]
    assert len(prs.slides) == 2
    for slide in prs.slides:
        career = [s.table for s in slide.shapes if s.has_table][-1]
        assert all(career.cell(r, 1).text == expected for r in range(2, len(career.rows)))
    assert '2026.12 ~ 2027.12' in [s.table.cell(5, 3).text for s in prs.slides[0].shapes if s.has_table]


def test_default_format_is_backward_compatible(tmp_path):
    data = _data(1, 'YYYY_MM')
    del data['projectPeriodFormat']
    raw, _ = render_pptx(_template(tmp_path), data)
    prs = Presentation(BytesIO(raw))
    career = [s.table for s in prs.slides[0].shapes if s.has_table][-1]
    assert career.cell(2, 1).text == '2026.02 ~ 2026.04'


@pytest.mark.parametrize('fmt', ['YY-MM', '2026.MM', 'YYYY-MM-DD', 1])
def test_invalid_format_is_rejected(tmp_path, fmt):
    with pytest.raises(ValueError, match='INVALID_PROJECT_PERIOD_FORMAT'):
        render_pptx(_template(tmp_path), _data(1, fmt))


def test_ym_accepts_iso_date_and_dates():
    assert _ym('2026-02-01', 'YYYY_MM') == '2026.02'
    assert _ym('2026.02', 'YY_MM') == '26.02'
    assert _ym('2026/04/30', 'YY_MM') == '26.04'


def test_missing_dates_use_safe_fallback():
    project = {'periodText': '2026.02 ~ 2026.04'}
    assert _project_value(project, 'period', 'YY_MM') == '26.02 ~ 26.04'
    assert _project_value({'periodText': '2026년 상반기'}, 'period', 'YY_MM') == '2026년 상반기'
