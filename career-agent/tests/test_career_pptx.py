"""Stage 4 template compatibility/pagination tests.

Place the client PPTX at templates/customer-profile.pptx, or set
BWG_TEST_PPTX_TEMPLATE to an absolute template path.
"""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import pytest
from pptx import Presentation
from career_pptx import render_pptx

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = Path(os.getenv('BWG_TEST_PPTX_TEMPLATE', ROOT / 'templates' / 'customer-profile.pptx'))


def data(n: int):
    return {
        'name': 'Stage4테스트', 'affiliation': '가상테스트사', 'positionTitle': '책임', 'ageYears': 42,
        'educationText': '가상대학교 / 전산학', 'degree': '', 'certificationsText': '검증용 자격증',
        'displayGrade': '특급', 'businessRole': '개발', 'assignmentPeriod': '2026.12 ~ 2027.12',
        'allocationRate': '70%',
        'projects': [
            {'projectName': f'가상 프로젝트 {i:02d}', 'startDate': '2026-01-01',
             'endDate': '2026-04-01', 'submittedRole': 'PL', 'clientName': '테스트고객사',
             'workDescription': '가상 상세업무'} for i in range(1, n + 1)
        ]
    }


@pytest.mark.parametrize('count,expected_pages,expected_rows', [
    (1, 1, [1]), (9, 1, [9]), (10, 2, [9, 1]),
    (19, 2, [9, 10]), (24, 2, [9, 15]), (25, 3, [9, 15, 1]),
])
def test_pagination_and_client_template(count, expected_pages, expected_rows):
    if not TEMPLATE.is_file():
        pytest.skip('Client template is not placed in templates/')
    pptx, meta = render_pptx(TEMPLATE, data(count))
    prs = Presentation(BytesIO(pptx))
    assert len(prs.slides) == expected_pages
    assert meta['pageProjectCounts'] == expected_rows
    first_tables = [x.table for x in prs.slides[0].shapes if x.has_table]
    assert len(first_tables) == 2
    assert first_tables[0].cell(0, 1).text == 'Stage4테스트'
    assert first_tables[0].cell(0, 3).text == '가상테스트사'
    assert first_tables[0].cell(4, 5).text == '특급'
    assert first_tables[0].cell(2, 3).text == ''  # No fabricated degree.
    assert first_tables[1].cell(2, 4).text == '가상 상세업무'
    for page, expected in zip(list(prs.slides)[1:], expected_rows[1:]):
        tables = [x.table for x in page.shapes if x.has_table]
        assert len(tables) == 1  # No personal info on continuation pages.
        assert len(tables[0].rows) == expected + 2
        assert tables[0].cell(0, 0).text == '경력사항 (계속)'


def test_empty_project_rejected():
    if not TEMPLATE.is_file():
        pytest.skip('Client template is not placed in templates/')
    with pytest.raises(ValueError, match='1..100'):
        render_pptx(TEMPLATE, data(0))
