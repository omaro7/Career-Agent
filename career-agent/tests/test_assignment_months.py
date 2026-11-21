"""Stage 4 assignment period/months: two React inputs, one or two PPTX targets.

Run: PYTHONPATH=worker pytest -q worker/tests/test_assignment_months.py
"""
import json
from io import BytesIO
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from career_pptx import render_pptx, _auto_layout, _validated_assignment_months


def make_template(tmp: Path, months_label: bool = False) -> Path:
    path = tmp / 'customer-profile.pptx'
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10.83), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    person = slide.shapes.add_table(6, 8, Inches(.4), Inches(.9), Inches(10), Inches(2.3)).table
    for c, label in {0:'성명', 2:'소속', 4:'직책', 6:'연령'}.items(): person.cell(0,c).text = label
    for (r,c),value in {(1,4):'자격증',(1,3):'학위',(4,4):'기술등급',
                        (5,0):'본 사업\n담당업무',(5,2):'본 사업\n투입기간',(5,6):'투입율'}.items():
        person.cell(r,c).text = value
    if months_label: person.cell(5,4).text='투입개월'
    career = slide.shapes.add_table(11, 5, Inches(.4), Inches(3.5), Inches(10), Inches(3.4)).table
    career.cell(0,0).text='경력사항'
    for c,label in enumerate(['사업명','참여기간','담당업무','발주처','수행업무']):career.cell(1,c).text = label
    prs.save(path)
    if months_label:
        layout = _auto_layout(Presentation(str(path)))
        layout['personalCells'].append({'table':slide.shapes[0].name,'cell':[5,5],'field':'assignmentMonths'})
        path.with_suffix('.mapping.json').write_text(json.dumps(layout,ensure_ascii=False),encoding='utf-8')
    return path


def payload(months='12', projects=1):
    return {
        'name':'가상 직원','affiliation':'샘플','positionTitle':'책임','ageYears':35,
        'educationText':'가상 대학교 / 전공','degree':'학사','certificationsText':'정보처리기사',
        'displayGrade':'특급','businessRole':'개발','assignmentPeriod':'2026.12 ~ 2027.12',
        'assignmentMonths':months,'allocationRate':'70%',
        'projectPeriodFormat':'YY_MM','careerPageScope':'ALL',
        'projects':[{'projectName':f'PROJ_{i}','startDate':'2026-01-01',
                     'endDate':'2026-02-01','submittedRole':'개발','clientName':'고객사'} for i in range(projects)],
    }


def get_person_table(out):
    return [s.table for s in Presentation(BytesIO(out)).slides[0].shapes if s.has_table][0]


@pytest.mark.parametrize('month_value,expected', [('12','12'),('10.5','10.5'),('12.0','12'),('0.5','0.5'),('600','600')])
def test_normalization(month_value, expected):
    assert _validated_assignment_months({'assignmentMonths':month_value}) == expected


@pytest.mark.parametrize('value',[0,'0','0.0','-1','12개월','12.25','600.5','601',True,{},[]])
def test_invalid_months_rejected(tmp_path,value):
    with pytest.raises(ValueError,match='INVALID_ASSIGNMENT_MONTHS'):
        render_pptx(make_template(tmp_path), payload(value))


def test_combined_cell_in_default_template(tmp_path):
    path = make_template(tmp_path)
    out,meta=render_pptx(path,payload('12',19))
    t=get_person_table(out)
    assert t.cell(5,3).text == '2026.12 ~ 2027.12\n(12개월)'
    assert meta['pageProjectCounts'] == [9,10]


def test_half_month_combined(tmp_path):
    out,_=render_pptx(make_template(tmp_path),payload('10.5'))
    assert get_person_table(out).cell(5,3).text.endswith('(10.5개월)')


def test_legacy_payload_remains_unchanged(tmp_path):
    data=payload()
    del data['assignmentMonths']
    out,_=render_pptx(make_template(tmp_path),data)
    assert get_person_table(out).cell(5,3).text=='2026.12 ~ 2027.12'


def test_separate_field_mapping(tmp_path):
    out,_=render_pptx(make_template(tmp_path,months_label=True),payload('12',3))
    t=get_person_table(out)
    assert t.cell(5,3).text == '2026.12 ~ 2027.12'
    assert t.cell(5,5).text == '12개월'


def test_no_months_text_when_months_blank(tmp_path):
    out,_=render_pptx(make_template(tmp_path),payload('',2))
    assert get_person_table(out).cell(5,3).text=='2026.12 ~ 2027.12'


def test_other_period_option_still_works(tmp_path):
    out,meta=render_pptx(make_template(tmp_path),payload('12',19))
    prs=Presentation(BytesIO(out))
    assert len(prs.slides)==2
    car=[s.table for s in prs.slides[0].shapes if s.has_table][-1]
    assert car.cell(2,1).text=='26.01 ~ 26.02'
    assert meta['projectCount']==19


def test_duplicate_month_text_is_rejected(tmp_path):
    data=payload('12')
    data['assignmentPeriod']='2026.12 ~ 2027.12 (12개월)'
    with pytest.raises(ValueError, match='INVALID_ASSIGNMENT_PERIOD'):
        render_pptx(make_template(tmp_path),data)


def test_auto_mapper_supports_independent_months_label(tmp_path):
    from career_pptx_auto_mapper import propose_mapping
    path=make_template(tmp_path,months_label=True)
    result=propose_mapping(path)
    assert result['mapping'] is not None
    matches=[i for i in result['mapping']['personalCells'] if i['field']=='assignmentMonths']
    assert len(matches)==1
    assert matches[0]['cell']==[5,5]
