from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import sys

import pytest
from pptx import Presentation
from pptx.util import Inches

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'career-api' / 'app'))
from word_profile import from_blocks, worker_payload, split_period


def sample_docx_blocks():
    return {
        'title': '경력 기술서',
        'blocks': [
            {'kind': 'paragraph', 'text': '성명: 홍길동'},
            {'kind': 'table', 'text': '소속 | 뱅크웨어글로벌 | 직급 | 책임'},
            {'kind': 'table', 'text': '프로젝트명 | 참여기간 | 담당역할 | 발주처 | 수행업무'},
            {'kind': 'table', 'text': '차세대 금융 시스템 | 2021.03 ~ 2023.06 | PL | 테스트은행 | 데이터 이관'},
        ],
    }


def test_docx_labeled_table_and_project_to_payload():
    draft = from_blocks(sample_docx_blocks())
    assert draft['name'] == '홍길동'
    assert draft['affiliation'] == '뱅크웨어글로벌'
    assert draft['positionTitle'] == '책임'
    assert len(draft['projects']) == 1
    payload = worker_payload(draft, template_id='tpl-customer.pptx', template_customer_name='제출고객사')
    assert payload['projects'][0]['startDate'] == '2021-03-01'
    assert payload['projects'][0]['endDate'] == '2023-06-01'
    assert payload['projects'][0]['submittedRole'] == 'PL'
    assert payload['customerName'] == '제출고객사'


def test_missing_fields_not_fabricated():
    result = from_blocks({'blocks':[{'kind':'paragraph','text':'경험 많은 엔지니어입니다.'}]})
    assert result['name'] == ''
    assert result['projects'] == []
    assert len(result['extractionWarnings']) == 2
    with pytest.raises(ValueError, match='성명'):
        worker_payload(result, template_id='a.pptx')


def test_period_ambiguous_preserved_without_fabrication():
    assert split_period('2020.01 ~ 2020.12') == ('2020-01-01', '2020-12-01')
    assert split_period('20~21') == ('','')
    p = from_blocks({'blocks':[
        {'kind':'paragraph','text':'성명: 테스트'},
        {'kind':'paragraph','text':'프로젝트명: 모호한 기간'},
        {'kind':'paragraph','text':'참여기간: 약 2년'},
    ]})
    assert p['projects'][0]['periodText'] == '약 2년'
    assert p['projects'][0]['startDate'] == ''
    assert p['extractionWarnings']


def test_existing_agent_renderer_writes_customer_cells(tmp_path):
    agent_source = Path(os.getenv('CAREER_AGENT_SOURCE', str(ROOT.parent / 'career-agent')))
    if not (agent_source / 'career_pptx.py').is_file():
        pytest.skip('Set CAREER_AGENT_SOURCE to the existing career-agent source directory.')
    sys.path.insert(0, str(agent_source))
    from career_pptx import render_pptx

    customer = tmp_path / 'tpl-customer.pptx'
    prs = Presentation()
    prs.slide_width = Inches(13.3)
    prs.slide_height = Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    marker = slide.shapes.add_textbox(Inches(0.2), Inches(.1), Inches(6), Inches(.4))
    marker.text = '고객사 전용 양식 테스트'
    personal = slide.shapes.add_table(2, 2, Inches(.3), Inches(.6), Inches(6), Inches(1))
    personal.table.cell(0, 0).text = '성명'
    personal.table.cell(0, 1).text = '샘플 이름 (덮어써야 함)'
    career = slide.shapes.add_table(4, 5, Inches(.3), Inches(2.0), Inches(12), Inches(3.4))
    for col, label in enumerate(['사업명','참여기간','담당업무','발주처','수행업무']):
        career.table.cell(0, col).text = label
    for col in range(5):
        career.table.cell(1,col).text = '기존 샘플(덮어써야 함)'
    prs.save(customer)
    mapping = {
        'version': 1, 'sourceSlideIndex': 0,
        'careerTable': {
            'name': career.name, 'headerRow': 0, 'dataStartRow':1,
            'columnMap': {'projectName':0,'period':1,'submittedRole':2,'clientName':3,'workDescription':4},
            'firstPageCapacity':3,'continuationPageCapacity':3,'continuationTopInches':.6,
        },
        'personalCells': [{'table':personal.name,'cell':[0,1],'field':'name'}],
        'continuationRemoveShapes':[personal.name],
    }
    customer.with_suffix('.mapping.json').write_text(json.dumps(mapping), encoding='utf-8')
    payload = worker_payload(from_blocks(sample_docx_blocks()),template_id=customer.name)
    result, meta = render_pptx(customer, payload)
    rendered = Presentation(BytesIO(result))
    assert len(rendered.slides) == 1
    assert rendered.slides[0].shapes[0].text == '고객사 전용 양식 테스트'
    tables = [shape.table for shape in rendered.slides[0].shapes if shape.has_table]
    assert tables[0].cell(0,1).text == '홍길동'
    assert tables[1].cell(1,0).text == '차세대 금융 시스템'
    assert tables[1].cell(1,1).text == '2021.03 ~ 2023.06'
    assert tables[1].cell(1,2).text == 'PL'
    assert tables[1].cell(1,4).text == '데이터 이관'
    assert meta['projectCount'] == 1
