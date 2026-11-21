from __future__ import annotations
import importlib.util
import os
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / 'career-api/app/word_project_extractor.py'
spec = importlib.util.spec_from_file_location('word_project_extractor', MODULE)
ex = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ex)


def review(*rows):
    return {'blocks': [{'kind': kind, 'text': value} for kind, value in rows]}


class ProjectExtractionTests(unittest.TestCase):
    def test_two_level_header_and_sources(self):
        r = review(
            ('table', '프로젝트명 / (담당업무명) | 참여기간 | 고객사 | 근무회사 | 개 발 환 경 | 개 발 환 경 | 역 할'),
            ('table', '프로젝트명 / (담당업무명) | 참여기간 | 고객사 | 근무회사 | 언어 | DBMS | 역 할'),
            ('table', '가상 프로젝트 구축 | 2023/03 ~ 2024/02 | 발주A | 수행B | Java, React | PostgreSQL | PL'),
        )
        projects = ex.extract_projects(r)['projects']
        self.assertEqual(len(projects), 1)
        p = projects[0]
        self.assertEqual(p['projectName'], '가상 프로젝트 구축')
        self.assertEqual(p['startDate'], '2023-03-01')
        self.assertEqual(p['endDate'], '2024-02-01')
        self.assertEqual(p['employerName'], '수행B')
        self.assertEqual(p['technologiesText'], 'Java, React')
        self.assertEqual(p['dbmsText'], 'PostgreSQL')
        self.assertEqual(p['sourceBlockIds'], ['B3'])

    def test_unrelated_work_experience_table_is_not_project(self):
        r = review(
            ('table', '회사명 | 기간 | 직위 | 담당업무'),
            ('table', '회사A | 2020/01 ~ 2021/02 | 차장 | 개발'),
        )
        self.assertEqual(ex.extract_projects(r)['projects'], [])

    def test_key_value_paragraph_projects(self):
        r = review(
            ('heading', '프로젝트 수행 경력'),
            ('paragraph', '프로젝트명: 대고객 시스템 개선'),
            ('paragraph', '참여기간: 2022.04 ~ 2023.01'),
            ('paragraph', '고객사: 회사A'),
            ('paragraph', '역할: PL'),
            ('paragraph', '기술스택: Spring Boot, Oracle'),
            ('paragraph', '프로젝트명: ERP 연계 개발'),
            ('paragraph', '기간: 2023.02 ~ 현재'),
        )
        items = ex.extract_projects(r)['projects']
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]['submittedRole'], 'PL')
        self.assertEqual(items[0]['sourceBlockIds'], ['B2','B3','B4','B5','B6'])
        self.assertEqual(items[1]['endDate'], '')
        self.assertTrue(any('종료일' in x for x in ex.extract_projects(r)['warnings']))

    def test_unlabelled_numbered_project_in_section(self):
        r = review(
            ('heading', '프로젝트 경력'),
            ('paragraph', '1. 신규 시스템 구축 (2023/01 ~ 2023/12)'),
        )
        items=ex.extract_projects(r)['projects']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['projectName'], '신규 시스템 구축')

    def test_invalid_month_does_not_invent_date(self):
        r = review(
            ('table','프로젝트명 | 수행기간 | 고객사'),
            ('table','테스트 | 2024/15 ~ 2025/12 | A사'),
        )
        p=ex.extract_projects(r)['projects'][0]
        self.assertEqual(p['startDate'], '')
        self.assertEqual(p['endDate'], '')
        self.assertEqual(p['periodText'],'2024/15 ~ 2025/12')

    def test_no_ollama_required(self):
        r = review(('table','프로젝트명 | 기간 | 고객사'),('table','A | 2024/01 ~ 2024/02 | B'))
        result=ex.extract_projects(r)
        self.assertFalse(result['diagnostics']['llmCalled'])
        self.assertEqual(result['diagnostics']['engine'], 'RULE_V1')

    def test_actual_docx_if_available(self):
        path = os.environ.get('BWG_TEST_DOCX')
        if not path or not Path(path).is_file():
            self.skipTest('Set BWG_TEST_DOCX to the real source document to run integration test')
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
        d = Document(path)
        blocks=[]
        for elem in d.element.body.iterchildren():
            if elem.tag == qn('w:p'):
                p=Paragraph(elem,d)
                if p.text.strip():
                    style=p.style.name.casefold() if p.style else ''
                    blocks.append({'kind':'heading' if style.startswith(('heading','제목')) else 'paragraph','text':p.text.strip()})
            elif elem.tag == qn('w:tbl'):
                for row in Table(elem,d).rows:
                    value=' | '.join(cell.text.strip().replace('\n',' / ') for cell in row.cells)
                    if value.strip(' |'):
                        blocks.append({'kind':'table','text':value})
        self.assertEqual(len(blocks),58)
        result=ex.extract_projects({'blocks':blocks})
        self.assertEqual(len(result['projects']),22)
        self.assertEqual([p['sourceBlockIds'][0] for p in result['projects']], [f'B{i}' for i in range(37,59)])
        self.assertEqual(result['projects'][0]['startDate'],'2026-06-01')
        self.assertEqual(result['projects'][1]['employerName'],'Sptek')
        self.assertEqual(result['projects'][14]['clientName'],'')


if __name__=='__main__':
    unittest.main(verbosity=2)
