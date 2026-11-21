"""Template selection and multi-page regression tests, no private example required."""
from io import BytesIO
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from pptx import Presentation
from pptx.util import Inches
import app as worker_app
from career_pptx import available_templates, resolve_selected_template


def make_template(path: Path, label: str):
    pres = Presentation()
    pres.slide_width, pres.slide_height = Inches(10.83), Inches(7.5)
    slide = pres.slides.add_slide(pres.slide_layouts[6])
    box = slide.shapes.add_textbox(Inches(.2), Inches(.1), Inches(6), Inches(.4))
    box.text = label
    p = slide.shapes.add_table(6, 8, Inches(.3), Inches(.72), Inches(10), Inches(2)).table
    for c, val in enumerate(['성명','','소속','','직책','','연령','']): p.cell(0, c).text = val
    t = slide.shapes.add_table(11, 5, Inches(.3), Inches(3.2), Inches(10), Inches(3.4)).table
    for c, val in enumerate(['사업명','참여기간','담당업무','발주처','수행업무']): t.cell(1,c).text=val
    for c in range(5): t.cell(2,c).text='sample'
    pres.save(path)


@pytest.fixture
def templates(tmp_path, monkeypatch):
    main = tmp_path/'customer-profile.pptx'
    make_template(main,'CLIENT A')
    make_template(tmp_path/'customer-bank.pptx', 'CLIENT B')
    pres = Presentation(); pres.slides.add_slide(pres.slide_layouts[6]); pres.save(tmp_path/'invalid.pptx')
    (tmp_path/'~$lock.pptx').write_text('lock',encoding='utf-8')
    monkeypatch.setattr(worker_app,'TEMPLATE_PATH',main)
    monkeypatch.delenv('CAREER_PPTX_TEMPLATE_DIR',raising=False)
    return tmp_path


def payload(template=None, n=19):
    d = {'name':'Sample','affiliation':'Sample Company','positionTitle':'Senior','ageYears':51,
         'educationText':'University','degree':'Bachelor','certificationsText':'Sample Certificate',
         'displayGrade':'Senior','businessRole':'Engineer','assignmentPeriod':'2026.12 ~ 2027.12',
         'allocationRate':'70%', 'projects':[{'projectName':f'Project {i}', 'startDate':'2026-01-01',
         'endDate':'2026-06-01','submittedRole':'Dev','clientName':'Customer','workDescription':''}
         for i in range(n)]}
    if template is not None: d['templateId']=template
    return d


def test_catalog_filters_only_compatible(templates):
    r = TestClient(worker_app.app).get('/pptx/templates')
    assert r.status_code == 200
    assert [x['templateId'] for x in r.json()['templates']] == ['customer-bank.pptx','customer-profile.pptx']
    assert r.json()['defaultTemplateId'] == 'customer-profile.pptx'


@pytest.mark.parametrize('bad',['../secret.pptx','/etc/passwd','..\\secret.pptx','invalid.pptx'])
def test_disallow_unlisted_or_traversal(templates,bad):
    with pytest.raises(ValueError,match='INVALID_TEMPLATE_SELECTION'):
        resolve_selected_template(templates,bad)


@pytest.mark.parametrize('template,marker', [('customer-profile.pptx','CLIENT A'),('customer-bank.pptx','CLIENT B')])
def test_selected_template_multi_page(templates,template,marker):
    r = TestClient(worker_app.app).post('/render-customer-pptx',json=payload(template))
    assert r.status_code==200, r.text[:300]
    assert r.headers['x-pptx-page-count']=='2'
    pres=Presentation(BytesIO(r.content))
    assert len(pres.slides)==2
    assert marker in pres.slides[0].shapes[0].text
    assert len([s for s in pres.slides[0].shapes if s.has_table])==2
    assert len([s for s in pres.slides[1].shapes if s.has_table])==1


def test_old_post_without_template_still_works(templates):
    r=TestClient(worker_app.app).post('/render-customer-pptx',json=payload())
    assert r.status_code==200


def test_worker_rejects_invalid_choice(templates):
    r=TestClient(worker_app.app).post('/render-customer-pptx',json=payload('../secret.pptx'))
    assert r.status_code==422


def test_added_template_appears_immediately(templates):
    make_template(templates/'customer-new.pptx','CLIENT NEW')
    assert 'customer-new.pptx' in [t['templateId'] for t in available_templates(templates)]
