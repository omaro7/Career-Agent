"""Self-contained parser tests; do not require private Excel profiles or extra packages."""
from datetime import date
from io import BytesIO
from pathlib import Path
import sys
import unittest
from xml.etree import ElementTree as ET
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from career_parser import parse_profile_bytes

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'


def workbook(header_row=55, title='프로젝트명', has_anchors=True, project_count=2):
    fields = {
        'C7': '성명(한글)', 'D7': '가상직원',
        'C8': '직급', 'D8': '전문위원',
        'C10': '생년월일', 'D10': '1980.01.01',
        'C13': '기술자분류', 'D13': '고급',
        f'B{header_row}': title,
        f'C{header_row}': '참여기간',
        f'F{header_row}': '담당업무/역할',
        f'H{header_row}': '발주처',
        f'C{header_row+1}': '시작월',
        f'D{header_row+1}': '종료월',
    }
    if not has_anchors:
        fields[f'F{header_row}'] = '기타'
    for i in range(project_count):
        row = header_row + 2 + i
        fields.update({f'B{row}': f'가상프로젝트{i+1}',
                       f'C{row}': 44927 + i * 365,
                       f'D{row}': 45292 + i * 365,
                       f'F{row}': '개발', f'G{row}': 'Java',
                       f'H{row}': '가상발주처'})
    root = ET.Element(f'{{{MAIN}}}worksheet')
    data = ET.SubElement(root, f'{{{MAIN}}}sheetData')
    grouped = {}
    for cell, value in fields.items():
        row = int(''.join(x for x in cell if x.isdigit()))
        grouped.setdefault(row, []).append((cell, value))
    for row, values in sorted(grouped.items()):
        rn = ET.SubElement(data, f'{{{MAIN}}}row', r=str(row))
        for cell, value in sorted(values):
            if isinstance(value, int):
                c = ET.SubElement(rn, f'{{{MAIN}}}c', r=cell)
                ET.SubElement(c, f'{{{MAIN}}}v').text = str(value)
            else:
                c = ET.SubElement(rn, f'{{{MAIN}}}c', r=cell, t='inlineStr')
                inline = ET.SubElement(c, f'{{{MAIN}}}is')
                ET.SubElement(inline, f'{{{MAIN}}}t').text = value
    wk = ET.Element(f'{{{MAIN}}}workbook')
    wk.set('xmlns:r', REL)
    sheets = ET.SubElement(wk, f'{{{MAIN}}}sheets')
    s = ET.SubElement(sheets, f'{{{MAIN}}}sheet', name='프로파일', sheetId='1')
    s.set(f'{{{REL}}}id', 'rId1')
    relroot = ET.Element(f'{{{PKG}}}Relationships')
    ET.SubElement(relroot, f'{{{PKG}}}Relationship', Id='rId1',
                  Type=f'{REL}/worksheet', Target='worksheets/sheet1.xml')
    buf = BytesIO()
    with ZipFile(buf, 'w') as z:
        z.writestr('xl/workbook.xml', ET.tostring(wk, encoding='utf-8'))
        z.writestr('xl/_rels/workbook.xml.rels', ET.tostring(relroot, encoding='utf-8'))
        z.writestr('xl/worksheets/sheet1.xml', ET.tostring(root, encoding='utf-8'))
    return buf.getvalue()


class HeaderVariantTest(unittest.TestCase):
    def parse(self, **kwargs):
        return parse_profile_bytes(workbook(**kwargs), date(2026, 11, 17))

    def test_original_form(self):
        result = self.parse()
        self.assertEqual(result['summary']['project_rows'], 2)
        self.assertFalse(any(i['code'] == 'PROJECT_HEADER_VARIANT' for i in result['validation_issues']))

    def test_renamed_header(self):
        result = self.parse(title='사업명')
        self.assertEqual(result['summary']['project_rows'], 2)
        self.assertIn('PROJECT_HEADER_VARIANT', {x['code'] for x in result['validation_issues']})

    def test_header_extra_spaces(self):
        self.assertEqual(self.parse(title='프로젝트 명')['summary']['project_rows'], 2)

    def test_annotated_header(self):
        result = self.parse(title='프로젝트명 (사업명 / 필수)')
        self.assertEqual(result['summary']['project_rows'], 2)

    def test_moved_header(self):
        result = self.parse(header_row=58)
        self.assertEqual(result['profile']['projects'][0]['source_row'], 61)
        self.assertIn('PROJECT_HEADER_VARIANT', {x['code'] for x in result['validation_issues']})

    def test_no_headers_rejected(self):
        with self.assertRaisesRegex(ValueError, 'TEMPLATE_MISMATCH'):
            self.parse(title='무관한 표')

    def test_missing_anchor_rejected(self):
        with self.assertRaisesRegex(ValueError, 'TEMPLATE_MISMATCH'):
            self.parse(has_anchors=False)

    def test_header_without_projects_warning(self):
        result = self.parse(project_count=0)
        self.assertIn('NO_PROJECTS', {x['code'] for x in result['validation_issues']})

    def test_actual_example_if_available(self):
        # Optional regression in a developer's environment; never distributed.
        import os
        fname = os.getenv('BWG_PROFILE_TEST_FILE')
        if not fname or not Path(fname).is_file():
            self.skipTest('Private workbook fixture not provided')
        result = parse_profile_bytes(Path(fname).read_bytes(), date(2026, 11, 17))
        self.assertGreater(result['summary']['project_rows'], 0)


if __name__ == '__main__':
    unittest.main()
