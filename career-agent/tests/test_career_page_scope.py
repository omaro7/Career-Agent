from io import BytesIO
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from career_pptx import render_pptx


def make_template(tmp_path: Path) -> Path:
    path = tmp_path / 'customer-profile.pptx'
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(10.83), Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    t = slide.shapes.add_table(6, 8, Inches(.4), Inches(.9), Inches(10), Inches(2.3)).table
    for c, label in {0: '성명', 2: '소속', 4: '직책', 6: '연령'}.items():
        t.cell(0,c).text = label
    for (r,c), value in {(1,4):'자격증',(1,3):'학위',(4,4):'기술등급',
                        (5,0):'본 사업\n담당업무',(5,2):'본 사업\n투입기간',(5,6):'투입율'}.items():
        t.cell(r,c).text = value
    t = slide.shapes.add_table(11, 5, Inches(.4), Inches(3.5), Inches(10), Inches(3.4)).table
    t.cell(0,0).text = '경력사항'
    for c, label in enumerate(['사업명','참여기간','담당업무','발주처','수행업무']):
        t.cell(1,c).text = label
    prs.save(path)
    return path


def payload(count: int, mode: str | None = None) -> dict:
    d = {
        'name':'가상 직원', 'affiliation':'가상 회사', 'positionTitle':'책임',
        'ageYears':35, 'educationText':'가상 대학교 / 학과', 'degree':'학사',
        'certificationsText':'자격증', 'displayGrade':'특급',
        'businessRole':'개발', 'assignmentPeriod':'2026.12 ~ 2027.12',
        'allocationRate':'70%', 'projectPeriodFormat':'YYYY_MM',
        'projects':[
            {'projectName': f'TEST_{i:02}', 'startDate':'2026-02-01', 'endDate':'2026-04-30',
             'submittedRole':'개발', 'clientName':'테스트기관', 'workDescription':'검증'}
            for i in range(count)
        ],
    }
    if mode is not None: d['careerPageScope'] = mode
    return d


@pytest.mark.parametrize('count', [1, 9, 10, 19, 25])
def test_first_page_only_count_and_order(tmp_path, count):
    raw,meta=render_pptx(make_template(tmp_path),payload(count,'FIRST_PAGE'))
    p=Presentation(BytesIO(raw))
    expected=min(count,9)
    assert len(p.slides)==meta['pageCount']==1
    assert meta['projectCount']==expected
    assert meta['selectedProjectCount']==count
    assert meta['careerPageScope']=='FIRST_PAGE'
    assert meta['pageProjectCounts']==[expected]
    first_slide=p.slides[0]
    table=[s.table for s in first_slide.shapes if s.has_table][-1]
    for idx in range(expected):
        assert table.cell(2+idx,0).text==f'TEST_{idx:02}'
    # stale rows cannot include content from omitted projects
    text=' '.join(c.text for row in table.rows for c in row.cells)
    assert f'TEST_{expected:02}' not in text
    assert any(s.has_table for s in first_slide.shapes)


@pytest.mark.parametrize('count,counts', [(1,[1]), (9,[9]), (10,[9,1]), (19,[9,10]), (25,[9,15,1])])
def test_all_pages_and_default(tmp_path,count,counts):
    raw,meta=render_pptx(make_template(tmp_path),payload(count))
    assert len(Presentation(BytesIO(raw)).slides)==len(counts)
    assert meta['pageProjectCounts']==counts
    assert meta['projectCount']==count
    assert meta['careerPageScope']=='ALL'


@pytest.mark.parametrize('bad', ['',None,'ONE','ONE_PAGE','all',12,{},[]])
def test_invalid_scope_is_rejected(tmp_path,bad):
    data=payload(4,'ALL'); data['careerPageScope']=bad
    with pytest.raises(ValueError,match='INVALID_CAREER_PAGE_SCOPE'):
        render_pptx(make_template(tmp_path),data)


def test_first_page_keeps_period_format(tmp_path):
    data=payload(19,'FIRST_PAGE');data['projectPeriodFormat']='YY_MM'
    raw,meta=render_pptx(make_template(tmp_path),data)
    t=[s.table for s in Presentation(BytesIO(raw)).slides[0].shapes if s.has_table][-1]
    assert t.cell(2,1).text=='26.02 ~ 26.04'
    assert meta['projectCount']==9




def test_first_page_removes_preexisting_continuation_slide(tmp_path):
    """Customer templates may contain a dedicated, pre-designed second slide."""
    import json
    path = tmp_path / 'two-slide.pptx'
    prs = Presentation()
    prs.slide_width,prs.slide_height=Inches(12), Inches(7.5)
    first=prs.slides.add_slide(prs.slide_layouts[6])
    person=first.shapes.add_table(2,4,Inches(.4),Inches(.7),Inches(11), Inches(.9))
    person.name='Executive Profile'
    person.table.cell(0,0).text='Name'; person.table.cell(0,1).text='OLDER SAMPLE NAME'
    person.table.cell(0,2).text='Company';person.table.cell(0,3).text='OLDER COMPANY'
    career=first.shapes.add_table(7,6,Inches(.4),Inches(2.4),Inches(11), Inches(3.7))
    career.name='Project Experience'
    for c,v in enumerate(['기간','발주기관','프로젝트명','수행역할','기술','수행내용']):
        career.table.cell(0,c).text=v
    second=prs.slides.add_slide(prs.slide_layouts[6])
    table2=second.shapes.add_table(11,5,Inches(.4), Inches(.9), Inches(11), Inches(5.8))
    table2.name='Projects Continued'
    for c,v in enumerate(['프로젝트명','기간','담당업무','발주처','수행업무']):
        table2.table.cell(0,c).text=v
    prs.save(path)
    m = {
        'version':1,'sourceSlideIndex':0,
        'careerTable':{'name':'Project Experience','headerRow':0,'dataStartRow':1,
            'columnMap':{'period':0,'clientName':1,'projectName':2,'submittedRole':3,'workDescription':5},
            'firstPageCapacity':6,'continuationPageCapacity':12,'continuationTopInches':.9,
            'bottomMarginInches':.4},
        'personalCells':[
            {'table':'Executive Profile','cell':[0,1],'field':'name'},
            {'table':'Executive Profile','cell':[0,3],'field':'affiliation'}],
        'continuationSlideIndex':1,
        'continuationCareerTable':{'name':'Projects Continued','headerRow':0,'dataStartRow':1,
            'columnMap':{'projectName':0,'period':1,'submittedRole':2,'clientName':3,'workDescription':4},
            'continuationPageCapacity':10,'continuationTopInches':.9,'bottomMarginInches':.4},
        'continuationRemoveShapes':['Executive Profile'],
    }
    path.with_suffix('.mapping.json').write_text(json.dumps(m,ensure_ascii=False), encoding='utf-8')
    pptx,meta=render_pptx(path,payload(19,'FIRST_PAGE'))
    assert meta['pageCount']==1
    assert meta['pageProjectCounts']==[6]
    prs=Presentation(BytesIO(pptx))
    assert len(prs.slides)==1
    assert next(s.table for s in prs.slides[0].shapes if s.name == 'Project Experience').cell(1,2).text == 'TEST_00'
