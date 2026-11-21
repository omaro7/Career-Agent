"""Stage 4: allocation rate numeric / percent normalization and PPTX output."""
from io import BytesIO
from pathlib import Path
import sys
import pytest
from pptx import Presentation

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from career_pptx import _normalized_allocation_rate, _get_value, render_pptx

@pytest.mark.parametrize('raw,expected', [
    ('70', '70%'), ('70%', '70%'), (' 70 % ', '70%'),
    (70, '70%'), ('70.5', '70.5%'), ('70.5%', '70.5%'),
    ('100', '100%'), ('0', '0%'), ('', ''), (None, ''),
])
def test_rate_normalization(raw, expected):
    assert _normalized_allocation_rate(raw) == expected
    assert _get_value({'allocationRate':raw}, 'allocationRate') == expected

@pytest.mark.parametrize('invalid', ['70%%', '105', '101%', '-5', '70퍼센트',
                                       'abc', '70.123', True, [], {}])
def test_bad_rate_rejected(invalid):
    with pytest.raises(ValueError, match='INVALID_ALLOCATION_RATE'):
        _normalized_allocation_rate(invalid)

@pytest.mark.parametrize('rate', ['70', '70%'])
def test_real_sinhyup_pptx_output(rate):
    path = Path(__file__).resolve().parents[2] / 'templates' / 'customer-profile-sinhyup.pptx'
    if not path.exists():
        # Development-only fallback to the supplied sample.
        path = Path('/mnt/data/customer-profile-sinhyup.pptx')
    if not path.exists():
        pytest.skip('optional source template missing')
    payload = {
        'name':'가상 직원', 'affiliation':'가상 회사', 'positionTitle':'개발자',
        'ageYears':31, 'educationText':'가상대학교 / 전공', 'degree':'학사',
        'certificationsText':'정보처리기사', 'displayGrade':'특급',
        'businessRole':'개발', 'assignmentPeriod':'2026.12 ~ 2027.12',
        'assignmentMonths':'12', 'allocationRate':rate,
        'projectPeriodFormat':'YYYY_MM', 'careerPageScope':'FIRST_PAGE',
        'projects':[{'projectName':'가상 프로젝트', 'startDate':'2026-02-01',
                     'endDate':'2026-04-01','submittedRole':'개발','clientName':'가상 고객'}],
    }
    output,meta = render_pptx(path,payload)
    prs = Presentation(BytesIO(output))
    assert len(prs.slides)==1 and meta['projectCount']==1
    tables=[s.table for s in prs.slides[0].shapes if s.has_table]
    assert tables[0].cell(5,7).text == '70%'
