"""Stage 4 multi-template regression tests; no real employee files needed."""
from io import BytesIO
import json
from pathlib import Path
from PIL import Image
from pptx import Presentation
from pptx.util import Inches
import pytest

from career_pptx import (available_templates, resolve_selected_template,
                         render_pptx, inspect_template)


def data(n=19):
    return {
        'name':'NEW EMPLOYEE', 'affiliation':'NEW COMPANY',
        'positionTitle':'Architect','ageYears':51,
        'educationText':'University / CS','degree':'Bachelor',
        'certificationsText':'Certificate','displayGrade':'Senior',
        'businessRole':'Developer', 'assignmentPeriod':'2026.12 to 2027.12',
        'allocationRate':'70%', 'projects':[
            {'projectName':f'New Project {i:02d}', 'startDate':'2025-01-01',
             'endDate':'2025-05-01', 'submittedRole':'Lead', 'clientName':'Bank',
             'workDescription':'Development'} for i in range(n)]}


def make_default(path: Path):
    prs=Presentation()
    prs.slide_width,prs.slide_height=Inches(10.83),Inches(7.5)
    slide=prs.slides.add_slide(prs.slide_layouts[6])
    p=slide.shapes.add_table(6,8,Inches(.4),Inches(.9),Inches(10), Inches(2.3))
    p.name='Personnel'
    t=p.table
    for c,label in {0:'성명',2:'소속',4:'직책',6:'연령'}.items(): t.cell(0,c).text=label
    for (r,c),v in {(1,4):'자격증',(1,3):'학위',(4,4):'기술등급',
                     (5,0):'본 사업\n담당업무',(5,2):'본 사업\n투입기간',(5,6):'투입율'}.items():
        t.cell(r,c).text=v
    t.cell(0,1).text='OLD EMPLOYEE';t.cell(2,1).text='OLD SCHOOL'
    s=slide.shapes.add_table(11,5,Inches(.4),Inches(3.5),Inches(10), Inches(3.4)); s.name='Career'
    t=s.table; t.cell(0,0).text='경력사항'
    for c,value in enumerate(['사업명','참여기간','담당업무','발주처','수행업무']):t.cell(1,c).text=value
    for r in range(2,11):
        for c in range(5):t.cell(r,c).text='OLD PROJECT'
    prs.save(path)


def make_custom(path: Path, logo: Path | None = None, two_slides=False):
    prs=Presentation();prs.slide_width,prs.slide_height=Inches(12),Inches(7.5)
    slide=prs.slides.add_slide(prs.slide_layouts[6])
    if logo is not None: slide.shapes.add_picture(str(logo),Inches(.1),Inches(.1),Inches(.45),Inches(.45))
    s=slide.shapes.add_table(2,4,Inches(.4),Inches(.7),Inches(11), Inches(.9));s.name='Executive Profile'
    s.table.cell(0,0).text='Name';s.table.cell(0,1).text='OLD EMPLOYEE'
    s.table.cell(0,2).text='Company';s.table.cell(0,3).text='OLD COMPANY'
    tb=slide.shapes.add_textbox(Inches(.4),Inches(1.7),Inches(5),Inches(.3));tb.name='Duty';tb.text='Role: {{businessRole}}'
    s=slide.shapes.add_table(7,6,Inches(.4),Inches(2.4),Inches(11), Inches(3.7)); s.name='Project Experience'
    for c,v in enumerate(['기간','발주기관','프로젝트명','수행역할','기술','수행내용']): s.table.cell(0,c).text=v
    for r in range(1,7):
        for c in range(6):s.table.cell(r,c).text='OLD PROJECT'
    if two_slides:
        sec=prs.slides.add_slide(prs.slide_layouts[6]);sec_tb=sec.shapes.add_textbox(Inches(.5),Inches(.2),Inches(5), Inches(.4))
        sec_tb.text='Continuation';sec_tb.name='Continuation Title'
        t=sec.shapes.add_table(11,5,Inches(.4), Inches(.9), Inches(11), Inches(5.8));t.name='Projects Continued'
        for c,v in enumerate(['프로젝트명','기간','담당업무','발주처','수행업무']):t.table.cell(0,c).text=v
        for r in range(1,11):
            for c in range(5):t.table.cell(r,c).text='OLD PROJECT'
    prs.save(path)


def mapping(two_slides=False):
    d={
        'version':1,'displayName':'Custom Bank', 'sourceSlideIndex':0,
        'careerTable':{
            'name':'Project Experience', 'headerRow':0,'dataStartRow':1,
            'columnMap':{'period':0,'clientName':1,'projectName':2,'submittedRole':3,'workDescription':5},
            'firstPageCapacity':6,'continuationPageCapacity':12,
            'continuationTopInches':.9,'bottomMarginInches':.4
        },
        'personalCells':[
            {'table':'Executive Profile','cell':[0,1],'field':'name'},
            {'table':'Executive Profile','cell':[0,3],'field':'affiliation'}
        ],
        'personalTextBoxes':[{'shape':'Duty','field':'businessRole'}],
        'continuationRemoveShapes':['Executive Profile','Duty'],
    }
    if two_slides:
        d.update({'continuationSlideIndex':1,
                  'continuationCareerTable':{
                      'name':'Projects Continued','headerRow':0,'dataStartRow':1,
                      'columnMap':{'projectName':0,'period':1,'submittedRole':2,'clientName':3,'workDescription':4},
                      'continuationPageCapacity':10,
                      'continuationTopInches':.9,'bottomMarginInches':.4}})
    return d


def tables(slide):
    return [s for s in slide.shapes if s.has_table]


def test_default_auto_19_pages(tmp_path):
    file=tmp_path/'customer-profile.pptx';make_default(file)
    cats=available_templates(tmp_path)
    assert cats[0]['status']=='READY' and cats[0]['layoutMode']=='AUTO'
    output,meta=render_pptx(file,data())
    prs=Presentation(BytesIO(output))
    assert meta['pageProjectCounts']==[9,10] and len(prs.slides)==2
    assert len(tables(prs.slides[1]))==1
    assert prs.slides[0].shapes[0].table.cell(0,1).text=='NEW EMPLOYEE'
    assert 'OLD PROJECT' not in '\n'.join(c.text for sh in tables(prs.slides[1]) for row in sh.table.rows for c in row.cells)


def test_unmapped_custom_is_visible_but_not_selectable(tmp_path):
    file=tmp_path/'customer-profile-woori.pptx';make_custom(file)
    cat=available_templates(tmp_path)
    assert len(cat)==1 and cat[0]['status']=='NEEDS_MAPPING'
    with pytest.raises(ValueError,match='TEMPLATE_NOT_READY'):
        resolve_selected_template(tmp_path,file.name)


def test_mapped_custom_new_columns_and_logo_preserved(tmp_path):
    logo=tmp_path/'logo.png'; Image.new('RGB',(24,24),'navy').save(logo)
    file=tmp_path/'customer-profile-sinhyup.pptx';make_custom(file,logo)
    file.with_suffix('.mapping.json').write_text(json.dumps(mapping(),ensure_ascii=False),encoding='utf-8')
    cat=available_templates(tmp_path)
    assert cat[0]['status']=='READY' and cat[0]['layoutMode']=='MAPPED'
    assert cat[0]['firstPageCapacity']==6
    pptx,meta=render_pptx(file,data(19))
    p=Presentation(BytesIO(pptx))
    assert meta['pageProjectCounts']==[6,12,1]
    assert len(p.slides)==3
    assert all(any(s.shape_type==13 for s in slide.shapes) for slide in p.slides)
    assert p.slides[0].shapes[1].table.cell(0,1).text=='NEW EMPLOYEE'
    assert len([sh for sh in p.slides[1].shapes if sh.has_table])==1
    career=tables(p.slides[1])[0].table
    assert career.cell(1,2).text=='New Project 06'
    assert career.cell(1,4).text==''  # unmapped old project content must be cleared
    assert all('OLD COMPANY' not in sh.text for slide in list(p.slides)[1:] for sh in slide.shapes if sh.has_text_frame)


def test_custom_mapping_bad_cell_is_invalid(tmp_path):
    f=tmp_path/'customer-profile-woori.pptx';make_custom(f)
    m=mapping();m['personalCells'][0]['cell']=[99,1]
    f.with_suffix('.mapping.json').write_text(json.dumps(m),encoding='utf-8')
    cat=available_templates(tmp_path)
    assert cat[0]['status']=='INVALID'


def test_two_slide_continuation_uses_slide2_and_drops_if_unused(tmp_path):
    file=tmp_path/'two-slide.pptx';make_custom(file,two_slides=True)
    file.with_suffix('.mapping.json').write_text(json.dumps(mapping(True),ensure_ascii=False),encoding='utf-8')
    assert available_templates(tmp_path)[0]['status']=='READY'
    pptx,meta=render_pptx(file,data(16))
    prs=Presentation(BytesIO(pptx))
    assert meta['pageProjectCounts']==[6,10] and len(prs.slides)==2
    assert tables(prs.slides[1])[0].table.cell(1,0).text=='New Project 06'
    pptx2,meta2=render_pptx(file,data(3))
    assert meta2['pageCount']==1
    assert len(Presentation(BytesIO(pptx2)).slides)==1
    pptx3,meta3=render_pptx(file,data(27))
    assert meta3['pageProjectCounts']==[6,10,10,1]
    assert len(Presentation(BytesIO(pptx3)).slides)==4


def test_traversal_not_allowed(tmp_path):
    make_default(tmp_path/'default.pptx')
    for name in ['../passwd','/etc/passwd','..\\secret.pptx','missing.pptx']:
        with pytest.raises(ValueError,match='INVALID_TEMPLATE_SELECTION'):
            resolve_selected_template(tmp_path,name)


def test_manual_shape_inspection(tmp_path):
    f=tmp_path/'customer-sample.pptx';make_custom(f)
    outline=inspect_template(f)
    assert outline['slides'][0]['shapes'][0]['name']=='Executive Profile'
    assert outline['slides'][0]['shapes'][0]['table']['columns']==4


@pytest.mark.parametrize('num,pages',[ (1,[1]),(9,[9]),(10,[9,1]),(19,[9,10]),(25,[9,15,1])])
def test_default_page_boundaries(tmp_path,num,pages):
    f=tmp_path/'default.pptx';make_default(f)
    _,meta=render_pptx(f,data(num))
    assert meta['pageProjectCounts']==pages
